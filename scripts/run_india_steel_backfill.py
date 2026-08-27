from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.intelligence_pipeline import (
    ALLOWED_COUNTRIES,
    Article,
    NotionClient,
    _already_linked_ids,
    _clean_id,
    _hyphenate_id,
    _is_useful_text,
    _load_existing_insights,
    _prop_date,
    _prop_multi,
    _prop_number,
    _prop_text,
    _prompt_system,
    _truncate,
    apply_operations,
    normalize_operations,
)
from src.openai_json_client import OpenAIJsonClient

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_NIKKEI_DB_ID = "354dec27-c9aa-803e-bef1-f446abac9b2e"
DEFAULT_GENERAL_DB_ID = "2eddec27-c9aa-8022-9699-c36467fd9477"
DEFAULT_INTELLIGENCE_DB_ID = "3f97b174-1c01-446c-8ebf-65e511f92621"

STEEL_LABEL_MARKERS = (
    "steel", "鉄鋼", "製鉄", "電炉", "tata", "jsw", "jindal", "sail",
    "arcelormittal", "am/ns", "amns", "nippon steel", "日本製鉄", "jfe",
    "posco", "essar", "thyssenkrupp", "nucor", "voestalpine",
)
STEEL_TOPIC_MARKERS = (
    "steel", "鉄鋼", "製鉄", "製鋼", "eaf", "electric arc", "blast furnace",
    "高炉", "電気炉", "rolling", "圧延", "coking coal", "原料炭", "iron ore",
    "鉄鉱石", "pellet", "ペレット", "sinter", "焼結", "scrap", "鉄スクラップ",
    "rebar", "tmt", "形鋼", "hot rolled", "cold rolled", "galvan", "steel plant",
    "green steel", "decarbon", "脱炭素", "capacity expansion", "bf/bof",
)
INDIA_MARKERS = ("india", "indian", "インド")


def env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(env(name, str(default)))


def env_bool(name: str, default: bool) -> bool:
    return env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def today_jst() -> date:
    return datetime.now(JST).date()


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    low = str(text or "").lower()
    return any(marker.lower() in low for marker in markers)


def metadata_is_india_steel(*, title: str, country: list[str], tags: list[str]) -> bool:
    joined_tags = " | ".join(tags)
    india = "India" in country or contains_any(joined_tags, INDIA_MARKERS) or contains_any(title, INDIA_MARKERS)
    steel = contains_any(joined_tags, STEEL_LABEL_MARKERS + STEEL_TOPIC_MARKERS) or contains_any(title, STEEL_LABEL_MARKERS + STEEL_TOPIC_MARKERS)
    return india and steel


def load_general(notion: NotionClient, db_id: str, cutoff: date, min_score: float, body_chars: int) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "PublishedAt", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "ImportanceScore", "number": {"greater_than_or_equal_to": min_score}},
        ]},
        sorts=[
            {"property": "ImportanceScore", "direction": "descending"},
            {"property": "PublishedAt", "direction": "descending"},
        ],
        max_pages=100,
    )
    out: list[Article] = []
    for row in rows:
        props = row.get("properties") or {}
        page_id = _hyphenate_id(str(row.get("id") or ""))
        title = _prop_text(props, "Name")
        country = [x for x in _prop_multi(props, "Country") if x in ALLOWED_COUNTRIES]
        tags = [x for x in [
            _prop_text(props, "Label"),
            _prop_text(props, "Type"),
            _prop_text(props, "PrimaryCountry"),
            *_prop_multi(props, "Sector"),
        ] if x]
        if not page_id or not metadata_is_india_steel(title=title, country=country, tags=tags):
            continue
        preview = _prop_text(props, "BodyPreview")
        body = preview
        if not _is_useful_text(body):
            try:
                body = notion.get_page_text(page_id, body_chars)
            except Exception as exc:
                logging.warning("general_body_fetch_failed page_id=%s error=%s", page_id, exc)
        if not _is_useful_text(body):
            continue
        # Last-resort content check catches labels that are broad/non-steel.
        if not contains_any(" | ".join([title, *tags, body[:1200]]), STEEL_LABEL_MARKERS + STEEL_TOPIC_MARKERS):
            continue
        out.append(Article(
            source="general",
            page_id=page_id,
            title=title,
            published_at=_prop_date(props, "PublishedAt"),
            importance_score=_prop_number(props, "ImportanceScore"),
            source_name=_prop_text(props, "Source"),
            country=country,
            tags=tags,
            body=_truncate(body, body_chars),
            notion_url=str(row.get("url") or ""),
        ))
    return out


