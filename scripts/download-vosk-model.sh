#!/usr/bin/env bash
# Download the Vosk Greek small model for wake word detection.
set -euo pipefail

MODEL_DIR="$(dirname "$0")/../packages/voice-daemon/models"
MODEL_NAME="vosk-model-small-el-gr-0.15"
MODEL_URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"

mkdir -p "$MODEL_DIR"

if [ -d "$MODEL_DIR/$MODEL_NAME" ]; then
    echo "✅  Model already exists: $MODEL_DIR/$MODEL_NAME"
    exit 0
fi

echo "📥  Downloading Vosk Greek model…"
echo "    URL: $MODEL_URL"
echo "    Destination: $MODEL_DIR/"

cd "$MODEL_DIR"

if command -v wget &>/dev/null; then
    wget -q --show-progress "$MODEL_URL" -O "${MODEL_NAME}.zip"
elif command -v curl &>/dev/null; then
    curl -L --progress-bar "$MODEL_URL" -o "${MODEL_NAME}.zip"
else
    echo "❌  Neither wget nor curl found. Please install one."
    exit 1
fi

echo "📦  Extracting…"
unzip -q "${MODEL_NAME}.zip"
rm "${MODEL_NAME}.zip"

echo "✅  Model ready: $MODEL_DIR/$MODEL_NAME"
