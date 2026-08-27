from __future__ import annotations

import logging
from typing import Any

from src import intelligence_pipeline as pipeline

PROCESSED_PROPERTY = "Intelligence Processed"
RAW_DB_IDS = {
    pipeline._clean_id(pipeline.DEFAULT_NIKKEI_DB_ID),
    pipeline._clean_id(pipeline.DEFAULT_GENERAL_DB_ID),
}


class ResumableNotionClient(pipeline.NotionClient):
    """Notion client that excludes source articles already handled by Intelligence."""

    def query_database(
        self,
        database_id: str,
        filter_obj: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        max_pages: int = 30,
    ) -> list[dict[str, Any]]:
        if pipeline._clean_id(database_id) in RAW_DB_IDS:
            processed_filter = {
                "property": PROCESSED_PROPERTY,
                "checkbox": {"equals": False},
            }
            if isinstance(filter_obj, dict) and isinstance(filter_obj.get("and"), list):
                filter_obj = {"and": [*filter_obj["and"], processed_filter]}
            elif filter_obj:
                filter_obj = {"and": [filter_obj, processed_filter]}
            else:
                filter_obj = processed_filter
        return super().query_database(database_id, filter_obj, sorts, max_pages)


def add_uncovered_noops(
    operations: list[dict[str, Any]],
    candidates: list[pipeline.Article],
) -> list[dict[str, Any]]:
    """Persist a disposition for every source article whenever GPT produced any valid op."""
    if not operations:
        return []
    covered: set[str] = set()
    for operation in operations:
        for ref in operation.get("article_refs") or []:
            if isinstance(ref, dict):
                covered.add(pipeline._clean_id(str(ref.get("page_id") or "")))
    out = list(operations)
    for article in candidates:
        if pipeline._clean_id(article.page_id) not in covered:
            out.append({"action": "noop", "article_refs": [article.ref()]})
    return out


def normalize_operations_with_processing_state(
    raw: Any,
    candidates: list[pipeline.Article],
    existing: list[pipeline.Insight],
) -> list[dict[str, Any]]:
    operations = pipeline.normalize_operations(raw, candidates, existing)
    return add_uncovered_noops(operations, candidates)


def _operation_page_ids(operations: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        for ref in operation.get("article_refs") or []:
            if not isinstance(ref, dict):
                continue
            page_id = pipeline._hyphenate_id(str(ref.get("page_id") or ""))
            key = pipeline._clean_id(page_id)
            if key and key not in seen:
                seen.add(key)
                ids.append(page_id)
    return ids


def mark_operations_processed(
    notion: pipeline.NotionClient,
    operations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for page_id in _operation_page_ids(operations):
        try:
            notion.update_page(page_id, {PROCESSED_PROPERTY: {"checkbox": True}})
        except Exception as exc:
            logging.exception("intelligence_processed_marker_failed page_id=%s", page_id)
            errors.append({
                "action": "mark_processed",
                "page_id": page_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return errors


def apply_operations_with_processing_state(
    notion: pipeline.NotionClient,
    intelligence_db_id: str,
    operations: list[dict[str, Any]],
    existing: list[pipeline.Insight],
    model: str,
    dry_run: bool,
) -> dict[str, Any]:
    result = pipeline.apply_operations(
        notion,
        intelligence_db_id,
        operations,
        existing,
        model,
        dry_run,
    )
    if not dry_run and not result.get("errors"):
        marker_errors = mark_operations_processed(notion, operations)
        if marker_errors:
            result["errors"].extend(marker_errors)
    return result


def install_daily_processing_state() -> None:
    """Patch the regular daily pipeline before its main() is invoked."""
    original_normalize = pipeline.normalize_operations
    original_apply = pipeline.apply_operations

    class DailyResumableNotionClient(ResumableNotionClient):
        pass

    def normalize(raw: Any, candidates: list[pipeline.Article], existing: list[pipeline.Insight]) -> list[dict[str, Any]]:
        operations = original_normalize(raw, candidates, existing)
        return add_uncovered_noops(operations, candidates)

    def apply(
        notion: pipeline.NotionClient,
        intelligence_db_id: str,
        operations: list[dict[str, Any]],
        existing: list[pipeline.Insight],
        model: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        result = original_apply(notion, intelligence_db_id, operations, existing, model, dry_run)
        if not dry_run and not result.get("errors"):
            marker_errors = mark_operations_processed(notion, operations)
            if marker_errors:
                result["errors"].extend(marker_errors)
        return result

    pipeline.NotionClient = DailyResumableNotionClient
    pipeline.normalize_operations = normalize
    pipeline.apply_operations = apply
