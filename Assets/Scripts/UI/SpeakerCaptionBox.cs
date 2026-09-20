using System.Collections;
using TMPro;
using UnityEngine;

namespace HackTheNorth.UI
{
    /// <summary>
    /// World-space dialogue/caption box that hovers at a fixed offset from a target
    /// (the wearer's head by default) and always faces them. Call ShowDialogue() to
    /// display a speaker + message pair, e.g. from a speech-to-text/LLM pipeline.
    /// </summary>
    public enum CaptionAnchorMode
    {
        /// <summary>Offset rotates with the target — right for a HUD box hovering near the wearer's head.</summary>
        HeadRelative,
        /// <summary>Offset stays fixed in world space — right for a box anchored to a tracked real-world object, which has no meaningful "forward."</summary>
        WorldOffset,
        /// <summary>Parented directly to the camera — a fixed HUD element (e.g. top-right corner) that never lags or swims as the wearer turns their head.</summary>
        HudLocked
    }

    [RequireComponent(typeof(CanvasGroup))]
    public class SpeakerCaptionBox : MonoBehaviour
    {
        [Header("Follow")]
        [Tooltip("Transform to hover near. Defaults to Camera.main at runtime if left empty.")]
        [SerializeField] private Transform followTarget;
        [SerializeField] private CaptionAnchorMode anchorMode = CaptionAnchorMode.HeadRelative;
        [Tooltip("HeadRelative: offset in the target's local space (x=right, y=up, z=forward). WorldOffset: offset added directly in world space (e.g. straight up from an anchor).")]
        [SerializeField] private Vector3 localOffset = new Vector3(0.25f, -0.1f, 1.0f);
        [SerializeField] private float positionSmoothTime = 0.15f;
        [SerializeField] private float rotationSmoothSpeed = 8f;
        [Tooltip("WorldOffset only: face this camera instead of the follow target. Defaults to Camera.main.")]
        [SerializeField] private Transform faceCamera;

        [Header("Content")]
        [SerializeField] private TMP_Text speakerNameLabel;
        [SerializeField] private TMP_Text messageLabel;

        [Header("Visibility")]
        [SerializeField] private float fadeDuration = 0.15f;
        [Tooltip("Seconds after the last ShowDialogue() call before auto-hiding. Set to 0 to disable auto-hide.")]
        [SerializeField] private float autoHideDelay = 4f;
        [Tooltip("Characters/sec for the message reveal. 0 = show instantly, no typewriter effect.")]
        [SerializeField] private float typewriterCharsPerSec = 45f;

        private CanvasGroup canvasGroup;
        private Vector3 velocity;
        private Coroutine fadeRoutine;
        private Coroutine autoHideRoutine;
        private Coroutine typeRoutine;
        private bool hudParented;

        private void Awake()
        {
            canvasGroup = GetComponent<CanvasGroup>();
            canvasGroup.alpha = 0f;

            if (followTarget == null && Camera.main != null)
            {
                followTarget = Camera.main.transform;
            }
        }

        private void LateUpdate()
        {
            if (followTarget == null)
            {
                if (Camera.main == null) return;
                followTarget = Camera.main.transform;
            }

            if (anchorMode == CaptionAnchorMode.HudLocked)
            {
                // Parent-lock instead of per-frame smoothing: a HUD element that trails the
                // head (even slightly) reads as broken, not "smooth" — real corner overlays
                // (e.g. Cluely-style) are rigidly fixed to the view.
                if (!hudParented)
                {
                    transform.SetParent(followTarget, worldPositionStays: false);
                    transform.localPosition = localOffset;
                    transform.localRotation = Quaternion.identity;
                    hudParented = true;
                }
                return;
            }

            Vector3 desiredPosition = anchorMode == CaptionAnchorMode.WorldOffset
                ? followTarget.position + localOffset
                : followTarget.position + followTarget.rotation * localOffset;
            transform.position = Vector3.SmoothDamp(transform.position, desiredPosition, ref velocity, positionSmoothTime);

            Transform faceTarget = anchorMode == CaptionAnchorMode.WorldOffset
                ? (faceCamera != null ? faceCamera : Camera.main != null ? Camera.main.transform : followTarget)
                : followTarget;
            if (faceTarget == null) return;

            Vector3 lookDirection = transform.position - faceTarget.position;
            if (lookDirection.sqrMagnitude > 0.0001f)
            {
                Quaternion desiredRotation = Quaternion.LookRotation(lookDirection);
                transform.rotation = Quaternion.Slerp(transform.rotation, desiredRotation, rotationSmoothSpeed * Time.deltaTime);
            }
        }

