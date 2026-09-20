"""Run one image through the Quest face-identification and research pipeline.

From the repository root:

    .venv/bin/python -m server.simulate_quest_capture /path/to/capture.jpg

The image is posted to the real FastAPI ``POST /id`` route as raw JPEG bytes. The
script then runs the same cached ``people.db`` research resolver used when a live
conversation starts. It does not add the image to ``faces/`` or modify the database.
"""

import argparse
import json
import sys
import time
from pathlib import Path


def log(stage, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[quest-test {timestamp}] {stage:<10} {message}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simulate a Meta Quest image upload and cached-research lookup."
    )
    parser.add_argument("image", type=Path, help="JPEG or PNG capture to test")
    parser.add_argument(
        "--frame-id",
        type=int,
        default=None,
        help="Quest frame ID (default: current Unix time in milliseconds)",
    )
    parser.add_argument(
        "--hfov",
        type=float,
        default=80.0,
        help="Capture horizontal field of view in degrees (default: 80)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete /id response after the readable summary",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        log("error", f"image does not exist: {image_path}")
        return 1
    if not 1 <= args.hfov < 180:
        log("error", "--hfov must be at least 1 and less than 180 degrees")
        return 1

    image_bytes = image_path.read_bytes()
    if not image_bytes:
        log("error", f"image is empty: {image_path}")
        return 1
    frame_id = args.frame_id
    if frame_id is None:
        frame_id = int(time.time() * 1000)

    log("input", f"{image_path} ({len(image_bytes):,} bytes)")
    log("startup", "loading the production face gallery and people.db")

    # Importing the server initializes the same InsightFace models and enrollment
    # gallery used in production. Keep this import after input validation because it
    # is intentionally the slowest part of the test.
    from fastapi.testclient import TestClient
    from server import server as backend

    backend.configure_request_logging(True, interval_seconds=0)
    log(
        "gallery",
        f"{len(backend.NAMES)} people / {len(backend.GALLERY)} enrollment images; "
        f"match threshold={backend.THRESHOLD:.3f}",
    )
    log("upload", f"POST /id?frame_id={frame_id}&hfov={args.hfov:g}")

    content_type = "image/png" if image_path.suffix.casefold() == ".png" else "image/jpeg"
    try:
        with TestClient(backend.api) as client:
            response = client.post(
                "/id",
                params={"frame_id": frame_id, "hfov": args.hfov},
                content=image_bytes,
                headers={"Content-Type": content_type},
            )
            response.raise_for_status()
            identity = response.json()

            if identity.get("error"):
                log("error", f"identity endpoint failed: {identity['error']}")
                return 1

            faces = identity.get("faces", [])
            timing = identity.get("ms", {})
            log(
                "detect",
                f"{len(faces)} face(s); embedded={identity.get('embedded', 0)}; "
                f"detect={timing.get('detect', '?')}ms total={timing.get('total', '?')}ms",
            )
            for index, face in enumerate(faces, start=1):
                person_id = face.get("name") or "UNKNOWN"
                profile = face.get("profile") or {}
                display_name = profile.get("name")
                label = f"{person_id} ({display_name})" if display_name else person_id
                log(
                    "match",
                    f"face {index}: {label}; score={face.get('score', 0):.3f}; "
                    f"bbox={face.get('bbox')}; distance~{face.get('distance_m')}m",
                )

            # POST /id cached this exact frame/result. This resolver is what the
            # conversation manager calls to select a recognized person and retrieve
            # their precomputed research from people.db.
            research = backend.resolve_latest_frame_research()
            if research is None:
                if any(face.get("name") for face in faces):
                    log(
                        "research",
                        "recognized face has no complete profile + cached research",
                    )
                else:
                    log("research", "no recognized face; research lookup skipped")
            else:
                log(
                    "research",
                    f"resolved {research['person_id']}; "
                    f"{len(research['concise'])} concise bullet(s), "
                    f"{len(research['verbose']):,} verbose characters, "
                    f"{len(research['sources'])} source(s)",
                )
                for bullet in research["concise"]:
                    log("bullet", bullet)

            if args.json:
                print(json.dumps(identity, indent=2, default=str))

            recognized = [face for face in faces if face.get("name")]
            if recognized:
                log("result", f"detected as {recognized[0]['name']}")
                return 0
            log("result", "no enrolled person matched above the threshold")
            return 2
    finally:
        # TestClient normally triggers this through FastAPI shutdown. Calling close is
        # idempotent and also covers failures before the client context fully starts.
        backend.CONVERSATIONS.close()


if __name__ == "__main__":
    sys.exit(main())
