from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from src.openai_json_client import OpenAIJsonClient

NOTION_VERSION = "2022-06-28"
JST = ZoneInfo("Asia/Tokyo")

DEFAULT_NIKKEI_DB_ID = "354dec27-c9aa-803e-bef1-f446abac9b2e"
DEFAULT_GENERAL_DB_ID = "2eddec27-c9aa-8022-9699-c36467fd9477"
DEFAULT_INTELLIGENCE_DB_ID = "3f97b174-1c01-446c-8ebf-65e511f92621"

ALLOWED_COUNTRIES = {
    "India", "Japan", "China", "United States", "EU", "Vietnam", "Thailand",
    "Indonesia", "Malaysia", "Philippines", "Korea", "MENA", "Other",
}
ALLOWED_THEMES = {
    "Capacity Expansion", "EAF/Green Steel", "JV/M&A", "Demand", "Pricing",
    "Raw Materials", "Policy/Tariff", "Decarbonization", "Power/Energy",
    "Supply Chain", "Technology", "Financials",
}
ALLOWED_EVENT_TYPES = {
    "New Plant", "Capacity Expansion", "JV/M&A", "Policy Change",
    "Market Shift", "Technology", "Financial Update", "Other",
}
ALLOWED_LEVELS = {"High", "Medium", "Low"}
NOISE_TEXTS = (
    "please enable js", "disable any ad blocker", "javascript is disabled",
    "利用規約", "プライバシーポリシー",
)


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or not value.strip() else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or not value.strip() else float(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _today_jst() -> date:
    return datetime.now(JST).date()


def _clean_id(value: str) -> str:
    return str(value or "").replace("-", "").strip()


def _hyphenate_id(value: str) -> str:
    raw = _clean_id(value)
    if len(raw) != 32:
        return str(value or "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _is_useful_text(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) < 80:
        return False
    low = text.lower()
    if any(noise in low for noise in NOISE_TEXTS) and len(text) < 300:
        return False
    return True


def _rich_text_plain(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("plain_text"), str):
            parts.append(item["plain_text"])
        elif isinstance(item.get("text"), dict) and isinstance(item["text"].get("content"), str):
            parts.append(item["text"]["content"])
    return "".join(parts).strip()


def _prop_text(props: dict[str, Any], name: str) -> str:
    prop = props.get(name) or {}
    ptype = prop.get("type")
    if ptype == "title":
        return _rich_text_plain(prop.get("title"))
    if ptype == "rich_text":
        return _rich_text_plain(prop.get("rich_text"))
    if ptype == "url":
        return str(prop.get("url") or "").strip()
    if ptype in {"select", "status"}:
        value = prop.get(ptype) or {}
        return str(value.get("name") or "").strip()
    return ""


def _prop_number(props: dict[str, Any], name: str) -> float:
    try:
        return float((props.get(name) or {}).get("number") or 0)
    except Exception:
        return 0.0


def _prop_date(props: dict[str, Any], name: str) -> str:
    return str(((props.get(name) or {}).get("date") or {}).get("start") or "").strip()


def _prop_multi(props: dict[str, Any], name: str) -> list[str]:
    values = (props.get(name) or {}).get("multi_select") or []
    return [str(x.get("name")) for x in values if isinstance(x, dict) and x.get("name")]


def _prop_relations(props: dict[str, Any], name: str) -> list[str]:
    values = (props.get(name) or {}).get("relation") or []
    return [_hyphenate_id(str(x["id"])) for x in values if isinstance(x, dict) and x.get("id")]


def _parse_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None


def _max_date(values: Iterable[str], default: date) -> date:
    parsed = [x for x in (_parse_date(v) for v in values) if x is not None]
    return max(parsed) if parsed else default


def _min_date(values: Iterable[str], default: date) -> date:
    parsed = [x for x in (_parse_date(v) for v in values) if x is not None]
    return min(parsed) if parsed else default


@dataclass
class Article:
    source: str
    page_id: str
    title: str
    published_at: str
    importance_score: float
    source_name: str
    country: list[str]
    tags: list[str]
    body: str
    notion_url: str

    def ref(self) -> dict[str, str]:
        return {"source": self.source, "page_id": self.page_id, "published_at": self.published_at}

    def to_prompt(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "page_id": self.page_id,
            "title": self.title,
            "published_at": self.published_at,
            "importance_score": self.importance_score,
            "source_name": self.source_name,
            "country": self.country,
            "tags": self.tags,
            "body": self.body,
        }


@dataclass
class Insight:
    page_id: str
    insight: str
    insight_key: str
    status: str
    importance: str
    confidence: str
    company: str
    country: list[str]
    theme: list[str]
    event_type: str
    key_facts: str
    what_changed: str
    business_implication: str
    watch_items: str
    first_seen: str
    last_updated: str
    last_processed: str
    nikkei_sources: list[str]
    general_sources: list[str]
    source_count: int
    model: str

    def to_prompt(self) -> dict[str, Any]:
        return {
            "insight": self.insight,
            "insight_key": self.insight_key,
            "status": self.status,
            "importance": self.importance,
            "confidence": self.confidence,
            "company": self.company,
            "country": self.country,
            "theme": self.theme,
            "event_type": self.event_type,
            "key_facts": _truncate(self.key_facts, 700),
            "what_changed": _truncate(self.what_changed, 500),
            "business_implication": _truncate(self.business_implication, 600),
            "watch_items": _truncate(self.watch_items, 400),
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "source_count": self.source_count,
        }


class NotionClient:
    def __init__(self, token: str, timeout: int = 30, max_retries: int = 4):
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, headers=self.headers, timeout=self.timeout, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    wait = float(response.headers.get("Retry-After") or min(2 ** attempt, 8))
                    logging.warning("notion_retry status=%s attempt=%s wait=%s", response.status_code, attempt + 1, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Notion request failed: {last_error}")

    def query_database(
        self,
        database_id: str,
        filter_obj: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        max_pages: int = 30,
    ) -> list[dict[str, Any]]:
        url = f"https://api.notion.com/v1/databases/{_clean_id(database_id)}/query"
        body: dict[str, Any] = {"page_size": 100}
        if filter_obj:
            body["filter"] = filter_obj
        if sorts:
            body["sorts"] = sorts
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            payload = dict(body)
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request("POST", url, json=payload).json()
            results.extend(x for x in (data.get("results") or []) if isinstance(x, dict))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return results

    def get_page_text(self, page_id: str, max_chars: int = 5000) -> str:
        cursor: str | None = None
        parts: list[str] = []
        while len("\n".join(parts)) < max_chars:
            url = f"https://api.notion.com/v1/blocks/{_clean_id(page_id)}/children?page_size=100"
            if cursor:
                url += f"&start_cursor={cursor}"
            data = self._request("GET", url).json()
            for block in data.get("results") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                content = block.get(btype) if btype else None
                if isinstance(content, dict):
                    text = _rich_text_plain(content.get("rich_text"))
                    if text:
                        parts.append(text)
                if len("\n".join(parts)) >= max_chars:
                    break
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return _truncate("\n".join(parts), max_chars)

    def create_page(self, database_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "https://api.notion.com/v1/pages",
            json={"parent": {"database_id": _clean_id(database_id)}, "properties": properties},
        ).json()

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"https://api.notion.com/v1/pages/{_clean_id(page_id)}",
            json={"properties": properties},
        ).json()


def _load_nikkei_articles(notion: NotionClient, db_id: str, cutoff: date, min_score: float, body_chars: int) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "Issue Date", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "Importance Score", "number": {"greater_than_or_equal_to": min_score}},
            {"property": "Full Text Status", "select": {"equals": "saved"}},
        ]},
        sorts=[
            {"property": "Importance Score", "direction": "descending"},
            {"property": "Issue Date", "direction": "descending"},
        ],
    )
    out: list[Article] = []
    for row in rows:
        props = row.get("properties") or {}
        page_id = _hyphenate_id(str(row.get("id") or ""))
        if not page_id:
            continue
        summary = _prop_text(props, "Summary")
        body = summary if _is_useful_text(summary) else ""
        if not _is_useful_text(body):
            try:
                body = notion.get_page_text(page_id, body_chars)
            except Exception as exc:
                logging.warning("nikkei_body_fetch_failed page_id=%s error=%s", page_id, exc)
                body = summary
        if not _is_useful_text(body):
            continue
        tags = sorted(set(_prop_multi(props, "Tags") + _prop_multi(props, "Matched Rules")))
        out.append(Article(
            source="nikkei",
            page_id=page_id,
            title=_prop_text(props, "Title"),
            published_at=_prop_date(props, "Issue Date"),
            importance_score=_prop_number(props, "Importance Score"),
            source_name=_prop_text(props, "Source") or "Nikkei",
            country=[x for x in _prop_multi(props, "Tags") if x in ALLOWED_COUNTRIES],
            tags=tags,
            body=_truncate(body, body_chars),
            notion_url=str(row.get("url") or ""),
        ))
    return out


