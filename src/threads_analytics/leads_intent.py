"""Lead intent classification using LLM.

This module classifies leads into intent categories based on their post content
and author bio, helping prioritize outreach to the most promising prospects.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from .config import get_settings
from .llm_client import get_llm_client

log = logging.getLogger(__name__)

IntentType = Literal["job_seeker", "founder", "service_buyer", "other", "unclear"]

SYSTEM_PROMPT = """Analyze this Threads post and classify the author's intent.

Post: {post_text}
Author bio: {author_bio}
Matched keyword: {keyword}

Classify into ONE category:
- "job_seeker": Looking for work, mentions skills, "hire me", "open to work"
- "founder": Building a company, mentions startup, "my company", "we're hiring"
- "service_buyer": Explicitly looking to hire/buy services, mentions budget, timeline
- "other": None of the above

Respond with JSON: {"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}"""


def classify_lead_intent(
    post_text: str,
    author_bio: str | None,
    matched_keyword: str,
) -> dict:
    """Classify lead intent using LLM.

    Args:
        post_text: The text content of the post to analyze.
        author_bio: The author's bio text, if available.
        matched_keyword: The keyword that matched this lead.

    Returns:
        A dictionary with keys:
            - "intent": One of "job_seeker", "founder", "service_buyer", "other", "unclear"
            - "confidence": Float between 0.0 and 1.0 indicating classification confidence
            - "reasoning": A brief explanation of the classification
    """
    settings = get_settings()
    client = get_llm_client()

    # Prepare the prompt with the actual values
    bio_text = author_bio if author_bio else "(not provided)"
    system_prompt = SYSTEM_PROMPT.format(
        post_text=post_text,
        author_bio=bio_text,
        keyword=matched_keyword,
    )

    try:
        response = client.create_message(
            model=settings.openrouter_model,
            system=system_prompt,
            messages=[{"role": "user", "content": "Classify this lead intent."}],
            max_tokens=500,
            temperature=0.3,  # Lower temperature for more consistent classification
        )

        # Parse the JSON response
        result_text = response.text.strip()

        # Try to extract JSON if wrapped in markdown code blocks
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]

        result_text = result_text.strip()
        result = json.loads(result_text)

        # Validate and normalize the result
        intent = result.get("intent", "unclear")
        if intent not in ("job_seeker", "founder", "service_buyer", "other", "unclear"):
            intent = "unclear"

        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))  # Clamp to 0.0-1.0

        reasoning = result.get("reasoning", "No reasoning provided")

        return {
            "intent": intent,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    except json.JSONDecodeError as exc:
        log.error("Failed to parse LLM response as JSON: %s - Response: %s", exc, response.text)
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "reasoning": f"Failed to parse response: {exc}",
        }

    except Exception as exc:
        log.error("Failed to classify lead intent: %s", exc)
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "reasoning": f"Error during classification: {exc}",
        }
