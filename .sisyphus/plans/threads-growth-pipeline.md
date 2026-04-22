# Multi-Account Threads Growth Flywheel

## TL;DR
> **Summary**: Convert the current single-account Threads analytics app into a multi-account, experiment-driven growth flywheel that uses only official Threads API capabilities, keeps humans in the approval loop, and compounds learning from publish outcomes back into future content decisions.
> **Deliverables**:
> - Multi-account tenancy and account-scoped credentials/queries
> - Unified idea generation + authoritative publish attribution
> - Approval/policy/quota/timing orchestration for posts and sanctioned replies
> - Closed-loop learning, controlled exploitation, notifications, and portfolio views
> **Effort**: XL
> **Parallel**: YES - 2 waves
> **Critical Path**: 1 → 2 → 3 → 5 → 6 → 7 → 8 → 10

## Context
### Original Request
The user wants this app to become a full Threads growth pipeline that maximizes official API usage, is semi-automated, learns what content works, and uses those learnings to grow reach and followers for the user’s persona.

### Interview Summary
- Optimize a **composite score** rather than a single metric.
- Keep **human approval before publish**.
- Use **tests-after** rather than strict TDD.
- Support **multiple Threads accounts now**.
- Run an **experiment-driven flywheel** as the core operating model.
- Use **controlled exploitation** rather than maximum monoculture.
- Allow **edgy but honest** content, but not deceptive/spam tactics.

### Metis Review (gaps addressed)
- Tenancy must come first because the current repo is single-account by construction.
- Unify the duplicate idea generation paths before strengthening learning loops.
- Replace fuzzy idea-to-post attribution with authoritative publish linkage.
- Add one pre-publish gate covering approval, quotas, anti-slop, brand safety, and API capability checks.
- Treat scheduler hardening and account isolation as prerequisites for any higher-level automation.

## Work Objectives
### Core Objective
Deliver a decision-complete plan for evolving this codebase into a safe, official-API-only, multi-account Threads growth system centered on a closed content flywheel: ingest → analyze → generate → approve → publish → measure → learn → exploit/explore.

### Deliverables
- First-class `Account` tenancy model with strict account scoping across owned data.
- Account-scoped Threads client construction, token/quota handling, and reply/publish workflows.
- One canonical ideation pipeline shared by CLI, scheduler, and web routes.
- Authoritative publish ledger and exact idea-to-post attribution.
- Approval workflow with unified policy gate.
- Timing optimizer and quota-aware planner.
- Account-aware orchestration for scheduled posts, approved replies, webhook events, and notifications.
- Composite KPI + exploit/explore engine + cross-account portfolio views.

### Definition of Done (verifiable conditions with commands)
- `ruff check .`
- `pytest tests/test_multi_account_isolation.py -q`
- `pytest tests/test_scheduler.py -q`
- `pytest tests/test_content_pipeline.py -q`
- `pytest tests/test_reply_workflow.py -q`
- `pytest tests/test_web_routes.py -q`
- `pytest tests/test_full_pipeline_with_mock_threads.py -q`

### Must Have
- Official Threads API only; no unsupported automation.
- Multi-account isolation at schema, query, client, scheduler, and UI layers.
- Human approval before any post/reply publish action.
- Exact publish attribution using returned Threads identifiers, not text similarity as primary truth.
- Controlled exploitation with novelty/fatigue controls.
- Per-account quotas and failures must not block unrelated accounts.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No automated likes/follows/DM access/scraping.
- No reliance on native scheduling or post editing capabilities the API does not provide.
- No third idea engine or wrapper layer that preserves duplicate generation stacks.
- No accidental cross-account analytics aggregation.
- No publish path that bypasses anti-slop + brand + approval checks.
- No manual-only acceptance criteria.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: **tests-after** using existing `pytest` + `ruff`, with new two-account fixtures and targeted workflow tests.
- QA policy: Every task below includes agent-executed happy-path and failure-path scenarios.
- Evidence: `.sisyphus/evidence/task-{N}-{slug}.{ext}`

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: tenancy foundation, account-scoped client/config, ideation consolidation, publish attribution foundation, approval/policy gate

