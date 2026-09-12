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
payload = {
    "page_size": 50,
    "sort": {"direction": "descending", "timestamp": "last_edited_time"},
}
resp = requests.post("https://api.notion.com/v1/search", headers=headers, json=payload, timeout=(10, 60))
resp.raise_for_status()
for row in resp.json().get("results", []):
    obj = row.get("object")
    rid = row.get("id")
    if obj == "database":
        title = "".join(x.get("plain_text", "") for x in row.get("title", []))
    else:
        props = row.get("properties") or {}
        title_prop = next((v for v in props.values() if v.get("type") == "title"), {})
        title = "".join(x.get("plain_text", "") for x in title_prop.get("title", []))
    print(f"{obj}\t{rid}\t{title}")
