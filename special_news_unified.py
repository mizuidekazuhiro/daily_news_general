from __future__ import annotations

import email
import imaplib
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

import direct_site_updates
import news_digest
import special_news_sent_ledger

JST = ZoneInfo("Asia/Tokyo")
SENT_HISTORY_DAYS = int(os.getenv("SPECIAL_NEWS_SENT_HISTORY_DAYS", "7"))
SENT_HISTORY_LIMIT = int(os.getenv("SPECIAL_NEWS_SENT_HISTORY_LIMIT", "250"))
LEDGER_HISTORY_DAYS = int(os.getenv("SPECIAL_NEWS_LEDGER_HISTORY_DAYS", "30"))
GMAIL_SAFETY_NET = os.getenv("SPECIAL_NEWS_GMAIL_SAFETY_NET", "true").strip().lower() in {"1", "true", "yes", "on"}
TARGET_MEDIA = ("日刊産業新聞", "鉄鋼新聞")
SOURCE_PRIORITY = {
    "鉄鋼新聞": ("direct", "alert"),
    "日刊産業新聞": ("direct", "alert"),
}
LEGACY_SUBJECT_PREFIXES = ("【専門紙記事一覧】", "鉄鋼サイト更新一覧")


def canonical_media_name(name: str) -> str:
    value = (name or "").strip()
    if "産業新聞" in value:
        return "日刊産業新聞"
    if "鉄鋼新聞" in value or "Japan Metal Daily" in value:
        return "鉄鋼新聞"
    return value


def unwrap_article_url(url: str) -> str:
    current = unescape((url or "").strip())
    for _ in range(3):
        if not current:
            return ""
        try:
            parsed = urllib.parse.urlsplit(current)
        except ValueError:
            return current
        host = parsed.netloc.lower().split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if host in {"google.com", "google.co.jp"} and parsed.path == "/url":
            qs = urllib.parse.parse_qs(parsed.query)
            target = (qs.get("url") or qs.get("q") or [""])[0]
            if target:
                current = urllib.parse.unquote(target)
                continue
        break
    return current


def article_identity(url: str, title: str = "") -> str:
    resolved = unwrap_article_url(url)
    try:
        parsed = urllib.parse.urlsplit(resolved)
    except ValueError:
        parsed = urllib.parse.SplitResult("", "", resolved, "", "")
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"

    match = re.search(r"/articles/-/(\d+)", path)
    if host.endswith("japanmetaldaily.com") and match:
        return f"jmd:{match.group(1)}"

    match = re.search(r"/news-[th](\d+)\.html$", path)
    if host.endswith("japanmetal.com") and match:
        return f"japanmetal:{match.group(1)}"

    if host:
        return f"url:{host}{path}"

    normalized_title = news_digest.normalize_title(title or "")
    return f"title:{normalized_title}" if normalized_title else ""


