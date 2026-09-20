"""Server self-checks. Run from the repo root: python3 -m server.test_server"""
import contextlib
import io
import json
import threading
import time

from fastapi.testclient import TestClient

from . import server as backend
from .server import (
    CONVERSATIONS,
    FACE_WIDTH_M,
    ConversationManager,
    RequestLogLimiter,
    _parse_talking_point_bullets,
    _remember_identity_result,
    _remember_latest_frame,
    _render_talking_point,
    _research_list,
    combine_research_context,
    configure_request_logging,
    distance_m,
    iou,
    log_identity_result,
    resolve_latest_frame_research,
)


def wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for background work")


def test_distance():
    near = distance_m(bbox_w=120, img_w=640, hfov_deg=80)
    far = distance_m(bbox_w=60, img_w=640, hfov_deg=80)
    assert near < far
    assert abs(far / near - 2) < 0.01
    assert abs(distance_m(140, 640, 80) - distance_m(70, 320, 80)) < 0.01
    assert distance_m(140, 640, 110) < distance_m(140, 640, 80)
    assert distance_m(0, 640, 80) is None


def test_iou():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0.0
    assert abs(iou([0, 0, 10, 10], [0, 0, 10, 5]) - 0.5) < 1e-9


def test_conversation_lifecycle():
    generated_from = []

    def generate(person_id, profile, research, transcript):
        generated_from.append(list(transcript))
        return {
            "headline": "Developer tools",
            "talking_points": ["Ask about current users"],
            "suggested_question": "What surprised you while building it?",
        }

    manager = ConversationManager(
        generate,
        refresh_seconds=0.05,
        start_phrases=["hello network"],
        end_phrases=["bye"],
    )
    try:
        manager.ingest("Hello network", chunk_id="pi:1")
        first_state = manager.state()
        session_id = first_state["session_id"]
        assert first_state["active"] and first_state["history_count"] == 0
        manager.ingest("Hello network", chunk_id="pi:1")
        assert manager.state()["session_id"] == session_id
        assert [event["type"] for event in manager.events_after(0)["events"]] == [
            "conversation_started", "identify_and_research_requested"
        ]

        manager.ingest("We are making a wearable networking assistant", chunk_id="pi:2")
        accepted, error = manager.provide_research(
            session_id,
            "alex",
            {"name": "Alex"},
            "Alex builds developer tools.",
            ["Builds developer tools"],
            ["https://example.com/alex"],
        )
        assert accepted and error is None
        assert "Alex builds developer tools" not in json.dumps(manager.events_after(0)), (
            "verbose research must never be sent through the consumer event feed"
        )
        wait_for(lambda: any(
            event["type"] == "talking_points_ready"
            for event in manager.events_after(0)["events"]
        ))
        assert generated_from[0] == ["We are making a wearable networking assistant"]

        manager.ingest("Maybe we should discuss latency", chunk_id="pi:3")
        assert manager.state()["active"], "'bye' must not match inside another word"
        assert manager.state()["history_count"] == 2

        manager.ingest("Okay, bye!", chunk_id="pi:4")
        assert manager.state() == {"active": False, "session_id": None, "history_count": 0}
        assert manager.events_after(0)["events"][-1]["type"] == "conversation_ended"
    finally:
        manager.close()


def test_triggers_across_chunks_and_no_active_restart():
    manager = ConversationManager(
        lambda *args: {},
        start_phrases=["start conversation"],
        end_phrases=["goodbye"],
    )
    try:
        manager.ingest("start con", chunk_id="split:1")
        assert not manager.state()["active"]
        manager.ingest("versation", chunk_id="split:2")
        session_id = manager.state()["session_id"]
        assert session_id

        manager.ingest("start conversation", chunk_id="split:3")
        assert manager.state()["session_id"] == session_id, "start words must not restart an active session"

        manager.ingest("good", chunk_id="split:4")
        assert manager.state()["active"]
        manager.ingest("bye", chunk_id="split:5")
        assert not manager.state()["active"]
    finally:
        manager.close()


