from __future__ import annotations

from src.intelligence_policy import _checklist_rule_ids, _validate_rule_checklist
from src.intelligence_rules import IntelligenceRule, RuleSet


def rule(rule_id: str, rule_type: str, scope: str) -> IntelligenceRule:
    return IntelligenceRule(
        rule_id=rule_id,
        name=rule_id,
        rule_type=rule_type,
        decision_scope=scope,
        condition=rule_id,
        score=0,
        priority=1,
        applies_to=("All",),
    )


def ruleset() -> RuleSet:
    return RuleSet(
        rules=(
            rule("REQ_UPDATE_MATERIAL_DELTA", "REQUIRE", "UPDATE"),
            rule("BLK_UPDATE_SINGLE_PERIOD_STATS", "BLOCK", "UPDATE"),
            rule("BLK_UPDATE_CONTEXT_ONLY", "BLOCK", "UPDATE"),
            rule("REQ_CREATE_DURABLE", "REQUIRE", "CREATE"),
            rule("BLK_CREATE_CORPORATE_HOUSEKEEPING", "BLOCK", "CREATE"),
        ),
        create_min_score=5,
    )


def test_update_requires_every_block_and_require_check():
    rs = ruleset()
    op = {"action": "update", "rule_hits": ["REQ_UPDATE_MATERIAL_DELTA"]}
    ok, reason, _ = _validate_rule_checklist(
        rs,
        op,
        ["REQ_UPDATE_MATERIAL_DELTA"],
        {"REQ_UPDATE_MATERIAL_DELTA": True},
    )
    assert ok is False
    assert "BLK_UPDATE_SINGLE_PERIOD_STATS" in reason
    assert "BLK_UPDATE_CONTEXT_ONLY" in reason


def test_true_monthly_block_is_promoted_to_policy_hit():
    rs = ruleset()
    op = {"action": "update"}
    checks = {
        "REQ_UPDATE_MATERIAL_DELTA": True,
        "BLK_UPDATE_SINGLE_PERIOD_STATS": True,
        "BLK_UPDATE_CONTEXT_ONLY": False,
    }
    ok, reason, hits = _validate_rule_checklist(rs, op, [], checks)
    assert ok is True
    assert reason == ""
    assert "REQ_UPDATE_MATERIAL_DELTA" in hits
    assert "BLK_UPDATE_SINGLE_PERIOD_STATS" in hits
    decision = rs.evaluate({"action": "update", "theme": [], "event_type": "Capacity Expansion", "rule_hits": hits})
    assert decision.allowed is False
    assert decision.reason == "blocked_by_rules:BLK_UPDATE_SINGLE_PERIOD_STATS"


def test_false_monthly_block_allows_material_update():
    rs = ruleset()
    op = {"action": "update"}
    checks = {
        "REQ_UPDATE_MATERIAL_DELTA": True,
        "BLK_UPDATE_SINGLE_PERIOD_STATS": False,
        "BLK_UPDATE_CONTEXT_ONLY": False,
    }
    ok, _, hits = _validate_rule_checklist(rs, op, [], checks)
    assert ok is True
    decision = rs.evaluate({"action": "update", "theme": [], "event_type": "Capacity Expansion", "rule_hits": hits})
    assert decision.allowed is True


def test_create_checklist_is_separate_from_update_checklist():
    rs = ruleset()
    assert _checklist_rule_ids(rs, "CREATE") == [
        "REQ_CREATE_DURABLE",
        "BLK_CREATE_CORPORATE_HOUSEKEEPING",
    ]
