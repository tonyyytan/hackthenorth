"""Exercise the complete Quest + transcript + Gemini conversation pipeline.

Run from the repository root, passing a capture of an enrolled person:

    .venv/bin/python -m server.simulate_conversation /path/to/capture.png

The simulation uses the real FastAPI routes and Gemini adapter. It does not modify
the face gallery or people.db. Identity resolution is deliberately gated until the
whole transcript stream has arrived so the first talking-point context is repeatable.
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


DEFAULT_UTTERANCES = [
    "random text",
    "what da hell",
    "hi how are you",
    "im good thanks, what about you",
    "im doing not so great",
    "today i tripped and fell and it hurt",
    "oh i am so sorry",
]


def log(stage, message):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[conversation-test {timestamp}] {stage:<12} {message}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simulate Quest identity, streamed utterances, Gemini, and Unity GETs."
    )
    parser.add_argument("image", type=Path, help="Quest JPEG or PNG capture")
    parser.add_argument("--trigger", default="hi", help="Start trigger (default: hi)")
    parser.add_argument("--hfov", type=float, default=80.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser.parse_args()


def main():
    args = parse_args()
    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        log("error", f"image does not exist: {image_path}")
        return 1
    if not 1 <= args.hfov < 180:
        log("error", "--hfov must be at least 1 and less than 180")
        return 1

    # ConversationManager reads this while server.server is imported. This changes
    # only the simulation process; it does not edit server/.env.
    os.environ["CONVERSATION_START_PHRASES"] = args.trigger

    image_bytes = image_path.read_bytes()
    log("startup", f"loading pipeline for {image_path} ({len(image_bytes):,} bytes)")

    from fastapi.testclient import TestClient
    from server import server as backend

    backend.configure_request_logging(True, interval_seconds=0)
    identity_gate = threading.Event()
    identity_returned = threading.Event()
    generation_called = threading.Event()
    captured_context = {}

    real_resolver = backend.resolve_latest_frame_research
    real_generator = backend.generate_talking_points

    def gated_resolver():
        log("identity", "worker started; waiting while transcript ingestion continues")
        if not identity_gate.wait(args.timeout):
            raise TimeoutError("transcript stream did not release identity resolver")
        result = real_resolver()
        if result:
            log(
                "identity",
                f"resolver returning {result['person_id']} with "
                f"{len(result['concise'])} concise item(s) and "
                f"{len(result['verbose']):,} raw verbose characters",
            )
        else:
            log("identity", "resolver returning no match")
        identity_returned.set()
        return result

    def traced_generator(person_id, profile, research, transcript):
        if not identity_returned.is_set():
            raise AssertionError("talking-point generation ran before identity returned")
        captured_context.update(
            person_id=person_id,
            profile=dict(profile),
            concise=list(research.get("concise", [])),
            combined_research=str(research.get("verbose", "")),
            transcript=list(transcript),
        )
        generation_called.set()
        log("gemini", "called only after identity/research returned; sending captured context")
        return real_generator(person_id, profile, research, transcript)

    backend.CONVERSATIONS.set_identity_resolver(gated_resolver)
    backend.CONVERSATIONS.generate = traced_generator

    # /utterance also feeds an unrelated legacy insight generator. Disable only that
    # legacy side path so this test makes exactly one model call: the Gemini pipeline
    # under test.
    backend.brain.add_utterance = lambda *unused_args, **unused_kwargs: False

    frame_id = int(time.time() * 1000)
    content_type = "image/png" if image_path.suffix.casefold() == ".png" else "image/jpeg"
    try:
        with TestClient(backend.api) as client:
            log("quest", f"POST /id frame_id={frame_id}")
            identity_response = client.post(
                "/id",
                params={"frame_id": frame_id, "hfov": args.hfov},
                content=image_bytes,
                headers={"Content-Type": content_type},
            )
            identity_response.raise_for_status()
            identity = identity_response.json()
            faces = identity.get("faces", [])
            if identity.get("error") or not any(face.get("name") for face in faces):
                log("error", f"capture was not recognized: {json.dumps(identity)}")
                return 2
            first_match = next(face for face in faces if face.get("name"))
            log(
                "quest",
                f"capture matched {first_match['name']} at score={first_match['score']:.3f}",
            )

            session_id = None
            for index, utterance in enumerate(DEFAULT_UTTERANCES, start=1):
                response = client.post(
                    "/utterance",
                    json={"chunk_id": f"simulation:{index}", "text": utterance},
                )
                response.raise_for_status()
                state = response.json()["conversation"]
                log(
                    "utterance",
                    f"{index}: {utterance!r} -> active={state['active']} "
                    f"history_count={state['history_count']}",
                )
                if index < 3 and state["active"]:
                    raise AssertionError("conversation started before the 'hi' trigger")
                if index == 3:
                    if not state["active"]:
                        raise AssertionError("'hi' did not start the conversation")
                    session_id = state["session_id"]
                    log("trigger", f"{args.trigger!r} started session {session_id}")
                if index >= 3 and state.get("session_id") != session_id:
                    raise AssertionError("session changed while streaming utterances")

            expected_transcript = [
                "how are you",
                "im good thanks, what about you",
                "im doing not so great",
                "today i tripped and fell and it hurt",
                "oh i am so sorry",
            ]
            with backend.CONVERSATIONS.lock:
                memory = list(backend.CONVERSATIONS.session["transcript"])
            log("memory", json.dumps(memory, ensure_ascii=False))
            if memory != expected_transcript:
                raise AssertionError(
                    f"unexpected active-session memory: {memory!r}; "
                    f"expected {expected_transcript!r}"
                )
            log("memory", "pre-trigger noise excluded; trigger word removed; ordering preserved")

            log("pipeline", "transcript complete; releasing identity/research step")
            identity_gate.set()

            deadline = time.monotonic() + args.timeout
            panel_two = None
            while time.monotonic() < deadline:
                state = client.get("/conversation/state").json()
                if state.get("last_error"):
                    raise RuntimeError(state["last_error"])
                candidate = client.get("/conversation/panel2").json()
                if candidate.get("version", 0) > 0:
                    panel_two = candidate
                    break
                time.sleep(0.1)
            if panel_two is None:
                raise TimeoutError("timed out waiting for Gemini talking points")
            if not generation_called.is_set():
                raise AssertionError("panel two updated without the traced Gemini call")

            combined = captured_context["combined_research"]
            concise_header = "Concise research:\n"
            verbose_header = "Verbose research:\n"
            if not combined.startswith(concise_header):
                raise AssertionError("combined model context does not start with concise research")
            if verbose_header not in combined:
                raise AssertionError("combined model context is missing verbose research")
            if captured_context["transcript"] != expected_transcript:
                raise AssertionError("Gemini did not receive the expected transcript snapshot")

            log("context", f"person_id={captured_context['person_id']}")
            log("context", f"transcript={json.dumps(captured_context['transcript'])}")
            print("\n===== GEMINI RESEARCH CONTEXT =====")
            print(combined)
            print("===== END GEMINI RESEARCH CONTEXT =====\n")

            panel_one = client.get("/conversation/panel1").json()
            panel_two = client.get("/conversation/panel2").json()
            if panel_one.get("session_id") != session_id:
                raise AssertionError("panel one does not belong to the active session")
            if panel_two.get("session_id") != session_id:
                raise AssertionError("panel two does not belong to the active session")
            if panel_one.get("bullets") != captured_context["concise"]:
                raise AssertionError("Unity panel one differs from concise research context")
            if not panel_two.get("talking_points"):
                raise AssertionError("Unity panel two has no talking points")

            events = client.get("/conversation/events", params={"after": 0}).json()["events"]
            event_types = [event["type"] for event in events]
            if event_types.index("research_ready") > event_types.index("talking_points_ready"):
                raise AssertionError("talking points published before research")
            log("events", " -> ".join(event_types))
            print("\n===== UNITY GET /conversation/panel1 =====")
            print(json.dumps(panel_one, indent=2, ensure_ascii=False))
            print("\n===== UNITY GET /conversation/panel2 =====")
            print(json.dumps(panel_two, indent=2, ensure_ascii=False))
            log("result", "all end-to-end assertions passed")
            return 0
    finally:
        identity_gate.set()
        backend.CONVERSATIONS.close()


if __name__ == "__main__":
    sys.exit(main())
