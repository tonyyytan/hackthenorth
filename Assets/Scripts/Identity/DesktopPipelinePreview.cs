using System.Collections;
using System.Net.Sockets;
using System.Text;
using HackTheNorth.UI;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.Networking;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Desktop-only test harness: drives the real two-panel UI (profile box + insight box,
    /// same formatting and state machine as FaceIdClient.Place()) purely from polling the
    /// server every 0.5s -- no Quest, no camera, no face detection needed at all. Lets you
    /// press Play in the Editor and watch the actual enter/exit animations, sizing and
    /// state-machine transitions (Listening/Researching/Continue conversation!/Worth asking)
    /// just by hitting the server with curl or the Pi, same as a real session would.
    /// Self-installs like DebugInsightOverlay/StatusOverlay -- no scene wiring needed.
    /// </summary>
    public class DesktopPipelinePreview : MonoBehaviour
    {
        private const int DiscoveryPort = 41234;
        private const string Magic = "HACKTHENORTH_ID_SERVER:";
        private const float PollInterval = 0.5f;

        private string serverUrl;
        private SpeakerCaptionBox profileBox;
        private SpeakerCaptionBox insightBox;
        private UdpClient discoveryClient;
        private bool everShown;

        // Editor-only tool: no Quest needed to test panel content/animations. Without this
        // guard it also self-installs in real device builds, spawning a second HUD-locked
        // pair of boxes on top of TrackedCaptionSpawner's real per-person ones -- the
        // "extra duplicate boxes" bug seen on-device.
#if UNITY_EDITOR
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Spawn()
        {
            if (FindAnyObjectByType<DesktopPipelinePreview>() != null) return;
            var go = new GameObject("DesktopPipelinePreview (Editor test only)");
            DontDestroyOnLoad(go);
            go.AddComponent<DesktopPipelinePreview>();
        }
#endif

        private void Start()
        {
            profileBox = SpeakerCaptionBoxFactory.Create(null, widthScale: 0.9f);
            profileBox.ConfigureHudLocked(new Vector3(0.30f, -0.05f, 1.4f));
            profileBox.SetTypewriterSpeed(0f); // instant text -- this tool tests state/content, not the reveal cosmetic

            insightBox = SpeakerCaptionBoxFactory.Create(null, widthScale: 1.15f);
            insightBox.ConfigureHudLocked(new Vector3(0.34f, 0.22f, 1.4f));
            insightBox.SetTypewriterSpeed(0f);

            Debug.Log("DesktopPipelinePreview: spawned profileBox and insightBox, starting discovery + poll loop.");
            StartDiscovery();
            StartCoroutine(PollLoop());
        }

        // Press H in Play mode to simulate the person leaving -- there's no real "walked
        // away" signal to poll for in this desktop-only test (no camera, no registry
        // eviction), so this is the manual trigger for testing the exit/dismiss animation.
        private void Update()
        {
            // This project uses the new Input System exclusively -- UnityEngine.Input throws
            // an InvalidOperationException on every single call when that's the case, which
            // (thrown every frame, forever) is expensive enough to look like the whole scene
            // had frozen. Keyboard.current can be null (no keyboard device yet), hence the guard.
            if (Keyboard.current != null && Keyboard.current.hKey.wasPressedThisFrame)
            {
                profileBox.Hide();
                insightBox.Hide();
                everShown = false;
                Debug.Log("DesktopPipelinePreview: simulated leaving (H pressed) -- both boxes dismissed.");
            }
        }

        private void StartDiscovery()
        {
            try
            {
                discoveryClient = new UdpClient(DiscoveryPort) { EnableBroadcast = true };
                discoveryClient.BeginReceive(OnDiscoveryReceive, null);
            }
            catch (System.Exception e)
            {
                Debug.LogWarning($"DesktopPipelinePreview: couldn't listen on UDP {DiscoveryPort}: {e.Message}");
            }
        }

        private void OnDiscoveryReceive(System.IAsyncResult result)
        {
            if (discoveryClient == null) return;
            System.Net.IPEndPoint sender = null;
            byte[] data;
            try { data = discoveryClient.EndReceive(result, ref sender); }
            catch (System.ObjectDisposedException) { return; }

            string text = Encoding.ASCII.GetString(data);
            if (text.StartsWith(Magic) && sender != null)
            {
                serverUrl = $"http://{sender.Address}:{text.Substring(Magic.Length)}";
            }
            discoveryClient?.BeginReceive(OnDiscoveryReceive, null);
        }

        private IEnumerator PollLoop()
        {
            while (true)
            {
                yield return new WaitForSeconds(PollInterval);
                if (string.IsNullOrEmpty(serverUrl)) continue;

                using var req = UnityWebRequest.Get($"{serverUrl}/debug/latest_insight");
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success) continue;

                var res = JsonUtility.FromJson<LatestResponse>(req.downloadHandler.text);
                Apply(res);
            }
        }

        // Mirrors FaceIdClient.Place()'s formatting/state-machine exactly, so what you see
        // here is what you'd see on-device -- not a simplified stand-in.
        private void Apply(LatestResponse res)
        {
            if (res == null || string.IsNullOrEmpty(res.person_id)) return;
            everShown = true;

            string displayName = string.IsNullOrEmpty(res.profile?.name) ? res.person_id : res.profile.name;
            var lines = new System.Collections.Generic.List<string>();
            if (!string.IsNullOrEmpty(res.profile?.role)) lines.Add($"• {res.profile.role}");
            if (!string.IsNullOrEmpty(res.profile?.working_on)) lines.Add($"• {res.profile.working_on}");
            if (!string.IsNullOrEmpty(res.profile?.looking_for)) lines.Add($"• {res.profile.looking_for}");
            if (lines.Count == 0 && !string.IsNullOrEmpty(res.profile?.opener)) lines.Add(res.profile.opener);
            Debug.Log($"DesktopPipelinePreview: person={res.person_id} profileNull={res.profile == null} lineCount={lines.Count}");
            if (lines.Count > 0) profileBox.ShowDialogue(displayName, string.Join("\n", lines));

            if (res.researching)
            {
                insightBox.ShowDialogue("Researching", "...");
            }
            else
            {
                string body = string.IsNullOrEmpty(res.insight?.suggested_question)
                    ? res.insight?.topic
                    : $"{res.insight?.topic}\n{res.insight.suggested_question}";
                if (!string.IsNullOrEmpty(body))
                {
                    insightBox.ShowDialogue("Worth asking", body);
                }
                else if (everShown)
                {
                    insightBox.ShowDialogue("Listening", "Say something to get started");
                }
            }
        }

        private void OnDestroy() => discoveryClient?.Close();

        [System.Serializable] private class LatestResponse { public string person_id; public Insight insight; public Profile profile; public bool researching; }
        [System.Serializable] private class Insight { public string topic, shared_interest, suggested_question; }
        [System.Serializable] private class Profile { public string name, role, working_on, looking_for, opener; }
    }
}
