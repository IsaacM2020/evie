# Evie

A voice assistant that lives on my Mac, hears everything, and only acts when it's actually me talking to it.

Most "AI assistants" are a chat box with a mic button bolted on. Evie isn't that. She runs an always-on open mic, tells my voice apart from everyone else's in the room, decides in ~400ms whether a sentence was even meant for her, and — if it was — either answers instantly, runs a real background job through Claude Code, or takes control of my Mac (Safari, Spotify, Calendar, any app) to get it done. She never fakes confidence: if she's not sure, she asks one question, not five.

**The number I'm proudest of:** across 178 hand-labeled test sentences — including people talking *near* her, not to her — **false_action = 0**. She has never once acted on speech that wasn't meant for her.

**Talk to her:** hold left ⌃⌥ (Control+Option), say the thing, let go — or flip on the Live open mic (⌃⌥⌘) and just talk normally, no key needed. She lives in a small glass capsule on the edge of the screen that grows into a card when there's something to show: an answer, a "which one?" list, a job with a Stop button. ⌃⌥T switches her to text-only for class. Her voice is Pocket TTS ("eve"), streamed so the first sound lands ~30ms after she starts a sentence. Background jobs run on Claude Haiku or Sonnet — picked automatically per job, Opus never touches production.

Full build log, every architecture decision, and why each one beat the alternatives: [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md).

## How she thinks

- **Reflex layer (Jev, ~400ms):** a tiny decision model answers a batch of yes/no and multiple-choice questions about every sentence she hears — is this for her? what route? what skill? does it need my calendar? It never writes prose, so it can't hallucinate its way past the rules.
- **Voice ID:** learns my voice from clips where I held the talk key, then rejects anyone else's speech before it ever reaches transcription or the cloud — real privacy, not a policy doc.
- **Fast hands:** 13+ skills (Spotify, volume, apps, timers, calendar, tasks) run as plain code with no LLM in the loop, checked against real state after ("did the volume actually change?") and undoable.
- **Full computer control:** for anything without a fast skill, she reads the screen as structured text (Accessibility tree / page DOM, never a screenshot), plans the whole route in one model call, and checks each step before trusting it.
- **Deep work:** genuinely hard or open-ended asks go to Claude Code running as a background agent in my main project folder, with full context — she says "on it" and keeps listening while it runs.
- **Shipped in shadow mode first:** every new capability (open mic, computer control) logs what it *would have done* before it's ever allowed to act — the same discipline a real product team would use for a risky rollout.

## Run

Everything below assumes `uv` and the ear models are installed once via `ops/get-ears-models.sh`.

| | |
|---|---|
| Start the core | `ops/install-core.sh` (launchd, auto-restarts; logs at `~/Library/Logs/Evie/core.log`) |
| Build the menu bar app | `mac/build.sh` (signed so mic/calendar/accessibility permissions stick) |
| Run tests | `uv run pytest` (offline) · `uv run pytest -m live` (hits real APIs) |
| Run evals | `uv run python -m evals.run --split all --label <name>` — see `docs/HOW-IT-WORKS.md` for the full eval suite (narration, ears, computer-use, proactive, tiers) |
| Check what's on screen | `curl -s -X POST localhost:8765/debug/do -d '{"op":"world"}'` |
| Check open-mic health | `curl -s localhost:8765/ears/stats` |
| Required env vars | `OPENROUTER_API_KEY`, `GROQ_API_KEY` in `.env`; Spotify/Todoist keys optional |

Deeper dive: [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) — every phase, every design decision, traced end to end with real numbers.
