import os
import wave
import asyncio
import json
from datetime import datetime
from collections import deque
from llama_cpp import Llama
from faster_whisper import WhisperModel
from piper import PiperVoice


class VoiceAIEngine:
    def __init__(
        self,
        llm_model_path="models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        tts_model_path="models/en_US-lessac-medium.onnx",
        memory_file="conversation_memory.json",
        max_memory_items=1000,  # Increased memory limit
        max_history_turns=50    # Keep more conversation history
    ):
        # 1. Initialize LLM Engine
        self.llm = Llama(
            model_path=llm_model_path,
            n_ctx=2048,  # Increased context for longer memory
            n_threads=4,  # Better performance
            n_gpu_layers=0,
            n_batch=128,
            verbose=False
        )

        # 2. Initialize STT
        self.stt = WhisperModel("base.en", device="cpu", compute_type="int8")

        # 3. Initialize Piper TTS
        self.tts_voice = PiperVoice.load(tts_model_path)

        # 4. Memory Configuration
        self.max_memory_items = max_memory_items
        self.max_history_turns = max_history_turns
        
        # 5. Load or initialize memory
        self.memory_file = memory_file
        self.long_term_memory = self.load_memory()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 6. Initialize conversation history
        self.history = self._build_conversation_history()
        
        # 7. Track conversation for memory extraction
        self.conversation_buffer = []
        self.last_save_time = datetime.now()
        self.save_interval_seconds = 30  # Auto-save every 30 seconds

    def load_memory(self):
        """Load long-term memory from file with better structure"""
        default_memory = {
            "user_profile": {
                "name": None,
                "age": None,
                "location": None,
                "occupation": None,
                "interests": [],
                "preferences": {},
                "memorable_dates": []
            },
            "conversation_history": [],  # Full conversation summaries
            "facts": [],  # Individual facts with metadata
            "preferences": [],  # User preferences
            "topics_discussed": [],  # Track topics
            "user_queries": [],  # Track what user asks about
            "session_history": []  # Track sessions
        }
        
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle missing keys
                    for key in default_memory:
                        if key not in loaded:
                            loaded[key] = default_memory[key]
                    return loaded
            except Exception as e:
                print(f"Error loading memory: {e}")
                return default_memory
        return default_memory

    def save_memory(self):
        """Save long-term memory to file with timestamp"""
        try:
            # Add metadata
            self.long_term_memory["last_updated"] = datetime.now().isoformat()
            self.long_term_memory["total_conversations"] = len(self.long_term_memory["conversation_history"])
            self.long_term_memory["total_facts"] = len(self.long_term_memory["facts"])
            
            # Limit memory size
            if len(self.long_term_memory["facts"]) > self.max_memory_items:
                self.long_term_memory["facts"] = self.long_term_memory["facts"][-self.max_memory_items:]
            
            if len(self.long_term_memory["conversation_history"]) > self.max_memory_items:
                self.long_term_memory["conversation_history"] = self.long_term_memory["conversation_history"][-self.max_memory_items:]
            
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.long_term_memory, f, indent=2, ensure_ascii=False)
            
            self.last_save_time = datetime.now()
            print(f"💾 Memory saved to {self.memory_file}")
            
        except Exception as e:
            print(f"Error saving memory: {e}")

    def _build_conversation_history(self):
        """Build initial conversation history with memory injection"""
        # Start with system prompt
        system_content = (
            "You are a concise, helpful voice assistant. "
            "Keep answers brief and conversational. "
            "Remember information about the user and use it to personalize responses."
        )
        
        # Inject user profile
        profile = self.long_term_memory.get("user_profile", {})
        if any(profile.values()):
            profile_lines = ["\nUser Information:"]
            for key, value in profile.items():
                if value:
                    if isinstance(value, list) and value:
                        profile_lines.append(f"- {key}: {', '.join(value)}")
                    elif isinstance(value, dict) and value:
                        prefs = ", ".join([f"{k}: {v}" for k, v in value.items()])
                        profile_lines.append(f"- {key}: {prefs}")
                    elif value:
                        profile_lines.append(f"- {key}: {value}")
            system_content += "\n" + "\n".join(profile_lines)
        
        # Inject recent facts and preferences (with context window)
        facts = self.long_term_memory.get("facts", [])[-30:]  # Last 30 facts
        if facts:
            system_content += "\n\nRecent facts about the user:"
            for fact in facts[-10:]:  # Last 10 for prompt efficiency
                if isinstance(fact, dict):
                    detail = fact.get("detail", "")
                    category = fact.get("category", "info")
                    system_content += f"\n- {category}: {detail}"
        
        preferences = self.long_term_memory.get("preferences", [])[-20:]
        if preferences:
            system_content += "\n\nUser preferences:"
            for pref in preferences[-10:]:
                if isinstance(pref, dict):
                    detail = pref.get("detail", "")
                    system_content += f"\n- {detail}"
        
        return [
            {"role": "system", "content": system_content}
        ]

    def extract_memory(self, user_text, assistant_response):
        """Enhanced memory extraction with more patterns and context"""
        memory_items = []
        combined_text = f"{user_text} {assistant_response}".lower()
        
        # More comprehensive patterns
        patterns = {
            "name": ["my name is", "i'm", "i am", "call me", "you can call me", "everyone calls me"],
            "age": ["i am", "years old", "age", "turning", "born in", "birthday"],
            "location": ["from", "live in", "located in", "reside in", "based in", "city", "country"],
            "occupation": ["work as", "job is", "i'm a", "i am a", "employed as", "career", "profession"],
            "interest": ["like", "enjoy", "love", "hobby", "interested in", "passion", "favorite", "prefer"],
            "dislike": ["don't like", "hate", "dislike", "can't stand", "not a fan of"],
            "skill": ["can", "able to", "good at", "skilled at", "expert in", "knowledgeable about"],
            "goal": ["want to", "planning to", "hoping to", "trying to", "goal is", "aim to"],
            "family": ["my", "mother", "father", "sister", "brother", "wife", "husband", "child", "parent"],
            "education": ["study", "learn", "school", "university", "college", "degree", "major in"],
            "travel": ["travel", "visit", "went to", "been to", "planning a trip", "vacation"],
            "food": ["eat", "food", "cook", "cuisine", "meal", "dinner", "lunch", "breakfast"],
            "health": ["health", "exercise", "gym", "workout", "diet", "sleep", "energy"],
            "technology": ["computer", "phone", "app", "software", "hardware", "tech", "digital"]
        }
        
        for category, pattern_list in patterns.items():
            for pattern in pattern_list:
                if pattern in combined_text:
                    # Find the most relevant piece of information
                    start = max(0, combined_text.find(pattern) + len(pattern))
                    end = min(start + 100, len(combined_text))
                    detail = combined_text[start:end].strip(" .,!?\n")
                    
                    # Clean up and validate
                    if len(detail) > 3 and len(detail) < 150:
                        # Try to extract just the key information
                        detail = ' '.join(detail.split()[:10])  # First 10 words
                        memory_items.append({
                            "category": category,
                            "detail": detail,
                            "timestamp": datetime.now().isoformat(),
                            "source": user_text[:50]  # Context snippet
                        })
                    break
        
        # Check for duplicate facts (simple dedup)
        existing_facts = [f.get("detail", "").lower() for f in self.long_term_memory["facts"][-50:]]
        unique_items = []
        for item in memory_items:
            if item["detail"].lower() not in existing_facts:
                unique_items.append(item)
                existing_facts.append(item["detail"].lower())
        
        return unique_items

    def update_user_profile(self, fact_items):
        """Update structured user profile from extracted facts"""
        profile = self.long_term_memory["user_profile"]
        
        for item in fact_items:
            category = item.get("category")
            detail = item.get("detail", "")
            
            if category == "name" and not profile["name"]:
                # Extract just the name (remove extra words)
                name_parts = detail.split()
                if name_parts:
                    profile["name"] = name_parts[0].title()
                    
            elif category == "age" and not profile["age"]:
                # Try to find a number
                import re
                age_match = re.search(r'\d+', detail)
                if age_match:
                    profile["age"] = age_match.group()
                    
            elif category == "location" and not profile["location"]:
                # Extract location (first few words)
                location_parts = detail.split()[:3]
                profile["location"] = " ".join(location_parts).title()
                
            elif category == "occupation" and not profile["occupation"]:
                # Extract occupation
                occ_parts = detail.split()[:3]
                profile["occupation"] = " ".join(occ_parts).title()
                
            elif category == "interest":
                if detail not in profile["interests"]:
                    profile["interests"].append(detail.title())
                    
            elif category == "preference":
                if detail not in profile["preferences"]:
                    if "preference" not in profile["preferences"]:
                        profile["preferences"]["general"] = []
                    if detail not in profile["preferences"]["general"]:
                        profile["preferences"]["general"].append(detail)
        
        # Limit interests/preferences
        if len(profile["interests"]) > 20:
            profile["interests"] = profile["interests"][-20:]
        if "general" in profile["preferences"] and len(profile["preferences"]["general"]) > 20:
            profile["preferences"]["general"] = profile["preferences"]["general"][-20:]

    def transcribe(self, audio_file_path):
        """Converts user speech to text."""
        segments, _ = self.stt.transcribe(audio_file_path)
        return "".join(segment.text for segment in segments).strip()

    async def generate_response(self, user_text):
        """Generates response text while retaining and updating memory."""
        # Add user message to history
        self.history.append({"role": "user", "content": user_text})
        
        # Keep conversation history manageable
        if len(self.history) > self.max_history_turns * 2 + 1:  # +1 for system prompt
            # Keep system prompt and recent history
            self.history = [self.history[0]] + self.history[-(self.max_history_turns * 2):]

        # Generate response
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.llm.create_chat_completion(
                messages=self.history,
                max_tokens=512,  # Increased for better responses
                temperature=0.7,
                top_p=0.9,
                frequency_penalty=0.1,
                presence_penalty=0.1
            )
        )

        reply = response["choices"][0]["message"]["content"]
        self.history.append({"role": "assistant", "content": reply})
        
        # Extract and store memory from this exchange
        new_memory = self.extract_memory(user_text, reply)
        
        if new_memory:
            # Store facts
            self.long_term_memory["facts"].extend(new_memory)
            
            # Update user profile
            self.update_user_profile(new_memory)
            
            # Store conversation summary
            conversation_summary = {
                "timestamp": datetime.now().isoformat(),
                "user": user_text[:200],
                "assistant": reply[:200],
                "extracted_facts": len(new_memory)
            }
            self.long_term_memory["conversation_history"].append(conversation_summary)
            
            # Track topics
            for item in new_memory:
                topic = item.get("category")
                if topic and topic not in self.long_term_memory["topics_discussed"]:
                    self.long_term_memory["topics_discussed"].append(topic)
            
            # Save memory after each significant exchange
            self.save_memory()
        
        # Auto-save periodically even if no new facts
        elif (datetime.now() - self.last_save_time).seconds > self.save_interval_seconds:
            self.save_memory()
        
        return reply

    def synthesize_speech(self, text, output_path="static/output.wav"):
        """Converts text response to audio file via Piper."""
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

    def clear_memory(self):
        """Clear all stored memory"""
        self.long_term_memory = {
            "user_profile": {
                "name": None,
                "age": None,
                "location": None,
                "occupation": None,
                "interests": [],
                "preferences": {},
                "memorable_dates": []
            },
            "conversation_history": [],
            "facts": [],
            "preferences": [],
            "topics_discussed": [],
            "user_queries": [],
            "session_history": []
        }
        self.save_memory()
        # Reset history but keep the base system prompt
        self.history = [
            {
                "role": "system",
                "content": (
                    "You are a concise, helpful voice assistant. "
                    "Keep answers brief and conversational. "
                    "Remember information about the user and use it to personalize responses."
                )
            }
        ]
        print("🧹 Memory cleared successfully")

    def get_memory_stats(self):
        """Get statistics about the current memory"""
        return {
            "total_facts": len(self.long_term_memory["facts"]),
            "total_conversations": len(self.long_term_memory["conversation_history"]),
            "topics_discussed": len(self.long_term_memory["topics_discussed"]),
            "user_profile": self.long_term_memory["user_profile"],
            "last_updated": self.long_term_memory.get("last_updated", "Never"),
            "facts_sample": self.long_term_memory["facts"][-5:] if self.long_term_memory["facts"] else []
        }