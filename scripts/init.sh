#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Lieutenant — Full Project Initialisation
#  Run once after cloning:  ./scripts/init.sh
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env so HF_TOKEN and other vars are available to subprocesses
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
fi

# ── Colours ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}ℹ ${NC} $*"; }
ok()    { echo -e "${GREEN}✅${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠️ ${NC} $*"; }
fail()  { echo -e "${RED}❌${NC} $*"; exit 1; }
header(){ echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# ── Pre-flight checks ────────────────────────────────────────────────
header "Pre-flight checks"

command -v python3 &>/dev/null || fail "python3 not found. Install Python 3.10+."

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 10) )); then
    fail "Python 3.10+ required (found $PY_VER)."
fi
ok "Python $PY_VER"

command -v node &>/dev/null || fail "node not found. Install Node.js 18+."
NODE_VER=$(node -v | sed 's/v//')
ok "Node.js $NODE_VER"

command -v npm &>/dev/null || fail "npm not found."
ok "npm $(npm -v)"

# Check for ffplay or mpv (needed for edge-tts audio playback on Linux)
if [[ "$(uname)" == "Darwin" ]]; then
    # macOS uses afplay (built-in)
    ok "macOS detected — afplay available for audio playback"
else
    if command -v ffplay &>/dev/null; then
        ok "ffplay available for audio playback"
    elif command -v mpv &>/dev/null; then
        ok "mpv available for audio playback"
    else
        warn "Neither ffplay nor mpv found. Install ffmpeg or mpv for TTS audio playback."
        warn "  Ubuntu/Debian: sudo apt install ffmpeg"
        warn "  Fedora:        sudo dnf install ffmpeg"
    fi
fi

# ── .env setup ────────────────────────────────────────────────────────
header "Environment configuration"

if [ -f "$ROOT_DIR/.env" ]; then
    info ".env already exists — keeping your current config."
else
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    ok "Created .env from .env.example"
    info "Edit .env to add your API keys (OPENAI_API_KEY, etc.)"
fi

# ── logs directory ────────────────────────────────────────────────────
mkdir -p "$ROOT_DIR/packages/logs"
ok "Logs directory ready"

# ═══════════════════════════════════════════════════════════════════════
#  1. Voice Daemon (Python)
# ═══════════════════════════════════════════════════════════════════════
header "Voice Daemon"

DAEMON_DIR="$ROOT_DIR/packages/voice-daemon"
cd "$DAEMON_DIR"

if [ ! -d ".venv" ]; then
    info "Creating Python virtual environment…"
    python3 -m venv .venv
    ok "Virtual environment created"
else
    info "Virtual environment already exists — reusing."
fi

info "Upgrading pip…"
.venv/bin/pip install --upgrade pip --quiet

info "Installing voice-daemon dependencies…"
.venv/bin/pip install -r requirements.txt --quiet
ok "Voice daemon dependencies installed"

# ── Pre-download Whisper medium model ─────────────────────────────────
info "Pre-loading Whisper medium model (this may take a few minutes on first run)…"
.venv/bin/python3 -c "
from faster_whisper import WhisperModel
import sys
try:
    m = WhisperModel('medium', device='cpu', compute_type='int8')
    print('✅ Whisper medium model ready')
except Exception as e:
    print(f'⚠️  Whisper model download deferred: {e}', file=sys.stderr)
" 2>&1 || warn "Whisper model will download on first use."

# ── Check Vosk model for wake word ────────────────────────────────────
info "Checking Vosk wake-word model…"
if [ -n "${VOSK_MODEL_PATH:-}" ] && [ -d "$VOSK_MODEL_PATH" ]; then
    ok "Vosk model found (VOSK_MODEL_PATH): $(basename "$VOSK_MODEL_PATH")"
else
    VOSK_MODELS=$(find "$DAEMON_DIR/models" -maxdepth 1 -type d -name 'vosk-model-*' 2>/dev/null)
    if [ -n "$VOSK_MODELS" ]; then
        ok "Vosk model already present: $(basename "$(echo "$VOSK_MODELS" | head -1)")"
    else
        bash "$ROOT_DIR/scripts/download-vosk-model.sh"
    fi
