from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict


def render_final_report_html(template_path: Path, report: Dict[str, Any], target_date: str) -> str:
    article_cards = []
    refs = []
    all_article_items = []

    for sec in report.get("article_sections", []):
        ref = escape(str(sec.get("ref_id", "A?")))
        url = escape(str(sec.get("url", "#")), quote=True)
        title = escape(str(sec.get("title", "")))
        score = escape(str(sec.get("importance_score", "")))
        one_line = escape(str(sec.get("one_line_summary", "")))
        why = escape(str(sec.get("why_it_matters", "")))
        action = escape(str(sec.get("business_action_hint", "")))

        refs.append(f'<li><a href="{url}">{ref}</a> {title}</li>')
        all_article_items.append(f'<li class="all-list-item"><a href="{url}">{ref}</a> {title}</li>')
        article_cards.append(
            f'<div class="article-card">'
            f'<div><strong><a href="{url}">{ref}</a> {title}</strong></div>'
            f'<div>Importance Score: {score}</div>'
            f'<div class="article-row"><div class="article-label">何が起きたか</div><div class="article-text">{one_line}</div></div>'
            f'<div class="article-row"><div class="article-label">なぜ重要か</div><div class="article-text">{why}</div></div>'
            f'<div class="article-row"><div class="article-label">見るべき点</div><div class="article-text">{action}</div></div>'
            f'</div>'
        )

    priority_watch_items = "".join(
        f'<li class="signal-item">{escape(str(x))}</li>' for x in report.get("priority_watch_items", [])
    )
    watch_items = "".join(
        f'<li class="watch-item">{escape(str(x))}</li>' for x in report.get("priority_watch_items", [])
    )

    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        report_title=escape(str(report.get("report_title", ""))),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        target_date=escape(str(target_date)),
        article_count=len(report.get("article_sections", [])),
        today_key_message=escape(str(report.get("today_key_message", ""))),
        executive_summary=escape(str(report.get("executive_summary", ""))),
        cross_article_implications=escape(str(report.get("cross_article_implications", ""))),
        priority_watch_items=priority_watch_items,
        watch_items=watch_items,
        article_items="".join(article_cards),
        all_article_items="".join(all_article_items),
        reference_links="".join(refs),
    )
