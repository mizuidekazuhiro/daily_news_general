import json

from scripts.nikkei_score_articles import (
    OUTPUT_JSON,
    SUMMARY_JSON,
    _contains_keyword,
    is_navigation_like_body,
    score_article,
    select_report_articles,
    split_keywords,
)


def test_split_keywords():
    raw = "鉄鋼 OR インド,政策、商社;資源|物流　再エネ"
    got = split_keywords(raw)
    assert got == ["鉄鋼", "インド", "政策", "商社", "資源", "物流", "再エネ"]


def test_split_keywords_does_not_split_or_inside_words():
    raw = "north africa,Hormuz,corporate"
    assert split_keywords(raw) == ["north africa", "Hormuz", "corporate"]


def test_ascii_keyword_uses_boundaries():
    assert _contains_keyword("US policy", "us")
    assert not _contains_keyword("USEN FIELDING", "us")
    assert _contains_keyword("new data center project", "data center")
    assert _contains_keyword("タイで投資", "タイ")
    assert not _contains_keyword("液晶パネルのタイプ", "タイ")
    assert _contains_keyword("インドで建設", "インド")
    assert not _contains_keyword("インドネシアで建設", "インド")


def test_score_article_basic():
    article = {
        "source_title": "鉄鋼各社、インドで投資",
        "page_title": "政策支援が追い風",
        "text": "本文です",
        "text_length": 100,
    }
    rules = [
        {"tag_name": "鉄鋼", "match_field": "title", "weight": 3, "priority": 2, "keywords": ["鉄鋼"], "negative_keywords": []},
        {"tag_name": "政策", "match_field": "both", "weight": 2, "priority": 5, "keywords": ["政策"], "negative_keywords": []},
    ]
    out = score_article(article, rules, min_report_score=5)
    assert out["importance_score"] == 5
    assert out["priority"] == 5
    assert set(out["tags"]) == {"鉄鋼", "政策"}
    assert out["reason_to_read"] != ""


def test_negative_keywords_suppress_rule_instead_of_double_penalty():
    article = {
        "source_title": "会社決算、設備投資も発表",
        "page_title": "",
        "text": "決算と同時にcapexを拡大する。",
    }
    rule = {
        "tag_name": "決算だけで投資文脈なし",
        "rule_type": "importance",
        "match_field": "both",
        "weight": -1,
        "priority": 0,
        "keywords": ["決算"],
        "negative_keywords": ["capex", "設備投資"],
    }
    out = score_article(article, [rule], min_report_score=5)
    assert out["importance_score"] == 0
    assert out["matched_rules"] == []
    assert out["score_breakdown"][0]["type"] == "suppressed_by_negative"


def test_negative_rule_applies_when_exception_absent():
    article = {"source_title": "会社決算を発表", "page_title": "", "text": "売上高と利益を公表した。"}
    rule = {
        "tag_name": "決算だけで投資文脈なし",
        "rule_type": "importance",
        "match_field": "both",
        "weight": -1,
        "priority": 0,
        "keywords": ["決算"],
        "negative_keywords": ["capex", "設備投資"],
    }
    out = score_article(article, [rule], min_report_score=5)
    assert out["importance_score"] == -1
    assert out["matched_rules"] == ["決算だけで投資文脈なし"]


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


def _mk(score, idx=0, excluded=False):
    return {"url": f"https://example.com/{score}-{idx}", "importance_score": score, "priority": 0, "exclude_candidate": excluded}


def test_select_report_top_rank_respects_min_score():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 3])]
    selected, _, mode = select_report_articles(articles, "top_importance_rank", 5, 5, False)
    assert mode == "top_importance_rank"
    assert [a["importance_score"] for a in selected] == [10, 8, 6, 5]


def test_select_report_top_rank_with_ties_only_among_eligible():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 5, 5, 4, 2])]
    selected, cutoff, _ = select_report_articles(articles, "top_importance_rank", 5, 5, True)
    assert cutoff == 5
    assert len(selected) == 6


def test_select_report_excludes_exclude_candidate():
    articles = [_mk(10, 0, excluded=True), _mk(8, 1), _mk(6, 2)]
    selected, _, _ = select_report_articles(articles, "top_importance_rank", 5, 5, False)
    assert [a["importance_score"] for a in selected] == [8, 6]


def test_select_report_threshold_mode():
    articles = [_mk(s, i) for i, s in enumerate([10, 8, 6, 5, 4, 3])]
    selected, cutoff, mode = select_report_articles(articles, "threshold", 5, 5, True)
    assert mode == "threshold"
    assert cutoff == 5
    assert [a["importance_score"] for a in selected] == [10, 8, 6, 5]


def test_navigation_like_existing_body_not_used_for_scoring():
    assert is_navigation_like_body("アクセスランキング\nトピック一覧\n速報\nビューアーで読む")
    rules = [{
        "tag_name": "速報",
        "match_field": "body",
        "weight": 10,
        "priority": 1,
        "keywords": ["速報"],
        "negative_keywords": [],
    }]
    article = {"source_title": "通常タイトル", "page_title": "通常タイトル", "text": "アクセスランキング\nトピック一覧\n速報"}
    out = score_article(article, rules, min_report_score=5)
    assert out["importance_score"] == 0
    assert out["body_used_for_scoring"] is False
