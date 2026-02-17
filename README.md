# 🎖️ Lieutenant — Voice Assistant

A Jarvis-style voice assistant system with Greek language support, offline wake word detection, streaming speech-to-text, intelligent agent responses, and a premium web UI with majestic waveform visualizations.

---

## Architecture

```
┌──────────────┐     WebSocket      ┌───────────────┐
│   Web UI     │◄──────────────────►│ Voice Daemon  │
│  (React+TS)  │   mic.level, stt,  │   (Python)    │
│  port 5173   │   agent, tts, etc  │   port 8765   │
└──────────────┘                    └───────┬───────┘
                                            │ HTTP SSE
                                            ▼
                                    ┌───────────────┐
                                    │ Agent Gateway  │
                                    │ (Python FastAPI)│
                                    │   port 8800    │
                                    └───────────────┘
```

### Packages

| Package | Tech | Purpose |
|---|---|---|
| `packages/voice-daemon` | Python 3.11+ | Mic capture, wake word, STT, TTS, WebSocket hub |
| `packages/agent-gateway` | Python FastAPI | OpenAI-compatible API, tool execution, agent logic |
| `packages/web-ui` | Vite + React + TS | Waveform visualization, transcript, controls |

### Data Flow

1. **Voice Daemon** captures microphone audio continuously
2. **Wake Detection** (Vosk Greek) triggers on "Υπολοχαγέ"
3. **Streaming STT** (faster-whisper) transcribes speech → partial/final results to UI
4. **Agent Gateway** receives text, streams response tokens back via SSE
5. **TTS** speaks response (sentence-chunked, starts within ~1s)
6. **Waveform** reacts to mic RMS (listening) or speaker RMS (speaking)

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
sudo apt-get install portaudio19-dev python3.11 python3.11-venv nodejs npm espeak
```

### 2. Clone & Setup

```bash
git clone <repo-url> lieutenant
cd lieutenant
cp .env.example .env   # Edit as needed
make install
```

### 3. Download Vosk Greek Model

The wake word detector requires a Vosk Greek model:

```bash
cd packages/voice-daemon/models
wget https://alphacephei.com/vosk/models/vosk-model-small-el-gr-0.15.zip
unzip vosk-model-small-el-gr-0.15.zip
cd ../../..
```

### 4. Run

```bash
make dev
```

Open **http://127.0.0.1:5173** in your browser.

---

## Usage

### Voice Interaction

1. Say **"Υπολοχαγέ"** to activate (or click the 🎤 button)
2. Speak your request in Greek
3. Watch the live transcript appear above the waveform
4. The assistant's response streams below the waveform
5. The assistant speaks the response aloud
6. The waveform reacts to your voice (green, listening) and the assistant's voice (purple, speaking)

### Controls

| Control | Action |
|---|---|
| 🎤 Button | Simulate wake word (manual trigger) |
| ■ Stop | Kill switch — stops everything immediately |
| ⚙ Settings | View configuration and backend status |

### Barge-in

Speak while the assistant is talking to interrupt (barge-in). TTS stops and the system resumes listening.

---

## Configuration

Edit `.env` at the repo root:

```env
# Ports
VOICE_DAEMON_PORT=8765
GATEWAY_PORT=8800
UI_PORT=5173

# Safety: require confirmation for destructive tool actions
SAFE_MODE=false

# Optional: OpenAI API key for LLM-powered agent reasoning
# OPENAI_API_KEY=sk-...

# Optional: Azure Speech for cloud STT/TTS
# AZURE_SPEECH_KEY=
# AZURE_SPEECH_REGION=westeurope

# STT/TTS backends
STT_BACKEND=local       # local | azure
TTS_BACKEND=local       # local | say | azure
STT_MODEL_SIZE=base     # tiny | base | small | medium | large-v3
```

### Backend Modes

| Feature | No API Key (Default) | With OpenAI Key | With Azure Key |
|---|---|---|---|
| Agent | Local rules + tools | GPT-4o-mini reasoning | GPT-4o-mini reasoning |
| STT | faster-whisper (local) | faster-whisper (local) | Azure Speech |
| TTS | macOS `say` / espeak | macOS `say` / espeak | Azure Neural TTS |

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
```

### Built-in Tools

| Tool | Trigger Phrases | Description |
|---|---|---|
| `shell` | τρέξε, εκτέλεσε, run | Execute shell commands |
| `fs_read` | διάβασε, read, δείξε | Read files |
| `fs_write` | γράψε, write, αποθήκευσε | Write files |
| `http_get` | κατέβασε, φέρε, fetch | HTTP GET requests |

All tool calls are logged to `logs/audit.jsonl`.

---

## Voice Daemon — Control API

