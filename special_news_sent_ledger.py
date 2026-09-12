from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LEDGER_TITLE = "SpecialistNewsSentLedger"
DEFAULT_PARENT_PAGE_ID = "2eddec27c9aa8098a6a9e50104794f7a"
SENT_LEDGER_DB_ID = (os.getenv("NOTION_SPECIAL_SENT_DB_ID") or "").strip()
SENT_LEDGER_PARENT_PAGE_ID = (os.getenv("NOTION_SPECIAL_SENT_PARENT_PAGE_ID") or DEFAULT_PARENT_PAGE_ID).strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
_RESOLVED_DB_ID: str | None = None


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


def _database_title(payload: dict[str, Any]) -> str:
    return "".join(part.get("plain_text", "") for part in payload.get("title", []))


def _database_is_accessible(database_id: str) -> bool:
    try:
        _request_json(f"https://api.notion.com/v1/databases/{database_id}", "GET")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def _search_ledger_database() -> str:
    payload = _request_json(
        "https://api.notion.com/v1/search",
        "POST",
        {
            "query": LEDGER_TITLE,
            "filter": {"property": "object", "value": "database"},
            "page_size": 100,
        },
    )
    for row in payload.get("results", []):
        if _database_title(row).strip() == LEDGER_TITLE:
            return str(row.get("id") or "").replace("-", "")
    return ""


def _create_ledger_database() -> str:
    if not SENT_LEDGER_PARENT_PAGE_ID:
        raise RuntimeError("NOTION_SPECIAL_SENT_PARENT_PAGE_ID is empty")
    payload = _request_json(
        "https://api.notion.com/v1/databases",
        "POST",
        {
            "parent": {"type": "page_id", "page_id": SENT_LEDGER_PARENT_PAGE_ID},
            "title": [{"type": "text", "text": {"content": LEDGER_TITLE}}],
            "properties": {
                "ArticleKey": {"title": {}},
                "Media": {
                    "select": {
                        "options": [
                            {"name": "鉄鋼新聞", "color": "blue"},
                            {"name": "日刊産業新聞", "color": "green"},
                        ]
                    }
                },
                "CanonicalURL": {"url": {}},
                "Headline": {"rich_text": {}},
                "PublishedAt": {"date": {}},
                "SentAt": {"date": {}},
                "DeliveryRunId": {"rich_text": {}},
                "Source": {
                    "select": {
                        "options": [
                            {"name": "direct", "color": "blue"},
                            {"name": "alert", "color": "yellow"},
                        ]
                    }
                },
            },
        },
    )
    database_id = str(payload.get("id") or "").replace("-", "")
    if not database_id:
        raise RuntimeError("Notion sent-ledger creation returned no database id")
    return database_id


def resolve_ledger_db_id() -> str:
    global _RESOLVED_DB_ID
    if _RESOLVED_DB_ID:
        return _RESOLVED_DB_ID

    if SENT_LEDGER_DB_ID and _database_is_accessible(SENT_LEDGER_DB_ID):
        _RESOLVED_DB_ID = SENT_LEDGER_DB_ID.replace("-", "")
        return _RESOLVED_DB_ID

    existing = _search_ledger_database()
    if existing:
        _RESOLVED_DB_ID = existing
        return existing

    _RESOLVED_DB_ID = _create_ledger_database()
    return _RESOLVED_DB_ID


def _title_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(part.get("plain_text", "") for part in prop.get("title", []))


def load_sent_keys(days: int = 30) -> set[str]:
    database_id = resolve_ledger_db_id()
    cutoff = (datetime.now(JST) - timedelta(days=days)).date().isoformat()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
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
    database_id = resolve_ledger_db_id()
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
                    "parent": {"database_id": database_id},
                    "properties": properties,
                },
            )
            created += 1
    return created
