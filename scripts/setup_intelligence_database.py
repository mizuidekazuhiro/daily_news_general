from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests

NOTION_VERSION = "2022-06-28"
GENERAL_DB_ID = os.getenv("INTELLIGENCE_GENERAL_DB_ID", "2eddec27-c9aa-8022-9699-c36467fd9477")
NIKKEI_DB_ID = os.getenv("INTELLIGENCE_NIKKEI_DB_ID", "354dec27-c9aa-803e-bef1-f446abac9b2e")
CONTAINER_ARTICLE_ID = "SYSTEM_INTELLIGENCE_DB_CONTAINER"
DATABASE_TITLE = "Intelligence DB (GitHub)"


def clean_id(value: str) -> str:
    return str(value or "").replace("-", "").strip()


class Notion:
    def __init__(self, token: str, timeout: int = 30, max_retries: int = 5):
        self.session = requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        last_response: requests.Response | None = None
        for attempt in range(self.max_retries):
            response = self.session.request(method, url, headers=self.headers, timeout=self.timeout, **kwargs)
            last_response = response
            if response.ok:
                return response.json()
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else float(min(2 ** attempt, 8))
                    except (TypeError, ValueError):
                        wait = float(min(2 ** attempt, 8))
                    time.sleep(max(wait, 0.1))
                    continue
            raise RuntimeError(
                f"Notion {method} {url} failed: HTTP {response.status_code} {response.text[:500]}"
            )
        if last_response is None:
            raise RuntimeError(f"Notion {method} {url} failed without a response")
        raise RuntimeError(
            f"Notion {method} {url} failed after {self.max_retries} attempts: "
            f"HTTP {last_response.status_code} {last_response.text[:500]}"
        )

    def query_database(self, database_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"https://api.notion.com/v1/databases/{clean_id(database_id)}/query", json=payload)


def find_or_create_container(notion: Notion) -> str:
    data = notion.query_database(
        GENERAL_DB_ID,
        {
            "page_size": 10,
            "filter": {"property": "ArticleId", "rich_text": {"equals": CONTAINER_ARTICLE_ID}},
        },
    )
    for row in data.get("results") or []:
        if isinstance(row, dict) and row.get("id"):
            return str(row["id"])

    page = notion.request(
        "POST",
        "https://api.notion.com/v1/pages",
        json={
            "parent": {"database_id": clean_id(GENERAL_DB_ID)},
            "properties": {
                "Name": {"title": [{"type": "text", "text": {"content": "[SYSTEM] Intelligence DB Container"}}]},
                "ArticleId": {"rich_text": [{"type": "text", "text": {"content": CONTAINER_ARTICLE_ID}}]},
                "BodyPreview": {"rich_text": [{"type": "text", "text": {"content": "System container page for the GitHub-managed Intelligence DB. Not a news article."}}]},
                "Importance": {"select": {"name": "Low"}},
                "ImportanceScore": {"number": 0},
                "Source": {"rich_text": [{"type": "text", "text": {"content": "SYSTEM"}}]},
            },
        },
    )
    return str(page["id"])


def find_child_database(notion: Notion, parent_page_id: str) -> str | None:
    cursor: str | None = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{clean_id(parent_page_id)}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = notion.request("GET", url)
        for block in data.get("results") or []:
            if not isinstance(block, dict) or block.get("type") != "child_database":
                continue
            title = str((block.get("child_database") or {}).get("title") or "")
            if title == DATABASE_TITLE and block.get("id"):
                return str(block["id"])
        if not data.get("has_more"):
            return None
        cursor = data.get("next_cursor")
        if not cursor:
            return None


