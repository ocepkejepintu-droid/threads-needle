- `ThreadsClient` reply-listing methods follow a best-effort pattern: call `_get()`, catch `httpx.HTTPError`, log a warning, and return an empty list instead of raising.
- Reply/comment timestamp parsing should stay centralized through `_parse_ts()`, and optional author metadata from Threads (`username`, `user_id`, `permalink`) should be read with `.get()` so sparse payloads do not crash parsing.
- New account-scoped workflow tables should follow the repo's SQLAlchemy 2.0 `Mapped[...] = mapped_column(...)` style, default `account_id` to `ForeignKey("accounts.id")` with `default=1`, and use `DateTime(timezone=True)` for workflow timestamps/claiming state.
- Comment inbox polling should stay idempotent by selecting existing `CommentInbox` rows on `(account_id, comment_thread_id)` and only refreshing mutable fetch metadata (`comment_text`, `comment_permalink`, `last_seen_at`) so workflow state like `sent`, `send_failed`, and `ignored` survives re-polls.
- Comment-reply drafting works best with an account-scoped `YouProfile` snapshot plus a few freshest `MyPost` and `MyReply` examples; the prompt should explicitly ban generic AI-helper and sales-pitch tone while still grounding the model in concrete voice evidence.
- Inbox redrafting should only fill `ai_draft_reply`/`ai_draft_generated_at` for rows missing an AI draft and must leave any human-edited `final_reply` untouched, even when generating a fresh draft for the same inbox item.
- Comment inbox approval must stay a pure state transition (`drafted/send_failed -> approved`) with no implicit API side effects; sending is a separate explicit action that re-runs the publish gate and only creates a `PublishLedger` row after a successful `create_reply()` publish.
- Comment send failures should preserve `final_reply`, move the inbox row to `send_failed`, and store a human-readable `send_error` so operators can retry without losing their edited text.
- Comment-run orchestration should treat Mission Control sync stages (`comment_inbox_sync`, `comment_drafts`) as best-effort pre-lead stages: persist their per-stage status in `Run.stage_progress`, include their summaries in the returned payload, and continue into lead search even if either stage fails.
- Account-scoped Mission Control mutations should reject legacy unprefixed POSTs with `reject_ambiguous_account_mutation()`, and bulk routes should 404 if any requested `CommentInbox` id falls outside the current account instead of silently partial-applying.
- Comment inbox account isolation currently lives in `web.routes_comments` scoping helpers (`_validate_scoped_ids`, `_load_scoped_item`); backend mutation helpers remain ID-based, so tests should assert cross-account no-ops by failing scope validation before calling approve/send/edit operations.
- In-memory quota-gate tests can exercise the real `gate_send_comment()` path by monkeypatching `threads_analytics.publish_gate.session_scope` to reuse the test `Session`, letting the second send see the first send's uncommitted `sent_at` update and trip the daily reply cap.

## Task 8: Mission Control Template (2026-04-17)

- The `content_pipeline.html` pattern is the gold standard for dashboard-style pages: full-width override via scoped CSS (`max-width: none !important` on `.main-col .main-inner`), inline `<style>` block with column/card styles, modal overlays, toast notifications, and all JS inline at the bottom.
- `routes_comments.py` already has a `_render_comments_template()` fallback: when `comments_mission_control.html` is missing, it renders bare HTML. When present, it uses `TemplateResponse`. Tests verify the template renders by checking text content in the response.
- The `with_account_context()` helper injects `account_path_prefix`, `account_home_path`, and `handle` into every template context. Use `{{ account_path_prefix }}` for all API fetch URLs.
- Sidebar nav links in `base.html` use the `{{ account_prefix }}` Jinja variable (set from `account_path_prefix|default('/accounts/default')`). SVG icons are inline with `viewBox="0 0 24 24"`.
- Comments banner JS in `base.html` has its own `COMMENTS_STAGES`, `fmtCommentsStep`, `commentsProgressPct` functions. The progress denominator was hardcoded to 2 (now 4 with `comment_inbox_sync` and `comment_drafts` stages added).
- The template `counts` dict uses string keys (`counts.drafted`, `counts.sent`) accessible via Jinja dot notation. The `inbox_items` list has dict items with snake_case keys.
- CSS uses CSS custom properties (`var(--surface)`, `var(--border)`, etc.) from `style.css`. Dark mode support comes free via the CSS variable overrides in `:root[data-theme="dark"]`.

## Task 10 — Route/UI Regression Coverage (2026-04-17)

- **Gate bypass pattern for route tests**: Monkeypatch `threads_analytics.comment_inbox.gate_send_comment` to return `GateResult(allowed=True)`. This bypasses all gate checks (capabilities, quota, slop, brand, token) without needing to set account capabilities or tokens.
- **ThreadsClient mock for route tests**: Patch `threads_analytics.threads_client.ThreadsClient.from_account` (not comment_inbox, since it uses a local import). This is the canonical class location.
- **`_seed_comment_inbox_item` helper**: Reusable helper for seeding CommentInbox rows in route tests. Requires account_id and run_id from `populated_app` fixture.
- **Edit demotion**: `edit_comment_reply` demotes `approved→drafted` and clears `approved_at`. No gate mocking needed for edit tests.
- **Route test structure**: All 4 new tests use `populated_app` + `default_account` fixtures from `fixtures_accounts.py`. The `populated_app` fixture already creates a Run for the default account, queryable via `session.query(Run).filter_by(account_id=default_account.id).first()`.
- **Ruff/pytest in venv**: Use `.venv/bin/ruff` and `.venv/bin/pytest` — not system python.

## Blocker: Final Verification Wave Approval (2026-04-17)

- All implementation tasks 1-10 are complete.
- All fixes (regenerate-selected bug, pipeline fail-soft, test file reconstruction) are applied and verified.
- Tests: 46 passed across test_comment_inbox.py, test_threads_client_comments.py, test_web_routes.py, test_reply_workflow.py.
- Lint: ruff check src/tests passes.
- UI smoke test: /accounts/default/comments returns 200 OK.
- The plan explicitly requires user explicit approval before marking F1-F4 complete. Waiting for user to say "approved" or "okay".
