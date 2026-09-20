using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace HackTheNorth.UI
{
    /// <summary>
    /// Builds the SpeakerCaptionBox visual hierarchy (Canvas/Shadow/Background/StatusDot/
    /// SpeakerName/Message) at runtime — no UnityEditor dependency, so this can spawn new
    /// caption boxes on-device (e.g. one per TrackedTarget), not just in the editor. The
    /// editor menu item (SpeakerCaptionBoxBuilder) calls this same method so both paths stay
    /// in sync. Visual language: translucent glass panel + a pulsing accent dot (Cluely-style
    /// "live overlay" look) rather than an opaque card with a solid accent sidebar.
    /// </summary>
    public static class SpeakerCaptionBoxFactory
    {
        public const float PanelWidth = 560f;
        public const float PanelHeight = 150f; // slimmer pill, not a tall card
        public const float WorldScale = 0.0015f;
        public const float DotSize = 14f;
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
            shadowRect.anchoredPosition = new Vector2(3f, -5f);
            var shadowImage = shadow.AddComponent<Image>();
            shadowImage.sprite = roundedSprite;
            shadowImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            shadowImage.color = new Color(0f, 0f, 0f, 0.3f);

            // Glassmorphic panel: translucent (not opaque) fill + a faint light border via
            // Outline, so it reads as "floating glass over passthrough" rather than a flat
            // opaque card — closer to Cluely's overlay look than the previous solid panel.
            var background = CreateUIObject("Background", root.transform);
            var backgroundRect = background.GetComponent<RectTransform>();
            StretchFull(backgroundRect);
            var backgroundImage = background.AddComponent<Image>();
            backgroundImage.sprite = roundedSprite;
            backgroundImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            backgroundImage.color = new Color(0.07f, 0.08f, 0.10f, 0.62f);
            var borderOutline = background.AddComponent<Outline>();
            borderOutline.effectColor = new Color(1f, 1f, 1f, 0.18f);
            borderOutline.effectDistance = new Vector2(1f, -1f);

            // Small pulsing status dot instead of a solid accent sidebar — reads as "live/
            // thinking" ambient presence rather than a static color block.
            var dot = CreateUIObject("StatusDot", root.transform);
            var dotRect = dot.GetComponent<RectTransform>();
            dotRect.anchorMin = dotRect.anchorMax = new Vector2(0f, 1f);
            dotRect.pivot = new Vector2(0f, 1f);
            dotRect.anchoredPosition = new Vector2(18f, -18f);
            dotRect.sizeDelta = new Vector2(DotSize, DotSize);
            var dotImage = dot.AddComponent<Image>();
            dotImage.sprite = roundedSprite;
            dotImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            dotImage.color = AccentColor;
            dot.AddComponent<PulsingIndicator>();

            float textLeftInset = 18f + DotSize + 10f;

            var speakerNameGo = CreateUIObject("SpeakerName", root.transform);
            var speakerNameRect = speakerNameGo.GetComponent<RectTransform>();
            speakerNameRect.anchorMin = new Vector2(0f, 1f);
            speakerNameRect.anchorMax = new Vector2(1f, 1f);
            speakerNameRect.pivot = new Vector2(0.5f, 1f);
            speakerNameRect.anchoredPosition = new Vector2(textLeftInset, -16f);
            speakerNameRect.sizeDelta = new Vector2(-textLeftInset - 20f, 24f);
            var speakerNameText = speakerNameGo.AddComponent<TextMeshProUGUI>();
            speakerNameText.font = font;
            speakerNameText.text = "Speaker";
            speakerNameText.fontSize = 20f; // slimmer header now that the dot carries the accent, not a bold name row
            speakerNameText.fontStyle = FontStyles.Bold;
            speakerNameText.color = new Color(AccentColor.r, AccentColor.g, AccentColor.b, 0.9f);
            speakerNameText.alignment = TextAlignmentOptions.TopLeft;
            speakerNameText.outlineWidth = 0.12f;
            speakerNameText.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.7f); // near-black, not pure #000 (avoids anti-aliasing halos)

            var messageGo = CreateUIObject("Message", root.transform);
            var messageRect = messageGo.GetComponent<RectTransform>();
            messageRect.anchorMin = Vector2.zero;
            messageRect.anchorMax = Vector2.one;
            messageRect.offsetMin = new Vector2(textLeftInset, 14f);
            messageRect.offsetMax = new Vector2(-20f, -44f);
            var messageText = messageGo.AddComponent<TextMeshProUGUI>();
            messageText.font = font;
            messageText.text = "Message text goes here.";
            messageText.fontSize = 19f; // near the ~1.5deg legibility floor at typical anchor distances (VR UI research)
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
