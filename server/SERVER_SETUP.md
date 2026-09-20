# Conversation server

This directory contains the server only. The Raspberry Pi transcription pipeline is a
separate component. The Quest continuously posts camera frames to `/id`; the server
caches the newest frame and couples its identity result to conversation mode. Research
is precomputed and stored on the person's `people.db` row as `verbose_research` and
`concise_research`.

## Runtime flow

1. The Pi posts finalized transcript chunks to `POST /utterance`.
2. A configured start phrase creates a clean conversation session and asynchronously
   identifies the largest enrolled person in the newest Quest frame.
3. If nobody with a database profile and both research fields matches, the server ends
   the conversation and both panel endpoints remain blank.
4. A successful match loads cached research and publishes `research_ready` with only
   the concise version for panel one.
5. The server immediately generates panel-two talking points from verbose research
   plus the current transcript, then publishes `talking_points_ready`.
6. It regenerates talking points at the configured interval while transcription keeps
   appending chunks. Only one generation runs at once.
7. An end phrase publishes `conversation_ended` and drops the session's transcript and
   research. Late results carrying the old session ID are rejected or ignored.

The self-installing Quest `ConversationPanelClient` polls `GET /conversation/panel1` and
`GET /conversation/panel2`; each returns a `text` field plus structured fields. `text` is
empty before data is ready and immediately after the conversation ends.
`GET /conversation/events?after=<event_id>` remains an ordered diagnostic feed.

## Install and run

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp server/.env.example server/.env
python3 -m server.server
```

To log HTTP request metadata, transcript chunks, triggers, face-match confidence,
research context, and panel updates (image bytes are never logged):

```bash
python3 -m server.server --log
```

To enable the same logging while always identifying Ashley from the fixed local
screenshot instead of the image posted by the Quest:

```bash
python3 -m server.server --log-no-picture
```

Both flags enable the same pipeline logging. `--log-no-picture` additionally replaces
every image posted to `/id` with the fixed Ashley screenshot. Pipeline lines are tagged
as `trigger_detected`, `image_parsed`, `research_context`, `panel1_update`, and
`panel2_update`; failures are tagged `pipeline_error`. Research logs intentionally
include the full verbose model context.

Repeated requests from the same client IP to the same method/path are logged at most
once every 0.5 seconds, with suppressed repeats counted on the next line. This interval
only throttles console output; it never delays a Pi or Quest request. Override it with
`--log-interval SECONDS`, or use `--log-interval 0` to print every request.

Set `GEMINI_API_KEY` in `server/.env`. For a keyless local integration test, set
`TALKING_POINTS_PROVIDER=dummy`. Also replace the default start phrase before the demo.
Run exactly one server process/worker for this prototype because conversation state and
the event queue are intentionally in memory.

## Component contracts

### Transcript input

```http
POST /utterance
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

The feed records `identify_and_research_requested`, `research_ready`,
`talking_points_ready`, errors, and conversation end. Every event includes a
monotonically increasing `id` and its `session_id`; the built-in resolver handles
identity/research without an external event consumer.

### Quest panel output

```http
GET /conversation/panel1

{
  "text": "• Founder at Example\n• Interested in applied AI",
  "session_id": "...",
  "person_id": "jane-doe",
  "display_name": "Jane Doe",
  "bullets": ["Founder at Example", "Interested in applied AI"]
}
```

`GET /conversation/panel2` similarly returns `text`, `headline`, `talking_points`,
`suggested_question`, and `version`. Both endpoints return `text: ""` while unavailable
or inactive. The verbose research is retained only in server-side session state.

`concise_research` may be a JSON array encoded in SQLite TEXT or newline-delimited TEXT.
`verbose_research` must be non-empty. `POST /conversation/research` remains available as
an optional compatibility/testing injection point, but normal operation loads both
columns from the profile matched against the latest `/id` frame.

## Manual smoke test

Set `TALKING_POINTS_PROVIDER=dummy`, start the server, and allow the Quest to post at
least one `/id` frame. Then trigger a conversation through the Pi or manually:

```bash
curl -X POST http://127.0.0.1:8000/conversation/start
curl -X POST http://127.0.0.1:8000/utterance \
  -H 'Content-Type: application/json' \
  -d '{"chunk_id":"demo:1","text":"What kinds of AR tools are you exploring?"}'
curl 'http://127.0.0.1:8000/conversation/panel1'
curl 'http://127.0.0.1:8000/conversation/panel2'
curl -X POST http://127.0.0.1:8000/conversation/stop
```

Run the server self-check with:

```bash
python3 -m server.test_server
```
