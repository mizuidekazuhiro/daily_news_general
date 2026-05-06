from pathlib import Path

from src.report_renderer import render_final_report_html


def test_renderer_new_layout_and_hides_legacy_blocks():
    rep = {
        "report_title": "朝刊ダイジェスト（2026-05-06・morning）",
        "executive_summary": "fallback summary",
        "today_key_message": "k",
        "cross_article_implications": "fallback implication",
        "priority_watch_items": ["x"],
        "integrated_insights": ["示唆1", "示唆2"],
        "article_sections": [
            {
                "ref_id": "A1",
                "title": "<危険>",
                "url": "https://nikkei.example/a?x=1&y=2",
                "page_id": "abcd-ef",
                "importance_score": 8,
                "one_line_summary": "o",
                "why_it_matters": "w",
                "business_action_hint": "b",
                "summary_and_implications": "1行目\n2行目",
            }
        ],
    }
    all_articles = [
        {"title": "t1", "url": "https://nikkei.example/1", "issue_date": "2026-05-06", "edition": "morning", "source": "Nikkei", "matched_rules": ["Japan"]},
        {"title": "t2", "url": "https://nikkei.example/2", "page_id": "pp-22", "importance_score": 2},
    ]
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=all_articles)
    for ng in ["生成日時", "対象日", "対象記事数", "朝刊ダイジェスト", "夕刊ダイジェスト", "優先確認事項", "Importance Score", "1行要約", "なぜ読むべきか", "業務への示唆", "A1", "ref_id", "一致ルール", "issue_date", "edition", "source", "重要度"]:
        assert ng not in html
    assert "本日のブリーフ" in html
    assert "<li>示唆1</li>" in html and "<li>示唆2</li>" in html
    assert "要約と示唆" in html and "1行目\n2行目" in html
    assert "white-space:pre-line" in html
    assert '<a href="https://nikkei.example/a?x=1&amp;y=2">&lt;危険&gt;</a>' in html
    assert "https://www.notion.so/abcdef" in html
    assert "https://www.notion.so/pp22" in html
    assert "Notionで開く" in html


def test_renderer_notion_link_shown_only_when_available_and_fallback_summary_used():
    rep = {
        "today_key_message": "k",
        "executive_summary": "e",
        "cross_article_implications": "c",
        "article_sections": [
            {
                "title": "t",
                "url": "https://nikkei.example/x",
                "one_line_summary": "s",
                "why_it_matters": "w",
                "business_action_hint": "b",
            }
        ],
    }
    all_articles = [{"title": "n", "url": "https://n.example"}, {"title": "no notion", "url": ""}]
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=all_articles)
    assert "s" in html and "w" in html and "b" in html
    assert html.count("Notionで開く") == 0


def test_mail_subject_format_morning_evening():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    m = mod._build_mail_subject("2026-05-06", "morning")
    e = mod._build_mail_subject("2026-05-06", "evening")
    assert m == "日経新聞朝刊要約｜2026-05-06"
    assert e == "日経新聞夕刊要約｜2026-05-06"
    for ng in ["重要5件", "ブリーフ", "[fallback]"]:
        assert ng not in m and ng not in e
