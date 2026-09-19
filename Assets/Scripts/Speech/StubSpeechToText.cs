using System;
using UnityEngine;

namespace HackTheNorth.Speech
{
    /// <summary>
    /// Placeholder ISpeechToText that only logs. Swap this out for a real backend (e.g. Meta
    /// Voice SDK or a cloud STT API) by implementing SubmitAudio to send audio out and firing
    /// OnTranscript when a result comes back.
    /// </summary>
    public class StubSpeechToText : MonoBehaviour, ISpeechToText
    {
        public event Action<string, bool> OnTranscript;

        public void SubmitAudio(float[] samples, int sampleRate)
        {
            Debug.Log($"StubSpeechToText: received {samples.Length} samples @ {sampleRate}Hz (no real STT wired up yet).");
        }

        // Call this manually (e.g. from the Inspector or a test script) to simulate a result
        // arriving until a real STT backend is wired in.
        public void SimulateTranscript(string text, bool isFinal)
        {
            OnTranscript?.Invoke(text, isFinal);
        }
    }
}
