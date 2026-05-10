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


def _watch_points(article: dict[str, Any]) -> list[str]:
    points = article.get("watch_points")
    if isinstance(points, list):
        return [str(x).strip() for x in points if str(x).strip()]
    return []

def _non_empty_text(value: Any) -> str:
    return str(value or "").strip()


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
    articles = all_articles or []
    for sec in report.get("article_sections", []):
        what_happened = _non_empty_text(sec.get("what_happened"))
        why_it_matters = _non_empty_text(sec.get("why_it_matters"))
        points = _watch_points(sec)
        summary_text = _summary_and_implications_text(sec)
        has_structured = bool(what_happened or why_it_matters or points)
        body = ""
        if has_structured:
            if what_happened:
                body += f'<div class="paragraph-block"><div class="paragraph-head">● 要約</div><div class="paragraph-body">{_esc(what_happened)}</div></div>'
            if why_it_matters:
                body += f'<div class="paragraph-block"><div class="paragraph-head">● なぜ重要か</div><div class="paragraph-body">{_esc(why_it_matters)}</div></div>'
            if points:
                body += '<div class="paragraph-block"><div class="paragraph-head">→ 影響と見るべき点</div><ul class="dot-list">' + "".join(f"<li>・{_esc(x)}</li>" for x in points) + "</ul></div>"
        elif summary_text:
            body += f'<div class="paragraph-block"><div class="paragraph-head">● 要約</div><div class="paragraph-body">{_esc(summary_text)}</div></div>'

        if not body and summary_text:
            body += f'<div class="paragraph-block"><div class="paragraph-head">● 要約</div><div class="paragraph-body">{_esc(summary_text)}</div></div>'

        ref_id = _non_empty_text(sec.get("ref_id"))
        header = f"■ {ref_id}｜" if ref_id else "■ "
        sections.append(
            '<article class="article-card">'
            f'<div class="article-title">{header}{_title_link(sec)}{_notion_link(sec)}</div>'
            f'<div class="article-body">{body}</div>'
            "</article>"
        )

    all_items = []
    for article in articles:
        all_items.append(f"<li class=\"all-list-item\">{_title_link(article)}{_notion_link(article)}</li>")

    tpl = Template(template_path.read_text(encoding="utf-8"))
    watchlist = report.get("watchlist")
    watch_items = [str(x).strip() for x in watchlist if str(x).strip()] if isinstance(watchlist, list) else []
    watchlist_section = ""
    if watch_items:
        watchlist_section = (
            '<section class=\"section-card watchlist-card\">'
            '<h3 class=\"section-title\">■ 継続して見る点</h3><ul class=\"watch-list\">'
            + "".join(f'<li class=\"watch-item\">→ {_esc(x)}</li>' for x in watch_items)
            + '</ul></section>'
        )
    executive_summary = _non_empty_text(report.get("executive_summary", ""))
    executive_summary_block = ""
    if executive_summary:
        executive_summary_block = f'<div class="paragraph-block lead"><div class="paragraph-head">● 背景・文脈</div><div class="paragraph-body">{_esc(executive_summary)}</div></div>'

    return tpl.safe_substitute(
        today_key_message=_esc(report.get("today_key_message", "")),
        brief_items="".join(
            f'<div class="paragraph-block signal-item"><div class="paragraph-head">● 注目ポイント</div><div class="paragraph-body">{_esc(x)}</div></div>'
            for x in _brief_items(report)
        ),
        article_items="".join(sections),
        watchlist_section=watchlist_section,
        all_article_count=len(articles),
        all_article_items="".join(all_items),
        executive_summary_block=executive_summary_block,
    )
