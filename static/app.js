// DOM Elements
const statusEl = document.getElementById('status');
const statusSubEl = document.getElementById('status-sub');
const visualizerCard = document.getElementById('visualizer');
const badgeEl = document.getElementById('status-badge');
const bars = document.querySelectorAll('.bar');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');

// State
let ws = null;
let audioCtx = null;
let micStream = null;
let audioProcessor = null;
let playbackAnalyser = null;
let currentAudioSource = null;
let isAudioPlaying = false;
let isRecording = false;
let isProcessing = false;
let audioBuffer = [];
let isSpeaking = false;
let silenceFrames = 0;

const MAX_SILENCE_FRAMES = 20;
const MIN_SPEECH_FRAMES = 10;
const RMS_THRESHOLD = 0.035;

// Event Listeners
startBtn.addEventListener('click', startVoiceSession);
stopBtn.addEventListener('click', stopSession);

function updateStatus(mainStatus, subStatus, className) {
    if (statusEl) statusEl.textContent = mainStatus;
    if (statusSubEl) statusSubEl.textContent = subStatus || '';

    if (badgeEl) {
        badgeEl.className = 'status-badge';
        if (className) badgeEl.classList.add(className);
    }

    if (visualizerCard) {
        visualizerCard.className = 'visualizer-card';
        if (className && (className === 'listening' || className === 'talking')) {
            visualizerCard.classList.add(className);
        }
    }
}

async function startVoiceSession() {
    try {
        updateStatus('Initializing...', 'Starting microphone', 'disconnected');

        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        }

        if (audioCtx.state === 'suspended') {
            await audioCtx.resume();
        }

        const constraints = {
            audio: {
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                latency: 0.01
            },
            video: false
        };

        micStream = await navigator.mediaDevices.getUserMedia(constraints);

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = protocol + '//' + location.host + '/ws';

        ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';

        ws.onopen = function() {
            updateStatus('Listening', 'Say something...', 'listening');
            startBtn.disabled = true;
            stopBtn.disabled = false;
            isRecording = true;
            isProcessing = false;
            isSpeaking = false;
            audioBuffer = [];
            silenceFrames = 0;

            ws.send(JSON.stringify({ type: 'init', sampleRate: audioCtx.sampleRate }));
            setupMicStreaming();
        };

        ws.onmessage = function(event) {
            if (event.data instanceof ArrayBuffer) {
                isProcessing = false;
                playIncomingAudio(event.data);
                return;
            }

            try {
                var data = JSON.parse(event.data);
                isProcessing = false;

                if (data.type === 'no_speech') {
                    if (isRecording) {
                        updateStatus('Listening', "Didn't catch that, try again", 'listening');
                    }
                } else if (data.type === 'error') {
                    if (isRecording) {
                        updateStatus('Listening', data.message || 'Something went wrong', 'listening');
                    }
                }
            } catch (e) {
                console.warn('Unrecognized server message:', event.data);
            }
        };

        ws.onerror = function(error) {
            console.error('WebSocket error:', error);
            updateStatus('Connection Error', 'Check console', 'disconnected');
            stopSession();
        };

        ws.onclose = function() {
            stopSession();
        };

    } catch (err) {
        console.error('Error starting session:', err);
        stopSession();
    }
}

function setupMicStreaming() {
    if (!audioCtx || !micStream) return;

    var source = audioCtx.createMediaStreamSource(micStream);
    audioProcessor = audioCtx.createScriptProcessor(2048, 1, 1);

    var micAnalyser = audioCtx.createAnalyser();
    micAnalyser.fftSize = 32;
    source.connect(micAnalyser);

    var dataArray = new Uint8Array(micAnalyser.frequencyBinCount);

    audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRecording || isProcessing || isAudioPlaying) {
            return;
        }

        // Live mic visualizer drive
        micAnalyser.getByteFrequencyData(dataArray);
        for (var i = 0; i < bars.length; i++) {
            var value = dataArray[i] || 0;
            var barHeight = Math.max(8, (value / 255) * 110);
            bars[i].style.height = barHeight + 'px';
        }

        var inputData = e.inputBuffer.getChannelData(0);
        var sum = 0;
        for (var i = 0; i < inputData.length; i++) {
            sum += inputData[i] * inputData[i];
        }
        var rms = Math.sqrt(sum / inputData.length);

        var pcm16 = new Int16Array(inputData.length);
        for (var j = 0; j < inputData.length; j++) {
            var sample = Math.max(-1, Math.min(1, inputData[j]));
            pcm16[j] = sample * 0x7FFF;
        }

        if (rms > RMS_THRESHOLD) {
            if (!isSpeaking) {
                isSpeaking = true;
                updateStatus('Listening', 'Recording...', 'listening');
            }
            silenceFrames = 0;
            audioBuffer.push(pcm16);
        } else if (isSpeaking) {
            silenceFrames++;
            audioBuffer.push(pcm16);
            
            if (silenceFrames > MAX_SILENCE_FRAMES && audioBuffer.length > MIN_SPEECH_FRAMES) {
                updateStatus('Thinking', 'Processing request...', 'thinking');
                sendAudioBuffer(audioBuffer);
                audioBuffer = [];
                isSpeaking = false;
                silenceFrames = 0;
            }
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioCtx.destination);
}

