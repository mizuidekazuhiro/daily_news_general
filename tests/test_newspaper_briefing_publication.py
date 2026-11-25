from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

from scripts.newspaper_briefing_link_mail import compose
from src.openai_json_client import OpenAIJsonClient
from src.newspaper_briefing import __main__ as cli, feeds, primary, publication
from src.newspaper_briefing.policy import Grant


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "newspaper_briefing"
MORNING = "2026-11-24T08:00:00+09:00"
EVENING = "2026-11-24T18:00:00+09:00"
LINK = publication.MLIT_PAGES + "road01_hh_021.html"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_live_collection_uses_only_routes_cleared_for_acquisition(monkeypatch):
    settings = feeds.load_settings(cli.ROOT / "config/newspaper_briefing.json",
                                   cli.ROOT / "config/newspaper_briefing_source_policy.json")
    attempted = []

    def fetch(feed, settings):
        attempted.append(feed.id)
        return feeds.FeedResult(feed.id, feeds.timestamp(MORNING)), []

    monkeypatch.setattr(feeds, "fetch_feed", fetch)
    capture = feeds.collect(settings)
    assert set(attempted) == {"nhk-main", "nhk-society", "nhk-culture", "nhk-science", "nhk-politics",
                              "nhk-economy", "nhk-international", "nhk-sports"}
    assert all(result.failure for result in capture.feeds if result.feed not in attempted)


def test_licensed_headlines_do_not_extend_permission_to_other_routes(tmp_path, monkeypatch):
    config = read(cli.ROOT / "config/newspaper_briefing.json")
    publisher = next(row for row in config["publishers"] if row["id"] == "okinawa")
    publisher["allow_distribution"] = True
    official = next(row for row in config["feeds"] if row["id"] == "okinawa-official")
    source_policy = {"sources": [{
        "id": official["id"], "kind": "headline", "url": official["url"],
        "basis_urls": [], "checked_on": "2026-11-24", "user_confirmation": "利用者による架空の許諾確認",
        "permissions": {"acquire": True, "save": True, "ai": False, "email": True},
        "constraints": "この公式フィードだけに適用する架空のテスト許諾",
    }]}
    config_path = tmp_path / "config.json"
    policy_path = tmp_path / "policy.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    policy_path.write_text(json.dumps(source_policy, ensure_ascii=False), encoding="utf-8")

    def capture(settings):
        data = feeds.read_capture(FIXTURES / "morning.json", settings)
        source = next(row for row in data.observations if row.feed == "okinawa-official")
        data.observations.append(replace(source, feed="okinawa-search", title="無許諾の検索経路にある別のニュース",
                                         original_url="https://news.google.com/rss/articles/unapproved-search",
                                         publisher_url="", guid="unapproved-search"))
        return data

    monkeypatch.setattr(cli, "collect", capture)
    monkeypatch.setattr(cli, "collect_primary", lambda *args: primary.empty_primary("off"))
    monkeypatch.setattr(publication, "collect_publication", lambda *args: primary.empty_primary("off"))
    assert cli.main(["--preview", "--publication-mail", "--edition", "morning", "--as-of", MORNING,
                     "--output-dir", str(tmp_path), "--config", str(config_path), "--source-policy", str(policy_path)]) == 0
    folder = tmp_path / "2026-11-24/morning/080000"
    mail = read(folder / "delivery/mail.json")
    assert mail["email_allowed"] is True and "海風市で通学路を変更" in mail["text"]
    assert "沖縄タイムス" in mail["text"] and "無許諾の検索経路" not in mail["text"]
    assert "無許諾の検索経路" in (folder / "report.txt").read_text(encoding="utf-8")
    publishers = read(folder / "diagnostics.json")["publishers"]
    published = {row["id"]: row["published_stories"] for row in publishers if row["published_stories"]}
    assert published == {"okinawa": len(read(folder / "delivery/report.json")["stories"])}
    assert any(row["id"] != "okinawa" and row["publication_stage"] == "selection" for row in publishers)


