from ai_engine import VoiceAIEngine

# Test without WebSocket
engine = VoiceAIEngine()

# Test text generation
async def test():
    response = await engine.generate_response("Hello, how are you?")
    print(f"Response: {response}")
    
    # Test TTS
    output = engine.synthesize_speech("Hello, I am your AI assistant")
    print(f"Audio saved to: {output}")

import asyncio
asyncio.run(test())