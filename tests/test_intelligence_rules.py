from __future__ import annotations

from src.intelligence_rules import IntelligenceRule, RuleSet


def rule(
    rule_id: str,
    rule_type: str,
    *,
    scope: str = "CREATE",
    score: float = 0,
    applies_to: tuple[str, ...] = ("All",),
) -> IntelligenceRule:
    return IntelligenceRule(
        rule_id=rule_id,
        name=rule_id,
        rule_type=rule_type,
        decision_scope=scope,
        condition=rule_id,
        score=score,
        priority=1,
        applies_to=applies_to,
    )


def ruleset() -> RuleSet:
    return RuleSet(
        rules=(
            rule("REQ_CREATE_DURABLE", "REQUIRE"),
            rule("REQ_CREATE_CONCRETE_CHANGE", "REQUIRE"),
            rule("REQ_UPDATE_MATERIAL_DELTA", "REQUIRE", scope="UPDATE"),
            rule("BST_NEW_PLANT", "BOOST", score=2, applies_to=("Steel", "Capacity Expansion")),
            rule("BST_CAPACITY_CAPEX", "BOOST", score=2, applies_to=("Steel", "Capacity Expansion")),
            rule("BST_MATERIAL_IMPACT", "BOOST", score=2),
            rule("BST_SOURCE_STRENGTH", "BOOST", score=1),
            rule("BLK_PERSONNEL_ONLY", "BLOCK"),
            rule("BLK_FINANCIALS_ONLY", "BLOCK", applies_to=("Financials",)),
        ),
        create_min_score=6,
    )


def create_operation(*hits: str, theme: list[str] | None = None) -> dict:
    return {
        "action": "create",
        "theme": theme or ["Capacity Expansion"],
        "event_type": "New Plant",
        "rule_hits": list(hits),
    }


def test_create_passes_when_requirements_and_threshold_are_met():
    decision = ruleset().evaluate(create_operation(
        "REQ_CREATE_DURABLE",
        "REQ_CREATE_CONCRETE_CHANGE",
        "BST_NEW_PLANT",
        "BST_CAPACITY_CAPEX",
        "BST_MATERIAL_IMPACT",
        "BST_SOURCE_STRENGTH",
    ))
    assert decision.allowed is True
    assert decision.score == 7
    assert decision.reason == "create_policy_pass"


def test_create_is_blocked_even_if_boost_score_is_high():
    decision = ruleset().evaluate(create_operation(
        "REQ_CREATE_DURABLE",
        "REQ_CREATE_CONCRETE_CHANGE",
        "BST_NEW_PLANT",
        "BST_CAPACITY_CAPEX",
        "BST_MATERIAL_IMPACT",
        "BLK_PERSONNEL_ONLY",
    ))
    assert decision.allowed is False
    assert decision.reason == "blocked_by_rules:BLK_PERSONNEL_ONLY"


def test_create_fails_when_required_rule_is_missing():
    decision = ruleset().evaluate(create_operation(
        "REQ_CREATE_DURABLE",
        "BST_NEW_PLANT",
        "BST_CAPACITY_CAPEX",
        "BST_MATERIAL_IMPACT",
    ))
    assert decision.allowed is False
    assert "REQ_CREATE_CONCRETE_CHANGE" in decision.missing_requirements


def test_create_fails_below_configured_threshold():
    decision = ruleset().evaluate(create_operation(
        "REQ_CREATE_DURABLE",
        "REQ_CREATE_CONCRETE_CHANGE",
        "BST_NEW_PLANT",
        "BST_MATERIAL_IMPACT",
    ))
    assert decision.allowed is False
    assert decision.score == 4
    assert decision.reason == "create_score_below_threshold:4<6"


def test_theme_specific_block_only_applies_when_theme_matches():
    base_hits = [
        "REQ_CREATE_DURABLE",
        "REQ_CREATE_CONCRETE_CHANGE",
        "BST_NEW_PLANT",
        "BST_CAPACITY_CAPEX",
        "BST_MATERIAL_IMPACT",
        "BLK_FINANCIALS_ONLY",
    ]
    capacity = ruleset().evaluate(create_operation(*base_hits, theme=["Capacity Expansion"]))
    assert capacity.allowed is True

    financial = ruleset().evaluate(create_operation(*base_hits, theme=["Financials", "Capacity Expansion"]))
    assert financial.allowed is False
    assert financial.reason == "blocked_by_rules:BLK_FINANCIALS_ONLY"


def test_update_requires_material_delta_but_no_create_score():
    decision = ruleset().evaluate({
        "action": "update",
        "theme": ["Capacity Expansion"],
        "event_type": "Capacity Expansion",
        "rule_hits": ["REQ_UPDATE_MATERIAL_DELTA"],
    })
    assert decision.allowed is True
    assert decision.score == 0


def test_unknown_rule_ids_do_not_help_score_or_requirements():
    decision = ruleset().evaluate(create_operation(
        "REQ_CREATE_DURABLE",
        "REQ_CREATE_CONCRETE_CHANGE",
        "UNKNOWN_RULE",
        "BST_NEW_PLANT",
    ))
    assert decision.allowed is False
    assert "UNKNOWN_RULE" not in decision.valid_hits
