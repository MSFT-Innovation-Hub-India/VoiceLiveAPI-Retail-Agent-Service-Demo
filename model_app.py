"""
Azure AI Foundry GPT-Realtime Model - User Interface

This module provides a Chainlit-based user interface for direct interaction with 
Azure AI Foundry's GPT-Realtime model, enabling Speech-to-Speech conversational experiences.

Key Features:
- Real-time speech-to-speech conversation interface
- Direct integration with GPT-Realtime model via voicelive_modelclient.py
- Low-latency audio streaming and processing
- Interactive web-based UI powered by Chainlit
- Session management for conversation tracking
- Audio chunk processing and playback
- Conversation interruption handling

Architecture:
- Chainlit web interface for user interaction
- VoiceLiveModelClient for GPT-Realtime model communication
- Real-time audio streaming with PCM16 format
- Session-based conversation state management
- Event-driven architecture for audio processing

User Experience:
- Users can speak directly to the AI and receive immediate spoken responses
- Supports conversation interruptions and resumptions
- Provides visual feedback for audio processing states
- Maintains conversation history and context

Technical Implementation:
- Uses WebSocket connections for real-time communication
- Implements audio chunk buffering and streaming
- Handles conversation state transitions
- Manages user session data and tracking IDs

Usage:
Run this application when you want to provide users with a direct speech-to-speech
interface to GPT-Realtime model. Ideal for voice-first applications, accessibility
features, and natural conversation experiences.

Dependencies:
- voicelive_modelclient.py: Core client for GPT-Realtime model interaction
- Chainlit: Web UI framework for conversational interfaces
- Azure Voice Live API: Backend speech processing services

Author: Microsoft Innovation Hub India
Version: 1.0
"""

import chainlit as cl
from voicelive_modelclient import VoiceLiveModelClient
from uuid import uuid4
import traceback


async def init_rtclient():
    """
    Initializes the GPT-Realtime model client for direct Speech-to-Speech conversations.
    
    This function sets up:
    - VoiceLiveModelClient connection to Azure Voice Live API with GPT-Realtime
    - Direct model integration without Azure AI Foundry Agent layer
    - Session management with unique tracking IDs for audio isolation
    - Event handlers for real-time speech processing and function calling
    
    Key Features:
    - Direct GPT-Realtime model integration for immediate speech-to-speech
    - Client-side function calling implementation for tool execution
    - Low-latency audio streaming with real-time processing
    - Azure Speech Voice integration for high-quality TTS synthesis
    - Server-side voice activity detection (VAD) for natural conversation flow
    
    Function Calling:
    - Automatic tool detection and execution based on conversation context
    - Real-time function parameter extraction and validation
    - Seamless integration of function results into conversation flow
    - Support for complex retail operations (search, order, shipment)
    
    Session Management:
    - Unique track IDs prevent audio session conflicts
    - Real-time transcript updates for conversation tracking
    - Conversation state management for context preservation
    
    Best Practices:
    - Ensure all required tools are properly imported and configured
    - Monitor function execution performance and timeouts
    - Implement proper error handling for speech processing failures
    - Validate audio format and quality across different devices
    """
    openai_realtime = VoiceLiveModelClient()
    cl.user_session.set("track_id", str(uuid4()))
    cl.user_session.set("transcript", ["1", "-"])
    cl.user_session.set("user_input_transcript", ["1", ""])

    async def handle_conversation_updated(event):
        """Used to play the response audio chunks as they are received from the server."""
        _audio = event.get("audio")
        if _audio:
            try:
                await cl.context.emitter.send_audio_chunk(
                    cl.OutputAudioChunk(
                        mimeType="pcm16",
                        data=_audio,
                        track=cl.user_session.get("track_id"),
                    )
                )
            except Exception as e:
                print(f"❌ Error sending audio chunk: {e}")

    async def handle_conversation_interrupt(event):
        """This applies when the user interrupts during an audio playback.
        This stops the audio playback to listen to what the user has to say"""
        print("🔄 Conversation interrupted - stopping audio playback")
        cl.user_session.set("track_id", str(uuid4()))
        await cl.context.emitter.send_audio_interrupt()
        user_transcript_msg_id = str(uuid4())
        cl.user_session.set("user_input_transcript", [user_transcript_msg_id, ""])
        await cl.Message(
            content="",
            author="user",
            type="user_message",
            id=user_transcript_msg_id,
        ).send()
        
    async def handle_conversation_message_interrupted(event):
        """This applies when the user interrupts with a chat input.
        This stops the audio playback to listen to what the user has to say"""
        print("🔄 Conversation interrupted due to user chat message - stopping audio playback")
        cl.user_session.set("track_id", str(uuid4()))


    async def handle_response_audio_transcript_updated(event):
        """Used to populate the chat context with transcription once an audio transcript of the response is done."""
        item_id = event.get("item_id")
        delta = event.get("transcript")
        if delta:
            transcript_ref = cl.user_session.get("transcript")
            # print(f"item_id in delta is {item_id}, and the one in the session is {transcript_ref[0]}")

            # identify if there is a new message or an update to an existing message (i.e. delta to an existing transcript)
            if transcript_ref[0] == item_id:
                _transcript = transcript_ref[1] + delta
                transcript_ref = [item_id, _transcript]
                cl.user_session.set("transcript", transcript_ref)
                # appending the delta transcript from audio to the previous transcript
                # using the message id as the key to update the message in the chat window
                await cl.Message(
                    content=_transcript,
                    author="assistant",
                    type="assistant_message",
                    id=item_id,
                ).update()
            else:
                transcript_ref = [item_id, delta]

                # now populate the assistant response transcript in the chat interface
                cl.user_session.set("transcript", transcript_ref)
                await cl.Message(
                    content=delta,
                    author="assistant",
                    type="assistant_message",
                    id=item_id,
                ).send()

    async def handle_user_input_transcript_done(event):
        """Used to populate the chat context with transcription once an audio transcript of user input is completed.
        Creates the user message directly with the transcript content.
        """
        transcript = event.get("transcript")
        msg_id = cl.user_session.get("user_input_transcript")[0]
        # await cl.Message(content=transcript, author="user", type="user_message").send()

        # A placeholder message was created for the user input transcript earlier. updating the message with the actual transcript
        await cl.Message(
            content=transcript, author="user", type="user_message", id=msg_id
        ).update()
        cl.user_session.set("user_input_transcript", [str(uuid4()), ""])

    openai_realtime.on("conversation.updated", handle_conversation_updated)
    openai_realtime.on("conversation.interrupted", handle_conversation_interrupt)
    openai_realtime.on(
        "conversation.text.delta", handle_response_audio_transcript_updated
    )
    openai_realtime.on(
        "conversation.input.text.done", handle_user_input_transcript_done
    )
    openai_realtime.on("conversation.message.interrupted", handle_conversation_message_interrupted)
    cl.user_session.set("openai_realtime", openai_realtime)


