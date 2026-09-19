using System.Collections;
using System.Text;
using HackTheNorth.Audio;
using HackTheNorth.Identity;
using HackTheNorth.LLM;
using HackTheNorth.Speech;
using HackTheNorth.UI;
using UnityEngine;
using UnityEngine.Networking;
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
        [Tooltip("Optional. When set, final transcripts are sent to server.py /utterance for the per-person insight.")]
        [SerializeField] private FaceIdClient faceIdClient;

        private ISpeechToText SpeechToText => speechToTextSource as ISpeechToText;
        private ILlmClient LlmClient => llmClientSource as ILlmClient;
        private bool isBound;

        private void OnEnable() => Rebind();
        private void OnDisable() => Unbind();

        // Proof of life on device. This scene draws nothing of its own — the background is
        // passthrough and the caption box starts invisible — so a black headset can't be told
        // apart from a dead app. If you see this banner, the build is running and only the
        // voice path is broken.
        // ponytail: a 4s banner, not a debug HUD. Delete it once the demo is stable.
        private void Start()
        {
            if (captionBox != null) captionBox.ShowDialogue("Ready", "listening…");
        }

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

            // Finished sentences go to server.py, which files them under whoever is biggest in
            // frame and feeds that person's insight (OMNI, else Claude); the insight shows up in
            // their caption box on the next /id. ServerSpeechToText already posted its own audio.
            if (faceIdClient != null && !(SpeechToText is ServerSpeechToText))
                StartCoroutine(PostUtterance(transcript));

            // Wiring point: once the segmentation-anchoring system exists, route this to the
            // specific TrackedTarget's caption box instead of the single fixed captionBox.
            LlmClient?.Query(transcript, result => captionBox.UpdateMessage(result));
        }

        private IEnumerator PostUtterance(string text)
        {
            // No person_id: the server uses whoever is biggest in the latest frame.
            string body = JsonUtility.ToJson(new Utterance { text = text });
            using var req = new UnityWebRequest($"{faceIdClient.ServerUrl}/utterance", "POST")
            {
                uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body)) { contentType = "application/json" },
                downloadHandler = new DownloadHandlerBuffer(),
            };
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogWarning($"CaptionPipeline: /utterance failed: {req.error}");
        }

        [System.Serializable] private class Utterance { public string text; }
    }
}
