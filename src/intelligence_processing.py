from __future__ import annotations

import logging
from typing import Any

import src.intelligence_pipeline as pipeline


_ORIGINAL_LOAD_NIKKEI = pipeline._load_nikkei_articles
_ORIGINAL_LOAD_GENERAL = pipeline._load_general_articles
_ORIGINAL_APPLY_OPERATIONS = pipeline.apply_operations
_PATCHED = False


def processed_article_ids(notion: pipeline.NotionClient, database_id: str) -> set[str]:
    """Return page IDs already classified by the Intelligence pipeline.

    `Intelligence Processed` is deliberately user-resettable in Notion. Turning
    it off is the explicit way to re-evaluate an article after a major policy
    change; normal reruns do not repeatedly spend model calls on stable NOOPs.
    """
    rows = notion.query_database(
        database_id,
        filter_obj={"property": "Intelligence Processed", "checkbox": {"equals": True}},
        max_pages=100,
    )
    return {
        pipeline._clean_id(str(row.get("id") or ""))
        for row in rows
        if row.get("id")
    }


def filter_unprocessed_articles(
    notion: pipeline.NotionClient,
    database_id: str,
    articles: list[pipeline.Article],
) -> list[pipeline.Article]:
    if not articles:
        return []
    processed = processed_article_ids(notion, database_id)
    return [article for article in articles if pipeline._clean_id(article.page_id) not in processed]


def _load_nikkei_unprocessed(
    notion: pipeline.NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[pipeline.Article]:
    articles = _ORIGINAL_LOAD_NIKKEI(notion, db_id, cutoff, min_score, body_chars)
    return filter_unprocessed_articles(notion, db_id, articles)


def _load_general_unprocessed(
    notion: pipeline.NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[pipeline.Article]:
    articles = _ORIGINAL_LOAD_GENERAL(notion, db_id, cutoff, min_score, body_chars)
    return filter_unprocessed_articles(notion, db_id, articles)


def mark_applied_articles_processed(
    notion: pipeline.NotionClient,
    result: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    """Persist successful CREATE/UPDATE/NOOP classifications on source pages."""
    if dry_run:
        return []

    page_ids: list[str] = []
    seen: set[str] = set()
    for applied in result.get("applied") or []:
        for ref in applied.get("article_refs") or []:
            page_id = pipeline._hyphenate_id(str(ref.get("page_id") or ""))
            key = pipeline._clean_id(page_id)
            if key and key not in seen:
                seen.add(key)
                page_ids.append(page_id)

    errors: list[dict[str, str]] = []
    for page_id in page_ids:
        try:
            notion.update_page(
                page_id,
                {"Intelligence Processed": {"checkbox": True}},
            )
        except Exception as exc:
            logging.exception("intelligence_mark_processed_failed page_id=%s", page_id)
            errors.append({
                "page_id": page_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return errors


def processing_apply_operations(
    notion: pipeline.NotionClient,
    intelligence_db_id: str,
    operations: list[dict[str, Any]],
    existing: list[pipeline.Insight],
    model: str,
    dry_run: bool,
) -> dict[str, Any]:
    result = _ORIGINAL_APPLY_OPERATIONS(
        notion,
        intelligence_db_id,
        operations,
        existing,
        model,
        dry_run,
    )
    marker_errors = mark_applied_articles_processed(notion, result, dry_run=dry_run)
    result["processing_mark_errors"] = marker_errors
    if marker_errors:
        result.setdefault("errors", []).extend(
            {"action": "mark_processed", "insight_key": "", "error": item["error"]}
            for item in marker_errors
        )
    return result


def apply_processing_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    pipeline._load_nikkei_articles = _load_nikkei_unprocessed
    pipeline._load_general_articles = _load_general_unprocessed
    pipeline.apply_operations = processing_apply_operations
    _PATCHED = True
