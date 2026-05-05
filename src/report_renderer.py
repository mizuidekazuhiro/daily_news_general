from __future__ import annotations
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict

def _opt(label: str, value: Any) -> str:
    txt = str(value or "").strip()
    return f"<div>{label}: {escape(txt)}</div>" if txt else ""

def render_final_report_html(template_path: Path, report: Dict[str, Any], target_date: str) -> str:
    sections = []
    refs = []
    for sec in report.get("article_sections", []):
        title = str(sec.get("title") or "").strip()
        if not title:
            continue
        link = sec.get("notion_url") or sec.get("page_url") or sec.get("url") or "#"
        safe_link = escape(str(link), quote=True)
        safe_title = escape(title)
        summary = str(sec.get("one_line_summary") or sec.get("summary") or "").strip()
        body = str(sec.get("article_text") or sec.get("full_text") or sec.get("text") or sec.get("text_excerpt") or "").strip()
        body_html = f'<div class="article-body">{escape(body)}</div>' if body else '<div class="article-body">本文未取得</div>'
        sections.append(
            f'<li><strong><a href="{safe_link}">{safe_title}</a></strong>'
            f"<div>Importance Score: {escape(str(sec.get('importance_score','')))}</div>"
            f"{_opt('1行要約', summary)}"
            f"{_opt('なぜ読むべきか', sec.get('why_it_matters'))}"
            f"{_opt('業務への示唆', sec.get('business_action_hint'))}"
            f"{body_html}</li>"
        )
        refs.append(f'<li><a href="{safe_link}">{safe_title}</a></li>')

    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        report_title=report.get("report_title", ""),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        target_date=target_date,
        article_count=len(report.get("article_sections", [])),
        today_key_message=report.get("today_key_message", ""),
        executive_summary=report.get("executive_summary", ""),
        cross_article_implications=report.get("cross_article_implications", ""),
        priority_watch_items="".join(f"<li>{escape(str(x))}</li>" for x in report.get("priority_watch_items", [])),
        article_items="".join(sections),
        reference_links="".join(refs),
        top_article_items="",
    )
