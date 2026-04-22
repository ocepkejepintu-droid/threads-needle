"""Tests for outcome tagger classification logic."""

from threads_analytics.outcome_tagger import classify_outcome


def test_classify_zero_reply():
    assert classify_outcome(views=100, likes=10, replies=0, reach_multiple=1.0) == "zero_reply"


def test_classify_breakout_by_reach():
    assert classify_outcome(views=50000, likes=100, replies=5, reach_multiple=5.0) == "breakout"


def test_classify_breakout_by_replies():
    assert classify_outcome(views=1000, likes=50, replies=20, reach_multiple=1.0) == "breakout"


def test_classify_healthy():
    assert classify_outcome(views=3000, likes=50, replies=5, reach_multiple=1.5) == "healthy"


def test_classify_stall():
    assert classify_outcome(views=800, likes=20, replies=2, reach_multiple=1.0) == "stall"


def test_classify_fallback_healthy():
    # Has replies but doesn't fit breakout/healthy/stall exactly
    assert classify_outcome(views=500, likes=10, replies=1, reach_multiple=0.3) == "healthy"
