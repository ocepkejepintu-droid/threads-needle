"""Shared helpers for Typer CLI commands."""

from __future__ import annotations

import typer

from .account_scope import get_account_by_slug, get_or_create_default_account
from .db import session_scope


def resolve_account_or_exit(account: str | None):
    """Return the requested account or exit the CLI with a user-facing error."""
    with session_scope() as session:
        acct = (
            get_account_by_slug(session, account)
            if account
            else get_or_create_default_account(session)
        )
    if acct is None:
        typer.secho(f"Unknown account slug: {account}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return acct


def update_env_file(key: str, value: str, path: str = ".env") -> None:
    """Update one key in a dotenv-style file, creating the file if needed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    found = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
