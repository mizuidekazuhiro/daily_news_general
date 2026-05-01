import json
import logging
import os
import re
import smtplib
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup


socket.setdefaulttimeout(12)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
DEFAULT_CONFIG_PATH = Path("config/direct_site_watchers.json")
DEFAULT_TEMPLATE_PATH = Path("templates/direct_site_updates_email.html")
DEFAULT_SUBJECT_PREFIX = "【サイト更新一覧】"

MAIL_FROM = os.getenv("MAIL_FROM", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
DIRECT_SITE_MAIL_TO = os.getenv("DIRECT_SITE_MAIL_TO", "")
DIRECT_SITE_MAIL_CC = os.getenv("DIRECT_SITE_MAIL_CC", "")
DIRECT_SITE_MAIL_BCC = os.getenv("DIRECT_SITE_MAIL_BCC", "")
DIRECT_SITE_MAIL_SUBJECT_PREFIX = os.getenv("DIRECT_SITE_MAIL_SUBJECT_PREFIX", "")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DIRECT_SITES_DB_ID = os.getenv("NOTION_DIRECT_SITES_DB_ID", "")
NOTION_DIRECT_SITES_ENABLED = os.getenv("NOTION_DIRECT_SITES_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@dataclass
class SiteItem:
    site_name: str
    title: str
    url: str
    published_at: datetime
    published_label: str
    date_source: str


def parse_recipients(raw: str) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for token in re.split(r"[,;\n]", raw):
        value = token.strip()
        if value:
            out.append(value)
    return out


def notion_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def extract_notion_prop(prop: Optional[Dict[str, Any]]) -> Any:
    if not prop:
        return None
    ptype = prop.get("type")
    if ptype == "checkbox":
        return prop.get("checkbox")
    if ptype == "number":
        return prop.get("number")
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if ptype in {"rich_text", "title"}:
        return "".join(p.get("plain_text", "") for p in prop.get(ptype, []))
    return None


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Tokyo")


def _split_lines(text: str) -> List[str]:
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_list_page_urls(raw_text: str) -> List[str]:
    if not raw_text:
        return []
    text = str(raw_text).strip()
    if not text:
        return []

    prepared = re.sub(r"(?<!^)https?://", lambda m: f" {m.group(0)}", text)
    markdown_urls = re.findall(r"\[[^\]]*\]\((https?://[^)\s]+)\)", prepared)
    cleaned = re.sub(r"\[[^\]]*\]\((https?://[^)\s]+)\)", r" \1 ", prepared)

    found_urls = markdown_urls + re.findall(r"https?://[^\s,;\]\[\)\(\"'<>]+", cleaned)
    seen = set()
    normalized_urls: List[str] = []
    for url in found_urls:
        normalized = normalize_url(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_urls.append(normalized)
    return normalized_urls


def normalize_article_url_pattern(pattern: str) -> str:
    return re.sub(r"\s+", "", (pattern or "").strip())


def normalize_site_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "SiteName": str(raw.get("SiteName", "")).strip(),
        "Enabled": bool(raw.get("Enabled", True)),
        "ListPageUrls": parse_list_page_urls(str(raw.get("ListPageUrls", ""))),
        "DisplayOrder": _safe_int(raw.get("DisplayOrder"), 9999),
        "MaxItemsPerSite": max(1, _safe_int(raw.get("MaxItemsPerSite"), 20)),
        "DeliveryEnabled": bool(raw.get("DeliveryEnabled", True)),
        "MaxItemsTotal": max(1, _safe_int(raw.get("MaxItemsTotal"), 50)),
        "SubjectPrefix": str(raw.get("SubjectPrefix", "")).strip(),
        "ArticleUrlPattern": normalize_article_url_pattern(str(raw.get("ArticleUrlPattern", ""))),
        "ListDatePattern": str(raw.get("ListDatePattern", "")).strip(),
        "ArticleDatePattern": str(raw.get("ArticleDatePattern", "")).strip(),
        "DateTimezone": str(raw.get("DateTimezone", "Asia/Tokyo")).strip() or "Asia/Tokyo",
        "DateGranularity": str(raw.get("DateGranularity", "datetime")).strip().lower() or "datetime",
        "TargetDateMode": str(raw.get("TargetDateMode", "rolling_24h")).strip().lower() or "rolling_24h",
        "LookbackHours": max(1, _safe_int(raw.get("LookbackHours"), 24)),
        "MaxPages": max(1, _safe_int(raw.get("MaxPages"), 1)),
        "ListContainerSelector": str(raw.get("ListContainerSelector", "")).strip(),
        "ArticleLinkSelector": str(raw.get("ArticleLinkSelector", "")).strip(),
        "ListDateSelector": str(raw.get("ListDateSelector", "")).strip(),
        "ArticleDateSelector": str(raw.get("ArticleDateSelector", "")).strip(),
        "NextPageSelector": str(raw.get("NextPageSelector", "")).strip(),
        "IncludeTitlePattern": str(raw.get("IncludeTitlePattern", "")).strip(),
        "ExcludeTitlePattern": str(raw.get("ExcludeTitlePattern", "")).strip(),
    }
    row["timezone"] = _safe_tz(row["DateTimezone"])
    return row


def load_sites_from_notion() -> List[Dict[str, Any]]:
    if not (NOTION_DIRECT_SITES_ENABLED and NOTION_TOKEN and NOTION_DIRECT_SITES_DB_ID):
        raise RuntimeError("notion_disabled_or_missing_credentials")
    url = f"https://api.notion.com/v1/databases/{NOTION_DIRECT_SITES_DB_ID}/query"
    rows: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    page = 0
    while True:
        body: Dict[str, Any] = {}
        if cursor:
            body["start_cursor"] = cursor
        request = urllib.request.Request(
            url,
            method="POST",
            headers=notion_headers(),
            data=json.dumps(body).encode("utf-8"),
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        page += 1
        page_results = payload.get("results", [])
        logging.info("direct-site Notion fetch page=%s rows=%s", page, len(page_results))
        for entry in page_results:
            props = entry.get("properties", {})
            row = {
                "SiteName": extract_notion_prop(props.get("SiteName")),
                "Enabled": extract_notion_prop(props.get("Enabled")),
                "ListPageUrls": extract_notion_prop(props.get("ListPageUrls")),
                "DisplayOrder": extract_notion_prop(props.get("DisplayOrder")),
                "MaxItemsPerSite": extract_notion_prop(props.get("MaxItemsPerSite")),
                "DeliveryEnabled": extract_notion_prop(props.get("DeliveryEnabled")),
                "MaxItemsTotal": extract_notion_prop(props.get("MaxItemsTotal")),
                "SubjectPrefix": extract_notion_prop(props.get("SubjectPrefix")),
                "ArticleUrlPattern": extract_notion_prop(props.get("ArticleUrlPattern")),
                "ListDatePattern": extract_notion_prop(props.get("ListDatePattern")),
                "ArticleDatePattern": extract_notion_prop(props.get("ArticleDatePattern")),
                "DateTimezone": extract_notion_prop(props.get("DateTimezone")),
                "DateGranularity": extract_notion_prop(props.get("DateGranularity")),
                "TargetDateMode": extract_notion_prop(props.get("TargetDateMode")),
                "LookbackHours": extract_notion_prop(props.get("LookbackHours")),
                "MaxPages": extract_notion_prop(props.get("MaxPages")),
                "ListContainerSelector": extract_notion_prop(props.get("ListContainerSelector")),
                "ArticleLinkSelector": extract_notion_prop(props.get("ArticleLinkSelector")),
                "ListDateSelector": extract_notion_prop(props.get("ListDateSelector")),
                "ArticleDateSelector": extract_notion_prop(props.get("ArticleDateSelector")),
                "NextPageSelector": extract_notion_prop(props.get("NextPageSelector")),
                "IncludeTitlePattern": extract_notion_prop(props.get("IncludeTitlePattern")),
                "ExcludeTitlePattern": extract_notion_prop(props.get("ExcludeTitlePattern")),
            }
            rows.append(normalize_site_row(row))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return rows


def load_sites_from_json(config_path: Path = DEFAULT_CONFIG_PATH) -> List[Dict[str, Any]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    rows = payload.get("sites", payload)
    return [normalize_site_row(item) for item in rows]


def load_sites() -> Tuple[str, List[Dict[str, Any]]]:
    try:
        notion_sites = load_sites_from_notion()
        if notion_sites:
            logging.info("config source=notion count=%s", len(notion_sites))
            return "notion", notion_sites
        raise RuntimeError("notion_empty")
    except Exception as exc:
        logging.warning("config source fallback to local because=%s", exc)
    local_sites = load_sites_from_json()
    logging.info("config source=local_json count=%s", len(local_sites))
    return "local_json", local_sites


def build_request_headers(url: str) -> Dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if origin:
        headers["Referer"] = f"{origin}/"
    return headers


def fetch_html(url: str) -> Tuple[str, int, str]:
    request = urllib.request.Request(url, headers=build_request_headers(url))
    with urllib.request.urlopen(request, timeout=12) as response:
        status = getattr(response, "status", 200)
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read()
        final_url = response.geturl()
    try:
        html = raw.decode(charset, errors="replace")
    except Exception:
        html = raw.decode("utf-8", errors="replace")
    return html, status, final_url


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(sorted(query_pairs))
    return urllib.parse.urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def _find_text_by_selector(node: BeautifulSoup, selector: str) -> str:
    if not selector:
        return ""
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def _extract_date_by_regex(text: str, pattern: str) -> str:
    if not text or not pattern:
        return ""
    m = re.search(pattern, text)
    return m.group(0).strip() if m else ""


def parse_date_text(raw_text: str, tz: ZoneInfo, granularity: str) -> Optional[datetime]:
    text = (raw_text or "").strip()
    if not text:
        return None
    patterns = [
        (r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})", True),
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})?", True),
        (r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", False),
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日", False),
        (r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", False),
        (r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", False),
    ]

    def _parse_month_name(month_text: str) -> Optional[int]:
        for fmt in ("%B", "%b"):
            try:
                return datetime.strptime(month_text, fmt).month
            except ValueError:
                continue
        return None

    for pattern, has_time in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            if pattern == r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})":
                month = _parse_month_name(m.group(2))
                if month is None:
                    continue
                dt = datetime(int(m.group(3)), month, int(m.group(1)))
            elif pattern == r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})":
                month = _parse_month_name(m.group(1))
                if month is None:
                    continue
                dt = datetime(int(m.group(3)), month, int(m.group(2)))
            elif has_time:
                hour = int(m.group(4))
                minute = int(m.group(5) or 0)
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, minute)
            else:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if granularity == "date":
                dt = datetime.combine(dt.date(), time(0, 0))
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    try:
        iso_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if iso_dt.tzinfo is None:
            iso_dt = iso_dt.replace(tzinfo=tz)
        return iso_dt.astimezone(tz)
    except ValueError:
        return None


