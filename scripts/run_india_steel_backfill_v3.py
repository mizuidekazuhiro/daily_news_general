from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Apply the shared Intelligence safety layer before v2 imports function references
# from src.intelligence_pipeline. This ensures the historical backfill and daily
# runner use exactly the same update/create guards.
from src.intelligence_safety import apply_safety_patch

apply_safety_patch()

# Business-judgment rules are loaded from Notion at prompt/normalization time.
# Apply this after the safety patch so policy wraps (rather than replaces) safety.
from src.intelligence_policy import apply_policy_patch

apply_policy_patch()

# Persist CREATE/UPDATE/NOOP classifications before v2 imports apply_operations,
# so its runtime reference includes the source-article processed marker.
from src.intelligence_processing import (
    apply_processing_patch,
    filter_intelligence_entry_candidates,
    filter_unprocessed_articles,
    intelligence_entry_floor,
)

apply_processing_patch()

from scripts import run_india_steel_backfill_v2 as v2
from src.intelligence_pipeline import Article, NotionClient

INDIA_EVIDENCE_RE = re.compile(r"(?:\bindia\b|\bindian\b|インド)", re.IGNORECASE)
_original_load_general = v2.load_general
_original_load_nikkei = v2.load_nikkei
_original_generate_operations = v2.generate_operations


def explicit_india_evidence_v3(article: Article) -> bool:
    """Require India as a real word; avoid false matches such as Indiana."""
    if any(tag in v2.INDIA_STEEL_LABELS for tag in article.tags):
        return True
    text = f"{article.title}\n{article.body[:3000]}"
    return bool(INDIA_EVIDENCE_RE.search(text))


def load_general_v3(
    notion: NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[Article]:
    """Apply structural entry exception, India evidence, processed filter and dedup."""
    articles = _original_load_general(
        notion,
        db_id,
        cutoff,
        intelligence_entry_floor(min_score),
        body_chars,
    )
    articles = filter_intelligence_entry_candidates(articles, min_score)
    articles = filter_unprocessed_articles(notion, db_id, articles)
    filtered = [a for a in articles if explicit_india_evidence_v3(a)]

    # Keep the highest-scored/newest row when the same story was ingested more than once.
    filtered.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)
    seen_titles: set[str] = set()
    deduped: list[Article] = []
    for article in filtered:
        key = re.sub(r"\s+", " ", article.title).strip().casefold()
        if key and key in seen_titles:
            continue
        if key:
            seen_titles.add(key)
        deduped.append(article)
    return deduped


def load_nikkei_v3(
    notion: NotionClient,
    db_id: str,
    cutoff: Any,
    min_score: float,
    body_chars: int,
) -> list[Article]:
    articles = _original_load_nikkei(
        notion,
        db_id,
        cutoff,
        intelligence_entry_floor(min_score),
        body_chars,
    )
    articles = filter_intelligence_entry_candidates(articles, min_score)
    return filter_unprocessed_articles(notion, db_id, articles)


def expand_short_refs_v3(raw: Any, ref_map: dict[str, Article]) -> Any:
    """Expand A01-style refs even when GPT puts them in page_id."""
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        return raw

    fixed = {**raw, "operations": []}
    for original in raw["operations"]:
        if not isinstance(original, dict):
            continue

        item = dict(original)
        expanded: list[dict[str, str]] = []
        seen: set[str] = set()

        for ref in item.get("article_refs") or []:
            short = ""
            full_ref: dict[str, str] | None = None

            if isinstance(ref, str):
                short = ref.strip()
            elif isinstance(ref, dict):
                short = str(ref.get("article_ref") or ref.get("ref") or "").strip()
                page_id = str(ref.get("page_id") or "").strip()
                if not short and page_id in ref_map:
                    short = page_id
                elif not short and page_id:
                    full_ref = ref

            article = ref_map.get(short)
            if article and short not in seen:
                seen.add(short)
                expanded.append(article.ref())
            elif full_ref is not None:
                expanded.append(full_ref)

        item["article_refs"] = expanded
        fixed["operations"].append(item)

    return fixed


def generate_operations_resilient(
    client: Any,
    *,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Any],
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    """Fall back to per-article classification if a whole GPT batch is unusable.

    This keeps a single malformed/ambiguous article from terminating a historical run.
    Articles that still fail individually are recorded as explicit noops with a
    classification_error marker in the raw log so the rest of the backfill can continue.
    """
    try:
        return _original_generate_operations(
            client,
            model=model,
            max_output_tokens=max_output_tokens,
            batch=batch,
            existing=existing,
        )
    except RuntimeError as exc:
        if "no valid Intelligence operations" not in str(exc):
            raise

    combined_operations: list[dict[str, Any]] = []
    raw_items: list[dict[str, Any]] = []
    failed_refs: list[str] = []

    for article in batch:
        try:
            raw, operations, prompt_payload = _original_generate_operations(
                client,
                model=model,
                max_output_tokens=max_output_tokens,
                batch=[article],
                existing=existing,
            )
            combined_operations.extend(operations)
            raw_items.append({
                "article_ref": article.page_id,
                "status": "classified",
                "raw_output": raw,
                "prompt": prompt_payload,
            })
        except RuntimeError as single_exc:
            if "no valid Intelligence operations" not in str(single_exc):
                raise
            failed_refs.append(article.page_id)
            combined_operations.append({
                "action": "noop",
                "article_refs": [article.ref()],
                "classification_error": "GPT returned no valid Intelligence operation after batch and single-article retries",
            })
            raw_items.append({
                "article_ref": article.page_id,
                "status": "classification_failed",
                "error": str(single_exc),
            })

    combined_operations = v2.coalesce_operations(combined_operations)
    combined_operations = v2.add_uncovered_noops(combined_operations, batch)
    raw = {
        "fallback": "per_article_after_batch_failure",
        "failed_article_refs": failed_refs,
        "items": raw_items,
    }
    prompt_payload = {
        "fallback": "per_article_after_batch_failure",
        "article_count": len(batch),
        "failed_article_refs": failed_refs,
        "new_articles": [article.to_prompt() for article in batch],
    }
    return raw, combined_operations, prompt_payload


# v2 resolves these globals at runtime. Patch only the backfill-specific behavior.
v2.expand_short_refs = expand_short_refs_v3
v2.explicit_india_evidence = explicit_india_evidence_v3
v2.load_general = load_general_v3
v2.load_nikkei = load_nikkei_v3
v2.generate_operations = generate_operations_resilient


if __name__ == "__main__":
    raise SystemExit(v2.main())
