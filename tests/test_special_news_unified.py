from pathlib import Path

import pytest

import special_news_unified as mod


def test_article_identity_unwraps_google_redirect_for_japanmetaldaily():
    wrapped = (
        "https://www.google.com/url?rct=j&sa=t&url="
        "https%3A%2F%2Fwww.japanmetaldaily.com%2Farticles%2F-%2F267141"
        "&ct=ga"
    )
    direct = "https://www.japanmetaldaily.com/articles/-/267141"
    assert mod.article_identity(wrapped) == "jmd:267141"
    assert mod.article_identity(wrapped) == mod.article_identity(direct)


def test_article_identity_unwraps_google_redirect_for_japanmetal():
    wrapped = (
        "https://www.google.com/url?rct=j&sa=t&url="
        "https%3A%2F%2Fwww.japanmetal.com%2Fnews-t20260911150723.html"
        "&ct=ga"
    )
    assert mod.article_identity(wrapped) == "japanmetal:20260911150723"


def test_merge_prefers_direct_for_both_specialist_papers():
    steel_direct = {
        "title": "Direct steel title",
        "link": "https://www.japanmetaldaily.com/articles/-/267141",
        "published": "2026-09-11 05:00",
    }
    steel_alert = {
        "title": "Alert steel title",
        "link": "https://www.google.com/url?url=https%3A%2F%2Fwww.japanmetaldaily.com%2Farticles%2F-%2F267141",
        "published": "2026-09-11 05:00",
    }
    industry_alert = {
        "title": "Alert industry title",
        "link": "https://www.google.com/url?url=https%3A%2F%2Fwww.japanmetal.com%2Fnews-t20260911150723.html",
        "published": "2026-09-11",
    }
    industry_direct = {
        "title": "Direct industry title",
        "link": "https://www.japanmetal.com/news-t20260911150723.html",
        "published": "2026-09-11 00:00",
    }

    merged = mod.merge_sources(
        {
            "鉄鋼新聞": [steel_alert],
            "日刊産業新聞": [industry_alert],
        },
        {
            "鉄鋼新聞": [steel_direct],
            "日刊産業新聞": [industry_direct],
        },
        sent_keys=set(),
    )
    by_media = {row["media_name"]: row["items"] for row in merged}

    assert len(by_media["鉄鋼新聞"]) == 1
    assert by_media["鉄鋼新聞"][0]["title"] == "Direct steel title"
    assert by_media["鉄鋼新聞"][0]["source"] == "direct"

    assert len(by_media["日刊産業新聞"]) == 1
    assert by_media["日刊産業新聞"][0]["title"] == "Direct industry title"
    assert by_media["日刊産業新聞"][0]["source"] == "direct"
    assert by_media["日刊産業新聞"][0]["link"] == "https://www.japanmetal.com/news-t20260911150723.html"


def test_merge_excludes_previously_sent_articles():
    alert = {
        "鉄鋼新聞": [
            {
                "title": "Already sent",
                "link": "https://www.google.com/url?url=https%3A%2F%2Fwww.japanmetaldaily.com%2Farticles%2F-%2F267141",
                "published": "2026-09-11 05:00",
            },
            {
                "title": "New",
                "link": "https://www.japanmetaldaily.com/articles/-/267142",
                "published": "2026-09-12 05:00",
            },
        ]
    }
    merged = mod.merge_sources(alert, {"鉄鋼新聞": [], "日刊産業新聞": []}, {"jmd:267141"})
    steel = next(row for row in merged if row["media_name"] == "鉄鋼新聞")
    assert [item["article_key"] for item in steel["items"]] == ["jmd:267142"]


def test_workflows_route_automatic_delivery_through_unified_job():
    special = Path(".github/workflows/special_news_delivery.yml").read_text(encoding="utf-8")
    direct = Path(".github/workflows/direct_site_updates.yml").read_text(encoding="utf-8")

    assert "python special_news_unified.py" in special
    assert 'NOTION_SPECIAL_NEWS_ENABLED: "true"' in special
    assert 'NOTION_DIRECT_SITES_ENABLED: "true"' in special
    assert 'SPECIAL_NEWS_ALLOW_LOCAL_CONFIG_FALLBACK: "false"' in special
    assert 'DIRECT_SITE_ALLOW_LOCAL_CONFIG_FALLBACK: "false"' in special
    assert "Notify specialist-news failure" in special
    assert "send_special_news_failure_mail.py" in special

    assert "schedule:" not in direct
    assert "workflow_dispatch:" in direct
    assert 'DIRECT_SITE_DIAGNOSTIC_ONLY: "true"' in direct
    assert "DIRECT_SITE_MAIL_TO" not in direct
    assert "MAIL_PASSWORD" not in direct



