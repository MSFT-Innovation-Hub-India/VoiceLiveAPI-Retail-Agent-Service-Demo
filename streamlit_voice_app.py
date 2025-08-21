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

    stream = sd.InputStream(channels=1, samplerate=AUDIO_SAMPLE_RATE, dtype="int16")
    try:
        stream.start()
        read_size = int(AUDIO_SAMPLE_RATE * 0.02)  # 20ms chunks
        while not stop_event.is_set():
            if stream.read_available >= read_size:
                data, _ = stream.read(read_size)
                audio = base64.b64encode(data).decode("utf-8")
                param = {"type": "input_audio_buffer.append", "audio": audio, "event_id": ""}
                data_json = json.dumps(param)
                connection.send(data_json)
            else:
                time.sleep(0.001)  # Small sleep to prevent busy waiting
    except Exception as e:
        logger.error(f"Audio stream interrupted. {e}")
    finally:
        stream.stop()
        stream.close()
        logger.info("Audio stream closed.")

def receive_audio_and_playback(connection: VoiceLiveConnection) -> None:
    """Continuously receive and play audio from API"""
    last_audio_item_id = None
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

                if event_type == "session.created":
                    session = event.get("session")
                    logger.info(f"Session created: {session.get('id')}")
                    # Update UI
                    if 'messages' not in st.session_state:
                        st.session_state.messages = []
                    st.session_state.messages.append({"type": "system", "content": f"Connected - Session: {session.get('id')}"})

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    user_transcript = event.get("transcript", "")
                    if user_transcript:
                        # Update UI with user input
                        if 'messages' not in st.session_state:
                            st.session_state.messages = []
                        st.session_state.messages.append({"type": "user", "content": user_transcript})
                        logger.info(f"User said: {user_transcript}")

                elif event_type == "response.text.done":
                    agent_text = event.get("text", "")
                    if agent_text:
                        # Update UI with agent response
                        if 'messages' not in st.session_state:
                            st.session_state.messages = []
                        st.session_state.messages.append({"type": "assistant", "content": agent_text})
                        logger.info(f"Agent text: {agent_text}")

                elif event_type == "response.audio_transcript.done":
                    agent_audio = event.get("transcript", "")
                    if agent_audio:
                        logger.info(f"Agent audio transcript: {agent_audio}")

                elif event_type == "response.audio.delta":
                    if event.get("item_id") != last_audio_item_id:
                        last_audio_item_id = event.get("item_id")

                    bytes_data = base64.b64decode(event.get("delta", ""))
                    if bytes_data:
                        logger.debug(f"Received audio data of length: {len(bytes_data)}")
                        audio_player.add_data(bytes_data)

                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("Speech started - stopping playback")
                    audio_player.stop()

                elif event_type == "error":
                    error_details = event.get("error", {})
                    error_type = error_details.get("type", "Unknown")
                    error_code = error_details.get("code", "Unknown")
                    error_message = error_details.get("message", "No message provided")
                    logger.error(f"API Error: Type={error_type}, Code={error_code}, Message={error_message}")
                    if 'messages' not in st.session_state:
                        st.session_state.messages = []
                    st.session_state.messages.append({"type": "error", "content": f"Error: {error_message}"})

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON event: {e}")
                continue

    except Exception as e:
        logger.error(f"Error in audio playback: {e}")
    finally:
        audio_player.terminate()
        logger.info("Playback done.")

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
    .message-user {
        background-color: #e3f2fd;
        padding: 10px;
        border-radius: 10px;
        margin: 5px 0;
        border-left: 4px solid #2196F3;
    }
    .message-assistant {
        background-color: #f3e5f5;
        padding: 10px;
        border-radius: 10px;
        margin: 5px 0;
        border-left: 4px solid #9C27B0;
    }
    .message-system {
        background-color: #f5f5f5;
        padding: 5px;
        border-radius: 5px;
        margin: 3px 0;
        font-style: italic;
        color: #666;
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
    
    # Display messages
    if st.session_state.messages:
        st.markdown("### 💬 Conversation")
        for msg in st.session_state.messages[-20:]:  # Show last 20 messages
            if msg["type"] == "user":
                st.markdown(f'<div class="message-user"><strong>You:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
            elif msg["type"] == "assistant":
                st.markdown(f'<div class="message-assistant"><strong>Assistant:</strong> {msg["content"]}</div>', unsafe_allow_html=True)
            elif msg["type"] == "system":
                st.markdown(f'<div class="message-system">{msg["content"]}</div>', unsafe_allow_html=True)
    
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

if __name__ == "__main__":
    main()
