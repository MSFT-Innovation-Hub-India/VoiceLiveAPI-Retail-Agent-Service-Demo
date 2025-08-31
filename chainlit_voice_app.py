import chainlit as cl
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
import asyncio
from collections import deque
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from typing import Dict, Union, Literal, Set
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

class VoiceLiveConnection:
    def __init__(self, url: str, headers: dict) -> None:
        self._url = url
        self._headers = headers
        self._websocket: WebSocketApp = None
        self._connected = False

    def connect(self, on_message=None, on_error=None, on_close=None, on_open=None):
        """Connect to the WebSocket"""
        # Wrap callbacks to keep internal connection state in sync
        def _on_open(ws):
            self._connected = True
            if on_open:
                on_open(ws)

        def _on_close(ws, close_status_code, close_msg):
            self._connected = False
            if on_close:
                on_close(ws, close_status_code, close_msg)

        self._websocket = WebSocketApp(
            self._url,
            header=self._headers,
            on_open=_on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=_on_close
        )
        
        # Run in a separate thread
        def run():
            self._websocket.run_forever()
        
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return thread

    def send_message(self, message: dict):
        """Send a message through the WebSocket"""
        if self._websocket and self._connected:
            self._websocket.send(json.dumps(message))
        else:
            logger.warning("WebSocket not connected. Cannot send message.")

    def close(self):
        """Close the WebSocket connection"""
        self._connected = False
        if self._websocket:
            self._websocket.close()

class AudioPlayerAsync:
    def __init__(self, sample_rate=AUDIO_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.player_thread = None
        
    def start(self):
        """Start the audio player thread"""
        if not self.is_playing:
            self.is_playing = True
            self.player_thread = threading.Thread(target=self._player_worker)
            self.player_thread.daemon = True
            self.player_thread.start()
    
    def stop(self):
        """Stop the audio player"""
        self.is_playing = False
        if self.player_thread:
            self.player_thread.join(timeout=1)
    
    def add_audio_chunk(self, audio_data: bytes):
        """Add audio data to the playback queue"""
        if self.is_playing:
            self.audio_queue.put(audio_data)

    def clear(self):
        """Clear any pending audio from the queue without stopping the player"""
        try:
            while not self.audio_queue.empty():
                self.audio_queue.get_nowait()
        except Exception:
            pass
    
    def _player_worker(self):
        """Worker thread that plays audio from the queue"""
        stream = None
        try:
            # Initialize the audio output stream
            stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.int16,
                callback=None,
                blocksize=1024
            )
            stream.start()
            
            while self.is_playing:
                try:
                    # Get audio data from queue (with timeout to allow checking stop condition)
                    audio_data = self.audio_queue.get(timeout=0.1)
                    
                    # Convert to numpy array and play
                    if audio_data:
                        # Decode base64 audio data
                        audio_bytes = base64.b64decode(audio_data)
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                        
                        # Play the audio
                        stream.write(audio_array)
                        
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error playing audio: {e}")
                    
        except Exception as e:
            logger.error(f"Error initializing audio stream: {e}")
        finally:
            if stream:
                stream.stop()
                stream.close()

# Global audio player
audio_player = AudioPlayerAsync()

# Global message queue for WebSocket messages
ws_message_queue = queue.Queue()

# Global connection state
connection_state = {
    'connected': False,
    'session_ready': False,
    'streaming': False,
    'user_speaking': False,
    'assistant_responding': False,
    'connection': None,
    'audio_thread': None
}

# Simple visual prefixes to differentiate roles in chat
USER_PREFIX = "🧑‍💬"
ASSISTANT_PREFIX = "🤖"

def get_azure_token():
    """Get Azure token using managed identity or CLI credentials"""
    try:
        # Try using DefaultAzureCredential
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        return token.token
    except Exception as e:
        logger.warning(f"Could not get Azure token: {e}")
        # Fallback to API key
        return None

