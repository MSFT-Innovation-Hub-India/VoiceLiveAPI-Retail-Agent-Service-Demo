# Contoso Retail Assistant — Voice + Chat (Azure Voice Live API + Chainlit)

An interactive retail assistant that supports natural voice and text conversations using Azure Voice Live API. Built with Chainlit for responsive, real-time UI and robust audio handling.

It uses an Azure AI Foundry Project and an Agent in it that has tool actions configured - in this sampple, it uses the Contoso Retail eCom APIs that are configured using the Swagger definition.

## 🛍 Use case

Contoso’s in-store/online assistant to:
- Answer product questions (price, specs, availability)
- place orders

- what are the products in Winter wear
- What are the products in Active Wear
- I want to order 5 numbers of Product ID 24


Optimized for hands-free, multi-turn conversations with live transcripts and spoken replies.
## 🔧 What’s inside (implementation)

- Chainlit app (`chainlit_voice_app.py`) with a threaded WebSocket client to Azure Voice Live API (`/voice-live/realtime`)
- Azure AD auth via DefaultAzureCredential; scopes: `https://ai.azure.com/.default`
- Server Voice settings: 24kHz audio, semantic VAD, deep noise suppression, server echo cancellation
- Half‑duplex mic gating to reduce echo/self‑interruptions (blocks mic while assistant audio plays + brief tail cooldown)
- Typed chat supported alongside voice; session-ready gating prevents dropped first messages
- Assistant interruption: typing or speaking will cancel current response and clear audio playback
- Logs written under `logs/` for run-time diagnostics

## ✅ Prerequisites

- Python 3.10+
- An Azure AI Foundry project/agent configured for Voice Live
- Ability to obtain AAD tokens via DefaultAzureCredential (e.g., Azure CLI sign-in)

Environment variables (in a `.env` at repo root). Start by copying the template:

```powershell
Copy-Item .env.example .env -Force
```

Then set these variables:
- `AZURE_VOICE_LIVE_ENDPOINT` (e.g., `https://<your-endpoint>.api.cognitive.microsoft.com`)
- `AZURE_VOICE_LIVE_API_VERSION` (defaults to `2025-05-01-preview`)
- `AI_FOUNDRY_PROJECT_NAME`
- `AI_FOUNDRY_AGENT_ID`

Sign in for AAD token (one of):
- Azure CLI: `az login`
- Managed identity or VS Code/Azure sign-in also works with DefaultAzureCredential

## 🚀 Setup & Run

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start the app:

```powershell
chainlit run chainlit_voice_app.py
```

Open the Chainlit URL shown in the terminal.

## 🎤 How to use

- Click “🎙 Start Voice” to begin a voice session. Speak naturally; server VAD detects turns.
- The assistant replies with audio and text. User messages appear right‑aligned; assistant left.
- You can also type. Sending text cancels any ongoing assistant audio and triggers a fresh reply.
- Click “🛑 Stop Voice” to end the voice session (you can continue typing if desired).

## ✨ Features

- Real-time voice conversation with server VAD and noise suppression
- Echo control: server echo cancellation plus half‑duplex mic gating with tail cooldown
- Live transcripts for user speech and assistant replies
- Typed chat supported; safe session-ready gating to avoid early drops
- Immediate interruption on new input (voice or text) to keep the flow natural

## 🧭 High-level flow

1) Chainlit UI ←→ WebSocket client
2) Azure Voice Live API session configured (VAD, noise/echo, voice)
3) Mic audio → `input_audio_buffer.append`
4) Server transcribes; user transcript is rendered
5) Agent responds; audio frames stream back and are played
6) Mic is temporarily gated during assistant audio and for a short cooldown

## 🛠 Troubleshooting

- “Could not reach the server” banner: Chainlit UI reconnect notice; if conversation continues, you can ignore. Refresh if it persists.
- No audio capture: ensure mic permission granted and the correct input device is active.
- Self‑interruptions/echo: increase `ASSISTANT_TAIL_COOLDOWN_MS` in `chainlit_voice_app.py` to 400–600ms; optionally raise `silence_duration_ms`/VAD thresholds in the session config.
- Typed message ignored on first try: ensure the app shows “Connected to Azure Voice Live API API” before sending; the app already waits briefly for session readiness.

## 📁 Project structure

- `chainlit_voice_app.py` — Chainlit app and Azure Voice Live API client
- `voice_live_proxy_aiohttp.py` — optional proxy sample (if used)
- `voice-live-*.py` — quickstarts/samples
- `logs/` — runtime logs

## 🔒 Notes on auth & data

- Uses Azure AD tokens (DefaultAzureCredential) with scope `https://ai.azure.com/.default`.
- Token is sent as `Authorization: Bearer` and as `agent-access-token` in the WS query.
- Audio and text are streamed to Azure Voice Live API. Review your organization’s privacy/compliance requirements before production use.

---

Made with Chainlit and Azure Voice Live API for a smooth retail voice experience.
# Contoso Retail Assistant - Voice Chat

Welcome to the **Contoso Retail Assistant**! This is a voice-enabled AI assistant powered by Azure Voice Live API API.

## 🎤 How to Use

1. **Click "Start Voice Chat"** to begin the conversation
2. **Speak naturally** - the system automatically detects when you're talking
3. The assistant will **respond with both text and audio**
4. **Audio responses play automatically** through your speakers
5. **Click "Stop Voice Chat"** when you're done

## ✨ Features

- **Real-time voice conversation** with natural turn-taking
- **Automatic speech detection** - no need to press buttons
- **Live transcription** of your speech and the assistant's responses
- **Audio playback** of assistant responses
- **Continuous conversation** flow

## 🛍️ What I Can Help With

- **Product inquiries** - Find information about our products
- **Order assistance** - Help with placing or tracking orders
- **General support** - Answer questions about our store and services
- **Product recommendations** - Suggest items based on your needs

## 🔧 Technical Requirements

- **Microphone access** - Allow the browser to use your microphone
- **Speaker/headphones** - For hearing assistant responses
- **Stable internet connection** - For real-time voice processing

---

**Ready to start?** Click the "🎤 Start Voice Chat" button below!
