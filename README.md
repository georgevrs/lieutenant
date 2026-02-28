# 🎖️ Lieutenant — Voice Assistant

A Jarvis-style, bilingual (Greek / English) voice assistant with offline wake word detection, real-time streaming STT, intelligent LLM-powered agent responses, edge-based neural TTS, barge-in interruption, conversation mode, and a premium web UI with waveform visualizations.

---

## Architecture

```
┌──────────────┐     WebSocket       ┌───────────────┐
│   Web UI     │◄───────────────────►│ Voice Daemon  │
│  (React+TS)  │  state, stt, agent, │   (Python)    │
│  port 5173   │  tts, settings, i18n│   port 8765   │
└──────────────┘                     └───────┬───────┘
                                             │ HTTP SSE
                                             ▼
                                     ┌───────────────┐
                                     │ Agent Gateway  │
                                     │ (Python FastAPI)│
                                     │   port 8800    │
                                     └───────────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                        OpenClaw CLI   Google Gemini   OpenAI GPT
                        (primary)      (fallback)      (fallback)
```

### Packages

| Package | Tech | Purpose |
|---|---|---|
| `packages/voice-daemon` | Python 3.11+ | Mic capture, wake word (Vosk), STT (faster-whisper), TTS (edge-tts), barge-in, settings |
| `packages/agent-gateway` | Python FastAPI | OpenAI-compatible API, OpenClaw/Gemini/GPT, tool execution, bilingual prompts |
| `packages/web-ui` | Vite + React + TS | Waveform, transcript, chat panel, controls, settings panel, i18n |

### Data Flow

1. **Voice Daemon** captures microphone audio continuously (16 kHz, mono)
2. **Wake Detection** (Vosk, grammar-constrained) triggers on "Υπολοχαγέ" (Greek) or "Lieutenant" (English)
3. **Streaming STT** (faster-whisper medium, CTranslate2 int8) transcribes speech → partial/final results to UI
4. **Agent Gateway** receives text, dispatches to OpenClaw CLI (primary) → Gemini (fallback) → local tools
5. **TTS** (edge-tts, Microsoft Neural voices) speaks response sentence-by-sentence (starts within ~1s)
6. **Waveform** reacts to mic RMS (listening/green) or speaker RMS (speaking/purple)
7. **Conversation mode** keeps listening after response — speak again without repeating the wake word

---

## Features

- **Bilingual**: Full Greek and English support — switch language live from the UI
- **Offline wake word**: Vosk-based, dual-model (Greek + English), grammar-constrained for reliability
- **Customizable wake words**: Change wake phrases and display name from the Settings panel (persisted to `.env`)
- **Streaming STT**: faster-whisper medium model with Silero VAD, auto-gain normalization
- **Neural TTS**: Microsoft edge-tts (male or female voices, Greek: `el-GR-NestorasNeural` / English: `en-US-GuyNeural`)
- **Barge-in**: Speak while the assistant is talking to interrupt — RMS-based energy detection
- **Conversation mode**: After a response, the system listens for follow-up questions (configurable timeout)
- **TTS echo suppression**: STT is deferred until TTS playback finishes + 0.5s guard to avoid hearing its own output
- **Markdown / emoji stripping**: Agent responses are cleaned before TTS for natural speech
- **Chat panel**: Plain-text conversation view with customizable assistant display name
- **LLM backends**: OpenClaw CLI (primary), Google Gemini (fallback), OpenAI GPT (fallback)
- **i18n**: Full Greek + English UI translations
- **Tool execution**: Shell commands, file I/O, HTTP requests — all audit-logged

---

## Quick Start

### Prerequisites

- **macOS** (primary target) or Linux
- **Python 3.11+**
- **Node.js 18+** and npm
- **portaudio** (for microphone access)

### 1. Install System Dependencies

```bash
# macOS
brew install portaudio python@3.11 node

# Linux (Debian/Ubuntu)
sudo apt-get install portaudio19-dev python3.11 python3.11-venv nodejs npm
```

