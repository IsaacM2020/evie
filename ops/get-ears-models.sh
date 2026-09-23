#!/bin/bash
# Evie's ears (once): Silero VAD (~2 MB, "is someone talking?") and WeSpeaker ResNet34
# (~26 MB, "is it Isaac?"), both run by sherpa-onnx. They land in App Support, not the repo.
set -euo pipefail
DIR="$HOME/Library/Application Support/Evie/models"
REL="https://github.com/k2-fsa/sherpa-onnx/releases/download"
mkdir -p "$DIR"
fetch() {  # url, file
  if [ -s "$DIR/$2" ]; then echo "have $2"; return; fi
  curl -fL --retry 3 -o "$DIR/$2.part" "$1"
  mv "$DIR/$2.part" "$DIR/$2"
  echo "got $2"
}
fetch "$REL/asr-models/silero_vad.onnx" silero_vad.onnx
fetch "$REL/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx" speaker.onnx
ls -la "$DIR"
