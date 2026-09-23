#!/bin/bash
# Download Kokoro (Evie's voice) model files into App Support. Safe to re-run.
set -euo pipefail
DIR="$HOME/Library/Application Support/Evie/kokoro"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
mkdir -p "$DIR"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ ! -s "$DIR/$f" ]; then
    curl -fL --retry 3 -o "$DIR/$f.part" "$BASE/$f"
    mv -f "$DIR/$f.part" "$DIR/$f"
  fi
done
ls -lh "$DIR"
