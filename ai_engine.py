import os
import wave
import asyncio
from llama_cpp import Llama
from faster_whisper import WhisperModel
from piper.voice import PiperVoice

class VoiceAIEngine:
    def __init__(
        self, 
        llm_model_path="models/llama-3.2-1b-instruct.Q4_K_M.gguf",
        tts_model_path="models/en_US-lessac-medium.onnx"
    ):
        # 1. Initialize LLM Engine (Cap threads to prevent CPU strain)
        self.llm = Llama(
            model_path=llm_model_path,
            n_ctx=2048,
            n_threads=4,
            verbose=False
        )
        
        # 2. Initialize STT (8-bit quantization for lightweight CPU inference)
        self.stt = WhisperModel("base.en", device="cpu", compute_type="int8")
        
        # 3. Initialize Piper TTS
        self.tts_voice = PiperVoice.load(tts_model_path)
        
        # 4. Conversation History (System Prompt + Memory Buffer)
        self.history = [
            {"role": "system", "content": "You are a concise, helpful voice assistant. Keep answers brief and conversational."}
        ]

    def transcribe(self, audio_file_path):
        """Converts user speech to text."""
        segments, _ = self.stt.transcribe(audio_file_path)
        return "".join([segment.text for segment in segments]).strip()

    async def generate_response(self, user_text):
        """Generates response text while retaining a sliding memory buffer."""
        self.history.append({"role": "user", "content": user_text})
    
        if len(self.history) > 11:
            self.history = [self.history[0]] + self.history[-10:]

        # Run blocking Llama inference in a separate thread
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
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with wave.open(output_path, "wb") as wav_file:
            self.tts_voice.synthesize(text, wav_file)
        return output_path