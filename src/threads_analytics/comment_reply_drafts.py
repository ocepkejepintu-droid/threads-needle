"""LLM drafting for inbound comment replies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final, Protocol, cast

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .llm_client import get_llm_client
from .models import CommentInbox, MyPost, MyReply, YouProfile

log = logging.getLogger(__name__)


class _DraftResponse(Protocol):
    text: str


class _DraftLLMClient(Protocol):
    def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> _DraftResponse: ...


_FALLBACK_VOICE_CONTEXT: Final[str] = """Voice context:
- No synthesized YouProfile is available yet.
- Sound like the account owner: warm, direct, thoughtful, and human.
- Reference the specific comment when useful.
- Stay concise, helpful, and natural.
- Avoid hype, sales language, hashtags, and generic AI-assistant phrasing."""


def _render_mapping_items(mapping: dict[object, object]) -> list[str]:
    rendered: list[str] = []
    for key, val in mapping.items():
        if val:
            rendered.append(f"{key}: {val}")
    return rendered


def _stringify_listish(value: object) -> list[str]:
    if isinstance(value, list):
        items: list[str] = []
        for item in cast(list[object], value):
            if isinstance(item, dict):
                rendered = "; ".join(_render_mapping_items(cast(dict[object, object], item)))
                if rendered:
                    items.append(rendered)
            elif item:
                items.append(f"{item}")
        return items
    if isinstance(value, dict):
        return _render_mapping_items(cast(dict[object, object], value))
    if value:
        return [f"{value}"]
    return []


def _build_voice_context(session: Session, account_id: int) -> str:
    """Build account-specific voice scaffolding for comment drafting."""

    you_profile = session.scalar(
        select(YouProfile)
        .where(YouProfile.account_id == account_id)
        .order_by(desc(YouProfile.created_at))
        .limit(1)
    )
    if you_profile is None:
        return _FALLBACK_VOICE_CONTEXT

    recent_posts = session.scalars(
        select(MyPost)
        .where(MyPost.account_id == account_id)
        .order_by(desc(MyPost.created_at))
        .limit(3)
    ).all()
    recent_replies = session.scalars(
        select(MyReply)
        .where(MyReply.account_id == account_id)
        .order_by(desc(MyReply.created_at))
        .limit(3)
    ).all()

    sections = ["Voice context:"]
    if you_profile.core_identity:
        sections.append(f"Core identity: {you_profile.core_identity}")

    voice_traits = _stringify_listish(you_profile.distinctive_voice_traits)
    if voice_traits:
        sections.append("Distinctive voice traits:")
        sections.extend(f"- {trait}" for trait in voice_traits[:5])

    stylistic_signatures = _stringify_listish(you_profile.stylistic_signatures)
    if stylistic_signatures:
        sections.append("Stylistic signatures:")
        sections.extend(f"- {signature}" for signature in stylistic_signatures[:5])

    exemplar_posts = _stringify_listish(you_profile.posts_that_sound_most_like_you)
    if exemplar_posts:
        sections.append("Posts that sound most like the account owner:")
        sections.extend(f"- {example}" for example in exemplar_posts[:3])

    if recent_posts:
        sections.append("Recent posts:")
        sections.extend(f"- {post.text.strip()}" for post in recent_posts if post.text.strip())

    if recent_replies:
        sections.append("Recent replies:")
        sections.extend(f"- {reply.text.strip()}" for reply in recent_replies if reply.text.strip())

    return "\n".join(sections)


def generate_comment_reply_draft(
    session: Session,
    account_id: int,
    comment_text: str,
    comment_author_username: str,
) -> str:
    """Generate a concise in-voice reply draft for a comment."""

    voice_context = _build_voice_context(session, account_id)
    system_prompt = f"""You draft comment replies for a Threads account owner.

{voice_context}

Write a single reply that sounds like the account owner, not an assistant.

Guidelines:
- Keep it under 280 characters.
- Be helpful, specific, and grounded in the actual comment.
- Sound warm, sharp, and human.
- Avoid generic encouragement, empty praise, sales pitch energy, or AI-helper tone.
- No hashtags. No emojis unless clearly necessary. No quotation marks around the whole reply.
- Output only the reply text."""

    user_prompt = f"""Draft a reply to this Threads comment.

Comment author: @{comment_author_username}
Comment text: {comment_text}"""

    try:
        client = cast(_DraftLLMClient, get_llm_client())
        response = client.create_message(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=300,
            temperature=0.7,
        )
        reply = response.text.strip()
        if len(reply) > 280:
            reply = reply[:277] + "..."
        return reply
    except Exception as exc:
        log.error("Failed to generate comment reply draft: %s", exc)
        fallback = f"Thanks for the thoughtful comment, @{comment_author_username} — appreciate you reading.".strip()
        if len(fallback) > 280:
            fallback = fallback[:277] + "..."
        return fallback


def draft_replies_for_inbox(
    session: Session,
    account_id: int,
    inbox_ids: list[int] | None = None,
    force_regenerate: bool = False,
) -> int:
    """Generate AI drafts for inbox comments awaiting a reply draft."""

    stmt = select(CommentInbox).where(
        CommentInbox.account_id == account_id,
        CommentInbox.status != CommentInbox.STATUS_IGNORED,
    )
    if inbox_ids is not None:
        stmt = stmt.where(CommentInbox.id.in_(inbox_ids))
    if not force_regenerate:
        stmt = stmt.where(CommentInbox.ai_draft_reply.is_(None))
    pending = session.scalars(stmt.order_by(CommentInbox.comment_created_at.desc())).all()

    generated = 0
    for item in pending:
        try:
            item.ai_draft_reply = generate_comment_reply_draft(
                session=session,
                account_id=account_id,
                comment_text=item.comment_text,
                comment_author_username=item.comment_author_username,
            )
            item.ai_draft_generated_at = datetime.now(timezone.utc)
            generated += 1
        except Exception as exc:
            log.error("Failed to draft inbox reply for comment %s: %s", item.comment_thread_id, exc)

    return generated