def is_in_window(article_dt: datetime, cfg: Dict[str, Any], now_dt: datetime) -> bool:
    mode = cfg["TargetDateMode"]
    if mode == "calendar_day":
        today = now_dt.astimezone(cfg["timezone"]).date()
        return article_dt.astimezone(cfg["timezone"]).date() in {today, today - timedelta(days=1)}
    start = now_dt.astimezone(cfg["timezone"]) - timedelta(hours=cfg["LookbackHours"])
    return start <= article_dt.astimezone(cfg["timezone"]) <= now_dt.astimezone(cfg["timezone"])


def passes_title_filter(title: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    if cfg["IncludeTitlePattern"] and not re.search(cfg["IncludeTitlePattern"], title):
        return False, "include_pattern_not_matched"
    if cfg["ExcludeTitlePattern"] and re.search(cfg["ExcludeTitlePattern"], title):
        return False, "exclude_pattern_matched"
    return True, "accepted"


def find_next_page_url(soup: BeautifulSoup, current_url: str, selector: str) -> Optional[str]:
    if selector:
        node = soup.select_one(selector)
        if node and node.get("href"):
            return urllib.parse.urljoin(current_url, node.get("href"))
    for node in soup.select("a[rel='next'], a.next, a.pagination-next"):
        href = node.get("href")
        if href:
            return urllib.parse.urljoin(current_url, href)
    for node in soup.select("a"):
        label = node.get_text(" ", strip=True).lower()
        if label in {"next", "next >", ">", "older"} and node.get("href"):
            return urllib.parse.urljoin(current_url, node.get("href"))
    return None


def extract_candidates_from_list_page(
    html: str,
    page_url: str,
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    raw_pattern = str(cfg.get("ArticleUrlPattern", ""))
    normalized_pattern = normalize_article_url_pattern(raw_pattern)
    anchors = soup.select(cfg["ArticleLinkSelector"]) if cfg["ArticleLinkSelector"] else soup.select("a[href]")
    href_samples: List[str] = []
    absolute_samples: List[str] = []
    total_with_href = 0
    seen = set()
    out: List[Dict[str, Any]] = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        if not href:
            continue
        total_with_href += 1
        if len(href_samples) < 10:
            href_samples.append(href)
        absolute_url = urllib.parse.urljoin(page_url, href)
        if len(absolute_samples) < 10:
            absolute_samples.append(absolute_url)
        if normalized_pattern and not (
            re.search(normalized_pattern, absolute_url) or re.search(normalized_pattern, href)
        ):
            continue
        normalized = normalize_url(absolute_url)
        if normalized in seen:
            continue
        seen.add(normalized)
        title = a.get_text(" ", strip=True) or (a.get("title") or "").strip()
        if not title:
            continue

        date_text = ""
        date_source = "failed"
        if cfg["ListDateSelector"]:
            container = a
            date_text = _find_text_by_selector(container, cfg["ListDateSelector"]) if container else ""
            if date_text:
                date_source = "list_selector"
        if not date_text and cfg["ListDatePattern"]:
            block = a.parent.get_text(" ", strip=True) if a.parent else ""
            date_text = _extract_date_by_regex(block, cfg["ListDatePattern"])
            if date_text:
                date_source = "list_regex"

        out.append({"title": title, "url": absolute_url, "date_text": date_text, "date_source": date_source})
    logging.info(
        "site name=%s raw ArticleUrlPattern=%s normalized ArticleUrlPattern=%s extracted links count=%s selector=%s a_href_total=%s href_samples=%s absolute_samples=%s",
        cfg["SiteName"],
        raw_pattern or "(empty)",
        normalized_pattern or "(empty)",
        len(out),
        cfg["ArticleLinkSelector"] or "(default a[href])",
        total_with_href,
        href_samples,
        absolute_samples,
    )
    return out


def enrich_date_from_article(candidate: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        html, status, _ = fetch_html(candidate["url"])
        logging.info(
            "site name=%s fetched article url=%s status=%s",
            cfg["SiteName"],
            candidate["url"],
            status,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            logging.warning(
                "site name=%s article fetch blocked reason=site_blocked_or_requires_auth status=403 url=%s",
                cfg["SiteName"],
                candidate["url"],
            )
        else:
            logging.warning("site name=%s article fetch error url=%s error=%s", cfg["SiteName"], candidate["url"], exc)
        return candidate
    except Exception as exc:
        logging.warning("site name=%s article fetch error url=%s error=%s", cfg["SiteName"], candidate["url"], exc)
        return candidate
    soup = BeautifulSoup(html, "html.parser")
    date_text = ""
    source = "failed"
    if cfg["ArticleDateSelector"]:
        date_text = _find_text_by_selector(soup, cfg["ArticleDateSelector"])
        if date_text:
            source = "article_selector"
    if not date_text and cfg["ArticleDatePattern"]:
        date_text = _extract_date_by_regex(soup.get_text(" ", strip=True), cfg["ArticleDatePattern"])
        if date_text:
            source = "article_regex"
    if date_text:
        candidate["date_text"] = date_text
        candidate["date_source"] = source
    return candidate


def collect_site_items(cfg: Dict[str, Any], now_dt: datetime) -> List[SiteItem]:
    if not cfg["Enabled"]:
        logging.info("site name=%s skipped reason=disabled", cfg["SiteName"])
        return []
    collected: List[SiteItem] = []
    site_seen_urls = set()
    pages_visited = set()

    logging.info("site name=%s configured_url_count=%s configured_urls=%s", cfg["SiteName"], len(cfg["ListPageUrls"]), cfg["ListPageUrls"])
    for list_url in cfg["ListPageUrls"]:
        current_url = list_url
        for _ in range(cfg["MaxPages"]):
            if current_url in pages_visited:
                break
            pages_visited.add(current_url)
            try:
                html, status, final_url = fetch_html(current_url)
                logging.info("site name=%s fetched list url=%s status=%s", cfg["SiteName"], current_url, status)
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    logging.warning(
                        "site name=%s list fetch blocked reason=site_blocked_or_requires_auth status=403 url=%s",
                        cfg["SiteName"],
                        current_url,
                    )
                else:
                    logging.warning("site name=%s list fetch error url=%s error=%s", cfg["SiteName"], current_url, exc)
                break
            except Exception as exc:
                logging.warning("site name=%s list fetch error url=%s error=%s", cfg["SiteName"], current_url, exc)
                break

            candidates = extract_candidates_from_list_page(html, final_url, cfg)
            logging.info("site name=%s extracted links count=%s", cfg["SiteName"], len(candidates))
            for cand in candidates:
                normalized_url = normalize_url(cand["url"])
                if normalized_url in site_seen_urls:
                    logging.info("site name=%s skipped reason=site_url_duplicate url=%s", cfg["SiteName"], cand["url"])
                    continue

                ok, reason = passes_title_filter(cand["title"], cfg)
                if not ok:
                    logging.info("site name=%s skipped reason=%s title=%s", cfg["SiteName"], reason, cand["title"])
                    continue

                parsed_dt = parse_date_text(cand["date_text"], cfg["timezone"], cfg["DateGranularity"])
                if not parsed_dt:
                    cand = enrich_date_from_article(cand, cfg)
                    parsed_dt = parse_date_text(cand["date_text"], cfg["timezone"], cfg["DateGranularity"])

                logging.info(
                    "site name=%s date extraction source=%s url=%s",
                    cfg["SiteName"],
                    cand["date_source"],
                    cand["url"],
                )

                if not parsed_dt:
                    logging.info("site name=%s skipped reason=date_parse_failed url=%s", cfg["SiteName"], cand["url"])
                    continue
                if not is_in_window(parsed_dt, cfg, now_dt):
                    logging.info("site name=%s skipped reason=out_of_window url=%s", cfg["SiteName"], cand["url"])
                    continue

                site_seen_urls.add(normalized_url)
                collected.append(
                    SiteItem(
                        site_name=cfg["SiteName"],
                        title=cand["title"],
                        url=cand["url"],
                        published_at=parsed_dt,
                        published_label=parsed_dt.astimezone(cfg["timezone"]).strftime("%Y-%m-%d %H:%M"),
                        date_source=cand["date_source"],
                    )
                )
                logging.info("site name=%s accepted url=%s", cfg["SiteName"], cand["url"])
                if len(collected) >= cfg["MaxItemsPerSite"]:
                    break

            if len(collected) >= cfg["MaxItemsPerSite"]:
                break

            soup = BeautifulSoup(html, "html.parser")
            next_url = find_next_page_url(soup, final_url, cfg["NextPageSelector"])
            if not next_url or next_url in pages_visited:
                break
            current_url = next_url

    logging.info("site name=%s final items per site=%s", cfg["SiteName"], len(collected))
    return collected[: cfg["MaxItemsPerSite"]]


def dedupe_and_limit(
    sites: Sequence[Dict[str, Any]],
    site_items: Dict[str, List[SiteItem]],
) -> Tuple[List[Tuple[Dict[str, Any], List[SiteItem]]], int]:
    global_url_seen = set()
    global_title_seen = set()
    duplicates_removed = 0
    global_limit_candidates = [s["MaxItemsTotal"] for s in sites if s.get("DeliveryEnabled")]
    global_limit = min(global_limit_candidates) if global_limit_candidates else 50

    ordered: List[Tuple[Dict[str, Any], List[SiteItem]]] = []
    total = 0
    for cfg in sorted(sites, key=lambda r: r["DisplayOrder"]):
        items = []
        for item in site_items.get(cfg["SiteName"], []):
            ukey = normalize_url(item.url)
            tkey = re.sub(r"\s+", " ", item.title.strip().lower())
            if ukey in global_url_seen or tkey in global_title_seen:
                duplicates_removed += 1
                continue
            global_url_seen.add(ukey)
            global_title_seen.add(tkey)
            items.append(item)
            total += 1
            if total >= global_limit:
                break
        ordered.append((cfg, items))
        if total >= global_limit:
            break

    logging.info("duplicates removed=%s", duplicates_removed)
    return ordered, duplicates_removed


def render_email(template_path: Path, sections: List[Tuple[Dict[str, Any], List[SiteItem]]], total: int, now_dt: datetime) -> str:
    template = template_path.read_text(encoding="utf-8")
    blocks: List[str] = []
    for cfg, items in sections:
        if not items:
            continue
        item_html = "\n".join(
            f"<li><a href='{item.url}'>{item.title}</a>"
            f" <span class='meta'>[{item.published_label}]</span>"
            f" <span class='meta'>({cfg['SiteName']})</span></li>"
            for item in items
        )
        blocks.append(f"<section><h2>{cfg['SiteName']} ({len(items)})</h2><ul>{item_html}</ul></section>")
    html_sections = "\n".join(blocks) if blocks else "<p>対象期間の更新はありませんでした。</p>"
    return (
        template.replace("{{generated_at}}", now_dt.strftime("%Y-%m-%d %H:%M %Z"))
        .replace("{{total_count}}", str(total))
        .replace("{{sections}}", html_sections)
    )


def send_mail(subject: str, html_body: str) -> bool:
    to_list = parse_recipients(DIRECT_SITE_MAIL_TO)
    cc_list = parse_recipients(DIRECT_SITE_MAIL_CC)
    bcc_list = parse_recipients(DIRECT_SITE_MAIL_BCC)
    recipients = to_list + cc_list + bcc_list
    if not recipients:
        logging.info("mail skipped reason=no_recipients")
        return False
    if not MAIL_FROM or not MAIL_PASSWORD:
        logging.warning("mail skipped reason=missing_smtp_credentials")
        return False

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("daily-news-bot", MAIL_FROM))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(MAIL_FROM, MAIL_PASSWORD)
        server.sendmail(MAIL_FROM, recipients, msg.as_string())
    logging.info("mail delivered to=%s", len(recipients))
    return True


def main() -> None:
    _, sites = load_sites()
    now_dt = datetime.now(ZoneInfo("Asia/Tokyo"))

    active_sites = [s for s in sites if s.get("Enabled")]
    site_results: Dict[str, List[SiteItem]] = {}
    for cfg in sorted(active_sites, key=lambda r: r["DisplayOrder"]):
        site_results[cfg["SiteName"]] = collect_site_items(cfg, now_dt)

    sections, _ = dedupe_and_limit(active_sites, site_results)
    total = sum(len(items) for _, items in sections)

    subject_prefix = DIRECT_SITE_MAIL_SUBJECT_PREFIX or next(
        (cfg["SubjectPrefix"] for cfg, items in sections if cfg.get("SubjectPrefix")),
        DEFAULT_SUBJECT_PREFIX,
    )
    subject = f"{subject_prefix} {now_dt.strftime('%Y-%m-%d')} ({total}件)"

    html_body = render_email(DEFAULT_TEMPLATE_PATH, sections, total, now_dt)
    send_mail(subject, html_body)


if __name__ == "__main__":
    main()
