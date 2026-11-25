from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
import json
from pathlib import Path
import unicodedata
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest
import yaml

from src.newspaper_briefing import __main__ as cli, delivery, feeds, output, stories
from src.newspaper_briefing.policy import Grant


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "newspaper_briefing"
END = feeds.timestamp("2026-11-24T08:00:00+09:00")
START = END - timedelta(hours=24)


@pytest.fixture
def settings():
    return feeds.load_settings(ROOT / "config" / "newspaper_briefing.json")


@pytest.fixture
def morning(settings):
    return feeds.read_capture(FIXTURES / "morning.json", settings)


def preview(tmp_path, edition="morning", extra=()):
    args = ["--edition", edition, "--offline-input", str(FIXTURES / f"{edition}.json"),
            "--output-dir", str(tmp_path), *extra]
    code = cli.main(args)
    path = tmp_path / "2026-11-24" / edition / ("080000" if edition == "morning" else "180000")
    return code, path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(morning, settings):
    _, articles, undated, popular, counts = stories.select_articles(morning, settings, START, END)
    return stories.rank(stories.cluster(articles, settings), settings, END), undated, popular, counts


def rss_fixture(settings, publisher, entries):
    media = settings.publishers[publisher]
    site = f"https://{media.hosts[0]}"
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = media.name
    for key, title, published in entries:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = f"{site}/__newspaper_briefing_fixture__/{key}"
        ET.SubElement(item, "guid").text = key
        ET.SubElement(item, "source", url=site).text = media.name
        if published:
            ET.SubElement(item, "pubDate").text = format_datetime(published)
    return ET.tostring(root, encoding="unicode")


def input_fixture(tmp_path, feeds):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"format": "rss-fixture", "captured_at": END.isoformat(), "feeds": feeds},
                               ensure_ascii=False), encoding="utf-8")
    return path


def test_quake_bulletins_share_one_preview_slot_without_hiding_separate_news(tmp_path, settings):
    titles = [
        "震度＝気象庁発表（24日7時40分）震度順・市区町村別 ：地震",
        "震度＝気象庁発表（24日7時31分）震度順・市区町村別 ：地震",
        "震源・震度＝気象庁発表（24日7時22分）",
        "宮城県で最大震度5弱 津波注意報を発表",
        "高校生の美術展で地域の作品33点を公開",
    ]
    items = [(str(i), title, END - timedelta(minutes=20 + i * 9)) for i, title in enumerate(titles)]
    offline = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", items)}])
    assert cli.main(["--preview", "--edition", "morning", "--offline-input", str(offline), "--limit", "3",
                     "--output-dir", str(tmp_path)]) == 0
    folder = tmp_path / "2026-11-24/morning/080000"
    report = read(folder / "report.json")
    assert {row["title"] for row in report["stories"]} == {"地震速報まとめ", *titles[3:]}
    roundup, = [row for row in report["stories"] if row["title"] == "地震速報まとめ"]
    assert len(roundup["earthquake_notices"]) == 3
    assert len(roundup["line"]["sources"]) == 3
    assert {row["title"] for row in read(folder / "ranking.json")} == set(titles)


def test_anniversary_articles_share_the_event_and_keep_other_fires_separate(tmp_path, settings):
    titles = [
        "ポルト湾 168人死亡の高層住宅火災から1年 抜き打ち検査を強化",
        "ポルト湾火災、遺族が対策の遅れを指摘 発生1年、死者168人",
        "リヴァ湾火災、遺族が対策の遅れを指摘 発生1年、死者168人",
        "ポルト湾火災、遺族が対策の遅れを指摘 発生2年、死者168人",
        "ポルト湾の倉庫で火災、3人が負傷",
    ]
    rows = [(str(i), title, END - timedelta(minutes=15 + i * 7)) for i, title in enumerate(titles)]
    offline = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", rows)}])
    status, folder = preview(tmp_path, extra=["--offline-input", str(offline)])
    assert status == 0
    grouped = {frozenset(item["headline"] for item in story["sources"]) for story in read(folder / "ranking.json")}
    assert grouped == {frozenset(titles[:2]), *(frozenset([title]) for title in titles[2:])}


def test_infection_reports_require_the_same_illness_and_location(tmp_path, settings):
    titles = [
        "英国 施設で働く6人がマールウイルス感染 2人死亡 保健当局が発表",
        "英でマールウイルス、6人感染し2人死亡",
        "マールウイルス、英国で6人感染2人死亡 当局は次回集計を発表へ",
        "仏でマールウイルス、6人感染し2人死亡",
        "英でミーロウイルス、6人感染し2人死亡",
    ]
    entries = [(f"infection-{index}", title, END - timedelta(minutes=index + 1))
               for index, title in enumerate(titles)]
    offline = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", entries)}])
    status, folder = preview(tmp_path, extra=["--offline-input", str(offline)])
    assert status == 0
    grouped = {frozenset(item["headline"] for item in story["sources"]) for story in read(folder / "ranking.json")}
    assert grouped == {frozenset(titles[:3]), *(frozenset([title]) for title in titles[3:])}


def test_attack_reports_require_the_same_actor_location_and_impact(tmp_path, settings):
    titles = [
        "アルバ首都の金融地区に攻撃 ネマ派、12人負傷",
        "アルバ首都の高層ビル、ネマ派が再び攻撃 12人負傷",
        "アルバ首都モラ金融街、ネマ攻撃１２人けが…XKタワー被害か",
        "ベルサ首都の金融地区に攻撃 ネマ派、12人負傷",
        "アルバ首都の金融地区に攻撃 レマ派、12人負傷",
        "アルバ首都モラ金融街、ネマ攻撃１２人けが…ZBタワー被害か",
    ]
    items = [(f"attack-{index}", title, END - timedelta(minutes=10 - index))
             for index, title in enumerate(titles)]
    offline = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", items)}])
    status, folder = preview(tmp_path, extra=["--offline-input", str(offline)])
    assert status == 0
    grouped = {frozenset(item["headline"] for item in story["sources"]) for story in read(folder / "ranking.json")}
    assert grouped == {frozenset(titles[:3]), *(frozenset([title]) for title in titles[3:])}