        /// <summary>Show the box with a speaker name and message, resetting the auto-hide timer.</summary>
        public void ShowDialogue(string speakerName, string message)
        {
            if (speakerNameLabel != null) speakerNameLabel.text = speakerName;
            RevealMessage(message);

            FadeTo(1f);

            if (autoHideDelay > 0f)
            {
                if (autoHideRoutine != null) StopCoroutine(autoHideRoutine);
                autoHideRoutine = StartCoroutine(AutoHideAfterDelay());
            }
        }

        /// <summary>Update just the message text of an already-visible box (e.g. streaming transcript).</summary>
        public void UpdateMessage(string message)
        {
            RevealMessage(message);

            if (autoHideDelay > 0f)
            {
                if (autoHideRoutine != null) StopCoroutine(autoHideRoutine);
                autoHideRoutine = StartCoroutine(AutoHideAfterDelay());
            }
        }

        /// <summary>
        /// Reveals text character-by-character rather than popping in all at once — the
        /// "feels alive, generating in real time" effect (Cluely-style) rather than a static
        /// panel that just changes. Skipped entirely (instant set) if the text is unchanged,
        /// so re-showing the same cached result doesn't replay the animation every poll.
        /// </summary>
        private void RevealMessage(string message)
        {
            if (messageLabel == null) return;
            message ??= string.Empty;

            if (typeRoutine != null) StopCoroutine(typeRoutine);
            if (typewriterCharsPerSec <= 0f || message == messageLabel.text)
            {
                messageLabel.text = message;
                return;
            }
            typeRoutine = StartCoroutine(TypewriterRoutine(message));
        }

        private IEnumerator TypewriterRoutine(string message)
        {
            messageLabel.text = string.Empty;
            float secondsPerChar = 1f / typewriterCharsPerSec;
            var builder = new System.Text.StringBuilder(message.Length);
            for (int i = 0; i < message.Length; i++)
            {
                builder.Append(message[i]);
                messageLabel.text = builder.ToString();
                yield return new WaitForSeconds(secondsPerChar);
            }
            typeRoutine = null;
        }

        public void Hide()
        {
            if (autoHideRoutine != null)
            {
                StopCoroutine(autoHideRoutine);
                autoHideRoutine = null;
            }
            FadeTo(0f);
        }

        public void SetFollowTarget(Transform target)
        {
            followTarget = target;
        }

        /// <summary>Configure this box to anchor to a world-space point (e.g. a TrackedTarget) instead of the wearer's head.</summary>
        public void ConfigureWorldAnchor(Transform target, Vector3? worldOffset = null, Transform faceCam = null)
        {
            anchorMode = CaptionAnchorMode.WorldOffset;
            followTarget = target;
            if (worldOffset.HasValue) localOffset = worldOffset.Value;
            faceCamera = faceCam;
        }

        /// <summary>Configure this box as a fixed HUD element parented to the camera (see HudLocked).</summary>
        public void ConfigureHudLocked(Vector3 localOffsetFromCamera)
        {
            anchorMode = CaptionAnchorMode.HudLocked;
            localOffset = localOffsetFromCamera;
            hudParented = false; // re-parent on the next LateUpdate with this offset
        }

        /// <summary>Runtime-safe wiring for SpeakerCaptionBoxFactory — assigns the TMP labels without needing UnityEditor's SerializedObject.</summary>
        public void Initialize(TMP_Text speakerLabel, TMP_Text messageLabelRef)
        {
            speakerNameLabel = speakerLabel;
            messageLabel = messageLabelRef;
        }

        private IEnumerator AutoHideAfterDelay()
        {
            yield return new WaitForSeconds(autoHideDelay);
            FadeTo(0f);
            autoHideRoutine = null;
        }

        private void FadeTo(float targetAlpha)
        {
            if (fadeRoutine != null) StopCoroutine(fadeRoutine);
            fadeRoutine = StartCoroutine(FadeRoutine(targetAlpha));
        }

        private IEnumerator FadeRoutine(float targetAlpha)
        {
            float startAlpha = canvasGroup.alpha;
            float t = 0f;
            while (t < fadeDuration)
            {
                t += Time.deltaTime;
                canvasGroup.alpha = Mathf.Lerp(startAlpha, targetAlpha, t / fadeDuration);
                yield return null;
            }
            canvasGroup.alpha = targetAlpha;
        }
    }
}
