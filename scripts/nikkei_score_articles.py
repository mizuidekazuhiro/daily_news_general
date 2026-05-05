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
INVENTORY_JSON = Path("logs/nikkei_issue_run_inventory.json")
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
NAV_BODY_KEYWORDS = ["速報", "アクセスランキング", "トピック一覧", "おくやみ", "プレスリリース", "メディア一覧", "ビューアーで読む", "朝刊・夕刊"]


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


def is_navigation_like_body(text: str) -> bool:
    body = str(text or "").strip()
    if not body:
        return False
    hits = sum(body.count(k) for k in NAV_BODY_KEYWORDS)
    sentence_count = body.count("。")
    return hits >= 2 or (sentence_count <= 1 and len(body) > 150)



def clean_text_for_scoring(text: str) -> str:
    body = str(text or "").replace("<br>", "\n")

    drop_exact = {
        "共有",
        "文字サイズ",
        "小",
        "中",
        "大",
        "自動翻訳",
        "英文（システムによる自動翻訳）を表示する",
        "日経の記事利用サービス",
        "保存",
        "印刷 翻訳",
        "検索する",
        "その他",
        "［有料会員限定］",
        "[有料会員限定]",
    }

    drop_patterns = [
        r"朝夕刊や電子版ではお伝えしきれない情報をお届けします。?.*",
        r"企業での記事共有や会議資料への転載・複製.*",
        r".*注文印刷.*",
        r".*詳しくはこちら.*",
        r"^\d+文字$",
        r".*javascript:void\(0\).*",
        r"^その他javascript:void\(0\).*$",
    ]

    kept = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in drop_exact:
            continue
        if any(re.search(pattern, line) for pattern in drop_patterns):
            continue
        kept.append(line)

    return "\n".join(kept).strip()


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




def derive_tags_from_rules(tags_by_type: dict[str,list[str]]) -> dict[str,list[str]]:
    return {
        'country_tags': tags_by_type.get('country', []),
        'sector_tags': tags_by_type.get('sector', []),
        'importance_tags': tags_by_type.get('importance', []),
        'business_impact_areas': tags_by_type.get('business_impact', []),
        'watch_themes': tags_by_type.get('watch_theme', []),
    }

def score_article(article: dict[str, Any], rules: list[dict[str, Any]], min_report_score: float) -> dict[str, Any]:
    source_title = str(article.get("source_title") or "")
    page_title = str(article.get("page_title") or "")
    raw_body = str(article.get("text") or "")
    body = clean_text_for_scoring(raw_body)
    body_used_for_scoring = not is_navigation_like_body(body)
    body_for_score = body if body_used_for_scoring else ""
    title_text = f"{source_title}\n{page_title}".lower()
    body_text = body_for_score.lower()
    both_text = f"{title_text}\n{body_text}"

    score = 0.0
    priority = 0
    tags: list[str] = []
    matched_rules: list[str] = []
    tags_by_type: dict[str, list[str]] = {}
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
            rtype = str(rule.get('rule_type') or '')
            if tag and rtype:
                tags_by_type.setdefault(rtype, [])
                if tag not in tags_by_type[rtype]:
                    tags_by_type[rtype].append(tag)
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

    derived = derive_tags_from_rules(tags_by_type)
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
            "body_used_for_scoring": body_used_for_scoring,
            **derived,
        }
    )
    return out


def select_report_articles(
    articles: list[dict[str, Any]],
    selection_mode: str,
    min_report_score: float,
    top_rank: int,
    include_ties: bool,
) -> tuple[list[dict[str, Any]], float | None, str]:
    mode = (selection_mode or "top_importance_rank").strip().lower()
    candidates = [a for a in articles if a.get("importance_score") is not None]
    if mode == "threshold":
        selected = [a for a in candidates if float(a.get("importance_score", 0)) >= min_report_score]
        return selected, min_report_score, "threshold"

    ordered = sorted(
        candidates,
        key=lambda a: (
            float(a.get("importance_score", 0)),
            int(a.get("priority", 0)),
            str(a.get("issue_date") or ""),
            str(a.get("source_title") or a.get("page_title") or ""),
        ),
        reverse=True,
    )
    if top_rank <= 0 or len(ordered) <= top_rank:
        return ordered, None, "top_importance_rank_with_ties" if include_ties else "top_importance_rank"

    cutoff_score = float(ordered[top_rank - 1].get("importance_score", 0))
    if include_ties:
        selected = [a for a in ordered if float(a.get("importance_score", 0)) >= cutoff_score]
        return selected, cutoff_score, "top_importance_rank_with_ties"
    return ordered[:top_rank], cutoff_score, "top_importance_rank"


