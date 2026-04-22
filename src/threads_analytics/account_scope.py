"""Helpers for shared-database account scoping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from .models import Account, GeneratedIdea

DEFAULT_ACCOUNT_SLUG = "default"
DEFAULT_ACCOUNT_NAME = "Default Account"


def get_account_by_slug(session: Session, slug: str) -> Account | None:
    return session.scalar(select(Account).where(Account.slug == slug))


def list_accounts(session: Session) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.id)).all())


def get_or_create_default_account(session: Session) -> Account:
    account = get_account_by_slug(session, DEFAULT_ACCOUNT_SLUG)
    if account is not None:
        return account

    account = Account(
        slug=DEFAULT_ACCOUNT_SLUG,
        name=DEFAULT_ACCOUNT_NAME,
        enabled_capabilities=[],
        soft_caps={},
    )
    session.add(account)
    session.flush()
    return account


def require_idea_ownership(
    session: Session, idea_id: int, account_slug: str | None = None
) -> GeneratedIdea | None:
    idea = session.get(GeneratedIdea, idea_id)
    if idea is None:
        return None
    account = (
        get_account_by_slug(session, account_slug)
        if account_slug
        else get_or_create_default_account(session)
    )
    if account is None:
        return None
    return idea if idea.account_id == account.id else None


def scope_statement(stmt: Select[Any], model: type, account_id: int) -> Select[Any]:
    return stmt.where(model.account_id == account_id)


def get_scoped(session: Session, model: type, ident, account_id: int):
    record = session.get(model, ident)
    if record is None:
        return None
    return record if record.account_id == account_id else None
