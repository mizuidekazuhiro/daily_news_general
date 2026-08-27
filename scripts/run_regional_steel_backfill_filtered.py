from __future__ import annotations

import os
import re
from typing import Any

from scripts import run_regional_steel_backfill as regional
from src.intelligence_pipeline import Article


_ORIGINAL_REGION_EVIDENCE = regional.explicit_region_evidence
_ORIGINAL_LOAD_EXISTING = regional._load_existing_insights

STEEL_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bsteel(?:making|works)?\b|\bblast\s+furnace\b|\belectric\s+arc\s+furnace\b|\bEAF\b|"
    r"\bdirect[-\s]+reduced\s+iron\b|\bDRI\b|\biron\s+ore\b|\bmetallurgical\s+coal\b|\bcoking\s+coal\b|"
    r"\bpig\s+iron\b|\bnickel\s+pig\s+iron\b|\bNPI\b|\bferrochrome\b|\bchromite\b|\bmanganese\s+ore\b|"
    r"\bstainless\b|\brebar\b|\bbillet\b|\bslab\b|\bhot[-\s]+rolled\b|\bcold[-\s]+rolled\b|"
    r"\bcrude\s+steel\b|\bgalvani[sz]ed\b|\bsteel\s+plate\b|\bsteel\s+pipe\b|\bwire\s+rod\b|"
    r"鉄鋼|製鉄|製鋼|鋼材|粗鋼|高炉|電炉|鉄鉱石|原料炭|コークス|スクラップ|ステンレス|"
    r"フェロクロム|クロマイト|鉄筋|ビレット|スラブ|熱延|冷延|めっき鋼|厚板|鋼管|線材|H形鋼|形鋼"
    r")",
    re.IGNORECASE,
)

STEEL_TAG_HINTS = {
    "steel",
    "steel plant investment",
    "製鉄所の設備投資",
    "green steel",
}


def explicit_steel_evidence(article: Article) -> bool:
    text = f"{article.title}\n{article.body[:5000]}"
    if STEEL_CONTEXT_RE.search(text):
        return True
    tags = {str(tag or "").strip().casefold() for tag in article.tags}
    return bool(tags & STEEL_TAG_HINTS)


def explicit_region_and_steel_evidence(article: Article, profile: regional.RegionProfile) -> bool:
    return _ORIGINAL_REGION_EVIDENCE(article, profile) and explicit_steel_evidence(article)


def filter_existing_for_region(existing: list[Any], profile: regional.RegionProfile) -> list[Any]:
    return [insight for insight in existing if profile.name in (getattr(insight, "country", None) or [])]


def load_existing_for_active_region(notion: Any, db_id: str, max_existing: int) -> list[Any]:
    existing = _ORIGINAL_LOAD_EXISTING(notion, db_id, max_existing)
    profile = regional.get_region_profile(os.getenv("REGIONAL_STEEL_BACKFILL_REGION", "Japan"))
    return filter_existing_for_region(existing, profile)


def main() -> int:
    # The underlying runner resolves these globals at execution time, so the
    # wrapper can add deterministic scope gates without duplicating the pipeline.
    regional.explicit_region_evidence = explicit_region_and_steel_evidence
    regional._load_existing_insights = load_existing_for_active_region
    return regional.main()


if __name__ == "__main__":
    raise SystemExit(main())
