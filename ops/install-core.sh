#!/bin/bash
set -euo pipefail
mkdir -p ~/Library/Logs/Evie
cd "$(dirname "$0")/.." && uv sync -q  # the plist runs .venv/bin/evie-core directly
cp ops/com.isaac.evie.core.plist ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)/com.isaac.evie.core" 2>/dev/null || true
# bootout returns before the old core has fully exited; bootstrapping too early fails with "5: Input/output error"
for i in $(seq 1 20); do launchctl print "gui/$(id -u)/com.isaac.evie.core" >/dev/null 2>&1 || break; sleep 0.5; done
# Safety net: never leave an old core running (each one holds ~2 GB of models)
pkill -f "$PWD/.venv/bin/evie-core" 2>/dev/null || true
for i in $(seq 1 10); do pgrep -f "$PWD/.venv/bin/evie-core" >/dev/null || break; sleep 0.5; done
pkill -9 -f "$PWD/.venv/bin/evie-core" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.isaac.evie.core.plist
# Kokoro + Whisper warm up at start, so give it up to 40s
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8765/status; then echo; echo "core up"; exit 0; fi
  sleep 1
done
echo "core did not start, see ~/Library/Logs/Evie/core.log"; exit 1
