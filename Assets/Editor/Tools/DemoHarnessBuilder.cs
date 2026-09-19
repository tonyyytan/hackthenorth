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
            var spawner = trackingGo.AddComponent<TrackedCaptionSpawner>();

            var mainCamera = Camera.main;

            var unprojectorSo = new SerializedObject(unprojector);
            unprojectorSo.FindProperty("raycastManager").objectReferenceValue = raycastManager;
            if (mainCamera != null)
            {
                unprojectorSo.FindProperty("sourceCamera").objectReferenceValue = mainCamera;
            }
            unprojectorSo.ApplyModifiedPropertiesWithoutUndo();

            var spawnerSo = new SerializedObject(spawner);
            spawnerSo.FindProperty("registry").objectReferenceValue = registry;
            if (mainCamera != null)
            {
                spawnerSo.FindProperty("faceCamera").objectReferenceValue = mainCamera;
            }
            spawnerSo.ApplyModifiedPropertiesWithoutUndo();

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

        /// <summary>
        /// Adds PassthroughCameraAccess + FaceIdClient (server.py bridge) to the SegmentationTracking
        /// GameObject, so they share its inactive-in-editor state. Run Create Demo Harness first.
        /// </summary>
        [MenuItem("Tools/HackTheNorth/Add Face ID Client")]
        public static void AddFaceIdClient()
        {
            var registry = Object.FindAnyObjectByType<TrackedTargetRegistry>(FindObjectsInactive.Include);
            var spawner = Object.FindAnyObjectByType<TrackedCaptionSpawner>(FindObjectsInactive.Include);
            var raycast = Object.FindAnyObjectByType<EnvironmentRaycastManager>(FindObjectsInactive.Include);
            if (registry == null || spawner == null)
            {
                Debug.LogError("AddFaceIdClient: run Tools/HackTheNorth/Create Demo Harness first.");
                return;
            }

            var go = registry.gameObject;
            // TryGetComponent, not ??: in the editor a missing GetComponent returns a fake-null object.
            if (!go.TryGetComponent(out PassthroughCameraAccess cam)) cam = go.AddComponent<PassthroughCameraAccess>();
            if (!go.TryGetComponent(out HackTheNorth.Identity.FaceIdClient client)) client = go.AddComponent<HackTheNorth.Identity.FaceIdClient>();

            var so = new SerializedObject(client);
            so.FindProperty("cameraAccess").objectReferenceValue = cam;
            so.FindProperty("raycastManager").objectReferenceValue = raycast;
            so.FindProperty("registry").objectReferenceValue = registry;
            so.FindProperty("spawner").objectReferenceValue = spawner;
            so.ApplyModifiedPropertiesWithoutUndo();

            EditorUtility.SetDirty(go);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();
            Debug.Log($"AddFaceIdClient: added to {go.name}. Set FaceIdClient.serverUrl to the LAN IP server.py prints.");
        }

        /// <summary>
        /// Idempotent repair: wires an already-present TrackedCaptionSpawner's object-reference
        /// fields via SerializedObject. Use this if the spawner was added to the scene directly
        /// (e.g. via the MCP bridge's AddComponentById) rather than through CreateDemoHarness —
        /// the bridge's reflection tools can set primitive fields but reliably fail to wire
        /// Unity Object references, so this menu item exists as the fallback.
        /// </summary>
        [MenuItem("Tools/HackTheNorth/Wire Tracked Caption Spawner")]
        public static void WireTrackedCaptionSpawner()
        {
            var spawner = Object.FindAnyObjectByType<TrackedCaptionSpawner>(FindObjectsInactive.Include);
            var registry = Object.FindAnyObjectByType<TrackedTargetRegistry>(FindObjectsInactive.Include);
            if (spawner == null || registry == null)
            {
                Debug.LogError("WireTrackedCaptionSpawner: need both a TrackedCaptionSpawner and a TrackedTargetRegistry in the scene.");
                return;
            }

            var so = new SerializedObject(spawner);
            so.FindProperty("registry").objectReferenceValue = registry;
            var mainCamera = Camera.main;
            if (mainCamera != null)
            {
                so.FindProperty("faceCamera").objectReferenceValue = mainCamera;
            }
            so.ApplyModifiedPropertiesWithoutUndo();

            EditorUtility.SetDirty(spawner);
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            EditorSceneManager.SaveOpenScenes();

            Debug.Log("WireTrackedCaptionSpawner: wired registry + faceCamera.");
        }
    }
}
