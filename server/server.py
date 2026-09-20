"""Networking assistant server.

Conversation integration:
    POST /utterance                  already-transcribed text chunks from the Pi
    GET  /conversation/panel1         cached concise person research for the Quest
    GET  /conversation/panel2         generated live talking points for the Quest
    GET  /conversation/events         ordered diagnostic/event feed

The Pi owns audio capture/transcription. The Quest continuously sends camera frames to
POST /id. At conversation start, the server uses the newest of those frames to identify
the person and loads their precomputed verbose_research and concise_research from
people.db. The server owns conversation state and periodic talking-point generation.

The existing identity endpoint remains available:

    POST /id?frame_id=1234    body = raw JPEG bytes
    -> {"frame_id": 1234, "faces": [{"name","score","bbox"}], "ms": {...}}

Enrollment is the filesystem:
    faces/ryan.jpg            one shot
    faces/ryan/*.jpg          3-5 shots at different angles (preferred, see README)
The filename/foldername is the canonical person id everywhere in the system.

Run from the repository root: python3 -m server.server
"""
import base64
import argparse
import json
import math
import os
import re
import socket
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import time
import warnings
from pathlib import Path

# Support both the old root location during development and server/server.py.
PROJECT_ROOT = Path(__file__).resolve().parent
if PROJECT_ROOT.name == "server":
    PROJECT_ROOT = PROJECT_ROOT.parent
SERVER_DIR = PROJECT_ROOT / "server"
SEARCH_DIR = PROJECT_ROOT / "search"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(SEARCH_DIR))

import cv2
import numpy as np

import brain
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

warnings.filterwarnings("ignore")
from insightface.app import FaceAnalysis


TALKING_POINTS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "talking_points": {"type": "array", "items": {"type": "string"}},
        "suggested_question": {"type": "string"},
    },
    "required": ["headline", "talking_points", "suggested_question"],
}


def load_server_env():
    """Load server/.env without adding another configuration dependency."""
    path = SERVER_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


load_server_env()


def _parse_phrases(variable, default):
    return [
        item.strip().casefold()
        for item in os.environ.get(variable, default).split(",")
        if item.strip()
    ]


def _gemini_json(prompt):
    """Small Gemini REST adapter; only talking-point generation depends on it."""
    if os.environ.get("TALKING_POINTS_PROVIDER", "gemini").casefold() == "dummy":
        return {
            "headline": "Conversation ideas",
            "talking_points": ["Ask about their current work", "Explore a possible shared interest"],
            "suggested_question": "What are you most excited to work on next?",
        }

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from server/.env")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash-lite")
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    body = {
        "model": model,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": TALKING_POINTS_SCHEMA,
        },
        "generation_config": {"max_output_tokens": 500},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Gemini HTTP {error.code}: {detail}") from error

    try:
        text_parts = [
            content["text"]
            for step in payload["steps"]
            if step.get("type") == "model_output"
            for content in step.get("content", [])
            if content.get("type") == "text"
        ]
        return json.loads("".join(text_parts))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Gemini returned an invalid talking-points response") from error


def generate_talking_points(person_id, profile, research, transcript):
    max_chars = int(os.environ.get("TALKING_POINT_TRANSCRIPT_CHARS", "16000"))
    transcript_text = "\n".join(transcript)[-max_chars:]
    prompt = f"""You are a discreet live networking copilot. Suggest what the wearer
could discuss next with this person. Use the live conversation as the strongest
signal and the research only as supporting context. Never invent shared interests
or claim the wearer knows something that was not said. Avoid repeating points the
conversation already covered.

Person ID: {person_id}
Profile: {json.dumps(profile or {}, ensure_ascii=False)}
Research briefing:
{research.get("verbose", "")}

Current conversation transcript, in chronological chunks:
{transcript_text or "(No speech after the trigger yet.)"}

Return a headline under 8 words, 2-4 actionable talking points under 18 words each,
and one natural suggested question under 20 words."""
    result = _gemini_json(prompt)
    return {
        "headline": str(result.get("headline") or "").strip()[:100],
        "talking_points": [
            str(item).strip() for item in result.get("talking_points", []) if str(item).strip()
        ][:4],
        "suggested_question": str(result.get("suggested_question") or "").strip()[:240],
    }


def combine_research_context(verbose, concise):
    """Put display-friendly facts before raw research for model-facing context."""
    verbose = str(verbose or "").strip()
    concise = [concise] if isinstance(concise, str) else concise or []
    concise = [str(item).strip() for item in concise if str(item).strip()]
    sections = []
    if concise:
        sections.append("Concise research:\n" + "\n".join(f"- {item}" for item in concise))
    if verbose:
        sections.append("Verbose research:\n" + verbose)
    return "\n\n".join(sections), concise


