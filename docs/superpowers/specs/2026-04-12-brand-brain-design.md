# Brand Brain Design

**Date:** 2026-04-12  
**Status:** Approved for implementation  
**Scope:** Pre-post validator + Real-time brand composer

---

## 1. Overview

Protect and enforce your unique voice across all Threads content. Before any post goes live, validate it against your "You" profile (voice signatures, protect list, double-down list). Give real-time feedback as you write, and score every draft for brand alignment.

---

## 2. Goals

- Prevent off-brand posts from ever being published
- Give real-time writing feedback (like Grammarly but for brand voice)
- Maintain consistency as content volume scales
- Protect what makes your account distinctive

---

## 3. Architecture

### 3.1 Components

```
┌─────────────────────────────────────────────────────────┐
│                    Brand Brain System                    │
├─────────────────────────────────────────────────────────┤
│  A. Voice Validator (pre-post check)                     │
│     └─ Validates draft posts before scheduling          │
│                                                         │
│  B. Real-time Composer (writing assistant)              │
│     └─ Live brand score as you type                     │
│                                                         │
│  C. Violation Reporter (post-mortem)                    │
│     └─ Analyze published posts for drift                │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Data Flow

```
Draft Post → Voice Validator → [PASS/FAIL]
                      ↓
              If FAIL: Show violations
              If PASS: Allow scheduling

Live Typing → Real-time Analyzer → Brand Score (0-100)
                      ↓
              Suggestions appear inline

Published Post → Violation Reporter → Weekly Brand Report
```

---

## 4. Database Models

**BrandCheck** (new table - validation results)
```python
class BrandCheck(Base):
    id: int (PK)
    
    # What was checked
    content_type: str  # "scheduled_post", "lead_reply", "experiment_hypothesis"
    content_text: str
    you_profile_id: int (FK → YouProfile)  # snapshot at time of check
    
    # Results
    overall_score: int  # 0-100
    passed: bool  # True if score >= 70
    
    # Breakdown
    voice_alignment_score: int  # 0-100
    protect_list_violations: list[str]  # JSON array of violated items
    double_down_score: int  # how many double-down elements present
    
    # Feedback
    suggestions: list[dict]  # JSON: [{"issue": "...", "suggestion": "..."}]
    
    checked_at: datetime
```

**BrandViolation** (new table - track violations over time)
```python
class BrandViolation(Base):
    id: int (PK)
    brand_check_id: int (FK → BrandCheck)
    
    violation_type: str  # "protect_list", "voice_mismatch", "generic_tone"
    severity: str  # "high", "medium", "low"
    description: str  # what was violated
    snippet: str  # the offending text
    
    created_at: datetime
```

---

## 5. Voice Validator (Pre-Post Check)

### 5.1 System Prompt

```
You are a brand guardian analyzing content against a creator's established voice profile.

VOICE PROFILE:
{you_profile.core_identity}

STYLISTIC SIGNATURES (must preserve):
{you_profile.stylistic_signatures}

PROTECT LIST (NEVER violate):
{you_profile.protect_list}

DOUBLE-DOWN LIST (strengthen when present):
{you_profile.double_down_list}

CONTENT TO ANALYZE:
{draft_text}

Analyze and respond with JSON:
{
  "overall_score": 0-100,
  "passed": true/false,
  "voice_alignment": 0-100,
  "protect_violations": ["violated item 1", "violated item 2"],
  "double_down_elements": ["element 1", "element 2"],
  "suggestions": [
    {"issue": "too generic", "suggestion": "add specific example from your work"}
  ],
  "tone_analysis": "brief assessment of tone match"
}
```

### 5.2 Validation Rules

**Auto-fail triggers (score = 0):**
- Any protect_list violation (these are non-negotiable)
- Excessive generic business speak ("synergy", "leverage", "optimize")
- Tone completely mismatched (formal vs casual mismatch)

**Scoring weights:**
- Protect list compliance: 40% (binary - any violation = fail)
- Voice alignment: 35% (matches stylistic signatures)
- Double-down presence: 15% (includes signature elements)
- Uniqueness: 10% (not generic/could only be you)

**Pass threshold:** 70/100

### 5.3 Integration Points

**Before scheduling a post:**
```python
def validate_before_schedule(post_text: str) -> BrandCheck:
    you_profile = get_latest_you_profile()
    result = run_brand_check(post_text, you_profile)
    
    if not result.passed:
        raise BrandViolationError(result.violations)
    
    return result
```

**Before sending lead reply:**
```python
# In leads.py send_reply()
brand_check = validate_before_schedule(reply_text)
if not brand_check.passed:
    # Still allow but warn user
    log.warning("Brand violation in reply: %s", brand_check.violations)
