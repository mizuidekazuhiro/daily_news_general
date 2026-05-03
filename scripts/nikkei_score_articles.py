import json
import os
import re
from pathlib import Path
from statistics import mean
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = Path("logs/nikkei_articles_full.json")
OUTPUT_JSON = Path("logs/nikkei_articles_scored.json")
SUMMARY_JSON = Path("logs/nikkei_score_summary.json")
NOTION_VERSION = "2022-06-28"


EXCLUDE_PATTERNS = {
    "スポーツ": ["スポーツ", "Jリーグ", "ボクシング"],
    "競馬": ["競馬"],
    "文化": ["文化"],
    "連載小説": ["連載小説"],
    "訃報": ["訃報", "おくやみ"],
    "人事のみ": ["人事"],
    "将棋・囲碁": ["将棋", "囲碁"],
    "芸能": ["芸能", "俳優"],
    "連載コラム": ["コラム"]
}


def env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def notion_headers(token: str) -> dict[str, str]:
    if not token:
        raise RuntimeError("NOTION_TOKEN が未設定です")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def parse_prop_text(prop: dict[str, Any]) -> str:
    ptype = prop.get("type")
    if ptype in {"title", "rich_text"}:
        return "".join(p.get("plain_text", "") for p in prop.get(ptype, []))
    if ptype == "select":
        return (prop.get("select") or {}).get("name", "")
    if ptype == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if ptype == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    return ""


def split_keywords(raw: str) -> list[str]:
    if not raw:
        return []
    text = str(raw).replace("\r\n", "\n")
    text = re.sub(r"\s*OR\s*", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"[\n,、;|　]+", "\n", text)
    return [t.strip() for t in text.split("\n") if t.strip()]


def should_exclude(source_title: str, page_title: str, body: str) -> tuple[bool, str]:
    full = f"{source_title}\n{page_title}\n{body}"
    reasons = []
    for reason, kws in EXCLUDE_PATTERNS.items():
        if any(kw in full for kw in kws):
            reasons.append(reason)
    if "人事のみ" in reasons and len(reasons) == 1 and any(x in full for x in ["異動", "人事"]):
        return True, "人事だけの記事"
    if reasons:
        return True, "、".join(sorted(set(reasons)))
    return False, ""