Wave 2: account-scoped analytics/routes, timing/quota planner, orchestration hardening, webhook/event ingestion + notifications, closed-loop learning + portfolio dashboard

### Dependency Matrix (full, all tasks)
- 1 blocks 2, 3, 6, 7, 8, 9, 10
- 2 blocks 5, 6, 7, 8, 9
- 3 depends on 1 and 2
- 4 is independent of 1/2 but blocks 10 and any future generation routes
- 5 depends on 2 and informs 8, 9, 10
- 6 depends on 1, 2, 5 and blocks 8
- 7 depends on 1, 2, 3, 5 and informs 8, 10
- 8 depends on 1, 2, 5, 6, 7
- 9 depends on 2, 5, 8
- 10 depends on 3, 4, 5, 7, 8, 9

### Agent Dispatch Summary (wave → task count → categories)
- Wave 1 → 5 tasks → deep, unspecified-high
- Wave 2 → 5 tasks → deep, unspecified-high

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Establish shared-DB multi-account tenancy foundation

  **What to do**: Add a first-class `Account` model and keep **one shared SQLite database** with strict `account_id` scoping on every account-owned record. Backfill existing single-account rows into a default account during migration. Create reusable two-account pytest fixtures and enforce account ownership in ORM relationships, repository/helpers, and route loaders. Decide now that cross-account learning is **opt-in later**; base tables remain per-account only.
  **Must NOT do**: Do not split into one DB per account. Do not leave legacy “global” reads in place. Do not add multi-process/distributed infra in this task.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: tenancy touches schema, migrations, fixtures, and every downstream workflow.
  - Skills: `[]` - No extra skill needed; task is repo-native architectural plumbing.
  - Omitted: [`review-work`] - Reserve broad review for the final verification wave.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 2, 3, 6, 7, 8, 9, 10 | Blocked By: none

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/config.py:40-49` - current global Threads credential shape that must move behind account ownership.
  - Pattern: `src/threads_analytics/models.py:29-119` - current base analytics models assume single-account ownership.
  - Pattern: `src/threads_analytics/models.py:634-674` - content-generation tables that must become account-scoped.
  - Test: `tests/test_web_routes.py` - current temp-DB + app fixture pattern to generalize.
  - Test: `tests/test_full_pipeline_with_mock_threads.py` - integration-test style that should gain two-account coverage.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_multi_account_isolation.py -q` passes.
  - [ ] `pytest tests/test_web_routes.py -q` passes with account-aware fixtures enabled.
  - [ ] Existing rows created before migration resolve to the default migrated account without null `account_id` values.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Two-account isolation holds at the data layer
    Tool: Bash
    Steps: Run `pytest tests/test_multi_account_isolation.py -q`; verify the fixture creates account-a and account-b, inserts posts/ideas for both, and queries return only matching-account rows.
    Expected: Test passes and evidence shows zero cross-account leakage.
    Evidence: .sisyphus/evidence/task-1-tenancy.txt

  Scenario: Cross-account fetch is rejected or empty
    Tool: Bash
    Steps: Run the failure-path test in `tests/test_multi_account_isolation.py` that loads account-a data through account-b scope.
    Expected: Access returns empty/404/guarded result and does not expose account-a records.
    Evidence: .sisyphus/evidence/task-1-tenancy-error.txt
  ```

  **Commit**: YES | Message: `feat(accounts): add multi-account tenancy foundation` | Files: `src/threads_analytics/models.py`, migration files, fixture/test files, account-loading helpers

- [x] 2. Refactor configuration and Threads client usage to the account boundary

  **What to do**: Replace direct use of global Threads credentials with account-scoped client creation. Store per-account access token, user id, refresh metadata, enabled capabilities, and soft limits on the account record/config layer. Route all publishing, reply sending, quota checks, and token refresh through an account-aware client factory. Update reply publishing to use the official container → publish flow rather than direct low-level posting.
  **Must NOT do**: Do not leave `get_settings().threads_access_token` / `threads_user_id` reads inside publisher, scheduler, ingest, or reply flows. Do not build a second client abstraction beside the existing wrapper.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: credentials, quota handling, and reply publishing are high-risk trust-boundary work.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`git-master`] - Git workflow is not the hard part here.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 5, 6, 7, 8, 9 | Blocked By: 1

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/config.py:40-49` - current global token/user config.
  - Pattern: `src/threads_analytics/threads_client.py` - existing official API wrapper; extend rather than replace.
  - Pattern: `src/threads_analytics/publisher.py:137-148` - publish path already captures returned `thread_id`; preserve and strengthen.
  - Pattern: `src/threads_analytics/leads.py` - reply sending currently needs official reply-container alignment.
  - External: `https://developers.facebook.com/docs/threads/` - official Threads API capability boundary; publishing/replies use container → publish flow and quotas are per user/account.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_reply_workflow.py -q` passes.
  - [ ] A targeted client-factory test proves account-a token refresh/quota checks do not touch account-b credentials.
  - [ ] Reply publishing tests use the same official flow style as post publishing.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Account-scoped publish and reply flows use the right credentials
    Tool: Bash
    Steps: Run `pytest tests/test_reply_workflow.py -q`; inspect captured fake client calls for account-a and account-b.
    Expected: Each call uses the matching account credentials and the reply flow uses container -> publish semantics.
    Evidence: .sisyphus/evidence/task-2-account-client.txt

  Scenario: Expired token affects only one account
    Tool: Bash
    Steps: Run the failure-path test in `tests/test_reply_workflow.py` with account-a token expired and account-b healthy.
    Expected: account-a publish is blocked with a token-expired error; account-b operations continue.
    Evidence: .sisyphus/evidence/task-2-account-client-error.txt
  ```

  **Commit**: YES | Message: `refactor(threads): scope clients and quotas per account` | Files: `src/threads_analytics/config.py`, `src/threads_analytics/threads_client.py`, `src/threads_analytics/publisher.py`, `src/threads_analytics/leads.py`, related tests