def load_nikkei(notion: NotionClient, db_id: str, cutoff: date, min_score: float, body_chars: int) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "Issue Date", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "Importance Score", "number": {"greater_than_or_equal_to": min_score}},
            {"property": "Full Text Status", "select": {"equals": "saved"}},
        ]},
        sorts=[
            {"property": "Importance Score", "direction": "descending"},
            {"property": "Issue Date", "direction": "descending"},
        ],
        max_pages=100,
    )
    out: list[Article] = []
    for row in rows:
        props = row.get("properties") or {}
        page_id = _hyphenate_id(str(row.get("id") or ""))
        title = _prop_text(props, "Title")
        tags = sorted(set(_prop_multi(props, "Tags") + _prop_multi(props, "Matched Rules")))
        country = [x for x in tags if x in ALLOWED_COUNTRIES]
        if not page_id or not metadata_is_india_steel(title=title, country=country, tags=tags):
            continue
        summary = _prop_text(props, "Summary")
        body = summary
        if not _is_useful_text(body):
            try:
                body = notion.get_page_text(page_id, body_chars)
            except Exception as exc:
                logging.warning("nikkei_body_fetch_failed page_id=%s error=%s", page_id, exc)
        if not _is_useful_text(body):
            continue
        if not contains_any(" | ".join([title, *tags, body[:1200]]), STEEL_LABEL_MARKERS + STEEL_TOPIC_MARKERS):
            continue
        out.append(Article(
            source="nikkei",
            page_id=page_id,
            title=title,
            published_at=_prop_date(props, "Issue Date"),
            importance_score=_prop_number(props, "Importance Score"),
            source_name=_prop_text(props, "Source") or "Nikkei",
            country=country,
            tags=tags,
            body=_truncate(body, body_chars),
            notion_url=str(row.get("url") or ""),
        ))
    return out


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


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

    logging.info("loading India steel candidates cutoff=%s min_score=%s", cutoff, min_score)
    existing = _load_existing_insights(notion, intelligence_db, max_existing)
    articles = [
        *load_nikkei(notion, nikkei_db, cutoff, min_score, body_chars),
        *load_general(notion, general_db, cutoff, min_score, body_chars),
    ]
    linked = _already_linked_ids(existing)
    pool = [a for a in articles if _clean_id(a.page_id) not in linked]
    pool.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)

    logs = Path("logs")
    summary: dict[str, Any] = {
        "run_date_jst": today_jst().isoformat(),
        "cutoff": cutoff.isoformat(),
        "lookback_days": lookback_days,
        "min_score": min_score,
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
        **{k: summary[k] for k in ["run_date_jst", "cutoff", "lookback_days", "min_score", "loaded_articles", "already_linked", "initial_unlinked", "dry_run"]},
        "articles": [{"source": a.source, "page_id": a.page_id, "title": a.title, "score": a.importance_score, "published_at": a.published_at, "tags": a.tags} for a in pool],
    })

    processed_ids: set[str] = set()
    for batch_no in range(1, max_batches + 1):
        remaining = [a for a in pool if _clean_id(a.page_id) not in processed_ids]
        if not remaining:
            break
        batch = remaining[:batch_size]
        existing = _load_existing_insights(notion, intelligence_db, max_existing)
        prompt_payload = {
            "scope": "India steel industry only. Exclude unrelated Indian industrial/news items even if metadata matched.",
            "run_date_jst": today_jst().isoformat(),
            "new_articles": [a.to_prompt() for a in batch],
            "existing_insights": [x.to_prompt() for x in existing],
        }
        raw = openai_client.generate_json(
            model=model,
            system_prompt=_prompt_system() + "\n13. This is an India-steel historical backfill. Prefer durable company/project/policy/raw-material insights; noop generic stock commentary, duplicate rewrites, and unrelated industrial news.",
            user_prompt=json.dumps(prompt_payload, ensure_ascii=False),
            max_output_tokens=max_output_tokens,
            temperature=0.2,
        )
        operations = normalize_operations(raw, batch, existing)
        if not operations:
            raise RuntimeError(f"Batch {batch_no}: GPT returned no valid operations")
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
        write_json(logs / f"india_steel_backfill_batch_{batch_no:02d}.json", {"input": prompt_payload, "raw_output": raw, "normalized_operations": operations, "result": result})
        write_json(logs / "india_steel_backfill_summary.json", summary)
        logging.info("batch=%s articles=%s created=%s updated=%s noops=%s errors=%s", batch_no, len(batch), result["created"], result["updated"], result["noops"], len(result["errors"]))
        if result["errors"]:
            raise RuntimeError(f"Batch {batch_no}: apply errors={len(result['errors'])}")

    remaining_count = len([a for a in pool if _clean_id(a.page_id) not in processed_ids])
    summary["processed_articles"] = len(processed_ids)
    summary["remaining_articles"] = remaining_count
    summary["complete"] = remaining_count == 0
    write_json(logs / "india_steel_backfill_summary.json", summary)
    logging.info("backfill_complete=%s processed=%s remaining=%s created=%s updated=%s noops=%s", summary["complete"], summary["processed_articles"], remaining_count, summary["created"], summary["updated"], summary["noops"])
    if not summary["complete"]:
        raise RuntimeError(f"Backfill stopped with {remaining_count} articles remaining; increase max batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
