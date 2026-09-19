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

            // Background panel
            var background = CreateUIObject("Background", root.transform);
            var backgroundRect = background.GetComponent<RectTransform>();
            StretchFull(backgroundRect);
            var backgroundImage = background.AddComponent<Image>();
            backgroundImage.sprite = roundedSprite;
            backgroundImage.type = roundedSprite != null ? Image.Type.Sliced : Image.Type.Simple;
            backgroundImage.color = new Color(0.153f, 0.153f, 0.153f, 0.85f); // RLDS SurfaceSecondaryBackground @ 85%

            // Speaker name label
            var speakerNameGo = CreateUIObject("SpeakerName", root.transform);
            var speakerNameRect = speakerNameGo.GetComponent<RectTransform>();
            speakerNameRect.anchorMin = new Vector2(0f, 1f);
            speakerNameRect.anchorMax = new Vector2(1f, 1f);
            speakerNameRect.pivot = new Vector2(0.5f, 1f);
            speakerNameRect.anchoredPosition = new Vector2(0f, -16f);
            speakerNameRect.sizeDelta = new Vector2(-40f, 28f);
            var speakerNameText = speakerNameGo.AddComponent<TextMeshProUGUI>();
            speakerNameText.font = defaultFont;
            speakerNameText.text = "Speaker";
            speakerNameText.fontSize = 20f; // RLDS Heading3
            speakerNameText.fontStyle = FontStyles.Bold;
            speakerNameText.color = new Color(0.392f, 0.710f, 1f, 1f); // RLDS IconNotification blue
            speakerNameText.alignment = TextAlignmentOptions.TopLeft;

            // Message label
            var messageGo = CreateUIObject("Message", root.transform);
            var messageRect = messageGo.GetComponent<RectTransform>();
            messageRect.anchorMin = Vector2.zero;
            messageRect.anchorMax = Vector2.one;
            messageRect.offsetMin = new Vector2(20f, 16f);
            messageRect.offsetMax = new Vector2(-20f, -50f);
            var messageText = messageGo.AddComponent<TextMeshProUGUI>();
            messageText.font = defaultFont;
            messageText.text = "Message text goes here.";
            messageText.fontSize = 16f; // RLDS Heading4 / Body
            messageText.color = new Color(1f, 1f, 1f, 0.9f); // RLDS TextPrimary
            messageText.alignment = TextAlignmentOptions.TopLeft;
            messageText.textWrappingMode = TextWrappingModes.Normal;
            messageText.overflowMode = TextOverflowModes.Truncate;

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
