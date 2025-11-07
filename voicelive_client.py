"""
Azure Voice Live API - Agent-Based Client

This module provides a client for interacting with Azure Voice Live API through an 
existing Agent configured in Azure AI Foundry Service.

Key Features:
- Integration with pre-configured Azure AI Foundry Agent
- No function calling implementation required (Agent handles autonomously)
- Azure Fast Transcript for speech-to-text conversion
- GPT-4o-mini model integration via Azure AI Foundry Agent
- Azure TTS with neural voices supporting Indic languages
- Multi-language support (en-IN and hi-IN) for transcript generation

Architecture:
- Azure Voice Live API connects to Azure AI Foundry Agent
- Azure Fast Transcript processes user speech and sends transcripts to GPT-4o-mini
- Agent autonomously invokes tool actions without client-side function calling
- Azure TTS service with Azure Speech Services generates response audio
- Neural voice (en-IN-AartiIndicNeural) optimized for Indic language support

Language Support:
- English (India) - en-IN
- Hindi (India) - hi-IN
- Neural voice technology for natural-sounding speech synthesis

Usage:
This client is designed for scenarios where you have an existing Agent in Azure AI Foundry
and want to leverage its autonomous capabilities. The Agent handles all tool invocations
and business logic, making this ideal for enterprise scenarios with pre-configured workflows.

Note: GPT-Realtime cannot be used with this approach as the Agent architecture requires
the transcript-based interaction model through Azure Fast Transcript.

Author: Microsoft Innovation Hub India
Version: 1.0
"""

from utils import array_buffer_to_base64, base64_to_array_buffer
import traceback
import inspect
import numpy as np
from chainlit.logger import logger
import json
import datetime
import asyncio
from collections import defaultdict
from azure.identity import DefaultAzureCredential
import uuid
import os
from dotenv import load_dotenv
import websockets

# Load environment variables
load_dotenv("./.env", override=True)

# Configuration: Required environment variables for Azure AI Foundry Agent integration
# AZURE_VOICE_LIVE_ENDPOINT: The endpoint URL for Azure Voice Live API
# AZURE_VOICE_LIVE_API_VERSION: API version (default: 2025-05-01-preview)
# AI_FOUNDRY_PROJECT_NAME: Azure AI Foundry project name containing the Agent
# AI_FOUNDRY_AGENT_ID: Unique identifier for the configured Agent in Azure AI Foundry
endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
project_name = os.getenv("AI_FOUNDRY_PROJECT_NAME")
agent_id = os.getenv("AI_FOUNDRY_AGENT_ID")


