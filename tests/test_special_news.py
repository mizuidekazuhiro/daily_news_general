import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import news_digest
from news_digest import (
    JST,
    build_special_media_row,
    build_special_news_subject,
    extract_entries_for_special_window,
    parse_env_bool,
    parse_mail_recipients,
    parse_feed_urls,
    parse_special_news_datetime_with_rule,
    render_special_news_html,
    normalize_special_date_rule,
)


class DummyEntry(dict):
    pass


def _entry(title, link, dt):
    e = DummyEntry(title=title, link=link)
    e.published_parsed = dt.timetuple()
    return e


def _entry_with_field(title, link, field, value):
    e = DummyEntry(title=title, link=link)
    setattr(e, field, value)
    return e


def test_parse_mail_recipients():
    assert parse_mail_recipients("a@example.com, b@example.com") == ["a@example.com", "b@example.com"]
    assert parse_mail_recipients("a@example.com\n b@example.com ; c@example.com") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_extract_entries_for_special_window_includes_within_24h():
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    window_start = now_jst - timedelta(hours=24)
    within = datetime(2026, 3, 11, 3, 30, tzinfo=timezone.utc)  # JST: 12:30

    actual = extract_entries_for_special_window(
        [_entry("A", "https://example.com/a", within)],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert len(actual) == 1
    assert actual[0]["title"] == "A"


def test_extract_entries_for_special_window_excludes_older_than_24h():
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    window_start = now_jst - timedelta(hours=24)
    older = datetime(2026, 3, 11, 2, 59, tzinfo=timezone.utc)  # JST: 11:59 (1 minute too old)

    actual = extract_entries_for_special_window(
        [_entry("A", "https://example.com/a", older)],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert actual == []


def test_extract_entries_for_special_window_handles_utc_to_jst_date_boundary():
    now_jst = datetime(2026, 3, 12, 2, 0, tzinfo=JST)
    window_start = now_jst - timedelta(hours=24)
    # UTC date is 3/11, JST is 3/12 00:30 (window内)
    cross_day_utc = datetime(2026, 3, 11, 15, 30, tzinfo=timezone.utc)

    actual = extract_entries_for_special_window(
        [_entry("Boundary", "https://example.com/b", cross_day_utc)],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert len(actual) == 1
    assert actual[0]["published"] == "2026-03-12 00:30"


def test_extract_entries_for_special_window_excludes_missing_datetime(caplog):
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    window_start = now_jst - timedelta(hours=24)
    missing = DummyEntry(title="No date", link="https://example.com/x")

    actual = extract_entries_for_special_window(
        [missing],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert actual == []
    assert "rss datetime not found" in caplog.text


def test_extract_entries_for_special_window_datetime_source_priority():
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    window_start = now_jst - timedelta(hours=24)
    # published is old, updated is within window. published優先なら除外される。
    entry = _entry_with_field("P", "https://example.com/p", "published", "Tue, 10 Mar 2026 00:00:00 +0000")
    setattr(entry, "updated", "Wed, 11 Mar 2026 20:00:00 +0000")

    actual = extract_entries_for_special_window(
        [entry],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert actual == []


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
        lambda _now_jst: {
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


def test_build_special_media_row_has_date_rule_defaults():
    row = build_special_media_row(
        media_name="媒体A",
        enabled=True,
        alert_ids=[],
        alert_feeds_raw="https://example.com/a",
        display_order=None,
        max_items=None,
        subject_prefix=None,
        delivery_enabled=True,
        max_items_total=10,
    )
    assert row is not None
    assert row["date_rule"]["date_source_type"] == "rss"
    assert row["date_rule"]["date_granularity"] == "datetime"


def test_extract_entries_for_special_window_date_granularity_date():
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    entry = _entry_with_field("A", "https://example.com/a", "published", "Wed, 11 Mar 2026 23:10:00 +0900")
    rule = normalize_special_date_rule("媒体A", {"date_granularity": "date", "target_date_mode": "calendar_day"})
    actual = extract_entries_for_special_window([entry], now_jst, "媒体A", "https://example.com/feed", rule)
    assert len(actual) == 1
    assert actual[0]["published"] == "2026-03-11"


def test_parse_special_news_datetime_with_rule_uses_selector_datetime_first():
    entry = DummyEntry(title="鉄鋼", link="https://example.com/steel")
    html_cache = {
        "https://example.com/steel": '<html><time class="article-header__published" datetime="2026/03/12 10:30">2026/03/12 11:59</time></html>'
    }
    rule = normalize_special_date_rule(
        "日刊鉄鋼新聞",
        {
            "date_source_type": "article_html",
            "date_css_selector": "time.article-header__published",
            "date_parse_pattern": r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}",
            "date_granularity": "datetime",
        },
    )

    actual = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, html_cache)
    assert actual["ok"] is True
    # datetime属性(10:30)が優先され、要素テキスト(11:59)は使われない
    assert actual["datetime"].astimezone(JST).strftime("%Y-%m-%d %H:%M") == "2026-03-12 10:30"


def test_parse_special_news_datetime_with_rule_uses_selector_text_when_no_datetime_attr():
    entry = DummyEntry(title="産業", link="https://example.com/sangyo")
    html_cache = {
        "https://example.com/sangyo": '<html><span class="font06">2026年3月12日</span></html>'
    }
    rule = normalize_special_date_rule(
        "日刊産業新聞",
        {
            "date_source_type": "article_html",
            "date_css_selector": "span.font06",
            "date_parse_pattern": r"\d{4}年\d{1,2}月\d{1,2}日",
            "date_granularity": "date",
        },
    )

    actual = parse_special_news_datetime_with_rule(entry, "日刊産業新聞", rule, html_cache)
    assert actual["ok"] is True
    assert actual["datetime"].astimezone(JST).strftime("%Y-%m-%d") == "2026-03-12"

def test_parse_special_news_datetime_with_rule_url_source_for_japanmetal(monkeypatch):
    entry = DummyEntry(title="産業", link="https://www.japanmetal.com/news-t20260316148097.html")
    rule = normalize_special_date_rule(
        "日刊産業新聞",
        {
            "date_source_type": "url",
            "date_parse_pattern": r"news-t(\d{4})(\d{2})(\d{2})\d+\.html",
            "date_granularity": "date",
            "target_date_mode": "calendar_day",
            "date_timezone": "Asia/Tokyo",
        },
    )
    monkeypatch.setattr(
        news_digest,
        "fetch_article_document",
        lambda link, cache: {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "canonical_url": "",
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "html": "",
        },
    )
    actual = parse_special_news_datetime_with_rule(entry, "日刊産業新聞", rule, {})
    assert actual["ok"] is True
    assert actual["datetime"].astimezone(JST).strftime("%Y-%m-%d") == "2026-03-16"


def test_parse_special_news_datetime_with_rule_url_fallback_to_rss(monkeypatch):
    dt = datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc)
    entry = _entry("産業", "https://www.japanmetal.com/no-date.html", dt)
    rule = normalize_special_date_rule(
        "日刊産業新聞",
        {
            "date_source_type": "url",
            "date_parse_pattern": r"news-t(\d{4})(\d{2})(\d{2})\d+\.html",
            "fallback_date_source_type": "rss",
            "date_granularity": "date",
        },
    )
    monkeypatch.setattr(
        news_digest,
        "fetch_article_document",
        lambda link, cache: {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "canonical_url": "",
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "html": "",
        },
    )
    actual = parse_special_news_datetime_with_rule(entry, "日刊産業新聞", rule, {})
    assert actual["ok"] is True
    assert actual["source_type"] == "rss"


def test_extract_redirect_url_from_google_wrapper_script():
    html = "<html><script>var redirectUrl='https://www.japanmetaldaily.com/articles/-/256198';</script></html>"
    info = news_digest._extract_redirect_url_from_wrapper(html)
    assert info["redirect_wrapper_detected"] is True
    assert info["redirect_url"] == "https://www.japanmetaldaily.com/articles/-/256198"


def test_extract_redirect_url_from_meta_refresh():
    html = '<html><meta http-equiv="refresh" content="0;url=https://www.japanmetaldaily.com/articles/-/256198"></html>'
    info = news_digest._extract_redirect_url_from_wrapper(html)
    assert info["redirect_wrapper_detected"] is True
    assert info["redirect_url"] == "https://www.japanmetaldaily.com/articles/-/256198"


def test_parse_special_news_datetime_refetches_article_before_selector(monkeypatch):
    entry = DummyEntry(title="鉄鋼", link="https://www.google.com/alerts/some-wrapper")
    wrapper_html = """
    <html><script>redirectUrl='https://www.japanmetaldaily.com/articles/-/256198';</script></html>
    """
    article_html = '<html><time class="article-header__published" datetime="2026-03-16 07:30">2026/3/16 8:20</time></html>'

    def fake_fetch(link: str):
        if "google.com/alerts" in link:
            return {
                "source_url": link,
                "initial_url": link,
                "final_url": link,
                "html": wrapper_html,
                "redirect_wrapper_detected": False,
                "redirect_url": "",
                "refetched_article_url": "",
                "refetch_success": False,
            }
        return {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "html": article_html,
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
        }

    monkeypatch.setattr(news_digest, "fetch_article_html", fake_fetch)
    rule = normalize_special_date_rule("日刊鉄鋼新聞")
    result = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, {})
    assert result["ok"] is True
    assert result["datetime"].astimezone(JST).strftime("%Y-%m-%d %H:%M") == "2026-03-16 07:30"


def test_wrapper_html_pattern_not_applied_when_refetch_success(monkeypatch):
    entry = DummyEntry(title="鉄鋼", link="https://www.google.com/alerts/some-wrapper")
    wrapper_html = "<html><meta http-equiv=\"refresh\" content=\"0;url=https://www.japanmetaldaily.com/articles/-/256198\"></html>"
    article_html = '<html><time class="article-header__published" datetime="2026/03/16 09:10"></time></html>'

    def fake_fetch(link: str):
        html = wrapper_html if "google.com/alerts" in link else article_html
        return {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "html": html,
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
        }

    monkeypatch.setattr(news_digest, "fetch_article_html", fake_fetch)
    rule = normalize_special_date_rule("日刊鉄鋼新聞")
    result = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, {})
    assert result["ok"] is True
    assert result["datetime"].astimezone(JST).strftime("%Y-%m-%d %H:%M") == "2026-03-16 09:10"


def test_extract_entries_for_special_window_calendar_day_uses_parsed_date(monkeypatch, caplog):
    now_jst = datetime(2026, 3, 17, 17, 53, tzinfo=JST)
    entry = DummyEntry(title="産業", link="https://www.japanmetal.com/news-t20260316148097.html")
    rule = normalize_special_date_rule(
        "日刊産業新聞",
        {
            "date_source_type": "url",
            "date_parse_pattern": r"news-t(\d{4})(\d{2})(\d{2})\d+\.html",
            "date_granularity": "date",
            "target_date_mode": "calendar_day",
        },
    )
    monkeypatch.setattr(
        news_digest,
        "fetch_article_document",
        lambda link, cache: {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "canonical_url": "",
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "html": "",
        },
    )

    caplog.set_level("INFO")
    actual = extract_entries_for_special_window([entry], now_jst, "日刊産業新聞", "https://example.com/feed", rule)
    assert len(actual) == 1
    assert "target_date=2026-03-16" in caplog.text
    assert "parsed_date=2026-03-16" in caplog.text
    assert "decision=accepted" in caplog.text
    assert "evaluation_mode=calendar_day" in caplog.text
    assert "out_of_window" not in caplog.text


def test_extract_entries_for_special_window_calendar_day_mismatch_is_rejected(monkeypatch, caplog):
    now_jst = datetime(2026, 3, 17, 17, 53, tzinfo=JST)
    entry = DummyEntry(title="産業", link="https://www.japanmetal.com/news-t20260315148097.html")
    rule = normalize_special_date_rule(
        "日刊産業新聞",
        {
            "date_source_type": "url",
            "date_parse_pattern": r"news-t(\d{4})(\d{2})(\d{2})\d+\.html",
            "date_granularity": "date",
            "target_date_mode": "calendar_day",
        },
    )
    monkeypatch.setattr(
        news_digest,
        "fetch_article_document",
        lambda link, cache: {
            "source_url": link,
            "initial_url": link,
            "final_url": link,
            "canonical_url": "",
            "redirect_wrapper_detected": False,
            "redirect_url": "",
            "refetched_article_url": "",
            "refetch_success": False,
            "html": "",
        },
    )

    caplog.set_level("INFO")
    actual = extract_entries_for_special_window([entry], now_jst, "日刊産業新聞", "https://example.com/feed", rule)
    assert actual == []
    assert "target_date=2026-03-16" in caplog.text
    assert "parsed_date=2026-03-15" in caplog.text
    assert "decision=target_date_mismatch" in caplog.text


def test_parse_special_news_datetime_with_rule_json_ld_newsarticle_datepublished_fallback():
    entry = DummyEntry(title="鉄鋼", link="https://example.com/steel-jsonld")
    html_cache = {
        "https://example.com/steel-jsonld": (
            "<html><head>"
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"NewsArticle","datePublished":"2026-03-16T05:00:00+09:00"}'
            "</script>"
            "</head><body></body></html>"
        )
    }
    rule = normalize_special_date_rule(
        "日刊鉄鋼新聞",
        {
            "date_source_type": "article_html",
            "date_css_selector": "time.article-header__published",
            "date_parse_pattern": r"\\d{4}/\\d{1,2}/\\d{1,2}\\s+\\d{1,2}:\\d{2}",
            "date_granularity": "datetime",
            "target_date_mode": "rolling_24h",
        },
    )

    actual = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, html_cache)
    assert actual["ok"] is True
    assert actual["datetime"].isoformat() == "2026-03-16T05:00:00+09:00"


