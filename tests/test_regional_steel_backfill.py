from __future__ import annotations

from scripts.run_regional_steel_backfill import explicit_region_evidence, get_region_profile
from src.intelligence_pipeline import Article


def _article(title: str, body: str, country: list[str] | None = None) -> Article:
    return Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title=title,
        published_at="2026-08-27",
        importance_score=5.0,
        source_name="test",
        country=country or [],
        tags=["Steel"],
        body=body,
        notion_url="",
    )


def test_japan_scope_accepts_domestic_steel_project():
    profile = get_region_profile("Japan")
    article = _article(
        "JFE Steel upgrades Kurashiki works",
        "JFE Steel will invest in new equipment at its Kurashiki district in Japan to improve steelmaking capacity.",
        ["Japan"],
    )
    assert explicit_region_evidence(article, profile)


def test_japan_scope_does_not_match_nippon_steel_name_alone():
    profile = get_region_profile("Japan")
    article = _article(
        "Nippon Steel advances overseas expansion",
        "Nippon Steel announced an investment at an integrated steel plant in India. The project concerns Indian operations only.",
        ["India", "Japan"],
    )
    assert not explicit_region_evidence(article, profile)


def test_japan_scope_does_not_match_japanese_company_name_alone():
    profile = get_region_profile("Japan")
    article = _article(
        "日本製鉄、インド製鉄所への投資を拡大",
        "インド国内の製鉄所で能力増強を実施する。現地の生産能力と設備投資を拡大する計画だ。",
        ["India", "Japan"],
    )
    assert not explicit_region_evidence(article, profile)


def test_india_profile_still_rejects_indian_owner_nationality_only():
    profile = get_region_profile("India")
    article = _article(
        "Tata Steel IJmuiden support package",
        "The Netherlands package was agreed with Tata's Indian owners and applies to the IJmuiden plant in Europe.",
        ["India", "EU"],
    )
    assert not explicit_region_evidence(article, profile)