- [x] 3. Scope pipeline stages, analytics, experiments, and routes by account

  **What to do**: Make account scoping explicit across ingest, metrics, experiments, growth patterns, perception, “you” profile, and all account-owned route payloads. Standardize request routing as **`/accounts/{account_slug}/...` for account-specific pages/actions** and keep **`/portfolio`** reserved for cross-account summaries only. CLI commands must accept `--account <slug>` for account-specific execution. All aggregate queries must require a conscious portfolio path instead of defaulting to global reads.
  **Must NOT do**: Do not silently infer “current account” from global config. Do not keep old non-prefixed mutating routes alive in parallel except as explicit redirects.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: many modules share the same current single-account assumption.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`frontend-design`] - This is behavior/scoping work, not design polish.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 7, 10 | Blocked By: 1, 2

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/pipeline.py` - current 12-stage orchestration that must run per account.
  - Pattern: `src/threads_analytics/metrics.py:1-24` - current analytics entrypoints assume one global post set.
  - Pattern: `src/threads_analytics/experiments.py:175-189` - experiment evaluation paths must become account-aware.
  - Pattern: `src/threads_analytics/web/routes.py` and domain route modules - central place to enforce `/accounts/{account_slug}` routing.
  - Test: `tests/test_web_routes.py` - extend with account-prefixed route coverage.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_multi_account_isolation.py -q` passes with pipeline/analytics coverage.
  - [ ] `pytest tests/test_web_routes.py -q` passes with `/accounts/{account_slug}` and `/portfolio` route checks.
  - [ ] Running an account-specific pipeline/test does not mutate other accounts’ metrics, patterns, or experiments.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Account-prefixed routes show only that account's state
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -q`; include assertions for `/accounts/account-a/growth/performance` and `/accounts/account-b/growth/performance` using distinct seeded data.
    Expected: Each page renders only its own records; `/portfolio` is the only aggregate view.
    Evidence: .sisyphus/evidence/task-3-account-routes.txt

  Scenario: Legacy global route cannot mutate shared state ambiguously
    Tool: Bash
    Steps: Run the failure-path route test that posts to an old non-account-prefixed mutating endpoint.
    Expected: Request is redirected, rejected, or explicitly mapped; no ambiguous mutation occurs.
    Evidence: .sisyphus/evidence/task-3-account-routes-error.txt
  ```

  **Commit**: YES | Message: `feat(routes): scope pipeline and analytics by account` | Files: pipeline/metrics/experiments modules, route modules, CLI, tests

