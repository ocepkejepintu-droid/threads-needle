#!/usr/bin/env python3
import os
os.environ['LLM_PROVIDER'] = 'openrouter'
os.environ['OPENROUTER_API_KEY'] = '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'
os.environ['OPENROUTER_MODEL'] = 'anthropic/claude-sonnet-4.6'

# Test the exact same way the server does it
from threads_analytics.llm_client import LLMClient

client = LLMClient()
print(f"Provider: {client.provider}")
print(f"Base URL: {client.base_url}")
print(f"Model: {client.default_model}")
print(f"API Key: {client.api_key[:20]}...")
print(f"Headers: {dict(client.client.headers)}")

# Test the exact same prompt as topics.py
SYSTEM = (
    "You are a content strategist analyzing a single creator's Threads posts. "
    "Identify the 5 to 10 distinct topics they actually post about. "
    "Prefer specific, usable labels ('building AI agents', 'Nigerian tech scene') "
    "over vague ones ('technology', 'life'). "
    "Topics should be mutually exclusive and collectively cover the posts given."
)

SCHEMA_INSTRUCTION = (
    "Respond with ONLY a JSON object, no prose, no fences. Shape:\n"
    "{\n"
    '  "topics": [\n'
    '    {"label": "...", "description": "one sentence", "post_ids": ["id1", "id2"]}\n'
    "  ]\n"
    "}\n"
    "Each post_id must match an id from the input. A post can belong to multiple topics."
)

user_msg = (
    f"Here are 9 recent posts from a creator. Extract their topics.\n\n"
    f"{SCHEMA_INSTRUCTION}\n\n"
    f'DATA:\n{{"recent_posts": [{{"id": "1", "text": "Post about AI"}}]}}'
)

print("\nTesting with topics-style prompt...")
try:
    resp = client.create_message(
        max_tokens=2048,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    print(f"Success! {resp.text[:200]}")
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'response') and e.response:
        print(f"Response: {e.response.text[:500]}")
