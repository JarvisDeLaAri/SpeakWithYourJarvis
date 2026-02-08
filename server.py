#!/usr/bin/env python3
"""
Voice Chat Server - Bidirectional with SQLite DB
Full conversation persistence + real-time polling
"""

import asyncio
import json
import os
import ssl
import sys
import tempfile
import subprocess
import datetime
import sqlite3
import urllib.request
from pathlib import Path

voice_env_path = Path(__file__).parent.parent / "voice-env" / "lib" / "python3.12" / "site-packages"
sys.path.insert(0, str(voice_env_path))

from aiohttp import web

# Configuration
HOST = "0.0.0.0"
PORT = 10010
SSL_CERT = Path(__file__).parent / "ssl" / "cert.pem"
SSL_KEY = Path(__file__).parent / "ssl" / "key.pem"
WHISPER_MODEL = "tiny"
LOG_FILE = Path(__file__).parent / "voice-chat.log"
DB_FILE = Path(__file__).parent / "voice-chat.db"
AUDIO_DIR = Path(__file__).parent / "audio"

# OpenClaw webhook
OPENCLAW_HOST = os.environ.get("OPENCLAW_HOST", "127.0.0.1")
OPENCLAW_PORT = int(os.environ.get("OPENCLAW_PORT", "0"))
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_TOKEN", "")

# Ensure audio directory exists
AUDIO_DIR.mkdir(exist_ok=True)

# Timing log for detailed performance tracking
TIMING_LOG = Path(__file__).parent / "timing.log"

def log_message(direction, message):
    timestamp = datetime.datetime.now().isoformat()
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{timestamp}] {direction}: {message}\n")
    print(f"[{timestamp}] {direction}: {message}")

def log_timing(request_id, step, details=""):
    """Log timing for performance analysis"""
    ts = datetime.datetime.now()
    timestamp = ts.isoformat()
    ms = int(ts.timestamp() * 1000)
    entry = f"{timestamp} | {ms} | req:{request_id} | {step} | {details}"
    with open(TIMING_LOG, 'a') as f:
        f.write(entry + "\n")
    print(f"⏱️ {entry}")

# ============ DATABASE ============

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            direction TEXT NOT NULL,
            text TEXT NOT NULL,
            audio_path TEXT,
            delivered INTEGER DEFAULT 0
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_delivered ON messages(delivered)')
    conn.commit()
    conn.close()
    log_message("DB", "Database initialized")

def db_insert_message(direction, text, audio_path=None):
    """Insert a message into the database"""
    conn = sqlite3.connect(str(DB_FILE))
    timestamp = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        'INSERT INTO messages (timestamp, direction, text, audio_path, delivered) VALUES (?, ?, ?, ?, ?)',
        (timestamp, direction, text, audio_path, 0)
    )
    msg_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return msg_id

def db_get_messages_since(since_id=0, limit=50):
    """Get messages since a given ID"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        'SELECT id, timestamp, direction, text, audio_path FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?',
        (since_id, limit)
    )
    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return messages

def db_mark_delivered(msg_id):
    """Mark a message as delivered"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute('UPDATE messages SET delivered = 1 WHERE id = ?', (msg_id,))
    conn.commit()
    conn.close()

# ============ OPENCLAW INTEGRATION ============

def call_main_session(text, msg_id):
    """Send voice message to OpenClaw main session and get response.
    This goes through the real Jarvis with full memory, personality, and context."""
    
    try:
        log_timing(str(msg_id), "MAIN_SESSION_START", "sending to main session")
        
        url = f"http://{OPENCLAW_HOST}:{OPENCLAW_PORT}/v1/chat/completions"
        data = json.dumps({
            "model": "agent:main",
            "user": "main",
            "messages": [
                {
                    "role": "user",
                    "content": f"[🎤 Voice Message #{msg_id}] The user sent a voice message through the voice chat app. Respond concisely (1-3 sentences) — this will be converted to speech. Be natural and conversational.\n\nThey said: \"{text}\""
                }
            ]
        }).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'Bearer {OPENCLAW_TOKEN}')
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode())
            response_text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            if response_text:
                log_timing(str(msg_id), "MAIN_SESSION_DONE", f"response: {response_text[:50]}...")
                return response_text
            else:
                log_message("ERROR", f"Empty response from main session")
                return None
            
    except Exception as e:
        log_message("ERROR", f"Main session call failed: {e}")
        return None