def test_utterance_endpoint_exit_trigger_clears_panels_and_resets_session():
    def resolve():
        return {
            "person_id": "ashley-moon",
            "profile": {"name": "Ashley Moon"},
            "verbose": "Ashley context",
            "concise": ["Ashley fact"],
            "sources": [],
        }

    manager = ConversationManager(
        lambda *args: {
            "headline": "",
            "talking_points": ["**Follow-up:** Ask what comes next."],
            "suggested_question": "",
        },
        refresh_seconds=10,
        start_phrases=["hello"],
        end_phrases=["exit"],
        identity_resolver=resolve,
    )
    original_manager = backend.CONVERSATIONS
    original_add_utterance = backend.brain.add_utterance
    original_get = backend.brain.get
    backend.CONVERSATIONS = manager
    backend.brain.add_utterance = lambda *args, **kwargs: False
    backend.brain.get = lambda *args, **kwargs: None
    client = TestClient(backend.api)
    try:
        started = client.post(
            "/utterance", json={"chunk_id": "http:1", "text": "Hello, I'm Andrew."}
        )
        assert started.status_code == 200
        first_session_id = started.json()["conversation"]["session_id"]
        assert first_session_id
        wait_for(lambda: bool(client.get("/conversation/panel2").json()["text"]))
        assert client.get("/conversation/panel1").json()["text"]

        ended = client.post(
            "/utterance", json={"chunk_id": "http:2", "text": "Okay, EXIT!!!"}
        )
        assert ended.status_code == 200
        assert ended.json()["conversation"] == {
            "active": False,
            "session_id": None,
            "history_count": 0,
        }
        assert client.get("/conversation/panel1").json()["text"] == ""
        assert client.get("/conversation/panel2").json()["text"] == ""

        events = client.get("/conversation/events", params={"after": 0}).json()["events"]
        ended_event = next(event for event in reversed(events) if event["type"] == "conversation_ended")
        assert ended_event["session_id"] == first_session_id
        assert ended_event["reason"] == "trigger"
        assert ended_event["trigger"] == "exit"

        restarted = client.post(
            "/utterance", json={"chunk_id": "http:3", "text": "hello"}
        ).json()["conversation"]
        assert restarted["active"]
        assert restarted["session_id"] != first_session_id
        assert restarted["history_count"] == 0
    finally:
        client.close()
        backend.CONVERSATIONS = original_manager
        backend.brain.add_utterance = original_add_utterance
        backend.brain.get = original_get
        manager.close()


def test_ended_session_discards_slow_generation():
    release = threading.Event()

    def slow_generate(*args):
        release.wait(timeout=1)
        return {"headline": "old", "talking_points": ["old"], "suggested_question": "old"}

    manager = ConversationManager(slow_generate, refresh_seconds=1)
    try:
        old_session = manager.start()
        accepted, _ = manager.provide_research(
            old_session, "old-person", {"name": "Old Person"}, "old research", ["old"]
        )
        assert accepted
        manager.stop()
        new_session = manager.start()
        accepted, error = manager.provide_research(
            old_session, "late-person", {}, "late research", ["late"]
        )
        assert not accepted and "stale" in error
        release.set()
        time.sleep(0.05)
        assert not any(
            event["type"] == "talking_points_ready" and event["session_id"] == new_session
            for event in manager.events_after(0)["events"]
        )
    finally:
        release.set()
        manager.close()


def test_latest_identity_populates_and_clears_panels():
    generated = []

    def resolve():
        return {
            "person_id": "alex",
            "profile": {"name": "Alex", "verbose_research": "Long context"},
            "verbose": "Long context",
            "concise": ["Builds developer tools", "Interested in spatial computing"],
            "sources": [],
        }

    def generate(person_id, profile, research, transcript):
        generated.append((person_id, research["verbose"], list(transcript)))
        return {
            "headline": "Ask about AR",
            "talking_points": ["Compare headset constraints"],
            "suggested_question": "What interaction felt most natural?",
        }

    manager = ConversationManager(
        generate,
        refresh_seconds=10,
        end_phrases=["bye", "see you later"],
        identity_resolver=resolve,
    )
    try:
        manager.start()
        wait_for(lambda: bool(manager.panel_two()["text"]))
        assert manager.panel_one()["bullets"] == [
            "Builds developer tools", "Interested in spatial computing"
        ]
        assert "Long context" not in manager.panel_one()["text"]
        assert manager.panel_two()["talking_points"] == ["Compare headset constraints"]
        assert generated == [(
            "alex",
            "Concise research:\n"
            "- Builds developer tools\n"
            "- Interested in spatial computing\n\n"
            "Verbose research:\n"
            "Long context",
            [],
        )]

        manager.ingest("See you later!")
        assert manager.panel_one()["text"] == ""
        assert manager.panel_two()["text"] == ""
        assert not manager.state()["active"]
    finally:
        manager.close()


def test_failed_identity_ends_conversation_and_blanks_panels():
    manager = ConversationManager(
        lambda *args: {},
        identity_resolver=lambda: None,
    )
    try:
        manager.start()
        wait_for(lambda: not manager.state()["active"])
        assert manager.panel_one()["text"] == ""
        assert manager.panel_two()["text"] == ""
        events = manager.events_after(0)["events"]
        assert events[-2]["type"] == "conversation_error"
        assert events[-1]["type"] == "conversation_ended"
        assert events[-1]["reason"] == "identity_not_found"
    finally:
        manager.close()


