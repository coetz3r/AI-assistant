import os
import json
import time
import asyncio
import ssl
import wave
import tempfile
import threading
from collections import deque
from aiohttp import web, WSMsgType
from ai_engine import AIEngine
from system_stats import SystemStats

engine = AIEngine()
stats_collector = SystemStats()

# Voice websocket connections currently open (for the monitor dashboard —
# separate from monitor sockets themselves, which don't count as "active").
active_voice_connections = set()

# ---- Turn history (for the AI Activity stage-timing panel + History page) --
# Kept as plain JSONL on disk (no DB dependency) so turns survive a restart
# and can be reviewed later. `turn_history` mirrors the tail of that file in
# memory so the live dashboard doesn't have to hit disk every second.
HISTORY_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'history.jsonl')
HISTORY_MEMORY_LIMIT = 200

turn_history = deque(maxlen=HISTORY_MEMORY_LIMIT)
latest_turn = None  # most recent turn's full record, incl. stage timings


def _ensure_history_dir():
    os.makedirs(os.path.dirname(HISTORY_LOG_PATH), exist_ok=True)


def record_turn_history(entry):
    """Appends a completed turn to the in-memory ring buffer (for the live
    dashboard) and to the on-disk JSONL log (for the History page, which
    needs turns to persist across restarts)."""
    global latest_turn
    turn_history.append(entry)
    latest_turn = entry

    try:
        _ensure_history_dir()
        with open(HISTORY_LOG_PATH, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        print(f"Failed to write history log: {e}")


def load_history_from_disk(limit=100, before=None):
    """Reads the persisted history log, newest first. Reads the whole file
    on each call — deliberately simple (matches this project's no-DB
    approach) and fine at the scale of a single-user assistant's turn log."""
    if not os.path.exists(HISTORY_LOG_PATH):
        return []

    entries = []
    try:
        with open(HISTORY_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Failed to read history log: {e}")
        return []

    entries.reverse()

    if before is not None:
        entries = [e for e in entries if e.get("timestamp", 0) < before]

    return entries[:limit]


# Seed both from disk on startup, so the Pipeline Timings panel and Recent
# Turns table show real data immediately after a restart instead of sitting
# empty until a brand-new turn happens in this process.
_seed = load_history_from_disk(limit=HISTORY_MEMORY_LIMIT)
if _seed:
    latest_turn = _seed[0]
    turn_history.extend(reversed(_seed))  # oldest-first, to match live appends


app = web.Application()
app.router.add_static('/webUI', path='webUI', name='webUI')
app.router.add_static('/static', path='static', name='static')


async def process_audio(audio_file, ws, turn_id, stop_flag, conn_state):

    turn_wall_start = time.monotonic()
    # Set by a preceding "turn_start" message if the client reports how long
    # its own VAD spent detecting speech onset — optional, defaults to None
    # until app.js is updated to send it (see webUI/history.html notes).
    vad_ms = conn_state.pop("pending_vad_ms", None)
    stt_ms = llm_ms = tts_ms = None
    user_text = reply_text = backend_used = None
    outcome = "error"

    try:
        def is_stale():
            return conn_state["turn_id"] != turn_id

        # Transcribe audio file
        stt_start = time.monotonic()
        user_text = engine.transcribe(audio_file)
        stt_ms = round((time.monotonic() - stt_start) * 1000)

        if is_stale():
            outcome = "stale"
            return

        # Noise and hallucination filter. Whisper is trained on captioned
        # web video and is well known for "hallucinating" these exact
        # phrases out of silence, background noise, or music playing near
        # the mic - treat them the same as an empty transcription.
        cleaned_text = user_text.strip().lower() if user_text else ""
        stripped_text = cleaned_text.strip(" .,!?")
        ignored_phrases = {
            "", "you", "bye", "thank you",
            "thanks for watching", "thank you for watching",
            "please subscribe", "subscribe to my channel", "like and subscribe",
            "blank_audio", "silence"
        }

        if not cleaned_text or len(stripped_text) < 2 or stripped_text in ignored_phrases:
            print("Ignored background noise or empty transcription.")
            await ws.send_str(json.dumps({"type": "no_speech"}))
            outcome = "no_speech"
            return

        print(f"User said: {user_text}")

        # Generate response — checks stop_flag between tokens so an
        # interrupt part-way through halts generation almost immediately
        llm_start = time.monotonic()
        reply_text = await engine.generate_response(user_text, stop_flag=stop_flag)
        llm_ms = round((time.monotonic() - llm_start) * 1000)

        # Grab which backend just handled this turn straight from the
        # engine's own dashboard stats — avoids needing a second return
        # value from generate_response just for the monitor.
        try:
            backend_used = engine.get_dashboard_stats().get("last_backend")
        except Exception:
            backend_used = None

        if is_stale() or stop_flag.is_set() or not reply_text:
            outcome = "cancelled" if (is_stale() or stop_flag.is_set()) else "empty_reply"
            return

        print(f"AI replied: {reply_text}")

        # Synthesize TTS
        tts_start = time.monotonic()
        audio_output_path = engine.synthesize_speech(reply_text)
        tts_ms = round((time.monotonic() - tts_start) * 1000)

        if is_stale() or stop_flag.is_set():
            outcome = "cancelled"
            return

        if audio_output_path and os.path.exists(audio_output_path):
            with open(audio_output_path, "rb") as f:
                audio_data = f.read()
            # Tag the response with its turn id right before the bytes so
            # the client can drop it if it's since moved on to a new turn
            await ws.send_str(json.dumps({"type": "response_turn", "turnId": turn_id}))
            await ws.send_bytes(audio_data)
            print(f"Audio response sent ({len(audio_data)} bytes)")
            outcome = "ok"
        else:
            print("Failed to generate audio response")
            await ws.send_str(json.dumps({"type": "error", "message": "Couldn't generate a reply, try again."}))
            outcome = "tts_failed"

    except asyncio.CancelledError:
        print(f"Turn {turn_id} cancelled (interrupted)")
        outcome = "cancelled"
        raise
    except Exception as e:
        print(f"Error processing audio: {e}")
        outcome = "error"
        try:
            await ws.send_str(json.dumps({"type": "error", "message": "Something went wrong processing that."}))
        except Exception:
            pass
    finally:
        # Skip logging turns that never really happened (stale audio from a
        # superseded turn, or noise/silence the filter threw out) — every
        # other outcome, including errors and interruptions, is worth
        # keeping so the History page reflects what actually occurred.
        if outcome not in ("stale", "no_speech"):
            record_turn_history({
                "timestamp": time.time(),
                "turn_id": turn_id,
                "source": "voice",
                "user_text": user_text,
                "reply_text": reply_text,
                "backend": backend_used,
                "outcome": outcome,
                "vad_ms": vad_ms,
                "stt_ms": stt_ms,
                "llm_ms": llm_ms,
                "tts_ms": tts_ms,
                "total_ms": round((time.monotonic() - turn_wall_start) * 1000),
            })
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
                    # Optional: client-measured VAD onset time in ms, if
                    # app.js is sending one (see webUI/history.html notes).
                    vad_ms = data.get("vadMs")
                    if isinstance(vad_ms, (int, float)):
                        conn_state["pending_vad_ms"] = round(vad_ms)
                    continue

                if data.get("type") == "interrupt":
                    print("Interrupt received — cancelling current turn")
                    _interrupt_active_turn(conn_state)
                    continue

                if data.get("type") == "text_query":
                    user_text = data.get("text", "").strip()
                    if user_text:
                        print(f"Text query: {user_text}")

                        llm_start = time.monotonic()
                        reply_text = await engine.generate_response(user_text)
                        llm_ms = round((time.monotonic() - llm_start) * 1000)
                        print(f"AI replied: {reply_text}")

                        try:
                            backend_used = engine.get_dashboard_stats().get("last_backend")
                        except Exception:
                            backend_used = None

                        tts_start = time.monotonic()
                        audio_output_path = engine.synthesize_speech(reply_text)
                        tts_ms = round((time.monotonic() - tts_start) * 1000)

                        if audio_output_path and os.path.exists(audio_output_path):
                            with open(audio_output_path, "rb") as f:
                                await ws.send_bytes(f.read())
                            print("Audio response sent")
                            outcome = "ok"
                        else:
                            await ws.send_str(json.dumps({"type": "error", "message": "Couldn't generate a reply, try again."}))
                            outcome = "tts_failed"

                        record_turn_history({
                            "timestamp": time.time(),
                            "turn_id": None,
                            "source": "text",
                            "user_text": user_text,
                            "reply_text": reply_text,
                            "backend": backend_used,
                            "outcome": outcome,
                            "vad_ms": None,
                            "stt_ms": None,
                            "llm_ms": llm_ms,
                            "tts_ms": tts_ms,
                            "total_ms": llm_ms + tts_ms,
                        })
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
    return web.FileResponse('./webUI/monitor.html')


async def handle_monitor_ai_page(request):
    return web.FileResponse('./webUI/ai.html')


async def handle_monitor_network_page(request):
    return web.FileResponse('./webUI/network.html')


async def handle_monitor_process_page(request):
    return web.FileResponse('./webUI/process.html')


async def handle_monitor_history_page(request):
    return web.FileResponse('./webUI/history.html')


async def handle_monitor_ws(request):
    """Pushes a JSON telemetry snapshot once a second to every monitor page
    (overview, AI activity, network, processes) — they all share this one
    feed and each just reads the fields it cares about."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    try:
        while True:
            snapshot = stats_collector.collect_all(
                engine_stats=engine.get_dashboard_stats(),
                active_connections=len(active_voice_connections),
                latest_turn=latest_turn,
                recent_turns=list(turn_history)[-15:][::-1],
            )
            await ws.send_str(json.dumps(snapshot))
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    except Exception as e:
        print(f"Monitor feed error: {e}")
    finally:
        pass

    return ws


async def handle_history_api(request):
    """Returns persisted turn history for the History page, newest first.
    ?limit=100 caps the page size; ?before=<unix ts> pages further back."""
    try:
        limit = int(request.query.get('limit', 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))

    before = request.query.get('before')
    try:
        before = float(before) if before is not None else None
    except ValueError:
        before = None

    entries = load_history_from_disk(limit=limit, before=before)
    return web.json_response({"entries": entries, "count": len(entries)})


async def handle_index(request):
    return web.FileResponse('./index.html')


async def handle_text(request):
    return web.json_response({"status": "OK", "message": "Voice AI Assistant running"})


app.router.add_get('/', handle_index)
app.router.add_get('/ws', handle_websocket)
app.router.add_get('/api/status', handle_text)
app.router.add_get('/monitor', handle_monitor_page)
app.router.add_get('/monitor/ai', handle_monitor_ai_page)
app.router.add_get('/monitor/network', handle_monitor_network_page)
app.router.add_get('/monitor/process', handle_monitor_process_page)
app.router.add_get('/monitor/history', handle_monitor_history_page)
app.router.add_get('/ws/monitor', handle_monitor_ws)
app.router.add_get('/api/history', handle_history_api)


if __name__ == '__main__':
    use_ssl = os.path.exists('cert.pem') and os.path.exists('key.pem')

    if use_ssl:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
        web.run_app(app, host='0.0.0.0', port=8000, ssl_context=ssl_context)
    else:
        web.run_app(app, host='0.0.0.0', port=8000)
