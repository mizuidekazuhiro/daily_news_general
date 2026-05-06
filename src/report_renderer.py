from __future__ import annotations

from datetime import datetime
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
    return (
        '<span style="color:#999;">｜</span>'
        f'<a href="{_esc(notion_url)}" style="font-size:12px;color:#666;">Notionで開く</a>'
    )


def _opt(label: str, value: Any) -> str:
    txt = str(value or "").strip()
    return f"<div>{_esc(label)}: {_esc(txt)}</div>" if txt else ""


def _article_meta(article: dict[str, Any]) -> str:
    score = _esc(article.get("importance_score", ""))
    issue_date = _esc(article.get("issue_date", ""))
    edition = _esc(article.get("edition", ""))
    source = _esc(article.get("source", ""))
    return f"重要度: {score} / {issue_date} / {edition} / {source}"


def _matched_rules(article: dict[str, Any]) -> str:
    rules = article.get("matched_rules")
    if not rules:
        return ""
    if isinstance(rules, list):
        text = ", ".join(str(x) for x in rules if str(x).strip())
    else:
        text = str(rules)
    text = text.strip()
    return f"<div>一致ルール: {_esc(text)}</div>" if text else ""


def render_final_report_html(
    template_path: Path,
    report: Dict[str, Any],
    target_date: str,
    all_articles: list[dict] | None = None,
) -> str:
    sections = []
    for sec in report.get("article_sections", []):
        sections.append(
            "<li>"
            f"<strong>{_title_link(sec)}</strong>{_notion_link(sec)}"
            f"<div>Importance Score: {_esc(sec.get('importance_score', ''))}</div>"
            f"{_opt('1行要約', sec.get('one_line_summary'))}"
            f"{_opt('なぜ読むべきか', sec.get('why_it_matters'))}"
            f"{_opt('業務への示唆', sec.get('business_action_hint'))}"
            "</li>"
        )

    all_items = []
    articles = all_articles or []
    for article in articles:
        all_items.append(
            "<li>"
            f"{_title_link(article)}{_notion_link(article)}"
            f"<div style=\"color:#666;font-size:12px;\">{_article_meta(article)}</div>"
            f"{_matched_rules(article)}"
            "</li>"
        )

    tpl = Template(template_path.read_text(encoding="utf-8"))
    return tpl.safe_substitute(
        report_title=_esc(report.get("report_title", "")),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        target_date=_esc(target_date),
        article_count=len(report.get("article_sections", [])),
        today_key_message=_esc(report.get("today_key_message", "")),
        executive_summary=_esc(report.get("executive_summary", "")),
        cross_article_implications=_esc(report.get("cross_article_implications", "")),
        priority_watch_items="".join(f"<li>{_esc(x)}</li>" for x in report.get("priority_watch_items", [])),
        article_items="".join(sections),
        all_article_count=len(articles),
        all_article_items="".join(all_items),
    )
