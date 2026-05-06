from __future__ import annotations

from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict


def _esc(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _safe_title(article: dict[str, Any]) -> str:
    title = str(article.get("title") or "").strip()
    return title if title else "(no title)"


def _title_link(article: dict[str, Any]) -> str:
    title = _esc(_safe_title(article))
    url = str(article.get("url") or "").strip()
    if not url:
        return title
    return f'<a href="{_esc(url)}">{title}</a>'


def _notion_link(article: dict[str, Any]) -> str:
    notion_url = str(article.get("notion_url") or "").strip()
    if not notion_url:
        page_id = str(article.get("page_id") or "").strip()
        if page_id:
            notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    if not notion_url:
        return ""
    return f'<span style="color:#999;">｜</span><a href="{_esc(notion_url)}" class="notion-link">Notionで開く</a>'


def _summary_and_implications_text(article: dict[str, Any]) -> str:
    text = str(article.get("summary_and_implications") or "").strip()
    if text:
        return text
    fallback = [
        str(article.get("one_line_summary") or "").strip(),
        str(article.get("why_it_matters") or "").strip(),
        str(article.get("business_action_hint") or "").strip(),
    ]
    return "\n\n".join(x for x in fallback if x)


def _brief_items(report: Dict[str, Any]) -> list[str]:
    integrated = report.get("integrated_insights")
    if isinstance(integrated, list) and integrated:
        return [str(x).strip() for x in integrated if str(x).strip()]
    if isinstance(integrated, str) and integrated.strip():
        return [integrated.strip()]
    fallback = [str(report.get("executive_summary") or "").strip(), str(report.get("cross_article_implications") or "").strip()]
    return [x for x in fallback if x]


def render_final_report_html(
    template_path: Path,
    report: Dict[str, Any],
    target_date: str,
    all_articles: list[dict] | None = None,
) -> str:
    del target_date
    sections = []
    for sec in report.get("article_sections", []):
        sections.append(
            '<article class="article-card">'
            f'<div class="article-title">{_title_link(sec)}{_notion_link(sec)}</div>'
            '<div class="article-body">'
            '<div class="article-label">要約と示唆</div>'
            f'{_esc(_summary_and_implications_text(sec))}'
            "</div>"
            "</article>"
        )

    all_items = []
    articles = all_articles or []
    for article in articles:
        all_items.append(f"<li>{_title_link(article)}{_notion_link(article)}</li>")

    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        today_key_message=_esc(report.get("today_key_message", "")),
        brief_items="".join(f"<li>{_esc(x)}</li>" for x in _brief_items(report)),
        article_items="".join(sections),
        all_article_count=len(articles),
        all_article_items="".join(all_items),
    )
