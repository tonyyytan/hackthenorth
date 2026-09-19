using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace HackTheNorth.UI
{
    /// <summary>
    /// Builds the SpeakerCaptionBox visual hierarchy (Canvas/Shadow/AccentBar/SpeakerName/
    /// Message) at runtime — no UnityEditor dependency, so this can spawn new caption boxes
    /// on-device (e.g. one per TrackedTarget), not just in the editor. The editor menu item
    /// (SpeakerCaptionBoxBuilder) calls this same method so both paths stay in sync.
    /// </summary>
    public static class SpeakerCaptionBoxFactory
    {
        public const float PanelWidth = 640f;
        public const float PanelHeight = 220f;
        public const float WorldScale = 0.0015f;
        public const float AccentBarWidth = 8f;
        public static readonly Color AccentColor = new Color(0.392f, 0.710f, 1f, 1f); // RLDS IconNotification blue

        /// <summary>
        /// Builds a new caption box under parent (or the scene root if null). roundedSprite and
        /// font are optional — pass Resources.Load or an editor-time asset lookup; both fall
        /// back gracefully (a plain rect / TMP's default font) if left null.
        /// </summary>
        public static SpeakerCaptionBox Create(Transform parent, Sprite roundedSprite = null, TMP_FontAsset font = null)
        {
            var root = new GameObject("SpeakerCaptionCanvas", typeof(RectTransform));
            root.transform.SetParent(parent, worldPositionStays: false);

            var canvas = root.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            var scaler = root.AddComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 10f;
            var canvasGroup = root.AddComponent<CanvasGroup>();
            canvasGroup.interactable = false;
            canvasGroup.blocksRaycasts = false;

            var rootRect = root.GetComponent<RectTransform>();
            rootRect.sizeDelta = new Vector2(PanelWidth, PanelHeight);
            root.transform.localScale = Vector3.one * WorldScale;

            var shadow = CreateUIObject("Shadow", root.transform);
            var shadowRect = shadow.GetComponent<RectTransform>();
            StretchFull(shadowRect);
            shadowRect.anchoredPosition = new Vector2(4f, -6f);
            var shadowImage = shadow.AddComponent<Image>();
            shadowImage.sprite = roundedSprite;
            shadowImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            shadowImage.color = new Color(0f, 0f, 0f, 0.35f);

            var background = CreateUIObject("Background", root.transform);
            var backgroundRect = background.GetComponent<RectTransform>();
            StretchFull(backgroundRect);
            var backgroundImage = background.AddComponent<Image>();
            backgroundImage.sprite = roundedSprite;
            backgroundImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            backgroundImage.color = new Color(0.098f, 0.098f, 0.098f, 0.92f);

            var accentBar = CreateUIObject("AccentBar", root.transform);
            var accentRect = accentBar.GetComponent<RectTransform>();
            accentRect.anchorMin = new Vector2(0f, 0f);
            accentRect.anchorMax = new Vector2(0f, 1f);
            accentRect.pivot = new Vector2(0f, 0.5f);
            accentRect.anchoredPosition = new Vector2(10f, 0f);
            accentRect.sizeDelta = new Vector2(AccentBarWidth, -20f);
            var accentImage = accentBar.AddComponent<Image>();
            accentImage.sprite = roundedSprite;
            accentImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            accentImage.color = AccentColor;

            var speakerNameGo = CreateUIObject("SpeakerName", root.transform);
            var speakerNameRect = speakerNameGo.GetComponent<RectTransform>();
            speakerNameRect.anchorMin = new Vector2(0f, 1f);
            speakerNameRect.anchorMax = new Vector2(1f, 1f);
            speakerNameRect.pivot = new Vector2(0.5f, 1f);
            speakerNameRect.anchoredPosition = new Vector2(AccentBarWidth + 14f, -18f);
            speakerNameRect.sizeDelta = new Vector2(-(AccentBarWidth + 14f) - 24f, 28f);
            var speakerNameText = speakerNameGo.AddComponent<TextMeshProUGUI>();
            speakerNameText.font = font;
            speakerNameText.text = "Speaker";
            speakerNameText.fontSize = 24f; // bumped from 22 — VR UI research: keep body text near/above the ~1.5deg legibility floor at typical anchor distances
            speakerNameText.fontStyle = FontStyles.Bold;
            speakerNameText.color = AccentColor;
            speakerNameText.alignment = TextAlignmentOptions.TopLeft;
            speakerNameText.outlineWidth = 0.15f;
            speakerNameText.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.8f); // near-black, not pure #000 (avoids anti-aliasing halos)

            var messageGo = CreateUIObject("Message", root.transform);
            var messageRect = messageGo.GetComponent<RectTransform>();
            messageRect.anchorMin = Vector2.zero;
            messageRect.anchorMax = Vector2.one;
            messageRect.offsetMin = new Vector2(AccentBarWidth + 24f, 18f);
            messageRect.offsetMax = new Vector2(-24f, -52f);
            var messageText = messageGo.AddComponent<TextMeshProUGUI>();
            messageText.font = font;
            messageText.text = "Message text goes here.";
            messageText.fontSize = 20f; // bumped from 17 — see speaker-name comment above; this was near the legibility floor for anchor distances beyond ~1.5m
            messageText.color = new Color(0.96f, 0.96f, 0.96f, 0.95f); // off-white, not pure #FFF
            messageText.alignment = TextAlignmentOptions.TopLeft;
            messageText.textWrappingMode = TextWrappingModes.Normal;
            messageText.overflowMode = TextOverflowModes.Truncate;
            messageText.outlineWidth = 0.1f;
            messageText.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.75f); // near-black, not pure #000

            var captionBox = root.AddComponent<SpeakerCaptionBox>();
            captionBox.Initialize(speakerNameText, messageText);

            return captionBox;
        }

        private static GameObject CreateUIObject(string name, Transform parent)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return go;
        }

        private static void StretchFull(RectTransform rect)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
        }
    }
}