@cl.on_chat_start
async def start():
    print("🚀 @cl.on_chat_start triggered - starting voice chat session")
    try:
        await cl.Message(
            content="Hi, Welcome! You are now connected to Voice AI Assistant representing Contoso Retail Fashions. Please note that the conversation will be recorded for quality purposes. Click the microphone icon below to start talking!"
        ).send()
        print("✅ Welcome message sent")

        await init_rtclient()
        print("✅ RT client initialized")

        openai_realtime: VoiceLiveModelClient = cl.user_session.get("openai_realtime")
        print("status of connection to realtime api", openai_realtime.is_connected())
        print("🎤 Voice chat session setup complete")

    except Exception as e:
        print(f"❌ Error in chat start: {e}")
        import traceback

        traceback.print_exc()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle text messages sent through the chat interface"""
    openai_realtime: VoiceLiveModelClient = cl.user_session.get("openai_realtime")
    if openai_realtime and openai_realtime.is_connected():
        # For text messages, we don't need to create placeholders since the message is already visible
        await openai_realtime.send_user_message_content(
            [{"type": "input_text", "text": message.content}]
        )
    else:
        await cl.Message(
            content="Please activate voice mode before sending messages!"
        ).send()


@cl.on_audio_start
async def on_audio_start():
    try:
        print("🎤 Audio recording started")
        openai_realtime: VoiceLiveModelClient = cl.user_session.get("openai_realtime")
        if not openai_realtime:
            await init_rtclient()
            openai_realtime = cl.user_session.get("openai_realtime")

        await openai_realtime.connect()
        print("🔗 Connected to Voice Live API")

        return True
    except Exception as e:
        print(f"❌ Failed to connect to Voice Live API: {e}")
        await cl.ErrorMessage(
            content=f"Failed to connect to Voice Live API: {e}"
        ).send()
        return False


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk):
    openai_realtime: VoiceLiveModelClient = cl.user_session.get("openai_realtime")

    try:
        if openai_realtime and openai_realtime.is_connected():
            await openai_realtime.append_input_audio(chunk.data)
        else:
            print("⚠️ RealtimeClient is not connected")
    except Exception as e:
        print(f"❌ Failed to send audio chunk to Voice Live API: {e}")
        print(f"Full traceback: \n{traceback.format_exc()}")
        await cl.ErrorMessage(
            content=f"Failed to send audio chunk to Voice Live API: {e}"
        ).send()


@cl.on_audio_end
@cl.on_chat_end
@cl.on_stop
async def on_end():
    openai_realtime: VoiceLiveModelClient = cl.user_session.get("openai_realtime")
    if openai_realtime and openai_realtime.is_connected():
        print("VoiceLiveModelClient session ended")
        await openai_realtime.disconnect()
