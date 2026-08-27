from __future__ import annotations

from typing import Any

from scripts import run_india_steel_backfill_v2 as v2
from src.intelligence_pipeline import Article


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


# generate_operations() resolves this module global at runtime, so replacing it
# here fixes both first-pass and semantic-retry outputs without duplicating v2.
v2.expand_short_refs = expand_short_refs_v3


if __name__ == "__main__":
    raise SystemExit(v2.main())
