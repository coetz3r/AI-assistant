import os
import json
import asyncio
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

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            temp_input = "temp_user_input.wav"
            with open(temp_input, "wb") as f:
                f.write(msg.data)
            
            user_text = engine.transcribe(temp_input)
            
            if user_text:
                reply_text = engine.generate_response(user_text)
                audio_output_path = engine.synthesize_speech(reply_text)
                
                with open(audio_output_path, "rb") as f:
                    await ws.send_bytes(f.read())

        elif msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("type") == "text_query":
                reply_text = engine.generate_response(data["text"])
                audio_output_path = engine.synthesize_speech(reply_text)
                with open(audio_output_path, "rb") as f:
                    await ws.send_bytes(f.read())

    return ws

app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_websocket)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8000)