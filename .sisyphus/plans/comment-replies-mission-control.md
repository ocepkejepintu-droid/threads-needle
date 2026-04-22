# Comment Replies Mission Control

## TL;DR
> **Summary**: Add an account-scoped Mission Control page for inbound comments on the user’s own Threads posts. V1 uses scheduled polling only, auto-generates LLM reply drafts in the user’s voice, supports bulk approval, and requires an explicit manual send action.
> **Deliverables**:
> - inbound comment inbox persistence + polling ingestion
> - account-scoped Mission Control route, template, and JSON actions
> - LLM draft generation for comments using existing voice/profile context
> - bulk approve + explicit send-selected workflow with failure recovery
> - automated test coverage for client, ingestion, routes, workflow, and account isolation
> **Effort**: Large
> **Parallel**: YES - 2 waves
> **Critical Path**: 1 → 2 → 3 → 4 → 5 → 6/7 → 8 → 9/10

## Context
### Original Request
Create a new Mission Control page for comment replies: catch comments on my posts, use an LLM to draft replies, and support a semi-automated workflow.

### Interview Summary
- Scope is **comments on my own posts only**.
- Automation mode is **draft generation only**; **sending stays manual**.
- Approval mode is **bulk approve**.
- Default draft tone is **the user’s voice, lightly edited**.
- V1 ingestion is **scheduled polling only**; **no webhooks**.
- Execution strategy is **tests-after**, but with explicit test cases defined up front.

### Metis Review (gaps addressed)
- Avoid overloading `Lead` / `LeadReply` as the storage model for inbound comments; use a dedicated inbox entity and reuse only drafting/gate/send primitives where appropriate.
- Keep approval and send as separate states/actions; approval must never auto-publish.
- Enforce idempotency on external comment IDs plus account scope.
- Bound historical ingestion to a deliberate v1 window to avoid backfill/performance creep.
- Make nested replies an explicit v1 exclusion rather than an accidental omission.

## Work Objectives
### Core Objective
Ship a bounded Mission Control feature that turns inbound comments on the user’s own Threads posts into editable, approveable, manually sendable reply drafts without introducing auto-send, webhook complexity, or lead-gen semantics into the inbox.

### Deliverables
- New DB-backed inbox entity for inbound comments with explicit workflow state.
- Threads client support for fetching top-level replies to owned posts.
- Polling ingestion service for recent owned posts with deduplication/upsert behavior.
- Comment-specific LLM drafting service using existing voice/profile context.
- Manual review workflow: edit, bulk approve, unapprove, ignore, send selected.
- Account-scoped Mission Control page + JSON endpoints.
- Route/status integration with existing comment-run controls.
- Automated tests for client, ingestion, route rendering, account isolation, approval boundary, send success/failure, and quota blocking.

### Definition of Done (verifiable conditions with commands)
- `pytest tests/test_threads_client_comments.py -q` passes.
- `pytest tests/test_comment_inbox.py -q` passes.
- `pytest tests/test_web_routes.py -k "comments or mission_control or prefixed" -q` passes.
- `pytest tests/test_reply_workflow.py -q` passes.
- `ruff check src tests` passes.

### Must Have
- Account-prefixed Mission Control page at a stable route.
- Polling-only v1 comment capture for recent owned posts.
- Dedicated inbox persistence keyed by account + external comment thread ID.
- LLM drafts generated automatically for new inbound comments.
- Bulk approval and explicit bulk/manual send from approved items.
- Draft edit support that invalidates stale approval.
- Send failures retained in queue with preserved edited text and visible error.
- Strict account isolation across storage, routes, filters, approvals, and sends.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No webhooks in v1.
- No auto-send after approval.
- No mentions inbox, moderation tools, or replies to other people’s posts.
- No nested-reply support in v1.
- No reuse of `Lead` as the primary storage model for inbound comments.
- No ambiguous non-prefixed mutating routes.
- No vague “AI helper” copy; drafts must stay concise, specific, and under 280 chars.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: **tests-after** using pytest + FastAPI TestClient + mocked Threads client interactions.
- QA policy: Every task below includes agent-executed happy-path and failure-path scenarios.
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. Shared contracts first, then route/UI/test surfaces.

