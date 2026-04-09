from datetime import datetime
from zoneinfo import ZoneInfo

from direct_site_updates import (
    dedupe_and_limit,
    extract_candidates_from_list_page,
    is_in_window,
    normalize_site_row,
    parse_date_text,
    render_email,
    SiteItem,
)


def test_extract_candidates_for_sample_patterns():
    cfg = normalize_site_row(
        {
            "SiteName": "sample",
            "ArticleUrlPattern": r"https://www\\.japanmetaldaily\\.com/articles/-/\\d+",
            "ListDatePattern": r"\\d{4}/\\d{1,2}/\\d{1,2}\\s+\\d{1,2}:\\d{2}",
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
            "ArticleUrlPattern": r"https://www\\.japanmetal\\.com/news-t\\d+\\.html|https://www\\.kallanish\\.com/en/news/[^\"\\s]+",
            "ListDatePattern": r"\\d{2}年\\d{2}月\\d{2}日|\\d{1,2}\\s+[A-Z][a-z]{2}\\s+\\d{4}",
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