def process_voice_message(text, msg_id):
    """Process voice message: send to main session, get response, generate TTS"""
    
    # Send to main session (the real Jarvis)
    response = call_main_session(text, msg_id)
    
    if response:
        # Save response to DB
        log_timing(str(msg_id), "12_DB_INSERT_START", "inserting jarvis msg to db")
        response_id = db_insert_message('jarvis', response)
        log_timing(str(msg_id), "13_DB_INSERT_DONE", f"msg_id: {response_id}")
        
        # Generate TTS
        log_timing(str(msg_id), "14_TTS_START", "generating voice")
        audio_path = generate_tts(response, response_id)
        log_timing(str(msg_id), "15_TTS_DONE", f"audio: {audio_path}")
        
        # Update with audio path
        if audio_path:
            conn = sqlite3.connect(str(DB_FILE))
            conn.execute('UPDATE messages SET audio_path = ? WHERE id = ?', (audio_path, response_id))
            conn.commit()
            conn.close()
        
        log_timing(str(msg_id), "16_READY_FOR_POLL", "response ready")
        log_message("JARVIS", f"#{response_id}: {response[:50]}...")
        return response_id
    
    return None

# ============ TTS ============

def generate_tts(text, msg_id):
    """Generate TTS audio for a response"""
    try:
        audio_filename = f"jarvis_{msg_id}.mp3"
        audio_path = AUDIO_DIR / audio_filename
        
        result = subprocess.run([
            'npx', 'node-edge-tts',
            '-t', text,
            '-v', 'en-GB-RyanNeural',
            '-f', str(audio_path),
            '--timeout', '15000'
        ], capture_output=True, timeout=30, cwd='/tmp/edge-tts')
        
        if audio_path.exists():
            log_message("TTS", f"Generated audio: {audio_filename}")
            return f"/audio/{audio_filename}"
        else:
            log_message("ERROR", f"TTS failed: {result.stderr.decode()}")
            return None
    except Exception as e:
        log_message("ERROR", f"TTS error: {e}")
        return None

# ============ WHISPER ============

_whisper_model = None
def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device='cpu', compute_type='int8')
        log_message("SYSTEM", f"Whisper '{WHISPER_MODEL}' loaded")
    return _whisper_model

# ============ HTML CLIENT ============

