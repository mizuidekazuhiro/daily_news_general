from __future__ import annotations

import re
from typing import Any

import src.intelligence_pipeline as pipeline


_ORIGINAL_PROMPT_SYSTEM = pipeline._prompt_system
_ORIGINAL_NORMALIZE_OPERATIONS = pipeline.normalize_operations
_ORIGINAL_PROPERTIES_FOR_OPERATION = pipeline._properties_for_operation

# Stable geography/project anchors encoded in Insight Keys. If one of these is
# present in an existing key, a source article must explicitly mention it before
# the article is allowed to update that row. This intentionally ignores noisy
# Notion country metadata and reads the article text itself.
GEO_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "india": ("india", "indian", "インド"),
    "japan": ("japan", "japanese", "日本"),
    "china": ("china", "chinese", "中国"),
    "united-states": ("united states", "u.s.", "us steel", "america", "american", "米国"),
    "eu": ("european union", "europe", "european", "eu ", "eu-"),
    "vietnam": ("vietnam", "vietnamese", "ベトナム"),
    "thailand": ("thailand", "thai ", "タイ"),
    "indonesia": ("indonesia", "indonesian", "インドネシア"),
    "korea": ("korea", "korean", "韓国"),
    "odisha": ("odisha", "orissa"),
    "bokaro": ("bokaro",),
    "jamshedpur": ("jamshedpur",),
    "hazira": ("hazira",),
    "ludhiana": ("ludhiana",),
    "rajayyapeta": ("rajayyapeta",),
    "rayalaseema": ("rayalaseema",),
    "andhra-pradesh": ("andhra pradesh", "andhra"),
    "dhinkia": ("dhinkia",),
    "vijayanagar": ("vijayanagar",),
    "dolvi": ("dolvi",),
    "kalinganagar": ("kalinganagar",),
}

COMPANY_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tata steel", ("tata steel", "tata")),
    ("jsw steel", ("jsw steel", "jsw")),
    ("steel authority of india", ("steel authority of india", "sail")),
    ("arcelormittal nippon steel india", ("am/ns", "amns", "arcelormittal nippon steel")),
    ("jindal steel", ("jindal steel", "jindal")),
    ("posco", ("posco",)),
    ("jfe steel", ("jfe steel", "jfe")),
)

DURABLE_CREATE_TERMS = (
    "commission", "inaugurat", "start construction", "construction began", "groundbreaking",
    "capacity", "mtpa", "million tonnes", "plant", "mill", "facility", "furnace", "eaf",
    "investment", "capex", "acquisition", "merger", "joint venture", " jv ", "stake",
    "tariff", "duty", "safeguard", "anti-dumping", "regulation", "policy", "notification",
    "approval", "approved", "contract", "order", "award", "expand", "expansion",
    "commercial-scale", "commercial scale", "technology demonstration", "pilot plant",
)

NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _articles_for_operation(operation: dict[str, Any], candidates: list[pipeline.Article]) -> list[pipeline.Article]:
    article_map = {(a.source, pipeline._clean_id(a.page_id)): a for a in candidates}
    out: list[pipeline.Article] = []
    seen: set[tuple[str, str]] = set()
    for ref in operation.get("article_refs") or []:
        if not isinstance(ref, dict):
            continue
        key = (str(ref.get("source") or "").strip().lower(), pipeline._clean_id(str(ref.get("page_id") or "")))
        article = article_map.get(key)
        if article and key not in seen:
            seen.add(key)
            out.append(article)
    return out


def _source_text(articles: list[pipeline.Article]) -> str:
    return _normalise_text("\n".join(f"{a.title}\n{a.body}\n{a.published_at}" for a in articles))


def _required_geo_alias_groups(insight_key: str) -> list[tuple[str, ...]]:
    segments = [x.strip().casefold() for x in str(insight_key or "").split("|") if x.strip()]
    return [GEO_KEY_ALIASES[x] for x in segments if x in GEO_KEY_ALIASES]


def _company_guard(existing: pipeline.Insight, source_text: str) -> bool:
    company = _normalise_text(existing.company)
    for needle, aliases in COMPANY_ANCHORS:
        if needle in company:
            return any(_normalise_text(alias) in source_text for alias in aliases)
    return True


