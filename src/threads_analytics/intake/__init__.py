"""Daily intake layer: fetch external signals, dedupe, filter, store."""

from __future__ import annotations

from .angle_it import AngleVariant, generate_angle_variants
from .fetchers import fetch_all_sources, RawIntakeItem
from .dedupe import dedupe_items
from .filter import filter_and_summarize_with_llm
from .runner import expire_old_items, run_intake_cycle

__all__ = [
    "fetch_all_sources",
    "dedupe_items",
    "filter_and_summarize_with_llm",
    "run_intake_cycle",
    "expire_old_items",
    "generate_angle_variants",
    "AngleVariant",
    "RawIntakeItem",
]
