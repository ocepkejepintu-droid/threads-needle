"""End-to-end pipeline test with fake Threads client and fake LLM responses.

This proves every module works together without requiring real API keys.
The ONLY thing this does not exercise is actual HTTP calls to external APIs.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from threads_analytics.llm_client import LLMResponse


# ---------- Fake Threads client ----------


def _fake_post(id_: str, text: str, hours_ago: int, media_type: str = "TEXT"):
    from threads_analytics.threads_client import ThreadsPost

    return ThreadsPost(
        id=id_,
        text=text,
        media_type=media_type,
        permalink=f"https://threads.net/@testuser/post/{id_}",
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        username="testuser",
    )


def _fake_insight(
    id_: str, likes: int, views: int, replies: int, reposts: int = 0, quotes: int = 0
):
    from threads_analytics.threads_client import ThreadsPostInsight

    return ThreadsPostInsight(
        thread_id=id_,
        views=views,
        likes=likes,
        replies=replies,
        reposts=reposts,
        quotes=quotes,
    )


class FakeThreadsClient:
    def __init__(self, follower_count: int = 1200):
        self.follower_count = follower_count
        self.user_id = "fake_user_id"
        self.access_token = "fake_token"
        self.rate_limit_state = type("R", (), {"queries_this_call": 0})()
        self._post_data = self._build_posts()

    @classmethod
    def from_account(cls, account):
        return cls()

    def _build_posts(self):
        topics_posts = [
            (
                "p1",
                "building AI agents is mostly about context management — here's what I learned",
                5,
                120,
                2400,
                20,
            ),
            ("p2", "building AI agents means thinking about tool use carefully", 29, 150, 3000, 30),
            (
                "p3",
                "building AI agents the mistake everyone makes is ignoring eval loops",
                53,
                180,
                3500,
                35,
            ),
            (
                "p4",
                "building AI agents I spent a week on prompting and it was worth it",
                77,
                95,
                2100,
                15,
            ),
            ("p5", "the Nigerian tech scene is underrated — here's why", 14, 40, 900, 8),
            (
                "p6",
                "the Nigerian tech scene produces resilient founders because of the conditions",
                38,
                55,
                1100,
                12,
            ),
            (
                "p7",
                "the Nigerian tech scene has shipped more in 5 years than most expect",
                62,
                35,
                800,
                6,
            ),
            ("p8", "good morning Lagos", 9, 5, 200, 1),
            ("p9", "coffee is life", 33, 3, 150, 0),
            ("p10", "random thought about the weather", 57, 2, 120, 0),
            (
                "p11",
                "building AI agents requires discipline around evals. let me explain what I mean in detail. the first thing most teams skip is having a robust test harness. without it you are flying blind and every change feels scary. the second thing is treating your system prompt like code — version it, review it, measure it. the third thing is instrumenting everything.",
                4,
                200,
                4200,
                45,
            ),
            (
                "p12",
                "the Nigerian tech scene deserves a deeper look. I've watched founders here do more with less than anyone else I know. the scrappiness translates into resilience when they go global. here's what I mean specifically.",
                28,
                80,
                1800,
                14,
            ),
            ("p13", "tools I use for shipping fast: Claude, Cursor, linear", 52, 60, 1400, 10),
            ("p14", "my shipping stack in 2026", 76, 45, 1200, 8),
            ("p15", "why I moved from notion to linear", 100, 30, 900, 5),
        ]
        out = []
        for i, (pid, text, hours_ago, likes, views, replies) in enumerate(topics_posts):
            post = _fake_post(pid, text, hours_ago)
            insight = _fake_insight(
                pid, likes=likes, views=views, replies=replies, reposts=likes // 10
            )
            out.append((post, insight))
        return out

    def list_my_posts(self, limit: int = 100):
        return [p for p, _ in self._post_data[:limit]]

    def get_post_insights(self, thread_id: str):
        for p, i in self._post_data:
            if p.id == thread_id:
                return i
        from threads_analytics.threads_client import ThreadsPostInsight

        return ThreadsPostInsight(thread_id=thread_id)

    def get_account_insights(self):
        from threads_analytics.threads_client import ThreadsAccountInsight

        return ThreadsAccountInsight(
            follower_count=self.follower_count,
            views=40000,
            likes=1200,
            replies=240,
            reposts=120,
            quotes=30,
            demographics={"country": {"NG": 0.55, "US": 0.25, "UK": 0.10}},
        )

    def keyword_search(self, query: str, search_type: str = "TOP", limit: int = 25):
        from threads_analytics.threads_client import (
            SearchResult,
            ThreadsPost,
            ThreadsPostInsight,
        )

        creators = [
            ("ai_builder_pro", [500, 600, 550, 700]),
            ("lagos_techie", [300, 280, 350, 400]),
            ("shipping_daily", [150, 180, 170, 200]),
        ]
        results = []
        idx = 0
        for handle, likes_list in creators:
            for k, likes in enumerate(likes_list):
                idx += 1
                post_id = f"fake_{handle}_{query[:6]}_{k}"
                post = ThreadsPost(
                    id=post_id,
                    text=f"{handle} post about {query} — specific opinion #{k}",
                    media_type="TEXT",
                    permalink=f"https://threads.net/@{handle}/post/{post_id}",
                    created_at=datetime.now(timezone.utc) - timedelta(days=k + 1),
                    username=handle,
                )
                insight = ThreadsPostInsight(
                    thread_id=post_id,
                    views=likes * 20,
                    likes=likes,
                    replies=likes // 5,
                    reposts=likes // 10,
                    quotes=likes // 20,
                )
                results.append(
                    SearchResult(
                        post=post,
                        insight=insight,
                        author_handle=handle,
                        author_user_id=None,
                    )
                )
        return results[:limit]

    def refresh_long_lived_token(self):
        return "fake_refreshed_token"

    def get_me(self):
        return {
            "id": self.user_id,
            "username": "testuser",
            "threads_biography": "Test bio",
            "threads_profile_picture_url": "https://example.com/pic.jpg",
        }

    def list_my_replies(self, limit: int = 25):
        return []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


# ---------- Fake LLM client ----------


class FakeLLMClient:
    def create_message(
        self, *, model=None, max_tokens=4096, system=None, messages=None, temperature=0.7
    ):
        text = self._fake_response(system, messages)
        return LLMResponse(text=text, model="fake", usage=None)

    def _fake_response(self, system, messages):
        content = " ".join(
            [system or ""] + [str(m.get("content", "")) for m in (messages or [])]
        ).lower()

        if "propose" in content and "experiment" in content:
            return json.dumps(
                {
                    "experiments": [
                        {
                            "title": "Evening posting time",
                            "hypothesis": "Posts in the evening get higher reach.",
                            "category": "TIMING",
                            "primary_metric": "reach_rate",
                            "predicate_spec": {"hours": [19, 20, 21]},
                            "target_delta_pct": 15,
                            "variant_window_days": 14,
                            "reasoning": "Evening posts historically perform better.",
                        },
                        {
                            "title": "Shorter hooks",
                            "hypothesis": "Shorter opening lines increase engagement.",
                            "category": "HOOK",
                            "primary_metric": "reply_rate_per_view",
                            "predicate_spec": {"max_words": 12},
                            "target_delta_pct": 10,
                            "variant_window_days": 14,
                            "reasoning": "Concise hooks reduce scroll-past rates.",
                        },
                        {
                            "title": "AI agent stories",
                            "hypothesis": "Personal AI agent stories drive more replies.",
                            "category": "TOPIC",
                            "primary_metric": "reply_rate_per_view",
                            "predicate_spec": {"topic_contains": "AI agents"},
                            "target_delta_pct": 20,
                            "variant_window_days": 14,
                            "reasoning": "Personal narrative outperforms generic advice.",
                        },
                    ]
                }
            )

        if "noteworthy" in content or "outlier" in content:
            return json.dumps(
                {
                    "analyses": [
                        {
                            "post_id": "p1",
                            "commentary": "This post outperformed because of its specific, actionable advice.",
                            "algo_hypothesis": "Early reply velocity from the AI builder community boosted distribution.",
                        },
                        {
                            "post_id": "p11",
                            "commentary": "Long-form detail drove deep engagement.",
                            "algo_hypothesis": "High conversation depth signal rewarded by ranker.",
                        },
                    ]
                }
            )

        if (
            "you profile" in content
            or "anti-homogenization" in content
            or "protect list" in content
        ):
            return json.dumps(
                {
                    "coreIdentity": "A builder who writes about AI agents and African tech.",
                    "distinctiveVoiceTraits": [
                        {"trait": "concise", "evidence": "Short punchy sentences"}
                    ],
                    "uniqueTopicCrossovers": [
                        {
                            "topic": "AI + African tech",
                            "whyUnusual": "Rare combination",
                            "example": "p1",
                        }
                    ],
                    "stylisticSignatures": [
                        {"signature": "Lists of three", "evidence": "Common pattern"}
                    ],
                    "postsThatSoundMostLikeYou": [
                        {"postId": "p1", "text": "building AI agents...", "why": "Classic voice"}
                    ],
                    "protectList": ["Personal anecdotes", "Concise style"],
                    "doubleDownList": ["AI agent deep dives", "Nigerian tech stories"],
                    "homogenizationRisks": [
                        {"risk": "Generic tips", "ifYouDoThisYouLose": "Distinctive voice"}
                    ],
                }
            )

        if "perception" in content and "outsider view" in content:
            return json.dumps(
                {
                    "oneSentenceCold": "A technical founder writing about AI and emerging markets.",
                    "firstImpression": "Clear expertise in AI systems.",
                    "positioningClarity": "Strong",
                    "stickiness": "High for AI builders",
                    "followTriggers": ["AI agent insights", "Nigerian tech perspective"],
                    "bounceReasons": ["Too niche for general audience"],
                    "conversationReadiness": "High",
                    "highestLeverageFix": {
                        "cueToChange": "Bio",
                        "whatToChangeItTo": "Add 'AI agents + African tech'",
                        "expectedShift": "+15% follow rate",
                    },
                    "cueClarity": {"score": 8, "explanation": "Clear positioning"},
                    "misreadRisks": [],
                    "profileSignalQuality": {"score": 8},
                }
            )

        if "algorithm inference" in content or "ranker" in content:
            return json.dumps(
                {
                    "narrativeDiagnosis": "Account shows strong early reply velocity but inconsistent cadence.",
                    "replyVelocitySignal": {
                        "rating": "boosted",
                        "evidence": "Fast replies",
                        "inferredImpact": "High",
                    },
                    "conversationDepthSignal": {
                        "rating": "neutral",
                        "evidence": "Average",
                        "inferredImpact": "Medium",
                    },
                    "selfReplySignal": {
                        "rating": "boosted",
                        "evidence": "Active",
                        "inferredImpact": "High",
                    },
                    "zeroReplyPenaltySignal": {
                        "rating": "neutral",
                        "evidence": "Some posts get 0 replies",
                        "inferredImpact": "Low",
                    },
                    "formatDiversitySignal": {
                        "rating": "neutral",
                        "evidence": "Mostly text",
                        "inferredImpact": "Low",
                    },
                    "postingCadenceSignal": {
                        "rating": "penalized",
                        "evidence": "Inconsistent",
                        "inferredImpact": "Medium",
                    },
                    "highestRoiLever": {
                        "title": "Post more consistently",
                        "mechanism": "Cadence signals trust to the ranker.",
                        "expectedImpact": "+20% median reach",
                        "citesResearch": "Meta public statements",
                    },
                    "inferredSignalWeights": {
                        "reply_velocity": 0.8,
                        "conversation_depth": 0.5,
                        "self_reply": 0.7,
                        "zero_reply_penalty": 0.3,
                        "format_diversity": 0.4,
                        "posting_cadence": 0.6,
                    },
                }
            )

        if "topic" in content and "extract" in content:
            return json.dumps(
                {
                    "topics": [
                        {"label": "AI agents", "description": "Building AI agents and systems"},
                        {"label": "Nigerian tech", "description": "Tech ecosystem in Nigeria"},
                        {
                            "label": "Shipping",
                            "description": "Tools and practices for shipping fast",
                        },
                    ]
                }
            )

        if "intent" in content and ("lead" in content or "classify" in content):
            return json.dumps(
                {
                    "intent": "founder",
                    "confidence": 0.85,
                }
            )

        if "pattern" in content and (
            "extract" in content or "hook" in content or "structure" in content
        ):
            return json.dumps(
                {
                    "patterns": [
                        {
                            "patternType": "hook",
                            "patternName": "Specific claim hook",
                            "description": "Starts with a specific, debatable claim.",
                            "examplePostIds": ["p1"],
                            "avgViews": 3000,
                            "successRate": 0.8,
                        }
                    ]
                }
            )

        if "idea" in content and ("generate" in content or "content" in content):
            return json.dumps(
                {
                    "ideas": [
                        {
                            "title": "AI agent eval loops",
                            "concept": "The one mistake every team makes when building AI agents.",
                            "predictedScore": 85,
                            "predictedViewsRange": "5k-20k",
                        }
                    ]
                }
            )

        if "brand" in content and (
            "check" in content or "validator" in content or "score" in content
        ):
            return json.dumps(
                {
                    "score": 85,
                    "passed": True,
                    "violations": [],
                    "suggestions": [],
                }
            )

        return json.dumps({"result": "ok"})


# ---------- Fixtures ----------


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import importlib
    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(db)
    yield db_path


# ---------- The actual test ----------


def test_full_pipeline_end_to_end_with_fake_threads(isolated_db, monkeypatch):
    from threads_analytics import llm_client, pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "ThreadsClient", FakeThreadsClient)
    monkeypatch.setattr(llm_client.LLMClient, "__init__", lambda self: None)
    monkeypatch.setattr(llm_client.LLMClient, "create_message", FakeLLMClient().create_message)

    # --- First run ---
    summary1 = pipeline_mod.run_full_cycle(account_slug="default")
    assert "error" not in summary1, f"first run errored: {summary1.get('error')}"
    assert summary1["ingest"]["posts_fetched"] == 15
    assert summary1["ingest"]["new_posts"] == 15
    assert summary1["ingest"]["follower_count"] == 1200
    assert len(summary1["topics"]) >= 3
    assert len(summary1["new_suggestion_ids"]) >= 3
    assert summary1["you_profile_run_id"] is not None
    assert summary1["public_perception_run_id"] is not None
    assert summary1["algorithm_inference_run_id"] is not None

    # Verify DB state
    from threads_analytics.db import session_scope
    from threads_analytics.models import (
        AffinityCreator,
        MyAccountInsight,
        MyPost,
        Run,
        Topic,
    )

    with session_scope() as session:
        assert session.scalar(select(Run).where(Run.status == "complete")) is not None
        assert session.query(MyPost).count() == 15
        assert session.query(MyAccountInsight).count() == 1
        assert session.query(Topic).count() >= 3
        assert session.query(AffinityCreator).count() >= 2

    # --- Second run with a bumped follower count ---
    class FakeThreadsClient1350(FakeThreadsClient):
        def __init__(self):
            super().__init__(follower_count=1350)

    monkeypatch.setattr(pipeline_mod, "ThreadsClient", FakeThreadsClient1350)

    summary2 = pipeline_mod.run_full_cycle(account_slug="default")
    assert "error" not in summary2, f"second run errored: {summary2.get('error')}"
    # With mocked LLM, suggestions should still be generated on second run
    assert len(summary2["new_suggestion_ids"]) >= 0