### 2. Clone & Setup

```bash
git clone <repo-url> lieutenant
cd lieutenant
cp .env.example .env   # Edit as needed
make install
```

### 3. Download Vosk Models

Wake word detection requires Vosk models. Download and extract to `packages/voice-daemon/models/`:

```bash
cd packages/voice-daemon/models

# Greek model (required)
wget https://alphacephei.com/vosk/models/vosk-model-el-gr-0.7.zip
unzip vosk-model-el-gr-0.7.zip && rm vosk-model-el-gr-0.7.zip

# English model (optional, for English wake word)
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip

cd ../../..
```

### 4. (Optional) Install OpenClaw

OpenClaw is the primary LLM backend. If installed, Lieutenant will use it automatically:

```bash
# Follow OpenClaw installation instructions at https://github.com/ANG13T/openclaw
# Ensure the `openclaw` CLI is in your PATH
```

If OpenClaw is not available, the gateway falls back to Google Gemini (requires `GOOGLE_API_KEY` in `.env`) or OpenAI GPT.

### 5. Run

```bash
make dev
```

Open **http://127.0.0.1:5173** in your browser.

---

## Usage

### Voice Interaction

1. Say **"Υπολοχαγέ"** (Greek) or **"Lieutenant"** (English) to activate — or click the 🎤 button
2. Speak your request
3. Watch the live transcript appear in the chat panel
4. The assistant streams its response and speaks it aloud
5. **Conversation mode**: After the response, speak again without repeating the wake word (times out after 5s of silence)
6. **Barge-in**: Speak while the assistant is talking to interrupt it

### Controls

| Control | Action |
|---|---|
| 🎤 Button | Simulate wake word (manual trigger) |
| ■ Stop | Kill switch — stops everything immediately |
| 🌐 Language | Toggle between Greek and English |
| ⚙ Settings | Wake words, display name, connection info |

### Settings Panel

From Settings (⚙) you can customize:
- **Greek wake word** — the phrase that activates the assistant in Greek (default: "υπολοχαγέ")
- **English wake word** — the phrase for English activation (default: "lieutenant")
- **Display name** — the assistant's name shown in the chat panel

Changes are auto-saved to the `.env` file and take effect immediately.

---

## Configuration

Edit `.env` at the repo root (copy from `.env.example`):

```env
# Ports
VOICE_DAEMON_PORT=8765
GATEWAY_PORT=8800
UI_PORT=5173

# Safety: require confirmation for destructive tool actions
SAFE_MODE=false

# Voice daemon settings
WAKE_PHRASE=υπολοχαγέ             # Greek wake phrase (also settable from UI)
WAKE_PHRASE_EN=lieutenant          # English wake phrase
DISPLAY_NAME=Lieutenant            # Chat display name
STT_BACKEND=local                  # local | azure
TTS_BACKEND=edge                   # edge | say | azure
STT_MODEL_SIZE=medium              # tiny | base | small | medium | large-v3
TTS_VOICE_GENDER=female            # female | male
LANGUAGE=el                        # el | en (startup language)

# Conversation mode
CONVERSE_MODE=true                 # true | false
CONVERSE_TIMEOUT=5.0               # seconds to wait for follow-up
MAX_HISTORY=30                     # max conversation turns in memory

# Barge-in tuning
BARGEIN_RMS_THRESHOLD=0.035        # mic energy to trigger interruption
BARGEIN_FRAMES_NEEDED=8            # consecutive high-energy frames (~512ms)
BARGEIN_COOLDOWN_S=1.5             # ignore barge-in for N s after TTS starts
BARGEIN_POST_TTS_GUARD_S=1.2       # guard after each TTS chunk ends

# TTS echo suppression
TTS_ECHO_GUARD_S=0.5               # suppress STT for N s after TTS ends

# LLM backends (agent-gateway)
OPENCLAW_TOKEN=                    # OpenClaw access token
OPENCLAW_WS_URL=ws://127.0.0.1:18789/ws
# GOOGLE_API_KEY=                  # Gemini fallback
# OPENAI_API_KEY=sk-...            # OpenAI fallback
HF_TOKEN=                          # HuggingFace (for Silero VAD download)
```

