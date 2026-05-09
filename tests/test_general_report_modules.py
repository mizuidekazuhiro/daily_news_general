from pathlib import Path

from src.article_enrichment import build_notion_payload, filter_targets, validate_article_json
from src.final_report_synthesis import build_input_hash, build_synthesis_input, validate_final_report
from src.notion_news_repository import filter_known_properties, validate_required
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles


def mk(scores):
    return [{"title": f"t{i}", "importance_score": s, "priority": 0} for i, s in enumerate(scores, 1)]


def test_selection_cases():
    assert len(select_articles(mk([10,8,6,5,4,3]), SelectionConfig())[0]) == 5
    assert len(select_articles(mk([10,8,6,5,4,4,4,2]), SelectionConfig())[0]) == 7
    assert len(select_articles(mk([2,2,2,1,1,1]), SelectionConfig())[0]) == 6
    assert len(select_articles(mk([3,2,1]), SelectionConfig())[0]) == 3
    assert len(select_articles(mk([10,8,6,5,4,4,4,2]), SelectionConfig(include_ties=False))[0]) == 5


def test_enrichment_filters_and_json():
    sel = [{"full_text": "x"*200, "gpt_processed": True}, {"full_text": "x"*200}, {"full_text": "x"*10}]
    targets, skipped = filter_targets(sel, force_reprocess=False)
    assert len(targets) == 0 and skipped == 3
    assert filter_targets(sel, force_reprocess=True)[0] == []
    data = {"summary":"あ"*60,"reason_to_read":"い"*50,"business_implications":"う"*90}
    assert validate_article_json(data)
    assert build_notion_payload(data, "gpt-5.1-mini")["GPT Processed"] is True


def test_final_synthesis_and_hash():
    arts = [{"title":"a","url":"u","summary":"s","reason_to_read":"r","business_implications":"b"}]
    inp = build_synthesis_input(arts)
    assert "full_text" not in inp[0]
    rep = {"report_title":"x","executive_summary":"e","today_key_message":"k","cross_article_implications":"c","integrated_insights":["i1"],"watchlist":[],"article_sections":[{"ref_id":"A1","url":"u"}]}
    assert validate_final_report(rep, 1)
    assert len(build_input_hash("2026-01-01", arts, rep)) == 64


def test_html_and_notion_helpers(tmp_path: Path):
    tpl = Path("templates/nikkei_final_report_email.html")
    rep = {
        "report_title": "r",
        "executive_summary": "本日の全体像です",
        "today_key_message": "k",
        "cross_article_implications": "c",
        "integrated_insights": ["長いシグナル本文"],
        "watchlist": ["継続監視1"],
        "article_sections": [
            {
                "ref_id": "A1",
                "title": "t",
                "url": "https://x",
                "importance_score": 1,
                "what_happened": "出来事",
                "why_it_matters": "重要性",
                "watch_points": ["監視点"],
                "summary_and_implications": "o\n\nw",
                "notion_url": "https://notion.so/page",
            },
            {
                "ref_id": "A2",
                "title": "t2",
                "url": "https://y",
                "importance_score": 1,
                "summary_and_implications": "fallback",
                "page_id": "abc-def",
            },
        ],
    }
    all_articles = [{"title": "all1", "url": "https://all1"}]
    html = render_final_report_html(tpl, rep, "2026-01-01", all_articles=all_articles)

    assert "■ 本日の読み筋" in html
    assert "● 背景・文脈" in html
    assert "■ 注目すべき変化" in html
    assert 'signal-item' in html and '● 注目ポイント' in html
    assert 'class="watch-item"' in html
    assert html.count('class="article-card"') == 2
    assert 'class="paragraph-block"' in html and 'class="paragraph-body"' in html
    assert 'class="notion-link"' in html
    assert 'class="all-list-item"' in html
    assert '<a href="https://x">t</a>' in html and '■ A1｜' in html

    rep["watchlist"] = []
    html_no_watch = render_final_report_html(tpl, rep, "2026-01-01", all_articles=all_articles)
    assert "■ 継続して見る点" not in html_no_watch

    assert filter_known_properties({"Title":"a","X":1}, ["Title"]) == {"Title":"a"}
    try:
        validate_required(["Title"])
        assert False
    except ValueError:
        assert True


def test_final_report_prompt_includes_quality_requirements():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class DummyClient:
        def __init__(self):
            self.captured_input = None
            self.client = self
            self.responses = self

        def create(self, **kwargs):
            self.captured_input = kwargs.get("input")
            class Resp:
                output_text = "{}"
                status = "completed"
            return Resp()

    cli = DummyClient()
    payload = {"articles": [{"ref_id": "A1"}]}
    mod._generate_report(cli, payload, retry=False)
    system_prompt = cli.captured_input[0]["content"]

    assert "today_key_messageは自然な2〜3文" in system_prompt
    assert "毎朝3分で読む新聞ブリーフ" in system_prompt
    assert "integrated_insightsは『注目すべき変化』として表示されるlist[str]で3〜5個" in system_prompt
    assert "各項目が2文以内" in system_prompt
    assert "禁止表現" in system_prompt
    assert "確認対象" in system_prompt
    assert "備えよ" in system_prompt
    assert "商社目線" in system_prompt
    assert "本日の結論" in system_prompt


def test_fallback_summary_and_implications_not_thin():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sections = mod._build_article_sections_from_input([
        {
            "title": "t",
            "url": "https://example.com",
            "summary": "",
            "text_excerpt": "本文抜粋です。",
            "reason_to_read": "",
            "business_implications": "",
        }
    ])
    text = sections[0]["summary_and_implications"]
    assert "確認対象です。" not in text
    assert "追加確認" not in text
    assert "確認します" not in text
    assert "確認する必要があります" not in text
    assert "点検します" not in text
    assert "\n\n" in text
    assert len(text) >= 60
