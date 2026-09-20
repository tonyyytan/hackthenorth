using System.Collections;
using HackTheNorth.Tracking;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace HackTheNorth.UI
{
    /// <summary>
    /// Small "● LIVE" indicator pinned to the top-left of view at all times, independent of
    /// the caption box's show/hide/fade state -- proof-of-life for a demo, so it's obvious at
    /// a glance that the app is actually running rather than a frozen/black screen or a
    /// recording. Self-spawns via RuntimeInitializeOnLoadMethod: needs zero scene wiring, so it
    /// can't be broken by the scene-save issues we've hit tonight, and it survives scene
    /// reloads (DontDestroyOnLoad) rather than needing to be re-added per scene.
    /// </summary>
    public static class StatusOverlaySpawner
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Spawn()
        {
            if (Object.FindAnyObjectByType<StatusOverlay>() != null) return; // already present
            var go = new GameObject("StatusOverlay(LIVE indicator)");
            Object.DontDestroyOnLoad(go);
            go.AddComponent<StatusOverlay>();
        }
    }

    public class StatusOverlay : MonoBehaviour
    {
        private static readonly Color IdleColor = new Color(0.35f, 1f, 0.45f, 0.85f); // dim green -- app running, nobody tracked yet
        private static readonly Color ActiveColor = new Color(0.392f, 0.710f, 1f, 0.95f); // accent blue -- a session is live (matches SpeakerCaptionBoxFactory.AccentColor)

        private Transform cam;
        private TextMeshProUGUI text;
        private Transform label;
        private TrackedTargetRegistry registry;
        private bool sessionActive;
        private Coroutine popRoutine;

        private void Start()
        {
            var canvasGo = new GameObject("StatusCanvas", typeof(RectTransform));
            canvasGo.transform.SetParent(transform, false);

            var canvas = canvasGo.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvasGo.AddComponent<CanvasScaler>();
            var canvasGroup = canvasGo.AddComponent<CanvasGroup>();
            canvasGroup.interactable = false;
            canvasGroup.blocksRaycasts = false;

            var rect = canvasGo.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(220f, 50f);
            canvasGo.transform.localScale = Vector3.one * 0.0012f;

            var textGo = new GameObject("Label", typeof(RectTransform));
            textGo.transform.SetParent(canvasGo.transform, false);
            var textRect = textGo.GetComponent<RectTransform>();
            textRect.anchorMin = Vector2.zero;
            textRect.anchorMax = Vector2.one;
            textRect.offsetMin = Vector2.zero;
            textRect.offsetMax = Vector2.zero;

            text = textGo.AddComponent<TextMeshProUGUI>();
            text.text = "● LIVE";
            text.fontSize = 26f;
            text.fontStyle = FontStyles.Bold;
            text.color = IdleColor;
            text.alignment = TextAlignmentOptions.MidlineLeft;
            text.outlineWidth = 0.15f;
            text.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.8f);
            label = textGo.transform;

            TryAttachToCamera(canvasGo.transform);
        }

        private void Update()
        {
            // Camera.main can come up after this overlay does (OVRCameraRig init order isn't
            // guaranteed relative to DontDestroyOnLoad objects) -- keep retrying until it's
            // actually parented, then stop.
            if (cam == null) TryAttachToCamera(transform.GetChild(0));

            if (registry == null) registry = FindAnyObjectByType<TrackedTargetRegistry>();
            bool active = registry != null && registry.Targets.Count > 0;
            if (active != sessionActive)
            {
                sessionActive = active;
                text.text = active ? "● TRACKING" : "● LIVE";
                text.color = active ? ActiveColor : IdleColor;
                if (popRoutine != null) StopCoroutine(popRoutine);
                popRoutine = StartCoroutine(PopBurst());
            }
        }

        // A brief scale burst right at the moment a session starts/stops -- makes the state
        // change actually noticeable instead of a text swap you might not catch mid-glance.
        private IEnumerator PopBurst()
        {
            const float duration = 0.25f;
            Vector3 baseScale = label.localScale;
            float t = 0f;
            while (t < duration)
            {
                t += Time.deltaTime;
                float p = t / duration;
                float bump = 1f + 0.35f * Mathf.Sin(p * Mathf.PI); // up and back down
                label.localScale = baseScale * bump;
                yield return null;
            }
            label.localScale = baseScale;
        }

        private void TryAttachToCamera(Transform canvasTransform)
        {
            if (Camera.main == null) return;
            cam = Camera.main.transform;
            canvasTransform.SetParent(cam, false);
            canvasTransform.localPosition = new Vector3(-0.20f, 0.13f, 1.0f); // top-left of view, pulled in from the previous edge-clipping offset
            canvasTransform.localRotation = Quaternion.identity;
        }
    }
}
