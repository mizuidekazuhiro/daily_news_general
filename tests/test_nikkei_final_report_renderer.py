from pathlib import Path

from src.report_renderer import render_final_report_html


def test_renderer_shows_all_articles_and_hides_ref_markers():
    rep = {
        "report_title": "r",
        "executive_summary": "e",
        "today_key_message": "k",
        "cross_article_implications": "c",
        "priority_watch_items": ["x"],
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
            }
        ],
    }
    all_articles = [
        {"title": "", "url": "", "importance_score": 1, "issue_date": "2026-05-06", "edition": "morning", "source": "Nikkei"},
        {"title": "t2", "url": "https://nikkei.example/2", "page_id": "pp-22", "importance_score": 2, "issue_date": "2026-05-06", "edition": "evening", "source": "Nikkei", "matched_rules": ["Japan", "Steel"]},
    ]
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-06", all_articles=all_articles)
    assert "A1" not in html and "ref_id" not in html
    assert '<a href="https://nikkei.example/a?x=1&amp;y=2">&lt;危険&gt;</a>' in html
    assert "https://www.notion.so/abcdef" in html
    assert "https://www.notion.so/pp22" in html
    assert "取得記事一覧（全件）" in html and "全2件" in html
    assert "(no title)" in html
    assert "一致ルール: Japan, Steel" in html


def test_load_notion_map_and_merge_fields(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    p = tmp_path / "nikkei_save_results.json"
    p.write_text('[{"url":"https://n/1","page_id":"abcd-ef","notion_url":""}]', encoding="utf-8")
    m = mod._load_notion_map(p)
    assert m["https://n/1"]["notion_url"] == "https://www.notion.so/abcdef"
    assert mod._load_notion_map(tmp_path / "missing.json") == {}

    merged = mod._merge_notion_fields(
        [{"url": "https://n/2", "title": "t", "page_id": "qwer-12"}],
        {},
    )
    assert merged[0]["notion_url"] == "https://www.notion.so/qwer12"
