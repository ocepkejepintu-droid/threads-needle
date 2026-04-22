"""Maps experiments to content generation strategies.

Experiments define WHAT to test (timing, length, topic, hook, etc.).
This module translates experiment metadata into generation prompts and constraints.
"""

from __future__ import annotations

from .models import Experiment

# Default topics the user posts about
DEFAULT_TOPICS = [
    "hiring remote workers",
    "virtual assistants",
    "English-speaking accountants",
    "remote job applications and CVs",
    "remote finance roles",
    "finding qualified candidates",
]


def _extract_topic_from_experiment(experiment: Experiment) -> str:
    """Extract the actual topic from experiment title/hypothesis."""
    title = experiment.title.lower()
    
    # TOPIC experiments directly specify what to post about
    if "agentic ai" in title or "ai build" in title or "cost figure" in title:
        return "agentic AI and automation costs"
    if "hiring tier" in title or "candidate count" in title or "funnel" in title:
        return "hiring process and candidate funnel breakdowns"
    if "bali infrastructure" in title:
        return "digital nomad life and remote work infrastructure"
    if "tools are easy" in title:
        return "AI tools vs operations reality"
    if "i was wrong" in title or "skeptic-to-convert" in title:
        return "lessons learned and changed opinions after years of experience"
    
    # For other categories, use default topics with the experiment constraint
    return "hiring and remote work"


def _get_length_constraint(experiment: Experiment) -> str | None:
    """Extract character length constraint from experiment."""
    title = experiment.title.lower()
    hypothesis = experiment.hypothesis.lower()
    
    # Look for character ranges
    import re
    for text in [title, hypothesis]:
        match = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*character', text)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        match = re.search(r'(\d+)\s*character', text)
        if match:
            return f"max {match.group(1)}"
    
    if "200-400" in title or "200–400" in title:
        return "200-400"
    if "120-220" in title or "120–220" in title:
        return "120-220"
    if "cap post length" in title:
        return "200-400"
    
    return None


def _get_timing_constraint(experiment: Experiment) -> str | None:
    """Extract timing constraint from experiment."""
    title = experiment.title.lower()
    hypothesis = experiment.hypothesis.lower()
    
    import re
    for text in [title, hypothesis]:
        # Match time ranges like 13:00-16:00 UTC
        match = re.search(r'(\d{1,2}):\d{2}\s*[-–]\s*(\d{1,2}):\d{2}', text)
        if match:
            return f"{match.group(1)}:00-{match.group(2)}:00"
    
    return None


def build_experiment_prompt(experiment: Experiment) -> tuple[str, dict]:
    """Build a content generation prompt and constraints from an experiment.
    
    Returns:
        (topic, constraints) where constraints is a dict of experiment settings
    """
    category = experiment.category
    topic = _extract_topic_from_experiment(experiment)
    constraints = {
        "category": category,
        "length": _get_length_constraint(experiment),
        "timing": _get_timing_constraint(experiment),
    }
    
    # Build the prompt modifier based on category
    if category == "TIMING":
        # TIMING: normal content, just schedule it differently
        prompt_modifier = (
            f"Generate content about {topic}. "
            "The experiment is testing POSTING TIMING, so the content itself should be natural and not mention time. "
            "Focus on strong, engaging posts that work well during Indonesian evening hours."
        )
    
    elif category == "LENGTH":
        length = constraints["length"] or "under 280"
        prompt_modifier = (
            f"Generate content about {topic}. "
            f"CRITICAL: Each post MUST be exactly {length} characters. "
            "Make it punchy and direct — no filler words. Every character counts."
        )
    
    elif category == "TOPIC":
        prompt_modifier = (
            f"Generate content specifically about: {topic}. "
            "This is the exact theme being tested, so stay tightly on this subject. "
            "Use the user's natural voice — casual Indonesian-English mix with real numbers and details."
        )
    
    elif category == "HOOK":
        prompt_modifier = (
            f"Generate content about {topic}. "
            "CRITICAL: The opening line must use the specific hook pattern being tested. "
            "Open with a strong, specific detail that makes people stop scrolling."
        )
    
    elif category == "ENGAGEMENT":
        prompt_modifier = (
            f"Generate content about {topic}. "
            "The experiment is testing engagement, so write posts that naturally invite replies. "
            "End with a question, a slightly controversial take, or ask for the reader's experience."
        )
    
    elif category == "MEDIA":
        prompt_modifier = (
            f"Generate content about {topic}. "
            "The experiment is testing visual posts, so describe what image would accompany this text. "
            "Make the text work well with a screenshot, chart, or photo."
        )
    
    elif category == "CADENCE":
        prompt_modifier = (
            f"Generate content about {topic}. "
            "The experiment is testing posting frequency, so create varied post types that don't feel repetitive. "
            "Mix complaint posts, insight posts, and question posts."
        )
    
    else:
        prompt_modifier = (
            f"Generate content about {topic}. "
            f"Apply this experiment constraint: {experiment.title}. "
            "Keep the user's natural voice and posting style."
        )
    
    return prompt_modifier, constraints
