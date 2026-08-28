import json
from pathlib import Path

from src.report_renderer import render_final_report_html

TEMPLATE = Path(__file__).resolve().parents[1] / "templates/nikkei_final_report_email.html"


def test_renderer_newspaper_brief_layout_and_labels():
    rep = {
        "today_key_message": "需要の流れが変化している。",
        "executive_summary": "政策と投資判断が背景にある。",
        "integrated_insights": ["価格改定が広がる", "在庫調整が進む"],
        "watchlist": ["為替感応度"],
        "article_sections": [
            {
                "ref_id": "A1",
                "title": "<危険>",
                "url": "https://nikkei.example/a?x=1&y=2",
                "page_id": "abcd-ef",
                "what_happened": "設備投資を延期した。",
                "why_it_matters": "需要前提に影響する。",
                "watch_points": ["対象市場の販売動向"],
            },
            {
                "ref_id": "A2",
                "title": "本文なし",
                "url": "https://nikkei.example/no-body",
                "what_happened": "要約のみ。",
                "why_it_matters": "重要性のみ。",
            },
        ],
    }
    all_articles = [
        {"title": "<危険>", "url": "https://nikkei.example/a?x=1&y=2", "full_text": "原文本文です。" * 20},
        {"title": "本文なし", "url": "https://nikkei.example/no-body"},
    ]
    html = render_final_report_html(TEMPLATE, rep, "2026-05-06", all_articles=all_articles)

    assert "■ 今日の結論" in html
    assert "背景・文脈" in html
    assert "■ 注目すべき変化" in html
    assert "■ 重要記事" in html
    assert "■ 継続して見る点" in html
    assert "■ 保存記事一覧（2件）" in html
    assert "● 要約" in html
    assert "● なぜ重要か" in html
    assert "→ 影響と見るべき点" in html
    assert "※ 原文本文" not in html
    assert "article-source-text" not in html
    assert "商社目線" not in html
    assert '<a href="https://nikkei.example/a?x=1&amp;y=2">&lt;危険&gt;</a>' in html
    assert "https://www.notion.so/abcdef" in html


def test_saved_article_list_is_compact_title_and_links_only():
    rep = {"today_key_message": "k", "integrated_insights": ["i"], "article_sections": []}
    html = render_final_report_html(
        TEMPLATE,
        rep,
        "2026-05-06",
        all_articles=[{"title": "all1", "url": "https://all1", "summary": "一覧には出さない要約"}],
    )
    assert "■ 保存記事一覧（1件）" in html
    assert '<a href="https://all1">all1</a>' in html
    assert "一覧には出さない要約" not in html


def test_saved_article_list_uses_successful_saves_and_existing_notion_articles(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "nikkei_save_results.json").write_text(
        json.dumps([
            {"title": "新規保存", "url": "https://new", "page_id": "new-id", "notion_url": "https://notion/new", "ok": True, "action": "created"},
            {"title": "保存失敗", "url": "https://failed", "page_id": "", "notion_url": "", "ok": False, "action": "failed"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (logs / "nikkei_articles_scored.json").write_text(
        json.dumps([
            {"source_title": "既存保存", "url": "https://existing", "page_id": "existing-id", "source": "notion_existing"},
            {"source_title": "未保存取得", "url": "https://unsaved", "source": "fetched"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    rep = {"today_key_message": "k", "integrated_insights": ["i"], "article_sections": []}
    html = render_final_report_html(
        TEMPLATE,
        rep,
        "2026-05-06",
        all_articles=[{"title": "fallback", "url": "https://fallback"}],
    )
    assert "■ 保存記事一覧（2件）" in html
    assert "新規保存" in html
    assert "既存保存" in html
    assert "保存失敗" not in html
    assert "未保存取得" not in html
    assert "fallback" not in html
    assert "https://notion/new" in html
    assert "https://www.notion.so/existingid" in html


def test_renderer_has_no_double_markers():
    rep = {
        "today_key_message": "k",
        "integrated_insights": ["i1"],
        "watchlist": ["w1"],
        "article_sections": [
            {"title": "t", "url": "https://x", "what_happened": "h", "why_it_matters": "m", "watch_points": ["p1"]}
        ],
    }
    html = render_final_report_html(TEMPLATE, rep, "2026-05-06", all_articles=[])
    assert "• ●" not in html
    assert "• ・" not in html
    assert "• →" not in html
    assert "list-style: none" in html
