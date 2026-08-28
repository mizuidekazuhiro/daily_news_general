from __future__ import annotations

import json
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict


def _esc(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _safe_title(article: dict[str, Any]) -> str:
    title = str(article.get("title") or article.get("source_title") or article.get("page_title") or "").strip()
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


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _saved_articles_from_logs(logs_dir: Path = Path("logs")) -> tuple[bool, list[dict[str, Any]]]:
    """Return articles confirmed to exist in Notion for this issue/run.

    New/updated articles come from successful save results. Articles that already
    existed in Notion come from the scoring inventory. The boolean indicates
    whether production log sources were available; when False, renderer callers
    may fall back to the explicitly supplied article list (useful in tests).
    """
    save_path = logs_dir / "nikkei_save_results.json"
    scored_path = logs_dir / "nikkei_articles_scored.json"
    logs_available = save_path.exists() or scored_path.exists()
    if not logs_available:
        return False, []

    merged: dict[str, dict[str, Any]] = {}

    for row in _load_json_list(scored_path):
        if str(row.get("source") or "").strip() != "notion_existing":
            continue
        url = str(row.get("url") or "").strip()
        key = url or f"page:{str(row.get('page_id') or '').strip()}"
        if not key:
            continue
        merged[key] = {
            "title": row.get("title") or row.get("source_title") or row.get("page_title") or "",
            "url": url,
            "page_id": row.get("page_id") or "",
            "notion_url": row.get("notion_url") or "",
            "source": "notion_existing",
        }

    for row in _load_json_list(save_path):
        if not bool(row.get("ok")):
            continue
        url = str(row.get("url") or "").strip()
        key = url or f"page:{str(row.get('page_id') or '').strip()}"
        if not key:
            continue
        merged[key] = {
            "title": row.get("title") or "",
            "url": url,
            "page_id": row.get("page_id") or "",
            "notion_url": row.get("notion_url") or "",
            "source": row.get("action") or "saved",
        }

    return True, list(merged.values())


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


def _sentence_title(text: str, index: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return f"変化{index}"
    for sep in ["：", ":", "。", "、"]:
        if sep in cleaned:
            head = cleaned.split(sep, 1)[0].strip()
            if 4 <= len(head) <= 24:
                return head
    return cleaned[:22] + ("..." if len(cleaned) > 22 else "")


def render_final_report_html(
    template_path: Path,
    report: Dict[str, Any],
    target_date: str,
    all_articles: list[dict] | None = None,
) -> str:
    del target_date
    sections = []
    fallback_articles = all_articles or []
    logs_available, saved_articles = _saved_articles_from_logs()
    articles = saved_articles if logs_available else fallback_articles

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
        all_items.append(f'<li class="all-list-item">{_title_link(article)}{_notion_link(article)}</li>')

    all_articles_section = ""
    if articles:
        all_articles_section = (
            '<section class="section-card saved-articles-card">'
            f'<h3 class="section-title">■ 保存記事一覧（{len(articles)}件）</h3>'
            '<div class="saved-articles-note">重要記事以外も含め、Notionに保存済みの記事を全件掲載しています。</div>'
            '<ol class="all-list">'
            + "".join(all_items)
            + '</ol></section>'
        )

    tpl = Template(template_path.read_text(encoding="utf-8"))
    watchlist = report.get("watchlist")
    watch_items = [str(x).strip() for x in watchlist if str(x).strip()] if isinstance(watchlist, list) else []
    watchlist_section = ""
    if watch_items:
        watchlist_section = (
            '<section class="section-card watchlist-card">'
            '<h3 class="section-title">■ 継続して見る点</h3><ul class="watch-list">'
            + "".join(f'<li class="watch-item">→ {_esc(x)}</li>' for x in watch_items)
            + '</ul></section>'
        )
    executive_summary = _non_empty_text(report.get("executive_summary", ""))
    executive_summary_block = ""
    if executive_summary:
        executive_summary_block = f'<div class="paragraph-block lead"><div class="paragraph-head">背景・文脈</div><div class="paragraph-body">{_esc(executive_summary)}</div></div>'

    brief_html = "".join(
        f'<div class="signal-item"><div class="signal-title">{i}. {_esc(_sentence_title(x, i))}</div><div class="signal-body">{_esc(x)}</div></div>'
        for i, x in enumerate(_brief_items(report), 1)
    )

    return tpl.safe_substitute(
        today_key_message=_esc(report.get("today_key_message", "")),
        brief_items=brief_html,
        article_items="".join(sections),
        watchlist_section=watchlist_section,
        all_articles_section=all_articles_section,
        all_article_count=len(articles),
        all_article_items="".join(all_items),
        executive_summary_block=executive_summary_block,
    )
