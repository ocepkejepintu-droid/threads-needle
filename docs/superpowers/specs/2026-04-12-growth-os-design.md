# Growth OS Design

**Date:** 2026-04-12  
**Status:** Approved for implementation  
**Scope:** Pattern extraction + Content generator + Feedback loop

---

## 1. Overview

Transform analytics into a self-improving content engine. Extract patterns from your best-performing posts, generate new content that matches those patterns, track performance, and feed insights back into the generator. This is the "brain" that makes your content consistently better over time.

---

## 2. Goals

- Identify what makes your posts viral (patterns from top 10%)
- Generate post ideas that match winning patterns
- Predict performance before publishing
- Auto-improve based on real results
- Build your personal "content playbook"

---

## 3. Architecture

### 3.1 The Growth Loop

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   INGEST    │────▶│   ANALYZE   │────▶│   EXTRACT   │
│  (posts +   │     │  (metrics)  │     │ (patterns)  │
│  insights)  │     │             │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
┌─────────────┐     ┌─────────────┐     ┌──────▼──────┐
│   LEARN     │◄────│   MEASURE   │◄────│   GENERATE  │
│  (update    │     │  (did it    │     │  (new post  │
│  patterns)  │     │   work?)    │     │   ideas)    │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 3.2 Components

| Component | Purpose | Data Source |
|-----------|---------|-------------|
| Pattern Extractor | Find what top posts have in common | MyPost + MyPostInsight |
| Content Generator | Create new posts using patterns | Patterns + Topics + YouProfile |
| Performance Predictor | Score post ideas before publishing | Historical performance |
| Feedback Loop | Update patterns based on results | Published post metrics |

---

## 4. Database Models

**ContentPattern** (new table - extracted patterns)
```python
class ContentPattern(Base):
    id: int (PK)
    
    # What was found
    pattern_type: str  # "hook", "structure", "timing", "topic", "format"
    pattern_name: str  # "contrarian opener", "data sandwich", "question hook"
    description: str  # human-readable explanation
    
    # Examples this came from
    example_post_ids: list[str]  # JSON array of thread_ids
    example_count: int  # how many posts showed this pattern
    
    # Performance stats
    avg_views: int
    avg_engagement_rate: float
    success_rate: float  # % of posts with this pattern that outperformed median
    
    # Metadata
    extracted_at: datetime
    confidence_score: float  # 0.0-1.0, how strong is this pattern
    is_active: bool  # can be retired if stops working
```

**GeneratedIdea** (new table - AI-generated content ideas)
```python
class GeneratedIdea(Base):
    id: int (PK)
    
    # The idea
    title: str  # short name
    concept: str  # the actual post text/idea
    
    # What patterns it uses
    patterns_used: list[int]  # JSON array of ContentPattern IDs
    
    # Predicted performance
    predicted_score: int  # 0-100
    predicted_views_range: str  # "1k-5k", "5k-20k", "20k+"
    
    # Status
    status: str  # "draft", "approved", "scheduled", "published", "rejected"
    
    # If published, actual results
    actual_post_id: str | None  # link to MyPost
    actual_performance: dict | None  # JSON with views, likes, etc.
    
    created_at: datetime
    generated_by: str  # "ai", "hybrid", "manual"
```

**PatternPerformance** (new table - track pattern effectiveness over time)
```python
class PatternPerformance(Base):
    """Time-series tracking of pattern effectiveness."""
    pattern_id: int (PK, FK → ContentPattern)
    date: date (PK)
    
    posts_using_pattern: int
    avg_performance_vs_baseline: float  # 1.0 = baseline, 2.0 = 2x better
    
    # Trend direction
    trend: str  # "improving", "stable", "declining"
```

---

## 5. Pattern Extractor

### 5.1 What to Extract

**Hook Patterns (first sentence):**
- Contrarian: "Most people think X, but actually Y"
- Data: "X% of [thing] fail because..."
- Question: "Why does X happen?"
- Story: "In 2023, I..."
- Bold claim: "This one change made us $X"

**Structure Patterns:**
- Data Sandwich: Hook → Data/Proof → Takeaway
- Story Arc: Setup → Conflict → Resolution → Lesson
- Listicle: "X things I learned about Y"
- Thread: Multi-post narrative

**Timing Patterns:**
- Day of week correlation
- Time of day performance
- Posting frequency impact

**Topic/Format Patterns:**
- Which topic + format combos perform best
- Text vs image vs video performance
- Carousel effectiveness

### 5.2 Extraction Algorithm