**Wave 1 — backend contract + ingestion core**
- 1. Comment inbox schema + state machine
- 2. Threads client inbound-comment fetch support
- 3. Polling ingestion/upsert service
- 4. LLM comment drafting service
- 5. Approval/send workflow service + gate logic

**Wave 2 — orchestration + web surface + tests**
- 6. Run-comments integration + status reporting
- 7. Account-scoped Mission Control routes + bulk JSON actions
- 8. Mission Control template, sidebar entry, and client-side interactions
- 9. Backend/account-isolation test coverage
- 10. End-to-end route/workflow regression coverage

### Dependency Matrix (full, all tasks)
- 1 blocks: 3, 4, 5, 7, 8, 9, 10
- 2 blocks: 3, 9, 10
- 3 blocks: 4, 6, 7, 8, 9, 10
- 4 blocks: 5, 7, 8, 9, 10
- 5 blocks: 7, 8, 9, 10
- 6 blocks: 8, 10
- 7 blocks: 8, 9, 10
- 8 blocks: 10
- 9 blocks: 10
- 10 blocks: Final Verification Wave only

### Agent Dispatch Summary
- Wave 1 → 5 tasks → unspecified-high, deep
- Wave 2 → 5 tasks → unspecified-high, visual-engineering
- Final Verification → 4 review tasks → oracle, unspecified-high, deep

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task has Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Define dedicated comment inbox schema and workflow state machine

  **What to do**: Add a dedicated inbound-comment persistence model in `src/threads_analytics/models.py` instead of overloading `Lead`. Include at minimum: `id`, `account_id`, `source_post_thread_id`, `source_post_text`, `source_post_created_at`, `comment_thread_id`, `comment_permalink`, `comment_author_username`, `comment_author_user_id`, `comment_text`, `comment_created_at`, `status`, `ai_draft_reply`, `final_reply`, `ai_draft_generated_at`, `approved_at`, `sent_at`, `send_error`, `published_reply_thread_id`, `first_seen_run_id`, `last_seen_at`, `claim_token`, `claimed_at`. Add a unique constraint on `(account_id, comment_thread_id)`. Define the explicit v1 states: `drafted`, `approved`, `sending`, `sent`, `send_failed`, `ignored`. New comments should land in `drafted` after draft generation succeeds; comments without a draft yet may remain transiently uncategorized in service code but must not require a visible `new` column.
  **Must NOT do**: Do not store inbound comments in `Lead`; do not add nested-reply fields or mention-specific fields in v1; do not create any auto-send state.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: schema + workflow contract affects all downstream tasks.
  - Skills: `[]` - schema work is repo-local and does not need an extra skill.
  - Omitted: `['playwright']` - no browser work in this task.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 3, 4, 5, 7, 8, 9, 10 | Blocked By: none

  **References**:
  - Pattern: `src/threads_analytics/models.py:478-533` - existing `Lead` status/draft/claim fields show the closest workflow pattern to reuse semantically, not structurally.
  - Pattern: `src/threads_analytics/models.py:615-653` - `LeadReply` shows send/result tracking and should influence success/failure fields.
  - Pattern: `src/threads_analytics/models.py:93-105` - `MyReply` shows how reply entities are currently stored with account scoping.
  - API/Type: `src/threads_analytics/models.py:81-88` - account-scoped `MyPost` fields define the source post identifiers to link to.

  **Acceptance Criteria**:
  - [ ] ORM metadata defines a dedicated comment inbox table with account-scoped uniqueness on external comment ID.
  - [ ] Workflow states are explicit and map to manual-send-only semantics.
  - [ ] The model includes persistent failure/error storage so failed sends do not lose edited text.

  **QA Scenarios**:
  ```
  Scenario: Schema supports inbox workflow
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "schema_and_state_machine" -q`
    Expected: Test passes and asserts the dedicated comment-inbox model exists with allowed statuses and unique account/comment constraint.
    Evidence: .sisyphus/evidence/task-1-comment-inbox-schema.txt

  Scenario: Approval does not imply auto-send in schema transitions
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "approval_boundary_state_machine" -q`
    Expected: Test passes and shows approved items remain unsent until an explicit send action transitions them to `sending`/`sent`.
    Evidence: .sisyphus/evidence/task-1-comment-inbox-schema-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): add comment inbox state model` | Files: `src/threads_analytics/models.py`, `tests/test_comment_inbox.py`

