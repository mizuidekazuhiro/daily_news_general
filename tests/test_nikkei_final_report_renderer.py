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
    assert "今日の結論" in html
    assert "今日の重要シグナル" in html
    assert "重要記事" in html
    assert "<li>示唆1</li>" in html and "<li>示唆2</li>" in html
    assert "なぜ重要か" in html
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


def test_merge_notion_fields_overrides_invalid_gpt_notion_and_renderer_uses_merged_url(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    gpt_sections = [{"title": "t", "url": "https://n/1", "notion_url": "https://evil.invalid/page", "summary_and_implications": "body"}]
    merged = mod._merge_notion_fields(gpt_sections, {"https://n/1": {"notion_url": "https://www.notion.so/correct1", "page_id": ""}})
    assert merged[0]["notion_url"] == "https://www.notion.so/correct1"

    rep = {"today_key_message": "k", "executive_summary": "e", "cross_article_implications": "c", "article_sections": merged}
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "https://www.notion.so/correct1" in html
    assert "https://evil.invalid/page" not in html


def test_renderer_shows_notion_link_when_gpt_output_has_no_notion_fields_but_page_id_exists():
    rep = {
        "today_key_message": "k",
        "executive_summary": "e",
        "cross_article_implications": "c",
        "article_sections": [{"title": "t", "url": "https://nikkei.example/x", "page_id": "abcd-ef", "summary_and_implications": "body"}],
    }
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "Notionで開く" in html
    assert "https://www.notion.so/abcdef" in html


def test_renderer_shows_notion_link_from_url_map_even_without_gpt_notion_fields():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    gpt_sections = [{"title": "t", "url": "https://n/2", "summary_and_implications": "body"}]
    merged = mod._merge_notion_fields(gpt_sections, {"https://n/2": {"notion_url": "https://www.notion.so/fromlog", "page_id": ""}})
    rep = {"today_key_message": "k", "executive_summary": "e", "cross_article_implications": "c", "article_sections": merged}
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "Notionで開く" in html
    assert "https://www.notion.so/fromlog" in html


def test_watchlist_section_visibility():
    rep = {"today_key_message": "k", "integrated_insights": ["i"], "watchlist": [], "article_sections": []}
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "要注意・継続ウォッチ" not in html

    rep_with_watch = {"today_key_message": "k", "integrated_insights": ["i"], "watchlist": ["北米EV需要の鈍化"], "article_sections": []}
    html2 = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep_with_watch, "2026-05-06", all_articles=[])
    assert "要注意・継続ウォッチ" in html2
    assert "北米EV需要の鈍化" in html2


def test_article_structured_blocks_and_summary_fallback_mode():
    rep = {
        "today_key_message": "k",
        "integrated_insights": ["i"],
        "article_sections": [
            {
                "title": "structured",
                "url": "https://example.com/1",
                "what_happened": "設備投資を延期した。",
                "why_it_matters": "需要前提に影響する。",
                "watch_points": ["対象市場の販売動向", "部材調達価格"],
            },
            {
                "title": "summary_only",
                "url": "https://example.com/2",
                "summary_and_implications": "本文要約です。\n\n示唆です。",
            },
        ],
    }
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "何が起きたか" in html
    assert "なぜ重要か" in html
    assert "見るべき点" in html
    assert "要約と示唆" in html
    assert "summary_only" in html
    assert "本文要約です。" in html
    # summary_only記事で「見るべき点」に本文を入れない
    assert "見るべき点</div><div>本文要約です。" not in html


def test_empty_structured_fields_do_not_render_empty_labels():
    rep = {
        "today_key_message": "k",
        "integrated_insights": ["i"],
        "article_sections": [
            {
                "title": "empty",
                "url": "https://example.com/e",
                "what_happened": "",
                "why_it_matters": "",
                "watch_points": [],
                "summary_and_implications": "要約のみ",
            }
        ],
    }
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=[])
    assert "何が起きたか</div><div></div>" not in html
    assert "なぜ重要か</div><div></div>" not in html
    assert "要約と示唆" in html
