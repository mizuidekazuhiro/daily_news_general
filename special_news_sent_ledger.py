from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_SENT_LEDGER_DB_ID = "79ff3b471013426ab857dc2085fa9086"
SENT_LEDGER_DB_ID = os.getenv("NOTION_SPECIAL_SENT_DB_ID", DEFAULT_SENT_LEDGER_DB_ID).strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN is required for specialist-news sent ledger")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _request_json(url: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, method=method, headers=_headers(), data=data)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _title_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(part.get("plain_text", "") for part in prop.get("title", []))


def load_sent_keys(days: int = 30) -> set[str]:
    if not SENT_LEDGER_DB_ID:
        raise RuntimeError("NOTION_SPECIAL_SENT_DB_ID is empty")
    cutoff = (datetime.now(JST) - timedelta(days=days)).date().isoformat()
    url = f"https://api.notion.com/v1/databases/{SENT_LEDGER_DB_ID}/query"
    keys: set[str] = set()
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {
            "page_size": 100,
            "filter": {
                "property": "SentAt",
                "date": {"on_or_after": cutoff},
            },
        }
        if cursor:
            body["start_cursor"] = cursor
        payload = _request_json(url, "POST", body)
        for row in payload.get("results", []):
            key = _title_value((row.get("properties") or {}).get("ArticleKey")).strip()
            if key:
                keys.add(key)
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return keys


def _date_value(published: str) -> dict[str, str] | None:
    value = (published or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            return {"start": dt.date().isoformat()}
        return {"start": dt.replace(tzinfo=JST).isoformat()}
    return None


def _text_property(value: str) -> dict[str, Any]:
    return {
        "rich_text": [
            {
                "type": "text",
                "text": {"content": (value or "")[:2000]},
            }
        ]
    }


def record_sent_articles(
    media_results: list[dict[str, Any]],
    sent_at: datetime,
    delivery_run_id: str,
) -> int:
    if not SENT_LEDGER_DB_ID:
        raise RuntimeError("NOTION_SPECIAL_SENT_DB_ID is empty")
    created = 0
    endpoint = "https://api.notion.com/v1/pages"
    for media in media_results:
        media_name = str(media.get("media_name") or "").strip()
        for item in media.get("items") or []:
            key = str(item.get("article_key") or "").strip()
            if not key:
                raise RuntimeError(f"missing article_key for sent item: {item!r}")
            properties: dict[str, Any] = {
                "ArticleKey": {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": key[:2000]},
                        }
                    ]
                },
                "Media": {"select": {"name": media_name}},
                "CanonicalURL": {"url": str(item.get("link") or "") or None},
                "Headline": _text_property(str(item.get("title") or "")),
                "SentAt": {"date": {"start": sent_at.astimezone(JST).isoformat()}},
                "DeliveryRunId": _text_property(delivery_run_id),
                "Source": {"select": {"name": str(item.get("source") or "direct")}},
            }
            published_value = _date_value(str(item.get("published") or ""))
            if published_value:
                properties["PublishedAt"] = {"date": published_value}
            _request_json(
                endpoint,
                "POST",
                {
                    "parent": {"database_id": SENT_LEDGER_DB_ID},
                    "properties": properties,
                },
            )
            created += 1
    return created
