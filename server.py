import os
import json
import asyncio
import ssl
import wave
from aiohttp import web, WSMsgType
from ai_engine import VoiceAIEngine

# Initialize the engine once when the server boots
engine = VoiceAIEngine()

app = web.Application()
app.router.add_static('/static/', path='./static', name='static')

async def handle_index(request):
    return web.FileResponse('./index.html')

async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    audio_buffer = bytearray()

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            audio_buffer.extend(msg.data)

            # Accumulate ~2 seconds of 16kHz 16-bit mono audio (64,000 bytes) before running Whisper
            if len(audio_buffer) >= 64000:
                temp_input = "temp_user_input.wav"

                # Write raw PCM stream into a formatted WAV container
                with wave.open(temp_input, "wb") as wf:
                    wf.setnchannels(1)       # Mono
                    wf.setsampwidth(2)      # 16-bit PCM (2 bytes per sample)
                    wf.setframerate(16000)  # 16kHz sample rate
                    wf.writeframes(audio_buffer)

                audio_buffer.clear()

                user_text = engine.transcribe(temp_input)

                if user_text:
                    reply_text = await engine.generate_response(user_text)
                    audio_output_path = engine.synthesize_speech(reply_text)

                    if audio_output_path and os.path.exists(audio_output_path):
                        with open(audio_output_path, "rb") as f:
                            await ws.send_bytes(f.read())

        elif msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("type") == "text_query":
                reply_text = await engine.generate_response(data["text"])
                audio_output_path = engine.synthesize_speech(reply_text)

                if audio_output_path and os.path.exists(audio_output_path):
                    with open(audio_output_path, "rb") as f:
                        await ws.send_bytes(f.read())

    return ws

app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_websocket)

if __name__ == '__main__':
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

    web.run_app(app, host='0.0.0.0', port=8000, ssl_context=ssl_context)