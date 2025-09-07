"""
Azure Voice Live API - Direct Model Client

This module provides a client for interacting directly with Azure Voice Live API using a 
GPT-Realtime model without going through an Agent in Azure AI Foundry Service.

Key Features:
- Direct interaction with Azure Voice Live API and GPT-Realtime model
- Speech-to-Speech capabilities using GPT-Realtime for real-time audio processing
- Function calling implementation to invoke tool actions autonomously
- Azure Speech Voice integration for Text-to-Speech (TTS) synthesis
- Real-time audio streaming and processing
- WebSocket-based communication for low-latency interactions

Architecture:
- Uses GPT-Realtime model for immediate speech-to-speech conversion
- Implements function calling to execute tool actions (product search, orders, etc.)
- Leverages Azure Speech Services for high-quality voice synthesis
- Direct API integration without Azure AI Foundry Agent dependencies

Usage:
This client is designed for scenarios where you need direct control over the 
conversation flow and want to implement custom function calling logic.
It's ideal for real-time voice applications requiring immediate responses.

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
from tools import available_functions, tools_list

# Load environment variables
load_dotenv("./.env", override=True)

# Configuration: Required environment variables for Azure Voice Live API
endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
agent_id = os.getenv("VOICE_LIVE_MODEL")


system_instructions= """
You are an AI Agent tasked with responding to questions from the customers of Contoso retail fashions regarding their shopping requirements. 
When the customer starts the conversation with a greeting, reciprocate as you respond to their queries. 
Refer to the context provided to you from the Contoso retail knowledge base to respond to their queries.
**DO NOT RESPOND BASED ON YOUR PERSONAL OPINIONS OR EXPERIENCES**
You do not have to say anything about files uploaded, etc to the user.
You have access to the following tools and knowledge. Use these to get context to respond to the user queries:
- API to search for products by category
    - These are the distinct category names for which sample data is available in the Contoso eCom APIs:
        > Apparel, Garments, Winter wear, Stockings, Active wear, Swim wear, Formal wear, Accessories
    - When the user query provides a category name in the request and asks for products in that category, the category name you pass to the API must be one of the above
    - If the user asks you the available categories, provide them the above list.
    - When you get the product search results, present them to the user in a numbered list format, with product name, price and description
- API to order a product based on product id and quantity
    - Always reconfirm with all the particulars (product details, quantities, prices, total amount) before creating the order. Wait for explicit user confirmation before proceeding.
    - In your response to the user, provide the order details including order id, product details, quantities, prices, total amount in a numbered list format
- APIs to create shipment orders
    - When creating the shipment order, always seek confirmation from the user about the destination address provided before creating it. Repeat back the complete address and ask for confirmation before proceeding.
    - When you get the shipment order response, provide the user with all the shipment order details in a numbered list format
- Search tool to perform a QnA on general Contoso retail policies, procedures and QnA
- APIs to analyze call logs.
    - When the user indicates there are no more questions, and wants to end the conversation, you must implicitly call this API to log the entire conversation for analysis. The user does not need to know you are doing this.
    - **You do not need to send the results of the analysis back to the user. You could just say the conversation has been logged for analysis.**
    - The data you send this API for call log analysis should be the full conversation between the customer and you and should be like:
        ###### Example Conversation History ###### 
        {
        "conversation": [
            {
            "role": "user",
            "message": "user input"
            },
            {
            "role": "assistant",
            "message": "agent response"
            },

            {
            "role": "user",
            "message": "user next question?"
            },
            {
            "role": "assistant",
            "message": "agent's next response "
            },
        ..... and so on ..... 
            ]
        }
        ###### End Example Conversation History###### 

