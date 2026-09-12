from datetime import datetime
from zoneinfo import ZoneInfo

import special_news_sent_ledger as ledger


def test_load_sent_keys_reads_dedicated_ledger_titles(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "SENT_LEDGER_DB_ID", "ledger-db")
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {
            "results": [
                {
                    "properties": {
                        "ArticleKey": {
                            "title": [{"plain_text": "jmd:267141"}]
                        }
                    }
                },
                {
                    "properties": {
                        "ArticleKey": {
                            "title": [
                                {"plain_text": "japanmetal:20260911150723"}
                            ]
                        }
                    }
                },
            ],
            "has_more": False,
        }

    monkeypatch.setattr(ledger, "_request_json", fake_request)
    keys = ledger.load_sent_keys(days=30)

    assert keys == {"jmd:267141", "japanmetal:20260911150723"}
    assert calls[0][0].endswith("/databases/ledger-db/query")
    assert calls[0][2]["filter"]["property"] == "SentAt"


def test_record_sent_articles_creates_dedicated_ledger_row(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "SENT_LEDGER_DB_ID", "ledger-db")
    monkeypatch.setattr(ledger, "_query_one_by_key", lambda article_key: None)
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {"id": "new-ledger-row"}

    monkeypatch.setattr(ledger, "_request_json", fake_request)
    media_results = [
        {
            "media_name": "日刊産業新聞",
            "items": [
                {
                    "article_key": "japanmetal:20260911150723",
                    "title": "sample industry",
                    "link": "https://www.japanmetal.com/news-t20260911150723.html",
                    "published": "2026-09-11",
                    "source": "alert",
                }
            ],
        }
    ]

    count = ledger.record_sent_articles(
        media_results,
        datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        "run-2",
    )

    assert count == 1
    assert calls[0][0] == "https://api.notion.com/v1/pages"
    assert calls[0][1] == "POST"
    assert calls[0][2]["parent"] == {"database_id": "ledger-db"}
    props = calls[0][2]["properties"]
    assert props["ArticleKey"]["title"][0]["text"]["content"] == (
        "japanmetal:20260911150723"
    )
    assert props["Media"]["select"]["name"] == "日刊産業新聞"
    assert props["Source"]["select"]["name"] == "alert"
    assert "Specialist Mail Sent" not in props
    assert "ArticleId" not in props


def test_record_sent_articles_updates_existing_ledger_row(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "SENT_LEDGER_DB_ID", "ledger-db")
    monkeypatch.setattr(
        ledger,
        "_query_one_by_key",
        lambda article_key: {"id": "existing-ledger-row"},
    )
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {"id": "existing-ledger-row"}

    monkeypatch.setattr(ledger, "_request_json", fake_request)
    media_results = [
        {
            "media_name": "鉄鋼新聞",
            "items": [
                {
                    "article_key": "jmd:267141",
                    "title": "sample",
                    "link": "https://www.japanmetaldaily.com/articles/-/267141",
                    "published": "2026-09-11 05:00",
                    "source": "direct",
                }
            ],
        }
    ]

    count = ledger.record_sent_articles(
        media_results,
        datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        "run-1",
    )

    assert count == 1
    assert calls[0][0].endswith("/v1/pages/existing-ledger-row")
    assert calls[0][1] == "PATCH"
    props = calls[0][2]["properties"]
    assert props["ArticleKey"]["title"][0]["text"]["content"] == "jmd:267141"
    assert props["Media"]["select"]["name"] == "鉄鋼新聞"
    assert props["Source"]["select"]["name"] == "direct"
    assert props["SentAt"]["date"]["start"].startswith("2026-09-12T07:30")


def test_query_one_by_key_uses_article_key_title(monkeypatch):
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {"results": [{"id": "row"}]}

    monkeypatch.setattr(ledger, "SENT_LEDGER_DB_ID", "ledger-db")
    monkeypatch.setattr(ledger, "_request_json", fake_request)

    result = ledger._query_one_by_key("jmd:1")

    assert result == {"id": "row"}
    assert calls[0][0].endswith("/databases/ledger-db/query")
    assert calls[0][2]["filter"] == {
        "property": "ArticleKey",
        "title": {"equals": "jmd:1"},
    }
