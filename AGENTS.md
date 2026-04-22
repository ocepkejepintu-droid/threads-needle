# threads-analytics — Agent Guide

**Generated:** 2026-04-15 · **Commit:** c8a70f5 · **Branch:** main

Fast reference for working in this codebase. When in doubt, trust executable sources (pyproject.toml, code) over this file.

## One-liner

Local-first analytics dashboard for a personal Threads account. Python 3.11+, FastAPI, SQLite, Claude via Anthropic SDK.

## Dev Commands

```bash
# Setup (once)
pip install -e ".[dev]"
cp .env.example .env
# Fill META_APP_ID, META_APP_SECRET, ANTHROPIC_API_KEY in .env
python scripts/setup_token.py      # OAuth flow, populates .env with access token

# Run
threads-analytics whoami           # verify token
threads-analytics run              # full ingest → analyze → recommend cycle (5–15 min)
threads-analytics serve            # dashboard at http://localhost:8000
threads-analytics backfill         # populate historical ground truth from existing posts
threads-analytics refresh          # rotate 60-day access token (run every ~50 days)

# Code quality
pytest                             # all tests (some require ANTHROPIC_API_KEY in .env)
pytest -k "not full_pipeline"      # skip expensive test that calls real Anthropic API
pytest tests/test_predicates.py    # fast pure-logic tests only
ruff check .                       # lint (line-length 100, target py311)
```

## Architecture

```
src/threads_analytics/
  cli.py              # Typer CLI entrypoint (threads-analytics command)
  config.py           # Pydantic Settings, .env loading with special override logic
  db.py               # SQLAlchemy engine, session_scope(), init_db()
  models.py           # SQLAlchemy ORM (Base, all tables)
  threads_client.py   # Thin wrapper around Threads Graph API
  pipeline.py         # run_full_cycle() orchestrates ingest → analyze → recommend
  web/
    app.py            # FastAPI factory (create_app), lifespan manages scheduler
    routes.py         # All HTTP routes
    templates/        # Jinja2 templates (no build step, vanilla HTML/CSS)
    static/           # CSS, JS, assets
tests/                # pytest tests
scripts/              # setup_token.py and OAuth variants
```

## Critical Patterns

**Database:**
- SQLite only. `init_db()` creates tables automatically via `Base.metadata.create_all()`.
- Always use `session_scope()` context manager for transactions.
- Tests use `tmp_path` for isolated DBs; monkeypatch `DATABASE_URL` and clear `config.get_settings.cache_clear()`.

**Configuration:**
- `.env` is the source of truth. `config.py` loads it with special logic: `.env` values fill in missing env vars (empty shell vars like `ANTHROPIC_API_KEY=` would otherwise shadow .env).
- `DATABASE_URL=sqlite:///data/threads.db` → `_resolve_sqlite_url()` converts relative paths to absolute rooted at project root. Prevents "readonly database" or "no such table" errors when running from different cwd.

**Environment Secrets:**
- `ANTHROPIC_API_KEY` required for LLM features (topic extraction, recommendations).
- `META_APP_ID` + `META_APP_SECRET` required for Threads API.
- Token refresh: `refresh` command exchanges long-lived token and rewrites `.env`.

**Testing:**
- `pytest` discovers all tests.
- `test_full_pipeline_with_mock_threads.py` requires `ANTHROPIC_API_KEY` (skipped otherwise). It makes real Anthropic calls but mocks Threads API.
- Fast tests (no API calls): `test_predicates.py`, `test_content_rules.py`, `test_verdict.py`.

**Scheduler:**
- FastAPI lifespan starts/stops APScheduler in `scheduler.py`.
- Background tasks run via scheduler (not async workers).

**Style:**
- See `STYLE_GUIDE.md` for UI color semantics, typography, spacing. TL;DR: white cards, system fonts, one hero per page, hypothesis labels on all claims.

## Common Gotchas

- **Token expiry:** Access tokens last 60 days. Run `refresh` every ~50 days or OAuth again.
- **Rate limits:** Threads API uses impression-based limits. Full runs on 1000+ posts take 5–15 min.
- **Keyword search:** Requires Meta App Review for `threads_keyword_search` permission. Without it, affinity discovery is empty but rest works.
- **Database path:** Never run SQLite with relative paths from different working directories. `_resolve_sqlite_url()` handles this—ensure you're using `get_settings().database_url`.
- **Environment loading:** If tests or CLI can't find API keys, check if shell has empty vars shadowing .env. `config.py` logic should handle this, but explicit `export ANTHROPIC_API_KEY=...` overrides .env.

## Entrypoints

- **CLI:** `threads_analytics.cli:app` (defined in pyproject.toml `[project.scripts]`)
- **Web:** `threads_analytics.web.app:create_app` (uvicorn factory)
- **Module:** All imports from `threads_analytics` package (src layout).

## File References

- Config & env: `src/threads_analytics/config.py`, `.env.example`
- DB & models: `src/threads_analytics/db.py`, `src/threads_analytics/models.py`
- CLI commands: `src/threads_analytics/cli.py`
- Web routes: `src/threads_analytics/web/routes.py`
- Tests: `tests/` (no conftest.py, fixtures in test files)