def _load_general_articles(notion: NotionClient, db_id: str, cutoff: date, min_score: float, body_chars: int) -> list[Article]:
    rows = notion.query_database(
        db_id,
        filter_obj={"and": [
            {"property": "PublishedAt", "date": {"on_or_after": cutoff.isoformat()}},
            {"property": "ImportanceScore", "number": {"greater_than_or_equal_to": min_score}},
        ]},
        sorts=[
            {"property": "ImportanceScore", "direction": "descending"},
            {"property": "PublishedAt", "direction": "descending"},
        ],
    )
    out: list[Article] = []
    for row in rows:
        props = row.get("properties") or {}
        page_id = _hyphenate_id(str(row.get("id") or ""))
        if not page_id:
            continue
        preview = _prop_text(props, "BodyPreview")
        body = preview if _is_useful_text(preview) else ""
        if not _is_useful_text(body):
            try:
                body = notion.get_page_text(page_id, body_chars)
            except Exception as exc:
                logging.warning("general_body_fetch_failed page_id=%s error=%s", page_id, exc)
                body = preview
        if not _is_useful_text(body):
            continue
        tags = [x for x in [_prop_text(props, "Label"), _prop_text(props, "Type"), _prop_text(props, "PrimaryCountry")] if x]
        out.append(Article(
            source="general",
            page_id=page_id,
            title=_prop_text(props, "Name"),
            published_at=_prop_date(props, "PublishedAt"),
            importance_score=_prop_number(props, "ImportanceScore"),
            source_name=_prop_text(props, "Source"),
            country=[x for x in _prop_multi(props, "Country") if x in ALLOWED_COUNTRIES],
            tags=tags,
            body=_truncate(body, body_chars),
            notion_url=str(row.get("url") or ""),
        ))
    return out


