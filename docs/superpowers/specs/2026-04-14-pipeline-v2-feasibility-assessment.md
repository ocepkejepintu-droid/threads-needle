# Threads Pipeline v2 — Feasibility Assessment

**Date:** 2026-04-14
**Spec:** `threads_pipeline_v2_plan.md`
**Assessor:** Code review against live codebase
**Verdict:** ~75% of spec is directly implementable. 25% needs scope change or downgrade.

---

## Executive Summary

The v2 spec is well-structured and the P0/P1 features are almost entirely buildable within the existing architecture. The main friction points are:

1. **SQLite vs PostgreSQL assumptions** in the data model — easily fixable with SQLAlchemy equivalents.
2. **Threads API surface** — `get_post_insights()` already exists, but some requested metrics are **impossible** to fetch.
3. **X/Twitter API cost** — official API is $100+/month; fallback path is the only realistic option.
4. **Threads.com scraping** — heavily bot-protected; swipe-file feature needs a different approach.

**Recommendation:** Ship P0 + P1 as written (with SQLite adaptations). Downgrade P2.3 (Swipe File) to manual paste. Defer P3.1 (Calendar) until P2 metrics prove value.

---

## Platform & Architecture Constraints

### Database: SQLite (not PostgreSQL)

The spec's SQL schema uses PostgreSQL-specific features. The live codebase uses SQLite via SQLAlchemy.

| Spec Feature | PostgreSQL Syntax | SQLite Equivalent | Impact |
|--------------|-------------------|-------------------|--------|
| UUID PK | `UUID DEFAULT gen_random_uuid()` | `Integer, primary_key=True, autoincrement=True` | **Low** — all existing tables already use integer PKs. Stay consistent. |
| JSONB | `JSONB` | `JSON` (SQLAlchemy `JSON` column, stores as text) | **Low** — codebase already uses `Mapped[dict[str, object]] = mapped_column(JSON)` everywhere. |
| TEXT[] arrays | `TEXT[]` | `JSON` with list serialization | **Low** — existing pattern: `keywords: Mapped[list[str]] = mapped_column(JSON, default=list)`. |
| Generated column | `GENERATED ALWAYS AS ... STORED` | Compute in Python / SQLAlchemy `@property` | **Low** — total_score is simple addition; compute on read or maintain in Python. |
| TIMESTAMPTZ | `TIMESTAMPTZ` | `DateTime(timezone=True)` | **None** — codebase already uses this. |
| CHECK constraints inline | `CHECK (source IN (...))` | SQLAlchemy `CheckConstraint` or enum validation in Python | **Low** — Python enum + validation is more common in this codebase anyway. |

**Conclusion:** Schema is fully portable. No blockers.

### LLM Infrastructure

The codebase already has a multi-provider LLM client with caching:
- `get_llm_client()` returns Anthropic / Z.ai GLM / OpenRouter client
- Token counting is not yet implemented but `anthropic` SDK returns `usage` objects
- Rate limiter: 360 req/60s per IP (web), but LLM calls are server-side and unthrottled

**Conclusion:** All Claude API calls in the spec are feasible. Cost tracking requires adding a lightweight `llm_calls` log table.

### Threads API Surface

The existing `ThreadsClient` already has:
- `get_post_insights(thread_id)` → returns `views, likes, replies, reposts, quotes`
- `list_my_posts()` with pagination
- `create_text_post()` with media container polling

**Critical finding:** The spec asks for `replies_in_first_hour` and `self_reply_seeded`. These **cannot** be fetched from the Threads Graph API. There is no webhook, no streaming, and no historical granularity. The only way to capture these is to:
- Poll `list_post_replies()` every N minutes after publish and count timestamps
- Track self-replies by logging every reply the operator sends through our system

This is a **scope addition**, not a blocker.

---

## Feature-by-Feature Verdict

### ✅ P0.1 — Daily Trend Digest

**Status: FULLY IMPLEMENTABLE**

