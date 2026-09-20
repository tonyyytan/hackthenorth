"""Server self-checks. Run from the repo root: python3 -m server.test_server"""
import json
import threading
import time

from .server import CONVERSATIONS, FACE_WIDTH_M, ConversationManager, distance_m, iou


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


if __name__ == "__main__":
    try:
        test_distance()
        test_iou()
        test_conversation_lifecycle()
        test_triggers_across_chunks_and_no_active_restart()
        test_ended_session_discards_slow_generation()
        print(f"ok (FACE_WIDTH_M={FACE_WIDTH_M})")
    finally:
        CONVERSATIONS.close()
