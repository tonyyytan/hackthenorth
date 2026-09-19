"""Identity service. Quest POSTs a JPEG, gets back named boxes.

    POST /id?frame_id=1234    body = raw JPEG bytes
    -> {"frame_id": 1234, "faces": [{"name","score","bbox"}], "ms": {...}}

Enrollment is the filesystem:
    faces/ryan.jpg            one shot
    faces/ryan/*.jpg          3-5 shots at different angles (preferred, see README)
The filename/foldername is the canonical person id everywhere in the system.

Run: python server.py
"""
import base64
import json
import math
import os
import urllib.request
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import time
import warnings
from pathlib import Path

import cv2
import numpy as np

import badge
import brain
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool

warnings.filterwarnings("ignore")
from insightface.app import FaceAnalysis

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
FACES_DIR = Path(__file__).parent / "faces"

app_fa = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                      allowed_modules=["detection", "recognition"])
app_fa.prepare(ctx_id=-1, det_size=(DET_SIZE, DET_SIZE))
api = FastAPI()


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
    db = Path(__file__).parent / "people.db"
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


# ponytail: OCR costs ~1.5s, so it never runs on the response path. One worker,
# one attempt per track; the name lands on a later frame. A badge is a fallback
# for people who never enrolled a face, so late-but-correct beats blocking.
_ocr_pool = ThreadPoolExecutor(max_workers=1)


def badge_roster():
    return {pid: p["name"] for pid, p in PROFILES.items() if p.get("name")}


# ponytail: one wearer, one camera, so a module-level track list is enough.
# IOU across frames; if fast head turns break the association, upgrade to CSRT here.
TRACKS = []  # [{bbox, name, score, last_embedded}]
PHOTOS = {}  # pid -> latest face+name-tag crop (BGR), the image OMNI sees
FOCUS = None  # pid of the biggest named face in the latest frame: who you're talking to


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
            "badge": hit["badge"] if hit else None,
            "badge_job": hit["badge_job"] if hit else None,
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

    # Badge fallback for anyone the face gallery could not name.
    for t in fresh:
        if t["badge_job"] is not None and t["badge_job"].done():
            t["badge"], _ = t["badge_job"].result()
            t["badge_job"] = None
        elif t["badge_job"] is None and not t["name"] and not t["badge"]:
            t["badge_job"] = _ocr_pool.submit(
                badge.identify_badge, img.copy(), t["bbox"], badge_roster())

    TRACKS[:] = fresh
    global FOCUS
    named = [t for t in fresh if t["name"] or t["badge"]]  # fresh is biggest-first
    FOCUS = (named[0]["name"] or named[0]["badge"]) if named else FOCUS
    for t in named:
        PHOTOS[t["name"] or t["badge"]] = upper_body(img, t["bbox"])
    t_end = time.perf_counter()
    return {
        "frame_id": frame_id,
        "faces": [{"name": t["name"] or t["badge"],
                   "method": "face" if t["name"] else "badge" if t["badge"] else None,
                   "score": t["score"], "bbox": t["bbox"],
                   "distance_m": distance_m(t["bbox"][2] - t["bbox"][0], img.shape[1], hfov),
                   "profile": PROFILES.get(t["name"] or t["badge"]),
                   "insight": brain.get(t["name"] or t["badge"])} for t in fresh],
        "ms": {"detect": round((t_det - t0) * 1000), "total": round((t_end - t0) * 1000)},
        "embedded": n_embedded,
    }


class _Face:
    """Minimal stand-in for insightface's Face so we can embed without re-detecting."""
    def __init__(self, bbox, kps):
        self.bbox, self.kps = bbox[:4], kps
        self.embedding = None


@api.post("/id")
async def post_id(request: Request, frame_id: int = -1, hfov: float = DEFAULT_HFOV):
    try:
        return identify(await request.body(), frame_id, hfov)
    except Exception as e:
        return {"frame_id": frame_id, "faces": [], "error": str(e)}


@api.post("/utterance")
async def utterance(msg: dict):
    """{"person_id": "alana-goyal", "text": "..."} from the Pi, or {"audio_b64": <wav>}
    from the Quest mic. person_id defaults to whoever is biggest in frame (FOCUS);
    audio without text is transcribed by OpenAI. The insight call runs in the
    background -- only the transcription is waited on."""
    pid = msg.get("person_id") or FOCUS
    audio = base64.b64decode(msg["audio_b64"]) if msg.get("audio_b64") else None
    text = msg.get("text") or ""
    if audio and not text:
        try:
            text = await run_in_threadpool(brain.transcribe, audio)
        except Exception as e:
            return {"ok": False, "person_id": pid, "text": "", "error": f"stt: {e}"}
    photo = PHOTOS.get(pid)
    image = cv2.imencode(".jpg", photo)[1].tobytes() if photo is not None and photo.size else None
    fired = brain.add_utterance(pid, text, PROFILES.get(pid), image=image, audio=audio)
    return {"ok": True, "person_id": pid, "text": text, "fired": fired, "insight": brain.get(pid)}


@api.post("/reload")
async def reload():
    """Re-read faces/ and people.db without a restart -- startup costs ~50s."""
    global NAMES, GALLERY, OWNER, PROFILES
    NAMES, GALLERY, OWNER = enroll()
    PROFILES = load_profiles()
    TRACKS.clear()
    PHOTOS.clear()
    return {"enrolled": NAMES, "profiles": sorted(PROFILES)}


@api.get("/stt-token")
def stt_token():
    """Single-use AssemblyAI streaming token for the Quest, so the real key never ships in
    the APK. ASSEMBLYAI_API_KEY comes from the env or server/.env (brain.py loads it)."""
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        return {"token": "", "error": "ASSEMBLYAI_API_KEY not set on the server"}
    req = urllib.request.Request("https://streaming.assemblyai.com/v3/token?expires_in_seconds=600",
                                 headers={"Authorization": key})
    try:
        return {"token": json.load(urllib.request.urlopen(req, timeout=10))["token"]}
    except Exception as e:
        return {"token": "", "error": f"assemblyai: {e}"}


@api.get("/health")
async def health():
    return {
        "recognizable": sorted(NAMES),                                  # face + in gallery
        "awaiting_face": sorted(set(PROFILES) - set(NAMES)),            # profile only
        "face_but_no_profile": sorted(set(NAMES) - set(PROFILES)),      # id typo, usually
        "threshold": THRESHOLD, "det_size": DET_SIZE,
    }


if __name__ == "__main__":
    import socket
    import uvicorn
    lan = socket.gethostbyname(socket.gethostname())
    print(f"{len(NAMES)} enrolled: {', '.join(NAMES) or 'nobody'}")
    print(f"Quest posts to  http://{lan}:8000/id?frame_id=N   (NOT localhost)")
    uvicorn.run(api, host="0.0.0.0", port=8000, log_level="warning")
