using HackTheNorth.UI;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace HackTheNorth.EditorTools
{
    /// <summary>
    /// Editor menu item that builds a SpeakerCaptionBox into the active scene, for the fixed
    /// head-relative HUD caption box. Delegates the actual visual construction to
    /// SpeakerCaptionBoxFactory (runtime-safe, shared with the per-tracked-target spawner) so
    /// both paths stay visually identical.
    /// </summary>
    public static class SpeakerCaptionBoxBuilder
    {
        [MenuItem("Tools/HackTheNorth/Create Speaker Caption Box")]
        public static GameObject CreateSpeakerCaptionBox()
        {
            Sprite roundedSprite = EditorGUIUtility.Load("UI/Skin/UISprite.psd") as Sprite;
            TMP_FontAsset defaultFont =
                AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset")
                ?? TMP_Settings.defaultFontAsset;

            SpeakerCaptionBox captionBox = SpeakerCaptionBoxFactory.Create(null, roundedSprite, defaultFont);
            GameObject root = captionBox.gameObject;

            Selection.activeGameObject = root;
            EditorUtility.SetDirty(root);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();

            return root;
        }

        /// <summary>
        /// Edit-mode preview of the two-panel split (profile + insight boxes stacked near a
        /// person) with example content, so the layout can be checked directly in the Scene
        /// view without Play mode, a build, or a real face match. Positions/text are set
        /// directly (not via ShowDialogue, whose fade/typewriter coroutines don't run outside
        /// Play mode) -- these are static preview objects, not wired to anything live.
        /// </summary>
        [MenuItem("Tools/HackTheNorth/Preview Two-Panel Layout")]
        public static void CreateTwoPanelPreview()
        {
            Sprite roundedSprite = EditorGUIUtility.Load("UI/Skin/UISprite.psd") as Sprite;
            TMP_FontAsset defaultFont =
                AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset")
                ?? TMP_Settings.defaultFontAsset;

            var root = new GameObject("TwoPanelPreview (Edit-mode only, safe to delete)");

            SpeakerCaptionBox profileBox = SpeakerCaptionBoxFactory.Create(root.transform, roundedSprite, defaultFont);
            profileBox.gameObject.name = "ProfileBox (example)";
            profileBox.transform.position = new Vector3(0.4f, 1.55f, 1.5f);
            SetText(profileBox, "Thor", "• Software engineer\n• Working on: real-time face ID\n• Looking for: a co-founder");

            SpeakerCaptionBox insightBox = SpeakerCaptionBoxFactory.Create(root.transform, roundedSprite, defaultFont);
            insightBox.gameObject.name = "InsightBox (example)";
            insightBox.transform.position = new Vector3(0.4f, 1.79f, 1.5f);
            SetText(insightBox, "Worth asking", "hackathon project progress\nWhat problem is your project trying to solve?");

            Selection.activeGameObject = root;
            EditorUtility.SetDirty(root);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();

            Debug.Log("Preview created — select TwoPanelPreview in the Hierarchy, click into the Scene view, and press F to frame it. Delete this GameObject when done; it's not wired to anything live.");
        }

        private static void SetText(SpeakerCaptionBox box, string speakerName, string message)
        {
            var texts = box.GetComponentsInChildren<TextMeshProUGUI>();
            if (texts.Length > 0) texts[0].text = speakerName; // SpeakerName is created before Message
            if (texts.Length > 1) texts[1].text = message;
            box.GetComponent<CanvasGroup>().alpha = 1f; // Awake() (which would zero this) doesn't run outside Play mode
        }
    }
}