def test_parse_special_news_datetime_with_rule_json_ld_newsarticle_array_and_graph_fallback():
    entry = DummyEntry(title="鉄鋼", link="https://example.com/steel-jsonld-graph")
    html_cache = {
        "https://example.com/steel-jsonld-graph": (
            "<html><head>"
            '<script type="application/ld+json">'
            '[{"@type":"BreadcrumbList"},{"@graph":[{"@type":"Organization"},{"@type":"NewsArticle","datePublished":"2026-03-16T06:00:00+09:00"}]}]'
            "</script>"
            "</head><body></body></html>"
        )
    }
    rule = normalize_special_date_rule("日刊鉄鋼新聞")

    actual = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, html_cache)
    assert actual["ok"] is True
    assert actual["datetime"].isoformat() == "2026-03-16T06:00:00+09:00"


def test_special_news_main_job_path_unchanged_for_non_special_logic():
    e = _entry("A", "https://example.com/a", datetime(2026, 3, 11, 3, 30, tzinfo=timezone.utc))
    now_jst = datetime(2026, 3, 12, 12, 0, tzinfo=JST)
    actual = extract_entries_for_special_window(
        [e],
        now_jst,
        "媒体A",
        "https://example.com/feed",
        normalize_special_date_rule("媒体A"),
    )
    assert len(actual) == 1


