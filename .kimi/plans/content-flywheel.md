# Content Flywheel Implementation Plan

## Overview

Build a content calendar and idea generation system that removes AI slop and produces human-sounding posts. The flywheel combines:

1. **Idea Generator** — AI-assisted but pattern-driven, using your actual winning hooks
2. **Content Calendar** — Weekly drag-and-drop view with queue status
3. **One-Click Schedule** — Queue posts for optimal times (Fri mornings, 8-12pm)
4. **Auto-Publisher** — Background job posts to Threads at scheduled time

Key constraint: Content must feel human — specific details, casual voice, real struggles, not generic advice.

---

## Architecture Decisions

### 1. Content Ideas Store
**Decision:** Store generated ideas in database, not ephemeral
**Rationale:** User can review, edit, reject ideas before scheduling; builds library of concepts

### 2. Scheduling Model
**Decision:** Store scheduled posts with `scheduled_at`, `posted_at`, `status` (draft/scheduled/posted/failed)
**Rationale:** Simple state machine; retry on failure; history for analytics

### 3. Voice Learning
**Decision:** Extract voice from your top 3 posts, not generic templates
**Rationale:** Your winning posts have specific patterns (Relatable Struggle Opener, Indonesian-English mix, hiring frustrations)

### 4. Human-Sloppiness Filter
**Decision:** Post-generation validation rules that reject AI-sounding content
**Rationale:** Explicit guardrails catch AI slop before it reaches you

### 5. Publishing Mechanism
**Decision:** Background thread polling every minute for due posts
**Rationale:** No complex cron needed; FastAPI startup/shutdown events handle lifecycle

---

## Task List

### Phase 1: Database Foundation

#### Task 1: Create ScheduledPost Model
**Description:** Add database model for content calendar with scheduling support

**Acceptance criteria:**
- [ ] Model has: id, text, status (draft/scheduled/posted/failed), scheduled_at, posted_at, thread_id (after posting), error_message
- [ ] Migration created and tested
- [ ] Model integrates with existing database session

**Files touched:**
- `src/threads_analytics/models.py`
- Alembic migration

**Estimated scope:** Small (1-2 files)

**Dependencies:** None

---

#### Task 2: Create ContentIdea Model
**Description:** Store AI-generated ideas before they become scheduled posts

**Acceptance criteria:**
- [ ] Model has: id, hook, body, source_pattern, relevance_score, used (bool), created_at
- [ ] Can convert Idea to ScheduledPost
- [ ] Migration created

**Files touched:**
- `src/threads_analytics/models.py`
- Alembic migration

**Estimated scope:** Small (1-2 files)

**Dependencies:** Task 1

---

### Checkpoint: Database Foundation
- [ ] Both migrations apply cleanly
- [ ] Models queryable via session

---

### Phase 2: Human-Sounding Idea Generator

#### Task 3: Extract Voice Patterns from Your Posts
**Description:** Analyze your top 3 performing posts to extract concrete patterns

**Acceptance criteria:**
- [ ] Extract sentence starters (e.g., "Susah juga ya...", "New remote roles...")
- [ ] Extract structure (complaint → context → solution/CTA)
- [ ] Extract tone markers (emoji usage, Indonesian-English ratio)
- [ ] Store as VoiceProfile in database

**Files touched:**
- `src/threads_analytics/voice_extractor.py` (new)

**Estimated scope:** Medium (3-4 files)

**Dependencies:** Task 2

---

#### Task 4: Build Anti-AI-Slop Rules
**Description:** Validation rules that reject generic AI content

**Acceptance criteria:**
- [ ] Rule: Rejects posts with "In today's fast-paced world..."
- [ ] Rule: Rejects posts starting with "Hey everyone!" or "I wanted to share..."
- [ ] Rule: Requires at least one specific number or detail
- [ ] Rule: Rejects overly formal vocabulary ("leverage", "synergy", "optimize")
- [ ] All rules testable with unit tests

**Files touched:**
- `src/threads_analytics/content_rules.py` (new)
- `tests/test_content_rules.py` (new)

**Estimated scope:** Small (2 files)

**Dependencies:** None

---

#### Task 5: Idea Generator Service
**Description:** Generate ideas using your patterns + anti-slop rules

**Acceptance criteria:**
- [ ] Takes prompt (e.g., "accountant hiring struggles") + voice profile
- [ ] Generates 3 variations using different hooks from pattern library
- [ ] Validates each through anti-slop rules
- [ ] Returns scored ideas (human-ness score + relevance score)
- [ ] Falls back to pattern remix if AI generation fails validation

**Files touched:**
- `src/threads_analytics/idea_generator.py` (new)
- `src/threads_analytics/prompts/idea_gen.txt` (new prompt template)

**Estimated scope:** Medium (3-4 files)

**Dependencies:** Task 3, Task 4

---

### Checkpoint: Idea Generator
- [ ] Can generate ideas from CLI: `threads-analytics generate-ideas --topic "hiring accountants"`
- [ ] Ideas pass anti-slop validation
- [ ] Ideas match your voice patterns

---

### Phase 3: Content Calendar UI

#### Task 6: Calendar View Route and Template
**Description:** Weekly calendar view showing scheduled posts

**Acceptance criteria:**
- [ ] Route `/calendar` displays weekly view
- [ ] Shows days Mon-Sun with scheduled posts
- [ ] Visual distinction: draft (gray), scheduled (blue), posted (green), failed (red)
- [ ] Shows post preview (first 100 chars) in calendar cell

**Files touched:**
- `src/threads_analytics/web/routes.py` (add route)
- `src/threads_analytics/web/templates/calendar.html` (new)

**Estimated scope:** Medium (3-4 files)