def main() -> int:
    enable_scoring = env_bool("NIKKEI_ENABLE_SCORING", "true")
    if not enable_scoring:
        print("skip scoring: NIKKEI_ENABLE_SCORING=false")
        return 0

    token = os.getenv("NOTION_TOKEN", "").strip()
    rules_db_id = os.getenv("NOTION_RULES_DB_ID", "").strip()
    if not rules_db_id:
        raise RuntimeError("NOTION_RULES_DB_ID is required when NIKKEI_ENABLE_SCORING=true")

    rule_types = {x.strip().lower() for x in os.getenv("NIKKEI_RULES_FILTER_RULE_TYPES", "country,sector,importance").split(",") if x.strip()}
    min_report_score = float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5"))
    report_selection_mode = os.getenv("NIKKEI_REPORT_SELECTION_MODE", "top_importance_rank")
    report_top_rank = int(os.getenv("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", "5"))
    report_include_ties = env_bool("NIKKEI_REPORT_INCLUDE_TIES", "true")

    rules = load_rules(token, rules_db_id, rule_types)
    fetched_articles = json.loads(INPUT_JSON.read_text(encoding="utf-8")) if INPUT_JSON.exists() else []
    inventory = json.loads(INVENTORY_JSON.read_text(encoding="utf-8")) if INVENTORY_JSON.exists() else []
    existing_articles = []
    backfilled_articles = []
    for item in inventory:
        if item.get('status') == 'existing_in_notion':
            src = item.get('notion_existing', {})
            existing_articles.append({
                'source_title': src.get('title') or item.get('title', ''),
                'page_title': src.get('title') or item.get('title', ''),
                'url': src.get('url') or item.get('url', ''),
                'issue_date': src.get('issue_date', ''),
                'edition': src.get('edition', ''),
                'text': src.get('text', ''),
                'text_length': len(src.get('text', '')),
                'page_id': src.get('page_id', ''),
                'source': 'notion_existing',
            })
        elif item.get('status') == 'backfilled_existing':
            m = next((x for x in fetched_articles if x.get('url') == item.get('url')), None)
            if m:
                backfilled_articles.append(m)
    articles = fetched_articles + existing_articles
    scored = [score_article(a, rules, min_report_score) for a in articles]
    scored.sort(key=lambda x: (x.get("exclude_candidate", False), -float(x.get("importance_score", 0)), -int(x.get("priority", 0)), -int(x.get("text_length", 0))))

    selected, cutoff_score, selection_mode_used = select_report_articles(
        scored,
        selection_mode=report_selection_mode,
        min_report_score=min_report_score,
        top_rank=report_top_rank,
        include_ties=report_include_ties,
    )
    selected_urls = {str(a.get("url") or "") for a in selected}
    for a in scored:
        included = str(a.get("url") or "") in selected_urls
        a["included_in_report"] = included
        if included:
            a["report_selection_reason"] = "top_importance_rank_or_tie" if selection_mode_used != "threshold" else "above_threshold"
            a["report_excluded_reason"] = ""
        else:
            a["report_selection_reason"] = ""
            a["report_excluded_reason"] = "below_top_rank_cutoff" if selection_mode_used != "threshold" else "below_threshold"

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
        "scoring_input_new_count": len(fetched_articles),
        "scoring_input_existing_count": len(existing_articles),
        "scoring_input_backfilled_body_count": len(backfilled_articles),
        "scoring_input_saved_body_count": sum(1 for a in existing_articles if str(a.get('text') or '').strip()),
        "scoring_input_title_only_count": sum(1 for a in articles if not str(a.get('text') or '').strip() and str(a.get('source_title') or a.get('page_title') or '').strip()),
        "scoring_input_total_count": len(articles),
        "report_selection_mode": selection_mode_used,
        "report_top_rank": report_top_rank,
        "report_include_ties": report_include_ties,
        "report_candidate_count": len([a for a in scored if a.get("importance_score") is not None]),
        "report_selected_count": len(selected),
        "report_cutoff_importance_score": cutoff_score,
        "selected_article_titles": [str(a.get("source_title") or a.get("page_title") or "") for a in selected],
        "selected_article_scores": [float(a.get("importance_score", 0)) for a in selected],
        "excluded_article_count": max(0, len(scored) - len(selected)),
        "excluded_reason": "below_top_rank_cutoff" if selection_mode_used != "threshold" else "below_threshold",
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for k, v in summary.items():
        print(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    if scored and selection_mode_used == "threshold" and summary["max_importance_score"] < min_report_score:
        print(f"WARNING: top_importance_score={summary['max_importance_score']} is below report threshold={min_report_score}. Rules may not be matching.")
    if summary["report_candidate_count"] < report_top_rank:
        print(f"INFO: report candidates are fewer than top rank. candidate_count={summary['report_candidate_count']} top_rank={report_top_rank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