def _update_identity_guard(existing: pipeline.Insight, articles: list[pipeline.Article]) -> tuple[bool, str]:
    text = _source_text(articles)
    if not text:
        return False, "missing_source_text"

    if not _company_guard(existing, text):
        return False, "company_mismatch"

    for aliases in _required_geo_alias_groups(existing.insight_key):
        if not any(_normalise_text(alias) in text for alias in aliases):
            return False, "geography_or_project_mismatch"

    return True, ""


def _canonical_numbers(value: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"(?<![A-Za-z])\d+(?:[,.]\d+)*(?![A-Za-z])", str(value or "")):
        canonical = token.replace(",", "").lstrip("0") or "0"
        out.add(canonical)
    return out


def _number_is_grounded(number: str, source_text: str) -> bool:
    compact = source_text.replace(",", "")
    # Avoid substring matches such as 12 inside 120.
    return bool(re.search(rf"(?<!\d){re.escape(number)}(?!\d)", compact))


def _unsupported_grounding_claims(operation: dict[str, Any], articles: list[pipeline.Article]) -> list[str]:
    claims = "\n".join(
        str(operation.get(field) or "")
        for field in ("key_facts", "what_changed")
    )
    source = _source_text(articles)
    unsupported = [n for n in sorted(_canonical_numbers(claims)) if not _number_is_grounded(n, source)]

    claim_text = _normalise_text(claims)
    for word in NUMBER_WORDS:
        if re.search(rf"\b{word}[ -]years?\b|\b{word}[ -]year\b", claim_text):
            if not re.search(rf"\b{word}[ -]years?\b|\b{word}[ -]year\b", source):
                unsupported.append(f"{word}-year")
    return unsupported


def _durable_create_guard(operation: dict[str, Any], articles: list[pipeline.Article]) -> tuple[bool, str]:
    # High/Medium items may still be created, but 'Other' must have a concrete
    # durable action. This blocks management commentary and generic diplomacy
    # from becoming standalone Intelligence rows.
    if operation.get("event_type") != "Other":
        return True, ""
    text = _source_text(articles)
    if any(term in text for term in DURABLE_CREATE_TERMS):
        return True, ""
    return False, "non_durable_other_event"


def _noop_from(operation: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "action": "noop",
        "article_refs": operation.get("article_refs") or [],
        "safety_reason": reason,
    }


def safe_normalize_operations(
    raw: Any,
    candidates: list[pipeline.Article],
    existing: list[pipeline.Insight],
) -> list[dict[str, Any]]:
    operations = _ORIGINAL_NORMALIZE_OPERATIONS(raw, candidates, existing)
    by_key = {x.insight_key: x for x in existing if x.insight_key}
    safe: list[dict[str, Any]] = []

    for operation in operations:
        if operation.get("action") == "noop":
            safe.append(operation)
            continue

        articles = _articles_for_operation(operation, candidates)
        if not articles:
            safe.append(_noop_from(operation, "missing_verified_articles"))
            continue

        unsupported = _unsupported_grounding_claims(operation, articles)
        if unsupported:
            safe.append(_noop_from(operation, "unsupported_numeric_or_duration_claim:" + ",".join(unsupported)))
            continue

        if operation.get("action") == "update":
            matched = by_key.get(str(operation.get("insight_key") or ""))
            if not matched:
                safe.append(_noop_from(operation, "missing_existing_insight"))
                continue
            allowed, reason = _update_identity_guard(matched, articles)
            if not allowed:
                safe.append(_noop_from(operation, reason))
                continue
        elif operation.get("action") == "create":
            allowed, reason = _durable_create_guard(operation, articles)
            if not allowed:
                safe.append(_noop_from(operation, reason))
                continue

        safe.append(operation)

    return safe


