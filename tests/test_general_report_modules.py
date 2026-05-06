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
    assert len(targets) == 1 and skipped == 2
    assert filter_targets(sel, force_reprocess=True)[0][0]["gpt_processed"] is True
    data = {"summary":"あ"*60,"reason_to_read":"い"*50,"business_implications":"う"*90}
    assert validate_article_json(data)
    assert build_notion_payload(data, "gpt-5.1-mini")["GPT Processed"] is True


def test_final_synthesis_and_hash():
    arts = [{"title":"a","url":"u","summary":"s","reason_to_read":"r","business_implications":"b"}]
    inp = build_synthesis_input(arts)
    assert "full_text" not in inp[0]
    rep = {"report_title":"x","executive_summary":"e","today_key_message":"k","cross_article_implications":"c","priority_watch_items":["1","2","3"],"article_sections":[{"ref_id":"A1","url":"u"}]}
    assert validate_final_report(rep, 1)
    assert len(build_input_hash("2026-01-01", arts, rep)) == 64


def test_html_and_notion_helpers(tmp_path: Path):
    tpl = Path("templates/nikkei_final_report_email.html")
    rep = {"report_title":"r","executive_summary":"e","today_key_message":"k","cross_article_implications":"c","priority_watch_items":["x","y","z"],"article_sections":[{"ref_id":"A1","title":"t","url":"https://x","importance_score":1,"one_line_summary":"o","why_it_matters":"w","business_action_hint":"b"}]}
    html = render_final_report_html(tpl, rep, "2026-01-01")
    assert "Meiryo UI" in html and '<a href="https://x">t</a>' in html and 'A1' not in html
    assert "商社目線の読み" not in html and "業務示唆" in html
    assert "https://x</li>" not in html
    assert filter_known_properties({"Title":"a","X":1}, ["Title"]) == {"Title":"a"}
    try:
        validate_required(["Title"])
        assert False
    except ValueError:
        assert True
