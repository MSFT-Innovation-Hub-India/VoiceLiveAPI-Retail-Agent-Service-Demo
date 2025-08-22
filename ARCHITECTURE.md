# Azure Voice Live API + TTS Avatar Integration Architecture

## Overview

This solution implements a real-time conversational AI system that combines Azure's Voice Live API with TTS Avatar capabilities, providing both voice interaction and visual representation through a web-based interface.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                CLIENT TIER                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐  │
│  │   Web Browser       │    │   Microphone        │    │   Speakers          │  │
│  │                     │    │   (Audio Input)     │    │   (Audio Output)    │  │
│  │ ┌─────────────────┐ │    │                     │    │                     │  │
│  │ │ HTML/CSS UI     │ │    └─────────────────────┘    └─────────────────────┘  │
│  │ │ - Avatar Canvas │ │              │                          ▲              │
│  │ │ - Chat History  │ │              │                          │              │
│  │ │ - Controls      │ │              ▼                          │              │
│  │ └─────────────────┘ │    ┌─────────────────────┐    ┌─────────────────────┐  │
│  │                     │    │   WebRTC Audio      │    │   Web Audio API     │  │
│  │ ┌─────────────────┐ │    │   Capture           │    │   Playback          │  │
│  │ │ JavaScript      │ │    │   - getUserMedia()  │    │   - AudioContext    │  │
│  │ │ - Voice Live    │ │    │   - MediaRecorder   │    │   - Sequential      │  │
│  │ │   Integration   │ │    │   - Audio Chunks    │    │     Audio Queue     │  │
│  │ │ - Avatar        │ │    └─────────────────────┘    └─────────────────────┘  │
│  │ │   Controls      │ │                                                        │
│  │ │ - WebSocket     │ │                                                        │
│  │ │   Client        │ │                                                        │
│  │ └─────────────────┘ │                                                        │
│  └─────────────────────┘                                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │ ▲
                           HTTP/WebSocket│ │
                          (port 8080)   │ │ (port 8765)
                                     ▼ │
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              APPLICATION TIER                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐              ┌─────────────────────────────────────┐    │
│  │   HTTP Server       │              │   WebSocket Proxy Server           │    │
│  │   (avatar_server.py)│              │   (voice_live_proxy_aiohttp.py)    │    │
│  │                     │              │                                     │    │
│  │ ┌─────────────────┐ │              │ ┌─────────────────────────────────┐ │    │
│  │ │ File Server     │ │              │ │ Authentication Handler          │ │    │
│  │ │ - HTML/CSS      │ │              │ │ - Azure AD Token Management     │ │    │
│  │ │ - JavaScript    │ │              │ │ - DefaultAzureCredential        │ │    │
│  │ │ - Static Assets │ │              │ │ - Token Refresh (5min buffer)   │ │    │
│  │ └─────────────────┘ │              │ └─────────────────────────────────┘ │    │
│  │                     │              │                                     │    │
│  │ ┌─────────────────┐ │              │ ┌─────────────────────────────────┐ │    │
│  │ │ CORS Handler    │ │              │ │ WebSocket Proxy                 │ │    │
│  │ │ - Cross-Origin  │ │              │ │ - Bidirectional Message Relay  │ │    │
│  │ │   Headers       │ │              │ │ - Client ↔ Azure API Bridge     │ │    │
│  │ │ - WebSocket     │ │              │ │ - Connection Management         │ │    │
│  │ │   Support       │ │              │ │ - Error Handling & Logging      │ │    │
│  │ └─────────────────┘ │              │ └─────────────────────────────────┘ │    │
│  └─────────────────────┘              └─────────────────────────────────────┘    │
│         │                                              │                        │
│    Serves Files                                   Proxies WebSocket             │
│    Port: 8080                                     Port: 8765                    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                                         │ ▲
                                            Authenticated│ │ WebSocket
                                              WebSocket  │ │ with Bearer Token
                                                         ▼ │
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 AZURE TIER                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                     Azure Voice Live API Service                           │ │
│  │                                                                             │ │
│  │ ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │ │
│  │ │ Session Mgmt    │  │ Audio Processing│  │ AI Agent Integration        │  │ │
│  │ │ - Session       │  │ - Speech-to-Text│  │ - Azure AI Foundry          │  │ │
│  │ │   Creation      │  │ - Text-to-Speech│  │ - Conversational AI         │  │ │
│  │ │ - State Mgmt    │  │ - Neural Voices │  │ - Context Management        │  │ │
│  │ │ - WebSocket     │  │ - Audio Codecs  │  │ - Response Generation       │  │ │
│  │ │   Lifecycle     │  │ - VAD (Voice    │  │ - Multi-modal Responses     │  │ │
│  │ └─────────────────┘  │   Activity      │  │   (Text + Audio)            │  │ │
│  │                      │   Detection)    │  └─────────────────────────────┘  │ │
│  │ ┌─────────────────┐  │ - Semantic VAD  │                                   │ │
│  │ │ Message Types   │  │ - Audio         │  ┌─────────────────────────────┐  │ │
│  │ │ - Session       │  │   Streaming     │  │ Azure Neural Voices         │  │ │
│  │ │ - Input Audio   │  │ - Real-time     │  │ - High-quality TTS          │  │ │
│  │ │ - Response      │  │   Processing    │  │ - Natural Speech Synthesis  │  │ │
│  │ │ - Audio Delta   │  └─────────────────┘  │ - Multiple Voice Options    │  │ │
│  │ │ - Text Delta    │                       │ - Emotional Expression      │  │ │
│  │ │ - Transcripts   │                       └─────────────────────────────┘  │ │
│  │ │ - Error Events  │                                                        │ │
│  │ └─────────────────┘                                                        │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │                          Azure Identity Platform                           │ │
│  │ ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │ │
│  │ │ Azure AD        │  │ Token           │  │ RBAC & Permissions          │  │ │
│  │ │ Authentication  │  │ Management      │  │ - API Access Control        │  │ │
│  │ │ - Service       │  │ - JWT Tokens    │  │ - Resource Authorization    │  │ │
│  │ │   Principal     │  │ - Token Refresh │  │ - Scope-based Access        │  │ │
│  │ │ - Managed       │  │ - Expiry Mgmt   │  │ - Security Boundaries       │  │ │
│  │ │   Identity      │  │ (1hr lifetime)  │  └─────────────────────────────┘  │ │
│  │ └─────────────────┘  └─────────────────┘                                   │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### Client Tier Components

