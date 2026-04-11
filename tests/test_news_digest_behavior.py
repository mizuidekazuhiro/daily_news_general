import json
from datetime import datetime
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import news_digest
from news_digest import JST


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_main_job_skips_when_mail_to_empty(monkeypatch):
    monkeypatch.setattr(news_digest, "MAIL_TO", "")
    called = {"send": False}

    def fake_send(_html):
        called["send"] = True

    monkeypatch.setattr(news_digest, "send_mail", fake_send)
    news_digest.run_main_news_delivery()
    assert called["send"] is False


def test_special_subject_prefix_priority_env_overrides_notion():
    assert news_digest.resolve_special_subject_prefix("【ENV】", "【NOTION】") == "【ENV】"


def test_special_subject_prefix_priority_notion_over_default():
    assert news_digest.resolve_special_subject_prefix("", "【NOTION】") == "【NOTION】"
    assert news_digest.resolve_special_subject_prefix("", "") == "【専門紙記事一覧】"


def test_fetch_special_news_config_from_notion_supports_pagination(monkeypatch):
    monkeypatch.setattr(news_digest, "NOTION_TOKEN", "token")
    monkeypatch.setattr(news_digest, "NOTION_SPECIAL_NEWS_DB_ID", "db")
    monkeypatch.setattr(news_digest, "parse_env_bool", lambda _name, _default: True)

    page1 = {
        "results": [
            {
                "properties": {
                    "MediaName": {"type": "title", "title": [{"plain_text": "媒体A"}]},
                    "Enabled": {"type": "checkbox", "checkbox": True},
                    "GoogleAlertFeeds": {"type": "rich_text", "rich_text": [{"plain_text": "https://example.com/a"}]},
                }
            }
        ],
        "has_more": True,
        "next_cursor": "cursor-1",
    }
    page2 = {
        "results": [
            {
                "properties": {
                    "MediaName": {"type": "title", "title": [{"plain_text": "媒体B"}]},
                    "Enabled": {"type": "checkbox", "checkbox": True},
                    "GoogleAlertFeeds": {"type": "rich_text", "rich_text": [{"plain_text": "https://example.com/b"}]},
                }
            }
        ],
        "has_more": False,
        "next_cursor": None,
    }
    responses = iter([DummyResponse(page1), DummyResponse(page2)])
    monkeypatch.setattr(news_digest.urllib.request, "urlopen", lambda *args, **kwargs: next(responses))

    rows = news_digest.fetch_special_news_config_from_notion()
    assert rows is not None
    assert [r["media_name"] for r in rows] == ["媒体A", "媒体B"]


def test_title_url_dedupe_key_uses_normalized_host_path_and_title():
    a = news_digest.build_dedupe_key("Steel price jumps", "https://news.google.com/rss/articles/abc?url=https%3A%2F%2Fexample.com%2Fnews%2F1%3Futm_source%3Dx")
    b = news_digest.build_dedupe_key("Steel price jumps", "https://example.com/news/1?utm_source=y")
    c = news_digest.build_dedupe_key("Steel price slips", "https://example.com/news/1?utm_source=y")
    assert a == b
    assert b != c


def test_date_window_modes_rolling_and_calendar(monkeypatch):
    now_jst = datetime(2026, 3, 17, 10, 0, tzinfo=JST)
    entry = news_digest.feedparser.FeedParserDict({"title": "A", "link": "https://example.com/a"})
    entry.published = "Tue, 17 Mar 2026 00:30:00 +0900"

    rolling_rule = news_digest.normalize_special_date_rule("媒体A", {"target_date_mode": "rolling_24h", "date_granularity": "datetime"})
    calendar_rule = news_digest.normalize_special_date_rule("媒体A", {"target_date_mode": "calendar_day", "date_granularity": "date"})

    rolling_items = news_digest.extract_entries_for_special_window([entry], now_jst, "媒体A", "https://example.com/feed", rolling_rule)
    calendar_items = news_digest.extract_entries_for_special_window([entry], now_jst, "媒体A", "https://example.com/feed", calendar_rule)
    assert len(rolling_items) == 1
    assert len(calendar_items) == 1
