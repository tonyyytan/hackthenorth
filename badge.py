"""Read a conference badge to identify someone who never enrolled a face.

The trick is that this is not open-ended OCR: we match against a known roster of
~40 people, so "ALANA G0YAL" with a zero in it still resolves. difflib absorbs
the noise that would otherwise need a better OCR engine.

    python badge.py        # self-check on a rendered badge
"""
import difflib
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


def match_roster(lines, roster, cutoff=0.7):
    """roster: {person_id: display_name}. Returns (person_id, score) or (None, 0.0)."""
    names = {n.lower(): pid for pid, n in roster.items()}
    texts = [t for t, _ in lines]
    # Badges routinely put the first and last name on separate lines, so try
    # adjacent pairs too, not just each line alone.
    probes = texts + [f"{a} {b}" for a, b in zip(texts, texts[1:])]
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
    return match_roster(read_lines(crop), roster)


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
              "charlie-oneill": "Charlie O'Neill", "ryan-qi": "Ryan Qi"}
    for target, blur in [("Alana Goyal", False), ("Brooke Joseph", True), ("Ryan Qi", False)]:
        lines = read_lines(_render_badge(target, blur))
        pid, score = match_roster(lines, roster)
        print(f"{target:15s} blur={blur!s:5s} -> {pid} ({score})  ocr={[t for t, _ in lines]}")
        assert pid, f"failed to read {target}"

    # A stranger's badge must not be forced onto the nearest roster name.
    pid, _ = match_roster(read_lines(_render_badge("Zbigniew Wrzeszcz")), roster)
    assert pid is None, f"false positive: matched stranger to {pid}"
    print("ok")
