# Evie

Isaac's voice-run operating system. Hold left ⌃⌥ (Control + Option), talk, let go. Left ⌃⌥⌘ flips the Live open mic on and off. Plan: ~/IsaacOS/projects/evie/jarvis-plan-2026-09-23.md

## Run
- Core: `ops/install-core.sh` (launchd keeps it alive; logs in ~/Library/Logs/Evie/core.log, one line per turn in turns.jsonl)
- Ear models (once): `ops/get-ears-models.sh` (Silero VAD + WeSpeaker, ~27 MB into ~/Library/Application Support/Evie/models). Pocket TTS and Whisper download themselves on first run.
- Menu bar app: `mac/build.sh` (signed with Isaac's Apple Development cert so permissions stick; launchd `com.isaac.evie.app` starts it at login and brings it back after a crash)
- Tests: `uv run pytest` (offline) · `uv run pytest -m live` (hits Jev, Groq, Pocket, Whisper, Claude Code, Spotify search, Todoist)
- Evals: `uv run python -m evals.run --split all --label <name>` · `uv run python -m evals.run_narration` · `uv run python -m evals.run_ears` · `uv run python -m evals.run_computer` (3b) · `uv run python -m evals.replay --synthetic|--recorded --backend groq local` (ears WER)
- Page reader test (headless WebKit, no screen): `~/Applications/Evie.app/Contents/MacOS/EvieBar --webtest evals/computer/pages/form.html read click:w3`
- Try one hands command: `curl -s -X POST localhost:8765/debug/do -H 'content-type: application/json' -d '{"op":"observe","args":{"app":"Safari"}}'`
- Open-mic health: `curl -s localhost:8765/ears/stats` (segments by speaker, echo drops, whether her voice goes through the app)
- Keys in `.env`: OPENROUTER_API_KEY, GROQ_API_KEY (required); SPOTIFY_APP_CLIENT_ID/SECRET, TODOIST_API (optional)
- Logs: core.log, turns.jsonl (every turn), actions.jsonl (every skill) in ~/Library/Logs/Evie
- Calendar check: `curl -s localhost:8765/debug/calendar` (right now, today, tomorrow, and which calendars are read: Google "Isaac" + Classroom only)

How it works: docs/HOW-IT-WORKS.md