- [x] 2. Extend Threads client for inbound comments on owned posts

  **What to do**: Add a client type/dataclass for inbound post comments and a new Threads client method for top-level replies to a specific owned post using the official replies endpoint. Parse and return external comment ID, text, username, user ID if present, permalink, and timestamp. Keep this method separate from `list_my_replies()` because `list_my_replies()` fetches the user’s own outbound replies and is not the inbound inbox source. Add unit tests around response parsing and create-reply reuse.
  **Must NOT do**: Do not use webhooks; do not use `conversation` for nested threading in v1; do not repurpose keyword-search code for inbox capture.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: API wrapper correctness is foundational and easy to regress.
  - Skills: `[]` - direct repo client work only.
  - Omitted: `['playwright']` - no UI.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 3, 9, 10 | Blocked By: 1

  **References**:
  - Pattern: `src/threads_analytics/threads_client.py:214-240` - existing reply-listing method shows client structure and parsing style.
  - Pattern: `src/threads_analytics/threads_client.py:446-468` - `create_reply()` is the send path that Mission Control will ultimately call.
  - External: `https://developers.facebook.com/docs/threads/retrieve-and-manage-replies/retrieve-replies/` - official replies retrieval contract.
  - External: `https://developers.facebook.com/docs/threads/retrieve-and-manage-replies/create-replies/` - official reply creation/publish flow.

  **Acceptance Criteria**:
  - [ ] Client exposes a dedicated inbound-comment fetch method for an owned post thread ID.
  - [ ] Parser handles missing optional author/user fields without crashing.
  - [ ] Existing outbound `create_reply()` behavior remains unchanged.

  **QA Scenarios**:
  ```
  Scenario: Parse inbound replies for owned post
    Tool: Bash
    Steps: Run `pytest tests/test_threads_client_comments.py -k "list_post_replies_parses_api_payload" -q`
    Expected: Test passes and validates correct parsing for comment ID `reply_456`, username, text, permalink, and timestamp.
    Evidence: .sisyphus/evidence/task-2-threads-client-comments.txt

  Scenario: Missing author metadata is tolerated
    Tool: Bash
    Steps: Run `pytest tests/test_threads_client_comments.py -k "list_post_replies_missing_author_fields" -q`
    Expected: Test passes and returns a usable comment object with nullable author fields instead of raising.
    Evidence: .sisyphus/evidence/task-2-threads-client-comments-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): fetch inbound post replies from threads api` | Files: `src/threads_analytics/threads_client.py`, `tests/test_threads_client_comments.py`

