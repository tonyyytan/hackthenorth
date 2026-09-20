import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import sounddevice as sd
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from assemblyai.streaming.v3 import (
    BeginEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    TerminationEvent,
    TurnEvent,
)

from google import genai
from google.genai import types

import pi_client


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not ASSEMBLYAI_API_KEY or not GEMINI_API_KEY:
    sys.exit(
        "Missing ASSEMBLYAI_API_KEY or GEMINI_API_KEY "
        "in environment / .env"
    )


# ============================================================
# AUDIO SETTINGS
# ============================================================

SAMPLE_RATE = 48000
CHANNELS = 1

# AssemblyAI accepts 50–1000 ms chunks.
CHUNK_MS = 50
BLOCKSIZE = SAMPLE_RATE * CHUNK_MS // 1000

# Maximum queued audio before dropping the oldest chunk.
MAX_BACKLOG_CHUNKS = 20


# ============================================================
# ASSEMBLYAI TURN DETECTION
# ============================================================

SPEECH_MODEL = "universal-streaming-english"

# Lower = end turns sooner.
END_OF_TURN_CONFIDENCE = 0.4

# Silence required once the model thinks the speaker is done.
MIN_SILENCE_WHEN_CONFIDENT_MS = 160

# Maximum silence before forcing the turn to end.
MAX_TURN_SILENCE_MS = 800


# ============================================================
# GEMINI SETTINGS
# ============================================================

GEMINI_MODEL = "gemini-3.6-flash-lite"
GEMINI_WORKERS = 4
MIN_WORDS_FOR_EXTRACTION = 2

gemini = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# GLOBALS
# ============================================================

audio_queue: "queue.Queue[bytes]" = queue.Queue(
    maxsize=MAX_BACKLOG_CHUNKS
)

print_lock = threading.Lock()


def log(*args, **kwargs):
    """Thread-safe printing."""
    with print_lock:
        print(*args, **kwargs, flush=True)


# ============================================================
# GEMINI OUTPUT SCHEMA
# ============================================================

class Introduction(BaseModel):
    name: Optional[str] = Field(
        description="The person's name, or null if not stated."
    )

    occupation: Optional[str] = Field(
        description=(
            "The person's job title, occupation, or status "
            "(for example CEO, student, freelancer, software engineer). "
            "Return null if not stated."
        )
    )

    affiliation: Optional[str] = Field(
        description=(
            "The company, school, team, or organization they belong to. "
            "Return null if not stated."
        )
    )


# ============================================================
# GEMINI SYSTEM INSTRUCTIONS
# ============================================================

SYSTEM_INSTRUCTION = """
You extract self-introduction details from short, possibly messy speech
transcripts.

The transcript is speech-to-text output and may contain filler words,
missing punctuation, or transcription errors.

Return a JSON object with exactly these keys:

"name"
"occupation"
"affiliation"

Rules:

- name: the person's name as spoken, in normal capitalization.
- occupation: their job title, occupation, or status.
- affiliation: the company, school, team, or organization they belong to.
- Only extract information that is explicitly stated.
- Never guess or infer information.
- If a field is not mentioned, return null.
- Never return "", "unknown", or "N/A".
- If the transcript is not an introduction at all, return all fields as null.
- If several people are mentioned, extract the primary speaker
  (the person using "I" or "my").
"""


GEN_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    response_mime_type="application/json",
    response_schema=Introduction,
    temperature=0,
    max_output_tokens=256,
    thinking_config=types.ThinkingConfig(
        thinking_level="minimal"
    ),
)


_NULLISH = {
    "",
    "null",
    "none",
    "unknown",
    "n/a",
    "na",
}


def _clean(value):
    """Normalize empty/null-like values to Python None."""
    if isinstance(value, str):
        value = value.strip()

        if value.lower() not in _NULLISH:
            return value

    return None


# ============================================================
# SEND GEMINI RESULT TO SERVER
# ============================================================

