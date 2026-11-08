#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=================================================="
echo "🛠️  Setting up Landline Phone AI Gateway on this Mac"
echo "=================================================="

# 1. Check Homebrew
if ! command -v brew >/dev/null 2>&1; then
    echo "❌ Homebrew is required but not installed."
    echo "   Please install it first: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# 2. Check and install CLI tools
echo "📦 Checking system packages..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "  -> Installing ffmpeg via brew..."
    brew install ffmpeg
else
    echo "  ✅ ffmpeg is installed ($(command -v ffmpeg))"
fi

if ! command -v whisper-cli >/dev/null 2>&1; then
    echo "  -> Installing whisper-cpp via brew..."
    brew install whisper-cpp
else
    echo "  ✅ whisper-cli is installed ($(command -v whisper-cli))"
fi

# 3. Check and install Python dependencies
echo "🐍 Checking Python dependencies..."
python3 -m pip install -r "$DIR/requirements.txt" --quiet
echo "  ✅ Python packages installed."

# 4. Check Whisper model
MODEL_DIR="$HOME/.cache/whisper"
MODEL_FILE="$MODEL_DIR/ggml-small.bin"
mkdir -p "$MODEL_DIR"

if [ ! -f "$MODEL_FILE" ] && [ ! -f "$MODEL_DIR/ggml-base.bin" ]; then
    echo "📥 Downloading Whisper Thai model (ggml-small.bin ~460MB)..."
    curl -L -o "$MODEL_FILE.tmp" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
    mv "$MODEL_FILE.tmp" "$MODEL_FILE"
    echo "  ✅ Model downloaded successfully."
else
    echo "  ✅ Whisper model found in $MODEL_DIR"
fi

# 5. Make all scripts executable
chmod +x "$DIR"/*.sh

echo ""
echo "=================================================="
echo "🎉 Setup complete! You are ready to run:"
echo "   ./start_daemon.sh   (Run in background)"
echo "   ./run.sh            (Run in foreground/debug)"
echo "=================================================="
