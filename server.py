import os
import json
import asyncio
import ssl
import wave
import tempfile
from aiohttp import web, WSMsgType
from ai_engine import VoiceAIEngine

# Initialize the engine once when the server boots
engine = VoiceAIEngine()

app = web.Application()
app.router.add_static('/static/', path='./static', name='static')

# Global lock – only one audio chunk is processed at a time
processing_lock = asyncio.Lock()


async def handle_index(request):
    return web.FileResponse('./index.html')


async def process_audio(audio_file, ws):
    """Process audio file and send response back through WebSocket.
    Protected by a lock so Whisper + LLM + TTS never run concurrently.
    """
    async with processing_lock:
        try:
            # Transcribe audio to text
            user_text = engine.transcribe(audio_file)

            if user_text and user_text.strip():
                print(f"User said: {user_text}")

                # Generate AI response
                reply_text = await engine.generate_response(user_text)
                print(f"AI replied: {reply_text}")

                # Convert response to speech
                audio_output_path = engine.synthesize_speech(reply_text)

                if audio_output_path and os.path.exists(audio_output_path):
                    with open(audio_output_path, "rb") as f:
                        audio_data = f.read()
                        await ws.send_bytes(audio_data)
                    print(f"Audio response sent ({len(audio_data)} bytes)")
                else:
                    print("Failed to generate audio response")
            else:
                print("No speech detected or empty transcription")

        except Exception as e:
            print(f"Error processing audio: {e}")
        finally:
            # Clean up temp file
            if os.path.exists(audio_file):
                try:
                    os.remove(audio_file)
                    print(f"Cleaned up: {audio_file}")
                except Exception:
                    pass


async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print("WebSocket client connected")

    audio_buffer = bytearray()
    BUFFER_SIZE = 128000  # ~4 seconds at 16 kHz, 16-bit mono

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            audio_buffer.extend(msg.data)

            # Process when we have enough audio data
            if len(audio_buffer) >= BUFFER_SIZE:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
                    temp_input = temp_wav.name

                    # Write WAV header + audio data
                    with wave.open(temp_input, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)
                        wf.writeframes(audio_buffer[:BUFFER_SIZE])

                    # Keep leftover data
                    audio_buffer = audio_buffer[BUFFER_SIZE:]

                    # Run under the lock (via process_audio)
                    asyncio.create_task(process_audio(temp_input, ws))

        elif msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                if data.get("type") == "text_query":
                    user_text = data.get("text", "").strip()
                    if user_text:
                        print(f"Text query: {user_text}")
                        async with processing_lock:
                            reply_text = await engine.generate_response(user_text)
                            print(f"AI replied: {reply_text}")

                            audio_output_path = engine.synthesize_speech(reply_text)

                            if audio_output_path and os.path.exists(audio_output_path):
                                with open(audio_output_path, "rb") as f:
                                    await ws.send_bytes(f.read())
                                print("Audio response sent")
            except json.JSONDecodeError:
                print("Invalid JSON received")

        elif msg.type == WSMsgType.ERROR:
            print(f"WebSocket error: {ws.exception()}")

    print("WebSocket client disconnected")
    return ws


async def handle_text(request):
    """Simple text-only endpoint for testing"""
    return web.json_response({"status": "OK", "message": "Voice AI Assistant is running"})


app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_websocket)
app.router.add_get('/api/status', handle_text)


if __name__ == '__main__':
    use_ssl = os.path.exists('cert.pem') and os.path.exists('key.pem')

    if use_ssl:
        print("Running with SSL (HTTPS/WSS)")
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
        web.run_app(app, host='0.0.0.0', port=8000, ssl_context=ssl_context)
    else:
        print("Running without SSL (HTTP/WS) - For development only")
        print("To enable SSL, generate certificates with:")
        print("   openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365")
        web.run_app(app, host='0.0.0.0', port=8000)