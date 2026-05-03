import json
from pathlib import Path

import scripts.nikkei_save_articles_to_notion as mod


class DummyResp:
    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def test_new_page_uses_summary_field_and_appends_body(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "INPUT_JSON", tmp_path / "articles.json")
    monkeypatch.setattr(mod, "FAILED_LOG_JSON", tmp_path / "failed.json")
    mod.INPUT_JSON.write_text(json.dumps([
        {"url": "https://example.com?a=1", "text": "line1\nline2", "summary": "short", "issue_date": "2026-05-01", "edition": "morning", "page_title": "t"}
    ]), encoding="utf-8")

    calls = []

    def fake_req(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        if method == "GET" and "/databases/" in url:
            return DummyResp({"properties": {
                "Name": {"type": "title"}, "URL": {"type": "url"}, "Summary": {"type": "rich_text"}, "Body": {"type": "rich_text"},
                "Issue Date": {"type": "date"}, "Edition": {"type": "select"}
            }})
        if method == "POST" and url.endswith("/query"):
            return DummyResp({"results": [], "has_more": False})
        if method == "POST" and url.endswith("/pages"):
            return DummyResp({"id": "new-page-id"})
        if method == "PATCH" and "/blocks/new-page-id/children" in url:
            return DummyResp({})
        raise AssertionError((method, url))

    monkeypatch.setattr(mod, "req", fake_req)
    mod.main()

    create_payload = [c for c in calls if c[0] == "POST" and c[1].endswith("/pages")][0][2]
    props = create_payload["properties"]
    assert props["Summary"]["rich_text"][0]["text"]["content"] == "short"
    assert props["Body"]["rich_text"][0]["text"]["content"] == "line1\nline2"
    assert props["Issue Date"]["date"]["start"] == "2026-05-01"
    assert props["Edition"]["select"]["name"] == "morning"
    assert any(c[0] == "PATCH" and "/blocks/new-page-id/children" in c[1] for c in calls)


def test_existing_page_no_duplicate_body_heading(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "INPUT_JSON", tmp_path / "articles.json")
    monkeypatch.setattr(mod, "FAILED_LOG_JSON", tmp_path / "failed.json")
    mod.INPUT_JSON.write_text(json.dumps([
        {"url": "https://example.com?p=1", "text": "body", "page_title": "t"}
    ]), encoding="utf-8")

    def fake_req(method, url, **kwargs):
        if method == "GET" and "/databases/" in url:
            return DummyResp({"properties": {"Name": {"type": "title"}, "URL": {"type": "url"}}})
        if method == "POST" and url.endswith("/query"):
            return DummyResp({"results": [{"id": "page1", "properties": {"URL": {"type": "url", "url": "https://example.com?p=1"}}}], "has_more": False})
        if method == "GET" and "/blocks/page1/children" in url:
            return DummyResp({"results": [{"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "記事本文"}]}}], "has_more": False})
        if method == "PATCH" and "/pages/page1" in url:
            return DummyResp({})
        raise AssertionError((method, url))

    monkeypatch.setattr(mod, "req", fake_req)
    called = {"append": 0}
    monkeypatch.setattr(mod, "append_body_blocks", lambda page_id, text: called.__setitem__("append", called["append"] + 1))
    mod.main()
    assert called["append"] == 0


def test_summary_not_filled_from_body_when_no_summary_field(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "INPUT_JSON", tmp_path / "articles.json")
    monkeypatch.setattr(mod, "FAILED_LOG_JSON", tmp_path / "failed.json")
    long_text = "x" * 2000
    mod.INPUT_JSON.write_text(json.dumps([{"url": "https://example.com/u", "text": long_text, "page_title": "t"}]), encoding="utf-8")

    payloads = []

    def fake_req(method, url, **kwargs):
        if method == "GET" and "/databases/" in url:
            return DummyResp({"properties": {"Name": {"type": "title"}, "URL": {"type": "url"}, "Summary": {"type": "rich_text"}, "Body": {"type": "rich_text"}}})
        if method == "POST" and url.endswith("/query"):
            return DummyResp({"results": [], "has_more": False})
        if method == "POST" and url.endswith("/pages"):
            payloads.append(kwargs["json"])
            return DummyResp({"id": "p"})
        if method == "PATCH" and "/blocks/p/children" in url:
            return DummyResp({})
        raise AssertionError((method, url))

    monkeypatch.setattr(mod, "req", fake_req)
    mod.main()
    props = payloads[0]["properties"]
    assert "Summary" not in props
    assert props["Body"]["rich_text"][0]["text"]["content"] == long_text[:1900]
