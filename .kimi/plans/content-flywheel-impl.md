# Content Flywheel Implementation Plan

Based on design spec: `docs/superpowers/specs/2026-04-12-content-flywheel-design.md`

---

## Task 1: Extend GeneratedIdea Model with Scheduling Fields

**Description:** Add `scheduled_at`, `posted_at`, `thread_id`, `error_message` fields to the existing GeneratedIdea model and create Alembic migration.

**Acceptance criteria:**
- [ ] Four new fields added to GeneratedIdea model
- [ ] Alembic migration created and tested (upgrade/downgrade)
- [ ] Fields are nullable where appropriate
- [ ] Model integrates with existing database session

**Verification:**
- [ ] Migration applies cleanly: `alembic upgrade head`
- [ ] Migration reverses cleanly: `alembic downgrade -1`
- [ ] Can query and set new fields via session

**Dependencies:** None

**Files touched:**
- `src/threads_analytics/models.py`
- `alembic/versions/` (new migration file)

**Estimated scope:** Small (2 files)

---

## Task 2: Build Anti-Slop Content Rules

**Description:** Create validation rules that reject generic AI content. Implement 5 rules with tests.

**Acceptance criteria:**
- [ ] Rule 1: Rejects generic openings ("In today's fast-paced world...", "Hey everyone!")
- [ ] Rule 2: Requires specific numbers/details in content
- [ ] Rule 3: Rejects corporate speak ("leverage", "synergy", "optimize")
- [ ] Rule 4: Rejects advice-template structures without personal context
- [ ] Rule 5: Requires emotional hook (frustration, curiosity, excitement)
- [ ] All rules have unit tests with pass/fail examples
- [ ] Returns human-ness score (0-100)

**Verification:**
- [ ] Tests pass: `pytest tests/test_content_rules.py -v`
- [ ] Test with known bad example: "In today's fast-paced world, here are 5 tips..." → should fail
- [ ] Test with your good example: "Susah juga ya cari akuntan..." → should pass

**Dependencies:** None

**Files touched:**
- `src/threads_analytics/content_rules.py` (new)
- `tests/test_content_rules.py` (new)

**Estimated scope:** Small (2 files)

---

## Checkpoint: Foundation Complete

- [ ] Database migration applies cleanly
- [ ] Content rules tests pass
- [ ] Both components work independently

---

## Task 3: Extract Voice Patterns from Top Posts

**Description:** Analyze your top 3 performing posts to extract concrete patterns (sentence starters, structure, tone markers) and store as VoiceProfile.

**Acceptance criteria:**
- [ ] Extract sentence starters from your top 3 posts
- [ ] Extract structure pattern (complaint → context → CTA)
- [ ] Extract tone markers (emoji usage, Indo-English ratio)
- [ ] Store patterns in simple data structure (dict/json)
- [ ] Voice profile is loadable by idea generator

**Verification:**
- [ ] Run: `python -c "from threads_analytics.voice_extractor import get_voice_profile; print(get_voice_profile())"`
- [ ] Output contains your patterns

**Dependencies:** Task 2 (needs content rules to validate extraction)

**Files touched:**
- `src/threads_analytics/voice_extractor.py` (new)

**Estimated scope:** Small (1 file)

---

## Task 4: Build Pattern-Driven Idea Generator

**Description:** Generate ideas using your voice patterns + anti-slop rules. Takes topic, generates 3 variations, validates each.

**Acceptance criteria:**
- [ ] Takes topic + voice profile as input
- [ ] Generates 3 variations using different hooks from pattern library
- [ ] Validates each through anti-slop rules
- [ ] Returns scored ideas with human-ness score
- [ ] Falls back to pattern remix if AI generation fails validation
- [ ] Creates GeneratedIdea records in database

**Verification:**
- [ ] CLI works: `threads-analytics generate-ideas --topic "hiring accountants" --count 3`
- [ ] Generated ideas pass anti-slop validation
- [ ] Ideas match your voice patterns (Indo-English mix, specific details)

**Dependencies:** Task 2, Task 3

**Files touched:**
- `src/threads_analytics/idea_generator.py` (new)
- `src/threads_analytics/prompts/idea_gen.txt` (new prompt template)
- `src/threads_analytics/cli.py` (add command)

**Estimated scope:** Medium (3-4 files)

---

## Checkpoint: Idea Generator Complete

- [ ] Can generate ideas from CLI
- [ ] Ideas pass anti-slop rules
- [ ] Ideas match your voice patterns
- [ ] Ideas saved to database

---

## Task 5: Create Threads Publishing Service

**Description:** Implement `publish_post(text)` method in ThreadsClient that actually posts to Threads API.

**Acceptance criteria:**
- [ ] Function `publish_post(text: str) -> thread_id` implemented
- [ ] Handles 10 posts/24h rate limit tracking
- [ ] Returns thread_id on success
- [ ] Raises descriptive error on failure
- [ ] Logs publish attempt (success/failure)

**Verification:**
- [ ] Test with dry-run: function accepts text and returns mock thread_id
- [ ] Rate limit tracking works (counts posts in last 24h)

**Dependencies:** None (extends existing ThreadsClient)

**Files touched:**
- `src/threads_analytics/publisher.py` (new)
- `src/threads_analytics/threads_client.py` (add method)