class ConversationManager:
    """Thread-safe state machine for one wearer's current conversation."""

    def __init__(
        self,
        talking_point_generator=generate_talking_points,
        refresh_seconds=None,
        start_phrases=None,
        end_phrases=None,
        identity_resolver=None,
    ):
        self.generate = talking_point_generator
        self.resolve_identity = identity_resolver
        self.start_phrases = start_phrases or _parse_phrases(
            "CONVERSATION_START_PHRASES", "start conversation"
        )
        self.end_phrases = end_phrases or _parse_phrases(
            "CONVERSATION_END_PHRASES", "bye,goodbye,see you later,end conversation"
        )
        self.refresh_seconds = float(
            refresh_seconds
            if refresh_seconds is not None
            else os.environ.get("TALKING_POINT_REFRESH_SECONDS", "4")
        )
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="conversation")
        self.events = deque(maxlen=250)
        self.next_event_id = 1
        self.seen_chunk_ids = set()
        self.chunk_id_order = deque()
        self.trigger_tail = ""
        self.session = None
        self.scheduler = threading.Thread(
            target=self._schedule_loop,
            name="conversation-scheduler",
            daemon=True,
        )
        self.scheduler.start()

    @staticmethod
    def _find_phrase(text, phrases):
        folded = (text or "").casefold()
        return next(
            (
                phrase
                for phrase in phrases
                if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded)
            ),
            None,
        )

    def ingest(self, text, chunk_id=None):
        text = (text or "").strip()
        if not text:
            return self.state()
        with self.lock:
            if chunk_id:
                chunk_id = str(chunk_id)
                if chunk_id in self.seen_chunk_ids:
                    return self.state()
                self.seen_chunk_ids.add(chunk_id)
                self.chunk_id_order.append(chunk_id)
                if len(self.chunk_id_order) > 1000:
                    self.seen_chunk_ids.discard(self.chunk_id_order.popleft())
            active = self.session is not None
            active_session_id = self.session["id"] if active else None
            phrases = self.end_phrases if active else self.start_phrases
            # Check both joins: chunkers may split at either a word boundary
            # ("start" + "conversation") or inside a word ("con" + "versation").
            candidates = (text, f"{self.trigger_tail} {text}", f"{self.trigger_tail}{text}")
            trigger = None
            trigger_remainder = ""
            for candidate in candidates:
                trigger = self._find_phrase(candidate, phrases)
                if trigger:
                    match = re.search(
                        rf"(?<!\w){re.escape(trigger)}(?!\w)",
                        candidate.casefold(),
                    )
                    trigger_remainder = candidate[match.end():].strip(" \t\r\n,.;:!?—–-")
                    break
            tail_size = max((len(phrase) for phrase in phrases), default=0) + 2
            self.trigger_tail = f"{self.trigger_tail} {text}"[-tail_size:]

        if not active and trigger:
            log_pipeline_event(
                "trigger_detected",
                action="start",
                phrase=trigger,
                chunk_id=chunk_id,
            )
            self.start("trigger", trigger, initial_transcript=trigger_remainder)
        elif active and trigger:
            log_pipeline_event(
                "trigger_detected",
                action="end",
                phrase=trigger,
                chunk_id=chunk_id,
                session_id=active_session_id,
            )
            self.stop("trigger", trigger)
        else:
            with self.lock:
                if self.session is not None:
                    self.session["transcript"].append(text)
                    self.wake.set()
        return self.state()

    def start(self, source="manual", trigger=None, initial_transcript=None):
        with self.lock:
            if self.session is not None:
                self._end_locked("restarted")
            self.session = {
                "id": uuid.uuid4().hex,
                "started_at": time.time(),
                "transcript": [initial_transcript] if initial_transcript else [],
                "person_id": None,
                "profile": {},
                "research": None,
                "generation_inflight": False,
                "last_generation_started": 0.0,
                "next_generation_at": 0.0,
                "talking_points_version": 0,
                "talking_points": None,
                "last_error": None,
            }
            self.trigger_tail = ""
            self._publish_locked("conversation_started", source=source, trigger=trigger)
            self._publish_locked("identify_and_research_requested")
            self.wake.set()
            session_id = self.session["id"]
            resolver = self.resolve_identity
        if resolver is not None:
            self.workers.submit(self._identity_worker, session_id, resolver)
        return session_id

    def set_identity_resolver(self, resolver):
        """Install the latest-frame resolver after the identity service is initialized."""
        with self.lock:
            self.resolve_identity = resolver

    def _identity_worker(self, session_id, resolver):
        """Resolve the newest Quest frame without blocking transcript ingestion."""
        try:
            result = resolver()
        except Exception as error:
            self._fail_identification(session_id, f"{type(error).__name__}: {error}")
            return
        if not result:
            self._fail_identification(session_id, "no enrolled person matched the latest frame")
            return
        accepted, error = self.provide_research(
            session_id=session_id,
            person_id=result["person_id"],
            profile=result["profile"],
            verbose=result["verbose"],
            concise=result["concise"],
            sources=result.get("sources"),
        )
        # A stale worker or a competing accepted result is harmless. Other failures mean
        # there is no usable research context, so leave conversation mode and blank panels.
        if not accepted and error not in {
            "session_id is missing or stale",
            "no active conversation",
            "research already received for this conversation",
        }:
            self._fail_identification(session_id, error)

    def _fail_identification(self, session_id, message):
        with self.lock:
            if self._current_locked(session_id) is None:
                return
            self.session["last_error"] = message
            self._publish_locked("conversation_error", stage="identity", message=message)
            log_pipeline_event(
                "pipeline_error",
                session_id=session_id,
                stage="identity",
                message=message,
            )
            self._end_locked("identity_not_found")
            self.wake.set()

    def stop(self, reason="manual", trigger=None):
        with self.lock:
            if self.session is None:
                return False
            self._end_locked(reason, trigger)
            self.wake.set()
            return True

    def _end_locked(self, reason, trigger=None):
        session_id = self.session["id"]
        person_id = self.session["person_id"]
        self._publish_locked("conversation_ended", reason=reason, trigger=trigger)
        log_pipeline_event(
            "panel1_update",
            session_id=session_id,
            person_id=person_id,
            text="",
            bullets=[],
            reason="conversation_ended",
        )
        log_pipeline_event(
            "panel2_update",
            session_id=session_id,
            person_id=person_id,
            text="",
            headline="",
            talking_points=[],
            suggested_question="",
            version=0,
            reason="conversation_ended",
        )
        self.session = None
        self.trigger_tail = ""

    def provide_research(self, session_id, person_id, profile, verbose, concise, sources=None):
        """Accept output from the separate face/database/research component."""
        if not str(verbose or "").strip():
            return False, "verbose_research is required"
        combined_context, concise = combine_research_context(verbose, concise)
        sources = [sources] if isinstance(sources, str) else sources or []
        with self.lock:
            session = self.session
            if session is None:
                return False, "no active conversation"
            if not session_id or session_id != session["id"]:
                return False, "session_id is missing or stale"
            if session["research"] is not None:
                return False, "research already received for this conversation"
            session["person_id"] = str(person_id or "unknown")
            session["profile"] = dict(profile or {})
            session["research"] = {
                "verbose": combined_context,
                "concise": concise,
                "sources": [str(item).strip() for item in sources if str(item).strip()][:8],
            }
            session["last_error"] = None
            self._publish_locked(
                "research_ready",
                person_id=session["person_id"],
                display_name=session["profile"].get("name") or session["person_id"],
                concise=session["research"]["concise"],
                sources=session["research"]["sources"],
            )
            panel_one = {
                "text": "\n".join(f"• {item}" for item in concise),
                "session_id": session["id"],
                "person_id": session["person_id"],
                "display_name": session["profile"].get("name") or session["person_id"],
                "bullets": list(concise),
            }
            log_pipeline_event(
                "research_context",
                session_id=session["id"],
                person_id=session["person_id"],
                concise=list(concise),
                verbose=str(verbose).strip(),
                model_context=combined_context,
            )
            log_pipeline_event("panel1_update", **panel_one)
            self._start_generation_locked(session)
            return True, None

    def _schedule_loop(self):
        while not self.closed.is_set():
            self.wake.wait(timeout=min(max(self.refresh_seconds / 2, 0.05), 1.0))
            self.wake.clear()
            with self.lock:
                session = self.session
                now = time.monotonic()
                if (
                    session is not None
                    and session["research"] is not None
                    and not session["generation_inflight"]
                    and now >= session["next_generation_at"]
                    and now - session["last_generation_started"] >= self.refresh_seconds
                ):
                    self._start_generation_locked(session)

    def _start_generation_locked(self, session):
        if session["research"] is None or session["generation_inflight"]:
            return
        session["generation_inflight"] = True
        session["last_generation_started"] = time.monotonic()
        self.workers.submit(
            self._generation_worker,
            session["id"],
            session["person_id"],
            dict(session["profile"]),
            dict(session["research"]),
            list(session["transcript"]),
        )

    def _generation_worker(self, session_id, person_id, profile, research, transcript):
        try:
            points = self.generate(person_id, profile, research, transcript)
        except Exception as error:
            with self.lock:
                session = self._current_locked(session_id)
                if session is None:
                    return
                session["generation_inflight"] = False
                session["next_generation_at"] = time.monotonic() + max(self.refresh_seconds * 3, 15)
                session["last_error"] = f"{type(error).__name__}: {error}"
                self._publish_locked(
                    "conversation_error", stage="talking_points", message=session["last_error"]
                )
                log_pipeline_event(
                    "pipeline_error",
                    session_id=session_id,
                    stage="talking_points",
                    message=session["last_error"],
                )
            return

        with self.lock:
            session = self._current_locked(session_id)
            if session is None:
                return
            session["generation_inflight"] = False
            session["next_generation_at"] = 0.0
            session["talking_points_version"] += 1
            session["talking_points"] = dict(points)
            session["last_error"] = None
            self._publish_locked(
                "talking_points_ready",
                person_id=person_id,
                version=session["talking_points_version"],
                headline=points.get("headline", ""),
                talking_points=points.get("talking_points", []),
                suggested_question=points.get("suggested_question", ""),
            )
            panel_two = self.panel_two()
            log_pipeline_event(
                "panel2_update",
                provider=os.environ.get("TALKING_POINTS_PROVIDER", "gemini"),
                **panel_two,
            )
            self.wake.set()

    def panel_one(self):
        """Quest polling payload for concise person research; inactive means blank text."""
        with self.lock:
            session = self.session
            if session is None or session["research"] is None:
                return {
                    "text": "", "session_id": None, "person_id": None, "bullets": []
                }
            bullets = list(session["research"]["concise"])
            return {
                "text": "\n".join(f"• {item}" for item in bullets),
                "session_id": session["id"],
                "person_id": session["person_id"],
                "display_name": session["profile"].get("name") or session["person_id"],
                "bullets": bullets,
            }

    def panel_two(self):
        """Quest polling payload for generated talking points; inactive means blank text."""
        with self.lock:
            session = self.session
            points = session.get("talking_points") if session is not None else None
            if session is None or not points:
                return {
                    "text": "", "session_id": None, "person_id": None,
                    "headline": "", "talking_points": [], "suggested_question": "",
                    "version": 0,
                }
            lines = [points.get("headline", ""), *points.get("talking_points", [])]
            if points.get("suggested_question"):
                lines.append(points["suggested_question"])
            return {
                "text": "\n".join(item for item in lines if item),
                "session_id": session["id"],
                "person_id": session["person_id"],
                "headline": points.get("headline", ""),
                "talking_points": list(points.get("talking_points", [])),
                "suggested_question": points.get("suggested_question", ""),
                "version": session["talking_points_version"],
            }

    def _current_locked(self, session_id):
        return self.session if self.session is not None and self.session["id"] == session_id else None

    def _publish_locked(self, event_type, **payload):
        event = {
            "id": self.next_event_id,
            "session_id": self.session["id"] if self.session else payload.pop("session_id", None),
            "type": event_type,
            "created_at": time.time(),
            **payload,
        }
        self.next_event_id += 1
        self.events.append(event)

    def events_after(self, event_id):
        with self.lock:
            oldest = self.events[0]["id"] if self.events else self.next_event_id
            return {
                "events": [dict(event) for event in self.events if event["id"] > event_id],
                "cursor_reset": bool(event_id and event_id < oldest - 1),
                "latest_event_id": self.next_event_id - 1,
            }

    def state(self):
        with self.lock:
            if self.session is None:
                return {"active": False, "session_id": None, "history_count": 0}
            session = self.session
            return {
                "active": True,
                "session_id": session["id"],
                "started_at": session["started_at"],
                "history_count": len(session["transcript"]),
                "person_id": session["person_id"],
                "research_status": "ready" if session["research"] is not None else "waiting",
                "talking_points_status": "running" if session["generation_inflight"] else "idle",
                "talking_points_version": session["talking_points_version"],
                "last_error": session["last_error"],
            }

    def close(self):
        self.closed.set()
        self.wake.set()
        self.scheduler.join(timeout=1)
        self.workers.shutdown(wait=False, cancel_futures=True)

