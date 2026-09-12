from __future__ import annotations

import os
import requests

token = os.getenv("NOTION_TOKEN", "").strip()
if not token:
    raise SystemExit("NOTION_TOKEN missing")

headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def title_of(row):
    if row.get("object") == "database":
        return "".join(x.get("plain_text", "") for x in row.get("title", []))
    props = row.get("properties") or {}
    title_prop = next((v for v in props.values() if isinstance(v, dict) and v.get("type") == "title"), {})
    return "".join(x.get("plain_text", "") for x in title_prop.get("title", []))


for query in ["DirectSiteUpdate", "SpecialNews", "Daily_report_log", "news", "Home"]:
    resp = requests.post(
        "https://api.notion.com/v1/search",
        headers=headers,
        json={"query": query, "page_size": 20},
        timeout=(10, 60),
    )
    resp.raise_for_status()
    print(f"QUERY={query}")
    for row in resp.json().get("results", []):
        print(f"{row.get('object')}\t{row.get('id')}\t{title_of(row)}")