async def handle_websocket_message(message_data: str):
    """Handle incoming WebSocket messages and send to Chainlit"""
    try:
        event = json.loads(message_data)
        event_type = event.get("type")

        print(f"[DEBUG] Received event: {event_type}")

        if event_type == "session.created":
            await cl.Message(content="🔗 **Connected to Azure Voice Live API**", author="System").send()
            connection_state['connected'] = True
            connection_state['session_ready'] = True
            # Start audio capture now that the session is ready
            if connection_state.get('streaming') and not (
                connection_state.get('audio_thread') and connection_state['audio_thread'].is_alive()
            ):
                t = threading.Thread(target=listen_and_send_audio, daemon=True)
                connection_state['audio_thread'] = t
                t.start()

        elif event_type == "session.updated":
            print("[DEBUG] Session updated")

        elif event_type == "input_audio_buffer.speech_started":
            print("[DEBUG] Speech started detected")
            # Only update and notify if not already speaking
            if not connection_state.get('user_speaking'):
                connection_state['user_speaking'] = True
                await cl.Message(content="🎤 **Listening...**", author="System").send()
            # Cancel any ongoing response to prevent overlap and clear pending audio
            try:
                if connection_state.get('connection'):
                    connection_state['connection'].send_message({
                        "type": "response.cancel",
                        "event_id": str(uuid.uuid4())
                    })
                audio_player.clear()
            except Exception as e:
                logger.debug(f"Failed to cancel response: {e}")

        elif event_type == "input_audio_buffer.speech_stopped":
            print("[DEBUG] Speech stopped detected")
            connection_state['user_speaking'] = False

        elif event_type == "input_audio_buffer.committed":
            print("[DEBUG] Audio buffer committed")

        elif event_type == "conversation.item.input_audio_transcription.completed":
            user_transcript = event.get("transcript", "")
            if user_transcript:
                print(f"[DEBUG] User transcript received: {user_transcript}")
                # Use Chainlit's native user-styled bubble so it matches typed messages
                try:
                    await cl.Message(content=user_transcript, author="user", type="user_message").send()
                except Exception:
                    await cl.Message(content=user_transcript, author="user").send()
                connection_state['user_speaking'] = False
                logger.info(f"User said: {user_transcript}")

        elif event_type == "response.created":
            current_response_id = event.get("response", {}).get("id")
            logger.info(f"New response created: {current_response_id}")
            print(f"[DEBUG] Response created: {current_response_id}")
            connection_state['assistant_responding'] = True
            cl.user_session.set("current_assistant_response", "")

        elif event_type in ("response.audio_transcript.delta", "response.output_text.delta", "response.text.delta"):
            delta = event.get("delta", "")
            if delta:
                current_response = cl.user_session.get("current_assistant_response", "")
                cl.user_session.set("current_assistant_response", current_response + delta)
                print(f"[DEBUG] Assistant delta: {delta}")

        elif event_type in ("response.audio_transcript.done", "response.output_text.done", "response.text.done"):
            complete_response = cl.user_session.get("current_assistant_response", "")
            if complete_response:
                print(f"[DEBUG] Assistant complete response: {complete_response}")
                await cl.Message(content=f"{ASSISTANT_PREFIX} {complete_response}").send()
            cl.user_session.set("current_assistant_response", "")
            connection_state['assistant_responding'] = False

        elif event_type == "response.audio.delta":
            audio_data = event.get("delta")
            if audio_data and connection_state.get('assistant_responding', False):
                audio_player.add_audio_chunk(audio_data)

        elif event_type == "response.audio.done":
            print("[DEBUG] Assistant audio done")
            connection_state['assistant_responding'] = False

        elif event_type in [
            "response.content_part.added",
            "response.output_item.added",
            "conversation.item.created",
            "response.content_part.done",
            "response.output_item.done",
            "response.done",
        ]:
            print(f"[DEBUG] {event_type}")
            if event_type == "response.done":
                connection_state['assistant_responding'] = False

        elif event_type == "error":
            err = event.get("error") or event
            logger.warning(f"Voice Live error event: {err}")
            print(f"[DEBUG] Error event: {json.dumps(err)[:500]}")
            connection_state['assistant_responding'] = False

        else:
            print(f"[DEBUG] Unhandled event type: {event_type}")

    except Exception as e:
        logger.error(f"Error handling WebSocket message: {e}")
        logger.error(traceback.format_exc())

