from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Keep the existing processed-state dry-run guard compatible while the generic
# regional runner is introduced.
if os.getenv("REGIONAL_STEEL_BACKFILL_DRY_RUN") is not None:
    os.environ["INDIA_STEEL_BACKFILL_DRY_RUN"] = os.environ["REGIONAL_STEEL_BACKFILL_DRY_RUN"]

from src.intelligence_safety import apply_safety_patch

apply_safety_patch()

from src.intelligence_policy import apply_policy_patch

apply_policy_patch()

from src.intelligence_processing import apply_processing_patch

apply_processing_patch()

from scripts.run_india_steel_backfill import (
    DEFAULT_GENERAL_DB_ID,
    DEFAULT_INTELLIGENCE_DB_ID,
    DEFAULT_NIKKEI_DB_ID,
    env,
    env_bool,
    env_float,
    env_int,
    today_jst,
    write_json,
)
from src.intelligence_pipeline import (
    Article,
    Insight,
    NotionClient,
    _already_linked_ids,
    _clean_id,
    _load_existing_insights,
    _load_general_articles,
    _load_nikkei_articles,
    _prompt_system,
    apply_operations,
    normalize_operations,
)
from src.openai_json_client import OpenAIJsonClient


@dataclass(frozen=True)
class RegionProfile:
    name: str
    slug: str
    evidence_re: re.Pattern[str]
    prompt_scope: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


