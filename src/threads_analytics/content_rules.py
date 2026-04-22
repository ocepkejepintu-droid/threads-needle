"""Anti-AI-slop validation rules for content.

Ensures generated content feels human by rejecting generic AI patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class ValidationResult:
    """Result of content validation."""

    passed: bool
    score: int  # 0-100 human-ness score
    failures: list[str]  # List of failed rule names


# Patterns that indicate AI slop
GENERIC_OPENINGS = [
    r"^in today's",
    r"^in this",
    r"^in our",
    r"^hey everyone",
    r"^hi everyone",
    r"^hello everyone",
    r"^i wanted to share",
    r"^i just wanted to",
    r"^let me tell you",
    r"^so,",
    r"^well,",
    r"^basically",
    r"^essentially",
    r"^the thing is",
]

CORPORATE_SPEAK = [
    r"\bleverage\b",
    r"\bsynergy\b",
    r"\boptimize\b",
    r"\boptimizing\b",
    r"\bstrategic\b",
    r"\bstrategy\b",
    r"\bholistic\b",
    r"\bscalable\b",
    r"\bscale\b",
    r"\bdisrupt\b",
    r"\bdisruption\b",
    r"\bimpactful\b",
    r"\bactionable\b",
    r"\bstreamline\b",
    r"\bbandwidth\b",
    r"\bcircle back\b",
    r"\bmoving forward\b",
    r"\bgoing forward\b",
    r"\bat the end of the day\b",
    r"\blow-hanging fruit\b",
    r"\bquick win\b",
    r"\bparadigm\b",
    r"\bdeep dive\b",
    r"\bsync up\b",
    r"\btouch base\b",
    r"\bping me\b",
    r"\btake this offline\b",
    r"\bthink outside the box\b",
    r"\bbest practice\b",
    r"\bcore competency\b",
    r"\bgame changer\b",
    r"\bmove the needle\b",
    r"\bpeel back the onion\b",
]

ADVICE_TEMPLATES = [
    r"here are \d+ tips",
    r"here are \d+ ways",
    r"top \d+ ways",
    r"top \d+ tips",
    r"\d+ things you need to know",
    r"\d+ reasons why",
    r"\d+ steps to",
    r"the ultimate guide to",
    r"a complete guide to",
    r"everything you need to know about",
]


def _check_generic_opening(text: str) -> bool:
    """Check if text starts with generic AI opening."""
    text_lower = text.lower().strip()
    for pattern in GENERIC_OPENINGS:
        if re.search(pattern, text_lower):
            return False
    return True


def _check_has_specific_numbers(text: str) -> bool:
    """Check if text contains specific numbers/details."""
    # Look for: prices ($, Rp), counts, percentages, time durations, specific years
    patterns = [
        r"\$\d+",  # Dollar amounts
        r"Rp[\d.]+",  # Rupiah
        r"\d+%",  # Percentages
        r"\d+\s*(jam|menit|hari|minggu|bulan|tahun)",  # Time durations (Indonesian)
        r"\d+\s*(hours?|minutes?|days?|weeks?|months?|years?)",  # Time durations (English)
        r"20\d{2}",  # Years
        r"\d+\s*(ribu|juta|miliar)",  # Indonesian numbers
        r"\d+\s*(k|thousand|million|billion)",  # English numbers
        r"\d+\.\d+",  # Decimals (often specific metrics)
    ]
    
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _check_no_corporate_speak(text: str) -> bool:
    """Check if text avoids corporate buzzwords."""
    text_lower = text.lower()
    for pattern in CORPORATE_SPEAK:
        if re.search(pattern, text_lower):
            return False
    return True


def _check_not_advice_template(text: str) -> bool:
    """Check if text isn't a generic advice template."""
    text_lower = text.lower()
    for pattern in ADVICE_TEMPLATES:
        if re.search(pattern, text_lower):
            return False
    return True


