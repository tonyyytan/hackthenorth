using HackTheNorth.Audio;
using HackTheNorth.LLM;
using HackTheNorth.Pipeline;
using HackTheNorth.Speech;
using HackTheNorth.Tracking;
using HackTheNorth.UI;
using Meta.XR;
using Meta.XR.EnvironmentDepth;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace HackTheNorth.EditorTools
{
    /// <summary>
    /// Wires the audio->STT->LLM->caption pipeline and the segmentation-anchoring
    /// tracking system into the active scene, using stub STT/LLM so the chain is
    /// testable end-to-end before real backends exist.
    /// </summary>
    public static class DemoHarnessBuilder
    {
        [MenuItem("Tools/HackTheNorth/Create Demo Harness")]
        public static void CreateDemoHarness()
        {
            var captionBox = Object.FindAnyObjectByType<SpeakerCaptionBox>(FindObjectsInactive.Include);
            if (captionBox == null)
            {
                Debug.LogError("DemoHarnessBuilder: no SpeakerCaptionBox found in the scene. Run " +
                    "Tools/HackTheNorth/Create Speaker Caption Box first.");
                return;
            }

            // --- Voice pipeline (mic -> stub STT -> stub LLM -> caption box) ---
            var pipelineGo = new GameObject("CaptionPipelineDemo");
            var mic = pipelineGo.AddComponent<MicCapture>();
            var stt = pipelineGo.AddComponent<StubSpeechToText>();
            var llm = pipelineGo.AddComponent<StubLlmClient>();
            var pipeline = pipelineGo.AddComponent<CaptionPipeline>();

            var pipelineSo = new SerializedObject(pipeline);
            pipelineSo.FindProperty("micCapture").objectReferenceValue = mic;
            pipelineSo.FindProperty("speechToText").objectReferenceValue = stt;
            pipelineSo.FindProperty("llmClient").objectReferenceValue = llm;
            pipelineSo.FindProperty("captionBox").objectReferenceValue = captionBox;
            pipelineSo.ApplyModifiedPropertiesWithoutUndo();

            // AddComponent() already ran OnEnable once (synchronously, on an active GameObject)
            // before the fields above were set, so the automatic subscription happened against
            // nulls. Rebind() explicitly (re)subscribes now that the references are wired.
            pipeline.Rebind();

            // --- Segmentation-anchoring tracking system ---
            // NOTE: EnvironmentDepthManager/EnvironmentRaycastManager make native XR calls in
            // OnEnable that can hang the Editor with no active Quest Link/device session, so
            // this GameObject is created INACTIVE. Enable it only when running on-device or
            // over Quest Link with "Spatial Data over Meta Quest Link" turned on.
            var trackingGo = new GameObject("SegmentationTracking");
            trackingGo.SetActive(false);

            var depthManager = Object.FindAnyObjectByType<EnvironmentDepthManager>(FindObjectsInactive.Include);
            if (depthManager == null)
            {
                depthManager = trackingGo.AddComponent<EnvironmentDepthManager>();
            }

            var raycastManager = trackingGo.AddComponent<EnvironmentRaycastManager>();
            var registry = trackingGo.AddComponent<TrackedTargetRegistry>();
            var unprojector = trackingGo.AddComponent<WorldPointUnprojector>();

            var unprojectorSo = new SerializedObject(unprojector);
            unprojectorSo.FindProperty("raycastManager").objectReferenceValue = raycastManager;
            var mainCamera = Camera.main;
            if (mainCamera != null)
            {
                unprojectorSo.FindProperty("sourceCamera").objectReferenceValue = mainCamera;
            }
            unprojectorSo.ApplyModifiedPropertiesWithoutUndo();

            Selection.activeGameObject = pipelineGo;
            EditorUtility.SetDirty(pipelineGo);
            EditorUtility.SetDirty(trackingGo);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();

            Debug.Log("DemoHarnessBuilder: created CaptionPipelineDemo and SegmentationTracking (inactive). " +
                "In Play Mode, right-click StubSpeechToText in the Inspector and call SimulateTranscript " +
                "to test the full pipeline without a mic/real STT. Enable SegmentationTracking only on-device " +
                "or over Quest Link with Spatial Data enabled.");
        }
    }
}
