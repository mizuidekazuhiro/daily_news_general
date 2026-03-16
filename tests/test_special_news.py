import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import news_digest
from news_digest import (
    build_special_media_row,
    build_special_news_subject,
    extract_entries_for_target_date,
    parse_env_bool,
    parse_mail_recipients,
    parse_feed_urls,
    render_special_news_html,
)


class DummyEntry(dict):
    pass


def _entry(title, link, dt):
    e = DummyEntry(title=title, link=link)
    e.published_parsed = dt.timetuple()
    return e


def test_parse_mail_recipients():
    assert parse_mail_recipients("a@example.com, b@example.com") == ["a@example.com", "b@example.com"]
    assert parse_mail_recipients("a@example.com\n b@example.com ; c@example.com") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_extract_entries_for_target_date_filters_jst_date():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 9, 0, tzinfo=jst)
    same_day_utc = datetime(2026, 3, 12, 1, 0, tzinfo=timezone.utc)
    next_day_utc = datetime(2026, 3, 13, 1, 0, tzinfo=timezone.utc)

    entries = [
        _entry("A", "https://example.com/a", same_day_utc),
        _entry("B", "https://example.com/b", next_day_utc),
    ]

    actual = extract_entries_for_target_date(entries, target, "媒体A", "https://example.com/feed")
    assert len(actual) == 1
    assert actual[0]["title"] == "A"


def test_build_special_news_subject():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 0, 0, tzinfo=jst)
    subject = build_special_news_subject(target, [{"media_name": "鉄鋼新聞"}, {"media_name": "産業新聞"}])
    assert "2026-03-12更新分" in subject
    assert "鉄鋼新聞・産業新聞" in subject


def test_render_special_news_html_handles_css_and_zero_items():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 0, 0, tzinfo=jst)
    html = render_special_news_html(target, [{"media_name": "鉄鋼新聞", "items": []}], 0)
    assert "margin" in html
    assert "対象日に該当記事はありませんでした" in html


def test_render_special_news_html_handles_empty_media_results():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 0, 0, tzinfo=jst)
    html = render_special_news_html(target, [], 0)
    assert "対象媒体" in html
    assert "対象日に該当記事はありませんでした" in html


def test_parse_env_bool_variants(monkeypatch):
    monkeypatch.delenv("NOTION_SPECIAL_NEWS_ENABLED", raising=False)
    assert parse_env_bool("NOTION_SPECIAL_NEWS_ENABLED", False) is False

    monkeypatch.setenv("NOTION_SPECIAL_NEWS_ENABLED", "")
    assert parse_env_bool("NOTION_SPECIAL_NEWS_ENABLED", False) is False

    monkeypatch.setenv("NOTION_SPECIAL_NEWS_ENABLED", "false")
    assert parse_env_bool("NOTION_SPECIAL_NEWS_ENABLED", True) is False

    monkeypatch.setenv("NOTION_SPECIAL_NEWS_ENABLED", "on")
    assert parse_env_bool("NOTION_SPECIAL_NEWS_ENABLED", False) is True


def test_parse_feed_urls_multiline_and_invalid_and_dedup():
    raw = "\nhttps://example.com/a\ninvalid-url\n https://example.com/a \nhttps://example.com/b\n"
    urls = parse_feed_urls(raw, "媒体A")
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_build_special_media_row_validations():
    assert build_special_media_row(
        media_name="",
        enabled=True,
        alert_ids=[],
        alert_feeds_raw="https://example.com/a",
        display_order=None,
        max_items=None,
        subject_prefix=None,
        delivery_enabled=True,
        max_items_total=10,
    ) is None

    assert build_special_media_row(
        media_name="媒体A",
        enabled=True,
        alert_ids=[],
        alert_feeds_raw="",
        display_order=None,
        max_items=None,
        subject_prefix=None,
        delivery_enabled=True,
        max_items_total=10,
    ) is None


def test_run_special_news_delivery_skips_when_recipient_empty(monkeypatch):
    monkeypatch.setattr(news_digest, "SPECIAL_NEWS_MAIL_TO", "")
    monkeypatch.setattr(news_digest, "SPECIAL_NEWS_MAIL_CC", "")
    monkeypatch.setattr(news_digest, "SPECIAL_NEWS_MAIL_BCC", "")
    monkeypatch.setattr(
        news_digest,
        "collect_special_news_articles",
        lambda _target: {
            "delivery_enabled": True,
            "media_results": [{"media_name": "媒体A", "items": []}],
            "total_items": 0,
            "subject_prefix": "【専門紙記事一覧】",
        },
    )
    sent = {"called": False}

    def fake_send(*args, **kwargs):
        sent["called"] = True

    monkeypatch.setattr(news_digest, "send_mail_generic", fake_send)

    news_digest.run_special_news_delivery()
    assert sent["called"] is False
