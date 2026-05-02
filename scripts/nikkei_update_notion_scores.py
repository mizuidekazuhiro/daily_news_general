import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = Path("logs/nikkei_articles_scored.json")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
DATABASE_ID = (os.getenv("NIKKEI_ARTICLES_DB_ID", "") or os.getenv("NOTION_ARTICLE_DB_ID", "")).strip()
ENABLED = os.getenv("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "false").lower() == "true"
NOTION_VERSION = "2022-06-28"


def headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def req(method, url, **kwargs):
    for i in range(6):
        r = requests.request(method, url, headers=headers(), timeout=60, **kwargs)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def query_by_url(url):
    payload = {"filter": {"property": "URL", "url": {"equals": url}}, "page_size": 1}
    r = req("POST", f"https://api.notion.com/v1/databases/{DATABASE_ID}/query", json=payload)
    res = r.json().get("results", [])
    return res[0] if res else None


def db_props():
    r = req("GET", f"https://api.notion.com/v1/databases/{DATABASE_ID}")
    return r.json().get("properties", {})


def to_multi(values):
    return [{"name": str(v)[:100]} for v in values if str(v).strip()]


def main() -> int:
    if not ENABLED:
        print("skip notion score update: NIKKEI_ENABLE_NOTION_SCORE_UPDATE=false")
        return 0
    if not NOTION_TOKEN or not DATABASE_ID:
        raise RuntimeError("NOTION_TOKEN / DB ID missing")
    if not INPUT_JSON.exists():
        raise FileNotFoundError(INPUT_JSON)

    props = db_props()
    items = json.loads(INPUT_JSON.read_text(encoding="utf-8"))

    updated = 0
    for a in items:
        url = a.get("url")
        if not url:
            continue
        page = query_by_url(url)
        if not page:
            continue

        payload = {}
        def ptype(name): return props.get(name, {}).get("type")

        if "Importance Score" in props and ptype("Importance Score") == "number":
            payload["Importance Score"] = {"number": float(a.get("importance_score") or 0)}
        if "Priority" in props and ptype("Priority") == "number":
            payload["Priority"] = {"number": int(a.get("priority") or 0)}
        if "Tags" in props:
            if ptype("Tags") == "multi_select":
                payload["Tags"] = {"multi_select": to_multi(a.get("tags", []))}
            elif ptype("Tags") == "rich_text":
                payload["Tags"] = {"rich_text": [{"text": {"content": ", ".join(a.get("tags", []))[:2000]}}]}
        if "Reason to Read" in props and ptype("Reason to Read") == "rich_text":
            payload["Reason to Read"] = {"rich_text": [{"text": {"content": str(a.get("reason_to_read") or "")[:2000]}}]}
        if "Notes" in props and ptype("Notes") == "rich_text":
            txt = f"exclude_candidate={a.get('exclude_candidate')} reason={a.get('exclude_reason','')}"
            payload["Notes"] = {"rich_text": [{"text": {"content": txt[:2000]}}]}

        if not payload:
            continue
        req("PATCH", f"https://api.notion.com/v1/pages/{page['id']}", json={"properties": payload})
        updated += 1
        time.sleep(0.25)

    print(f"updated notion pages: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
