"""Strict Intelligence output contract, independent of business-policy weights."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def retryable_noop_reason(operation: dict[str, Any]) -> str:
    """Integrity/technical rejection is not a successfully classified NOOP."""
    reason = str(operation.get("safety_reason") or "")
    if reason:
        return reason
    policy = str(operation.get("policy_reason") or "")
    return policy if policy.startswith("missing_rule_checks:") else ""


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def build_schema(ruleset: Any, *, countries: set[str], themes: set[str],
                 event_types: set[str], levels: set[str]) -> dict[str, Any]:
    # Use the same cached Notion snapshot and checklist helper as the prompt.
    from src.intelligence_policy import _checklist_rule_ids

    text = {"type": "string"}
    nonempty = {"type": "string", "pattern": r"\S"}
    refs = {"type": "array", "minItems": 1, "items": _object({
        "source": {"type": "string", "enum": ["nikkei", "general"]},
        "page_id": nonempty, "published_at": text,
    })}
    variants = []
    for action in ("create", "update"):
        check_ids = _checklist_rule_ids(ruleset, action) if ruleset is not None else []
        properties = {
            "action": {"type": "string", "enum": [action]},
            "matched_existing_key": {"type": ["string", "null"]},
            "insight_key": nonempty, "insight": nonempty, "company": text,
            "country": {"type": "array", "items": {"type": "string", "enum": sorted(countries)}},
            "theme": {"type": "array", "items": {"type": "string", "enum": sorted(themes)}},
            "event_type": {"type": "string", "enum": sorted(event_types)},
            "importance": {"type": "string", "enum": sorted(levels)},
            "confidence": {"type": "string", "enum": sorted(levels)},
            "key_facts": nonempty, "what_changed": text,
            "business_implication": text, "watch_items": text,
            "article_refs": refs,
            "rule_checks": _object({key: {"type": "boolean"} for key in check_ids}),
            "rule_hits": {"type": "array", "items": nonempty}, "rule_reason": nonempty,
        }
        variants.append(_object(properties))
    variants.append(_object({"action": {"type": "string", "enum": ["noop"]},
                             "article_refs": refs, "rule_reason": nonempty}))
    return _object({"operations": {"type": "array", "minItems": 1,
                                   "items": {"anyOf": variants}}})


def validate_operations(raw: dict[str, Any], candidates: list[Any], existing: list[Any],
                        ruleset: Any, normalize: Callable[..., list[dict[str, Any]]]) -> None:
    """Reject the whole generated batch before *any* operation is applied."""
    def ref_key(ref: dict[str, Any]) -> tuple[str, str]:
        return ref["source"], ref["page_id"].replace("-", "").strip()

    expected = {ref_key(article.ref()): article for article in candidates}
    existing_keys = {item.insight_key for item in existing}
    known_rules = set(ruleset.by_id) if ruleset is not None else set()
    seen: set[tuple[str, str]] = set()
    insight_keys: set[str] = set()
    for index, operation in enumerate(raw["operations"]):
        for ref in operation["article_refs"]:
            key = ref_key(ref)
            if key not in expected:
                raise ValueError(f"unknown_article_ref:operation={index}")
            if key in seen:
                raise ValueError(f"duplicate_article_ref:operation={index}")
            if ref["published_at"] != expected[key].published_at:
                raise ValueError(f"published_at_mismatch:operation={index}")
            seen.add(key)
        if not operation["rule_reason"].strip():
            raise ValueError(f"empty_rule_reason:operation={index}")
        if operation["action"] == "noop":
            continue
        key = operation["insight_key"].strip()
        if not key or key in insight_keys:
            raise ValueError(f"empty_or_duplicate_insight_key:operation={index}")
        insight_keys.add(key)
        matched = operation["matched_existing_key"]
        if operation["action"] == "update":
            if matched not in existing_keys or key != matched:
                raise ValueError(f"update_identity_mismatch:operation={index}")
        elif matched is not None or key in existing_keys:
            raise ValueError(f"create_conflicts_with_existing:operation={index}")
        if any(rule not in known_rules for rule in operation["rule_hits"]):
            raise ValueError(f"unknown_rule_id:operation={index}")
        hits = set(operation["rule_hits"])
        if any((rule in hits) != value for rule, value in operation["rule_checks"].items()):
            raise ValueError(f"rule_checks_hits_mismatch:operation={index}")
    if seen != set(expected):
        raise ValueError(f"unclassified_articles:count={len(set(expected) - seen)}")
    normalized = normalize(raw, candidates, existing)
    if len(normalized) != len(raw["operations"]):
        raise ValueError("operations_dropped_by_normalizer")
    normalized_refs = [ref_key(ref) for op in normalized for ref in op.get("article_refs", [])]
    if len(normalized_refs) != len(seen) or set(normalized_refs) != seen:
        raise ValueError("references_dropped_by_normalizer")
    for operation in normalized:
        reason = retryable_noop_reason(operation)
        if reason:
            raise ValueError("integrity_rejected:" + reason)


def generate_operations_json(*, client: Any, candidates: list[Any], existing: list[Any],
                             normalize: Callable[..., list[dict[str, Any]]],
                             diagnostics_path: Path, **generation: Any) -> dict[str, Any]:
    from src import intelligence_pipeline as pipeline
    from src.intelligence_rules import get_active_rules

    # system_prompt has already loaded the policy snapshot before this call.
    ruleset = get_active_rules()
    schema = build_schema(ruleset, countries=pipeline.ALLOWED_COUNTRIES,
                          themes=pipeline.ALLOWED_THEMES, event_types=pipeline.ALLOWED_EVENT_TYPES,
                          levels=pipeline.ALLOWED_LEVELS)
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.with_name("intelligence_contract.json").write_text(
        json.dumps({"schema": schema,
                    "rules_fingerprint": ruleset.fingerprint if ruleset is not None else None,
                    "candidate_refs": [article.ref() for article in candidates]},
                   ensure_ascii=False, indent=2), encoding="utf-8",
    )
    generation["system_prompt"] += (
        "\nOUTPUT CONTRACT: Follow the supplied JSON Schema. Classify EVERY new article exactly once, "
        "including a NOOP with article_refs and a nonempty rule_reason when appropriate. "
        "Fields belong directly to each operation, never inside record. "
        "Do not claim that a record has been saved. Copy source IDs and published_at exactly. "
        "For CREATE matched_existing_key is null; UPDATE preserves the exact existing key."
    )
    return client.generate_structured_json(
        **generation, schema=schema, diagnostics_path=diagnostics_path,
        validate=lambda raw: validate_operations(raw, candidates, existing, ruleset, normalize),
    )
