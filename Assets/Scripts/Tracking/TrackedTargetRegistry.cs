using System.Collections.Generic;
using UnityEngine;

namespace HackTheNorth.Tracking
{
    /// <summary>
    /// Owns the pool of TrackedTarget anchors, keyed by track ID. Upstream detections
    /// call Update(trackId, worldPosition) each frame; this either moves the existing
    /// anchor for that ID or creates a new one, and evicts anchors that haven't been
    /// seen in staleTimeout seconds (the detector stopped reporting them).
    /// </summary>
    public class TrackedTargetRegistry : MonoBehaviour
    {
        [SerializeField] private float staleTimeout = 2f;

        private readonly Dictionary<string, TrackedTarget> targets = new();

        public IReadOnlyDictionary<string, TrackedTarget> Targets => targets;

        /// <summary>Create or move the anchor for trackId to worldPosition. Returns it.</summary>
        public TrackedTarget Update(string trackId, Vector3 worldPosition)
        {
            if (targets.TryGetValue(trackId, out TrackedTarget existing))
            {
                existing.UpdatePose(worldPosition);
                return existing;
            }

            var go = new GameObject($"TrackedTarget_{trackId}");
            go.transform.SetParent(transform, worldPositionStays: true);
            var target = go.AddComponent<TrackedTarget>();
            target.Initialize(trackId, worldPosition);
            targets[trackId] = target;
            return target;
        }

        /// <summary>
        /// Nearest-neighbor fallback for detectors that don't hand back a stable trackId
        /// frame to frame: matches worldPosition to the closest existing target within
        /// matchRadius, or creates a new target with a freshly generated ID otherwise.
        /// </summary>
        public TrackedTarget UpdateNearest(Vector3 worldPosition, float matchRadius)
        {
            TrackedTarget closest = null;
            float closestDistSqr = matchRadius * matchRadius;

            foreach (TrackedTarget candidate in targets.Values)
            {
                float distSqr = (candidate.transform.position - worldPosition).sqrMagnitude;
                if (distSqr <= closestDistSqr)
                {
                    closestDistSqr = distSqr;
                    closest = candidate;
                }
            }

            if (closest != null)
            {
                closest.UpdatePose(worldPosition);
                return closest;
            }

            return Update(System.Guid.NewGuid().ToString("N"), worldPosition);
        }

        private void Update()
        {
            List<string> stale = null;
            foreach (var kvp in targets)
            {
                if (kvp.Value.TimeSinceLastSeen > staleTimeout)
                {
                    stale ??= new List<string>();
                    stale.Add(kvp.Key);
                }
            }

            if (stale == null) return;
            foreach (string id in stale)
            {
                if (targets.TryGetValue(id, out TrackedTarget target) && target != null)
                {
                    Destroy(target.gameObject);
                }
                targets.Remove(id);
            }
        }

        public bool TryGet(string trackId, out TrackedTarget target) => targets.TryGetValue(trackId, out target);
    }
}
