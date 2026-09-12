from datetime import datetime
from zoneinfo import ZoneInfo

import special_news_sent_ledger as ledger


def test_load_sent_keys_reads_article_ids(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "ARTICLES_DB_ID", "db")
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {
            "results": [
                {
                    "properties": {
                        "ArticleId": {
                            "rich_text": [{"plain_text": "jmd:267141"}]
                        }
                    }
                },
                {
                    "properties": {
                        "ArticleId": {
                            "rich_text": [
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
    filters = calls[0][2]["filter"]["and"]
    assert filters[0]["property"] == "Specialist Mail Sent"
    assert filters[1]["property"] == "Specialist Mail Sent At"


def test_record_sent_articles_updates_existing_article(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "ARTICLES_DB_ID", "db")
    monkeypatch.setattr(
        ledger,
        "_find_article",
        lambda article_key, canonical_url: {"id": "existing-page"},
    )
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {"id": "existing-page"}

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
    assert calls[0][0].endswith("/v1/pages/existing-page")
    assert calls[0][1] == "PATCH"
    props = calls[0][2]["properties"]
    assert props["ArticleId"]["rich_text"][0]["text"]["content"] == "jmd:267141"
    assert props["Specialist Media"]["select"]["name"] == "鉄鋼新聞"
    assert props["Specialist Mail Source"]["select"]["name"] == "direct"
    assert props["Specialist Mail Sent"]["checkbox"] is True
    assert props["Specialist Mail Sent At"]["date"]["start"].startswith(
        "2026-09-12T07:30"
    )


def test_record_sent_articles_creates_minimal_article_when_missing(monkeypatch):
    monkeypatch.setattr(ledger, "NOTION_TOKEN", "token")
    monkeypatch.setattr(ledger, "ARTICLES_DB_ID", "db")
    monkeypatch.setattr(ledger, "_find_article", lambda article_key, canonical_url: None)
    calls = []

    def fake_request(url, method, payload=None):
        calls.append((url, method, payload))
        return {"id": "new-page"}

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
    props = calls[0][2]["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "sample industry"
    assert props["ArticleId"]["rich_text"][0]["text"]["content"] == (
        "japanmetal:20260911150723"
    )
    assert props["Sector"]["multi_select"] == [{"name": "Steel"}]
    assert props["PrimaryCountry"]["select"]["name"] == "Japan"


def test_find_article_prefers_article_id_then_url(monkeypatch):
    calls = []
    responses = iter([None, {"id": "url-match"}])

    def fake_query(filter_payload):
        calls.append(filter_payload)
        return next(responses)

    monkeypatch.setattr(ledger, "_query_one", fake_query)
    result = ledger._find_article(
        "jmd:1",
        "https://www.japanmetaldaily.com/articles/-/1",
    )

    assert result == {"id": "url-match"}
    assert calls[0]["property"] == "ArticleId"
    assert calls[1]["property"] == "NormalizedURL"