def test_research_list_accepts_sqlite_text_formats():
    assert _research_list('["one", "two"]') == ["one", "two"]
    assert _research_list("- one\n• two") == ["one", "two"]


def test_combined_research_context_keeps_every_concise_item():
    concise = [f"Fact {number}" for number in range(1, 8)]
    combined, normalized = combine_research_context("Raw research", concise)
    assert normalized == concise
    assert combined.index("Fact 1") < combined.index("Raw research")
    assert "Fact 7" in combined


def test_bullet_only_gemini_response_is_parsed_and_rendered_for_quest():
    points = _parse_talking_point_bullets(
        "* **Project challenge:** Ask what proved hardest.\n"
        "- **Future direction:** Ask what they want to explore next."
    )
    assert points == [
        "**Project challenge:** Ask what proved hardest.",
        "**Future direction:** Ask what they want to explore next.",
    ]
    assert _render_talking_point(points[0]) == (
        "• <b>Project challenge:</b> Ask what proved hardest."
    )


def test_latest_frame_resolver_uses_new_database_columns():
    token = _remember_latest_frame(b"jpeg", 7, 80.0)
    _remember_identity_result(token, {
        "faces": [{
            "name": "alex",
            "profile": {
                "name": "Alex",
                "verbose_research": "Full model-facing context",
                "concise_research": '["First bullet", "Second bullet"]',
            },
        }],
    })
    result = resolve_latest_frame_research()
    assert result["person_id"] == "alex"
    assert result["verbose"] == "Full model-facing context"
    assert result["concise"] == ["First bullet", "Second bullet"]


def test_request_log_limiter_is_per_source_method_and_path():
    limiter = RequestLogLimiter(0.5)
    assert limiter.record("quest", "GET", "/conversation/panel1", now=0.0) == 0
    assert limiter.record("quest", "GET", "/conversation/panel1", now=0.1) is None
    assert limiter.record("quest", "GET", "/conversation/panel1", now=0.49) is None
    assert limiter.record("quest", "GET", "/conversation/panel1", now=0.5) == 2
    assert limiter.record("quest", "GET", "/conversation/panel2", now=0.1) == 0
    assert limiter.record("pi", "POST", "/utterance", now=0.1) == 0


def test_pipeline_logging_covers_trigger_identity_research_and_panels():
    manager = ConversationManager(
        lambda *args: {
            "headline": "Ask about AR",
            "talking_points": ["Discuss spatial interfaces"],
            "suggested_question": "What are you building next?",
        },
        refresh_seconds=10,
    )
    output = io.StringIO()
    configure_request_logging(True)
    try:
        with contextlib.redirect_stdout(output):
            manager.ingest("start conversation", chunk_id="log:1")
            session_id = manager.state()["session_id"]
            accepted, error = manager.provide_research(
                session_id,
                "ashley-moon",
                {"name": "Ashley Moon"},
                "Verbose Ashley context",
                ["Concise Ashley fact"],
            )
            assert accepted and error is None
            wait_for(lambda: "panel2_update" in output.getvalue())
            log_identity_result(
                {
                    "frame_id": 12,
                    "faces": [{"name": "ashley-moon", "score": 0.536, "bbox": [1, 2, 3, 4]}],
                    "ms": {"detect": 10, "total": 20},
                },
                "fixed_ashley",
            )
            manager.ingest("bye", chunk_id="log:2")
        logged = output.getvalue()
        for event in (
            "trigger_detected",
            "image_parsed",
            "research_context",
            "panel1_update",
            "panel2_update",
        ):
            assert event in logged
        assert '"person": "ashley-moon"' in logged
        assert '"confidence": 0.536' in logged
        assert "Concise Ashley fact" in logged
        assert "Verbose Ashley context" in logged
        assert "What are you building next?" in logged
    finally:
        configure_request_logging(False)
        manager.close()


if __name__ == "__main__":
    try:
        test_distance()
        test_iou()
        test_conversation_lifecycle()
        test_triggers_across_chunks_and_no_active_restart()
        test_utterance_endpoint_exit_trigger_clears_panels_and_resets_session()
        test_ended_session_discards_slow_generation()
        test_latest_identity_populates_and_clears_panels()
        test_failed_identity_ends_conversation_and_blanks_panels()
        test_research_list_accepts_sqlite_text_formats()
        test_combined_research_context_keeps_every_concise_item()
        test_bullet_only_gemini_response_is_parsed_and_rendered_for_quest()
        test_latest_frame_resolver_uses_new_database_columns()
        test_request_log_limiter_is_per_source_method_and_path()
        test_pipeline_logging_covers_trigger_identity_research_and_panels()
        print(f"ok (FACE_WIDTH_M={FACE_WIDTH_M})")
    finally:
        CONVERSATIONS.close()
