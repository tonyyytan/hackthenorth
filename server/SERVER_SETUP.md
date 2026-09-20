# Conversation server

This directory contains the server only. The Raspberry Pi transcription pipeline and
the face/database/research pipeline are separate components with small HTTP contracts.
The conversation pipeline never captures audio, calls AssemblyAI, segments faces,
matches identities, or researches people. The pre-existing standalone `/id` identity
endpoint remains available but is not coupled to conversation mode.

## Runtime flow

1. The Pi posts finalized transcript chunks to `POST /transcript`.
2. A configured start phrase creates a clean conversation session and publishes an
   `identify_and_research_requested` event containing the new `session_id`.
3. The separate face/research component consumes that event, performs its full flow,
   then posts the matched profile plus verbose and concise research to
   `POST /conversation/research` with the same `session_id`.
4. The server publishes `research_ready` with only the concise version for panel one.
5. The server immediately generates panel-two talking points from verbose research
   plus the current transcript, then publishes `talking_points_ready`.
6. It regenerates talking points at the configured interval while transcription keeps
   appending chunks. Only one generation runs at once.
7. An end phrase publishes `conversation_ended` and drops the session's transcript and
   research. Late results carrying the old session ID are rejected or ignored.

Consumers poll `GET /conversation/events?after=<event_id>` for ordered events. This is
deliberately transport-simple for the prototype; the event contract can later sit behind
WebSockets without changing the state machine.

## Install and run

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp server/.env.example server/.env
python3 -m server.server
```

Set `GEMINI_API_KEY` in `server/.env`. For a keyless local integration test, set
`TALKING_POINTS_PROVIDER=dummy`. Also replace the default start phrase before the demo.
Run exactly one server process/worker for this prototype because conversation state and
the event queue are intentionally in memory.

## Component contracts

### Transcript input

```http
POST /transcript
Content-Type: application/json

{
  "chunk_id": "pi-session-42:turn-18",
  "text": "What are you working on right now?"
}
```

`chunk_id` is optional but strongly recommended. Reusing it is idempotent, which makes
network retries safe and prevents a retried trigger from starting a second session.

### Event output

```http
GET /conversation/events?after=0
```

The face/research component reacts to `identify_and_research_requested`. The Meta Quest
consumer reacts to `research_ready`, `talking_points_ready`, and `conversation_ended`.
Every event includes a monotonically increasing `id` and its `session_id`.

### Face/research result input

```http
POST /conversation/research
Content-Type: application/json

{
  "session_id": "SESSION_ID_FROM_THE_EVENT",
  "person_id": "jane-doe",
  "profile": {
    "name": "Jane Doe",
    "role": "Founder at Example",
    "links": ["https://example.com/jane"]
  },
  "verbose_research": "Detailed model-facing research goes here...",
  "concise_research": [
    "Founder at Example",
    "Recently launched a developer platform",
    "Interested in applied AI"
  ],
  "sources": ["https://example.com/jane"]
}
```

The verbose research is retained server-side for generation. Only concise research is
included in the `research_ready` event intended for the headset.

## Manual smoke test

Set `TALKING_POINTS_PROVIDER=dummy`, start the server, then:

```bash
curl -X POST http://127.0.0.1:8000/conversation/start
curl 'http://127.0.0.1:8000/conversation/events?after=0'
```

Copy the returned session ID into:

```bash
curl -X POST http://127.0.0.1:8000/transcript \
  -H 'Content-Type: application/json' \
  -d '{"chunk_id":"demo:1","text":"What kinds of AR tools are you exploring?"}'

curl -X POST http://127.0.0.1:8000/conversation/research \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id":"PASTE_SESSION_ID",
    "person_id":"demo-person",
    "profile":{"name":"Demo Person"},
    "verbose_research":"Demo Person builds developer tools and is interested in AR.",
    "concise_research":["Builds developer tools","Interested in AR"]
  }'

curl 'http://127.0.0.1:8000/conversation/events?after=0'
curl -X POST http://127.0.0.1:8000/conversation/stop
```

Run the server self-check with:

```bash
python3 -m server.test_server
```
