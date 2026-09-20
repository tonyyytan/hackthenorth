import os
import queue
import threading
import sounddevice as sd
from dotenv import load_dotenv

import assemblyai as aai
from google import genai

# ======================
# ENV
# ======================
load_dotenv()

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini = genai.Client(api_key=GEMINI_API_KEY)

# ======================
# SETTINGS
# ======================
SAMPLE_RATE = 48000
CHANNELS = 1
BLOCKSIZE = 8000

audio_queue = queue.Queue(maxsize=100)

# ======================
# CALLBACK (LIGHTWEIGHT ONLY)
# ======================
def audio_callback(indata, frames, time, status):
    if status:
        print("⚠️", status)

    try:
        audio_queue.put_nowait(indata.copy())
    except queue.Full:
        pass  # drop audio instead of crashing

# ======================
# GEMINI
# ======================
def extract(text):
    prompt = f"""
Extract ONLY:
- name
- occupation

Return JSON only.

Text:
{text}
"""

    try:
        res = gemini.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        print("\n🤖 GEMINI:", res.text)

    except Exception as e:
        print("❌ Gemini Error:", e)

# ======================
# MAIN
# ======================
def main():
    print("🎤 Stable streaming pipeline...")

    transcriber = aai.streaming.v3.RealTimeTranscriber(
        aai.streaming.v3.RealTimeTranscriberOptions(
            api_key=ASSEMBLYAI_API_KEY
        )
    )

    def on_turn(client, event):
        # Print partial transcripts on the same line so you know it's hearing you
        if not event.end_of_turn:
            if event.transcript:
                print(f"👂 {event.transcript}", end="\r")
            return

        # V3 uses `end_of_turn` to indicate a finalized thought
        if event.end_of_turn and event.transcript:
            text = event.transcript.strip()
            print(f"\n📝 {text}")

            # async Gemini
            threading.Thread(
                target=extract,
                args=(text,),
                daemon=True
            ).start()

    def on_begin(client, event):
        print("🔗 Connected")

    def on_error(client, error):
        print("\n❌ AssemblyAI Error:", error)

    transcriber.on(aai.streaming.v3.RealTimeEvents.Begin, on_begin)
    transcriber.on(aai.streaming.v3.RealTimeEvents.Turn, on_turn)
    transcriber.on(aai.streaming.v3.RealTimeEvents.Error, on_error)

    transcriber.connect(
        aai.streaming.v3.RealTimeParameters(
            sample_rate=SAMPLE_RATE,
            speech_model="universal-streaming-english"
        )
    )

    # ======================
    # AUDIO STREAM
    # ======================
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        blocksize=BLOCKSIZE,
        callback=audio_callback
    ):
        print("🎧 Listening...")

        try:
            while True:
                data = audio_queue.get()

                # THIS is where streaming happens (NOT callback)
                transcriber.stream(data.tobytes())

        except KeyboardInterrupt:
            print("\n🛑 Stopping...")
            transcriber.disconnect()

if __name__ == "__main__":
    main()