```

---

## 6. Real-Time Composer

### 6.1 UI Design

**New page: `/compose`**

```
┌────────────────────────────────────────────────────────────┐
│ Brand Composer                                    Score: 85 │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Working on a new automation that saves 20hrs/week   │ │
│  │ for our accounting clients.                         │ │
│  │                                                      │ │
│  │ Most people think it's just "faster data entry"... │ │
│  │ but it's actually about removing the cognitive     │ │
│  │ load that makes accountants quit.                  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  Score: 85/100                                            │
│  ████████████████████░░░░░░░░░░                          │
│                                                            │
│  ✅ Protect list: Clear                                   │
│  ✅ Voice: Matches "contrarian takes backed by data"     │
│  ✅ Double-down: Specific numbers (20hrs), real impact    │
│  ⚠️ Suggestion: Add one more specific client detail      │
│                                                            │
│  [Save Draft]  [Check Again]  [Schedule Post]            │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 6.2 Live Analysis

**Debounce:** 500ms after typing stops
**Analysis triggers:**
- Character count > 50 (don't check too early)
- Pause in typing
- Manual "Check" button

**Score display:**
- 90-100: 🟢 Strong brand alignment
- 70-89: 🟡 Good, minor tweaks suggested
- 50-69: 🟠 Off-brand risk
- 0-49: 🔴 Significant violations

### 6.3 Inline Suggestions

**As you type, highlight issues:**
```
"We leverage cutting-edge AI to optimize your workflows"
    ^^^^^^^   ^^^^^^^^^^^^^^^
    [Too corporate - try "use"] [Too buzzwordy - be specific]
```

**Popup on hover:**
- Why this is flagged
- Suggested rewrite
- "Ignore this" button (learns your preferences)

---

## 7. Violation Reporter (Post-Mortem)

### 7.1 Weekly Brand Report

**New page: `/you/brand-report`**

```
Brand Health Report (Last 7 Days)
═══════════════════════════════════════════════════════════

Overall Score Trend
[Sparkline: 82 → 78 → 85 → 81 → 88 → 86 → 89]

Posts Analyzed: 12
Passed: 10 (83%)
Flagged: 2 (17%)

Top Violations This Week
1. "Too generic" - 3 posts (add more specific examples)
2. "Missing contrarian angle" - 2 posts (your signature)
3. "Overly formal" - 1 post (relax the tone)

Protect List Status: ✅ Clean (0 violations)

Recommendations
• You're drifting toward generic business advice
• Add more "behind the scenes" specifics
• Include more data points (your strength)

Compare to Your Best Posts
┌────────────────────────────────────────────────────────┐
│ This week's avg: 84/100                                │
│ Your best post ever: 96/100                            │
│ Gap: +12 points possible                               │
└────────────────────────────────────────────────────────┘
```

### 7.2 Drift Detection

**Alert when:**
- 3+ consecutive posts score < 70
- Weekly average drops > 10 points from previous week
- Protect list violation occurs (immediate alert)

---

## 8. UI Components

### 8.1 Brand Score Badge

**Add to post cards (Experiments, Posts pages):**
```
┌──────────────────────┐
│ Post title...       [85] │ ← small badge
│ Preview text...          │
└──────────────────────┘
```

### 8.2 Validation Modal

**When user tries to schedule low-score post:**
```
⚠️ Brand Check Failed (Score: 45/100)

Violations:
• "Too generic" - This could be from any account
• Missing your signature "data-backed" angle
• Protect list: Mentions "AI" without "your own data"

Options:
[Edit Post]  [Schedule Anyway (Not Recommended)]  [Save as Draft]
```

---

## 9. CLI Commands

```bash
# Check a post without publishing
threads-analytics brand-check "Your post text here"

# Show brand health stats
threads-analytics brand-health

# Force recheck all recent posts
threads-analytics brand-audit --since 2026-01-01

# Update You profile (re-run analysis)
threads-analytics refresh-you-profile
```

---

## 10. Implementation Plan

**Phase 1: Voice Validator**
- BrandCheck model
- Validation logic
- Integration with scheduler

**Phase 2: Real-Time Composer**
- `/compose` page
- Live scoring UI
- Debounced analysis

**Phase 3: Violation Reporter**
- Weekly reports
- Drift detection
- Trend analytics

---

## 11. Success Metrics

After 30 days:
- [ ] 90%+ of scheduled posts pass brand check
- [ ] Average brand score > 80/100
- [ ] 0 protect list violations in published posts
- [ ] Users report "feels more like me" in content

---

**Spec approved for implementation.**
