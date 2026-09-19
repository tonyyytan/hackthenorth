using Meta.XR;
using UnityEngine;

namespace HackTheNorth.Tracking
{
    /// <summary>
    /// Converts a 2D detection (pixel coordinates in a camera frame) into a real-world
    /// 3D point, using Meta's EnvironmentRaycastManager (Depth API under the hood) so we
    /// don't have to hand-roll depth-buffer unprojection math.
    ///
    /// Requires an EnvironmentRaycastManager in the scene (enabled) and, on-device, the
    /// Scene permission granted — see MRUK's EnvironmentRaycastManager docs. This only
    /// works on Quest 3/3S hardware (or in-editor over Meta Quest Link with
    /// "Spatial Data over Meta Quest Link" enabled) — EnvironmentRaycastManager.IsSupported
    /// reports false otherwise.
    /// </summary>
    public class WorldPointUnprojector : MonoBehaviour
    {
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private Camera sourceCamera;
        [SerializeField] private float maxDistance = 10f;

        private void Awake()
        {
            if (sourceCamera == null) sourceCamera = Camera.main;
        }

        /// <summary>
        /// Unprojects a pixel coordinate (e.g. the center of a segmentation box, in the
        /// same camera frame sourceCamera renders) into a world-space point.
        /// Returns false if the environment raycast misses, is occluded, or isn't ready yet.
        /// </summary>
        public bool TryGetWorldPoint(Vector2 pixel, out Vector3 worldPoint)
        {
            worldPoint = default;

            if (raycastManager == null || sourceCamera == null) return false;
            if (!EnvironmentRaycastManager.IsSupported) return false;

            Ray ray = sourceCamera.ScreenPointToRay(pixel);
            if (!raycastManager.Raycast(ray, out EnvironmentRaycastHit hit, maxDistance)) return false;

            worldPoint = hit.point;
            return true;
        }
    }
}
