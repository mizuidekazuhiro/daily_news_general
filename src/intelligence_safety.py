from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import src.intelligence_safety_legacy as _legacy

# v2 compatibility layer over the proven v1 safety implementation.  Keep the
# stable identity/topic/source guards in one place while tightening generic
# grounding edges found during regional acceptance audits.

_WORD_VALUES: dict[str, Decimal] = {
    "one": Decimal("1"),
    "two": Decimal("2"),
    "three": Decimal("3"),
    "four": Decimal("4"),
    "five": Decimal("5"),
    "six": Decimal("6"),
    "seven": Decimal("7"),
    "eight": Decimal("8"),
    "nine": Decimal("9"),
    "ten": Decimal("10"),
}
_WORD_SCALED_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(lakh(?:\s+crores?)?|million|billion|thousand|mtpa|ktpa)\b",
    re.IGNORECASE,
)
_WORD_SCALE: dict[str, Decimal] = {
    "lakh": Decimal("100000"),
    "lakh crore": Decimal("100000"),
    "lakh crores": Decimal("100000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "thousand": Decimal("1000"),
    "mtpa": Decimal("1000000"),
    "ktpa": Decimal("1000"),
}

# Do not start a numeric token in the middle of an alphanumeric identifier.
# The old expression interpreted FY2006 as 006 (=6) when the source wrote
# "FY 2006", producing a false grounding failure.
_legacy._NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:[,.]\d+)*(?![A-Za-z0-9])")
_V1_NUMERIC_VARIANTS = _legacy._numeric_variants


def _numeric_variants(value: str) -> set[str]:
    """Extend v1 unit equivalence to spelled-out scaled quantities.

    Example: source "one million tonnes" supports model wording "1 Mt" without
    weakening the numeric grounding requirement for unrelated numbers.
    """
    variants = set(_V1_NUMERIC_VARIANTS(value))
    text = str(value or "")
    for match in _WORD_SCALED_RE.finditer(text):
        base = _WORD_VALUES[match.group(1).casefold()]
        scale = _WORD_SCALE[re.sub(r"\s+", " ", match.group(2).casefold()).strip()]
        base_token = _legacy._decimal_token(str(base))
        scaled_token = _legacy._decimal_token(str(base * scale))
        if base_token is not None:
            variants.add(base_token)
        if scaled_token is not None:
            variants.add(scaled_token)
    return variants


_legacy._numeric_variants = _numeric_variants


def _source_text_with_date_aliases(articles: list[Any]) -> str:
    """Ground an ISO timestamp and its calendar-date representation equally.

    Article metadata can carry `2026-04-20T09:35:00...` while a model cites the
    same verified date as `2026-04-20`.  The strict numeric tokenizer correctly
    refuses to start/end inside alphanumeric identifiers, so the day immediately
    before the ISO `T` would otherwise disappear from source numeric variants.
    Add only the exact YYYY-MM-DD prefix as an alias; unrelated dates or numbers
    remain unsupported.
    """
    parts: list[str] = []
    for article in articles:
        published = str(getattr(article, "published_at", "") or "")
        match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$", published)
        date_alias = match.group(1) if match else ""
        parts.append(
            "\n".join(
                [
                    str(getattr(article, "title", "") or ""),
                    str(getattr(article, "body", "") or ""),
                    published,
                    date_alias,
                ]
            )
        )
    return _legacy._normalise_text("\n".join(parts))


_legacy._source_text = _source_text_with_date_aliases

# A material project-state upgrade must be evidenced in substantive article
# body text, not merely a headline, SEO title or navigation snippet.  These are
# deliberately narrow milestone classes; ordinary factual deltas remain under
# the existing policy + source-grounding checks.
_STATUS_MILESTONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:formed|established|finali[sz]ed|completed)\s+(?:a\s+|the\s+)?"
        r"(?:\d{1,3}:\d{1,3}\s+)?(?:joint venture|jv)\b|"
        r"\b(?:joint venture|jv)\s+(?:has\s+|was\s+|is\s+)?"
        r"(?:formed|established|finali[sz]ed|completed)\b|"
        r"\b(?:joint venture agreement|jva)\s+(?:has\s+been\s+|was\s+)?"
        r"(?:signed|executed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:completed|closed|finali[sz]ed)\s+(?:the\s+)?acquisition\b|"
        r"\bacquisition\s+(?:has\s+been\s+|was\s+)?(?:completed|closed|finali[sz]ed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcommissioned\b|\bfully\s+operational\b|\bcommercial\s+operations?\s+"
        r"(?:began|started|commenced)\b|\b(?:began|started|commenced)\s+commercial\s+production\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfinal\s+investment\s+decision\b|\bFID\b|"
        r"\b(?:project|plant|expansion|investment)\s+(?:has\s+been\s+|was\s+)?approved\b|"
        r"\bapproved\s+(?:the\s+)?(?:project|plant|expansion|investment)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconstruction\s+(?:has\s+)?(?:started|begun|commenced|launched)\b|"
        r"\b(?:started|began|commenced|launched)\s+construction\b|"
        r"\bfoundation\s+stone\s+(?:was\s+)?(?:laid|placed)\b|\bgroundbreaking\s+(?:was\s+)?held\b",
        re.IGNORECASE,
    ),
)


def _body_text(articles: list[Any]) -> str:
    return _legacy._normalise_text("\n".join(str(article.body or "") for article in articles))


def _headline_only_status_upgrade(operation: dict[str, Any], articles: list[Any]) -> bool:
    claims = "\n".join(
        str(operation.get(field) or "")
        for field in ("key_facts", "what_changed")
    )
    body = _body_text(articles)
    if not claims.strip():
        return False
    for pattern in _STATUS_MILESTONE_PATTERNS:
        if pattern.search(claims) and not pattern.search(body):
            return True
    return False


def safe_normalize_operations(
    raw: Any,
    candidates: list[Any],
    existing: list[Any],
) -> list[dict[str, Any]]:
    operations = _legacy.safe_normalize_operations(raw, candidates, existing)
    safe: list[dict[str, Any]] = []
    for operation in operations:
        if operation.get("action") != "update":
            safe.append(operation)
            continue
        articles = _legacy._articles_for_operation(operation, candidates)
        if articles and _headline_only_status_upgrade(operation, articles):
            safe.append(_legacy._noop_from(operation, "headline_only_status_upgrade"))
            continue
        safe.append(operation)
    return safe


safe_properties_for_operation = _legacy.safe_properties_for_operation
safe_prompt_system = _legacy.safe_prompt_system


def apply_safety_patch() -> None:
    _legacy.pipeline._prompt_system = safe_prompt_system
    _legacy.pipeline.normalize_operations = safe_normalize_operations
    _legacy.pipeline._properties_for_operation = safe_properties_for_operation


def __getattr__(name: str) -> Any:
    # Preserve compatibility for tests/importers that use v1 helpers directly.
    return getattr(_legacy, name)