- [x] 3. Build idempotent polling ingestion for recent owned posts

  **What to do**: Add a new ingestion/service module for Mission Control that scans owned posts from `my_posts` for the last **30 days** only, newest first, and fetches top-level replies for each post. Upsert inbox rows by `(account_id, comment_thread_id)`. Update mutable metadata (`comment_text`, `comment_permalink`, `last_seen_at`) on repeat polls, but do not generate duplicates. Preserve `sent`, `send_failed`, and `ignored` workflow state on re-poll. Return a summary payload suitable for run-status display.
  **Must NOT do**: Do not ingest all history; do not ingest nested replies; do not overwrite edited drafts on repeat polls.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: this is the idempotency-critical data ingestion step.
  - Skills: `[]` - repo-local service logic.
  - Omitted: `['playwright']` - backend only.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 4, 6, 7, 8, 9, 10 | Blocked By: 1, 2

  **References**:
  - Pattern: `src/threads_analytics/pipeline.py:144-154` - account-scoped comments-cycle orchestration shape.
  - Pattern: `src/threads_analytics/models.py:81-88` - owned posts table used as polling source.
  - Pattern: `src/threads_analytics/models.py:478-555` - account-scoped lead ingestion fields show how search/audit rows are stored per run.
  - Test: `tests/test_reply_workflow.py:36-62` - shows account-scoped fixture setup for reply-domain records.

  **Acceptance Criteria**:
  - [ ] First poll inserts inbox items for top-level comments on owned posts within the 30-day window.
  - [ ] Repeated polls with the same external comment IDs do not create duplicates.
  - [ ] Repeat polls update metadata without resetting approval/send state.

  **QA Scenarios**:
  ```
  Scenario: Poll creates inbox rows and deduplicates by external ID
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "poll_deduplicates_external_reply_id" -q`
    Expected: Test passes and two polls containing external ID `17890001` result in one inbox row for the same account.
    Evidence: .sisyphus/evidence/task-3-comment-polling.txt

  Scenario: Older posts outside backfill window are ignored
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "poll_ignores_posts_older_than_30_days" -q`
    Expected: Test passes and comments attached to a 31-day-old post do not enter the inbox.
    Evidence: .sisyphus/evidence/task-3-comment-polling-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): ingest inbound comments with polling` | Files: `src/threads_analytics/comment_inbox.py`, `tests/test_comment_inbox.py`

- [x] 4. Add comment-specific LLM drafting in the user's voice

  **What to do**: Implement a dedicated drafting service for inbound comments rather than reusing the lead prompt unchanged. Use `YouProfile` if available plus a small account-scoped sample of recent `MyPost` and `MyReply` text as voice context. Generate concise replies under 280 chars. Draft only for new inbox items lacking a draft, and never overwrite `final_reply` when the user has edited it. If profile/context is missing, fall back to a safe concise helper tone without crashing.
  **Must NOT do**: Do not reuse the lead prompt verbatim; do not produce marketing/sales replies; do not overwrite user-edited text on re-draft.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: LLM prompt design + fallback logic + workflow preservation require careful reasoning.
  - Skills: `[]` - no external doc lookup needed.
  - Omitted: `['playwright']` - non-UI task.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 5, 7, 8, 9, 10 | Blocked By: 1, 3

  **References**:
  - Pattern: `src/threads_analytics/leads.py:24-43` - existing inline system-prompt structure.
  - Pattern: `src/threads_analytics/leads.py:46-105` - batch drafting flow and account scoping.
  - Pattern: `src/threads_analytics/leads.py:140-187` - LLM call/fallback/truncation behavior.
  - API/Type: `src/threads_analytics/web/routes_common.py:281-282` - `_get_latest_you_profile()` is the voice-profile lookup seam.
  - API/Type: `src/threads_analytics/models.py:93-105` - `MyReply` provides real reply examples for voice context.

  **Acceptance Criteria**:
  - [ ] Draft generation uses account-scoped voice context when available.
  - [ ] Drafts stay under 280 characters.
  - [ ] Re-running drafting never clobbers a non-null `final_reply`.

  **QA Scenarios**:
  ```
  Scenario: Draft generation uses account-scoped voice context
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "draft_generation_uses_you_profile_and_recent_replies" -q`
    Expected: Test passes and the mocked LLM prompt includes account-specific voice context before generating a draft for comment `reply_456`.
    Evidence: .sisyphus/evidence/task-4-comment-drafts.txt

  Scenario: Re-draft does not overwrite edited reply text
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "redraft_preserves_final_reply" -q`
    Expected: Test passes and an inbox item with `final_reply="Thanks — appreciate it."` keeps that exact edited text after a re-draft attempt.
    Evidence: .sisyphus/evidence/task-4-comment-drafts-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): generate inbound reply drafts in account voice` | Files: `src/threads_analytics/comment_reply_drafts.py`, `tests/test_comment_inbox.py`

