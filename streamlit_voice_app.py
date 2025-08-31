import streamlit as st
import os
import uuid
import json
import time
import base64
import logging
import threading
import numpy as np
import sounddevice as sd
import queue
from collections import deque
from dotenv import load_dotenv
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential
from typing import Dict, Union, Literal, Set
from typing_extensions import Iterator, TypedDict, Required
import websocket
from websocket import WebSocketApp
from datetime import datetime
import traceback

# Load environment variables
load_dotenv("./.env", override=True)

# Global variables for thread coordination
AUDIO_SAMPLE_RATE = 24000
logger = logging.getLogger(__name__)

# Global stop event for threads
stop_event = threading.Event()

# Global message queue for thread-safe communication
message_queue = queue.Queue()
status_queue = queue.Queue()

# Global flag for UI refresh
ui_refresh_needed = threading.Event()

class VoiceLiveConnection:
    def __init__(self, url: str, headers: dict) -> None:
        self._url = url
        self._headers = headers
        self._ws = None
        self._message_queue = queue.Queue()
        self._connected = False
        self._ws_thread = None

    def connect(self) -> None:
        def on_message(ws, message):
            self._message_queue.put(message)

        def on_error(ws, error):
            st.error(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            st.info("Connection closed")
            self._connected = False

        def on_open(ws):
            st.success("Connected to Voice Live API")
            self._connected = True

        self._ws = websocket.WebSocketApp(
            self._url,
            header=self._headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        # Start WebSocket in a separate thread
        self._ws_thread = threading.Thread(target=self._ws.run_forever)
        self._ws_thread.daemon = True
        self._ws_thread.start()

        # Wait for connection to be established
        timeout = 10  # seconds
        start_time = time.time()
        while not self._connected and time.time() - start_time < timeout:
            time.sleep(0.1)

        if not self._connected:
            raise ConnectionError("Failed to establish WebSocket connection")

    def recv(self) -> str:
        try:
            return self._message_queue.get(timeout=0.1)
        except queue.Empty:
            return None

    def send(self, message: str) -> None:
        if self._ws and self._connected:
            self._ws.send(message)

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._connected = False

class AzureVoiceLive:
    def __init__(
        self,
        *,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._azure_endpoint = azure_endpoint
        self._api_version = api_version
        self._token = token
        self._api_key = api_key
        self._connection = None

    def connect(self, project_name: str, agent_id: str, agent_access_token: str) -> VoiceLiveConnection:
        if self._connection is not None:
            raise ValueError("Already connected to the Voice Live API.")
        if not project_name:
            raise ValueError("Project name is required.")
        if not agent_id:
            raise ValueError("Agent ID is required.")
        if not agent_access_token:
            raise ValueError("Agent access token is required.")

        azure_ws_endpoint = self._azure_endpoint.rstrip('/').replace("https://", "wss://")

        url = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self._api_version}&agent-project-name={project_name}&agent-id={agent_id}&agent-access-token={agent_access_token}"
        auth_header = {"Authorization": f"Bearer {self._token}"} if self._token else {"api-key": self._api_key}
        request_id = uuid.uuid4()
        headers = {"x-ms-client-request-id": str(request_id), **auth_header}

        self._connection = VoiceLiveConnection(url, headers)
        self._connection.connect()
        return self._connection

class AudioPlayerAsync:
    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(
            callback=self.callback,
            samplerate=AUDIO_SAMPLE_RATE,
            channels=1,
            dtype=np.int16,
            blocksize=2400,
        )
        self.playing = False

    def callback(self, outdata, frames, time, status):
        if status:
            logger.warning(f"Stream status: {status}")
        with self.lock:
            data = np.empty(0, dtype=np.int16)
            while len(data) < frames and len(self.queue) > 0:
                item = self.queue.popleft()
                frames_needed = frames - len(data)
                data = np.concatenate((data, item[:frames_needed]))
                if len(item) > frames_needed:
                    self.queue.appendleft(item[frames_needed:])
            if len(data) < frames:
                data = np.concatenate((data, np.zeros(frames - len(data), dtype=np.int16)))
        outdata[:] = data.reshape(-1, 1)

    def add_data(self, data: bytes):
        with self.lock:
            # Only add data if we're currently playing or queue is empty
            # This prevents adding stale audio data after interruption
            if self.playing or len(self.queue) == 0:
                np_data = np.frombuffer(data, dtype=np.int16)
                self.queue.append(np_data)
                if not self.playing and len(self.queue) > 0:
                    self.start()

    def start(self):
        if not self.playing:
            self.playing = True
            self.stream.start()

    def stop(self):
        with self.lock:
            self.queue.clear()
        self.playing = False
        self.stream.stop()

    def terminate(self):
        with self.lock:
            self.queue.clear()
        self.stream.stop()
        self.stream.close()

def listen_and_send_audio(connection: VoiceLiveConnection) -> None:
    """Continuously listen to audio and send to API"""
    logger.info("Starting continuous audio stream...")
    
    try:
        # List available audio devices for debugging
        logger.info(f"Available audio devices: {sd.query_devices()}")
        
        stream = sd.InputStream(channels=1, samplerate=AUDIO_SAMPLE_RATE, dtype="int16")
        stream.start()
        logger.info("Audio input stream started successfully")
        
        read_size = int(AUDIO_SAMPLE_RATE * 0.02)  # 20ms chunks
        audio_sent_count = 0
        
        while not stop_event.is_set():
            if stream.read_available >= read_size:
                data, _ = stream.read(read_size)
                audio = base64.b64encode(data).decode("utf-8")
                param = {"type": "input_audio_buffer.append", "audio": audio, "event_id": ""}
                data_json = json.dumps(param)
                connection.send(data_json)
                audio_sent_count += 1
                
                # Log every 100 audio chunks to confirm audio is being sent
                if audio_sent_count % 100 == 0:
                    logger.info(f"Sent {audio_sent_count} audio chunks to API")
            else:
                time.sleep(0.001)  # Small sleep to prevent busy waiting
                
    except Exception as e:
        logger.error(f"Audio stream interrupted. {e}")
        logger.error(f"Exception details: {traceback.format_exc()}")
    finally:
        if 'stream' in locals():
            stream.stop()
            stream.close()
        logger.info("Audio stream closed.")

def receive_audio_and_playback(connection: VoiceLiveConnection) -> None:
    """Continuously receive and play audio from API"""
    last_audio_item_id = None
    current_response_id = None
    audio_player = AudioPlayerAsync()

    logger.info("Starting continuous audio playback...")
    try:
        while not stop_event.is_set():
            raw_event = connection.recv()
            if raw_event is None:
                continue

            try:
                event = json.loads(raw_event)
                event_type = event.get("type")
                print(f"[DEBUG] Received event: {event_type}")  # Debug all events

                if event_type == "session.created":
                    session = event.get("session")
                    logger.info(f"Session created: {session.get('id')}")
                    print(f"[DEBUG] Session created: {session.get('id')}")  # Direct console output
                    # Add to session state directly with lock
                    if 'messages' not in st.session_state:
                        st.session_state.messages = []
                    st.session_state.messages.append({
                        "type": "system", 
                        "content": f"🟢 Connected - Session: {session.get('id')[:8]}...",
                        "timestamp": datetime.now().strftime("%H:%M:%S")
                    })
                    print(f"[DEBUG] Added connection message. Total messages: {len(st.session_state.messages)}")

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    user_transcript = event.get("transcript", "")
                    if user_transcript:
                        print(f"[DEBUG] User transcript received: {user_transcript}")  # Direct console output
                        # Add to session state directly
                        if 'messages' not in st.session_state:
                            st.session_state.messages = []
                        st.session_state.messages.append({
                            "type": "user", 
                            "content": user_transcript,
                            "timestamp": datetime.now().strftime("%H:%M:%S")
                        })
                        st.session_state.user_speaking = False
                        print(f"[DEBUG] Added user message. Total messages: {len(st.session_state.messages)}")
                        logger.info(f"User said: {user_transcript}")
                        # Try to trigger UI refresh - this may not work from background thread
                        try:
                            st.rerun()
                        except:
                            # Set flag for main thread to check
                            ui_refresh_needed.set()

                elif event_type == "response.audio_transcript.delta":
                    transcript_delta = event.get("delta", "")
                    if transcript_delta:
                        # Build up the assistant's response incrementally
                        if not hasattr(st.session_state, 'current_assistant_response'):
                            st.session_state.current_assistant_response = ""
                        st.session_state.current_assistant_response += transcript_delta
                        print(f"[DEBUG] Assistant transcript delta: {transcript_delta}")

                elif event_type == "response.audio_transcript.done":
                    # Assistant has finished speaking, save the complete response
                    if hasattr(st.session_state, 'current_assistant_response') and st.session_state.current_assistant_response:
                        print(f"[DEBUG] Assistant complete response: {st.session_state.current_assistant_response}")
                        if 'messages' not in st.session_state:
                            st.session_state.messages = []
                        st.session_state.messages.append({
                            "type": "assistant", 
                            "content": st.session_state.current_assistant_response,
                            "timestamp": datetime.now().strftime("%H:%M:%S")
                        })
                        print(f"[DEBUG] Added complete assistant message. Total messages: {len(st.session_state.messages)}")
                        # Clear the current response buffer
                        st.session_state.current_assistant_response = ""
                        st.session_state.assistant_responding = False
                        # Try to trigger UI refresh - this may not work from background thread
                        try:
                            st.rerun()
                        except:
                            # Set flag for main thread to check
                            ui_refresh_needed.set()

                elif event_type == "response.created":
                    current_response_id = event.get("response", {}).get("id")
                    logger.info(f"New response created: {current_response_id}")
                    print(f"[DEBUG] Response created: {current_response_id}")
                    st.session_state.assistant_responding = True
                    # Initialize the response buffer
                    st.session_state.current_assistant_response = ""

                elif event_type == "response.audio.delta":
                    response_id = event.get("response_id")
                    # Only process audio if it's from the current response
                    if response_id != current_response_id:
                        logger.debug(f"Ignoring audio from old response: {response_id}")
                        continue
                        
                    if event.get("item_id") != last_audio_item_id:
                        # New audio item started, clear any previous audio in queue
                        last_audio_item_id = event.get("item_id")
                        logger.debug(f"New audio item started: {last_audio_item_id}")

                    bytes_data = base64.b64decode(event.get("delta", ""))
                    if bytes_data:
                        logger.debug(f"Received audio data of length: {len(bytes_data)}")
                        audio_player.add_data(bytes_data)

                elif event_type == "session.updated":
                    print("[DEBUG] Session updated")
                    logger.info("Session configuration updated")

                elif event_type == "input_audio_buffer.committed":
                    print("[DEBUG] Audio buffer committed")
                    logger.info("User audio committed for processing")

                elif event_type == "conversation.item.created":
                    print("[DEBUG] Conversation item created")
                    logger.info("New conversation item created")

                elif event_type == "response.output_item.added":
                    print("[DEBUG] Response output item added")
                    logger.info("Response output item added")

                elif event_type == "response.content_part.added":
                    print("[DEBUG] Response content part added")
                    logger.info("Response content part added")

                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("Speech started - stopping playback and cancelling response")
                    print("[DEBUG] Speech started detected")  # Direct console output
                    st.session_state.user_speaking = True
                    st.session_state.assistant_responding = False
                    audio_player.stop()
                    current_response_id = None  # Clear current response tracking
                    # Cancel any ongoing response to prevent dual audio
                    cancel_response = {
                        "type": "response.cancel",
                        "event_id": str(uuid.uuid4())
                    }
                    connection.send(json.dumps(cancel_response))

                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("Speech stopped - user finished speaking")
                    print("[DEBUG] Speech stopped detected")
                    st.session_state.user_speaking = False

                elif event_type == "response.audio.done":
                    logger.info("Assistant finished speaking")
                    print("[DEBUG] Assistant audio done")
                    st.session_state.assistant_responding = False

                elif event_type == "error":
                    error_details = event.get("error", {})
                    error_type = error_details.get("type", "Unknown")
                    error_code = error_details.get("code", "Unknown")
                    error_message = error_details.get("message", "No message provided")
                    logger.error(f"API Error: Type={error_type}, Code={error_code}, Message={error_message}")
                    if 'messages' not in st.session_state:
                        st.session_state.messages = []
                    st.session_state.messages.append({
                        "type": "system", 
                        "content": f"⚠️ Error: {error_message}",
                        "timestamp": datetime.now().strftime("%H:%M:%S")
                    })

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON event: {e}")
                continue

    except Exception as e:
        logger.error(f"Error in audio playback: {e}")
    finally:
        audio_player.terminate()
        logger.info("Playback done.")

def process_message_queues():
    """Process messages and status updates from background threads"""
    # Process new messages
    while not message_queue.empty():
        try:
            message = message_queue.get_nowait()
            if 'messages' not in st.session_state:
                st.session_state.messages = []
            st.session_state.messages.append(message)
        except queue.Empty:
            break
    
    # Process status updates
    while not status_queue.empty():
        try:
            status_key, status_value = status_queue.get_nowait()
            if status_key == "user_speaking":
                st.session_state.user_speaking = status_value
            elif status_key == "assistant_responding":
                st.session_state.assistant_responding = status_value
        except queue.Empty:
            break

def main():
    # Page configuration
    st.set_page_config(
        page_title="Contoso Retail Assistant Agent",
        page_icon="🎤",
        layout="wide"
    )
    
    # Custom CSS for styling
    st.markdown("""
    <style>
    .main-header {
        text-align: center;
        color: #2E86AB;
        font-size: 2.5rem;
        margin-bottom: 2rem;
        font-weight: bold;
    }
    .microphone-container {
        display: flex;
        justify-content: center;
        align-items: center;
        margin: 3rem 0;
    }
    .mic-button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border: none;
        border-radius: 50%;
        width: 120px;
        height: 120px;
        font-size: 3rem;
        color: white;
        cursor: pointer;
        box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        transition: all 0.3s ease;
    }
    .mic-button:hover {
        transform: scale(1.05);
        box-shadow: 0 12px 35px rgba(0,0,0,0.4);
    }
    .listening {
        background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
        animation: pulse 1.5s infinite;
    }
    @keyframes pulse {
        0% { transform: scale(1); }
        50% { transform: scale(1.05); }
        100% { transform: scale(1); }
    }
    .status-message {
        text-align: center;
        font-size: 1.2rem;
        margin: 1rem 0;
    }
    .chat-container {
        max-height: 400px;
        overflow-y: auto;
        padding: 20px;
        background-color: #f8f9fa;
        border-radius: 15px;
        margin: 20px 0;
        border: 1px solid #e9ecef;
    }
    .message-user {
        background: linear-gradient(135deg, #007bff 0%, #0056b3 100%);
        color: white;
        padding: 15px 20px;
        border-radius: 20px 20px 5px 20px;
        margin: 10px 0 10px 50px;
        box-shadow: 0 3px 10px rgba(0,123,255,0.3);
        position: relative;
        animation: slideInRight 0.3s ease-out;
    }
    .message-user::before {
        content: "👤";
        position: absolute;
        left: -40px;
        top: 10px;
        font-size: 24px;
        background: white;
        border-radius: 50%;
        width: 35px;
        height: 35px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .message-assistant {
        background: linear-gradient(135deg, #28a745 0%, #1e7e34 100%);
        color: white;
        padding: 15px 20px;
        border-radius: 20px 20px 20px 5px;
        margin: 10px 50px 10px 0;
        box-shadow: 0 3px 10px rgba(40,167,69,0.3);
        position: relative;
        animation: slideInLeft 0.3s ease-out;
    }
    .message-assistant::before {
        content: "🤖";
        position: absolute;
        right: -40px;
        top: 10px;
        font-size: 24px;
        background: white;
        border-radius: 50%;
        width: 35px;
        height: 35px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .message-system {
        background-color: #6c757d;
        color: white;
        padding: 8px 15px;
        border-radius: 15px;
        margin: 5px auto;
        font-style: italic;
        font-size: 0.9rem;
        text-align: center;
        max-width: 300px;
        opacity: 0.8;
    }
    .message-timestamp {
        font-size: 0.7rem;
        opacity: 0.7;
        margin-top: 5px;
    }
    .message-speaking {
        border: 2px solid #ffc107;
        animation: speaking 1s infinite;
    }
    .message-typing {
        background: linear-gradient(135deg, #6c757d 0%, #495057 100%);
        color: white;
        padding: 15px 20px;
        border-radius: 20px 20px 20px 5px;
        margin: 10px 50px 10px 0;
        position: relative;
        animation: typing 1.5s infinite;
    }
    .message-typing::before {
        content: "🤖";
        position: absolute;
        right: -40px;
        top: 10px;
        font-size: 24px;
        background: white;
        border-radius: 50%;
        width: 35px;
        height: 35px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    @keyframes slideInRight {
        from { transform: translateX(50px); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideInLeft {
        from { transform: translateX(-50px); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes speaking {
        0% { box-shadow: 0 3px 10px rgba(255,193,7,0.3); }
        50% { box-shadow: 0 3px 20px rgba(255,193,7,0.6); }
        100% { box-shadow: 0 3px 10px rgba(255,193,7,0.3); }
    }
    @keyframes typing {
        0%, 60%, 100% { opacity: 1; }
        30% { opacity: 0.7; }
    }
    .typing-dots {
        animation: typingDots 1.4s infinite;
    }
    @keyframes typingDots {
        0%, 20% { content: ""; }
        40% { content: "."; }
        60% { content: ".."; }
        80%, 100% { content: "..."; }
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Header
    st.markdown('<h1 class="main-header">🛍️ Contoso Retail Assistant Agent</h1>', unsafe_allow_html=True)
    
    # Initialize session state
    if 'connection' not in st.session_state:
        st.session_state.connection = None
    if 'streaming_active' not in st.session_state:
        st.session_state.streaming_active = False
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'audio_threads' not in st.session_state:
        st.session_state.audio_threads = []
    if 'user_speaking' not in st.session_state:
        st.session_state.user_speaking = False
    if 'assistant_responding' not in st.session_state:
        st.session_state.assistant_responding = False
    
    # Connection setup
    if st.session_state.connection is None:
        with st.spinner("Connecting to Azure Voice Live API..."):
            try:
                # Get environment variables
                endpoint = os.environ.get("AZURE_VOICE_LIVE_ENDPOINT")
                agent_id = os.environ.get("AI_FOUNDRY_AGENT_ID")
                project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME")
                api_version = os.environ.get("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
                api_key = os.environ.get("AZURE_VOICE_LIVE_API_KEY")
                
                if not all([endpoint, agent_id, project_name]):
                    st.error("Missing required environment variables. Please check your .env file.")
                    st.stop()
                
                # Get Azure credential
                credential = DefaultAzureCredential()
                scopes = "https://ai.azure.com/.default"
                token = credential.get_token(scopes)
                
                # Create client and connection
                client = AzureVoiceLive(
                    azure_endpoint=endpoint,
                    api_version=api_version,
                    token=token.token,
                )
                
                connection = client.connect(
                    project_name=project_name,
                    agent_id=agent_id,
                    agent_access_token=token.token
                )
                
                # Configure session
                session_update = {
                    "type": "session.update",
                    "session": {
                        "turn_detection": {
                            "type": "azure_semantic_vad",
                            "threshold": 0.3,
                            "prefix_padding_ms": 200,
                            "silence_duration_ms": 200,
                            "remove_filler_words": False,
                            "end_of_utterance_detection": {
                                "model": "semantic_detection_v1",
                                "threshold": 0.01,
                                "timeout": 2,
                            },
                        },
                        "input_audio_noise_reduction": {
                            "type": "azure_deep_noise_suppression"
                        },
                        "input_audio_echo_cancellation": {
                            "type": "server_echo_cancellation"
                        },
                        "voice": {
                            "name": "en-US-Ava:DragonHDLatestNeural",
                            "type": "azure-standard",
                            "temperature": 0.8,
                        },
                    },
                    "event_id": ""
                }
                connection.send(json.dumps(session_update))
                
                st.session_state.connection = connection
                st.success("Connected to Azure Voice Live API!")
                
            except Exception as e:
                st.error(f"Failed to connect: {e}")
                st.stop()
    
    # Microphone interface
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown('<div class="microphone-container">', unsafe_allow_html=True)
        
        # Microphone button
        if st.session_state.streaming_active:
            st.markdown('<p class="status-message">🎤 Listening... Click to stop</p>', unsafe_allow_html=True)
            if st.button("🔴", key="mic_stop", help="Stop listening"):
                # Stop streaming
                stop_event.set()
                st.session_state.streaming_active = False
                # Wait for threads to stop
                for thread in st.session_state.audio_threads:
                    if thread.is_alive():
                        thread.join(timeout=2)
                st.session_state.audio_threads = []
                stop_event.clear()
                st.success("Stopped listening")
                st.rerun()
        else:
            st.markdown('<p class="status-message">Click the microphone to start listening</p>', unsafe_allow_html=True)
            if st.button("🎤", key="mic_start", help="Start listening"):
                # Start streaming
                stop_event.clear()
                st.session_state.streaming_active = True
                
                # Start audio threads
                send_thread = threading.Thread(
                    target=listen_and_send_audio, 
                    args=(st.session_state.connection,),
                    daemon=True
                )
                receive_thread = threading.Thread(
                    target=receive_audio_and_playback, 
                    args=(st.session_state.connection,),
                    daemon=True
                )
                
                send_thread.start()
                receive_thread.start()
                
                st.session_state.audio_threads = [send_thread, receive_thread]
                st.success("Started listening!")
                st.rerun()
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Display conversation prominently
    st.markdown("### 💬 Live Conversation")
    
    # Add a refresh button for real-time updates
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        if st.button("🔄 Refresh Chat", help="Click to see latest messages"):
            st.rerun()
    
    # Add a simple test button to add a message manually
    with col1:
        if st.button("🧪 Test Message", help="Add a test message"):
            if 'messages' not in st.session_state:
                st.session_state.messages = []
            st.session_state.messages.append({
                "type": "system", 
                "content": f"Test message at {datetime.now().strftime('%H:%M:%S')}",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()
    
    # Debug info
    with st.expander("🔧 Debug Info", expanded=False):
        st.write(f"Messages in session: {len(st.session_state.messages)}")
        st.write(f"User speaking: {st.session_state.user_speaking}")
        st.write(f"Assistant responding: {st.session_state.assistant_responding}")
        st.write(f"Connection active: {st.session_state.connection is not None}")
        st.write(f"Streaming active: {st.session_state.streaming_active}")
        st.write(f"Audio threads count: {len(st.session_state.audio_threads)}")
        
        # Show thread status
        if st.session_state.audio_threads:
            for i, thread in enumerate(st.session_state.audio_threads):
                st.write(f"Thread {i+1} alive: {thread.is_alive()}")
        
        if st.session_state.messages:
            st.write("Last message:", st.session_state.messages[-1])
    
    # Check if UI refresh is needed (from background threads)
    if ui_refresh_needed.is_set():
        ui_refresh_needed.clear()
        st.rerun()
    
    # Create a container for the chat
    chat_container = st.container()
    with chat_container:
        if st.session_state.messages:
            st.markdown('<div class="chat-container">', unsafe_allow_html=True)
            
            for i, msg in enumerate(st.session_state.messages[-15:]):  # Show last 15 messages
                timestamp = msg.get("timestamp", datetime.now().strftime("%H:%M:%S"))
                
                if msg["type"] == "user":
                    speaking_class = "message-speaking" if st.session_state.user_speaking else ""
                    st.markdown(f'''
                    <div class="message-user {speaking_class}">
                        <div><strong>You</strong></div>
                        <div>{msg["content"]}</div>
                        <div class="message-timestamp">{timestamp}</div>
                    </div>
                    ''', unsafe_allow_html=True)
                    
                elif msg["type"] == "assistant":
                    responding_class = "message-speaking" if st.session_state.assistant_responding else ""
                    st.markdown(f'''
                    <div class="message-assistant {responding_class}">
                        <div><strong>Contoso Assistant</strong></div>
                        <div>{msg["content"]}</div>
                        <div class="message-timestamp">{timestamp}</div>
                    </div>
                    ''', unsafe_allow_html=True)
                    
                elif msg["type"] == "system":
                    st.markdown(f'''
                    <div class="message-system">
                        {msg["content"]}
                    </div>
                    ''', unsafe_allow_html=True)
            
            # Show typing indicator when assistant is thinking
            if st.session_state.assistant_responding and not any(msg["type"] == "assistant" for msg in st.session_state.messages[-1:]):
                st.markdown(f'''
                <div class="message-typing">
                    <div><strong>Contoso Assistant</strong></div>
                    <div>Thinking<span class="typing-dots">...</span></div>
                </div>
                ''', unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Auto-scroll to bottom script
            st.markdown('''
            <script>
            const chatContainer = document.querySelector('.chat-container');
            if (chatContainer) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
            </script>
            ''', unsafe_allow_html=True)
        else:
            st.markdown('''
            <div class="chat-container">
                <div style="text-align: center; color: #6c757d; padding: 50px;">
                    <h4>🎤 Start a conversation</h4>
                    <p>Click the microphone above and start speaking!</p>
                </div>
            </div>
            ''', unsafe_allow_html=True)
    
    # Add dynamic status indicators
    if st.session_state.streaming_active:
        if st.session_state.user_speaking:
            st.markdown('''
            <div style="text-align: center; padding: 10px; background: linear-gradient(135deg, #007bff 0%, #0056b3 100%); color: white; border-radius: 10px; margin: 10px 0; animation: pulse 1s infinite;">
                <strong>🎤 YOU ARE SPEAKING</strong> - I'm listening...
            </div>
            ''', unsafe_allow_html=True)
        elif st.session_state.assistant_responding:
            st.markdown('''
            <div style="text-align: center; padding: 10px; background: linear-gradient(135deg, #28a745 0%, #1e7e34 100%); color: white; border-radius: 10px; margin: 10px 0; animation: pulse 1s infinite;">
                <strong>🤖 ASSISTANT RESPONDING</strong> - Speaking now...
            </div>
            ''', unsafe_allow_html=True)
        else:
            st.markdown('''
            <div style="text-align: center; padding: 10px; background: linear-gradient(135deg, #6f42c1 0%, #563d7c 100%); color: white; border-radius: 10px; margin: 10px 0;">
                <strong>🔴 LIVE</strong> - Ready to listen...
            </div>
            ''', unsafe_allow_html=True)
    else:
        st.markdown('''
        <div style="text-align: center; padding: 10px; background: linear-gradient(135deg, #6c757d 0%, #495057 100%); color: white; border-radius: 10px; margin: 10px 0;">
            <strong>⏸️ OFFLINE</strong> - Click microphone to start
        </div>
        ''', unsafe_allow_html=True)
    
    # Instructions
    with st.expander("ℹ️ How to use"):
        st.markdown("""
        1. **Click the microphone** to start continuous listening
        2. **Speak naturally** - the system will automatically detect when you're talking
        3. The assistant will **respond with both text and audio**
        4. **Audio responses play automatically** through your speakers
        5. **Click the red button** to stop listening
        6. View your **conversation history** below the microphone
        
        **Note:** The microphone stays active continuously once started, just like the original voice agent!
        """)
    
    # Add a periodic refresh to catch UI updates from background threads
    # This JavaScript will check for updates every 1 second
    st.markdown(f"""
    <script>
    // Check if messages have been updated and refresh if needed
    let messageCount = {len(st.session_state.messages) if st.session_state.messages else 0};
    let lastKnownCount = sessionStorage.getItem('lastMessageCount') || 0;
    
    if (messageCount != lastKnownCount) {{
        sessionStorage.setItem('lastMessageCount', messageCount);
        // Small delay to allow message processing to complete
        setTimeout(function() {{
            window.location.reload();
        }}, 500);
    }}
    
    // Set up periodic check for new messages
    setInterval(function() {{
        let currentCount = {len(st.session_state.messages) if st.session_state.messages else 0};
        let storedCount = sessionStorage.getItem('lastMessageCount') || 0;
        if (currentCount != storedCount) {{
            sessionStorage.setItem('lastMessageCount', currentCount);
            window.location.reload();
        }}
    }}, 1000);
    </script>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
