#!/usr/bin/env python3
"""
Enhanced WebSocket proxy for Azure Voice Live API using aiohttp
This proxy can send proper Authorization headers that the Voice Live API requires
"""

import asyncio
import aiohttp
from aiohttp import web
import json
import os
import uuid
import logging
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

# Load environment variables
load_dotenv("./.env", override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VoiceLiveProxyAioHttp:
    def __init__(self):
        self.endpoint = os.environ.get("AZURE_VOICE_LIVE_ENDPOINT")
        self.api_key = os.environ.get("AZURE_VOICE_LIVE_API_KEY")
        self.api_version = os.environ.get("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")
        
        # Validate required environment variables
        if not self.endpoint:
            raise ValueError("AZURE_VOICE_LIVE_ENDPOINT environment variable is required")
        if not self.api_key:
            raise ValueError("AZURE_VOICE_LIVE_API_KEY environment variable is required")
        
        logger.info(f"Voice Live endpoint configured: {self.endpoint}")
        
        # Initialize Azure credential
        self.credential = DefaultAzureCredential()
        self.token = None
        self.token_expires = None
        logger.info("Azure credential initialized for aiohttp proxy")
        
    async def get_fresh_token(self):
        """Get a fresh Azure AD token"""
        try:
            # Check if we need a new token
            if (self.token is None or 
                self.token_expires is None or 
                datetime.now() >= self.token_expires - timedelta(minutes=5)):
                
                logger.info("Getting fresh Azure AD token...")
                scopes = "https://ai.azure.com/.default"
                token = self.credential.get_token(scopes)
                
                self.token = token.token
                self.token_expires = datetime.fromtimestamp(token.expires_on)
                logger.info(f"Got fresh Azure AD token, expires at: {self.token_expires}")
                
            return self.token
            
        except Exception as e:
            logger.error(f"Failed to get Azure AD token: {e}")
            return None

    async def handle_client_connection(self, request):
        """Handle incoming WebSocket connections from clients"""
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        
        try:
            # Parse query parameters from request path
            query_string = request.query_string
            query_params = parse_qs(query_string)
            
            # Get required parameters
            project_name = query_params.get('project_name', [None])[0]
            agent_id = query_params.get('agent_id', [None])[0]
            
            # Use defaults from environment if not provided
            if not project_name:
                project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME")
            if not agent_id:
                agent_id = os.environ.get("AI_FOUNDRY_AGENT_ID")
            
            if not project_name or not agent_id:
                logger.error(f"Missing required parameters: project_name={project_name}, agent_id={agent_id}")
                await ws_client.close(code=1008, reason="Missing project_name or agent_id")
                return ws_client
            
            logger.info(f"New client connection for project: {project_name}, agent: {agent_id}")
            
            # Get fresh Azure AD token
            token = await self.get_fresh_token()
            if not token:
                await ws_client.send_str(json.dumps({
                    "type": "connection.error",
                    "message": "Failed to get Azure AD token"
                }))
                await ws_client.close(code=1008, reason="Token error")
                return ws_client
            
            # Construct Azure Voice Live WebSocket URL
            azure_ws_endpoint = self.endpoint.rstrip('/').replace("https://", "wss://")
            azure_url = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self.api_version}&agent-project-name={project_name}&agent-id={agent_id}&agent-access-token={token}"
            
            # Prepare headers for Azure connection
            request_id = str(uuid.uuid4())
            headers = {
                "Authorization": f"Bearer {token}",
                "x-ms-client-request-id": request_id,
                "User-Agent": "VoiceLiveProxy/1.0"
            }
            
            logger.info("Connecting to Azure Voice Live API with proper headers...")
            
            # Connect to Azure Voice Live API using aiohttp
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.ws_connect(
                        azure_url, 
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as ws_azure:
                        
                        logger.info("✅ Successfully connected to Azure Voice Live API!")
                        
                        # Send connection success to client
                        await ws_client.send_str(json.dumps({
                            "type": "connection.established",
                            "message": "Connected to Azure Voice Live API with proper authentication"
                        }))
                        
                        # Create forwarding tasks
                        async def forward_client_to_azure():
                            try:
                                async for msg in ws_client:
                                    if msg.type == aiohttp.WSMsgType.TEXT:
                                        logger.debug(f"Client -> Azure: {msg.data}")
                                        await ws_azure.send_str(msg.data)
                                    elif msg.type == aiohttp.WSMsgType.ERROR:
                                        logger.error(f"Client WebSocket error: {ws_client.exception()}")
                                        break
                            except Exception as e:
                                logger.error(f"Error forwarding client to Azure: {e}")
                        
                        async def forward_azure_to_client():
                            try:
                                async for msg in ws_azure:
                                    if msg.type == aiohttp.WSMsgType.TEXT:
                                        logger.debug(f"Azure -> Client: {msg.data}")
                                        await ws_client.send_str(msg.data)
                                    elif msg.type == aiohttp.WSMsgType.ERROR:
                                        logger.error(f"Azure WebSocket error: {ws_azure.exception()}")
                                        break
                            except Exception as e:
                                logger.error(f"Error forwarding Azure to client: {e}")
                        
                        # Run both forwarding tasks concurrently
                        await asyncio.gather(
                            forward_client_to_azure(),
                            forward_azure_to_client(),
                            return_exceptions=True
                        )
                        
                except aiohttp.ClientError as e:
                    logger.error(f"Failed to connect to Azure API: {e}")
                    await ws_client.send_str(json.dumps({
                        "type": "connection.error",
                        "message": f"Failed to connect to Azure Voice Live API: {e}"
                    }))
                    await ws_client.close(code=1011, reason="Azure connection failed")
                    
        except Exception as e:
            logger.error(f"Proxy error: {e}")
            try:
                await ws_client.send_str(json.dumps({
                    "type": "connection.error", 
                    "message": f"Internal proxy error: {e}"
                }))
                await ws_client.close(code=1011, reason="Internal error")
            except:
                pass
        
        return ws_client

async def main():
    """Start the aiohttp-based WebSocket proxy server"""
    port = int(os.environ.get("PROXY_PORT", 8765))
    proxy = VoiceLiveProxyAioHttp()
    
    # Create aiohttp web application
    app = web.Application()
    app.router.add_get('/', proxy.handle_client_connection)
    
    logger.info(f"🚀 Starting aiohttp Voice Live Proxy on port {port}")
    logger.info("✨ Features: Proper Authorization headers, automatic token refresh")
    logger.info(f"🔗 Connect from browser: ws://localhost:{port}/?project_name=YOUR_PROJECT&agent_id=YOUR_AGENT")
    
    # Start the server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()
    
    logger.info("🎉 aiohttp Voice Live Proxy started successfully!")
    
    # Keep running
    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        logger.info("Shutting down proxy server...")
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
