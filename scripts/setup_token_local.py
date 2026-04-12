"""Local OAuth callback server to capture the auth code.

This starts a temporary HTTP server on localhost:8080 to receive the OAuth
callback from Meta, avoiding the https://localhost redirect URI issue.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

# Add parent to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from threads_analytics.config import get_settings

# Global to store the captured code
captured_code = None


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global captured_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        if "code" in params:
            captured_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif; max-width:600px; margin:50px auto; text-align:center;">
                <h1>✅ Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                </body></html>
            """)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: No code received")
    
    def log_message(self, format, *args):
        # Suppress logs
        pass


def main():
    settings = get_settings()
    
    if not settings.meta_app_id or not settings.meta_app_secret:
        print("Error: META_APP_ID and META_APP_SECRET must be set in .env")
        return 1
    
    # Use localhost:8080 instead of https://localhost
    redirect_uri = "http://localhost:8080/"
    
    print("=" * 60)
    print("Threads OAuth Setup with Local Server")
    print("=" * 60)
    print()
    print(f"App ID: {settings.meta_app_id}")
    print(f"Redirect URI: {redirect_uri}")
    print()
    print("IMPORTANT: You MUST add this redirect URI to your Meta app:")
    print(f"  {redirect_uri}")
    print()
    print("Go to: https://developers.facebook.com/apps/ -> Your App ->")
    print("  Products -> Threads -> Settings -> Valid OAuth Redirect URIs")
    print()
    input("Press Enter once you've added the redirect URI...")
    print()
    
    # Build OAuth URL
    scope = "threads_basic,threads_manage_insights,threads_keyword_search,threads_read_replies"
    auth_url = (
        f"https://threads.net/oauth/authorize?"
        f"client_id={settings.meta_app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scope}&"
        f"response_type=code"
    )
    
    # Start local server
    global captured_code
    captured_code = None
    
    server = socketserver.TCPServer(("", 8080), OAuthHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    print("Local server started on http://localhost:8080")
    print()
    print("Opening browser for authorization...")
    webbrowser.open(auth_url)
    print()
    print("Waiting for authorization... (check your browser)")
    
    # Wait for code
    import time
    timeout = 120
    waited = 0
    while captured_code is None and waited < timeout:
        time.sleep(0.5)
        waited += 0.5
    
    server.shutdown()
    
    if captured_code is None:
        print("\n❌ Timeout waiting for authorization")
        return 1
    
    print(f"\n✅ Authorization code received!")
    print()
    
    # Exchange code for token
    print("Exchanging code for access token...")
    
    token_url = "https://graph.threads.net/oauth/access_token"
    response = httpx.post(token_url, data={
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": captured_code,
    })
    
    if response.status_code != 200:
        print(f"\n❌ Token exchange failed: {response.status_code}")
        print(response.text)
        return 1
    
    data = response.json()
    access_token = data.get("access_token")
    user_id = data.get("user_id")
    
    if not access_token:
        print("\n❌ No access_token in response")
        print(data)
        return 1
    
    # Exchange for long-lived token
    print("Exchanging for long-lived token...")
    ll_url = "https://graph.threads.net/access_token"
    ll_response = httpx.get(ll_url, params={
        "grant_type": "th_exchange_token",
        "client_secret": settings.meta_app_secret,
        "access_token": access_token,
    })
    
    if ll_response.status_code == 200:
        ll_data = ll_response.json()
        access_token = ll_data.get("access_token", access_token)
        expires_in = ll_data.get("expires_in", "unknown")
        print(f"  Long-lived token received (expires in {expires_in} seconds)")
    else:
        print("  (Using short-lived token)")
    
    # Get username
    print("Fetching user info...")
    me_response = httpx.get(
        f"https://graph.threads.net/v1.0/me",
        params={"access_token": access_token, "fields": "username"}
    )
    username = None
    if me_response.status_code == 200:
        username = me_response.json().get("username")
        print(f"  Username: @{username}")
    
    # Update .env file
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_content = env_path.read_text()
    
    # Replace or add values
    def replace_or_add(content, key, value):
        if f"{key}=" in content:
            lines = content.split("\n")
            new_lines = []
            for line in lines:
                if line.startswith(f"{key}="):
                    new_lines.append(f"{key}={value}")
                else:
                    new_lines.append(line)
            return "\n".join(new_lines)
        else:
            return content + f"\n{key}={value}"
    
    env_content = replace_or_add(env_content, "THREADS_ACCESS_TOKEN", access_token)
    env_content = replace_or_add(env_content, "THREADS_USER_ID", user_id or "")
    if username:
        env_content = replace_or_add(env_content, "THREADS_HANDLE", username)
    
    env_path.write_text(env_content)
    
    print()
    print("🎉 Success! Your .env file has been updated with:")
    print(f"  THREADS_ACCESS_TOKEN=***{access_token[-10:]}" if len(access_token) > 10 else f"  THREADS_ACCESS_TOKEN={access_token}")
    print(f"  THREADS_USER_ID={user_id}")
    if username:
        print(f"  THREADS_HANDLE={username}")
    print()
    print("You can now run: threads-analytics whoami")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
