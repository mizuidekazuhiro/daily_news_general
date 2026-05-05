from src.report_selection import SelectionConfig, select_articles
from src.report_renderer import render_final_report_html
from pathlib import Path

def mk(scores):
    return [{"title":f"t{i}","importance_score":s,"priority":0,"url":f"https://x/{i}","issue_date":"20260505"} for i,s in enumerate(scores,1)]

def test_top5_fixed_and_no_ties():
    sel,_=select_articles(mk([10,9,8,7,6,6,5]), SelectionConfig(mode="top_importance_rank",top_rank=5,include_ties=False))
    assert len(sel)==5

def test_html_hides_empty_labels():
    rep={"report_title":"r","today_key_message":"k","executive_summary":"e","cross_article_implications":"c","priority_watch_items":[],"article_sections":[{"ref_id":"A1","title":"t","url":"https://x","importance_score":1,"one_line_summary":"","why_it_matters":"","business_action_hint":""}]}
    html=render_final_report_html(Path("templates/nikkei_final_report_email.html"), rep, "2026-05-05")
    assert "1行要約:" not in html and "なぜ読むべきか:" not in html and "業務への示唆:" not in html