REGION_PROFILES: dict[str, RegionProfile] = {
    "India": RegionProfile(
        name="India",
        slug="india",
        evidence_re=_rx(
            r"(?:\b(?:in|into|across|within|from|to)\s+india\b|"
            r"\bindia(?:n)?\s+(?:operations?|business|capacity|production|output|plant|plants|mill|mills|steel|steelmaking|market|demand|supply|project|projects|investment|investments|capex|facility|facilities|mine|mines|mining|scrap|imports?|exports?|policy|tariff|dut(?:y|ies)|government|customers?|automotive|infrastructure|expansion|manufacturing|sector|industry)\b|"
            r"\b(?:odisha|orissa|jharkhand|gujarat|maharashtra|andhra\s+pradesh|karnataka|punjab|chhattisgarh|west\s+bengal|tamil\s+nadu|uttar\s+pradesh|telangana|rajasthan|jamshedpur|kalinganagar|hazira|vijayanagar|dolvi|salem|ludhiana|bokaro|bhilai|rayalaseema|rajayyapeta|anakapalli|keonjhar|paradeep|dhinkia|gadchiroli|sambalpur)\b|"
            r"インド(?:国内|事業|市場|生産|製鉄|鉄鋼|工場|投資|設備|能力|政策|需要|供給))"
        ),
        prompt_scope="India steel industry and India-linked steel projects only.",
    ),
    "Japan": RegionProfile(
        name="Japan",
        slug="japan",
        evidence_re=_rx(
            r"(?:\b(?:in|into|across|within|from|to)\s+japan\b|"
            r"\bjapan(?:ese)?\s+(?:operations?|business|capacity|production|output|plant|plants|mill|mills|steel|steelmaking|market|demand|supply|project|projects|investment|investments|capex|facility|facilities|scrap|imports?|exports?|policy|tariff|dut(?:y|ies)|government|customers?|automotive|infrastructure|expansion|manufacturing|sector|industry)\b|"
            r"\b(?:tokyo|chiba|kimitsu|kashima|nagoya|wakayama|oita|himeji|kurashiki|fukuyama|kawasaki|kobe|kitakyushu|muroran|hirohata)\b|"
            r"日本(?:国内|市場|政府|鉄鋼業|鉄鋼市場|製鉄所|工場|事業|生産|需要|供給|投資|設備|能力|政策)|"
            r"国内(?:製鉄所|鉄鋼|市場|需要|供給|生産|設備|能力|投資|事業|工場)|"
            r"(?:君津|鹿島|千葉|京浜|倉敷|福山|名古屋|和歌山|大分|広畑|室蘭|神戸|加古川)(?:製鉄所|地区|工場|事業所)?)"
        ),
        prompt_scope="Japan steel industry and steel projects physically or commercially tied to Japan. Overseas projects of Japanese-headquartered companies are out of scope unless the event materially changes Japan operations or supply into Japan.",
    ),
    "China": RegionProfile(
        name="China",
        slug="china",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+china\b|\bchina(?:'s|n)?\s+(?:steel|market|production|capacity|plant|policy|exports?|imports?|demand|industry|government)\b|中国(?:国内|市場|鉄鋼|生産|能力|製鉄所|政策|輸出|輸入|需要|政府))"),
        prompt_scope="China steel industry and China-linked steel projects only.",
    ),
    "United States": RegionProfile(
        name="United States",
        slug="us",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+(?:the\s+)?(?:united states|u\.s\.|usa)\b|\b(?:u\.s\.|us|american)\s+(?:steel|market|production|capacity|plant|policy|tariff|imports?|exports?|demand|industry|government)\b)"),
        prompt_scope="United States steel industry and US-linked steel projects only.",
    ),
    "EU": RegionProfile(
        name="EU",
        slug="eu",
        evidence_re=_rx(r"(?:\b(?:eu|european union|europe)\s+(?:steel|market|production|capacity|plant|policy|cbam|imports?|exports?|demand|industry)|\b(?:netherlands|germany|france|italy|spain|belgium|sweden|poland)\b.*\b(?:steel|plant|mill|project|capacity)\b)"),
        prompt_scope="European Union / European steel industry and EU-linked steel projects only.",
    ),
    "Vietnam": RegionProfile(
        name="Vietnam",
        slug="vietnam",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+vietnam\b|\bvietnam(?:ese)?\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government)\b|ベトナム(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要))"),
        prompt_scope="Vietnam steel industry and Vietnam-linked steel projects only.",
    ),
    "Thailand": RegionProfile(
        name="Thailand",
        slug="thailand",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+thailand\b|\bthai(?:land)?\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government)\b|タイ(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要))"),
        prompt_scope="Thailand steel industry and Thailand-linked steel projects only.",
    ),
    "Indonesia": RegionProfile(
        name="Indonesia",
        slug="indonesia",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+indonesia\b|\bindonesia(?:n)?\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government|nickel)\b|インドネシア(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要|ニッケル))"),
        prompt_scope="Indonesia steel industry and Indonesia-linked steel projects only.",
    ),
    "Malaysia": RegionProfile(
        name="Malaysia",
        slug="malaysia",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+malaysia\b|\bmalaysia(?:n)?\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government)\b|マレーシア(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要))"),
        prompt_scope="Malaysia steel industry and Malaysia-linked steel projects only.",
    ),
    "Philippines": RegionProfile(
        name="Philippines",
        slug="philippines",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+(?:the\s+)?philippines\b|\bphilippine(?:s)?\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government)\b|フィリピン(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要))"),
        prompt_scope="Philippines steel industry and Philippines-linked steel projects only.",
    ),
    "Korea": RegionProfile(
        name="Korea",
        slug="korea",
        evidence_re=_rx(r"(?:\b(?:in|into|across|within|from|to)\s+(?:south\s+)?korea\b|\b(?:south\s+)?korean\s+(?:steel|market|production|capacity|plant|policy|imports?|exports?|demand|industry|government)\b|韓国(?:国内|市場|鉄鋼|生産|製鉄|工場|投資|政策|需要))"),
        prompt_scope="South Korea steel industry and Korea-linked steel projects only.",
    ),
    "MENA": RegionProfile(
        name="MENA",
        slug="mena",
        evidence_re=_rx(r"(?:\b(?:saudi arabia|uae|united arab emirates|oman|qatar|egypt|bahrain|morocco|algeria)\b.*\b(?:steel|plant|mill|capacity|project|iron|dri|eaf)\b|\bmena\s+(?:steel|market|industry|demand|capacity)\b)"),
        prompt_scope="Middle East and North Africa steel industry and MENA-linked steel projects only.",
    ),
}


def get_region_profile(name: str) -> RegionProfile:
    cleaned = str(name or "").strip()
    if cleaned in REGION_PROFILES:
        return REGION_PROFILES[cleaned]
    lowered = cleaned.casefold()
    for profile in REGION_PROFILES.values():
        if profile.name.casefold() == lowered or profile.slug.casefold() == lowered:
            return profile
    raise ValueError(f"Unsupported REGIONAL_STEEL_BACKFILL_REGION={name!r}; supported={sorted(REGION_PROFILES)}")


