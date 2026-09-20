using UnityEngine;
using UnityEngine.UI;

namespace HackTheNorth.UI
{
    /// <summary>
    /// Gentle breathing-opacity loop on a Graphic (the small status dot on the caption box) —
    /// the "feels alive" ambient cue Cluely-style overlays use, independent of whether content
    /// is actively changing.
    /// </summary>
    public class PulsingIndicator : MonoBehaviour
    {
        [SerializeField] private float minAlpha = 0.45f;
        [SerializeField] private float maxAlpha = 1f;
        [SerializeField] private float speed = 1.6f;

        private Graphic graphic;
        private Color baseColor;

        private void Awake()
        {
            graphic = GetComponent<Graphic>();
            baseColor = graphic.color;
        }

        private void Update()
        {
            float t = (Mathf.Sin(Time.time * speed) + 1f) * 0.5f;
            baseColor.a = Mathf.Lerp(minAlpha, maxAlpha, t);
            graphic.color = baseColor;
        }
    }
}
