"""Compare the facts readers saw in the morning with the evening evidence."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
import json
from pathlib import Path
import unicodedata

from .bulletins import select_issue_entries
from .evidence import FIELD_SECTION, NUMBER_FIELDS, conditions, conflicts, validate_carried_source
from .feeds import Settings, timestamp, url_key
from .stories import conflicting, disjoint_known, features, outage_scope, similarity, within_event_interval


COMPARISON_MISSING = "朝版との比較ができないため、指定時間帯の新着のみ掲載"
COLLECTION_FAILED = "収集に失敗したため、更新の有無は判定できません"
GROUPS = ("訂正", "続報", "新規", "朝版未掲載")
DEFAULT_STORY_LIMIT = 30
MATERIAL = frozenset(FIELD_SECTION) - {"発表内容", "発生時刻", "震源", "影響", "次の予定"}
STAGES = {"当選確実": 1, "当選": 2, "閣議決定": 1, "可決": 2, "成立": 3}


def finalized_snapshot(selected: Iterable[dict], cutoff: datetime) -> dict:
    return {"version": 2, "edition": "morning", "as_of": cutoff.isoformat(), "stories": [
        {"id": entry["row"]["id"], "title": entry["row"]["title"],
         "published_at": entry["published_at"],
         "article_keys": sorted(story_keys(entry["scored"]["story"])),
         "outage_regions": sorted(outage_scope(features(article.title)
                                                for article in entry["scored"]["story"].articles) or ()),
         "facts": entry["published_facts"]}
        for entry in selected
    ]}


def story_keys(story) -> set[str]:
    return set().union(*(article.keys for article in story.articles))


def valid_baseline(data: dict, cutoff: datetime, settings: Settings) -> bool:
    at, stories = timestamp(data["as_of"]), data["stories"]
    if (data["version"] != 2 or data["edition"] != "morning" or at.date() != cutoff.date()
            or at >= cutoff or not isinstance(stories, list)):
        return False
    for story in stories:
        if (not isinstance(story["id"], str) or not isinstance(story["title"], str)
                or timestamp(story["published_at"]) > at
                or not isinstance(story["article_keys"], list)
                or not all(isinstance(k, str) for k in story["article_keys"])
                or not isinstance(story["facts"], list)):
            return False
        if "outage_regions" in story and (not isinstance(story["outage_regions"], list)
                                          or not all(isinstance(place, str) for place in story["outage_regions"])):
            return False
        for fact in story["facts"]:
            if (fact["field"] not in {*FIELD_SECTION, "見出し"}
                    or not all(isinstance(fact[k], str) for k in ("id", "value", "scope", "definition", "measured_at"))
                    or timestamp(fact["at"]) > at):
                return False
            source = fact["source"]
            if (not isinstance(source["origin"], str) or not validate_carried_source(source, settings)):
                return False
    return len({story["id"] for story in stories}) == len(stories)


def morning_snapshot(path: Path | None, output: Path, end: datetime, settings: Settings, *,
                     publication: bool = False) -> dict | None:
    pattern = "*/delivery/snapshot.json" if publication else "*/snapshot.json"
    paths = ([path / "snapshot.json" if path.is_dir() else path] if path is not None else
             sorted((output / end.date().isoformat() / "morning").glob(pattern), reverse=True))
    for candidate in paths:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
            if valid_baseline(data, end, settings):
                return data
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).replace(",", "").split()).rstrip("。")


def same_origin(first: dict, second: dict) -> bool:
    a, b = first["source"], second["source"]
    return bool(url_key(a["link"]) == url_key(b["link"]) or (a["origin"] and a["origin"] == b["origin"]))


def comparable(old: dict, new: dict) -> bool:
    if old["field"] != new["field"]:
        return False
    a, b = conditions(old), conditions(new)
    if any(left and right and left != right for left, right in zip(a[:2], b[:2])):
        return False
    return same_origin(old, new) or (all(a[:2]) and a[:2] == b[:2])


def change_for(previous: list[dict], fact: dict, since: datetime, cutoff: datetime) -> dict | None:
    field = fact["field"]
    if field not in MATERIAL and not (field in FIELD_SECTION and fact["correction"] and fact["corrects"]):
        return None
    old = [value for value in previous if comparable(value, fact)]
    if any(normalized(value["value"]) == normalized(fact["value"]) for value in old):
        return None
    at = timestamp(fact["at"])
    if at <= since and fact.get("observed_at"):
        # A title can be edited at the same URL without an updated timestamp.
        # Require a previously seen version and an evening observation of that URL.
        if any(url_key(value["source"]["link"]) == url_key(fact["source"]["link"])
               and timestamp(value["at"]) == at for value in old):
            at = timestamp(fact["observed_at"])
    if not since < at <= cutoff:
        return None
    if fact["correction"]:
        corrected = [value for value in old if same_origin(value, fact) and (
            (fact["corrects"] and normalized(value["value"]) == normalized(fact["corrects"]))
            or (not fact["corrects"] and (not value["measured_at"] or not fact["measured_at"]
                                         or value["measured_at"] == fact["measured_at"])))
        ]
        if len({normalized(value["value"]) for value in corrected}) == 1:
            return {"kind": "訂正", "before": corrected[0], "after": fact, "at": at,
                    "link": fact["source"]["link"]}
        return None
    if old and field in NUMBER_FIELDS:
        # Conflicting observations at the same measurement are not changes over time.
        old = [value for value in old if (not value["measured_at"] or not fact["measured_at"]
                                          or value["measured_at"] < fact["measured_at"])]
        if not old:
            return None
    elif not old and (any(value["field"] == field for value in previous)
                      or (field in NUMBER_FIELDS and fact["source"]["kind"] != "primary")):
        return None
    stage = next((rank for token, rank in STAGES.items() if fact["value"].endswith(token)), 0)
    if stage and any(STAGES.get(value["value"], 0) > stage for value in old):
        return None
    prior = max(old, key=lambda value: value["at"], default=None)
    if prior is None:
        prior = next((value for value in previous if value["field"] == "見出し"), None)
    return {"kind": "続報", "before": prior,
            "after": fact, "at": at, "link": fact["source"]["link"]}


def candidate_baseline(story, by_key: dict, by_title: list) -> dict | None:
    own_titles = [features(article.title) for article in story.articles]
    own_scope = outage_scope(own_titles) or frozenset()

    def different_outage(previous, title):
        prior_scope = frozenset(previous["outage_regions"]) if "outage_regions" in previous else title.outage_regions
        return disjoint_known(own_scope, prior_scope)

    exact = {candidate["id"]: candidate for key in story_keys(story) if ":title:" not in key
             for candidate in by_key.get(key, ())}
    if len(exact) == 1:
        candidate = next(iter(exact.values()))
        title = features(candidate["title"])
        prior_keys = {key for key in candidate["article_keys"] if ":title:" not in key}
        # The same article may be grouped with a different local follow-up in each edition.
        if len(title.text) >= 8 and any(
            current.text == title.text and article.keys.intersection(prior_keys)
            for current, article in zip(own_titles, story.articles)
        ):
            return candidate
        if not different_outage(candidate, title) and any(not conflicting(current, title) for current in own_titles):
            return candidate
    scores = []
    for previous, title in by_title:
        if different_outage(previous, title):
            continue
        published_at = timestamp(previous["published_at"])
        match = max((similarity(current, title) for current, article in zip(own_titles, story.articles)
                     if within_event_interval(current, title, article.published_at - published_at)),
                    default=0)
        if match >= 0.48:
            scores.append((match, previous))
    scores.sort(key=lambda result: result[0], reverse=True)
    if scores and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.08):
        return scores[0][1]
    return None


def evening_entries(ranked: list[dict], baseline: dict | None, cutoff: datetime, evidence,
                    collection_state: str, limit: int = DEFAULT_STORY_LIMIT) -> tuple[list[dict], dict]:
    unknown = dict.fromkeys(GROUPS)
    if collection_state == "failed":
        return [], unknown
    if baseline is None:
        return select_issue_entries(ranked, evidence, limit), unknown
    since = timestamp(baseline["as_of"])
    by_key = defaultdict(list)
    by_title = [(row, features(row["title"])) for row in baseline["stories"]]
    for previous, _ in by_title:
        for key in previous["article_keys"]:
            by_key[key].append(previous)
    picked, matched = [], {}
    for position, entry in enumerate(ranked, 1):
        story = entry["scored"]["story"]
        previous = candidate_baseline(story, by_key, by_title)
        if previous:
            seen, changes = set(), []
            facts = sorted(evidence(entry), key=lambda f: (f["at"], f["correction"]), reverse=True)
            disputed = {fact["id"] for difference in conflicts(facts) if difference["kind"] == "same_conditions"
                        for fact in difference["facts"]}
            for fact in facts:
                if fact["id"] in disputed and not fact["correction"]:
                    continue
                value = fact["field"], normalized(fact["value"]), conditions(fact)
                if value in seen:
                    continue
                if change := change_for(previous["facts"], fact, since, cutoff):
                    changes.append(change)
                    seen.add(value)
            if not changes:
                continue
            if previous["id"] in matched:
                existing = matched[previous["id"]]
                if any(not conflicting(features(story.articles[0].title), features(a.title))
                       for a in existing["scored"]["story"].articles):
                    existing["changes"].extend(changes)
                continue
            entry["row"]["id"] = previous["id"]
            entry["changes"] = changes
            matched[previous["id"]] = entry
        elif entry["published_at"] > since:
            entry["row"]["update"] = "新規"
        elif position <= limit:
            # A timely headline first seen again in the evening is not a new publication.
            # Keep pre-morning material only when it still leads the overall ranking.
            entry["row"]["update"] = "朝版未掲載"
        else:
            continue
        picked.append(entry)

    for entry in matched.values():
        entry["changes"].sort(key=lambda item: (GROUPS.index(item["kind"]), -item["at"].timestamp()))
        unique = {}
        for item in entry["changes"]:
            fact = item["after"]
            unique.setdefault((item["kind"], fact["field"], normalized(fact["value"]), conditions(fact)), item)
        entry["changes"] = list(unique.values())
        for update in entry["changes"]:
            if update["kind"] != "続報":
                continue
            corrected = next((item for item in entry["changes"] if item["kind"] == "訂正"
                              and item["at"] < update["at"] and comparable(item["after"], update["after"])), None)
            if corrected:
                update["before"] = corrected["after"]
        entry["row"]["update"] = entry["changes"][0]["kind"]
        entry["row"]["update_evidence"] = entry["changes"]
    picked.sort(key=lambda entry: min(GROUPS.index(entry["row"]["update"]), 2))
    selected = select_issue_entries(picked, evidence, limit)
    counts = Counter(group for entry in selected for group in (
        {item["kind"] for item in entry["changes"]} if entry.get("changes") else {entry["row"]["update"]}
    ))
    return selected, {group: counts[group] for group in GROUPS}
