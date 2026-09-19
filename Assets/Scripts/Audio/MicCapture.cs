using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Android;

namespace HackTheNorth.Audio
{
    /// <summary>
    /// Wraps Unity's Microphone API into a polling ring-buffer reader. Fires OnAudioFrame
    /// with newly captured PCM samples each frame, and exposes a naive RMS-threshold
    /// IsSpeaking flag. This VAD is a placeholder good enough for a demo, not production
    /// quality — swap for a real voice-activity detector if false triggers are a problem.
    /// </summary>
    public class MicCapture : MonoBehaviour
    {
        [SerializeField] private int sampleRate = 16000;
        [SerializeField] private int clipLengthSeconds = 10;
        [SerializeField] private float speechRmsThreshold = 0.02f;

        public event Action<float[]> OnAudioFrame;
        public bool IsSpeaking { get; private set; }

        public int SampleRate => sampleRate;

        private AudioClip micClip;
        private string micDevice;
        private int readPosition;
        private float[] frameBuffer;

        private IEnumerator Start()
        {
            // Quest: RECORD_AUDIO is a runtime permission. Without it Microphone.devices is
            // empty and the whole voice pipeline dies silently.
            // ponytail: poll the permission instead of the callback API; it's a one-time
            // startup dialog, and the callback version is three more types for no gain.
            if (!Permission.HasUserAuthorizedPermission(Permission.Microphone))
            {
                Permission.RequestUserPermission(Permission.Microphone);
                while (!Permission.HasUserAuthorizedPermission(Permission.Microphone)) yield return null;
            }

            if (Microphone.devices.Length == 0)
            {
                Debug.LogWarning("MicCapture: no microphone devices found.");
                enabled = false;
                yield break;
            }

            micDevice = Microphone.devices[0];
            micClip = Microphone.Start(micDevice, true, clipLengthSeconds, sampleRate);
            Debug.Log($"MicCapture: recording from '{micDevice}' at {sampleRate}Hz.");
        }

        private void Update()
        {
            if (micClip == null) return;

            int writePosition = Microphone.GetPosition(micDevice);
            if (writePosition == readPosition) return;

            int sampleCount = writePosition - readPosition;
            if (sampleCount < 0) sampleCount += micClip.samples; // ring buffer wrapped

            if (frameBuffer == null || frameBuffer.Length != sampleCount)
            {
                frameBuffer = new float[sampleCount];
            }

            micClip.GetData(frameBuffer, readPosition);
            readPosition = writePosition;

            IsSpeaking = ComputeRms(frameBuffer) > speechRmsThreshold;
            OnAudioFrame?.Invoke(frameBuffer);
        }

        private void OnDestroy()
        {
            if (micDevice != null && Microphone.IsRecording(micDevice))
            {
                Microphone.End(micDevice);
            }
        }

        private static float ComputeRms(float[] samples)
        {
            if (samples.Length == 0) return 0f;

            double sumSquares = 0;
            for (int i = 0; i < samples.Length; i++)
            {
                sumSquares += samples[i] * samples[i];
            }
            return Mathf.Sqrt((float)(sumSquares / samples.Length));
        }
    }
}
