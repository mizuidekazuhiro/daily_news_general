from __future__ import annotations

import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.run_india_steel_backfill import (
    DEFAULT_GENERAL_DB_ID,
    DEFAULT_INTELLIGENCE_DB_ID,
    DEFAULT_NIKKEI_DB_ID,
    INDIA_MARKERS,
    INDIA_STEEL_LABELS,
    contains_any,
    env,
    env_bool,
    env_float,
    env_int,
    load_general,
    load_nikkei,
    today_jst,
    write_json,
)
from src.intelligence_pipeline import (
    Article,
    NotionClient,
    _already_linked_ids,
    _clean_id,
    _load_existing_insights,
    _prompt_system,
    apply_operations,
    normalize_operations,
)
from src.openai_json_client import OpenAIJsonClient


def explicit_india_evidence(article: Article) -> bool:
    # Do not trust PrimaryCountry/Country metadata alone: older rows contain broad false positives.
    if any(tag in INDIA_STEEL_LABELS for tag in article.tags):
        return True
    text = f"{article.title}\n{article.body[:3000]}"
    return contains_any(text, INDIA_MARKERS)


def prompt_article(article: Article, short_ref: str) -> dict[str, Any]:
    return {
        "article_ref": short_ref,
        "source": article.source,
        "title": article.title,
        "published_at": article.published_at,
        "importance_score": article.importance_score,
        "source_name": article.source_name,
        "country": article.country,
        "tags": article.tags,
        "body": article.body,
    }


def expand_short_refs(raw: Any, ref_map: dict[str, Article]) -> Any:
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
            if isinstance(ref, str):
                short = ref.strip()
            elif isinstance(ref, dict):
                short = str(ref.get("article_ref") or ref.get("ref") or "").strip()
                # Backward compatibility if the model still emitted a full page reference.
                if not short and ref.get("page_id"):
                    expanded.append(ref)
                    continue
            article = ref_map.get(short)
            if article and short not in seen:
                seen.add(short)
                expanded.append(article.ref())
        item["article_refs"] = expanded
        fixed["operations"].append(item)
    return fixed


def coalesce_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    noops: list[dict[str, Any]] = []
    for op in operations:
        if op.get("action") == "noop":
            noops.append(op)
            continue
        key = str(op.get("insight_key") or "").strip()
        if not key:
            continue
        if key not in keyed:
            keyed[key] = dict(op)
            continue
        previous = keyed[key]
        refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ref in [*(previous.get("article_refs") or []), *(op.get("article_refs") or [])]:
            token = (str(ref.get("source") or ""), _clean_id(str(ref.get("page_id") or "")))
            if token not in seen:
                seen.add(token)
                refs.append(ref)
        merged = dict(op)
        merged["article_refs"] = refs
        if previous.get("action") == "update" or op.get("action") == "update":
            merged["action"] = "update"
        keyed[key] = merged
    return [*keyed.values(), *noops]


def add_uncovered_noops(operations: list[dict[str, Any]], batch: list[Article]) -> list[dict[str, Any]]:
    covered: set[str] = set()
    for op in operations:
        for ref in op.get("article_refs") or []:
            covered.add(_clean_id(str(ref.get("page_id") or "")))
    out = list(operations)
    for article in batch:
        if _clean_id(article.page_id) not in covered:
            out.append({"action": "noop", "article_refs": [article.ref()]})
    return out


