from datetime import datetime
from zoneinfo import ZoneInfo

import special_news_sent_ledger as ledger


def test_load_sent_keys_reads_article_keys(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "resolve_ledger_db_id", lambda: "db")
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
                            "title": [{"plain_text": "japanmetal:20260911150723"}]
                        }
                    }
                },
            ],
            "has_more": False,
        }

    monkeypatch.setattr(ledger, "_request_json", fake_request)
    keys = ledger.load_sent_keys(days=30)

    assert keys == {"jmd:267141", "japanmetal:20260911150723"}
    assert calls[0][2]["filter"]["property"] == "SentAt"


def test_record_sent_articles_writes_only_after_caller_invokes_it(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "resolve_ledger_db_id", lambda: "db")
    payloads = []

    def fake_request(url, method, payload=None):
        payloads.append(payload)
        return {"id": "page"}

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
    sent_at = datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo("Asia/Tokyo"))

    count = ledger.record_sent_articles(media_results, sent_at, "run-1")

    assert count == 1
    props = payloads[0]["properties"]
    assert props["ArticleKey"]["title"][0]["text"]["content"] == "jmd:267141"
    assert props["Media"]["select"]["name"] == "鉄鋼新聞"
    assert props["Source"]["select"]["name"] == "direct"
    assert props["SentAt"]["date"]["start"].startswith("2026-09-12T07:30")



def test_resolve_ledger_creates_database_when_not_found(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "SENT_LEDGER_DB_ID", "")
    monkeypatch.setattr(ledger, "_RESOLVED_DB_ID", None)
    monkeypatch.setattr(ledger, "_search_ledger_database", lambda: "")
    monkeypatch.setattr(ledger, "_create_ledger_database", lambda: "created-db")

    assert ledger.resolve_ledger_db_id() == "created-db"
    assert ledger.resolve_ledger_db_id() == "created-db"
