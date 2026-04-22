#!/usr/bin/env python3
import os


def main():
    os.environ["LLM_PROVIDER"] = "openrouter"
    os.environ["OPENROUTER_API_KEY"] = (
        "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"
    )
    os.environ["OPENROUTER_MODEL"] = "anthropic/claude-sonnet-4.6"

    from threads_analytics.llm_client import create_llm_client

    client = create_llm_client()

    # Actual topics extraction prompt from the app
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

    user_msg = f'Here are 3 recent posts from a creator. Extract their topics.\n\n{SCHEMA_INSTRUCTION}\n\nPOSTS:\n[{{"id": "1", "text": "Building AI agents for hiring"}}, {{"id": "2", "text": "Remote work in Indonesia"}}, {{"id": "3", "text": "BPO industry trends"}}]'

    try:
        resp = client.create_message(
            max_tokens=2048, system=SYSTEM, messages=[{"role": "user", "content": user_msg}]
        )
        print("Success!")
        print(f"Response: {resp.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")
        if hasattr(e, "response") and e.response:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text[:1000]}")


if __name__ == "__main__":
    main()
