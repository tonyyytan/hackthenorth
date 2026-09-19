using System;
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
    /// NOT verified against a live AssemblyAI account in this session (no API key available) —
    /// this implements the protocol exactly as documented as of the research done for this
    /// feature (see CLAUDE.md), but connecting for the first time with a real key is the
    /// remaining verification step. One documented ambiguity: some AssemblyAI docs say
    /// `speech_model` is required with no default, others say it defaults to
    /// `universal-3-5-pro` — this implementation omits it and lets the server default; if
    /// connection fails, try setting speechModel explicitly.
    /// </summary>
    public class AssemblyAiStreamingSpeechToText : MonoBehaviour, ISpeechToText
    {
        [Tooltip("Prefer the ASSEMBLYAI_API_KEY environment variable instead — this field is serialized into the scene/prefab and would leak into git if committed with a real key.")]
        [SerializeField] private string apiKeyFallback = "";
        [SerializeField] private int sampleRate = 16000;
        [SerializeField] private bool formatTurns = true;
        [Tooltip("min_latency prioritizes real-time responsiveness over transcription accuracy — right for a live voice-query use case.")]
        [SerializeField] private string mode = "min_latency";
        [Tooltip("Leave blank to let the server pick a default (see class-level ambiguity note). Only set this if connecting without it fails.")]
        [SerializeField] private string speechModel = "";

        public event Action<string, bool> OnTranscript;

        private ClientWebSocket socket;
        private CancellationTokenSource cts;
        private bool isConnecting;

        public int SampleRate => sampleRate;

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
                    if (!string.IsNullOrEmpty(transcript))
                    {
                        OnTranscript?.Invoke(transcript, endOfTurn);
                    }
                    break;
                case "Begin":
                    Debug.Log("AssemblyAiStreamingSpeechToText: session began.");
                    break;
                case "Termination":
                    Debug.Log("AssemblyAiStreamingSpeechToText: session terminated by server.");
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

        public void SubmitAudio(float[] samples, int sampleRateArg)
        {
            if (socket == null || socket.State != WebSocketState.Open) return;
            if (sampleRateArg != sampleRate)
            {
                Debug.LogWarning($"AssemblyAiStreamingSpeechToText: incoming sample rate ({sampleRateArg}) doesn't match the connection's sample_rate ({sampleRate}) — set MicCapture's sampleRate to {sampleRate} or audio will sound wrong to the STT.");
            }

            byte[] pcm16 = FloatToPcm16(samples);
            _ = SendAsync(pcm16);
        }

        private async Task SendAsync(byte[] data)
        {
            try
            {
                await socket.SendAsync(new ArraySegment<byte>(data), WebSocketMessageType.Binary, true, cts?.Token ?? CancellationToken.None);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"AssemblyAiStreamingSpeechToText: send failed: {e.Message}");
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