- [x] 5. Implement explicit approve/edit/send workflow with reply gate reuse

  **What to do**: Add service-layer actions for: edit draft, bulk approve selected items, bulk unapprove back to drafted, bulk ignore, and explicit send-selected. Editing an approved item must demote it back to `drafted`. Sending must call a dedicated comment-send gate modeled after `gate_send_reply`, then `ThreadsClient.create_reply(reply_to_id=comment_thread_id, text=reply_text)`, then persist `published_reply_thread_id`, `sent_at`, and a `PublishLedger` entry. On failure, set `send_failed`, persist `send_error`, and preserve the editable draft text.
  **Must NOT do**: Do not let approval trigger background sending; do not discard text on send failure; do not reuse `Lead.status` or `LeadReply` rows as the state store for comments.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: this task owns the safety boundary between approval and publish.
  - Skills: `[]` - no extra skill required.
  - Omitted: `['playwright']` - backend workflow task.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 7, 8, 9, 10 | Blocked By: 1, 4

  **References**:
  - Pattern: `src/threads_analytics/leads.py:189-260` - send flow, per-account credentials, and publish-ledger writing.
  - Pattern: `src/threads_analytics/publish_gate.py:129-194` - approval/quota/brand checks for replies.
  - Pattern: `src/threads_analytics/publish_gate.py:197-207` - invalidation-on-edit behavior pattern from content workflow.
  - Test: `tests/test_reply_workflow.py:65-109` - account-scoped credential usage for replies.
  - Test: `tests/test_reply_workflow.py:112-180` - failure handling must not cross account boundaries.

  **Acceptance Criteria**:
  - [ ] Bulk approval updates only selected inbox items and does not publish.
  - [ ] Send-selected publishes only approved items and records success/failure per item.
  - [ ] Failed sends preserve edited draft text and surface a stored error.

  **QA Scenarios**:
  ```
  Scenario: Bulk approval does not publish
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "bulk_approve_manual_send_boundary" -q`
    Expected: Test passes and items `comment_1` and `comment_2` move to `approved` while no `create_reply()` call occurs.
    Evidence: .sisyphus/evidence/task-5-comment-workflow.txt

  Scenario: Send failure preserves edited text and sets send_failed
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "send_failure_preserves_approved_draft" -q`
    Expected: Test passes and a failed publish leaves `final_reply="Thanks — appreciate it."`, `status="send_failed"`, and a non-empty `send_error`.
    Evidence: .sisyphus/evidence/task-5-comment-workflow-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): add manual approval and send workflow` | Files: `src/threads_analytics/comment_inbox.py`, `src/threads_analytics/publish_gate.py`, `tests/test_comment_inbox.py`

- [x] 6. Integrate Mission Control sync into existing run-comments orchestration

  **What to do**: Extend the existing comments-run pipeline so the current account-scoped `/run/comments` trigger also performs Mission Control inbox sync + draft generation before or alongside existing lead-search work. Add clear stage names to the returned summary for inbox sync and comment draft generation so the UI can display progress. Preserve existing route contracts in `routes_pipeline.py` and keep the account-prefixed trigger/status behavior intact.
  **Must NOT do**: Do not create a second competing global comments-run trigger for v1; do not break existing lead-search comment flow.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: orchestration changes touch existing background-run plumbing.
  - Skills: `[]` - repo-local pipeline task.
  - Omitted: `['playwright']` - no browser work.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 8, 10 | Blocked By: 3

  **References**:
  - Pattern: `src/threads_analytics/pipeline.py:144-154` - account-scoped `run_comments_cycle()` entry point.
  - Pattern: `src/threads_analytics/web/routes_pipeline.py:89-132` - current trigger + status endpoints for comments runs.
  - Pattern: `src/threads_analytics/web/templates/base.html` - existing sidebar/button/banner already expects comments-run polling.

  **Acceptance Criteria**:
  - [ ] Existing `/accounts/{account_slug}/run/comments` route still starts a background run.
  - [ ] Status payload includes Mission Control-specific sync/draft progress in a stable JSON shape.
  - [ ] Existing lead-search comment behavior remains functional.

  **QA Scenarios**:
  ```
  Scenario: Comments run status includes mission-control stages
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -k "comments_run_status_includes_mission_control_progress" -q`
    Expected: Test passes and `/accounts/default/run/comments/status` returns JSON with `running`, `last_comments_run_summary`, and Mission Control stage keys.
    Evidence: .sisyphus/evidence/task-6-comments-run-integration.txt

  Scenario: Legacy ambiguous comments-run mutation stays rejected
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -k "legacy_comments_run_mutation_rejected" -q`
    Expected: Test passes and POST `/run/comments` still returns the account-prefix error instead of mutating global state.
    Evidence: .sisyphus/evidence/task-6-comments-run-integration-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): wire mission control into comments run cycle` | Files: `src/threads_analytics/pipeline.py`, `src/threads_analytics/web/routes_pipeline.py`, `tests/test_web_routes.py`

