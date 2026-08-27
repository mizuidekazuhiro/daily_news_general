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
19. A material project-state upgrade (for example JV formed, acquisition closed, plant commissioned, FID/approval, construction started) must be supported by substantive article body text, not only by a headline, SEO title or navigation snippet.
""".strip()


def _checklist_rule_ids(ruleset: Any, action: str) -> list[str]:
    action = str(action or "").upper()
    rule_types = {"BLOCK", "REQUIRE"}
    if action == "CREATE":
        # CREATE scoring is entirely determined by BOOST hits. Require an
        # explicit boolean for each BOOST too so a valid structural event is not
        # rejected merely because the model forgot to mention a true boost in
        # rule_hits. The conditions and scores still come only from Notion.
        rule_types.add("BOOST")
    return [
        rule.rule_id
        for rule in ruleset.rules
        if rule.rule_type in rule_types
        and rule.decision_scope in {"ALL", action}
    ]


def policy_prompt_system() -> str:
    ruleset = _ensure_rules_loaded()
    base = _integrity_prompt_system()
    if ruleset is None:
        return base
    create_checks = _checklist_rule_ids(ruleset, "CREATE")
    update_checks = _checklist_rule_ids(ruleset, "UPDATE")
    checklist = f"""

MANDATORY POLICY CHECKLIST:
- For every CREATE and UPDATE operation, return `rule_checks` as an object whose keys are exact Notion Rule IDs and values are JSON booleans.
- CREATE must explicitly check every applicable policy-control ID in this list, including every BOOST used for CREATE scoring: {create_checks}
- UPDATE must explicitly check every BLOCK/REQUIRE ID in this list: {update_checks}
- `true` means the rule condition is satisfied by the referenced article(s); `false` means it is not.
- You MUST evaluate every listed rule one-by-one. Do not omit a rule merely because you think it is obviously false.
- `rule_hits` must include every rule whose `rule_checks` value is true. The application treats the explicit checklist as authoritative for listed rules.
- A missing listed check makes the operation invalid and the application will convert it to NOOP.
- Example: a JSW monthly production release with no restart/completion/capacity milestone must set BLK_UPDATE_SINGLE_PERIOD_STATS=true even if it mentions an ongoing BF upgrade.
""".rstrip()
    return base + ruleset.prompt_fragment() + checklist


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


def _policy_fields(raw: Any, operation: dict[str, Any]) -> tuple[list[str], str, dict[str, bool]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        return [], "", {}
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
        return [], "", {}
    hits = []
    seen: set[str] = set()
    for value in best.get("rule_hits") or []:
        rule_id = str(value or "").strip()
        if rule_id and rule_id not in seen:
            seen.add(rule_id)
            hits.append(rule_id)

    checks: dict[str, bool] = {}
    raw_checks = best.get("rule_checks")
    if isinstance(raw_checks, dict):
        for key, value in raw_checks.items():
            rule_id = str(key or "").strip()
            if rule_id and isinstance(value, bool):
                checks[rule_id] = value
    return hits, str(best.get("rule_reason") or "").strip(), checks


def _validate_rule_checklist(
    ruleset: Any,
    operation: dict[str, Any],
    hits: list[str],
    checks: dict[str, bool],
) -> tuple[bool, str, list[str]]:
    required_ids = _checklist_rule_ids(ruleset, str(operation.get("action") or ""))
    missing = [rule_id for rule_id in required_ids if rule_id not in checks]
    if missing:
        return False, "missing_rule_checks:" + ",".join(missing), hits

    control_ids = set(required_ids)
    merged: list[str] = []
    seen: set[str] = set()
    for rule_id in hits:
        if rule_id in control_ids:
            continue
        if rule_id not in seen:
            seen.add(rule_id)
            merged.append(rule_id)
    for rule_id in required_ids:
        if checks.get(rule_id) and rule_id not in seen:
            seen.add(rule_id)
            merged.append(rule_id)
    return True, "", merged


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

        if operation.get("action") == "update" and safety._headline_only_status_upgrade(operation, articles):
            safe.append(_noop_from(operation, "headline_only_status_upgrade", safety_reason=True))
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

        hits, rule_reason, checks = _policy_fields(raw, operation)
        checklist_ok, checklist_reason, checked_hits = _validate_rule_checklist(ruleset, operation, hits, checks)
        checked = dict(operation)
        checked["rule_hits"] = checked_hits
        checked["rule_reason"] = rule_reason
        checked["rule_checks"] = checks
        if not checklist_ok:
            out.append(_noop_from(checked, checklist_reason, hits=tuple(checked_hits)))
            continue

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