THRESHOLD = 0.45      # tuned against false positives; a wrong name is worse than no name
DET_SIZE = 320        # 330ms vs 869ms at 640, same 6/6 faces found
MAX_FACES = 4         # biggest N only; someone small in frame isn't who we're talking to
REID_AFTER = 10.0     # seconds before a tracked face is re-embedded. Each re-embed
                      # costs ~600ms and spikes that frame; a face doesn't stop being
                      # itself, so refresh slowly. Lower it if people swap places a lot.
FACE_WIDTH_M = 0.16   # detector bbox width across an average adult face, in metres.
                      # THE calibration knob for distance: stand a known 2.0m away,
                      # read distance_m, scale this constant by (2.0 / reading).
DEFAULT_HFOV = 80.0   # horizontal FOV of the sending camera, degrees. Override per
                      # request with ?hfov= -- the Quest exposes real intrinsics via
                      # PassthroughCameraAccess, so pass them once you have them.
EMBED_BUDGET = 1      # embeds per frame. 441ms each on CPU -- re-embedding everyone
                      # every frame costs more than a frame period, so the tracker
                      # could never win. New faces jump the queue; known faces refresh
                      # round-robin. Raise this the moment inference gets a GPU.
IOU_MATCH = 0.3
FACES_DIR = PROJECT_ROOT / "faces"

