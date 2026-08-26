import os
import wave
import asyncio
import json
import multiprocessing
import threading
import time
import re
from collections import deque
from datetime import datetime
from llama_cpp import Llama
from faster_whisper import WhisperModel
from piper import PiperVoice
from dotenv import load_dotenv
from groq import AsyncGroq
from memory_manager import MemoryManager

load_dotenv()

class AIEngine:
    def __init__(
        self,
        llm_model_path="models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        tts_model_path="models/en_US-lessac-medium.onnx",
        use_external_api=True
    ):
        num_cores = max(1, multiprocessing.cpu_count() // 2)
        
        # 1. Local LLM (Main & Extraction)
        self.llm = Llama(
            model_path=llm_model_path,
            n_ctx=2048,
            n_threads=num_cores,
            n_gpu_layers=0,
            n_batch=512,
            verbose=False
        )

        # 2. External (Only for heavy fallback generation, not memory)
        self.use_external_api = use_external_api
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

        # 3. STT/TTS
        self.stt = WhisperModel("base.en", device="cpu", compute_type="int8")
        self.tts_voice = PiperVoice.load(tts_model_path)

        # 4. Memory Subsystem
        self.memory = MemoryManager()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        self.stats = {
            "total_requests": 0,
            "local_count": 0,
            "cloud_count": 0,
            "last_backend": None,
            "last_latency_ms": None,
            "avg_latency_ms": None,
        }
        self.recent_turns = deque(maxlen=20)

    async def extract_memory_local(self, user_text, assistant_response):
        """Uses local LLM to extract facts."""
        start_t = time.time()
        
        # TinyLlama needs a very strict, simple prompt to behave
        prompt = (
            f"<|system|>\nYou are a memory extractor. Extract 1-2 permanent facts about the user from this chat. "
            f"Ignore fluff. Output ONLY valid JSON: {{\"facts\": [\"content\", importance(1-10)]}}<|user|>\n"
            f"User: {user_text}\nAI: {assistant_response}\n<|assistant|>\n"
        )

        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None, 
            lambda: self.llm(prompt, max_tokens=128, temperature=0.1, stop=["<|user|>"])
        )
        
        raw_text = output["choices"][0]["text"].strip()
        extracted = []
        try:
            # Attempt to find JSON in output
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                facts = data.get("facts", [])
                # Handle both list of strings or list of [str, int]
                for f in facts:
                    if isinstance(f, list) and len(f) >= 2:
                        extracted.append({"content": f[0], "importance": f[1]})
                    elif isinstance(f, str):
                        extracted.append({"content": f, "importance": 5})
        except Exception as e:
            print(f"[MEMORY EXTRACT ERROR] {e} | Raw: {raw_text}")
        
        duration = time.time() - start_t
        return extracted, duration

    async def generate_response(self, user_text, stop_flag=None):
        if stop_flag is None:
            stop_flag = threading.Event()

        turn_start = time.time()
        
        # 1. MEMORY RECALL
        recalled_memories, recall_dur = self.memory.get_relevant_memories(user_text)
        print(f"[MEMORY RECALL] {len(recalled_memories)} facts found ({recall_dur:.3f}s)")

        # 2. CONTEXT COMPILATION
        system_content = (
            "You are a direct, concise voice assistant. 1-3 short sentences only. "
            "Use context naturally. No filler."
        )
        if recalled_memories:
            system_content += "\nRelevant facts:\n" + "\n".join([f"- {m['content']}" for m in recalled_memories])

        history = [{"role": "system", "content": system_content}, {"role": "user", "content": user_text}]

        # 3. GENERATION
        is_heavy = len(user_text.split()) > 30 or "code" in user_text.lower()
        reply = ""
        backend = "local"

        if not is_heavy:
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(
                None, 
                lambda: self._run_local_llm_sync(history, stop_flag)
            )
        
        if not reply and self.use_external_api:
            print("[LLM] Offloading to Groq...")
            try:
                resp = await self.groq_client.chat.completions.create(
                    model="llama3-8b-8192",
                    messages=history,
                    max_tokens=256
                )
                reply = resp.choices[0].message.content.strip()
                backend = "groq"
            except Exception as e:
                reply = "I'm having trouble connecting."
                backend = "error"

        # 4. STATS & LOGGING
        latency_ms = (time.time() - turn_start) * 1000
        self._update_stats(backend, latency_ms, user_text)
        print(f"[LLM] {backend.upper()} response in {latency_ms/1000:.2f}s")

        # 5. ASYNC MEMORY UPDATE (Two-way)
        if reply:
            asyncio.create_task(self._bg_memory_task(user_text, reply))

        return reply

    def _run_local_llm_sync(self, messages, stop_flag):
        # Convert Chat ML for TinyLlama
        prompt = ""
        for m in messages:
            prompt += f"<|{m['role']}|>\n{m['content']}\n"
        prompt += "<|assistant|>\n"
        
        output = self.llm(
            prompt,
            max_tokens=256,
            temperature=0.7,
            stop=["<|user|>", "<|system|>"],
            stream=False # Keep sync for executor
        )
        return output["choices"][0]["text"].strip()

    async def _bg_memory_task(self, user_text, reply):
        # Fact Extraction
        facts, extract_dur = await self.extract_memory_local(user_text, reply)
        
        for f in facts:
            status, upsert_dur = self.memory.upsert_fact(
                f["content"], 
                "user_stated" if len(user_text) > 10 else "system_derived",
                f["importance"],
                1.0
            )
            print(f"[MEMORY {status.upper()}] {f['content']} ({upsert_dur:.3f}s)")
        
        print(f"[FACT EXTRACTION] Total duration: {extract_dur:.3f}s")

    def _update_stats(self, backend, latency_ms, user_text):
        self.stats["total_requests"] += 1
        if backend == "local": self.stats["local_count"] += 1
        else: self.stats["cloud_count"] += 1
        self.stats["last_backend"] = backend
        self.stats["last_latency_ms"] = round(latency_ms, 1)
        self.recent_turns.appendleft({
            "timestamp": datetime.now().isoformat(),
            "user_snippet": user_text[:50],
            "backend": backend,
            "latency_ms": round(latency_ms, 1),
        })

    def transcribe(self, audio_file_path):
        start_t = time.time()
        segments, _ = self.stt.transcribe(audio_file_path, beam_size=1, vad_filter=True)
        text = "".join([s.text for s in segments]).strip()
        print(f"[STT] Transcribed in {time.time() - start_t:.2f}s")
        return text

    def synthesize_speech(self, text, output_path="static/output.wav"):
        start_t = time.time()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.tts_voice.config.sample_rate)
            self.tts_voice.synthesize(text, wav_file)
        print(f"[TTS] Synthesized in {time.time() - start_t:.2f}s")
        return output_path

    def get_dashboard_stats(self):
        with sqlite3.connect(self.memory.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        
        return {
            **self.stats,
            "recent_turns": list(self.recent_turns),
            "total_facts": count,
            "session_id": self.session_id,
        }