| Source | Feasibility | Notes |
|--------|-------------|-------|
| HackerNews front page | ✅ Easy | `https://hacker-news.firebaseio.com/v0/...` — no auth, well-documented. Filter by AI/LLM keywords in title. |
| Anthropic changelog RSS | ✅ Easy | `https://www.anthropic.com/rss.xml` or similar. Use `feedparser` or stdlib `xml.etree`. |
| OpenAI blog RSS | ✅ Easy | `https://openai.com/blog/rss.xml` exists. |
| Google DeepMind / Gemini releases | ⚠️ Medium | No standard RSS. May need to scrape `deepmind.google` or use their blog feed. Fallback: skip if unavailable. |
| X curated list | ⚠️ Medium | Official API is $100+/month Basic tier. **Recommended:** build "paste URL" fallback first. If budget allows later, add API. |

**Dedupe logic:** 80% title similarity is doable with `difflib.SequenceMatcher` or `python-Levenshtein`. No external heavy ML needed.

**Claude filtering batch:** Send 10–15 items at once. One API call per day. Cost: ~$0.02/day.

**Cron:** APScheduler already runs in FastAPI lifespan. Add a daily job at 01:00 UTC (08:00 WIB).

**Required new code:**
- `src/threads_analytics/intake/` package with fetchers
- `IntakeItem` model
- Daily cron job registration

---

### ✅ P0.2 — Intake Kanban Column

**Status: FULLY IMPLEMENTABLE**

The existing Kanban board (`content_pipeline.html`) already renders columns dynamically from a `status` field. Adding `intake` as a new status is straightforward.

**UI changes:**
- Add column before Draft in the board template
- Card design: reuse existing card CSS, add badge chips for score + mechanics
- Sorting: server-side via query param, or client-side JS (simpler for <50 items)

**No blockers.**

---

### ✅ P1.1 — Angle-It

**Status: FULLY IMPLEMENTABLE**

All ingredients exist:
- LLM client with retry logic ✓
- Modal UI pattern in templates ✓
- Draft creation endpoint ✓
- JSON parser with forgiveness (recently built) ✓

**Prompt engineering effort:** Medium. The voice prompt is detailed in the spec. Need to test with 5–10 real trends and iterate. Budget 1–2 hours of prompt tuning.

**One detail to clarify:** The spec says "feed top-performing mechanic data from last 30 days as context." This requires P2.1 (outcome tagging) to already exist. For Week 2 launch, either:
- Use hardcoded default priorities (binary_verdict, token_receipt), or
- Build a lightweight mechanic popularity tracker from existing `MyPostInsight` data

**Recommended:** Use existing insight data as a temporary stand-in.

---

### ✅ P1.2 — Scoring Rubric Gate

**Status: FULLY IMPLEMENTABLE**

- 6 HTML range inputs (`<input type="range">`) with live JS sum → trivial
- Auto-score endpoint → one Claude call per draft
- Tier assignment → pure Python logic
- Validation rules → backend + frontend

**Data model:** Add 6 `Integer` columns + `mechanic` + `tier` to the `GeneratedIdea` model (or whatever the Draft table is called in the codebase — actually, looking at models.py, drafts are `GeneratedIdea` with `status="draft"`).

Wait — looking at the codebase more carefully, there is no `Drafts` table in models.py. The content pipeline uses `GeneratedIdea` which has statuses: `draft`, `approved`, `scheduled`, `published`, `rejected`. So the rubric columns should go on `GeneratedIdea`.

**Blockers:** None.

---

### ✅ P1.3 — Tier-Slot Validation

**Status: FULLY IMPLEMENTABLE**

The slot schedule is a config table (or even a Python constant to start). The spec's schedule is simple:

```python
SLOT_SCHEDULE = [
    {"day": "mon", "time": "11:00", "tier": "hero"},
    {"day": "tue", "time": "12:00", "tier": "engine"},
    # ...
]
```

- Query scheduled posts for the week
- Filter slots by tier
- Mark taken slots as disabled
- Validation on the server side (reject mismatched tier)

**No blockers.**

---

### ✅ P2.1 — Post-Mortem Auto-Tagger