def explicit_region_evidence(article: Article, profile: RegionProfile) -> bool:
    text = f"{article.title}\n{article.body[:5000]}"
    return bool(profile.evidence_re.search(text))


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


def _generate_once(
    client: OpenAIJsonClient,
    *,
    profile: RegionProfile,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Insight],
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    ref_map = {f"A{i:02d}": article for i, article in enumerate(batch, start=1)}
    prompt_payload = {
        "scope": profile.prompt_scope,
        "run_date_jst": today_jst().isoformat(),
        "new_articles": [prompt_article(article, ref) for ref, article in ref_map.items()],
        "existing_insights": [x.to_prompt() for x in existing],
    }
    system_prompt = _prompt_system() + f"""

IMPORTANT REGIONAL BACKFILL REFERENCE OVERRIDE:
- Every new article has a short `article_ref` such as A01.
- In output, `article_refs` MUST be an array of those exact short strings, for example ["A01","A02"].
- Do NOT copy or invent Notion page IDs.
- This is a {profile.name} steel historical backfill. Prefer durable company/project/policy/raw-material insights; noop generic stock commentary, duplicate rewrites, and unrelated industrial news.
- Do not combine independently trackable themes merely because they involve the same company.
- Account for every input article: use create/update if it contributes durable intelligence, otherwise explicit noop.
- For an existing topic, prefer update over creating a near-duplicate insight.
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


def generate_operations_resilient(
    client: OpenAIJsonClient,
    *,
    profile: RegionProfile,
    model: str,
    max_output_tokens: int,
    batch: list[Article],
    existing: list[Insight],
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    try:
        return _generate_once(
            client,
            profile=profile,
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
            raw, operations, prompt_payload = _generate_once(
                client,
                profile=profile,
                model=model,
                max_output_tokens=max_output_tokens,
                batch=[article],
                existing=existing,
            )
            combined_operations.extend(operations)
            raw_items.append({"article_ref": article.page_id, "status": "classified", "raw_output": raw, "prompt": prompt_payload})
        except RuntimeError as single_exc:
            if "no valid Intelligence operations" not in str(single_exc):
                raise
            failed_refs.append(article.page_id)
            combined_operations.append({
                "action": "noop",
                "article_refs": [article.ref()],
                "classification_error": "GPT returned no valid Intelligence operation after batch and single-article retries",
            })
            raw_items.append({"article_ref": article.page_id, "status": "classification_failed", "error": str(single_exc)})
    combined_operations = coalesce_operations(combined_operations)
    combined_operations = add_uncovered_noops(combined_operations, batch)
    raw = {"fallback": "per_article_after_batch_failure", "failed_article_refs": failed_refs, "items": raw_items}
    prompt_payload = {
        "fallback": "per_article_after_batch_failure",
        "article_count": len(batch),
        "failed_article_refs": failed_refs,
        "new_articles": [article.to_prompt() for article in batch],
    }
    return raw, combined_operations, prompt_payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    notion_token = env("NOTION_TOKEN", "")
    openai_key = env("OPENAI_API_KEY", "")
    if not notion_token or not openai_key:
        raise RuntimeError("NOTION_TOKEN and OPENAI_API_KEY are required")

    profile = get_region_profile(env("REGIONAL_STEEL_BACKFILL_REGION", "Japan"))
    lookback_days = env_int("REGIONAL_STEEL_BACKFILL_LOOKBACK_DAYS", 180)
    min_score = env_float("REGIONAL_STEEL_BACKFILL_MIN_SCORE", 4.0)
    batch_size = env_int("REGIONAL_STEEL_BACKFILL_BATCH_SIZE", 12)
    max_batches = env_int("REGIONAL_STEEL_BACKFILL_MAX_BATCHES", 50)
    max_existing = env_int("REGIONAL_STEEL_BACKFILL_MAX_EXISTING", 500)
    body_chars = env_int("REGIONAL_STEEL_BACKFILL_BODY_CHARS", 6000)
    max_output_tokens = env_int("REGIONAL_STEEL_BACKFILL_MAX_OUTPUT_TOKENS", 10000)
    model = env("REGIONAL_STEEL_BACKFILL_MODEL", "gpt-5-mini")
    dry_run = env_bool("REGIONAL_STEEL_BACKFILL_DRY_RUN", False)

    notion = NotionClient(notion_token)
    openai_client = OpenAIJsonClient(openai_key)
    cutoff = today_jst() - timedelta(days=lookback_days)
    nikkei_db = env("INTELLIGENCE_NIKKEI_DB_ID", DEFAULT_NIKKEI_DB_ID)
    general_db = env("INTELLIGENCE_GENERAL_DB_ID", DEFAULT_GENERAL_DB_ID)
    intelligence_db = env("NOTION_INTELLIGENCE_DB_ID", DEFAULT_INTELLIGENCE_DB_ID)

    logging.info("loading %s steel candidates cutoff=%s min_score=%s", profile.name, cutoff, min_score)
    existing = _load_existing_insights(notion, intelligence_db, max_existing)
    nikkei_all = _load_nikkei_articles(notion, nikkei_db, cutoff, min_score, body_chars)
    general_all = _load_general_articles(notion, general_db, cutoff, min_score, body_chars)
    nikkei = [article for article in nikkei_all if explicit_region_evidence(article, profile)]
    general = [article for article in general_all if explicit_region_evidence(article, profile)]
    articles = [*nikkei, *general]

    # Region evidence can overlap across cross-border stories. De-duplicate exact
    # ingested pages and preserve the strongest/newest row per normalized title.
    linked = _already_linked_ids(existing)
    pool = [article for article in articles if _clean_id(article.page_id) not in linked]
    pool.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)
    seen_titles: set[str] = set()
    deduped: list[Article] = []
    for article in pool:
        key = re.sub(r"\s+", " ", article.title).strip().casefold()
        if key and key in seen_titles:
            continue
        if key:
            seen_titles.add(key)
        deduped.append(article)
    pool = deduped

    logs = Path("logs")
    base = f"{profile.slug}_steel_backfill"
    summary: dict[str, Any] = {
        "region": profile.name,
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
    write_json(logs / f"{base}_candidates.json", {**summary, "articles": [
        {"source": a.source, "page_id": a.page_id, "title": a.title, "score": a.importance_score, "published_at": a.published_at, "tags": a.tags}
        for a in pool
    ]})

    processed_ids: set[str] = set()
    for batch_no in range(1, max_batches + 1):
        remaining = [a for a in pool if _clean_id(a.page_id) not in processed_ids]
        if not remaining:
            break
        batch = remaining[:batch_size]
        existing = _load_existing_insights(notion, intelligence_db, max_existing)
        raw, operations, prompt_payload = generate_operations_resilient(
            openai_client,
            profile=profile,
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
        write_json(logs / f"{base}_batch_{batch_no:02d}.json", {"input": prompt_payload, "raw_output": raw, "normalized_operations": operations, "result": result})
        write_json(logs / f"{base}_summary.json", summary)
        logging.info("region=%s batch=%s articles=%s created=%s updated=%s noops=%s errors=%s", profile.name, batch_no, len(batch), result["created"], result["updated"], result["noops"], len(result["errors"]))
        if result["errors"]:
            raise RuntimeError(f"Batch {batch_no}: apply errors={len(result['errors'])}")

    remaining_count = len([a for a in pool if _clean_id(a.page_id) not in processed_ids])
    summary["processed_articles"] = len(processed_ids)
    summary["remaining_articles"] = remaining_count
    summary["complete"] = remaining_count == 0
    write_json(logs / f"{base}_summary.json", summary)
    logging.info("regional_backfill_complete=%s region=%s processed=%s remaining=%s created=%s updated=%s noops=%s", summary["complete"], profile.name, summary["processed_articles"], remaining_count, summary["created"], summary["updated"], summary["noops"])
    if not summary["complete"] and not dry_run:
        raise RuntimeError(f"Backfill stopped with {remaining_count} articles remaining; increase max batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
