from __future__ import annotations

from scripts import setup_intelligence_database as setup


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(self._payload)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_notion_request_retries_rate_limit(monkeypatch):
    notion = setup.Notion("token", max_retries=3)
    notion.session = FakeSession([
        FakeResponse(429, {"code": "rate_limited"}, {"Retry-After": "0"}),
        FakeResponse(200, {"results": []}),
    ])
    monkeypatch.setattr(setup.time, "sleep", lambda _seconds: None)

    result = notion.request("POST", "https://api.notion.com/test", json={})

    assert result == {"results": []}
    assert notion.session.calls == 2


def test_resolve_database_id_prefers_configured_database(monkeypatch):
    configured = "3c9dec27-c9aa-81d3-8de8-c6d687f3db77"
    monkeypatch.setenv("NOTION_INTELLIGENCE_DB_ID", configured)

    class FakeNotion:
        def __init__(self):
            self.queries = []

        def query_database(self, database_id, payload):
            self.queries.append((database_id, payload))
            return {"results": []}

    notion = FakeNotion()
    resolved = setup.resolve_database_id(notion)

    assert resolved == configured
    assert notion.queries == [(configured, {"page_size": 1})]
