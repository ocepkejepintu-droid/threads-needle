"""Brand Brain voice validator — validates content against brand guidelines.

Uses LLM to analyze content for:
- Protect list violations (things to NEVER do)
- Voice alignment (consistency with core identity)
- Double-down elements (unique strengths to emphasize)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .llm_client import create_llm_client
from .models import YouProfile

log = logging.getLogger(__name__)


@dataclass
class BrandCheckSuggestion:
    """A suggestion for improving content."""

    issue: str
    suggestion: str


@dataclass
class BrandCheck:
    """Result of brand validation check."""

    overall_score: int  # 0-100
    passed: bool
    voice_alignment: int  # 0-100
    protect_violations: list[str] = field(default_factory=list)
    double_down_elements: list[str] = field(default_factory=list)
    suggestions: list[BrandCheckSuggestion] = field(default_factory=list)


# System prompt template for brand validation
VALIDATION_SYSTEM_TEMPLATE = """You are a brand guardian analyzing content against a creator's voice profile.

VOICE PROFILE:
{core_identity}

STYLISTIC SIGNATURES:
{stylistic_signatures}

PROTECT LIST (NEVER violate):
{protect_list}

DOUBLE-DOWN LIST:
{double_down_list}

CONTENT TO ANALYZE:
{content_text}

Analyze and respond with JSON:
{{
  "overall_score": 0-100,
  "passed": true/false,
  "voice_alignment": 0-100,
  "protect_violations": ["..."],
  "double_down_elements": ["..."],
  "suggestions": [{{"issue": "...", "suggestion": "..."}}]
}}"""


def validate_content(
    content_text: str,
    you_profile: YouProfile,
    content_type: str = "scheduled_post",
) -> BrandCheck:
    """Validate content against brand guidelines.

    Uses LLM to analyze:
    - Protect list violations
    - Voice alignment
    - Double-down elements

    Args:
        content_text: The content text to validate
        you_profile: The user's voice profile
        content_type: Type of content (e.g., "scheduled_post", "reply")

    Returns:
        BrandCheck object with score and suggestions
    """
    # Extract protect list and double-down list from profile
    protect_list = you_profile.protect_list or []
    double_down_list = you_profile.double_down_list or []
    stylistic_signatures = you_profile.stylistic_signatures or []

    # Format stylistic signatures for prompt
    sig_text = "\n".join(
        f"- {sig.get('signature', 'Unknown')}: {sig.get('evidence', '')}"
        for sig in stylistic_signatures
    ) if stylistic_signatures else "No specific signatures recorded."

    # Format protect list for prompt
    protect_text = "\n".join(f"- {item}" for item in protect_list) if protect_list else "None defined."

    # Format double-down list for prompt
    double_down_text = "\n".join(f"- {item}" for item in double_down_list) if double_down_list else "None defined."

    # Build the system prompt
    system_prompt = VALIDATION_SYSTEM_TEMPLATE.format(
        core_identity=you_profile.core_identity or "No core identity defined.",
        stylistic_signatures=sig_text,
        protect_list=protect_text,
        double_down_list=double_down_text,
        content_text=content_text,
    )

    # Call LLM for validation
    try:
        client = create_llm_client()
        resp = client.create_message(
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": "Analyze this content against the brand guidelines."}],
            temperature=0.3,  # Lower temperature for consistent evaluation
        )
        result = _safe_json(resp.text)
    except Exception as e:
        log.warning("Brand validation LLM call failed: %s", e)
        # Return a permissive fallback on error
        return BrandCheck(
            overall_score=50,
            passed=True,
            voice_alignment=50,
            protect_violations=[],
            double_down_elements=[],
            suggestions=[BrandCheckSuggestion(
                issue="Validation unavailable",
                suggestion="Please review manually due to technical error."
            )],
        )

    if not result:
        log.warning("Brand validation produced no parseable JSON: %s", resp.text[:400])
        return BrandCheck(
            overall_score=50,
            passed=True,
            voice_alignment=50,
            protect_violations=[],
            double_down_elements=[],
            suggestions=[BrandCheckSuggestion(
                issue="Parse error",
                suggestion="Could not parse validation result. Please review manually."
            )],
        )

    # Parse suggestions
    raw_suggestions = result.get("suggestions", [])
    suggestions = []
    for s in raw_suggestions:
        if isinstance(s, dict):
            suggestions.append(BrandCheckSuggestion(
                issue=s.get("issue", ""),
                suggestion=s.get("suggestion", "")
            ))

    # Build and return BrandCheck
    return BrandCheck(
        overall_score=result.get("overall_score", 50),
        passed=result.get("passed", True),
        voice_alignment=result.get("voice_alignment", 50),
        protect_violations=result.get("protect_violations", []),
        double_down_elements=result.get("double_down_elements", []),
        suggestions=suggestions,
    )


def check_protect_list_violations(text: str, protect_list: list[str]) -> list[str]:
    """Check if text violates any protect list items.

    This is a local, rule-based check as a fast-path before LLM validation.

    Args:
        text: Content text to check
        protect_list: List of protect items (phrases or patterns to avoid)

    Returns:
        List of violated protect items
    """
    if not protect_list:
        return []

    violations = []
    text_lower = text.lower()

    for item in protect_list:
        # Simple substring check (case-insensitive)
        if item.lower() in text_lower:
            violations.append(item)

    return violations


def calculate_brand_score(alignment: int, violations: list, double_downs: list) -> int:
    """Calculate 0-100 brand score.

    Scoring weights:
    - Protect compliance: 40% (binary - any violation = 0)
    - Voice alignment: 35%
    - Double-down presence: 15%
    - Uniqueness: 10%

    Args:
        alignment: Voice alignment score (0-100)
        violations: List of protect list violations
        double_downs: List of double-down elements present

    Returns:
        Overall brand score (0-100)
    """
    # Protect compliance: 40% weight, binary
    protect_score = 0 if violations else 40

    # Voice alignment: 35% weight, scaled
    alignment_score = (alignment / 100) * 35

    # Double-down presence: 15% weight
    # Full score if at least one double-down element is present
    double_down_score = 15 if double_downs else 0

    # Uniqueness: 10% weight (based on alignment as proxy)
    uniqueness_score = (alignment / 100) * 10

    total_score = protect_score + alignment_score + double_down_score + uniqueness_score
    return min(100, max(0, int(total_score)))


def _safe_json(text: str) -> dict | None:
    """Safely parse JSON from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
