"""Manual OAuth setup - no server needed.

Since the Meta console is having issues, this uses a simple copy-paste flow.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from threads_analytics.config import get_settings


def main():
    settings = get_settings()
    
    if not settings.meta_app_id or not settings.meta_app_secret:
        print("Error: META_APP_ID and META_APP_SECRET must be set in .env")
        return 1
    
    redirect_uri = "https://localhost/"
    
    print("=" * 70)
    print("Threads OAuth Setup (Manual)")
    print("=" * 70)
    print()
    print("Since the Meta console is having issues, we'll do this manually.")
    print()
    print("Step 1: Open this URL in your browser:")
    print()
    scope = "threads_basic,threads_manage_insights,threads_keyword_search,threads_read_replies"
    auth_url = (
        f"https://threads.net/oauth/authorize?"
        f"client_id={settings.meta_app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scope}&"
        f"response_type=code"
    )
    print(f"  {auth_url}")
    print()
    print("Step 2: Log in to Threads and click 'Allow'")
    print()
    print("Step 3: The browser will try to redirect to localhost and show an error.")
    print("        This is NORMAL. Copy the ENTIRE URL from your browser's address bar.")
    print("        It should look like:")
    print("        https://localhost/?code=AQxxxx...#_")
    print()
    
    redirect_url = input("Paste the full redirect URL here: ").strip()
    
    # Extract code from URL
    match = re.search(r'[?&]code=([^&#]+)', redirect_url)
    if not match:
        print("\n❌ Could not find code in URL. Make sure you pasted the full URL.")
        print("The URL should contain '?code=AQ...'")
        return 1
    
    code = match.group(1)
    print(f"\n✓ Code extracted: {code[:20]}...")
    print()
    
    # Exchange for token
    print("Exchanging code for access token...")
    
    token_url = "https://graph.threads.net/oauth/access_token"
    response = httpx.post(token_url, data={
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    })
    
    if response.status_code != 200:
        print(f"\n❌ Token exchange failed: {response.status_code}")
        print(response.text)
        
        if "redirect_uri" in response.text.lower():
            print()
            print("The redirect URI isn't whitelisted in your Meta app.")
            print("You'll need to fix this in the Meta console eventually.")
            print("For now, try the local server version:")
            print("  python scripts/setup_token_local.py")
            print()
            print("Which uses http://localhost:8080/ instead of https://localhost/")
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
        print(f"  ✓ Long-lived token received (expires in {expires_in} seconds)")
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
        print(f"  ✓ Username: @{username}")
    
    # Update .env file
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_content = env_path.read_text()
    
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
    print("You can now run:")
    print("  threads-analytics whoami")
    print("  threads-analytics run")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
