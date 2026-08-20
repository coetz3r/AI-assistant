// DOM Elements
const statusEl = document.getElementById('status');
const visualizer = document.getElementById('visualizer');
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

// Constants
const SAMPLE_RATE = 16000;
const AUDIO_BUFFER_SIZE = 4096;
const RMS_THRESHOLD = 0.02;

// Event Listeners
startBtn.addEventListener('click', startVoiceSession);
stopBtn.addEventListener('click', stopSession);

async function startVoiceSession() {
    try {
        // Initialize Audio Context
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)({ 
                sampleRate: SAMPLE_RATE 
            });
        }

        // Resume if suspended (mobile browser policy)
        if (audioCtx.state === 'suspended') {
            await audioCtx.resume();
        }

        // Get microphone access
        micStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                channelCount: 1,
                sampleRate: SAMPLE_RATE,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }, 
            video: false 
        });

        // Determine protocol (WSS for HTTPS, WS for HTTP)
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = protocol + '//' + location.host + '/ws';
        
        console.log('Connecting to: ' + wsUrl);
        ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';

        // WebSocket event handlers
        ws.onopen = function() {
            console.log('WebSocket connected');
            statusEl.textContent = 'Listening';
            statusEl.classList.add('connected');
            startBtn.disabled = true;
            stopBtn.disabled = false;
            isRecording = true;
            setupMicStreaming();
        };

        ws.onmessage = function(event) {
            if (event.data instanceof ArrayBuffer) {
                console.log('Received audio: ' + event.data.byteLength + ' bytes');
                playIncomingAudio(event.data);
            }
        };

        ws.onerror = function(error) {
            console.error('WebSocket error:', error);
            statusEl.textContent = 'Connection Error';
            stopSession();
        };

        ws.onclose = function(event) {
            console.log('WebSocket closed: ' + event.code + ' - ' + (event.reason || 'Normal closure'));
            if (event.code !== 1000 && event.code !== 1001) {
                statusEl.textContent = 'Disconnected - Tap Start to reconnect';
            }
            stopSession();
        };

    } catch (err) {
        console.error('Error starting voice session:', err);
        if (err.name === 'NotAllowedError') {
            alert('Microphone access denied. Please allow microphone permissions and try again.');
        } else if (err.name === 'NotFoundError') {
            alert('No microphone found. Please connect a microphone and try again.');
        } else {
            alert('Error: ' + (err.message || 'Unknown error occurred'));
        }
        stopSession();
    }
}

function setupMicStreaming() {
    if (!audioCtx || !micStream) return;

    var source = audioCtx.createMediaStreamSource(micStream);
    
    // Create audio processor for streaming
    audioProcessor = audioCtx.createScriptProcessor(AUDIO_BUFFER_SIZE, 1, 1);
    
    audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRecording) {
            return;
        }

        try {
            var inputData = e.inputBuffer.getChannelData(0);

            // Barge-In Logic: Calculate RMS volume level
            var sum = 0;
            for (var i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            var rms = Math.sqrt(sum / inputData.length);

            // Interrupt playback if voice threshold is exceeded
            if (rms > RMS_THRESHOLD && isAudioPlaying) {
                console.log('Barge-in detected - interrupting playback');
                stopPlayback();
            }

            // Convert Float32 to Int16 PCM
            var pcm16 = new Int16Array(inputData.length);
            for (var j = 0; j < inputData.length; j++) {
                // Clamp to [-1, 1] and convert to 16-bit
                var sample = Math.max(-1, Math.min(1, inputData[j]));
                pcm16[j] = sample * 0x7FFF;
            }
            
            ws.send(pcm16.buffer);
            
        } catch (err) {
            console.error('Error processing audio:', err);
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioCtx.destination);
}