Important confirmation requirements:
**Empathize with the customer when you respond**
"""

class VoiceLiveModelClient:
    """
    Azure Voice Live API Client for Direct GPT-Realtime Model Integration
    
    This client provides direct integration with Azure Voice Live API using GPT-Realtime
    model for immediate speech-to-speech conversational experiences. It implements
    function calling capabilities to execute tool actions autonomously.
    
    Key Capabilities:
    - Direct GPT-Realtime model integration for speech-to-speech processing
    - Function calling implementation for tool action execution
    - Azure Speech Voice integration for high-quality TTS
    - Real-time audio streaming with low latency
    - Server-side voice activity detection (VAD)
    - Custom system instructions and conversation management
    
    Function Calling:
    - Automatically invokes available tools based on conversation context
    - Supports product search, order management, and customer service functions
    - Implements dynamic function execution with parameter validation
    - Handles tool responses and integrates them into conversation flow
    
    Audio Configuration:
    - Input audio sampling rate: 24kHz
    - Server VAD with configurable threshold and timing
    - Echo cancellation and noise reduction
    - Azure Speech Services for TTS synthesis
    
    Best Practices:
    - Ensure all required tools are properly imported and configured
    - Implement proper error handling for function execution
    - Monitor token usage and conversation context length
    - Handle WebSocket connection lifecycle and reconnection logic
    - Validate function parameters before execution
    """

    def __init__(self):
        self.ws = None
        self.event_handlers = defaultdict(list)
        self.session_config = {
            "input_audio_sampling_rate": 24000,
            "instructions": system_instructions,
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            },
            "tools": tools_list,
            "tool_choice": "auto",
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": "en-IN-AartiIndicNeural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
            "input_audio_transcription": {"model": "whisper-1"},
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
            f"&model={agent_id}"
            f"&agent-access-token={access_token}"
        )

    async def connect(self):
        """
        Establishes WebSocket connection to Azure Voice Live API for GPT-Realtime model.
        
        This method:
        - Obtains Azure authentication token using DefaultAzureCredential
        - Constructs WebSocket URL with proper API version and model parameters
        - Establishes secure WebSocket connection with authentication headers
        - Prepares the client for real-time audio communication
        
        Raises:
            Exception: If connection fails or authentication is invalid
            
        Best Practices:
        - Ensure proper Azure credentials are configured
        - Handle connection timeouts and retries
        - Monitor connection health for production deployments
        """
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
        """
        Asynchronously receives and processes messages from the WebSocket connection.
        
        This is the core event processing function that:
        - Listens for incoming messages from the Azure Voice Live API
        - Decodes JSON-encoded messages and processes them by event type
        - Handles various event types including:
          * Audio responses and speech detection
          * Function call requests and responses
          * Session updates and error handling
          * Conversation state management
          
        Key Event Types Processed:
        - response.audio.delta: Real-time audio chunks for playback
        - response.function_call_arguments.delta: Function calling parameters
        - response.function_call_arguments.done: Complete function execution
        - input_audio_buffer.speech_started/stopped: Voice activity detection
        - error: Error handling and logging
        
        Function Calling Integration:
        - Automatically detects function call requests from the model
        - Executes available functions with provided parameters
        - Sends function results back to the model for continued conversation
        - Handles function execution errors gracefully
        
        Best Practices:
        - Monitor for connection drops and implement reconnection logic
        - Handle function execution timeouts appropriately
        - Log important events for debugging and monitoring
        - Validate function parameters before execution
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
                print(f"response done received...{event}")
                try:
                    _status = event.get("response", {}).get("status", None)
                    if "completed" == _status:
                        output_type = (
                            event.get("response", {})
                            .get("output", [{}])[0]
                            .get("type", None)
                        )
                        if "function_call" == output_type:
                            function_name = (
                                event.get("response", {})
                                .get("output", [{}])[0]
                                .get("name", None)
                            )
                            arguments = json.loads(
                                event.get("response", {})
                                .get("output", [{}])[0]
                                .get("arguments", None)
                            )
                            tool_call_id = (
                                event.get("response", {})
                                .get("output", [{}])[0]
                                .get("call_id", None)
                            )

                            function_to_call = available_functions[function_name]
                            # invoke the function with the arguments and get the response
                            response = function_to_call(**arguments)
                            print(
                                f"called function {function_name}, and the response is:",
                                response,
                            )
                            # send the function call response to the server(model)
                            await self.send(
                                "conversation.item.create",
                                {
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": tool_call_id,
                                        "output": json.dumps(response),
                                    }
                                },
                            )
                            # signal the model(server) to generate a response based on the function call output sent to it
                            await self.send(
                                "response.create", {"response": self.response_config}
                            )
                except Exception as e:
                    print("Error in processing function call:", e)
                    print(traceback.format_exc())
                    pass
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
