from scripts.nikkei_score_articles import score_article, split_keywords


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
