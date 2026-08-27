from __future__ import annotations

from src.intelligence_pipeline import Article
from src.intelligence_processing import (
    filter_intelligence_entry_candidates,
    filter_region_unprocessed_articles,
    filter_unprocessed_articles,
    intelligence_entry_floor,
    mark_applied_articles_processed,
    mark_page_ids_region_processed,
)


class FakeNotion:
    def __init__(self, processed_ids=None):
        self.processed_ids = processed_ids or []
        self.updated = []

    def query_database(self, database_id, filter_obj=None, max_pages=30, **kwargs):
        assert filter_obj == {"property": "Intelligence Processed", "checkbox": {"equals": True}}
        return [{"id": page_id} for page_id in self.processed_ids]

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))
        return {"id": page_id}


class FakeRegionalNotion:
    def __init__(self, region_rows=None):
        self.region_rows = region_rows or []
        self.updated = []

    def query_database(self, database_id, filter_obj=None, max_pages=30, **kwargs):
        multi = (filter_obj or {}).get("multi_select") or {}
        if "contains" in multi:
            region = multi["contains"]
            out = []
            for row in self.region_rows:
                names = [x["name"] for x in row.get("properties", {}).get("Intelligence Regions Processed", {}).get("multi_select", [])]
                if region in names:
                    out.append(row)
            return out
        if multi.get("is_not_empty") is True:
            return list(self.region_rows)
        raise AssertionError(filter_obj)

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))
        return {"id": page_id}


def _region_row(page_id: str, regions: list[str]):
    return {
        "id": page_id,
        "properties": {
            "Intelligence Regions Processed": {
                "multi_select": [{"name": value} for value in regions],
            }
        },
    }


def make_article(
    page_id: str,
    *,
    title: str = "Test article",
    score: float = 8.0,
    tags: list[str] | None = None,
) -> Article:
    return Article(
        source="general",
        page_id=page_id,
        title=title,
        published_at="2026-08-27",
        importance_score=score,
        source_name="test",
        country=["India"],
        tags=tags or [],
        body="A sufficiently long test body about a material Indian steel project and its investment milestone.",
        notion_url="",
    )


def test_filter_unprocessed_articles_excludes_persisted_noops():
    notion = FakeNotion(processed_ids=["11111111-1111-1111-1111-111111111111"])
    articles = [
        make_article("11111111-1111-1111-1111-111111111111"),
        make_article("22222222-2222-2222-2222-222222222222"),
    ]
    filtered = filter_unprocessed_articles(notion, "db", articles)
    assert [x.page_id for x in filtered] == ["22222222-2222-2222-2222-222222222222"]


def test_region_processing_ignores_global_processed_state_from_other_scope():
    article_india = make_article("11111111-1111-1111-1111-111111111111")
    article_japan = make_article("22222222-2222-2222-2222-222222222222")
    notion = FakeRegionalNotion(region_rows=[
        _region_row(article_india.page_id, ["India"]),
        _region_row(article_japan.page_id, ["Japan"]),
    ])
    filtered = filter_region_unprocessed_articles(
        notion, "db", [article_india, article_japan], "Japan",
    )
    assert [x.page_id for x in filtered] == [article_india.page_id]


def test_mark_region_processed_appends_without_overwriting_other_regions():
    page_id = "11111111-1111-1111-1111-111111111111"
    notion = FakeRegionalNotion(region_rows=[_region_row(page_id, ["India"])])
    errors = mark_page_ids_region_processed(
        notion, "db", [page_id], region="Japan", dry_run=False,
    )
    assert errors == []
    assert notion.updated == [
        (
            page_id,
            {"Intelligence Regions Processed": {"multi_select": [{"name": "India"}, {"name": "Japan"}]}},
        )
    ]


def test_mark_applied_articles_processed_marks_noop_and_success_once():
    notion = FakeNotion()
    result = {
        "applied": [
            {"action": "noop", "article_refs": [{"source": "general", "page_id": "11111111-1111-1111-1111-111111111111"}]},
            {"action": "create", "article_refs": [
                {"source": "general", "page_id": "22222222-2222-2222-2222-222222222222"},
                {"source": "general", "page_id": "11111111-1111-1111-1111-111111111111"},
            ]},
        ]
    }
    errors = mark_applied_articles_processed(notion, result, dry_run=False)
    assert errors == []
    assert [x[0] for x in notion.updated] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    assert all(x[1] == {"Intelligence Processed": {"checkbox": True}} for x in notion.updated)


def test_dry_run_never_marks_sources_processed():
    notion = FakeNotion()
    result = {"applied": [{"action": "noop", "article_refs": [{"source": "general", "page_id": "11111111-1111-1111-1111-111111111111"}]}]}
    errors = mark_applied_articles_processed(notion, result, dry_run=True)
    assert errors == []
    assert notion.updated == []


def test_structural_entry_allows_low_score_binding_joint_venture():
    article = make_article(
        "33333333-3333-3333-3333-333333333333",
        title="JSW Steel, Japan's JFE Receive CCI Nod For BPSL Joint Venture To Boost Steel Output",
        score=2.5,
        tags=["JSW Steel India", "Steel"],
    )
    assert filter_intelligence_entry_candidates([article], 4.0) == [article]


def test_low_score_generic_stock_article_stays_out():
    article = make_article(
        "44444444-4444-4444-4444-444444444444",
        title="JSW Steel shares rise 2% in afternoon trade",
        score=2.5,
        tags=["JSW Steel India", "Steel"],
    )
    assert filter_intelligence_entry_candidates([article], 4.0) == []


def test_structural_exception_has_hard_floor():
    article = make_article(
        "55555555-5555-5555-5555-555555555555",
        title="Company announces joint venture for steel facility",
        score=1.5,
        tags=["Steel"],
    )
    assert intelligence_entry_floor(4.0) == 2.0
    assert filter_intelligence_entry_candidates([article], 4.0) == []
