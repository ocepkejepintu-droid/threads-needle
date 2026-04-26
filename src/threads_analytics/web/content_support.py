"""Shared support helpers for content routes."""

from __future__ import annotations

from datetime import datetime
from json import JSONDecodeError
from typing import Any

from fastapi import Request

from ..config import get_schedule_timezone


def validate_image_bytes(data: bytes) -> bool:
    """Return true when bytes look like one of the accepted image formats."""
    if len(data) < 12:
        return False
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


# Tier-slot schedule uses local schedule timezone; tuple is weekday, hour, minute, tier, label.
SLOT_SCHEDULE = [
    (0, 11, 0, "hero", "Mon 11:00"),
    (1, 12, 0, "engine", "Tue 12:00"),
    (2, 12, 0, "engine", "Wed 12:00"),
    (2, 20, 0, "engine", "Wed 20:00"),
    (3, 12, 0, "engine", "Thu 12:00"),
    (4, 11, 0, "hero", "Fri 11:00"),
    (4, 14, 0, "engine", "Fri 14:00"),
    (5, 10, 0, "signal", "Sat 10:00"),
]


def slot_matches_tier(slot_time: datetime, tier: str | None) -> bool:
    """Check if a scheduled datetime matches a tier's allowed slots."""
    if tier is None:
        return True
    local = slot_time.astimezone(get_schedule_timezone())
    weekday = local.weekday()
    hour = local.hour
    minute = local.minute
    return any(
        day == weekday and slot_hour == hour and slot_minute == minute and slot_tier == tier
        for day, slot_hour, slot_minute, slot_tier, _ in SLOT_SCHEDULE
    )


def allowed_slot_labels(tier: str | None) -> list[str]:
    """Return human-readable slot labels for a content tier."""
    return [label for _, _, _, slot_tier, label in SLOT_SCHEDULE if slot_tier == tier]


async def request_json_or_none(request: Request) -> dict[str, Any] | None:
    """Parse JSON request bodies, returning None for malformed JSON payloads."""
    try:
        data = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
