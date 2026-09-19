using HackTheNorth.Audio;
using HackTheNorth.LLM;
using HackTheNorth.Speech;
using HackTheNorth.UI;
using UnityEngine;

namespace HackTheNorth.Pipeline
{
    /// <summary>
    /// Wires MicCapture -> ISpeechToText -> ILlmClient -> SpeakerCaptionBox into one demo
    /// flow: audio in, transcript out, LLM result shown in the caption box.
    /// </summary>
    public class CaptionPipeline : MonoBehaviour
    {
        [SerializeField] private MicCapture micCapture;
        [Tooltip("Any ISpeechToText: StubSpeechToText or ServerSpeechToText.")]
        [SerializeField] private MonoBehaviour speechToText;
        [Tooltip("Optional. Leave empty with ServerSpeechToText: the server's OMNI insight lands via FaceIdClient.")]
        [SerializeField] private StubLlmClient llmClient;
        [SerializeField] private SpeakerCaptionBox captionBox;

        private ISpeechToText SpeechToText => speechToText as ISpeechToText;
        private ILlmClient LlmClient => llmClient;
        private bool isBound;

        private void OnEnable() => Rebind();
        private void OnDisable() => Unbind();

        /// <summary>
        /// (Re)subscribes to the current micCapture/speechToText references. Call this
        /// explicitly after assigning those fields via editor tooling (e.g. SerializedObject
        /// in a builder script) — AddComponent() already runs OnEnable synchronously before
        /// such tooling gets a chance to set the fields, so the automatic OnEnable subscription
        /// happens against nulls and silently does nothing.
        /// </summary>
        public void Rebind()
        {
            Unbind();
            if (micCapture != null) micCapture.OnAudioFrame += HandleAudioFrame;
            if (speechToText != null) SpeechToText.OnTranscript += HandleTranscript;
            isBound = true;
        }

        private void Unbind()
        {
            if (!isBound) return;
            if (micCapture != null) micCapture.OnAudioFrame -= HandleAudioFrame;
            if (speechToText != null) SpeechToText.OnTranscript -= HandleTranscript;
            isBound = false;
        }

        private void HandleAudioFrame(float[] samples)
        {
            SpeechToText.SubmitAudio(samples, micCapture.SampleRate);
        }

        private void HandleTranscript(string transcript, bool isFinal)
        {
            if (!isFinal) return;

            captionBox.ShowDialogue("You said", transcript);

            // Wiring point: once the segmentation-anchoring system exists, route this to the
            // specific TrackedTarget's caption box instead of the single fixed captionBox.
            if (llmClient != null) LlmClient.Query(transcript, result => captionBox.UpdateMessage(result));
        }
    }
}
