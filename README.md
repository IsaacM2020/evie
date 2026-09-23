# Evie

Isaac's voice-run operating system. Hold 🌐 (Fn), talk, let go. Plan: ~/IsaacOS/projects/evie/jarvis-plan-2026-09-23.md

## Run
- Core: `ops/install-core.sh` (launchd keeps it alive; logs in ~/Library/Logs/Evie/core.log, one line per turn in turns.jsonl)
- Voice model (once): `ops/get-voice.sh` (Kokoro, ~350 MB into ~/Library/Application Support/Evie/kokoro)
- Menu bar app: `mac/build.sh` (signed with Isaac's Apple Development cert so permissions stick)
- Tests: `uv run pytest` (offline) · `uv run pytest -m live` (hits Jev, Groq, Kokoro, Whisper, Claude Code)
- Evals: `uv run python -m evals.run --split all --label <name>` · `uv run python -m evals.run_narration`
- Calendar check: `curl -s localhost:8765/debug/calendar` (the menu bar app pushes macOS Calendar events)

How it works: docs/HOW-IT-WORKS.md
