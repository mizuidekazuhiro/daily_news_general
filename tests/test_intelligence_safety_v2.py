from __future__ import annotations

from src.intelligence_pipeline import Article, Insight
from src.intelligence_safety import _unsupported_grounding_claims, safe_normalize_operations


def article(title: str, body: str, page_id: str = "11111111-1111-1111-1111-111111111111") -> Article:
    return Article(
        source="general",
        page_id=page_id,
        title=title,
        published_at="2026-08-27",
        importance_score=8.0,
        source_name="test",
        country=["India"],
        tags=[],
        body=body,
        notion_url="",
    )


def insight(key: str, company: str, country: list[str]) -> Insight:
    return Insight(
        page_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        insight="Existing project",
        insight_key=key,
        status="Tracking",
        importance="High",
        confidence="High",
        company=company,
        country=country,
        theme=["JV/M&A"],
        event_type="JV/M&A",
        key_facts="Existing fact.",
        what_changed="Existing delta.",
        business_implication="Existing implication.",
        watch_items="Existing watch.",
        first_seen="2026-01-01",
        last_updated="2026-07-01",
        last_processed="2026-07-01",
        nikkei_sources=[],
        general_sources=[],
        source_count=0,
        model="gpt-5-mini",
    )


def raw_update(a: Article, x: Insight, *, facts: str, changed: str):
    return {
        "operations": [{
            "action": "update",
            "matched_existing_key": x.insight_key,
            "insight_key": x.insight_key,
            "insight": x.insight,
            "company": x.company,
            "country": x.country,
            "theme": x.theme,
            "event_type": x.event_type,
            "importance": "High",
            "confidence": "High",
            "key_facts": facts,
            "what_changed": changed,
            "business_implication": "Material project implication.",
            "watch_items": "Next milestone.",
            "article_refs": [a.ref()],
        }]
    }


def test_spelled_scaled_quantity_supports_digit_mt_wording():
    a = article(
        "Tata Steel capex update",
        "Tata Steel plans an industrial facility of approximately one million tonnes per year in Jamshedpur.",
    )
    op = {"key_facts": "The planned facility is approximately 1 Mtpa.", "what_changed": ""}
    assert _unsupported_grounding_claims(op, [a]) == []


def test_fy_identifier_does_not_create_spurious_six_claim():
    a = article(
        "West Bokaro demand notice",
        "The notice alleges 16.24 million tonnes between FY 2000-01 and FY 2006-07 and seeks Rs 1,755 crore.",
    )
    op = {
        "key_facts": "The notice alleges 16.24 million tonnes between FY2000-01 and FY2006-07 and seeks Rs 1,755 crore.",
        "what_changed": "Rs 1,755 crore demand notice issued.",
    }
    assert _unsupported_grounding_claims(op, [a]) == []


def test_headline_only_or_contradicted_formed_jv_upgrade_is_blocked():
    a = article(
        "SAIL & Krakatau Steel form JV for stainless slab plant",
        "SAIL and PT Krakatau Steel signed an MoU in Indonesia to explore a proposed joint venture. "
        "Final ownership, capacity and approvals remain subject to feasibility studies.",
    )
    x = insight("sail|indonesia|greenfield-jv|2026", "Steel Authority of India Limited (SAIL) / PT Krakatau Steel", ["India", "Indonesia"])
    raw = raw_update(
        a,
        x,
        facts="SAIL and PT Krakatau Steel have formed a joint venture in Indonesia.",
        changed="The project advanced from MoU to a formed joint venture.",
    )
    ops = safe_normalize_operations(raw, [a], [x])
    assert ops[0]["action"] == "noop"
    assert ops[0].get("safety_reason") == "headline_only_status_upgrade"


def test_body_supported_jva_upgrade_can_pass_status_guard():
    a = article(
        "JSW Steel and POSCO sign Odisha JVA",
        "JSW Steel and POSCO signed a Joint Venture Agreement for a 50:50 integrated steel project in Odisha. "
        "The parties will establish the joint venture for the greenfield steel plant.",
    )
    x = insight("jsw-posco|odisha|greenfield-jv|2026", "JSW Steel / POSCO Group", ["India"])
    raw = raw_update(
        a,
        x,
        facts="JSW Steel and POSCO signed a Joint Venture Agreement for the Odisha project.",
        changed="The parties signed the JVA.",
    )
    ops = safe_normalize_operations(raw, [a], [x])
    assert ops[0]["action"] == "update"