```bash
# Simulate wake word
curl -X POST http://127.0.0.1:8765/control/wake

# Kill switch (stop everything)
curl -X POST http://127.0.0.1:8765/control/stop

# Push-to-talk start/stop
curl -X POST http://127.0.0.1:8765/control/push_to_talk/start
curl -X POST http://127.0.0.1:8765/control/push_to_talk/stop

# Status
curl http://127.0.0.1:8765/status
```

### WebSocket Messages

Connect to `ws://127.0.0.1:8765/ws` to receive real-time events:

```json
{"type": "state", "value": "IDLE"}
{"type": "mic.level", "rms": 0.12}
{"type": "stt.partial", "text": "θέλω να ..."}
{"type": "stt.final", "text": "θέλω να μου πεις τον καιρό"}
{"type": "agent.chunk", "text": "Βεβαίως, "}
{"type": "agent.done"}
{"type": "tts.level", "rms": 0.08}
{"type": "error", "message": "..."}
```

---

## State Machine

```
IDLE ──(wake)──► LISTENING ──(final transcript)──► THINKING ──(first agent chunk)──► SPEAKING ──(TTS done)──► IDLE
  ▲                                                                                      │
  └──────────────────────────── (kill switch) ◄──────────────────────────────────────────┘
                                                    ▲           │
                                                    └──(barge-in)──┘
```

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
| `portaudio` not found | `brew install portaudio` (macOS) or `apt-get install portaudio19-dev` (Linux) |
| Vosk model missing | Download from https://alphacephei.com/vosk/models — extract to `packages/voice-daemon/models/` |
| No Greek TTS voice | Install Greek voice in macOS `System Preferences > Accessibility > Speech`. On Linux: `apt-get install espeak` |
| faster-whisper slow | Use `STT_MODEL_SIZE=tiny` for faster (lower quality) transcription |
| WebSocket not connecting | Ensure voice-daemon is running on port 8765 |
| Agent not responding | Ensure agent-gateway is running on port 8800 |

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
│   │       │   ├── chat.py   # /v1/chat/completions
│   │       │   └── models.py # /v1/models
│   │       └── agent/
│   │           ├── core.py   # Agent reasoning + tool dispatch
│   │           ├── tools.py  # fs_read, fs_write, shell, http_get
│   │           └── audit.py  # Tool call audit logging
│   │
│   ├── voice-daemon/         # Voice processing daemon
│   │   ├── requirements.txt
│   │   ├── models/           # Vosk models (gitignored)
│   │   └── lieutenant_daemon/
│   │       ├── __init__.py   # Entry point
│   │       ├── __main__.py
│   │       ├── server.py     # FastAPI + orchestration
│   │       ├── state.py      # State machine
│   │       ├── ws_hub.py     # WebSocket broadcasting
│   │       ├── audio_capture.py  # Mic input
│   │       ├── wake.py       # Wake word detection (Vosk)
│   │       ├── stt.py        # Speech-to-text (faster-whisper)
│   │       ├── tts.py        # Text-to-speech (say/piper/azure)
│   │       └── agent_client.py   # Agent gateway HTTP client
│   │
│   └── web-ui/               # React web interface
│       ├── package.json
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── index.html
│       └── src/
│           ├── main.tsx
│           ├── App.tsx
│           ├── index.css
│           ├── types.ts
│           ├── hooks/
│           │   └── useDaemon.ts  # WebSocket connection hook
│           └── components/
│               ├── Waveform.tsx       # Canvas waveform visualization
│               ├── StateIndicator.tsx # State display
│               ├── Transcript.tsx     # STT transcript
│               ├── AgentResponse.tsx  # Streaming agent text
│               ├── Controls.tsx       # Wake + Kill buttons
│               └── Settings.tsx       # Settings drawer
│
└── logs/                     # Runtime logs (gitignored)
    └── audit.jsonl           # Tool call audit trail
```

---

## Key Design Decisions

1. **Agent Gateway in Python (FastAPI)** — Chosen over Node.js for consistency with the voice-daemon (both Python), shared tooling, and because the optional OpenAI Python SDK is first-class. FastAPI provides native async/SSE streaming.

2. **Vosk for wake word** — True custom KWS for "Υπολοχαγέ" would require training a model (days of work). Instead, we run lightweight continuous Vosk recognition on Greek audio and trigger when the transcript contains the wake phrase. Low CPU with the small Greek model (~50MB).

3. **faster-whisper for STT** — Best quality/speed tradeoff for offline Greek transcription. Uses CTranslate2 (int8 quantization) for CPU efficiency. Partial results emitted every ~1s of audio.

4. **Sentence-chunked TTS** — Instead of waiting for the full agent response, we buffer until sentence boundaries and start TTS immediately. This gives perceived latency of ~1s from first agent tokens.

5. **Echo suppression** — During TTS playback, wake detection is disabled to prevent the speaker output from re-triggering the wake word. Re-enabled after TTS completes or on kill switch.

6. **All services on localhost** — Security by default. No external network exposure.

---

## License

See [LICENSE](LICENSE).
