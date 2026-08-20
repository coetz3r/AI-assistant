import os
import wave
import asyncio
import json
from datetime import datetime
from llama_cpp import Llama
from faster_whisper import WhisperModel
from piper import PiperVoice


class VoiceAIEngine:
    def __init__(
        self,
        llm_model_path="models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        tts_model_path="models/en_US-lessac-medium.onnx",
        memory_file="conversation_memory.json"
    ):
        # 1. Initialize LLM Engine
        self.llm = Llama(
            model_path="models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
            n_ctx=512,
            n_threads=1,
            n_gpu_layers=0,  # keep on CPU
            n_batch=64,
            verbose=False
        )

        # 2. Initialize STT
        self.stt = WhisperModel("base.en", device="cpu", compute_type="int8")

        # 3. Initialize Piper TTS
        self.tts_voice = PiperVoice.load(tts_model_path)

        # 4. Conversation History (short-term)
        self.history = [
            {
                "role": "system",
                "content": (
                    "You are a concise, helpful voice assistant. "
                    "Keep answers brief and conversational."
                )
            }
        ]

        # 5. Long-term Memory System
        self.memory_file = memory_file
        self.long_term_memory = self.load_memory()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Inject existing long-term memory into the system prompt so the model
        # remembers facts even after a restart
        self._inject_memory_into_prompt()

    def load_memory(self):
        """Load long-term memory from file"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"facts": [], "preferences": [], "conversations": []}

    def save_memory(self):
        """Save long-term memory to file"""
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.long_term_memory, f, indent=2, ensure_ascii=False)

    def _inject_memory_into_prompt(self):
        """Add known facts/preferences to the system message so the LLM uses them"""
        facts = self.long_term_memory.get("facts", [])
        prefs = self.long_term_memory.get("preferences", [])

        if not facts and not prefs:
            return

        memory_lines = []
        for item in facts[-20:]:  # keep the prompt from growing too large
            cat = item.get("category", "info")
            detail = item.get("detail", "")
            if detail:
                memory_lines.append(f"- {cat}: {detail}")

        for item in prefs[-10:]:
            detail = item.get("detail", "") if isinstance(item, dict) else str(item)
            if detail:
                memory_lines.append(f"- preference: {detail}")

        if memory_lines:
            memory_block = "Known information about the user:\n" + "\n".join(memory_lines)
            # Update the system message
            self.history[0]["content"] += "\n\n" + memory_block

    def extract_memory(self, text):
        """Extract important facts from conversation (simple pattern matching)"""
        memory_items = []

        personal_patterns = [
            ("name", ["my name is", "i'm", "i am", "call me"]),
            ("age", ["i am", "years old"]),
            ("location", ["from", "live in", "located in"]),
            ("job", ["work as", "job is", "i'm a", "i am a"]),
            ("interest", ["like", "enjoy", "love", "hobby", "interested in"]),
        ]

        lower = text.lower()
        for category, patterns in personal_patterns:
            for pattern in patterns:
                if pattern in lower:
                    start = lower.find(pattern) + len(pattern)
                    end = min(start + 50, len(text))
                    detail = text[start:end].strip(" .,!?\n")
                    if len(detail) > 2:
                        memory_items.append({
                            "category": category,
                            "detail": detail,
                            "timestamp": datetime.now().isoformat()
                        })
                    break

        return memory_items

    def transcribe(self, audio_file_path):
        """Converts user speech to text."""
        segments, _ = self.stt.transcribe(audio_file_path)
        return "".join(segment.text for segment in segments).strip()

    async def generate_response(self, user_text):
        """Generates response text while retaining memory."""
        self.history.append({"role": "user", "content": user_text})

        # Extract and store new memory
        new_memory = self.extract_memory(user_text)
        if new_memory:
            self.long_term_memory["facts"].extend(new_memory)
            self.save_memory()
            # Optionally re-inject so the current turn already knows the new fact
            # (simple approach: just keep going; next restart will pick it up)

        # Keep history manageable
        if len(self.history) > 11:
            self.history = [self.history[0]] + self.history[-10:]

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.llm.create_chat_completion(
                messages=self.history,
                max_tokens=256,
                temperature=0.7
            )
        )

        reply = response["choices"][0]["message"]["content"]
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def synthesize_speech(self, text, output_path="static/output.wav"):
        """Converts text response to audio file via Piper."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.tts_voice.config.sample_rate)

            # Prefer the modern method name; fall back for older piper versions
            if hasattr(self.tts_voice, "synthesize_wav"):
                self.tts_voice.synthesize_wav(text, wav_file)
            else:
                self.tts_voice.synthesize(text, wav_file)

        return output_path

    def clear_memory(self):
        """Clear all stored memory"""
        self.long_term_memory = {
            "facts": [],
            "preferences": [],
            "conversations": []
        }
        self.save_memory()
        # Reset history but keep the base system prompt
        self.history = [
            {
                "role": "system",
                "content": (
                    "You are a concise, helpful voice assistant. "
                    "Keep answers brief and conversational."
                )
            }
        ]