class VoiceLiveClient:
    """
    Azure Voice Live API Client for Azure AI Foundry Agent Integration
    
    This client facilitates communication with Azure Voice Live API through an existing
    Azure AI Foundry Agent. It handles real-time voice conversations with automatic
    transcript generation and neural voice synthesis.
    
    Key Capabilities:
    - Azure Fast Transcript integration for speech-to-text
    - GPT-4o-mini model interaction via Azure AI Foundry Agent
    - Neural voice synthesis with Indic language support
    - Semantic voice activity detection (VAD)
    - Echo cancellation and noise reduction
    - Multi-language support (en-IN, hi-IN)
    
    Configuration:
    - Input audio sampling rate: 24kHz
    - Voice: en-IN-AartiIndicNeural (Azure Standard neural voice)
    - Turn detection: Azure Semantic VAD with configurable thresholds
    - Audio processing: Deep noise suppression and server echo cancellation
    
    Best Practices:
    - Ensure proper environment variables are configured
    - Handle WebSocket connection lifecycle appropriately
    - Implement proper error handling for network interruptions
    - Monitor audio quality and adjust sampling rates if needed
    """

    def __init__(self):
        self.ws = None
        self.event_handlers = defaultdict(list)
        self.session_config = {
            "input_audio_sampling_rate": 24000,
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.3,
                "prefix_padding_ms": 200,
                "silence_duration_ms": 200,
                "remove_filler_words": False,
                "interrupt_response": True,
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.01,
                    "timeout": 2,
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": "en-IN-Meera:DragonHDV2.3Neural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
            "input_audio_transcription": {"model": "azure-speech", "language": "en-IN, hi-IN"},
        }
        self.response_config = {"modalities": ["text", "audio"]}

    def on(self, event_name, handler):
        self.event_handlers[event_name].append(handler)

    def dispatch(self, event_name, event):
        """Dispatches an event to all registered handlers for the given event name.
        In this case, this dispatcher is used to notify the Chainlit UI of events it should know of
        to take actions in the UI"""
        for handler in self.event_handlers[event_name]:
            if inspect.iscoroutinefunction(handler):
                asyncio.create_task(handler(event))
            else:
                handler(event)

    def is_connected(self):
        return self.ws is not None

    def log(self, *args):
        logger.debug(f"[Websocket/{datetime.datetime.utcnow().isoformat()}]", *args)

    def get_azure_token(self) -> str:
        """Get Azure access token using DefaultAzureCredential."""
        try:
            credential = DefaultAzureCredential()
            scopes = "https://ai.azure.com/.default"
            token = credential.get_token(scopes)
            return token.token
        except Exception as e:
            logger.error(f"Failed to get Azure token: {e}")
            raise

    def get_websocket_url(self, access_token: str) -> str:
        """Generate WebSocket URL for Voice Live API."""
        azure_ws_endpoint = endpoint.rstrip("/").replace("https://", "wss://")
        return (
            f"{azure_ws_endpoint}/voice-live/realtime?api-version={api_version}"
            f"&agent-project-name={project_name}&agent-id={agent_id}"
            f"&agent-access-token={access_token}"
        )

    async def connect(self):
        """Connects the client using a WS Connection to the Realtime API."""
        if self.is_connected():
            # raise Exception("Already connected")
            self.log("Already connected")  # Get access token

        access_token = self.get_azure_token()
        # Build WebSocket URL and headers
        ws_url = self.get_websocket_url(access_token)
        self.ws = await websockets.connect(
            ws_url,
            additional_headers={
                "Authorization": f"Bearer {self.get_azure_token()}",
                "x-ms-client-request-id": str(uuid.uuid4()),
            },
        )
        print(f"Connected to Azure Voice Live API....")
        asyncio.create_task(self.receive())

        await self.update_session()

    async def disconnect(self):
        """Disconnects the client from the WS Connection to the Voice Live API."""
        if self.ws:
            await self.ws.close()
            self.ws = None
            self.log(f"Disconnected from the Voice Live API")

    def _generate_id(self, prefix):
        return f"{prefix}{int(datetime.datetime.utcnow().timestamp() * 1000)}"

    async def send(self, event_name, data=None):
        """
        Sends an event to the Voice Live API over the websocket connection.
        """
        if not self.is_connected():
            raise Exception("Voice Live API is not connected")
        data = data or {}
        if not isinstance(data, dict):
            raise Exception("data must be a dictionary")
        event = {"event_id": self._generate_id("evt_"), "type": event_name, **data}
        await self.ws.send(json.dumps(event))

    async def send_user_message_content(self, content=[]):
        """
        When the user types in the query in the chat window, it is sent to the server to elicit a response
        First a conversation.item.create event is sent, followed up with a response.create event to signal the server to respond
        """
        if content:
            await self.send(
                "conversation.item.create",
                {
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": content,
                    }
                },
            )
            # this is the trigger to the server to start responding to the user query
            await self.send("response.create", {"response": self.response_config})

            # raise this event to the UI to pause the audio playback, in case it is doing so already,
            # when the user submits a query in the chat interface
            _event = {"type": "conversation_interrupted"}
            # signal the UI to stop playing audio
            self.dispatch("conversation.message.interrupted", _event)

    async def update_session(self):
        """
        Asynchronously updates the session configuration if the client is connected. These include aspects like voice activate detection, function calls, etc.
        """
        if self.is_connected():
            await self.send("session.update", {"session": self.session_config})
            print("session updated...")

    async def receive(self):
        """Asynchronously receives and processes messages from the WebSocket connection.
        This function listens for incoming messages from the WebSocket connection (`self.ws`),
        decodes the JSON-encoded messages, and processes them based on their event type.
        It handles various event types such as errors, audio responses, speech detection,
        and function call responses.
        """
        async for message in self.ws:
            event = json.loads(message)
            # print("event_type", event["type"])
            if event["type"] == "error":
                # print("Some error !!", message)
                pass
            if event["type"] == "response.audio.delta":
                # response audio delta events received from server that need to be relayed
                # to the UI for playback
                delta = event["delta"]
                array_buffer = base64_to_array_buffer(delta)
                append_values = array_buffer.tobytes()
                _event = {"audio": append_values}
                # print(f"🎵 Audio chunk received: {len(append_values)} bytes")
                # send event to chainlit UI to play this audio
                self.dispatch("conversation.updated", _event)
            elif event["type"] == "response.audio.done":
                # server has finished sending back the audio response to the user query
                # let the chainlit UI know that the response audio has been completely received
                self.dispatch("conversation.updated", event)
            elif event["type"] == "input_audio_buffer.committed":
                # user has stopped speaking. The audio delta input from the user captured till now should now be processed by the server.
                # Hence we need to send a 'response.create' event to signal the server to respond
                await self.send("response.create", {"response": self.response_config})
            elif event["type"] == "input_audio_buffer.speech_started":
                # The server has detected speech input from the user. Hence use this event to signal the UI to stop playing any audio if playing one
                # Also trigger creation of user message placeholder
                print("conversation interrupted through new audio input .......")
                _event = {"type": "conversation_interrupted"}
                # signal the UI to stop playing audio
                self.dispatch("conversation.interrupted", _event)

                # Signal that user started speaking to create placeholder
                _speech_event = {"type": "user_speech_started"}
                # self.dispatch("user.speech.started", _speech_event)
            elif event["type"] == "input_audio_buffer.speech_stopped":
                # User stopped speaking - can update placeholder to show processing
                _speech_event = {"type": "user_speech_stopped"}
                # self.dispatch("user.speech.stopped", _speech_event)
            elif event["type"] == "response.audio_transcript.delta":
                # this event is received when the transcript of the server's audio response to the user has started to come in.
                # send this to the UI to display the transcript in the chat window, even as the audio of the response gets played
                delta = event["delta"]
                item_id = event["item_id"]
                _event = {"transcript": delta, "item_id": item_id}
                # signal the UI to display the transcript of the response audio in the chat window
                self.dispatch("conversation.text.delta", _event)
            elif (
                event["type"] == "conversation.item.input_audio_transcription.completed"
            ):
                # this event is received when the transcript of the user's query (i.e. input audio) has been completed.
                # Since this happens asynchronous to the respond audio transcription, the sequence of the two in the chat window
                # would not necessarily be correct all the time
                user_query_transcript = event["transcript"]
                _event = {"transcript": user_query_transcript}
                self.dispatch("conversation.input.text.done", _event)
            elif event["type"] == "response.done":
                # when a user request entails a function call, response.done does not return an audio
                # It instead returns the functions that match the intent, along with the arguments to invoke it
                # checking for function call hints in the response
                print("response done received...")
            else:
                # print("Unknown event type:", event.get("type"))
                pass

    async def close(self):
        await self.ws.close()

    async def append_input_audio(self, array_buffer):
        """
        Appends the provided audio data to the input audio buffer that is sent to the server. We are not asking the server to start responding yet.
        This function takes an array buffer containing audio data, converts it to a base64 encoded string,
        and sends it to the input audio buffer for further processing.

        Note that the server will not start responding just because we sent this audio buffer
        It will do so only when it receives an event 'response.create' from the client
        """
        # Check if the array buffer is not empty and send the audio data to the input buffer
        if len(array_buffer) > 0:
            await self.send(
                "input_audio_buffer.append",
                {
                    "audio": array_buffer_to_base64(np.array(array_buffer)),
                },
            )

    async def clear_input_audio_buffer(self):
        """
        Clears the input audio buffer on the server side.
        This is useful when conversation is interrupted to ensure fresh state.
        """
        if self.is_connected():
            await self.send("input_audio_buffer.clear")
            print("Input audio buffer cleared")