HTML_CLIENT = """
<!DOCTYPE html>
<html>
<head>
    <title>🦞 Jarvis Voice Chat</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #fff; min-height: 100vh; margin: 0; padding: 20px;
            display: flex; flex-direction: column; align-items: center;
        }
        h1 { font-size: 1.8em; margin-bottom: 5px; }
        .subtitle { color: #27ae60; margin-bottom: 15px; font-size: 0.9em; }
        
        /* Pipeline status */
        .pipeline {
            display: flex; gap: 6px; margin-bottom: 15px; flex-wrap: wrap;
            justify-content: center; max-width: 400px;
        }
        .pipeline-step {
            padding: 4px 10px; border-radius: 12px; font-size: 0.75em;
            background: rgba(255,255,255,0.1); color: #666;
            transition: all 0.3s;
        }
        .pipeline-step.active { background: #f39c12; color: #fff; animation: pulse 1s infinite; }
        .pipeline-step.done { background: #27ae60; color: #fff; }
        .pipeline-step.error { background: #e74c3c; color: #fff; }
        
        .controls { display: flex; flex-direction: column; align-items: center; gap: 12px; margin-bottom: 15px; }
        .status-row { display: flex; gap: 10px; align-items: center; }
        
        #status {
            padding: 8px 16px; border-radius: 20px; background: #333;
            text-align: center; font-size: 0.9em;
        }
        #status.listening { background: #e74c3c; animation: pulse 1.5s infinite; }
        #status.processing { background: #f39c12; }
        #status.speaking { background: #27ae60; }
        
        #connection {
            padding: 8px 16px; border-radius: 20px; background: #333;
            font-size: 0.85em;
        }
        #connection.online { background: #27ae60; }
        #connection.offline { background: #e74c3c; }
        
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
        
        .mode-toggle {
            display: flex; align-items: center; gap: 8px;
            background: rgba(255,255,255,0.1); padding: 8px 14px;
            border-radius: 20px; font-size: 0.85em;
        }
        .mode-toggle input { width: 18px; height: 18px; cursor: pointer; }
        
        #toggle-btn {
            width: 120px; height: 120px; border-radius: 50%; border: none;
            background: linear-gradient(145deg, #27ae60, #1e8449);
            color: white; font-size: 1em; cursor: pointer;
            box-shadow: 0 8px 25px rgba(39, 174, 96, 0.3);
            transition: all 0.3s;
        }
        #toggle-btn:hover { transform: scale(1.05); }
        #toggle-btn.active {
            background: linear-gradient(145deg, #e74c3c, #c0392b);
            box-shadow: 0 8px 25px rgba(231, 76, 60, 0.3);
        }
        
        #transcript {
            margin-top: 15px; padding: 15px;
            background: rgba(255,255,255,0.1);
            border-radius: 10px; max-width: 500px; width: 100%;
            min-height: 150px; max-height: 350px; overflow-y: auto;
        }
        .message { padding: 10px 14px; margin: 8px 0; border-radius: 12px; max-width: 85%; font-size: 0.95em; }
        .user { background: #3498db; margin-left: auto; text-align: right; }
        .jarvis { background: #27ae60; margin-right: auto; }
        .thinking { background: #9b59b6; margin-right: auto; font-style: italic; opacity: 0.8; }
        .buffering { background: #f39c12; margin-left: auto; text-align: right; font-style: italic; opacity: 0.8; }
        
        .action-buttons { display: flex; gap: 10px; margin-top: 10px; }
        .action-btn {
            padding: 8px 16px; border-radius: 8px; border: none;
            background: rgba(255,255,255,0.15); color: #fff;
            cursor: pointer; font-size: 0.85em; transition: all 0.2s;
        }
        .action-btn:hover { background: rgba(255,255,255,0.25); }
        .action-btn.send { background: #3498db; }
        .action-btn.clear { background: #e74c3c; }
        
        .info { color: #888; font-size: 0.8em; margin-top: 12px; text-align: center; }
    </style>
</head>
<body>
    <h1>🦞 Jarvis de la Ari</h1>
    <p class="subtitle">Bidirectional Voice Chat</p>
    
    <div class="pipeline" id="pipeline">
        <div class="pipeline-step" data-step="record">🎤 Record</div>
        <div class="pipeline-step" data-step="transcribe">📝 Transcribe</div>
        <div class="pipeline-step" data-step="send">📤 Send to AI</div>
        <div class="pipeline-step" data-step="thinking">🧠 Thinking</div>
        <div class="pipeline-step" data-step="tts">🔊 Voice</div>
    </div>
    
    <div class="controls">
        <div class="status-row">
            <div id="status">Tap to talk</div>
            <div id="connection" class="online">● Connected</div>
        </div>
        
        <div class="mode-toggle">
            <input type="checkbox" id="over-mode">
            <label for="over-mode">Wait for "over" to send</label>
        </div>
        
        <button id="toggle-btn">🎤 Talk</button>
        
        <div class="action-buttons">
            <button class="action-btn send" id="send-btn" style="display:none">📤 Send Now</button>
            <button class="action-btn clear" id="clear-btn">🗑️ Clear</button>
        </div>
    </div>
    
    <div id="transcript"></div>
    <p class="info">Speak naturally • Say "over" to send (if enabled)</p>
    
    <script>
        const statusEl = document.getElementById('status');
        const connEl = document.getElementById('connection');
        const toggleBtn = document.getElementById('toggle-btn');
        const transcriptEl = document.getElementById('transcript');
        const overModeCheckbox = document.getElementById('over-mode');
        const sendBtn = document.getElementById('send-btn');
        const clearBtn = document.getElementById('clear-btn');
        
        let mediaRecorder = null;
        let audioChunks = [];
        let isListening = false;
        let stream = null;
        let lastMessageId = 0;
        let pollInterval = null;
        let audioQueue = [];
        let isPlaying = false;
        let displayedMessageIds = new Set();
        
        // Buffer for "over" mode
        let speechBuffer = [];
        let bufferEl = null;
        
        // Pipeline status
        const pipelineSteps = ['record', 'transcribe', 'send', 'thinking', 'tts'];
        
        function setPipelineStep(step, state) {
            // state: 'active', 'done', 'error', or '' (reset)
            const el = document.querySelector(`.pipeline-step[data-step="${step}"]`);
            if (el) {
                el.classList.remove('active', 'done', 'error');
                if (state) el.classList.add(state);
            }
        }
        
        function resetPipeline() {
            pipelineSteps.forEach(s => setPipelineStep(s, ''));
        }
        
        function setPipelineUpTo(step, doneState = 'done') {
            const idx = pipelineSteps.indexOf(step);
            pipelineSteps.forEach((s, i) => {
                if (i < idx) setPipelineStep(s, doneState);
                else if (i === idx) setPipelineStep(s, 'active');
                else setPipelineStep(s, '');
            });
        }
        
        // Start polling
        function startPolling() {
            if (pollInterval) return;
            pollInterval = setInterval(pollMessages, 400);
            connEl.textContent = '● Connected';
            connEl.className = 'online';
        }
        
        async function pollMessages() {
            try {
                const response = await fetch(`/api/poll?since=${lastMessageId}`);
                const data = await response.json();
                
                if (data.messages && data.messages.length > 0) {
                    for (const msg of data.messages) {
                        if (!displayedMessageIds.has(msg.id)) {
                            if (msg.direction === 'jarvis') {
                                setPipelineStep('thinking', 'done');
                                setPipelineStep('tts', 'active');
                                addMessage(msg.text, 'jarvis', msg.id);
                                if (msg.audio_path) {
                                    queueAudio(msg.audio_path, msg.id);
                                }
                            }
                            displayedMessageIds.add(msg.id);
                        }
                        lastMessageId = Math.max(lastMessageId, msg.id);
                    }
                }
                
                connEl.textContent = '● Connected';
                connEl.className = 'online';
            } catch (err) {
                connEl.textContent = '○ Offline';
                connEl.className = 'offline';
            }
        }
        
        function queueAudio(audioPath, msgId) {
            audioQueue.push({ path: audioPath, id: msgId });
            playNextAudio();
        }
        
        async function playNextAudio() {
            if (isPlaying || audioQueue.length === 0) return;
            
            isPlaying = true;
            const { path, id } = audioQueue.shift();
            
            statusEl.textContent = '🔊 Speaking...';
            statusEl.className = 'speaking';
            
            try {
                const audio = new Audio(path);
                await audio.play();
                await new Promise(resolve => {
                    audio.onended = resolve;
                    audio.onerror = resolve;
                    setTimeout(resolve, 60000);
                });
                fetch(`/api/delivered/${id}`, { method: 'POST' });
            } catch (err) {
                console.error('Audio error:', err);
            }
            
            isPlaying = false;
            setPipelineStep('tts', 'done');
            
            if (audioQueue.length > 0) {
                playNextAudio();
            } else if (isListening) {
                statusEl.textContent = '🔴 Listening...';
                statusEl.className = 'listening';
                // Reset pipeline for next message
                setTimeout(resetPipeline, 1500);
            } else {
                statusEl.textContent = 'Tap to talk';
                statusEl.className = '';
                // Reset pipeline after completion
                setTimeout(resetPipeline, 2000);
            }
        }
        
        async function initMic() {
            try {
                stream = await navigator.mediaDevices.getUserMedia({ 
                    audio: { echoCancellation: true, noiseSuppression: true }
                });
                
                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                
                mediaRecorder.ondataavailable = (e) => { 
                    if (e.data.size > 0) audioChunks.push(e.data); 
                };
                
                mediaRecorder.onstop = async () => {
                    if (audioChunks.length > 0) {
                        const blob = new Blob(audioChunks, { type: 'audio/webm' });
                        audioChunks = [];
                        if (blob.size > 500) {
                            await processAudio(blob);
                        }
                    }
                    if (isListening) {
                        setTimeout(startRecording, 100);
                    }
                };
                
                return true;
            } catch (err) {
                statusEl.textContent = '❌ Mic error';
                return false;
            }
        }
        
        function startRecording() {
            if (!mediaRecorder || !isListening) return;
            if (mediaRecorder.state === 'recording') return;
            
            audioChunks = [];
            setPipelineStep('record', 'active');
            try {
                mediaRecorder.start();
                // 8 second chunks for more natural speech
                setTimeout(() => {
                    if (mediaRecorder && mediaRecorder.state === 'recording') {
                        mediaRecorder.stop();
                    }
                }, 8000);
            } catch(e) {
                console.error('Recording error:', e);
            }
        }
        
        function stopRecording() {
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                try { mediaRecorder.stop(); } catch(e) {}
            }
        }
        
        function releaseMic() {
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                stream = null;
            }
            mediaRecorder = null;
        }
        
        async function toggleListening() {
            isListening = !isListening;
            
            if (isListening) {
                if (!mediaRecorder) {
                    if (!await initMic()) {
                        isListening = false;
                        return;
                    }
                }
                toggleBtn.textContent = '🛑 Stop';
                toggleBtn.classList.add('active');
                statusEl.textContent = '🔴 Listening...';
                statusEl.className = 'listening';
                
                if (overModeCheckbox.checked) {
                    speechBuffer = [];
                    sendBtn.style.display = 'inline-block';
                    showBufferIndicator();
                }
                
                startRecording();
            } else {
                toggleBtn.textContent = '🎤 Talk';
                toggleBtn.classList.remove('active');
                stopRecording();
                sendBtn.style.display = 'none';
                
                // If in over mode and has buffer, send it
                if (overModeCheckbox.checked && speechBuffer.length > 0) {
                    sendBufferedSpeech();
                }
                
                setTimeout(() => {
                    if (!isListening) {
                        releaseMic();
                        statusEl.textContent = 'Tap to talk';
                        statusEl.className = '';
                    }
                }, 500);
            }
        }
        
        function showBufferIndicator() {
            if (bufferEl) return;
            bufferEl = document.createElement('div');
            bufferEl.className = 'message buffering';
            bufferEl.id = 'buffer-indicator';
            bufferEl.textContent = '📝 Recording... (say "over" or click Send)';
            transcriptEl.appendChild(bufferEl);
            transcriptEl.scrollTop = transcriptEl.scrollHeight;
        }
        
        function updateBufferIndicator(text) {
            if (bufferEl) {
                const preview = speechBuffer.join(' ').slice(-100);
                bufferEl.textContent = '📝 ' + (preview || 'Recording...');
            }
        }
        
        function removeBufferIndicator() {
            if (bufferEl) {
                bufferEl.remove();
                bufferEl = null;
            }
        }
        
        async function processAudio(blob) {
            const overMode = overModeCheckbox.checked;
            
            statusEl.textContent = '⏳ Processing...';
            statusEl.className = 'processing';
            setPipelineStep('record', 'done');
            setPipelineStep('transcribe', 'active');
            
            try {
                const formData = new FormData();
                formData.append('audio', blob, 'recording.webm');
                formData.append('buffer_mode', overMode ? '1' : '0');
                
                const response = await fetch('/api/transcribe', { method: 'POST', body: formData });
                const data = await response.json();
                const reqId = data.req_id || '';
                setPipelineStep('transcribe', 'done');
                
                if (data.transcript && data.transcript !== '(silence)') {
                    const text = data.transcript.trim();
                    
                    if (overMode) {
                        // Check if user said "over"
                        const lowerText = text.toLowerCase();
                        if (lowerText.includes('over') && (lowerText.endsWith('over') || lowerText.endsWith('over.'))) {
                            // Remove "over" and send
                            const cleanText = text.replace(/\\s*over\\.?\\s*$/i, '').trim();
                            if (cleanText) speechBuffer.push(cleanText);
                            sendBufferedSpeech(reqId);
                        } else {
                            speechBuffer.push(text);
                            updateBufferIndicator(text);
                        }
                    } else {
                        // Immediate mode - send right away
                        await sendToServer(text, reqId);
                    }
                }
            } catch (err) {
                console.error('Process error:', err);
            }
            
            if (isListening) {
                statusEl.textContent = '🔴 Listening...';
                statusEl.className = 'listening';
            }
        }
        
        async function sendBufferedSpeech(reqId = '') {
            removeBufferIndicator();
            
            if (speechBuffer.length === 0) return;
            
            const fullText = speechBuffer.join(' ').trim();
            speechBuffer = [];
            
            if (fullText) {
                await sendToServer(fullText, reqId);
            }
        }
        
        async function sendToServer(text, reqId = '') {
            setPipelineStep('send', 'active');
            try {
                const response = await fetch('/api/speak', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text, req_id: reqId })
                });
                const data = await response.json();
                
                setPipelineStep('send', 'done');
                setPipelineStep('thinking', 'active');
                
                if (data.message_id) {
                    addMessage(text, 'user', data.message_id);
                    displayedMessageIds.add(data.message_id);
                    lastMessageId = Math.max(lastMessageId, data.message_id);
                    showThinking();
                }
            } catch (err) {
                console.error('Send error:', err);
                setPipelineStep('send', 'error');
            }
        }
        
        // Manual send button
        sendBtn.addEventListener('click', () => {
            if (speechBuffer.length > 0) {
                sendBufferedSpeech();
            }
        });
        
        // Clear button - UI only
        clearBtn.addEventListener('click', () => {
            transcriptEl.innerHTML = '';
            displayedMessageIds.clear();
            removeBufferIndicator();
            thinkingEl = null;
        });
        
        let thinkingEl = null;
        function showThinking() {
            if (thinkingEl) return;
            thinkingEl = document.createElement('div');
            thinkingEl.className = 'message thinking';
            thinkingEl.textContent = '🦞 Thinking...';
            transcriptEl.appendChild(thinkingEl);
            transcriptEl.scrollTop = transcriptEl.scrollHeight;
        }
        
        function hideThinking() {
            if (thinkingEl) {
                thinkingEl.remove();
                thinkingEl = null;
            }
        }
        
        function addMessage(text, type, id) {
            if (type === 'jarvis') hideThinking();
            
            const div = document.createElement('div');
            div.className = 'message ' + type;
            div.dataset.id = id;
            const prefix = type === 'user' ? '👤 ' : '🦞 ';
            div.textContent = prefix + text;
            transcriptEl.appendChild(div);
            transcriptEl.scrollTop = transcriptEl.scrollHeight;
        }
        
        async function loadHistory() {
            try {
                const response = await fetch('/api/history?limit=20');
                const data = await response.json();
                if (data.messages) {
                    for (const msg of data.messages) {
                        if (!displayedMessageIds.has(msg.id)) {
                            addMessage(msg.text, msg.direction, msg.id);
                            displayedMessageIds.add(msg.id);
                            lastMessageId = Math.max(lastMessageId, msg.id);
                        }
                    }
                }
            } catch (err) {
                console.error('History error:', err);
            }
        }
        
        toggleBtn.addEventListener('click', toggleListening);
        
        loadHistory();
        startPolling();
    </script>
</body>
</html>
"""

