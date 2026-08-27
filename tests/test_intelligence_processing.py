from __future__ import annotations

from src.intelligence_pipeline import Article
from src.intelligence_processing import filter_unprocessed_articles, mark_applied_articles_processed


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


def make_article(page_id: str) -> Article:
    return Article(
        source="general",
        page_id=page_id,
        title="Test article",
        published_at="2026-08-27",
        importance_score=8.0,
        source_name="test",
        country=["India"],
        tags=[],
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