fi

# ── Pre-download Silero VAD ───────────────────────────────────────────
info "Pre-loading Silero VAD model…"
.venv/bin/python3 -c "
import torch
try:
    model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True)
    print('Silero VAD model ready')
except Exception as e:
    print(f'Silero VAD will download on first use: {e}')
" 2>&1 || warn "Silero VAD model will download on first use."

ok "Voice daemon ready"

# ═══════════════════════════════════════════════════════════════════════
#  2. Agent Gateway (Python)
# ═══════════════════════════════════════════════════════════════════════
header "Agent Gateway"

GATEWAY_DIR="$ROOT_DIR/packages/agent-gateway"
cd "$GATEWAY_DIR"

if [ ! -d ".venv" ]; then
    info "Creating Python virtual environment…"
    python3 -m venv .venv
    ok "Virtual environment created"
else
    info "Virtual environment already exists — reusing."
fi

info "Upgrading pip…"
.venv/bin/pip install --upgrade pip --quiet

info "Installing agent-gateway dependencies…"
.venv/bin/pip install -r requirements.txt --quiet
ok "Agent gateway dependencies installed"

# ═══════════════════════════════════════════════════════════════════════
#  3. Web UI (Node/React)
# ═══════════════════════════════════════════════════════════════════════
header "Web UI"

UI_DIR="$ROOT_DIR/packages/web-ui"
cd "$UI_DIR"

info "Installing npm dependencies…"
npm install --silent 2>/dev/null || npm install
ok "Web UI dependencies installed"

# ═══════════════════════════════════════════════════════════════════════
#  4. Verify installations
# ═══════════════════════════════════════════════════════════════════════
header "Verification"

cd "$ROOT_DIR"

# Check daemon packages
"$DAEMON_DIR/.venv/bin/python3" -c "
import faster_whisper, edge_tts, torch, vosk, sounddevice, fastapi
print('✅ Voice daemon: all core packages importable')
" 2>&1 || warn "Some voice daemon packages may have issues."

# Check gateway packages
"$GATEWAY_DIR/.venv/bin/python3" -c "
import fastapi, httpx, openai
print('✅ Agent gateway: all core packages importable')
" 2>&1 || warn "Some gateway packages may have issues."

# Check node modules
if [ -d "$UI_DIR/node_modules" ]; then
    ok "Web UI: node_modules present"
else
    warn "Web UI: node_modules missing — run 'cd packages/web-ui && npm install'"
fi

# ═══════════════════════════════════════════════════════════════════════
#  Done
# ═══════════════════════════════════════════════════════════════════════
header "Lieutenant is ready! 🎖️"

echo ""
echo -e "  ${BOLD}Quick start:${NC}"
echo ""
echo -e "    ${CYAN}make dev${NC}          Start all 3 services in dev mode"
echo -e "    ${CYAN}make start${NC}        Start in production mode"
echo -e "    ${CYAN}make stop${NC}         Stop all services"
echo ""
echo -e "  ${BOLD}Services:${NC}"
echo ""
echo -e "    Voice Daemon    → ${GREEN}ws://127.0.0.1:8765${NC}"
echo -e "    Agent Gateway   → ${GREEN}http://127.0.0.1:8800${NC}"
echo -e "    Web UI          → ${GREEN}http://127.0.0.1:5173${NC}"
echo ""
echo -e "  ${BOLD}Configuration:${NC}"
echo ""
echo -e "    Edit ${YELLOW}.env${NC} to set API keys and preferences."
echo -e "    STT: Whisper medium  │  TTS: edge-tts (Microsoft Neural)"
echo -e "    Conversation mode: enabled (5s follow-up window)"
echo ""
echo -e "  ${BOLD}Tip:${NC} Say ${CYAN}\"Υπολοχαγέ\"${NC} (or press Wake in the UI) to start."
echo ""
