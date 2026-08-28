from __future__ import annotations

from src.intelligence_pipeline import Article
from src.intelligence_safety import safe_normalize_operations


def _article() -> Article:
    return Article(
        source="general",
        page_id="348dec27-c9aa-81bd-b78c-eab51311515d",
        title="Vietnam’s Vnsteel acquires environmental approval for galvanized steel capacity increase",
        published_at="2026-04-20T09:35:00.000+00:00",
        importance_score=4.0,
        source_name="SteelOrbis",
        country=["Vietnam"],
        tags=["Vietnam"],
        body=(
            "Vietnamese steel producer Vingal Industrial Galvanizing JSC gained environmental approval "
            "from Dong Nai province for its Bien Hoa II facility. The project will raise total galvanized "
            "steel capacity from 40,000 mt/year to 60,000 mt/year."
        ),
        notion_url="",
    )


def _raw(article: Article, *, claim_date: str) -> dict:
    return {
        "operations": [{
            "action": "create",
            "matched_existing_key": None,
            "insight_key": "vingal|vietnam|galvanized_capacity_increase|apr2026",
            "insight": "Vingal obtains environmental approval for galvanized capacity increase",
            "company": "Vingal Industrial Galvanizing JSC (Vnsteel)",
            "country": ["Vietnam"],
            "theme": ["Capacity Expansion"],
            "event_type": "Capacity Expansion",
            "importance": "Medium",
            "confidence": "High",
            "key_facts": (
                "Dong Nai granted environmental approval for Vingal's capacity-increase project, "
                f"raising galvanized steel capacity from 40,000 mt/year to 60,000 mt/year (article date {claim_date})."
            ),
            "what_changed": (
                "Environmental approval was obtained and permitted galvanized capacity increased "
                "from 40,000 mt/year to 60,000 mt/year."
            ),
            "business_implication": "Adds local galvanized supply capacity.",
            "watch_items": "Construction and commissioning timeline.",
            "article_refs": [article.ref()],
        }]
    }


def test_iso_timestamp_grounds_same_calendar_date_claim():
    article = _article()
    ops = safe_normalize_operations(_raw(article, claim_date="2026-04-20"), [article], [])
    assert ops[0]["action"] == "create"


def test_iso_timestamp_does_not_ground_different_calendar_date_claim():
    article = _article()
    ops = safe_normalize_operations(_raw(article, claim_date="2026-04-21"), [article], [])
    assert ops[0]["action"] == "noop"
    assert "21" in ops[0]["safety_reason"]