app_fa = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                      allowed_modules=["detection", "recognition"])
app_fa.prepare(ctx_id=-1, det_size=(DET_SIZE, DET_SIZE))
api = FastAPI()


class RequestLogLimiter:
    """Thread-safe duplicate-log limiter; never delays or rejects an HTTP request."""

    def __init__(self, interval_seconds=0.5):
        self.interval_seconds = max(float(interval_seconds), 0.0)
        self.lock = threading.Lock()
        self.entries = {}

    def record(self, source, method, path, now=None):
        """Return suppressed-repeat count when this request should log, else None."""
        now = time.monotonic() if now is None else now
        key = (source, method, path)
        with self.lock:
            previous = self.entries.get(key)
            if previous is not None and now - previous[0] < self.interval_seconds:
                self.entries[key] = (previous[0], previous[1] + 1)
                return None
            suppressed = previous[1] if previous is not None else 0
            self.entries[key] = (now, 0)
            return suppressed


REQUEST_LOGGING_ENABLED = False
REQUEST_LOG_LIMITER = RequestLogLimiter()
PIPELINE_LOG_LOCK = threading.Lock()
ASHLEY_IMAGE_PATH = Path(
    "/Users/andrewdeng/Desktop/Screenshot 2026-09-20 at 5.57.33\u202fAM.png"
)
IDENTITY_IMAGE_OVERRIDE = None


