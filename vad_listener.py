import wave
import numpy as np
import onnxruntime as ort
import sounddevice as sd

class SileroVADListener:
    def __init__(self, model_path="models/silero_vad.onnx", sample_rate=16000, threshold=0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.window_size_samples = 512
        self.session = ort.InferenceSession(model_path)
        self.reset_state()

    def reset_state(self):
        self.state = np.zeros((2, 1, 64), dtype=np.float32)
        self.sr = np.array(self.sample_rate, dtype=np.int64)

    def is_speech(self, chunk: np.ndarray) -> float:
        tensor_input = np.expand_dims(chunk, axis=0).astype(np.float32)
        out, self.state = self.session.run(None, {'input': tensor_input, 'state': self.state, 'sr': self.sr})
        return out[0][0]

    def listen_until_speech_ends(self, output_file="temp_user_input.wav", silence_duration_sec=0.8):
        self.reset_state()
        pcm_buffer = []
        speech_detected = False
        silent_chunks = 0
        max_silent_chunks = int((silence_duration_sec * self.sample_rate) / self.window_size_samples)

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='float32') as stream:
            while True:
                chunk, _ = stream.read(self.window_size_samples)
                flat_chunk = chunk.flatten()
                prob = self.is_speech(flat_chunk)

                if prob >= self.threshold:
                    speech_detected = True
                    pcm_buffer.append(flat_chunk)
                    silent_chunks = 0
                elif speech_detected:
                    pcm_buffer.append(flat_chunk)
                    silent_chunks += 1
                    if silent_chunks >= max_silent_chunks:
                        break

        audio_data = (np.concatenate(pcm_buffer) * 32767).astype(np.int16)
        with wave.open(output_file, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())

        return output_file