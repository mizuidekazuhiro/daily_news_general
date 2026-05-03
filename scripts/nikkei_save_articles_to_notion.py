import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
DATABASE_ID = (os.getenv("NIKKEI_ARTICLES_DB_ID", "") or os.getenv("NOTION_ARTICLE_DB_ID", "")).strip()
INPUT_JSON = Path("logs/nikkei_articles_full.json")
FAILED_LOG_JSON = Path("logs/nikkei_save_failed.json")
NOTION_VERSION = "2022-06-28"

SUMMARY_SOURCE_FIELDS = ["summary", "description", "meta_description", "body_summary"]
BODY_PROP_NAMES = ["Body", "Article Body", "Article Text", "Text", "Content", "本文", "記事本文", "Scoring Text", "スコアリング用本文"]
SUMMARY_PROP_NAMES = ["Summary", "要約", "AI Summary"]
ISSUE_DATE_PROP_NAMES = ["Issue Date", "Issued Date", "Published Date"]


def headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def req(method, url, **kwargs):
    for _ in range(6):
        r = requests.request(method, url, headers=headers(), timeout=60, **kwargs)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def ng(url):
    return (parse_qs(urlparse(url).query).get("ng") or [""])[0]


def load_existing():
    keys = set()
    pages = {}
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        d = req("POST", f"https://api.notion.com/v1/databases/{DATABASE_ID}/query", json=payload).json()
        for it in d.get("results", []):
            p = it.get("properties", {}).get("URL", {})
            u = ""
            if p.get("type") == "url":
                u = p.get("url") or ""
            elif p.get("type") == "rich_text":
                u = "".join(x.get("plain_text", "") for x in p.get("rich_text", []))
            if u:
                keys.add(u)
                nid = ng(u)
                if nid:
                    keys.add(nid)
                pages[u] = it.get("id", "")
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return keys, pages


def resolve_prop(props, names, allowed=("rich_text",)):
    for name in names:
        meta = props.get(name)
        if meta and meta.get("type") in allowed:
            return name
    return None


def split_blocks(text, limit=1800):
    out = []
    cur = ""
    for ln in [x.strip() for x in (text or "").splitlines() if x.strip()]:
        if cur and len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ln
        else:
            cur = (cur + "\n" + ln).strip()
    if cur:
        out.append(cur)
    return out


def append_body_blocks(page_id, text):
    chunks = split_blocks(text)
    if not chunks:
        return 0
    children = [{
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "記事本文"}}]},
    }]
    children += [{
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]},
    } for c in chunks]

    appended = 0
    for i in range(0, len(children), 100):
        packet = children[i:i + 100]
        req("PATCH", f"https://api.notion.com/v1/blocks/{page_id}/children", json={"children": packet})
        appended += len(packet)
    return appended


def get_summary_text(article):
    for key in SUMMARY_SOURCE_FIELDS:
        value = (article.get(key) or "").strip()
        if value:
            return value
    return ""


def set_typed_prop(prop_meta, value):
    if not value:
        return None
    ptype = prop_meta.get("type")
    if ptype == "rich_text":
        return {"rich_text": [{"type": "text", "text": {"content": str(value)[:1900]}}]}
    if ptype == "date":
        return {"date": {"start": str(value)}}
    if ptype == "select":
        return {"select": {"name": str(value)}}
    return None


def has_body_heading(page_id):
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = req("GET", url).json()
        for b in data.get("results", []):
            if b.get("type") == "heading_2":
                rich = b.get("heading_2", {}).get("rich_text", [])
                text = "".join(x.get("plain_text", "") for x in rich)
                if text.strip() == "記事本文":
                    return True
        if not data.get("has_more"):
            return False
        cursor = data.get("next_cursor")


