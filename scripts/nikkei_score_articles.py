import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = Path("logs/nikkei_articles_full.json")
OUTPUT_JSON = Path("logs/nikkei_articles_scored.json")
NOTION_VERSION = "2022-06-28"

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
RULES_DB_ID = (os.getenv("NOTION_RULES_DB_ID", "") or "2eddec27c9aa80818f6aceda3258fef0").strip()
RULE_TYPES = {
    x.strip().lower()
    for x in os.getenv("NIKKEI_RULES_FILTER_RULE_TYPES", "country,sector,importance").split(",")
    if x.strip()
}

LOW_VALUE_WORDS = ["おくやみ", "訃報", "叙勲", "将棋", "囲碁", "競馬", "連載小説", "文化", "スポーツ"]


def split_keywords(raw: str) -> list[str]:
    if not raw:
        return []
    text = str(raw).replace("\r\n", "\n")
    text = re.sub(r"\s+OR\s+", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"[\n,、;|　]+", "\n", text)
    parts = [x.strip() for x in text.split("\n")]
    return [x for x in parts if x]


def notion_headers() -> dict[str, str]:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN が未設定です")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def parse_rich_text(prop: dict[str, Any]) -> str:
    t = prop.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", []))
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
    if t == "select":
        v = prop.get("select") or {}
        return v.get("name", "")
    if t == "multi_select":
        return ",".join((x or {}).get("name", "") for x in prop.get("multi_select", []))
    if t == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if t == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    return ""


def load_rules() -> list[dict[str, Any]]:
    rules = []
    cursor = None
    while True:
        payload: dict[str, Any] = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{RULES_DB_ID}/query",
            headers=notion_headers(),
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("results", []):
            p = item.get("properties", {})
            enabled = parse_rich_text(p.get("Enabled", {})).lower() == "true" or (p.get("Enabled", {}).get("checkbox") is True)
            if not enabled:
                continue
            rule_type = parse_rich_text(p.get("RuleType", {})).strip().lower()
            if RULE_TYPES and rule_type not in RULE_TYPES:
                continue
            weight_raw = parse_rich_text(p.get("Weight", {})).strip()
            priority_raw = parse_rich_text(p.get("Priority", {})).strip()
            try:
                weight = float(weight_raw) if weight_raw else 0.0
            except Exception:
                weight = 0.0
            try:
                priority = int(float(priority_raw)) if priority_raw else 0
            except Exception:
                priority = 0
            rules.append(
                {
                    "tag_name": parse_rich_text(p.get("TagName", {})).strip(),
                    "rule_type": rule_type,
                    "match_field": (parse_rich_text(p.get("MatchField", {})).strip().lower() or "both"),
                    "weight": weight,
                    "priority": priority,
                    "keywords": split_keywords(parse_rich_text(p.get("Keywords", {}))),
                    "negative_keywords": split_keywords(parse_rich_text(p.get("NegativeKeywords", {}))),
                }
            )
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return rules


def add_unique(lst: list[str], value: str) -> None:
    if value and value not in lst:
        lst.append(value)


def main() -> int:
    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"{INPUT_JSON} がありません")

    articles = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    rules = load_rules()

    out = []
    for article in articles:
        source_title = str(article.get("source_title") or "")
        page_title = str(article.get("page_title") or "")
        body = str(article.get("text") or "")

        title_text = f"{source_title}\n{page_title}".lower()
        both_text = f"{source_title}\n{page_title}\n{body}".lower()

        score = 0.0
        priority = 0
        tags: list[str] = []
        country_tags: list[str] = []
        sector_tags: list[str] = []
        importance_tags: list[str] = []
        business_impact_areas: list[str] = []
        watch_themes: list[str] = []
        matched_rules: list[str] = []
        negative_matches: list[str] = []
        score_breakdown: list[dict[str, Any]] = []

        for rule in rules:
            target_text = title_text if rule["match_field"] == "title" else both_text
            matched_kw = next((kw for kw in rule["keywords"] if kw.lower() in target_text), None)
            matched_neg = next((kw for kw in rule["negative_keywords"] if kw.lower() in target_text), None)

            if matched_kw is not None:
                delta = rule["weight"]
                score += delta
                priority = max(priority, rule["priority"])
                add_unique(matched_rules, rule["tag_name"])
                add_unique(tags, rule["tag_name"])
                if rule["rule_type"] == "country":
                    add_unique(country_tags, rule["tag_name"])
                elif rule["rule_type"] == "sector":
                    add_unique(sector_tags, rule["tag_name"])
                    add_unique(business_impact_areas, rule["tag_name"])
                elif rule["rule_type"] == "importance":
                    add_unique(importance_tags, rule["tag_name"])
                    add_unique(watch_themes, rule["tag_name"])
                score_breakdown.append({
                    "tag_name": rule["tag_name"], "rule_type": rule["rule_type"], "matched_keyword": matched_kw,
                    "match_field": rule["match_field"], "weight": rule["weight"], "priority": rule["priority"],
                    "score_delta": delta, "reason": "keyword_match"
                })

            if matched_neg is not None:
                delta = -abs(rule["weight"])
                score += delta
                priority = max(priority, rule["priority"])
                add_unique(negative_matches, f"{rule['tag_name']}:{matched_neg}")
                score_breakdown.append({
                    "tag_name": rule["tag_name"], "rule_type": rule["rule_type"], "matched_keyword": matched_neg,
                    "match_field": rule["match_field"], "weight": rule["weight"], "priority": rule["priority"],
                    "score_delta": delta, "reason": "negative_keyword_match"
                })

        combined = f"{source_title}\n{page_title}\n{body}"
        lower_combined = combined.lower()
        is_low_value = any(w in combined for w in LOW_VALUE_WORDS)

        exclude_candidate = False
        exclude_reason = ""
        if "人事記事をもっと見る" in body:
            exclude_candidate, exclude_reason = True, "人事記事マーカーに一致"
        elif len(re.findall("▽", body[:800])) >= 2 and re.search(r"（\d+月\d+日）", body[:800]):
            exclude_candidate, exclude_reason = True, "辞令形式（▽複数+日付）"
        elif re.match(r"^[^\s]{2,25}$", source_title or page_title) and len(re.findall("▽", body[:1000])) >= 2:
            exclude_candidate, exclude_reason = True, "タイトル単独+辞令形式"
        elif len(negative_matches) >= 2:
            exclude_candidate, exclude_reason = True, "NegativeKeywordsへの強一致"

        reason_to_read = ""
        if exclude_candidate:
            reason_to_read = f"除外候補: {exclude_reason}"
        elif score >= float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5")):
            top = tags[:5]
            if top:
                reason_to_read = f"{ '、'.join(top) }のルールに一致。商社業務への影響観点を確認。"

        article.update({
            "importance_score": score,
            "priority": priority,
            "tags": tags,
            "country_tags": country_tags,
            "sector_tags": sector_tags,
            "importance_tags": importance_tags,
            "business_impact_areas": business_impact_areas,
            "watch_themes": watch_themes,
            "matched_rules": matched_rules,
            "negative_matches": negative_matches,
            "score_breakdown": score_breakdown,
            "reason_to_read": reason_to_read,
            "is_low_value": is_low_value,
            "exclude_candidate": exclude_candidate,
            "exclude_reason": exclude_reason,
        })
        out.append(article)

    out.sort(key=lambda x: (x.get("importance_score", 0), x.get("priority", 0), x.get("text_length", 0)), reverse=True)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"scored {len(out)} articles -> {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
