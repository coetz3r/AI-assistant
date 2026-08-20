# AI Assistant - Self-Hosted Voice Companion

A lightweight, privacy-focused, locally hosted AI voice assistant built with Python, WebSockets, and HTML5. This project turns a local Linux server into a responsive, real-time voice interface accessible from any desktop or mobile browser via secure local network or Tailscale connections.

---

## Features

- **Local Inference:** Built on `llama-cpp-python` for text generation and `faster-whisper` for lightweight speech-to-text.
- **Natural TTS:** Speech synthesis powered by `piper-tts` for fast, natural-sounding audio output.
- **Real-Time WebSockets:** Low-latency bi-directional voice and text streaming over an encrypted HTTPS/WSS server using `aiohttp`.
- **Mobile & Desktop Ready:** PWA-ready web UI with custom launcher icons and automatic reconnection handling for on-the-go workspace use.
- **Tailscale Support:** Secure remote network access without exposing server ports to the public internet.

---

## Tech Stack

- **Backend:** Python 3, `aiohttp`, `llama-cpp-python`, `faster-whisper`, `piper-tts`, `wave`
- **Frontend:** Vanilla HTML5, JavaScript (Web Audio API / WebSockets), CSS
- **Network & Security:** SSL/TLS (Self-Signed Certificates), Tailscale

---

## Architecture & Data Flow

1. **Client Audio Capture:** The browser captures microphone audio via the Web Audio API and streams binary PCM audio chunks over a secure WebSocket (`/ws`).
2. **STT Processing:** `server.py` formats incoming audio with standard WAV headers and passes the buffer to `faster-whisper` for transcription.
3. **LLM Reasoning:** Transcribed text is processed by `llama-cpp-python` (Llama 3.2 1B Instruct) with system context and conversation history.
4. **TTS Synthesis:** `piper-tts` synthesizes the generated response into WAV format, which is sent back over the WebSocket and played directly in the browser.

---

## Setup & Usage

### 1. Clone the Repository
```bash
git clone https://github.com/coetz3r/AI-assistant.git
cd AI-assistant
```

### 2. Set Up Environment & Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install aiohttp llama-cpp-python faster-whisper piper-tts
```

### 3. Generate SSL Certificates
```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
```

### 4. Run the Server
```bash
python3 server.py
```

### 5. Access the Assistant
Open your browser and navigate to:
- **Local:** `https://localhost:8000`
- **Tailscale / LAN:** `https://<YOUR-SERVER-IP>:8000`