**Dependencies:** Task 1

---

#### Task 7: Drag-and-Drop Rescheduling
**Description:** Client-side drag to move posts between days

**Acceptance criteria:**
- [ ] Can drag post from one day to another
- [ ] Updates `scheduled_at` via AJAX PATCH request
- [ ] Visual feedback during drag
- [ ] Snaps to time slots (morning: 8am, 10am, 12pm)

**Files touched:**
- `src/threads_analytics/web/static/calendar.js` (new)
- `src/threads_analytics/web/routes.py` (PATCH endpoint)

**Estimated scope:** Medium (3 files)

**Dependencies:** Task 6

---

#### Task 8: Idea Generator UI
**Description:** Interface to generate and approve ideas

**Acceptance criteria:**
- [ ] Route `/ideas` shows idea generator form
- [ ] Form: topic input + generate button
- [ ] Displays generated ideas as cards
- [ ] Each card has: preview, "Schedule" button, "Reject" button, "Edit" link
- [ ] Scheduling opens date/time picker

**Files touched:**
- `src/threads_analytics/web/routes.py` (add routes)
- `src/threads_analytics/web/templates/ideas.html` (new)
- `src/threads_analytics/web/static/ideas.js` (new)

**Estimated scope:** Medium (4-5 files)

**Dependencies:** Task 5, Task 7

---

### Checkpoint: Calendar UI
- [ ] Calendar view loads with current week's posts
- [ ] Can reschedule via drag-and-drop
- [ ] Can generate ideas and schedule them

---

### Phase 4: Auto-Publishing

#### Task 9: Threads Publishing Service
**Description:** Actually post to Threads API

**Acceptance criteria:**
- [ ] Function `publish_to_threads(text: str) -> thread_id`
- [ ] Handles 10 posts/24h rate limit
- [ ] Returns thread_id on success, raises on failure
- [ ] Logs publish attempt (success/failure)

**Files touched:**
- `src/threads_analytics/publisher.py` (new)

**Estimated scope:** Small (1-2 files)

**Dependencies:** None (uses existing Threads client)

---

#### Task 10: Background Publisher Thread
**Description:** Poll for due posts and publish them

**Acceptance criteria:**
- [ ] Runs every 60 seconds in background
- [ ] Finds posts with `scheduled_at <= now()` and status=scheduled
- [ ] Publishes via Task 9 service
- [ ] Updates status to posted (with thread_id) or failed (with error)
- [ ] Logs all actions
- [ ] Graceful shutdown on app stop

**Files touched:**
- `src/threads_analytics/scheduler.py` (new)
- `src/threads_analytics/web/app.py` (startup/shutdown hooks)

**Estimated scope:** Medium (3 files)

**Dependencies:** Task 9

---

### Checkpoint: Auto-Publishing
- [ ] Schedule a post for 2 minutes from now
- [ ] Wait, verify it auto-posts
- [ ] Check Threads to confirm it went live

---

### Phase 5: Integration & Polish

#### Task 11: Sidebar Navigation Update
**Description:** Add Calendar and Ideas to sidebar

**Acceptance criteria:**
- [ ] Calendar icon links to `/calendar`
- [ ] Ideas icon links to `/ideas`
- [ ] Remove old broken Lead links

**Files touched:**
- `src/threads_analytics/web/templates/base.html`

**Estimated scope:** XS (1 file)

**Dependencies:** None

---

#### Task 12: One-Click Schedule from Ideas
**Description:** "Schedule This" button auto-sets optimal time

**Acceptance criteria:**
- [ ] Clicking "Schedule" on idea opens modal
- [ ] Modal suggests next available optimal slot (Fri 9am if today is before Friday, else next Friday)
- [ ] User can accept suggestion or pick custom time
- [ ] One click creates ScheduledPost and closes modal

**Files touched:**
- `src/threads_analytics/web/templates/ideas.html`
- `src/threads_analytics/web/static/ideas.js`

**Estimated scope:** Small (2 files)

**Dependencies:** Task 8, Task 10

---

#### Task 13: CLI Commands
**Description:** Command-line interface for power users

**Acceptance criteria:**
- [ ] `threads-analytics generate-ideas --topic "X" --count 5`
- [ ] `threads-analytics list-scheduled`
- [ ] `threads-analytics post-now --id 123` (immediate publish)

**Files touched:**
- `src/threads_analytics/cli.py` (add commands)

**Estimated scope:** Small (1 file)

**Dependencies:** Task 5, Task 9

---

### Final Checkpoint: Complete
- [ ] Full flow: Generate idea → Schedule → Auto-post
- [ ] All tests pass
- [ ] UI responsive and clean
- [ ] Rate limits respected

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Threads API rate limits (10/day) | High | Track quota in UI; warn user at 8 posts; queue gracefully |
| AI still generates slop despite rules | Medium | Pattern remix fallback; user can edit before schedule |
| Background thread dies silently | High | Health check endpoint; log rotation; restart on failure |
| Timezone confusion (WIB vs UTC) | Medium | Store UTC internally, display WIB in UI, clear labeling |
| User edits post after scheduling | Low | Copy text at schedule time; edits create new draft |

---

## Open Questions

1. **Should we support images?** Threads API supports single image. Start with text-only, add images in Phase 2?
2. **Auto-delete failed posts?** Keep for 7 days then purge, or manual delete only?
3. **Idea freshness?** Should ideas expire after N days if not used?

---

## Success Metrics

- [ ] Generate 5 ideas in < 10 seconds
- [ ] 0 AI-slop posts pass validation (test with known bad examples)
- [ ] Schedule and auto-post works end-to-end
- [ ] User can plan full week in < 15 minutes
