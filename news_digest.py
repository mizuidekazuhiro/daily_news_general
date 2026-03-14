import feedparser
import smtplib
import re
import os
import socket
import urllib.parse
import urllib.request
import logging
import json
import argparse
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timedelta, timezone
from html import escape

from openai import OpenAI

# =====================
# タイムアウト設定
# =====================
socket.setdefaulttimeout(10)

# =====================
# logging
# =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# =====================
# メール設定
# =====================
MAIL_FROM = os.getenv("MAIL_FROM", "")
MAIL_TO = os.getenv("MAIL_TO", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

SPECIAL_NEWS_MAIL_TO = os.getenv("SPECIAL_NEWS_MAIL_TO", "")
SPECIAL_NEWS_MAIL_CC = os.getenv("SPECIAL_NEWS_MAIL_CC", "")
SPECIAL_NEWS_MAIL_BCC = os.getenv("SPECIAL_NEWS_MAIL_BCC", "")
SPECIAL_NEWS_MAIL_SUBJECT_PREFIX = os.getenv("SPECIAL_NEWS_MAIL_SUBJECT_PREFIX", "【専門紙記事一覧】")
SPECIAL_NEWS_CONFIG_PATH = os.getenv("SPECIAL_NEWS_CONFIG_PATH", os.path.join("config", "special_news_media.json"))
SPECIAL_NEWS_TEMPLATE_PATH = os.getenv("SPECIAL_NEWS_TEMPLATE_PATH", os.path.join("templates", "special_news_email.html"))
SPECIAL_NEWS_MAX_ITEMS_TOTAL = int(os.getenv("SPECIAL_NEWS_MAX_ITEMS_TOTAL", "50"))
SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA = int(os.getenv("SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA", "20"))
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_SPECIAL_NEWS_DB_ID = os.getenv("NOTION_SPECIAL_NEWS_DB_ID", "")
NOTION_SPECIAL_NEWS_ENABLED = os.getenv("NOTION_SPECIAL_NEWS_ENABLED", "true").lower() == "true"

# =====================
# JST
# =====================
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)

# =====================
# 媒体設定（★パターン②）
# =====================
MEDIA = {
    "Kallanish": [
        "https://news.google.com/rss/search?q=site:kallanish.com&hl=en&ceid=US:en"
    ],
    "BigMint": [
        "https://news.google.com/rss/search?q=BigMint&hl=en&ceid=US:en"
    ],
    "Fastmarkets": [
        "https://news.google.com/rss/search?q=Fastmarkets&hl=en&ceid=US:en"
    ],
    "Argus": [
        "https://news.google.com/rss/search?q=site:argusmedia.com&hl=en&ceid=US:en"
    ],
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+-人事+-訃報+-文化+-スポーツ&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+市場&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+産業&hl=ja&gl=JP&ceid=JP:ja"
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Bloomberg&hl=en&ceid=US:en"
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Reuters&hl=en&ceid=US:en"
    ],
    "MySteel": [
        "https://news.google.com/rss/search?q=steel+mysteel&hl=en&ceid=US:en"
    ],
}

# =====================
# 重要度キーワード
# =====================
IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel","iron","scrap","rebar","H形鋼","H Beam","製鉄","鉄鋼","高炉","電炉","ferrous"],
    "建設": ["construction","infrastructure","建設","ゼネコン"],
    "AI": ["ai","artificial intelligence","semiconductor","半導体","生成ai","Data Center","データセンター"],
    "企業": ["m&a","買収","商社","三菱商事","住友商事","伊藤忠商事","丸紅","三井物産"],
    "通商": ["trade","tariff","sanction","関税","AD"],
    "重点国": ["india","indian","インド","vietnam","ベトナム","Bangladesh","バングラデシュ"]
}

# =====================
# 色分け
# =====================
COLOR_BG = {3:"#fff5f5",2:"#fffaf0",1:"#f0f9ff",0:"#ffffff"}
COLOR_BORDER = {3:"#c53030",2:"#dd6b20",1:"#3182ce",0:"#d0d7de"}