- [x] 4. Consolidate ideation into one canonical generation pipeline

  **What to do**: Choose **`idea_generator.py` as the canonical idea engine** because it already centers pattern-driven generation, anti-slop validation, voice awareness, and experiment-linked generation. Migrate any missing capabilities from `growth_generator.py` into that canonical path, update the pipeline and routes to call the same generator, remove duplicate CLI command registration, and preserve a single provenance shape for generated ideas.
  **Must NOT do**: Do not keep `growth_generator.py` and `idea_generator.py` both active behind different entrypoints. Do not add a third “unified” adapter module that leaves both old engines intact.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: the task is broad refactoring/unification but narrower than tenancy work.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`refactor`] - Use only if the executor needs AST help; the plan itself should not depend on it.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 10 | Blocked By: none

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/idea_generator.py:164-272` - recommended canonical generation entrypoint.
  - Pattern: `src/threads_analytics/growth_generator.py:205-410` - absorb useful logic from here, then retire duplicate callers.
  - Pattern: `src/threads_analytics/pipeline.py` - currently invokes growth-oriented generation in the daily flow.
  - Pattern: `src/threads_analytics/web/routes_growth.py` - currently route-level generation path.
  - Pattern: `src/threads_analytics/cli.py:547-577` and `src/threads_analytics/cli.py:623-665` - duplicate `generate_ideas` command registrations that must collapse to one.

  **Acceptance Criteria** (agent-executable only):
  - [ ] One generator entrypoint is used by pipeline, CLI, and growth routes.
  - [ ] Duplicate CLI command registration is removed.
  - [ ] `pytest tests/test_content_pipeline.py -q` passes with assertions on unified idea provenance.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Pipeline, CLI, and route all generate through the same engine
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; assert the same generator function/service is invoked from pipeline, CLI, and growth route tests.
    Expected: One canonical idea engine is exercised everywhere and provenance fields are consistent.
    Evidence: .sisyphus/evidence/task-4-idea-engine.txt

  Scenario: Duplicate command path is gone
    Tool: Bash
    Steps: Run the failure-path/assertion test in `tests/test_content_pipeline.py` that checks CLI registration count for `generate-ideas`.
    Expected: Exactly one command registration remains; no shadowed duplicate path exists.
    Evidence: .sisyphus/evidence/task-4-idea-engine-error.txt
  ```

  **Commit**: YES | Message: `refactor(content): unify idea generation pipeline` | Files: `src/threads_analytics/idea_generator.py`, `src/threads_analytics/growth_generator.py`, `src/threads_analytics/pipeline.py`, `src/threads_analytics/web/routes_growth.py`, `src/threads_analytics/cli.py`, related tests