def configure_request_logging(enabled, interval_seconds=0.5):
    global REQUEST_LOGGING_ENABLED, REQUEST_LOG_LIMITER
    REQUEST_LOGGING_ENABLED = bool(enabled)
    REQUEST_LOG_LIMITER = RequestLogLimiter(interval_seconds)


def log_pipeline_event(event, **payload):
    """Print one readable, atomic pipeline event whenever request logging is enabled."""
    if not REQUEST_LOGGING_ENABLED:
        return
    timestamp = time.strftime("%H:%M:%S")
    details = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    with PIPELINE_LOG_LOCK:
        print(f"[pipeline {timestamp}] {event}\n{details}", flush=True)


def configure_identity_image_override(enabled):
    """Use Ashley's fixed screenshot instead of the image posted to /id."""
    global IDENTITY_IMAGE_OVERRIDE
    if not enabled:
        IDENTITY_IMAGE_OVERRIDE = None
        return
    try:
        jpeg = ASHLEY_IMAGE_PATH.read_bytes()
    except OSError as error:
        raise RuntimeError(f"could not read Ashley image at {ASHLEY_IMAGE_PATH}: {error}") from error
    if cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) is None:
        raise RuntimeError(f"Ashley image is not decodable: {ASHLEY_IMAGE_PATH}")
    IDENTITY_IMAGE_OVERRIDE = jpeg


@api.middleware("http")
async def log_http_request(request: Request, call_next):
    """Optional metadata-only request log enabled by the CLI's --log flag."""
    if not REQUEST_LOGGING_ENABLED:
        return await call_next(request)

    source = request.client.host if request.client else "unknown"
    method = request.method.upper()
    path = request.url.path
    query = request.url.query
    display_target = f"{path}?{query}" if query else path
    suppressed = REQUEST_LOG_LIMITER.record(source, method, path)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        if suppressed is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            repeats = f" (+{suppressed} suppressed)" if suppressed else ""
            timestamp = time.strftime("%H:%M:%S")
            print(
                f"[http {timestamp}] {source} {method} {display_target} "
                f"-> {status_code} {elapsed_ms}ms{repeats}",
                flush=True,
            )


def enroll():
    """faces/<name>.jpg or faces/<name>/*.jpg -> (names, embeddings, owner index)."""
    names, embs, owner = [], [], []
    shots = {}
    for p in sorted(FACES_DIR.glob("*")):
        if p.is_dir():
            shots[p.name] = sorted(p.glob("*.jpg")) + sorted(p.glob("*.png"))
        elif p.suffix.lower() in (".jpg", ".png"):
            shots.setdefault(p.stem, []).append(p)

    for name, paths in shots.items():
        kept = 0
        for path in paths:
            img = cv2.imread(str(path))
            faces = app_fa.get(img) if img is not None else []
            if len(faces) != 1:
                print(f"  skip {path.name}: {len(faces)} faces, need exactly 1")
                continue
            embs.append(faces[0].normed_embedding)
            owner.append(len(names))
            kept += 1
        if kept:
            names.append(name)
            print(f"  {name}: {kept} shot(s)")
    gallery = np.stack(embs) if embs else np.zeros((0, 512), "float32")
    return names, gallery, np.array(owner, dtype=int)


def load_profiles():
    """people.db keyed by the same id as faces/<id>. One id scheme, never two."""
    db = PROJECT_ROOT / "people.db"
    if not db.exists():
        return {}
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM people").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    return {r["id"]: {k: r[k] for k in r.keys() if k != "id"} for r in rows}


print("enrolling...")
NAMES, GALLERY, OWNER = enroll()
PROFILES = load_profiles()
CONVERSATIONS = ConversationManager()


def match(emb):
    """Max similarity across that person's shots -> (name, score)."""
    if not len(GALLERY):
        return None, 0.0
    sims = GALLERY @ emb
    # ponytail: per-person max via np.maximum.at; a dict would be the same speed at this size
    best = np.full(len(NAMES), -1.0, dtype="float32")
    np.maximum.at(best, OWNER, sims)
    i = int(best.argmax())
    return (NAMES[i] if best[i] >= THRESHOLD else None), round(float(best[i]), 3)


def distance_m(bbox_w, img_w, hfov_deg):
    """Rough distance from apparent face width. Works because adult faces vary little.

    Uses FOV rather than a focal length in pixels so it is scale-invariant -- Unity can
    downscale the frame however it likes without rescaling an intrinsic and silently
    putting every panel at the wrong depth.
    """
    if bbox_w <= 0:
        return None
    focal_px = (img_w / 2) / math.tan(math.radians(hfov_deg) / 2)
    return round(FACE_WIDTH_M * focal_px / bbox_w, 2)


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if not inter:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return inter / (area(a) + area(b) - inter)


