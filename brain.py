"""Per-person conversation insight. Never on the response path, never blocks.

Transcript goes in, {topic, shared_interest, suggested_question} comes out some
seconds later. Callers read the last cached answer, which may be stale or empty
-- a panel showing yesterday's topic beats a panel that blanks mid-conversation.

Config (either works, one client):
    OPENAI_API_KEY=sk-...
    GEMINI_API_KEY=...   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
                         LLM_MODEL=gemini-2.0-flash

    python brain.py      # self-check, runs without a key
"""
import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

FIRE_EVERY = 4        # utterances
FIRE_AFTER = 15.0     # seconds, whichever comes first
KEEP_LINES = 12       # transcript lines sent as context
FIELDS = ("topic", "shared_interest", "suggested_question")

PROMPT = """You are helping someone at a hackathon talk to {name}.

Their profile:
{profile}

Recent conversation:
{transcript}

Reply with ONLY a JSON object with exactly these keys:
  topic               - what they are talking about right now, under 8 words
  shared_interest     - something the two of them have in common, under 12 words
  suggested_question  - one specific question worth asking next, under 15 words
Use "" for anything you cannot infer. No markdown, no prose."""

_buffers = defaultdict(list)
_cache = {}
_fired = {}
_inflight = set()
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2)


def _client():
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=os.environ.get("LLM_BASE_URL") or None)


def _ask(prompt):
    client = _client()
    if client is None:
        return None
    r = client.chat.completions.create(
        model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=200,
    )
    return r.choices[0].message.content


def parse(raw):
    """Defensive by design: anything unparseable yields None, and None never
    overwrites a cached answer. A bad response must not blank a panel."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):                       # models add fences anyway
        text = text.split("```")[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return {k: str(obj.get(k) or "")[:120] for k in FIELDS}


def _work(pid, prompt):
    try:
        got = parse(_ask(prompt))
        if got:
            with _lock:
                _cache[pid] = got
    except Exception:
        pass                                          # stale cache is the fallback
    finally:
        with _lock:
            _inflight.discard(pid)


def add_utterance(pid, text, profile=None, now=None, ask=None):
    """Buffer a line; fire a call when it is worth one. Returns True if fired."""
    now = time.time() if now is None else now
    if not pid or not (text or "").strip():
        return False
    with _lock:
        _buffers[pid].append(text.strip())
        n = len(_buffers[pid])
        # The time trigger only applies once we've fired before, otherwise the very
        # first utterance looks infinitely overdue and burns a call on one line.
        due = n % FIRE_EVERY == 0 or (pid in _fired and now - _fired[pid] >= FIRE_AFTER)
        if not due or pid in _inflight:
            return False
        _fired[pid] = now
        _inflight.add(pid)
        lines = _buffers[pid][-KEEP_LINES:]

    prompt = PROMPT.format(
        name=(profile or {}).get("name", pid),
        profile=json.dumps(profile or {}, indent=1),
        transcript="\n".join(lines),
    )
    # ponytail: `ask` is only here so the self-check runs without a key
    (_pool.submit(_work, pid, prompt) if ask is None
     else _work_sync(pid, ask(prompt)))
    return True


def _work_sync(pid, raw):
    got = parse(raw)
    with _lock:
        _inflight.discard(pid)
        if got:
            _cache[pid] = got


def get(pid):
    return _cache.get(pid)


if __name__ == "__main__":
    good = '```json\n{"topic":"ONNX on edge","shared_interest":"both ship CV","suggested_question":"What killed your latency?"}\n```'
    assert parse(good)["topic"] == "ONNX on edge"
    assert parse("garbage") is None
    assert parse('{"topic":"x"}')["shared_interest"] == "", "missing keys become empty"
    assert parse('[1,2]') is None, "non-dict must not pass"
    assert parse(None) is None

    fired = [add_utterance("p1", f"line {i}", ask=lambda p: good) for i in range(4)]
    assert fired == [False, False, False, True], f"fired on wrong beat: {fired}"
    assert get("p1")["topic"] == "ONNX on edge"

    # A later bad response must not wipe the good cached answer.
    for i in range(4):
        add_utterance("p1", f"more {i}", ask=lambda p: "not json at all")
    assert get("p1")["topic"] == "ONNX on edge", "bad response blanked the panel"

    assert add_utterance("p2", "   ") is False, "blank utterance must not fire"
    assert get("p2") is None
    print("ok (no API key needed for this check)")