- [x] 5. Add an authoritative publish ledger and exact attribution model

  **What to do**: Introduce an append-only publish/event ledger that records account, source record (`GeneratedIdea` or approved reply), workflow type, approval timestamp, publish attempt lifecycle, Threads `creation_id`, returned `thread_id`, error codes, and recovery source. Make this ledger the primary truth for idea/reply → published artifact linkage. Keep the current text-similarity matcher only as an explicit recovery path for manual/off-scheduler posts, never as the main attribution mechanism.
  **Must NOT do**: Do not continue using fuzzy text similarity as the primary linkage source. Do not overload `GeneratedIdea.status` with publish-attempt history.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: event/log design plus model and publish-path integration.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`review-work`] - Final review will validate attribution quality globally.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 6, 8, 9, 10 | Blocked By: 2

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/publisher.py:137-148` - existing publish path already captures returned `thread_id`; extend from here.
  - Pattern: `src/threads_analytics/ingest.py:179-229` - current fuzzy matcher that must become fallback-only.
  - Pattern: `src/threads_analytics/models.py:655-656` - `GeneratedIdea.actual_post_id` exists but is not sufficient as full publish history.
  - Pattern: `src/threads_analytics/web/routes_content.py` and `src/threads_analytics/web/routes_growth.py` - publish/schedule actions must write authoritative events.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_content_pipeline.py -q` passes with publish-ledger assertions.
  - [ ] Publishing an approved idea stores `creation_id`, `thread_id`, account, and final status in the ledger.
  - [ ] Recovery-mode matching is tested separately and never used when a direct publish record exists.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Direct publish creates exact attribution
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; verify the happy-path test publishes an approved idea and persists a ledger row with exact account + idea + thread identifiers.
    Expected: Ingest/analytics can join through the ledger without text similarity.
    Evidence: .sisyphus/evidence/task-5-publish-ledger.txt

  Scenario: Manual/off-platform post uses fallback recovery only
    Tool: Bash
    Steps: Run the failure-path/recovery test in `tests/test_content_pipeline.py` where a post exists without a publish-ledger entry.
    Expected: Recovery matcher is invoked explicitly, marks the linkage as recovered, and does not overwrite direct-ledger truth.
    Evidence: .sisyphus/evidence/task-5-publish-ledger-error.txt
  ```

  **Commit**: YES | Message: `feat(publish): add authoritative publish ledger` | Files: models/migrations, publisher/ingest integration, tests

- [x] 6. Implement per-item approval and a unified pre-publish policy gate

  **What to do**: Make approval **per post/reply item**, not per batch. Approval must be invalidated whenever content text, media, target account, or scheduled time changes. Build one gate service used by publish-now routes, scheduling routes, scheduler execution, and approved-reply sending. The gate must check: explicit approval, account active/token valid, official API capability, anti-slop rules, brand validation, quota budget, and duplicate-publish prevention.
  **Must NOT do**: Do not let route handlers apply only a subset of checks. Do not allow “scheduled” to imply “approved forever.” Do not let brand checks remain optional for publish paths.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: one workflow policy must replace fragmented route-level behavior.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`systematic-debugging`] - This is design consolidation, not incident work.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 8 | Blocked By: 1, 2, 5

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/content_rules.py:189-233` - anti-slop validation currently exists.
  - Pattern: `src/threads_analytics/brand_validator.py:70-173` - brand/voice validation rules to fold into the gate.
  - Pattern: `src/threads_analytics/web/routes_content.py:170-191` - existing content checks that must stop being route-fragmented.
  - Pattern: `src/threads_analytics/web/routes_growth.py:191-207` - growth-route edit/schedule checks that must call the same gate.
  - Pattern: `src/threads_analytics/scheduler.py:48-123` - current scheduled publish / approved reply execution path that must call the gate.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_content_pipeline.py -q` passes with approval invalidation coverage.
  - [ ] `pytest tests/test_reply_workflow.py -q` passes with brand/quota/approval gating coverage.
  - [ ] Unapproved or edited-after-approval content cannot publish from route, CLI, or scheduler paths.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Approved content passes the unified gate and publishes once
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; verify a fully approved item passes anti-slop, brand, quota, and account checks and publishes successfully.
    Expected: Publish succeeds exactly once and ledger status becomes published.
    Evidence: .sisyphus/evidence/task-6-policy-gate.txt

  Scenario: Editing after approval invalidates publish eligibility
    Tool: Bash
    Steps: Run the failure-path test in `tests/test_content_pipeline.py` where an approved item is edited before scheduling/publish.
    Expected: Publish is blocked with an "approval required" style error until re-approved.
    Evidence: .sisyphus/evidence/task-6-policy-gate-error.txt
  ```

  **Commit**: YES | Message: `feat(workflow): add unified approval and publish gate` | Files: workflow/policy modules, routes, scheduler, tests