def send_to_server(server_url: str, data: dict):
    """
    Sends the Gemini result to the existing /utterance endpoint
    using pi_client.py.
    """

    try:
        payload_text = json.dumps(
            {
                "name": data.get("name"),
                "occupation": data.get("occupation"),
                "affiliation": data.get("affiliation"),
            }
        )

        result = pi_client.send_text(
            server_url,
            text=payload_text,
        )

        log(f"📡 Sent to server: {result}")

    except Exception as e:
        log(f"❌ Failed to send to server: {e}")


# ============================================================
# GEMINI EXTRACTION
# ============================================================

def extract(
    text: str,
    turn_id,
    turn_end_time: float,
    server_url: Optional[str],
):
    """Run Gemini on one completed speech turn."""

    start_time = time.perf_counter()

    try:
        response = gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Transcript:\n{text}",
            config=GEN_CONFIG,
        )

        if isinstance(response.parsed, Introduction):
            intro = response.parsed
        else:
            intro = Introduction.model_validate_json(
                response.text
            )

    except Exception as e:
        log(
            f"\n❌ Gemini error "
            f"(turn {turn_id}): {e}"
        )
        return

    data = {
        key: _clean(value)
        for key, value in intro.model_dump().items()
    }

    gemini_ms = (
        time.perf_counter() - start_time
    ) * 1000

    total_ms = (
        time.perf_counter() - turn_end_time
    ) * 1000

    # Nothing useful extracted.
    if not any(data.values()):
        log(
            f"   ↳ (turn {turn_id}) "
            f"no introduction "
            f"[{gemini_ms:.0f} ms]"
        )
        return

    # Print locally.
    log(
        f"🤖 (turn {turn_id}) "
        f"{json.dumps(data)} "
        f"[Gemini {gemini_ms:.0f} ms | "
        f"turn→result {total_ms:.0f} ms]"
    )

    # Send to the other computer.
    if server_url:
        send_to_server(
            server_url,
            data,
        )


# ============================================================
# GEMINI WARM-UP
# ============================================================

def warm_up_gemini():
    """Validate the Gemini model before the first real request."""

    try:
        gemini.models.get(
            model=GEMINI_MODEL
        )

        log("🔥 Gemini ready")

    except Exception as e:
        log(
            f"⚠️ Gemini warm-up failed "
            f"(check GEMINI_MODEL): {e}"
        )


# ============================================================
# AUDIO CALLBACK
# ============================================================

def audio_callback(
    indata,
    frames,
    time_info,
    status,
):
    """
    Keep the callback extremely small.

    Audio is copied to a queue and processed
    outside the PortAudio callback thread.
    """

    if status:
        log(f"\n⚠️ {status}")

    chunk = indata.tobytes()

    try:
        audio_queue.put_nowait(chunk)

    except queue.Full:

        # Drop the oldest chunk so we remain close to real time.
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            audio_queue.put_nowait(chunk)
        except queue.Full:
            pass


# ============================================================
# AUDIO GENERATOR FOR ASSEMBLYAI
# ============================================================

def audio_chunks(
    stop_event: threading.Event,
):
    """
    Generator that AssemblyAI consumes.

    A timeout keeps Ctrl+C responsive.
    """

    while not stop_event.is_set():

        try:
            yield audio_queue.get(
                timeout=0.1
            )

        except queue.Empty:
            continue


# ============================================================
# MAIN
# ============================================================