def test_evening_recognizes_the_same_outage_article_when_local_coverage_changes(tmp_path, settings):
    broad = "新潟 長野 宮城 大雪の影響で停電 1500戸に影響"
    at = END - timedelta(hours=3)
    morning = [("broad", broad, at), ("north", "宮城 大雪で停電続く 住民が避難", at)]
    capture = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", morning)}])
    status, folder = preview(tmp_path, extra=["--offline-input", str(capture)])
    assert status == 0
    story, = [row for row in read(folder / "snapshot.json")["stories"] if row["title"] == broad]
    assert story["outage_regions"] == ["宮城"]
    evening = [("broad", broad, at), ("west", "新潟 大雪で停電続く 住民が復旧待つ", END + timedelta(hours=2))]
    capture = input_fixture(tmp_path, [{"feed": "kahoku-search", "rss": rss_fixture(settings, "kahoku", evening)}])
    status, folder = preview(tmp_path, edition="evening", extra=[
        "--offline-input", str(capture), "--as-of", (END + timedelta(hours=10)).isoformat()])
    assert status == 0
    report = read(folder / "report.json")
    assert report["comparison_state"] == "matched"
    assert not any(row["title"] == broad for row in report["stories"])


@pytest.mark.parametrize(("legacy_config", "requested_limit", "limit"), [
    (False, None, 30),
    (True, None, 30),
    (False, 40, 40),
])
def test_full_publisher_corpus_reaches_ranking_before_the_mail_cutoff(tmp_path, settings, legacy_config,
                                                                    requested_limit, limit):
    recent = [(f"local-{n}", f"地域便り{n:03}", END - timedelta(minutes=n + 1)) for n in range(145)]
    old_priorities = [
        ("warning", "青葉市が沿岸に大津波警報を発表", START + timedelta(hours=2)),
        ("water", "白浜市で送水管が破裂 市内12000戸で断水", START + timedelta(hours=1)),
    ]
    input_path = input_fixture(tmp_path, [
        {"feed": "okinawa-search", "rss": rss_fixture(settings, "okinawa", [
            *recent, *old_priorities, ("undated", "時刻未定の地域便り", None),
            ("stale", "前日の地域便り", START - timedelta(minutes=1)),
        ])},
        {"feed": "okinawa-official", "rss": rss_fixture(settings, "okinawa", recent[:1])},
        {"feed": "google-top", "rss": rss_fixture(settings, "okinawa", recent[1:2])},
        {"feed": "asahi-search", "rss": rss_fixture(settings, "asahi", [
            ("exhibit", "公園で花の展示", START + timedelta(minutes=1)),
        ])},
        {"feed": "yomiuri-search", "rss": rss_fixture(settings, "yomiuri", [
            ("outside", "当日の対象外の話題", START - timedelta(minutes=1)),
        ])},
        {"feed": "shikoku-search", "failure": "HTTP 503"},
    ])
    extra = ["--offline-input", str(input_path), "--headlines-only"]
    if requested_limit is not None:
        extra.extend(("--limit", str(requested_limit)))
    if legacy_config:
        config = read(ROOT / "config/newspaper_briefing.json")
        config["collection"]["max_articles"] = 140
        next(p for p in config["publishers"] if p["id"] == "okinawa")["max_articles"] = 2
        legacy = tmp_path / "legacy.json"
        legacy.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        extra.extend(("--config", str(legacy)))
    code, path = preview(tmp_path, extra=extra)
    assert code == 0
    ranking = read(path / "ranking.json")
    report = read(path / "report.json")
    assert report["article_count"] == 148
    assert [row["rank"] for row in ranking] == list(range(1, 149))
    assert [row["id"] for row in ranking[:limit]] == [row["id"] for row in report["stories"]]
    assert {row["title"] for row in report["stories"][:2]} == {title for _, title, _ in old_priorities}
    assert all(row["sources"][0]["publisher"] == "okinawa" for row in report["stories"])
    assert all("summary" in story for story in report["stories"][:5])
    assert all("summary" not in story and story["line"]["sources"] for story in report["stories"][5:])
    rows = {row["id"]: row for row in read(path / "diagnostics.json")["publishers"]}
    fields = ("received", "in_window", "articles", "ranked_stories", "selected_stories", "published_stories")
    assert tuple(rows["okinawa"][field] for field in fields) == (151, 149, 147, 147, limit, limit)
    assert rows["okinawa"]["undated"] == 1 and rows["okinawa"]["possible_cap"]
    assert rows["okinawa"]["discovery_received"] == 1
    assert rows["asahi"]["publication_stage"] == "selection" and rows["asahi"]["ranked_stories"] == 1
    assert rows["asahi"]["published_stories"] == 0
    assert rows["yomiuri"]["publication_stage"] == "collection" and rows["yomiuri"]["received"] == 1
    assert rows["yomiuri"]["in_window"] == rows["yomiuri"]["articles"] == 0
    assert rows["shikoku"]["publication_stage"] == "collection" and rows["shikoku"]["sources"][0]["failure"] == "HTTP 503"
    snapshot = path / "snapshot.json"
    assert len(read(snapshot)["stories"]) == limit
    if requested_limit is not None:
        code, evening = preview(tmp_path, "evening", extra=[
            "--offline-input", str(input_path), "--as-of", (END + timedelta(hours=10)).isoformat(),
            "--snapshot", str(snapshot), "--limit", "5",
        ])
        assert code == 0
        result = read(evening / "report.json")
        assert result["comparison_state"] == "matched" and result["stories"] == []