def _decode_subject(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


def _find_sent_mailbox(conn: imaplib.IMAP4_SSL) -> str:
    status, rows = conn.list()
    if status != "OK" or not rows:
        raise RuntimeError("sent mailbox listing failed")
    for row in rows:
        text = row.decode("utf-8", errors="replace") if isinstance(row, bytes) else str(row)
        if "\\Sent" not in text:
            continue
        match = re.match(r'^\((?P<flags>.*?)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$', text)
        if match:
            return match.group("name").strip()
    raise RuntimeError("sent mailbox not found")


def _message_text(msg: email.message.Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]
    for part in parts:
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        if str(part.get("Content-Disposition", "")).lower().startswith("attachment"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            if isinstance(raw, str):
                chunks.append(raw)
            continue
        charset = part.get_content_charset() or "utf-8"
        chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)


def _extract_article_keys(text: str) -> set[str]:
    keys: set[str] = set()
    decoded = unescape(text or "")
    for raw_url in re.findall(r"https?://[^\s<>'\"]+", decoded):
        url = raw_url.rstrip(").,;]")
        key = article_identity(url)
        if key and (key.startswith("jmd:") or key.startswith("japanmetal:")):
            keys.add(key)
    return keys


def load_recent_gmail_article_keys(days: int = SENT_HISTORY_DAYS) -> set[str]:
    user = (os.getenv("MAIL_USER") or news_digest.MAIL_FROM or "").strip()
    password = (news_digest.MAIL_PASSWORD or "").strip()
    if not user or not password:
        raise RuntimeError("sent-history credentials missing")

    conn = imaplib.IMAP4_SSL(os.getenv("MAIL_IMAP_HOST", "imap.gmail.com"), int(os.getenv("MAIL_IMAP_PORT", "993")))
    try:
        conn.login(user, password)
        mailbox = _find_sent_mailbox(conn)
        status, _ = conn.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError("sent mailbox select failed")

        since = (datetime.now(JST) - timedelta(days=days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", since)
        if status != "OK" or not data:
            raise RuntimeError("sent-history search failed")

        message_ids = data[0].split()[-SENT_HISTORY_LIMIT:]
        prefixes = tuple(
            p for p in (
                (news_digest.SPECIAL_NEWS_MAIL_SUBJECT_PREFIX or "").strip(),
                *LEGACY_SUBJECT_PREFIXES,
            )
            if p
        )
        keys: set[str] = set()
        matched_messages = 0

        for message_id in reversed(message_ids):
            status, header_parts = conn.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            if status != "OK" or not header_parts:
                continue
            header_bytes = b"".join(
                part[1]
                for part in header_parts
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes)
            )
            header_msg = email.message_from_bytes(header_bytes)
            subject = _decode_subject(header_msg.get("Subject"))
            if not any(subject.startswith(prefix) for prefix in prefixes):
                continue

            status, full_parts = conn.fetch(message_id, "(RFC822)")
            if status != "OK" or not full_parts:
                raise RuntimeError(f"sent-history body fetch failed for message={message_id!r}")
            raw_message = b"".join(
                part[1]
                for part in full_parts
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes)
            )
            if not raw_message:
                continue
            matched_messages += 1
            keys.update(_extract_article_keys(_message_text(email.message_from_bytes(raw_message))))

        news_digest.logging.info(
            "Unified special-news sent history days=%s matched_messages=%s article_keys=%s",
            days,
            matched_messages,
            len(keys),
        )
        return keys
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def load_effective_sent_keys() -> set[str]:
    ledger_keys = special_news_sent_ledger.load_sent_keys(LEDGER_HISTORY_DAYS)
    news_digest.logging.info(
        "Unified special-news ledger loaded keys=%s history_days=%s",
        len(ledger_keys),
        LEDGER_HISTORY_DAYS,
    )
    gmail_keys: set[str] = set()
    if GMAIL_SAFETY_NET:
        try:
            gmail_keys = load_recent_gmail_article_keys()
        except Exception as exc:
            news_digest.logging.warning(
                "Unified special-news Gmail safety net unavailable; continuing with Notion ledger only: %s",
                exc,
            )
    combined = ledger_keys | gmail_keys
    news_digest.logging.info(
        "Unified special-news sent-state ledger=%s gmail_safety=%s combined=%s",
        len(ledger_keys),
        len(gmail_keys),
        len(combined),
    )
    return combined


def _alert_results(now_jst: datetime) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    result = news_digest.collect_special_news_articles(now_jst, apply_limits=False)
    if not result.get("delivery_enabled", True):
        return {}, {}
    out: dict[str, list[dict[str, Any]]] = {}
    limits: dict[str, int] = {}
    for media in result.get("media_results", []):
        name = canonical_media_name(str(media.get("media_name") or ""))
        if name not in TARGET_MEDIA:
            continue
        if not media.get("delivery_enabled", True):
            news_digest.logging.info(
                "Unified special-news alert source skipped media=%s reason=delivery_disabled",
                name,
            )
            continue
        out[name] = [dict(item) for item in (media.get("items") or []) if isinstance(item, dict)]
        limits[name] = int(media.get("max_items") or news_digest.SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)
    return out, limits

def _direct_results(now_jst: datetime) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    _, sites = direct_site_updates.load_sites()
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in TARGET_MEDIA}
    limits: dict[str, int] = {}
    for cfg in sorted(sites, key=lambda row: row.get("DisplayOrder", 9999)):
        name = canonical_media_name(str(cfg.get("SiteName") or ""))
        if name not in TARGET_MEDIA or not cfg.get("Enabled"):
            continue
        items = direct_site_updates.collect_site_items(cfg, now_jst, apply_limit=False)
        if not cfg.get("DeliveryEnabled", True):
            news_digest.logging.info(
                "Unified special-news direct source monitored media=%s items=%s delivery_enabled=false",
                name,
                len(items),
            )
            continue
        limits[name] = int(cfg.get("MaxItemsPerSite") or 20)
        out[name].extend(
            {
                "title": item.title,
                "link": item.url,
                "published": item.published_label,
            }
            for item in items
        )
    return out, limits

