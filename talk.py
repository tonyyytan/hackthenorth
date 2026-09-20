"""Manual conversation-insight trigger, for testing/demoing without the Pi's mic pipeline.

Simulates what the Pi normally sends to POST /utterance: types a line of "what was
said" for a given enrolled person, fires the LLM immediately (bypassing brain.py's
normal 4-utterances/15s batching via force=true), and prints the generated
{topic, shared_interest, suggested_question}. If that person is currently in view
of the Quest's passthrough camera (a real /id call has seen them), the caption box
picks up the same cached result on its next poll -- no extra step needed.

Usage:
    python talk.py                       # interactive loop
    python talk.py thor "so what do you do for work?"   # one-shot
"""
import sys
import time
import urllib.request
import json

SERVER = "http://localhost:8000"


def _post(person_id, text, force):
    body = json.dumps({"person_id": person_id, "text": text, "force": force}).encode()
    req = urllib.request.Request(f"{SERVER}/utterance", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def say(person_id, text, poll_seconds=6.0):
    """The insight call runs async server-side -- the immediate response usually reads the
    cache before that finishes. Poll with blank utterances until it actually lands."""
    res = _post(person_id, text, force=True)
    deadline = time.time() + poll_seconds
    while not (res.get("insight") or {}).get("topic") and time.time() < deadline:
        time.sleep(0.4)
        res = _post(person_id, "", force=False)
    return res


def show(res):
    if not res.get("ok"):
        print(f"  error: {res.get('error')}")
        return
    insight = res.get("insight") or {}
    print(f"  fired: {res['fired']}")
    print(f"  topic:              {insight.get('topic', '')}")
    print(f"  shared_interest:    {insight.get('shared_interest', '')}")
    print(f"  suggested_question: {insight.get('suggested_question', '')}")


def main():
    if len(sys.argv) >= 3:
        show(say(sys.argv[1], " ".join(sys.argv[2:])))
        return

    print(f"Talking to {SERVER}. Check /health first if you don't know who's enrolled.")
    print("Type: <person_id> <text you'd say>   (blank line to quit)\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        parts = line.split(" ", 1)
        if len(parts) < 2:
            print("  need both a person_id and text, e.g.: thor so what do you do")
            continue
        try:
            show(say(parts[0], parts[1]))
        except Exception as e:
            print(f"  request failed: {e} (is server.py running?)")


if __name__ == "__main__":
    main()
