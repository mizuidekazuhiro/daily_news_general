from __future__ import annotations

import src.intelligence_pipeline as pipeline
from src import intelligence_policy


def _article() -> pipeline.Article:
    return pipeline.Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title="SAIL & Krakatau Steel form JV for new stainless steel slab plant in Indonesia",
        published_at="2026-07-09",
        importance_score=3.0,
        source_name="test",
        country=["India", "Indonesia"],
        tags=["JV/M&A"],
        body=(
            "Market roundup. Indian steelmakers call for controls on imports. "
            "Nickel prices stabilize. No substantive announcement of a signed JV agreement appears in the article body."
        ),
        notion_url="",
    )


def _existing() -> pipeline.Insight:
    return pipeline.Insight(
        page_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        insight="SAIL / PT Krakatau Steel — proposed Indonesia stainless-slab JV under MoU",
        insight_key="sail|indonesia|stainless-slab-proposed-jv|2026",
        status="Tracking",
        importance="High",
        confidence="Medium",
        company="Steel Authority of India Limited (SAIL) / PT Krakatau Steel",
        country=["India", "Indonesia"],
        theme=["JV/M&A", "Raw Materials"],
        event_type="JV/M&A",
        key_facts="SAIL and Krakatau Steel signed an MoU to explore a proposed JV.",
        what_changed="MoU signed; feasibility work pending.",
        business_implication="Binding terms remain unconfirmed.",
        watch_items="JV agreement, capacity, capex and approvals.",
        first_seen="2026-07-08",
        last_updated="2026-07-08",
        last_processed="2026-07-08",
        nikkei_sources=[],
        general_sources=[],
        source_count=0,
        model="gpt-5-mini",
    )


def test_policy_integrity_path_blocks_headline_only_jv_status_upgrade():
    article = _article()
    existing = _existing()
    raw = {
        "operations": [{
            "action": "update",
            "matched_existing_key": existing.insight_key,
            "insight_key": existing.insight_key,
            "insight": existing.insight,
            "company": existing.company,
            "country": existing.country,
            "theme": existing.theme,
            "event_type": existing.event_type,
            "importance": "High",
            "confidence": "Low",
            "key_facts": "SAIL and PT Krakatau Steel have formed a joint venture for a stainless-steel slab plant in Indonesia.",
            "what_changed": "The proposed JV was formed.",
            "business_implication": "Binding JV status would increase execution visibility.",
            "watch_items": "Obtain the signed JV agreement.",
            "article_refs": [article.ref()],
        }]
    }

    ops = intelligence_policy._integrity_normalize_operations(raw, [article], [existing])

    assert len(ops) == 1
    assert ops[0]["action"] == "noop"
    assert ops[0]["safety_reason"] == "headline_only_status_upgrade"
