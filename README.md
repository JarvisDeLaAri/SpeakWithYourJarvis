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

## Known Issues & Notes

### 🔁 TTS Feedback Loop (Echo Bug)
The browser mic can pick up the TTS audio playback from the speakers, causing Whisper to transcribe Jarvis's own voice as a new user message. This creates phantom messages that the user never actually said.

**Workaround:** Mute the microphone while the TTS response is playing back, or use headphones to prevent the mic from capturing speaker output. A proper fix would be to implement echo cancellation or automatically pause recording during TTS playback.

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

---

## Security Hardening (v1.2.0)

### What Changed
The voice chat server was migrated from running as `root` to a dedicated service user as part of a full VPS security hardening. All apps now run under least-privilege users, serve HTTPS directly (no Nginx proxy), and are firewalled.

### Why
Running web-facing apps as root means any vulnerability gives an attacker full system access. With a dedicated service user, a compromised app can only touch its own files — no SSH keys, no API tokens, no other services.

### What Broke & How We Fixed It

| # | Issue | Cause | Fix |
|---|-------|-------|-----|
| 1 | **Whisper model download failed** | HuggingFace cache at `/root/.cache/` — the service user couldn't write | Created a dedicated HOME directory as HOME, set `HF_HOME` env var, ACL on existing cache |
| 2 | **Messages saved but never forwarded to Jarvis** | `.env` not loaded — `OPENCLAW_PORT` defaulted to `0` (disabled) | Added `EnvironmentFile=` to systemd service |
| 3 | **TTS failed — npm cache permission denied** | `npx node-edge-tts` tried to write to `/nonexistent/.npm/` | Installed `node-edge-tts` globally, set proper HOME |
| 4 | **TTS subprocess error — cwd not found** | Old `cwd='/tmp/edge-tts'` path never existed | Removed cwd param (not needed with global install) |
| 5 | **User message delayed until full AI cycle complete** | `/api/speak` was synchronous — waited for AI + TTS before returning | Return immediately, run AI+TTS in background thread |
| 6 | **Audio never played — race condition** | Message inserted to DB before TTS finished → poll got `audio_path: null` | Generate TTS first, then insert complete row with audio |
| 7 | **Response showed raw `MEDIA:` file paths** | Main session used its own TTS tool, injecting MEDIA tags into response text | Updated prompt: "plain text only, app handles TTS" |

### Current Architecture
```
User speaks → Whisper transcribes → text shown instantly (async return)
  → background thread:
      text → OpenClaw main session → plain text response
      → node-edge-tts generates MP3
      → complete row (text + audio_path) inserted to DB
  → client polls → sees message with audio → displays text + plays voice
```

### Service Config
```ini
[Service]
User=<service-user>
Group=<service-user>
EnvironmentFile=/path/to/voice-chat/.env
Environment=HOME=/var/lib/<service-user>
Environment=HF_HOME=/path/to/huggingface/cache
```

---

## Changelog

**v1.2.0.0** — Security hardening: dedicated service user, direct HTTPS, async response, TTS race fix
**v1.0.0** — First sacred release 🦞

Built by Jarvis de la Ari & Ariel @ Bresleveloper AI