def _published_sort_key(item: dict[str, Any]) -> datetime:
    raw = str(item.get("published") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.min


def _source_keys(items: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for item in items:
        key = article_identity(str(item.get("link") or ""), str(item.get("title") or ""))
        if key:
            keys.add(key)
    return keys


def merge_sources(
    alert_results: dict[str, list[dict[str, Any]]],
    direct_results: dict[str, list[dict[str, Any]]],
    sent_keys: set[str],
    max_items_total: int = 50,
    media_limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    sources = {"alert": alert_results, "direct": direct_results}
    media_limits = media_limits or {}
    media_results: list[dict[str, Any]] = []
    total = 0

    for media_name in TARGET_MEDIA:
        direct_items = sources["direct"].get(media_name, [])
        alert_items = sources["alert"].get(media_name, [])
        direct_keys = _source_keys(direct_items)
        alert_keys = _source_keys(alert_items)
        overlap_keys = direct_keys & alert_keys

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        sent_removed = 0
        for source_name in SOURCE_PRIORITY[media_name]:
            for raw in sources[source_name].get(media_name, []):
                title = str(raw.get("title") or "").strip()
                raw_link = str(raw.get("link") or "").strip()
                key = article_identity(raw_link, title)
                if not key or key in seen:
                    continue
                seen.add(key)
                if key in sent_keys:
                    sent_removed += 1
                    news_digest.logging.info(
                        "Unified special-news skipped already-sent media=%s source=%s key=%s title=%s",
                        media_name,
                        source_name,
                        key,
                        title,
                    )
                    continue
                merged.append(
                    {
                        "title": title,
                        "link": unwrap_article_url(raw_link),
                        "published": str(raw.get("published") or ""),
                        "source": source_name,
                        "article_key": key,
                    }
                )

        merged.sort(key=_published_sort_key, reverse=True)
        before_limit = len(merged)
        per_media_limit = max(1, int(media_limits.get(media_name, news_digest.SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA)))
        merged = merged[:per_media_limit]
        remain = max(0, max_items_total - total)
        merged = merged[:remain]
        total += len(merged)

        news_digest.logging.info(
            "Unified source diff media=%s direct=%s alert=%s overlap=%s direct_only=%s alert_only=%s "
            "sent_removed=%s merged_before_limit=%s delivered=%s media_limit=%s global_remaining_after=%s",
            media_name,
            len(direct_keys),
            len(alert_keys),
            len(overlap_keys),
            len(direct_keys - alert_keys),
            len(alert_keys - direct_keys),
            sent_removed,
            before_limit,
            len(merged),
            per_media_limit,
            max(0, max_items_total - total),
        )

        media_results.append(
            {
                "media_name": media_name,
                "items": merged,
                "display_order": 1 if media_name == "日刊産業新聞" else 2,
            }
        )
        if total >= max_items_total:
            break

    return media_results

def run() -> None:
    now_jst = datetime.now(JST)
    sent_keys = load_effective_sent_keys()
    alert, alert_limits = _alert_results(now_jst)
    direct, direct_limits = _direct_results(now_jst)
    media_limits = {
        media: alert_limits.get(media) or direct_limits.get(media) or news_digest.SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA
        for media in TARGET_MEDIA
    }
    media_results = merge_sources(
        alert,
        direct,
        sent_keys,
        max_items_total=news_digest.SPECIAL_NEWS_MAX_ITEMS_TOTAL,
        media_limits=media_limits,
    )
    total_items = sum(len(m.get("items") or []) for m in media_results)
    if total_items == 0:
        news_digest.logging.info(
            "Unified special-news delivery skipped reason=no_new_items"
        )
        return

    to_list = news_digest.parse_mail_recipients(news_digest.SPECIAL_NEWS_MAIL_TO)
    cc_list = news_digest.parse_mail_recipients(news_digest.SPECIAL_NEWS_MAIL_CC)
    bcc_list = news_digest.parse_mail_recipients(news_digest.SPECIAL_NEWS_MAIL_BCC)
    if not (to_list or cc_list or bcc_list):
        raise RuntimeError("special-news recipients are empty")
    if not news_digest.MAIL_FROM or not news_digest.MAIL_PASSWORD:
        raise RuntimeError("special-news SMTP credentials are missing")

    html_body = news_digest.render_special_news_html(now_jst, media_results, total_items)
    subject = news_digest.build_special_news_subject(now_jst, media_results)
    text_fallback = (
        f"専門紙記事一覧\n対象日: {now_jst.strftime('%Y-%m-%d')}\n"
        f"新着総件数: {total_items}件"
    )
    delivery_run_id = f"{now_jst.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"

    news_digest.send_mail_generic(
        html_body,
        subject,
        to_list,
        cc_list,
        bcc_list,
        text_fallback=text_fallback,
    )
    recorded = special_news_sent_ledger.record_sent_articles(
        media_results,
        sent_at=datetime.now(JST),
        delivery_run_id=delivery_run_id,
    )
    if recorded != total_items:
        raise RuntimeError(
            f"sent-ledger record count mismatch: expected={total_items} recorded={recorded}"
        )
    news_digest.logging.info(
        "Unified special-news email delivered successfully total_items=%s ledger_recorded=%s run_id=%s",
        total_items,
        recorded,
        delivery_run_id,
    )


if __name__ == "__main__":
    run()