**Status: IMPLEMENTABLE with one limitation**

What works:
- `get_post_insights()` already fetches views/likes/replies/reposts/quotes ✓
- Outcome classification rules are pure Python ✓
- `post_outcomes` table (or reuse `MyPostInsight` with extra fields) ✓
- Hourly cron via APScheduler ✓

What **does NOT work** without extra infrastructure:
| Metric | API Available? | Workaround |
|--------|---------------|------------|
| `views` | ✅ Yes | `get_post_insights()` |
| `likes` | ✅ Yes | `get_post_insights()` |
| `replies` | ✅ Yes | `get_post_insights()` |
| `reposts` | ✅ Yes | `get_post_insights()` |
| `reply_to_like_ratio` | ✅ Computed | `replies / max(likes, 1)` |
| `reach_multiple` | ✅ Computed | `views / median_30d_views` |
| `replies_in_first_hour` | ❌ **No** | Must poll `list_post_replies()` every 5 min for first hour and count. Complex. |
| `self_reply_seeded` | ❌ **No** | Must track operator replies sent through our system. Doable if we log them. |

**Recommendation:** Implement P2.1 without `replies_in_first_hour` for v2.0. Add it later if the operator really needs velocity analysis. The 4 outcome tags (breakout/healthy/stall/zero_reply) work fine without it.

---

### ✅ P2.2 — Performance Dashboard

**Status: FULLY IMPLEMENTABLE**

All data is local. No external APIs needed after P2.1 populates outcomes.

**Topic clustering via Claude:** This is the expensive part. The spec says "run weekly, cache results." A weekly batch of 20–30 posts costs ~$0.05. Totally reasonable.

**Dashboard load time:** <2s is easy with SQLite on this data volume.

**No blockers.**

---

### ⚠️ P2.3 — Swipe File + Mechanic Detector

**Status: NEEDS REDESIGN**

**The scraping problem:**
Threads.com is heavily protected against bots:
- Cloudflare / bot detection on all routes
- Requires JavaScript rendering
- No public API for fetching arbitrary post content by URL
- The Graph API only returns the **authenticated user's own** posts and posts they can search for via keyword — not arbitrary URLs

**What this means:**
- Pasting a `threads.com/@user/post/123` URL and expecting to extract the post text, likes, and replies is **not reliably possible** without a headless browser and even then may fail.

**Recommended redesign for P2.3:**

| Spec Approach | Reality | Suggested Replacement |
|---------------|---------|----------------------|
| Paste Threads URL, auto-scrape | ❌ Unreliable / blocked | **Manual paste** of post text + optional screenshot upload |
| Auto-fetch views/likes/replies | ❌ No API | **Manual entry** of engagement numbers (operator copies from app) |
| Claude analyzes full post | ✅ Works | Send manually pasted text to Claude for mechanic detection |
| Browsable library | ✅ Works | Same as spec — store and filter |
| "Inspire a draft" | ✅ Works | Pre-seed mechanic tag on new Draft |

**This turns P2.3 from an automation feature into a lightweight manual curation tool.** Still valuable — the operator sees viral posts in the wild and captures them. But the capture step is manual, not auto-scrape.

**Alternative (advanced):** If the operator is viewing Threads on their phone, they can use the native Share → Copy Link, then paste into our app. The URL itself gives us the `thread_id` at the end. We *could* try `ThreadsClient.get_post_insights(thread_id)` if the post is public and the API cooperates. But Meta's API is scoped to the authenticated user's content + keyword search results. Arbitrary public posts may return 403.

**Verdict:** Build the manual-capture version. It's 80% of the value with 20% of the complexity.

---

### ⏸️ P3.1 — Calendar Availability Gate

**Status: IMPLEMENTABLE BUT RECOMMENDED DEFER**

Google Calendar OAuth read-only is straightforward:
1. OAuth consent screen + `https://www.googleapis.com/auth/calendar.readonly` scope
2. Store refresh token in `.env` (existing token pattern)
3. Query events for the 60-min window after proposed publish time
4. Warn on conflict, suggest next free slot