# =====================
# 翻訳設定
# =====================
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06")
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "512"))
OPENAI_TRANSLATION_BATCH_SIZE = int(os.getenv("OPENAI_TRANSLATION_BATCH_SIZE", "30"))
TITLE_TRANSLATION_CACHE_PATH = os.getenv(
    "TITLE_TRANSLATION_CACHE_PATH",
    os.path.join("data", "title_translation_cache.json")
)
OPENAI_RESPONSE_TRUNCATE_CHARS = int(os.getenv("OPENAI_RESPONSE_TRUNCATE_CHARS", "500"))

# =====================
# ユーティリティ
# =====================
def clean(text):
    return re.sub("<[^<]+?>", "", text).strip()

def importance_score(text):
    text = text.lower()
    score = 0

    for words in IMPORTANT_KEYWORDS.values():
        for w in words:
            if w.isascii() and w.isalpha():
                if re.search(rf"\b{re.escape(w)}\b", text):
                    score += 1
            else:
                if w in text:
                    score += 1

    return min(score, 3)

def published(entry):
    published_dt = get_published_datetime(entry)
    if not published_dt:
        return "N/A"
    return published_dt.strftime("%Y-%m-%d %H:%M")

def get_published_datetime(entry):
    parsed = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        parsed = entry.published_parsed
    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
        parsed = entry.updated_parsed
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(JST)
        except (TypeError, ValueError):
            return None
    return None

def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except:
        return []

def mask_email(addr):
    if "@" not in addr:
        return "***"
    local, domain = addr.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"