def test_ai_email_has_a_summary_for_every_rank_and_multiple_verified_links(tmp_path, monkeypatch):
    settings = feeds.load_settings(cli.ROOT / "config/newspaper_briefing.json")
    for publisher, route in (("okinawa", "okinawa-official"), ("asahi", "asahi-search")):
        settings.publishers[publisher] = replace(settings.publishers[publisher], allow_distribution=True, allow_ai=True)
        settings.grants["headline", route] = Grant(
            id=route, kind="headline", url=settings.feeds[route].url, checked_on="2026-11-24",
            basis_urls=["https://example.invalid/permission"], constraints="テスト用",
            permissions={"acquire": True, "save": True, "ai": True, "email": True},
        )

    def observation(publisher, route, slug, title):
        link = f"https://{settings.publishers[publisher].hosts[0]}/test/{slug}"
        at = feeds.timestamp(MORNING)
        return feeds.Observation(route, publisher, settings.publishers[publisher].name, feeds.host(link), title,
                                 link, link, slug, at, at, "rss.pubDate", None, publisher, "fixture", "", "")

    title = "青葉市の港南研究所、湾で新種の海藻を発見"
    alternative = "海藻の新種を確認 港南研究所が発表 青葉市"
    records = [observation("okinawa", "okinawa-official", "discovery", title),
               observation("asahi", "asahi-search", "independent", alternative),
               observation("kahoku", "kahoku-search", "unapproved", title)]
    records.extend(observation("okinawa", "okinawa-official", f"event-{day}", f"市立資料館が12月{day}日の特別展示を発表")
                   for day in range(1, 32))
    captured = feeds.timestamp(MORNING)
    capture = feeds.Capture(captured, [feeds.FeedResult(route, captured) for route in
                                     ("okinawa-official", "asahi-search", "kahoku-search")], records)
    monkeypatch.setattr(cli, "load_settings", lambda *_: settings)
    monkeypatch.setattr(cli, "collect", lambda _: capture)
    monkeypatch.setattr(publication, "collect_publication", lambda *args: primary.empty_primary("off"))
    monkeypatch.setenv("NEWSPAPER_BRIEFING_OPENAI_API_KEY", "test")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_OPENAI_MODEL", "test")
    selected, compared = [], []

    def choose(_client, user_prompt, **kwargs):
        payload = json.loads(user_prompt)
        if "articles" in payload[0]:
            [pair] = payload
            assert {article["headline"] for article in pair["articles"]} == {title, alternative}
            compared.append(pair["id"])
            return {"pairs": [{"id": pair["id"], "same_event": True, "shared_detail": "港南研究所"}]}
        assert 1 <= len(payload) <= 10
        assert [item["id"] for item in payload] == [f"s{number}" for number in range(1, len(payload) + 1)]
        selected.extend(item["id"] for item in payload)
        assert all("url" not in fact for item in payload for fact in item["facts"])
        assert all(fact["name"] != "河北新報" for item in payload for fact in item["facts"])
        rows = {}
        definitions = kwargs["schema"]["properties"]["stories"]["properties"]
        for item in payload:
            row = {}
            for section in ("what", "why", "next"):
                available = [fact["id"] for fact in item["facts"] if fact["section"] == section]
                assert definitions[item["id"]]["properties"][section]["enum"] == (available or [""])
                row[section] = available[0] if available else ""
            rows[item["id"]] = row
        return {"stories": rows}

    monkeypatch.setattr(OpenAIJsonClient, "generate_structured_json", choose)
    assert cli.main(["--preview", "--publication-mail", "--ai", "--headlines-only", "--edition", "morning",
                     "--as-of", MORNING, "--output-dir", str(tmp_path)]) == 0
    folder = tmp_path / "2026-11-24/morning/080000/delivery"
    report = read(folder / "report.json")
    assert len(compared) == 1 and read(folder.parent / "diagnostics.json")["event_matching_ai"]["merged"] == 1
    assert len(report["stories"]) == len(selected) == 30 and report["story_count"] == 32
    assert all(story["summary"]["sections"]["what"]["fact_ids"] for story in report["stories"])
    assert report["stories"][-1]["summary"]["compact"] is True and report["ai_state"] == "ok"
    mail = read(folder / "mail.json")
    assert mail["html"].count("/test/discovery") == mail["html"].count("/test/independent") == 1
    assert "関連記事" in mail["html"] and "/test/unapproved" not in mail["html"]
    assert report["delivery"]["email"]["state"] == "preview"