### LLM Backend Priority

| Priority | Backend | Requirement |
|---|---|---|
| 1 | **OpenClaw CLI** | `openclaw` in PATH |
| 2 | **Google Gemini** | `GOOGLE_API_KEY` set |
| 3 | **OpenAI GPT** | `OPENAI_API_KEY` set |
| 4 | **Local tool dispatch** | Always available (shell, fs, http) |

The active backend is shown in the UI's chat panel via a badge indicator.

### TTS Backends

| Backend | Config | Voice |
|---|---|---|
| **edge-tts** (default) | `TTS_BACKEND=edge` | Microsoft Neural — `el-GR-NestorasNeural` / `en-US-GuyNeural` (male) or `el-GR-AthinaNeural` / `en-US-JennyNeural` (female) |
| **macOS say** | `TTS_BACKEND=say` | System voice |
| **Azure Speech** | `TTS_BACKEND=azure` | Azure Neural TTS (requires key) |

---

## Agent Gateway — OpenAI-Compatible API

The gateway exposes a standard OpenAI-compatible API:

```bash
# List models
curl http://127.0.0.1:8800/v1/models

# Chat (non-streaming)
curl http://127.0.0.1:8800/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-agent","messages":[{"role":"user","content":"Γεια σου!"}]}'

# Chat (streaming SSE)
curl http://127.0.0.1:8800/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local-agent","messages":[{"role":"user","content":"τρέξε ls -la"}],"stream":true}'

# Switch language
curl -X POST http://127.0.0.1:8800/v1/language \
  -H "Content-Type: application/json" \
  -d '{"language":"en"}'
```

### Built-in Tools

| Tool | Trigger Phrases | Description |
|---|---|---|
| `shell` | τρέξε, εκτέλεσε, run, execute | Execute shell commands |
| `fs_read` | διάβασε, read, δείξε, show | Read files |
| `fs_write` | γράψε, write, αποθήκευσε, save | Write files |
| `http_get` | κατέβασε, φέρε, fetch, get | HTTP GET requests |

All tool calls are logged to `logs/audit.jsonl`.

---

## Voice Daemon — Control API

```bash
# Simulate wake word
curl -X POST http://127.0.0.1:8765/control/wake

# Kill switch (stop everything)
curl -X POST http://127.0.0.1:8765/control/stop

# Push-to-talk
curl -X POST http://127.0.0.1:8765/control/push_to_talk/start
curl -X POST http://127.0.0.1:8765/control/push_to_talk/stop

# Get/set language
curl http://127.0.0.1:8765/control/language
curl -X POST http://127.0.0.1:8765/control/language \
  -H "Content-Type: application/json" -d '{"language":"en"}'

# Get/set settings (wake words + display name)
curl http://127.0.0.1:8765/control/settings
curl -X POST http://127.0.0.1:8765/control/settings \
  -H "Content-Type: application/json" \
  -d '{"wake_phrase_el":"υπολοχαγέ","wake_phrase_en":"lieutenant","display_name":"Lieutenant"}'

# Status
curl http://127.0.0.1:8765/status
```

### WebSocket Messages

Connect to `ws://127.0.0.1:8765/ws` to receive real-time events:

```json
{"type": "state",       "value": "IDLE"}
{"type": "mic.level",   "rms": 0.12}
{"type": "stt.partial", "text": "θέλω να ..."}
{"type": "stt.final",   "text": "θέλω να μου πεις τον καιρό"}
{"type": "agent.chunk", "text": "Βεβαίως, "}
{"type": "agent.done",  "backend": "openclaw"}
{"type": "tts.level",   "rms": 0.08}
{"type": "language",    "value": "el"}
{"type": "settings",    "wake_phrase_el": "υπολοχαγέ", "wake_phrase_en": "lieutenant", "display_name": "Lieutenant"}
{"type": "error",       "message": "..."}
```

