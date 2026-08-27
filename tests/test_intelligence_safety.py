from __future__ import annotations

from src.intelligence_pipeline import Article, Insight
from src.intelligence_safety import safe_normalize_operations, safe_properties_for_operation


def make_article(
    page_id: str,
    *,
    title: str,
    body: str,
    country: list[str] | None = None,
    source: str = "general",
    published_at: str = "2026-08-27",
) -> Article:
    return Article(
        source=source,
        page_id=page_id,
        title=title,
        published_at=published_at,
        importance_score=8.0,
        source_name="test",
        country=country or [],
        tags=[],
        body=body,
        notion_url="",
    )


def make_insight(
    *,
    key: str,
    title: str,
    company: str,
    country: list[str],
    event_type: str = "Capacity Expansion",
) -> Insight:
    return Insight(
        page_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        insight=title,
        insight_key=key,
        status="Tracking",
        importance="High",
        confidence="High",
        company=company,
        country=country,
        theme=["Capacity Expansion"],
        event_type=event_type,
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


def operation_raw(article: Article, existing: Insight, **overrides):
    item = {
        "action": "update",
        "matched_existing_key": existing.insight_key,
        "insight_key": existing.insight_key,
        "insight": "GPT attempted replacement title",
        "company": existing.company,
        "country": existing.country,
        "theme": ["Capacity Expansion"],
        "event_type": existing.event_type,
        "importance": "High",
        "confidence": "High",
        "key_facts": "New verified fact.",
        "what_changed": "New verified delta.",
        "business_implication": "Updated implication.",
        "watch_items": "New watch item.",
        "article_refs": [article.ref()],
    }
    item.update(overrides)
    return {"operations": [item]}


def test_cross_geography_update_is_noop_even_with_noisy_country_metadata():
    existing = make_insight(
        key="tata-steel|india|capacity-capex|fy27",
        title="Tata Steel India capacity strategy",
        company="Tata Steel",
        country=["India"],
    )
    article = make_article(
        "11111111-1111-1111-1111-111111111111",
        title="Tata Steel secures support for IJmuiden",
        body="Tata Steel said its Netherlands operations will receive European support for cleaner technology.",
        country=["India", "EU"],  # deliberately noisy metadata
    )

    ops = safe_normalize_operations(operation_raw(article, existing), [article], [existing])
    assert ops[0]["action"] == "noop"
    assert ops[0]["safety_reason"] == "geography_or_project_mismatch"


def test_named_project_location_must_be_in_source_text():
    existing = make_insight(
        key="sail|bokaro|brownfield-expansion|2026",
        title="SAIL Bokaro expansion",
        company="Steel Authority of India Limited (SAIL)",
        country=["India"],
    )
    article = make_article(
        "22222222-2222-2222-2222-222222222222",
        title="SAIL appoints interim leadership",
        body="SAIL announced an interim CMD after a leadership change in India.",
        country=["India"],
    )

    ops = safe_normalize_operations(operation_raw(article, existing), [article], [existing])
    assert ops[0]["action"] == "noop"
    assert ops[0]["safety_reason"] == "geography_or_project_mismatch"


def test_unsupported_duration_claim_is_blocked():
    article = make_article(
        "33333333-3333-3333-3333-333333333333",
        title="India imposes safeguard duty",
        body="India decided to impose a 12% safeguard duty on certain flat steel products.",
        country=["India"],
    )
    raw = {
        "operations": [{
            "action": "create",
            "matched_existing_key": None,
            "insight_key": "india|steel|safeguard-duty|2026",
            "insight": "India safeguard duty",
            "company": "Indian steel sector",
            "country": ["India"],
            "theme": ["Policy/Tariff"],
            "event_type": "Policy Change",
            "importance": "High",
            "confidence": "Medium",
            "key_facts": "India imposed a 12% safeguard duty as a three-year measure.",
            "what_changed": "A 12% duty was introduced for three years.",
            "business_implication": "Supports domestic pricing.",
            "watch_items": "Official notification and product scope.",
            "article_refs": [article.ref()],
        }]
    }

    ops = safe_normalize_operations(raw, [article], [])
    assert ops[0]["action"] == "noop"
    assert "three-year" in ops[0]["safety_reason"]


def test_valid_decimal_capacity_claim_remains_allowed():
    article = make_article(
        "44444444-4444-4444-4444-444444444444",
        title="Tata Steel commissions EAF in Ludhiana",
        body="Tata Steel inaugurated a scrap-based EAF in Ludhiana with capacity of 0.75 Mtpa and investment of Rs 3,200 crore in India.",
        country=["India"],
    )
    raw = {
        "operations": [{
            "action": "create",
            "matched_existing_key": None,
            "insight_key": "tata-steel|ludhiana|eaf-scrap|2026",
            "insight": "Tata Steel commissions 0.75 Mtpa Ludhiana EAF",
            "company": "Tata Steel",
            "country": ["India"],
            "theme": ["EAF/Green Steel"],
            "event_type": "New Plant",
            "importance": "High",
            "confidence": "High",
            "key_facts": "Tata Steel commissioned a 0.75 Mtpa scrap-based EAF with Rs 3,200 crore investment.",
            "what_changed": "The 0.75 Mtpa facility was commissioned.",
            "business_implication": "Adds scrap-based steel capacity.",
            "watch_items": "Ramp-up and scrap sourcing.",
            "article_refs": [article.ref()],
        }]
    }

    ops = safe_normalize_operations(raw, [article], [])
    assert ops[0]["action"] == "create"


def test_management_commentary_other_event_defaults_to_noop():
    article = make_article(
        "55555555-5555-5555-5555-555555555555",
        title="Tata Steel CEO highlights critical minerals risks",
        body="Tata Steel CEO said critical-minerals dependence creates layered supply vulnerabilities for the industry in India.",
        country=["India"],
    )
    raw = {
        "operations": [{
            "action": "create",
            "matched_existing_key": None,
            "insight_key": "tata-steel|india|critical-minerals-commentary|2026",
            "insight": "Tata Steel highlights critical minerals risks",
            "company": "Tata Steel",
            "country": ["India"],
            "theme": ["Raw Materials"],
            "event_type": "Other",
            "importance": "Medium",
            "confidence": "Medium",
            "key_facts": "Management highlighted critical-minerals supply vulnerability.",
            "what_changed": "Management articulated a broader risk framing.",
            "business_implication": "May influence sourcing strategy.",
            "watch_items": "Any concrete sourcing contracts or investments.",
            "article_refs": [article.ref()],
        }]
    }

    ops = safe_normalize_operations(raw, [article], [])
    assert ops[0]["action"] == "noop"
    assert ops[0]["safety_reason"] == "non_durable_other_event"


def test_update_properties_preserve_identity_and_accumulate_facts():
    existing = make_insight(
        key="jsw-steel|india|capacity-strategy|2026",
        title="JSW India capacity strategy",
        company="JSW Steel",
        country=["India"],
    )
    operation = {
        "action": "update",
        "insight_key": existing.insight_key,
        "insight": "Replacement title that must not be used",
        "company": "Different company",
        "country": ["India", "United States"],
        "theme": ["Financials"],
        "event_type": "Financial Update",
        "importance": "Medium",
        "confidence": "Medium",
        "key_facts": "FY26 Indian operations reached a new production milestone.",
        "what_changed": "New production evidence was added.",
        "business_implication": "Scale-up remains on track.",
        "watch_items": "Next expansion milestone.",
        "article_refs": [{"source": "general", "page_id": "66666666-6666-6666-6666-666666666666", "published_at": "2026-08-27"}],
    }

    props = safe_properties_for_operation(operation, "gpt-5-mini", existing)
    assert props["Insight"]["title"][0]["text"]["content"] == existing.insight
    assert props["Company"]["rich_text"][0]["text"]["content"] == existing.company
    assert [x["name"] for x in props["Country"]["multi_select"]] == ["India"]
    assert props["Event Type"]["select"]["name"] == existing.event_type
    fact_text = props["Key Facts"]["rich_text"][0]["text"]["content"]
    assert "Historical core fact." in fact_text
    assert "FY26 Indian operations reached a new production milestone." in fact_text
    watch_text = props["Watch Items"]["rich_text"][0]["text"]["content"]
    assert "Previous watch item." in watch_text
    assert "Next expansion milestone." in watch_text
    assert props["Importance"]["select"]["name"] == "High"
