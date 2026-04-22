"""Angle-It: convert a raw intake item into 3 Yoseph-voice draft variants."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..llm_client import get_llm_client
from .fetchers import RawIntakeItem

log = logging.getLogger(__name__)

_VALID_MECHANICS = {
    "binary_verdict",
    "community_ask",
    "teaser_drop",
    "structured_finding",
    "mid_experiment",
    "token_receipt",
    "signal",
}

_ANGLE_IT_SYSTEM_PROMPT = """You write Threads posts for @yosephgratika, an Indonesian BPO founder who runs AI data pipelines at production scale.

Voice rules (MANDATORY):
- Language: Bahasa Indonesia mixed with English technical terms (Bahasa campur)
- Tone: blunt, operator-grade, self-deprecating
- Slang: use "wkwk", "koplak", "njir", "di nerf" naturally where it fits
- Specifics: always name specific model versions, token counts, or team numbers when possible
- Ending: every post MUST end with a reply invitation (question, tease, or unresolved stake)
- Length: 80–500 characters for the full post body

Mechanics available:
- binary_verdict: Contested opinion with named stakes ("X overrated, Y underrated")
- community_ask: Real operational pain + direct call for help
- teaser_drop: Concrete promise + "more details soon"
- structured_finding: Phase-based or tiered insight from real work
- mid_experiment: Live test posted before outcome known
- token_receipt: Specific number + blunt verdict + named model version

For the given trend, output EXACTLY 3 variants as a JSON object with this shape:
{
  "variants": [
    {
      "hook": "first 8 words",
      "body": "full post in Bahasa campur, 80-500 chars",
      "mechanic": "one of the 6 mechanics above",
      "rubric": {
        "hook_test": 0-20,
        "mechanic_fit": 0-20,
        "operator_standing": 0-20,
        "trend_freshness": 0-15,
        "reply_invitation": 0-15,
        "voice_signature": 0-10
      },
      "reasoning": "why this angle + mechanic works"
    }
  ]
}

Rules:
1. Each variant must use a DIFFERENT mechanic if possible.
2. At least one variant should use binary_verdict or token_receipt (highest-performing mechanics).
3. Be strict with rubric scores — a 90+ total should be rare.
4. Output ONLY the JSON object. No markdown, no code fences, no extra text."""


@dataclass
class AngleVariant:
    hook: str
    body: str
    mechanic: str
    rubric: dict[str, int]
    reasoning: str

    @property
    def total_score(self) -> int:
        return sum(self.rubric.values())


def _forgiving_json_parse(text: str) -> dict:
    """Extract and parse JSON from LLM response, handling common corruption."""
    text = text.strip()
    if not text:
        raise ValueError("Empty response")

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Extract outermost JSON object
    if text.startswith("{") and text.endswith("}"):
        candidate = text
    else:
        m = re.search(r"(\{.*\})", text, re.DOTALL)
        candidate = m.group(1) if m else text

    # Fix trailing commas
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    # Fix single quotes
    candidate = candidate.replace("'", '"')

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        raise ValueError(f"Could not parse JSON from response: {text[:200]}")


def generate_angle_variants(item: RawIntakeItem) -> list[AngleVariant]:
    """Call Claude to generate 3 angle variants for an intake item."""
    client = get_llm_client()

    user_prompt = f"""Trend summary: {item.summary or item.source_title}
Source: {item.source}
Title: {item.source_title}
URL: {item.source_url}

Generate 3 post variants."""

    try:
        resp = client.create_message(
            max_tokens=4096,
            system=_ANGLE_IT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.8,
        )
        log.info("Angle-It LLM call: %s tokens in / %s tokens out",
                 resp.usage.get("input_tokens") if resp.usage else "?",
                 resp.usage.get("output_tokens") if resp.usage else "?")
    except Exception as exc:
        log.error("Angle-It LLM call failed: %s", exc)
        raise

    try:
        data = _forgiving_json_parse(resp.text)
    except ValueError as exc:
        log.error("Angle-It JSON parse failed: %s. Raw: %s", exc, resp.text[:500])
        raise

    variants_data = data.get("variants", [])
    if not variants_data or not isinstance(variants_data, list):
        log.error("Angle-It response missing variants array: %s", data)
        raise ValueError("Invalid response structure — missing variants")

    variants: list[AngleVariant] = []
    for v in variants_data:
        mechanic = v.get("mechanic", "")
        if mechanic not in _VALID_MECHANICS:
            mechanic = "token_receipt"  # safe fallback

        rubric = v.get("rubric", {})
        # Clamp rubric values to valid ranges
        cleaned_rubric = {
            "hook_test": _clamp(rubric.get("hook_test", 10), 0, 20),
            "mechanic_fit": _clamp(rubric.get("mechanic_fit", 10), 0, 20),
            "operator_standing": _clamp(rubric.get("operator_standing", 10), 0, 20),
            "trend_freshness": _clamp(rubric.get("trend_freshness", 7), 0, 15),
            "reply_invitation": _clamp(rubric.get("reply_invitation", 7), 0, 15),
            "voice_signature": _clamp(rubric.get("voice_signature", 5), 0, 10),
        }

        variants.append(
            AngleVariant(
                hook=v.get("hook", ""),
                body=v.get("body", ""),
                mechanic=mechanic,
                rubric=cleaned_rubric,
                reasoning=v.get("reasoning", ""),
            )
        )

    # Ensure we have exactly 3 (pad with fallback if LLM returned fewer)
    while len(variants) < 3:
        variants.append(
            AngleVariant(
                hook=item.source_title[:50],
                body=item.summary or item.source_title,
                mechanic="token_receipt",
                rubric={"hook_test": 10, "mechanic_fit": 10, "operator_standing": 10,
                        "trend_freshness": 7, "reply_invitation": 7, "voice_signature": 5},
                reasoning="Fallback variant due to incomplete LLM response",
            )
        )

    return variants[:3]


def _clamp(val, low, high):
    if val is None:
        return low
    try:
        return max(low, min(high, int(val)))
    except (ValueError, TypeError):
        return low