def release(link, at, title, lead):
    return {"source": "mlit-press", "link": link, "issued_at": at,
            "match": {"title": title}, "facts": [{"field": "発表内容", "value": lead}], "press_lead": True}


def test_press_rss_uses_only_dated_first_party_material(monkeypatch):
    settings = feeds.load_settings(cli.ROOT / "config/newspaper_briefing.json",
                                   cli.ROOT / "config/newspaper_briefing_source_policy.json")
    grant = settings.grants[("primary", "mlit-press")]
    end = feeds.timestamp(MORNING)
    rss = f'''<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>報道発表</title>
      <link>https://www.mlit.go.jp/</link><description>報道発表</description>
      <item><title>通学路の除雪</title><link>{LINK.replace("https", "http", 1)}</link>
        <dc:creator>国土交通省</dc:creator><dc:date>2026-11-24T07:00:00+09:00</dc:date></item>
      <item><title>無関係</title><link>https://other.invalid/document.html</link>
        <dc:creator>国土交通省</dc:creator><dc:date>2026-11-24T07:00:00+09:00</dc:date></item>
      <item><title>未来の発表</title><link>{publication.MLIT_PAGES}road02.html</link>
        <dc:creator>国土交通省</dc:creator><dc:date>2026-11-24T09:00:00+09:00</dc:date></item>
    </channel></rss>'''.encode()
    lead = "豪雪地域の小学生が通う道路で除雪を優先する"
    page = f'''<meta property="og:url" content="{LINK}"><div id="contents" class="pressContents">
    <div class="section"><div class="clearfix"><p class="date">11月24日</p>
    <p>{lead}。続く本文は保存しない。</p></div></div></div>'''.encode()
    files = {grant.url: rss, LINK: page}
    monkeypatch.setattr(publication, "fetch_bytes", lambda url, timeout: files[url])
    docs, detail = publication.press_documents(grant, end - timedelta(hours=24), end, 1)
    assert docs == [release(LINK, "2026-11-24T07:00:00+09:00", "通学路の除雪", lead)]
    assert detail["feed_entries"] == 3 and detail["accepted"] == 1
    assert publication.press_lead(page.replace(lead.encode(), "無断転載を禁じます".encode()), LINK) is None


def test_normal_preview_finalizes_only_approved_facts_for_recurring_mail(tmp_path, monkeypatch):
    latest = release(LINK, "2026-11-24T07:30:00+09:00", "通学路の除雪",
                     "対象の通学路では登校前の除雪を進める")
    latest["match"]["article_url"] = "https://www.okinawatimes.co.jp/__newspaper_briefing_fixture__/road"
    follow = release(publication.MLIT_PAGES + "road02_hh_022.html", "2026-11-24T14:30:00+09:00",
                     "交通規制を解除", "補修工事が終了し、午後2時に通行を再開した")
    follow["match"]["article_url"] = "https://news.web.nhk/__newspaper_briefing_fixture__/future"
    denied = latest | {"source": "unapproved", "match": {"title": "許可未確認の見出し"}}
    unrelated = release(publication.MLIT_PAGES + "road03_hh_023.html", "2026-11-24T07:35:00+09:00",
                        "別件の公式発表", "新聞横断版のニュースには該当しない発表")
    health = [{"id": "mlit-press", "name": "国土交通省", "state": "ok"}]

    def collect(settings, start, end):
        return {"sources": health, "documents": [latest, follow, denied, unrelated]}

    def unexpected(*args, **kwargs):
        raise AssertionError("preview attempted delivery")

    monkeypatch.setattr(cli, "collect", lambda settings, **kw: feeds.read_capture(FIXTURES / "morning.json", settings))
    monkeypatch.setattr(cli, "collect_primary", lambda *args: {"state": "ok", "sources": [], "documents": []})
    monkeypatch.setattr(publication, "collect_publication", collect)
    monkeypatch.setattr(cli, "deliver", unexpected)
    for edition, as_of in (("morning", MORNING), ("evening", EVENING)):
        assert cli.main(["--preview", "--publication-mail", "--edition", edition, "--as-of", as_of,
                         "--output-dir", str(tmp_path)]) == 0

    root = tmp_path / "2026-11-24"
    morning = root / "morning/080000"
    evening = root / "evening/180000"
    assert len(read(morning / "report.json")["stories"]) > 1
    [approved] = read(morning / "delivery/report.json")["stories"]
    [ranked] = [row for row in read(morning / "ranking.json") if row["id"] == approved["id"]]
    assert "通学路を変更" in ranked["title"] and approved["rank"] == ranked["rank"]
    install = {"PublicationMail": True, "Previews": str(tmp_path)}
    for edition, folder, hour, caption in (("morning", morning, 8, "登校前の除雪"),
                                           ("evening", evening, 18, "通行を再開")):
        package = compose(install, feeds.timestamp(f"2026-11-24T{hour:02d}:10:00+09:00"), edition)
        assert package["state"] == "ready" and package["link_count"] == 1
        assert caption in package["text"] and "国土交通省資料を基に作成" in package["text"]
        assert "許可未確認の見出し" not in package["text"]
        assert "通学路を変更" not in package["text"] and "別件の公式発表" not in package["text"]
    report = read(evening / "delivery/report.json")
    assert report["comparison_state"] == "matched" and report["changes"]["新規"] == 1
    assert "登校前の除雪" not in (evening / "delivery/report.txt").read_text(encoding="utf-8")