# ============ HTTP HANDLERS ============

async def index(request):
    return web.Response(text=HTML_CLIENT, content_type='text/html')

async def transcribe_handler(request):
    """Transcribe audio only (for buffer mode)"""
    req_id = datetime.datetime.now().strftime("%H%M%S%f")[:10]
    try:
        log_timing(req_id, "1_AUDIO_RECEIVED", "audio chunk received")
        
        reader = await request.multipart()
        field = await reader.next()
        
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
            audio_path = f.name
            while True:
                chunk = await field.read_chunk()
                if not chunk: break
                f.write(chunk)
        
        log_timing(req_id, "2_FFMPEG_START", "converting to wav")
        wav_path = audio_path.replace('.webm', '.wav')
        subprocess.run(['ffmpeg', '-i', audio_path, '-ar', '16000', '-ac', '1', wav_path, '-y'],
                      capture_output=True, timeout=10)
        log_timing(req_id, "3_FFMPEG_DONE", "wav ready")
        
        if not os.path.exists(wav_path):
            return web.json_response({'error': 'Audio conversion failed'}, status=500)
        
        log_timing(req_id, "4_WHISPER_START", "transcribing")
        model = get_whisper_model()
        segments, _ = model.transcribe(wav_path, language='en')
        transcript = ' '.join([s.text for s in segments]).strip()
        log_timing(req_id, "5_WHISPER_DONE", f"transcript: {transcript[:30]}...")
        
        os.unlink(audio_path)
        os.unlink(wav_path)
        
        if not transcript:
            return web.json_response({'transcript': '(silence)'})
        
        log_message("TRANSCRIBE", transcript)
        return web.json_response({'transcript': transcript, 'req_id': req_id})
        
    except Exception as e:
        log_message("ERROR", str(e))
        return web.json_response({'error': str(e)}, status=500)