# ponytail: one wearer, one camera, so a module-level track list is enough.
# IOU across frames; if fast head turns break the association, upgrade to CSRT here.
TRACKS = []  # [{bbox, name, score, last_embedded}]
PHOTOS = {}  # pid -> latest face+name-tag crop (BGR), the image OMNI sees
FOCUS = None  # pid of the biggest named face in the latest frame: who you're talking to
_latest_frame_lock = threading.Lock()
_latest_frame = None  # newest JPEG posted by the Quest, plus its completed /id result when ready
_latest_frame_sequence = 0


def upper_body(img, bbox):
    """Face plus the name tag below it, so OMNI sees both."""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    H, W = img.shape[:2]
    return img[max(0, y1 - h // 2):min(H, y2 + int(2.6 * h)),
               max(0, x1 - w):min(W, x2 + w)].copy()


def identify(jpeg, frame_id, hfov):
    t0 = time.perf_counter()
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("undecodable image")

    bboxes, kpss = app_fa.models["detection"].detect(img, max_num=0, metric="default")
    t_det = time.perf_counter()

    order = np.argsort([-(b[2] - b[0]) * (b[3] - b[1]) for b in bboxes])[:MAX_FACES]
    now, fresh = time.time(), []

    # Carry identity forward from whichever previous track overlaps this box.
    for i in order:
        bbox = [int(v) for v in bboxes[i][:4]]
        prev = max(TRACKS, key=lambda t: iou(t["bbox"], bbox), default=None)
        hit = prev if prev and iou(prev["bbox"], bbox) >= IOU_MATCH else None
        fresh.append({
            "idx": int(i), "bbox": bbox,
            "name": hit["name"] if hit else None,
            "score": hit["score"] if hit else 0.0,
            "last_embedded": hit["last_embedded"] if hit else 0.0,
        })

    # Spend the embed budget: never-seen faces first, then the stalest known one.
    stale = [t for t in fresh if now - t["last_embedded"] >= REID_AFTER]
    stale.sort(key=lambda t: t["last_embedded"])  # 0.0 (new) sorts first
    n_embedded = 0
    for t in stale[:EMBED_BUDGET]:
        i = t["idx"]
        emb = app_fa.models["recognition"].get(img, _Face(bboxes[i], kpss[i]))
        t["name"], t["score"] = match(emb / np.linalg.norm(emb))
        t["last_embedded"] = now
        n_embedded += 1

    TRACKS[:] = fresh
    global FOCUS
    named = [t for t in fresh if t["name"]]  # fresh is biggest-first
    FOCUS = named[0]["name"] if named else FOCUS
    for t in named:
        PHOTOS[t["name"]] = upper_body(img, t["bbox"])
    t_end = time.perf_counter()
    return {
        "frame_id": frame_id,
        "faces": [{"name": t["name"],
                   "method": "face" if t["name"] else None,
                   "score": t["score"], "bbox": t["bbox"],
                   "distance_m": distance_m(t["bbox"][2] - t["bbox"][0], img.shape[1], hfov),
                   "profile": PROFILES.get(t["name"]),
                   "insight": brain.get(t["name"])} for t in fresh],
        "ms": {"detect": round((t_det - t0) * 1000), "total": round((t_end - t0) * 1000)},
        "embedded": n_embedded,
    }


class _Face:
    """Minimal stand-in for insightface's Face so we can embed without re-detecting."""
    def __init__(self, bbox, kps):
        self.bbox, self.kps = bbox[:4], kps
        self.embedding = None


_identity_lock = threading.Lock()


def identify_serialized(jpeg, frame_id, hfov):
    """Keep legacy face state serialized while leaving FastAPI's event loop free."""
    with _identity_lock:
        return identify(jpeg, frame_id, hfov)


def log_identity_result(result, image_source):
    """Expose face-parser output without dumping image bytes into the log."""
    faces = [
        {
            "person": face.get("name") or "unknown",
            "confidence": round(float(face.get("score") or 0.0), 3),
            "bbox": face.get("bbox"),
        }
        for face in result.get("faces", [])
    ]
    log_pipeline_event(
        "image_parsed",
        image_source=image_source,
        frame_id=result.get("frame_id"),
        faces=faces,
        timing_ms=result.get("ms", {}),
    )


def _remember_latest_frame(jpeg, frame_id, hfov):
    global _latest_frame, _latest_frame_sequence
    with _latest_frame_lock:
        _latest_frame_sequence += 1
        token = _latest_frame_sequence
        _latest_frame = {
            "token": token,
            "jpeg": jpeg,
            "frame_id": frame_id,
            "hfov": hfov,
            "received_at": time.time(),
            "result": None,
        }
        return token


def _remember_identity_result(token, result):
    with _latest_frame_lock:
        if _latest_frame is not None and _latest_frame["token"] == token:
            _latest_frame["result"] = result


def _research_list(value):
    """SQLite-friendly concise research: accept JSON arrays or newline-delimited text."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        return [str(item).strip() for item in decoded if str(item).strip()]
    return [line.lstrip("-*• ").strip() for line in text.splitlines() if line.strip()]


def resolve_latest_frame_research():
    """Identify the newest Quest frame and return its cached database research.

    The largest recognized face that also has a people.db profile wins. Missing people
    or missing research intentionally produce no result, which ends conversation mode.
    """
    with _latest_frame_lock:
        frame = dict(_latest_frame) if _latest_frame is not None else None
    if frame is None:
        return None
    result = frame["result"]
    if result is None:
        result = identify_serialized(frame["jpeg"], frame["frame_id"], frame["hfov"])
        _remember_identity_result(frame["token"], result)
        log_identity_result(
            result,
            "fixed_ashley" if IDENTITY_IMAGE_OVERRIDE is not None else "latest_quest_frame",
        )

    for face in result.get("faces", []):
        person_id = face.get("name")
        profile = face.get("profile")
        if not person_id or not isinstance(profile, dict):
            continue
        verbose = str(profile.get("verbose_research") or "").strip()
        concise = _research_list(profile.get("concise_research"))
        if not verbose or not concise:
            continue
        return {
            "person_id": person_id,
            "profile": dict(profile),
            "verbose": verbose,
            "concise": concise,
            "sources": _research_list(profile.get("research_sources")),
        }
    return None


# ConversationManager is created during server initialization, before the identity
# functions below are defined. Attach the concrete latest-frame resolver now.
CONVERSATIONS.set_identity_resolver(resolve_latest_frame_research)


@api.post("/id")
async def post_id(request: Request, frame_id: int = -1, hfov: float = DEFAULT_HFOV):
    try:
        posted_jpeg = await request.body()
        jpeg = IDENTITY_IMAGE_OVERRIDE if IDENTITY_IMAGE_OVERRIDE is not None else posted_jpeg
        token = _remember_latest_frame(jpeg, frame_id, hfov)
        result = await run_in_threadpool(identify_serialized, jpeg, frame_id, hfov)
        _remember_identity_result(token, result)
        log_identity_result(
            result,
            "fixed_ashley" if IDENTITY_IMAGE_OVERRIDE is not None else "quest_upload",
        )
        return result
    except Exception as e:
        return {"frame_id": frame_id, "faces": [], "error": str(e)}


@api.post("/utterance")
async def utterance(msg: dict, request: Request):
    """Ingest text (or raw WAV for legacy clients) into the conversation coordinator.

    `chunk_id` makes Pi retries idempotent. `force=true` additionally runs the
    legacy brain.py demo path; it is unrelated to conversation mode.
    """
    audio = base64.b64decode(msg["audio_b64"]) if msg.get("audio_b64") else None
    text = msg.get("text") or ""
    if audio and not text:
        try:
            text = await run_in_threadpool(brain.transcribe, audio)
        except Exception as e:
            return {"ok": False, "person_id": msg.get("person_id"), "text": "", "error": f"stt: {e}"}
    if REQUEST_LOGGING_ENABLED:
        source = request.client.host if request.client else "unknown"
        chunk_id = msg.get("chunk_id") or msg.get("transcript_id")
        timestamp = time.strftime("%H:%M:%S")
        print(
            f"[utterance {timestamp}] {source} chunk_id={chunk_id or '-'} "
            f"text={json.dumps(text, ensure_ascii=False)}",
            flush=True,
        )
    conversation = CONVERSATIONS.ingest(text, msg.get("chunk_id") or msg.get("transcript_id"))

    # person_id remains for the legacy brain.py demo only. Conversation identity and
    # research arrive independently through POST /conversation/research.
    pid = msg.get("person_id") or FOCUS or "unknown"

    # The old one-shot demo remains available when force=true. Live conversation
    # talking points use the session coordinator above, not brain.py's separate buffer.
    photo = PHOTOS.get(pid)
    image = cv2.imencode(".jpg", photo)[1].tobytes() if photo is not None and photo.size else None
    fired = brain.add_utterance(
        pid, text, PROFILES.get(pid), image=image, audio=audio, force=bool(msg.get("force"))
    )
    return {
        "ok": True,
        "person_id": pid,
        "text": text,
        "fired": fired,
        "insight": brain.get(pid),
        "conversation": conversation,
    }


@api.post("/conversation/start")
async def start_conversation():
    session_id = CONVERSATIONS.start(source="manual")
    return {"ok": True, "session_id": session_id, "state": CONVERSATIONS.state()}


@api.post("/conversation/stop")
async def stop_conversation():
    stopped = CONVERSATIONS.stop(reason="manual")
    return {"ok": True, "stopped": stopped, "state": CONVERSATIONS.state()}


@api.post("/conversation/research")
async def conversation_research(msg: dict):
    """Receive output from the separate face/database/research component."""
    profile = msg.get("profile") if isinstance(msg.get("profile"), dict) else {}
    concise = msg.get("concise_research", msg.get("concise", []))
    sources = msg.get("sources") or []
    if not isinstance(concise, (list, tuple)) or not all(isinstance(item, str) for item in concise):
        raise HTTPException(status_code=422, detail="concise_research must be an array of strings")
    if not isinstance(sources, (list, tuple)) or not all(isinstance(item, str) for item in sources):
        raise HTTPException(status_code=422, detail="sources must be an array of strings")
    accepted, error = CONVERSATIONS.provide_research(
        session_id=str(msg.get("session_id") or ""),
        person_id=str(msg.get("person_id") or "unknown"),
        profile=profile,
        verbose=msg.get("verbose_research", msg.get("verbose", "")),
        concise=concise,
        sources=sources,
    )
    if not accepted:
        status = 409 if error in {
            "no active conversation",
            "session_id is missing or stale",
            "research already received for this conversation",
        } else 422
        raise HTTPException(status_code=status, detail=error)
    return {"ok": True, "state": CONVERSATIONS.state()}


@api.get("/conversation/events")
async def conversation_events(after: int = 0):
    """Ordered polling feed for the face/research component and headset UI."""
    result = CONVERSATIONS.events_after(max(after, 0))
    result["state"] = CONVERSATIONS.state()
    return result


@api.get("/conversation/state")
async def conversation_state():
    return CONVERSATIONS.state()


@api.get("/conversation/panel1")
async def conversation_panel_one():
    """Polled by the Quest for concise cached research about the matched person."""
    return CONVERSATIONS.panel_one()


@api.get("/conversation/panel2")
async def conversation_panel_two():
    """Polled by the Quest for Gemini-generated live talking points."""
    return CONVERSATIONS.panel_two()


@api.on_event("shutdown")
async def shutdown_conversation_workers():
    CONVERSATIONS.close()


@api.post("/reload")
async def reload():
    """Re-read faces/ and people.db without a restart -- startup costs ~50s."""
    global NAMES, GALLERY, OWNER, PROFILES
    NAMES, GALLERY, OWNER = enroll()
    PROFILES = load_profiles()
    TRACKS.clear()
    PHOTOS.clear()
    with _latest_frame_lock:
        if _latest_frame is not None:
            _latest_frame["result"] = None
    return {"enrolled": NAMES, "profiles": sorted(PROFILES)}


@api.get("/debug/latest_insight")
async def latest_insight():
    """Whatever talk.py (or the Pi) most recently generated, for anyone -- lets
    DebugInsightOverlay.cs show real LLM output in-headset with no working face
    match at all (no printed photo, PassthroughCameraAccess not working over Link, etc)."""
    pid, insight = brain.latest()
    return {"person_id": pid, "insight": insight, "profile": PROFILES.get(pid) if pid else None}


@api.get("/health")
async def health():
    return {
        "recognizable": sorted(NAMES),                                  # face + in gallery
        "awaiting_face": sorted(set(PROFILES) - set(NAMES)),            # profile only
        "face_but_no_profile": sorted(set(NAMES) - set(PROFILES)),      # id typo, usually
        "threshold": THRESHOLD, "det_size": DET_SIZE,
        "conversation": CONVERSATIONS.state(),
        "start_phrases": CONVERSATIONS.start_phrases,
        "end_phrases": CONVERSATIONS.end_phrases,
    }


DISCOVERY_PORT = 41234
DISCOVERY_MAGIC = b"HACKTHENORTH_ID_SERVER:8000"


def _broadcast_presence():
    """So ServerDiscovery.cs on the Quest can find this server without anyone hand-typing
    or updating a LAN IP -- broadcasts "I'm here, port 8000" on the local subnet every
    second. Harmless if nobody's listening; this is the thing that survives venue Wi-Fi
    handing out a different IP than last time."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            sock.sendto(DISCOVERY_MAGIC, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass  # network briefly down (e.g. Wi-Fi reconnecting) -- keep trying
        time.sleep(1.0)


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description="Run the networking-assistant server.")
    parser.add_argument(
        "--log",
        action="store_true",
        help="Log request metadata and the text of each utterance chunk.",
    )
    parser.add_argument(
        "--log-no-picture",
        action="store_true",
        help=(
            "Enable --log and use the fixed Ashley screenshot for /id instead of "
            "the posted image."
        ),
    )
    parser.add_argument(
        "--log-interval",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Minimum interval between duplicate log lines per client/method/path (default: 0.5).",
    )
    args = parser.parse_args()
    if args.log_interval < 0:
        parser.error("--log-interval must be zero or greater")
    configure_request_logging(args.log or args.log_no_picture, args.log_interval)
    try:
        configure_identity_image_override(args.log_no_picture)
    except RuntimeError as error:
        parser.error(str(error))

    lan = socket.gethostbyname(socket.gethostname())
    print(f"{len(NAMES)} enrolled: {', '.join(NAMES) or 'nobody'}")
    print(f"Quest posts to  http://{lan}:8000/id?frame_id=N   (NOT localhost)")
    print(f"Broadcasting presence on UDP {DISCOVERY_PORT} for auto-discovery")
    if args.log or args.log_no_picture:
        print(
            f"HTTP request logging enabled (duplicate interval: {args.log_interval:.3g}s)"
        )
    if args.log_no_picture:
        print(f"Fixed /id image enabled: {ASHLEY_IMAGE_PATH}")
    threading.Thread(target=_broadcast_presence, daemon=True).start()
    uvicorn.run(api, host="0.0.0.0", port=8000, log_level="info", access_log=False)