def test_load_effective_sent_keys_uses_ledger_when_gmail_is_unavailable(monkeypatch):
    monkeypatch.setattr(mod.special_news_sent_ledger, "load_sent_keys", lambda days: {"jmd:1"})
    monkeypatch.setattr(mod, "GMAIL_SAFETY_NET", True)
    monkeypatch.setattr(
        mod,
        "load_recent_gmail_article_keys",
        lambda: (_ for _ in ()).throw(RuntimeError("imap down")),
    )
    assert mod.load_effective_sent_keys() == {"jmd:1"}


def test_merge_applies_sent_filter_and_sort_before_media_limit(caplog):
    direct = {
        "鉄鋼新聞": [
            {
                "title": "old unsent",
                "link": "https://www.japanmetaldaily.com/articles/-/100",
                "published": "2026-09-10 05:00",
            },
            {
                "title": "new sent",
                "link": "https://www.japanmetaldaily.com/articles/-/102",
                "published": "2026-09-12 05:00",
            },
            {
                "title": "newest unsent",
                "link": "https://www.japanmetaldaily.com/articles/-/103",
                "published": "2026-09-12 06:00",
            },
        ],
        "日刊産業新聞": [],
    }
    alert = {"鉄鋼新聞": [], "日刊産業新聞": []}

    caplog.set_level("INFO")
    merged = mod.merge_sources(
        alert,
        direct,
        sent_keys={"jmd:102"},
        media_limits={"鉄鋼新聞": 1, "日刊産業新聞": 20},
        max_items_total=50,
    )
    steel = next(row for row in merged if row["media_name"] == "鉄鋼新聞")

    assert [item["article_key"] for item in steel["items"]] == ["jmd:103"]
    assert "sent_removed=1" in caplog.text
    assert "merged_before_limit=2" in caplog.text
    assert "delivered=1" in caplog.text


def test_source_diff_log_reports_direct_alert_overlap(caplog):
    direct = {
        "鉄鋼新聞": [
            {"title": "shared", "link": "https://www.japanmetaldaily.com/articles/-/1", "published": "2026-09-12 05:00"},
            {"title": "direct only", "link": "https://www.japanmetaldaily.com/articles/-/2", "published": "2026-09-12 05:01"},
        ],
        "日刊産業新聞": [],
    }
    alert = {
        "鉄鋼新聞": [
            {"title": "shared alert", "link": "https://www.japanmetaldaily.com/articles/-/1", "published": "2026-09-12 05:00"},
            {"title": "alert only", "link": "https://www.japanmetaldaily.com/articles/-/3", "published": "2026-09-12 05:02"},
        ],
        "日刊産業新聞": [],
    }

    caplog.set_level("INFO")
    mod.merge_sources(
        alert,
        direct,
        sent_keys=set(),
        media_limits={"鉄鋼新聞": 20, "日刊産業新聞": 20},
    )

    assert "media=鉄鋼新聞 direct=2 alert=2 overlap=1 direct_only=1 alert_only=1" in caplog.text


def test_run_fails_when_smtp_credentials_are_missing(monkeypatch):
    item = {
        "title": "new article",
        "link": "https://www.japanmetaldaily.com/articles/-/999",
        "published": "2026-09-12 06:00",
    }
    monkeypatch.setattr(mod, "load_effective_sent_keys", lambda: set())
    monkeypatch.setattr(mod, "_alert_results", lambda now: ({"鉄鋼新聞": [], "日刊産業新聞": []}, {}))
    monkeypatch.setattr(mod, "_direct_results", lambda now: ({"鉄鋼新聞": [item], "日刊産業新聞": []}, {}))
    monkeypatch.setattr(mod.news_digest, "SPECIAL_NEWS_MAIL_TO", "recipient@example.com")
    monkeypatch.setattr(mod.news_digest, "SPECIAL_NEWS_MAIL_CC", "")
    monkeypatch.setattr(mod.news_digest, "SPECIAL_NEWS_MAIL_BCC", "")
    monkeypatch.setattr(mod.news_digest, "MAIL_FROM", "")
    monkeypatch.setattr(mod.news_digest, "MAIL_PASSWORD", "")

    with pytest.raises(RuntimeError, match="SMTP credentials"):
        mod.run()



