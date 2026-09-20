using System.Collections.Generic;
using HackTheNorth.UI;
using UnityEngine;

namespace HackTheNorth.Tracking
{
    /// <summary>
    /// Spawns one SpeakerCaptionBox per TrackedTarget on demand, anchored in world space above
    /// the target. Call GetOrCreateCaptionBox(trackId) after registering the target with
    /// TrackedTargetRegistry, then ShowDialogue/UpdateMessage on the returned box when a
    /// search/LLM result for that target lands.
    /// </summary>
    public class TrackedCaptionSpawner : MonoBehaviour
    {
        [SerializeField] private TrackedTargetRegistry registry;
        [Tooltip("How far to the side of the tracked point the box sits, so it doesn't cover the person/object itself.")]
        [SerializeField] private float lateralOffset = 0.5f;
        [Tooltip("How far above the tracked point the box sits.")]
        [SerializeField] private float verticalOffset = 0.35f;
        [SerializeField] private Camera faceCamera;

        private readonly Dictionary<string, SpeakerCaptionBox> captionBoxes = new();

        private void Awake()
        {
            if (faceCamera == null) faceCamera = Camera.main;
        }

        /// <summary>Returns the existing caption box for trackId, or creates one anchored to that TrackedTarget.</summary>
        public SpeakerCaptionBox GetOrCreateCaptionBox(string trackId)
        {
            if (captionBoxes.TryGetValue(trackId, out SpeakerCaptionBox existing) && existing != null)
            {
                return existing;
            }

            if (registry == null || !registry.TryGet(trackId, out TrackedTarget target))
            {
                Debug.LogWarning($"TrackedCaptionSpawner: no TrackedTarget registered for '{trackId}' yet.");
                return null;
            }

            // Parented to the scene root, not this component's own transform — the spawner
            // commonly lives on the same (intentionally inactive, see TrackedTargetRegistry
            // comments) GameObject as EnvironmentRaycastManager, and a spawned caption box must
            // stay active/visible regardless of that parent's state.
            SpeakerCaptionBox box = SpeakerCaptionBoxFactory.Create(null);
            Vector3 sideOffset = ComputeSideOffset(target.transform.position);
            box.ConfigureWorldAnchor(target.transform, sideOffset, faceCamera != null ? faceCamera.transform : null);
            captionBoxes[trackId] = box;
            return box;
        }

        /// <summary>Removes and destroys the caption box for a target that's no longer tracked.</summary>
        public void RemoveCaptionBox(string trackId)
        {
            if (!captionBoxes.TryGetValue(trackId, out SpeakerCaptionBox box)) return;
            captionBoxes.Remove(trackId);
            if (box != null) Destroy(box.gameObject);
        }

        /// <summary>
        /// Offsets the box to whichever side of the target is away from the wearer's view
        /// direction (computed once, at anchor time, from the wearer's position) plus a fixed
        /// height above — so it reads as "next to" the person/object instead of covering them.
        /// A pure world-space offset (e.g. always +X) would drift onto the target itself
        /// depending on which direction the wearer is actually standing relative to it.
        /// </summary>
        private Vector3 ComputeSideOffset(Vector3 targetPosition)
        {
            Vector3 wearerPos = faceCamera != null ? faceCamera.transform.position : targetPosition + Vector3.back;
            Vector3 toTarget = targetPosition - wearerPos;
            toTarget.y = 0f;
            Vector3 sideDir = toTarget.sqrMagnitude > 0.0001f
                ? Vector3.Cross(Vector3.up, toTarget.normalized)
                : Vector3.right;

            return sideDir * lateralOffset + Vector3.up * verticalOffset;
        }
    }
}
