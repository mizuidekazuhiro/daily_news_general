import json

from scripts.nikkei_score_articles import OUTPUT_JSON, SUMMARY_JSON, score_article, select_report_articles, split_keywords


def test_split_keywords():
    raw = "鉄鋼 OR インド,政策、商社;資源|物流　再エネ"
    got = split_keywords(raw)
    assert got == ["鉄鋼", "インド", "政策", "商社", "資源", "物流", "再エネ"]


def test_score_article_basic():
    article = {
        "source_title": "鉄鋼各社、インドで投資",
        "page_title": "政策支援が追い風",
        "text": "本文です",
        "text_length": 100,
    }
    rules = [
        {
            "tag_name": "鉄鋼",
            "match_field": "title",
            "weight": 3,
            "priority": 2,
            "keywords": ["鉄鋼"],
            "negative_keywords": [],
        },
        {
            "tag_name": "政策",
            "match_field": "both",
            "weight": 2,
            "priority": 5,
            "keywords": ["政策"],
            "negative_keywords": [],
        },
    ]
    out = score_article(article, rules, min_report_score=5)
    assert out["importance_score"] == 5
    assert out["priority"] == 5
    assert set(out["tags"]) == {"鉄鋼", "政策"}
    assert out["reason_to_read"] != ""


def test_title_only_existing_is_scored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/nikkei_articles_full.json").write_text("[]", encoding="utf-8")
    (tmp_path / "logs/nikkei_issue_run_inventory.json").write_text(json.dumps([
        {"status": "existing_in_notion", "url": "https://example.com/a", "title": "既存記事", "notion_existing": {"page_id": "p1", "url": "https://example.com/a", "title": "既存記事", "text": ""}}
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("NIKKEI_ENABLE_SCORING", "true")
    monkeypatch.setenv("NOTION_RULES_DB_ID", "dummy")
    monkeypatch.setenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5")

    import scripts.nikkei_score_articles as mod
    monkeypatch.setattr(mod, "load_rules", lambda *args, **kwargs: [])

    assert mod.main() == 0
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    scored = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert summary["scoring_input_title_only_count"] == 1
    assert summary["scored_article_count"] == 1
    assert scored[0]["page_id"] == "p1"


def _mk(score, idx=0):
    return {"url": f"https://example.com/{score}-{idx}", "importance_score": score, "priority": 0}


def test_select_report_top_rank_basic_5_selected():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 3])]
    selected, _, mode = select_report_articles(articles, "top_importance_rank", 5, 5, True)
    assert mode == "top_importance_rank_with_ties"
    assert len(selected) == 5


def test_select_report_top_rank_with_ties_includes_7():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 4, 4, 2])]
    selected, cutoff, _ = select_report_articles(articles, "top_importance_rank", 5, 5, True)
    assert cutoff == 4
    assert len(selected) == 7


def test_select_report_top_rank_with_ties_includes_6():
    articles = [_mk(s, i) for i, s in enumerate([2, 2, 2, 1, 1, 1])]
    selected, cutoff, _ = select_report_articles(articles, "top_importance_rank", 5, 5, True)
    assert cutoff == 1
    assert len(selected) == 6


def test_select_report_when_only_3_candidates_selects_all():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6])]
    selected, _, _ = select_report_articles(articles, "top_importance_rank", 5, 5, True)
    assert len(selected) == 3


def test_select_report_include_ties_false_selects_exact_top_5():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 4, 4, 2])]
    selected, cutoff, mode = select_report_articles(articles, "top_importance_rank", 5, 5, False)
    assert mode == "top_importance_rank"
    assert cutoff == 4
    assert len(selected) == 5


def test_select_report_threshold_mode_uses_legacy_threshold():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 3])]
    selected, cutoff, mode = select_report_articles(articles, "threshold", 5, 5, True)
    assert mode == "threshold"
    assert cutoff == 5
    assert [a["importance_score"] for a in selected] == [10, 8, 6, 5]
