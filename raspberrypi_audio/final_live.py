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
# SERVER SETTINGS
# ============================================================

# pi_client.py automatically discovers server.py
# over the LAN using UDP broadcast.

SERVER_DISCOVERY_TIMEOUT = 15.0


# ============================================================
# AUDIO SETTINGS
# ============================================================

SAMPLE_RATE = 48000
CHANNELS = 1

# AssemblyAI accepts 50-1000 ms audio chunks.
CHUNK_MS = 50

BLOCKSIZE = (
    SAMPLE_RATE * CHUNK_MS // 1000
)

MAX_AUDIO_QUEUE = 20


# ============================================================
# ASSEMBLYAI SETTINGS
# ============================================================

SPEECH_MODEL = "universal-streaming-english"

# Turn detection
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
# AUDIO CALLBACK
# ============================================================

def audio_callback(
    indata,
    frames,
    time_info,
    status,
):
    """
    Keep the PortAudio callback extremely lightweight.

    The callback only copies the microphone audio
    into the queue. Network operations happen elsewhere.
    """

    if status:
        print(
            f"\n⚠️ Audio: {status}",
            flush=True,
        )

    chunk = indata.tobytes()

    try:
        audio_queue.put_nowait(chunk)

    except queue.Full:
        # Drop the oldest chunk to stay near real time.
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

    It continuously provides microphone audio
    as it becomes available.
    """

    while not stop_event.is_set():

        try:
            yield audio_queue.get(
                timeout=0.1
            )

        except queue.Empty:
            continue


# ============================================================
# SEND TRANSCRIPT TO SERVER
# ============================================================

def send_transcript(
    server_url: str,
    transcript: str,
):
    """
    Send ONLY the transcript text to server.py.

    No speaker ID.
    No person ID.
    No Gemini result.
    """

    try:
        result = pi_client.send_text(
            server_url,
            text=transcript,
        )

        print(
            f"📡 Sent to server: {result}",
            flush=True,
        )

    except Exception as e:
        print(
            f"❌ Failed to send transcript: {e}",
            flush=True,
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "🎤 Starting Raspberry Pi → AssemblyAI → Server",
        flush=True,
    )

    print(
        "Only transcript text will be sent.",
        flush=True,
    )


    # ========================================================
    # DISCOVER SERVER
    # ========================================================

    try:

        server_url = pi_client.discover(
            timeout=SERVER_DISCOVERY_TIMEOUT
        )

    except TimeoutError as e:

        print(
            f"❌ {e}",
            flush=True,
        )

        sys.exit(1)

    print(
        f"📡 Server found at: {server_url}",
        flush=True,
    )


    # ========================================================
    # STOP EVENT
    # ========================================================

    stop_event = threading.Event()


    # ========================================================
    # SERVER THREAD POOL
    # ========================================================

    # Sending an HTTP request can take some time.
    # We don't want that to block AssemblyAI's streaming.
    server_executor = ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="server",
    )


    # ========================================================
    # ASSEMBLYAI CLIENT
    # ========================================================

    client = StreamingClient(
        StreamingClientOptions(
            api_key=ASSEMBLYAI_API_KEY
        )
    )


    # ========================================================
    # CALLBACKS
    # ========================================================

    def on_begin(
        client,
        event: BeginEvent,
    ):
        print(
            f"🔗 AssemblyAI connected "
            f"(session {event.id})",
            flush=True,
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
        # PARTIAL TRANSCRIPT
        # ----------------------------------------------------

        if not event.end_of_turn:

            print(
                f"\r\033[K👂 {transcript[-120:]}",
                end="",
                flush=True,
            )

            return


        # ----------------------------------------------------
        # FINAL TRANSCRIPT
        # ----------------------------------------------------

        turn_id = getattr(
            event,
            "turn_order",
            None,
        )

        print(
            f"\r\033[K📝 {transcript}",
            flush=True,
        )


        # ----------------------------------------------------
        # SEND ONLY THE TEXT
        # ----------------------------------------------------

        server_executor.submit(
            send_transcript,
            server_url,
            transcript,
        )


    def on_terminated(
        client,
        event: TerminationEvent,
    ):
        print(
            f"🔚 AssemblyAI session ended "
            f"after "
            f"{event.audio_duration_seconds:.1f}s",
            flush=True,
        )


    def on_error(
        client,
        error: StreamingError,
    ):
        print(
            f"\n❌ AssemblyAI error: {error}",
            flush=True,
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

            print(
                "🎧 Listening...",
                flush=True,
            )

            print(
                "📝 Final transcripts are sent "
                "to server.py.",
                flush=True,
            )

            print(
                "Press Ctrl+C to stop.\n",
                flush=True,
            )

            # AssemblyAI continuously pulls
            # audio from this generator.
            client.stream(
                audio_chunks(stop_event)
            )


    except KeyboardInterrupt:

        print(
            "\n🛑 Stopping...",
            flush=True,
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