def load_rules(token: str, rules_db_id: str, rule_types: set[str]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    cursor = None
    while True:
        payload: dict[str, Any] = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{rules_db_id}/query",
            headers=notion_headers(token),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            p = item.get("properties", {})
            enabled = p.get("Enabled", {}).get("checkbox") is True or parse_prop_text(p.get("Enabled", {})).lower() == "true"
            rule_type = parse_prop_text(p.get("RuleType", {})).strip().lower()
            if not enabled:
                continue
            if rule_types and rule_type and rule_type not in rule_types:
                continue
            weight_text = parse_prop_text(p.get("Weight", {})).strip()
            priority_text = parse_prop_text(p.get("Priority", {})).strip()
            rules.append(
                {
                    "tag_name": parse_prop_text(p.get("TagName", {})).strip(),
                    "rule_type": rule_type,
                    "match_field": (parse_prop_text(p.get("MatchField", {})).strip().lower() or "both"),
                    "weight": float(weight_text) if weight_text else 0.0,
                    "priority": int(float(priority_text)) if priority_text else 0,
                    "keywords": split_keywords(parse_prop_text(p.get("Keywords", {}))),
                    "negative_keywords": split_keywords(parse_prop_text(p.get("NegativeKeywords", {}))),
                }
            )
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return rules


def score_article(article: dict[str, Any], rules: list[dict[str, Any]], min_report_score: float) -> dict[str, Any]:
    source_title = str(article.get("source_title") or "")
    page_title = str(article.get("page_title") or "")
    body = str(article.get("text") or "")
    title_text = f"{source_title}\n{page_title}".lower()
    body_text = body.lower()
    both_text = f"{title_text}\n{body_text}"

    score = 0.0
    priority = 0
    tags: list[str] = []
    matched_rules: list[str] = []
    negative_matches: list[str] = []
    score_breakdown: list[dict[str, Any]] = []

    for rule in rules:
        field = rule.get("match_field", "both")
        target = title_text if field == "title" else body_text if field == "body" else both_text

        positive_hits = [kw for kw in rule.get("keywords", []) if kw.lower() in target]
        negative_hits = [kw for kw in rule.get("negative_keywords", []) if kw.lower() in target]

        if positive_hits:
            score += float(rule.get("weight", 0.0))
            priority = max(priority, int(rule.get("priority", 0)))
            tag = rule.get("tag_name", "")
            if tag and tag not in tags:
                tags.append(tag)
            if tag and tag not in matched_rules:
                matched_rules.append(tag)
            score_breakdown.append({"tag": tag, "type": "positive", "hits": positive_hits, "weight": rule.get("weight", 0.0)})

        if negative_hits:
            score -= abs(float(rule.get("weight", 0.0)))
            tag = rule.get("tag_name", "")
            for hit in negative_hits:
                negative_matches.append(f"{tag}:{hit}")
            score_breakdown.append({"tag": tag, "type": "negative", "hits": negative_hits, "weight": -abs(float(rule.get("weight", 0.0)))})

    exclude_candidate, exclude_reason = should_exclude(source_title, page_title, body)
    if exclude_candidate:
        reason_to_read = f"除外候補: {exclude_reason}"
    elif score >= min_report_score and tags:
        reason_to_read = f"{'、'.join(tags)}のルールに一致。商社業務への影響確認対象。"
    else:
        reason_to_read = ""

    out = dict(article)
    out.update(
        {
            "importance_score": score,
            "priority": priority,
            "tags": tags,
            "matched_rules": matched_rules,
            "negative_matches": negative_matches,
            "reason_to_read": reason_to_read,
            "exclude_candidate": exclude_candidate,
            "exclude_reason": exclude_reason,
            "score_breakdown": score_breakdown,
        }
    )
    return out


def main() -> int:
    enable_scoring = env_bool("NIKKEI_ENABLE_SCORING", "true")
    if not enable_scoring:
        print("skip scoring: NIKKEI_ENABLE_SCORING=false")
        return 0

    token = os.getenv("NOTION_TOKEN", "").strip()
    rules_db_id = os.getenv("NOTION_RULES_DB_ID", "").strip()
    if not rules_db_id:
        raise RuntimeError("NOTION_RULES_DB_ID is required when NIKKEI_ENABLE_SCORING=true")
    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"{INPUT_JSON} がありません。先に本文取得を実行してください。")

    rule_types = {x.strip().lower() for x in os.getenv("NIKKEI_RULES_FILTER_RULE_TYPES", "country,sector,importance").split(",") if x.strip()}
    min_report_score = float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5"))

    rules = load_rules(token, rules_db_id, rule_types)
    articles = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    scored = [score_article(a, rules, min_report_score) for a in articles]
    scored.sort(key=lambda x: (x.get("exclude_candidate", False), -float(x.get("importance_score", 0)), -int(x.get("priority", 0)), -int(x.get("text_length", 0))))

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")

    by_type: dict[str, int] = {}
    for r in rules:
        key = r.get("rule_type", "") or "(empty)"
        by_type[key] = by_type.get(key, 0) + 1

    scores = [float(x.get("importance_score", 0.0)) for x in scored]
    summary = {
        "rules_db_id": rules_db_id,
        "loaded_rules_count": len(rules),
        "loaded_rules_count_by_type": by_type,
        "enabled_rules_count": len(rules),
        "total_keyword_count": sum(len(r.get("keywords", [])) for r in rules),
        "sample_rule_names": [r.get("tag_name", "") for r in rules[:10]],
        "articles_with_any_match": sum(1 for x in scored if x.get("matched_rules")),
        "max_importance_score": max(scores) if scores else 0.0,
        "min_importance_score": min(scores) if scores else 0.0,
        "avg_importance_score": mean(scores) if scores else 0.0,
        "scored_article_count": len(scored),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for k, v in summary.items():
        print(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    if scored and summary["max_importance_score"] < min_report_score:
        print(f"WARNING: top_importance_score={summary['max_importance_score']} is below report threshold={min_report_score}. Rules may not be matching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
