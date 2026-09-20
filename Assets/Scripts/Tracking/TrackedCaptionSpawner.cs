using System.Collections.Generic;
using HackTheNorth.UI;
using UnityEngine;

namespace HackTheNorth.Tracking
{
    /// <summary>
    /// Spawns TWO SpeakerCaptionBoxes per TrackedTarget, stacked near the target — a Cluely-style
    /// split rather than one box carrying everything:
    ///   - the PROFILE box: static bullet-point facts about the person (role, what they're
    ///     working on, what they're looking for) — changes rarely, once their profile loads.
    ///   - the INSIGHT box: live conversational suggestions (topic, a question worth asking) —
    ///     updates continuously as brain.py generates new ones from the conversation.
    /// Call GetOrCreateProfileBox/GetOrCreateInsightBox after registering the target with
    /// TrackedTargetRegistry, then ShowDialogue/UpdateMessage on the returned box.
    /// </summary>
    public class TrackedCaptionSpawner : MonoBehaviour
    {
        [SerializeField] private TrackedTargetRegistry registry;
        [Tooltip("How far to the side of the tracked point the boxes sit, so they don't cover the person/object itself.")]
        [SerializeField] private float lateralOffset = 0.5f;
        [Tooltip("How far above the tracked point the profile box sits. The insight box stacks above it.")]
        [SerializeField] private float verticalOffset = 0.30f;
        [Tooltip("Vertical gap between the two stacked boxes.")]
        [SerializeField] private float stackSpacing = 0.24f;
        [SerializeField] private Camera faceCamera;

        private readonly Dictionary<string, SpeakerCaptionBox> profileBoxes = new();
        private readonly Dictionary<string, SpeakerCaptionBox> insightBoxes = new();

        private void Awake()
        {
            if (faceCamera == null) faceCamera = Camera.main;
        }

        /// <summary>Bullet-point facts about the person — role, what they're working on/looking for.</summary>
        public SpeakerCaptionBox GetOrCreateProfileBox(string trackId)
            => GetOrCreate(profileBoxes, trackId, verticalOffset, widthScale: 0.9f);

        /// <summary>Live conversational suggestions — topic, a question worth asking next. Sized
        /// as the visual "hero" — wider than the profile box, not an identical twin rectangle.</summary>
        public SpeakerCaptionBox GetOrCreateInsightBox(string trackId)
            => GetOrCreate(insightBoxes, trackId, verticalOffset + stackSpacing, widthScale: 1.15f);

        private SpeakerCaptionBox GetOrCreate(Dictionary<string, SpeakerCaptionBox> boxes, string trackId, float height, float widthScale)
        {
            if (boxes.TryGetValue(trackId, out SpeakerCaptionBox existing) && existing != null)
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
            SpeakerCaptionBox box = SpeakerCaptionBoxFactory.Create(null, widthScale: widthScale);
            Vector3 sideOffset = ComputeSideOffset(target.transform.position, height);
            box.ConfigureWorldAnchor(target.transform, sideOffset, faceCamera != null ? faceCamera.transform : null);
            boxes[trackId] = box;
            return box;
        }

        /// <summary>Removes and destroys both boxes for a target that's no longer tracked.</summary>
        public void RemoveCaptionBox(string trackId)
        {
            RemoveFrom(profileBoxes, trackId);
            RemoveFrom(insightBoxes, trackId);
        }

        private static void RemoveFrom(Dictionary<string, SpeakerCaptionBox> boxes, string trackId)
        {
            if (!boxes.TryGetValue(trackId, out SpeakerCaptionBox box)) return;
            boxes.Remove(trackId);
            if (box != null) Destroy(box.gameObject);
        }

        /// <summary>
        /// Offsets the box to whichever side of the target is away from the wearer's view
        /// direction (computed once, at anchor time, from the wearer's position) plus a fixed
        /// height above — so it reads as "next to" the person/object instead of covering them.
        /// A pure world-space offset (e.g. always +X) would drift onto the target itself
        /// depending on which direction the wearer is actually standing relative to it.
        /// </summary>
        private Vector3 ComputeSideOffset(Vector3 targetPosition, float height)
        {
            Vector3 wearerPos = faceCamera != null ? faceCamera.transform.position : targetPosition + Vector3.back;
            Vector3 toTarget = targetPosition - wearerPos;
            toTarget.y = 0f;
            Vector3 sideDir = toTarget.sqrMagnitude > 0.0001f
                ? Vector3.Cross(Vector3.up, toTarget.normalized)
                : Vector3.right;

            return sideDir * lateralOffset + Vector3.up * height;
        }
    }
}