def _load_existing_insights(notion: NotionClient, db_id: str, max_existing: int) -> list[Insight]:
    rows = notion.query_database(
        db_id,
        filter_obj={"property": "Status", "select": {"does_not_equal": "Closed"}},
        sorts=[{"property": "Last Updated", "direction": "descending"}],
    )
    out: list[Insight] = []
    for row in rows[:max_existing]:
        props = row.get("properties") or {}
        out.append(Insight(
            page_id=_hyphenate_id(str(row.get("id") or "")),
            insight=_prop_text(props, "Insight"),
            insight_key=_prop_text(props, "Insight Key"),
            status=_prop_text(props, "Status") or "Tracking",
            importance=_prop_text(props, "Importance") or "Medium",
            confidence=_prop_text(props, "Confidence") or "Medium",
            company=_prop_text(props, "Company"),
            country=[x for x in _prop_multi(props, "Country") if x in ALLOWED_COUNTRIES],
            theme=[x for x in _prop_multi(props, "Theme") if x in ALLOWED_THEMES],
            event_type=_prop_text(props, "Event Type") or "Other",
            key_facts=_prop_text(props, "Key Facts"),
            what_changed=_prop_text(props, "What Changed"),
            business_implication=_prop_text(props, "Business Implication"),
            watch_items=_prop_text(props, "Watch Items"),
            first_seen=_prop_date(props, "First Seen"),
            last_updated=_prop_date(props, "Last Updated"),
            last_processed=_prop_date(props, "Last Processed"),
            nikkei_sources=_prop_relations(props, "Nikkei Sources"),
            general_sources=_prop_relations(props, "General Sources"),
            source_count=int(_prop_number(props, "Source Count")),
            model=_prop_text(props, "Model"),
        ))
    return out


