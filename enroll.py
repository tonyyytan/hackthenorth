"""Enroll a person: capture several angles from the webcam + write their profile.

    python enroll.py ryan
    python enroll.py ryan --role "CS @ Waterloo" --working-on "AR networking assistant"
    python enroll.py ryan --shots 5 --no-preview

Writes faces/<id>/shot_*.jpg (the gallery) and a profile row in people.db.
The <id> is the canonical person id everywhere in the system.

Run it at the venue, under venue lighting -- embeddings shift with lighting.
Tell the person to turn their head slightly between beeps; the angles are the point.
"""
import argparse
import sqlite3
import time
import warnings
from pathlib import Path

import cv2

warnings.filterwarnings("ignore")
from insightface.app import FaceAnalysis

MIN_FACE_BRIGHTNESS = 40   # mean grey of the face crop. Measured: 5 = unusable,
                           # 47 = workable, 151 = a good photo. A near-black shot in
                           # the gallery is worse than no shot -- it is noise that
                           # can false-match.
HERE = Path(__file__).parent
DB = HERE / "people.db"
FIELDS = ["name", "role", "bio", "links", "working_on", "looking_for"]


def face_brightness(frame, face):
    x1, y1, x2, y2 = [max(0, int(v)) for v in face.bbox]
    crop = frame[y1:y2, x1:x2]
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean() if crop.size else 0.0


def db():
    con = sqlite3.connect(DB)
    con.execute(f"CREATE TABLE IF NOT EXISTS people (id TEXT PRIMARY KEY, {', '.join(FIELDS)})")
    return con


def save_profile(pid, values):
    con = db()
    cols = ", ".join(FIELDS)
    con.execute(f"INSERT OR REPLACE INTO people (id, {cols}) VALUES (?{', ?' * len(FIELDS)})",
                [pid] + [values.get(f, "") or "" for f in FIELDS])
    con.commit()
    con.close()


def capture(pid, n_shots, preview, gap=0.8):
    """Auto-shoot whenever exactly one face is in frame. Returns count saved."""
    det = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                       allowed_modules=["detection"])
    det.prepare(ctx_id=-1, det_size=(320, 320))

    out = HERE / "faces" / pid
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("shot_*.jpg"):
        old.unlink()

    cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cam.isOpened():
        raise SystemExit("no webcam (tried index 0)")
    # Webcam auto-exposure starts black and takes ~1s to adapt. Measured: frame
    # brightness 3 -> 88 over the first 30 frames. Shooting immediately banks a
    # useless all-black shot, so burn these.
    for _ in range(30):
        cam.read()

    saved, last, deadline = 0, 0.0, time.time() + 60
    print(f"capturing {n_shots} shots -- turn your head slightly between each")
    try:
        while saved < n_shots and time.time() < deadline:
            ok, frame = cam.read()
            if not ok:
                continue
            faces = det.get(frame)
            too_dark = bool(faces) and face_brightness(frame, faces[0]) < MIN_FACE_BRIGHTNESS
            ready = len(faces) == 1 and not too_dark and time.time() - last > gap

            if preview:
                for f in faces:
                    x1, y1, x2, y2 = [int(v) for v in f.bbox]
                    colour = (0, 200, 0) if len(faces) == 1 else (0, 0, 255)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
                msg = (f"{saved}/{n_shots}" if len(faces) == 1 and not too_dark
                       else "too dark - turn a light on" if too_dark
                       else f"need exactly 1 face (saw {len(faces)})")
                cv2.putText(frame, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                cv2.imshow(f"enrolling {pid} - esc to stop", frame)
                if cv2.waitKey(1) == 27:
                    break

            if ready:
                cv2.imwrite(str(out / f"shot_{saved}.jpg"), frame)
                saved, last = saved + 1, time.time()
                print(f"  shot {saved}/{n_shots}")
    finally:
        cam.release()
        if preview:
            cv2.destroyAllWindows()

    if saved < n_shots:
        print(f"  only got {saved}/{n_shots} -- more light, or face the camera squarely")
    if saved == 0:
        # ponytail: leave no empty dir behind, the server would just warn about it
        out.rmdir()
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("id", help="canonical person id, e.g. ryan")
    ap.add_argument("--shots", type=int, default=5)
    ap.add_argument("--no-preview", action="store_true")
    for f in FIELDS:
        ap.add_argument(f"--{f.replace('_', '-')}", default=None)
    args = ap.parse_args()

    n = capture(args.id, args.shots, not args.no_preview)
    print(f"saved {n} shot(s) to faces/{args.id}/")
    if not n:
        raise SystemExit("no shots captured -- nothing enrolled")

    vals = {f: getattr(args, f) for f in FIELDS}
    vals["name"] = vals["name"] or args.id
    for f in FIELDS:
        if vals[f] is None:
            vals[f] = input(f"{f.replace('_', ' ')}: ").strip()
    save_profile(args.id, vals)
    print(f"profile saved. curl -X POST http://127.0.0.1:8000/reload to pick it up live.")