async def speak_handler(request):
    """Handle text from user (after transcription) - now with AUTO-RESPONSE"""
    try:
        data = await request.json()
        text = data.get('text', '').strip()
        req_id = data.get('req_id', datetime.datetime.now().strftime("%H%M%S%f")[:10])
        
        if not text:
            return web.json_response({'error': 'No text'}, status=400)
        
        log_timing(req_id, "6_TEXT_RECEIVED", f"text: {text[:30]}...")
        log_message("USER", text)
        
        # Insert user message into database
        log_timing(req_id, "7_DB_INSERT_START", "inserting user msg to db")
        msg_id = db_insert_message('user', text)
        log_timing(req_id, "8_DB_INSERT_DONE", f"msg_id: {msg_id}")
        
        # AUTO-RESPOND: Call Claude directly and save response
        log_timing(req_id, "9_AI_START", "calling claude directly")
        response_id = process_voice_message(text, msg_id)
        log_timing(req_id, "10_AI_DONE", f"response_id: {response_id}")
        
        return web.json_response({'message_id': msg_id, 'response_id': response_id, 'req_id': req_id})
        
    except Exception as e:
        log_message("ERROR", str(e))
        return web.json_response({'error': str(e)}, status=500)

async def respond_handler(request):
    """API for Jarvis (OpenClaw) to send responses"""
    req_id = datetime.datetime.now().strftime("%H%M%S%f")[:10]
    try:
        data = await request.json()
        text = data.get('text', '').strip()
        
        if not text:
            return web.json_response({'error': 'No text provided'}, status=400)
        
        log_timing(req_id, "11_RESPOND_RECEIVED", f"jarvis response: {text[:30]}...")
        
        # Insert to DB
        log_timing(req_id, "12_DB_INSERT_START", "inserting jarvis msg to db")
        msg_id = db_insert_message('jarvis', text)
        log_timing(req_id, "13_DB_INSERT_DONE", f"msg_id: {msg_id}")
        
        # Generate TTS
        log_timing(req_id, "14_TTS_START", "generating voice")
        audio_path = generate_tts(text, msg_id)
        log_timing(req_id, "15_TTS_DONE", f"audio: {audio_path}")
        
        # Update with audio path
        if audio_path:
            conn = sqlite3.connect(str(DB_FILE))
            conn.execute('UPDATE messages SET audio_path = ? WHERE id = ?', (audio_path, msg_id))
            conn.commit()
            conn.close()
        
        log_timing(req_id, "16_READY_FOR_POLL", "response ready in db")
        log_message("JARVIS", f"#{msg_id}: {text[:50]}...")
        
        return web.json_response({
            'ok': True,
            'message_id': msg_id,
            'audio_path': audio_path,
            'req_id': req_id
        })
        
    except Exception as e:
        log_message("ERROR", f"Respond error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def poll_handler(request):
    """Poll for new messages"""
    since_id = int(request.query.get('since', 0))
    messages = db_get_messages_since(since_id)
    return web.json_response({'messages': messages})

async def history_handler(request):
    """Get recent message history"""
    limit = int(request.query.get('limit', 20))
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        'SELECT id, timestamp, direction, text, audio_path FROM messages ORDER BY id DESC LIMIT ?',
        (limit,)
    )
    messages = [dict(row) for row in cursor.fetchall()]
    messages.reverse()
    conn.close()
    return web.json_response({'messages': messages})

