"""Tests for leads module."""
import pytest
from datetime import datetime, timezone
from threads_analytics.leads import (
    generate_reply_draft,
    should_skip_post,
)
from threads_analytics.models import Lead, LeadSource


def test_should_skip_own_post():
    """Your own posts should be skipped."""
    your_user_id = "user_123"
    author_user_id = "user_123"  # Same as your_user_id
    post_text = "This is my own post"
    reply_count = 0
    
    skip, reason = should_skip_post(
        post_text=post_text,
        author_user_id=author_user_id,
        your_user_id=your_user_id,
        reply_count=reply_count,
    )
    
    assert skip is True
    assert reason == "own_post"


def test_should_skip_too_many_replies():
    """Posts with >10 replies should be skipped."""
    your_user_id = "user_123"
    author_user_id = "user_456"  # Different from your_user_id
    post_text = "This post has many replies"
    reply_count = 15  # > 10 replies
    
    skip, reason = should_skip_post(
        post_text=post_text,
        author_user_id=author_user_id,
        your_user_id=your_user_id,
        reply_count=reply_count,
    )
    
    assert skip is True
    assert reason == "too_many_replies"


def test_should_not_skip_valid_post():
    """Valid posts should not be skipped."""
    your_user_id = "user_123"
    author_user_id = "user_456"  # Different from your_user_id
    post_text = "This is a normal post with enough text content"
    reply_count = 3  # <= 10 replies
    
    skip, reason = should_skip_post(
        post_text=post_text,
        author_user_id=author_user_id,
        your_user_id=your_user_id,
        reply_count=reply_count,
    )
    
    assert skip is False
    assert reason is None


def test_should_skip_too_short():
    """Very short posts (<20 chars) should be skipped."""
    your_user_id = "user_123"
    author_user_id = "user_456"  # Different from your_user_id
    post_text = "Hi"  # Very short (<20 chars)
    reply_count = 0
    
    skip, reason = should_skip_post(
        post_text=post_text,
        author_user_id=author_user_id,
        your_user_id=your_user_id,
        reply_count=reply_count,
    )
    
    assert skip is True
    assert reason == "too_short"
