import chainlit as cl
from voicelive_client import VoiceLiveClient
from uuid import uuid4
import traceback


async def init_rtclient():
    openai_realtime = VoiceLiveClient()
    cl.user_session.set("track_id", str(uuid4()))
    cl.user_session.set("transcript", ["1", "-"])
    cl.user_session.set("listening_indicator_id", None)  # Track listening indicator

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
        
        # Reset transcript tracking completely on interruption to prevent stale responses
        print("Resetting transcript state due to interruption")
        cl.user_session.set("transcript", ["1", "-"])
        
        # Clear any pending audio buffer to ensure fresh conversation state
        openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")
        if openai_realtime and openai_realtime.is_connected():
            try:
                # Clear the input audio buffer on the server side
                await openai_realtime.clear_input_audio_buffer()
                print("Cleared input audio buffer on server")
            except Exception as e:
                print(f"Error clearing input audio buffer: {e}")

    async def handle_response_audio_transcript_updated(event):
        """Used to populate the chat context with transcription once an audio transcript of the response is done."""
        try:
            # print("event in conversation text delta", event)
            item_id = event.get("item_id")
            delta = event.get("transcript")
            if delta:
                transcript_ref = cl.user_session.get("transcript")
                # print(f"Handling response audio transcript update event ... {event}")
                # print(f"delta received in audio response transcript update is {delta}")
                # print(
                #     f"item_id in audio response transcript update is {item_id}, and the one in the session is {transcript_ref[0]}"
                # )
                
                # Skip processing if this is a stale response (from before interruption)
                if transcript_ref[0] == "1" and transcript_ref[1] == "-":
                    # Fresh state - this is a new response
                    pass
                elif transcript_ref[0] != item_id and transcript_ref[0] != "1":
                    # This might be a stale response from before interruption
                    print(f"⚠️ Potential stale response detected. Current session item: {transcript_ref[0]}, received item: {item_id}")
                    # Allow it to proceed but log it for debugging
                
                # identify if there is a new message or an update to an existing message (i.e. delta to an existing transcript)
                if transcript_ref[0] == item_id:
                    _transcript = transcript_ref[1] + delta
                    transcript_ref = [item_id, _transcript]
                    cl.user_session.set("transcript", transcript_ref)
                    # appending the delta transcript from audio to the previous transcript
                    # using the message id as the key to update the message in the chat window
                    print(f"📝 Updating existing message {item_id} with delta: {delta[:30]}...")
                    await cl.Message(
                        content=_transcript,
                        author="assistant",
                        type="assistant_message",
                        id=item_id,
                    ).update()
                else:
                    # New assistant response starting
                    print(f"🆕 Starting new assistant response with ID: {item_id} (previous: {transcript_ref[0]})")

                    # now populate the assistant response transcript in the chat interface
                    transcript_ref = [item_id, delta]
                    cl.user_session.set("transcript", transcript_ref)
                    await cl.Message(
                        content=delta,
                        author="assistant",
                        type="assistant_message",
                        id=item_id,
                    ).send()
                    print(
                        f"Created new assistant message {item_id} with initial content: {delta[:50]}..."
                    )
                    # Remove the listening indicator if it exists when assistant starts responding
                    listening_indicator_id = cl.user_session.get("listening_indicator_id")
                    if listening_indicator_id:
                        try:
                            await cl.Message(
                                content="",
                                author="user",
                                type="user_message",
                                id=listening_indicator_id,
                            ).update()
                            cl.user_session.set("listening_indicator_id", None)
                            print(f"Removed listening indicator {listening_indicator_id}")
                        except Exception as e:
                            print(f"Error removing listening indicator: {e}")
                    
                    # No longer creating placeholder user messages here - will create them when transcript is ready

        except Exception as e:
            print(f"Error handling conversation thread update: {e}")
            # Continue gracefully

    async def handle_user_input_transcript_done(event):
        """Used to populate the chat context with transcription once an audio transcript of user input is completed.
        Creates the user message directly with the transcript content.
        """
        transcript = event.get("transcript")
        print("Final user input transcript received:", transcript)
        
        # Remove any listening indicator first
        listening_indicator_id = cl.user_session.get("listening_indicator_id")
        if listening_indicator_id:
            try:
                await cl.Message(
                    content="",
                    author="user", 
                    type="user_message",
                    id=listening_indicator_id,
                ).update()
                cl.user_session.set("listening_indicator_id", None)
                print(f"Removed listening indicator {listening_indicator_id}")
            except Exception as e:
                print(f"Error removing listening indicator: {e}")
        
        # Only create user message if we have actual transcript content
        if transcript and transcript.strip():
            user_msg_id = str(uuid4())
            print(f"Creating user message with ID: {user_msg_id} and transcript: {transcript}")
            await cl.Message(
                content=transcript,
                author="user",
                type="user_message", 
                id=user_msg_id
            ).send()
        else:
            print("No transcript content received, skipping user message creation")

    openai_realtime.on("conversation.updated", handle_conversation_updated)
    openai_realtime.on("conversation.interrupted", handle_conversation_interrupt)
    openai_realtime.on(
        "conversation.text.delta", handle_response_audio_transcript_updated
    )
    openai_realtime.on(
        "conversation.input.text.done", handle_user_input_transcript_done
    )
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

        openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")
        print("status of connection to realtime api", openai_realtime.is_connected())
        print("🎤 Voice chat session setup complete")

    except Exception as e:
        print(f"❌ Error in chat start: {e}")
        import traceback

        traceback.print_exc()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle text messages sent through the chat interface"""
    openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")
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
        openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")
        if not openai_realtime:
            await init_rtclient()
            openai_realtime = cl.user_session.get("openai_realtime")

        await openai_realtime.connect()
        print("🔗 Connected to Voice Live API")
        
        # Create a listening indicator
        listening_indicator_id = str(uuid4())
        cl.user_session.set("listening_indicator_id", listening_indicator_id)
        await cl.Message(
            content="🎤 Listening...",
            author="user",
            type="user_message",
            id=listening_indicator_id,
        ).send()
        print(f"Created listening indicator with ID: {listening_indicator_id}")
        
        return True
    except Exception as e:
        print(f"❌ Failed to connect to Voice Live API: {e}")
        await cl.ErrorMessage(
            content=f"Failed to connect to Voice Live API: {e}"
        ).send()
        return False


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk):
    openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")

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
    openai_realtime: VoiceLiveClient = cl.user_session.get("openai_realtime")
    if openai_realtime and openai_realtime.is_connected():
        print("VoiceLiveClient session ended")
        await openai_realtime.disconnect()