**Estimated scope:** Small (2 files)

---

## Task 6: Build Background Publisher Scheduler

**Description:** Background thread that polls every 60 seconds for due posts and publishes them.

**Acceptance criteria:**
- [ ] Runs every 60 seconds in background
- [ ] Finds posts with `scheduled_at <= now()` and status="scheduled"
- [ ] Publishes via Task 5 service
- [ ] Updates status: scheduled → published (with thread_id) or failed (with error)
- [ ] Graceful shutdown on app stop (FastAPI lifespan events)
- [ ] Logs all actions

**Verification:**
- [ ] Scheduler starts with app: `python -m threads_analytics.web` shows scheduler running
- [ ] Schedule a post for 2 minutes from now, wait, verify status changes to published

**Dependencies:** Task 5

**Files touched:**
- `src/threads_analytics/scheduler.py` (new)
- `src/threads_analytics/web/app.py` (add lifespan events)

**Estimated scope:** Medium (2 files)

---

## Checkpoint: Auto-Publishing Complete

- [ ] Scheduler runs in background
- [ ] Due posts auto-publish
- [ ] Status updates correctly
- [ ] Graceful shutdown works

---

## Task 7: Update Growth Ideas UI with Schedule Button

**Description:** Add "Schedule" dropdown to idea cards with quick-schedule options.

**Acceptance criteria:**
- [ ] Schedule dropdown appears on draft idea cards
- [ ] Dropdown shows: "Next Fri 9am", "Next Fri 10am", "Custom..."
- [ ] Clicking option schedules immediately
- [ ] Card updates to show scheduled time
- [ ] Status changes from draft → scheduled

**Verification:**
- [ ] Open `/growth/ideas`, see Schedule button on draft cards
- [ ] Click "Next Fri 9am", card updates to "Scheduled for Fri 9am"
- [ ] Database shows `scheduled_at` set correctly

**Dependencies:** Task 4, Task 6

**Files touched:**
- `src/threads_analytics/web/templates/growth_ideas.html`
- `src/threads_analytics/web/static/ideas.js` (new)
- `src/threads_analytics/web/routes.py` (add schedule endpoint)

**Estimated scope:** Medium (3 files)

---

## Task 8: Build Calendar View with Drag-and-Drop

**Description:** Weekly calendar view showing scheduled posts with drag-to-reschedule.

**Acceptance criteria:**
- [ ] Route `/calendar` displays weekly view
- [ ] Shows days Mon-Sun with scheduled posts
- [ ] Color coding: blue=scheduled, green=published, yellow=failed
- [ ] Shows post preview (first 100 chars) in calendar cell
- [ ] Can drag post to different day → updates scheduled_at
- [ ] PATCH endpoint for reschedule updates

**Verification:**
- [ ] Open `/calendar`, see current week's scheduled posts
- [ ] Drag post from Tue to Fri, refresh page, post moved
- [ ] Database shows updated `scheduled_at`

**Dependencies:** Task 7

**Files touched:**
- `src/threads_analytics/web/routes.py` (add calendar route, PATCH endpoint)
- `src/threads_analytics/web/templates/calendar.html` (new)
- `src/threads_analytics/web/static/calendar.js` (new)

**Estimated scope:** Medium (4 files)

---

## Task 9: Update Sidebar Navigation

**Description:** Add Calendar link to sidebar, remove old broken Lead links.

**Acceptance criteria:**
- [ ] Calendar icon links to `/calendar`
- [ ] Remove old Lead links if still present
- [ ] Icons and tooltips work correctly

**Verification:**
- [ ] Open any page, see Calendar icon in sidebar
- [ ] Click Calendar icon, navigates to `/calendar`

**Dependencies:** Task 8

**Files touched:**
- `src/threads_analytics/web/templates/base.html`

**Estimated scope:** XS (1 file)

---

## Task 10: Add CLI Commands

**Description:** CLI commands for power users to generate ideas, list scheduled, post now.

**Acceptance criteria:**
- [ ] `threads-analytics generate-ideas --topic "X" --count 5`
- [ ] `threads-analytics list-scheduled`
- [ ] `threads-analytics post-now --id 123` (immediate publish)

**Verification:**
- [ ] All three CLI commands work and produce expected output

**Dependencies:** Task 4, Task 5

**Files touched:**
- `src/threads_analytics/cli.py` (add commands)

**Estimated scope:** Small (1 file)

---

## Final Checkpoint: Complete

- [ ] Full flow works: Generate idea → Schedule → Auto-post
- [ ] All tests pass
- [ ] UI responsive and clean
- [ ] Rate limits respected
- [ ] User can plan full week in < 15 minutes

---

## Implementation Order

**Phase 1 (Foundation):** Tasks 1-2 → Checkpoint
**Phase 2 (Generator):** Tasks 3-4 → Checkpoint  
**Phase 3 (Publishing):** Tasks 5-6 → Checkpoint
**Phase 4 (UI):** Tasks 7-9 → Checkpoint
**Phase 5 (CLI):** Task 10 → Final Checkpoint

**Parallel opportunities:**
- Task 2 (content rules) can be done in parallel with Task 1 (database)
- Task 3 (voice extraction) can be done in parallel with Task 5 (publisher)
