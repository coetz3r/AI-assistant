import os
import json
import asyncio
import ssl
import wave
import tempfile
import threading
from aiohttp import web, WSMsgType
from ai_engine import AIEngine
from system_stats import SystemStats

engine = AIEngine()
stats_collector = SystemStats()

# Voice websocket connections currently open (for the monitor dashboard —
# separate from monitor sockets themselves, which don't count as "active").
active_voice_connections = set()

app = web.Application()
app.router.add_static('/static', path='static', name='static')


async def process_audio(audio_file, ws, turn_id, stop_flag, conn_state):

    try:
        def is_stale():
            return conn_state["turn_id"] != turn_id

        # Transcribe audio file
        user_text = engine.transcribe(audio_file)

        if is_stale():
            return

        # Noise and hallucination filter
        cleaned_text = user_text.strip().lower() if user_text else ""
        ignored_phrases = ["", "[blank_audio]", "you", "thank you.", "thank you", "bye.", "bye"]

        if not cleaned_text or len(cleaned_text) < 2 or cleaned_text in ignored_phrases:
            print("Ignored background noise or empty transcription.")
            await ws.send_str(json.dumps({"type": "no_speech"}))
            return

        print(f"User said: {user_text}")

        # Generate response — checks stop_flag between tokens so an
        # interrupt part-way through halts generation almost immediately
        reply_text = await engine.generate_response(user_text, stop_flag=stop_flag)

        if is_stale() or stop_flag.is_set() or not reply_text:
            return

        print(f"AI replied: {reply_text}")

        # Synthesize TTS
        audio_output_path = engine.synthesize_speech(reply_text)

        if is_stale() or stop_flag.is_set():
            return

        if audio_output_path and os.path.exists(audio_output_path):
            with open(audio_output_path, "rb") as f:
                audio_data = f.read()
            # Tag the response with its turn id right before the bytes so
            # the client can drop it if it's since moved on to a new turn
            await ws.send_str(json.dumps({"type": "response_turn", "turnId": turn_id}))
            await ws.send_bytes(audio_data)
            print(f"Audio response sent ({len(audio_data)} bytes)")
        else:
            print("Failed to generate audio response")
            await ws.send_str(json.dumps({"type": "error", "message": "Couldn't generate a reply, try again."}))

    except asyncio.CancelledError:
        print(f"Turn {turn_id} cancelled (interrupted)")
        raise
    except Exception as e:
        print(f"Error processing audio: {e}")
        try:
            await ws.send_str(json.dumps({"type": "error", "message": "Something went wrong processing that."}))
        except Exception:
            pass
    finally:
        if os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                print(f"Cleaned up: {audio_file}")
            except Exception:
                pass


def _interrupt_active_turn(conn_state):
    """Cancels whatever's currently in flight for this connection and marks
    it stale so a late-finishing thread can't sneak a response through."""
    conn_state["turn_id"] = -1
    if conn_state["stop_flag"] is not None:
        conn_state["stop_flag"].set()
    active_task = conn_state["active_task"]
    if active_task and not active_task.done():
        active_task.cancel()


async def handle_websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    print("WebSocket client connected")
    active_voice_connections.add(ws)
    client_sample_rate = 16000

    # Per-connection state: which turn is "live", the task processing it,
    # and the flag that tells its background LLM thread to stop early.
    conn_state = {"turn_id": 0, "active_task": None, "stop_flag": None}

    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            turn_id = conn_state["turn_id"]
            stop_flag = threading.Event()
            conn_state["stop_flag"] = stop_flag

            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
                temp_input = temp_wav.name

                with wave.open(temp_input, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(client_sample_rate)
                    wf.writeframes(msg.data)

                # A new turn always supersedes whatever was still running
                if conn_state["active_task"] and not conn_state["active_task"].done():
                    conn_state["active_task"].cancel()

                conn_state["active_task"] = asyncio.create_task(
                    process_audio(temp_input, ws, turn_id, stop_flag, conn_state)
                )

        elif msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)

                if data.get("type") == "init":
                    reported_rate = data.get("sampleRate")
                    if isinstance(reported_rate, (int, float)) and reported_rate > 0:
                        client_sample_rate = int(reported_rate)
                        print(f"Client reported sample rate: {client_sample_rate} Hz")
                    continue

                if data.get("type") == "turn_start":
                    turn_id = data.get("turnId")
                    if isinstance(turn_id, (int, float)):
                        conn_state["turn_id"] = turn_id
                    continue

                if data.get("type") == "interrupt":
                    print("Interrupt received — cancelling current turn")
                    _interrupt_active_turn(conn_state)
                    continue

                if data.get("type") == "text_query":
                    user_text = data.get("text", "").strip()
                    if user_text:
                        print(f"Text query: {user_text}")
                        reply_text = await engine.generate_response(user_text)
                        print(f"AI replied: {reply_text}")

                        audio_output_path = engine.synthesize_speech(reply_text)

                        if audio_output_path and os.path.exists(audio_output_path):
                            with open(audio_output_path, "rb") as f:
                                await ws.send_bytes(f.read())
                            print("Audio response sent")
                        else:
                            await ws.send_str(json.dumps({"type": "error", "message": "Couldn't generate a reply, try again."}))
            except json.JSONDecodeError:
                print("Invalid JSON received")

        elif msg.type == WSMsgType.ERROR:
            print(f"WebSocket error: {ws.exception()}")

    # Clean up any turn still running when the client disconnects
    _interrupt_active_turn(conn_state)
    active_voice_connections.discard(ws)

    print("WebSocket client disconnected")
    return ws


async def handle_monitor_page(request):
    return web.FileResponse('./static/monitor.html')


async def handle_monitor_ws(request):
    """Pushes a JSON telemetry snapshot once a second to the monitor UI."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    print("Monitor client connected")

    try:
        while True:
            snapshot = stats_collector.collect_all(
                engine_stats=engine.stats,
                active_connections=len(active_voice_connections),
            )
            await ws.send_str(json.dumps(snapshot))
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    except Exception as e:
        print(f"Monitor feed error: {e}")
    finally:
        print("Monitor client disconnected")

    return ws


async def handle_index(request):
    return web.FileResponse('./index.html')


async def handle_text(request):
    return web.json_response({"status": "OK", "message": "Voice AI Assistant running"})


app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_websocket)
app.router.add_get('/api/status', handle_text)
app.router.add_get('/monitor', handle_monitor_page)
app.router.add_get('/ws/monitor', handle_monitor_ws)


if __name__ == '__main__':
    use_ssl = os.path.exists('cert.pem') and os.path.exists('key.pem')

    if use_ssl:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
        web.run_app(app, host='0.0.0.0', port=8000, ssl_context=ssl_context)
    else:
        web.run_app(app, host='0.0.0.0', port=8000)
