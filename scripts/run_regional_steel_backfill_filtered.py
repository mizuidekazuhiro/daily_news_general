from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import run_regional_steel_backfill as regional
from src.intelligence_pipeline import Article


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

# Scraped pages can append related-story titles or navigation to the article
# body.  Geography/steel words in that tail are not evidence about the event.
# Cut only on recognizable navigation markers and only after substantive text
# has begun, so legitimate article copy remains available for classification.
NAVIGATION_TAIL_RE = re.compile(
    r"(?:"
    r"■\s*[「『]?より詳しい情報を知りたい|"
    r"(?:^|\n)\s*(?:関連記事|関連情報|あわせて読みたい|おすすめ記事)\s*(?:[:：]|$)|"
    r"\bRelated\s+(?:Articles|Stories)\b|"
    r"\bRead\s+More\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Japan must be the physical/commercial event geography. A phrase such as
# "Japanese steelmaker" or a Japanese company name does not make an overseas
# project a Japan Insight. Japanese `国内...` is not sufficient on its own
# because, in an overseas story, it refers to that foreign country's domestic
# market/production rather than Japan.
JAPAN_EVENT_RE = re.compile(
    r"(?:"
    r"\b(?:in|into|across|within|from|to)\s+japan\b|"
    r"\bjapan(?:'s)?\s+(?:steel\s+(?:market|industry|production|demand|supply|capacity|imports?|exports?|policy)|"
    r"domestic\s+(?:steel|market|production|plant|plants|mill|mills|capacity|investment|operations?))\b|"
    r"\b(?:kimitsu|kashima|chiba|keihin|kurashiki|fukuyama|nagoya|wakayama|oita|hirohata|muroran|kakogawa|"
    r"kyushu\s+works?|east\s+nippon\s+works?|west\s+nippon\s+works?)\b|"
    r"日本(?:国内|市場|政府|鉄鋼業|鉄鋼市場|製鉄所|工場|事業|生産|需要|供給|投資|設備|能力|政策)|"
    r"(?:君津|鹿島|千葉|京浜|倉敷|福山|名古屋|和歌山|大分|広畑|室蘭|神戸|加古川|九州)(?:製鉄所|地区|工場|事業所)"
    r")",
    re.IGNORECASE,
)


def primary_article_text(article: Article) -> str:
    body = str(article.body or "")[:5000]
    match = NAVIGATION_TAIL_RE.search(body)
    if match and match.start() >= 200:
        body = body[: match.start()]
    return f"{article.title}\n{body}"


def explicit_steel_evidence(article: Article) -> bool:
    return bool(STEEL_CONTEXT_RE.search(primary_article_text(article)))


def explicit_region_evidence_strict(article: Article, profile: regional.RegionProfile) -> bool:
    text = primary_article_text(article)
    if profile.name == "Japan":
        return bool(JAPAN_EVENT_RE.search(text))
    return bool(profile.evidence_re.search(text))


def explicit_region_and_steel_evidence(article: Article, profile: regional.RegionProfile) -> bool:
    return explicit_region_evidence_strict(article, profile) and explicit_steel_evidence(article)


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
