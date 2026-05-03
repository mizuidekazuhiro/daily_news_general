import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = Path("logs/nikkei_articles_scored.json")
NOTION_VERSION = "2022-06-28"


def env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_req(token: str, method: str, url: str, **kwargs):
    last = None
    for _ in range(6):
        resp = requests.request(method, url, headers=notion_headers(token), timeout=60, **kwargs)
        last = resp
        if resp.status_code == 429:
            sleep_s = int(resp.headers.get("Retry-After", "2"))
            time.sleep(max(1, sleep_s))
            continue
        resp.raise_for_status()
        return resp
    if last is not None:
        last.raise_for_status()


def rt(text: str) -> list[dict[str, Any]]:
    return [{"text": {"content": text[:2000]}}] if text else []


def build_props_payload(article: dict[str, Any], db_props: dict[str, Any], missing: set[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    def ptype(name: str) -> str:
        return (db_props.get(name) or {}).get("type", "")

    targets = ["Importance Score", "Priority", "Tags", "Reason to Read", "Matched Rules", "Exclude Candidate", "Exclude Reason", "Included in Report", "Report Selection Reason", "Report Excluded Reason"]
    for t in targets:
        if t not in db_props:
            missing.add(t)

    if ptype("Importance Score") == "number":
        payload["Importance Score"] = {"number": float(article.get("importance_score") or 0)}
    if ptype("Priority") == "number":
        payload["Priority"] = {"number": int(article.get("priority") or 0)}

    tags = [str(x) for x in article.get("tags", []) if str(x).strip()]
    if ptype("Tags") == "multi_select":
        payload["Tags"] = {"multi_select": [{"name": t[:100]} for t in tags]}
    elif ptype("Tags") == "rich_text":
        payload["Tags"] = {"rich_text": rt("、".join(tags))}

    matched = [str(x) for x in article.get("matched_rules", []) if str(x).strip()]
    if ptype("Matched Rules") == "multi_select":
        payload["Matched Rules"] = {"multi_select": [{"name": t[:100]} for t in matched]}
    elif ptype("Matched Rules") == "rich_text":
        payload["Matched Rules"] = {"rich_text": rt("、".join(matched))}

    if ptype("Reason to Read") == "rich_text":
        payload["Reason to Read"] = {"rich_text": rt(str(article.get("reason_to_read") or ""))}
    if ptype("Exclude Candidate") == "checkbox":
        payload["Exclude Candidate"] = {"checkbox": bool(article.get("exclude_candidate", False))}
    if ptype("Exclude Reason") == "rich_text":
        payload["Exclude Reason"] = {"rich_text": rt(str(article.get("exclude_reason") or ""))}
    if ptype("Included in Report") == "checkbox":
        payload["Included in Report"] = {"checkbox": bool(article.get("included_in_report", False))}
    if ptype("Report Selection Reason") == "rich_text":
        payload["Report Selection Reason"] = {"rich_text": rt(str(article.get("report_selection_reason") or ""))}
    if ptype("Report Excluded Reason") == "rich_text":
        payload["Report Excluded Reason"] = {"rich_text": rt(str(article.get("report_excluded_reason") or ""))}

    return payload


def page_url_value(page_props: dict[str, Any], prop_name: str) -> str:
    p = page_props.get(prop_name, {})
    ptype = p.get("type")
    if ptype == "url":
        return p.get("url") or ""
    if ptype == "rich_text":
        return "".join(x.get("plain_text", "") for x in p.get("rich_text", []))
    return ""


def main() -> int:
    if not env_bool("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "false"):
        print("skip notion score update: NIKKEI_ENABLE_NOTION_SCORE_UPDATE=false")
        return 0

    token = os.getenv("NOTION_TOKEN", "").strip()
    db_id = (os.getenv("NIKKEI_ARTICLES_DB_ID", "") or os.getenv("NOTION_ARTICLE_DB_ID", "")).strip()
    if not token or not db_id:
        raise RuntimeError("NOTION_TOKEN / NIKKEI_ARTICLES_DB_ID(or NOTION_ARTICLE_DB_ID) missing")
    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"{INPUT_JSON} がありません")

    db_resp = notion_req(token, "GET", f"https://api.notion.com/v1/databases/{db_id}")
    db_props = db_resp.json().get("properties", {})
    url_prop_candidates = [name for name, v in db_props.items() if v.get("type") in {"url", "rich_text"} and "url" in name.lower()]
    if not url_prop_candidates:
        raise RuntimeError("URL 検索可能なプロパティが見つかりません")

    items = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    query_resp = notion_req(token, "POST", f"https://api.notion.com/v1/databases/{db_id}/query", json={"page_size": 100})
    pages = query_resp.json().get("results", [])
    url_index: dict[str, dict[str, Any]] = {}
    for p in pages:
        props = p.get("properties", {})
        for pname in url_prop_candidates:
            val = page_url_value(props, pname).strip()
            if val:
                url_index[val] = p

    target_count = len(items)
    found_pages = updated = skipped = failed = 0
    existing_page_id_count = 0
    url_lookup_count = 0
    missing_props: set[str] = set()

    for a in items:
        url = str(a.get("url") or "").strip()
        if not url:
            skipped += 1
            continue
        page = None
        page_id = str(a.get('page_id') or '').strip()
        if page_id:
            existing_page_id_count += 1
            page = {'id': page_id}
        else:
            url_lookup_count += 1
            page = url_index.get(url)
        if not page:
            skipped += 1
            continue
        found_pages += 1
        payload = build_props_payload(a, db_props, missing_props)
        if not payload:
            continue
        try:
            notion_req(token, "PATCH", f"https://api.notion.com/v1/pages/{page['id']}", json={"properties": payload})
            updated += 1
            time.sleep(0.2)
        except Exception:
            failed += 1

    print(f"score_update_target_count: {target_count}")
    print(f"score_update_found_pages: {found_pages}")
    print(f"score_update_updated: {updated}")
    print(f"score_update_skipped_no_page: {skipped}")
    print(f"score_update_failed: {failed}")
    print(f"score_update_existing_page_id_count: {existing_page_id_count}")
    print(f"score_update_url_lookup_count: {url_lookup_count}")
    print(f"missing_score_properties: {json.dumps(sorted(missing_props), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