def main():
    arts = json.loads(INPUT_JSON.read_text(encoding="utf-8")) if INPUT_JSON.exists() else []
    if not arts:
        print("existing_url_count: 0")
        print("saved: 0")
        print("skipped: 0")
        print("failed: 0")
        print("updated_existing: 0")
        return

    props = req("GET", f"https://api.notion.com/v1/databases/{DATABASE_ID}").json().get("properties", {})
    title_prop = next((k for k, v in props.items() if v.get("type") == "title"), None)
    body_prop = resolve_prop(props, BODY_PROP_NAMES)
    summary_prop = resolve_prop(props, SUMMARY_PROP_NAMES)
    issue_date_prop = resolve_prop(props, ISSUE_DATE_PROP_NAMES, allowed=("date", "select", "rich_text"))
    edition_prop = resolve_prop(props, ["Edition"], allowed=("date", "select", "rich_text"))

    keys, pages = load_existing()
    print("existing_url_count:", len(keys))
    saved = skipped = failed = updated_existing = 0
    body_blocks_appended = summary_written = summary_skipped_no_summary_field = 0
    issue_date_written = edition_written = 0
    failures = []

    for a in arts:
        u = a.get("url", "")
        k = ng(u)
        page_id = a.get("page_id") or pages.get(u, "")
        text = (a.get("text") or "").strip()
        title = (a.get("source_title") or a.get("page_title") or "Untitled")[:2000]
        summary_text = get_summary_text(a)
        is_existing = (u in keys) or (k and k in keys) or bool(page_id)

        try:
            if is_existing and page_id:
                patch = {}
                if body_prop and text:
                    patch[body_prop] = {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}
                if summary_prop:
                    if summary_text:
                        patch[summary_prop] = {"rich_text": [{"type": "text", "text": {"content": summary_text[:1900]}}]}
                        summary_written += 1
                    else:
                        summary_skipped_no_summary_field += 1
                if issue_date_prop and a.get("issue_date"):
                    issue_value = set_typed_prop(props[issue_date_prop], a.get("issue_date"))
                    if issue_value:
                        patch[issue_date_prop] = issue_value
                        issue_date_written += 1
                if edition_prop and a.get("edition") in ("morning", "evening"):
                    edition_value = set_typed_prop(props[edition_prop], a.get("edition"))
                    if edition_value:
                        patch[edition_prop] = edition_value
                        edition_written += 1
                if patch:
                    req("PATCH", f"https://api.notion.com/v1/pages/{page_id}", json={"properties": patch})
                if text and not has_body_heading(page_id):
                    body_blocks_appended += append_body_blocks(page_id, text)
                updated_existing += 1
                skipped += 1
                continue

            if is_existing:
                skipped += 1
                continue

            properties = {title_prop: {"title": [{"text": {"content": title}}]}}
            if "URL" in props:
                properties["URL"] = {"url": u}
            if body_prop and text:
                properties[body_prop] = {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}
            if summary_prop:
                if summary_text:
                    properties[summary_prop] = {"rich_text": [{"type": "text", "text": {"content": summary_text[:1900]}}]}
                    summary_written += 1
                else:
                    summary_skipped_no_summary_field += 1
            if issue_date_prop and a.get("issue_date"):
                issue_value = set_typed_prop(props[issue_date_prop], a.get("issue_date"))
                if issue_value:
                    properties[issue_date_prop] = issue_value
                    issue_date_written += 1
            if edition_prop and a.get("edition") in ("morning", "evening"):
                edition_value = set_typed_prop(props[edition_prop], a.get("edition"))
                if edition_value:
                    properties[edition_prop] = edition_value
                    edition_written += 1

            payload = {"parent": {"database_id": DATABASE_ID}, "properties": properties}
            created = req("POST", "https://api.notion.com/v1/pages", json=payload).json()
            if text and created.get("id"):
                body_blocks_appended += append_body_blocks(created["id"], text)
            saved += 1
        except Exception as e:
            failed += 1
            failures.append({
                "url": u,
                "title": title,
                "error_type": type(e).__name__,
                "error_message": str(e),
            })

    if failures:
        FAILED_LOG_JSON.parent.mkdir(parents=True, exist_ok=True)
        FAILED_LOG_JSON.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    print("saved:", saved)
    print("skipped:", skipped)
    print("failed:", failed)
    print("updated_existing:", updated_existing)
    print("body_blocks_appended:", body_blocks_appended)
    print("summary_written:", summary_written)
    print("summary_skipped_no_summary_field:", summary_skipped_no_summary_field)
    print("issue_date_written:", issue_date_written)
    print("edition_written:", edition_written)


if __name__ == "__main__":
    main()
