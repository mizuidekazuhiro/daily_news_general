from datetime import datetime
from zoneinfo import ZoneInfo
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from direct_site_updates import (
    dedupe_and_limit,
    extract_candidates_from_list_page,
    is_in_window,
    normalize_site_row,
    parse_date_text,
    render_email,
    SiteItem,
    load_sites_from_notion,
    parse_list_page_urls,
)


def test_extract_candidates_for_sample_patterns():
    cfg = normalize_site_row(
        {
            "SiteName": "sample",
            "ArticleUrlPattern": r"https://www\.japanmetaldaily\.com/articles/-/\d+",
            "ListDatePattern": r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}",
        }
    )
    html = """
    <div>
      <a href="https://www.japanmetaldaily.com/articles/-/12345">A title</a>
      <span>2026/04/05 08:30</span>
    </div>
    """
    rows = extract_candidates_from_list_page(html, "https://example.com", cfg)
    assert len(rows) == 1
    assert rows[0]["title"] == "A title"


def test_extract_candidates_for_japanmetal_and_kallanish():
    cfg = normalize_site_row(
        {
            "SiteName": "sample2",
            "ArticleUrlPattern": r"https://www\.japanmetal\.com/news-t\d+\.html|https://www\.kallanish\.com/en/news/[^\"\s]+",
            "ListDatePattern": r"\d{2}年\d{2}月\d{2}日|\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}",
        }
    )
    html = """
      <a href="https://www.japanmetal.com/news-t20260405001.html">JapanMetal</a>
      <div>26年04月05日</div>
      <a href="https://www.kallanish.com/en/news/steel/asia/something">Kallanish Asia</a>
      <div>5 Apr 2026</div>
      <a href="https://www.kallanish.com/en/news/steel/regions/middle-east/article2">Kallanish Middle East</a>
      <div>4 Apr 2026</div>
      <a href="https://www.kallanish.com/en/news/steel/regions/north-america/article3">Kallanish NA</a>
      <div>4 Apr 2026</div>
    """
    rows = extract_candidates_from_list_page(html, "https://example.com", cfg)
    assert len(rows) == 4


def test_date_parse_and_window_modes():
    tz = ZoneInfo("Asia/Tokyo")
    cfg_rolling = normalize_site_row(
        {
            "SiteName": "rolling",
            "TargetDateMode": "rolling_24h",
            "LookbackHours": 24,
            "DateTimezone": "Asia/Tokyo",
            "DateGranularity": "datetime",
        }
    )
    now = datetime(2026, 4, 5, 12, 0, tzinfo=tz)
    dt = parse_date_text("2026/04/05 08:30", tz, "datetime")
    assert dt is not None
    assert is_in_window(dt, cfg_rolling, now)

    cfg_calendar = normalize_site_row(
        {
            "SiteName": "calendar",
            "TargetDateMode": "calendar_day",
            "DateTimezone": "Asia/Tokyo",
            "DateGranularity": "date",
        }
    )
    dt2 = parse_date_text("2026年4月4日", tz, "date")
    assert dt2 is not None
    assert is_in_window(dt2, cfg_calendar, now)


def test_date_parse_english_month_formats():
    tz = ZoneInfo("Asia/Tokyo")
    dt1 = parse_date_text("May 1, 2026", tz, "date")
    dt2 = parse_date_text("1 May 2026", tz, "date")
    assert dt1 is not None
    assert dt2 is not None
    assert dt1.date().isoformat() == "2026-05-01"
    assert dt2.date().isoformat() == "2026-05-01"


def test_dedupe_and_email_render(tmp_path):
    cfg = normalize_site_row({"SiteName": "A", "DisplayOrder": 1, "MaxItemsTotal": 10, "DeliveryEnabled": True})
    item1 = SiteItem("A", "title1", "https://example.com/a", datetime(2026, 4, 5, tzinfo=ZoneInfo("Asia/Tokyo")), "2026-04-05 00:00", "list_regex")
    item2 = SiteItem("A", "title1", "https://example.com/a?utm=1", datetime(2026, 4, 5, tzinfo=ZoneInfo("Asia/Tokyo")), "2026-04-05 00:00", "list_regex")
    sections, removed = dedupe_and_limit([cfg], {"A": [item1, item2]})
    assert removed == 1
    template = tmp_path / "mail.html"
    template.write_text("{{generated_at}} {{total_count}} {{sections}}", encoding="utf-8")
    html = render_email(template, sections, 1, datetime(2026, 4, 5, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert "title1" in html


class _DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_load_sites_from_notion_pagination(monkeypatch):
    monkeypatch.setattr("direct_site_updates.NOTION_DIRECT_SITES_ENABLED", True)
    monkeypatch.setattr("direct_site_updates.NOTION_TOKEN", "token")
    monkeypatch.setattr("direct_site_updates.NOTION_DIRECT_SITES_DB_ID", "db")

    p1 = {
        "results": [
            {"properties": {"SiteName": {"type": "title", "title": [{"plain_text": "A"}]}, "ListPageUrls": {"type": "rich_text", "rich_text": [{"plain_text": "https://example.com"}]}}}
        ],
        "has_more": True,
        "next_cursor": "c1",
    }
    p2 = {
        "results": [
            {"properties": {"SiteName": {"type": "title", "title": [{"plain_text": "B"}]}, "ListPageUrls": {"type": "rich_text", "rich_text": [{"plain_text": "https://example.org"}]}}}
        ],
        "has_more": False,
        "next_cursor": None,
    }
    responses = iter([_DummyResponse(p1), _DummyResponse(p2)])
    monkeypatch.setattr("direct_site_updates.urllib.request.urlopen", lambda *args, **kwargs: next(responses))

    rows = load_sites_from_notion()
    assert [r["SiteName"] for r in rows] == ["A", "B"]


def test_extract_candidates_article_pattern_matches_href_or_absolute():
    cfg = normalize_site_row(
        {
            "SiteName": "pattern-dual",
            "ArticleUrlPattern": r"^/news/",
        }
    )
    html = """
      <a href="/news/a1">Article 1</a>
      <a href="https://example.com/other">Other</a>
    """
    rows = extract_candidates_from_list_page(html, "https://example.com/list", cfg)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/news/a1"


def test_parse_list_page_urls_newline_separated():
    raw = "https://a.example.com/list\nhttps://b.example.com/list"
    urls = parse_list_page_urls(raw)
    assert urls == ["https://a.example.com/list", "https://b.example.com/list"]


def test_parse_list_page_urls_comma_separated():
    raw = "https://a.example.com/list, https://b.example.com/list"
    urls = parse_list_page_urls(raw)
    assert urls == ["https://a.example.com/list", "https://b.example.com/list"]


def test_parse_list_page_urls_markdown_links():
    raw = "[steel](https://www.kallanish.com/en/news/steel/)"
    urls = parse_list_page_urls(raw)
    assert urls == ["https://www.kallanish.com/en/news/steel"]


def test_parse_list_page_urls_joined_https_urls():
    raw = "https://www.kallanish.com/https://www.kallanish.com/en/news/steel/"
    urls = parse_list_page_urls(raw)
    assert urls == ["https://www.kallanish.com/", "https://www.kallanish.com/en/news/steel"]


def test_parse_list_page_urls_dedupes_and_filters_invalid():
    raw = "  ;;;\nhttps://a.example.com/list#top\nhttps://a.example.com/list\nnot_a_url"
    urls = parse_list_page_urls(raw)
    assert urls == ["https://a.example.com/list"]