def parse_mail_recipients(raw):
    if not raw:
        return []
    return [r.strip() for r in raw.split(",") if r.strip()]

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
    if ptype == "rich_text":
        return "".join(part.get("plain_text", "") for part in prop.get("rich_text", []))
    if ptype == "title":
        return "".join(part.get("plain_text", "") for part in prop.get("title", []))
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
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except Exception as exc:
        logging.error("Failed to fetch Notion special-news config: %s", exc)
        raise

    media_rows = []
    for row in payload.get("results", []):
        props = row.get("properties", {})
        media_rows.append({
            "enabled": bool(extract_notion_property_value(props.get("Enabled"))),
            "media_name": extract_notion_property_value(props.get("MediaName")),
            "alert_ids": extract_notion_property_value(props.get("GoogleAlertIds")) or [],
            "alert_feeds": [
                v.strip() for v in (extract_notion_property_value(props.get("GoogleAlertFeeds")) or "").splitlines() if v.strip()
            ],
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
        active = [m for m in notion_rows if m.get("enabled") and m.get("media_name")]
        if not active:
            logging.warning("No enabled media in Notion config for special-news")
        return active

    path = Path(SPECIAL_NEWS_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Special news config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    media = payload.get("media", [])
    active = [
        {
            "enabled": bool(m.get("enabled", True)),
            "media_name": m.get("media_name", ""),
            "alert_ids": m.get("alert_ids", []),
            "alert_feeds": m.get("alert_feeds", []),
            "display_order": int(m.get("display_order", 999)),
            "max_items": int(m.get("max_items", SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)),
            "subject_prefix": m.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
            "delivery_enabled": bool(payload.get("delivery_enabled", True)),
            "max_items_total": int(payload.get("max_items_total", SPECIAL_NEWS_MAX_ITEMS_TOTAL)),
        }
        for m in media
        if m.get("enabled", True) and m.get("media_name")
    ]
    return sorted(active, key=lambda x: x["display_order"])

def extract_entries_for_target_date(entries, target_date):
    filtered = []
    for e in entries:
        dt = get_published_datetime(e)
        if not dt:
            continue
        if dt.date() != target_date.date():
            continue
        filtered.append({
            "title": clean(e.get("title", "")),
            "link": normalize_link(e.get("link", "")),
            "published": dt.strftime("%Y-%m-%d %H:%M"),
        })
    return filtered

def collect_special_news_articles(target_date):
    logging.info("Special-news job started")
    logging.info("Special-news target_date=%s timezone=Asia/Tokyo", target_date.strftime("%Y-%m-%d"))
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
        logging.info("Special-news media=%s fetched=%s", media["media_name"], len(all_entries))
        filtered = extract_entries_for_target_date(all_entries, target_date)
        unique = []
        seen = set()
        for item in filtered:
            key = normalize_title(item["title"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        limited = unique[:media.get("max_items", SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)]
        logging.info("Special-news media=%s filtered=%s", media["media_name"], len(limited))
        results.append({
            "media_name": media["media_name"],
            "items": limited,
            "display_order": media["display_order"],
            "subject_prefix": media.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
        })

    results = sorted(results, key=lambda x: x["display_order"])
    total = 0
    for media_result in results:
        remain = max(0, max_items_total - total)
        media_result["items"] = media_result["items"][:remain]
        total += len(media_result["items"])

    return {
        "delivery_enabled": delivery_enabled,
        "media_results": results,
        "total_items": total,
    }

def render_special_news_html(target_date, media_results, total_items):
    template = Path(SPECIAL_NEWS_TEMPLATE_PATH).read_text(encoding="utf-8")
    section_html = []
    for media in media_results:
        items = media["items"]
        lis = "".join(
            f'<li><a href="{escape(i["link"])}">{escape(i["title"])}</a>'
            f'<span class="meta">（{escape(i["published"])}）</span></li>'
            for i in items
        )
        if not lis:
            lis = "<li>対象記事はありませんでした。</li>"
        section_html.append(
            f"<section><h3>{escape(media['media_name'])}</h3>"
            f"<p class='count'>件数: {len(items)}件</p><ol>{lis}</ol></section>"
        )

    if not section_html:
        section_html.append("<section><h3>対象媒体</h3><p>対象記事はありませんでした。</p></section>")

    return template.format(
        target_date=target_date.strftime("%Y-%m-%d"),
        total_items=total_items,
        media_sections="\n".join(section_html),
    )

def build_special_news_subject(target_date, media_results):
    media_names = "・".join(m["media_name"] for m in media_results)
    return f"{SPECIAL_NEWS_MAIL_SUBJECT_PREFIX}{target_date.strftime('%Y-%m-%d')}更新分（{media_names}）"

def send_mail_generic(html, subject, to_list, cc_list=None, bcc_list=None):
    cc_list = cc_list or []
    bcc_list = bcc_list or []
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Daily News Bot", MAIL_FROM))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    recipients = to_list + cc_list + bcc_list
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(MAIL_FROM, MAIL_PASSWORD)
        s.sendmail(MAIL_FROM, recipients, msg.as_string())

def normalize_link(url):
    if "news.google.com" in url and "url=" in url:
        url = urllib.parse.unquote(re.sub(r".*url=", "", url))
    return re.sub(r"&utm_.*", "", url)

def is_nikkei_noise(title, summary):
    noise = [
        "会社情報","与信管理","NIKKEI COMPASS",
        "会社概要","現状と将来性","業界の動向",
        "経営・財務","リスク情報","企業分析","基本情報",
        "セミナー","イベント","説明会","講演","参加者募集",
        "オンライン開催","受講料","主催",
        "キャンペーン","SALE","セール","発売","初売り",
        "無料","最大","OFF",
        "新製品","サービス開始","提供開始",
        "PR","提供","公式","【","［"
    ]
    return any(n in title or n in summary for n in noise)

def is_within_24h(dt):
    current = datetime.now(JST)
    return dt >= current - timedelta(hours=24)

def normalize_title(title):
    t = title.lower()
    t = re.sub(r"（.*?）|\(.*?\)", "", t)
    t = re.sub(r"-.*$", "", t)
    t = re.sub(r"(reuters|bloomberg|yahoo|msn|dメニュー|ロイター)", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def is_japanese(text):
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))

def normalize_cache_key(title):
    return re.sub(r"\s+", " ", title.strip())

def load_translation_cache(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logging.warning("Translation cache is not a dict. Reinitializing to empty cache.")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Failed to load translation cache; reinitializing: %s", exc)
        return {}

def save_translation_cache(path, cache):
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    tmp_path.replace(cache_path)

def truncate_for_log(text, limit):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"

def estimate_max_output_tokens(titles):
    total_chars = sum(len(title) for title in titles)
    estimated_tokens = max(128, total_chars // 2)
    return max(OPENAI_MAX_OUTPUT_TOKENS, min(2048, estimated_tokens))

def normalize_translations_payload(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "translations" in payload:
            translations = payload["translations"]
            if isinstance(translations, list):
                return translations
            if isinstance(translations, dict):
                payload = translations
            else:
                return None
        if all(isinstance(k, str) and k.isdigit() for k in payload.keys()):
            ordered_keys = sorted(payload.keys(), key=lambda x: int(x))
            return [payload[key] for key in ordered_keys]
    return None

def parse_translation_response(content, expected_len):
    payload = json.loads(content)
    translations = normalize_translations_payload(payload)
    if not isinstance(translations, list) or len(translations) != expected_len:
        raise ValueError("Invalid translation response format.")
    return translations

def is_valid_translation(source, translated):
    if not isinstance(translated, str):
        return False
    candidate = translated.strip()
    if not candidate:
        return False
    if candidate == source.strip():
        return False
    return True

def build_translation_messages(titles):
    system_prompt = (
        "あなたは翻訳エンジン。入力配列を日本語の配列に翻訳して返す。"
        "固有名詞/企業名/略語は原則保持し、過度な意訳を避ける。"
        "タイトルなので短く自然な日本語にする。入力順序を必ず維持する。"
        "出力は{\"translations\":[...]}の厳密JSONのみ。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(titles, ensure_ascii=False)}
    ]

def build_response_format(use_schema):
    if use_schema:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_response",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["translations"]
                }
            }
        }
    return {"type": "json_object"}

def request_translations(client, titles, model, use_schema):
    response_format = build_response_format(use_schema)
    response = client.chat.completions.create(
        model=model,
        messages=build_translation_messages(titles),
        temperature=0,
        max_tokens=estimate_max_output_tokens(titles),
        response_format=response_format
    )
    return response, response_format["type"]

def translate_titles_to_ja(titles, client_factory=OpenAI):
    total_titles = len(titles)
    if total_titles == 0:
        return []

    cache = load_translation_cache(TITLE_TRANSLATION_CACHE_PATH)
    normalized_titles = [normalize_cache_key(title) for title in titles]
    translations = [None] * total_titles
    cache_hits = 0
    cache_misses = 0
    missing_keys = []
    key_to_title = {}
    key_to_indices = {}

    for idx, (title, key) in enumerate(zip(titles, normalized_titles)):
        if key in cache:
            translations[idx] = cache[key]
            cache_hits += 1
        else:
            cache_misses += 1
            if key not in key_to_title:
                key_to_title[key] = title
                missing_keys.append(key)
            key_to_indices.setdefault(key, []).append(idx)

    api_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cached_updates = 0

    if missing_keys:
        logging.info("OPENAI_API_KEY is set? %s", bool(os.getenv("OPENAI_API_KEY")))
        logging.info("OPENAI_MODEL=%s", OPENAI_MODEL)
        if not os.getenv("OPENAI_API_KEY"):
            logging.warning(
                "OPENAI_API_KEY is not set. Using original titles for %s items.",
                len(missing_keys)
            )
        else:
            client = client_factory()
            for start in range(0, len(missing_keys), OPENAI_TRANSLATION_BATCH_SIZE):
                batch_keys = missing_keys[start:start + OPENAI_TRANSLATION_BATCH_SIZE]
                batch_titles = [key_to_title[key] for key in batch_keys]
                response_format_type = "json_schema"
                try:
                    logging.info(
                        "Translation batch: api_calls=%s batch_size=%s model=%s response_format=%s cache_hits=%s cache_misses=%s",
                        api_calls,
                        len(batch_titles),
                        OPENAI_MODEL,
                        response_format_type,
                        cache_hits,
                        cache_misses
                    )
                    response, response_format_type = request_translations(
                        client,
                        batch_titles,
                        OPENAI_MODEL,
                        use_schema=True
                    )
                    api_calls += 1
                    usage = getattr(response, "usage", None)
                    if usage:
                        prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                        completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                        total_tokens += getattr(usage, "total_tokens", 0) or 0

                    content = response.choices[0].message.content
                    batch_translations = parse_translation_response(content, len(batch_titles))
                except Exception as exc:
                    raw_content = ""
                    if "content" in locals():
                        raw_content = truncate_for_log(content, OPENAI_RESPONSE_TRUNCATE_CHARS)
                    logging.warning(
                        "OpenAI translation failed with json_schema: %s; raw_content=%s",
                        exc,
                        raw_content
                    )
                    response_format_type = "json_object"
                    try:
                        logging.info(
                            "Translation batch: api_calls=%s batch_size=%s model=%s response_format=%s cache_hits=%s cache_misses=%s",
                            api_calls,
                            len(batch_titles),
                            OPENAI_MODEL,
                            response_format_type,
                            cache_hits,
                            cache_misses
                        )
                        response, response_format_type = request_translations(
                            client,
                            batch_titles,
                            OPENAI_MODEL,
                            use_schema=False
                        )
                        api_calls += 1
                        usage = getattr(response, "usage", None)
                        if usage:
                            prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                            completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                            total_tokens += getattr(usage, "total_tokens", 0) or 0
                        content = response.choices[0].message.content
                        batch_translations = parse_translation_response(content, len(batch_titles))
                    except Exception as retry_exc:
                        raw_content = ""
                        if "content" in locals():
                            raw_content = truncate_for_log(content, OPENAI_RESPONSE_TRUNCATE_CHARS)
                        logging.warning(
                            "OpenAI translation failed after fallback: %s; raw_content=%s",
                            retry_exc,
                            raw_content
                        )
                        continue

                for key, translated in zip(batch_keys, batch_translations):
                    source_title = key_to_title[key]
                    if is_valid_translation(source_title, translated):
                        cache[key] = translated
                        cached_updates += 1
                    else:
                        translated = source_title
                    for idx in key_to_indices.get(key, []):
                        translations[idx] = translated

    for idx, title in enumerate(titles):
        if translations[idx] is None:
            translations[idx] = title

    if cached_updates:
        save_translation_cache(TITLE_TRANSLATION_CACHE_PATH, cache)

    logging.info(
        "Translation stats: total=%s, cache_hits=%s, cache_misses=%s, api_calls=%s, tokens_prompt=%s, tokens_completion=%s, tokens_total=%s",
        total_titles,
        cache_hits,
        cache_misses,
        api_calls,
        prompt_tokens,
        completion_tokens,
        total_tokens
    )
    return translations

# =====================
# generate
# =====================
def generate_html():
    final_articles = []

    for media, feeds in MEDIA.items():
        collected = []
        buffer_zero = []
        seen = set()
        offset = 0
        exhausted = False

        while len(collected) < 15 and not exhausted:
            found_recent = False

            for url in feeds:
                entries = safe_parse(url)
                slice_entries = entries[offset:offset+15]

                if not slice_entries:
                    continue

                for e in slice_entries:
                    title = clean(e.get("title", ""))
                    summary_raw = clean(e.get("summary", ""))
                    link = normalize_link(e.get("link",""))

                    dt = get_published_datetime(e)
                    if not dt:
                        continue

                    if not is_within_24h(dt):
                        continue

                    found_recent = True

                    if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                        continue

                    key = normalize_title(title)
                    if key in seen:
                        continue
                    seen.add(key)

                    score = importance_score(title + summary_raw)

                    item = {
                        "media": media,
                        "title": title,
                        "title_ja": "",
                        "summary": "",
                        "score": score,
                        "published": published(e),
                        "link": link
                    }

                    if score >= 1:
                        collected.append(item)
                    else:
                        buffer_zero.append(item)

            if not found_recent:
                exhausted = True

            offset += 15

        for a in sorted(buffer_zero, key=lambda x:x["published"], reverse=True):
            if len(collected) >= 15:
                break
            collected.append(a)

        final_articles.extend(collected)

    # ★翻訳ルール（英語のみ）
    target_media = {"Kallanish","BigMint","Fastmarkets","Argus","MySteel","Reuters","Bloomberg"}
    to_translate = []
    translate_indices = []
    for idx, article in enumerate(final_articles):
        if article["media"] in target_media and not is_japanese(article["title"]):
            to_translate.append(article["title"])
            translate_indices.append(idx)
        else:
            article["title_ja"] = article["title"]

    logging.info(
        "Article collection: collected=%s translate_targets=%s",
        len(final_articles),
        len(to_translate)
    )
    if to_translate:
        translated = translate_titles_to_ja(to_translate)
        for idx, translated_title in zip(translate_indices, translated):
            final_articles[idx]["title_ja"] = translated_title

    body_html = """
    <html>
    <body style="
        font-family:
            'Meiryo UI',
            'Meiryo',
            'Yu Gothic',
            'Hiragino Kaku Gothic ProN',
            sans-serif;
    ">
    <h2>主要ニュース速報（重要度順）</h2>
    """

    for a in sorted(final_articles, key=lambda x:(x["score"],x["published"]), reverse=True):
        stars = "★"*a["score"] if a["score"] else "－"
        display_title = a.get("title_ja") or a["title"]
        is_english_article = not is_japanese(a["title"])
        body_html += f"""
        <div style="background:{COLOR_BG[a['score']]};
                    border-left:5px solid {COLOR_BORDER[a['score']]};
                    padding:12px;margin-bottom:14px;">
            <b>{display_title}</b><br>
        """
        if is_english_article:
            body_html += f"""
            <div style="font-size:12px;color:#666;font-style:italic;margin-top:4px;">
                🇬🇧 EN: {a['title']}
            </div>
            """
        if a["summary"]:
            body_html += f"<div>{a['summary']}</div>"
        body_html += f"""
            <div style="font-size:12px;color:#555;">
                {a['media']}｜重要度:{stars}｜{a['published']}
            </div>
            <a href="{a['link']}">▶ 元記事</a>
        </div>
        """
    body_html += "</body></html>"
    return body_html

def send_mail(html):
    send_mail_generic(
        html=html,
        subject=f"主要ニュースまとめ｜{now_jst.strftime('%Y-%m-%d')}",
        to_list=parse_mail_recipients(MAIL_TO),
    )

def run_special_news_delivery():
    target_date = datetime.now(JST) - timedelta(days=1)
    result = collect_special_news_articles(target_date)
    if not result["delivery_enabled"]:
        logging.info("Special-news delivery disabled by configuration")
        return

    to_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_TO)
    cc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_CC)
    bcc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_BCC)
    if not to_list:
        logging.error("SPECIAL_NEWS_MAIL_TO is required but not configured")
        raise ValueError("SPECIAL_NEWS_MAIL_TO is required")

    logging.info(
        "Special-news recipients to=%s cc=%s bcc=%s",
        [mask_email(v) for v in to_list],
        [mask_email(v) for v in cc_list],
        [mask_email(v) for v in bcc_list],
    )
    html = render_special_news_html(target_date, result["media_results"], result["total_items"])
    subject = build_special_news_subject(target_date, result["media_results"])
    send_mail_generic(html, subject, to_list, cc_list, bcc_list)
    logging.info("Special-news email delivered successfully; total_items=%s", result["total_items"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=["main", "special", "all"], default="main")
    args = parser.parse_args()

    if args.job in {"main", "all"}:
        send_mail(generate_html())
    if args.job in {"special", "all"}:
        run_special_news_delivery()
