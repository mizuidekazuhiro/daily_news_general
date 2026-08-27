from __future__ import annotations

import os
from typing import Any

import src.intelligence_pipeline as pipeline
from src.intelligence_rules import get_active_rules, load_active_rules


_ORIGINAL_PROMPT_SYSTEM = pipeline._prompt_system
_ORIGINAL_NORMALIZE_OPERATIONS = pipeline.normalize_operations
_PATCHED = False


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ensure_rules_loaded() -> Any:
    active = get_active_rules()
    if active is not None:
        return active

    database_id = str(os.getenv("INTELLIGENCE_RULES_DB_ID") or "").strip()
    required = _env_bool("INTELLIGENCE_RULES_REQUIRED", bool(database_id))
    if not database_id:
        if required:
            raise RuntimeError("INTELLIGENCE_RULES_DB_ID is required")
        return None

    token = str(os.getenv("NOTION_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("NOTION_TOKEN is required to load Intelligence Rules")
    notion = pipeline.NotionClient(token)
    return load_active_rules(notion, database_id)


def policy_prompt_system() -> str:
    ruleset = _ensure_rules_loaded()
    base = _ORIGINAL_PROMPT_SYSTEM()
    if ruleset is None:
        return base
    return base + ruleset.prompt_fragment()


def _clean_ref_ids(refs: Any) -> set[str]:
    out: set[str] = set()
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        page_id = str(ref.get("page_id") or "").strip()
        if page_id:
            out.add(pipeline._clean_id(page_id))
    return out


def _raw_key(item: dict[str, Any]) -> str:
    action = str(item.get("action") or "").strip().lower()
    if action == "update":
        return str(item.get("matched_existing_key") or item.get("insight_key") or "").strip()
    return str(item.get("insight_key") or "").strip()


def _policy_fields(raw: Any, operation: dict[str, Any]) -> tuple[list[str], str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        return [], ""
    target_action = str(operation.get("action") or "").strip().lower()
    target_key = str(operation.get("insight_key") or "").strip()
    target_refs = _clean_ref_ids(operation.get("article_refs"))

    best: dict[str, Any] | None = None
    best_overlap = -1
    for item in raw["operations"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "").strip().lower() != target_action:
            continue
        if _raw_key(item) != target_key:
            continue
        raw_refs = _clean_ref_ids(item.get("article_refs"))
        overlap = len(target_refs & raw_refs) if target_refs else 0
        if overlap > best_overlap:
            best = item
            best_overlap = overlap

    if best is None:
        return [], ""
    hits = []
    seen: set[str] = set()
    for value in best.get("rule_hits") or []:
        rule_id = str(value or "").strip()
        if rule_id and rule_id not in seen:
            seen.add(rule_id)
            hits.append(rule_id)
    return hits, str(best.get("rule_reason") or "").strip()


def _noop_from(operation: dict[str, Any], reason: str, score: float, hits: tuple[str, ...]) -> dict[str, Any]:
    return {
        "action": "noop",
        "article_refs": operation.get("article_refs") or [],
        "policy_reason": reason,
        "policy_score": score,
        "policy_rule_hits": list(hits),
    }


def policy_normalize_operations(
    raw: Any,
    candidates: list[pipeline.Article],
    existing: list[pipeline.Insight],
) -> list[dict[str, Any]]:
    operations = _ORIGINAL_NORMALIZE_OPERATIONS(raw, candidates, existing)
    ruleset = _ensure_rules_loaded()
    if ruleset is None:
        return operations

    out: list[dict[str, Any]] = []
    for operation in operations:
        if operation.get("action") == "noop":
            out.append(operation)
            continue

        hits, rule_reason = _policy_fields(raw, operation)
        checked = dict(operation)
        checked["rule_hits"] = hits
        checked["rule_reason"] = rule_reason
        decision = ruleset.evaluate(checked)
        if not decision.allowed:
            out.append(_noop_from(checked, decision.reason, decision.score, decision.valid_hits))
            continue

        checked["policy_score"] = decision.score
        checked["policy_rule_hits"] = list(decision.valid_hits)
        checked["policy_reason"] = decision.reason
        out.append(checked)
    return out


def apply_policy_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    pipeline._prompt_system = policy_prompt_system
    pipeline.normalize_operations = policy_normalize_operations
    _PATCHED = True
