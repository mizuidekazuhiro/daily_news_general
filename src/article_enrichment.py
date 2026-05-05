from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate_article_json(data: Dict[str, Any]) -> bool:
    keys = ["summary", "reason_to_read", "business_implications"]
    if not all(isinstance(data.get(k), str) and data.get(k).strip() for k in keys):
        return False
    return len(data["summary"]) >= 50 and len(data["reason_to_read"]) >= 40 and len(data["business_implications"]) >= 80


def build_notion_payload(data: Dict[str, Any], model: str) -> Dict[str, Any]:
    return {
        "Summary": data["summary"],
        "Reason to Read": data["reason_to_read"],
        "Business Implications": data["business_implications"],
        "GPT Processed": True,
        "GPT Model": model,
    }


def filter_targets(
    selected: List[Dict[str, Any]],
    force_reprocess: bool = False,
    min_importance_score: float = 3.0,
) -> Tuple[List[Dict[str, Any]], int]:
    out = []
    skipped = 0
    for a in selected:
        if (not force_reprocess) and a.get("gpt_processed"):
            a["gpt_enrichment_skipped_reason"] = "already_processed"
            skipped += 1
            continue

        try:
            importance_score = float(a.get("importance_score", 0) or 0)
        except Exception:
            importance_score = 0.0

        if importance_score <= min_importance_score:
            a["gpt_enrichment_skipped_reason"] = f"importance_score_lte_{min_importance_score:g}"
            skipped += 1
            continue

        if len((a.get("full_text") or "").strip()) < 120:
            a["gpt_enrichment_skipped_reason"] = "missing_or_short_text"
            skipped += 1
            continue

        out.append(a)
    return out, skipped
