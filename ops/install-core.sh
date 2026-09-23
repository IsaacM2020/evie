#!/bin/bash
set -euo pipefail
mkdir -p ~/Library/Logs/Evie
cp "$(dirname "$0")/com.isaac.evie.core.plist" ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)/com.isaac.evie.core" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.isaac.evie.core.plist
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8765/status; then echo; echo "core up"; exit 0; fi
  sleep 1
done
echo "core did not start, see ~/Library/Logs/Evie/core.log"; exit 1
