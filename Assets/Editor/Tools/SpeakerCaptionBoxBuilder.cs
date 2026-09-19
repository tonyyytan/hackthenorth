using HackTheNorth.UI;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

namespace HackTheNorth.EditorTools
{
    /// <summary>
    /// Builds a world-space "SpeakerCaptionBox" prefab-like hierarchy directly into the
    /// active scene: a Canvas that follows the wearer's head with a rounded panel and
    /// speaker-name / message TMP labels, wired to a SpeakerCaptionBox component.
    /// </summary>
    public static class SpeakerCaptionBoxBuilder
    {
        private const float PanelWidth = 640f;
        private const float PanelHeight = 220f;
        private const float WorldScale = 0.0015f;
        private const float AccentBarWidth = 8f;
        private static readonly Color AccentColor = new Color(0.392f, 0.710f, 1f, 1f); // RLDS IconNotification blue

        [MenuItem("Tools/HackTheNorth/Create Speaker Caption Box")]
        public static GameObject CreateSpeakerCaptionBox()
        {
            Sprite roundedSprite = EditorGUIUtility.Load("UI/Skin/UISprite.psd") as Sprite;
            TMP_FontAsset defaultFont =
                AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset")
                ?? TMP_Settings.defaultFontAsset;

            var root = new GameObject("SpeakerCaptionCanvas", typeof(RectTransform));
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

            // Drop shadow (offset duplicate of the panel, sits behind it for legibility over passthrough)
            var shadow = CreateUIObject("Shadow", root.transform);
            var shadowRect = shadow.GetComponent<RectTransform>();
            StretchFull(shadowRect);
            shadowRect.anchoredPosition = new Vector2(4f, -6f);
            var shadowImage = shadow.AddComponent<Image>();
            shadowImage.sprite = roundedSprite;
            shadowImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            shadowImage.color = new Color(0f, 0f, 0f, 0.35f);

            // Background panel
            var background = CreateUIObject("Background", root.transform);
            var backgroundRect = background.GetComponent<RectTransform>();
            StretchFull(backgroundRect);
            var backgroundImage = background.AddComponent<Image>();
            backgroundImage.sprite = roundedSprite;
            backgroundImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            backgroundImage.color = new Color(0.098f, 0.098f, 0.098f, 0.92f); // darker + more opaque than RLDS default for passthrough contrast

            // Accent bar: thin colored strip on the left edge, like a speaker-color tab
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

            // Speaker name label
            var speakerNameGo = CreateUIObject("SpeakerName", root.transform);
            var speakerNameRect = speakerNameGo.GetComponent<RectTransform>();
            speakerNameRect.anchorMin = new Vector2(0f, 1f);
            speakerNameRect.anchorMax = new Vector2(1f, 1f);
            speakerNameRect.pivot = new Vector2(0.5f, 1f);
            speakerNameRect.anchoredPosition = new Vector2(AccentBarWidth + 14f, -18f);
            speakerNameRect.sizeDelta = new Vector2(-(AccentBarWidth + 14f) - 24f, 28f);
            var speakerNameText = speakerNameGo.AddComponent<TextMeshProUGUI>();
            speakerNameText.font = defaultFont;
            speakerNameText.text = "Speaker";
            speakerNameText.fontSize = 22f; // RLDS Heading3, bumped slightly for AR legibility
            speakerNameText.fontStyle = FontStyles.Bold;
            speakerNameText.color = AccentColor;
            speakerNameText.alignment = TextAlignmentOptions.TopLeft;
            speakerNameText.outlineWidth = 0.15f;
            speakerNameText.outlineColor = new Color(0f, 0f, 0f, 0.8f);

            // Message label
            var messageGo = CreateUIObject("Message", root.transform);
            var messageRect = messageGo.GetComponent<RectTransform>();
            messageRect.anchorMin = Vector2.zero;
            messageRect.anchorMax = Vector2.one;
            messageRect.offsetMin = new Vector2(AccentBarWidth + 24f, 18f);
            messageRect.offsetMax = new Vector2(-24f, -52f);
            var messageText = messageGo.AddComponent<TextMeshProUGUI>();
            messageText.font = defaultFont;
            messageText.text = "Message text goes here.";
            messageText.fontSize = 17f; // RLDS Heading4 / Body, bumped slightly for AR legibility
            messageText.color = new Color(1f, 1f, 1f, 0.95f); // RLDS TextPrimary
            messageText.alignment = TextAlignmentOptions.TopLeft;
            messageText.textWrappingMode = TextWrappingModes.Normal;
            messageText.overflowMode = TextOverflowModes.Truncate;
            messageText.outlineWidth = 0.1f;
            messageText.outlineColor = new Color(0f, 0f, 0f, 0.75f);

            // Wire up the runtime component
            var captionBox = root.AddComponent<SpeakerCaptionBox>();
            var so = new SerializedObject(captionBox);
            so.FindProperty("speakerNameLabel").objectReferenceValue = speakerNameText;
            so.FindProperty("messageLabel").objectReferenceValue = messageText;
            so.ApplyModifiedPropertiesWithoutUndo();

            Selection.activeGameObject = root;
            EditorUtility.SetDirty(root);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();

            return root;
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
