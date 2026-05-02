from dataclasses import dataclass
from typing import List, Dict


POSITIVE_TOPICS = {
    "総合商社": 18, "鉄鋼": 14, "電炉": 14, "建設": 12, "データセンター": 14,
    "資源": 13, "エネルギー": 13, "為替": 11, "金利": 11, "日銀": 12,
    "地政学": 10, "M&A": 14, "投資": 10, "物流": 10,
}
POSITIVE_COMPANIES = ["三井物産", "三菱商事", "伊藤忠", "住友商事", "丸紅"]
NEGATIVE = ["スポーツ", "芸能", "レシピ", "占い", "ライフ", "事故"]

@dataclass
class ImportanceResult:
    importance_score: int
    importance_reason: str
    matched_topics: List[str]
    matched_companies: List[str]


def score_article(title: str, section: str = "", body: str = "") -> ImportanceResult:
    text = f"{title} {section} {body}"
    score = 0
    topics = []
    companies = []
    for k, w in POSITIVE_TOPICS.items():
        if k.lower() in text.lower():
            score += w
            topics.append(k)
    for c in POSITIVE_COMPANIES:
        if c in text:
            score += 12
            companies.append(c)
    for n in NEGATIVE:
        if n in text:
            score -= 25
    score = max(0, min(100, score))
    reason = "; ".join([f"topic:{t}" for t in topics] + [f"company:{c}" for c in companies]) or "業務関連キーワードが少ない"
    return ImportanceResult(score, reason, topics, companies)


def rank_important_articles(rows: List[Dict], limit: int = 10) -> List[Dict]:
    enriched = []
    for row in rows:
        res = score_article(row.get("title", ""), row.get("section", ""), row.get("body_text", ""))
        item = dict(row)
        item.update({
            "importance_score": res.importance_score,
            "importance_reason": res.importance_reason,
            "matched_topics": res.matched_topics,
            "matched_companies": res.matched_companies,
        })
        enriched.append(item)
    enriched.sort(key=lambda x: x["importance_score"], reverse=True)
    return enriched[:limit]
