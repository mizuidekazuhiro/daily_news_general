from datetime import datetime
from zoneinfo import ZoneInfo

from direct_site_updates import collect_site_items, normalize_site_row


def test_japanmetal_historical_daily_archive_live():
    """Public-site integration check for the structure verified during the P0 repair."""
    cfg = normalize_site_row(
        {
            "SiteName": "日刊産業新聞",
            "Enabled": True,
            "ListPageUrls": "https://www.japanmetal.com/cat/news-t",
            "ArticleUrlPattern": r"/news-t[0-9]+[.]html",
            # Deliberately preserve the old/stale Notion pattern here.
            # Production must still work because the article URL contains YYYYMMDD.
            "ListDatePattern": r"[0-9]{2}年[0-9]{2}月[0-9]{2}日",
            "DateGranularity": "date",
            "TargetDateMode": "calendar_day",
            "LookbackHours": 36,
            "MaxItemsPerSite": 20,
            "MaxPages": 1,
            "FetchMode": "direct",
            "FetchArticleBody": True,
            "DateFallbackMode": "require_date",
            "DateTimezone": "Asia/Tokyo",
        }
    )
    fixed_now = datetime(2026, 9, 12, 7, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    items = collect_site_items(cfg, fixed_now)

    urls = {item.url for item in items}
    titles = {item.title for item in items}

    assert len(items) >= 8
    assert "https://www.japanmetal.com/news-t20260911150723.html" in urls
    assert "https://www.japanmetal.com/news-t20260911150730.html" in urls
    assert any("岸和田製鋼" in title for title in titles)
    assert any("鉄鋼協会" in title for title in titles)
    assert all(item.published_at.date().isoformat() == "2026-09-11" for item in items)