def _check_has_emotional_hook(text: str) -> bool:
    """Check if text has emotional hook (frustration, curiosity, excitement)."""
    # Frustration indicators
    frustration = [
        r"\bsusah\b", r"\b Ribet\b", r"\bmasalah\b", r"\bbingung\b",
        r"\bfrustrat", r"\bstuck\b", r"\bstruggling",
        r"\bangry\b", r"\bannoyed\b", r"\bfrustrated\b",
        r"\bcan't stand\b", r"\bsick of\b", r"\btired of\b",
        r"!{2,}",  # Multiple exclamation marks
    ]
    
    # Curiosity indicators
    curiosity = [
        r"\bgimana\b", r"\bgmn\b", r"\bkenapa\b", r"\bkok\b",
        r"\bhow\b", r"\bwhy\b", r"\bwhat if\b", r"\bever wonder",
        r"\bsecret\b", r"\btruth\b", r"\breal reason",
        r"\?",  # Questions
    ]
    
    # Excitement indicators
    excitement = [
        r"\bexcited\b", r"\bthrilled\b", r"\bamazing\b", r"\bincredible",
        r"\bkeren\b", r"\bmantap\b", r"\bbagus\b", r"\bsenang\b",
        r"\byay\b", r"\bwoohoo\b", r"\bfinally\b", r"\bjust launched",
    ]
    
    text_lower = text.lower()
    
    # Check each emotional category
    has_frustration = any(re.search(p, text_lower) for p in frustration)
    has_curiosity = any(re.search(p, text_lower) for p in curiosity)
    has_excitement = any(re.search(p, text_lower) for p in excitement)
    
    return has_frustration or has_curiosity or has_excitement


# List of validation rules: (name, check_function, weight)
VALIDATION_RULES: list[tuple[str, Callable[[str], bool], int]] = [
    ("no_generic_opening", _check_generic_opening, 20),
    ("has_specific_numbers", _check_has_specific_numbers, 25),
    ("no_corporate_speak", _check_no_corporate_speak, 20),
    ("not_advice_template", _check_not_advice_template, 15),
    ("has_emotional_hook", _check_has_emotional_hook, 20),
]


def _check_character_limit(text: str) -> bool:
    """Check if text is within Threads 280 character limit."""
    return len(text) <= 280


def validate_content(text: str) -> ValidationResult:
    """Validate content against anti-slop rules.
    
    Returns a ValidationResult with:
    - passed: True if content passes all rules
    - score: 0-100 human-ness score
    - failures: list of failed rule names
    """
    if not text or len(text.strip()) < 20:
        return ValidationResult(
            passed=False,
            score=0,
            failures=["content_too_short"]
        )
    
    # Check character limit (hard requirement for Threads)
    if len(text) > 280:
        return ValidationResult(
            passed=False,
            score=max(0, 100 - (len(text) - 280)),  # Penalty for being over
            failures=[f"too_long_{len(text)}_chars_max_280"]
        )
    
    failures = []
    total_score = 0
    max_score = sum(weight for _, _, weight in VALIDATION_RULES)
    
    for rule_name, check_func, weight in VALIDATION_RULES:
        if check_func(text):
            total_score += weight
        else:
            failures.append(rule_name)
    
    # Normalize to 0-100
    score = int((total_score / max_score) * 100)
    
    # Pass if score >= 80 (all rules pass)
    # Or score >= 60 with only 1 failure
    passed = score >= 80 or (score >= 60 and len(failures) <= 1)
    
    return ValidationResult(
        passed=passed,
        score=score,
        failures=failures
    )


def get_validation_feedback(failures: list[str]) -> list[str]:
    """Get human-readable feedback for failed rules."""
    feedback_map = {
        "content_too_short": "Content is too short (minimum 20 characters)",
        "no_generic_opening": "Starts with generic AI opening like 'In today's world' or 'Hey everyone'",
        "has_specific_numbers": "Missing specific details (add numbers, prices, percentages, or timeframes)",
        "no_corporate_speak": "Uses corporate buzzwords (leverage, synergy, optimize, etc.)",
        "not_advice_template": "Sounds like generic advice template ('Here are 5 tips...')",
        "has_emotional_hook": "Lacks emotional hook (add frustration, curiosity, or excitement)",
    }
    
    return [feedback_map.get(f, f"Failed: {f}") for f in failures]
