from scripts.run_nikkei_final_report import _build_article_sections_from_input
from src.report_renderer import render_final_report_html
from pathlib import Path

def test_build_article_sections_uses_text_for_full_text():
    out=_build_article_sections_from_input([{"title":"t","text":"FULL","text_excerpt":"EX"}])
    assert out[0]["full_text"]=="FULL"

def test_renderer_fallback_body_display(tmp_path: Path):
    tpl=tmp_path/'t.html'
    tpl.write_text('<ul>${article_items}</ul>',encoding='utf-8')
    html=render_final_report_html(tpl,{"article_sections":[{"title":"A","url":"u","importance_score":1}]},'2026-01-01')
    assert '本文未取得' in html
