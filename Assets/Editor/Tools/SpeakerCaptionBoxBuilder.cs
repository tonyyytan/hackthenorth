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
    }
}
