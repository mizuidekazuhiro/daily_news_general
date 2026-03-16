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
from html.parser import HTMLParser
from string import Template
from typing import Any, Dict, List, Optional
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import formataddr
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
SPECIAL_NEWS_WINDOW_HOURS = int(os.getenv("SPECIAL_NEWS_WINDOW_HOURS", "24"))
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_SPECIAL_NEWS_DB_ID = os.getenv("NOTION_SPECIAL_NEWS_DB_ID", "")
SPECIAL_NEWS_NOTION_ENABLED_DEFAULT = False
ENV_BOOL_TRUE_VALUES = {"true", "1", "yes", "on"}
ENV_BOOL_FALSE_VALUES = {"false", "0", "no", "off"}
DEFAULT_SPECIAL_DATE_RULE = {
    "date_source_type": "rss",
    "date_parse_pattern": "",
    "date_css_selector": "",
    "date_timezone": "Asia/Tokyo",
    "date_granularity": "datetime",
    "target_date_mode": "rolling_24h",
    "lookback_hours": SPECIAL_NEWS_WINDOW_HOURS,
    "fallback_date_source_type": "",
    "fallback_date_parse_pattern": "",
}
SPECIAL_DATE_RULE_PRESETS = {
    "日刊鉄鋼新聞": {
        "date_source_type": "article_html",
        "date_css_selector": "time.article-header__published",
        "date_parse_pattern": r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}",
        "date_granularity": "datetime",
        "target_date_mode": "rolling_24h",
        "date_timezone": "Asia/Tokyo",
    },
    "日刊産業新聞": {
        "date_source_type": "url",
        "date_css_selector": "",
        "date_parse_pattern": r"news-t(\d{4})(\d{2})(\d{2})\d+\.html",
        "date_granularity": "date",
        "target_date_mode": "calendar_day",
        "date_timezone": "Asia/Tokyo",
    },
}
VALID_DATE_SOURCE_TYPES = {"rss", "article_html", "meta", "json_ld", "url"}
VALID_DATE_GRANULARITY = {"datetime", "date"}
VALID_TARGET_DATE_MODE = {"rolling_24h", "calendar_day"}
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
def parse_special_news_article_datetime(entry: Any) -> Optional[Dict[str, str]]:
    source_order = ["published_parsed", "updated_parsed", "published", "updated"]
    source = None
    raw_value: Any = None
    article_dt_aware: Optional[datetime] = None
    for field in source_order:
        value = getattr(entry, field, None)
        if not value:
            continue
        source = field
        raw_value = value
        if field.endswith("_parsed"):
            try:
                article_dt_aware = datetime(*value[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                article_dt_aware = None
        else:
            if isinstance(value, datetime):
                article_dt_aware = value
            else:
                try:
                    article_dt_aware = parsedate_to_datetime(str(value))
                except (TypeError, ValueError):
                    article_dt_aware = None
        break
    if not article_dt_aware:
        return None
    if article_dt_aware.tzinfo is None:
        article_dt_aware = article_dt_aware.replace(tzinfo=timezone.utc)
    article_dt_jst = article_dt_aware.astimezone(JST)
    return {
        "source": source or "unknown",
        "raw": str(raw_value),
        "article_dt_jst": article_dt_jst,
        "article_dt_original": article_dt_aware,
    }
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
def parse_mail_recipients(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    recipients = []
    for token in re.split(r"[,;\n]", str(raw)):
        value = token.strip()
        if value:
            recipients.append(value)
    return recipients
def parse_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        logging.info("%s is not set; using default=%s", name, default)
        return default
    normalized = raw.strip().lower()
    if not normalized:
        logging.info("%s is empty; using default=%s", name, default)
        return default
    if normalized in ENV_BOOL_TRUE_VALUES:
        logging.info("%s explicitly enabled", name)
        return True
    if normalized in ENV_BOOL_FALSE_VALUES:
        logging.info("%s explicitly disabled", name)
        return False
    logging.warning("%s has invalid boolean value=%r; using default=%s", name, raw, default)
    return default
def notion_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
def extract_notion_property_value(prop: Optional[Dict[str, Any]]) -> Any:
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
def safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
def is_valid_http_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
def shorten_url(url: str, max_len: int = 120) -> str:
    if len(url) <= max_len:
        return url
    return f"{url[:max_len-3]}..."
def parse_feed_urls(raw_feeds: str, media_name: str) -> List[str]:
    urls: List[str] = []
    seen = set()
    for line in (raw_feeds or "").splitlines():
        feed = line.strip()
        if not feed:
            continue
        if not is_valid_http_url(feed):
            logging.warning("Special-news media=%s invalid feed URL skipped: %s", media_name, feed)
            continue
        if feed in seen:
            continue
        seen.add(feed)
        urls.append(feed)
    return urls
def normalize_special_date_rule(media_name: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rule = dict(DEFAULT_SPECIAL_DATE_RULE)
    if media_name in SPECIAL_DATE_RULE_PRESETS:
        rule.update(SPECIAL_DATE_RULE_PRESETS[media_name])
    if overrides:
        for k, v in overrides.items():
            if v is None:
                continue
            if isinstance(v, str):
                rule[k] = v.strip()
            else:
                rule[k] = v
    date_source = str(rule.get("date_source_type") or "").strip().lower()
    if date_source not in VALID_DATE_SOURCE_TYPES:
        date_source = DEFAULT_SPECIAL_DATE_RULE["date_source_type"]
    rule["date_source_type"] = date_source
    fallback_source = str(rule.get("fallback_date_source_type") or "").strip().lower()
    if fallback_source and fallback_source not in VALID_DATE_SOURCE_TYPES:
        fallback_source = ""
    rule["fallback_date_source_type"] = fallback_source
    granularity = str(rule.get("date_granularity") or "").strip().lower()
    if granularity not in VALID_DATE_GRANULARITY:
        granularity = DEFAULT_SPECIAL_DATE_RULE["date_granularity"]
    rule["date_granularity"] = granularity
    target_mode = str(rule.get("target_date_mode") or "").strip().lower()
    if target_mode not in VALID_TARGET_DATE_MODE:
        target_mode = "calendar_day" if granularity == "date" else DEFAULT_SPECIAL_DATE_RULE["target_date_mode"]
    rule["target_date_mode"] = target_mode
    rule["lookback_hours"] = safe_int(rule.get("lookback_hours"), SPECIAL_NEWS_WINDOW_HOURS)
    if rule["lookback_hours"] <= 0:
        rule["lookback_hours"] = SPECIAL_NEWS_WINDOW_HOURS
    tz_name = str(rule.get("date_timezone") or "Asia/Tokyo").strip() or "Asia/Tokyo"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "Asia/Tokyo"
        tz = ZoneInfo("Asia/Tokyo")
    rule["date_timezone"] = tz_name
    rule["timezone"] = tz
    return rule
def fetch_article_html(link: str) -> Dict[str, Any]:
    if not link or not is_valid_http_url(link):
        return {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "html": "",
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "error": "invalid_url",
        }
    req = urllib.request.Request(
        link,
        headers={"User-Agent": "Mozilla/5.0 (special-news-date-extractor)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        charset = res.headers.get_content_charset() or "utf-8"
        body = res.read()
        final_url = res.geturl()
    html = body.decode(charset, errors="replace")
    return {
        "source_url": link,
        "initial_url": link,
        "final_url": final_url,
        "html": html,
        "redirect_wrapper_detected": False,
        "redirect_url": "",
        "refetched_article_url": "",
        "refetch_success": False,
    }
def _extract_canonical_url(html: str) -> str:
    patterns = [
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']',
        r'<meta\b[^>]*(?:property|name)=["\']og:url["\'][^>]*content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, html or "", flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""
def _extract_redirect_url_from_wrapper(html: str) -> Dict[str, Any]:
    wrapper = {"redirect_wrapper_detected": False, "redirect_url": "", "failure_reason": ""}
    if not html:
        return wrapper
    js_redirect = re.search(r"redirectUrl\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE)
    if js_redirect:
        wrapper["redirect_wrapper_detected"] = True
        wrapper["redirect_url"] = js_redirect.group(1).strip()
        wrapper["failure_reason"] = "redirect_url_extracted"
        return wrapper
    navigate_to = re.search(r"google\.navigateTo\((['\"])(.*?)\1\)", html, flags=re.IGNORECASE | re.DOTALL)
    if navigate_to:
        wrapper["redirect_wrapper_detected"] = True
        wrapper["redirect_url"] = navigate_to.group(2).strip()
        wrapper["failure_reason"] = "redirect_url_extracted"
        return wrapper
    meta_refresh = re.search(
        r'<meta\b[^>]*http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*url=([^"\';>]+)',
        html,
        flags=re.IGNORECASE,
    )
    if meta_refresh:
        wrapper["redirect_wrapper_detected"] = True
        wrapper["redirect_url"] = meta_refresh.group(1).strip()
        wrapper["failure_reason"] = "redirect_url_extracted"
        return wrapper
    return wrapper
def fetch_article_document(link: str, html_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    cached = html_cache.get(link)
    if isinstance(cached, dict):
        return cached
    if isinstance(cached, str):
        compat_doc = {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "html": cached,
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
        }
        html_cache[link] = compat_doc
        return compat_doc
    doc = fetch_article_html(link)
    html = doc.get("html", "")
    final_url = doc.get("final_url", link)
    wrapper = _extract_redirect_url_from_wrapper(html)
    doc.update(wrapper)
    canonical_url = _extract_canonical_url(html)
    if canonical_url and not doc.get("canonical_url"):
        doc["canonical_url"] = canonical_url
    if wrapper.get("redirect_wrapper_detected") and wrapper.get("redirect_url"):
        redirect_url = normalize_link(wrapper["redirect_url"])
        if is_valid_http_url(redirect_url):
            doc["refetched_article_url"] = redirect_url
            try:
                refetched = fetch_article_html(redirect_url)
                doc["html"] = refetched.get("html", "")
                doc["final_url"] = refetched.get("final_url", redirect_url)
                doc["refetch_success"] = True
                refetched_canonical = _extract_canonical_url(doc.get("html", ""))
                if refetched_canonical:
                    doc["canonical_url"] = refetched_canonical
            except Exception as exc:
                doc["refetch_success"] = False
                doc["refetch_error"] = str(exc)
    html_cache[link] = doc
    return doc
def _extract_from_meta(html: str, selector: str) -> List[str]:
    values = []
    pattern = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
    attr_pattern = re.compile(r'([a-zA-Z0-9:_-]+)=["\']([^"\']+)["\']')
    selector_lower = (selector or "").strip().lower()
    date_meta_key = re.compile(r"date|time|published|publish|modified|updated|article", re.IGNORECASE)
    date_like_value = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
    for tag in pattern.findall(html):
        attrs = {k.lower(): v.strip() for k, v in attr_pattern.findall(tag)}
        tag_l = tag.lower()
        if selector_lower and selector_lower not in tag_l:
            continue
        content = attrs.get("content", "")
        if not content:
            continue
        marker = " ".join([
            attrs.get("property", ""),
            attrs.get("name", ""),
            attrs.get("itemprop", ""),
            attrs.get("http-equiv", ""),
        ])
        if not (date_meta_key.search(marker) and date_like_value.search(content)):
            continue
        values.append(content)
    return values


def _get_notion_property(props: Dict[str, Any], key: str) -> Optional[Any]:
    if key in props:
        return props.get(key)
    key_trimmed = key.strip()
    for prop_key, value in props.items():
        if str(prop_key).strip() == key_trimmed:
            return value
    return None
def _extract_from_json_ld(html: str, selector: str) -> List[str]:
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if selector:
        return [b for b in blocks if selector in b]
    return blocks
def _iter_json_ld_objects(value: Any) -> List[Dict[str, Any]]:
    objects: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        objects.append(value)
        graph = value.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                objects.extend(_iter_json_ld_objects(node))
        elif isinstance(graph, dict):
            objects.extend(_iter_json_ld_objects(graph))
    elif isinstance(value, list):
        for item in value:
            objects.extend(_iter_json_ld_objects(item))
    return objects


def _extract_newsarticle_date_published(html: str) -> Optional[str]:
    for block in _extract_from_json_ld(html, ""):
        text = (block or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        for obj in _iter_json_ld_objects(payload):
            raw_type = obj.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            normalized = {str(t).strip().lower() for t in types if t}
            if "newsarticle" not in normalized:
                continue
            date_published = obj.get("datePublished")
            if isinstance(date_published, str) and date_published.strip():
                return date_published.strip()
    return None
class _SimpleHtmlNode:
    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_SimpleHtmlNode"] = None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: List["_SimpleHtmlNode"] = []
        self.text_parts: List[str] = []
    def get_text(self, strip: bool = False) -> str:
        chunks = list(self.text_parts)
        for child in self.children:
            child_text = child.get_text(strip=False)
            if child_text:
                chunks.append(child_text)
        text = " ".join(c for c in chunks if c)
        text = re.sub(r"\s+", " ", text)
        return text.strip() if strip else text
class _SimpleHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _SimpleHtmlNode("document", {})
        self.stack: List[_SimpleHtmlNode] = [self.root]
    def handle_starttag(self, tag: str, attrs: List[tuple]):
        attrs_dict = {str(k): str(v) for k, v in attrs if k}
        node = _SimpleHtmlNode(tag.lower(), attrs_dict, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack.append(node)
    def handle_endtag(self, _tag: str):
        if len(self.stack) > 1:
            self.stack.pop()
    def handle_data(self, data: str):
        if self.stack:
            self.stack[-1].text_parts.append(data)
def _parse_simple_selector(selector_part: str) -> Dict[str, Any]:
    token = selector_part.strip()
    if not token:
        return {"tag": "", "id": "", "classes": []}
    tag_match = re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*", token)
    tag = tag_match.group(0).lower() if tag_match else ""
    node_id = ""
    id_match = re.search(r"#([a-zA-Z0-9_-]+)", token)
    if id_match:
        node_id = id_match.group(1)
    classes = re.findall(r"\.([a-zA-Z0-9_-]+)", token)
    return {"tag": tag, "id": node_id, "classes": classes}
def _selector_matches(node: _SimpleHtmlNode, selector_part: str) -> bool:
    parsed = _parse_simple_selector(selector_part)
    if parsed["tag"] and node.tag != parsed["tag"]:
        return False
    if parsed["id"] and node.attrs.get("id", "") != parsed["id"]:
        return False
    if parsed["classes"]:
        classes = set((node.attrs.get("class", "") or "").split())
        if any(cls not in classes for cls in parsed["classes"]):
            return False
    return True
def _select_one(html: str, selector: str) -> Optional[_SimpleHtmlNode]:
    parts = [p for p in (selector or "").strip().split() if p]
    if not html or not parts:
        return None
    parser = _SimpleHtmlParser()
    parser.feed(html)
    def walk(node: _SimpleHtmlNode):
        for child in node.children:
            yield child
            yield from walk(child)
    for node in walk(parser.root):
        if not _selector_matches(node, parts[-1]):
            continue
        ancestor = node.parent
        idx = len(parts) - 2
        while idx >= 0:
            while ancestor and not _selector_matches(ancestor, parts[idx]):
                ancestor = ancestor.parent
            if not ancestor:
                break
            ancestor = ancestor.parent
            idx -= 1
        if idx < 0:
            return node
    return None
def _extract_date_text_candidates(entry: Any, rule: Dict[str, Any], html_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    source_type = rule["date_source_type"]
    source_url = normalize_link(entry.get("link", ""))
    if source_type == "rss":
        rss_parsed = parse_special_news_article_datetime(entry)
        if not rss_parsed:
            return {"ok": False, "reason": "rss datetime not found", "failure_reason": "rss_fallback_used"}
        return {
            "ok": True,
            "source": rss_parsed.get("source", "rss"),
            "text": rss_parsed["article_dt_original"].isoformat(),
            "datetime": rss_parsed["article_dt_original"],
            "source_url": source_url,
            "initial_url": source_url,
            "final_url": source_url,
            "datetime_source": "rss",
        }
    selector = (rule.get("date_css_selector", "") or "").strip()
    if source_type == "url":
        doc: Dict[str, Any] = {
            "source_url": source_url,
            "initial_url": source_url,
            "final_url": source_url,
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "canonical_url": "",
        }
        try:
            doc = fetch_article_document(source_url, html_cache)
        except Exception as exc:
            doc["fetch_error"] = str(exc)
        common = {
            "source_url": source_url,
            "initial_url": doc.get("initial_url", source_url),
            "final_url": doc.get("final_url", source_url),
            "redirect_wrapper_detected": doc.get("redirect_wrapper_detected", False),
            "redirect_url": doc.get("redirect_url", ""),
            "refetched_article_url": doc.get("refetched_article_url", ""),
            "refetch_success": doc.get("refetch_success", False),
            "selector": selector,
        }
        url_candidates = [doc.get("final_url", ""), doc.get("canonical_url", ""), source_url]
        url_candidates = [u for u in url_candidates if u]
        if not url_candidates:
            return {"ok": False, "reason": "url not found", "failure_reason": "url_pattern_not_matched", **common}
        url_text = "\n".join(url_candidates)
        return {
            "ok": True,
            "source": "url",
            "text": url_text,
            "used_value_for_parse": url_candidates[0],
            "datetime_source": "url",
            **common,
        }
    doc = fetch_article_document(source_url, html_cache)
    html = doc.get("html", "")
    common = {
        "source_url": source_url,
        "initial_url": doc.get("initial_url", source_url),
        "final_url": doc.get("final_url", source_url),
        "redirect_wrapper_detected": doc.get("redirect_wrapper_detected", False),
        "redirect_url": doc.get("redirect_url", ""),
        "refetched_article_url": doc.get("refetched_article_url", ""),
        "refetch_success": doc.get("refetch_success", False),
        "selector": selector,
        "selector_state": "configured" if selector else "selector_empty",
    }
    if source_type == "article_html":
        if selector:
            selected = _select_one(html, selector)
            if selected:
                selected_datetime_attr = (selected.attrs.get("datetime", "") or "").strip()
                selected_text = selected.get_text(strip=True)
                used_value = selected_datetime_attr or selected_text
                if used_value:
                    return {
                        "ok": True,
                        "source": "article_html(selector)",
                        "text": used_value,
                        "selector": selector,
                        "selector_found": True,
                        "selected_tag": selected.tag,
                        "selected_text": selected_text,
                        "selected_datetime_attr": selected_datetime_attr,
                        "used_value_for_parse": used_value,
                        "datetime_source": "selector_datetime_attr" if selected_datetime_attr else "selector_text",
                        **common,
                    }
                return {
                    "ok": False,
                    "reason": "selector value is empty",
                    "failure_reason": "selector_empty",
                    "selector": selector,
                    "selector_found": True,
                    "selected_tag": selected.tag,
                    "selected_text": selected_text,
                    "selected_datetime_attr": selected_datetime_attr,
                    "used_value_for_parse": "",
                    "datetime_source": "selector",
                    **common,
                }
            selector_failure = {
                "ok": False,
                "reason": f"selector not found: {selector}",
                "failure_reason": "selector_not_found",
                "selector": selector,
                "selector_found": False,
                "datetime_source": "selector",
                **common,
            }
        else:
            selector_failure = {
                "ok": False,
                "reason": "selector empty",
                "failure_reason": "selector_empty",
                "selector": "",
                "selector_found": False,
                "datetime_source": "selector",
                **common,
            }
        json_ld_date_published = _extract_newsarticle_date_published(html)
        if json_ld_date_published:
            return {
                "ok": True,
                "source": "article_html(json_ld_newsarticle)",
                "text": json_ld_date_published,
                "used_value_for_parse": json_ld_date_published,
                "raw_datetime_text": json_ld_date_published,
                "datetime_source": "json_ld_newsarticle_datePublished",
                **common,
            }
        meta_values = _extract_from_meta(html, "")
        if meta_values:
            meta_value = meta_values[0]
            return {
                "ok": True,
                "source": "article_html(meta)",
                "text": meta_value,
                "used_value_for_parse": meta_value,
                "datetime_source": "meta",
                **common,
            }
        if html:
            selector_failure["allow_fallback"] = True
            selector_failure["used_value_for_parse"] = html
            selector_failure["datetime_source"] = "article_html_fulltext"
            return selector_failure
        return {"ok": False, "reason": "article html empty", "failure_reason": "meta_not_found", **common}
    if source_type == "meta":
        values = _extract_from_meta(html, rule.get("date_css_selector", ""))
        if not values:
            return {"ok": False, "reason": "meta content not found", "failure_reason": "meta_not_found", **common}
        value = values[0]
        return {"ok": True, "source": "meta", "text": value, "used_value_for_parse": value, "datetime_source": "meta", **common}
    if source_type == "json_ld":
        values = _extract_from_json_ld(html, rule.get("date_css_selector", ""))
        if not values:
            return {"ok": False, "reason": "json_ld block not found", "failure_reason": "jsonld_not_found", **common}
        return {"ok": True, "source": "json_ld", "text": "\n".join(values), "used_value_for_parse": "\n".join(values), "datetime_source": "json_ld", **common}
    return {"ok": False, "reason": f"unsupported source type: {source_type}", "failure_reason": "pattern_not_matched", **common}
def _log_special_date_extract(media_name: str, source_type: str, payload: Dict[str, Any], pattern: str, decision: str, failure_reason: str) -> None:
    logging.info(
        "Special-news date-extract media=%s source_url=%s initial_url=%s final_url=%s redirect_wrapper_detected=%s redirect_url=%s refetched_article_url=%s refetch_success=%s source_type=%s selector=%s selector_state=%s selector_found=%s selected_tag=%s selected_text=%s selected_datetime_attr=%s datetime_source=%s raw_datetime_text=%s parsed_datetime=%s parsed_date=%s target_date=%s evaluation_mode=%s pattern=%s decision=%s failure_reason=%s",
        media_name,
        payload.get("source_url", ""),
        payload.get("initial_url", ""),
        payload.get("final_url", ""),
        payload.get("redirect_wrapper_detected", False),
        payload.get("redirect_url", ""),
        payload.get("refetched_article_url", ""),
        payload.get("refetch_success", False),
        source_type,
        payload.get("selector", ""),
        payload.get("selector_state", ""),
        payload.get("selector_found", False),
        payload.get("selected_tag", ""),
        payload.get("selected_text", ""),
        payload.get("selected_datetime_attr", ""),
        payload.get("datetime_source", ""),
        payload.get("raw_datetime_text", payload.get("used_value_for_parse", payload.get("text", ""))),
        payload.get("parsed_datetime", ""),
        payload.get("parsed_date", ""),
        payload.get("target_date", ""),
        payload.get("evaluation_mode", ""),
        pattern,
        decision,
        failure_reason,
    )
def parse_special_news_datetime_with_rule(entry: Any, media_name: str, rule: Dict[str, Any], html_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    def try_extract(source_type: str, pattern: str) -> Dict[str, Any]:
        local_rule = dict(rule)
        local_rule["date_source_type"] = source_type
        extracted = _extract_date_text_candidates(entry, local_rule, html_cache)
        if not extracted.get("ok"):
            _log_special_date_extract(media_name, source_type, extracted, pattern, "extract_failed", extracted.get("failure_reason", "extract_failed"))
            extracted["allow_fallback"] = extracted.get("allow_fallback", True)
            return {**extracted, "ok": False, "source_type": source_type}
        direct_dt = extracted.get("datetime")
        if isinstance(direct_dt, datetime):
            dt_aware = direct_dt if direct_dt.tzinfo else direct_dt.replace(tzinfo=rule["timezone"])
            dt_local = dt_aware.astimezone(rule["timezone"])
            extracted["parsed_datetime"] = dt_local.isoformat()
            extracted["parsed_date"] = dt_local.date().isoformat()
            extracted["raw_datetime_text"] = str(extracted.get("raw_datetime_text", extracted.get("used_value_for_parse", extracted.get("text", ""))))
            _log_special_date_extract(media_name, source_type, extracted, pattern, "direct_datetime_used", "")
            return {"ok": True, "source_type": source_type, "adopted_source": extracted.get("source", source_type), "datetime": dt_aware}
        used_value_for_parse = str(extracted.get("used_value_for_parse", extracted.get("text", "")))
        if extracted.get("datetime_source") == "json_ld_newsarticle_datePublished":
            try:
                dt_aware = parse_flexible_datetime(used_value_for_parse, rule["timezone"], "datetime")
            except ValueError as exc:
                _log_special_date_extract(media_name, source_type, extracted, pattern, "parse_failed", "pattern_not_matched")
                return {**extracted, "ok": False, "source_type": source_type, "reason": str(exc), "failure_reason": "pattern_not_matched", "allow_fallback": True}
            dt_local = dt_aware.astimezone(rule["timezone"])
            extracted["raw_datetime_text"] = used_value_for_parse
            extracted["parsed_datetime"] = dt_local.isoformat()
            extracted["parsed_date"] = dt_local.date().isoformat()
            _log_special_date_extract(media_name, source_type, extracted, pattern, "direct_datetime_used", "")
            return {
                "ok": True,
                "source_type": source_type,
                "adopted_source": extracted.get("source", source_type),
                "datetime": dt_aware,
                "parsed_date": extracted.get("parsed_date", ""),
            }
        if not pattern:
            _log_special_date_extract(media_name, source_type, extracted, pattern, "pattern_skipped", "pattern_not_matched")
            return {**extracted, "ok": False, "source_type": source_type, "reason": "date parse pattern is empty", "failure_reason": "pattern_not_matched", "allow_fallback": True}
        m = re.search(pattern, used_value_for_parse, flags=re.DOTALL)
        if not m:
            failure_reason = "url_pattern_not_matched" if source_type == "url" else "pattern_not_matched"
            _log_special_date_extract(media_name, source_type, extracted, pattern, "pattern_not_matched", failure_reason)
            return {**extracted, "ok": False, "source_type": source_type, "reason": "date text not matched by pattern", "failure_reason": failure_reason, "allow_fallback": True}
        if source_type == "url" and len(m.groups()) >= 3:
            matched = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        else:
            matched = m.group(0).strip()
        try:
            dt_aware = parse_flexible_datetime(matched, rule["timezone"], rule["date_granularity"])
        except ValueError as exc:
            _log_special_date_extract(media_name, source_type, extracted, pattern, "parse_failed", "pattern_not_matched")
            return {**extracted, "ok": False, "source_type": source_type, "reason": str(exc), "failure_reason": "pattern_not_matched", "allow_fallback": True}
        dt_local = dt_aware.astimezone(rule["timezone"])
        extracted["used_value_for_parse"] = used_value_for_parse
        extracted["raw_datetime_text"] = matched
        extracted["parsed_datetime"] = dt_local.isoformat()
        extracted["parsed_date"] = dt_local.date().isoformat()
        extracted["datetime_source"] = extracted.get("datetime_source", source_type)
        _log_special_date_extract(media_name, source_type, extracted, pattern, "pattern_matched", "")
        return {
            "ok": True,
            "source_type": source_type,
            "adopted_source": extracted.get("source", source_type),
            "datetime": dt_aware,
            "matched_text": matched,
            "parsed_date": extracted.get("parsed_date", ""),
        }
    primary = try_extract(rule["date_source_type"], rule.get("date_parse_pattern", ""))
    if primary.get("ok"):
        return primary
    fallback_type = rule.get("fallback_date_source_type") or ""
    if not fallback_type or not primary.get("allow_fallback", True):
        return primary
    fallback = try_extract(fallback_type, rule.get("fallback_date_parse_pattern") or rule.get("date_parse_pattern", ""))
    if fallback.get("ok"):
        fallback["primary_failure_reason"] = primary.get("reason")
        return fallback
    return {
        "ok": False,
        "source_type": fallback_type,
        "reason": f"primary={primary.get('reason')}; fallback={fallback.get('reason')}",
    }
def parse_flexible_datetime(raw: str, tz: ZoneInfo, granularity: str) -> datetime:
    text = raw.strip()
    try:
        dt_iso = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt_iso.tzinfo is None:
            dt_iso = dt_iso.replace(tzinfo=tz)
        return dt_iso.astimezone(tz)
    except ValueError:
        pass
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    dt_naive = None
    for f in fmts:
        try:
            dt_naive = datetime.strptime(normalized, f)
            break
        except ValueError:
            continue
    if dt_naive is None:
        try:
            dt_any = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            raise ValueError(f"unparseable datetime text: {text}")
        if dt_any.tzinfo is None:
            dt_any = dt_any.replace(tzinfo=tz)
        return dt_any.astimezone(tz)
    dt_aware = dt_naive.replace(tzinfo=tz)
    if granularity == "date":
        dt_aware = dt_aware.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt_aware
def build_special_media_row(
    media_name: Optional[str],
    enabled: bool,
    alert_ids: Any,
    alert_feeds_raw: str,
    display_order: Any,
    max_items: Any,
    subject_prefix: Optional[str],
    delivery_enabled: Any,
    max_items_total: Any,
    date_source_type: Optional[str] = None,
    date_parse_pattern: Optional[str] = None,
    date_css_selector: Optional[str] = None,
    date_timezone: Optional[str] = None,
    date_granularity: Optional[str] = None,
    target_date_mode: Optional[str] = None,
    lookback_hours: Any = None,
    fallback_date_source_type: Optional[str] = None,
    fallback_date_parse_pattern: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    name = (media_name or "").strip()
    if not name:
        logging.warning("Special-news media row skipped: MediaName is empty")
        return None
    if not enabled:
        logging.info("Special-news media=%s skipped: enabled=false", name)
        return None
    feeds = parse_feed_urls(alert_feeds_raw or "", name)
    if not feeds:
        logging.warning("Special-news media=%s skipped: GoogleAlertFeeds has no valid URLs", name)
        return None
    if isinstance(alert_ids, list):
        normalized_ids = [str(v).strip() for v in alert_ids if str(v).strip()]
    elif alert_ids:
        normalized_ids = [str(alert_ids).strip()]
    else:
        normalized_ids = []
    date_rule = normalize_special_date_rule(
        name,
        {
            "date_source_type": date_source_type,
            "date_parse_pattern": date_parse_pattern,
            "date_css_selector": date_css_selector,
            "date_timezone": date_timezone,
            "date_granularity": date_granularity,
            "target_date_mode": target_date_mode,
            "lookback_hours": lookback_hours,
            "fallback_date_source_type": fallback_date_source_type,
            "fallback_date_parse_pattern": fallback_date_parse_pattern,
        },
    )
    row = {
        "enabled": True,
        "media_name": name,
        "alert_ids": normalized_ids,
        "alert_feeds": feeds,
        "display_order": safe_int(display_order, 999),
        "max_items": safe_int(max_items, SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA),
        "subject_prefix": (subject_prefix or SPECIAL_NEWS_MAIL_SUBJECT_PREFIX).strip() or SPECIAL_NEWS_MAIL_SUBJECT_PREFIX,
        "delivery_enabled": bool(delivery_enabled) if delivery_enabled is not None else True,
        "max_items_total": safe_int(max_items_total, SPECIAL_NEWS_MAX_ITEMS_TOTAL),
        "date_rule": date_rule,
    }
    logging.info(
        "Special-news media loaded name=%s enabled=%s feeds=%s alert_ids=%s DateSourceType=%s DateCssSelector=%s DateGranularity=%s TargetDateMode=%s",
        row["media_name"],
        row["enabled"],
        len(row["alert_feeds"]),
        len(row["alert_ids"]),
        row["date_rule"]["date_source_type"],
        row["date_rule"].get("date_css_selector", ""),
        row["date_rule"]["date_granularity"],
        row["date_rule"]["target_date_mode"],
    )
    return row
def fetch_special_news_config_from_notion() -> Optional[List[Dict[str, Any]]]:
    notion_enabled = parse_env_bool("NOTION_SPECIAL_NEWS_ENABLED", SPECIAL_NEWS_NOTION_ENABLED_DEFAULT)
    if not notion_enabled:
        logging.info("Special-news config source: local file (Notion disabled)")
        return None
    if not NOTION_TOKEN or not NOTION_SPECIAL_NEWS_DB_ID:
        logging.warning("Notion special-news enabled but credentials are missing; fallback to local config")
        return None
    url = f"https://api.notion.com/v1/databases/{NOTION_SPECIAL_NEWS_DB_ID}/query"
    req = urllib.request.Request(url, data=json.dumps({}).encode("utf-8"), headers=notion_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except Exception as exc:
        logging.error("Failed to fetch Notion special-news config: %s; fallback to local config", exc)
        return None
    rows = payload.get("results", [])
    if not rows:
        logging.warning("Notion special-news DB has no rows; fallback to local config")
        return None
    media_rows = []
    for idx, row in enumerate(rows):
        props = row.get("properties", {})
        media_name_raw = extract_notion_property_value(props.get("MediaName"))
        selector_prop = _get_notion_property(props, "DateCssSelector")
        selector_prop_exists = selector_prop is not None
        selector_raw = extract_notion_property_value(selector_prop) if selector_prop_exists else None
        selector_value = str(selector_raw or "").strip()
        selector_state = "missing_column" if not selector_prop_exists else ("empty_value" if selector_value == "" else "present_value")
        logging.info(
            "Special-news Notion row index=%s media=%s DateCssSelector_state=%s DateCssSelector_value=%s",
            idx,
            media_name_raw or "",
            selector_state,
            selector_value,
        )
        normalized = build_special_media_row(
            media_name=media_name_raw,
            enabled=bool(extract_notion_property_value(props.get("Enabled"))),
            alert_ids=extract_notion_property_value(props.get("GoogleAlertIds")) or [],
            alert_feeds_raw=extract_notion_property_value(props.get("GoogleAlertFeeds")) or "",
            display_order=extract_notion_property_value(props.get("DisplayOrder")),
            max_items=extract_notion_property_value(props.get("MaxItemsPerMedia")),
            subject_prefix=extract_notion_property_value(props.get("SubjectPrefix")),
            delivery_enabled=extract_notion_property_value(props.get("DeliveryEnabled")),
            max_items_total=extract_notion_property_value(props.get("MaxItemsTotal")),
            date_source_type=extract_notion_property_value(props.get("DateSourceType")),
            date_parse_pattern=extract_notion_property_value(props.get("DateParsePattern")),
            date_css_selector=selector_value,
            date_timezone=extract_notion_property_value(props.get("DateTimezone")),
            date_granularity=extract_notion_property_value(props.get("DateGranularity")),
            target_date_mode=extract_notion_property_value(props.get("TargetDateMode")),
            lookback_hours=extract_notion_property_value(props.get("LookbackHours")),
            fallback_date_source_type=extract_notion_property_value(props.get("FallbackDateSourceType")),
            fallback_date_parse_pattern=extract_notion_property_value(props.get("FallbackDateParsePattern")),
        )
        if normalized is None:
            logging.warning("Notion media row index=%s skipped due to invalid settings", idx)
            continue
        media_rows.append(normalized)
    return media_rows
def load_special_news_media_config() -> Dict[str, Any]:
    notion_rows = fetch_special_news_config_from_notion()
    if notion_rows:
        # 優先順位: 環境変数 > Notion > コード既定値
        subject_prefix = SPECIAL_NEWS_MAIL_SUBJECT_PREFIX or notion_rows[0].get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX)
        delivery_enabled = notion_rows[0].get("delivery_enabled", True)
        max_items_total = notion_rows[0].get("max_items_total", SPECIAL_NEWS_MAX_ITEMS_TOTAL)
        return {
            "source": "notion",
            "media": sorted(notion_rows, key=lambda x: x["display_order"]),
            "delivery_enabled": delivery_enabled,
            "max_items_total": max_items_total,
            "subject_prefix": subject_prefix,
        }
    path = Path(SPECIAL_NEWS_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Special news config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    media_rows = []
    for idx, m in enumerate(payload.get("media", [])):
        normalized = build_special_media_row(
            media_name=m.get("media_name"),
            enabled=bool(m.get("enabled", True)),
            alert_ids=m.get("alert_ids", []),
            alert_feeds_raw="\n".join(m.get("alert_feeds", [])),
            display_order=m.get("display_order"),
            max_items=m.get("max_items"),
            subject_prefix=m.get("subject_prefix"),
            delivery_enabled=payload.get("delivery_enabled", True),
            max_items_total=payload.get("max_items_total", SPECIAL_NEWS_MAX_ITEMS_TOTAL),
            date_source_type=m.get("date_source_type"),
            date_parse_pattern=m.get("date_parse_pattern"),
            date_css_selector=m.get("date_css_selector"),
            date_timezone=m.get("date_timezone"),
            date_granularity=m.get("date_granularity"),
            target_date_mode=m.get("target_date_mode"),
            lookback_hours=m.get("lookback_hours"),
            fallback_date_source_type=m.get("fallback_date_source_type"),
            fallback_date_parse_pattern=m.get("fallback_date_parse_pattern"),
        )
        if normalized is None:
            logging.warning("Local media row index=%s skipped due to invalid settings", idx)
            continue
        media_rows.append(normalized)
    return {
        "source": "local",
        "media": sorted(media_rows, key=lambda x: x["display_order"]),
        "delivery_enabled": bool(payload.get("delivery_enabled", True)),
        "max_items_total": safe_int(payload.get("max_items_total"), SPECIAL_NEWS_MAX_ITEMS_TOTAL),
        "subject_prefix": payload.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
    }
def extract_entries_for_special_window(
    entries: List[Any],
    now_jst: datetime,
    media_name: str,
    feed_url: str,
    date_rule: Dict[str, Any],
    html_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    filtered = []
    html_cache = html_cache if html_cache is not None else {}
    tz = date_rule["timezone"]
    now_local = now_jst.astimezone(tz)
    window_start = now_local - timedelta(hours=date_rule["lookback_hours"])
    run_date_jst = now_jst.astimezone(ZoneInfo("Asia/Tokyo")).date()
    allowed_dates = {run_date_jst - timedelta(days=1), run_date_jst}
    for e in entries:
        title = clean(e.get("title", ""))
        parsed_dt_info = parse_special_news_datetime_with_rule(e, media_name, date_rule, html_cache)
        if not parsed_dt_info.get("ok"):
            logging.warning(
                "Special-news media=%s DateSourceType=%s DateGranularity=%s TargetDateMode=%s feed=%s title=%s extraction_failed reason=%s",
                media_name,
                date_rule["date_source_type"],
                date_rule["date_granularity"],
                date_rule["target_date_mode"],
                shorten_url(feed_url),
                title or "(no title)",
                parsed_dt_info.get("reason", "unknown"),
            )
            continue
        article_dt_local = parsed_dt_info["datetime"].astimezone(tz)
        parsed_date_text = str(parsed_dt_info.get("parsed_date") or "")
        evaluation_mode = "rolling_24h"
        decision = "accepted"
        failure_reason = ""
        in_window = False
        if date_rule["target_date_mode"] == "calendar_day":
            evaluation_mode = "calendar_day"
            article_date = date.fromisoformat(parsed_date_text) if parsed_date_text else article_dt_local.date()
            in_window = article_date in allowed_dates
            if not in_window:
                decision = "target_date_mismatch"
                failure_reason = "target_date_mismatch"
        else:
            in_window = window_start <= article_dt_local < now_local
            if not in_window:
                decision = "out_of_window"
                failure_reason = "out_of_window"
        logging.info(
            "Special-news media=%s DateSourceType=%s DateGranularity=%s TargetDateMode=%s feed=%s title=%s adopted_source=%s article_dt=%s parsed_date=%s run_date_jst=%s allowed_dates=%s evaluation_mode=%s decision=%s failure_reason=%s",
            media_name,
            date_rule["date_source_type"],
            date_rule["date_granularity"],
            date_rule["target_date_mode"],
            shorten_url(feed_url),
            title or "(no title)",
            parsed_dt_info.get("adopted_source", parsed_dt_info.get("source_type", "unknown")),
            article_dt_local.isoformat(),
            parsed_date_text,
            run_date_jst.isoformat(),
            str(sorted(d.isoformat() for d in allowed_dates)),
            evaluation_mode,
            decision,
            failure_reason,
        )
        if not in_window:
            continue
        published_text = article_dt_local.strftime("%Y-%m-%d") if date_rule["date_granularity"] == "date" else article_dt_local.strftime("%Y-%m-%d %H:%M")
        filtered.append({
            "title": title,
            "link": normalize_link(e.get("link", "")),
            "published": published_text,
        })
    return filtered
def collect_special_news_articles(now_jst: Optional[datetime] = None) -> Dict[str, Any]:
    now_jst = now_jst or datetime.now(JST)
    logging.info("Special-news job started")
    logging.info(
        "Special-news window_end=%s default_timezone=Asia/Tokyo default_hours=%s",
        now_jst.isoformat(),
        SPECIAL_NEWS_WINDOW_HOURS,
    )
    config = load_special_news_media_config()
    media_config = config["media"]
    results = []
    delivery_enabled = bool(config["delivery_enabled"])
    max_items_total = safe_int(config["max_items_total"], SPECIAL_NEWS_MAX_ITEMS_TOTAL)
    logging.info(
        "Special-news config source=%s media_count=%s delivery_enabled=%s max_items_total=%s",
        config["source"],
        len(media_config),
        delivery_enabled,
        max_items_total,
    )
    for media in media_config:
        all_entries = []
        feed_filtered = []
        html_cache: Dict[str, Dict[str, Any]] = {}
        date_rule = media.get("date_rule", normalize_special_date_rule(media["media_name"]))
        for feed in media.get("alert_feeds", []):
            feed_short = shorten_url(feed)
            try:
                parsed = feedparser.parse(feed)
                entries = getattr(parsed, "entries", []) or []
                bozo = bool(getattr(parsed, "bozo", False))
                if bozo:
                    logging.warning("Special-news media=%s feed=%s parse warning bozo=%s", media["media_name"], feed_short, getattr(parsed, "bozo_exception", "unknown"))
                logging.info("Special-news media=%s feed=%s fetch=success fetched=%s", media["media_name"], feed_short, len(entries))
            except Exception as exc:
                logging.error("Special-news media=%s feed=%s fetch=failed reason=%s", media["media_name"], feed_short, exc)
                continue
            all_entries.extend(entries)
            filtered_items = extract_entries_for_special_window(
                entries,
                now_jst,
                media["media_name"],
                feed,
                date_rule,
                html_cache,
            )
            logging.info(
                "Special-news media=%s feed=%s filtered=%s",
                media["media_name"],
                feed_short,
                len(filtered_items),
            )
            feed_filtered.extend(filtered_items)
        unique = []
        seen = set()
        for item in feed_filtered:
            key = normalize_title(item.get("title", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        limited = unique[:media.get("max_items", SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)]
        logging.info("Special-news media=%s fetched=%s filtered=%s", media["media_name"], len(all_entries), len(limited))
        results.append({
            "media_name": media["media_name"],
            "items": limited,
            "display_order": media["display_order"],
            "subject_prefix": media.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
            "alert_ids": media.get("alert_ids", []),
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
        "subject_prefix": config.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
    }
def render_special_news_html(target_date: datetime, media_results: Optional[List[Dict[str, Any]]], total_items: int) -> str:
    template_path = Path(SPECIAL_NEWS_TEMPLATE_PATH)
    template = Template(template_path.read_text(encoding="utf-8"))
    safe_media_results = [m for m in (media_results or []) if isinstance(m, dict)]
    section_html = []
    for media in safe_media_results:
        media_name = str(media.get("media_name") or "(媒体名未設定)")
        items = media.get("items") or []
        valid_items = [i for i in items if isinstance(i, dict)]
        lis = "".join(
            f'<li><a href="{escape(str(i.get("link") or "#"))}">{escape(str(i.get("title") or "(タイトルなし)"))}</a>'
            f'<span class="meta">（{escape(str(i.get("published") or "日時不明"))}）</span></li>'
            for i in valid_items
        )
        if not lis:
            lis = "<li>対象日に該当記事はありませんでした。</li>"
        section_html.append(
            f"<section><h3>{escape(media_name)}</h3>"
            f"<p class='count'>件数: {len(valid_items)}件</p><ol>{lis}</ol></section>"
        )
    if not section_html:
        section_html.append("<section><h3>対象媒体</h3><p>対象日に該当記事はありませんでした。</p></section>")
    return template.safe_substitute(
        target_date=target_date.strftime("%Y-%m-%d"),
        total_items=str(total_items),
        media_sections="\n".join(section_html),
    )
def build_special_news_subject(target_date: datetime, media_results: List[Dict[str, Any]], subject_prefix: Optional[str] = None) -> str:
    prefix = subject_prefix or SPECIAL_NEWS_MAIL_SUBJECT_PREFIX
    media_names = "・".join(m.get("media_name", "") for m in media_results if m.get("media_name"))
    suffix = f"（{media_names}）" if media_names else "（対象媒体なし）"
    return f"{prefix}{target_date.strftime('%Y-%m-%d')}更新分{suffix}"
def send_mail_generic(html, subject, to_list, cc_list=None, bcc_list=None, text_fallback: Optional[str] = None):
    cc_list = cc_list or []
    bcc_list = bcc_list or []
    recipients = [r for r in (to_list + cc_list + bcc_list) if r]
    if not recipients:
        raise ValueError("No recipients for email delivery")
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("Daily News Bot", MAIL_FROM))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if text_fallback:
        msg.preamble = text_fallback
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
    now_jst = datetime.now(JST)
    result = collect_special_news_articles(now_jst)
    if not result["delivery_enabled"]:
        logging.info("Special-news delivery disabled by configuration")
        return
    # 宛先優先順位: 環境変数のみ（Notion未対応）
    to_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_TO)
    cc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_CC)
    bcc_list = parse_mail_recipients(SPECIAL_NEWS_MAIL_BCC)
    all_recipients = [r for r in (to_list + cc_list + bcc_list) if r]
    if not all_recipients:
        logging.warning("Special-news recipients are empty; skip delivery")
        return
    logging.info(
        "Special-news recipients to=%s cc=%s bcc=%s",
        [mask_email(v) for v in to_list],
        [mask_email(v) for v in cc_list],
        [mask_email(v) for v in bcc_list],
    )
    try:
        html = render_special_news_html(now_jst, result.get("media_results"), result.get("total_items", 0))
    except Exception as exc:
        raise RuntimeError(f"Failed to render special-news HTML: {exc}") from exc
    subject = build_special_news_subject(
        now_jst,
        result.get("media_results", []),
        subject_prefix=result.get("subject_prefix", SPECIAL_NEWS_MAIL_SUBJECT_PREFIX),
    )
    text_fallback = f"専門紙記事一覧\n対象日: {now_jst.strftime('%Y-%m-%d')}\n総件数: {result.get('total_items', 0)}件"
    send_mail_generic(html, subject, to_list, cc_list, bcc_list, text_fallback=text_fallback)
    logging.info("Special-news email delivered successfully; total_items=%s", result.get("total_items", 0))
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=["main", "special", "all"], default="main")
    args = parser.parse_args()
    if args.job in {"main", "all"}:
        send_mail(generate_html())
    if args.job in {"special", "all"}:
        run_special_news_delivery()
