import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_digest import extract_entries_for_target_date, parse_mail_recipients, build_special_news_subject


class DummyEntry(dict):
    pass


def _entry(title, link, dt):
    e = DummyEntry(title=title, link=link)
    e.published_parsed = dt.timetuple()
    return e


def test_parse_mail_recipients():
    assert parse_mail_recipients("a@example.com, b@example.com") == ["a@example.com", "b@example.com"]


def test_extract_entries_for_target_date_filters_jst_date():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 9, 0, tzinfo=jst)
    same_day_utc = datetime(2026, 3, 12, 1, 0, tzinfo=timezone.utc)
    next_day_utc = datetime(2026, 3, 13, 1, 0, tzinfo=timezone.utc)

    entries = [
        _entry("A", "https://example.com/a", same_day_utc),
        _entry("B", "https://example.com/b", next_day_utc),
    ]

    actual = extract_entries_for_target_date(entries, target)
    assert len(actual) == 1
    assert actual[0]["title"] == "A"


def test_build_special_news_subject():
    jst = timezone(timedelta(hours=9))
    target = datetime(2026, 3, 12, 0, 0, tzinfo=jst)
    subject = build_special_news_subject(target, [{"media_name": "鉄鋼新聞"}, {"media_name": "産業新聞"}])
    assert "2026-03-12更新分" in subject
    assert "鉄鋼新聞・産業新聞" in subject
