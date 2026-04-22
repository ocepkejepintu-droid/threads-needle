"""Tests for Angle-It JSON parsing and response handling."""

from __future__ import annotations

import pytest

from threads_analytics.intake.angle_it import (
    _forgiving_json_parse,
    _clamp,
    _VALID_MECHANICS,
)


def test_forgiving_json_parse_plain():
    text = '{"variants": [{"hook": "test", "body": "body", "mechanic": "token_receipt", "rubric": {}, "reasoning": "r"}]}'
    data = _forgiving_json_parse(text)
    assert "variants" in data
    assert len(data["variants"]) == 1


def test_forgiving_json_parse_markdown_fences():
    text = '```json\n{"variants": []}\n```'
    data = _forgiving_json_parse(text)
    assert "variants" in data


def test_forgiving_json_parse_trailing_comma():
    text = '{"variants": [{"hook": "h", "body": "b", "mechanic": "token_receipt", "rubric": {"a": 1,}, "reasoning": "r",}],}'
    data = _forgiving_json_parse(text)
    assert len(data["variants"]) == 1


def test_forgiving_json_parse_single_quotes():
    text = "{'variants': [{'hook': 'h', 'body': 'b', 'mechanic': 'token_receipt', 'rubric': {}, 'reasoning': 'r'}]}"
    data = _forgiving_json_parse(text)
    assert len(data["variants"]) == 1


def test_forgiving_json_parse_extra_text():
    text = 'Here is the JSON:\n\n{"variants": []}\n\nHope that helps!'
    data = _forgiving_json_parse(text)
    assert "variants" in data


def test_forgiving_json_parse_empty_raises():
    with pytest.raises(ValueError):
        _forgiving_json_parse("")


def test_clamp():
    assert _clamp(50, 0, 20) == 20
    assert _clamp(-5, 0, 20) == 0
    assert _clamp(10, 0, 20) == 10
    assert _clamp(None, 0, 20) == 0


def test_valid_mechanics_set():
    assert "binary_verdict" in _VALID_MECHANICS
    assert "token_receipt" in _VALID_MECHANICS
    assert "signal" in _VALID_MECHANICS
    assert "invalid" not in _VALID_MECHANICS