def _merge_cumulative(existing: str, new: str, limit: int = 1900) -> str:
    old = str(existing or "").strip()
    delta = str(new or "").strip()
    if not old:
        return pipeline._truncate(delta, limit)
    if not delta:
        return pipeline._truncate(old, limit)

    old_norm = _normalise_text(old)
    delta_norm = _normalise_text(delta)
    if delta_norm in old_norm:
        return pipeline._truncate(old, limit)
    if old_norm in delta_norm:
        return pipeline._truncate(delta, limit)

    # Keep the durable historical core and reserve space for the newest delta.
    separator = "\nLatest verified update: "
    delta_budget = min(650, max(250, limit // 3))
    old_budget = max(200, limit - len(separator) - delta_budget)
    return pipeline._truncate(old, old_budget) + separator + pipeline._truncate(delta, delta_budget)


def safe_properties_for_operation(
    operation: dict[str, Any],
    model: str,
    existing: pipeline.Insight | None = None,
) -> dict[str, Any]:
    props = _ORIGINAL_PROPERTIES_FOR_OPERATION(operation, model, existing)
    if existing is None:
        return props

    # Identity fields are immutable on update. A subsequent article may add
    # evidence, but it cannot redefine what the row is about.
    props["Insight"] = pipeline._title_prop(existing.insight)
    props["Insight Key"] = pipeline._rich_prop(existing.insight_key)
    props["Status"] = pipeline._select_prop(existing.status or "Tracking")
    props["Company"] = pipeline._rich_prop(existing.company)
    props["Country"] = pipeline._multi_prop(existing.country)
    props["Theme"] = pipeline._multi_prop(existing.theme)
    props["Event Type"] = pipeline._select_prop(existing.event_type or "Other")

    # Key Facts and Watch Items are cumulative. What Changed remains the latest
    # delta, and analysis fields can be refreshed only after identity guards pass.
    props["Key Facts"] = pipeline._rich_prop(_merge_cumulative(existing.key_facts, operation.get("key_facts") or ""))
    props["Watch Items"] = pipeline._rich_prop(_merge_cumulative(existing.watch_items, operation.get("watch_items") or ""))
    if not str(operation.get("what_changed") or "").strip():
        props["What Changed"] = pipeline._rich_prop(existing.what_changed)
    if not str(operation.get("business_implication") or "").strip():
        props["Business Implication"] = pipeline._rich_prop(existing.business_implication)

    # Do not silently downgrade an established High/Medium row because one
    # incremental article was scored lower.
    rank = {"Low": 0, "Medium": 1, "High": 2}
    new_importance = str(operation.get("importance") or existing.importance)
    keep_importance = existing.importance if rank.get(existing.importance, 1) >= rank.get(new_importance, 1) else new_importance
    props["Importance"] = pipeline._select_prop(keep_importance)
    return props


def safe_prompt_system() -> str:
    return _ORIGINAL_PROMPT_SYSTEM() + """

SAFETY AND KNOWLEDGE-MAINTENANCE OVERRIDES:
13. UPDATE IDENTITY LOCK: An update must concern the same entity/company AND the same geography/project/topic as the existing row. Company name alone is never sufficient. If an existing insight_key contains a named country, state, city, plant or project, the new source must explicitly concern that geography/project. Otherwise create a separate durable insight or noop.
14. For update, do NOT redefine the row. Keep the existing insight title, company, country, theme and event_type conceptually unchanged. The application layer will enforce this lock.
15. For update, key_facts must contain ONLY the new source-supported factual delta. Do not rewrite or summarize away prior facts; the application layer merges the new delta into historical Key Facts.
16. CREATE IS RARE. Create only when there is a concrete durable item worth tracking over time: plant/capacity milestone, named investment, JV/M&A, formal policy/trade measure, material contract/order, project-level technology milestone, or a material structural supply/demand shift.
17. Default to noop for management commentary/opinion, generic risk statements, one-off monthly statistics, think-tank recommendations, broad diplomatic cooperation, or generic MOUs without a named steel project/contract/investment.
18. Every number, amount, capacity, percentage, date and stated duration in key_facts/what_changed must appear in the referenced new article text. If the source does not state it, omit it and put the uncertainty in watch_items.
19. Never infer execution risk, capex reallocation, delays or causality from a management/personnel change unless the source explicitly links that change to the tracked project.
""".strip()


def apply_safety_patch() -> None:
    pipeline._prompt_system = safe_prompt_system
    pipeline.normalize_operations = safe_normalize_operations
    pipeline._properties_for_operation = safe_properties_for_operation
