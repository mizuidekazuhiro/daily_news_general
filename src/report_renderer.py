from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict


def _opt(label: str, value: Any) -> str:
    txt = str(value or "").strip()
    return f"<div>{label}: {txt}</div>" if txt else ""


def render_final_report_html(template_path: Path, report: Dict[str, Any], target_date: str) -> str:
    sections = []
    refs = []
    for sec in report.get("article_sections", []):
        ref = sec.get("ref_id", "A?")
        url = sec.get("url", "#")
        refs.append(f'<li><a href="{url}">{ref}</a> {sec.get("title", "")}</li>')
        sections.append(
            f"<li><strong><a href=\"{url}\">{ref}</a> {sec.get('title','')}</strong>"
            f"<div>Importance Score: {sec.get('importance_score','')}</div>"
            f"{_opt('1行要約', sec.get('one_line_summary'))}"
            f"{_opt('なぜ読むべきか', sec.get('why_it_matters'))}"
            f"{_opt('業務への示唆', sec.get('business_action_hint'))}</li>"
        )
    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        report_title=report.get("report_title", ""),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        target_date=target_date,
        article_count=len(report.get("article_sections", [])),
        today_key_message=report.get("today_key_message", ""),
        executive_summary=report.get("executive_summary", ""),
        cross_article_implications=report.get("cross_article_implications", ""),
        priority_watch_items="".join(f"<li>{x}</li>" for x in report.get("priority_watch_items", [])),
        article_items="".join(sections),
        reference_links="".join(refs),
    )
