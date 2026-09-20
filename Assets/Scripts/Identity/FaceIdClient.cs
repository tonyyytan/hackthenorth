using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using HackTheNorth.Tracking;
using Meta.XR;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEngine.Networking;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Streams passthrough camera frames to the Python identity service (server.py, POST /id)
    /// and anchors one caption box per recognized person: face bbox -> passthrough-camera ray
    /// -> depth raycast -> TrackedTarget keyed by person id -> TrackedCaptionSpawner box.
    /// Up to maxInFlight requests at once, so the result rate tracks server time, not server +
    /// network + encode. Boxes are world-anchored with each frame's own camera pose, so head
    /// motion never lags; only the person's own movement waits on the next result.
    /// </summary>
    public class FaceIdClient : MonoBehaviour
    {
        [Tooltip("LAN address of the machine running server.py. NOT localhost: the Quest is a separate device.")]
        [SerializeField] private string serverUrl = "http://192.168.137.167:8000";
        [SerializeField] private PassthroughCameraAccess cameraAccess;
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private TrackedTargetRegistry registry;
        [SerializeField] private TrackedCaptionSpawner spawner;
        [SerializeField, Range(10, 100)] private int jpegQuality = 70;
        [Tooltip("Metres along the ray when the depth raycast misses and the server sent no distance_m.")]
        [SerializeField] private float fallbackDistance = 1.5f;

        [Tooltip("Frames uploading/processing at once. 2 hides network time behind server time; more just queues on the server.")]
        [SerializeField, Range(1, 3)] private int maxInFlight = 2;

        public string ServerUrl => serverUrl;

        /// <summary>Overrides the configured server address — used by ServerDiscovery so nobody
        /// has to hand-type/update a LAN IP that changes every time the venue's Wi-Fi does.</summary>
        public void SetServerUrl(string url) => serverUrl = url;

        private int frameId, lastPlaced, inFlight;
        private float retryAt;
        private int statCount;
        private float statSeconds, statSince;
        private readonly HashSet<string> shown = new();

        private IEnumerator Start()
        {
            while (true)
            {
                if (!cameraAccess.IsPlaying || inFlight >= maxInFlight || Time.time < retryAt)
                {
                    yield return null;
                    continue;
                }

                // Pose and pixels from the same frame -- bbox coordinates only mean anything against this pose.
                Pose pose = cameraAccess.GetCameraPose();
                Vector2Int size = cameraAccess.CurrentResolution;
                int id = ++frameId;
                inFlight++;

                // JPEG encode off the render thread: on the main thread it costs tens of ms per
                // camera frame, which the wearer sees as judder.
                var pixels = new NativeArray<Color32>(cameraAccess.GetColors(), Allocator.Persistent);
                Task<byte[]> encode = Task.Run(() =>
                {
                    using NativeArray<byte> jpg = ImageConversion.EncodeNativeArrayToJPG(
                        pixels, GraphicsFormat.R8G8B8A8_UNorm, (uint)size.x, (uint)size.y, 0, jpegQuality);
                    return jpg.ToArray();
                });
                while (!encode.IsCompleted) yield return null;
                pixels.Dispose();

                if (encode.IsFaulted)
                {
                    Debug.LogWarning($"FaceIdClient: JPEG encode failed: {encode.Exception?.GetBaseException().Message}");
                    inFlight--;
                    continue;
                }
                // Don't wait for the reply: the next frame uploads while the server works on this one.
                StartCoroutine(Send(id, encode.Result, pose, size));
            }
        }

        private IEnumerator Send(int id, byte[] jpeg, Pose pose, Vector2Int size)
        {
            float sentAt = Time.realtimeSinceStartup;
            string url = $"{serverUrl}/id?frame_id={id}&hfov={HorizontalFov(pose):F1}";
            using var req = new UnityWebRequest(url, "POST")
            {
                uploadHandler = new UploadHandlerRaw(jpeg) { contentType = "image/jpeg" },
                downloadHandler = new DownloadHandlerBuffer(),
            };
            yield return req.SendWebRequest();
            inFlight--;

            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogWarning($"FaceIdClient: {url} failed: {req.error}");
                retryAt = Time.time + 1f; // server down/restarting; don't spin
                yield break;
            }
            // Two requests in flight can land out of order; an older frame would drag boxes backwards.
            if (id <= lastPlaced) yield break;
            lastPlaced = id;
            Place(JsonUtility.FromJson<IdResponse>(req.downloadHandler.text), pose, size);
            LogRate(Time.realtimeSinceStartup - sentAt);
        }

        // Every 5s: how often boxes get fresh positions, and how stale each one is when it lands.
        private void LogRate(float roundTrip)
        {
            statCount++;
            statSeconds += roundTrip;
            if (Time.realtimeSinceStartup - statSince < 5f) return;
            float window = Time.realtimeSinceStartup - statSince;
            Debug.Log($"FaceIdClient: {statCount / window:F1} results/s, {statSeconds / statCount * 1000f:F0} ms round trip");
            statCount = 0;
            statSeconds = 0f;
            statSince = Time.realtimeSinceStartup;
        }

        // Registry evicts targets it stops hearing about; drop their boxes the same frame, or the
        // box's destroyed follow target makes it fall back to following the wearer's head.
        private void Update()
        {
            foreach (string id in shown.Where(id => !registry.TryGet(id, out _)).ToList())
            {
                spawner.RemoveCaptionBox(id);
                shown.Remove(id);
            }
        }

        private void Place(IdResponse res, Pose pose, Vector2Int size)
        {
            if (!string.IsNullOrEmpty(res.error)) Debug.LogWarning($"FaceIdClient: server error: {res.error}");

            foreach (Face f in res.faces ?? Array.Empty<Face>())
            {
                if (string.IsNullOrEmpty(f.name) || f.bbox == null || f.bbox.Length != 4) continue; // unknown face

                // JPEG pixels are top-down, viewport is bottom-up.
                // ponytail: if boxes land vertically mirrored on-device, drop the "1f -".
                var viewport = new Vector2((f.bbox[0] + f.bbox[2]) * 0.5f / size.x,
                                           1f - (f.bbox[1] + f.bbox[3]) * 0.5f / size.y);
                Ray ray = cameraAccess.ViewportPointToRay(viewport, pose);
                Vector3 point = raycastManager != null && EnvironmentRaycastManager.IsSupported
                                && raycastManager.Raycast(ray, out EnvironmentRaycastHit hit, 10f)
                    ? hit.point
                    : ray.GetPoint(f.distance_m > 0 ? f.distance_m : fallbackDistance);

                registry.Update(f.name, point);
                var box = spawner.GetOrCreateCaptionBox(f.name);
                if (box == null) continue;
                box.ShowDialogue(string.IsNullOrEmpty(f.profile?.name) ? f.name : f.profile.name, Body(f));
                shown.Add(f.name);
            }
        }

        // The web-research opener fills the box from the first frame; once the conversation
        // insight exists it takes over, since it knows what you're actually talking about.
        // ponytail: research summary stays off the box (~50 words is too long to read in AR);
        // it still reaches the insight via the profile brain.py is given.
        private static string Body(Face f) => string.Join("\n", new[]
        {
            f.profile?.role,
            f.insight?.topic,
            string.IsNullOrEmpty(f.insight?.suggested_question) ? f.profile?.opener : f.insight.suggested_question,
        }.Where(s => !string.IsNullOrEmpty(s)));

        // Measured from the camera's own rays, so it stays right whatever resolution is picked.
        private float HorizontalFov(Pose pose) => Vector3.Angle(
            cameraAccess.ViewportPointToRay(new Vector2(0f, 0.5f), pose).direction,
            cameraAccess.ViewportPointToRay(new Vector2(1f, 0.5f), pose).direction);

        // Mirrors server.py identify()'s response. Field names must match the JSON keys.
        [Serializable] private class IdResponse { public int frame_id; public Face[] faces; public string error; }
        [Serializable] private class Face
        {
            public string name, method;
            public float score, distance_m;
            public int[] bbox; // x1, y1, x2, y2 in JPEG pixels
            public Profile profile;
            public Insight insight;
        }
        [Serializable] private class Profile { public string name, role, bio, links, working_on, looking_for, research, opener; }
        [Serializable] private class Insight { public string topic, shared_interest, suggested_question; }
    }
}
