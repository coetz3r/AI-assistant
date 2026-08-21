// DOM Elements
const statusEl = document.getElementById('status');
const statusSubEl = document.getElementById('status-sub');
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
let isProcessing = false;
let audioBuffer = [];
let isSpeaking = false;
let silenceFrames = 0;
const MAX_SILENCE_FRAMES = 30;
const MIN_SPEECH_FRAMES = 10;
const RMS_THRESHOLD = 0.015;

// Event Listeners
startBtn.addEventListener('click', startVoiceSession);
stopBtn.addEventListener('click', stopSession);

// Status update function
function updateStatus(mainStatus, subStatus, className) {
    if (statusEl) {
        statusEl.textContent = mainStatus;
        // Remove all status classes
        statusEl.classList.remove('listening', 'thinking', 'talking', 'connected', 'disconnected');
        if (className) {
            statusEl.classList.add(className);
        }
    }
    if (statusSubEl) {
        statusSubEl.textContent = subStatus || '';
    }
}

// Mobile audio context resume
document.addEventListener('touchstart', function resumeAudio() {
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(err => console.warn('Failed to resume audio context:', err));
    }
}, { once: false });

async function startVoiceSession() {
    try {
        updateStatus('Initializing...', 'Starting microphone', 'disconnected');

        // Initialize Audio Context
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)({ 
                sampleRate: 16000 
            });
        }

        if (audioCtx.state === 'suspended') {
            await audioCtx.resume();
        }

        console.log('Audio context state:', audioCtx.state);

        // Clean microphone constraints
        const constraints = {
            audio: {
                channelCount: 1,
                sampleRate: 16000,
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                latency: 0.01
            },
            video: false
        };

        micStream = await navigator.mediaDevices.getUserMedia(constraints);

        const audioTrack = micStream.getAudioTracks()[0];
        console.log('Microphone track:', {
            label: audioTrack.label,
            enabled: audioTrack.enabled,
            settings: audioTrack.getSettings()
        });

        // Connect WebSocket
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = protocol + '//' + location.host + '/ws';

        console.log('Connecting to: ' + wsUrl);
        ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';

        ws.onopen = function() {
            console.log('WebSocket connected');
            updateStatus('Listening', 'Say something...', 'listening');
            startBtn.disabled = true;
            stopBtn.disabled = false;
            isRecording = true;
            isProcessing = false;
            isSpeaking = false;
            audioBuffer = [];
            silenceFrames = 0;
            setupMicStreaming();
        };

        ws.onmessage = function(event) {
            if (event.data instanceof ArrayBuffer) {
                console.log('Received audio: ' + event.data.byteLength + ' bytes');
                isProcessing = false;
                playIncomingAudio(event.data);
            }
        };

        ws.onerror = function(error) {
            console.error('WebSocket error:', error);
            updateStatus('Connection Error', 'Check console for details', 'disconnected');
            stopSession();
        };

        ws.onclose = function(event) {
            console.log('WebSocket closed: ' + event.code + ' - ' + (event.reason || 'Normal closure'));
            if (event.code !== 1000 && event.code !== 1001) {
                updateStatus('Disconnected', 'Tap Start to reconnect', 'disconnected');
            }
            stopSession();
        };

    } catch (err) {
        console.error('Error starting voice session:', err);
        
        if (err.name === 'NotAllowedError') {
            updateStatus('Permission Denied', 'Allow microphone access', 'disconnected');
            alert('Microphone access denied. Please allow microphone permissions.');
        } else if (err.name === 'NotFoundError') {
            updateStatus('No Microphone', 'Connect a microphone', 'disconnected');
            alert('No microphone found. Please connect a microphone.');
        } else {
            updateStatus('Error', err.message || 'Unknown error', 'disconnected');
            alert('Error: ' + (err.message || 'Unknown error occurred'));
        }
        stopSession();
    }
}

