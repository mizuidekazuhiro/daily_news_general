from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Apply shared integrity, Notion business policy and processed-state behavior
# before importing runtime references from intelligence_pipeline.
from src.intelligence_safety import apply_safety_patch

apply_safety_patch()

from src.intelligence_policy import apply_policy_patch

apply_policy_patch()

from src.intelligence_processing import (
    apply_processing_patch,
    filter_intelligence_entry_candidates,
    filter_unprocessed_articles,
    intelligence_entry_floor,
)

apply_processing_patch()

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
from src.steel_region_profiles import RegionProfile, get_region_profile

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_NIKKEI_DB_ID = "354dec27-c9aa-803e-bef1-f446abac9b2e"
DEFAULT_GENERAL_DB_ID = "2eddec27-c9aa-8022-9699-c36467fd9477"
DEFAULT_INTELLIGENCE_DB_ID = "3c9dec27-c9aa-81d3-8de8-c6d687f3db77"

STEEL_MARKERS = (
    "steel", "鉄鋼", "製鉄", "製鋼", "電炉", "高炉", "eaf", "electric arc",
    "blast furnace", "rolling", "圧延", "coking coal", "met coal", "原料炭",
    "iron ore", "鉄鉱石", "pellet", "sinter", "scrap", "鉄スクラップ", "rebar",
    "tmt", "形鋼", "hot rolled", "cold rolled", "galvan", "steel plant", "green steel",
    "decarbon", "脱炭素", "bf/bof", "billet", "slab", "crude steel", "stainless",
    "tata", "jsw", "jindal", "sail", "arcelormittal", "am/ns", "amns", "nippon steel",
    "日本製鉄", "jfe", "posco", "nucor", "steel dynamics", "cleveland-cliffs", "thyssenkrupp",
    "voestalpine", "salzgitter", "ssab", "baowu", "baosteel", "hesteel", "shagang",
)
NOISE_SOURCES = {"AD HOC NEWS"}
NOISE_TITLE_PATTERNS = (
    r"\bstock\s*\(", r"\bstock:\s*", r"\bnet worth\b", r"\bbest .*steel stocks\b",
    r"\bmarket size\b", r"\bmarket analysis, forecast, size\b", r"\bstate street discloses\b",
    r"\bwhy google discover changes\b",
)


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


def contains_steel(text: str) -> bool:
    low = str(text or "").casefold()
    return any(marker.casefold() in low for marker in STEEL_MARKERS)


def title_is_noise(title: str, source_name: str) -> bool:
    if str(source_name or "").strip() in NOISE_SOURCES:
        return True
    low = str(title or "").casefold()
    return any(re.search(pattern, low, re.IGNORECASE) for pattern in NOISE_TITLE_PATTERNS)


def profile_text(profile: RegionProfile, *, title: str, body: str, tags: list[str], primary_country: str = "") -> bool:
    text = " | ".join([title, primary_country, *tags, body[:6000]])
    return profile.matches(text)


def metadata_prefilter(profile: RegionProfile, *, title: str, country: list[str], tags: list[str], primary_country: str = "") -> bool:
    metadata = " | ".join([title, primary_country, *country, *tags])
    return contains_steel(metadata) and profile.matches(metadata)