#### Web Browser Application
- **Location**: `avatar_voice_live_app.html` + `avatar_voice_live_simple.js`
- **Purpose**: User interface and client-side logic
- **Key Features**:
  - Avatar canvas for visual representation
  - Real-time chat history display
  - Audio/video controls
  - WebSocket client for Voice Live API communication

#### Audio Processing (Client-Side)
- **Input**: WebRTC `getUserMedia()` API for microphone access
- **Output**: Web Audio API for sequential audio playback
- **Features**:
  - Real-time audio capture and chunking
  - Audio queue management to prevent overlaps
  - Voice Activity Detection (VAD) feedback prevention

### Application Tier Components

#### HTTP Server (`avatar_server.py`)
- **Port**: 8080
- **Purpose**: Static file server for web application
- **Features**:
  - Serves HTML, CSS, JavaScript files
  - CORS headers for WebSocket support
  - Simple development server

#### WebSocket Proxy Server (`voice_live_proxy_aiohttp.py`)
- **Port**: 8765
- **Purpose**: Secure proxy for Azure Voice Live API
- **Key Features**:
  - **Authentication**: Manages Azure AD tokens using `DefaultAzureCredential`
  - **Token Management**: Automatic refresh with 5-minute buffer
  - **WebSocket Proxy**: Bidirectional message relay
  - **Security**: Adds Bearer token authentication to all requests
  - **Connection Management**: Handles WebSocket lifecycle

### Azure Tier Components

#### Azure Voice Live API Service
- **Endpoint**: Configured via `AZURE_VOICE_LIVE_ENDPOINT`
- **API Version**: `2025-05-01-preview`
- **Key Capabilities**:
  - Real-time speech processing
  - Conversational AI integration
  - Neural text-to-speech
  - Multi-modal responses (text + audio)
  - Session and context management

#### Azure Identity Platform
- **Authentication**: Azure AD for API access
- **Authorization**: Role-based access control
- **Token Lifecycle**: 1-hour JWT tokens with automatic refresh

