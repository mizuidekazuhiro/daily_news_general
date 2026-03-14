import json
import logging
import urllib.request
from pathlib import Path

from src.config.job_config import (
    NOTION_SPECIAL_NEWS_DB_ID,
    NOTION_SPECIAL_NEWS_ENABLED,
    NOTION_TOKEN,
    SPECIAL_NEWS_CONFIG_PATH,
    SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA,
    SPECIAL_NEWS_MAIL_BCC,
    SPECIAL_NEWS_MAIL_CC,
    SPECIAL_NEWS_MAIL_SUBJECT_PREFIX,
    SPECIAL_NEWS_MAIL_TO,
    SPECIAL_NEWS_MAX_ITEMS_TOTAL,
)
from src.jobs.main_news_job import clean, get_published_datetime, normalize_link, normalize_title, safe_parse
from src.renderers.special_news_renderer import build_special_news_subject, render_special_news_html
from src.utils.date_utils import previous_day_jst
from src.utils.mail_utils import mask_email, parse_mail_recipients, send_html_email


def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def extract_notion_property_value(prop):
    if not prop:
        return None
    ptype = prop.get("type")
    if ptype == "checkbox":
        return prop.get("checkbox")
    if ptype == "number":
        return prop.get("number")
    if ptype == "url":
        return prop.get("url")
    if ptype == "select":
        selected = prop.get("select")
        return selected.get("name") if selected else None
    if ptype == "multi_select":
        return [s.get("name") for s in prop.get("multi_select", []) if s.get("name")]
    if ptype in {"rich_text", "title"}:
        return "".join(part.get("plain_text", "") for part in prop.get(ptype, []))
    return None


def fetch_special_news_config_from_notion():
    if not NOTION_SPECIAL_NEWS_ENABLED:
        logging.info("Notion special-news setting disabled by NOTION_SPECIAL_NEWS_ENABLED=false")
        return None
    if not NOTION_TOKEN or not NOTION_SPECIAL_NEWS_DB_ID:
        logging.info("Notion credentials for special-news are not fully configured; fallback to local config")
        return None

    url = f"https://api.notion.com/v1/databases/{NOTION_SPECIAL_NEWS_DB_ID}/query"
    req = urllib.request.Request(url, data=json.dumps({}).encode("utf-8"), headers=notion_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as res:
        payload = json.loads(res.read().decode("utf-8"))

    media_rows = []
    for row in payload.get("results", []):
        props = row.get("properties", {})
        media_rows.append({
            "enabled": bool(extract_notion_property_value(props.get("Enabled"))),
            "media_name": extract_notion_property_value(props.get("MediaName")),
            "alert_ids": extract_notion_property_value(props.get("GoogleAlertIds")) or [],
            "alert_feeds": [v.strip() for v in (extract_notion_property_value(props.get("GoogleAlertFeeds")) or "").splitlines() if v.strip()],
            "display_order": int(extract_notion_property_value(props.get("DisplayOrder")) or 999),
            "max_items": int(extract_notion_property_value(props.get("MaxItemsPerMedia")) or SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA),
            "subject_prefix": extract_notion_property_value(props.get("SubjectPrefix")) or SPECIAL_NEWS_MAIL_SUBJECT_PREFIX,
            "delivery_enabled": bool(extract_notion_property_value(props.get("DeliveryEnabled"))),
            "max_items_total": int(extract_notion_property_value(props.get("MaxItemsTotal")) or SPECIAL_NEWS_MAX_ITEMS_TOTAL),
        })
    return media_rows


def load_special_news_media_config():
    notion_rows = fetch_special_news_config_from_notion()
    if notion_rows is not None:
        return [m for m in notion_rows if m.get("enabled") and m.get("media_name")]

    with Path(SPECIAL_NEWS_CONFIG_PATH).open("r", encoding="utf-8") as f:
        payload = json.load(f)

    media = payload.get("media", [])
    active = [{
        "enabled": bool(m.get("enabled", True)),
        "media_name": m.get("media_name", ""),
        "alert_ids": m.get("alert_ids", []),
        "alert_feeds": m.get("alert_feeds", []),
        "display_order": int(m.get("display_order", 999)),
        "max_items": int(m.get("max_items", SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)),
        "subject_prefix": m.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
        "delivery_enabled": bool(payload.get("delivery_enabled", True)),
        "max_items_total": int(payload.get("max_items_total", SPECIAL_NEWS_MAX_ITEMS_TOTAL)),
    } for m in media if m.get("enabled", True) and m.get("media_name")]
    return sorted(active, key=lambda x: x["display_order"])


def extract_entries_for_target_date(entries, target_date):
    filtered = []
    for e in entries:
        dt = get_published_datetime(e)
        if dt and dt.date() == target_date.date():
            filtered.append({"title": clean(e.get("title", "")), "link": normalize_link(e.get("link", "")), "published": dt.strftime("%Y-%m-%d %H:%M")})
    return filtered


def collect_special_news_articles(target_date):
    media_config = load_special_news_media_config()
    results = []
    delivery_enabled = True
    max_items_total = SPECIAL_NEWS_MAX_ITEMS_TOTAL

    for media in media_config:
        delivery_enabled = media.get("delivery_enabled", delivery_enabled)
        max_items_total = media.get("max_items_total", max_items_total)
        all_entries = []
        for feed in media.get("alert_feeds", []):
            all_entries.extend(safe_parse(feed))
        filtered = extract_entries_for_target_date(all_entries, target_date)
        unique, seen = [], set()
        for item in filtered:
            key = normalize_title(item["title"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        limited = unique[:media.get("max_items", SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)]
        results.append({"media_name": media["media_name"], "items": limited, "display_order": media["display_order"], "subject_prefix": media.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX)})

    results = sorted(results, key=lambda x: x["display_order"])
    total = 0
    for media_result in results:
        remain = max(0, max_items_total - total)
        media_result["items"] = media_result["items"][:remain]
        total += len(media_result["items"])

    return {"delivery_enabled": delivery_enabled, "media_results": results, "total_items": total}


def send_special_news_email(html, subject, to_list, cc_list, bcc_list):
    send_html_email(html, subject, to_list, cc_list, bcc_list)


def run_special_news_delivery():
    # special は JST 前日更新分の配信が仕様。
    target_date = previous_day_jst()
    result = collect_special_news_articles(target_date)
    if not result["delivery_enabled"]:
        logging.info("Special-news delivery disabled by configuration")
        return

    to_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_TO)
    cc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_CC)
    bcc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_BCC)
    if not to_list:
        raise ValueError("SPECIAL_NEWS_MAIL_TO is required")

    logging.info("Special-news recipients to=%s cc=%s bcc=%s", [mask_email(v) for v in to_list], [mask_email(v) for v in cc_list], [mask_email(v) for v in bcc_list])
    html = render_special_news_html(target_date, result["media_results"], result["total_items"])
    subject = build_special_news_subject(target_date, result["media_results"])
    send_special_news_email(html, subject, to_list, cc_list, bcc_list)