- [x] 7. Build an account-level timing optimizer and quota-aware planner

  **What to do**: Add a planner that ranks approved items per account by (a) account-local slot score and (b) exploit/explore policy. Use a trailing 90-day window for slot scoring when enough data exists, otherwise fall back to the best trailing 30-day signal and then to neutral defaults. Define product defaults now: **70/30 exploit/explore split**, **max 3 reuses of the same winning pattern per 14 days per account**, **7-day cooldown on exact hook reuse**, **soft caps of 4 post publishes/day/account and 25 approved replies/day/account**, while always respecting official API hard quotas.
  **Must NOT do**: Do not optimize globally across all accounts in this task. Do not consume official hard quotas as if they are desired product targets. Do not ship a bandit/RL system yet.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: planner logic depends on account scoping, attribution, metrics, and workflow constraints.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`benchmark`] - This is decision logic, not performance benchmarking.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 8, 10 | Blocked By: 1, 2, 3, 5

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/growth_patterns.py` - current timing extraction that should become operationalized slot scoring.
  - Pattern: `src/threads_analytics/publisher.py` - current rate limiting behavior to replace with account-aware soft/hard quota planning.
  - Pattern: `src/threads_analytics/metrics.py` - source for recent performance signals used by planner scoring.
  - External: `https://developers.facebook.com/docs/threads/` - official quotas and capability boundary; planner must treat these as ceilings, not strategy goals.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_content_pipeline.py -q` passes with slot-ranking and exploit/explore assertions.
  - [ ] Planner tests prove account-a quota exhaustion does not lower account-b ranking outcomes.
  - [ ] Exact hook reuse and pattern overuse are blocked per configured guardrails.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Planner ranks approved items with exploit/explore controls
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; verify the happy-path planner test produces 70/30 exploit/explore scheduling recommendations with timing slots ranked from historical account data.
    Expected: Recommended order reflects slot score, novelty rules, and per-account soft caps.
    Evidence: .sisyphus/evidence/task-7-planner.txt

  Scenario: Pattern fatigue guardrail blocks over-exploitation
    Tool: Bash
    Steps: Run the failure-path planner test where the same winning hook/pattern exceeds 3 uses in 14 days.
    Expected: Additional items are demoted or blocked with a fatigue reason instead of being scheduled.
    Evidence: .sisyphus/evidence/task-7-planner-error.txt
  ```

  **Commit**: YES | Message: `feat(planner): add timing and quota-aware scheduling` | Files: planner/timing modules, publisher integration, tests

- [x] 8. Harden the scheduler into a single-owner, account-aware orchestrator

  **What to do**: Keep the product as **single worker / single DB** for now, but make execution durable and idempotent. Rework scheduler selection so due publish/reply jobs are claimed through DB state (claim token / claimed-at / final status) before execution, then run them account by account through the unified gate, planner, and publish ledger. Ensure restart safety, per-account quota isolation, and official reply publishing support. Fix any publish-now/scheduling path that currently assumes status-only workflows.
  **Must NOT do**: Do not introduce distributed queues or multi-process workers here. Do not rely on in-memory thread state as the only ownership signal.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: automation correctness, idempotency, and restart behavior are the highest operational risks.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`playwright`] - Core scheduler correctness should be proven with deterministic tests first.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 9, 10 | Blocked By: 1, 2, 5, 6, 7

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/scheduler.py:25-27,132-163` - current singleton thread/start-stop model that must become durable.
  - Pattern: `src/threads_analytics/scheduler.py:48-123` - current polling loop over due posts and approved replies.
  - Pattern: `src/threads_analytics/web/app.py` - scheduler lifecycle wiring.
  - Pattern: `src/threads_analytics/web/routes_content.py:248-262` - current publish/scheduling path that needs hardening.
  - Test: `tests/test_full_pipeline_with_mock_threads.py` - useful model for end-to-end orchestration checks.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_scheduler.py -q` passes.
  - [ ] Scheduler tests prove the same due item publishes at most once across restart/duplicate-poll scenarios.
  - [ ] Scheduler tests prove account-a quota failure or token failure does not block account-b execution.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Due items publish exactly once across a normal scheduler run
    Tool: Bash
    Steps: Run `pytest tests/test_scheduler.py -q`; verify a due approved post and approved reply are claimed, gated, published, and marked complete once.
    Expected: Single publish per item with matching ledger entries and no duplicate execution.
    Evidence: .sisyphus/evidence/task-8-scheduler.txt

  Scenario: Scheduler restart mid-batch remains idempotent
    Tool: Bash
    Steps: Run the restart failure-path test in `tests/test_scheduler.py` that simulates a process stop after claim but before completion, then resumes execution.
    Expected: Resume logic finishes or retries safely without double publishing the same item.
    Evidence: .sisyphus/evidence/task-8-scheduler-error.txt
  ```

  **Commit**: YES | Message: `feat(scheduler): harden multi-account orchestration` | Files: `src/threads_analytics/scheduler.py`, app wiring, workflow/claim models, tests

