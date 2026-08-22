import os
import wave
import asyncio
import json
import multiprocessing
import threading
from datetime import datetime
from llama_cpp import Llama
from faster_whisper import WhisperModel
from piper import PiperVoice
from dotenv import load_dotenv
from groq import AsyncGroq

# Load environment variables
load_dotenv()


class AIEngine:
    def __init__(
        self,
        llm_model_path="models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        tts_model_path="models/en_US-lessac-medium.onnx",
        memory_file="conversation_memory.json",
        max_memory_items=1000,
        max_history_turns=50,
        use_external_api=True
    ):
        num_cores = max(1, multiprocessing.cpu_count() // 2)
        
        # 1. Initialize Local LLM Engine
        self.llm = Llama(
            model_path=llm_model_path,
            n_ctx=2048,
            n_threads=num_cores,
            n_gpu_layers=0,
            n_batch=512,
            verbose=False
        )

        # 2. Initialize External Cloud LLM (Groq)
        self.use_external_api = use_external_api
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

        # 3. Initialize STT
        self.stt = WhisperModel("base.en", device="cpu", compute_type="int8")

        # 4. Initialize Piper TTS
        self.tts_voice = PiperVoice.load(tts_model_path)

        # 5. Memory Configuration
        self.max_memory_items = max_memory_items
        self.max_history_turns = max_history_turns
        self.memory_file = memory_file
        self.long_term_memory = self.load_memory()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 6. Initialize conversation history
        self.history = self._build_conversation_history()
        
        # 7. Tracking metrics
        self.last_save_time = datetime.now()
        self.save_interval_seconds = 30

    def load_memory(self):
        default_memory = {
            "facts": [],
            "conversation_history": [],
            "topics_discussed": [],
            "last_updated": None
        }
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    for key in default_memory:
                        if key not in loaded:
                            loaded[key] = default_memory[key]
                    return loaded
            except Exception as e:
                print(f"Error loading memory: {e}")
                return default_memory
        return default_memory

    def save_memory(self):
        try:
            self.long_term_memory["last_updated"] = datetime.now().isoformat()
            self.long_term_memory["total_conversations"] = len(self.long_term_memory["conversation_history"])
            self.long_term_memory["total_facts"] = len(self.long_term_memory["facts"])
            
            if len(self.long_term_memory["facts"]) > self.max_memory_items:
                self.long_term_memory["facts"] = self.long_term_memory["facts"][-self.max_memory_items:]
            
            if len(self.long_term_memory["conversation_history"]) > self.max_memory_items:
                self.long_term_memory["conversation_history"] = self.long_term_memory["conversation_history"][-self.max_memory_items:]
            
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.long_term_memory, f, indent=2, ensure_ascii=False)
            
            self.last_save_time = datetime.now()
            print(f"Memory saved to {self.memory_file}")
        except Exception as e:
            print(f"Error saving memory: {e}")

    def _build_conversation_history(self):
        system_content = (
            "You are a direct, concise voice assistant.\n"
            "- Answer the user's question directly in 1 to 3 short sentences.\n"
            "- NEVER end responses with conversational filler like 'Goodbye', "
            "'Have a nice day', or 'Is there anything else?'.\n"
            "- NEVER explain what you are or list your capabilities unless explicitly asked.\n"
            "- Speak naturally and casually, as if talking on a phone call.\n"
            "- Use recalled context naturally to personalize responses, without "
            "announcing that you're recalling it."
        )
        facts = self.long_term_memory.get("facts", [])[-20:]
        if facts:
            system_content += "\n\nEnduring facts learned about the user:"
            for fact in facts:
                system_content += f"\n- {fact}"
        
        return [{"role": "system", "content": system_content}]

    async def extract_memory_autonomous(self, user_text, assistant_response):
        extraction_prompt = (
            "You are an autonomous memory module. Analyze this conversation exchange and extract "
            "any new, permanent facts, technical specifications, or explicit preferences about the user.\n"
            "Ignore greetings or casual banter. Output ONLY a valid JSON object matching this schema:\n"
            '{"new_facts": ["fact 1", "fact 2"]}\n\n'
            f"User: {user_text}\n"
            f"Assistant: {assistant_response}"
        )

        try:
            response = await self.groq_client.chat.completions.create(
                model="openai/gpt-oss-20b", #llama-3.3-70b-versatile
                messages=[{"role": "user", "content": extraction_prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            data = json.loads(response.choices[0].message.content)
            extracted = data.get("new_facts", [])
            
            existing_facts = set(self.long_term_memory["facts"])
            unique_facts = [f for f in extracted if f not in existing_facts]
            
            return unique_facts
        except Exception as e:
            print(f"Autonomous memory extraction error: {e}")
            return []

    def transcribe(self, audio_file_path, no_speech_threshold=0.6, min_avg_logprob=-1.0):
        segments, _ = self.stt.transcribe(
            audio_file_path,
            beam_size=1,
            best_of=1,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        kept_text = []
        for segment in segments:
            # Whisper's own estimate that this chunk was silence/noise, not speech
            if segment.no_speech_prob > no_speech_threshold:
                continue
            # Low-confidence segments are frequently hallucinated words from
            # background noise rather than real speech
            if segment.avg_logprob < min_avg_logprob:
                continue
            kept_text.append(segment.text)

        return "".join(kept_text).strip()

    def _run_local_llm_streaming(self, messages, stop_flag):
        # interrupt system
        text_parts = []
        try:
            stream = self.llm.create_chat_completion(
                messages=messages,
                max_tokens=256,
                temperature=0.7,
                top_p=0.9,
                frequency_penalty=0.1,
                presence_penalty=0.1,
                stream=True
            )
            for chunk in stream:
                if stop_flag.is_set():
                    break
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    text_parts.append(delta)
        except Exception as e:
            print(f"Local LLM execution failed: {e}")
        return "".join(text_parts).strip()

    async def generate_response(self, user_text, stop_flag=None):
        if stop_flag is None:
            stop_flag = threading.Event()

        self.history.append({"role": "user", "content": user_text})
        
        if len(self.history) > self.max_history_turns * 2 + 1:
            self.history = [self.history[0]] + self.history[-(self.max_history_turns * 2):]

        is_heavy_task = len(user_text.split()) > 25 or any(
            k in user_text.lower() for k in ["write code", "debug", "complex", "refactor", "analyze deep"]
        )

        reply = ""

        if not is_heavy_task:
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(
                None, self._run_local_llm_streaming, self.history, stop_flag
            )

        if stop_flag.is_set():
            # interrupt & Groq history logging
            self.history.pop()
            return ""

        if not reply and self.use_external_api:
            print("Offloading request to Groq for assistance...")
            try:
                groq_response = await self.groq_client.chat.completions.create(
                    model="openai/gpt-oss-20b",
                    messages=self.history,
                    max_tokens=256,
                    temperature=0.7
                )
                reply = groq_response.choices[0].message.content.strip()
            except Exception as e:
                print(f"Groq assist call failed: {e}")
                reply = "I had trouble processing that locally and couldn't reach the assistant model."

        if stop_flag.is_set():
            self.history.pop()
            return ""

        self.history.append({"role": "assistant", "content": reply})

        if self.use_external_api:
            # Groq bg memory task
            asyncio.create_task(self._update_memory_background(user_text, reply))

        return reply

    async def _update_memory_background(self, user_text, reply):
        try:
            new_facts = await self.extract_memory_autonomous(user_text, reply)
            if new_facts:
                self.long_term_memory["facts"].extend(new_facts)
                conversation_summary = {
                    "timestamp": datetime.now().isoformat(),
                    "user": user_text[:200],
                    "assistant": reply[:200],
                    "extracted_facts": new_facts
                }
                self.long_term_memory["conversation_history"].append(conversation_summary)
                self.save_memory()
            elif (datetime.now() - self.last_save_time).seconds > self.save_interval_seconds:
                self.save_memory()
        except Exception as e:
            print(f"Background memory update failed: {e}")

    def synthesize_speech(self, text, output_path="static/output.wav"):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.tts_voice.config.sample_rate)

            if hasattr(self.tts_voice, "synthesize_wav"):
                self.tts_voice.synthesize_wav(text, wav_file)
            else:
                self.tts_voice.synthesize(text, wav_file)

        return output_path