def test_run_skips_email_and_ledger_when_no_new_items(monkeypatch, caplog):
    monkeypatch.setattr(mod, "load_effective_sent_keys", lambda: set())
    monkeypatch.setattr(
        mod,
        "_alert_results",
        lambda now: ({"鉄鋼新聞": [], "日刊産業新聞": []}, {}),
    )
    monkeypatch.setattr(
        mod,
        "_direct_results",
        lambda now: ({"鉄鋼新聞": [], "日刊産業新聞": []}, {}),
    )
    sent = {"mail": False, "ledger": False}
    monkeypatch.setattr(
        mod.news_digest,
        "send_mail_generic",
        lambda *args, **kwargs: sent.__setitem__("mail", True),
    )
    monkeypatch.setattr(
        mod.special_news_sent_ledger,
        "record_sent_articles",
        lambda *args, **kwargs: sent.__setitem__("ledger", True),
    )

    caplog.set_level("INFO")
    mod.run()

    assert sent == {"mail": False, "ledger": False}
    assert "delivery skipped reason=no_new_items" in caplog.text


def test_alert_results_excludes_delivery_disabled_media(monkeypatch):
    monkeypatch.setattr(
        mod.news_digest,
        "collect_special_news_articles",
        lambda now, apply_limits=False: {
            "delivery_enabled": True,
            "media_results": [
                {
                    "media_name": "鉄鋼新聞",
                    "delivery_enabled": False,
                    "items": [{"title": "x", "link": "https://www.japanmetaldaily.com/articles/-/1"}],
                    "max_items": 20,
                },
                {
                    "media_name": "日刊産業新聞",
                    "delivery_enabled": True,
                    "items": [{"title": "y", "link": "https://www.japanmetal.com/news-t20260912001.html"}],
                    "max_items": 20,
                },
            ],
        },
    )

    results, limits = mod._alert_results(mod.datetime.now(mod.JST))

    assert "鉄鋼新聞" not in results
    assert "鉄鋼新聞" not in limits
    assert len(results["日刊産業新聞"]) == 1


def test_direct_results_monitors_but_does_not_deliver_disabled_media(monkeypatch):
    disabled = {
        "SiteName": "鉄鋼新聞",
        "Enabled": True,
        "DeliveryEnabled": False,
        "DisplayOrder": 1,
        "MaxItemsPerSite": 20,
    }
    enabled = {
        "SiteName": "日刊産業新聞",
        "Enabled": True,
        "DeliveryEnabled": True,
        "DisplayOrder": 2,
        "MaxItemsPerSite": 20,
    }
    monkeypatch.setattr(mod.direct_site_updates, "load_sites", lambda: ("notion", [disabled, enabled]))
    calls = []

    class Item:
        def __init__(self, title, url):
            self.title = title
            self.url = url
            self.published_label = "2026-09-12"

    def fake_collect(cfg, now, apply_limit=False):
        calls.append(cfg["SiteName"])
        return [Item(cfg["SiteName"], f"https://example.com/{cfg['SiteName']}")]

    monkeypatch.setattr(mod.direct_site_updates, "collect_site_items", fake_collect)

    results, limits = mod._direct_results(mod.datetime.now(mod.JST))

    assert calls == ["鉄鋼新聞", "日刊産業新聞"]
    assert results["鉄鋼新聞"] == []
    assert len(results["日刊産業新聞"]) == 1
    assert "鉄鋼新聞" not in limits


def test_email_template_is_inline_styled_and_matches_unified_architecture():
    template = Path("templates/special_news_email.html").read_text(encoding="utf-8")

    assert "<style" not in template.lower()
    assert "Google Alert を参照して自動生成" not in template
    assert "公式サイトを主系" in template
    assert 'name="viewport"' in template
    assert "max-width:680px" in template
