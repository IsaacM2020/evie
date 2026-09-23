# Evie

Isaac's voice-run operating system. Plan: ~/IsaacOS/projects/evie/jarvis-plan-2026-09-23.md

## Run
- Core: `ops/install-core.sh` (launchd keeps it alive; logs in ~/Library/Logs/Evie/core.log)
- Menu bar app: `mac/build.sh`
- Tests: `uv run pytest` (offline) · `uv run pytest -m live` (hits Jev)
- Evals: `uv run python -m evals.run --split tune --label <name>`
- Calendar check: `uv run python -m evie.gcal tomorrow`

How it works: docs/HOW-IT-WORKS.md
