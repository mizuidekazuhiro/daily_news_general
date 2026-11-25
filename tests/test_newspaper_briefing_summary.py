import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.newspaper_briefing import __main__ as cli, feeds, stories, summaries
from src.newspaper_briefing.evidence import claim, fact_values, rss_claims
from src.openai_json_client import OpenAIJsonClient


FIXTURES = Path(__file__).resolve().parent / "fixtures/newspaper_briefing"
END = feeds.timestamp("2026-11-24T08:00:00+09:00")


def run_preview(root, edition="morning", *extra):
    clock = "08:00:00" if edition == "morning" else "18:00:00"
    result = cli.main(["--edition", edition, "--as-of", f"2026-11-24T{clock}+09:00",
                       "--offline-input", str(FIXTURES / f"{edition}.json"), "--output-dir", str(root), *extra])
    folder = root / "2026-11-24" / edition / clock.replace(":", "")
    return result, folder


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_morning_preview_uses_the_mail_body_and_never_enters_delivery(tmp_path, monkeypatch):
    def fail_delivery(*args, **kwargs):
        pytest.fail("the preview entered delivery")
    monkeypatch.setattr(cli, "deliver", fail_delivery)
    code, folder = run_preview(tmp_path)
    assert code == 0
    report, evening_base = read(folder / "report.json"), read(folder / "snapshot.json")
    mail = read(folder / "mail.json")
    assert mail["html"] == (folder / "report.html").read_text(encoding="utf-8")
    assert "朝の要点" in mail["text"]
    assert 1 <= len(report["overview"]) <= 3
    assert [story["id"] for story in evening_base["stories"]] == [story["id"] for story in report["stories"]]
    assert all(story["summary"]["status"] == "見出しベース" for story in report["stories"][:5])
    assert all("summary" not in story and story["line"]["sources"] for story in report["stories"][5:])


def test_missing_morning_compares_no_updates_and_says_what_is_available(tmp_path):
    code, folder = run_preview(tmp_path, "evening", "--window-hours", "3")
    assert code == 0
    report = read(folder / "report.json")
    assert report["changes"] == {"訂正": None, "続報": None, "新規": None, "朝版未掲載": None}
    assert "朝版との比較ができないため、指定時間帯の新着のみ掲載" in read(folder / "mail.json")["text"]
    assert "続報0件" not in read(folder / "mail.json")["text"]


