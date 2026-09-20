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
    /// flow: audio in, transcript out, LLM result shown in the caption box. The box never shows
    /// raw transcript text — it's a "here's what I found" HUD surfacing researched info about
    /// the topic/person/object the wearer asked about, not a live captioning tool.
    ///
    /// speechToTextSource/llmClientSource are typed as MonoBehaviour (Unity can't serialize
    /// interface references directly) but must implement ISpeechToText/ILlmClient — drag in
    /// StubSpeechToText, AssemblyAiStreamingSpeechToText, StubLlmClient, or any future
    /// implementation. RequireInterface() logs an error if the wrong type is assigned.
    ///
    /// Wearer-vs-other-speaker routing: with only one microphone, there's no separate audio
    /// channel for "the other person" — AssemblyAI's diarization (speaker_label: "A", "B", ...)
    /// distinguishes acoustic voices, but has no idea which one is the wearer. The heuristic
    /// here: the FIRST speaker label to produce a substantial utterance (>= MinWordsToLockIdentity
    /// words) is assumed to be the wearer. Only their speech triggers the LLM-query flow (a
    /// question about someone/something); any OTHER speaker's speech is ignored entirely — it's
    /// not a question to look up, and the box doesn't caption it.
    ///
    /// Real, live-tested limitation: diarization over-segments in practice — confirmed live with
    /// two real people talking, AssemblyAI reported 3 distinct speakers ("A", "B", "C") for what
    /// was really 2 people, almost certainly a short noise/echo blip misclassified as its own
    /// speaker. The word-count gate exists specifically so a one-word phantom blip can't hijack
    /// the wearer identity — but it's a mitigation, not a fix for the underlying diarization
    /// accuracy limit, and this heuristic will still misfire if someone else genuinely produces
    /// a substantial utterance before the wearer does in a given session.
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
        private string wearerSpeakerLabel;
        private const int MinWordsToLockIdentity = 2;

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

        /// <summary>Resets the wearer-identity heuristic (e.g. for a fresh session/testing).</summary>
        public void ResetWearerIdentity()
        {
            wearerSpeakerLabel = null;
        }

        private void HandleAudioFrame(float[] samples)
        {
            SpeechToText?.SubmitAudio(samples, micCapture.SampleRate);
        }

        private void HandleTranscript(string transcript, string speakerLabel, bool isFinal)
        {
            if (!isFinal) return;

            bool hasRealLabel = !string.IsNullOrEmpty(speakerLabel)
                && speakerLabel != "PENDING" && speakerLabel != "UNKNOWN";

            bool isWearer;
            if (!hasRealLabel)
            {
                isWearer = true; // diarization not available/not yet resolved for this turn
            }
            else if (wearerSpeakerLabel == null)
            {
                // Don't let a short phantom blip (see class doc) claim the wearer identity —
                // wait for a substantial utterance before locking it in.
                int wordCount = transcript.Split(' ', System.StringSplitOptions.RemoveEmptyEntries).Length;
                if (wordCount >= MinWordsToLockIdentity)
                {
                    wearerSpeakerLabel = speakerLabel;
                    isWearer = true;
                }
                else
                {
                    isWearer = true; // treat unattributed short first utterance as the wearer's, but don't lock it in
                }
            }
            else
            {
                isWearer = speakerLabel == wearerSpeakerLabel;
            }

            if (isWearer)
            {
                // The box shows researched info about what was asked, not the raw transcript —
                // it's a "here's what I found" HUD, not a captioning tool. Show a placeholder
                // immediately (speech-to-LLM round trip isn't instant) then swap in the real
                // answer when it arrives.
                captionBox.ShowDialogue("Researching", "...");
                LlmClient?.Query(transcript, result => captionBox.UpdateMessage(result));
            }
            // Someone else's speech isn't captioned — the box only surfaces researched
            // answers to the wearer's own questions, never raw speech from either party.
        }
    }
}