def _already_linked_ids(insights: list[Insight]) -> set[str]:
    out: set[str] = set()
    for insight in insights:
        out.update(_clean_id(x) for x in insight.nikkei_sources)
        out.update(_clean_id(x) for x in insight.general_sources)
    return out


def select_candidates(articles: list[Article], insights: list[Insight], max_candidates: int) -> tuple[list[Article], int]:
    linked = _already_linked_ids(insights)
    unseen = [a for a in articles if _clean_id(a.page_id) not in linked]
    unseen.sort(key=lambda a: (a.importance_score, a.published_at, a.title), reverse=True)
    return unseen[:max_candidates], len(articles) - len(unseen)


def _prompt_system() -> str:
    return f"""
You maintain a Notion Intelligence DB for a business news workflow.
Return STRICT JSON only. No Markdown or explanatory text outside JSON.

Input:
- new_articles: recent source articles not yet linked to Intelligence
- existing_insights: ongoing Intelligence rows that may need updating

Output schema:
{{"operations":[{{
  "action":"create"|"update"|"noop",
  "matched_existing_key":string|null,
  "insight_key":string,
  "insight":string,
  "company":string,
  "country":[string],
  "theme":[string],
  "event_type":string,
  "importance":"High"|"Medium"|"Low",
  "confidence":"High"|"Medium"|"Low",
  "key_facts":string,
  "what_changed":string,
  "business_implication":string,
  "watch_items":string,
  "article_refs":[{{"source":"nikkei"|"general","page_id":string,"published_at":string}}]
}}]}}

Rules:
1. Intelligence is event/strategy-centric, not article-centric. Several articles about one continuing event or strategy must update one row rather than create duplicates.
2. Use update when a new article materially changes, confirms, narrows, or extends an existing insight. matched_existing_key must exactly equal an input existing insight_key.
3. Use create only for a genuinely new durable event/strategy.
4. Use noop if there is no durable business-intelligence value or no meaningful new information.
5. Every non-noop operation must contain at least one exact new_articles reference. Group multiple articles about the same event into one operation.
6. key_facts = source-supported facts only. what_changed = delta versus prior knowledge. business_implication = analysis, not fact.
7. Never fabricate numbers, companies, dates, locations, product scope, causality, or certainty. Put unknowns in what_changed/watch_items.
8. For updates preserve the existing insight_key. For creates prefer stable lowercase ASCII keys such as company|country|topic|timeframe.
9. Keep fields concise and specific; avoid generic consulting prose.
10. Country values: {sorted(ALLOWED_COUNTRIES)}
11. Theme values: {sorted(ALLOWED_THEMES)}
12. Event type values: {sorted(ALLOWED_EVENT_TYPES)}
""".strip()


def _normalize_list(value: Any, allowed: set[str]) -> list[str]:
    raw = value if isinstance(value, list) else []
    out = []
    for item in raw:
        text = str(item or "").strip()
        if text in allowed and text not in out:
            out.append(text)
    return out


