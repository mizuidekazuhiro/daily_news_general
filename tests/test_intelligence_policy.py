from __future__ import annotations

import src.intelligence_pipeline as pipeline
from src import intelligence_policy
from src.intelligence_rules import IntelligenceRule, RuleSet, clear_active_rules, set_active_rules


def rule(rule_id: str, rule_type: str, score: float = 0, scope: str = "CREATE") -> IntelligenceRule:
    return IntelligenceRule(
        rule_id=rule_id,
        name=rule_id,
        rule_type=rule_type,
        decision_scope=scope,
        condition=rule_id,
        score=score,
        priority=1,
        applies_to=("All",),
    )


def article() -> pipeline.Article:
    return pipeline.Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title="Company secures long-term ore rights",
        published_at="2026-08-27",
        importance_score=8.0,
        source_name="test",
        country=["India"],
        tags=["Raw Materials"],
        body="Company secured long-term rights to a strategic ore deposit in India, improving raw-material security.",
        notion_url="",
    )


def test_policy_can_allow_other_event_without_legacy_durable_keyword_guard(monkeypatch):
    # This synthetic event deliberately avoids the legacy safety module's fixed
    # DURABLE_CREATE_TERMS. It should nevertheless pass when the Notion policy
    # says it is a concrete durable raw-material event.
    ruleset = RuleSet(
        rules=(
            rule("REQ_CREATE_DURABLE", "REQUIRE"),
            rule("REQ_CREATE_CONCRETE_CHANGE", "REQUIRE"),
            rule("BST_RAW_MATERIALS", "BOOST", 2),
            rule("BST_MATERIAL_IMPACT", "BOOST", 2),
            rule("BST_SOURCE_STRENGTH", "BOOST", 1),
        ),
        create_min_score=5,
    )
    set_active_rules(ruleset)
    try:
        a = article()
        raw = {
            "operations": [{
                "action": "create",
                "matched_existing_key": None,
                "insight_key": "company|india|ore-rights|2026",
                "insight": "Company secures long-term ore rights",
                "company": "Company",
                "country": ["India"],
                "theme": ["Raw Materials"],
                "event_type": "Other",
                "importance": "High",
                "confidence": "High",
                "key_facts": "Company secured long-term rights to a strategic ore deposit in India.",
                "what_changed": "Long-term ore rights were secured.",
                "business_implication": "Improves raw-material security.",
                "watch_items": "Development and production milestones.",
                "article_refs": [a.ref()],
                "rule_hits": [
                    "REQ_CREATE_DURABLE",
                    "REQ_CREATE_CONCRETE_CHANGE",
                    "BST_RAW_MATERIALS",
                    "BST_MATERIAL_IMPACT",
                    "BST_SOURCE_STRENGTH",
                ],
                "rule_reason": "Concrete durable raw-material rights event.",
            }]
        }
        ops = intelligence_policy.policy_normalize_operations(raw, [a], [])
        assert len(ops) == 1
        assert ops[0]["action"] == "create"
        assert ops[0]["policy_score"] == 5
    finally:
        clear_active_rules()


def test_policy_block_rule_converts_create_to_noop():
    ruleset = RuleSet(
        rules=(
            rule("REQ_CREATE_DURABLE", "REQUIRE"),
            rule("REQ_CREATE_CONCRETE_CHANGE", "REQUIRE"),
            rule("BST_MATERIAL_IMPACT", "BOOST", 2),
            rule("BST_SOURCE_STRENGTH", "BOOST", 1),
            rule("BLK_COMMENTARY_ONLY", "BLOCK", -3),
        ),
        create_min_score=3,
    )
    set_active_rules(ruleset)
    try:
        a = article()
        raw = {
            "operations": [{
                "action": "create",
                "matched_existing_key": None,
                "insight_key": "company|india|commentary|2026",
                "insight": "Management commentary",
                "company": "Company",
                "country": ["India"],
                "theme": ["Raw Materials"],
                "event_type": "Other",
                "importance": "Medium",
                "confidence": "Medium",
                "key_facts": "Company secured long-term rights to a strategic ore deposit in India.",
                "what_changed": "Long-term ore rights were secured.",
                "business_implication": "Context only.",
                "watch_items": "Future actions.",
                "article_refs": [a.ref()],
                "rule_hits": [
                    "REQ_CREATE_DURABLE",
                    "REQ_CREATE_CONCRETE_CHANGE",
                    "BST_MATERIAL_IMPACT",
                    "BST_SOURCE_STRENGTH",
                    "BLK_COMMENTARY_ONLY",
                ],
            }]
        }
        ops = intelligence_policy.policy_normalize_operations(raw, [a], [])
        assert ops[0]["action"] == "noop"
        assert ops[0]["policy_reason"] == "blocked_by_rules:BLK_COMMENTARY_ONLY"
    finally:
        clear_active_rules()
