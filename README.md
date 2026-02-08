# 🦞 SpeakWithYourJarvis

**Bidirectional real-time voice chat with AI** — Speak naturally, hear AI respond in a British accent.

---

## How It Works

```
You speak into the browser
        │
        ▼
┌──────────────────┐
│  🎤 Browser Mic  │  Records 8-second audio chunks via WebRTC
└────────┬─────────┘
         │ audio blob (webm)
         ▼
┌──────────────────┐
│  🔄 FFmpeg       │  Converts webm → wav (16kHz mono)
└────────┬─────────┘
         │ wav file
         ▼
┌──────────────────┐
│  📝 Whisper      │  Transcribes speech → text (tiny model, fast)
└────────┬─────────┘
         │ text
         ▼
┌──────────────────┐
│  💾 SQLite       │  Saves your message to the database
└────────┬─────────┘
         │ text + msg_id
         ▼
┌──────────────────┐
│  🧠 Main Session │  Sends to OpenClaw main session (the real Jarvis)
│                  │  Full memory, personality, and conversation context
└────────┬─────────┘
         │ response text
         ▼
┌──────────────────┐
│  🔊 Edge TTS     │  Converts response → MP3 (British Ryan voice)
└────────┬─────────┘
         │ audio file
         ▼
┌──────────────────┐
│  💾 SQLite       │  Saves response + audio path to database
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  📡 Browser Poll │  Browser polls /api/poll every 400ms
│                  │  picks up new messages + plays audio
└──────────────────┘
         │
         ▼
    You hear Jarvis respond 🦞
```

---

## The Code Explained

Everything lives in one file: **`server.py`** (~500 lines)

### 1. Database Layer (SQLite)

```
messages table:
  id | timestamp | direction | text | audio_path | delivered
```

- `direction` is either `"user"` or `"jarvis"`
- Every message (yours and AI's) is stored persistently
- The browser polls by `id` — "give me everything after message #X"
- Simple, no external DB needed, survives restarts

### 2. Audio Processing Pipeline

When you speak, the browser records in 8-second chunks. Each chunk goes through:

1. **Upload** — Browser sends the webm blob to `/api/transcribe`
2. **FFmpeg** — Converts webm → wav at 16kHz mono (what Whisper expects)
3. **Whisper** — `faster-whisper` with the `tiny` model transcribes to text
4. **Return** — Text goes back to browser, which then POSTs to `/api/speak`

### 3. AI Response

Voice messages are routed through the **OpenClaw main session** — the same Jarvis that handles WhatsApp, cron jobs, and everything else. This means:

- Full memory and conversation context
- Personality, preferences, and soul
- Access to all tools and integrations
- Continuity across voice and text conversations

The response is saved to SQLite and converted to TTS audio for playback.

### 4. Text-to-Speech

Uses `node-edge-tts` (Microsoft Edge's free TTS):
- Voice: `en-GB-RyanNeural` (British accent)
- Output: MP3 files saved to `audio/` directory
- Each response gets its own file: `jarvis_42.mp3`

### 5. The Web UI

The entire frontend is embedded as an HTML string in `server.py` (no build step, no dependencies). Features:

- **Pipeline indicator** — Shows which step is active (Record → Transcribe → Send → Thinking → Voice)
- **"Over" mode** — Toggle to accumulate speech. Say "over" to send, or click the Send button. Good for longer messages.
- **Auto-polling** — Checks for new messages every 400ms
- **Audio queue** — Plays responses in order, shows "Speaking..." status
- **Chat history** — Loads last 20 messages on page load

### 6. Server Framework

Uses `aiohttp` with self-signed SSL (required for browser mic access):

| Endpoint | What it does |
|----------|-------------|
| `GET /` | Serves the web UI |
| `POST /api/transcribe` | Audio → text (Whisper) |
| `POST /api/speak` | User text → triggers AI response |
| `POST /api/respond` | AI/agent pushes response back |
| `GET /api/poll?since=ID` | Browser polls for new messages |
| `GET /api/history` | Load conversation history |
| `GET /db` | Raw database viewer (debug) |
| `GET /timing` | Performance timing logs (debug) |

> **Note:** Ports, tokens, and other config are set as constants at the top of `server.py`. See the source for details.

---

## Setup

### Requirements
- Python 3.10+
- FFmpeg
- Node.js (for Edge TTS)
- `pip install aiohttp faster-whisper`

### Run

```bash
# Generate SSL certs (first time only)
mkdir -p ssl
openssl req -x509 -newkey rsa:4096 -keyout ssl/key.pem -out ssl/cert.pem -days 365 -nodes -subj "/CN=localhost"

# Optional: direct Claude API (faster responses)
export ANTHROPIC_API_KEY=your-key-here

# Start
python server.py
```

### Systemd Service

```ini
[Unit]
Description=Jarvis Voice Chat
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/voice-chat
ExecStart=/path/to/venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## File Structure

```
voice-chat/
├── server.py          # Everything — server, UI, logic
├── ssl/
│   ├── cert.pem       # Self-signed SSL cert
│   └── key.pem        # SSL private key
├── audio/             # Generated TTS audio files
├── voice-chat.db      # SQLite conversation database
├── voice-chat.log     # Server logs
├── timing.log         # Performance timing
└── HEALTH_AUDIT.md    # Reliability improvement notes
```

---

**v1.0.0** — First sacred release 🦞

Built by Jarvis de la Ari & Ariel @ Bresleveloper AI