def on_open(ws):
    """Called when WebSocket connection is opened"""
    print("[DEBUG] WebSocket connection opened")
    connection_state['connected'] = True
    print("[DEBUG] Connection established successfully")
    
    # Add a small delay to ensure SSL handshake is complete
    def send_session_config():
        try:
            import time
            time.sleep(0.1)  # Small delay to ensure connection is fully established
            
            session_update = {
                "type": "session.update",
                "session": {
                    "input_audio_sampling_rate": 24000,
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
            ws.send(json.dumps(session_update))
            print("[DEBUG] Session configuration sent successfully")
        except Exception as e:
            print(f"[DEBUG] Failed to send session config: {e}")
    
    # Send session config in a separate thread with a small delay
    threading.Thread(target=send_session_config, daemon=True).start()

def on_message(ws, message):
    """Called when a WebSocket message is received"""
    try:
        # Store message in queue to be processed by main thread
        ws_message_queue.put(message)
    except Exception as e:
        logger.error(f"Error in on_message: {e}")
        logger.error(traceback.format_exc())

def on_error(ws, error):
    """Called when a WebSocket error occurs"""
    logger.error(f"WebSocket error: {error}")
    connection_state['connected'] = False
    connection_state['session_ready'] = False
    print(f"[DEBUG] WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    """Called when WebSocket connection is closed"""
    print(f"[DEBUG] WebSocket connection closed: {close_status_code} - {close_msg}")
    connection_state['connected'] = False
    connection_state['session_ready'] = False
    connection_state['streaming'] = False

def listen_and_send_audio():
    """Capture audio from microphone and send to WebSocket"""
    print("[DEBUG] Starting audio capture thread")
    print(f"[DEBUG] Connection state: {connection_state}")
    # Wait until connected if not yet
    wait_attempts = 0
    while not connection_state.get('connected', False) and connection_state.get('streaming', False) and not stop_event.is_set():
        time.sleep(0.1)
        wait_attempts += 1
        if wait_attempts % 10 == 0:
            print("[DEBUG] Waiting for connection to become ready...")
    
    def audio_callback(indata, frames, time, status):
        if status:
            print(f"[DEBUG] Audio callback status: {status}")
        
        if connection_state['connected'] and connection_state['streaming']:
            # Convert audio to the format expected by Azure Voice Live API
            audio_data = (indata[:, 0] * 32767).astype(np.int16)
            audio_b64 = base64.b64encode(audio_data.tobytes()).decode('utf-8')
            
            # Send audio data via WebSocket
            audio_message = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64
            }
            
            if connection_state['connection']:
                connection_state['connection'].send_message(audio_message)

    # We rely on server-side VAD; no periodic commits
    
    # Start audio input stream
    try:
        with sd.InputStream(
            callback=audio_callback,
            channels=1,
            samplerate=AUDIO_SAMPLE_RATE,
            dtype=np.float32,
            blocksize=1024
        ):
            print("[DEBUG] Audio input stream started")
            while connection_state['streaming'] and not stop_event.is_set():
                time.sleep(0.1)
            print("[DEBUG] Audio input stream stopped")
    except Exception as e:
        logger.error(f"Error in audio capture: {e}")

async def process_websocket_messages():
    """Background task to process WebSocket messages from queue"""
    while True:
        try:
            # Check for new messages in the queue (non-blocking)
            try:
                message = ws_message_queue.get_nowait()
                await handle_websocket_message(message)
            except queue.Empty:
                # No messages, wait a bit
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error processing WebSocket messages: {e}")
            await asyncio.sleep(1)

@cl.on_chat_start
async def start():
    """Called when a new chat session starts"""
    await cl.Message(content="🎤 **Welcome to Contoso Retail Assistant!**\n\nUse the mic button to talk, or type a message below.", author="System").send()
    # Show controls
    await send_controls()
    
    # Initialize session variables
    cl.user_session.set("current_assistant_response", "")
    # Clear any previous stop signal
    stop_event.clear()
    
    # Start audio player (but not audio capture yet)
    audio_player.start()
    
    # Start background task to process WebSocket messages
    cl.user_session.set("message_processor", asyncio.create_task(process_websocket_messages()))

@cl.on_message
async def main(message: cl.Message):
    """Handle text messages"""
    # Note: Avoid updating the incoming message content for compatibility with current Chainlit version
    # If right-aligned styling is needed, render a separate mirrored message or use global CSS instead.
    # If agent is speaking, interrupt before processing new input
    if connection_state.get('connected'):
        await interrupt_assistant()
    # Ensure connection (no audio unless mic is started)
    await ensure_connection(start_audio=False)
    # Send typed text to the agent and request a response
    await send_text_to_agent(message.content)

async def start_voice_conversation():
    """Start voice conversation"""
    if connection_state['connected']:
        await cl.Message(content="🔴 **Voice conversation already active!**", author="System").send()
        return
    
    await cl.Message(content="🔄 **Connecting to Azure Voice Live API...**", author="System").send()
    
    try:
        # Ensure any previous stop signal is cleared before starting audio
        stop_event.clear()
        # Ensure connected and start audio streaming
        await ensure_connection(start_audio=True)
        # If already connected, ensure the mic thread is running
        if connection_state.get('connected'):
            connection_state['streaming'] = True
            if not (connection_state.get('audio_thread') and connection_state['audio_thread'].is_alive()):
                t = threading.Thread(target=listen_and_send_audio, daemon=True)
                connection_state['audio_thread'] = t
                t.start()
        
        await cl.Message(content="✅ **Voice conversation started!** Speak naturally and the assistant will respond.", author="System").send()
        await send_controls()
        
    except Exception as e:
        await cl.Message(content=f"❌ **Error starting voice conversation**: {str(e)}", author="System").send()
        logger.error(f"Error starting voice conversation: {e}")

async def stop_voice_conversation():
    """Stop voice conversation"""
    connection_state['streaming'] = False
    connection_state['connected'] = False
    
    if connection_state['connection']:
        connection_state['connection'].close()
        connection_state['connection'] = None
    
    stop_event.set()
    
    await cl.Message(content="🛑 **Voice conversation stopped.**", author="System").send()
    await send_controls()

@cl.action_callback("start_voice")
async def start_voice(action: cl.Action):
    """Start voice conversation"""
    if connection_state['connected']:
        await cl.Message(content="🔴 **Voice conversation already active!**", author="System").send()
        return
    await start_voice_conversation()

@cl.action_callback("stop_voice")
async def stop_voice(action: cl.Action):
    if not connection_state['connected']:
        await cl.Message(content="ℹ️ Voice is not active.", author="System").send()
        return
    await stop_voice_conversation()
@cl.on_chat_end
async def end():
    """Called when chat session ends"""
    # Clean up resources
    connection_state['streaming'] = False
    connection_state['connected'] = False
    
    # Cancel the message processor task
    message_processor = cl.user_session.get("message_processor")
    if message_processor:
        message_processor.cancel()
    
    if connection_state['connection']:
        connection_state['connection'].close()
        connection_state['connection'] = None
    
    audio_player.stop()
    stop_event.set()

if __name__ == "__main__":
    # Run the Chainlit app
    cl.run()

# ---------- Helpers ----------
async def interrupt_assistant():
    """Cancel any ongoing assistant response and clear playback."""
    try:
        if connection_state.get('connection') and connection_state.get('connected'):
            connection_state['connection'].send_message({
                "type": "response.cancel",
                "event_id": str(uuid.uuid4()),
            })
        audio_player.clear()
        connection_state['assistant_responding'] = False
        cl.user_session.set("current_assistant_response", "")
    except Exception as e:
        logger.debug(f"Interrupt failed: {e}")
async def send_controls():
    """Render mic controls as buttons."""
    start_disabled = connection_state.get('connected', False)
    stop_disabled = not connection_state.get('connected', False)
    actions = [
    cl.Action(name="start_voice", label="🎙 Start Voice", payload={"action": "start"}, disabled=start_disabled),
    cl.Action(name="stop_voice", label="🛑 Stop Voice", payload={"action": "stop"}, disabled=stop_disabled),
    ]
    await cl.Message(content="", author="System", actions=actions).send()

async def ensure_connection(start_audio: bool):
    """Ensure a websocket connection exists. Optionally enable audio streaming."""
    if connection_state.get('connection') and connection_state.get('connected'):
        # Don't disable streaming on typed messages; only enable when requested
        if start_audio:
            connection_state['streaming'] = True
        return

    # Get Azure configuration
    endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
    api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
    project_name = os.getenv("AI_FOUNDRY_PROJECT_NAME")
    agent_id = os.getenv("AI_FOUNDRY_AGENT_ID")

    if not all([endpoint, project_name, agent_id]):
        await cl.Message(content="❌ **Error**: Missing Azure configuration. Please check your environment variables.", author="System").send()
        return

    # Acquire AAD token
    credential = DefaultAzureCredential()
    scopes = "https://ai.azure.com/.default"
    token = credential.get_token(scopes)

    azure_ws_endpoint = endpoint.rstrip('/').replace("https://", "wss://")
    agent_access_token = token.token
    ws_url = (
        f"{azure_ws_endpoint}/voice-live/realtime?api-version={api_version}"
        f"&agent-project-name={project_name}&agent-id={agent_id}&agent-access-token={agent_access_token}"
    )
    headers = {
        "Authorization": f"Bearer {token.token}",
        "x-ms-client-request-id": str(uuid.uuid4()),
    }

    connection = VoiceLiveConnection(ws_url, headers)
    connection_state['connection'] = connection

    # Start WebSocket connection
    connection.connect(
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Wait a bit for session to be created
    for _ in range(50):  # ~5s
        if connection_state.get('connected'):
            break
        await asyncio.sleep(0.1)

    # Flag whether to stream audio; the session.created handler will start mic if True
    connection_state['streaming'] = start_audio

async def send_text_to_agent(text: str):
    """Send a typed text message to the Voice Live session and request a response."""
    if not (connection_state.get('connection') and connection_state.get('connected')):
        return
    # Wait briefly for session to be fully ready (after session.created)
    if not connection_state.get('session_ready'):
        # Give it up to ~2s to become ready
        for _ in range(20):
            if connection_state.get('session_ready'):
                break
            await asyncio.sleep(0.1)
        if not connection_state.get('session_ready'):
            await cl.Message(content="⏳ Still connecting… please try again in a moment.", author="System").send()
            return

    # Create a conversation item with user text, then request a response
    try:
        connection_state['connection'].send_message({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text}
                ]
            }
        })
        connection_state['connection'].send_message({"type": "response.create"})
    except Exception as e:
        logger.error(f"Failed to send typed message: {e}")
