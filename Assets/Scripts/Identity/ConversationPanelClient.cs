using System.Collections;
using HackTheNorth.UI;
using UnityEngine;
using UnityEngine.Networking;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Production Quest UI for conversation mode. Polls the server's two read-only panel
    /// endpoints and mirrors their text into separate HUD panels. Empty text is authoritative:
    /// it clears the panel so ended/failed sessions can never leave stale person context visible.
    /// </summary>
    public class ConversationPanelClient : MonoBehaviour
    {
        private const float PollInterval = 0.5f;

        private FaceIdClient faceIdClient;
        private SpeakerCaptionBox researchPanel;
        private SpeakerCaptionBox talkingPointsPanel;
        private string lastResearchText = string.Empty;
        private string lastResearchTitle = string.Empty;
        private string lastTalkingPointsText = string.Empty;

        // Auto-spawn disabled for now: this expects /conversation/panel1|2 to return Andrew's
        // ConversationManager shape ({text, display_name, bullets, ...}), but that route was
        // reconciled during the main merge to keep FaceIdClient.cs's simpler FOCUS-based shape
        // ({person_id, profile} / {person_id, insight, researching}), which the Quest's
        // world-anchored per-person panels (TrackedCaptionSpawner) already depend on. Running
        // both at once means duplicate 0.5s pollers hitting the same endpoints and this script's
        // own separate HUD-locked panels always coming back empty. Needs a decision with
        // whoever owns the ConversationManager side about which panel shape is canonical before
        // re-enabling this.
        // [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Spawn()
        {
            if (FindAnyObjectByType<ConversationPanelClient>() != null) return;
            var go = new GameObject("ConversationPanels");
            DontDestroyOnLoad(go);
            go.AddComponent<ConversationPanelClient>();
        }

        private void Start()
        {
            researchPanel = SpeakerCaptionBoxFactory.Create(null);
            researchPanel.name = "ConversationResearchPanel";
            researchPanel.transform.localScale *= 0.55f;
            researchPanel.ConfigureHudLocked(new Vector3(-0.25f, 0.035f, 1.3f));
            researchPanel.Clear();

            talkingPointsPanel = SpeakerCaptionBoxFactory.Create(null);
            talkingPointsPanel.name = "ConversationTalkingPointsPanel";
            talkingPointsPanel.transform.localScale *= 0.55f;
            talkingPointsPanel.ConfigureHudLocked(new Vector3(0.25f, 0.035f, 1.3f));
            talkingPointsPanel.Clear();

            StartCoroutine(PollLoop());
        }

        private IEnumerator PollLoop()
        {
            while (true)
            {
                if (faceIdClient == null)
                    faceIdClient = FindAnyObjectByType<FaceIdClient>();

                if (faceIdClient != null && !string.IsNullOrEmpty(faceIdClient.ServerUrl))
                    yield return PollBoth(faceIdClient.ServerUrl.TrimEnd('/'));
                else
                    ClearPanels();

                yield return new WaitForSeconds(PollInterval);
            }
        }

        private IEnumerator PollBoth(string serverUrl)
        {
            using var panelOneRequest = UnityWebRequest.Get($"{serverUrl}/conversation/panel1");
            using var panelTwoRequest = UnityWebRequest.Get($"{serverUrl}/conversation/panel2");
            panelOneRequest.timeout = 2;
            panelTwoRequest.timeout = 2;

            // Start both before yielding so network latency overlaps.
            var panelOneOperation = panelOneRequest.SendWebRequest();
            var panelTwoOperation = panelTwoRequest.SendWebRequest();
            yield return panelOneOperation;
            yield return panelTwoOperation;

            if (panelOneRequest.result != UnityWebRequest.Result.Success ||
                panelTwoRequest.result != UnityWebRequest.Result.Success)
            {
                ClearPanels();
                yield break;
            }

            PanelOneResponse panelOne;
            PanelTwoResponse panelTwo;
            try
            {
                panelOne = JsonUtility.FromJson<PanelOneResponse>(panelOneRequest.downloadHandler.text);
                panelTwo = JsonUtility.FromJson<PanelTwoResponse>(panelTwoRequest.downloadHandler.text);
            }
            catch (System.Exception error)
            {
                Debug.LogWarning($"ConversationPanelClient: invalid panel response: {error.Message}");
                ClearPanels();
                yield break;
            }

            ApplyResearch(panelOne);
            ApplyTalkingPoints(panelTwo);
        }

        private void ApplyResearch(PanelOneResponse response)
        {
            string text = response?.text ?? string.Empty;
            if (string.IsNullOrWhiteSpace(text))
            {
                if (!string.IsNullOrEmpty(lastResearchText)) researchPanel.Clear();
                lastResearchText = string.Empty;
                lastResearchTitle = string.Empty;
                return;
            }

            string title = string.IsNullOrWhiteSpace(response.display_name)
                ? "About this person"
                : response.display_name;
            // Re-show even when unchanged to keep SpeakerCaptionBox's auto-hide timer alive;
            // unchanged text skips its typewriter animation internally.
            researchPanel.ShowDialogue(title, text);
            lastResearchText = text;
            lastResearchTitle = title;
        }

        private void ApplyTalkingPoints(PanelTwoResponse response)
        {
            string text = response?.text ?? string.Empty;
            if (string.IsNullOrWhiteSpace(text))
            {
                if (!string.IsNullOrEmpty(lastTalkingPointsText)) talkingPointsPanel.Clear();
                lastTalkingPointsText = string.Empty;
                return;
            }

            talkingPointsPanel.ShowDialogue("Talking points", text);
            lastTalkingPointsText = text;
        }

        private void ClearPanels()
        {
            researchPanel?.Clear();
            talkingPointsPanel?.Clear();
            lastResearchText = string.Empty;
            lastResearchTitle = string.Empty;
            lastTalkingPointsText = string.Empty;
        }

        private void OnDestroy()
        {
            if (researchPanel != null) Destroy(researchPanel.gameObject);
            if (talkingPointsPanel != null) Destroy(talkingPointsPanel.gameObject);
        }

        [System.Serializable]
        private class PanelOneResponse
        {
            public string text;
            public string session_id;
            public string person_id;
            public string display_name;
            public string[] bullets;
        }

        [System.Serializable]
        private class PanelTwoResponse
        {
            public string text;
            public string session_id;
            public string person_id;
            public string headline;
            public string[] talking_points;
            public string suggested_question;
            public int version;
        }
    }
}
