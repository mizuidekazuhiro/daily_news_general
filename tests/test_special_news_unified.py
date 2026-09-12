from pathlib import Path

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
    assert "NOTION_DIRECT_SITES_DB_ID" in special
    assert "schedule:" not in direct
    assert "workflow_dispatch:" in direct