def test_selected_story_counts_only_the_publishers_cited_for_each_fact(tmp_path, settings):
    base = "青葉市営バスの新しい運行計画を正式決定"
    inputs = [
        ("nhk-main", "nhk", base, 1),
        ("asahi-search", "asahi", base + " 1000人に影響", 2),
        ("nikkei-search", "nikkei", base + " 午前9時記者会見予定", 3),
        ("yomiuri-search", "yomiuri", base, 4),
    ]
    input_path = input_fixture(tmp_path, [
        {"feed": feed, "rss": rss_fixture(settings, publisher, [(publisher, title, END - timedelta(minutes=age))])}
        for feed, publisher, title, age in inputs
    ])
    code, path = preview(tmp_path, extra=["--offline-input", str(input_path), "--headlines-only"])
    assert code == 0
    [story] = read(path / "report.json")["stories"]
    assert story["publisher_count"] == 4
    sections = story["summary"]["sections"]
    assert sections["what"]["sources"][0]["publisher"] == "nhk"
    assert "1000人に影響" in sections["why"]["text"] and sections["why"]["sources"][0]["publisher"] == "asahi"
    assert "午前9時記者会見予定" in sections["next"]["text"] and sections["next"]["sources"][0]["publisher"] == "nikkei"
    rows = {row["id"]: row for row in read(path / "diagnostics.json")["publishers"]}
    for publisher in ("nhk", "asahi", "nikkei"):
        assert rows[publisher]["selected_stories"] == rows[publisher]["published_stories"] == 1
    assert rows["yomiuri"]["selected_stories"] == 1 and rows["yomiuri"]["published_stories"] == 0
    assert rows["yomiuri"]["publication_stage"] == "selection"
    mail = read(path / "mail.json")["text"]
    for key in ("what", "why", "next"):
        assert sections[key]["sources"][0]["link"] in mail
    assert "読売新聞" not in mail


@pytest.mark.parametrize(("headlines", "impact"), [
    pytest.param(
        (
            "11月23日 キーウ住宅攻撃で11人死亡、22時間ぶり79歳女性救出",
            "11月23日 キーウ住宅攻撃、22時間ぶり79歳女性救出",
            "11月24日 キーウ住宅攻撃で複数の負傷者、救助続く",
            "キーウで停戦に向けた外相会談",
        ),
        "死者は11人", id="international",
    ),
    pytest.param(
        (
            "11月23日 汐見博物館で一般公開始まり、復元展示に来場者",
            "11月23日 汐見博物館で一般公開始まり",
            "11月24日 汐見博物館で別の展示室の一般公開始まり",
            "汐見博物館が所蔵品の保存について専門家を招き意見交換",
        ),
        "一般公開始まり", id="culture",
    ),
    pytest.param(
        (
            "11月23日 青浜線の信号トラブルで3本が運休",
            "11月23日 青浜線の信号トラブルで3本運休",
            "11月24日 青浜線の信号トラブルで4本運休",
            "青浜の地域交通を巡り自治体が住民説明会を開催",
        ),
        "3本", id="transport",
    ),
])
def test_one_headline_and_source_link_per_event_in_the_email(tmp_path, settings, headlines, impact):
    reports = zip(("sankei", "47news", "asahi", "tokyo"), headlines, strict=True)
    input_path = input_fixture(tmp_path, [
        {"feed": f"{publisher}-search", "rss": rss_fixture(settings, publisher, [
            (publisher, title, END - timedelta(minutes=30 - index)),
        ])}
        for index, (publisher, title) in enumerate(reports)
    ])
    code, folder = preview(tmp_path, extra=["--offline-input", str(input_path), "--headlines-only"])
    assert code == 0
    report, mail = read(folder / "report.json"), read(folder / "mail.json")
    assert len(report["stories"]) == 3
    [story] = [item for item in report["stories"] if item["title"] in headlines[:2]]
    assert {source["publisher"] for source in story["sources"]} == {"sankei", "47news"}
    title, link = story["title"], story["title_source"]["link"]
    assert mail["text"].count(title) == mail["html"].count(title) == 1
    assert mail["text"].count(link) == mail["html"].count(link) == 1
    assert "何が起きたか" in mail["text"] and impact in story["summary"]["sections"]["why"]["text"]
    assert mail["text"].index("朝の要点") < mail["text"].index(title)
    assert all(title in mail["text"] for title in headlines[2:])


def test_full_headline_in_impact_is_displayed_once_even_with_different_widths(tmp_path, settings):
    title = "青浜自動車道 通行止め 汐見IC～北岬JCT（上り）"
    input_path = input_fixture(tmp_path, [{
        "feed": "nhk-main", "rss": rss_fixture(settings, "nhk", [("road", title, END - timedelta(minutes=3))]),
    }])
    code, folder = preview(tmp_path, extra=["--offline-input", str(input_path), "--headlines-only"])
    assert code == 0
    [story] = read(folder / "report.json")["stories"]
    impact = story["summary"]["sections"]["why"]
    assert "汐見IC" in impact["text"] and impact["sources"] == [story["title_source"]]
    mail = read(folder / "mail.json")
    normalized_title = unicodedata.normalize("NFKC", title)
    assert unicodedata.normalize("NFKC", mail["text"]).count(normalized_title) == 1
    assert unicodedata.normalize("NFKC", mail["html"]).count(normalized_title) == 1


def test_repeated_source_links_do_not_bypass_the_email_permission_check(monkeypatch):
    source = {"publisher": "sample", "name": "例示新聞", "link_kind": "publisher", "kind": "headline",
              "link": "https://example.invalid/news/1", "email": True}
    items = [
        {"kind": "article", "text": "見出し", "story_id": "one", "sources": [source]},
        {"kind": "bullet", "text": "補足の事実", "story_id": "one",
         "sources": [source | {"kind": "rss_description", "email": False}]},
    ]
    monkeypatch.setattr(output, "issue_content", lambda _: items)
    _, mail, cited = output.render({"subject": "朝版", "edition": "morning", "as_of": END,
                                     "collection_state": "ok", "offline": False, "stories": [{"title_source": source}]})
    assert "補足の事実" in mail["text"] and mail["text"].count(source["link"]) == 1
    assert mail["link_count"] == cited["sample"] == 1
    assert mail["email_allowed"] is False


