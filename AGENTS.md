# Project Context

## Product vision

This project is a wearable, real-time networking assistant: conceptually, "Cluely for networking." A user wears a Raspberry Pi audio device and Meta Quest 3 headset. The system listens for a configurable trigger phrase, identifies the person in front of the user, researches that person, and privately surfaces useful context and live talking points during the conversation.

Treat this description as the intended product architecture. When changing code, first inspect what is already implemented rather than assuming every component below exists.

## Devices and responsibilities

- **Raspberry Pi:** Continuously captures audio, transcribes it, and streams transcript events to the server. Trigger-word detection may happen on the Pi or server; preserve the existing implementation unless a task explicitly changes that boundary.
- **Server:** Owns conversation/session state, receives transcript events, coordinates face identification and person research, invokes the talking-point generator, and pushes UI-ready updates to the headset.
- **Meta Quest 3:** Captures the current view when requested, segments/detects the visible face, participates in matching it against the enrolled-person database, and displays concise research and talking points.
- **Person database:** Associates face identity data with known profile information.
- **Research agent/harness:** Uses the matched profile to research the person online. The current concept mentions Claude Code, but keep the provider/harness replaceable unless a task explicitly requires a particular integration.
- **Talking-point generator:** Combines the accumulated transcript with the researched profile to produce a refreshed, concise set of useful talking points for the user.

## Conversation lifecycle

1. Outside conversation mode, the Raspberry Pi continues listening/transcribing and the system watches for the configured start trigger.
2. When the start trigger is detected, the server begins a **new conversation session**. It must discard or isolate transcript context from the previous session so information does not leak between conversations.
3. The server immediately signals the Meta Quest to capture/process the visible face.
4. The face is segmented/detected and compared with the person database. A successful match returns the person's associated profile information.
5. The research component enriches that profile with online research and returns two representations:
   - a short bullet-point version for immediate display in the Meta Quest UI;
   - a verbose version for downstream reasoning by the talking-point generator.
6. As soon as research is available, the system performs the first talking-point generation pass using the verbose research plus all transcript context belonging to the active conversation.
7. Throughout face matching, research, and generation, Raspberry Pi transcription continues independently. New transcript events are appended to the active session without waiting for those slower jobs.
8. While conversation mode remains active, the server periodically regenerates talking points from the latest active-session transcript plus the verbose research. The initial target cadence is roughly every four seconds, but it should be configurable and generation jobs should not pile up or publish stale results.
9. When the configured end trigger (for example, "bye") is detected, the server ends the active session, stops its periodic work, and clears or isolates its conversation history.

## Concurrency and state invariants

- Audio ingestion/transcription must remain live while face lookup, research, and talking-point generation run concurrently.
- Every transcript, research result, generation request, and UI update should be associated with a conversation/session ID.
- Results from an old or ended session must never update the current Meta Quest UI.
- Starting or ending a conversation should cancel, ignore, or supersede in-flight work from the previous session.
- Only one talking-point generation job per session should normally be in flight; coalesce or skip timer ticks rather than building a backlog.
- Preserve chronological transcript ordering and make repeated/retried events idempotent where practical.
- Research bullet points are presentation output; verbose research is reasoning context. Do not accidentally send the verbose payload to the constrained headset UI unless requested.

## Open implementation choices

The exact start phrase, end phrases, refresh interval, speech-to-text provider, LLM/provider, face-matching location, transport protocol, and retention policy are not final unless established elsewhere in the code or by the user. Prefer configuration and clean interfaces over hard-coding these choices.