```python
def extract_patterns(session: Session) -> list[ContentPattern]:
    """Find patterns in top 20% of posts."""
    
    # 1. Identify top performers (top 20% by views)
    all_posts = get_posts_with_metrics(session)
    top_posts = sorted(all_posts, key=lambda p: p.views, reverse=True)[:int(len(all_posts) * 0.2)]
    
    # 2. Analyze hooks
    hook_patterns = analyze_hooks(top_posts)
    
    # 3. Analyze structure
    structure_patterns = analyze_structure(top_posts)
    
    # 4. Analyze timing
    timing_patterns = analyze_timing(top_posts)
    
    # 5. Store patterns with confidence scores
    patterns = []
    for pattern_type, findings in [
        ("hook", hook_patterns),
        ("structure", structure_patterns),
        ("timing", timing_patterns),
    ]:
        for finding in findings:
            if finding['confidence'] > 0.6:  # Only strong patterns
                pattern = ContentPattern(
                    pattern_type=pattern_type,
                    pattern_name=finding['name'],
                    description=finding['description'],
                    example_post_ids=finding['examples'],
                    example_count=len(finding['examples']),
                    avg_views=finding['avg_views'],
                    success_rate=finding['success_rate'],
                    confidence_score=finding['confidence'],
                )
                patterns.append(pattern)
    
    return patterns
```

### 5.3 Hook Analysis Prompt (Claude)

```
Analyze these top-performing posts and identify common hook patterns.

TOP POSTS:
{% for post in top_posts %}
Post {{ loop.index }} ({{ post.views }} views):
"{{ post.text[:200] }}"

{% endfor %}

Identify hook patterns like:
- Contrarian takes
- Data-driven openers
- Story-based hooks
- Question hooks
- Bold claims

For each pattern found:
1. Name it
2. Describe the structure
3. List which posts use it
4. Calculate average views
5. Assign confidence (0-1)

Return JSON array of patterns.
```

---

## 6. Content Generator

### 6.1 Idea Generation