def main():
    log("🎤 Starting live audio pipeline...")

    # --------------------------------------------------------
    # Discover server on the LAN
    # --------------------------------------------------------

    server_url = None

    try:
        log("🔎 Searching for server.py on the LAN...")

        server_url = pi_client.discover(
            timeout=15.0
        )

        log(
            f"📡 Found server: "
            f"{server_url}"
        )

    except TimeoutError as e:
        log(
            f"⚠️ Server discovery failed: {e}"
        )

        log(
            "⚠️ Transcription will continue "
            "without sending results."
        )

    # --------------------------------------------------------
    # Gemini worker pool
    # --------------------------------------------------------

    executor = ThreadPoolExecutor(
        max_workers=GEMINI_WORKERS,
        thread_name_prefix="gemini",
    )

    executor.submit(
        warm_up_gemini
    )

    # --------------------------------------------------------
    # Runtime state
    # --------------------------------------------------------

    stop_event = threading.Event()
    seen_turns = set()

    # --------------------------------------------------------
    # AssemblyAI client
    # --------------------------------------------------------

    client = StreamingClient(
        StreamingClientOptions(
            api_key=ASSEMBLYAI_API_KEY
        )
    )

    # --------------------------------------------------------
    # AssemblyAI callbacks
    # --------------------------------------------------------

    def on_begin(
        client,
        event: BeginEvent,
    ):
        log(
            f"🔗 AssemblyAI connected "
            f"(session {event.id})"
        )

    def on_turn(
        client,
        event: TurnEvent,
    ):
        transcript = (
            event.transcript or ""
        ).strip()

        # ----------------------------------------------------
        # Partial transcript
        # ----------------------------------------------------

        if not event.end_of_turn:

            if transcript:

                with print_lock:
                    print(
                        f"\r\033[K👂 "
                        f"{transcript[-110:]}",
                        end="",
                        flush=True,
                    )

            return

        # ----------------------------------------------------
        # Final transcript
        # ----------------------------------------------------

        turn_id = getattr(
            event,
            "turn_order",
            None,
        )

        if not transcript:
            return

        if turn_id in seen_turns:
            return

        seen_turns.add(turn_id)

        turn_end_time = (
            time.perf_counter()
        )

        log(
            f"\r\033[K📝 "
            f"{transcript}"
        )

        # Skip meaningless short utterances.
        if (
            len(transcript.split())
            < MIN_WORDS_FOR_EXTRACTION
        ):
            return

        # ----------------------------------------------------
        # Run Gemini in worker thread
        # ----------------------------------------------------

        executor.submit(
            extract,
            transcript,
            turn_id,
            turn_end_time,
            server_url,
        )

    def on_terminated(
        client,
        event: TerminationEvent,
    ):
        log(
            f"🔚 Session ended "
            f"({event.audio_duration_seconds:.1f}s "
            f"of audio)"
        )

    def on_error(
        client,
        error: StreamingError,
    ):
        log(
            f"\n❌ AssemblyAI error: "
            f"{error}"
        )

    # --------------------------------------------------------
    # Register callbacks
    # --------------------------------------------------------

    client.on(
        StreamingEvents.Begin,
        on_begin,
    )

    client.on(
        StreamingEvents.Turn,
        on_turn,
    )

    client.on(
        StreamingEvents.Termination,
        on_terminated,
    )

    client.on(
        StreamingEvents.Error,
        on_error,
    )

    # --------------------------------------------------------
    # Connect to AssemblyAI
    # --------------------------------------------------------

    client.connect(
        StreamingParameters(
            sample_rate=SAMPLE_RATE,
            encoding="pcm_s16le",
            speech_model=SPEECH_MODEL,
            format_turns=False,
            end_of_turn_confidence_threshold=(
                END_OF_TURN_CONFIDENCE
            ),
            min_end_of_turn_silence_when_confident=(
                MIN_SILENCE_WHEN_CONFIDENT_MS
            ),
            max_turn_silence=(
                MAX_TURN_SILENCE_MS
            ),
        )
    )

    # --------------------------------------------------------
    # Start microphone
    # --------------------------------------------------------

    try:

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCKSIZE,
            latency="low",
            callback=audio_callback,
        ):

            log(
                "🎧 Listening... "
                "(Ctrl+C to stop)"
            )

            # AssemblyAI continuously pulls
            # audio from our generator.
            client.stream(
                audio_chunks(stop_event)
            )

    except KeyboardInterrupt:

        log(
            "\n🛑 Stopping..."
        )

    finally:

        stop_event.set()

        try:
            client.disconnect(
                terminate=True
            )
        except Exception:
            pass

        executor.shutdown(
            wait=False,
            cancel_futures=True,
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()

