# Voice Chat Health & Performance Audit Guide 🦞

*15 suggestions to audit, monitor, and improve the voice chat service (port 10010)*

---

## 🔍 Live Monitoring

### 1. Health Endpoint
Add a `/health` endpoint that returns:
```json
{
  "status": "ok",
  "whisper_loaded": true,
  "active_connections": 2,
  "uptime_seconds": 3600,
  "last_transcription_ms": 680,
  "memory_mb": 512
}
```

### 2. Prometheus Metrics
Expose `/metrics` endpoint for Prometheus scraping:
- `voice_transcriptions_total` (counter)
- `voice_transcription_duration_seconds` (histogram)
- `voice_tts_duration_seconds` (histogram)
- `voice_active_connections` (gauge)
- `voice_errors_total` (counter by type)

### 3. Real-time Log Streaming
WebSocket endpoint `/ws/logs` for live log tailing from dashboard.

---

## ⚡ Performance Improvements

### 4. Whisper Model Caching
- Pre-load model on startup (already done ✅)
- Consider `whisper-small` for better accuracy vs `tiny`
- GPU acceleration if available (CUDA)

### 5. Connection Pooling
- Reuse HTTP connections to OpenClaw API
- Keep-alive for WebSocket connections
- Connection timeout handling

### 6. Audio Buffer Optimization
- Tune chunk size for latency vs quality
- Implement VAD (Voice Activity Detection) to skip silence
- Compress audio before transmission

### 7. Async Processing Pipeline
```
Audio → Queue → Whisper Worker → Queue → AI Worker → Queue → TTS Worker
```
- Separate worker threads for each stage
- Non-blocking I/O throughout

---

## 🛡️ Reliability & Recovery

### 8. Automatic Restart on Crash
Systemd service with restart policy:
```ini
[Service]
Restart=always
RestartSec=5
WatchdogSec=30
```

### 9. Watchdog Timer
- Internal heartbeat every 10 seconds
- If processing takes >60s, log warning
- If >120s, force restart transcription

### 10. Graceful Degradation
- If Whisper fails, return error message via TTS
- If OpenClaw API slow, use cached responses
- If TTS fails, return text-only response

### 11. Request Timeout Handling
```python
async with asyncio.timeout(30):
    result = await transcribe(audio)
```
- Whisper timeout: 30s
- AI response timeout: 60s
- TTS timeout: 15s

---

## 📊 Debugging & Diagnostics

### 12. Request Tracing
- Unique request ID for each voice interaction
- Log timing for each stage (already in timing.log ✅)
- Trace ID in all log messages

### 13. Audio Dump for Debugging
- Save failed transcriptions to `/audio/failed/`
- Include metadata: timestamp, duration, error
- Auto-cleanup after 24h

### 14. Memory Profiling
- Track memory usage over time
- Alert if >80% of available RAM
- Log memory stats every 5 minutes

---

## 🔧 Operational

### 15. Status Dashboard
Web UI showing:
- Current connections
- Last 10 interactions (transcript + response)
- Error rate graph
- Latency percentiles (p50, p95, p99)
- System resources (CPU, RAM, disk)

---

## 🚀 Quick Implementation Priority

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| 🔴 High | Health endpoint | 1h | Immediate monitoring |
| 🔴 High | Request timeouts | 2h | Prevent hangs |
| 🔴 High | Systemd watchdog | 1h | Auto-recovery |
| 🟡 Med | Prometheus metrics | 3h | Long-term visibility |
| 🟡 Med | Audio dump on fail | 2h | Debug stuck issues |
| 🟢 Low | Status dashboard | 8h | Nice to have |

---

## 📝 Current Issue Analysis

**Problem:** Service got stuck after Whisper transcription (23:41:27)

**Likely causes:**
1. Deadlock in async code path
2. Memory pressure from long-running process
3. OpenClaw API timeout not handled

**Fix:** Implement #9 (Watchdog) + #11 (Timeouts) to prevent future hangs.

---

*Created: 2026-02-05*
