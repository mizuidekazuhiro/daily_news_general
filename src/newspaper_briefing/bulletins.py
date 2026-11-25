"""Condense routine earthquake notices without merging their underlying events."""

from collections import defaultdict
import re
import unicodedata

from .feeds import timestamp
from .stories import QUAKE_CLOCK, QUAKE_NOTICE, stable_id


OBSERVATION = re.compile(r"\d{1,2}月\d{1,2}日\d{1,2}時\d{2}分ごろ、"
                         r"(?P<region>[^、]+)を震源とする地震があり、最大震度(?P<intensity>[12])が観測されました")
TIMED_BULLETIN = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}時\d{2}分発表\s+"
                            r"[^、。]{2,36}\s+深さ\s*(?:約\s*)?\d+\s*km\s+最大震度[12]")
SIGNIFICANT_QUAKE = re.compile(r"震度\s*[3-7]|(?:大)?津波(?:警報|注意報)|避難|被害|死傷|死亡|負傷|"
                               r"けが|行方不明|倒壊|停電|断水|運休|通行止め|訂正|訓練")


def routine_notice(entry: dict, evidence) -> dict | None:
    row = entry["row"]
    if entry.get("changes") or row["update"] not in {"", "新規"}:
        return None
    titles = [source["headline"] for source in row.get("sources", ())]
    if not titles and row.get("article_links"):
        titles = [fact["value"] for fact in entry["facts"] if fact["field"] == "見出し"]
    headlines = [unicodedata.normalize("NFKC", title) for title in titles]
    if headlines and any(SIGNIFICANT_QUAKE.search(title) or not (
        (QUAKE_NOTICE.match(title) and QUAKE_CLOCK.search(title)) or TIMED_BULLETIN.fullmatch(title)
    ) for title in headlines):
        return None
    facts = evidence(entry)
    intensities = [fact for fact in facts if fact["field"] == "最大震度"]
    if (any(fact["value"] not in {"1", "2"} for fact in intensities)
            or any(fact["source"]["kind"] == "primary" and SIGNIFICANT_QUAKE.search(fact["value"])
                   for fact in facts)):
        return None
    for fact in facts:
        source = fact["source"]
        if (source["kind"] != "primary" or source["route"] != "jma-earthquake"
                or fact["field"] != "発表内容" or not fact["measured_at"]):
            continue
        if observation := OBSERVATION.fullmatch(fact["value"]):
            at = timestamp(fact["measured_at"])
            intensity = observation["intensity"]
            caption = f"{at.month}月{at.day}日{at.hour}時{at.minute:02d}分"
            visible = [f for f in intensities if f["value"] == intensity and f["source"] == source
                       and f["measured_at"] == fact["measured_at"]]
            return {"region": observation["region"], "caption": caption, "at": at,
                    "intensity": intensity, "facts": [fact, *visible]}
    if headlines:
        for fact in facts:
            if fact["field"] == "見出し" and (clock := QUAKE_CLOCK.search(unicodedata.normalize("NFKC", fact["value"]))):
                return {"region": "発表時刻", "caption": clock.group().strip(), "at": entry["published_at"],
                        "intensity": "", "facts": [fact]}
    return None


def earthquake_roundup(members: list[tuple[dict, dict]]) -> dict:
    references, notices, regions = {}, [], defaultdict(list)
    for entry, notice in sorted(members, key=lambda member: member[1]["at"]):
        fact = notice["facts"][0]
        source = fact["source"]
        references[source["id"]] = source
        regions[notice["region"], notice["intensity"]].append(notice["caption"])
        notices.append({"id": entry["row"]["id"], "title": entry["row"]["title"], "at": notice["at"],
                        "region": notice["region"], "source": source})
        entry["published_facts"] = notice["facts"]
    refs = list(references.values())
    descriptions = []
    for (region, intensity), times in regions.items():
        observation = f"{region}　{'、'.join(times)}"
        if intensity:
            observation += f"（{'いずれも' if len(times) > 1 else ''}最大震度{intensity}）"
        descriptions.append(observation)
    text = "。".join(descriptions) + "。"
    first = members[0][0]["row"]
    title = "地震速報まとめ"
    published_at = min(entry["published_at"] for entry, _ in members)
    return {"row": {
        "id": stable_id("quake-bulletins-", *(notice["id"] for notice in notices)),
        "title": title, "title_source": refs[0], "category": "災害", "rank": first.get("rank"), "published_at": published_at,
        "update": first["update"], "update_evidence": [], "earthquake_notices": notices,
        "sources": [source for entry, _ in members for source in entry["row"].get("sources", ())],
        "line": {"text": f"{title}（{len(notices)}件）　{text}", "sources": refs,
                 "fact_ids": [fact["id"] for _, notice in members for fact in notice["facts"]]},
    }, "published_at": published_at,
        "notice_members": [entry for entry, _ in members]}


def select_issue_entries(entries: list[dict], evidence, limit: int) -> list[dict]:
    """Count the roundup at the rank of its highest-ranked notice."""
    members = [(entry, notice) for entry in entries if (notice := routine_notice(entry, evidence)) is not None]
    if len(members) < 2:
        return entries[:limit]
    member_ids = {entry["row"]["id"] for entry, _ in members}
    first_id = members[0][0]["row"]["id"]
    selected = []
    for entry in entries:
        story_id = entry["row"]["id"]
        if story_id == first_id:
            selected.append(earthquake_roundup(members))
        elif story_id not in member_ids:
            selected.append(entry)
        if len(selected) == limit:
            break
    return selected


def individual_entries(entries: list[dict]):
    for entry in entries:
        yield from entry.get("notice_members", (entry,))
