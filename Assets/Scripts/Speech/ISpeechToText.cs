using System;

namespace HackTheNorth.Speech
{
    public interface ISpeechToText
    {
        void SubmitAudio(float[] samples, int sampleRate);

        /// <summary>
        /// Fires with (transcript, speakerLabel, isFinal). isFinal distinguishes a settled
        /// result from a live partial. speakerLabel identifies which acoustic voice said it
        /// ("A", "B", ... or null if the backend doesn't support diarization) — a raw voice
        /// identity, not a role; see CaptionPipeline for how the first-heard speaker gets
        /// treated as "the wearer."
        /// </summary>
        event Action<string, string, bool> OnTranscript;
    }
}
