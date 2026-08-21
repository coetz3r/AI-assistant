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
let isProcessing = false; // Track if we're waiting for a response

// Constants
const SAMPLE_RATE = 16000;
const AUDIO_BUFFER_SIZE = 4096;
const SILENCE_THRESHOLD = 0.005; // Lower threshold for better voice detection
const MIN_SPEECH_DURATION = 300; // Minimum ms of speech before sending
const MAX_SILENCE_DURATION = 1500; // Max ms of silence before ending utterance

// Voice Activity Detection State
let vadState = {
    isSpeaking: false,
    speechStartTime: 0,
    silenceStartTime: 0,
    audioBuffer: [],
    isListening: false
};

// Mobile audio context flag
let audioContextStarted = false;

// Event Listeners
startBtn.addEventListener('click', startVoiceSession);
stopBtn.addEventListener('click', stopSession);

// Add touch event for mobile audio context resume
document.addEventListener('touchstart', function resumeAudio() {
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().then(() => {
            console.log('Audio context resumed via touch');
        }).catch(err => {
            console.warn('Failed to resume audio context:', err);
        });
    }
}, { once: false });

async function startVoiceSession() {
    try {
        // Initialize Audio Context
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)({ 
                sampleRate: SAMPLE_RATE 
            });
        }

        // CRITICAL: Mobile browsers need user gesture to start audio
        if (audioCtx.state === 'suspended') {
            await audioCtx.resume();
        }

        console.log('Audio context state:', audioCtx.state);

        // Get microphone access with clean constraints
        const constraints = {
            audio: {
                channelCount: 1,
                sampleRate: SAMPLE_RATE,
                echoCancellation: false, // Disabled for cleaner capture
                noiseSuppression: false, // Disabled for cleaner capture
                autoGainControl: false,  // Disabled for cleaner capture
                latency: 0.01
            },
            video: false
        };

        micStream = await navigator.mediaDevices.getUserMedia(constraints);

        // Log microphone info for debugging
        const audioTrack = micStream.getAudioTracks()[0];
        console.log('Microphone track:', {
            label: audioTrack.label,
            enabled: audioTrack.enabled,
            settings: audioTrack.getSettings()
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
            isProcessing = false;
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
        
        // Enhanced error handling for mobile
        if (err.name === 'NotAllowedError') {
            alert('Microphone access denied. Please allow microphone permissions in your browser settings and try again.');
        } else if (err.name === 'NotFoundError') {
            alert('No microphone found. Please connect a microphone and try again.');
        } else if (err.name === 'NotSupportedError') {
            alert('Your browser does not support the required audio features. Please try Chrome or Safari.');
        } else if (err.name === 'SecurityError') {
            alert('Security error. Please try again or use HTTPS.');
        } else {
            alert('Error: ' + (err.message || 'Unknown error occurred'));
        }
        stopSession();
    }
}

function setupMicStreaming() {
    if (!audioCtx || !micStream) return;

    // Ensure audio context is running
    if (audioCtx.state === 'suspended') {
        audioCtx.resume().catch(err => console.warn('Could not resume audio context:', err));
    }

    var source = audioCtx.createMediaStreamSource(micStream);

    // Create audio processor for streaming with VAD
    audioProcessor = audioCtx.createScriptProcessor(AUDIO_BUFFER_SIZE, 1, 1);

    audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRecording || isProcessing) {
            return;
        }

        try {
            var inputData = e.inputBuffer.getChannelData(0);

            // Calculate RMS volume level
            var sum = 0;
            for (var i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            var rms = Math.sqrt(sum / inputData.length);

            var now = performance.now();

            // Voice Activity Detection
            if (rms > SILENCE_THRESHOLD) {
                // Speech detected
                if (!vadState.isSpeaking) {
                    // Speech just started
                    vadState.isSpeaking = true;
                    vadState.speechStartTime = now;
                    vadState.audioBuffer = [];
                    vadState.isListening = true;
                    console.log('Speech detected');
                    statusEl.textContent = 'Speaking...';
                }
                
                // Buffer audio data
                var pcm16 = new Int16Array(inputData.length);
                for (var j = 0; j < inputData.length; j++) {
                    var sample = Math.max(-1, Math.min(1, inputData[j]));
                    pcm16[j] = sample * 0x7FFF;
                }
                vadState.audioBuffer.push(pcm16);
                vadState.silenceStartTime = 0;

            } else if (vadState.isSpeaking) {
                // Silence after speech
                if (vadState.silenceStartTime === 0) {
                    vadState.silenceStartTime = now;
                }

                // Check if silence duration exceeds threshold
                if (now - vadState.silenceStartTime > MAX_SILENCE_DURATION) {
                    // End of utterance - send accumulated audio
                    var speechDuration = now - vadState.speechStartTime;
                    
                    if (speechDuration > MIN_SPEECH_DURATION && vadState.audioBuffer.length > 0) {
                        console.log('Sending audio - duration: ' + speechDuration.toFixed(0) + 'ms');
                        sendAudioBuffer(vadState.audioBuffer);
                        vadState.audioBuffer = [];
                    }
                    
                    vadState.isSpeaking = false;
                    vadState.isListening = false;
                    vadState.silenceStartTime = 0;
                    statusEl.textContent = 'Processing...';
                }
            }

        } catch (err) {
            console.error('Error processing audio:', err);
        }
    };

    source.connect(audioProcessor);
    audioProcessor.connect(audioCtx.destination);
}

function sendAudioBuffer(audioBuffers) {
    if (!ws || ws.readyState !== WebSocket.OPEN || isProcessing) return;
    
    try {
        isProcessing = true;
        
        // Calculate total length
        var totalLength = 0;
        for (var i = 0; i < audioBuffers.length; i++) {
            totalLength += audioBuffers[i].length;
        }
        
        // Combine all buffers into one
        var combined = new Int16Array(totalLength);
        var offset = 0;
        for (var i = 0; i < audioBuffers.length; i++) {
            combined.set(audioBuffers[i], offset);
            offset += audioBuffers[i].length;
        }
        
        ws.send(combined.buffer);
        console.log('Sent audio: ' + combined.length + ' samples');
        
        // Reset VAD state
        vadState.audioBuffer = [];
        
    } catch (err) {
        console.error('Error sending audio:', err);
        isProcessing = false;
    }
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
            isProcessing = false; // Allow new audio input after response

            currentAudioSource.onended = function() {
                console.log('Audio playback finished');
                currentAudioSource = null;
                isAudioPlaying = false;
                visualizer.classList.add('idle');
                statusEl.textContent = 'Listening';
            };

            console.log('Playing audio: ' + audioBuffer.duration.toFixed(2) + ' seconds');
        }, function(error) {
            console.error('Error decoding audio:', error);
            visualizer.classList.add('idle');
            isAudioPlaying = false;
            isProcessing = false;
            statusEl.textContent = 'Listening';
        });

    } catch (err) {
        console.error('Error playing audio:', err);
        visualizer.classList.add('idle');
        isAudioPlaying = false;
        isProcessing = false;
        statusEl.textContent = 'Listening';
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
    isProcessing = false;

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

    // Reset VAD state
    vadState = {
        isSpeaking: false,
        speechStartTime: 0,
        silenceStartTime: 0,
        audioBuffer: [],
        isListening: false
    };

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
        e.preventDefault(); // Prevent page scroll
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
    } else if (!document.hidden && isRecording) {
        console.log('Tab visible - resuming recording');
    }
});

// Handle beforeunload - clean up
window.addEventListener('beforeunload', function() {
    stopSession();
});

console.log('Voice AI Assistant initialized (clean version)');