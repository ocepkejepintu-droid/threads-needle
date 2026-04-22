from __future__ import annotations

from threads_analytics.threads_client import ThreadsClient


def test_list_post_replies_parses_api_payload(monkeypatch):
    client = ThreadsClient(access_token="test-token", user_id="me")

    def fake_get(self, path, params=None):
        assert path == "/post_123/replies"
        assert params == {
            "fields": "id,text,username,user_id,permalink,timestamp",
            "limit": 25,
        }
        return {
            "data": [
                {
                    "id": "reply_456",
                    "text": "Great post",
                    "username": "commenter",
                    "user_id": "user_789",
                    "permalink": "https://www.threads.net/@commenter/post/reply_456",
                    "timestamp": "2024-08-01T12:34:56+0000",
                }
            ]
        }

    monkeypatch.setattr(ThreadsClient, "_get", fake_get)

    try:
        replies = client.list_post_replies("post_123")
    finally:
        client.close()

    assert len(replies) == 1
    reply = replies[0]
    assert reply.id == "reply_456"
    assert reply.username == "commenter"
    assert reply.text == "Great post"
    assert reply.permalink == "https://www.threads.net/@commenter/post/reply_456"
    assert reply.created_at.isoformat() == "2024-08-01T12:34:56+00:00"


def test_list_post_replies_missing_author_fields(monkeypatch):
    client = ThreadsClient(access_token="test-token", user_id="me")

    def fake_get(self, path, params=None):
        assert path == "/post_123/replies"
        return {
            "data": [
                {
                    "id": "reply_456",
                    "text": "Anonymous-ish reply",
                    "timestamp": "2024-08-01T12:34:56+0000",
                }
            ]
        }

    monkeypatch.setattr(ThreadsClient, "_get", fake_get)

    try:
        replies = client.list_post_replies("post_123")
    finally:
        client.close()

    assert len(replies) == 1
    reply = replies[0]
    assert reply.id == "reply_456"
    assert reply.username is None
    assert reply.user_id is None
    assert reply.permalink is None
    assert reply.text == "Anonymous-ish reply"


def test_list_post_replies_paginates_beyond_first_page(monkeypatch):
    client = ThreadsClient(access_token="test-token", user_id="me")

    page_calls: list[tuple[str, dict | None]] = []

    def fake_get(self, path, params=None):
        page_calls.append((path, params))
        return {
            "data": [
                {"id": f"r{idx}", "text": f"reply {idx}", "timestamp": "2024-08-01T12:00:00+0000"}
                for idx in range(25)
            ],
            "paging": {"next": "https://graph.threads.net/v1.0/next-page-url"},
        }

    monkeypatch.setattr(ThreadsClient, "_get", fake_get)

    second_page_called = [False]

    class _FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def json(self):
            return self._data

    def fake_raw_get(url):
        second_page_called[0] = True
        return _FakeResponse(
            {
                "data": [
                    {
                        "id": f"r{idx + 25}",
                        "text": f"reply {idx + 25}",
                        "timestamp": "2024-08-01T12:00:00+0000",
                    }
                    for idx in range(10)
                ]
            }
        )

    monkeypatch.setattr(client._client, "get", fake_raw_get)

    try:
        replies = client.list_post_replies("post_123", limit=35)
    finally:
        client.close()

    assert len(replies) == 35
    assert replies[0].id == "r0"
    assert replies[34].id == "r34"
    assert second_page_called[0] is True
    assert page_calls[0][1] == {
        "fields": "id,text,username,user_id,permalink,timestamp",
        "limit": 35,
    }


def test_list_post_replies_paginates_all_pages_when_limit_none(monkeypatch):
    """When limit=None, pagination should exhaust all pages without explicit cap."""
    client = ThreadsClient(access_token="test-token", user_id="me")

    call_count = [0]

    def fake_get(self, path, params=None):
        call_count[0] += 1
        if path == "/post_123/replies":
            assert params.get("limit") == 100
            return {
                "data": [
                    {
                        "id": f"p{idx}",
                        "text": f"page1 reply {idx}",
                        "timestamp": "2024-08-01T12:00:00+0000",
                    }
                    for idx in range(100)
                ],
                "paging": {"next": "https://graph.threads.net/v1.0/page2"},
            }
        return {"data": []}

    monkeypatch.setattr(ThreadsClient, "_get", fake_get)

    second_page_called = [False]

    class _FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def json(self):
            return self._data

    def fake_raw_get(url):
        second_page_called[0] = True
        return _FakeResponse(
            {
                "data": [
                    {
                        "id": f"p{idx + 100}",
                        "text": f"page2 reply {idx}",
                        "timestamp": "2024-08-01T12:00:00+0000",
                    }
                    for idx in range(50)
                ]
            }
        )

    monkeypatch.setattr(client._client, "get", fake_raw_get)

    try:
        replies = client.list_post_replies("post_123", limit=None)
    finally:
        client.close()

    assert len(replies) == 150
    assert second_page_called[0] is True
