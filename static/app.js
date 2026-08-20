const statusEl = document.getElementById('status');
const visualizer = document.getElementById('visualizer');
const bars = document.querySelectorAll('.bar');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');

let ws = null;
let audioCtx = null;
let micStream = null;
let audioProcessor = null;

// Audio Playback & Barge-In State
let playbackAnalyser = null;
let currentAudioSource = null;

startBtn.addEventListener('click', async () => {
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        }

        // Resume audio context if suspended by mobile browser power policy
        if (audioCtx.state === 'suspended') {
            await audioCtx.resume();
        }

        micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${location.host}/ws`);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            statusEl.textContent = 'Listening / Connected';
            statusEl.classList.add('connected');
            startBtn.disabled = true;
            stopBtn.disabled = false;

            setupMicStreaming();
        };

        ws.onmessage = async (event) => {
            if (event.data instanceof ArrayBuffer) {
                playIncomingAudio(event.data);
            }
        };

        ws.onclose = () => {
            stopSession();
        };

    } catch (err) {
        console.error('Error starting voice stream:', err);
        alert('Microphone access or WebSocket connection failed.');
    }
});

stopBtn.addEventListener('click', () => {
    stopSession();
});

function setupMicStreaming() {
    const source = audioCtx.createMediaStreamSource(micStream);
    audioProcessor = audioCtx.createScriptProcessor(4096, 1, 1);

    audioProcessor.onaudioprocess = (e) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            const inputData = e.inputBuffer.getChannelData(0);

            // Barge-In Logic: Calculate Root Mean Square (RMS) volume level
            let sum = 0;
            for (let i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            const rms = Math.sqrt(sum / inputData.length);

            // Interrupt playback immediately if voice threshold is exceeded (> 0.02)
            if (rms > 0.02 && currentAudioSource) {
                stopPlayback();
            }

            // Convert Float32 to Int16 PCM array buffer for WebSocket transmission
            const pcm16 = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                pcm16[i] = Math.max(-1, Math.min(1, inputData[i])) * 0x7FFF;
            }
            ws.send(pcm16.buffer);
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioCtx.destination);
}

function stopPlayback() {
    if (currentAudioSource) {
        try {
            currentAudioSource.stop();
            currentAudioSource.disconnect();
        } catch (e) {
            // Handled if already stopped
        }
        currentAudioSource = null;
    }
    visualizer.classList.add('idle');
}

function playIncomingAudio(arrayBuffer) {
    stopPlayback();
    visualizer.classList.remove('idle');

    if (!playbackAnalyser) {
        playbackAnalyser = audioCtx.createAnalyser();
        playbackAnalyser.fftSize = 32;
        playbackAnalyser.connect(audioCtx.destination);
        animateVisualizer();
    }

    audioCtx.decodeAudioData(arrayBuffer, (buffer) => {
        currentAudioSource = audioCtx.createBufferSource();
        currentAudioSource.buffer = buffer;
        currentAudioSource.connect(playbackAnalyser);
        currentAudioSource.start();

        currentAudioSource.onended = () => {
            currentAudioSource = null;
            visualizer.classList.add('idle');
        };
    });
}

function animateVisualizer() {
    if (!playbackAnalyser) return;
    const dataArray = new Uint8Array(playbackAnalyser.frequencyBinCount);

    function draw() {
        requestAnimationFrame(draw);
        playbackAnalyser.getByteFrequencyData(dataArray);

        bars.forEach((bar, index) => {
            const value = dataArray[index] || 0;
            const barHeight = Math.max(8, (value / 255) * 110);
            bar.style.height = `${barHeight}px`;
        });
    }
    draw();
}

function stopSession() {
    stopPlayback();

    // Prevent recursive loop when manually closing WebSocket
    if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
    }

    if (micStream) {
        micStream.getTracks().forEach(track => track.stop());
        micStream = null;
    }

    // Check context status before closing to prevent DOMExceptions
    if (audioCtx && audioCtx.state !== 'closed') {
        audioCtx.close();
        audioCtx = null;
    }

    statusEl.textContent = 'Disconnected';
    statusEl.classList.remove('connected');
    startBtn.disabled = false;
    stopBtn.disabled = true;
}