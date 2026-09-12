from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_ARTICLES_DB_ID = "2eddec27-c9aa-8022-9699-c36467fd9477"
ARTICLES_DB_ID = (
    os.getenv("NOTION_ARTICLE_DB_ID")
    or os.getenv("NOTION_SPECIAL_SENT_DB_ID")
    or DEFAULT_ARTICLES_DB_ID
).strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN is required for specialist-news sent state")
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


def _rich_text_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(part.get("plain_text", "") for part in prop.get("rich_text", []))


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


def _rich_text_property(value: str) -> dict[str, Any]:
    return {
        "rich_text": [
            {
                "type": "text",
                "text": {"content": (value or "")[:2000]},
            }
        ]
    }


def _title_property(value: str) -> dict[str, Any]:
    return {
        "title": [
            {
                "type": "text",
                "text": {"content": (value or "")[:2000]},
            }
        ]
    }


def _query_one(filter_payload: dict[str, Any]) -> dict[str, Any] | None:
    payload = _request_json(
        f"https://api.notion.com/v1/databases/{ARTICLES_DB_ID}/query",
        "POST",
        {"page_size": 1, "filter": filter_payload},
    )
    rows = payload.get("results", [])
    return rows[0] if rows else None


def _find_article(article_key: str, canonical_url: str) -> dict[str, Any] | None:
    existing = _query_one(
        {
            "property": "ArticleId",
            "rich_text": {"equals": article_key},
        }
    )
    if existing:
        return existing
    if canonical_url:
        return _query_one(
            {
                "property": "NormalizedURL",
                "url": {"equals": canonical_url},
            }
        )
    return None


def load_sent_keys(days: int = 30) -> set[str]:
    if not ARTICLES_DB_ID:
        raise RuntimeError("NOTION_ARTICLE_DB_ID is empty")
    cutoff = (datetime.now(JST) - timedelta(days=days)).date().isoformat()
    endpoint = f"https://api.notion.com/v1/databases/{ARTICLES_DB_ID}/query"
    keys: set[str] = set()
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {
            "page_size": 100,
            "filter": {
                "and": [
                    {
                        "property": "Specialist Mail Sent",
                        "checkbox": {"equals": True},
                    },
                    {
                        "property": "Specialist Mail Sent At",
                        "date": {"on_or_after": cutoff},
                    },
                ]
            },
        }
        if cursor:
            body["start_cursor"] = cursor
        payload = _request_json(endpoint, "POST", body)
        for row in payload.get("results", []):
            props = row.get("properties") or {}
            key = _rich_text_value(props.get("ArticleId")).strip()
            if key:
                keys.add(key)
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return keys


def _delivery_properties(
    item: dict[str, Any],
    media_name: str,
    sent_at: datetime,
    delivery_run_id: str,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "ArticleId": _rich_text_property(str(item.get("article_key") or "")),
        "NormalizedURL": {"url": str(item.get("link") or "") or None},
        "Specialist Mail Sent": {"checkbox": True},
        "Specialist Mail Sent At": {
            "date": {"start": sent_at.astimezone(JST).isoformat()}
        },
        "Specialist Mail Run ID": _rich_text_property(delivery_run_id),
        "Specialist Mail Source": {
            "select": {"name": str(item.get("source") or "direct")}
        },
        "Specialist Media": {"select": {"name": media_name}},
    }
    published_value = _date_value(str(item.get("published") or ""))
    if published_value:
        properties["PublishedAt"] = {"date": published_value}
    return properties


def record_sent_articles(
    media_results: list[dict[str, Any]],
    sent_at: datetime,
    delivery_run_id: str,
) -> int:
    if not ARTICLES_DB_ID:
        raise RuntimeError("NOTION_ARTICLE_DB_ID is empty")
    recorded = 0
    for media in media_results:
        media_name = str(media.get("media_name") or "").strip()
        for item in media.get("items") or []:
            article_key = str(item.get("article_key") or "").strip()
            canonical_url = str(item.get("link") or "").strip()
            title = str(item.get("title") or "").strip()
            if not article_key:
                raise RuntimeError(f"missing article_key for sent item: {item!r}")

            properties = _delivery_properties(
                item,
                media_name=media_name,
                sent_at=sent_at,
                delivery_run_id=delivery_run_id,
            )
            existing = _find_article(article_key, canonical_url)
            if existing:
                page_id = str(existing.get("id") or "").strip()
                if not page_id:
                    raise RuntimeError(f"existing article has no page id: {article_key}")
                _request_json(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    "PATCH",
                    {"properties": properties},
                )
            else:
                create_properties = {
                    "Name": _title_property(title or article_key),
                    **properties,
                    "Source": _rich_text_property(media_name),
                    "Sector": {"multi_select": [{"name": "Steel"}]},
                    "PrimaryCountry": {"select": {"name": "Japan"}},
                }
                _request_json(
                    "https://api.notion.com/v1/pages",
                    "POST",
                    {
                        "parent": {"database_id": ARTICLES_DB_ID},
                        "properties": create_properties,
                    },
                )
            recorded += 1
    return recorded