- [x] 9. Add webhook/event ingestion and in-app notifications on top of polling truth

  **What to do**: Keep polling/ingest as the canonical source of truth, then add webhook/event ingestion as an accelerator that writes into the same event history and refresh queues. Create an in-app notification/alert surface (not email/slack) for token expiry, missing permissions, publish failures, quota exhaustion, approval backlog, brand drift, and significant KPI drops. Notifications must be account-scoped, dismissible, and link back to the affected account workflow page.
  **Must NOT do**: Do not make webhooks mandatory for correctness. Do not create cross-account/global alerts that hide which account is affected.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: combines event ingestion, UI surfacing, and workflow integration.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`frontend-design`] - UI can remain utilitarian; correctness and scoping matter more than polish.

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: 10 | Blocked By: 2, 5, 8

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/web/app.py` - current lifecycle entrypoint for adding webhook/notification wiring.
  - Pattern: `src/threads_analytics/brand_reporter.py` - existing drift detection that should finally feed surfaced alerts.
  - Pattern: existing route modules and templates - place account-scoped alert components near workflow and growth pages.
  - External: `https://developers.facebook.com/docs/threads/` - webhook/event capability boundary; treat as optional acceleration only.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_web_routes.py -q` passes with notification rendering/assertion coverage.
  - [ ] Event-ingestion tests prove webhook updates write account-scoped events without replacing polling-based truth.
  - [ ] Drift/publish/quota/token alerts are generated against the right account and can be dismissed.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Webhook/event update accelerates but does not replace canonical state
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; verify an incoming event updates the ledger/notification state while canonical polling-based reconciliation still succeeds.
    Expected: Event data enriches freshness only; canonical workflow state remains consistent.
    Evidence: .sisyphus/evidence/task-9-events.txt

  Scenario: Account-scoped alert appears for a failing account only
    Tool: Bash
    Steps: Run `pytest tests/test_web_routes.py -q`; include a seeded token-expiry or quota-exhaustion alert for account-a and visit account-b and portfolio pages.
    Expected: The alert appears only where account-a context is active; account-b remains clean except for intentional portfolio summaries.
    Evidence: .sisyphus/evidence/task-9-events-error.txt
  ```

  **Commit**: YES | Message: `feat(alerts): add webhook acceleration and notifications` | Files: webhook/event handlers, alert models/routes/templates, tests

- [x] 10. Close the flywheel with composite KPI scoring, controlled exploitation, and portfolio views

  **What to do**: Implement the closed learning loop that turns attributed publish outcomes into future generation/planning decisions. Define two explicit scores:
  - `account_growth_score = 0.40 * follower_velocity_z + 0.25 * profile_clicks_z + 0.20 * views_z + 0.15 * conversation_depth_z`
  - `post_outcome_score = 0.30 * views_z + 0.25 * reply_rate_z + 0.20 * quote_rate_z + 0.15 * repost_rate_z + 0.10 * like_rate_z`
  Use account-local learnings by default. Add an **opt-in shared playbook** layer that can recommend cross-account patterns only for accounts explicitly tagged to the same niche. Surface portfolio pages that compare account scores, top patterns, fatigue/exploration usage, and action queues without flattening per-account truth.
  **Must NOT do**: Do not train ML/RL models in this phase. Do not let one account’s winning pattern automatically contaminate unrelated accounts. Do not hide the raw component metrics behind a single opaque score.

  **Recommended Agent Profile**:
  - Category: `deep` - Reason: this task ties together analytics, ideation, planner outputs, and portfolio UI.
  - Skills: `[]` - No extra skill needed.
  - Omitted: [`benchmark`] - Focus on correctness of growth logic, not runtime benchmarking.

  **Parallelization**: Can Parallel: YES | Wave 3 | Blocks: none | Blocked By: 3, 4, 5, 7, 8, 9

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `src/threads_analytics/metrics.py` - existing account-level ground-truth metrics to reuse.
  - Pattern: `src/threads_analytics/growth_patterns.py` - source for winning-pattern extraction feeding exploitation controls.
  - Pattern: `src/threads_analytics/idea_generator.py` - canonical generator that should now consume closed-loop learnings.
  - Pattern: `src/threads_analytics/web/routes_growth.py` and growth templates - natural home for score/pattern/queue views.
  - Pattern: `src/threads_analytics/brand_reporter.py` - brand drift should remain visible alongside growth scores.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `pytest tests/test_content_pipeline.py -q` passes with score-formula and learnings feedback assertions.
  - [ ] `pytest tests/test_web_routes.py -q` passes with `/portfolio` and account growth-page assertions.
  - [ ] Shared playbook recommendations appear only for accounts explicitly assigned to the same niche/group.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Published outcomes feed the next recommendation cycle
    Tool: Bash
    Steps: Run `pytest tests/test_content_pipeline.py -q`; verify attributed post results update `post_outcome_score` and shift the next idea/planner recommendations for that account.
    Expected: Closed-loop learning changes future recommendations while preserving per-account boundaries.
    Evidence: .sisyphus/evidence/task-10-learning-loop.txt

  Scenario: Shared playbook stays opt-in and niche-bounded
    Tool: Bash
    Steps: Run the failure-path learning test where two accounts are in different niches and one account has a dominant winning pattern.
    Expected: No cross-account recommendation leakage occurs until both accounts are explicitly grouped into the same niche/playbook.
    Evidence: .sisyphus/evidence/task-10-learning-loop-error.txt
  ```

  **Commit**: YES | Message: `feat(growth): close learning loop and add portfolio views` | Files: metrics/learning/planner integrations, portfolio routes/templates, tests

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Real Manual QA — unspecified-high (+ playwright if UI)
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Use one commit per numbered task when the task changes a coherent slice.
- Keep tenancy/schema commits isolated from orchestration/UI commits.
- Avoid mixing workflow policy changes with dashboard polish in the same commit.
- Suggested order mirrors the dependency matrix; do not land task 10 before tasks 1-9 are green.

## Success Criteria
- Multiple Threads accounts can coexist without data/quotas/results leaking across accounts.
- One canonical idea engine feeds CLI, routes, scheduler, and learning systems.
- Every published post/reply can be traced back to the exact approved record and later performance.
- The system recommends what to publish, when to publish, and how hard to exploit a winning pattern without violating honesty/brand constraints.
- Failures (expired token, missing permission, quota exhaustion, restart mid-batch) degrade gracefully and remain account-local.