## Data Flow Architecture

### 1. Session Initialization Flow
```
Browser → HTTP Server (8080) → Static Files
Browser → WebSocket Proxy (8765) → Azure AD Auth → Voice Live API
```

### 2. Real-time Conversation Flow
```
User Speech → Microphone → WebRTC → JavaScript Client
                                      ↓
JavaScript Client → WebSocket → Proxy Server → Azure Voice Live API
                                      ↓
Azure Voice Live API → AI Processing → Response Generation
                                      ↓
Response (Audio + Text) → Proxy Server → WebSocket → JavaScript Client
                                                            ↓
Audio Playback + UI Update ← Web Audio API ← JavaScript Client
```

### 3. Authentication Flow
```
Proxy Server → DefaultAzureCredential → Azure AD → Access Token
                    ↓
Access Token → Bearer Header → Voice Live API Requests
                    ↓
Token Expiry Check → Auto Refresh (every ~55 minutes)
```

## Message Types & Protocol

### WebSocket Message Categories

1. **Session Management**
   - `session.created`
   - `session.updated`
   - `session.configure`

2. **Audio Input Processing**
   - `input_audio_buffer.append`
   - `input_audio_buffer.commit`
   - `input_audio_buffer.speech_started`
   - `input_audio_buffer.speech_stopped`

3. **AI Response Processing**
   - `response.created`
   - `response.audio.delta`
   - `response.text.delta`
   - `response.done`

4. **Transcript Processing**
   - `conversation.item.input_audio_transcription.completed`
   - `response.audio_transcript.done`

## Security Architecture

### Authentication Chain
1. **Client → Proxy**: No authentication (localhost development)
2. **Proxy → Azure**: Azure AD Bearer token authentication
3. **Token Management**: Automatic refresh with 5-minute buffer
4. **Credential Source**: `DefaultAzureCredential` (supports multiple auth methods)

### Network Security
- **HTTP Server**: Localhost only (development setup)
- **WebSocket Proxy**: Localhost only with Azure AD upstream
- **CORS**: Configured for WebSocket cross-origin support

## Deployment Architecture

### Local Development Setup
```
Terminal 1: python voice_live_proxy_aiohttp.py  (Port 8765)
Terminal 2: python avatar_server.py             (Port 8080)
Browser:    http://localhost:8080                (Web App)
```

### Environment Configuration
- `.env` file with Azure endpoints and credentials
- Azure AD authentication via DefaultAzureCredential
- Voice Live API endpoint and version configuration

## Technical Stack

### Frontend
- **HTML5/CSS3**: User interface
- **JavaScript ES6+**: Client logic and WebSocket handling
- **WebRTC**: Audio capture
- **Web Audio API**: Audio playback and processing

### Backend Proxy
- **Python 3.8+**: Runtime environment
- **aiohttp**: Async WebSocket proxy server
- **azure-identity**: Authentication library
- **asyncio**: Concurrent connection handling

### Azure Services
- **Azure Voice Live API**: Core conversational AI service
- **Azure AI Foundry**: Agent and model integration
- **Azure AD**: Identity and access management
- **Azure Neural Voices**: Text-to-speech processing

## Performance Characteristics

### Latency Sources
1. **Network**: Client ↔ Proxy ↔ Azure (typically <100ms)
2. **Audio Processing**: Real-time streaming with chunked delivery
3. **AI Processing**: Variable based on response complexity
4. **Audio Playback**: Sequential queue processing

### Scalability Considerations
- **Proxy Server**: Single-threaded async handling
- **Azure Voice Live**: Managed service with built-in scaling
- **WebSocket Connections**: One per client session
- **Token Management**: Shared across all connections

## Error Handling & Monitoring

### Client-Side Error Handling
- WebSocket connection failures
- Audio device access issues
- Playback queue management errors

### Proxy-Side Error Handling
- Azure AD token refresh failures
- Voice Live API connection issues
- WebSocket proxy relay errors

### Monitoring & Logging
- Console logging on both client and proxy
- Azure AD token lifecycle logging
- WebSocket connection state tracking
- Audio processing pipeline monitoring

This architecture provides a robust, scalable foundation for real-time conversational AI with visual avatar integration, leveraging Azure's enterprise-grade services while maintaining development-friendly local proxy architecture.