def normalize_operations(raw: Any, candidates: list[Article], existing: list[Insight]) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("operations"), list):
        return []
    article_map = {(a.source, _clean_id(a.page_id)): a for a in candidates}
    existing_keys = {x.insight_key for x in existing if x.insight_key}
    out: list[dict[str, Any]] = []
    for item in raw["operations"]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in {"create", "update", "noop"}:
            continue
        refs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ref in item.get("article_refs") or []:
            if not isinstance(ref, dict):
                continue
            source = str(ref.get("source") or "").strip().lower()
            page_id = _hyphenate_id(str(ref.get("page_id") or ""))
            key = (source, _clean_id(page_id))
            article = article_map.get(key)
            if article and key not in seen:
                seen.add(key)
                refs.append(article.ref())
        if action == "noop":
            out.append({"action": "noop", "article_refs": refs})
            continue
        if not refs:
            continue
        matched = str(item.get("matched_existing_key") or "").strip()
        insight_key = str(item.get("insight_key") or "").strip()
        if action == "update":
            if matched not in existing_keys:
                continue
            insight_key = matched
        elif not insight_key:
            continue
        importance = str(item.get("importance") or "Medium").strip()
        confidence = str(item.get("confidence") or "Medium").strip()
        event_type = str(item.get("event_type") or "Other").strip()
        out.append({
            "action": action,
            "matched_existing_key": matched or None,
            "insight_key": insight_key,
            "insight": _truncate(str(item.get("insight") or ""), 180),
            "company": _truncate(str(item.get("company") or ""), 300),
            "country": _normalize_list(item.get("country"), ALLOWED_COUNTRIES),
            "theme": _normalize_list(item.get("theme"), ALLOWED_THEMES),
            "event_type": event_type if event_type in ALLOWED_EVENT_TYPES else "Other",
            "importance": importance if importance in ALLOWED_LEVELS else "Medium",
            "confidence": confidence if confidence in ALLOWED_LEVELS else "Medium",
            "key_facts": _truncate(str(item.get("key_facts") or ""), 1900),
            "what_changed": _truncate(str(item.get("what_changed") or ""), 1900),
            "business_implication": _truncate(str(item.get("business_implication") or ""), 1900),
            "watch_items": _truncate(str(item.get("watch_items") or ""), 1900),
            "article_refs": refs,
        })
    return out


