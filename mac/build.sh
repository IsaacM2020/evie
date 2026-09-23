#!/bin/bash
# Build Evie.app with SwiftPM (no Xcode needed), install to ~/Applications, run the selftest.
set -euo pipefail
cd "$(dirname "$0")/EvieBar"
swift build -c release
APP="$HOME/Applications/Evie.app"
pkill -x EvieBar 2>/dev/null || true
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/EvieBar "$APP/Contents/MacOS/EvieBar"
cp ../Info.plist "$APP/Contents/Info.plist"
# Sign with Isaac's Apple Development cert, not ad-hoc: an ad-hoc signature changes every
# build, and macOS then forgets the Calendar / Mic / Accessibility permissions.
IDENTITY="9DBBC5F16763E0FD030B51A5450E4F7E79F809C5"
codesign --force --sign "$IDENTITY" "$APP"
"$APP/Contents/MacOS/EvieBar" --selftest
open "$APP"
echo "Evie.app installed and running"