async def delivered_handler(request):
    """Mark message as delivered"""
    msg_id = int(request.match_info['id'])
    db_mark_delivered(msg_id)
    return web.json_response({'ok': True})

async def serve_audio(request):
    """Serve audio files"""
    filename = request.match_info['filename']
    filepath = AUDIO_DIR / filename
    if filepath.exists():
        return web.FileResponse(filepath)
    return web.Response(status=404)

async def logs_handler(request):
    return web.FileResponse(LOG_FILE) if LOG_FILE.exists() else web.Response(text="No logs")

async def db_viewer(request):
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute('SELECT * FROM messages ORDER BY id DESC LIMIT 50')
    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return web.json_response({'messages': messages})

async def timing_viewer(request):
    """View timing logs"""
    if TIMING_LOG.exists():
        lines = TIMING_LOG.read_text().strip().split('\n')[-50:]
        return web.Response(text='\n'.join(lines), content_type='text/plain')
    return web.Response(text="No timing logs yet")

async def timing_clear(request):
    """Clear timing logs"""
    if TIMING_LOG.exists():
        TIMING_LOG.unlink()
    return web.Response(text="Timing logs cleared")

# ============ MAIN ============

def main():
    log_message("SYSTEM", "Voice chat starting - BIDIRECTIONAL MODE v2")
    
    init_db()
    get_whisper_model()
    
    app = web.Application(client_max_size=50*1024*1024)
    
    app.router.add_get('/', index)
    app.router.add_post('/api/transcribe', transcribe_handler)
    app.router.add_post('/api/speak', speak_handler)
    app.router.add_post('/api/respond', respond_handler)
    app.router.add_get('/api/poll', poll_handler)
    app.router.add_get('/api/history', history_handler)
    app.router.add_post('/api/delivered/{id}', delivered_handler)
    app.router.add_get('/audio/{filename}', serve_audio)
    app.router.add_get('/logs', logs_handler)
    app.router.add_get('/db', db_viewer)
    app.router.add_get('/timing', timing_viewer)
    app.router.add_post('/timing/clear', timing_clear)
    
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(str(SSL_CERT), str(SSL_KEY))
    
    print(f"🦞 Jarvis Voice Chat v2 on https://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, ssl_context=ssl_context)

if __name__ == '__main__':
    main()