---

## State Machine

```
IDLE ──(wake)──► LISTENING ──(final transcript)──► THINKING ──(first chunk)──► SPEAKING ──(TTS done)──► IDLE
  ▲                                                                                     │          │
  │                                                                                     │    (converse
  └──── (kill switch) ◄────────────────────────────────────────────────────────────────┘    timeout)
                                        ▲              │                                      │
                                        └──(barge-in)──┘              LISTENING ◄─────────────┘
                                                                   (follow-up, no wake word needed)
```

### Key Transitions

- **Wake** → IDLE to LISTENING (via wake word, button, or API)
- **Barge-in** → SPEAKING to LISTENING (user speaks over TTS, TTS stops)
- **Conversation mode** → SPEAKING to LISTENING (after TTS ends, waits for follow-up)
- **Kill switch** → Any state to IDLE (stops STT, TTS, agent)

---

## Replacing Agent Gateway with OpenClaw

The Agent Gateway is designed as a **drop-in replacement target**. To swap it with OpenClaw:

1. **OpenClaw must expose** the same endpoints:
   - `POST /v1/chat/completions` (with `stream=true` SSE support)
   - `GET /v1/models`

2. **Update `.env`**:
   ```env
   GATEWAY_PORT=<openclaw-port>
   ```

3. **No changes needed** in voice-daemon or web-ui — they communicate via the standard OpenAI API format.

The voice-daemon's `agent_client.py` uses standard HTTP SSE streaming against the `/v1/chat/completions` endpoint, making it backend-agnostic.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| No microphone access | Check `System Preferences > Privacy > Microphone` on macOS |
| `portaudio` not found | `brew install portaudio` (macOS) or `apt install portaudio19-dev` (Linux) |
| Vosk model missing | Download from https://alphacephei.com/vosk/models — extract to `packages/voice-daemon/models/` |
| English wake word not triggering | Ensure `vosk-model-small-en-us-0.15` is in `models/`. Switch to English via the UI language toggle. |
| faster-whisper slow on CPU | Use `STT_MODEL_SIZE=small` or `tiny` for faster (lower quality) transcription |
| TTS not speaking | Verify `TTS_BACKEND=edge` in `.env`. Check internet connection (edge-tts requires network). |
| Agent not responding | Ensure agent-gateway is running on port 8800. Check if OpenClaw/Gemini key is configured. |
| Echo / self-triggering | Increase `TTS_ECHO_GUARD_S` (default 0.5) or `BARGEIN_POST_TTS_GUARD_S` (default 1.2) |
| WebSocket not connecting | Ensure voice-daemon is running on port 8765 |

---

## Project Structure