def load_general(
    notion: NotionClient,
    db_id: str,
    cutoff: date,
    min_score: float,
    body_chars: int,
    profile: RegionProfile,
) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "PublishedAt", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "ImportanceScore", "number": {"greater_than_or_equal_to": intelligence_entry_floor(min_score)}},
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
        source_name = _prop_text(props, "Source")
        label = _prop_text(props, "Label")
        primary_country = _prop_text(props, "PrimaryCountry")
        country = [x for x in _prop_multi(props, "Country") if x in ALLOWED_COUNTRIES]
        tags = [x for x in [label, _prop_text(props, "Type"), primary_country, *_prop_multi(props, "Sector")] if x]
        if not page_id or title_is_noise(title, source_name):
            continue
        if not metadata_prefilter(profile, title=title, country=country, tags=tags, primary_country=primary_country):
            continue

        body = _prop_text(props, "BodyPreview")
        if not (_is_useful_text(body) and contains_steel(f"{title}\n{body}") and profile_text(profile, title=title, body=body, tags=tags, primary_country=primary_country)):
            try:
                full_body = notion.get_page_text(page_id, body_chars)
                if _is_useful_text(full_body):
                    body = full_body
            except Exception as exc:
                logging.warning("general_body_fetch_failed page_id=%s error=%s", page_id, exc)
        if not _is_useful_text(body):
            continue
        if not contains_steel(f"{title}\n{body}\n{' '.join(tags)}"):
            continue
        if not profile_text(profile, title=title, body=body, tags=tags, primary_country=primary_country):
            continue

        out.append(Article(
            source="general",
            page_id=page_id,
            title=title,
            published_at=_prop_date(props, "PublishedAt"),
            importance_score=_prop_number(props, "ImportanceScore"),
            source_name=source_name,
            country=country,
            tags=tags,
            body=_truncate(body, body_chars),
            notion_url=str(row.get("url") or ""),
        ))
    return out


def load_nikkei(
    notion: NotionClient,
    db_id: str,
    cutoff: date,
    min_score: float,
    body_chars: int,
    profile: RegionProfile,
) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "Issue Date", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "Importance Score", "number": {"greater_than_or_equal_to": intelligence_entry_floor(min_score)}},
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
        if not page_id or not metadata_prefilter(profile, title=title, country=country, tags=tags):
            continue

        body = _prop_text(props, "Summary")
        if not (_is_useful_text(body) and contains_steel(f"{title}\n{body}") and profile_text(profile, title=title, body=body, tags=tags)):
            try:
                full_body = notion.get_page_text(page_id, body_chars)
                if _is_useful_text(full_body):
                    body = full_body
            except Exception as exc:
                logging.warning("nikkei_body_fetch_failed page_id=%s error=%s", page_id, exc)
        if not _is_useful_text(body):
            continue
        if not contains_steel(f"{title}\n{body}\n{' '.join(tags)}"):
            continue
        if not profile_text(profile, title=title, body=body, tags=tags):
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


def dedupe_articles(articles: list[Article]) -> list[Article]:
    articles = sorted(articles, key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)
    out: list[Article] = []
    seen: set[str] = set()
    for article in articles:
        key = re.sub(r"\s+", " ", article.title).strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(article)
    return out


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


def coalesce_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    noops: list[dict[str, Any]] = []
    for operation in operations:
        if operation.get("action") == "noop":
            noops.append(operation)
            continue
        key = str(operation.get("insight_key") or "").strip()
        if not key:
            continue
        if key not in keyed:
            keyed[key] = dict(operation)
            continue
        previous = keyed[key]
        refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ref in [*(previous.get("article_refs") or []), *(operation.get("article_refs") or [])]:
            token = (str(ref.get("source") or ""), _clean_id(str(ref.get("page_id") or "")))
            if token not in seen:
                seen.add(token)
                refs.append(ref)
        merged = dict(operation)
        merged["article_refs"] = refs
        if previous.get("action") == "update" or operation.get("action") == "update":
            merged["action"] = "update"
        keyed[key] = merged
    return [*keyed.values(), *noops]


def add_uncovered_noops(operations: list[dict[str, Any]], batch: list[Article]) -> list[dict[str, Any]]:
    covered: set[str] = set()
    for operation in operations:
        for ref in operation.get("article_refs") or []:
            covered.add(_clean_id(str(ref.get("page_id") or "")))
    out = list(operations)
    for article in batch:
        if _clean_id(article.page_id) not in covered:
            out.append({"action": "noop", "article_refs": [article.ref()]})
    return out


