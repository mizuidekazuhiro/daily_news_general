import json
from pathlib import Path


def test_update_uses_page_id_first(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/nikkei_articles_scored.json").write_text(json.dumps([
        {"url": "https://example.com/a", "page_id": "page-1", "importance_score": 5, "priority": 1, "tags": [], "matched_rules": []}
    ]), encoding="utf-8")
    monkeypatch.setenv("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "true")
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NIKKEI_ARTICLES_DB_ID", "db")

    import scripts.nikkei_update_notion_scores as mod

    calls = []

    class Resp:
        def __init__(self, data):
            self._data = data
        def json(self):
            return self._data

    def fake_req(token, method, url, **kwargs):
        calls.append((method, url))
        if method == "GET":
            return Resp({"properties": {"URL": {"type": "url"}, "Importance Score": {"type": "number"}, "Priority": {"type": "number"}}})
        if method == "POST":
            return Resp({"results": []})
        return Resp({})

    monkeypatch.setattr(mod, "notion_req", fake_req)
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "score_update_existing_page_id_count: 1" in out
    assert "score_update_url_lookup_count: 0" in out
    assert any(m == "PATCH" and u.endswith("/pages/page-1") for m, u in calls)