```
lieutenant/
├── .env.example              # Configuration template
├── .gitignore
├── Makefile                  # dev, start, install, clean
├── README.md
├── LICENSE
│
├── packages/
│   ├── agent-gateway/        # OpenAI-compatible agent API
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py       # FastAPI app
│   │       ├── routes/
│   │       │   ├── chat.py   # /v1/chat/completions (SSE streaming)
│   │       │   ├── models.py # /v1/models
│   │       │   └── language.py # /v1/language
│   │       └── agent/
│   │           ├── core.py   # OpenClaw CLI + Gemini fallback + tool dispatch
│   │           ├── tools.py  # fs_read, fs_write, shell, http_get
│   │           └── audit.py  # Tool call audit logging
│   │
│   ├── voice-daemon/         # Voice processing daemon
│   │   ├── requirements.txt
│   │   ├── models/           # Vosk models (gitignored)
│   │   │   ├── vosk-model-el-gr-0.7/         # Greek wake word model
│   │   │   └── vosk-model-small-en-us-0.15/  # English wake word model
│   │   └── lieutenant_daemon/
│   │       ├── __init__.py
│   │       ├── __main__.py   # Entry point
│   │       ├── server.py     # FastAPI + orchestration + settings + state machine
│   │       ├── state.py      # State machine (IDLE/LISTENING/THINKING/SPEAKING)
│   │       ├── ws_hub.py     # WebSocket broadcasting
│   │       ├── audio_capture.py  # Mic input (portaudio, 16kHz mono)
│   │       ├── wake.py       # Wake word detection (Vosk, dual-model, grammar-based)
│   │       ├── stt.py        # Speech-to-text (faster-whisper + Silero VAD)
│   │       ├── tts.py        # Text-to-speech (edge-tts neural voices)
│   │       └── agent_client.py   # Agent gateway HTTP SSE client
│   │
│   └── web-ui/               # React web interface
│       ├── package.json
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── index.html
│       └── src/
│           ├── main.tsx
│           ├── App.tsx       # Main layout + state wiring
│           ├── index.css
│           ├── types.ts      # Shared TypeScript types
│           ├── i18n.ts       # Greek/English UI translations
│           ├── hooks/
│           │   └── useDaemon.ts  # WebSocket + settings state
│           └── components/
│               ├── Waveform.tsx       # Canvas waveform visualization
│               ├── StateIndicator.tsx # State display with i18n
│               ├── Transcript.tsx     # Live STT transcript
│               ├── AgentResponse.tsx  # Streaming agent text
│               ├── ChatPanel.tsx      # Conversation view (plain text, display name)
│               ├── Controls.tsx       # Wake + Kill + Language buttons
│               ├── LogPanel.tsx       # Real-time daemon logs
│               └── Settings.tsx       # Editable wake words + display name
│
├── scripts/
│   ├── download-vosk-model.sh  # Vosk model downloader
│   ├── test_openclaw_ws.js     # OpenClaw WebSocket test
│   └── test_suite.py           # Integration test suite
│
└── logs/                     # Runtime logs (gitignored)
    └── audit.jsonl           # Tool call audit trail
```

---

## Key Design Decisions

1. **Edge-TTS for speech synthesis** — Microsoft Neural TTS voices via the `edge-tts` package, providing high-quality Greek and English voices for free over the network. Sentence-chunked for low perceived latency (~1s from first agent tokens).

2. **faster-whisper medium for STT** — Best quality/speed tradeoff for offline bilingual transcription. Uses CTranslate2 (int8 quantization) for CPU efficiency. Combined with Silero VAD for utterance detection and auto-gain normalization.

3. **Vosk grammar-based wake word** — Instead of training a custom KWS model, we run lightweight Vosk recognition constrained to a grammar containing only the wake phrase. Dual-model support (Greek 50MB + English 40MB) with hot-reloading on language switch. Phonetic variant matching for robustness.

4. **OpenClaw → Gemini fallback** — OpenClaw CLI is the primary LLM backend (runs locally). If unavailable, falls back to Google Gemini API, then OpenAI. Bilingual system prompts instruct the LLM to avoid markdown/emoji for clean TTS output.

5. **TTS echo suppression** — STT start is deferred until after acknowledgment TTS finishes + a 0.5s guard. During TTS playback, the wake detector is disabled. This prevents the assistant from hearing its own output and re-triggering.

6. **Barge-in with RMS detection** — During TTS sentence gaps, an RMS energy detector checks for real human speech. Consecutive high-energy frames trigger interruption, stopping TTS and resuming STT.

7. **Settings persistence** — Wake words and display name are editable in the web UI. Changes are persisted directly to the `.env` file and broadcast to all connected clients via WebSocket.

8. **All services on localhost** — Security by default. No external network exposure.

---

## License

See [LICENSE](LICENSE).
