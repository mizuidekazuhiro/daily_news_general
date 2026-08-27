from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import src.intelligence_pipeline as pipeline


_ORIGINAL_PROMPT_SYSTEM = pipeline._prompt_system
_ORIGINAL_NORMALIZE_OPERATIONS = pipeline.normalize_operations
_ORIGINAL_PROPERTIES_FOR_OPERATION = pipeline._properties_for_operation

# Stable geography/project anchors encoded in Insight Keys. Project/location
# anchors require literal source-text evidence. Administrative aliases that
# identify the same project are intentionally grouped here.
GEO_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "japan": ("japan", "japanese", "日本"),
    "china": ("china", "chinese", "中国"),
    "united-states": ("united states", "u.s.", "america", "american", "米国"),
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
    "rajayyapeta": ("rajayyapeta", "anakapalli", "anakapalle"),
    "rayalaseema": ("rayalaseema", "sunnapurallapalle"),
    "andhra-pradesh": ("andhra pradesh", "andhra"),
    "dhinkia": ("dhinkia", "paradeep", "paradip"),
    "vijayanagar": ("vijayanagar",),
    "dolvi": ("dolvi",),
    "kalinganagar": ("kalinganagar",),
    "vsp": ("visakhapatnam steel plant", "vsp", "visakhapatnam"),
    "minas-revuboe": ("minas de revuboe", "minas revuboe", "mdr", "moatize"),
}

COUNTRY_KEY_PATTERNS: dict[str, tuple[str, ...]] = {
    "india": (
        r"\bin india\b",
        r"\bindia(?:n)? operations?\b",
        r"\bindia(?:n)? (?:steel|capacity|production|output|capex|investment|plant|project|market|demand|business)\b",
        r"\b(?:jharkhand|odisha|orissa|andhra pradesh|ludhiana|hazira|jamshedpur|vijayanagar|dolvi|bokaro|kalinganagar|rajayyapeta|anakapalli|anakapalle|rayalaseema|paradeep|paradip|visakhapatnam)\b",
        r"インド(?:国内|事業|生産|能力|投資|製鉄|鉄鋼)",
    ),
}

# Topic anchors are derived from stable Insight-Key segments. This prevents a
# same-company/same-location article about a different facility or question from
# overwriting a tracked project.
TOPIC_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "capacity-strategy": ("capacity", "expansion", "mtpa", "capex", "investment", "commission", "restart", "ramp"),
    "capacity-capex": ("capacity", "capex", "investment", "expansion", "mtpa", "commission", "restart", "ramp"),
    "brownfield-expansion": ("brownfield", "expansion", "capacity", "tender", "epc", "blast furnace", "commission"),
    "greenfield-jv": ("joint venture", " jv ", "greenfield", "integrated steel", "steel plant", "capacity"),
    "greenfield": ("greenfield", "steel plant", "integrated steel", "capacity", "construction", "foundation", "project"),
    "low-carbon-ironmaking": ("easymelt", "hisarna", "ironmaking", "blast furnace", "low-carbon", "low carbon", "decarbon"),
    "eaf": ("eaf", "electric arc furnace", "scrap-based", "scrap based"),
    "safeguard-duty": ("safeguard duty", "safeguard", "duty", "tariff"),
    "production-trends": ("crude steel production", "finished steel", "steel consumption", "steel output"),
    "automotive-crc": ("pickling line", "tandem cold mill", "pltcm", "cold-rolled", "cold rolled", "automotive", "ahss", "galvanized", "galvanised"),
    "ladle-explosion": ("ladle explosion", "sms-1", "caster-2", "molten steel", "fatalit", "casualt"),
    "coking-coal-acquisition": ("coking coal", "coal mine", "minas de revuboe", "minas revuboe", "mdr", "moatize"),
    "lpg-shortage-impact": ("lpg", "liquefied petroleum gas", "hormuz", "gas tanker", "petroleum ministry"),
}

COMPANY_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tata steel", ("tata steel", "tata")),
    ("jsw steel", ("jsw steel", "jsw")),
    ("steel authority of india", ("steel authority of india", "sail")),
    ("arcelormittal nippon steel india", ("am/ns", "amns", "arcelormittal nippon steel")),
    ("jindal steel", ("jindal steel", "jindal")),
    ("posco", ("posco",)),
    ("jfe steel", ("jfe steel", "jfe")),
    ("rashtriya ispat nigam", ("rashtriya ispat nigam", "rinl", "visakhapatnam steel plant")),
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
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[,.]\d+)*(?![A-Za-z])")
_SCALED_NUMBER_PATTERNS: tuple[tuple[re.Pattern[str], Decimal], ...] = (
    (re.compile(r"(\d+(?:[,.]\d+)*)\s+lakh\s+crores?\b", re.I), Decimal("100000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s+lakh\b", re.I), Decimal("100000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s+(?:million|mn)\b", re.I), Decimal("1000000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s+billion\b", re.I), Decimal("1000000000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s+(?:thousand|k)\b", re.I), Decimal("1000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s*(?:mtpa|mt\b)", re.I), Decimal("1000000")),
    (re.compile(r"(\d+(?:[,.]\d+)*)\s*(?:ktpa|kt\b)", re.I), Decimal("1000")),
)


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _key_segments(insight_key: str) -> list[str]:
    return [x.strip().casefold() for x in str(insight_key or "").split("|") if x.strip()]


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


