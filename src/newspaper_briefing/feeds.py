"""Fetch publisher feeds and retain only the metadata needed for a briefing."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from time import sleep
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

import feedparser
import requests

from .policy import Grants, approved, read_policy


JST = timezone(timedelta(hours=9), "JST")
GOOGLE_HOST = "news.google.com"
LOCALE = {"hl": "ja", "gl": "JP", "ceid": "JP:ja"}
SCORE_KEYS = ("impact", "breadth", "recency", "editorial")
WIRE_GROUPS = {
    "共同通信": "kyodo", "共同": "kyodo", "時事通信": "jiji", "時事": "jiji",
    "ロイター": "reuters", "AP通信": "ap", "AFP": "afp",
}
WIRE_CREDIT = re.compile(r"[（(【［\[]\s*(" + "|".join(WIRE_GROUPS) + r")\s*[）)】］\]]")
SECONDARY_REPORT = re.compile(r"(?:海外|現地|米|英|独|仏|ドイツ|韓国|ロシア)(?:紙|メディア)?(?:の)?報道[）)」】。\s]*$")


def text_key(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def clean_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r"[\s<>\\\x00-\x1f\x7f]", value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password:
            if parts.port in (None, 80, 443):
                return value
    except ValueError:
        pass
    return ""


def host(url: str) -> str:
    return (urlsplit(url).hostname or "") if url else ""


def url_key(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    tracking = {"fbclid", "gclid"}
    if parts.hostname == GOOGLE_HOST:
        tracking.update({"oc", "ceid", "hl", "gl"})
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in tracking)
    return parts.hostname.removeprefix("www.") + (parts.path.rstrip("/") or "/") + ("?" + urlencode(query) if query else "")


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("日時には +09:00 などのタイムゾーンが必要です")
    return parsed.astimezone(JST)


def feed_date(value: str | None) -> datetime | None:
    if value:
        try:
            return timestamp(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                return parsed.astimezone(JST) if parsed.tzinfo else None
            except (ValueError, TypeError, OverflowError):
                pass
    return None


class FeedText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value: str, limit: int) -> str:
    parser = FeedText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())[:limit]


@dataclass(frozen=True)
class Publisher:
    id: str
    name: str
    region: str
    domain: str
    aliases: tuple[str, ...]
    hosts: tuple[str, ...]
    reporting_group: str
    search_query: str
    exclude: str = ""
    confirm_search_origin: bool = False
    allow_ai: bool = False
    allow_distribution: bool = False
    supplemental_terms: tuple[str, ...] = ()

    def matches(self, name: str, source_host: str) -> bool:
        return source_host in self.hosts and any(text_key(name) == text_key(alias) for alias in (self.name, *self.aliases))


@dataclass(frozen=True)
class Feed:
    id: str
    url: str
    kind: str
    publisher: str = ""
    title: str = ""
    confirm_origin: bool = False
    personal_only: bool = False
    description_allowed: bool = False

    @property
    def primary(self) -> bool:
        return self.kind in {"official", "editorial", "search"}


def google_search(query: str) -> str:
    return f"https://{GOOGLE_HOST}/rss/search?" + urlencode({"q": query, **LOCALE})


@dataclass(frozen=True)
class Settings:
    publishers: dict[str, Publisher]
    feeds: dict[str, Feed]
    weights: dict[str, int]
    timeout: float
    retries: int
    workers: int
    max_supplemental_feeds: int
    search_cap_hint: int
    grants: Grants = field(default_factory=dict)

    def description_permitted(self, feed: Feed, *uses: str) -> bool:
        return (feed.kind in {"official", "editorial"}
                and approved(self.grants, "rss_description", feed.id, feed.url, "acquire", "save", *uses))


def load_settings(path: Path, policy_path: Path | None = None) -> Settings:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    # Previously saved configurations can still be replayed without their article caps.
    publishers = {p["id"]: Publisher(**{key: value for key, value in p.items() if key != "max_articles"})
                  for p in raw["publishers"]}
    if len(publishers) != len(raw["publishers"]) or not publishers:
        raise ValueError("媒体IDが空、または重複しています")
    feeds = {f["id"]: Feed(**f) for f in raw["feeds"]}
    if len(feeds) != len(raw["feeds"]):
        raise ValueError("フィードIDが重複しています")
    for p in publishers.values():
        if p.search_query != f"site:{p.domain} when:1d" or not p.hosts or not p.reporting_group:
            raise ValueError(f"検索クエリ、ドメインまたは取材元グループが不正です ({p.id})")
        if len(p.supplemental_terms) > 3:
            raise ValueError(f"媒体の補完検索数が不正です ({p.id})")
        re.compile(p.exclude)
        for index, query in enumerate((p.search_query, *(f"{p.search_query} {term}" for term in p.supplemental_terms))):
            feed_id = f"{p.id}-search" + (f"-{index}" if index else "")
            if feed_id in feeds:
                raise ValueError(f"フィードIDが重複しています ({feed_id})")
            feeds[feed_id] = Feed(feed_id, google_search(query), "search", p.id, query)
    for feed in feeds.values():
        if not clean_url(feed.url) or not feed.url.startswith("https://"):
            raise ValueError(f"HTTPS フィードURLが必要です ({feed.id})")
        if feed.kind not in {"official", "editorial", "search", "discovery", "popularity"}:
            raise ValueError(f"フィード種別が不正です ({feed.id})")
        if feed.kind != "discovery" and feed.publisher not in publishers:
            raise ValueError(f"フィードの媒体IDが不正です ({feed.id})")
    weights = raw["weights"]
    if set(weights) != set(SCORE_KEYS) or any(type(n) is not int or n < 0 for n in weights.values()) or sum(weights.values()) != 100:
        raise ValueError("配点は非負の整数で合計100点にしてください")
    collection = {key: value for key, value in raw["collection"].items() if key != "max_articles"}
    settings = Settings(publishers, feeds, weights, grants=read_policy(policy_path), **collection)
    if not (0 < settings.timeout <= 60 and 0 <= settings.retries <= 3 and 1 <= settings.workers <= 12
            and 0 <= settings.max_supplemental_feeds <= 12
            and settings.search_cap_hint > 0):
        raise ValueError("収集設定が範囲外です")
    return settings


@dataclass(frozen=True)
class Observation:
    feed: str
    publisher: str
    source_name: str
    source_host: str
    title: str
    original_url: str
    publisher_url: str
    guid: str
    fetched_at: datetime
    published_at: datetime | None
    published_basis: str
    updated_at: datetime | None = None
    reporting_group: str = ""
    origin_basis: str = ""
    description: str = ""
    category: str = ""

    @property
    def link(self) -> str:
        return self.publisher_url or self.original_url


@dataclass(frozen=True)
class FeedResult:
    feed: str
    fetched_at: datetime
    received: int = 0
    excluded: int = 0
    failure: str = ""
    http_status: int | None = None
    possible_cap: bool = False


@dataclass(frozen=True)
class Capture:
    captured_at: datetime
    feeds: list[FeedResult]
    observations: list[Observation]

    def to_json(self) -> dict:
        return {"version": 1, **asdict(self)}


def publisher_link(entry, original: str, publisher: Publisher) -> str:
    supplied = [original, *(link.get("href", "") for link in entry.get("links", []) if link.get("rel") == "alternate")]
    if host(original) == GOOGLE_HOST:
        supplied.extend(value for key, value in parse_qsl(urlsplit(original).query) if key == "url")
    return next((url for value in supplied if (url := clean_url(value)) and host(url) in publisher.hosts
                 and urlsplit(url).path not in {"", "/"}), "")


def reporting_origin(entry, publisher: Publisher, title: str, feed: Feed) -> tuple[str, str]:
    authors = [a.get("name", "") for a in entry.get("authors", [])]
    known = {text_key(name): group for name, group in WIRE_GROUPS.items()}
    known.update({text_key(name): publisher.reporting_group for name in (publisher.name, *publisher.aliases)})
    credits = {known[text_key(a)] for a in authors if text_key(a) in known}
    credits.update(WIRE_GROUPS[match] for match in WIRE_CREDIT.findall(unicodedata.normalize("NFKC", title)))
    if len(credits) == 1:
        return next(iter(credits)), "配信元表記"
    if credits:
        return "", "配信元表記に不一致"
    if SECONDARY_REPORT.search(title):
        return "", "別媒体の報道。取材元名は未確認"
    if feed.confirm_origin or (feed.kind == "search" and publisher.confirm_search_origin):
        return publisher.reporting_group, "媒体の編集・通信社フィード"
    return "", ""


def normalize_entry(entry, feed: Feed, settings: Settings, fetched_at: datetime, version: str) -> Observation | None:
    original = clean_url(entry.get("link", ""))
    if not original:
        return None
    source = entry.get("source", {})
    source_name = str(source.get("title", "")).strip()[:120]
    source_host = host(clean_url(source.get("href", source.get("url", ""))))
    publisher = settings.publishers.get(feed.publisher)
    if feed.kind in {"search", "discovery"}:
        if feed.kind == "discovery":
            publisher = next((p for p in settings.publishers.values() if p.matches(source_name, source_host)), None)
        if not publisher or not publisher.matches(source_name, source_host):
            return None
    elif publisher and host(original) in publisher.hosts:
        source_name, source_host = publisher.name, host(original)
    else:
        return None

    canonical = publisher_link(entry, original, publisher)
    if not canonical and host(original) != GOOGLE_HOST:
        return None
    title = " ".join(unescape(entry.get("title", "")).split())
    if feed.kind in {"search", "discovery"}:
        labels = {source_name, publisher.name, *publisher.aliases}
        suffix = "|".join(re.escape(label) for label in sorted(labels, key=lambda s: (-len(s), s)))
        title = re.sub(r"\s+[-–—]\s+(?:" + suffix + r")$", "", title)
        title = re.sub(r"[：:]\s*" + re.escape(source_name) + r"$", "", title)
    title = title.strip()[:500]
    if not text_key(title) or (publisher.exclude and re.search(publisher.exclude, f"{title}\n{canonical}")):
        return None
    group, origin_basis = ("", "") if not feed.primary else reporting_origin(entry, publisher, title, feed)
    # Feedparser aliases missing "updated" to "published" on indexed access.
    published = feed_date(dict.get(entry, "published"))
    updated = feed_date(dict.get(entry, "updated"))
    basis = ("atom.published" if version.startswith("atom") else "rss.pubDate") if published else ""
    if feed.kind == "popularity" and not published and updated:
        published, basis = updated, "rdf.dc:date"
    tags = entry.get("tags", [])
    return Observation(
        feed.id, publisher.id, source_name, source_host, title, original, canonical,
        str(entry.get("id", ""))[:2048], fetched_at, published, basis, updated, group, origin_basis,
        plain(str(entry.get("summary", "")), 320) if settings.description_permitted(feed) else "",
        str(tags[0].get("term", ""))[:60] if tags else "",
    )


def parse_feed(feed: Feed, raw: bytes, settings: Settings, fetched_at: datetime, status: int = 200):
    parsed = feedparser.parse(raw)
    entries = parsed.entries
    if not parsed.version or (parsed.bozo and type(parsed.bozo_exception).__name__ != "CharacterEncodingOverride"):
        return FeedResult(feed.id, fetched_at, failure="無効なRSS/Atom", http_status=status), []
    records = []
    for entry in entries:
        record = normalize_entry(entry, feed, settings, fetched_at, parsed.version)
        if record:
            records.append(record)
    return FeedResult(
        feed.id, fetched_at, len(entries), len(entries) - len(records), http_status=status,
        possible_cap=feed.kind == "search" and len(entries) >= settings.search_cap_hint,
    ), records


def fetch_feed(feed: Feed, settings: Settings):
    for attempt in range(settings.retries + 1):
        status, retry = None, False
        try:
            with requests.get(feed.url, timeout=settings.timeout, stream=True, allow_redirects=False,
                              headers={"User-Agent": "NewspaperBriefing/1.0 RSS reader"}) as response:
                status = response.status_code
                if status == 200:
                    body = bytearray()
                    for chunk in response.iter_content(32768):
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            break
                    if len(body) <= 2_000_000:
                        return parse_feed(feed, bytes(body), settings, datetime.now(JST), status)
                    failure = "フィードの応答が2 MBを超過"
                else:
                    failure, retry = f"HTTP {status}", status in {408, 429} or 500 <= status < 600
        except requests.RequestException as exc:
            failure = type(exc).__name__
            retry = not isinstance(exc, requests.exceptions.SSLError)
        if not retry or attempt == settings.retries:
            return FeedResult(feed.id, datetime.now(JST), failure=failure, http_status=status), []
        sleep(0.5 * (2 ** attempt))


def collect(settings: Settings, *, verified_only: bool = True) -> Capture:
    supplemental_ids = {f"{p.id}-search-{i}" for p in settings.publishers.values()
                        for i in range(1, len(p.supplemental_terms) + 1)}

    def obtain(feed: Feed):
        if verified_only and not approved(settings.grants, "headline", feed.id, feed.url, "acquire", "save"):
            return FeedResult(feed.id, datetime.now(JST), failure="取得・保存の利用条件を確認できていません"), []
        return fetch_feed(feed, settings)

    with ThreadPoolExecutor(max_workers=settings.workers) as pool:
        outcomes = list(pool.map(obtain,
                                 (f for f in settings.feeds.values() if f.id not in supplemental_ids)))
        capped = {result.feed for result, _ in outcomes if result.possible_cap}
        extras = [f for f in settings.feeds.values() if f.id in supplemental_ids and f"{f.publisher}-search" in capped]
        outcomes.extend(pool.map(obtain, extras[:settings.max_supplemental_feeds]))
    return Capture(datetime.now(JST), [result for result, _ in outcomes],
                   [record for _, rows in outcomes for record in rows])


def read_capture(path: Path, settings: Settings) -> Capture:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    captured = timestamp(data["captured_at"])
    if data.get("format") == "rss-fixture":
        feeds, observations = [], []
        for fixture in data["feeds"]:
            feed = settings.feeds[fixture["feed"]]
            if fixture.get("failure"):
                result, rows = FeedResult(feed.id, captured, failure=fixture["failure"]), []
            else:
                result, rows = parse_feed(feed, fixture["rss"].encode("utf-8"), settings, captured)
            feeds.append(result)
            observations.extend(rows)
        return Capture(captured, feeds, observations)
    if data.get("version") != 1:
        raise ValueError("オフライン入力のバージョンが不明です")
    feeds = [FeedResult(**(row | {"fetched_at": timestamp(row["fetched_at"])})) for row in data["feeds"]]
    observations = []
    for row in data["observations"]:
        feed, publisher = settings.feeds[row["feed"]], settings.publishers[row["publisher"]]
        link, canonical = clean_url(row["original_url"]), clean_url(row["publisher_url"])
        if (not link or host(link) not in (GOOGLE_HOST, *publisher.hosts)
                or (canonical and host(canonical) not in publisher.hosts)
                or (feed.publisher and feed.publisher != publisher.id)):
            raise ValueError("オフライン入力の記事URLが不正です")
        fields = {key: timestamp(row[key]) if row.get(key) else None
                  for key in ("fetched_at", "published_at", "updated_at")}
        keep_description = settings.description_permitted(feed)
        observations.append(Observation(**(row | fields | {
            "publisher_url": canonical, "description": row.get("description", "") if keep_description else "",
        })))
    return Capture(captured, feeds, observations)


def window_state(record: Observation, start: datetime, end: datetime) -> str:
    if record.published_at is None:
        return "undated"
    if record.published_at > end or (record.updated_at and record.updated_at > end):
        return "future"
    return "in_window" if record.published_at > start else "outside_window"


def diagnostics(capture: Capture, settings: Settings, counts: Counter[tuple[str, str, str]]) -> dict:
    per_feed = {}
    for result in capture.feeds:
        feed = settings.feeds[result.feed]
        per_feed[feed.id] = {**asdict(result), "url": feed.url, "kind": feed.kind, "name": feed.title,
                             **{key: counts["feed", feed.id, key]
                                for key in ("in_window", "undated", "future", "outside_window")}}
    per_publisher = []
    for publisher in settings.publishers.values():
        results = [per_feed[f.id] for f in settings.feeds.values() if f.publisher == publisher.id and f.primary and f.id in per_feed]
        usable = sum(not result["failure"] for result in results)
        discovery = counts["publisher", publisher.id, "discovery_received"]
        per_publisher.append({
            "id": publisher.id, "name": publisher.name, "region": publisher.region, "domain": publisher.domain,
            "state": ("missing" if not usable else "partial" if usable < len(results) else "ok"),
            "received": sum(result["received"] for result in results) + discovery,
            "discovery_received": discovery, "excluded": sum(result["excluded"] for result in results),
            **{field: counts["publisher", publisher.id, field] for field in
               ("in_window", "undated", "future", "outside_window", "articles", "discovery_only")},
            "possible_cap": any(result["possible_cap"] for result in results),
            "sources": results,
        })
    return {"publishers": per_publisher, "auxiliary": [row for row in per_feed.values()
            if row["kind"] in {"discovery", "popularity"}], "captured_at": capture.captured_at}
