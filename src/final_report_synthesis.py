from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def build_synthesis_input(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "title": a.get("title"),
            "url": a.get("url"),
            "source": a.get("source"),
            "importance_score": a.get("importance_score"),
            "priority": a.get("priority"),
            "matched_rules": a.get("matched_rules"),
            "summary": a.get("summary"),
            "reason_to_read": a.get("reason_to_read"),
            "business_implications": a.get("business_implications"),
        }
        for a in articles
    ]


def validate_final_report(data: Dict[str, Any], expected_count: int) -> bool:
    req = ["report_title", "executive_summary", "today_key_message", "cross_article_implications", "priority_watch_items", "article_sections"]
    if not all(k in data for k in req):
        return False
    if not isinstance(data["priority_watch_items"], list) or not (3 <= len(data["priority_watch_items"]) <= 5):
        return False
    secs = data["article_sections"]
    if not isinstance(secs, list) or len(secs) != expected_count:
        return False
    for i, s in enumerate(secs, 1):
        if s.get("ref_id") != f"A{i}" or not s.get("url"):
            return False
    return True


def build_input_hash(target_date: str, articles: List[Dict[str, Any]], final_report: Dict[str, Any]) -> str:
    payload = {
        "target_date": target_date,
        "articles": [
            {
                "page_id": a.get("page_id"),
                "url": a.get("url"),
                "updated_time": a.get("updated_time"),
                "summary": a.get("summary"),
                "reason_to_read": a.get("reason_to_read"),
                "business_implications": a.get("business_implications"),
            }
            for a in articles
        ],
        "final_report": final_report,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
