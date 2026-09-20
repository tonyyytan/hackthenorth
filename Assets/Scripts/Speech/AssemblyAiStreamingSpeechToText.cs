using System;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace HackTheNorth.Speech
{
    /// <summary>
    /// Real-time speech-to-text over AssemblyAI's Universal-Streaming API
    /// (wss://streaming.assemblyai.com/v3/ws). Feed it 16kHz mono PCM float samples via
    /// SubmitAudio (e.g. from MicCapture); OnTranscript fires on every "Turn" message from the
    /// server, with isFinal = the server's end_of_turn flag.
    ///
    /// API key: set the ASSEMBLYAI_API_KEY environment variable (preferred — never gets
    /// committed) or the apiKeyFallback field in the Inspector (only for local testing; do NOT
    /// commit a real key typed into a scene/prefab).
    ///
    /// Verified live this session: connects, receives Begin/SpeechStarted/Turn messages, and
    /// real speech transcribes correctly end-to-end onto SpeakerCaptionBox (audio must be
    /// re-chunked into 50-1000ms frames first — see SubmitAudio's comment, AssemblyAI's server
    /// rejects anything outside that window with error_code 3007).
    ///
    /// Diarization (speakerLabels=true, the default) distinguishes acoustic voices ("A", "B",
    /// ...) so CaptionPipeline can tell the wearer's voice apart from someone else's — see its
    /// comments for the wearer-identification heuristic. Not yet verified with two real
    /// speakers (only tested solo this session).
    /// </summary>
    public class AssemblyAiStreamingSpeechToText : MonoBehaviour, ISpeechToText
    {
        [Tooltip("Prefer the ASSEMBLYAI_API_KEY environment variable instead — this field is serialized into the scene/prefab and would leak into git if committed with a real key.")]
        [SerializeField] private string apiKeyFallback = "";
        [SerializeField] private int sampleRate = 16000;
        [SerializeField] private bool formatTurns = true;
        [Tooltip("min_latency prioritizes real-time responsiveness over transcription accuracy — right for a live voice-query use case.")]
        [SerializeField] private string mode = "min_latency";
        [Tooltip("Leave blank to let the server pick a default. Only set this if connecting without it fails (confirmed working blank in this session).")]
        [SerializeField] private string speechModel = "";
        [Tooltip("Distinguishes acoustic voices (wearer vs. another nearby person) via AssemblyAI's real-time diarization.")]
        [SerializeField] private bool speakerLabels = true;
        [Tooltip("Optional hint for diarization (1-10). 0 = let the server decide.")]
        [SerializeField] private int maxSpeakers = 0;

        public event Action<string, string, bool> OnTranscript;

        private ClientWebSocket socket;
        private CancellationTokenSource cts;
        private bool isConnecting;

        public int SampleRate => sampleRate;
        public int FramesSent => framesSent;

        private void OnEnable()
        {
            _ = ConnectAsync();
        }

        // Redundant with OnEnable by design: ConnectAsync() no-ops if already connect(ed/ing),
        // so this costs nothing, and Start()'s firing guarantees are more predictable than
        // OnEnable's across domain-reload/editor-tooling scenarios (observed empirically this
        // session: OnEnable did not reliably establish a connection after a script recompile or
        // when the component was added via the Unity MCP bridge's reflection-based AddComponent
        // — a manual re-trigger was needed both times. Not yet confirmed whether this also
        // affects a genuinely fresh Play Mode entry / on-device app launch, which is the
        // scenario that actually matters for real use).
        private void Start()
        {
            _ = ConnectAsync();
        }

        /// <summary>Manually (re)trigger a connection attempt — useful for testing via the Inspector or the Unity MCP bridge, where OnEnable's automatic firing can't always be relied on to have happened yet.</summary>
        public void Reconnect()
        {
            _ = ConnectAsync();
        }

        private void OnDisable()
        {
            _ = DisconnectAsync();
        }

        private string ResolveApiKey()
        {
            string envKey = Environment.GetEnvironmentVariable("ASSEMBLYAI_API_KEY");
            return !string.IsNullOrEmpty(envKey) ? envKey : apiKeyFallback;
        }

        private async Task ConnectAsync()
        {
            if (isConnecting || (socket != null && socket.State == WebSocketState.Open)) return;
            isConnecting = true;

            string apiKey = ResolveApiKey();
            if (string.IsNullOrEmpty(apiKey))
            {
                Debug.LogWarning("AssemblyAiStreamingSpeechToText: no API key set (ASSEMBLYAI_API_KEY env var or apiKeyFallback field). Not connecting.");
                isConnecting = false;
                return;
            }

            cts = new CancellationTokenSource();
            socket = new ClientWebSocket();
            socket.Options.SetRequestHeader("Authorization", apiKey);

            string url = $"wss://streaming.assemblyai.com/v3/ws?sample_rate={sampleRate}&encoding=pcm_s16le&format_turns={(formatTurns ? "true" : "false")}&mode={mode}";
            if (!string.IsNullOrEmpty(speechModel)) url += $"&speech_model={speechModel}";
            if (speakerLabels) url += "&speaker_labels=true";
            if (maxSpeakers > 0) url += $"&max_speakers={maxSpeakers}";

            try
            {
                await socket.ConnectAsync(new Uri(url), cts.Token);
                Debug.Log("AssemblyAiStreamingSpeechToText: connected.");
                _ = ReceiveLoopAsync(cts.Token);
            }
            catch (Exception e)
            {
                Debug.LogError($"AssemblyAiStreamingSpeechToText: connect failed: {e.Message}");
            }
            finally
            {
                isConnecting = false;
            }
        }

        private async Task ReceiveLoopAsync(CancellationToken token)
        {
            var buffer = new byte[8192];
            var messageBuilder = new StringBuilder();

            try
            {
                while (socket.State == WebSocketState.Open && !token.IsCancellationRequested)
                {
                    WebSocketReceiveResult result;
                    messageBuilder.Clear();
                    do
                    {
                        result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token);
                        if (result.MessageType == WebSocketMessageType.Close) break;
                        messageBuilder.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                    } while (!result.EndOfMessage);

                    if (result.MessageType == WebSocketMessageType.Close) break;

                    HandleServerMessage(messageBuilder.ToString());
                }
            }
            catch (OperationCanceledException)
            {
                // expected on Disconnect
            }
            catch (Exception e)
            {
                Debug.LogWarning($"AssemblyAiStreamingSpeechToText: receive loop ended: {e.Message}");
            }
        }

        private void HandleServerMessage(string json)
        {
            // Hand-rolled field extraction instead of a JSON library: Unity's JsonUtility can't
            // parse AssemblyAI's variable-shape messages (Begin/Turn/SpeechStarted/Termination
            // all differ), and this pipeline only needs a couple of fields out of each.
            string type = ExtractStringField(json, "type");
            switch (type)
            {
                case "Turn":
                    string transcript = ExtractStringField(json, "transcript");
                    bool endOfTurn = ExtractBoolField(json, "end_of_turn");
                    string speakerLabel = ExtractStringField(json, "speaker_label");
                    if (!string.IsNullOrEmpty(transcript))
                    {
                        Debug.Log($"AssemblyAiStreamingSpeechToText: transcript ({(endOfTurn ? "final" : "partial")}, speaker {speakerLabel ?? "?"}): {transcript}");
                        OnTranscript?.Invoke(transcript, speakerLabel, endOfTurn);
                    }
                    break;
                case "Begin":
                    Debug.Log("AssemblyAiStreamingSpeechToText: session began.");
                    break;
                case "Termination":
                    Debug.Log("AssemblyAiStreamingSpeechToText: session terminated by server.");
                    break;
                case "SpeechStarted":
                    // Expected, frequent (fires per detected utterance) — not worth a warning.
                    break;
                case "Error":
                    Debug.LogError($"AssemblyAiStreamingSpeechToText: server error: {json}");
                    break;
                default:
                    Debug.LogWarning($"AssemblyAiStreamingSpeechToText: unhandled message type '{type}': {json}");
                    break;
            }
        }

        private static string ExtractStringField(string json, string field)
        {
            var match = Regex.Match(json, $"\"{field}\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
            return match.Success ? Regex.Unescape(match.Groups[1].Value) : null;
        }

        private static bool ExtractBoolField(string json, string field)
        {
            var match = Regex.Match(json, $"\"{field}\"\\s*:\\s*(true|false)");
            return match.Success && match.Groups[1].Value == "true";
        }

        // ClientWebSocket.SendAsync is NOT reentrant — calling it again before the previous call
        // completes throws "There is already one outstanding 'SendAsync' call for this
        // WebSocket instance". SubmitAudio is invoked once per MicCapture frame (many times a
        // second); without serializing sends here, most of those calls would throw and their
        // audio would silently never reach AssemblyAI (the exception is only logged, easy to
        // miss, and this pipeline's own logging has proven unreliable to observe via tooling —
        // this bug produced a live "connected + session began, but zero transcripts ever" fault
        // caught by end-to-end testing, not by inspection).
        private readonly SemaphoreSlim sendLock = new(1, 1);
        private bool loggedFirstAudioFrame;
        private int framesSent;

        // AssemblyAI rejects any single binary frame outside 50-1000ms of audio (confirmed live:
        // error_code 3007 "Input Duration Violation" for a 2050ms frame — MicCapture's raw
        // per-Update() chunk sizes don't respect this at all, so audio must be re-buffered into
        // properly-sized chunks here rather than forwarded as-is. 50ms (their actual minimum,
        // not the 100ms originally used) trades a little more per-message overhead for half the
        // client-side buffering latency — matters since real-time responsiveness was an explicit
        // requirement and this delay stacks with everything downstream (LLM call, etc.).
        private const int TargetChunkMs = 50;
        private readonly List<byte> pendingAudio = new();
        private int TargetChunkBytes => sampleRate * 2 * TargetChunkMs / 1000; // 16-bit mono

        public void SubmitAudio(float[] samples, int sampleRateArg)
        {
            if (!loggedFirstAudioFrame)
            {
                loggedFirstAudioFrame = true;
                Debug.Log($"AssemblyAiStreamingSpeechToText: SubmitAudio reached (socket null: {socket == null}, state: {socket?.State}, samples: {samples.Length}).");
            }
            framesSent++;

            if (socket == null || socket.State != WebSocketState.Open) return;
            if (sampleRateArg != sampleRate)
            {
                Debug.LogWarning($"AssemblyAiStreamingSpeechToText: incoming sample rate ({sampleRateArg}) doesn't match the connection's sample_rate ({sampleRate}) — set MicCapture's sampleRate to {sampleRate} or audio will sound wrong to the STT.");
            }

            pendingAudio.AddRange(FloatToPcm16(samples));

            int targetBytes = TargetChunkBytes;
            while (pendingAudio.Count >= targetBytes)
            {
                byte[] chunk = pendingAudio.GetRange(0, targetBytes).ToArray();
                pendingAudio.RemoveRange(0, targetBytes);
                _ = SendAsync(chunk);
            }
        }

        private async Task SendAsync(byte[] data)
        {
            await sendLock.WaitAsync();
            try
            {
                if (socket == null || socket.State != WebSocketState.Open) return;
                await socket.SendAsync(new ArraySegment<byte>(data), WebSocketMessageType.Binary, true, cts?.Token ?? CancellationToken.None);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"AssemblyAiStreamingSpeechToText: send failed: {e.Message}");
            }
            finally
            {
                sendLock.Release();
            }
        }

        private static byte[] FloatToPcm16(float[] samples)
        {
            byte[] bytes = new byte[samples.Length * 2];
            for (int i = 0; i < samples.Length; i++)
            {
                short s = (short)Mathf.Clamp(samples[i] * short.MaxValue, short.MinValue, short.MaxValue);
                bytes[i * 2] = (byte)(s & 0xFF);
                bytes[i * 2 + 1] = (byte)((s >> 8) & 0xFF);
            }
            return bytes;
        }

        private async Task DisconnectAsync()
        {
            if (socket == null) return;
            try
            {
                if (socket.State == WebSocketState.Open)
                {
                    var terminateBytes = Encoding.UTF8.GetBytes("{\"type\":\"Terminate\"}");
                    await socket.SendAsync(new ArraySegment<byte>(terminateBytes), WebSocketMessageType.Text, true, CancellationToken.None);
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
                }
            }
            catch
            {
                // best-effort close
            }
            finally
            {
                cts?.Cancel();
                socket?.Dispose();
                socket = null;
            }
        }
    }
}
