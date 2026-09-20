import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import sounddevice as sd
from dotenv import load_dotenv

import pi_client

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


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")

if not ASSEMBLYAI_API_KEY:
    sys.exit(
        "Missing ASSEMBLYAI_API_KEY in environment / .env"
    )


# ============================================================
# SERVER CONFIGURATION
# ============================================================

# pi_client.py automatically discovers server.py over the LAN.
# You can optionally set:
#
# SERVER_URL=http://192.168.x.x:8000
#
# in .env to skip UDP discovery.

SERVER_DISCOVERY_TIMEOUT = 15.0


# ============================================================
# AUDIO SETTINGS
# ============================================================

SAMPLE_RATE = 48000
CHANNELS = 1

# AssemblyAI accepts 50–1000 ms chunks.
CHUNK_MS = 50

BLOCKSIZE = (
    SAMPLE_RATE * CHUNK_MS // 1000
)

MAX_AUDIO_QUEUE = 20


# ============================================================
# ASSEMBLYAI SETTINGS
# ============================================================

SPEECH_MODEL = "universal-streaming-english"

# Expect two microphones / speakers.
MAX_SPEAKERS = 2

# End-of-turn detection.
END_OF_TURN_CONFIDENCE = 0.4
MIN_SILENCE_WHEN_CONFIDENT_MS = 160
MAX_TURN_SILENCE_MS = 800


# ============================================================
# AUDIO QUEUE
# ============================================================

audio_queue: "queue.Queue[bytes]" = queue.Queue(
    maxsize=MAX_AUDIO_QUEUE
)


# ============================================================
# OUTPUT LOCK
# ============================================================

print_lock = threading.Lock()


def log(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs, flush=True)


# ============================================================
# SPEAKER MAPPING
# ============================================================

def speaker_name(label: Optional[str]) -> str:
    """
    Convert AssemblyAI speaker labels to friendly names.

    A -> Speaker 1
    B -> Speaker 2
    """

    if label is None:
        return "Speaker ?"

    label = str(label).upper()

    if label == "A":
        return "Speaker 1"

    if label == "B":
        return "Speaker 2"

    return f"Speaker {label}"


def speaker_id(label: Optional[str]) -> Optional[str]:
    """
    Convert AssemblyAI speaker labels into stable IDs
    sent to server.py.
    """

    if label is None:
        return None

    label = str(label).upper()

    if label == "A":
        return "SPEAKER_1"

    if label == "B":
        return "SPEAKER_2"

    return f"SPEAKER_{label}"


# ============================================================
# SEND TRANSCRIPT TO SERVER
# ============================================================

def send_transcript(
    server_url: str,
    transcript: str,
    label: Optional[str],
):
    """
    Send one completed transcript turn to server.py
    using the existing pi_client.py module.
    """

    speaker = speaker_name(label)
    person = speaker_id(label)

    message = f"[{speaker}] {transcript}"

    try:
        result = pi_client.send_text(
            server_url,
            text=message,
            person_id=person,
        )

        log(
            f"📡 Sent {speaker} to server: "
            f"{result}"
        )

    except Exception as e:
        log(
            f"❌ Failed to send {speaker} "
            f"to server: {e}"
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
    Keep the PortAudio callback lightweight.
    """

    if status:
        log(f"\n⚠️ Audio: {status}")

    chunk = indata.tobytes()

    try:
        audio_queue.put_nowait(chunk)

    except queue.Full:

        # Drop the oldest audio to remain near real time.
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass

        try:
            audio_queue.put_nowait(chunk)
        except queue.Full:
            pass


# ============================================================
# AUDIO GENERATOR
# ============================================================

def audio_chunks(
    stop_event: threading.Event,
):
    """
    Generator consumed by AssemblyAI.
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

    log(
        "🎤 Starting "
        "Raspberry Pi → AssemblyAI → Server pipeline..."
    )

    # ========================================================
    # DISCOVER SERVER
    # ========================================================

    try:
        server_url = pi_client.discover(
            timeout=SERVER_DISCOVERY_TIMEOUT
        )

    except TimeoutError as e:
        log(f"❌ {e}")
        sys.exit(1)

    log(
        f"📡 Server discovered at: "
        f"{server_url}"
    )

    # ========================================================
    # THREADS
    # ========================================================

    stop_event = threading.Event()

    # Sending HTTP requests can take some time.
    # Keep them outside the AssemblyAI callback thread.
    server_executor = ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="server",
    )

    # Prevent printing/sending the same completed
    # turn twice.
    seen_turns = set()

    # ========================================================
    # ASSEMBLYAI CLIENT
    # ========================================================

    client = StreamingClient(
        StreamingClientOptions(
            api_key=ASSEMBLYAI_API_KEY
        )
    )

    # ========================================================
    # ASSEMBLYAI CALLBACKS
    # ========================================================

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

        if not transcript:
            return

        # ----------------------------------------------------
        # Speaker
        # ----------------------------------------------------

        label = getattr(
            event,
            "speaker_label",
            None,
        )

        speaker = speaker_name(label)

        # ----------------------------------------------------
        # Partial transcript
        # ----------------------------------------------------

        if not event.end_of_turn:

            with print_lock:
                print(
                    f"\r\033[K"
                    f"👂 [{speaker}] "
                    f"{transcript[-120:]}",
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

        if turn_id in seen_turns:
            return

        seen_turns.add(turn_id)

        log(
            f"\r\033[K"
            f"📝 [{speaker}] "
            f"{transcript}"
        )

        # ----------------------------------------------------
        # SEND TO SERVER
        # ----------------------------------------------------

        # Submit the HTTP request in another thread so
        # the AssemblyAI streaming callback is never blocked.
        server_executor.submit(
            send_transcript,
            server_url,
            transcript,
            label,
        )

    def on_terminated(
        client,
        event: TerminationEvent,
    ):
        log(
            f"🔚 AssemblyAI session ended "
            f"after "
            f"{event.audio_duration_seconds:.1f}s"
        )

    def on_error(
        client,
        error: StreamingError,
    ):
        log(
            f"\n❌ AssemblyAI error: "
            f"{error}"
        )

    # ========================================================
    # REGISTER CALLBACKS
    # ========================================================

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

    # ========================================================
    # CONNECT TO ASSEMBLYAI
    # ========================================================

    client.connect(
        StreamingParameters(
            sample_rate=SAMPLE_RATE,
            encoding="pcm_s16le",
            speech_model=SPEECH_MODEL,

            # Speaker diarization
            speaker_labels=True,
            max_speakers=MAX_SPEAKERS,

            # Turn detection
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

    # ========================================================
    # START MICROPHONE
    # ========================================================

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
                "🎧 Listening for "
                "Speaker 1 and Speaker 2..."
            )

            log(
                "📡 Every completed transcript "
                "will be sent to server.py."
            )

            log(
                "Press Ctrl+C to stop.\n"
            )

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

        server_executor.shutdown(
            wait=True,
            cancel_futures=False,
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