def test_outage_coverage_unifies_local_details_without_joining_other_regions(tmp_path, settings):
    reports = [
        ("nhk-main", "nhk", "長野県 雪の影響で停電続く 半数は復旧", 110),
        ("shinmai-search", "shinmai", "入浴できず 長野市や長野県北部で停電解消されず", 100),
        ("asahi-search", "asahi", "岐阜県と長野県で大雪、停電相次ぐ", 90),
        ("chunichi-search", "chunichi", "岐阜県で大雪のため停電 復旧進む", 80),
        ("nikkei-search", "nikkei", "長野県の変電所で点検に伴う計画停電", 70),
        ("kobe-search", "kobe", "福島県猪苗代町の水族館、大雪で全館停電", 60),
        ("47news-search", "47news", "大雪の福島県、停電復旧のため自衛隊派遣へ", 40),
    ]
    input_path = input_fixture(tmp_path, [
        {"feed": feed, "rss": rss_fixture(settings, publisher, [(publisher, title, END - timedelta(minutes=age))])}
        for feed, publisher, title, age in reports
    ])
    code, folder = preview(tmp_path, extra=["--offline-input", str(input_path), "--headlines-only"])
    assert code == 0
    stories_by_source = {frozenset(s["publisher"] for s in row["sources"]) for row in read(folder / "ranking.json")}
    assert stories_by_source == {
        frozenset(("nhk", "shinmai", "asahi")), frozenset(("chunichi",)),
        frozenset(("nikkei",)), frozenset(("kobe", "47news")),
    }
    fukushima = next(story for story in read(folder / "report.json")["stories"] if "自衛隊" in story["title"])
    consequence = fukushima["summary"]["sections"]["why"]
    assert "水族館" in consequence["text"] and consequence["sources"][0]["publisher"] == "kobe"


def test_fire_merge_retrieves_paraphrases_but_keeps_other_fires_separate(tmp_path, settings):
    titles = [
        "松山 住宅が全焼 男女2人遺体発見",
        "愛媛・松山で住宅火災、2人死亡",
        "松山市の住宅火災で2遺体発見、姉弟か",
        "松山市で倉庫火災、2人死亡",
        "愛媛 火災2件 3人死亡",
        "今治市の住宅火災で2人死亡",
    ]
    input_path = input_fixture(tmp_path, [
        {"feed": "okinawa-official", "rss": rss_fixture(settings, "okinawa", [
            (str(index), title, END - timedelta(minutes=90 - index))
            for index, title in enumerate(titles)
        ])}
    ])
    code, folder = preview(tmp_path, extra=["--offline-input", str(input_path), "--headlines-only"])
    assert code == 0
    groups = {frozenset(source["headline"] for source in row["sources"]) for row in read(folder / "ranking.json")}
    assert groups == {frozenset(titles[:3]), *(frozenset([title]) for title in titles[3:])}
    assert "複数の出来事の概況" in read(folder / "mail.json")["text"]


def test_parallel_outages_and_repeat_incidents_have_conflicting_anchors():
    positive = stories.features("長野県の暴風雪で停電続く 復旧作業を開始")
    planned = stories.features("長野県の変電所点検で停電、復旧まで2時間")
    assert stories.similarity(positive, planned) == 0
    gujo = stories.features("岐阜県郡上市で大雪による停電続く")
    takayama = stories.features("岐阜県高山市で大雪による停電続く")
    assert stories.similarity(gujo, takayama) == 0
    morning = stories.features("松山市で住宅火災、2人死亡 午前3時に出火")
    evening = stories.features("松山市で住宅火災、2人死亡 午後9時に出火")
    assert stories.similarity(morning, evening) == 0


@pytest.mark.parametrize("mode", ("no_permission", "unavailable", "declined", "bad_anchor", "bad_id", "matched"))
def test_event_ai_only_matches_approved_headlines_and_checks_the_returned_pair(tmp_path, settings, monkeypatch, mode):
    from src import openai_json_client
    from src.newspaper_briefing import event_ai

    titles = [
        ("asahi", "青葉市の港南研究所、湾で新種の海藻を発見"),
        ("nikkei", "海藻の新種を確認 港南研究所が発表 青葉市"),
        ("kobe", "青葉市 港南研究所で新種のトンボを報告"),
    ]
    input_path = input_fixture(tmp_path, [
        {"feed": f"{publisher}-search", "rss": rss_fixture(settings, publisher, [
            (publisher, title, END - timedelta(minutes=30 - index)),
        ])}
        for index, (publisher, title) in enumerate(titles)
    ])
    capture = feeds.read_capture(input_path, settings)
    _, articles, _, _, _ = stories.select_articles(capture, settings, START, END)
    initial = stories.cluster(articles, settings)
    assert len(initial) == 3
    for publisher in ("asahi",) if mode == "no_permission" else ("asahi", "nikkei"):
        settings.publishers[publisher] = replace(settings.publishers[publisher], allow_ai=True)
        feed = settings.feeds[f"{publisher}-search"]
        settings.grants["headline", feed.id] = Grant(
            id=feed.id, kind="headline", url=feed.url, checked_on="2026-11-24",
            basis_urls=["https://example.invalid/permission"], constraints="テスト用",
            permissions={"acquire": True, "save": True, "ai": True, "email": False},
        )
    for var in ("NEWSPAPER_BRIEFING_OPENAI_API_KEY", "NEWSPAPER_BRIEFING_OPENAI_MODEL"):
        if mode == "unavailable":
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, "test-event-matching")
    requests = []

    class FakeClient:
        def __init__(self, _key):
            pass

        def generate_structured_json(self, **options):
            [pair] = json.loads(options["user_prompt"])
            requests.append(pair)
            assert pair["id"] == "p1"
            assert {article["headline"] for article in pair["articles"]} == {titles[0][1], titles[1][1]}
            assert "港南研究所" in pair["shared_details"]
            assert all(detail in stories.title_text(article["headline"])
                       for detail in pair["shared_details"] for article in pair["articles"])
            return {"pairs": [{"id": "unexpected-id" if mode == "bad_id" else pair["id"],
                               "same_event": mode != "declined",
                               "shared_detail": "さくら科学館" if mode == "bad_anchor" else "港南研究所"}]}

    monkeypatch.setattr(openai_json_client, "OpenAIJsonClient", FakeClient)
    updated, status = event_ai.refine(initial, settings, enabled=True)
    assert len(requests) == (0 if mode in {"no_permission", "unavailable"} else 1)
    assert status["state"] == {"no_permission": "no_licensed_pair", "unavailable": "unavailable",
                               "bad_anchor": "invalid", "bad_id": "invalid"}.get(mode, "ok")
    assert status["merged"] == (1 if mode == "matched" else 0)
    assert len(updated) == (2 if mode == "matched" else 3)
    assert sum(len(group.articles) for group in updated) == 3
    if mode == "matched":
        monkeypatch.setattr(cli, "load_settings", lambda *_: settings)
        monkeypatch.setattr(cli, "collect", lambda _: capture)
        code = cli.main(["--preview", "--ai", "--edition", "morning", "--headlines-only",
                         "--as-of", END.isoformat(), "--output-dir", str(tmp_path / "online-preview")])
        assert code == 0
        folder = tmp_path / "online-preview/2026-11-24/morning/080000"
        assert read(folder / "diagnostics.json")["event_matching_ai"] == status
        assert len(read(folder / "ranking.json")) == 2


