from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict


def _title_link(sec: Dict[str, Any]) -> str:
    ref = escape(str(sec.get("ref_id", "A?")))
    title = escape(str(sec.get("title", "")))
    url = escape(str(sec.get("url", "#")), quote=True)
    return f'<a href="{url}">{ref}</a> {title}'


def _notion_link(sec: Dict[str, Any]) -> str:
    notion_url = sec.get("notion_url")
    page_id = sec.get("page_id")
    url = ""
    if isinstance(notion_url, str) and notion_url.strip():
        url = notion_url.strip()
    elif isinstance(page_id, str) and page_id.strip():
        url = f"https://www.notion.so/{page_id.strip().replace('-', '')}"
    if not url:
        return ""
    return f' <a class="notion-link" href="{escape(url, quote=True)}">Notionで開く</a>'


def _summary_and_implications_text(sec: Dict[str, Any]) -> str:
    v = sec.get("summary_and_implications") or sec.get("business_implications") or ""
    return escape(str(v))


def _watch_points(sec: Dict[str, Any]) -> str:
    points = sec.get("watch_points")
    if isinstance(points, list):
        txt = " / ".join(str(x) for x in points if str(x).strip())
        return escape(txt)
    return escape(str(points or ""))


def _brief_items(report: Dict[str, Any]) -> list[str]:
    items = report.get("integrated_insights")
    if isinstance(items, list):
        return [str(x) for x in items if str(x).strip()]
    if isinstance(items, str) and items.strip():
        return [items.strip()]
    return []


def render_final_report_html(template_path: Path, report: Dict[str, Any], target_date: str, all_articles: list[Dict[str, Any]] | None = None) -> str:
    article_cards: list[str] = []
    article_sections = report.get("article_sections", [])
    for sec in article_sections:
        what_happened = escape(str(sec.get("what_happened") or sec.get("summary") or ""))
        why_it_matters = escape(str(sec.get("why_it_matters") or sec.get("reason_to_read") or ""))
        watch_points = _watch_points(sec)
        summary_and_implications = _summary_and_implications_text(sec)
        article_cards.append(
            f'<div class="article-card">'
            f'<div><strong>{_title_link(sec)}</strong>{_notion_link(sec)}</div>'
            f'<div class="article-row"><div class="article-label">何が起きたか</div><div class="article-text">{what_happened}</div></div>'
            f'<div class="article-row"><div class="article-label">なぜ重要か</div><div class="article-text">{why_it_matters}</div></div>'
            f'<div class="article-row"><div class="article-label">見るべき点</div><div class="article-text">{watch_points}</div></div>'
            f'<div class="article-row"><div class="article-label">要約と示唆</div><div class="article-text">{summary_and_implications}</div></div>'
            f'</div>'
        )

    signal_items = "".join(f'<li class="signal-item">{escape(x)}</li>' for x in _brief_items(report))
    watchlist = report.get("watchlist") or []
    watch_items = "".join(f'<li class="watch-item">{escape(str(x))}</li>' for x in watchlist if str(x).strip())
    watchlist_section = ""
    if watch_items:
        watchlist_section = (
            '<section class="section-card watchlist-card">'
            '<h3 class="section-title">要注意・継続ウォッチ</h3>'
            f'<ul class="watch-list">{watch_items}</ul>'
            '</section>'
        )

    all_source = all_articles if all_articles is not None else article_sections
    all_article_items = "".join(
        f'<li class="all-list-item">{_title_link(sec)}</li>' for sec in all_source
    )

    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        report_title=escape(str(report.get("report_title", ""))),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        target_date=escape(str(target_date)),
        article_count=len(article_sections),
        today_key_message=escape(str(report.get("today_key_message", ""))),
        integrated_insights=signal_items,
        article_items="".join(article_cards),
        watchlist_section=watchlist_section,
        all_article_items=all_article_items,
    )
