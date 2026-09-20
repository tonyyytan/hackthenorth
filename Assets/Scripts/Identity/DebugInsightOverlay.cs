using System.Collections;
using System.Net.Sockets;
using System.Text;
using HackTheNorth.UI;
using UnityEngine;
using UnityEngine.Networking;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Fallback path for testing/demoing the LLM insight pipeline with zero working camera/face
    /// pipeline (no printed photo on hand, PassthroughCameraAccess not working over Quest Link,
    /// etc.). Self-installs a small always-visible HUD box, independent of FaceIdClient entirely,
    /// and polls the server's GET /debug/latest_insight -- whatever talk.py (or the Pi) most
    /// recently generated for ANYONE shows up here. Purely a debug/demo aid, not part of the
    /// real per-person anchored flow.
    /// </summary>
    public class DebugInsightOverlay : MonoBehaviour
    {
        private const int DiscoveryPort = 41234;
        private const string Magic = "HACKTHENORTH_ID_SERVER:";
        private const float PollInterval = 1.5f; // safely under SpeakerCaptionBox's 4s auto-hide

        private string serverUrl;
        private SpeakerCaptionBox box;
        private UdpClient discoveryClient;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Spawn()
        {
            if (FindAnyObjectByType<DebugInsightOverlay>() != null) return;
            var go = new GameObject("DebugInsightOverlay");
            DontDestroyOnLoad(go);
            go.AddComponent<DebugInsightOverlay>();
        }

        private void Start()
        {
            box = SpeakerCaptionBoxFactory.Create(null);
            box.transform.localScale *= 0.6f; // smaller footprint so its edges don't clip the FOV
            // Lower-center-ish: clear of StatusOverlay's top-left LIVE indicator, close enough
            // to center that the whole panel (not just its middle) stays comfortably in view.
            box.ConfigureHudLocked(new Vector3(-0.14f, -0.09f, 1.3f));
            box.ShowDialogue("Debug", "Waiting for talk.py output...");

            StartDiscovery();
            StartCoroutine(PollLoop());
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
                Debug.LogWarning($"DebugInsightOverlay: couldn't listen on UDP {DiscoveryPort}: {e.Message}");
            }
        }

        private void OnDiscoveryReceive(System.IAsyncResult result)
        {
            if (discoveryClient == null) return;
            System.Net.IPEndPoint sender = null;
            byte[] data;
            try
            {
                data = discoveryClient.EndReceive(result, ref sender);
            }
            catch (System.ObjectDisposedException)
            {
                return;
            }

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
                if (res?.insight == null || string.IsNullOrEmpty(res.insight.topic)) continue;

                string name = string.IsNullOrEmpty(res.profile?.name) ? res.person_id : res.profile.name;
                string body = string.IsNullOrEmpty(res.insight.suggested_question)
                    ? res.insight.topic
                    : $"{res.insight.topic}\n{res.insight.suggested_question}";
                box.ShowDialogue(name, body);
            }
        }

        private void OnDestroy()
        {
            discoveryClient?.Close();
            discoveryClient = null;
        }

        [System.Serializable] private class LatestResponse { public string person_id; public Insight insight; public Profile profile; }
        [System.Serializable] private class Insight { public string topic, shared_interest, suggested_question; }
        [System.Serializable] private class Profile { public string name; }
    }
}
