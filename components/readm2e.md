# Voice AI Assistant

A fully local, offline voice assistant that runs entirely on your own machine — no cloud APIs, no internet dependency. Speak into your browser, and the assistant transcribes your speech, generates a reply with a local LLM, and speaks the answer back to you.

---

## How It Works

```
Browser (mic) --WebSocket--> aiohttp server --> VoiceAIEngine
                                                    ├── faster-whisper (speech → text)
                                                    ├── llama.cpp / TinyLlama (text → reply)
                                                    └── Piper (text → speech)
                                <--WebSocket-- WAV audio reply
```

1. **Client (`index.html` + `app.js`)** captures microphone audio in the browser, runs simple Voice Activity Detection (VAD) to detect when you start/stop speaking, and streams the captured utterance to the server as raw PCM16 audio over a WebSocket.
2. **Server (`server.py`)** is an `aiohttp` web app that serves the frontend, accepts the WebSocket connection, wraps incoming audio into a WAV file, and hands it off to the AI engine.
3. **AI Engine (`ai_engine.py`)** does the heavy lifting:
   - **Speech-to-Text**: [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (`base.en` model) transcribes the WAV file to text.
   - **Response Generation**: a local GGUF model (default: TinyLlama 1.1B Chat) running via [`llama-cpp-python`](https://github.com/abetlen/llama-cpp-python) generates a conversational reply.
   - **Text-to-Speech**: [Piper](https://github.com/rhasspy/piper) synthesizes the reply into a WAV file, which is streamed back to the browser and played automatically.
4. The engine also maintains **long-term memory** — it extracts facts (name, age, location, interests, etc.) from the conversation and persists them to `conversation_memory.json`, injecting relevant facts into the system prompt on future runs so the assistant "remembers" the user across sessions.

---

## Features

- **Hands-free interaction** — client-side VAD automatically detects speech start/end, no push-to-talk needed.
- **Persistent memory** — remembers user facts/preferences across sessions via a local JSON store.
- **Fully local / offline** — STT, LLM, and TTS all run on-device; nothing is sent to external services.
- **Mobile-friendly PWA-style UI** — installable "Add to Home Screen" icon, fullscreen standalone mode, mobile audio-context handling.
- **Live audio visualizer** — animated equalizer bars react to the assistant's speech during playback.
- **Optional TLS** — automatically serves over HTTPS/WSS if `cert.pem` / `key.pem` are present, otherwise falls back to HTTP/WS for local development.
- **Text query support** — the WebSocket also accepts plain JSON text queries as an alternative to voice input.

---

## Project Structure

| File | Purpose |
|---|---|
| `server.py` | aiohttp server: serves the UI, handles the `/ws` WebSocket, routes audio/text to the engine |
| `ai_engine.py` | `VoiceAIEngine` class — STT, LLM response generation, TTS, and memory management |
| `index.html` | Single-page frontend UI (status badge, visualizer, start/stop controls) |
| `app.js` | Client-side mic capture, VAD, WebSocket streaming, and audio playback logic |
| `static/style.css` | (referenced, not included) — page styling |
| `conversation_memory.json` | Auto-generated file where long-term user memory is persisted |

---

## Requirements

- Python 3.9+
- Local model files:
  - An LLM in GGUF format (default path: `models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf`)
  - A Piper voice model (default path: `models/en_US-lessac-medium.onnx`)
- Python packages:
  ```
  aiohttp
  llama-cpp-python
  faster-whisper
  piper-tts
  ```
- A modern browser with microphone access (Chrome or Safari recommended) and, for production use, HTTPS (required by browsers to grant microphone permissions on non-localhost origins).

---

## Setup

1. Install dependencies:
   ```bash
   pip install aiohttp llama-cpp-python faster-whisper piper-tts
   ```
2. Download and place your models under `models/`:
   - A quantized chat LLM (e.g. TinyLlama 1.1B Chat, `.gguf` format)
   - A Piper TTS voice (`.onnx` + config)
3. (Optional) Generate a self-signed TLS certificate for local HTTPS/WSS:
   ```bash
   openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
   ```
4. Run the server:
   ```bash
   python server.py
   ```
5. Open `https://localhost:8000` (or `http://localhost:8000` without SSL) in your browser, tap **Start Voice**, and start talking.

---

## Notes & Limitations

- Only one audio chunk is processed at a time (`asyncio.Lock`), so overlapping utterances are queued rather than processed in parallel.
- VAD (silence/speech detection) runs entirely client-side using an RMS volume threshold — it is simple and may need tuning (`SILENCE_THRESHOLD`, `MAX_SILENCE_DURATION` in `app.js`) for noisy environments or sensitive microphones.
- Memory extraction in `ai_engine.py` uses keyword/pattern matching rather than an NLU model, so it's heuristic and may occasionally mis-tag facts.
- Without a valid `cert.pem`/`key.pem` pair, the server runs over plain HTTP/WS, which most browsers only allow microphone access for on `localhost`.
