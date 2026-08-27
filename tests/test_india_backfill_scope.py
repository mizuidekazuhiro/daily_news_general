from __future__ import annotations

from scripts.run_india_steel_backfill_v3 import explicit_india_evidence_v3
from src.intelligence_pipeline import Article


def article(title: str, body: str, *, tags: list[str] | None = None) -> Article:
    return Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title=title,
        published_at="2026-04-01",
        importance_score=4.0,
        source_name="Test",
        country=[],
        tags=tags or [],
        body=body,
        notion_url="",
    )


def test_indian_owner_wording_does_not_make_ijmuiden_an_india_article():
    item = article(
        "Dutch MPs back Tata Steel IJmuiden support package",
        "The agreement was reached with Tata Steel's Indian owners. The subsidy applies to the IJmuiden plant in the Netherlands.",
        tags=["Tata Steel"],
    )
    assert explicit_india_evidence_v3(item) is False


def test_india_operations_capacity_context_is_in_scope():
    item = article(
        "Tata Steel expands India capacity",
        "Tata Steel plans to increase India steelmaking capacity and investment across its India operations.",
    )
    assert explicit_india_evidence_v3(item) is True


def test_named_indian_steel_location_is_in_scope_without_generic_india_word():
    item = article(
        "JFE invests in JSW Kalinga joint venture",
        "The partnership includes JSW Sambalpur Steel and establishes joint control over the Kalinganagar-linked steel business.",
        tags=["JSW Steel India"],
    )
    assert explicit_india_evidence_v3(item) is True


def test_cross_border_project_with_explicit_india_supply_link_is_in_scope():
    item = article(
        "SAIL and Krakatau study stainless slab project",
        "The proposed Indonesia plant would supply stainless slabs to SAIL's Salem Steel Plant in India.",
        tags=["SAIL"],
    )
    assert explicit_india_evidence_v3(item) is True
