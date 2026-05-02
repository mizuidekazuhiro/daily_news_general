import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
DATABASE_ID = (os.getenv("NIKKEI_ARTICLES_DB_ID", "") or os.getenv("NOTION_ARTICLE_DB_ID", "")).strip()

INPUT_JSON = Path("logs/nikkei_articles_full.json")
SLEEP_SECONDS = float(os.getenv("NOTION_SAVE_SLEEP_SECONDS", "0.5"))

NOTION_VERSION = "2022-06-28"


def headers():
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN が未設定です。")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_request(method, url, **kwargs):
    for attempt in range(1, 6):
        r = requests.request(method, url, headers=headers(), timeout=60, **kwargs)

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "2"))
            print(f"  rate limited. sleep {wait}s")
            time.sleep(wait)
            continue

        if 500 <= r.status_code < 600:
            print(f"  notion server error {r.status_code}. retry {attempt}/5")
            time.sleep(2 * attempt)
            continue

        try:
            r.raise_for_status()
        except Exception:
            print("  notion error status:", r.status_code)
            print("  notion error body:", r.text[:3000])
            raise
        return r

    r.raise_for_status()


def get_database_properties():
    r = notion_request(
        "GET",
        f"https://api.notion.com/v1/databases/{DATABASE_ID}",
    )
    return r.json().get("properties", {})


def has_prop(db_props, name):
    return name in db_props


def chunk_text(text, size=1800):
    text = text or ""
    return [text[i:i + size] for i in range(0, len(text), size)]


def parse_issue_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except Exception:
        return None


def search_existing_page(url):
    payload = {
        "filter": {
            "property": "URL",
            "url": {
                "equals": url
            }
        },
        "page_size": 1,
    }

    r = notion_request(
        "POST",
        f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
        json=payload,
    )

    results = r.json().get("results", [])
    return results[0] if results else None


def build_properties(article, db_props):
    title = article.get("source_title") or article.get("page_title") or "Untitled"
    url = article.get("url", "")
    issue_date = parse_issue_date(article.get("issue_date", ""))
    edition = article.get("edition") or "morning"
    text_length = int(article.get("text_length") or 0)
    status = article.get("status") or "success"
    image_count = int(article.get("image_count") or 0)

    properties = {}

    def prop_type(name):
        return db_props.get(name, {}).get("type")

    def set_title(name, value):
        if name in db_props and prop_type(name) == "title":
            properties[name] = {
                "title": [{"text": {"content": str(value)[:2000]}}]
            }

    def set_url(name, value):
        if name not in db_props:
            return
        t = prop_type(name)
        if t == "url":
            properties[name] = {"url": value or None}
        elif t == "rich_text":
            properties[name] = {
                "rich_text": [{"text": {"content": str(value)[:2000]}}]
            }

    def set_text_or_select(name, value):
        if name not in db_props:
            return
        t = prop_type(name)
        value = str(value or "")
        if t == "select":
            properties[name] = {"select": {"name": value}}
        elif t == "rich_text":
            properties[name] = {
                "rich_text": [{"text": {"content": value[:2000]}}]
            }
        elif t == "multi_select":
            properties[name] = {"multi_select": [{"name": value}]} if value else {}

    def set_number(name, value):
        if name in db_props and prop_type(name) == "number":
            properties[name] = {"number": value}

    def set_checkbox(name, value):
        if name in db_props and prop_type(name) == "checkbox":
            properties[name] = {"checkbox": bool(value)}

    def set_date(name, value):
        if name in db_props and prop_type(name) == "date" and value:
            properties[name] = {"date": {"start": value}}

    # title型プロパティを自動検出。通常は Title
    title_prop = None
    for name, prop in db_props.items():
        if prop.get("type") == "title":
            title_prop = name
            break
    if not title_prop:
        raise RuntimeError("title型のプロパティが見つかりません。")

    set_title(title_prop, title)
    set_url("URL", url)
    set_date("Issue Date", issue_date)

    # Notion側の型に応じて select / rich_text を自動で出し分ける
    set_text_or_select("Edition", edition)
    set_text_or_select("Source", "Nikkei")
    set_text_or_select("Fetch Status", status)
    set_text_or_select("Full Text Status", "saved" if text_length > 0 else "failed")

    set_number("Text Length", text_length)
    set_number("Image Count", image_count)

    set_checkbox("GPT Processed", False)
    set_checkbox("Has Image", image_count > 0)

    return properties


def create_article_page(article, db_props):
    properties = build_properties(article, db_props)

    payload = {
        "parent": {
            "database_id": DATABASE_ID
        },
        "properties": properties,
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "記事本文"
                            }
                        }
                    ]
                }
            }
        ],
    }

    r = notion_request(
        "POST",
        "https://api.notion.com/v1/pages",
        json=payload,
    )
    return r.json()


def append_text_blocks(page_id, text):
    chunks = chunk_text(text, 1800)

    if not chunks:
        chunks = ["本文なし"]

    blocks = []
    for chunk in chunks:
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": chunk
                            }
                        }
                    ]
                }
            }
        )

    for i in range(0, len(blocks), 80):
        batch = blocks[i:i + 80]
        notion_request(
            "PATCH",
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            json={"children": batch},
        )
        time.sleep(SLEEP_SECONDS)


def append_metadata(page_id, article):
    meta_text = (
        f"URL: {article.get('url', '')}\n"
        f"Issue Date: {article.get('issue_date', '')}\n"
        f"Edition: {article.get('edition', '')}\n"
        f"Selector: {article.get('selector', '')}\n"
        f"Text Length: {article.get('text_length', '')}\n"
        f"Image Count: {article.get('image_count', 0)}"
    )

    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": "メタデータ"
                        }
                    }
                ]
            }
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": meta_text[:2000]
                        }
                    }
                ]
            }
        },
    ]

    notion_request(
        "PATCH",
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json={"children": blocks},
    )


def main():
    if not DATABASE_ID:
        raise RuntimeError("NIKKEI_ARTICLES_DB_ID または NOTION_ARTICLE_DB_ID が未設定です。")
    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"{INPUT_JSON} がありません。")

    db_props = get_database_properties()
    articles = json.loads(INPUT_JSON.read_text(encoding="utf-8"))

    saved = 0
    skipped = 0
    failed = 0

    print("target_articles:", len(articles))
    print("database_id:", DATABASE_ID)
    print("db_properties:", ", ".join(db_props.keys()))

    for i, article in enumerate(articles, 1):
        url = article.get("url", "")
        title = article.get("source_title") or article.get("page_title") or "Untitled"

        if not url:
            print(f"[{i}/{len(articles)}] skip no url")
            skipped += 1
            continue

        try:
            existing = search_existing_page(url)
            if existing:
                print(f"[{i}/{len(articles)}] skip duplicate: {title[:70]}")
                skipped += 1
                continue

            print(f"[{i}/{len(articles)}] save: {title[:80]}")

            page = create_article_page(article, db_props)
            page_id = page["id"]

            append_text_blocks(page_id, article.get("text", ""))
            append_metadata(page_id, article)

            saved += 1
            time.sleep(SLEEP_SECONDS)

        except Exception as e:
            failed += 1
            print(f"  failed: {title[:80]} | {e}")

    print("saved:", saved)
    print("skipped:", skipped)
    print("failed:", failed)


if __name__ == "__main__":
    main()
