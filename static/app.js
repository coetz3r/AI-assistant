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

const MAX_SILENCE_FRAMES = 7;      // ~900ms of trailing silence (was ~2.56s)
const MIN_SPEECH_FRAMES = 10;
const MIN_RMS_THRESHOLD = 0.018;   // floor so a silent room doesn't trigger on a whisper of noise
const NOISE_FLOOR_MULTIPLIER = 3;  // speech must exceed ambient noise by this factor
let noiseFloor = 0.01;             // running estimate of ambient noise, updated while not speaking

// Speech onset confirmation: a single loud frame (a tap, a cough, a door)
// crosses the RMS threshold just as easily as real speech does. Instead of
// trusting the first loud frame, we hold it in a small pre-roll buffer and
// only commit to "the user is speaking" once several consecutive frames
// stay loud. The pre-roll is kept and prepended once confirmed, so the
// first syllables aren't lost while we wait to be sure.
const SPEECH_ONSET_FRAMES = 3;             // ~384ms of sustained level before we trust it's real speech starting
const CONTINUATION_THRESHOLD_MULTIPLIER = 0.6;  // once speech has been confirmed, allow the level to dip lower before it counts as silence
let onsetFrames = 0;
let onsetBuffer = [];

// Barge-in: interrupting the AI mid-reply
const BARGE_IN_RMS_MULTIPLIER = 4.5;  // stricter than normal speech threshold — avoid tripping on residual TTS bleed through the mic
const BARGE_IN_CONFIRM_FRAMES = 4;    // ~512ms of sustained loud speech before we trust it as a real interruption
let bargeInFrames = 0;

// Turn tracking so a stale reply (superseded by a barge-in) never gets played
let turnId = 0;
let pendingResponseTurnId = null;

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
            onsetFrames = 0;
            onsetBuffer = [];
            turnId = 0;
            pendingResponseTurnId = null;
            bargeInFrames = 0;

            ws.send(JSON.stringify({ type: 'init', sampleRate: audioCtx.sampleRate }));
            setupMicStreaming();
        };

        ws.onmessage = function(event) {
            if (event.data instanceof ArrayBuffer) {
                isProcessing = false;

                // If a newer turn has started since this response was requested
                // (i.e. the user interrupted), the response is stale — drop it.
                if (pendingResponseTurnId !== null && pendingResponseTurnId !== turnId) {
                    console.log('Discarding stale response for turn', pendingResponseTurnId);
                    pendingResponseTurnId = null;
                    return;
                }
                pendingResponseTurnId = null;
                playIncomingAudio(event.data);
                return;
            }

            try {
                var data = JSON.parse(event.data);

                if (data.type === 'response_turn') {
                    // Meta message that always precedes the binary payload
                    // for this turn — just remember which turn it's for.
                    pendingResponseTurnId = data.turnId;
                    return;
                }

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
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRecording) {
            return;
        }

        var inputData = e.inputBuffer.getChannelData(0);
        var sum = 0;
        for (var i = 0; i < inputData.length; i++) {
            sum += inputData[i] * inputData[i];
        }
        var rms = Math.sqrt(sum / inputData.length);

        if (isAudioPlaying) {
            // The AI is talking — only listen for a loud, sustained
            // interruption. Playback visualizer handles the bars here.
            var bargeThreshold = Math.max(MIN_RMS_THRESHOLD, noiseFloor * NOISE_FLOOR_MULTIPLIER) * BARGE_IN_RMS_MULTIPLIER;
            if (rms > bargeThreshold) {
                bargeInFrames++;
                if (bargeInFrames >= BARGE_IN_CONFIRM_FRAMES) {
                    handleBargeIn();
                }
            } else {
                bargeInFrames = 0;
            }
            return;
        }

        if (isProcessing) {
            return;
        }

        // Live mic visualizer drive
        micAnalyser.getByteFrequencyData(dataArray);
        for (var i = 0; i < bars.length; i++) {
            var value = dataArray[i] || 0;
            var barHeight = Math.max(8, (value / 255) * 110);
            bars[i].style.height = barHeight + 'px';
        }

        var effectiveThreshold = Math.max(MIN_RMS_THRESHOLD, noiseFloor * NOISE_FLOOR_MULTIPLIER);
        var continuationThreshold = effectiveThreshold * CONTINUATION_THRESHOLD_MULTIPLIER;

        var pcm16 = new Int16Array(inputData.length);
        for (var j = 0; j < inputData.length; j++) {
            var sample = Math.max(-1, Math.min(1, inputData[j]));
            pcm16[j] = sample * 0x7FFF;
        }

        if (isSpeaking) {
            // Already confirmed as real speech - use the lower continuation
            // threshold (hysteresis) so a quieter word or a brief natural
            // pause mid-sentence doesn't get counted as trailing silence.
            if (rms > continuationThreshold) {
                silenceFrames = 0;
            } else {
                silenceFrames++;
            }
            audioBuffer.push(pcm16);

            if (silenceFrames > MAX_SILENCE_FRAMES && audioBuffer.length > MIN_SPEECH_FRAMES) {
                updateStatus('Thinking', 'Processing request...', 'thinking');
                sendAudioBuffer(audioBuffer);
                audioBuffer = [];
                isSpeaking = false;
                silenceFrames = 0;
                onsetFrames = 0;
                onsetBuffer = [];
            }
        } else if (rms > effectiveThreshold) {
            // Loud enough to maybe be speech - but don't commit on a single
            // frame, since short transient noises (taps, coughs, a door)
            // cross this threshold just as easily as a real word starting.
            // Hold it in the pre-roll buffer until we've seen it sustain.
            onsetFrames++;
            onsetBuffer.push(pcm16);

            if (onsetFrames >= SPEECH_ONSET_FRAMES) {
                isSpeaking = true;
                updateStatus('Listening', 'Recording...', 'listening');
                silenceFrames = 0;
                audioBuffer = onsetBuffer;   // keep the pre-roll so the first syllables aren't clipped
                onsetBuffer = [];
                onsetFrames = 0;
            }
        } else {
            // Quiet frame while not speaking: either a noise blip that
            // didn't sustain long enough to confirm (drop the pre-roll) or
            // genuine silence, in which case track it as the ambient noise
            // floor so the threshold adapts to the room.
            if (onsetFrames > 0) {
                onsetFrames = 0;
                onsetBuffer = [];
            }
            noiseFloor = noiseFloor * 0.95 + rms * 0.05;
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioCtx.destination);
}

function handleBargeIn() {
    console.log('Barge-in detected — interrupting AI');
    bargeInFrames = 0;

    stopPlayback();
    isAudioPlaying = false;
    isProcessing = false;
    pendingResponseTurnId = null;

    if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'interrupt' })); } catch (e) {}
    }

    isSpeaking = false;
    silenceFrames = 0;
    audioBuffer = [];

    updateStatus('Listening', 'Go ahead...', 'listening');
}

function sendAudioBuffer(buffers) {
    if (!ws || ws.readyState !== WebSocket.OPEN || isProcessing) return;
    
    try {
        isProcessing = true;
        turnId++;
        ws.send(JSON.stringify({ type: 'turn_start', turnId: turnId }));

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
    onsetFrames = 0;
    onsetBuffer = [];
    pendingResponseTurnId = null;
    bargeInFrames = 0;

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