def _generate_once(
    client: OpenAIJsonClient,
    *,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Any],
    profile: RegionProfile,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    ref_map = {f"A{i:02d}": article for i, article in enumerate(batch, start=1)}
    payload = {
        "scope": profile.scope_prompt,
        "region": profile.slug,
        "run_date_jst": today_jst().isoformat(),
        "new_articles": [prompt_article(article, ref) for ref, article in ref_map.items()],
        "existing_insights": [x.to_prompt() for x in existing],
    }
    system_prompt = _prompt_system() + f"""

REGIONAL BACKFILL OVERRIDE ({profile.label}):
- Every new article has a short article_ref such as A01. Output article_refs MUST use those exact short strings.
- This is a historical steel-Intelligence backfill for {profile.label}; company nationality alone is not geographic evidence.
- Use the actual event geography in Country. For cross-border projects include all materially involved event geographies that are allowed values.
- Prefer durable company/project/policy/raw-material insights; noop generic stock moves, generic commentary, monthly statistics and duplicate rewrites.
- Do not combine independently trackable projects merely because the company is the same.
- Account for every input article with create/update/noop. For an existing topic, prefer update over a near-duplicate create.
- Regional focus: {profile.focus}
""".strip()
    raw = client.generate_json(
        model=model,
        system_prompt=system_prompt,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        max_output_tokens=max_output_tokens,
        temperature=0.2,
    )
    expanded = expand_short_refs(raw, ref_map)
    operations = normalize_operations(expanded, batch, existing)
    if not operations:
        retry_payload = {
            **payload,
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
    operations = add_uncovered_noops(coalesce_operations(operations), batch)
    return raw, operations, payload


def generate_operations_resilient(
    client: OpenAIJsonClient,
    *,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Any],
    profile: RegionProfile,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    try:
        return _generate_once(
            client,
            model=model,
            max_output_tokens=max_output_tokens,
            batch=batch,
            existing=existing,
            profile=profile,
        )
    except RuntimeError as exc:
        if "no valid Intelligence operations" not in str(exc):
            raise

    operations: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    for article in batch:
        try:
            raw, single_ops, prompt = _generate_once(
                client,
                model=model,
                max_output_tokens=max_output_tokens,
                batch=[article],
                existing=existing,
                profile=profile,
            )
            operations.extend(single_ops)
            items.append({"article_ref": article.page_id, "status": "classified", "raw_output": raw, "prompt": prompt})
        except RuntimeError as single_exc:
            if "no valid Intelligence operations" not in str(single_exc):
                raise
            failed.append(article.page_id)
            operations.append({
                "action": "noop",
                "article_refs": [article.ref()],
                "classification_error": "GPT returned no valid Intelligence operation after batch and single-article retries",
            })
            items.append({"article_ref": article.page_id, "status": "classification_failed", "error": str(single_exc)})
    operations = add_uncovered_noops(coalesce_operations(operations), batch)
    raw = {"fallback": "per_article_after_batch_failure", "failed_article_refs": failed, "items": items}
    prompt = {"fallback": "per_article_after_batch_failure", "region": profile.slug, "new_articles": [a.to_prompt() for a in batch]}
    return raw, operations, prompt


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    notion_token = env("NOTION_TOKEN", "")
    openai_key = env("OPENAI_API_KEY", "")
    if not notion_token or not openai_key:
        raise RuntimeError("NOTION_TOKEN and OPENAI_API_KEY are required")

    profile = get_region_profile(env("STEEL_BACKFILL_REGION", "japan"))
    lookback_days = env_int("STEEL_BACKFILL_LOOKBACK_DAYS", 180)
    min_score = env_float("STEEL_BACKFILL_MIN_SCORE", 4.0)
    batch_size = env_int("STEEL_BACKFILL_BATCH_SIZE", 12)
    max_batches = env_int("STEEL_BACKFILL_MAX_BATCHES", 50)
    max_existing = env_int("STEEL_BACKFILL_MAX_EXISTING", 700)
    body_chars = env_int("STEEL_BACKFILL_BODY_CHARS", 6000)
    max_output_tokens = env_int("STEEL_BACKFILL_MAX_OUTPUT_TOKENS", 10000)
    model = env("STEEL_BACKFILL_MODEL", "gpt-5-mini")
    dry_run = env_bool("STEEL_BACKFILL_DRY_RUN", True)

    notion = NotionClient(notion_token)
    openai_client = OpenAIJsonClient(openai_key)
    cutoff = today_jst() - timedelta(days=lookback_days)
    nikkei_db = env("INTELLIGENCE_NIKKEI_DB_ID", DEFAULT_NIKKEI_DB_ID)
    general_db = env("INTELLIGENCE_GENERAL_DB_ID", DEFAULT_GENERAL_DB_ID)
    intelligence_db = env("NOTION_INTELLIGENCE_DB_ID", DEFAULT_INTELLIGENCE_DB_ID)

    logging.info("loading steel Intelligence region=%s cutoff=%s min_score=%s", profile.slug, cutoff, min_score)
    existing = _load_existing_insights(notion, intelligence_db, max_existing)
    nikkei = load_nikkei(notion, nikkei_db, cutoff, min_score, body_chars, profile)
    general = load_general(notion, general_db, cutoff, min_score, body_chars, profile)
    articles = filter_intelligence_entry_candidates(dedupe_articles([*nikkei, *general]), min_score)
    nikkei_ids = {_clean_id(a.page_id) for a in nikkei}
    general_ids = {_clean_id(a.page_id) for a in general}
    if nikkei:
        nikkei = filter_unprocessed_articles(notion, nikkei_db, [a for a in articles if _clean_id(a.page_id) in nikkei_ids])
    if general:
        general = filter_unprocessed_articles(notion, general_db, [a for a in articles if _clean_id(a.page_id) in general_ids])
    articles = dedupe_articles([*nikkei, *general])

    linked = _already_linked_ids(existing)
    pool = [article for article in articles if _clean_id(article.page_id) not in linked]
    pool.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)

    logs = Path("logs")
    prefix = f"steel_backfill_{profile.slug}"
    summary: dict[str, Any] = {
        "region": profile.slug,
        "run_date_jst": today_jst().isoformat(),
        "cutoff": cutoff.isoformat(),
        "lookback_days": lookback_days,
        "min_score": min_score,
        "nikkei_loaded": len(nikkei),
        "general_loaded": len(general),
        "loaded_articles": len(articles),
        "initial_unlinked": len(pool),
        "dry_run": dry_run,
        "batches": [],
        "created": 0,
        "updated": 0,
        "noops": 0,
        "errors": [],
    }
    write_json(logs / f"{prefix}_candidates.json", {
        **{k: summary[k] for k in ["region", "run_date_jst", "cutoff", "lookback_days", "min_score", "nikkei_loaded", "general_loaded", "loaded_articles", "initial_unlinked", "dry_run"]},
        "articles": [{
            "source": a.source,
            "page_id": a.page_id,
            "title": a.title,
            "score": a.importance_score,
            "published_at": a.published_at,
            "tags": a.tags,
        } for a in pool],
    })

    processed_ids: set[str] = set()
    for batch_no in range(1, max_batches + 1):
        remaining = [a for a in pool if _clean_id(a.page_id) not in processed_ids]
        if not remaining:
            break
        batch = remaining[:batch_size]
        existing = _load_existing_insights(notion, intelligence_db, max_existing)
        raw, operations, prompt = generate_operations_resilient(
            openai_client,
            model=model,
            max_output_tokens=max_output_tokens,
            batch=batch,
            existing=existing,
            profile=profile,
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
        write_json(logs / f"{prefix}_batch_{batch_no:02d}.json", {
            "input": prompt,
            "raw_output": raw,
            "normalized_operations": operations,
            "result": result,
        })
        write_json(logs / f"{prefix}_summary.json", summary)
        logging.info(
            "region=%s batch=%s articles=%s created=%s updated=%s noops=%s errors=%s",
            profile.slug,
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
    write_json(logs / f"{prefix}_summary.json", summary)
    logging.info(
        "steel_backfill_complete=%s region=%s processed=%s remaining=%s created=%s updated=%s noops=%s",
        summary["complete"], profile.slug, len(processed_ids), remaining_count,
        summary["created"], summary["updated"], summary["noops"],
    )
    if not summary["complete"] and not dry_run:
        raise RuntimeError(f"Backfill stopped with {remaining_count} articles remaining; increase max batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