@pytest.mark.parametrize("case", ["agree", "disagree", "unsupported"])
def test_event_ai_groups_several_international_headlines_without_a_domestic_location(
        tmp_path, settings, monkeypatch, case):
    from src import openai_json_client
    from src.newspaper_briefing import event_ai

    titles = {
        "asahi": "セリア国立劇場で長期改修が完了 公演が再開",
        "nikkei": "長期改修を終え、記念公演へ セリア国立劇場",
        "kobe": "旧市街に観客戻る セリア国立劇場、長期改修後の初公演",
    }
    fixture = []
    for publisher, headline in titles.items():
        feed = settings.feeds[f"{publisher}-search"]
        settings.publishers[publisher] = replace(settings.publishers[publisher], allow_ai=True)
        settings.grants["headline", feed.id] = Grant(
            id=feed.id, kind="headline", url=feed.url, checked_on="2026-11-24",
            basis_urls=["https://example.invalid/permission"], constraints="テスト用",
            permissions={"acquire": True, "save": True, "ai": True, "email": True},
        )
        fixture.append({"feed": feed.id, "rss": rss_fixture(settings, publisher, [(publisher, headline, END)])})

    capture = feeds.read_capture(input_fixture(tmp_path, fixture), settings)
    _, articles, _, _, _ = stories.select_articles(capture, settings, START, END)
    initial = [stories.Story(article.id, [article]) for article in articles]
    assert not any(stories.features(article.title).places for article in articles)
    calls = []

    def compare(_client, user_prompt, **kwargs):
        payload = json.loads(user_prompt)
        calls.append(payload)
        assert len(payload) == 3
        assert all(set(article) == {"headline", "published_at"} for pair in payload for article in pair["articles"])
        results = []
        for pair in payload:
            headlines = {article["headline"] for article in pair["articles"]}
            assert len(headlines) == 2 and headlines <= set(titles.values())
            different = headlines == {titles["asahi"], titles["kobe"]}
            rejected = different and case == "disagree"
            anchor = "セリア国" if different and case == "unsupported" else "国立劇場"
            results.append({"id": pair["id"], "same_event": not rejected, "shared_detail": "" if rejected else anchor})
        return {"pairs": results}

    monkeypatch.setenv("NEWSPAPER_BRIEFING_OPENAI_API_KEY", "test")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_OPENAI_MODEL", "test")
    monkeypatch.setattr(openai_json_client.OpenAIJsonClient, "generate_structured_json", compare)
    updated, status = event_ai.refine(initial, settings, enabled=True)
    assert len(calls) == 1
    if case != "agree":
        assert status["merged"] == 1
        assert not any({"asahi", "kobe"} <= {article.publisher for article in group.articles} for group in updated)
    else:
        assert status["merged"] == 2
        assert [{article.publisher for article in group.articles} for group in updated] == [set(titles)]


def test_source_alias_and_host_and_description(morning, settings):
    assert len(settings.publishers) == 19
    feed = settings.feeds["asahi-search"]
    assert feed.url.endswith("q=site%3Aasahi.com+when%3A1d&hl=ja&gl=JP&ceid=JP%3Aja")
    assert not any("系列サイト" in row.title for row in morning.observations)
    assert not any(row.description for row in morning.observations)
    assert not any(row.updated_at for row in morning.observations)
    assert all(row.published_basis == "rss.pubDate" for row in morning.observations if row.published_at)
    affiliate = '<rss version="2.0"><channel><title>RSS</title><item><title>ニュース - 中日しごと情報</title>' \
                '<link>https://news.google.com/rss/articles/fake</link>' \
                '<source url="https://job.chunichi.co.jp/">中日しごと情報</source></item></channel></rss>'
    result, _ = feeds.parse_feed(settings.feeds["chunichi-search"], affiliate.encode(), settings, END)
    assert result.received == result.excluded == 1


def test_canonical_article_dedupe_and_editorial_vote(morning, settings):
    ranked, _, _, _ = evaluate(morning, settings)
    row = ranked[0]
    assert "震度6強" in row["main"].title
    assert len(row["story"].articles) == 1
    assert {o.feed for o in row["main"].records} == {"nhk-main", "nhk-society", "nhk-search", "google-top"}
    assert row["main"].link.startswith("https://news.web.nhk/")
    assert row["parts"]["impact"].points == 40
    assert row["parts"]["editorial"].points == 5
    assert row["publisher_count"] == row["independent_count"] == 1
    assert feeds.url_key("https://www.jiji.com/jc/article?k=1") != feeds.url_key("https://www.jiji.com/jc/article?k=2")
    discovery = next(row for row in ranked if "小劇場" in row["main"].title)
    assert discovery["publisher_count"] == 1
    assert discovery["independent_count"] == discovery["parts"]["editorial"].points == 0


