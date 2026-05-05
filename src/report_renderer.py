from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict


def _opt(label: str, value: Any) -> str:
    txt = str(value or "").strip()
    return f"<div>{label}: {txt}</div>" if txt else ""


def render_final_report_html(template_path: Path, report: Dict[str, Any], target_date: str) -> str:
    refs = []
    for sec in report.get("article_sections", []):
        title = str(sec.get("title") or "").strip()
        link = sec.get("notion_url") or sec.get("page_url") or sec.get("url") or "#"
        if not title:
            continue
        refs.append(
            f'<li><a href="{escape(str(link), quote=True)}">{escape(title)}</a></li>'
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
        priority_watch_items="",
        article_items="",
        reference_links="".join(refs),
    )
