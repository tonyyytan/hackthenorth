using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using HackTheNorth.Identity;
using UnityEngine;
using UnityEngine.Networking;

namespace HackTheNorth.Speech
{
    /// <summary>
    /// ISpeechToText backed by server.py: cuts mic audio into utterances on silence, POSTs each
    /// as a WAV to /utterance, and fires OnTranscript with the text the server returns. The
    /// server transcribes it (OpenAI) and hands the audio itself to OMNI along with the photo of
    /// whoever is in frame, so the insight lands in that person's caption box via FaceIdClient.
    /// </summary>
    public class ServerSpeechToText : MonoBehaviour, ISpeechToText
    {
        [Tooltip("Shares its serverUrl, so the address is set in one place.")]
        [SerializeField] private FaceIdClient faceIdClient;
        [SerializeField] private float speechRmsThreshold = 0.02f;
        [Tooltip("Seconds of quiet that end an utterance.")]
        [SerializeField] private float endSilence = 0.7f;
        [SerializeField] private float minSeconds = 0.5f;
        [SerializeField] private float maxSeconds = 15f;

        public event Action<string, bool> OnTranscript;

        private readonly List<float> utterance = new();
        private float quietFor;

        public void SubmitAudio(float[] samples, int sampleRate)
        {
            bool loud = Rms(samples) > speechRmsThreshold;
            quietFor = loud ? 0f : quietFor + samples.Length / (float)sampleRate;
            if (loud || utterance.Count > 0) utterance.AddRange(samples); // copies; MicCapture reuses its buffer

            bool ended = utterance.Count > 0 && quietFor >= endSilence;
            if (!ended && utterance.Count < maxSeconds * sampleRate) return;

            if (utterance.Count >= minSeconds * sampleRate) StartCoroutine(Post(Wav(utterance, sampleRate)));
            utterance.Clear();
        }

        private IEnumerator Post(byte[] wav)
        {
            string body = JsonUtility.ToJson(new Request { audio_b64 = Convert.ToBase64String(wav) });
            using var req = new UnityWebRequest($"{faceIdClient.ServerUrl}/utterance", "POST")
            {
                uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body)) { contentType = "application/json" },
                downloadHandler = new DownloadHandlerBuffer(),
            };
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"ServerSpeechToText: /utterance failed: {req.error}");
                yield break;
            }
            var res = JsonUtility.FromJson<Response>(req.downloadHandler.text);
            if (!string.IsNullOrEmpty(res.error)) Debug.LogWarning($"ServerSpeechToText: {res.error}");
            if (!string.IsNullOrEmpty(res.text)) OnTranscript?.Invoke(res.text, true);
        }

        // 16-bit mono PCM WAV: the format both OpenAI STT and OMNI accept.
        private static byte[] Wav(List<float> samples, int sampleRate)
        {
            using var ms = new MemoryStream();
            using var w = new BinaryWriter(ms);
            int dataBytes = samples.Count * 2;
            w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + dataBytes);
            w.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); w.Write(16);
            w.Write((short)1); w.Write((short)1); w.Write(sampleRate); w.Write(sampleRate * 2);
            w.Write((short)2); w.Write((short)16);
            w.Write(Encoding.ASCII.GetBytes("data")); w.Write(dataBytes);
            foreach (float s in samples) w.Write((short)(Mathf.Clamp(s, -1f, 1f) * short.MaxValue));
            w.Flush();
            return ms.ToArray();
        }

        private static float Rms(float[] samples)
        {
            if (samples.Length == 0) return 0f;
            double sum = 0;
            foreach (float s in samples) sum += s * s;
            return Mathf.Sqrt((float)(sum / samples.Length));
        }

        [Serializable] private class Request { public string audio_b64; }
        [Serializable] private class Response { public string person_id, text, error; public bool fired; }
    }
}
