#!/usr/bin/env python3
"""Simple interactive token getter - run this yourself."""

import re
from pathlib import Path
import httpx

APP_ID = "932942422833342"
APP_SECRET = "b96558d83a12e869b01a397b2642d748"
REDIRECT_URI = "https://localhost/"

print("\n" + "="*60)
print("GET THREADS ACCESS TOKEN")
print("="*60)
print("\n1. Open this URL in your browser:\n")
scope = "threads_basic,threads_manage_insights,threads_keyword_search,threads_read_replies"
url = f"https://threads.net/oauth/authorize?client_id={APP_ID}&redirect_uri={REDIRECT_URI}&scope={scope}&response_type=code"
print(f"{url}\n")

print("2. Log in to Threads and click 'Allow'")
print("3. The page will show an error - copy the FULL URL from address bar")
print("   (It looks like: https://localhost/?code=AQxxxx...)\n")

redirect_url = input("Paste the full URL here: ").strip()

# Extract code
match = re.search(r'[?&]code=([^&#]+)', redirect_url)
if not match:
    print("\n❌ Could not find code in URL")
    exit(1)

code = match.group(1)
print(f"\n✓ Got code: {code[:20]}...")

# Exchange for token
print("\nExchanging for access token...")
resp = httpx.post("https://graph.threads.net/oauth/access_token", data={
    "client_id": APP_ID,
    "client_secret": APP_SECRET,
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT_URI,
    "code": code,
})

if resp.status_code != 200:
    print(f"\n❌ Failed: {resp.status_code}")
    print(resp.text)
    if "redirect_uri" in resp.text.lower():
        print("\n⚠️  The redirect URI isn't whitelisted in your Meta app.")
        print("   You need to go to developers.facebook.com and add:")
        print(f"   {REDIRECT_URI}")
    exit(1)

data = resp.json()
token = data["access_token"]
user_id = data.get("user_id", "")

# Get long-lived token
print("Getting long-lived token...")
ll = httpx.get("https://graph.threads.net/access_token", params={
    "grant_type": "th_exchange_token",
    "client_secret": APP_SECRET,
    "access_token": token,
})
if ll.status_code == 200:
    token = ll.json().get("access_token", token)
    print("✓ Got long-lived token (60 days)")

# Get username
print("Getting username...")
me = httpx.get("https://graph.threads.net/v1.0/me", params={
    "access_token": token,
    "fields": "username"
})
username = me.json().get("username", "") if me.status_code == 200 else ""
if username:
    print(f"✓ Username: @{username}")

# Update .env
env_path = Path(".env")
content = env_path.read_text()

for key, val in [
    ("THREADS_ACCESS_TOKEN", token),
    ("THREADS_USER_ID", user_id),
    ("THREADS_HANDLE", username),
]:
    if f"{key}=" in content:
        content = re.sub(f"{key}=.*", f"{key}={val}", content)
    else:
        content += f"\n{key}={val}"

env_path.write_text(content)

print("\n" + "="*60)
print("✅ SUCCESS! .env file updated.")
print("="*60)
print(f"\nAccess Token: ***{token[-10:]}")
print(f"User ID: {user_id}")
print(f"Username: @{username}")
print("\nNext steps:")
print("  source .venv/bin/activate")
print("  threads-analytics whoami")