function sendAudioBuffer(buffers) {
    if (!ws || ws.readyState !== WebSocket.OPEN || isProcessing) return;
    
    try {
        isProcessing = true;
        var totalLength = 0;
        for (var i = 0; i < buffers.length; i++) {
            totalLength += buffers[i].length;
        }
        
        var combined = new Int16Array(totalLength);
        var offset = 0;
        for (var i = 0; i < buffers.length; i++) {
            combined.set(buffers[i], offset);
            offset += buffers[i].length;
        }
        
        ws.send(combined.buffer);
    } catch (err) {
        console.error('Error sending audio payload:', err);
        isProcessing = false;
    }
}

function animateVisualizer() {
    if (!playbackAnalyser) return;
    var dataArray = new Uint8Array(playbackAnalyser.frequencyBinCount);

    function draw() {
        if (!playbackAnalyser || !isAudioPlaying) {
            for (var i = 0; i < bars.length; i++) {
                bars[i].style.height = '12px';
            }
            return;
        }

        requestAnimationFrame(draw);

        try {
            playbackAnalyser.getByteFrequencyData(dataArray);
            for (var i = 0; i < bars.length; i++) {
                var value = dataArray[i] || 0;
                var barHeight = Math.max(8, (value / 255) * 110);
                bars[i].style.height = barHeight + 'px';
            }
        } catch (err) {
            console.error('Visualizer render error:', err);
        }
    }
    draw();
}

function playIncomingAudio(arrayBuffer) {
    try {
        stopPlayback();

        isSpeaking = false;
        silenceFrames = 0;
        audioBuffer = [];

        isAudioPlaying = true;
        isProcessing = false;
        updateStatus('Talking', 'AI is responding...', 'talking');

        if (!playbackAnalyser) {
            playbackAnalyser = audioCtx.createAnalyser();
            playbackAnalyser.fftSize = 32;
            playbackAnalyser.smoothingTimeConstant = 0.8;
            playbackAnalyser.connect(audioCtx.destination);
        }

        animateVisualizer();

        audioCtx.decodeAudioData(arrayBuffer, function(decodedAudio) {
            currentAudioSource = audioCtx.createBufferSource();
            currentAudioSource.buffer = decodedAudio;
            currentAudioSource.connect(playbackAnalyser);
            currentAudioSource.start();

            currentAudioSource.onended = function() {
                currentAudioSource = null;
                isAudioPlaying = false;
                isProcessing = false;
                
                if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
                    updateStatus('Listening', 'Say something...', 'listening');
                } else {
                    updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');
                }
            };
        }, function(error) {
            console.error('Error decoding audio:', error);
            resetAudioState();
        });

    } catch (err) {
        console.error('Error playing audio:', err);
        resetAudioState();
    }
}

function resetAudioState() {
    isAudioPlaying = false;
    isProcessing = false;
    if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
        updateStatus('Listening', 'Say something...', 'listening');
    } else {
        updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');
    }
}

function stopPlayback() {
    if (currentAudioSource) {
        try {
            currentAudioSource.stop();
            currentAudioSource.disconnect();
        } catch (e) {}
        currentAudioSource = null;
        isAudioPlaying = false;
    }
}

function stopSession() {
    isRecording = false;
    isProcessing = false;
    isSpeaking = false;
    audioBuffer = [];
    silenceFrames = 0;

    updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');

    stopPlayback();
    if (playbackAnalyser) {
        try { playbackAnalyser.disconnect(); } catch (e) {}
        playbackAnalyser = null;
    }

    if (ws) {
        try {
            ws.onclose = null;
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                ws.close(1000, 'User stopped session');
            }
        } catch (e) {}
        ws = null;
    }

    if (micStream) {
        try { micStream.getTracks().forEach(track => track.stop()); } catch (e) {}
        micStream = null;
    }

    if (audioProcessor) {
        try { audioProcessor.disconnect(); } catch (e) {}
        audioProcessor = null;
    }

    if (audioCtx && audioCtx.state !== 'closed') {
        try { audioCtx.close(); } catch (e) {}
        audioCtx = null;
    }

    startBtn.disabled = false;
    stopBtn.disabled = true;
}