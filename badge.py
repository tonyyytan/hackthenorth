"""Read a conference badge to identify someone who never enrolled a face.

The trick is that this is not open-ended OCR: we match against a known roster of
~40 people, so "ALANA G0YAL" with a zero in it still resolves. difflib absorbs
the noise that would otherwise need a better OCR engine.

Reader: a Baseten-hosted vision model when BASETEN_API_KEY + BASETEN_VISION_MODEL are
set (reads stylised/angled tags OCR misses), else local RapidOCR. Either way the text
is only a probe into the roster -- a model that invents a name matches nobody.

    python badge.py        # self-check on a rendered badge
"""
import base64
import difflib
import os
import re

import cv2

_ocr = None


def ocr():
    # ponytail: lazy singleton so importing this doesn't add 2s to server startup
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        # ponytail: capped at 2 threads. Unlimited, OCR grabs all 10 cores in the
        # background and drags the face response from ~110ms to ~800ms -- exactly
        # when someone new walks up. A slower badge read nobody is waiting on is
        # the right trade.
        _ocr = RapidOCR(intra_op_num_threads=2)
    return _ocr


def chest_crop(img, bbox):
    """Region where a lanyard badge hangs, given a face box. Roughly: one face
    height below the chin, three face widths wide."""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) // 2
    top, bot = y2 + int(0.2 * h), y2 + int(2.6 * h)
    left, right = cx - int(1.6 * w), cx + int(1.6 * w)
    H, W = img.shape[:2]
    crop = img[max(0, top):min(H, bot), max(0, left):min(W, right)]
    return crop if crop.size else None


def read_lines(crop, min_conf=0.5):
    result, _ = ocr()(crop)
    return [(t.strip(), c) for _, t, c in (result or []) if c >= min_conf and t.strip()]


def read_with_baseten(crop):
    """Name on the tag via Baseten Model APIs, as read_lines-style [(text, conf)].
    None means "not configured", so the caller falls back to OCR."""
    key, model = os.environ.get("BASETEN_API_KEY"), os.environ.get("BASETEN_VISION_MODEL")
    if not (key and model):
        return None
    from openai import OpenAI
    jpeg = base64.b64encode(cv2.imencode(".jpg", crop)[1].tobytes()).decode()
    r = OpenAI(api_key=key, base_url="https://inference.baseten.co/v1").chat.completions.create(
        model=model, temperature=0, max_tokens=20,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg}"}},
            {"type": "text", "text": "What person's name is printed on the name tag or badge? "
                                     "Reply with only the name, or NONE if there is no readable name."},
        ]}],
    )
    text = (r.choices[0].message.content or "").strip()
    return [] if not text or text.upper().startswith("NONE") else [(text, 1.0)]


def match_roster(lines, roster, cutoff=0.7):
    """roster: {person_id: display_name}. Returns (person_id, score) or (None, 0.0)."""
    names = {n.lower(): pid for pid, n in roster.items()}
    texts = [t for t, _ in lines]
    # Badges routinely put the first and last name on separate lines, so try
    # adjacent pairs too, in both orders: OCR sorts boxes top-down, and a tall
    # capital in the last name ("Aleyner") can sit higher than the first name.
    pairs = list(zip(texts, texts[1:]))
    probes = texts + [f"{a} {b}" for a, b in pairs] + [f"{b} {a}" for a, b in pairs]
    best = (None, 0.0)
    for text in probes:
        probe = re.sub(r"[^a-z ]+", "", text.lower()).strip()
        if len(probe) < 4:
            continue
        for hit in difflib.get_close_matches(probe, names, n=1, cutoff=cutoff):
            score = difflib.SequenceMatcher(None, probe, hit).ratio()
            if score > best[1]:
                best = (names[hit], round(score, 3))
    return best


def identify_badge(img, bbox, roster):
    crop = chest_crop(img, bbox)
    if crop is None:
        return None, 0.0
    try:
        lines = read_with_baseten(crop)
    except Exception:
        lines = None                                  # Baseten down -> local OCR
    return match_roster(read_lines(crop) if lines is None else lines, roster)


def _render_badge(name, noise=False):
    """A fake badge, for the self-check. Not used at runtime."""
    import numpy as np
    img = np.full((160, 460, 3), 245, dtype="uint8")
    cv2.putText(img, name, (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3)
    cv2.putText(img, "HACK THE NORTH", (20, 135), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (90, 90, 90), 2)
    if noise:
        img = cv2.GaussianBlur(img, (5, 5), 0)
    return img


if __name__ == "__main__":
    roster = {"alana-goyal": "Alana Goyal", "brooke-joseph": "Brooke Joseph",
              "charlie-oneill": "Charlie O'Neill", "ryan-qi": "Ryan Qi", "eli-aleyner": "Eli Aleyner"}
    for target, blur in [("Alana Goyal", False), ("Brooke Joseph", True), ("Ryan Qi", False)]:
        lines = read_lines(_render_badge(target, blur))
        pid, score = match_roster(lines, roster)
        print(f"{target:15s} blur={blur!s:5s} -> {pid} ({score})  ocr={[t for t, _ in lines]}")
        assert pid, f"failed to read {target}"

    # A vision model's answer goes through the same roster gate.
    assert match_roster([("Charlie O'Neill", 1.0)], roster)[0] == "charlie-oneill"
    assert match_roster([("Elon Musk", 1.0)], roster)[0] is None, "hallucinated name must not match"
    # real handwritten tag: OCR returned the last name first, and misread both words
    assert match_roster([("Aleynor", 0.9), ("El:", 0.85)], roster)[0] == "eli-aleyner"

    # A stranger's badge must not be forced onto the nearest roster name.
    pid, _ = match_roster(read_lines(_render_badge("Zbigniew Wrzeszcz")), roster)
    assert pid is None, f"false positive: matched stranger to {pid}"
    print("ok")
