from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import run_india_steel_backfill_v2 as v2
from src.intelligence_pipeline import Article

INDIA_EVIDENCE_RE = re.compile(r"(?:\bindia\b|\bindian\b|インド)", re.IGNORECASE)
_original_load_general = v2.load_general


def explicit_india_evidence_v3(article: Article) -> bool:
    """Require India as a real word; avoid false matches such as Indiana."""
    if any(tag in v2.INDIA_STEEL_LABELS for tag in article.tags):
        return True
    text = f"{article.title}\n{article.body[:3000]}"
    return bool(INDIA_EVIDENCE_RE.search(text))


def load_general_v3(*args: Any, **kwargs: Any) -> list[Article]:
    """Apply strict India evidence and collapse exact-title duplicate rows."""
    articles = _original_load_general(*args, **kwargs)
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


# v2 resolves these globals at runtime. Patch only the backfill-specific behavior.
v2.expand_short_refs = expand_short_refs_v3
v2.explicit_india_evidence = explicit_india_evidence_v3
v2.load_general = load_general_v3


if __name__ == "__main__":
    raise SystemExit(v2.main())