def _company_guard(existing: pipeline.Insight, source_text: str) -> bool:
    company = _normalise_text(existing.company)
    for needle, aliases in COMPANY_ANCHORS:
        if needle in company:
            return any(_normalise_text(alias) in source_text for alias in aliases)
    return True


def _geography_guard(insight_key: str, source_text: str) -> bool:
    for segment in _key_segments(insight_key):
        patterns = COUNTRY_KEY_PATTERNS.get(segment)
        if patterns and not any(re.search(pattern, source_text, re.IGNORECASE) for pattern in patterns):
            return False

        # Project keys often combine geography and topic, e.g. odisha-dhinkia or
        # rayalaseema-lowcarbon. Require every geography anchor encoded in the
        # segment rather than only exact segment matches.
        for anchor, aliases in GEO_KEY_ALIASES.items():
            if anchor != segment and anchor not in segment:
                continue
            if not any(_normalise_text(alias) in source_text for alias in aliases):
                return False
    return True


def _topic_guard(insight_key: str, source_text: str) -> bool:
    for segment in _key_segments(insight_key):
        for anchor, aliases in TOPIC_KEY_ALIASES.items():
            if anchor not in segment:
                continue
            if not any(_normalise_text(alias) in source_text for alias in aliases):
                return False
    return True


def _update_identity_guard(existing: pipeline.Insight, articles: list[pipeline.Article]) -> tuple[bool, str]:
    text = _source_text(articles)
    if not text:
        return False, "missing_source_text"

    if not _company_guard(existing, text):
        return False, "company_mismatch"
    if not _geography_guard(existing.insight_key, text):
        return False, "geography_or_project_mismatch"
    if not _topic_guard(existing.insight_key, text):
        return False, "topic_mismatch"

    return True, ""


def _decimal_token(value: str) -> str | None:
    try:
        decimal = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite():
        return None
    if decimal == decimal.to_integral():
        return str(decimal.quantize(Decimal("1")))
    return format(decimal.normalize(), "f").rstrip("0").rstrip(".")


def _canonical_numbers(value: str) -> set[str]:
    out: set[str] = set()
    for token in _NUMBER_RE.findall(str(value or "")):
        canonical = _decimal_token(token)
        if canonical is not None:
            out.add(canonical)
    return out


def _numeric_variants(value: str) -> set[str]:
    """Return literal and unit-equivalent numeric representations.

    This prevents valid facts from being rejected solely because a model writes
    `500,000 tpa` while the source writes `500 KT`, or `136,000 crore` while the
    source writes `1.36 lakh crore`.
    """
    text = str(value or "")
    variants = set(_canonical_numbers(text))
    for pattern, multiplier in _SCALED_NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            base = _decimal_token(match.group(1))
            if base is None:
                continue
            scaled = _decimal_token(str(Decimal(base) * multiplier))
            if scaled is not None:
                variants.add(scaled)
    return variants


def _number_is_grounded(number: str, source_text: str) -> bool:
    canonical = _decimal_token(number)
    if canonical is None:
        return False
    return canonical in _numeric_variants(source_text)


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
    # Legacy helper used by direct safety tests. Production business policy is
    # controlled by Notion via intelligence_policy.py.
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
13. UPDATE IDENTITY LOCK: An update must concern the same entity/company AND the same geography/project/topic as the existing row. Company name alone is never sufficient. Administrative labels for the same physical project (for example Rajayyapeta in Anakapalli district) are the same project, not separate Insights.
14. TOPIC LOCK: A same-company/same-country article still cannot update an unrelated facility or metric. A downstream PLTCM Insight cannot be updated merely because an upstream CSP at the same site reports lifetime output.
15. For update, do NOT redefine the row. Keep the existing insight title, company, country, theme and event_type conceptually unchanged. The application layer will enforce this lock.
16. For update, key_facts must contain ONLY the new source-supported factual delta. Do not rewrite or summarize away prior facts; the application layer merges the new delta into historical Key Facts.
17. CREATE IS RARE. Create only when there is a concrete durable item worth tracking over time: plant/capacity milestone, named investment, JV/M&A, formal policy/trade measure, material contract/order, project-level technology milestone, or a material structural supply/demand shift.
18. Default to noop for management commentary/opinion, generic risk statements, one-off monthly statistics, think-tank recommendations, broad diplomatic cooperation, or generic MOUs without a named steel project/contract/investment.
19. Every number, amount, capacity, percentage, date and stated duration in key_facts/what_changed must be supported by the referenced new article text. Preserve the source's numeric expression and unit where practical; do not invent unit conversions.
20. Never infer execution risk, capex reallocation, delays or causality from a management/personnel change unless the source explicitly links that change to the tracked project.
""".strip()


def apply_safety_patch() -> None:
    pipeline._prompt_system = safe_prompt_system
    pipeline.normalize_operations = safe_normalize_operations
    pipeline._properties_for_operation = safe_properties_for_operation
