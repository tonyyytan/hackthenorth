using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using HackTheNorth.Tracking;
using Meta.XR;
using UnityEngine;
using UnityEngine.Networking;

namespace HackTheNorth.Identity
{
    /// <summary>
    /// Streams passthrough camera frames to the Python identity service (server.py, POST /id)
    /// and anchors one caption box per recognized person: face bbox -> passthrough-camera ray
    /// -> depth raycast -> TrackedTarget keyed by person id -> TrackedCaptionSpawner box.
    /// One request in flight at a time, so the frame rate self-limits to server latency.
    /// </summary>
    public class FaceIdClient : MonoBehaviour
    {
        [Tooltip("LAN address of the machine running server.py. NOT localhost: the Quest is a separate device.")]
        [SerializeField] private string serverUrl = "http://192.168.1.100:8000";
        [SerializeField] private PassthroughCameraAccess cameraAccess;
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private TrackedTargetRegistry registry;
        [SerializeField] private TrackedCaptionSpawner spawner;
        [SerializeField, Range(10, 100)] private int jpegQuality = 70;
        [Tooltip("Metres along the ray when the depth raycast misses and the server sent no distance_m.")]
        [SerializeField] private float fallbackDistance = 1.5f;

        private Texture2D frame;
        private int frameId;
        private readonly HashSet<string> shown = new();

        private IEnumerator Start()
        {
            while (true)
            {
                if (!cameraAccess.IsPlaying) { yield return null; continue; }

                // Pose and pixels from the same frame -- bbox coordinates only mean anything against this pose.
                Pose pose = cameraAccess.GetCameraPose();
                Vector2Int size = cameraAccess.CurrentResolution;
                if (frame == null || frame.width != size.x || frame.height != size.y)
                    frame = new Texture2D(size.x, size.y, TextureFormat.RGBA32, false);
                frame.LoadRawTextureData(cameraAccess.GetColors());
                byte[] jpeg = frame.EncodeToJPG(jpegQuality);

                string url = $"{serverUrl}/id?frame_id={++frameId}&hfov={HorizontalFov(pose):F1}";
                using var req = new UnityWebRequest(url, "POST")
                {
                    uploadHandler = new UploadHandlerRaw(jpeg) { contentType = "image/jpeg" },
                    downloadHandler = new DownloadHandlerBuffer(),
                };
                yield return req.SendWebRequest();

                if (req.result != UnityWebRequest.Result.Success)
                {
                    Debug.LogWarning($"FaceIdClient: {url} failed: {req.error}");
                    yield return new WaitForSeconds(1f); // server down/restarting; don't spin
                    continue;
                }
                Place(JsonUtility.FromJson<IdResponse>(req.downloadHandler.text), pose, size);
            }
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

        private static string Body(Face f) => string.Join("\n", new[]
        {
            f.profile?.role, f.insight?.topic, f.insight?.suggested_question,
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
        [Serializable] private class Profile { public string name, role, bio, links, working_on, looking_for; }
        [Serializable] private class Insight { public string topic, shared_interest, suggested_question; }
    }
}