**Why defer:**
- It's P3 (lowest priority) for a reason
- Adds OAuth complexity that can break silently (token expiry, scope changes)
- The operator already knows their own schedule
- Success metrics for v2 don't depend on this

**Build it only if:** P0–P2 are live and the operator has actually scheduled a post during a meeting, then complained about missing reply velocity.

---

## Summary Table

| Feature | Feasibility | Effort | Blockers | Recommendation |
|---------|-------------|--------|----------|----------------|
| P0.1 Daily Trend Digest | ✅ High | 2–3 days | None | **Ship as written** |
| P0.2 Intake Column | ✅ High | 1 day | None | **Ship as written** |
| P1.1 Angle-It | ✅ High | 2–3 days | None | **Ship as written** |
| P1.2 Rubric Gate | ✅ High | 2 days | None | **Ship as written** |
| P1.3 Tier-Slot Validation | ✅ High | 1–2 days | None | **Ship as written** |
| P2.1 Post-Mortem Tagger | ✅ High | 2 days | Missing `replies_in_first_hour` | **Ship without first-hour metric** |
| P2.2 Performance Dashboard | ✅ High | 2–3 days | None | **Ship as written** |
| P2.3 Swipe File | ⚠️ Medium | 2 days | Threads scraping blocked | **Build manual-capture version** |
| P3.1 Calendar Gate | ✅ High | 2–3 days | None | **Defer to v2.1+** |

---

## Data Model Additions (SQLite-Adapted)

Instead of the spec's PostgreSQL schema, add these SQLAlchemy models:

```python
# intake_items
class IntakeItem(Base):
    __tablename__ = "intake_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # hn, anthropic, openai, gemini, manual
    source_url: Mapped[str] = mapped_column(String(2048))
    source_title: Mapped[str] = mapped_column(String(512))
    raw_data: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_standing_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_mechanics: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="new")  # new|converted|archived|expired
    converted_to_idea_id: Mapped[int | None] = mapped_column(ForeignKey("generated_ideas.id"), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utcnow() + timedelta(days=7))

# post_outcomes
class PostOutcome(Base):
    __tablename__ = "post_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_thread_id: Mapped[str] = mapped_column(ForeignKey("my_posts.thread_id"))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    reply_to_like_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    reach_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_tag: Mapped[str | None] = mapped_column(String(32), nullable=True)  # breakout|healthy|stall|zero_reply
    # replies_in_first_hour deferred to v2.1

# swipe_items
class SwipeItem(Base):
    __tablename__ = "swipe_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(2048), unique=True)
    author_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    post_body: Mapped[str] = mapped_column(Text)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)  # manually entered
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # manually entered
    replies: Mapped[int | None] = mapped_column(Integer, nullable=True)  # manually entered
    identified_mechanic: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hook_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

# pipeline_config
class PipelineConfig(Base):
    __tablename__ = "pipeline_config"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

**Extend `GeneratedIdea`:**
```python
intake_item_id: Mapped[int | None] = mapped_column(ForeignKey("intake_items.id"), nullable=True)
mechanic: Mapped[str | None] = mapped_column(String(32), nullable=True)
rubric_hook_test: Mapped[int | None] = mapped_column(Integer, nullable=True)
rubric_mechanic_fit: Mapped[int | None] = mapped_column(Integer, nullable=True)
rubric_operator_standing: Mapped[int | None] = mapped_column(Integer, nullable=True)
rubric_trend_freshness: Mapped[int | None] = mapped_column(Integer, nullable=True)
rubric_reply_invitation: Mapped[int | None] = mapped_column(Integer, nullable=True)
rubric_voice_signature: Mapped[int | None] = mapped_column(Integer, nullable=True)
tier: Mapped[str | None] = mapped_column(String(16), nullable=True)

@property
def total_score(self) -> int | None:
    scores = [self.rubric_hook_test, self.rubric_mechanic_fit,
              self.rubric_operator_standing, self.rubric_trend_freshness,
              self.rubric_reply_invitation, self.rubric_voice_signature]
    if all(s is not None for s in scores):
        return sum(scores)
    return None