def test_fetch_special_news_config_from_notion_reads_trimmed_date_css_selector(monkeypatch):
    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return (
                b'{"results":[{"properties":{"MediaName":{"type":"title","title":[{"plain_text":"\xe6\x97\xa5\xe5\x88\x8a\xe9\x89\x84\xe9\x8b\xbc\xe6\x96\xb0\xe8\x81\x9e"}]},'
                b'"Enabled":{"type":"checkbox","checkbox":true},'
                b'"GoogleAlertFeeds":{"type":"rich_text","rich_text":[{"plain_text":"https://example.com/feed"}]},'
                b'"DateSourceType":{"type":"select","select":{"name":"article_html"}},'
                b'"DateCssSelector ":{"type":"rich_text","rich_text":[{"plain_text":"  time.article-header__published  "}]}}}]}'
            )

    monkeypatch.setattr(news_digest, "NOTION_TOKEN", "token")
    monkeypatch.setattr(news_digest, "NOTION_SPECIAL_NEWS_DB_ID", "db")
    monkeypatch.setattr(news_digest, "parse_env_bool", lambda *_: True)
    monkeypatch.setattr(news_digest.urllib.request, "urlopen", lambda *args, **kwargs: DummyResponse())

    rows = news_digest.fetch_special_news_config_from_notion()
    assert rows is not None
    steel = rows[0]
    assert steel["date_rule"]["date_css_selector"] == "time.article-header__published"


def test_article_html_meta_fallback_uses_single_date_candidate():
    entry = DummyEntry(title="鉄鋼", link="https://example.com/steel-meta")
    html_cache = {
        "https://example.com/steel-meta": (
            '<html><head>'
            '<meta name="viewport" content="width=1098">'
            '<meta name="description" content="summary">'
            '<meta property="article:published_time" content="2026-03-16 05:00">'
            '</head><body></body></html>'
        )
    }
    rule = normalize_special_date_rule(
        "日刊鉄鋼新聞",
        {
            "date_source_type": "article_html",
            "date_css_selector": "time.article-header__published",
            "date_parse_pattern": r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}",
            "fallback_date_source_type": "",
        },
    )

    actual = parse_special_news_datetime_with_rule(entry, "日刊鉄鋼新聞", rule, html_cache)
    assert actual["ok"] is True
    assert actual["datetime"].astimezone(JST).strftime("%Y-%m-%d %H:%M") == "2026-03-16 05:00"
