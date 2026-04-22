"""Extract voice patterns from user's top-performing posts.

Analyzes successful content to identify what makes it work —
sentence starters, structure, tone markers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Any

from sqlalchemy import desc, select

from .db import session_scope
from .models import MyPost, MyPostInsight


@dataclass
class VoiceProfile:
    """Extracted voice patterns from user's successful posts."""
    
    # Sentence starters that work
    opening_patterns: list[str]
    
    # Structure template
    structure: list[str]  # e.g., ["complaint", "context", "cta"]
    
    # Tone markers
    emoji_frequency: float  # 0.0 - 1.0
    indonesian_ratio: float  # 0.0 - 1.0
    exclamation_ratio: float  # 0.0 - 1.0
    
    # Content characteristics
    avg_length: int  # characters
    uses_specific_numbers: bool
    uses_questions: bool
    
    # Examples for reference
    example_posts: list[dict[str, Any]]
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceProfile":
        return cls(**data)


def _get_top_posts(limit: int = 3) -> list[tuple[MyPost, int]]:
    """Get top performing posts by views."""
    with session_scope() as session:
        # Get latest insights for each post
        latest_insights: dict[str, MyPostInsight] = {}
        for ins in session.scalars(
            select(MyPostInsight).order_by(desc(MyPostInsight.fetched_at))
        ).all():
            latest_insights.setdefault(ins.thread_id, ins)
        
        # Get posts with views
        posts_with_views: list[tuple[MyPost, int]] = []
        for post in session.scalars(select(MyPost)).all():
            insight = latest_insights.get(post.thread_id)
            views = insight.views if insight else 0
            posts_with_views.append((post, views))
        
        # Sort by views desc
        posts_with_views.sort(key=lambda x: x[1], reverse=True)
        
        return posts_with_views[:limit]


def _extract_opening_patterns(posts: list[MyPost]) -> list[str]:
    """Extract sentence starters from posts."""
    patterns = []
    
    for post in posts:
        text = post.text.strip()
        if not text:
            continue
        
        # Get first sentence (split by . ! ? but keep the delimiter)
        first_sentence = re.split(r'([.!?]\s+)', text)[0]
        
        # Clean and normalize
        first_sentence = first_sentence.strip().lower()
        
        # Extract pattern (first 3-5 words)
        words = first_sentence.split()
        if len(words) >= 3:
            pattern = " ".join(words[:min(5, len(words))])
            patterns.append(pattern)
    
    return patterns


def _analyze_structure(post: MyPost) -> list[str]:
    """Analyze post structure."""
    text = post.text.strip()
    if not text:
        return []
    
    structure = []
    
    # Check for complaint/frustration opening
    complaint_markers = [
        r"\bsusah\b", r"\bbingung\b", r"\bribet\b", r"\bmasalah\b",
        r"\bstuck\b", r"\bfrustrat", r"\bangry\b", r"\bsick of\b",
        r"\btired of\b", r"\bcan't\b", r"\bsusah\b",
    ]
    text_lower = text.lower()
    has_complaint = any(re.search(m, text_lower) for m in complaint_markers)
    
    # Check for context/detail
    has_context = len(text) > 100
    
    # Check for CTA/question
    cta_markers = [r"\?", r"\bDM\b", r"\blink in bio\b", r"\bapply\b", r"\bcheck out\b"]
    has_cta = any(re.search(m, text_lower) for m in cta_markers)
    
    if has_complaint:
        structure.append("complaint")
    if has_context:
        structure.append("context")
    if has_cta:
        structure.append("cta")
    
    return structure if structure else ["personal_story"]


def _analyze_tone(post: MyPost) -> dict[str, float]:
    """Analyze tone markers."""
    text = post.text
    
    # Emoji frequency
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE
    )
    emoji_count = len(emoji_pattern.findall(text))
    emoji_freq = min(1.0, emoji_count / max(1, len(text) / 100))
    
    # Indonesian vs English ratio
    indonesian_words = ["gue", "lu", "kamu", "aku", "nya", "yang", "dan", "ini", "itu", 
                       "juga", "sih", "dong", "kan", "ya", "tidak", "nggak", "gak",
                       "susah", "ribet", "bingung", "senang", "bagus", "keren"]
    words = text.lower().split()
    indo_count = sum(1 for w in words if w in indonesian_words)
    indo_ratio = indo_count / max(1, len(words))
    
    # Exclamation ratio
    excl_count = text.count("!")
    excl_ratio = min(1.0, excl_count / max(1, len(text) / 50))
    
    return {
        "emoji_frequency": round(emoji_freq, 2),
        "indonesian_ratio": round(indo_ratio, 2),
        "exclamation_ratio": round(excl_ratio, 2),
    }


def _check_specific_numbers(post: MyPost) -> bool:
    """Check if post uses specific numbers."""
    text = post.text
    number_patterns = [
        r"\$\d+",
        r"Rp[\d.]+",
        r"\d+%",
        r"\d+\s*(jam|menit|hari|minggu|bulan|tahun)",
        r"\d+\s*(hours?|minutes?|days?|weeks?|months?|years?)",
        r"20\d{2}",
        r"\d+\s*(ribu|juta|miliar)",
        r"\d+\s*(k|thousand|million|billion)",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in number_patterns)


def extract_voice_profile() -> VoiceProfile:
    """Extract voice profile from user's top posts."""
    top_posts = _get_top_posts(limit=3)
    
    if not top_posts:
        # Return default profile if no posts
        return VoiceProfile(
            opening_patterns=["susah juga ya", "baru aja", "akhirnya"],
            structure=["complaint", "context", "cta"],
            emoji_frequency=0.3,
            indonesian_ratio=0.6,
            exclamation_ratio=0.4,
            avg_length=150,
            uses_specific_numbers=True,
            uses_questions=True,
            example_posts=[],
        )
    
    # Extract patterns
    posts = [p for p, _ in top_posts]
    opening_patterns = _extract_opening_patterns(posts)
    
    # Analyze structure (use most common)
    structures = [_analyze_structure(p) for p in posts]
    structure = structures[0] if structures else ["personal_story"]
    
    # Analyze tone (average)
    tone_metrics = [_analyze_tone(p) for p in posts]
    avg_emoji = sum(t["emoji_frequency"] for t in tone_metrics) / len(tone_metrics)
    avg_indo = sum(t["indonesian_ratio"] for t in tone_metrics) / len(tone_metrics)
    avg_excl = sum(t["exclamation_ratio"] for t in tone_metrics) / len(tone_metrics)
    
    # Content characteristics
    avg_length = int(sum(len(p.text) for p in posts) / len(posts))
    uses_numbers = any(_check_specific_numbers(p) for p in posts)
    uses_questions = any("?" in p.text for p in posts)
    
    # Examples
    examples = [
        {
            "text": p.text[:200] + "..." if len(p.text) > 200 else p.text,
            "views": views,
        }
        for p, views in top_posts
    ]
    
    return VoiceProfile(
        opening_patterns=opening_patterns,
        structure=structure,
        emoji_frequency=round(avg_emoji, 2),
        indonesian_ratio=round(avg_indo, 2),
        exclamation_ratio=round(avg_excl, 2),
        avg_length=avg_length,
        uses_specific_numbers=uses_numbers,
        uses_questions=uses_questions,
        example_posts=examples,
    )


def get_voice_profile() -> dict[str, Any]:
    """Get voice profile as dictionary (for CLI/API use)."""
    profile = extract_voice_profile()
    return profile.to_dict()


if __name__ == "__main__":
    # Test extraction
    profile = extract_voice_profile()
    print(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False))
