using UnityEngine;

namespace HackTheNorth.Tracking
{
    /// <summary>
    /// One tracked real-world detection: an anchor Transform kept at the latest
    /// unprojected world position for a given track ID, plus a staleness timer so
    /// callers can evict targets that stop being redetected.
    /// </summary>
    public class TrackedTarget : MonoBehaviour
    {
        public string TrackId { get; private set; }
        public float LastSeenTime { get; private set; }

        public void Initialize(string trackId, Vector3 worldPosition)
        {
            TrackId = trackId;
            UpdatePose(worldPosition);
        }

        public void UpdatePose(Vector3 worldPosition)
        {
            transform.position = worldPosition;
            LastSeenTime = Time.time;
        }

        public float TimeSinceLastSeen => Time.time - LastSeenTime;
    }
}
