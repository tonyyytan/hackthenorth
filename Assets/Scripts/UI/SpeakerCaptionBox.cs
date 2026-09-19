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
    [RequireComponent(typeof(CanvasGroup))]
    public class SpeakerCaptionBox : MonoBehaviour
    {
        [Header("Follow")]
        [Tooltip("Transform to hover near. Defaults to Camera.main at runtime if left empty.")]
        [SerializeField] private Transform followTarget;
        [Tooltip("Offset from the target, in the target's local space (x=right, y=up, z=forward).")]
        [SerializeField] private Vector3 localOffset = new Vector3(0.25f, -0.1f, 1.0f);
        [SerializeField] private float positionSmoothTime = 0.15f;
        [SerializeField] private float rotationSmoothSpeed = 8f;

        [Header("Content")]
        [SerializeField] private TMP_Text speakerNameLabel;
        [SerializeField] private TMP_Text messageLabel;

        [Header("Visibility")]
        [SerializeField] private float fadeDuration = 0.15f;
        [Tooltip("Seconds after the last ShowDialogue() call before auto-hiding. Set to 0 to disable auto-hide.")]
        [SerializeField] private float autoHideDelay = 4f;

        private CanvasGroup canvasGroup;
        private Vector3 velocity;
        private Coroutine fadeRoutine;
        private Coroutine autoHideRoutine;

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

            Vector3 desiredPosition = followTarget.position + followTarget.rotation * localOffset;
            transform.position = Vector3.SmoothDamp(transform.position, desiredPosition, ref velocity, positionSmoothTime);

            Vector3 lookDirection = transform.position - followTarget.position;
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
            if (messageLabel != null) messageLabel.text = message;

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
            if (messageLabel != null) messageLabel.text = message;

            if (autoHideDelay > 0f)
            {
                if (autoHideRoutine != null) StopCoroutine(autoHideRoutine);
                autoHideRoutine = StartCoroutine(AutoHideAfterDelay());
            }
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