def test_gallery_url_does_not_replace_the_article_headline(settings):
    title = "光浜市で断水、給水所を開設"
    item = '<item><title>{} - 西日本新聞me</title><link>https://news.google.com/rss/articles/{}</link>' \
           '<source url="https://www.nishinippon.co.jp/">西日本新聞me</source></item>'
    raw = ('<rss version="2.0"><channel><title>RSS</title>'
           + item.format(title, "article")
           + item.format(f"給水所に並ぶ人たち - {title} - 写真・画像(2/2)", "gallery")
           + '</channel></rss>')
    result, rows = feeds.parse_feed(settings.feeds["nishinihon-search"], raw.encode(), settings, END)
    assert result.excluded == 1 and [row.title for row in rows] == [title]


def test_syndication_group_count_and_independent_diminishing(morning, settings):
    ranked, _, _, _ = evaluate(morning, settings)
    wire = next(r for r in ranked if "倉庫全焼" in r["main"].title)
    shared = next(r for r in ranked if "東浜市議会" in r["main"].title)
    assert wire["publisher_count"] == shared["publisher_count"] == 2
    assert wire["independent_count"] == shared["independent_count"] == 1
    assert wire["parts"]["breadth"].reasons[0]["reason"] == "kyodo"
    assert shared["parts"]["breadth"].reasons[0]["reason"] == "chunichi"

    first, second = wire["story"].articles
    paraphrase = replace(second.title_record, title="白浜市の倉庫火災で3人が負傷", reporting_group="different")
    second = replace(second, records=(paraphrase,), title_record=paraphrase)
    third_record = replace(paraphrase, title="白浜市で倉庫が全焼し3人負傷", reporting_group="third")
    third = replace(second, id="third", records=(third_record,), title_record=third_record)
    two = stories.assess(stories.Story("two", [first, second]), settings, END)["parts"]["breadth"].points
    three = stories.assess(stories.Story("three", [first, second, third]), settings, END)["parts"]["breadth"].points
    one = wire["parts"]["breadth"].points
    assert 0 < three - two < two - one < one


@pytest.mark.parametrize("first,second", [
    ("青葉県 光浜市で倉庫全焼、3人が負傷", "青葉県 西浜市で倉庫全焼、3人が負傷"),
    ("震度＝気象庁発表（4日10時37分）震度順・市区町村別", "震度＝気象庁発表（3日18時21分）震度順・市区町村別"),
    ("首相が輸入米の関税を見直す方針を正式決定", "首相が輸入小麦の関税を見直す方針を正式決定"),
    ("青井氏、青葉市長選で当選確実", "青井氏、港南市長選で当選確実"),
    ("光浜市で倉庫全焼 3人が負傷、午前2時に出火", "光浜市で倉庫全焼 3人が負傷、午前7時に出火"),
    ("ナギサテックCEOに花井氏復帰", "NY株反発 ナギサテックCEO花井氏復帰で関連株も上昇"),
])
def test_similar_events_are_kept_separate(first, second):
    assert stories.similarity(stories.features(first), stories.features(second)) == 0


def test_fire_and_seismic_paraphrases():
    assert stories.similarity(
        stories.features("光浜市 住宅が全焼 男女2人遺体発見"),
        stories.features("青葉県・光浜市で住宅火災、2人死亡"),
    )
    assert stories.similarity(
        stories.features("震度＝気象庁発表（5日2時55分）震度順・市区町村別"),
        stories.features("震源・震度＝気象庁発表（５日２時５５分）"),
    )


def test_named_executive_and_venue_in_different_phrasings():
    assert stories.similarity(
        stories.features("ナギサテックCEO 辞任の花井氏が復帰へ 株価が上昇"),
        stories.features("花井氏、ＣＥＯに復帰 取締役決定とナギサテックが発表"),
    )
    assert stories.similarity(
        stories.features("復元した夕凪城正殿で一般公開始まり来場者でにぎわう"),
        stories.features("5年ぶりに夕凪城正殿を一般公開、式典に地元の高校生"),
    )


def test_regional_story_and_date_boundary(morning, settings):
    ranked, undated, _, counts = evaluate(morning, settings)
    local = next(r for r in ranked if "夏浜橋" in r["main"].title)
    assert local["main"].publisher == "okinawa" and local["category"] == "社会"
    assert local["main"].published_at.isoformat() == "2026-11-23T23:58:00+09:00"
    assert len(undated) == 1 and undated[0].published_at is None and not undated[0].published_basis
    assert feeds.feed_date("2026-11-24") is None
    assert feeds.feed_date("Tue, 24 Nov 2026 00:00:00") is None
    result = feeds.diagnostics(morning, settings, counts)
    nhk = next(s for r in result["publishers"] for s in r["sources"] if s["feed"] == "nhk-society")
    assert nhk["outside_window"] == nhk["future"] == nhk["undated"] == 1


