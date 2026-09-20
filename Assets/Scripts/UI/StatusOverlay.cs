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
        private Transform cam;

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

            var text = textGo.AddComponent<TextMeshProUGUI>();
            text.text = "● LIVE";
            text.fontSize = 26f;
            text.fontStyle = FontStyles.Bold;
            text.color = new Color(0.35f, 1f, 0.45f, 0.85f); // dim green -- visible but not distracting
            text.alignment = TextAlignmentOptions.MidlineLeft;
            text.outlineWidth = 0.15f;
            text.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.8f);

            TryAttachToCamera(canvasGo.transform);
        }

        private void Update()
        {
            // Camera.main can come up after this overlay does (OVRCameraRig init order isn't
            // guaranteed relative to DontDestroyOnLoad objects) -- keep retrying until it's
            // actually parented, then stop.
            if (cam == null) TryAttachToCamera(transform.GetChild(0));
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
