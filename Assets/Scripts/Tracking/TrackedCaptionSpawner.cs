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
        [SerializeField] private Vector3 worldOffset = new Vector3(0f, 0.15f, 0f);
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
            box.ConfigureWorldAnchor(target.transform, worldOffset, faceCamera != null ? faceCamera.transform : null);
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
    }
}
