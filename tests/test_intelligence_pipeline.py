from __future__ import annotations

from src.intelligence_pipeline import (
    Article,
    Insight,
    _properties_for_operation,
    normalize_operations,
    select_candidates,
)


def article(page_id: str, source: str = "general", score: float = 5.0, published: str = "2026-08-27") -> Article:
    return Article(
        source=source,
        page_id=page_id,
        title=f"Article {page_id}",
        published_at=published,
        importance_score=score,
        source_name="test",
        country=["India"],
        tags=["Capacity Expansion"],
        body="本文" * 100,
        notion_url="",
    )


def insight(
    key: str = "jsw-steel|india|capacity-strategy|2026",
    nikkei_sources: list[str] | None = None,
    general_sources: list[str] | None = None,
) -> Insight:
    return Insight(
        page_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        insight="Existing",
        insight_key=key,
        status="Tracking",
        importance="High",
        confidence="High",
        company="JSW Steel",
        country=["India"],
        theme=["Capacity Expansion"],
        event_type="Capacity Expansion",
        key_facts="old facts",
        what_changed="old change",
        business_implication="old implication",
        watch_items="old watch",
        first_seen="2026-01-01",
        last_updated="2026-07-01",
        last_processed="2026-07-01",
        nikkei_sources=nikkei_sources or [],
        general_sources=general_sources or [],
        source_count=len(nikkei_sources or []) + len(general_sources or []),
        model="gpt-5-mini",
    )


def test_select_candidates_excludes_already_linked_and_sorts():
    linked = "11111111-1111-1111-1111-111111111111"
    a1 = article(linked, score=10)
    a2 = article("22222222-2222-2222-2222-222222222222", score=4)
    a3 = article("33333333-3333-3333-3333-333333333333", score=8)

    selected, already_linked = select_candidates(
        [a1, a2, a3],
        [insight(general_sources=[linked])],
        max_candidates=10,
    )

    assert already_linked == 1
    assert [x.page_id for x in selected] == [a3.page_id, a2.page_id]


def test_normalize_update_requires_existing_key_and_exact_article_ref():
    a = article("22222222-2222-2222-2222-222222222222")
    existing = insight()
    raw = {
        "operations": [
            {
                "action": "update",
                "matched_existing_key": existing.insight_key,
                "insight_key": "do-not-use-new-key",
                "insight": "Updated",
                "company": "JSW Steel",
                "country": ["India", "INVALID"],
                "theme": ["Capacity Expansion", "INVALID"],
                "event_type": "Capacity Expansion",
                "importance": "High",
                "confidence": "High",
                "key_facts": "facts",
                "what_changed": "changed",
                "business_implication": "implication",
                "watch_items": "watch",
                "article_refs": [
                    {"source": "general", "page_id": a.page_id, "published_at": a.published_at},
                    {"source": "general", "page_id": "99999999-9999-9999-9999-999999999999", "published_at": a.published_at},
                ],
            }
        ]
    }

    ops = normalize_operations(raw, [a], [existing])
    assert len(ops) == 1
    assert ops[0]["action"] == "update"
    assert ops[0]["insight_key"] == existing.insight_key
    assert ops[0]["country"] == ["India"]
    assert ops[0]["theme"] == ["Capacity Expansion"]
    assert len(ops[0]["article_refs"]) == 1
    assert ops[0]["article_refs"][0]["page_id"] == a.page_id


def test_normalize_rejects_update_for_missing_existing_key():
    a = article("22222222-2222-2222-2222-222222222222")
    raw = {
        "operations": [
            {
                "action": "update",
                "matched_existing_key": "missing|key",
                "insight_key": "missing|key",
                "article_refs": [{"source": "general", "page_id": a.page_id}],
            }
        ]
    }
    assert normalize_operations(raw, [a], [insight()]) == []


def test_properties_merge_existing_source_relations_and_dates():
    old_nikkei = "11111111-1111-1111-1111-111111111111"
    new_general = "22222222-2222-2222-2222-222222222222"
    existing = insight(nikkei_sources=[old_nikkei])
    operation = {
        "action": "update",
        "insight_key": existing.insight_key,
        "insight": "Updated insight",
        "company": "JSW Steel",
        "country": ["India"],
        "theme": ["Capacity Expansion", "EAF/Green Steel"],
        "event_type": "Capacity Expansion",
        "importance": "High",
        "confidence": "High",
        "key_facts": "new facts",
        "what_changed": "new change",
        "business_implication": "new implication",
        "watch_items": "new watch",
        "article_refs": [
            {"source": "general", "page_id": new_general, "published_at": "2026-08-27"}
        ],
    }

    props = _properties_for_operation(operation, model="gpt-5-mini", existing=existing)

    assert props["First Seen"]["date"]["start"] == "2026-01-01"
    assert props["Last Updated"]["date"]["start"] == "2026-08-27"
    assert props["Source Count"]["number"] == 2
    assert [x["id"] for x in props["Nikkei Sources"]["relation"]] == [old_nikkei]
    assert [x["id"] for x in props["General Sources"]["relation"]] == [new_general]


def test_noop_drops_unknown_article_refs():
    a = article("22222222-2222-2222-2222-222222222222")
    raw = {
        "operations": [
            {
                "action": "noop",
                "article_refs": [
                    {"source": "general", "page_id": a.page_id},
                    {"source": "nikkei", "page_id": "99999999-9999-9999-9999-999999999999"},
                ],
            }
        ]
    }
    ops = normalize_operations(raw, [a], [])
    assert ops == [{"action": "noop", "article_refs": [a.ref()]}]