def _title_prop(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": _truncate(value, 180)}}]}


def _rich_prop(value: str) -> dict[str, Any]:
    text = _truncate(value, 1900)
    return {"rich_text": [] if not text else [{"type": "text", "text": {"content": text}}]}


def _select_prop(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def _multi_prop(values: list[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": x} for x in values]}


def _date_prop(value: date | str) -> dict[str, Any]:
    return {"date": {"start": value.isoformat() if isinstance(value, date) else str(value)}}


def _relation_prop(ids: list[str]) -> dict[str, Any]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in ids:
        key = _clean_id(value)
        if key and key not in seen:
            seen.add(key)
            unique.append(_hyphenate_id(value))
    return {"relation": [{"id": x} for x in unique]}


def _merge_unique_ids(old: list[str], new: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in [*old, *new]:
        key = _clean_id(value)
        if key and key not in seen:
            seen.add(key)
            out.append(_hyphenate_id(value))
    return out


def _operation_source_ids(operation: dict[str, Any]) -> tuple[list[str], list[str]]:
    nikkei: list[str] = []
    general: list[str] = []
    for ref in operation.get("article_refs") or []:
        page_id = _hyphenate_id(str(ref.get("page_id") or ""))
        if ref.get("source") == "nikkei" and page_id:
            nikkei.append(page_id)
        elif ref.get("source") == "general" and page_id:
            general.append(page_id)
    return nikkei, general


def _properties_for_operation(operation: dict[str, Any], model: str, existing: Insight | None = None) -> dict[str, Any]:
    today = _today_jst()
    dates = [str(x.get("published_at") or "") for x in operation.get("article_refs") or []]
    first_new = _min_date(dates, today)
    last_new = _max_date(dates, today)
    new_nikkei, new_general = _operation_source_ids(operation)
    if existing:
        nikkei = _merge_unique_ids(existing.nikkei_sources, new_nikkei)
        general = _merge_unique_ids(existing.general_sources, new_general)
        first_seen = min(x for x in [_parse_date(existing.first_seen), first_new] if x is not None)
        last_updated = max(x for x in [_parse_date(existing.last_updated), last_new] if x is not None)
    else:
        nikkei = _merge_unique_ids([], new_nikkei)
        general = _merge_unique_ids([], new_general)
        first_seen, last_updated = first_new, last_new
    return {
        "Insight": _title_prop(operation.get("insight") or operation["insight_key"]),
        "Insight Key": _rich_prop(operation["insight_key"]),
        "Status": _select_prop("Tracking"),
        "Importance": _select_prop(operation["importance"]),
        "Confidence": _select_prop(operation["confidence"]),
        "Company": _rich_prop(operation.get("company") or ""),
        "Country": _multi_prop(operation.get("country") or []),
        "Theme": _multi_prop(operation.get("theme") or []),
        "Event Type": _select_prop(operation.get("event_type") or "Other"),
        "Key Facts": _rich_prop(operation.get("key_facts") or ""),
        "What Changed": _rich_prop(operation.get("what_changed") or ""),
        "Business Implication": _rich_prop(operation.get("business_implication") or ""),
        "Watch Items": _rich_prop(operation.get("watch_items") or ""),
        "First Seen": _date_prop(first_seen),
        "Last Updated": _date_prop(last_updated),
        "Last Processed": _date_prop(today),
        "Source Count": {"number": len(nikkei) + len(general)},
        "Nikkei Sources": _relation_prop(nikkei),
        "General Sources": _relation_prop(general),
        "Model": _rich_prop(model),
    }


def apply_operations(
    notion: NotionClient,
    intelligence_db_id: str,
    operations: list[dict[str, Any]],
    existing: list[Insight],
    model: str,
    dry_run: bool,
) -> dict[str, Any]:
    by_key = {x.insight_key: x for x in existing if x.insight_key}
    created = updated = noops = 0
    errors: list[dict[str, str]] = []
    applied: list[dict[str, Any]] = []
    for operation in operations:
        if operation["action"] == "noop":
            noops += 1
            applied.append({"action": "noop", "article_refs": operation.get("article_refs", [])})
            continue
        key = operation["insight_key"]
        matched = by_key.get(key)
        action = "update" if operation["action"] == "update" or matched else "create"
        try:
            props = _properties_for_operation(operation, model, matched if action == "update" else None)
            if dry_run:
                page_id = matched.page_id if matched else "dry-run-new-page"
            elif action == "update":
                if not matched:
                    raise ValueError(f"Cannot update missing insight key: {key}")
                page = notion.update_page(matched.page_id, props)
                page_id = _hyphenate_id(str(page.get("id") or matched.page_id))
            else:
                page = notion.create_page(intelligence_db_id, props)
                page_id = _hyphenate_id(str(page.get("id") or ""))
            if action == "update":
                updated += 1
            else:
                created += 1
            applied.append({"action": action, "insight_key": key, "page_id": page_id, "article_refs": operation.get("article_refs", [])})
        except Exception as exc:
            logging.exception("intelligence_apply_failed action=%s key=%s", action, key)
            errors.append({"action": action, "insight_key": key, "error": f"{type(exc).__name__}: {exc}"})
    return {"created": created, "updated": updated, "noops": noops, "errors": errors, "applied": applied}


def _input_hash(candidates: list[Article], existing: list[Insight]) -> str:
    payload = {
        "candidate_ids": sorted(_clean_id(x.page_id) for x in candidates),
        "existing": sorted((x.insight_key, x.last_updated) for x in existing if x.insight_key),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = _env_str("NOTION_TOKEN")
    api_key = _env_str("OPENAI_API_KEY")
    if not token:
        raise RuntimeError("NOTION_TOKEN is required")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    nikkei_db = _env_str("INTELLIGENCE_NIKKEI_DB_ID", DEFAULT_NIKKEI_DB_ID)
    general_db = _env_str("INTELLIGENCE_GENERAL_DB_ID", DEFAULT_GENERAL_DB_ID)
    intelligence_db = _env_str("NOTION_INTELLIGENCE_DB_ID", DEFAULT_INTELLIGENCE_DB_ID)
    model = _env_str("INTELLIGENCE_MODEL", "gpt-5-mini")
    lookback_days = max(1, _env_int("INTELLIGENCE_LOOKBACK_DAYS", 2))
    min_score = _env_float("INTELLIGENCE_MIN_IMPORTANCE_SCORE", 4.0)
    max_candidates = max(1, _env_int("INTELLIGENCE_MAX_CANDIDATES", 10))
    max_existing = max(1, _env_int("INTELLIGENCE_MAX_EXISTING_INSIGHTS", 100))
    body_chars = max(500, _env_int("INTELLIGENCE_ARTICLE_BODY_CHARS", 3500))
    max_output_tokens = max(1000, _env_int("INTELLIGENCE_MAX_OUTPUT_TOKENS", 7000))
    dry_run = _env_bool("INTELLIGENCE_DRY_RUN", False)

    today = _today_jst()
    cutoff = today - timedelta(days=lookback_days)
    logs = Path("logs")
    logs.mkdir(parents=True, exist_ok=True)
    notion = NotionClient(token)

    existing = _load_existing_insights(notion, intelligence_db, max_existing)
    nikkei = _load_nikkei_articles(notion, nikkei_db, cutoff, min_score, body_chars)
    general = _load_general_articles(notion, general_db, cutoff, min_score, body_chars)
    all_articles = [*nikkei, *general]
    candidates, already_linked_count = select_candidates(all_articles, existing, max_candidates)

    input_summary = {
        "run_date_jst": today.isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "lookback_days": lookback_days,
        "min_importance_score": min_score,
        "nikkei_loaded": len(nikkei),
        "general_loaded": len(general),
        "already_linked_count": already_linked_count,
        "candidate_count": len(candidates),
        "existing_insight_count": len(existing),
        "candidate_titles": [x.title for x in candidates],
        "input_hash": _input_hash(candidates, existing),
        "dry_run": dry_run,
    }
    _write_json(logs / "intelligence_input_summary.json", input_summary)

    if not candidates:
        summary = {**input_summary, "skipped": True, "skip_reason": "no_unlinked_high_importance_articles", "created": 0, "updated": 0, "noops": 0, "errors": []}
        _write_json(logs / "intelligence_summary.json", summary)
        logging.info("intelligence_skipped reason=no_unlinked_high_importance_articles")
        return summary

    prompt_payload = {
        "run_date_jst": today.isoformat(),
        "new_articles": [x.to_prompt() for x in candidates],
        "existing_insights": [x.to_prompt() for x in existing],
    }
    _write_json(logs / "intelligence_prompt_input.json", prompt_payload)
    client = OpenAIJsonClient(api_key)
    raw_output = client.generate_json(
        model=model,
        system_prompt=_prompt_system(),
        user_prompt=json.dumps(prompt_payload, ensure_ascii=False),
        max_output_tokens=max_output_tokens,
        temperature=0.2,
    )
    _write_json(logs / "intelligence_gpt_output.json", raw_output)
    operations = normalize_operations(raw_output, candidates, existing)
    if not operations:
        raise RuntimeError("GPT returned no valid Intelligence operations")

    result = apply_operations(notion, intelligence_db, operations, existing, model, dry_run)
    summary = {**input_summary, "skipped": False, "operation_count": len(operations), **result}
    _write_json(logs / "intelligence_summary.json", summary)
    logging.info(
        "intelligence_complete candidates=%s operations=%s created=%s updated=%s noops=%s errors=%s dry_run=%s",
        len(candidates), len(operations), result["created"], result["updated"], result["noops"], len(result["errors"]), dry_run,
    )
    if result["errors"]:
        raise RuntimeError(f"Intelligence apply completed with {len(result['errors'])} errors")
    return summary


def main() -> int:
    try:
        run()
        return 0
    except Exception as exc:
        logging.exception("intelligence_pipeline_failed")
        logs = Path("logs")
        logs.mkdir(parents=True, exist_ok=True)
        _write_json(logs / "intelligence_failure.json", {
            "failed": True,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "run_date_jst": _today_jst().isoformat(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
