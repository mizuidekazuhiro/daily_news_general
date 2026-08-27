from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import src.intelligence_pipeline as pipeline


CONFIG_CREATE_MIN_SCORE = "CFG_CREATE_MIN_SCORE"
ALLOWED_RULE_TYPES = {"BLOCK", "BOOST", "REQUIRE", "CONFIG"}
ALLOWED_SCOPES = {"CREATE", "UPDATE", "ALL"}


@dataclass(frozen=True)
class IntelligenceRule:
    rule_id: str
    name: str
    rule_type: str
    decision_scope: str
    condition: str
    score: float
    priority: int
    applies_to: tuple[str, ...]
    rationale: str = ""
    example: str = ""
    counterexample: str = ""
    version: str = ""

    def to_prompt(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "type": self.rule_type,
            "scope": self.decision_scope,
            "condition": self.condition,
            "score": self.score,
            "applies_to": list(self.applies_to),
            "example": self.example,
            "counterexample": self.counterexample,
        }


@dataclass(frozen=True)
class RuleDecision:
    allowed: bool
    reason: str
    score: float
    valid_hits: tuple[str, ...]
    missing_requirements: tuple[str, ...] = ()
    block_hits: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[IntelligenceRule, ...]
    create_min_score: float

    @property
    def by_id(self) -> dict[str, IntelligenceRule]:
        return {rule.rule_id: rule for rule in self.rules}

    @property
    def fingerprint(self) -> str:
        payload = {
            "create_min_score": self.create_min_score,
            "rules": [rule.to_prompt() for rule in self.rules],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    def summary(self) -> dict[str, Any]:
        counts = {kind: 0 for kind in ("BLOCK", "BOOST", "REQUIRE")}
        for rule in self.rules:
            if rule.rule_type in counts:
                counts[rule.rule_type] += 1
        return {
            "rule_count": len(self.rules),
            "create_min_score": self.create_min_score,
            "fingerprint": self.fingerprint,
            "counts": counts,
        }

    def prompt_fragment(self) -> str:
        rules_json = json.dumps(
            [rule.to_prompt() for rule in self.rules],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"""

BUSINESS POLICY RULES (loaded from Notion Intelligence Rules):
- These rules control business judgment only. Data-integrity safety rules remain code-enforced separately.
- For every CREATE or UPDATE operation, return `rule_hits` as an array of exact Rule IDs that are TRUE for that operation and `rule_reason` as one short sentence.
- Evaluate rules semantically against the referenced article(s), not by keyword matching alone.
- Include a BLOCK rule in rule_hits whenever its condition is true, even if BOOST rules are also true.
- Include a REQUIRE rule only when its condition is actually satisfied.
- Include a BOOST rule only when its condition is actually satisfied.
- For CREATE, the application will reject the operation if any applicable BLOCK rule hits, any applicable REQUIRE rule is missing, or total applicable BOOST score is below {self.create_min_score:g}.
- For UPDATE, the application will reject the operation if any applicable REQUIRE rule is missing. CREATE score does not apply to UPDATE.
- Never invent Rule IDs. Use only the IDs below.
Rules={rules_json}
""".rstrip()

    @staticmethod
    def _operation_tags(operation: dict[str, Any]) -> set[str]:
        tags = {"Steel"}
        tags.update(str(x) for x in (operation.get("theme") or []) if x)
        event_type = str(operation.get("event_type") or "").strip()
        if event_type:
            tags.add(event_type)
        return tags

    def _applies(self, rule: IntelligenceRule, operation: dict[str, Any]) -> bool:
        action = str(operation.get("action") or "").upper()
        if rule.decision_scope not in {"ALL", action}:
            return False
        if not rule.applies_to or "All" in rule.applies_to:
            return True
        return bool(set(rule.applies_to) & self._operation_tags(operation))

    def evaluate(self, operation: dict[str, Any]) -> RuleDecision:
        action = str(operation.get("action") or "").strip().lower()
        if action not in {"create", "update"}:
            return RuleDecision(True, "not_policy_scoped", 0.0, ())

        by_id = self.by_id
        raw_hits = operation.get("rule_hits") or []
        hits: list[str] = []
        seen: set[str] = set()
        for value in raw_hits if isinstance(raw_hits, list) else []:
            rule_id = str(value or "").strip()
            if rule_id in by_id and rule_id not in seen:
                seen.add(rule_id)
                hits.append(rule_id)

        applicable = [rule for rule in self.rules if self._applies(rule, operation)]
        required = [rule.rule_id for rule in applicable if rule.rule_type == "REQUIRE"]
        missing = tuple(rule_id for rule_id in required if rule_id not in seen)
        if missing:
            return RuleDecision(
                False,
                "missing_required_rules:" + ",".join(missing),
                0.0,
                tuple(hits),
                missing_requirements=missing,
            )

        blocked = tuple(
            rule.rule_id
            for rule in applicable
            if rule.rule_type == "BLOCK" and rule.rule_id in seen
        )
        if blocked:
            return RuleDecision(
                False,
                "blocked_by_rules:" + ",".join(blocked),
                0.0,
                tuple(hits),
                block_hits=blocked,
            )

        if action == "create":
            score = sum(
                rule.score
                for rule in applicable
                if rule.rule_type == "BOOST" and rule.rule_id in seen
            )
            if score < self.create_min_score:
                return RuleDecision(
                    False,
                    f"create_score_below_threshold:{score:g}<{self.create_min_score:g}",
                    score,
                    tuple(hits),
                )
            return RuleDecision(True, "create_policy_pass", score, tuple(hits))

        return RuleDecision(True, "update_policy_pass", 0.0, tuple(hits))


_ACTIVE_RULESET: RuleSet | None = None


def set_active_rules(ruleset: RuleSet | None) -> None:
    global _ACTIVE_RULESET
    _ACTIVE_RULESET = ruleset


def get_active_rules() -> RuleSet | None:
    return _ACTIVE_RULESET


def clear_active_rules() -> None:
    set_active_rules(None)


def _enabled(props: dict[str, Any]) -> bool:
    return bool((props.get("Enabled") or {}).get("checkbox"))


def _parse_rule(row: dict[str, Any]) -> IntelligenceRule | None:
    props = row.get("properties") or {}
    if not _enabled(props):
        return None
    rule_id = pipeline._prop_text(props, "Rule ID").strip()
    rule_type = pipeline._prop_text(props, "Rule Type").strip().upper()
    scope = pipeline._prop_text(props, "Decision Scope").strip().upper() or "ALL"
    if not rule_id:
        raise RuntimeError("Enabled Intelligence Rule has blank Rule ID")
    if rule_type not in ALLOWED_RULE_TYPES:
        raise RuntimeError(f"Invalid Intelligence Rule type for {rule_id}: {rule_type}")
    if scope not in ALLOWED_SCOPES:
        raise RuntimeError(f"Invalid Intelligence Rule scope for {rule_id}: {scope}")
    return IntelligenceRule(
        rule_id=rule_id,
        name=pipeline._prop_text(props, "Rule") or rule_id,
        rule_type=rule_type,
        decision_scope=scope,
        condition=pipeline._prop_text(props, "Condition"),
        score=pipeline._prop_number(props, "Score"),
        priority=int(pipeline._prop_number(props, "Priority")),
        applies_to=tuple(pipeline._prop_multi(props, "Applies To")),
        rationale=pipeline._prop_text(props, "Rationale"),
        example=pipeline._prop_text(props, "Example"),
        counterexample=pipeline._prop_text(props, "Counterexample"),
        version=pipeline._prop_text(props, "Version"),
    )


def load_rule_set(notion: Any, database_id: str) -> RuleSet:
    rows = notion.query_database(
        database_id,
        filter_obj={"property": "Enabled", "checkbox": {"equals": True}},
        sorts=[{"property": "Priority", "direction": "ascending"}],
    )
    parsed = [rule for row in rows if (rule := _parse_rule(row)) is not None]
    if not parsed:
        raise RuntimeError("Intelligence Rules DB returned no enabled rules")

    seen: set[str] = set()
    duplicates: list[str] = []
    for rule in parsed:
        if rule.rule_id in seen:
            duplicates.append(rule.rule_id)
        seen.add(rule.rule_id)
    if duplicates:
        raise RuntimeError("Duplicate Intelligence Rule IDs: " + ",".join(sorted(set(duplicates))))

    configs = [rule for rule in parsed if rule.rule_id == CONFIG_CREATE_MIN_SCORE and rule.rule_type == "CONFIG"]
    if len(configs) != 1 or configs[0].score <= 0:
        raise RuntimeError("Enabled CONFIG_CREATE_MIN_SCORE rule with positive Score is required")

    rules = tuple(rule for rule in parsed if rule.rule_type != "CONFIG")
    ruleset = RuleSet(rules=rules, create_min_score=configs[0].score)
    logging.info(
        "intelligence_rules_loaded db=%s rules=%s create_min_score=%s fingerprint=%s",
        database_id,
        len(rules),
        ruleset.create_min_score,
        ruleset.fingerprint,
    )
    return ruleset


def load_active_rules(notion: Any, database_id: str) -> RuleSet:
    ruleset = load_rule_set(notion, database_id)
    set_active_rules(ruleset)
    return ruleset
