## 2026-04-15
- `routes_experiments.py` had two separate account-resolution call sites; both needed the same None-safe fallback to satisfy diagnostics.
- `reportOptionalMemberAccess` surfaced when `acct.id` was read without narrowing `None` first.
## 2026-04-15
- `ruff` was not available on the shell PATH; verified lint with `./.venv/bin/ruff check ...` instead.
