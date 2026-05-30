from __future__ import annotations

from collections import Counter
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


def _article_full_text(article: dict[str, Any]) -> str:
    for key in ["full_text", "text", "article_body", "body", "text_excerpt"]:
        value = str(article.get(key) or "").strip()
        if value:
            return value
    return ""


def _article_key(article: dict[str, Any]) -> str:
    return str(article.get("url") or "").strip()


def _article_lookup(articles: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for article in articles:
        key = _article_key(article)
        if key and key not in out:
            out[key] = article
    return out


def _category_for(article: dict[str, Any]) -> str:
    text = " ".join(
        str(article.get(k) or "")
        for k in ["title", "summary", "Summary", "matched_rules", "Business Implications", "business_implications"]
    )
    if any(x in text for x in ["タングステン", "アルミ", "銅", "LNG", "原油", "電気料金", "エネルギー", "資源", "素材", "鉄", "半導体", "メモリー"]):
        return "素材・エネルギー・製造"
    if any(x in text for x in ["規制", "制裁", "政策", "政府", "税", "補助", "法", "EU", "中国", "米国", "安全保障"]):
        return "政策・規制・地政学"
    if any(x in text for x in ["投資", "買収", "M&A", "PPP", "インフラ", "データセンター", "設備", "工場", "不動産"]):
        return "投資・インフラ・不動産"
    if any(x in text for x in ["為替", "金利", "FRB", "株", "市場", "貯蓄率", "消費", "物価"]):
        return "金融・市場・消費"
    if any(x in text for x in ["EV", "自動車", "BYD", "物流", "AI", "クラウド"]):
        return "モビリティ・テック"
    return "その他"


def _category_summary(articles: list[dict]) -> str:
    if not articles:
        return ""
    counts = Counter(_category_for(a) for a in articles)
    preferred_order = [
        "素材・エネルギー・製造",
        "政策・規制・地政学",
        "投資・インフラ・不動産",
        "金融・市場・消費",
        "モビリティ・テック",
        "その他",
    ]
    rows = []
    for key in preferred_order:
        count = counts.get(key, 0)
        if count:
            rows.append(f'<li>{_esc(key)}：{count}件</li>')
    return '<ul class="compact-list">' + "".join(rows) + "</ul>" if rows else ""


def render_final_report_html(
    template_path: Path,
    report: Dict[str, Any],
    target_date: str,
    all_articles: list[dict] | None = None,
) -> str:
    del target_date
    sections = []
    articles = all_articles or []
    article_by_url = _article_lookup(articles)

    for idx, sec in enumerate(report.get("article_sections", []), 1):
        source_article = article_by_url.get(_article_key(sec), {})
        merged_sec = {**source_article, **sec}
        what_happened = _non_empty_text(merged_sec.get("what_happened"))
        why_it_matters = _non_empty_text(merged_sec.get("why_it_matters"))
        points = _watch_points(merged_sec)
        summary_text = _summary_and_implications_text(merged_sec)
        has_structured = bool(what_happened or why_it_matters or points)
        body = ""
        if has_structured:
            if what_happened:
                body += f'<div class="paragraph-block"><div class="paragraph-head">要点</div><div class="paragraph-body">{_esc(what_happened)}</div></div>'
            if why_it_matters:
                body += f'<div class="paragraph-block"><div class="paragraph-head">なぜ読むべきか</div><div class="paragraph-body">{_esc(why_it_matters)}</div></div>'
            if points:
                body += '<div class="paragraph-block"><div class="paragraph-head">見るべき点</div><ul class="dot-list">' + "".join(f"<li>{_esc(x)}</li>" for x in points) + "</ul></div>"
        elif summary_text:
            body += f'<div class="paragraph-block"><div class="paragraph-head">要点</div><div class="paragraph-body">{_esc(summary_text)}</div></div>'

        if not body and summary_text:
            body += f'<div class="paragraph-block"><div class="paragraph-head">要点</div><div class="paragraph-body">{_esc(summary_text)}</div></div>'

        full_text = _article_full_text(merged_sec)
        if full_text:
            body += (
                '<div class="paragraph-block source-text-block">'
                '<div class="paragraph-head">本文</div>'
                f'<div class="source-text">{_esc(full_text)}</div>'
                '</div>'
            )

        sections.append(
            '<article class="article-card">'
            f'<div class="article-title"><span class="article-index">{idx}.</span> {_title_link(merged_sec)}{_notion_link(merged_sec)}</div>'
            f'<div class="article-body">{body}</div>'
            "</article>"
        )

    all_items = []
    for article in articles:
        all_items.append(f'<li class="all-list-item">{_title_link(article)}{_notion_link(article)}</li>')

    tpl = Template(template_path.read_text(encoding="utf-8"))
    watchlist = report.get("watchlist")
    watch_items = [str(x).strip() for x in watchlist if str(x).strip()] if isinstance(watchlist, list) else []
    watchlist_section = ""
    if watch_items:
        watchlist_section = (
            '<section class="section-card watchlist-card">'
            '<h3 class="section-title">■ 継続ウォッチ</h3><ul class="watch-list">'
            + "".join(f'<li class="watch-item">{_esc(x)}</li>' for x in watch_items)
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
        all_article_count=len(articles),
        article_category_summary=_category_summary(articles),
        all_article_items="".join(all_items),
        executive_summary_block=executive_summary_block,
    )