def test_missing_eligible_facts_blocks_smtp_and_does_not_claim_zero_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "collect", lambda settings: feeds.read_capture(FIXTURES / "evening.json", settings))
    monkeypatch.setattr(cli, "collect_primary", lambda *args: primary.empty_primary("ok"))
    monkeypatch.setattr(publication, "collect_publication", lambda *args: primary.empty_primary("ok"))

    def unexpected(*args, **kwargs):
        raise AssertionError("SMTP was called for an empty evidence package")

    monkeypatch.setattr("src.newspaper_briefing.delivery.prepare_email", unexpected)
    assert cli.main(["--publication-mail", "--send-email", "--edition", "evening", "--as-of", EVENING,
                     "--output-dir", str(tmp_path)]) == 3
    folder = tmp_path / "2026-11-24/evening/180000/delivery"
    report = read(folder / "report.json")
    assert report["delivery"]["email"]["state"] == "blocked"
    assert report["comparison_state"] == "insufficient_evidence"
    assert all(count is None for count in report["changes"].values())
    assert read(folder / "mail.json")["email_allowed"] is False


def test_smtp_receives_the_same_approved_briefing_and_is_reserved_once(tmp_path, monkeypatch):
    document = release(LINK, "2026-11-24T07:30:00+09:00", "通学路の除雪", "対象の通学路では登校前の除雪を進める")
    document["match"]["article_url"] = "https://www.okinawatimes.co.jp/__newspaper_briefing_fixture__/road"
    monkeypatch.setattr(cli, "collect", lambda settings: feeds.read_capture(FIXTURES / "morning.json", settings))
    monkeypatch.setattr(cli, "collect_primary", lambda *args: primary.empty_primary("ok"))
    monkeypatch.setattr(publication, "collect_publication", lambda *args: {"sources": [], "documents": [document]})
    monkeypatch.setattr(cli, "DEFAULT_OUTPUT", tmp_path / "ledger")
    sent = []
    monkeypatch.setattr("src.newspaper_briefing.delivery.prepare_email",
                        lambda report, text, html: lambda: sent.append((report["subject"], text, html)))
    args = ["--publication-mail", "--send-email", "--edition", "morning", "--as-of", MORNING,
            "--output-dir", str(tmp_path)]
    assert cli.main(args) == 0
    folder = tmp_path / "2026-11-24/morning/080000/delivery"
    mail = read(folder / "mail.json")
    assert sent == [(mail["subject"], mail["text"], mail["html"])]
    assert "登校前の除雪" in sent[0][1] and "通学路を変更" not in sent[0][1]
    assert read(folder / "report.json")["delivery"]["email"]["state"] == "sent"
    assert cli.main(args) == 0
    assert len(sent) == 1
    assert read(folder / "report.json")["delivery"]["email"]["state"] == "duplicate"