- [x] 7. Add account-scoped Mission Control routes and bulk action endpoints

  **What to do**: Create a dedicated route module (recommended: `src/threads_analytics/web/routes_comments.py`) and register it in `build_router()`. Add a read route that redirects legacy `GET /comments?account=...` to `/accounts/{slug}/comments`, plus the prefixed page route. Add prefixed JSON endpoints for bulk approve, bulk unapprove, bulk ignore, send selected, edit reply text, and regenerate selected drafts. Reject non-prefixed mutating routes. All routes must filter by account and reject cross-account item IDs.
  **Must NOT do**: Do not put this page inside `routes_growth.py`; do not allow unprefixed POST mutations; do not accept cross-account IDs silently.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: route surface + account isolation are central correctness concerns.
  - Skills: `[]` - route work only.
  - Omitted: `['playwright']` - no rendering polish in this task.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 8, 9, 10 | Blocked By: 1, 3, 4, 5

  **References**:
  - Pattern: `src/threads_analytics/web/routes.py:17-25` - router registration pattern.
  - Pattern: `src/threads_analytics/web/routes_common.py:49-79` - account-path, redirect, and ambiguous-mutation helpers.
  - Pattern: `src/threads_analytics/web/routes_content.py:118-194` - account-prefixed page route using `with_account_context()`.
  - Pattern: `src/threads_analytics/web/routes_content.py:196-265` - JSON mutation route shape for account-scoped content actions.
  - Test: `tests/test_web_routes.py:259-331` - current prefixed render and mutation-rejection assertions.

  **Acceptance Criteria**:
  - [ ] `GET /comments?account=default` redirects to `/accounts/default/comments`.
  - [ ] `GET /accounts/default/comments` renders only that account’s inbox items.
  - [ ] Bulk action endpoints reject item IDs that belong to another account.

  **QA Scenarios**:
  ```
  Scenario: Mission Control route renders account-scoped inbox only
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -k "comment_mission_control_account_scope" -q`
    Expected: Test passes and account `default` sees only its own comment inbox items while account `alt` sees only its own.
    Evidence: .sisyphus/evidence/task-7-comments-routes.txt

  Scenario: Cross-account bulk action is rejected
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -k "comment_bulk_action_rejects_cross_account_ids" -q`
    Expected: Test passes and the API returns 404 or 400 for inbox item IDs owned by another account.
    Evidence: .sisyphus/evidence/task-7-comments-routes-error.txt
  ```

  **Commit**: YES | Message: `feat(comments): add mission control routes and bulk actions` | Files: `src/threads_analytics/web/routes.py`, `src/threads_analytics/web/routes_comments.py`, `tests/test_web_routes.py`

