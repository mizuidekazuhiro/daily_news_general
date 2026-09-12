from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_SENT_LEDGER_DB_ID = "f16be6ad-2c7c-4b1b-9e3e-1d63c36a39d7"
SENT_LEDGER_DB_ID = (
    os.getenv("NOTION_SPECIAL_SENT_DB_ID")
    or DEFAULT_SENT_LEDGER_DB_ID
).strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN is required for specialist-news sent ledger")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _request_json(
    url: str,
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    retryable_statuses = {429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.request(
                method,
                url,
                headers=_headers(),
                json=payload,
                timeout=(10, 180),
            )
            if response.status_code in retryable_statuses:
                if attempt >= 3:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                wait = (
                    float(retry_after)
                    if retry_after
                    else min(8.0, 2.0 ** (attempt - 1))
                )
                time.sleep(max(1.0, wait))
                continue
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt >= 3:
                raise
            time.sleep(min(8.0, 2.0 ** (attempt - 1)))
    if last_error:
        raise last_error
    raise RuntimeError(f"Notion request failed without response: {method} {url}")


def _title_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(part.get("plain_text", "") for part in prop.get("title", []))


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


def _query_one_by_key(article_key: str) -> dict[str, Any] | None:
    payload = _request_json(
        f"https://api.notion.com/v1/databases/{SENT_LEDGER_DB_ID}/query",
        "POST",
        {
            "page_size": 1,
            "filter": {
                "property": "ArticleKey",
                "title": {"equals": article_key},
            },
        },
    )
    rows = payload.get("results", [])
    return rows[0] if rows else None


def load_sent_keys(days: int = 30) -> set[str]:
    if not SENT_LEDGER_DB_ID:
        raise RuntimeError("NOTION_SPECIAL_SENT_DB_ID is empty")
    cutoff = (datetime.now(JST) - timedelta(days=days)).date().isoformat()
    endpoint = f"https://api.notion.com/v1/databases/{SENT_LEDGER_DB_ID}/query"
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
        payload = _request_json(endpoint, "POST", body)
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


def _ledger_properties(
    item: dict[str, Any],
    media_name: str,
    sent_at: datetime,
    delivery_run_id: str,
) -> dict[str, Any]:
    article_key = str(item.get("article_key") or "").strip()
    properties: dict[str, Any] = {
        "ArticleKey": _title_property(article_key),
        "CanonicalURL": {"url": str(item.get("link") or "") or None},
        "Headline": _rich_text_property(str(item.get("title") or "")),
        "Media": {"select": {"name": media_name}},
        "Source": {"select": {"name": str(item.get("source") or "direct")}},
        "SentAt": {"date": {"start": sent_at.astimezone(JST).isoformat()}},
        "DeliveryRunId": _rich_text_property(delivery_run_id),
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
    if not SENT_LEDGER_DB_ID:
        raise RuntimeError("NOTION_SPECIAL_SENT_DB_ID is empty")
    recorded = 0
    for media in media_results:
        media_name = str(media.get("media_name") or "").strip()
        for item in media.get("items") or []:
            article_key = str(item.get("article_key") or "").strip()
            if not article_key:
                raise RuntimeError(f"missing article_key for sent item: {item!r}")

            properties = _ledger_properties(
                item,
                media_name=media_name,
                sent_at=sent_at,
                delivery_run_id=delivery_run_id,
            )
            existing = _query_one_by_key(article_key)
            if existing:
                page_id = str(existing.get("id") or "").strip()
                if not page_id:
                    raise RuntimeError(
                        f"existing ledger row has no page id: {article_key}"
                    )
                _request_json(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    "PATCH",
                    {"properties": properties},
                )
            else:
                _request_json(
                    "https://api.notion.com/v1/pages",
                    "POST",
                    {
                        "parent": {"database_id": SENT_LEDGER_DB_ID},
                        "properties": properties,
                    },
                )
            recorded += 1
    return recorded
