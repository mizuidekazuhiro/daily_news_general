from __future__ import annotations

import logging
import os
import re
from typing import Any

import src.intelligence_pipeline as pipeline


_ORIGINAL_LOAD_NIKKEI = pipeline._load_nikkei_articles
_ORIGINAL_LOAD_GENERAL = pipeline._load_general_articles
_ORIGINAL_LOAD_EXISTING = pipeline._load_existing_insights
_ORIGINAL_APPLY_OPERATIONS = pipeline.apply_operations
_PATCHED = False
_LINKED_MIGRATION_DONE = False

# Source-importance scoring is intentionally broader and can underrate durable
# Intelligence events (for example a binding 50:50 steel JV can arrive as a
# stock-market article with ImportanceScore 2.5).  Let a narrow set of clearly
# structural headlines through to the Intelligence policy layer, which remains
# responsible for CREATE/UPDATE/NOOP.
STRUCTURAL_ENTRY_SCORE_FLOOR = 2.0
STRUCTURAL_ENTRY_PATTERNS = (
    r"\bjoint venture\b",
    r"\b50\s*:\s*50\b.*\bjv\b",
    r"\bjv\b.*\b(?:steel|plant|facility|company)\b",
    r"\bacquir(?:e|es|ed|ing|ition)\b",
    r"\bmerger\b|\bmerge(?:s|d)?\b|\btakeover\b",
    r"\bcommission(?:s|ed|ing)?\b|\binaugurat(?:e|es|ed|ion)\b",
    r"\bgreenfield\b|\bbrownfield\b",
    r"\bcapacity\b.*\b(?:mtpa|million tonnes|million tons|tpa|ktpa)\b",
    r"\b(?:mtpa|million tonnes|million tons|tpa|ktpa)\b.*\bcapacity\b",
    r"\belectric arc furnace\b|\beaf\b|\bblast furnace\b",
    r"\bpreferred bidder\b|\bmining block\b|\biron[- ]ore block\b|\bcoal block\b",
    r"\bsafeguard duty\b|\banti[- ]dumping\b|\bcountervailing duty\b",
    r"\b(?:government|cabinet|regulator|commission)\b.*\b(?:approv(?:e|es|ed|al)|notification|policy)\b",
    r"\b(?:epc|engineering)\b.*\b(?:contract|order|award)\b",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_dry_run() -> bool:
    return _env_bool("INTELLIGENCE_DRY_RUN") or _env_bool("INDIA_STEEL_BACKFILL_DRY_RUN")


def intelligence_entry_floor(min_score: float) -> float:
    return min(float(min_score), STRUCTURAL_ENTRY_SCORE_FLOOR)


def is_structural_entry_candidate(article: pipeline.Article) -> bool:
    haystack = " | ".join([
        str(article.title or ""),
        " ".join(str(tag or "") for tag in article.tags),
    ]).casefold()
    return any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in STRUCTURAL_ENTRY_PATTERNS)


def filter_intelligence_entry_candidates(
    articles: list[pipeline.Article],
    min_score: float,
) -> list[pipeline.Article]:
    """Keep normal high-score items plus low-score, clearly structural events."""
    threshold = float(min_score)
    return [
        article
        for article in articles
        if article.importance_score >= threshold
        or (
            article.importance_score >= STRUCTURAL_ENTRY_SCORE_FLOOR
            and is_structural_entry_candidate(article)
        )
    ]


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


def mark_page_ids_processed(
    notion: pipeline.NotionClient,
    page_ids: list[str],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    if dry_run:
        return []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_page_id in page_ids:
        page_id = pipeline._hyphenate_id(raw_page_id)
        key = pipeline._clean_id(page_id)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            notion.update_page(page_id, {"Intelligence Processed": {"checkbox": True}})
        except Exception as exc:
            logging.exception("intelligence_mark_processed_failed page_id=%s", page_id)
            errors.append({"page_id": page_id, "error": f"{type(exc).__name__}: {exc}"})
    return errors


def _load_existing_and_migrate_linked(
    notion: pipeline.NotionClient,
    db_id: str,
    max_existing: int,
) -> list[pipeline.Insight]:
    """One-time per process: mark currently linked source pages as classified.

    Existing Tracking Insights are already accepted Intelligence decisions. Their
    source pages should not be fetched and parsed again before the linked-ID check.
    Closed rows are excluded by the underlying loader and therefore do not seed
    this migration.
    """
    global _LINKED_MIGRATION_DONE
    existing = _ORIGINAL_LOAD_EXISTING(notion, db_id, max_existing)
    if _LINKED_MIGRATION_DONE or _is_dry_run():
        return existing

    page_ids: list[str] = []
    for item in existing:
        page_ids.extend(item.nikkei_sources)
        page_ids.extend(item.general_sources)
    errors = mark_page_ids_processed(notion, page_ids, dry_run=False)
    if errors:
        raise RuntimeError(f"Failed to migrate {len(errors)} linked Intelligence source markers")
    _LINKED_MIGRATION_DONE = True
    logging.info("intelligence_linked_sources_marked_processed count=%s", len(set(map(pipeline._clean_id, page_ids))))
    return existing


def _load_nikkei_unprocessed(
    notion: pipeline.NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[pipeline.Article]:
    articles = _ORIGINAL_LOAD_NIKKEI(
        notion,
        db_id,
        cutoff,
        intelligence_entry_floor(min_score),
        body_chars,
    )
    articles = filter_intelligence_entry_candidates(articles, min_score)
    return filter_unprocessed_articles(notion, db_id, articles)


def _load_general_unprocessed(
    notion: pipeline.NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[pipeline.Article]:
    articles = _ORIGINAL_LOAD_GENERAL(
        notion,
        db_id,
        cutoff,
        intelligence_entry_floor(min_score),
        body_chars,
    )
    articles = filter_intelligence_entry_candidates(articles, min_score)
    return filter_unprocessed_articles(notion, db_id, articles)


def mark_applied_articles_processed(
    notion: pipeline.NotionClient,
    result: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    """Persist successful CREATE/UPDATE/NOOP classifications on source pages."""
    page_ids: list[str] = []
    for applied in result.get("applied") or []:
        for ref in applied.get("article_refs") or []:
            page_ids.append(str(ref.get("page_id") or ""))
    return mark_page_ids_processed(notion, page_ids, dry_run=dry_run)


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
    pipeline._load_existing_insights = _load_existing_and_migrate_linked
    pipeline._load_nikkei_articles = _load_nikkei_unprocessed
    pipeline._load_general_articles = _load_general_unprocessed
    pipeline.apply_operations = processing_apply_operations
    _PATCHED = True
