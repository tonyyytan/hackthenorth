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
        public const float PanelHeight = 172f; // bumped for the larger bold message text below
        public const float WorldScale = 0.0015f;
        public const float DotSize = 14f;
        public static readonly Color AccentColor = new Color(0.392f, 0.710f, 1f, 1f); // RLDS IconNotification blue

        /// <summary>
        /// Builds a new caption box under parent (or the scene root if null). font is optional
        /// (falls back to TMP's default). roundedSprite is also optional — BUG HISTORY: this
        /// used to fall back to a plain sharp-cornered rect (Image.Type.Simple) whenever null,
        /// which is what every real runtime caller (FaceIdClient, TrackedCaptionSpawner,
        /// DebugInsightOverlay — anything not going through the Editor-only preview tools) was
        /// actually hitting, since they all call Create(null) with no sprite. Every on-device
        /// box has been sharp-cornered this whole time despite the design intent. Now a
        /// procedurally-generated rounded-rect sprite is always used unless one is explicitly
        /// passed in, so rounded corners work identically in the Editor preview and on-device.
        /// </summary>
        /// <param name="widthScale">
        /// Widens or narrows the panel (height/fonts stay fixed for legibility). Use this to
        /// give different box roles different visual weight — e.g. a "hero" live-suggestion
        /// box wider than a secondary static-facts box — instead of every box on screen being
        /// an identical, visually flat rectangle.
        /// </param>
        public static SpeakerCaptionBox Create(Transform parent, Sprite roundedSprite = null, TMP_FontAsset font = null, float widthScale = 1f)
        {
            roundedSprite ??= GetDefaultRoundedSprite();
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
            rootRect.sizeDelta = new Vector2(PanelWidth * widthScale, PanelHeight);
            root.transform.localScale = Vector3.one * WorldScale;

            // Two stacked shadow layers (a cheap fake blur — Unity UI Image can't blur on its
            // own) instead of one hard-edged offset copy, for an actual soft "floating card"
            // look rather than a flat drop-shadow silhouette.
            var shadowFar = CreateUIObject("ShadowFar", root.transform);
            var shadowFarRect = shadowFar.GetComponent<RectTransform>();
            shadowFarRect.anchorMin = Vector2.zero;
            shadowFarRect.anchorMax = Vector2.one;
            shadowFarRect.offsetMin = new Vector2(-10f, -14f);
            shadowFarRect.offsetMax = new Vector2(10f, 6f);
            shadowFarRect.anchoredPosition = new Vector2(6f, -12f);
            var shadowFarImage = shadowFar.AddComponent<Image>();
            shadowFarImage.sprite = roundedSprite;
            shadowFarImage.type = Image.Type.Sliced;
            shadowFarImage.color = new Color(0f, 0f, 0f, 0.16f);

            var shadow = CreateUIObject("Shadow", root.transform);
            var shadowRect = shadow.GetComponent<RectTransform>();
            StretchFull(shadowRect);
            shadowRect.anchoredPosition = new Vector2(3f, -5f);
            var shadowImage = shadow.AddComponent<Image>();
            shadowImage.sprite = roundedSprite;
            shadowImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            shadowImage.color = new Color(0f, 0f, 0f, 0.32f);

            // Glassmorphic panel: translucent (not opaque) fill + a faint light border via
            // Outline, so it reads as "floating glass over passthrough" rather than a flat
            // opaque card — closer to Cluely's overlay look than the previous solid panel.
            var background = CreateUIObject("Background", root.transform);
            var backgroundRect = background.GetComponent<RectTransform>();
            StretchFull(backgroundRect);
            var backgroundImage = background.AddComponent<Image>();
            backgroundImage.sprite = roundedSprite;
            backgroundImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            backgroundImage.color = new Color(0.06f, 0.06f, 0.07f, 0.82f); // darker/denser — the previous 0.62 alpha read as washed-out, not glass
            var borderOutline = background.AddComponent<Outline>();
            borderOutline.effectColor = new Color(1f, 1f, 1f, 0.14f); // a bit more present — the previous 0.07 read as no edge at all
            borderOutline.effectDistance = new Vector2(1.75f, -1.75f);

            // A top-edge highlight — real glass/acrylic catches ambient light along its
            // upper edge, which is most of what actually reads as "glass" rather than "flat
            // dark rectangle." Inset from the sides so it doesn't overhang the rounded corners.
            var topHighlight = CreateUIObject("TopHighlight", root.transform);
            var topHighlightRect = topHighlight.GetComponent<RectTransform>();
            topHighlightRect.anchorMin = new Vector2(0f, 1f);
            topHighlightRect.anchorMax = new Vector2(1f, 1f);
            topHighlightRect.pivot = new Vector2(0.5f, 1f);
            topHighlightRect.anchoredPosition = new Vector2(0f, -3f);
            topHighlightRect.sizeDelta = new Vector2(-44f, 2.5f); // thicker highlight bar
            var topHighlightImage = topHighlight.AddComponent<Image>();
            topHighlightImage.color = new Color(1f, 1f, 1f, 0.26f);

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
            // Small MUTED caption label, not a bold headline — matches the actual Cluely
            // reference, where "Suggestion"/"About X" are small dim labels and the real
            // content below is the large bold text. The previous version had this inverted.
            speakerNameText.fontSize = 15f;
            speakerNameText.fontStyle = FontStyles.Normal;
            speakerNameText.color = new Color(0.68f, 0.70f, 0.75f, 0.85f);
            speakerNameText.alignment = TextAlignmentOptions.TopLeft;
            speakerNameText.outlineWidth = 0.08f;
            speakerNameText.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.6f);

            var messageGo = CreateUIObject("Message", root.transform);
            var messageRect = messageGo.GetComponent<RectTransform>();
            messageRect.anchorMin = Vector2.zero;
            messageRect.anchorMax = Vector2.one;
            messageRect.offsetMin = new Vector2(textLeftInset, 16f);
            messageRect.offsetMax = new Vector2(-20f, -42f);
            var messageText = messageGo.AddComponent<TextMeshProUGUI>();
            messageText.font = font;
            messageText.text = "Message text goes here.";
            // The actual content — large and bold, the visual focus of the card.
            messageText.fontSize = 22f;
            messageText.fontStyle = FontStyles.Bold;
            messageText.color = new Color(1f, 1f, 1f, 0.98f);
            messageText.alignment = TextAlignmentOptions.TopLeft;
            messageText.textWrappingMode = TextWrappingModes.Normal;
            messageText.overflowMode = TextOverflowModes.Truncate;
            messageText.outlineWidth = 0.1f;
            messageText.outlineColor = new Color(0.05f, 0.05f, 0.05f, 0.75f); // near-black, not pure #000

            var captionBox = root.AddComponent<SpeakerCaptionBox>();
            captionBox.Initialize(speakerNameText, messageText);

            return captionBox;
        }

        private static Sprite cachedRoundedSprite;

        /// <summary>
        /// A procedural rounded-rect sprite (9-sliced so the corners stay crisp at any size),
        /// generated once and cached — no dependency on Editor-only assets like
        /// UI/Skin/UISprite.psd, which isn't available in an on-device build at all.
        /// </summary>
        private static Sprite GetDefaultRoundedSprite()
        {
            if (cachedRoundedSprite != null) return cachedRoundedSprite;

            const int size = 64;
            const int radius = 22; // pronounced, pill-like corners (matching the Cluely reference)
            var tex = new Texture2D(size, size, TextureFormat.RGBA32, false) { filterMode = FilterMode.Bilinear };
            var pixels = new Color32[size * size];
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    float alpha = RoundedRectAlpha(x, y, size, size, radius);
                    pixels[y * size + x] = new Color(1f, 1f, 1f, alpha);
                }
            }
            tex.SetPixels32(pixels);
            tex.Apply();

            cachedRoundedSprite = Sprite.Create(tex, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f), 100f,
                0, SpriteMeshType.FullRect, new Vector4(radius, radius, radius, radius));
            return cachedRoundedSprite;
        }

        /// <summary>Signed-distance-field rounded-rect mask, antialiased over ~1px.</summary>
        private static float RoundedRectAlpha(int x, int y, int w, int h, int r)
        {
            float px = x + 0.5f - w * 0.5f;
            float py = y + 0.5f - h * 0.5f;
            float qx = Mathf.Max(Mathf.Abs(px) - (w * 0.5f - r), 0f);
            float qy = Mathf.Max(Mathf.Abs(py) - (h * 0.5f - r), 0f);
            float dist = Mathf.Sqrt(qx * qx + qy * qy) - r;
            return Mathf.Clamp01(0.5f - dist);
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