def test_routine_earthquake_observations_take_one_slot_in_each_edition(tmp_path, monkeypatch):
    def observation(event, clock, location, intensity):
        at = f"2026-11-24T{clock}:00+09:00"
        hour, minute = clock.split(":")
        return {
            "source": "jma-earthquake", "link": primary.JMA_DOCUMENTS + f"{event}.xml", "issued_at": at,
            "match": {"event_id": event, "reported_at": at},
            "facts": [
                {"field": "発表内容", "value": f"11月24日{int(hour)}時{minute}分ごろ、{location}を震源とする地震があり、"
                 f"最大震度{intensity}が観測されました", "measured_at": at},
                {"field": "最大震度", "value": str(intensity), "measured_at": at},
            ],
        }

    morning_quakes = [observation("1", "04:12", "岩手県内陸北部", 1), observation("2", "06:09", "岩手県内陸北部", 2),
                      observation("3", "07:01", "福井県嶺北", 1), observation("4", "07:23", "秋田県沿岸北部", 5)]
    afternoon_quakes = [observation("5", "11:10", "岩手県内陸北部", 2), observation("6", "12:30", "岩手県内陸北部", 1)]
    other = release(LINK, "2026-11-24T07:30:00+09:00", "通学路の除雪", "対象の通学路では登校前の除雪を進める")
    other["match"]["article_url"] = "https://www.okinawatimes.co.jp/__newspaper_briefing_fixture__/road"

    def capture(settings, **kwargs):
        data = feeds.read_capture(FIXTURES / "morning.json", settings)
        seed = data.observations[0]
        for doc in [*morning_quakes, *afternoon_quakes]:
            at = feeds.timestamp(doc["issued_at"])
            identifier = "quake-" + doc["match"]["event_id"]
            link = f"https://news.web.nhk/__newspaper_briefing_fixture__/{identifier}"
            data.observations.append(replace(seed, title=f"震源・震度＝気象庁発表（{at.day}日{at.hour}時{at.minute:02d}分）",
                                             original_url=link, publisher_url=link, guid=identifier, published_at=at))
        return data

    monkeypatch.setattr(cli, "collect", capture)
    monkeypatch.setattr(cli, "collect_primary", lambda *args: primary.empty_primary("ok"))
    monkeypatch.setattr(publication, "collect_publication", lambda settings, start, end: {
        "sources": [{"id": "jma-earthquake", "state": "ok"}, {"id": "mlit-press", "state": "ok"}],
        "documents": [*morning_quakes, other, *afternoon_quakes],
    })
    root = tmp_path / "2026-11-24"
    for edition, as_of in (("morning", MORNING), ("evening", EVENING)):
        assert cli.main(["--preview", "--publication-mail", "--edition", edition, "--as-of", as_of,
                         "--output-dir", str(tmp_path), "--limit", "3"]) == 0

    morning = root / "morning/080000/delivery"
    evening = root / "evening/180000/delivery"
    rows = read(morning / "report.json")["stories"]
    assert len(rows) == 3
    assert any("最大震度5" in row["title"] for row in rows)
    assert any("登校前の除雪" in row["title"] for row in rows)
    roundup, = [row for row in rows if row["title"] == "地震速報まとめ"]
    assert len(roundup["earthquake_notices"]) == 3
    assert len(read(morning / "snapshot.json")["stories"]) == 5
    afternoon = read(evening / "report.json")
    assert afternoon["changes"]["新規"] == 1
    roundup, = afternoon["stories"]
    assert roundup["title"] == "地震速報まとめ" and len(roundup["earthquake_notices"]) == 2
    plain = (evening / "report.txt").read_text(encoding="utf-8")
    assert plain.count("地震速報まとめ") == 1
    assert "岩手県内陸北部" in plain and "11時10分" in plain and "12時30分" in plain
    assert "6時09分" not in plain and primary.JMA_DOCUMENTS + "5.xml" in plain