```

---

## Open Questions from Spec — Resolved

| # | Question | Resolution |
|---|----------|------------|
| 1 | Digest still runs if operator is sick? | Yes. Items accumulate and auto-expire after 7 days. No issue. |
| 2 | Multi-language support? | Out of scope. Bahasa campur stays hardcoded in prompts. |
| 3 | Team access? | Single-user only. No auth system beyond existing token. |
| 4 | Version-control for prompts? | Store in git as Python constants / `.txt` files. Load at runtime. |
| 5 | Retroactive outcome tagging? | Yes, backfill after P2.1 ships. One-time migration using existing `MyPostInsight` rows. |

---

## Cost Projection (Updated)

| Feature | Claude Calls / Day | Est. Cost / Month |
|---------|-------------------|-------------------|
| P0.1 Intake filter | 1 (batch of 10–15 items) | ~$0.60 |
| P1.1 Angle-It | 2–3 (only when converting) | ~$2–5 |
| P1.2 Auto-score | 2–3 (only when promoting) | ~$2–5 |
| P2.1 Outcome tagger | 0 (pure Python) | $0 |
| P2.2 Topic clustering | 1 per week (batch) | ~$0.20 |
| P2.3 Swipe analysis | 2–3 per week (manual use) | ~$2 |
| **Total** | | **~$7–13 / month** |

The spec's "$15–30/month" estimate is conservative. At actual usage volume, this is closer to **$10/month**.

---

## Recommended Build Order

Based on dependency chains and value delivery:

### Sprint 1 (Week 1): P0 Foundation
1. Schema migrations (`IntakeItem`, `PipelineConfig`, `GeneratedIdea` extensions)
2. Source fetchers (HN, Anthropic RSS, OpenAI RSS)
3. Dedupe + Claude filtering
4. Daily cron job
5. Intake Kanban column UI
6. Convert-to-Draft stub (basic, no Angle-It yet)

**Gate:** 5 consecutive days of auto-populated intake.

### Sprint 2 (Week 2): P1.1 Angle-It
1. Angle-It modal UI
2. Claude prompt tuning (voice fidelity)
3. Variant selection + edit flow
4. Intake → Draft linkage

### Sprint 3 (Week 3): P1.2 + P1.3 Quality Gates
1. Rubric modal with 6 sliders
2. Auto-score endpoint
3. Tier assignment logic
4. Tier-slot validation in scheduler

**Gate:** 8+ posts scheduled through rubric gate.

### Sprint 4 (Week 4): P2.1 Learning Loop
1. Hourly metric fetcher (`get_post_insights` for published posts)
2. Outcome classification
3. Outcome badges on Published cards

### Sprint 5 (Week 5): P2.2 Dashboard
1. `/performance` route
2. 5 dashboard sections
3. Weekly topic clustering (cached)

### Sprint 6 (Week 6): P2.3 Swipe File (Manual)
1. `/swipe` route
2. Manual paste form (URL + post text + engagement)
3. Claude mechanic detection
4. "Inspire a draft" action

### Deferred: P3.1 Calendar
- Revisit after 4+ weeks of v2 operation.

---

## Risk Register (Updated)

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Claude API cost creep | Low | Low | Actual projection is $10/mo. Cache aggressively. |
| X API unaffordable | Certain | None | Fallback (manual paste) is already the plan. |
| Threads API changes | Medium | Medium | Meta has been stable on v1.0. Monitor changelogs. |
| Over-automation / voice drift | Low | High | All drafts editable. Tier 1/2 copy stays hand-written. |
| Rubric gaming | Medium | Medium | Auto-score baseline always shown next to manual score. |
| Threads scraping blocked | Certain (for auto) | Low | P2.3 redesigned to manual capture. No auto-scrape. |
| Google Calendar OAuth fragility | Medium | Low | Deferred. Not on critical path. |

---

*End of assessment. Proceed to implementation planning for Sprint 1.*