def generate_operations(
    client: OpenAIJsonClient,
    *,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    ref_map = {f"A{i:02d}": article for i, article in enumerate(batch, start=1)}
    prompt_payload = {
        "scope": "India steel industry only. Exclude unrelated industrial/news items, generic investment/stock commentary and generic market-research pages.",
        "run_date_jst": today_jst().isoformat(),
        "new_articles": [prompt_article(article, ref) for ref, article in ref_map.items()],
        "existing_insights": [x.to_prompt() for x in existing],
    }
    system_prompt = _prompt_system() + """

IMPORTANT BACKFILL REFERENCE OVERRIDE:
- Every new article has a short `article_ref` such as A01.
- In output, `article_refs` MUST be an array of those exact short strings, for example ["A01","A02"].
- Do NOT copy or invent Notion page IDs.
13. This is an India-steel historical backfill. Prefer durable company/project/policy/raw-material insights; noop generic stock commentary, duplicate rewrites, and unrelated industrial news.
14. Do not combine independently trackable themes merely because they involve the same company. A capacity roadmap and a distinct steelmaking technology project should normally be separate insights.
15. Account for every input article: use create/update if it contributes durable intelligence, otherwise explicit noop.
16. For an existing topic, prefer update over creating a near-duplicate insight.
""".strip()

    raw = client.generate_json(
        model=model,
        system_prompt=system_prompt,
        user_prompt=json.dumps(prompt_payload, ensure_ascii=False),
        max_output_tokens=max_output_tokens,
        temperature=0.2,
    )
    expanded = expand_short_refs(raw, ref_map)
    operations = normalize_operations(expanded, batch, existing)

    if not operations:
        # One semantic retry: JSON may be valid while refs/action keys are unusable.
        retry_payload = {
            **prompt_payload,
            "previous_invalid_output": raw,
            "repair_instruction": "Regenerate the complete operations JSON. Use ONLY short article_refs A01.. and exact existing insight_key values. Do not explain.",
        }
        raw = client.generate_json(
            model=model,
            system_prompt=system_prompt,
            user_prompt=json.dumps(retry_payload, ensure_ascii=False),
            max_output_tokens=max_output_tokens,
            temperature=0.2,
        )
        expanded = expand_short_refs(raw, ref_map)
        operations = normalize_operations(expanded, batch, existing)

    if not operations:
        raise RuntimeError("GPT returned no valid Intelligence operations after semantic retry")

    operations = coalesce_operations(operations)
    operations = add_uncovered_noops(operations, batch)
    return raw, operations, prompt_payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    notion_token = env("NOTION_TOKEN", "")
    openai_key = env("OPENAI_API_KEY", "")
    if not notion_token or not openai_key:
        raise RuntimeError("NOTION_TOKEN and OPENAI_API_KEY are required")

    lookback_days = env_int("INDIA_STEEL_BACKFILL_LOOKBACK_DAYS", 180)
    min_score = env_float("INDIA_STEEL_BACKFILL_MIN_SCORE", 4.0)
    batch_size = env_int("INDIA_STEEL_BACKFILL_BATCH_SIZE", 20)
    max_batches = env_int("INDIA_STEEL_BACKFILL_MAX_BATCHES", 50)
    max_existing = env_int("INDIA_STEEL_BACKFILL_MAX_EXISTING", 500)
    body_chars = env_int("INDIA_STEEL_BACKFILL_BODY_CHARS", 3500)
    max_output_tokens = env_int("INDIA_STEEL_BACKFILL_MAX_OUTPUT_TOKENS", 10000)
    model = env("INDIA_STEEL_BACKFILL_MODEL", "gpt-5-mini")
    dry_run = env_bool("INDIA_STEEL_BACKFILL_DRY_RUN", False)

    notion = NotionClient(notion_token)
    openai_client = OpenAIJsonClient(openai_key)
    cutoff = today_jst() - timedelta(days=lookback_days)
    nikkei_db = env("INTELLIGENCE_NIKKEI_DB_ID", DEFAULT_NIKKEI_DB_ID)
    general_db = env("INTELLIGENCE_GENERAL_DB_ID", DEFAULT_GENERAL_DB_ID)
    intelligence_db = env("NOTION_INTELLIGENCE_DB_ID", DEFAULT_INTELLIGENCE_DB_ID)

    logging.info("loading India steel candidates v2 cutoff=%s min_score=%s", cutoff, min_score)
    existing = _load_existing_insights(notion, intelligence_db, max_existing)
    nikkei = load_nikkei(notion, nikkei_db, cutoff, min_score, body_chars)
    general = [
        article
        for article in load_general(notion, general_db, cutoff, min_score, body_chars)
        if explicit_india_evidence(article)
    ]
    articles = [*nikkei, *general]
    linked = _already_linked_ids(existing)
    pool = [article for article in articles if _clean_id(article.page_id) not in linked]
    pool.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)

    logs = Path("logs")
    summary: dict[str, Any] = {
        "run_date_jst": today_jst().isoformat(),
        "cutoff": cutoff.isoformat(),
        "lookback_days": lookback_days,
        "min_score": min_score,
        "nikkei_loaded": len(nikkei),
        "general_loaded": len(general),
        "loaded_articles": len(articles),
        "already_linked": len(articles) - len(pool),
        "initial_unlinked": len(pool),
        "dry_run": dry_run,
        "batches": [],
        "created": 0,
        "updated": 0,
        "noops": 0,
        "errors": [],
    }
    write_json(logs / "india_steel_backfill_candidates.json", {
        **{k: summary[k] for k in ["run_date_jst", "cutoff", "lookback_days", "min_score", "nikkei_loaded", "general_loaded", "loaded_articles", "already_linked", "initial_unlinked", "dry_run"]},
        "articles": [
            {
                "source": a.source,
                "page_id": a.page_id,
                "title": a.title,
                "score": a.importance_score,
                "published_at": a.published_at,
                "tags": a.tags,
            }
            for a in pool
        ],
    })

    processed_ids: set[str] = set()
    for batch_no in range(1, max_batches + 1):
        remaining = [a for a in pool if _clean_id(a.page_id) not in processed_ids]
        if not remaining:
            break
        batch = remaining[:batch_size]
        existing = _load_existing_insights(notion, intelligence_db, max_existing)
        raw, operations, prompt_payload = generate_operations(
            openai_client,
            model=model,
            max_output_tokens=max_output_tokens,
            batch=batch,
            existing=existing,
        )
        result = apply_operations(notion, intelligence_db, operations, existing, model, dry_run)
        processed_ids.update(_clean_id(a.page_id) for a in batch)
        batch_summary = {
            "batch": batch_no,
            "articles": len(batch),
            "titles": [a.title for a in batch],
            "operations": len(operations),
            "created": result["created"],
            "updated": result["updated"],
            "noops": result["noops"],
            "errors": result["errors"],
        }
        summary["batches"].append(batch_summary)
        summary["created"] += result["created"]
        summary["updated"] += result["updated"]
        summary["noops"] += result["noops"]
        summary["errors"].extend(result["errors"])
        write_json(
            logs / f"india_steel_backfill_batch_{batch_no:02d}.json",
            {"input": prompt_payload, "raw_output": raw, "normalized_operations": operations, "result": result},
        )
        write_json(logs / "india_steel_backfill_summary.json", summary)
        logging.info(
            "batch=%s articles=%s created=%s updated=%s noops=%s errors=%s",
            batch_no,
            len(batch),
            result["created"],
            result["updated"],
            result["noops"],
            len(result["errors"]),
        )
        if result["errors"]:
            raise RuntimeError(f"Batch {batch_no}: apply errors={len(result['errors'])}")

    remaining_count = len([a for a in pool if _clean_id(a.page_id) not in processed_ids])
    summary["processed_articles"] = len(processed_ids)
    summary["remaining_articles"] = remaining_count
    summary["complete"] = remaining_count == 0
    write_json(logs / "india_steel_backfill_summary.json", summary)
    logging.info(
        "backfill_complete=%s processed=%s remaining=%s created=%s updated=%s noops=%s",
        summary["complete"],
        summary["processed_articles"],
        remaining_count,
        summary["created"],
        summary["updated"],
        summary["noops"],
    )
    if not summary["complete"] and not dry_run:
        raise RuntimeError(f"Backfill stopped with {remaining_count} articles remaining; increase max batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