function setupMicStreaming() {
    if (!audioCtx || !micStream) return;

    if (audioCtx.state === 'suspended') {
        audioCtx.resume().catch(err => console.warn('Could not resume audio context:', err));
    }

    var source = audioCtx.createMediaStreamSource(micStream);
    
    audioProcessor = audioCtx.createScriptProcessor(2048, 1, 1);

    audioProcessor.onaudioprocess = function(e) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRecording || isProcessing) {
            return;
        }

        try {
            var inputData = e.inputBuffer.getChannelData(0);
            
            var sum = 0;
            for (var i = 0; i < inputData.length; i++) {
                sum += inputData[i] * inputData[i];
            }
            var rms = Math.sqrt(sum / inputData.length);

            if (rms > RMS_THRESHOLD) {
                if (!isSpeaking) {
                    isSpeaking = true;
                    silenceFrames = 0;
                    audioBuffer = [];
                    updateStatus('Speaking...', 'Listening to you...', 'listening');
                    console.log('Speech detected - RMS: ' + rms.toFixed(4));
                }
                
                var pcm16 = new Int16Array(inputData.length);
                for (var j = 0; j < inputData.length; j++) {
                    var sample = Math.max(-1, Math.min(1, inputData[j]));
                    pcm16[j] = sample * 0x7FFF;
                }
                audioBuffer.push(pcm16);
                silenceFrames = 0;
                
            } else if (isSpeaking) {
                silenceFrames++;
                
                if (silenceFrames > MAX_SILENCE_FRAMES && audioBuffer.length > MIN_SPEECH_FRAMES) {
                    console.log('Speech ended - frames: ' + audioBuffer.length + ', silence: ' + silenceFrames);
                    sendAudioBuffer(audioBuffer);
                    audioBuffer = [];
                    isSpeaking = false;
                    silenceFrames = 0;
                    updateStatus('Thinking...', 'Processing your request', 'thinking');
                }
            }
            
        } catch (err) {
            console.error('Error processing audio:', err);
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
        console.log('Sent audio: ' + combined.length + ' samples');
        
    } catch (err) {
        console.error('Error sending audio:', err);
        isProcessing = false;
    }
}

function playIncomingAudio(arrayBuffer) {
    try {
        // Stop current playback
        stopPlayback();
        visualizer.classList.remove('idle');

        updateStatus('Talking...', 'Playing response', 'talking');

        // Setup analyser
        if (!playbackAnalyser) {
            playbackAnalyser = audioCtx.createAnalyser();
            playbackAnalyser.fftSize = 32;
            playbackAnalyser.smoothingTimeConstant = 0.8;
            playbackAnalyser.connect(audioCtx.destination);
            animateVisualizer();
        }

        // Decode and play
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
                isProcessing = false;
                
                // Return to listening state
                if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
                    updateStatus('Listening', 'Say something...', 'listening');
                } else {
                    updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');
                }
            };

            console.log('Playing audio: ' + audioBuffer.duration.toFixed(2) + ' seconds');
        }, function(error) {
            console.error('Error decoding audio:', error);
            visualizer.classList.add('idle');
            isAudioPlaying = false;
            isProcessing = false;
            
            if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
                updateStatus('Listening', 'Say something...', 'listening');
            } else {
                updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');
            }
        });

    } catch (err) {
        console.error('Error playing audio:', err);
        visualizer.classList.add('idle');
        isAudioPlaying = false;
        isProcessing = false;
        
        if (isRecording && ws && ws.readyState === WebSocket.OPEN) {
            updateStatus('Listening', 'Say something...', 'listening');
        } else {
            updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');
        }
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
        } catch (err) {}
    }
    draw();
}

function stopSession() {
    console.log('Stopping session...');
    isRecording = false;
    isProcessing = false;
    isSpeaking = false;
    audioBuffer = [];
    silenceFrames = 0;

    updateStatus('Disconnected', 'Tap Start to connect', 'disconnected');

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
            ws.onclose = null;
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
    startBtn.disabled = false;
    stopBtn.disabled = true;
    visualizer.classList.add('idle');

    console.log('Session stopped cleanly');
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        if (!startBtn.disabled) {
            startBtn.click();
        } else if (!stopBtn.disabled) {
            stopBtn.click();
        }
    }
});

// Clean up on page unload
window.addEventListener('beforeunload', function() {
    stopSession();
});

console.log('Voice AI Assistant initialized with status indicators');