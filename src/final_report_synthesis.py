from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def _pick_text(article: Dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        v = article.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if limit <= 0:
        return ""
    return text[:limit]


def build_synthesis_input(articles: List[Dict[str, Any]], text_chars: int = 1800) -> List[Dict[str, Any]]:
    out = []
    for i, a in enumerate(articles, 1):
        full_text = _pick_text(a, ["full_text", "text", "article_body", "body"])
        out.append({"ref_id": f"A{i}", "title": a.get("title"), "url": a.get("url"), "source": a.get("source"), "edition": a.get("edition"), "issue_date": a.get("issue_date"), "importance_score": a.get("importance_score"), "priority": a.get("priority"), "matched_rules": a.get("matched_rules"), "summary": a.get("summary"), "reason_to_read": a.get("reason_to_read"), "business_implications": a.get("business_implications"), "text_excerpt": _clip(full_text, text_chars)})
    return out


def validate_final_report_errors(data: Dict[str, Any], expected_count: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["report_is_not_object"]
    for key in ["report_title", "executive_summary", "today_key_message", "cross_article_implications"]:
        if not data.get(key):
            errors.append(f"missing_{key}")
    if "priority_watch_items" in data and not isinstance(data.get("priority_watch_items"), list):
        errors.append("priority_watch_items_not_list")
    sections = data.get("article_sections")
    if sections is None:
        errors.append("missing_article_sections")
    elif not isinstance(sections, list):
        errors.append("article_sections_not_list")
    elif len(sections) != expected_count:
        errors.append(f"article_sections_count_{len(sections)}_expected_{expected_count}")
    return errors


def validate_final_report(data: Dict[str, Any], expected_count: int) -> bool:
    return not validate_final_report_errors(data, expected_count)


def build_input_hash(target_date: str, articles: List[Dict[str, Any]], final_report: Dict[str, Any]) -> str:
    payload = {"target_date": target_date, "articles": [{"page_id": a.get("page_id"), "url": a.get("url"), "updated_time": a.get("updated_time"), "summary": a.get("summary"), "reason_to_read": a.get("reason_to_read"), "business_implications": a.get("business_implications"), "text_excerpt": a.get("text_excerpt")} for a in articles], "final_report": final_report}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
