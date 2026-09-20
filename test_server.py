"""Self-check for the bits with real logic in them. python test_server.py"""
from server import FACE_WIDTH_M, distance_m, iou


def test_distance():
    # Halving the apparent width must double the distance. Checked at conversational
    # range -- the result is rounded to 2dp, so very small distances lose precision
    # to rounding rather than to the maths.
    near = distance_m(bbox_w=120, img_w=640, hfov_deg=80)
    far = distance_m(bbox_w=60, img_w=640, hfov_deg=80)
    assert near < far, "smaller face must read as further away"
    assert abs(far / near - 2) < 0.01, f"not linear in 1/width: {near} -> {far}"

    # Scale invariance is the whole reason this takes an FOV and not a focal length:
    # Unity downscaling the frame must not move the panel.
    full = distance_m(bbox_w=140, img_w=640, hfov_deg=80)
    half = distance_m(bbox_w=70, img_w=320, hfov_deg=80)
    assert abs(full - half) < 0.01, f"not scale-invariant: {full} vs {half}"

    # Sanity against real geometry: at 80 degrees across 640px, a face subtends
    # about 61/distance pixels. So conversational range (1.5m) is a ~40px face,
    # and a 140px face means someone is right in front of you.
    assert 0.3 < full < 0.6, f"140px face should read as very close, got {full}m"
    assert 1.3 < distance_m(41, 640, 80) < 1.7, "40px face should be ~1.5m"

    # Wider lens sees more, so the same pixel width means the face is closer.
    assert distance_m(140, 640, 110) < distance_m(140, 640, 80)
    assert distance_m(0, 640, 80) is None, "zero-width bbox must not divide by zero"


def test_iou():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0.0, "touching edges do not overlap"
    assert abs(iou([0, 0, 10, 10], [0, 0, 10, 5]) - 0.5) < 1e-9


if __name__ == "__main__":
    test_distance()
    test_iou()
    print(f"ok (FACE_WIDTH_M={FACE_WIDTH_M})")