**Input:**
- Extracted patterns (what works)
- Your topics (what you talk about)
- You profile (how you sound)
- Recent trends (what's relevant)

**Output:**
- 5-10 post ideas
- Each with predicted score
- Pattern breakdown (which patterns it uses)

### 6.2 Generation Prompt

```
Generate 5 post ideas for a Threads account.

ACCOUNT PROFILE:
{{ you_profile.core_identity }}

TOPICS:
{{ topics }}

WINNING PATTERNS (use these):
{% for pattern in patterns %}
- {{ pattern.pattern_name }}: {{ pattern.description }}
  Example: "{{ pattern.examples[0] }}"
{% endfor %}

STYLISTIC SIGNATURES:
{{ you_profile.stylistic_signatures }}

Generate 5 distinct post ideas that:
1. Use at least one winning pattern
2. Match the stylistic signatures
3. Cover different topics
4. Sound like the account owner (not generic)

For each idea, provide:
- Title/name
- Full post text (under 280 chars if possible)
- Which patterns it uses
- Why it should work

Return as JSON array.
```

### 6.3 Performance Prediction

**Simple heuristic model:**
```python
def predict_performance(idea: GeneratedIdea) -> int:
    """Predict 0-100 score based on pattern history."""
    
    base_score = 50  # Neutral baseline
    
    # Add points for each pattern used
    for pattern_id in idea.patterns_used:
        pattern = get_pattern(pattern_id)
        if pattern.success_rate > 0.5:
            base_score += 15
        elif pattern.success_rate > 0.3:
            base_score += 5
    
    # Bonus for combining multiple strong patterns
    if len(idea.patterns_used) >= 2:
        base_score += 10
    
    # Penalty if no patterns (untested approach)
    if not idea.patterns_used:
        base_score -= 20
    
    return min(max(base_score, 0), 100)
```

---

## 7. Feedback Loop

### 7.1 Weekly Pattern Update

```python
def update_pattern_performance():
    """Run every week to update pattern effectiveness."""
    
    for pattern in active_patterns:
        # Get all posts using this pattern (published in last 30 days)
        posts = get_posts_using_pattern(pattern)
        
        if len(posts) >= 5:  # Need minimum sample size
            avg_performance = calculate_avg_performance(posts)
            baseline = get_baseline_performance()
            
            vs_baseline = avg_performance / baseline
            
            # Update pattern stats
            pattern.avg_performance_vs_baseline = vs_baseline
            
            # Determine trend
            if vs_baseline > 1.2:
                pattern.trend = "improving"
            elif vs_baseline < 0.8:
                pattern.trend = "declining"
            else:
                pattern.trend = "stable"
            
            # Retire patterns that stop working
            if pattern.trend == "declining" for 3 consecutive weeks:
                pattern.is_active = False
```

### 7.2 Learn and Improve

**Pattern refinement:**
- If a pattern's success rate drops below 30% for 4 weeks → retire it
- If new pattern emerges from recent top posts → add it
- If pattern works better with certain topics → note the combo

---

## 8. UI Components

### 8.1 Pattern Library

**New page: `/growth/patterns`**

```
Your Winning Patterns
═══════════════════════════════════════════════════════════

Hooks (3 patterns)
┌─────────────────────────────────────────────────────────┐
│ 🏆 Contrarian Take                                        │
│ Used in: 8 posts | Avg views: 12,400 | Success: 75%      │
│ Example: "Most people think AI replaces workers. It      │
│ doesn't—it replaces tasks. Here's the difference..."     │
│ [View all posts]  [Generate more like this]              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 📊 Data-Driven Opener                                     │
│ Used in: 5 posts | Avg views: 8,900 | Success: 60%       │
│ Example: "73% of BPOs fail in year one. Not because of   │
│ competition—because of this one mistake..."              │
│ [View all posts]  [Generate more like this]              │
└─────────────────────────────────────────────────────────┘

Structures (2 patterns)
[...]

Timing (1 pattern)
[...]
```

### 8.2 Idea Generator

**New page: `/growth/ideas`**

```
Generated Ideas (Last 7 Days)
═══════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────┐
│ 💡 Why We Stopped Hiring "Experienced" Accountants       │
│ Score: 88/100  🟢 High Predicted Performance            │
│ Patterns: Contrarian Take + Data-Driven                 │
│ Topic: Hiring/Talent                                    │
│                                                         │
│ "We stopped hiring 'experienced' accountants.           │
│ Instead, we hire curious people and train them.         │
│ Result: 40% better retention, 25% lower cost.           │
│ Here's our 90-day training system..."                   │
│                                                         │
│ [Edit]  [Schedule]  [Dismiss]  [Mark as Used]          │
└─────────────────────────────────────────────────────────┘

[Generate 5 More Ideas]
```

### 8.3 Performance Dashboard

**New page: `/growth/performance`**

```
Growth OS Performance
═══════════════════════════════════════════════════════════

This Month vs Last Month
┌─────────────────┬─────────────┬─────────────┬──────────┐
│ Metric          │ This Month  │ Last Month  │ Change   │
├─────────────────┼─────────────┼─────────────┼──────────┤
│ Avg Views       │ 4,200       │ 3,100       │ +35% 🟢  │
│ Posts Using AI  │ 8           │ 4           │ +100% 🟢 │
│ Pattern Success │ 72%         │ 65%         │ +7% 🟢   │
│ Viral Posts     │ 2           │ 0           │ +2 🟢    │
└─────────────────┴─────────────┴─────────────┴──────────┘

Pattern Effectiveness Over Time
[Line chart: Contrarian ↑, Data-Driven →, Story Arc ↓]

AI-Generated vs Manual Posts
┌──────────────┬────────────┬──────────────┐
│ Type         │ Avg Views  │ Engagement   │
├──────────────┼────────────┼──────────────┤
│ AI-Generated │ 4,800      │ 8.2%         │
│ Manual       │ 3,200      │ 6.1%         │
└──────────────┴────────────┴──────────────┘
```

---

## 9. Integration Points

### 9.1 Pipeline Integration

New step in `pipeline.py`:
```python
# 13. Pattern extraction (weekly, not every run)
if should_extract_patterns():
    summary["patterns"] = extract_patterns(session)

# 14. Update pattern performance
summary["pattern_performance"] = update_pattern_performance(session)
```

### 9.2 Experiment Integration

Generated ideas can become experiments:
```python
# In growth module
idea = generate_idea()
if idea.predicted_score > 80:
    # Auto-create experiment hypothesis
    experiment = create_experiment(
        title=f"Test: {idea.title}",
        hypothesis="Posts using pattern X will outperform baseline",
        category="CONTENT",
        # ...
    )
```

---

## 10. CLI Commands

```bash
# Extract patterns manually
threads-analytics extract-patterns

# Generate content ideas
threads-analytics generate-ideas --count 10

# Show pattern performance
threads-analytics pattern-stats

# Predict performance of a draft
threads-analytics predict "Your draft post text"

# Compare AI vs manual performance
threads-analytics growth-report
```

---

## 11. Implementation Phases

**Phase 1: Pattern Extractor**
- ContentPattern model
- Hook/structure/timing analysis
- Pattern library UI

**Phase 2: Content Generator**
- GeneratedIdea model
- AI generation pipeline
- Ideas queue UI

**Phase 3: Feedback Loop**
- Performance tracking
- Pattern retirement
- Weekly reports

---

## 12. Success Metrics

After 60 days:
- [ ] 5+ distinct patterns extracted from top posts
- [ ] 20+ AI-generated ideas created
- [ ] 5+ ideas published with tracked performance
- [ ] Pattern success rate > 60%
- [ ] AI-generated posts outperform manual by 20%+

---

**Spec approved for implementation.**
