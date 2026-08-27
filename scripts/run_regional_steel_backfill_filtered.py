from __future__ import annotations

import re

from scripts import run_regional_steel_backfill as regional
from src.intelligence_pipeline import Article


_ORIGINAL_REGION_EVIDENCE = regional.explicit_region_evidence

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


def main() -> int:
    # The underlying runner resolves this global at execution time, so the
    # wrapper can add a deterministic steel gate without duplicating the
    # regional pipeline implementation.
    regional.explicit_region_evidence = explicit_region_and_steel_evidence
    return regional.main()


if __name__ == "__main__":
    raise SystemExit(main())
