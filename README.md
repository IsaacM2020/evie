# Evie

Isaac's voice-run operating system. Hold 🌐 (Fn), talk, let go. Plan: ~/IsaacOS/projects/evie/jarvis-plan-2026-09-23.md

## Run
- Core: `ops/install-core.sh` (launchd keeps it alive; logs in ~/Library/Logs/Evie/core.log, one line per turn in turns.jsonl)
- Ear models (once): `ops/get-ears-models.sh` (Silero VAD + WeSpeaker, ~27 MB into ~/Library/Application Support/Evie/models). Pocket TTS and Whisper download themselves on first run.
- Menu bar app: `mac/build.sh` (signed with Isaac's Apple Development cert so permissions stick)
- Tests: `uv run pytest` (offline) · `uv run pytest -m live` (hits Jev, Groq, Pocket, Whisper, Claude Code, Spotify search, Todoist)
- Evals: `uv run python -m evals.run --split all --label <name>` · `uv run python -m evals.run_narration` · `uv run python -m evals.run_ears`
- Keys in `.env`: OPENROUTER_API_KEY, GROQ_API_KEY (required); SPOTIFY_APP_CLIENT_ID/SECRET, TODOIST_API (optional)
- Logs: core.log, turns.jsonl (every turn), actions.jsonl (every skill) in ~/Library/Logs/Evie
- Calendar check: `curl -s localhost:8765/debug/calendar` (the menu bar app pushes macOS Calendar events)

How it works: docs/HOW-IT-WORKS.md