def test_jiji_popularity_and_rdf_never_supply_articles(morning, settings):
    ranked, _, popular, _ = evaluate(morning, settings)
    jiji = next(r for r in ranked if r["main"].publisher == "jiji")
    assert output.story_row(jiji, settings, popular)["publisher_popularity"]
    assert jiji["parts"]["editorial"].missing
    assert all("ランキングにだけ" not in r["main"].title for r in ranked)
    rdf = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <channel><title>Ranking</title></channel><item>
        <title>News</title><link>https://www.jiji.com/jc/article?k=test</link>
        <dc:date>2026-11-24T07:00:00+09:00</dc:date></item></rdf:RDF>"""
    result, rows = feeds.parse_feed(settings.feeds["jiji-popular"], rdf, settings, END)
    assert not result.failure and rows[0].published_basis == "rdf.dc:date"
    result, rows = feeds.parse_feed(settings.feeds["jiji-popular"], b"<html><title>bad</title></html>", settings, END)
    assert result.failure and not rows


def test_evening_diff_and_replay_without_any_network(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("offline preview reached the network")

    monkeypatch.setattr(feeds.requests, "get", unexpected)
    assert preview(tmp_path)[0] == 0
    code, path = preview(tmp_path, "evening")
    report = read(path / "report.json")
    morning_report = read(tmp_path / "2026-11-24/morning/080000/report.json")
    assert code == 0 and report["baseline_as_of"].endswith("08:00:00+09:00")
    assert Counter(s["update"] for s in report["stories"]) == {"続報": 2, "新規": 2}
    by_title = {s["title"]: s for s in report["stories"]}
    election = next(s for s in by_title.values() if "青井氏" in s["title"])
    assert election["id"] == next(s["id"] for s in morning_report["stories"] if "青葉市長選" in s["title"])
    assert any(fact["before"]["value"] == "3人" and fact["after"]["value"] == "5人"
               for s in by_title.values() for fact in s["update_evidence"] if fact["after"]["field"] == "負傷者")
    assert "沖浜市" not in "".join(by_title)
    assert stories.material_facts("けが人3人") == stories.material_facts("3人が負傷")
    code, _ = preview(tmp_path, "evening", ("--offline-input", str(path / "capture.json")))
    assert code == 0 and report == read(path / "report.json")


def test_evening_no_prior_snapshot_custom_window(tmp_path):
    code, path = preview(tmp_path, "evening", ("--snapshot", str(tmp_path / "missing"), "--window-hours", "3"))
    data = read(path / "report.json")
    assert code == 0 and "差分比較なし" in data["comparison"]
    assert data["window_start"] == "2026-11-24T15:00:00+09:00"
    assert all(not story["update"] for story in data["stories"])


def test_jst_day_drives_subject_paths_and_replay(tmp_path):
    utc = "2026-11-23T23:00:00Z"
    code, path = preview(tmp_path, extra=("--as-of", utc))
    assert code == 0 and (path / "report.json").is_file()
    report = read(path / "report.json")
    assert report["subject"] == "新聞横断ニュース｜朝｜2026-11-24"
    assert report["as_of"] == "2026-11-24T08:00:00+09:00"
    assert preview(tmp_path, extra=("--as-of", utc, "--offline-input", str(path / "capture.json")))[0] == 0
    assert report == read(path / "report.json")


def test_same_url_substantive_update_from_an_older_article(morning, settings):
    q = next(o for o in morning.observations if "倉庫全焼" in o.title and o.publisher == "okinawa")
    newer = END + timedelta(hours=10)
    old = replace(q, published_at=START - timedelta(hours=3))
    update = replace(old, title=old.title.replace("3人", "5人"), updated_at=newer - timedelta(minutes=5))
    capture = replace(morning, observations=[update])
    _, articles, _, _, _ = stories.select_articles(capture, settings, newer - timedelta(hours=24), newer)
    assert articles[0].title_record.updated_at == update.updated_at
    assert stories.ranked_time(update, newer) == update.updated_at


def test_partial_and_total_outage_diagnostics(tmp_path):
    code, path = preview(tmp_path)
    report = read(path / "report.json")
    assert code == 0 and report["collection_state"] == "partial"
    shikoku = next(r for r in read(path / "diagnostics.json")["publishers"] if r["id"] == "shikoku")
    assert shikoku["state"] == "missing" and shikoku["sources"][0]["failure"] == "HTTP 503"
    fixture = read(FIXTURES / "morning.json")
    fixture["feeds"] = [r for r in fixture["feeds"] if r["feed"] in {"google-top", "shikoku-search"}]
    fail = tmp_path / "fail.json"
    fail.write_text(json.dumps(fixture), encoding="utf-8")
    code, path = preview(tmp_path / "failed", extra=("--offline-input", str(fail)))
    assert code == 2 and read(path / "report.json")["collection_state"] == "failed"
    assert not (path / "snapshot.json").exists()
    assert "有効な媒体フィードを取得できません" in (path / "report.html").read_text(encoding="utf-8")


def test_feed_retry_caps_and_supplement_budget(settings, monkeypatch):
    responses = iter([429, 200])
    sample = read(FIXTURES / "morning.json")["feeds"][0]["rss"].encode()

    class Response:
        def __enter__(self):
            self.status_code = next(responses)
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            return iter([sample])

    monkeypatch.setattr(feeds.requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(feeds, "sleep", lambda _: None)
    result, rows = feeds.fetch_feed(settings.feeds["nhk-main"], settings)
    assert result.http_status == 200 and len(rows) == 2

    p = settings.publishers["asahi"]
    base = settings.feeds["asahi-search"]
    sources = [base, *(replace(base, id=f"{base.id}-{i}") for i in (1, 2))]
    environment = replace(settings, publishers={p.id: replace(p, supplemental_terms=("科学", "文化"))},
                          feeds={f.id: f for f in sources}, max_supplemental_feeds=1)
    calls = []

    def capped(feed, _):
        calls.append(feed.id)
        return feeds.FeedResult(feed.id, END, received=100, possible_cap=True), []

    monkeypatch.setattr(feeds, "fetch_feed", capped)
    feeds.collect(environment, verified_only=False)
    assert calls == ["asahi-search", "asahi-search-1"]


def test_score_ties_are_deterministic(settings, monkeypatch):
    def assess(subject, *_):
        points, latest, key = subject
        return {"story": stories.Story(key, []), "score": 35,
                "latest": END + timedelta(minutes=latest), "parts": {"impact": stories.ScorePart(points, 40, (), False)}}

    monkeypatch.setattr(stories, "assess", assess)
    ranked = stories.rank([(10, 0, "y"), (10, 0, "a"), (20, -10, "z"), (10, 1, "b")], settings, END)
    assert [r["story"].id for r in ranked] == ["z", "b", "a", "y"]


def test_headline_and_link_fallback_and_no_delivery(tmp_path, monkeypatch):
    def unexpected(*_, **__):
        pytest.fail("offline input or preview reached a delivery client")

    monkeypatch.setattr(delivery, "prepare_email", unexpected)
    monkeypatch.setattr(delivery, "prepare_notion", unexpected)
    code, path = preview(tmp_path)
    report = read(path / "report.json")
    assert code == 0 and all(r["state"] == "preview" for r in report["delivery"].values())
    assert all(s["summary"]["status"] == "見出しベース" for s in report["stories"][:5])
    assert all("summary" not in s for s in report["stories"][5:])
    assert all(s["summary"]["sections"]["what"]["sources"] for s in report["stories"][:5])
    page = (path / "report.html").read_text(encoding="utf-8")
    assert "Googleニュース経由" in page and "何が起きたか" in page
    code, path = preview(tmp_path, extra=("--send-email", "--write-notion", "--ai"))
    report = read(path / "report.json")
    assert code == 3 and all(r["state"] == "blocked" for r in report["delivery"].values())
    assert report["ai_state"] == "disabled"


def test_delivery_claim_is_atomic_and_unknown_blocks_resend(tmp_path):
    ledger = tmp_path / "delivery.sqlite3"
    attempts = []
    key = ("2026-11-24", "morning", "email")

    def interrupted():
        attempts.append(1)
        raise TimeoutError

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: delivery.reserve_and_send(ledger, key, interrupted), range(2)))
    assert {r["state"] for r in results} == {"unknown", "duplicate"}
    assert len(attempts) == 1
    assert delivery.reserve_and_send(ledger, key, interrupted)["previous_state"] == "unknown"
    for other in [("2026-11-24", "morning", "notion"), ("2026-11-24", "evening", "email"), ("2026-11-25", "morning", "email")]:
        assert delivery.reserve_and_send(ledger, other, lambda: None)["state"] == "sent"


def test_delivery_reuses_existing_gmail_account_for_the_briefing_recipient(monkeypatch):
    for name in ("SMTP_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(f"NEWSPAPER_BRIEFING_{name}", raising=False)
    monkeypatch.setenv("MAIL_FROM", "sender@example.invalid")
    monkeypatch.setenv("MAIL_PASSWORD", "test-password")
    monkeypatch.setenv("MAIL_TO", "legacy@example.invalid")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_SMTP_TO", "briefing@example.invalid")
    smtp = MagicMock()
    session = smtp.return_value.__enter__.return_value
    session.send_message.return_value = {}
    monkeypatch.setattr(delivery.smtplib, "SMTP", smtp)

    send = delivery.prepare_email({"subject": "新聞横断ニュース｜朝｜2026-11-24"}, "確認済み本文", "<p>確認済み本文</p>")
    smtp.assert_not_called()
    send()

    smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    session.starttls.assert_called_once()
    session.login.assert_called_once_with("sender@example.invalid", "test-password")
    args, envelope = session.send_message.call_args
    assert envelope == {"from_addr": "sender@example.invalid", "to_addrs": ["briefing@example.invalid"]}
    assert args[0]["To"] == "briefing@example.invalid"
    assert args[0].get_body(preferencelist=("plain",)).get_content().strip() == "確認済み本文"


def test_custom_smtp_does_not_reuse_the_existing_gmail_password(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "sender@example.invalid")
    monkeypatch.setenv("MAIL_PASSWORD", "test-password")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_SMTP_FROM", "other-sender@example.invalid")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_SMTP_USERNAME", "other-sender@example.invalid")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_SMTP_TO", "briefing@example.invalid")
    monkeypatch.delenv("NEWSPAPER_BRIEFING_SMTP_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="NEWSPAPER_BRIEFING_SMTP_PASSWORD"):
        delivery.prepare_email({"subject": "テスト"}, "body", "<p>body</p>")


def test_delivery_settings_do_not_use_legacy_destinations(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "sender@example.invalid")
    monkeypatch.setenv("MAIL_PASSWORD", "test-password")
    monkeypatch.setenv("MAIL_TO", "legacy@example.invalid")
    monkeypatch.setenv("NOTION_ARTICLE_DB_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("NOTION_TOKEN", "legacy-test")
    for name in ("SMTP_FROM", "SMTP_TO", "NOTION_DATABASE_ID", "NOTION_TOKEN"):
        monkeypatch.delenv(f"NEWSPAPER_BRIEFING_{name}", raising=False)
    with pytest.raises(ValueError, match="NEWSPAPER_BRIEFING_SMTP_TO"):
        delivery.prepare_email({"subject": "テスト"}, "body", "<p>body</p>")
    with pytest.raises(ValueError, match="NEWSPAPER_BRIEFING_NOTION_TOKEN"):
        delivery.prepare_notion({"subject": "テスト"})


def test_notion_uses_only_the_configured_database_and_submits_once(tmp_path, monkeypatch):
    _, path = preview(tmp_path)
    report = read(path / "report.json")
    for key in ("window_start", "as_of"):
        report[key] = feeds.timestamp(report[key])
    database_id, source_id = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_NOTION_TOKEN", "synthetic-test")
    monkeypatch.setenv("NEWSPAPER_BRIEFING_NOTION_DATABASE_ID", database_id)
    monkeypatch.delenv("NEWSPAPER_BRIEFING_NOTION_DATA_SOURCE_ID", raising=False)
    calls = []

    def fake(method, path, _token, **kwargs):
        calls.append((method, path))
        if path == f"databases/{database_id}":
            return {"data_sources": [{"id": source_id}]}
        if path == f"data_sources/{source_id}":
            return {"properties": {"名前": {"type": "title"}}}
        assert kwargs["json"]["parent"]["data_source_id"] == source_id
        assert kwargs["json"]["properties"]["名前"]["title"][0]["text"]["content"] == report["subject"]
        return {"object": "page", "id": "created"}

    monkeypatch.setattr(delivery, "notion_request", fake)
    write = delivery.prepare_notion(report)
    assert len(calls) == 2 and all(m == "GET" for m, _ in calls)
    ledger, identity = tmp_path / "notion.sqlite3", ("2026-11-24", "morning", "notion")
    assert delivery.reserve_and_send(ledger, identity, write)["state"] == "sent"
    assert delivery.reserve_and_send(ledger, identity, write)["state"] == "duplicate"
    assert calls.count(("POST", "pages")) == 1


def test_workflow_is_manual_preview_only():
    workflow = yaml.load((ROOT / ".github/workflows/newspaper_briefing_preview.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    steps = workflow["jobs"]["preview"]["steps"]
    invocation = next(s["run"] for s in steps if "--edition" in s.get("run", ""))
    assert "--offline-input" in invocation
    assert "--send-email" not in invocation and "--write-notion" not in invocation and "--ai" not in invocation
    assert any(s.get("uses", "").startswith("actions/upload-artifact@") for s in steps)
