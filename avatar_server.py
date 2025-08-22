#!/usr/bin/env python3
"""
Simple HTTP server for the Avatar + Voice Live application.
This serves the HTML and JavaScript files needed for the avatar integration.
"""

import http.server
import socketserver
import webbrowser
import os

# Server configuration
PORT = 8080
HOST = 'localhost'

class AvatarHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler for serving avatar application files"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.getcwd(), **kwargs)
    
    def end_headers(self):
        # Add CORS headers for WebSocket connections
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()
    
    def do_GET(self):
        # Serve the main HTML file for root requests
        if self.path == '/' or self.path == '':
            self.path = '/avatar_voice_live_app.html'
        return super().do_GET()

def main():
    """Start the avatar application server"""
    
    print("🎭 Starting Avatar Application Server")
    print(f"🌐 Server: http://{HOST}:{PORT}")
    print("📋 Make sure Voice Live Proxy is running:")
    print("   python voice_live_proxy_aiohttp.py (port 8765)")
    print()
    
    try:
        with socketserver.TCPServer((HOST, PORT), AvatarHTTPRequestHandler) as httpd:
            print(f"✅ Avatar server running on http://{HOST}:{PORT}")
            print("🚀 Opening browser...")
            
            # Open browser automatically
            try:
                webbrowser.open(f'http://{HOST}:{PORT}')
            except:
                print(f"Please open http://{HOST}:{PORT} in your browser")
            
            print("Press Ctrl+C to stop the server")
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print("\n🛑 Avatar server stopped")
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"❌ Port {PORT} is already in use")
        else:
            print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()