def test_default_live_preview_never_calls_a_dispatcher(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("a preview entered delivery or performed an unexpected request")

    monkeypatch.setattr(cli, "collect", lambda settings: feeds.read_capture(FIXTURES / "morning.json", settings))
    monkeypatch.setattr(cli, "deliver", unexpected)
    monkeypatch.setattr(feeds.requests, "get", unexpected)
    code = cli.main(["--preview", "--edition", "morning", "--as-of", "2026-11-24T08:00:00+09:00",
                     "--headlines-only", "--output-dir", str(tmp_path)])
    assert code == 0
    report = read(tmp_path / "2026-11-24/morning/080000/report.json")
    assert report["offline"] is False and report["delivery"] == dict.fromkeys(("email", "notion"), {"state": "preview"})


def test_sample_official_facts_disagreement_correction_and_followup(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("the offline pipeline reached a live integration")

    monkeypatch.setattr(cli, "deliver", unexpected)
    monkeypatch.setattr(feeds.requests, "get", unexpected)
    policy = ("--source-policy", str(FIXTURES / "summary_policy.json"))
    code, morning = run_preview(tmp_path, "morning", *policy, "--primary-input", str(FIXTURES / "primary_morning.json"))
    assert code == 0
    base = read(morning / "report.json")
    plain_code, plain_morning = run_preview(tmp_path / "unsupplemented", "morning", "--headlines-only")
    assert plain_code == 0
    plain = read(plain_morning / "report.json")
    assert [(row["id"], row["score"]) for row in base["stories"]] == [(row["id"], row["score"]) for row in plain["stories"]]
    topic = base["stories"][0]
    assert topic["summary"]["status"] == "根拠付き要約"
    sections = topic["summary"]["sections"]
    assert "青葉町沿岸の住民" in sections["why"]["text"]
    assert "11月24日12時" in sections["next"]["text"]
    assert all(sections[key]["sources"][0]["link"].startswith("https://aoba.example.invalid/notices/")
               for key in ("what", "why", "next"))
    note = topic["summary"]["notes"][0]
    assert "数字が一致していない" in note["text"]
    assert "青葉県資料を基に作成 4人" in note["text"] and "青葉救助本部資料を基に作成 6人" in note["text"]
    assert len(note["sources"]) == 2
    assert base["overview"][0] == note
    code, evening = run_preview(tmp_path, "evening", *policy, "--primary-input", str(FIXTURES / "primary_evening.json"),
                                "--snapshot", str(morning / "snapshot.json"))
    assert code == 0
    report, message = read(evening / "report.json"), read(evening / "mail.json")
    story = next(item for item in report["stories"] if item["id"] == topic["id"])
    correction, following = [c for c in story["update_evidence"] if c["after"]["field"] == "負傷者"]
    assert (correction["kind"], correction["before"]["value"], correction["after"]["value"]) == ("訂正", "4人", "5人")
    assert (following["kind"], following["before"]["value"], following["after"]["value"]) == ("続報", "5人", "8人")
    assert report["changes"]["訂正"] == 1 and report["changes"]["続報"] == 3
    assert message["html"].index(">訂正</h2>") < message["html"].index(">重要な続報</h2>")
    assert message["html"].index(">重要な続報</h2>") < message["html"].index(">新規・朝版未掲載</h2>")
    assert message["text"].count("4人 → 5人") == message["text"].count("5人 → 8人") == 1
    assert message["text"].count(story["title"]) == 1
    assert message["email_allowed"] is False and report["delivery"]["email"]["state"] == "preview"


def test_only_verified_paths_and_publication_cutoff(tmp_path):
    policy = read(FIXTURES / "summary_policy.json")
    policy["sources"][0]["permissions"]["email"] = False
    policy["sources"][1]["permissions"]["save"] = False
    configured = write(tmp_path / "policy.json", policy)
    code, folder = run_preview(tmp_path, "morning", "--source-policy", configured,
                               "--primary-input", str(FIXTURES / "primary_morning.json"))
    assert code == 0
    assert "https://aoba.example.invalid" not in read(folder / "mail.json")["text"]
    assert len(read(folder / "primary.json")["documents"]) == 1
    fixture = read(FIXTURES / "primary_morning.json")
    fixture["documents"][0]["issued_at"] = "2026-11-24T08:00:01+09:00"
    fixture["documents"][1]["link"] = "https://rescue.example.invalid/releases/%2E%2E/private"
    amended = write(tmp_path / "amended.json", fixture)
    code, folder = run_preview(tmp_path, "morning", "--source-policy", str(FIXTURES / "summary_policy.json"),
                               "--primary-input", amended)
    assert code == 0 and read(folder / "primary.json")["documents"] == []
    assert all(s["summary"]["status"] == "見出しベース" for s in read(folder / "report.json")["stories"][:5])
    code, folder = run_preview(tmp_path, "morning", "--primary-input", str(tmp_path / "unavailable.json"))
    assert code == 0 and read(folder / "diagnostics.json")["primary"]["state"] == "input_failed"


def test_approved_rss_description_is_the_only_eligible_feed_text(tmp_path):
    root = FIXTURES.parents[2]
    settings = feeds.load_settings(root / "config/newspaper_briefing.json")
    feed = settings.feeds["okinawa-official"]
    policy = read(FIXTURES / "summary_policy.json")
    policy["sources"].append({
        "id": feed.id, "kind": "rss_description", "url": feed.url,
        "basis_urls": ["https://example.invalid/feed-terms"], "checked_on": "2026-11-24",
        "permissions": {"acquire": True, "save": True, "ai": False, "email": True},
        "constraints": "fixture only",
    })
    policy_path = write(tmp_path / "policy.json", policy)
    settings = feeds.load_settings(root / "config/newspaper_briefing.json", Path(policy_path))
    settings.publishers["okinawa"] = replace(settings.publishers["okinawa"], allow_distribution=True)
    fixture = read(FIXTURES / "morning.json")
    official = next(x for x in fixture["feeds"] if x["feed"] == feed.id)
    search = next(x for x in fixture["feeds"] if x["feed"] == "asahi-search")
    marker = "影響：青葉町沿岸の世帯が対象。"

    def inject(rss):
        return rss.replace("</title>", "</title><description>" + marker + "</description>", 2)

    _, accepted = feeds.parse_feed(feed, inject(official["rss"]).encode(), settings, END)
    _, discarded = feeds.parse_feed(settings.feeds[search["feed"]], inject(search["rss"]).encode(), settings, END)
    licensed = next(observation for observation in accepted if observation.description == marker)
    [fact] = rss_claims(licensed, settings, END)
    assert fact["field"] == "影響" and fact["value"] == "青葉町沿岸の世帯が対象"
    assert fact["source"]["email"] and not fact["source"]["ai"] and fact["source"]["link"] == licensed.link
    assert discarded and all(not observation.description for observation in discarded)


@pytest.mark.parametrize("mode", ["no_licensed_input", "unavailable", "invalid_id", "invalid_link", "invalid_text", "ok"])
def test_ai_only_selects_verified_picture_and_falls_back(mode, monkeypatch):
    ref = {"id": "r-approved", "publisher": "municipality", "origin": "municipality", "kind": "primary",
           "name": "自治体資料", "link": "https://example.invalid/announcement/1", "link_kind": "publisher",
           "ai": mode != "no_licensed_input", "email": True}
    headline = claim("見出し", "堤防を緊急点検", ref | {"id": "h", "kind": "headline", "ai": False}, END)
    approved = claim("発表内容", "市が堤防を緊急点検した", ref, END)
    secrets = ("NEWSPAPER_BRIEFING_OPENAI_API_KEY", "NEWSPAPER_BRIEFING_OPENAI_MODEL")
    for name in secrets:
        if mode == "unavailable":
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, "mock-test")

    def choose(_self, user_prompt, **kwargs):
        facts = json.loads(user_prompt)[0]["facts"]
        assert len(facts) == 1 and facts[0]["value"] == approved["value"]
        item = {"id": "invented" if mode == "invalid_id" else "story",
                "what": [{"id": approved["id"], "url": "https://wrong.invalid" if mode == "invalid_link" else ref["link"]}],
                "why": [], "next": []}
        if mode == "invalid_text":
            item["text"] = "市内で9人が負傷した"
        return {"stories": [item]}

    monkeypatch.setattr(OpenAIJsonClient, "generate_structured_json", choose)
    row = {"id": "story"}
    state = summaries.build_summaries([{"row": row}], lambda _: [headline, approved], True)
    assert state == ("invalid" if mode.startswith("invalid_") else mode)
    if mode.startswith("invalid_") or mode == "unavailable":
        assert row["summary"]["status"] == "要約は掲載できませんでした"
        assert headline["value"] in row["line"]["text"] and "9人" not in row["line"]["text"]
        assert row["line"]["sources"] == [headline["source"]]
    else:
        assert row["line"]["sources"] == [ref]


def test_same_url_update_and_earlier_unlisted_story(tmp_path):
    code, morning = run_preview(tmp_path)
    assert code == 0
    source = read(morning / "capture.json")
    source["captured_at"] = "2026-11-24T17:59:59+09:00"
    record = next(o for o in source["observations"] if o["feed"] == "okinawa-official" and "倉庫全焼" in o["title"])
    record["title"] = record["title"].replace("3人", "5人")
    record["fetched_at"] = source["captured_at"]
    earlier = record | {"title": "気象庁が青空県沿岸に大津波警報を発表", "guid": "unlisted-critical",
                       "original_url": record["original_url"] + "-other", "publisher_url": record["publisher_url"] + "-other",
                       "published_at": "2026-11-24T07:01:00+09:00", "reporting_group": ""}
    source["observations"].append(earlier)
    amended = write(tmp_path / "same_url.json", source)
    code, evening = run_preview(tmp_path, "evening", "--offline-input", amended, "--snapshot", str(morning / "snapshot.json"))
    assert code == 0
    report = read(evening / "report.json")
    case = next(s for s in report["stories"] if "白浜市" in s["title"] and s["update"] == "続報")
    evidence = next(x for x in case["update_evidence"] if x["after"]["field"] == "負傷者")
    assert evidence["before"]["source"]["link"] == evidence["after"]["source"]["link"]
    assert evidence["before"]["value"] == "3人" and evidence["after"]["value"] == "5人"
    late = next(s for s in report["stories"] if "青空県" in s["title"])
    assert late["update"] == "朝版未掲載" and late["published_at"] == earlier["published_at"]
    assert "朝版未掲載・公表 11月24日 07:01" in read(evening / "mail.json")["text"]


def test_transport_event_anchors_and_policy_target():
    rail = stories.features("青葉鉄道の浦辺駅で男女2人が特急にはねられ死亡 10万人に影響")
    same_rail = stories.features("浦辺駅で人身事故 男女２人死亡")
    other_rail = stories.features("高浜駅で人身事故 男女2人死亡")
    assert stories.similarity(rail, same_rail) > 0
    assert stories.similarity(rail, other_rail) == 0
    closure = stories.features("青葉道でトラック炎上 早瀬IC―青山IC間の下り線が通行止め")
    reopening = stories.features("青葉自動車道通行止め解除 早瀬IC―青山IC（下り）")
    other_direction = stories.features("青葉道通行止め解除 早瀬IC―青山IC（上り）")
    assert stories.similarity(closure, reopening) > 0
    assert stories.similarity(closure, other_direction) == 0
    proposal = stories.features("野党 青井大臣の問責決議案協議")
    agreement = stories.features("参議院 野党6党 青井文科相に対する問責決議案 提出方針で一致")
    unrelated = stories.features("野党 高野大臣の問責決議案提出で一致")
    assert stories.similarity(proposal, agreement) > 0
    assert stories.similarity(proposal, unrelated) == 0
    assert dict(fact_values("線路点検 青葉鉄道で３本運休"))["影響"] == "3本運休"
    outage = "雪で停電続く 青葉県内約3000戸 山口県内400戸"
    assert dict(fact_values(outage))["影響"] == "停電続く 青葉県内約3000戸 山口県内400戸"


@pytest.mark.parametrize("state", ["ok", "partial", "failed"])
def test_zero_updates_is_not_reported_for_an_outage(tmp_path, state):
    root = FIXTURES.parents[2]
    settings = feeds.load_settings(root / "config/newspaper_briefing.json")
    code, base = run_preview(tmp_path)
    assert code == 0
    captured = read(base / "capture.json")
    record = next(item for item in captured["observations"] if item["feed"] == "okinawa-official" and "倉庫全焼" in item["title"])
    successful = [{"feed": f"{key}-search", "fetched_at": captured["captured_at"]} for key in settings.publishers]
    fixture = {"version": 1, "captured_at": captured["captured_at"], "feeds": successful, "observations": [record]}
    input_file = write(tmp_path / "one_story.json", fixture)
    code, morning = run_preview(tmp_path / "zero", "morning", "--offline-input", input_file)
    assert code == 0
    for entry in successful:
        if state == "failed" or (state == "partial" and entry["feed"] == "asahi-search"):
            entry["failure"] = "HTTP 503"
    if state == "failed":
        fixture["observations"] = []
    input_file = write(tmp_path / "zero.json", fixture)
    code, evening = run_preview(tmp_path / "zero", "evening", "--offline-input", input_file, "--snapshot",
                                str(morning / "snapshot.json"))
    report, text = read(evening / "report.json"), read(evening / "mail.json")["text"]
    assert report["collection_state"] == state and not report["stories"]
    if state == "failed":
        assert code == 2 and all(v is None for v in report["changes"].values()) and "続報0件" not in text
        assert "収集に失敗したため、更新の有無は判定できません" in text
    else:
        assert code == 0 and all(v == 0 for v in report["changes"].values()) and "続報0件" in text
        if state == "partial":
            assert "確認できた範囲では" in text and "未取得 朝日新聞" in text
