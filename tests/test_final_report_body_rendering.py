from pathlib import Path
from src.final_report_synthesis import _clip
from src.report_renderer import render_final_report_html


def test_clip_zero_and_negative_return_full():
    t = "abcdef"
    assert _clip(t, 0) == t
    assert _clip(t, -1) == t
    assert _clip(t, 3) == "abc"


def test_renderer_shows_body_and_escape(tmp_path: Path):
    tpl = tmp_path / "tpl.html"
    tpl.write_text("<ul>${article_items}</ul><ul>${reference_links}</ul>", encoding="utf-8")
    report = {
        "article_sections": [
            {
                "title": "<A>",
                "url": "https://example.com?a=1&b=2",
                "importance_score": 7,
                "one_line_summary": "sum<1>",
                "why_it_matters": "why",
                "business_action_hint": "act",
                "full_text": "body <x>",
            }
        ]
    }
    html = render_final_report_html(tpl, report, "2026-01-01")
    assert "本文未取得" not in html
    assert "body &lt;x&gt;" in html
    assert "&lt;A&gt;" in html
