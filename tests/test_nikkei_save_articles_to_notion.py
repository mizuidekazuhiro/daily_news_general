import json

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


def test_save_results_created_updated_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "INPUT_JSON", tmp_path / "articles.json")
    monkeypatch.setattr(mod, "FAILED_LOG_JSON", tmp_path / "failed.json")
    monkeypatch.setattr(mod, "SAVE_RESULTS_JSON", tmp_path / "save_results.json")
    mod.INPUT_JSON.write_text(json.dumps([
        {"url": "https://example.com/new", "text": "line", "page_title": "new title"},
        {"url": "https://example.com/existing", "text": "line", "page_title": "old title"},
        {"url": "https://example.com/fail", "text": "line", "page_title": "bad title"},
    ]), encoding="utf-8")

    def fake_req(method, url, **kwargs):
        if method == "GET" and "/databases/" in url:
            return DummyResp({"properties": {"Name": {"type": "title"}, "URL": {"type": "url"}, "Body": {"type": "rich_text"}}})
        if method == "POST" and url.endswith("/query"):
            return DummyResp({"results": [{"id": "page-existing", "properties": {"URL": {"type": "url", "url": "https://example.com/existing"}}}], "has_more": False})
        if method == "POST" and url.endswith("/pages"):
            payload = kwargs.get("json", {})
            page_url = payload.get("properties", {}).get("URL", {}).get("url")
            if page_url == "https://example.com/fail":
                raise RuntimeError("boom")
            return DummyResp({"id": "new-page-id"})
        if method == "PATCH" and "/pages/page-existing" in url:
            return DummyResp({"id": "page-existing", "url": "https://www.notion.so/page-existing-url"})
        if method == "PATCH" and "/blocks/" in url:
            return DummyResp({})
        if method == "GET" and "/blocks/" in url:
            return DummyResp({"results": [], "has_more": False})
        raise AssertionError((method, url))

    monkeypatch.setattr(mod, "req", fake_req)
    mod.main()

    assert mod.SAVE_RESULTS_JSON.exists()
    rows = json.loads(mod.SAVE_RESULTS_JSON.read_text(encoding="utf-8"))
    assert len(rows) == 3

    by_url = {r["url"]: r for r in rows}
    created = by_url["https://example.com/new"]
    assert set(["url", "title", "page_id", "notion_url", "action", "ok", "error"]).issubset(created.keys())
    assert created["action"] == "created" and created["ok"] is True
    assert created["notion_url"] == "https://www.notion.so/newpageid"

    updated = by_url["https://example.com/existing"]
    assert updated["action"] == "updated" and updated["ok"] is True
    assert updated["page_id"] == "page-existing"
    assert updated["notion_url"] == "https://www.notion.so/page-existing-url"

    failed = by_url["https://example.com/fail"]
    assert failed["action"] == "failed" and failed["ok"] is False
    assert failed["error"]


def test_existing_page_no_duplicate_body_heading(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "INPUT_JSON", tmp_path / "articles.json")
    monkeypatch.setattr(mod, "FAILED_LOG_JSON", tmp_path / "failed.json")
    monkeypatch.setattr(mod, "SAVE_RESULTS_JSON", tmp_path / "save_results.json")
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
    monkeypatch.setattr(mod, "append_body_blocks", lambda page_id, text, title='': called.__setitem__("append", called["append"] + 1))
    mod.main()
    assert called["append"] == 0
