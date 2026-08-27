from __future__ import annotations

import os
from typing import Any

import src.intelligence_pipeline as pipeline
import src.intelligence_safety as safety
from src.intelligence_rules import get_active_rules, load_active_rules


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


def _integrity_prompt_system() -> str:
    """Keep only non-negotiable data-integrity instructions in code.

    CREATE/UPDATE/NOOP business judgment is intentionally absent here and comes
    from the Notion Intelligence Rules control plane.
    """
    return safety._ORIGINAL_PROMPT_SYSTEM() + """

DATA-INTEGRITY OVERRIDES (code-enforced, not business-policy rules):
13. UPDATE IDENTITY LOCK: An update must concern the same entity/company AND the same geography/project/topic as the existing row. Company name alone is never sufficient. If an existing insight_key contains a named country, state, city, plant or project, the new source must explicitly concern that geography/project. A nationality reference such as 'Indian owner' does not establish that an article concerns Indian operations.
14. TOPIC LOCK: A same-company/same-country article still cannot update an unrelated topic. Capacity/capex rows require capacity, production, investment or expansion evidence; technology/project rows require evidence about that technology/project.
15. For update, do NOT redefine the row. Keep the existing insight title, company, country, theme and event_type conceptually unchanged. The application layer enforces this lock.
16. For update, key_facts must contain ONLY the new source-supported factual delta. Do not rewrite or summarize away prior facts; the application layer merges the new delta into historical Key Facts.
17. Every number, amount, capacity, percentage, date and stated duration in key_facts/what_changed must appear in the referenced new article text. If the source does not state it, omit it and put the uncertainty in watch_items.
18. Never infer execution risk, capex reallocation, delays or causality from a management/personnel change unless the source explicitly links that change to the tracked project.
""".strip()


def policy_prompt_system() -> str:
    ruleset = _ensure_rules_loaded()
    base = _integrity_prompt_system()
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


def _noop_from(
    operation: dict[str, Any],
    reason: str,
    *,
    safety_reason: bool = False,
    score: float = 0.0,
    hits: tuple[str, ...] = (),
) -> dict[str, Any]:
    out = {
        "action": "noop",
        "article_refs": operation.get("article_refs") or [],
    }
    if safety_reason:
        out["safety_reason"] = reason
    else:
        out["policy_reason"] = reason
        out["policy_score"] = score
        out["policy_rule_hits"] = list(hits)
    return out


def _integrity_normalize_operations(
    raw: Any,
    candidates: list[pipeline.Article],
    existing: list[pipeline.Insight],
) -> list[dict[str, Any]]:
    """Apply only hard integrity checks; business CREATE rules live in Notion."""
    operations = safety._ORIGINAL_NORMALIZE_OPERATIONS(raw, candidates, existing)
    by_key = {x.insight_key: x for x in existing if x.insight_key}
    safe: list[dict[str, Any]] = []

    for operation in operations:
        if operation.get("action") == "noop":
            safe.append(operation)
            continue

        articles = safety._articles_for_operation(operation, candidates)
        if not articles:
            safe.append(_noop_from(operation, "missing_verified_articles", safety_reason=True))
            continue

        unsupported = safety._unsupported_grounding_claims(operation, articles)
        if unsupported:
            safe.append(
                _noop_from(
                    operation,
                    "unsupported_numeric_or_duration_claim:" + ",".join(unsupported),
                    safety_reason=True,
                )
            )
            continue

        if operation.get("action") == "update":
            matched = by_key.get(str(operation.get("insight_key") or ""))
            if not matched:
                safe.append(_noop_from(operation, "missing_existing_insight", safety_reason=True))
                continue
            allowed, reason = safety._update_identity_guard(matched, articles)
            if not allowed:
                safe.append(_noop_from(operation, reason, safety_reason=True))
                continue

        safe.append(operation)

    return safe


def policy_normalize_operations(
    raw: Any,
    candidates: list[pipeline.Article],
    existing: list[pipeline.Insight],
) -> list[dict[str, Any]]:
    operations = _integrity_normalize_operations(raw, candidates, existing)
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
            out.append(
                _noop_from(
                    checked,
                    decision.reason,
                    score=decision.score,
                    hits=decision.valid_hits,
                )
            )
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
    # `apply_safety_patch()` is still applied first so update property writes keep
    # identity locks and cumulative fields. Here we replace only the prompt and
    # classification-normalization path, removing the legacy hard-coded CREATE
    # business rule so Notion is the single business-policy source of truth.
    pipeline._prompt_system = policy_prompt_system
    pipeline.normalize_operations = policy_normalize_operations
    _PATCHED = True
