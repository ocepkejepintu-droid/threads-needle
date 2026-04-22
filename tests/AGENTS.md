# tests — Test Suite

**Scope:** pytest suite with pure-logic and integration tests.

## Structure

```
tests/
  test_predicates.py                  # Fast pure-logic tests
  test_verdict.py                     # Statistical engine tests
  test_content_rules.py               # Content validation tests
  test_affinity_scoring.py            # Affinity scoring logic
  test_leads.py                       # Lead engine tests
  test_web_routes.py                  # FastAPI route tests
  test_full_pipeline_with_mock_threads.py  # Integration test (requires Anthropic key)
```

## Conventions

- **Isolated DBs:** Use `tmp_path` fixture and monkeypatch `DATABASE_URL` to a temp SQLite file.
- **Settings cache:** After monkeypatching DB URL, call `config.get_settings.cache_clear()`.
- **No `conftest.py`:** Fixtures are defined locally in test files.
- **Mock client:** `test_full_pipeline_with_mock_threads.py` patches `threads_client` but makes real Anthropic API calls.

## Anti-Patterns

- **Do not** run `test_full_pipeline_with_mock_threads.py` without `ANTHROPIC_API_KEY` in `.env` — it will be skipped.
- **Do not** commit to a real database in tests — always use `tmp_path`.
- **Do not** rely on module-level state persisting across test functions.