- [x] 8. Build the Mission Control page, selection UX, and sidebar entry

  **What to do**: Add a new template (recommended: `comments_mission_control.html`) extending `base.html`. Reuse the operational-board patterns from `content_pipeline.html`, but tune the page for comment-response work: hero summary cards at top, selectable rows/cards grouped by status, original post preview, inbound comment text, editable draft area or modal, bulk action bar, per-item send/error indicators, and a visible “Sync comments” control/status. Add a sidebar/nav entry to surface the page in the dashboard chrome. Use `{{ account_path_prefix }}` for every fetch/form URL.
  **Must NOT do**: Do not add gradients/glassmorphism; do not hide send errors; do not make send happen automatically when a card is approved.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` - Reason: this is the main UX surface and must feel like first-class mission control, not a raw CRUD table.
  - Skills: `['playwright']` - useful for agent-driven UI verification during execution.
  - Omitted: `[]` - no meaningful omission beyond normal backend-only tools.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 10 | Blocked By: 1, 3, 4, 5, 6, 7

  **References**:
  - Pattern: `src/threads_analytics/web/templates/content_pipeline.html:481-1400` - selection, modal, and operational board interactions.
  - Pattern: `src/threads_analytics/web/templates/base.html` - global sidebar, run-status banner, account prefix context.
  - Pattern: `src/threads_analytics/web/templates/ground_truth.html` - hero + summary-card composition for dashboard pages.
  - Pattern: `src/threads_analytics/web/routes_content.py:180-194` - template context delivery for dashboard pages.

  **Acceptance Criteria**:
  - [ ] Page exposes status groups for at least `Drafted`, `Approved`, `Sent`, `Failed`, and `Ignored`.
  - [ ] User can select multiple drafted items and approve them in one action.
  - [ ] Approved items remain editable, and editing demotes them back to drafted.

  **QA Scenarios**:
  ```
  Scenario: Mission Control page renders bulk workflow controls
    Tool: Playwright
    Steps: Open `/accounts/default/comments`; verify summary cards, status columns, checkboxes, bulk action bar, and `Sync comments` control are visible.
    Expected: Page loads without JS errors and exposes visible bulk approve + send-selected controls.
    Evidence: .sisyphus/evidence/task-8-comments-ui.png

  Scenario: Editing an approved item demotes it back to drafted
    Tool: Playwright
    Steps: Open `/accounts/default/comments`; approve inbox item `reply_456`; edit its draft text to `Thanks — appreciate it.` and save.
    Expected: Item moves from `Approved` back to `Drafted`, approval badge/count updates, and no send occurs.
    Evidence: .sisyphus/evidence/task-8-comments-ui-error.png
  ```

  **Commit**: YES | Message: `feat(comments): add mission control page and selection ux` | Files: `src/threads_analytics/web/templates/comments_mission_control.html`, `src/threads_analytics/web/templates/base.html`, `src/threads_analytics/web/static/style.css`

- [x] 9. Add backend and account-isolation tests for comment inbox behavior

  **What to do**: Create or extend backend-focused pytest coverage for the new comment inbox domain. Cover schema, polling dedupe, 30-day window, voice-context drafting, approval/send state transitions, quota guard behavior, ignore flow, and cross-account isolation. Prefer a dedicated test module (recommended: `tests/test_comment_inbox.py`) plus a Threads client-specific module.
  **Must NOT do**: Do not rely on manual DB inspection; do not leave idempotency or quota behavior untested.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: comprehensive backend regression coverage is required for safe rollout.
  - Skills: `[]` - pytest + repo-local patterns only.
  - Omitted: `['playwright']` - backend tests only.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 10 | Blocked By: 1, 2, 3, 4, 5, 7

  **References**:
  - Test: `tests/test_reply_workflow.py:65-180` - account-scoped reply sending and failure handling.
  - Test: `tests/test_web_routes.py:259-331` - account-prefixed render + ambiguous mutation rejection.
  - Pattern: `src/threads_analytics/publish_gate.py:129-194` - gate expectations that require coverage.
  - Pattern: `src/threads_analytics/leads.py:211-239` - daily limit enforcement shape.

  **Acceptance Criteria**:
  - [ ] Dedicated backend tests exist for dedupe, quota block, approval/send boundary, and send-failure preservation.
  - [ ] Cross-account access attempts are explicitly asserted and rejected.
  - [ ] Ignored items remain ignored after re-poll unless manually reset by a future explicit action.

  **QA Scenarios**:
  ```
  Scenario: Reply quota gate blocks send at cap
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "quota_guard_blocks_over_limit" -q`
    Expected: Test passes and send is blocked once the account reaches the configured reply cap.
    Evidence: .sisyphus/evidence/task-9-comment-tests.txt

  Scenario: Nested replies are ignored by design in v1
    Tool: Bash
    Steps: Run `pytest tests/test_comment_inbox.py -k "nested_reply_handling" -q`
    Expected: Test passes and nested replies are explicitly ignored or filtered, matching the v1 scope boundary.
    Evidence: .sisyphus/evidence/task-9-comment-tests-error.txt
  ```

  **Commit**: YES | Message: `test(comments): cover inbox workflow and isolation` | Files: `tests/test_comment_inbox.py`, `tests/test_threads_client_comments.py`

