from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def configured_test_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(db)

    from threads_analytics.db import init_db

    init_db()
    yield


@pytest.fixture()
def default_account(configured_test_db):
    from threads_analytics.account_scope import get_or_create_default_account
    from threads_analytics.db import session_scope

    with session_scope() as session:
        account = get_or_create_default_account(session)
    return account


@pytest.fixture()
def accounts(configured_test_db):
    from threads_analytics.db import session_scope
    from threads_analytics.models import Account

    with session_scope() as session:
        account_a = Account(
            slug="account-a",
            name="Account A",
            threads_access_token="token-a",
            threads_user_id="user-a",
            threads_handle="handle_a",
            enabled_capabilities=["analytics"],
            soft_caps={"daily_runs": 10},
        )
        account_b = Account(
            slug="account-b",
            name="Account B",
            threads_access_token="token-b",
            threads_user_id="user-b",
            threads_handle="handle_b",
            enabled_capabilities=["analytics"],
            soft_caps={"daily_runs": 5},
        )
        session.add_all([account_a, account_b])
        session.flush()
    return account_a, account_b


@pytest.fixture()
def account_a(accounts):
    return accounts[0]


@pytest.fixture()
def account_b(accounts):
    return accounts[1]
