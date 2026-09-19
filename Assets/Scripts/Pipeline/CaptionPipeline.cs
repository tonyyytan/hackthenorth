using HackTheNorth.Audio;
using HackTheNorth.LLM;
using HackTheNorth.Speech;
using HackTheNorth.UI;
using UnityEngine;
using UnityEngine.Serialization;

namespace HackTheNorth.Pipeline
{
    /// <summary>
    /// Wires MicCapture -> ISpeechToText -> ILlmClient -> SpeakerCaptionBox into one demo
    /// flow: audio in, transcript out, LLM result shown in the caption box.
    ///
    /// speechToTextSource/llmClientSource are typed as MonoBehaviour (Unity can't serialize
    /// interface references directly) but must implement ISpeechToText/ILlmClient — drag in
    /// StubSpeechToText, AssemblyAiStreamingSpeechToText, StubLlmClient, or any future
    /// implementation. RequireInterface() logs an error if the wrong type is assigned.
    /// </summary>
    public class CaptionPipeline : MonoBehaviour
    {
        [SerializeField] private MicCapture micCapture;
        [FormerlySerializedAs("speechToText")]
        [SerializeField] private MonoBehaviour speechToTextSource;
        [FormerlySerializedAs("llmClient")]
        [SerializeField] private MonoBehaviour llmClientSource;
        [SerializeField] private SpeakerCaptionBox captionBox;

        private ISpeechToText SpeechToText => speechToTextSource as ISpeechToText;
        private ILlmClient LlmClient => llmClientSource as ILlmClient;
        private bool isBound;

        private void OnEnable() => Rebind();
        private void OnDisable() => Unbind();

        /// <summary>
        /// (Re)subscribes to the current micCapture/speechToTextSource references. Call this
        /// explicitly after assigning those fields via editor tooling (e.g. SerializedObject
        /// in a builder script) — AddComponent() already runs OnEnable synchronously before
        /// such tooling gets a chance to set the fields, so the automatic OnEnable subscription
        /// happens against nulls and silently does nothing.
        /// </summary>
        public void Rebind()
        {
            Unbind();

            if (speechToTextSource != null && SpeechToText == null)
            {
                Debug.LogError($"CaptionPipeline: speechToTextSource '{speechToTextSource.GetType().Name}' does not implement ISpeechToText.");
            }
            if (llmClientSource != null && LlmClient == null)
            {
                Debug.LogError($"CaptionPipeline: llmClientSource '{llmClientSource.GetType().Name}' does not implement ILlmClient.");
            }

            if (micCapture != null) micCapture.OnAudioFrame += HandleAudioFrame;
            if (SpeechToText != null) SpeechToText.OnTranscript += HandleTranscript;
            isBound = true;
        }

        private void Unbind()
        {
            if (!isBound) return;
            if (micCapture != null) micCapture.OnAudioFrame -= HandleAudioFrame;
            if (SpeechToText != null) SpeechToText.OnTranscript -= HandleTranscript;
            isBound = false;
        }

        private void HandleAudioFrame(float[] samples)
        {
            SpeechToText?.SubmitAudio(samples, micCapture.SampleRate);
        }

        private void HandleTranscript(string transcript, bool isFinal)
        {
            if (!isFinal) return;

            captionBox.ShowDialogue("You said", transcript);

            // Wiring point: once the segmentation-anchoring system exists, route this to the
            // specific TrackedTarget's caption box instead of the single fixed captionBox.
            LlmClient?.Query(transcript, result => captionBox.UpdateMessage(result));
        }
    }
}
