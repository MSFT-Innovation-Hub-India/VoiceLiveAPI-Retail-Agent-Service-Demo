class VoiceLiveApp {
    constructor() {
        console.log('🏗️ VoiceLiveApp constructor called');
        this.ws = null;
        this.isConnected = false;
        this.sessionId = null;
        this.sessionInitialized = false;
        this.conversationContext = [];
        this.mediaRecorder = null;
        this.audioContext = null;
        this.microphoneStream = null;
        this.audioWorkletNode = null;
        this.isListening = false;
        this.avatarElement = document.getElementById('avatar');
        this.speechSynthesis = window.speechSynthesis || null;
        this.currentUtterance = null;
        this.isVoiceLiveAudioPlaying = false;
        this.responseInProgress = false;

        // Add audio playback functionality with improved timing
        this.audioPlaybackContext = null;
        this.audioPlaybackChunks = [];
        this.currentAudioSource = null;
        this.isPlayingAudio = false;
        
        // Add transcript handling
        this.currentResponseText = '';
        
        // Avatar session management
        this.avatarSessionStarted = false;
        this.avatarVideoElement = document.getElementById('avatarVideo');
        
        // Connect/disconnect button (using startSessionBtn as the main connect button)
        this.connectBtn = document.getElementById('startSessionBtn');
        console.log('🔘 Connect button found:', this.connectBtn);
        this.connectBtn.addEventListener('click', () => this.toggleConnection());
        
        // Listen button (microphone)
        this.listenBtn = document.getElementById('micBtn');
        this.listenBtn.addEventListener('click', () => this.toggleMicrophone());
        
        // Text to speech button
        this.speakBtn = document.getElementById('speakBtn');
        this.speakBtn.addEventListener('click', () => this.speakText());
        
        // Text input
        this.textInput = document.getElementById('textInput');
        this.textInput.addEventListener('keypress', (event) => {
            if (event.key === 'Enter') {
                this.speakText();
            }
        });
        
        // Stop button
        this.stopBtn = document.getElementById('stopSpeakingBtn');
        this.stopBtn.addEventListener('click', () => this.stopSpeaking());

        // Avatar session button (this will be the stop session button)
        this.stopSessionBtn = document.getElementById('stopSessionBtn');
        if (this.stopSessionBtn) {
            this.stopSessionBtn.addEventListener('click', () => this.stopAvatarSession());
        }

        // Update UI
        this.updateUI();
    }

    addChatMessage(message, type = 'system') {
        const chatLog = document.getElementById('chatHistory');
        if (!chatLog) {
            console.error('❌ chatHistory element not found!');
            return;
        }
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${type}`;
        
        // Add timestamp
        const timestamp = new Date().toLocaleTimeString();
        messageDiv.innerHTML = `<span class="timestamp">[${timestamp}]</span> ${message}`;
        
        chatLog.appendChild(messageDiv);
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    updateUI() {
        // Update connection status
        const statusElement = document.getElementById('connectionStatus');
        if (statusElement) {
            statusElement.innerHTML = this.isConnected 
                ? '<div class="status-connected">Connected</div>' 
                : '<div class="status-disconnected">Not Connected</div>';
        }
        
        // Update buttons - using the correct HTML IDs
        if (this.connectBtn) {
            this.connectBtn.textContent = this.isConnected ? 'Connected - Stop Session' : 'Start Avatar Session';
            this.connectBtn.className = this.isConnected ? 'btn btn-danger' : 'btn btn-primary';
        }
        
        if (this.listenBtn) {
            this.listenBtn.disabled = !this.isConnected;
            this.listenBtn.textContent = this.isListening ? '🎤 Listening' : '🎤';
        }
        
        if (this.speakBtn) {
            this.speakBtn.disabled = !this.isConnected;
        }
        
        if (this.stopBtn) {
            this.stopBtn.disabled = !this.isConnected;
        }
        
        if (this.stopSessionBtn) {
            this.stopSessionBtn.disabled = !this.isConnected;
        }
    }

    async toggleConnection() {
        console.log('🔘 Start Avatar Session button clicked! Current state:', this.isConnected);
        if (this.isConnected) {
            // If connected, stop the session and disconnect
            await this.stopAvatarSession();
            await this.disconnect();
        } else {
            // If not connected, connect first then start avatar session
            await this.connect();
            if (this.isConnected) {
                await this.startAvatarSession();
            }
        }
    }

    async connect() {
        try {
            console.log('🌐 Starting connection to Voice Live API...');
            this.addChatMessage('Attempting to connect to Voice Live API...', 'system');
            
            // Connect to the WebSocket proxy
            console.log('🔗 Creating WebSocket connection to ws://localhost:8765');
            this.ws = new WebSocket('ws://localhost:8765');
            
            this.ws.onopen = () => {
                this.isConnected = true;
                this.addChatMessage('Connected to Voice Live API proxy!', 'system');
                this.updateUI();
                this.initializeSession();
            };
            
            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event);
            };
            
            this.ws.onerror = (error) => {
                this.handleVoiceLiveError(error);
            };
            
            this.ws.onclose = (event) => {
                this.handleVoiceLiveClose(event);
            };
            
        } catch (error) {
            console.error('Connection error:', error);
            this.addChatMessage(`Connection error: ${error.message}`, 'error');
        }
    }

    async initializeSession() {
        try {
            console.log('Initializing Voice Live session...');
            
            const sessionConfig = {
                type: 'session.update',
                session: {
                    modalities: ['text', 'audio'],
                    // Using correct Azure Voice Live API format based on working examples
                    voice: {
                        name: 'en-US-Ava:DragonHDLatestNeural',
                        type: 'azure-standard',
                        temperature: 0.8
                    },
                    input_audio_format: 'pcm16',
                    output_audio_format: 'pcm16',
                    input_audio_transcription: {
                        model: 'whisper-1'
                    },
                    turn_detection: {
                        type: 'azure_semantic_vad',
                        threshold: 0.3,
                        prefix_padding_ms: 200,
                        silence_duration_ms: 200,
                        remove_filler_words: false,
                        end_of_utterance_detection: {
                            model: 'semantic_detection_v1',
                            threshold: 0.01,
                            timeout: 2
                        }
                    },
                    input_audio_noise_reduction: {
                        type: 'azure_deep_noise_suppression'
                    },
                    input_audio_echo_cancellation: {
                        type: 'server_echo_cancellation'
                    }
                },
                event_id: ''
            };
            
            this.ws.send(JSON.stringify(sessionConfig));
            this.addChatMessage('Session configuration sent (Azure format)', 'system');
            
        } catch (error) {
            console.error('Session initialization error:', error);
            this.addChatMessage(`Session initialization error: ${error.message}`, 'error');
        }
    }

    async disconnect() {
        try {
            this.addChatMessage('Disconnecting...', 'system');
            
            // Stop listening if active
            if (this.isListening) {
                await this.stopListening();
            }
            
            // Stop any avatar session
            await this.stopAvatarSession();
            
            // Close WebSocket
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
            }
            
            this.isConnected = false;
            this.sessionId = null;
            this.sessionInitialized = false;
            this.responseInProgress = false;
            this.updateUI();
            
        } catch (error) {
            console.error('Disconnect error:', error);
            this.addChatMessage(`Disconnect error: ${error.message}`, 'error');
        }
    }

    handleWebSocketMessage(event) {
        try {
            const message = JSON.parse(event.data);
            console.log('Received message:', message.type, message);
            
            // Special logging for audio messages
            if (message.type && message.type.includes('audio')) {
                console.log('🎵 AUDIO MESSAGE:', message.type, 'Delta length:', message.delta ? message.delta.length : 'no delta');
            }
            
            switch (message.type) {
                case 'session.created':
                    this.handleSessionCreated(message);
                    break;
                    
                case 'session.updated':
                    this.handleSessionUpdated(message);
                    break;
                    
                case 'input_audio_buffer.committed':
                    this.handleAudioBufferCommitted(message);
                    break;
                    
                case 'input_audio_buffer.speech_started':
                    this.handleSpeechStarted(message);
                    break;
                    
                case 'input_audio_buffer.speech_stopped':
                    this.handleSpeechStopped(message);
                    break;
                    
                case 'conversation.item.created':
                    this.handleConversationItemCreated(message);
                    break;
                    
                case 'response.created':
                    this.handleResponseCreated(message);
                    break;
                    
                case 'response.output_item.added':
                    this.handleResponseOutputItemAdded(message);
                    break;
                    
                case 'response.content_part.added':
                    this.handleResponseContentPartAdded(message);
                    break;
                    
                case 'response.content_part.done':
                    this.handleResponseContentPartDone(message);
                    break;
                    
                case 'response.output_item.done':
                    this.handleResponseOutputItemDone(message);
                    break;
                    
                case 'response.done':
                    this.handleResponseDone(message);
                    break;
                    
                case 'response.audio.delta':
                    console.log('🎵 RESPONSE AUDIO DELTA received, delta length:', message.delta ? message.delta.length : 'no delta');
                    this.handleAudioDelta(message.delta);
                    break;
                    
                case 'response.audio.done':
                    this.handleAudioDone(message);
                    break;
                    
                case 'response.text.delta':
                    this.handleTextDelta(message.delta);
                    break;
                    
                case 'response.text.done':
                    this.handleTextDone(message);
                    break;
                    
                case 'conversation.item.input_audio_transcription.completed':
                    this.handleUserTranscript(message);
                    break;
                    
                case 'response.audio_transcript.done':
                    this.handleAIAudioTranscript(message);
                    break;
                    
                case 'error':
                    this.handleApiError(message);
                    break;
                    
                default:
                    console.log('🔍 UNHANDLED MESSAGE TYPE:', message.type, message);
                    // Log full message for audio-related unhandled types
                    if (message.type && (message.type.includes('audio') || message.type.includes('delta'))) {
                        console.log('🔍 FULL AUDIO MESSAGE:', JSON.stringify(message, null, 2));
                    }
            }
            
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
            this.addChatMessage(`Message handling error: ${error.message}`, 'error');
        }
    }

    handleSessionCreated(message) {
        console.log('Session created:', message);
        this.sessionId = message.session.id;
        this.sessionInitialized = true;
        this.addChatMessage(`Session created with ID: ${this.sessionId}`, 'system');
    }

    handleSessionUpdated(message) {
        console.log('Session updated:', message);
        this.addChatMessage('Session configuration updated successfully', 'system');
    }

    handleAudioBufferCommitted(message) {
        console.log('Audio buffer committed:', message);
        this.addChatMessage('Audio buffer committed, processing...', 'system');
    }

    handleSpeechStarted(message) {
        console.log('Speech started detected:', message);
        
        // Ignore speech detection if we're currently playing AI audio (feedback prevention)
        if (this.isPlayingAudio) {
            console.log('⚠️ Ignoring speech detection during AI audio playback (preventing feedback)');
            return;
        }
        
        // Ignore if response is already in progress (prevent overlapping requests)
        if (this.responseInProgress) {
            console.log('⚠️ Ignoring speech detection - response already in progress');
            return;
        }
        
        this.addChatMessage('🎤 Speech started', 'user');
        
        // IMPORTANT: Stop any currently playing audio immediately to prevent overlap
        this.stopCurrentAudio();
    }

    handleSpeechStopped(message) {
        console.log('Speech stopped detected:', message);
        
        // Only process if we're not playing audio and not already processing
        if (this.isPlayingAudio) {
            console.log('⚠️ Ignoring speech stopped during AI audio playback');
            return;
        }
        
        if (this.responseInProgress) {
            console.log('⚠️ Ignoring speech stopped - response already in progress');
            return;
        }
        
        this.addChatMessage('🎤 Speech stopped', 'user');
    }

    handleConversationItemCreated(message) {
        console.log('Conversation item created:', message);
    }

    handleResponseCreated(message) {
        console.log('Response created:', message);
        this.responseInProgress = true;
        this.addChatMessage('🤖 AI is responding...', 'system');
        
        // Clear any previous audio chunks and text, stop current audio
        this.audioPlaybackChunks = [];
        this.currentResponseText = ''; // Reset transcript accumulation
        this.stopCurrentAudio();
    }

    handleResponseOutputItemAdded(message) {
        console.log('Response output item added:', message);
    }

    handleResponseContentPartAdded(message) {
        console.log('Response content part added:', message);
    }

    handleResponseContentPartDone(message) {
        console.log('Response content part done:', message);
    }

    handleResponseOutputItemDone(message) {
        console.log('Response output item done:', message);
    }

    handleResponseDone(message) {
        console.log('Response completed:', message);
        this.responseInProgress = false;
        
        // Debug: Check if we received any audio chunks
        console.log('🔍 Debug - Audio buffers in playback queue:', this.playbackQueue ? this.playbackQueue.length : 0);
        console.log('🔍 Debug - Legacy audio chunks:', this.audioPlaybackChunks ? this.audioPlaybackChunks.length : 0);
        
        if (this.playbackQueue && this.playbackQueue.length > 0) {
            console.log('🎵 Audio buffers queued for playback');
            this.addChatMessage('✅ AI response completed with queued audio', 'system');
            // Sequential playback is already started when chunks arrive
        } else if (this.audioPlaybackChunks && this.audioPlaybackChunks.length > 0) {
            console.log('🎵 Playing legacy audio chunks...');
            this.finishAudioPlayback();
            this.addChatMessage('✅ AI response completed with legacy audio', 'system');
        } else {
            console.log('⚠️ No audio chunks were received for this response');
            this.addChatMessage('⚠️ AI response completed but no audio received', 'system');
        }
    }

    handleApiError(message) {
        console.error('API Error:', message);
        this.addChatMessage(`API Error: ${message.error.message || JSON.stringify(message.error)}`, 'error');
    }

    handleTextDelta(delta) {
        console.log('📝 Text delta received:', delta);
        
        // Initialize text accumulation if needed
        if (!this.currentResponseText) {
            this.currentResponseText = '';
        }
        
        // Accumulate text delta
        this.currentResponseText += delta;
        
        // Log accumulated text for debugging
        console.log('📝 Accumulated response text so far:', this.currentResponseText);
    }

    handleTextDone(message) {
        console.log('📝 Text response completed:', message);
        
        if (this.currentResponseText) {
            // Display the complete AI response
            this.addChatMessage(`🤖 AI: ${this.currentResponseText}`, 'assistant');
            console.log('📝 Final AI response:', this.currentResponseText);
            this.currentResponseText = ''; // Reset for next response
        }
    }
    
    handleUserTranscript(message) {
        console.log('👤 User transcript completed:', message);
        const transcript = message.transcript || '';
        if (transcript.trim()) {
            this.addChatMessage(`👤 User: ${transcript}`, 'user');
            console.log('👤 User said:', transcript);
        }
    }
    
    handleAIAudioTranscript(message) {
        console.log('🤖 AI audio transcript completed:', message);
        const transcript = message.transcript || '';
        if (transcript.trim()) {
            // Only add if we don't already have text response (avoid duplication)
            if (!this.currentResponseText || this.currentResponseText.trim() === '') {
                this.addChatMessage(`🤖 AI (audio): ${transcript}`, 'assistant');
            }
            console.log('🤖 AI said (audio):', transcript);
        }
    }

    // IMPROVED: Handle audio delta with immediate playback for better timing
    async handleAudioDelta(base64AudioData) {
        try {
            console.log('🎵 Received audio delta, length:', base64AudioData.length);
            
            // Initialize audio context if needed
            if (!this.audioPlaybackContext) {
                this.audioPlaybackContext = new (window.AudioContext || window.webkitAudioContext)();
                this.audioPlaybackChunks = [];
                this.currentAudioSource = null;
                this.isPlayingAudio = false;
                this.audioQueue = [];
                this.playbackQueue = [];
                console.log('✅ Initialized audio playback context with queuing');
            }

            // Convert base64 to ArrayBuffer and decode
            const binaryString = atob(base64AudioData);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }

            // Decode audio buffer and add to playback queue
            try {
                const audioBuffer = await this.audioPlaybackContext.decodeAudioData(bytes.buffer.slice());
                this.playbackQueue.push(audioBuffer);
                console.log('🎵 Added audio buffer to playback queue, queue length:', this.playbackQueue.length);
                
                // Start sequential playback if not already playing
                if (!this.isPlayingAudio) {
                    this.playQueuedAudioSequentially();
                }
            } catch (decodeError) {
                console.error('Audio decode error:', decodeError);
                // Fallback: store raw bytes
                this.audioPlaybackChunks.push(bytes);
            }

        } catch (error) {
            console.error('Error handling audio delta:', error);
        }
    }

    // NEW METHOD: Play queued audio buffers sequentially without overlap
    async playQueuedAudioSequentially() {
        if (this.isPlayingAudio || this.playbackQueue.length === 0) {
            return;
        }

        this.isPlayingAudio = true;
        console.log('🎵 Starting sequential audio playback, queue length:', this.playbackQueue.length);

        try {
            while (this.playbackQueue.length > 0) {
                const audioBuffer = this.playbackQueue.shift();
                
                // Stop any current audio to prevent overlap
                this.stopCurrentAudio();
                
                // Create and configure audio source
                this.currentAudioSource = this.audioPlaybackContext.createBufferSource();
                this.currentAudioSource.buffer = audioBuffer;
                this.currentAudioSource.connect(this.audioPlaybackContext.destination);
                
                console.log(`🔊 Playing audio chunk (${audioBuffer.duration.toFixed(2)}s), ${this.playbackQueue.length} remaining`);
                
                // Wait for this chunk to finish before playing next
                await new Promise((resolve) => {
                    this.currentAudioSource.onended = () => {
                        console.log('✅ Audio chunk completed');
                        this.currentAudioSource = null;
                        resolve();
                    };
                    this.currentAudioSource.start(0);
                });
                
                // Small gap between chunks to ensure clean transitions
                await new Promise(resolve => setTimeout(resolve, 10));
            }
            
            console.log('🎵 Sequential audio playback completed');
            
        } catch (error) {
            console.error('Error in sequential audio playback:', error);
        } finally {
            this.isPlayingAudio = false;
            this.currentAudioSource = null;
        }
    }

    // Helper method to stop current audio to prevent overlaps
    stopCurrentAudio() {
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource.disconnect();
            } catch (error) {
                console.warn('Error stopping current audio:', error);
            }
            this.currentAudioSource = null;
        }
    }

    handleAudioDone(message) {
        console.log('Audio response completed:', message);
        this.addChatMessage('Audio response completed', 'system');
        
        // If we haven't started playing yet, play all chunks now
        if (!this.isPlayingAudio && this.audioPlaybackChunks.length > 0) {
            this.finishAudioPlayback();
        }
    }

    // IMPROVED: Better audio finishing with overlap prevention
    async finishAudioPlayback() {
        try {
            console.log('Finishing audio playback, chunks available:', this.audioPlaybackChunks.length);
            
            if (this.isPlayingAudio) {
                console.log('Audio already playing, not starting another playback');
                return;
            }
            
            // Stop any currently playing audio first to prevent overlap
            this.stopCurrentAudio();
            
            if (!this.audioPlaybackContext || this.audioPlaybackChunks.length === 0) {
                console.log('No audio context or chunks available');
                return;
            }

            this.isPlayingAudio = true;

            // Combine all audio chunks
            let totalLength = 0;
            this.audioPlaybackChunks.forEach(chunk => totalLength += chunk.length);
            console.log('Total audio data length:', totalLength, 'bytes');

            const combinedAudio = new Uint8Array(totalLength);
            let offset = 0;
            this.audioPlaybackChunks.forEach(chunk => {
                combinedAudio.set(chunk, offset);
                offset += chunk.length;
            });

            console.log('Decoding final audio data...');
            
            // Create WAV buffer
            const wavBuffer = this.createWavBuffer(combinedAudio, 24000);
            
            // Decode the audio data
            const audioBuffer = await this.audioPlaybackContext.decodeAudioData(wavBuffer);
            
            // Create and play the audio source
            this.currentAudioSource = this.audioPlaybackContext.createBufferSource();
            this.currentAudioSource.buffer = audioBuffer;
            this.currentAudioSource.connect(this.audioPlaybackContext.destination);
            
            // Handle when audio finishes
            this.currentAudioSource.onended = () => {
                this.currentAudioSource = null;
                this.isPlayingAudio = false;
                console.log('Final audio playback finished');
            };
            
            this.currentAudioSource.start(0);

            console.log('✅ Playing final AI audio response');
            this.addChatMessage('🔊 Playing AI audio response', 'system');
            
            // Clean up
            this.audioPlaybackChunks = [];
            
        } catch (error) {
            console.error('Error playing final audio:', error);
            this.addChatMessage(`Audio playback error: ${error.message}`, 'system');
            
            // Clean up on error
            this.audioPlaybackChunks = [];
            this.isPlayingAudio = false;
            this.currentAudioSource = null;
        }
    }

    // NEW METHOD: Stop current audio to prevent overlaps
    stopCurrentAudio() {
        if (this.currentAudioSource) {
            console.log('🛑 Stopping current audio to prevent overlap');
            try {
                this.currentAudioSource.stop();
            } catch (e) {
                console.log('Audio already stopped or stopping');
            }
            this.currentAudioSource = null;
        }
        this.isPlayingAudio = false;
    }

    createWavBuffer(pcmData, sampleRate = 24000, numChannels = 1, bitsPerSample = 16) {
        const byteRate = sampleRate * numChannels * bitsPerSample / 8;
        const blockAlign = numChannels * bitsPerSample / 8;
        const dataSize = pcmData.length;
        const fileSize = 36 + dataSize;

        const buffer = new ArrayBuffer(44 + dataSize);
        const view = new DataView(buffer);

        // RIFF header
        view.setUint32(0, 0x52494646, false); // "RIFF"
        view.setUint32(4, fileSize, true);
        view.setUint32(8, 0x57415645, false); // "WAVE"

        // fmt chunk
        view.setUint32(12, 0x666d7420, false); // "fmt "
        view.setUint32(16, 16, true); // chunk size
        view.setUint16(20, 1, true); // PCM format
        view.setUint16(22, numChannels, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, byteRate, true);
        view.setUint16(32, blockAlign, true);
        view.setUint16(34, bitsPerSample, true);

        // data chunk
        view.setUint32(36, 0x64617461, false); // "data"
        view.setUint32(40, dataSize, true);

        // PCM data
        const pcmView = new Uint8Array(buffer, 44);
        pcmView.set(pcmData);

        return buffer;
    }

    handleVoiceLiveError(error) {
        console.error('Voice Live WebSocket error:', error);
        this.addChatMessage(`Voice Live error: ${error.message || 'Unknown error'}`, 'error');
    }

    handleVoiceLiveClose(event) {
        console.log('Voice Live WebSocket closed:', event);
        this.isConnected = false;
        this.sessionId = null;
        this.sessionInitialized = false;
        this.responseInProgress = false;
        this.addChatMessage(`Voice Live connection closed: ${event.code} - ${event.reason}`, 'system');
        this.updateUI();
        
        // Clean up audio resources
        this.stopCurrentAudio();
        this.audioPlaybackChunks = [];
    }

    async toggleMicrophone() {
        if (this.isListening) {
            await this.stopListening();
        } else {
            await this.startListening();
        }
    }

    async startListening() {
        try {
            console.log('Starting to listen...');
            
            if (!this.isConnected || !this.sessionInitialized) {
                this.addChatMessage('Please connect to Voice Live API first', 'error');
                return;
            }

            // Get microphone access
            this.microphoneStream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 24000
                } 
            });

            // Create audio context for processing
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
            
            // Load the AudioWorklet module
            try {
                await this.audioContext.audioWorklet.addModule('data:text/javascript,' + encodeURIComponent(`
                    class PCMProcessor extends AudioWorkletProcessor {
                        process(inputs, outputs, parameters) {
                            const input = inputs[0];
                            if (input.length > 0) {
                                const inputData = input[0];
                                if (inputData) {
                                    // Convert float32 to int16 PCM
                                    const pcm16 = new Int16Array(inputData.length);
                                    for (let i = 0; i < inputData.length; i++) {
                                        pcm16[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32767));
                                    }
                                    this.port.postMessage(pcm16.buffer);
                                }
                            }
                            return true;
                        }
                    }
                    registerProcessor('pcm-processor', PCMProcessor);
                `));
            } catch (error) {
                console.error('Error loading AudioWorklet:', error);
                this.addChatMessage('Error setting up audio processing', 'error');
                return;
            }

            // Create audio worklet node
            this.audioWorkletNode = new AudioWorkletNode(this.audioContext, 'pcm-processor');
            
            // Handle audio data from worklet
            this.audioWorkletNode.port.onmessage = (event) => {
                this.sendPCMAudioChunk(new Uint8Array(event.data));
            };

            // Connect audio graph
            const source = this.audioContext.createMediaStreamSource(this.microphoneStream);
            source.connect(this.audioWorkletNode);

            this.isListening = true;
            this.updateUI();
            this.addChatMessage('🎤 Started listening...', 'system');

        } catch (error) {
            console.error('Error starting to listen:', error);
            this.addChatMessage(`Error starting microphone: ${error.message}`, 'error');
        }
    }

    async sendPCMAudioChunk(pcm16Data) {
        try {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                return;
            }

            // Convert to base64 for transmission
            let binary = '';
            const bytes = new Uint8Array(pcm16Data);
            const len = bytes.byteLength;
            for (let i = 0; i < len; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            const base64Audio = btoa(binary);

            // Send audio chunk to Voice Live API
            const message = {
                type: 'input_audio_buffer.append',
                audio: base64Audio
            };

            this.ws.send(JSON.stringify(message));

        } catch (error) {
            console.error('Error sending PCM audio chunk:', error);
        }
    }

    stopListening() {
        try {
            console.log('Stopping listening...');
            
            if (this.audioWorkletNode) {
                this.audioWorkletNode.disconnect();
                this.audioWorkletNode = null;
            }
            
            if (this.audioContext && this.audioContext.state !== 'closed') {
                this.audioContext.close();
                this.audioContext = null;
            }
            
            if (this.microphoneStream) {
                this.microphoneStream.getTracks().forEach(track => track.stop());
                this.microphoneStream = null;
            }

            // Commit the audio buffer
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'input_audio_buffer.commit'
                }));
            }

            this.isListening = false;
            this.updateUI();
            this.addChatMessage('🎤 Stopped listening', 'system');
            
        } catch (error) {
            console.error('Error stopping listening:', error);
            this.addChatMessage(`Error stopping microphone: ${error.message}`, 'error');
        }
    }

    async speakText() {
        try {
            const text = this.textInput.value.trim();
            if (!text) {
                this.addChatMessage('Please enter some text to speak', 'error');
                return;
            }

            if (!this.isConnected || !this.sessionInitialized) {
                this.addChatMessage('Please connect to Voice Live API first', 'error');
                return;
            }

            // Debug session state
            console.log('🗣️ Starting speakText...');
            console.log('🔍 Session state - Connected:', this.isConnected, 'Initialized:', this.sessionInitialized, 'Response in progress:', this.responseInProgress);
            console.log('🔍 Session ID:', this.sessionId);

            if (this.responseInProgress) {
                this.addChatMessage('⏳ Please wait for the current response to complete', 'error');
                return;
            }

            this.responseInProgress = true;
            this.addChatMessage(`👤 User: ${text}`, 'user');
            this.textInput.value = '';

            // Reset audio chunks and queue for new response
            this.audioPlaybackChunks = [];
            if (this.playbackQueue) {
                this.playbackQueue = [];
            }
            console.log('🔄 Reset audio chunks and playback queue for new response');

            // Send text message to Voice Live API
            const message = {
                type: 'conversation.item.create',
                item: {
                    type: 'message',
                    role: 'user',
                    content: [
                        {
                            type: 'input_text',
                            text: text
                        }
                    ]
                }
            };

            console.log('📤 Sending conversation item:', message);
            this.ws.send(JSON.stringify(message));

            // Request a response
            const responseMessage = {
                type: 'response.create',
                response: {
                    modalities: ['text', 'audio'],
                    instructions: 'Respond naturally and conversationally.'
                }
            };

            console.log('📤 Sending response request:', responseMessage);
            this.ws.send(JSON.stringify(responseMessage));

        } catch (error) {
            console.error('Error sending text:', error);
            this.addChatMessage(`Error sending text: ${error.message}`, 'error');
        }
    }

    stopSpeaking() {
        try {
            // Stop browser TTS if active
            if (this.speechSynthesis && this.currentUtterance) {
                this.speechSynthesis.cancel();
                this.currentUtterance = null;
                this.isVoiceLiveAudioPlaying = false;
            }

            // Stop Voice Live audio if playing
            this.stopCurrentAudio();

            // Cancel any ongoing response
            if (this.responseInProgress && this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({
                    type: 'response.cancel'
                }));
                this.responseInProgress = false;
            }

            // Clear audio chunks
            this.audioPlaybackChunks = [];

            this.addChatMessage('🛑 Stopped speaking', 'system');
            
        } catch (error) {
            console.error('Error stopping speech:', error);
            this.addChatMessage(`Error stopping speech: ${error.message}`, 'error');
        }
    }

    async toggleAvatarSession() {
        if (this.avatarSessionStarted) {
            await this.stopAvatarSession();
        } else {
            await this.startAvatarSession();
        }
    }

    async startAvatarSession() {
        try {
            this.addChatMessage('Starting Azure TTS Avatar session...', 'system');
            
            if (!this.isConnected) {
                this.addChatMessage('Please connect to Voice Live API first', 'error');
                return;
            }

            // For now, we'll use a placeholder video or image for the avatar
            // In a full implementation, this would connect to Azure TTS Avatar service
            if (this.avatarVideoElement) {
                // Set a placeholder or avatar video
                this.avatarVideoElement.style.display = 'block';
                this.avatarVideoElement.innerHTML = `
                    <div style="
                        width: 100%; 
                        height: 100%; 
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        display: flex; 
                        align-items: center; 
                        justify-content: center;
                        border-radius: 15px;
                        color: white;
                        font-size: 1.2rem;
                        text-align: center;
                        padding: 2rem;
                    ">
                        <div>
                            <div style="font-size: 4rem; margin-bottom: 1rem;">🤖</div>
                            <div>Azure TTS Avatar</div>
                            <div style="font-size: 0.9rem; margin-top: 0.5rem; opacity: 0.8;">
                                Ready to assist you!
                            </div>
                        </div>
                    </div>
                `;
                console.log('Avatar placeholder displayed');
            }

            this.avatarSessionStarted = true;
            this.addChatMessage('✅ Avatar session started successfully!', 'system');
            this.addChatMessage('💡 Avatar is ready - you can now use voice or text chat', 'system');
            this.updateUI();

        } catch (error) {
            console.error('Error starting avatar session:', error);
            this.addChatMessage(`Avatar session error: ${error.message}`, 'error');
        }
    }

    async stopAvatarSession() {
        try {
            this.addChatMessage('Stopping avatar session...', 'system');
            
            if (this.avatarVideoElement) {
                this.avatarVideoElement.style.display = 'none';
                this.avatarVideoElement.innerHTML = '';
            }

            this.avatarSessionStarted = false;
            this.addChatMessage('🛑 Avatar session stopped', 'system');
            this.updateUI();
            
        } catch (error) {
            console.error('Error stopping avatar session:', error);
            this.addChatMessage(`Error stopping avatar session: ${error.message}`, 'error');
        }
    }
}

// Initialize the app when the page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 DOM loaded, initializing VoiceLiveApp...');
    window.app = new VoiceLiveApp();
    console.log('✅ VoiceLiveApp initialized:', window.app);
});