function playIncomingAudio(arrayBuffer) {
    try {
        // Stop current playback if any
        stopPlayback();
        visualizer.classList.remove('idle');

        // Setup analyser for visualizer if not exists
        if (!playbackAnalyser) {
            playbackAnalyser = audioCtx.createAnalyser();
            playbackAnalyser.fftSize = 32;
            playbackAnalyser.smoothingTimeConstant = 0.8;
            playbackAnalyser.connect(audioCtx.destination);
            animateVisualizer();
        }

        // Decode and play audio
        audioCtx.decodeAudioData(arrayBuffer, function(audioBuffer) {
            currentAudioSource = audioCtx.createBufferSource();
            currentAudioSource.buffer = audioBuffer;
            currentAudioSource.connect(playbackAnalyser);
            currentAudioSource.start();
            isAudioPlaying = true;

            currentAudioSource.onended = function() {
                console.log('Audio playback finished');
                currentAudioSource = null;
                isAudioPlaying = false;
                visualizer.classList.add('idle');
            };

            console.log('Playing audio: ' + audioBuffer.duration.toFixed(2) + ' seconds');
        }, function(error) {
            console.error('Error decoding audio:', error);
        });

    } catch (err) {
        console.error('Error playing audio:', err);
        visualizer.classList.add('idle');
        isAudioPlaying = false;
    }
}

function stopPlayback() {
    if (currentAudioSource) {
        try {
            currentAudioSource.stop();
            currentAudioSource.disconnect();
        } catch (e) {
            // Ignore if already stopped
        }
        currentAudioSource = null;
        isAudioPlaying = false;
    }
    visualizer.classList.add('idle');
}

function animateVisualizer() {
    if (!playbackAnalyser) return;
    
    var dataArray = new Uint8Array(playbackAnalyser.frequencyBinCount);

    function draw() {
        if (!playbackAnalyser) return;
        
        requestAnimationFrame(draw);
        
        try {
            playbackAnalyser.getByteFrequencyData(dataArray);
            
            for (var i = 0; i < bars.length; i++) {
                var value = dataArray[i] || 0;
                // Scale height: 8px minimum, up to 110px maximum
                var barHeight = Math.max(8, (value / 255) * 110);
                bars[i].style.height = barHeight + 'px';
            }
        } catch (err) {
            // Silent fail for visualizer errors
        }
    }
    draw();
}

function stopSession() {
    console.log('Stopping session...');
    isRecording = false;
    
    // Stop playback
    stopPlayback();
    if (playbackAnalyser) {
        try {
            playbackAnalyser.disconnect();
        } catch (e) {}
        playbackAnalyser = null;
    }

    // Close WebSocket
    if (ws) {
        try {
            ws.onclose = null; // Prevent recursive calls
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                ws.close(1000, 'User stopped session');
            }
        } catch (e) {}
        ws = null;
    }

    // Stop microphone
    if (micStream) {
        try {
            micStream.getTracks().forEach(function(track) { track.stop(); });
        } catch (e) {}
        micStream = null;
    }

    // Disconnect audio processor
    if (audioProcessor) {
        try {
            audioProcessor.disconnect();
        } catch (e) {}
        audioProcessor = null;
    }

    // Close audio context
    if (audioCtx && audioCtx.state !== 'closed') {
        try {
            audioCtx.close();
        } catch (e) {}
        audioCtx = null;
    }

    // Update UI
    statusEl.textContent = 'Disconnected';
    statusEl.classList.remove('connected');
    startBtn.disabled = false;
    stopBtn.disabled = true;
    visualizer.classList.add('idle');

    console.log('Session stopped cleanly');
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
        if (!startBtn.disabled) {
            startBtn.click();
        } else if (!stopBtn.disabled) {
            stopBtn.click();
        }
    }
});

// Handle page visibility change (save resources when tab is hidden)
document.addEventListener('visibilitychange', function() {
    if (document.hidden && isRecording) {
        console.log('Tab hidden - reducing resource usage');
        // Stop recording but keep connection
        if (audioProcessor) {
            audioProcessor.disconnect();
            audioProcessor = null;
        }
    } else if (!document.hidden && isRecording) {
        console.log('Tab visible - resuming recording');
        if (micStream && audioCtx) {
            setupMicStreaming();
        }
    }
});

// Handle beforeunload - clean up
window.addEventListener('beforeunload', function() {
    stopSession();
});

console.log('Voice AI Assistant initialized');