def create_database(notion: Notion, parent_page_id: str) -> str:
    properties: dict[str, Any] = {
        "Insight": {"title": {}},
        "Insight Key": {"rich_text": {}},
        "Status": {"select": {"options": [
            {"name": "Tracking", "color": "blue"},
            {"name": "Stable", "color": "green"},
            {"name": "Closed", "color": "gray"},
        ]}},
        "Importance": {"select": {"options": [
            {"name": "High", "color": "red"},
            {"name": "Medium", "color": "yellow"},
            {"name": "Low", "color": "gray"},
        ]}},
        "Confidence": {"select": {"options": [
            {"name": "High", "color": "green"},
            {"name": "Medium", "color": "yellow"},
            {"name": "Low", "color": "gray"},
        ]}},
        "Company": {"rich_text": {}},
        "Country": {"multi_select": {"options": [
            {"name": "India", "color": "orange"}, {"name": "Japan", "color": "gray"},
            {"name": "China", "color": "red"}, {"name": "United States", "color": "blue"},
            {"name": "EU", "color": "purple"}, {"name": "Vietnam", "color": "green"},
            {"name": "Thailand", "color": "yellow"}, {"name": "Indonesia", "color": "orange"},
            {"name": "Malaysia", "color": "green"}, {"name": "Philippines", "color": "blue"},
            {"name": "Korea", "color": "purple"}, {"name": "MENA", "color": "brown"},
            {"name": "Other", "color": "gray"},
        ]}},
        "Theme": {"multi_select": {"options": [
            {"name": "Capacity Expansion", "color": "brown"}, {"name": "EAF/Green Steel", "color": "green"},
            {"name": "JV/M&A", "color": "blue"}, {"name": "Demand", "color": "yellow"},
            {"name": "Pricing", "color": "orange"}, {"name": "Raw Materials", "color": "brown"},
            {"name": "Policy/Tariff", "color": "purple"}, {"name": "Decarbonization", "color": "green"},
            {"name": "Power/Energy", "color": "blue"}, {"name": "Supply Chain", "color": "gray"},
            {"name": "Technology", "color": "pink"}, {"name": "Financials", "color": "yellow"},
        ]}},
        "Event Type": {"select": {"options": [
            {"name": "New Plant", "color": "brown"}, {"name": "Capacity Expansion", "color": "orange"},
            {"name": "JV/M&A", "color": "blue"}, {"name": "Policy Change", "color": "purple"},
            {"name": "Market Shift", "color": "yellow"}, {"name": "Technology", "color": "pink"},
            {"name": "Financial Update", "color": "green"}, {"name": "Other", "color": "gray"},
        ]}},
        "Key Facts": {"rich_text": {}},
        "What Changed": {"rich_text": {}},
        "Business Implication": {"rich_text": {}},
        "Watch Items": {"rich_text": {}},
        "First Seen": {"date": {}},
        "Last Updated": {"date": {}},
        "Last Processed": {"date": {}},
        "Source Count": {"number": {}},
        "Nikkei Sources": {"relation": {"database_id": clean_id(NIKKEI_DB_ID), "type": "single_property", "single_property": {}}},
        "General Sources": {"relation": {"database_id": clean_id(GENERAL_DB_ID), "type": "single_property", "single_property": {}}},
        "Model": {"rich_text": {}},
    }
    data = notion.request(
        "POST",
        "https://api.notion.com/v1/databases",
        json={
            "parent": {"type": "page_id", "page_id": clean_id(parent_page_id)},
            "is_inline": True,
            "title": [{"type": "text", "text": {"content": DATABASE_TITLE}}],
            "properties": properties,
        },
    )
    return str(data["id"])


def resolve_database_id(notion: Notion) -> str:
    configured = os.getenv("NOTION_INTELLIGENCE_DB_ID", "").strip()
    if configured:
        notion.query_database(configured, {"page_size": 1})
        return configured

    parent = find_or_create_container(notion)
    database_id = find_child_database(notion, parent)
    if not database_id:
        database_id = create_database(notion, parent)
    notion.query_database(database_id, {"page_size": 1})
    return database_id


def main() -> int:
    token = os.getenv("NOTION_TOKEN", "").strip()
    if not token:
        raise RuntimeError("NOTION_TOKEN is required")
    notion = Notion(token)
    print(resolve_database_id(notion))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"setup_intelligence_database failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
