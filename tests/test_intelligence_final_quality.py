from __future__ import annotations

from src.intelligence_pipeline import Article, Insight
from src.intelligence_safety import _unsupported_grounding_claims, _update_identity_guard


def article(title: str, body: str) -> Article:
    return Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title=title,
        published_at="2026-08-27",
        importance_score=8.0,
        source_name="test",
        country=["India"],
        tags=[],
        body=body,
        notion_url="",
    )


def insight(key: str, company: str, title: str = "Tracked Insight") -> Insight:
    return Insight(
        page_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        insight=title,
        insight_key=key,
        status="Tracking",
        importance="High",
        confidence="High",
        company=company,
        country=["India"],
        theme=["Capacity Expansion"],
        event_type="Capacity Expansion",
        key_facts="Historical core fact.",
        what_changed="Previous delta.",
        business_implication="Previous implication.",
        watch_items="Previous watch item.",
        first_seen="2026-01-01",
        last_updated="2026-07-01",
        last_processed="2026-07-01",
        nikkei_sources=[],
        general_sources=[],
        source_count=0,
        model="gpt-5-mini",
    )


def test_grounding_accepts_kt_to_tpa_equivalence():
    source = article(
        "SAIL signs JV",
        "The planned stainless steel plant will have capacity of 500 KT per year.",
    )
    op = {
        "key_facts": "The plant will have 500,000 tpa capacity.",
        "what_changed": "Capacity of 500,000 tpa was announced.",
    }
    assert _unsupported_grounding_claims(op, [source]) == []


def test_grounding_accepts_lakh_crore_equivalence():
    source = article(
        "AMNS foundation laid",
        "The project was announced with investment of Rs 1.36 lakh crore.",
    )
    op = {
        "key_facts": "Reported project investment is Rs 136,000 crore.",
        "what_changed": "The Rs 136,000 crore investment was confirmed.",
    }
    assert _unsupported_grounding_claims(op, [source]) == []


def test_grounding_accepts_decimal_format_equivalence():
    source = article("AMNS Hazira", "The new line has capacity of 2 Mtpa.")
    op = {
        "key_facts": "The line adds 2.0 Mtpa capacity.",
        "what_changed": "The 2.0 Mtpa line was commissioned.",
    }
    assert _unsupported_grounding_claims(op, [source]) == []


def test_compound_project_key_enforces_location_anchor():
    tracked = insight(
        "jsw-steel|andhra-pradesh|rayalaseema-lowcarbon|2026",
        "JSW Steel",
    )
    unrelated = article(
        "JSW Steel update",
        "JSW Steel announced a new investment in Maharashtra and discussed low-carbon steel production.",
    )
    allowed, reason = _update_identity_guard(tracked, [unrelated])
    assert not allowed
    assert reason == "geography_or_project_mismatch"


def test_same_site_different_mill_does_not_update_automotive_crc():
    tracked = insight(
        "amns-india|hazira|automotive-crc|2026",
        "ArcelorMittal Nippon Steel India (AM/NS India)",
    )
    csp = article(
        "AM/NS India CSP mill reaches 41 million tonnes",
        "At Hazira, AM/NS India said its compact strip production mill reached cumulative production of 41 million tonnes.",
    )
    allowed, reason = _update_identity_guard(tracked, [csp])
    assert not allowed
    assert reason == "topic_mismatch"


def test_rajjayyapeta_and_anakapalli_are_same_project_geography():
    tracked = insight(
        "amns-india|andhra-pradesh|rajayyapeta-greenfield|2026",
        "ArcelorMittal Nippon Steel India (AM/NS India)",
    )
    foundation = article(
        "AM/NS foundation laid in Anakapalli district",
        "ArcelorMittal Nippon Steel India laid the foundation for its greenfield integrated steel plant in Anakapalli district, Andhra Pradesh.",
    )
    allowed, reason = _update_identity_guard(tracked, [foundation])
    assert allowed, reason
