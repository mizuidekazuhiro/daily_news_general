"""Nikkei headline normalization and conservative article-type exclusions.

Business keywords and weights remain exclusively in the existing Rules DB.
"""
from __future__ import annotations

import re
from typing import Any

_PUBLISHER_SUFFIX = re.compile(r"\s*[-－–—|｜]\s*日本経済新聞(?:\s*電子版)?\s*$")
_GENERIC_TITLES = {"日本経済新聞", "日本経済新聞 電子版", "朝刊・夕刊", "朝刊・夕刊 - 日本経済新聞"}
_TYPE_LABELS = {
    "スポーツ": ("スポーツ", "Jリーグ", "ボクシング"),
    "競馬": ("競馬",), "文化": ("文化", "文化往来"), "連載小説": ("連載小説",),
    "訃報": ("訃報", "おくやみ"), "人事のみ": ("人事",),
    "将棋・囲碁": ("将棋", "囲碁"), "芸能": ("芸能", "俳優"),
    "連載コラム": ("コラム", "連載コラム"),
}


def clean_headline(value: Any) -> str:
    """Strip only a publisher suffix, never genuine mentions of Japan."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = _PUBLISHER_SUFFIX.sub("", text.splitlines()[0]).strip()
    text = re.sub(r"\s+", " ", text)
    return "" if text in _GENERIC_TITLES else text


def headline_for_article(article: dict[str, Any]) -> tuple[str, str]:
    # Page headline metadata is more authoritative than issue-link anchor text,
    # which can contain the whole card (headline plus introductory paragraphs).
    for field in ("headline", "h1_text", "page_title", "title", "source_title"):
        text = clean_headline(article.get(field))
        if not text:
            continue
        if field in {"title", "source_title"}:
            # For legacy records without separate page metadata, remove a body
            # prefix only when it exactly appears in the supplied article body.
            for line in str(article.get("text") or "").splitlines():
                line = re.sub(r"\s+", " ", line).strip()
                if len(line) < 20:
                    continue
                at = text.find(line[:40])
                if at > 0:
                    text = text[:at].rstrip()
                    break
        if text:
            return text, field
    return "", "missing"


def article_type_exclusion(headline: str, source_title: str = "") -> tuple[bool, str]:
    """Only an explicit leading genre label is evidence for exclusion.

    '企業文化', '人事改革', 'コラムニスト', or body mentions of personnel
    do not establish the article's genre. Uncertain cases keep normal scoring.
    """
    text = clean_headline(headline)
    source = clean_headline(source_title)
    # Preserve an explicit genre prefix on the issue card, but only when the
    # canonical headline follows it exactly. Never use its body suffix.
    prefix = source.split(text, 1)[0].strip() if text and text in source else ""
    labels_to_check = [text, prefix]
    reasons = []
    for reason, labels in _TYPE_LABELS.items():
        for label in labels:
            pattern = r"^(?:【|［|\[|（|\()?" + re.escape(label) + r"(?=$|[\s:：、）)\]］】])"
            if any(re.search(pattern, candidate) for candidate in labels_to_check):
                reasons.append(reason)
                break
    if reasons == ["人事のみ"]:
        return True, "人事だけの記事"
    return bool(reasons), "、".join(sorted(set(reasons)))