- [x] 10. Add route/UI regression coverage for Mission Control workflows

  **What to do**: Extend route-level and integration coverage so the new page behaves like the rest of the app. Cover prefixed render, legacy redirect, mutation rejection, sync-status JSON, bulk approve, edit demotion, send-selected success/failure, and account isolation. If browser automation is available during execution, add one Playwright smoke flow; otherwise keep FastAPI TestClient assertions exhaustive.
  **Must NOT do**: Do not leave the page only browser-tested or only unit-tested; it needs route-level regression coverage.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` - Reason: route/UI integration must match the final page behavior.
  - Skills: `['playwright']` - useful if execution agent can run UI smoke tests.
  - Omitted: `[]` - both UI and route testing are relevant here.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: Final Verification Wave | Blocked By: 6, 7, 8, 9

  **References**:
  - Test: `tests/test_web_routes.py:259-331` - page render, redirects, and mutation rejection patterns.
  - Test: `tests/test_web_routes.py:334-419` - status endpoint and notification/webhook JSON route patterns.
  - Pattern: `src/threads_analytics/web/routes_pipeline.py:116-132` - JSON status payload shape for long-running comments operations.
  - Pattern: `src/threads_analytics/web/templates/content_pipeline.html:927-1399` - client-side fetch/mutation and toast flow patterns.

  **Acceptance Criteria**:
  - [ ] Route tests prove prefixed render, redirect, and mutation rejection all work for Mission Control.
  - [ ] Integration tests prove selected approved comments send only when explicitly requested.
  - [ ] UI smoke coverage (if available) proves there are no blocking JS/runtime errors on page load and primary bulk actions.

  **QA Scenarios**:
  ```
  Scenario: Route regression for comments mission control
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -k "comment_mission_control or comments_run_status" -q`
    Expected: Tests pass for render, redirect, ambiguous mutation rejection, and status payload behavior.
    Evidence: .sisyphus/evidence/task-10-comments-route-regression.txt

  Scenario: Send-selected happy path works end to end
    Tool: Playwright
    Steps: Open `/accounts/default/comments`; select approved item `reply_456`; click `Send selected`; wait for completion banner.
    Expected: Item moves to `Sent`, success toast appears, and no JS console/runtime error is emitted.
    Evidence: .sisyphus/evidence/task-10-comments-route-regression.png
  ```

  **Commit**: YES | Message: `test(comments): add mission control route and workflow regressions` | Files: `tests/test_web_routes.py`, `tests/test_comment_inbox.py`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- One commit per numbered task above.
- Keep backend contract tasks separate from web/UI tasks for easier rollback.
- Never combine approval/send workflow changes with unrelated UI polish.
- Final verification wave runs on the fully assembled branch; no squash/amend assumptions in the plan.

## Success Criteria
- Mission Control exists as a first-class account-scoped page reachable from dashboard navigation.
- Every top-level inbound comment on owned posts within the 30-day polling window is deduplicated into the inbox.
- The app auto-generates a reply draft for new inbox items without overwriting user-edited text.
- Users can bulk approve drafts, but nothing is sent until they explicitly send selected approved items.
- Failed sends remain visible, editable, and recoverable.
- Automated coverage proves account isolation, approval/send boundaries, dedupe, quota blocking, and route correctness.
