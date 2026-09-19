using System;

namespace HackTheNorth.Speech
{
    public interface ISpeechToText
    {
        void SubmitAudio(float[] samples, int sampleRate);

        /// <summary>Fires with recognized text. isFinal distinguishes a settled result from a live partial.</summary>
        event Action<string, bool> OnTranscript;
    }
}
