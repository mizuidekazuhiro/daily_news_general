"""Normalize the claims that can directly support the briefing."""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .feeds import GOOGLE_HOST, Observation, Settings, clean_url, host, timestamp, url_key
from .policy import Grant, approved, https_url
from .stories import (CENSURE_TARGET, CONDITIONAL, CORRECTION, QUAKE_CLOCK, QUAKE_NOTICE, ROAD_SECTION,
                      conflicting, features, material_facts, stable_id)


FIELD_SECTION = {
    "発表内容": "what", "発生時刻": "what", "震源": "what", "最大震度": "what", "マグニチュード": "what",
    "決定段階": "what", "選挙結果": "what", "公式確認": "what",
    "死者": "what", "負傷者": "what", "死傷者": "what", "行方不明者": "what",
    "避難者": "why", "全壊": "why", "対象範囲": "why", "影響": "why", "規制状態": "why",
    "適用日": "next", "次の予定": "next",
}
NUMBER_FIELDS = {"死者", "負傷者", "死傷者", "行方不明者", "避難者", "全壊", "最大震度", "マグニチュード"}
COUNT_LABELS = dict(zip(("死亡", "けが", "死傷", "行方不明", "避難", "全壊"),
                        ("死者", "負傷者", "死傷者", "行方不明者", "避難者", "全壊")))
UNCERTAIN = re.compile(r"見通し|の可能性|未定|検討|場合|予定か|(?:決定|解除|確認)(?:へ|か|の方向)|変更する方針")
EXPLICIT_FIELDS = {
    "対象範囲": re.compile(r"対象(?:範囲)?(?:は|を)[ ]*([^、。　]{1,32}?)(?:に(?:決定|確定|拡大)|と(?:決定|確定))"),
    "適用日": re.compile(r"(?:適用日|施行日)(?:は|を)[ ]*(\d{1,2}月\d{1,2}日)(?:に|と)?(?:決定|確定)?"),
    "規制状態": re.compile(r"(?:通行止め|規制|避難指示)(?:を|の)?(?:解除|発令)(?!へ|か|予定)"),
    "公式確認": re.compile(r"([^、。　]{2,24}?)(?:を|が)公式に確認"),
}
HEADLINE_CONTEXT = {
    "影響": re.compile(r"(?:(?:断水|停電|運休|通行止め|避難)(?:など)?[、 ]*)?"
                      r"(?:約|少なくとも)?[0-9][0-9,]*(?:万[0-9,]*)?(?:人|戸|世帯)(?:に影響|が停電|で断水|が避難)"
                      r"|(?:停電|断水)(?:が)?(?:続く|継続)[ 　]+"
                      r"(?:[一-龯々]{2,8}(?:約|少なくとも)?[0-9][0-9,]*(?:万[0-9,]*)?(?:戸|世帯)[ 　、]*){1,3}"
                      r"|(?:最大)?(?:[0-9]+本(?:が)?運休|[0-9]+便(?:が)?欠航)"
                      r"|(?:全館|全域)(?:で)?(?:停電|断水)"
                      r"|一般公開(?:が|を)?(?:始まる|始まり|開始|再開)"),
    "次の予定": re.compile(r"(?:[0-9]{1,2}月[0-9]{1,2}日|(?:あす|明日|きょう|今日|今夜)(?:午前|午後)?"
                         r"|(?:午前|午後)[0-9]{1,2}時(?:[0-9]{1,2}分)?)"
                         r"(?:に|から|までに)?[ ]*(?:記者会見|正式発表|採決|開票|運転再開|復旧|施行)(?:の)?予定"),
}
MEASUREMENT = re.compile(r"\d{1,2}月\d{1,2}日(?:午前|午後)?\d{1,2}時(?:\d{1,2}分)?現在")
URL_TEXT = re.compile(r"https?://|www[.]|\n|\r", re.I)


def publisher_link_allowed(link: str, publisher) -> bool:
    link = clean_url(link)
    if not link or host(link) not in (GOOGLE_HOST, *publisher.hosts) or urlsplit(link).path in {"", "/"}:
        return False
    return host(link) != GOOGLE_HOST or bool(re.fullmatch(r"/(?:rss/)?articles/[A-Za-z0-9_-]+", urlsplit(link).path))


def reference(record: Observation, settings: Settings, kind: str = "headline") -> dict | None:
    publisher, feed = settings.publishers[record.publisher], settings.feeds[record.feed]
    link = record.link
    if not publisher_link_allowed(link, publisher):
        return None
    if kind == "rss_description":
        ai = settings.description_permitted(feed, "ai")
        email = settings.description_permitted(feed, "email")
    else:
        ai = approved(settings.grants, "headline", feed.id, feed.url, "acquire", "save", "ai")
        email = approved(settings.grants, "headline", feed.id, feed.url, "acquire", "save", "email")
    return {
        "id": stable_id("r-", record.publisher, record.feed, kind, link),
        "publisher": record.publisher, "name": publisher.name, "link": link,
        "link_kind": "google" if host(link) == GOOGLE_HOST else "publisher",
        "kind": kind, "route": feed.id, "origin": record.reporting_group, "personal_only": feed.personal_only,
        "ai": bool(not feed.personal_only and publisher.allow_ai and ai),
        "email": bool(not feed.personal_only and publisher.allow_distribution and email),
    }


def primary_reference(grant: Grant, url: str) -> dict | None:
    if not https_url(url) or not url.startswith(grant.ref_prefix):
        return None
    path = unquote(urlsplit(url).path)
    if not path.startswith(urlsplit(grant.ref_prefix).path) or any(part in {".", ".."} for part in path.split("/")):
        return None
    if path.endswith("/") or "\\" in path:
        return None
    return {
        "id": stable_id("r-", grant.id, url), "publisher": grant.id, "name": f"{grant.name}資料を基に作成",
        "link": url, "link_kind": "publisher", "kind": "primary", "route": grant.id, "origin": grant.id,
        "ai": grant.allows("ai"), "email": grant.allows("email"),
    }


def validate_carried_source(source: dict, settings: Settings) -> bool:
    kind, link, route = source["kind"], source["link"], source["route"]
    if kind == "primary":
        grant = settings.grants.get(("primary", route))
        return bool(grant and grant.kind == "primary" and grant.allows("acquire", "save", "email")
                    and source == primary_reference(grant, link))
    publisher, feed = settings.publishers.get(source["publisher"]), settings.feeds.get(route)
    if (kind not in {"headline", "rss_description"} or not publisher or not feed
            or feed.publisher not in {"", publisher.id} or not publisher_link_allowed(link, publisher)):
        return False
    allowed = (not feed.personal_only and publisher.allow_distribution
               and approved(settings.grants, kind, route, feed.url, "acquire", "save", "email"))
    expected_id = stable_id("r-", publisher.id, feed.id, kind, link)
    expected_kind = "google" if host(link) == GOOGLE_HOST else "publisher"
    return (source["id"] == expected_id and source["name"] == publisher.name and source["link_kind"] == expected_kind
            and (source["email"] is False or allowed))


def claim(field: str, value: str, ref: dict, at: datetime, *, scope: str = "", definition: str = "",
          measured_at: str = "", corrects: str = "", correction: bool = False,
          observed: datetime | None = None) -> dict:
    return {
        "id": stable_id("f-", ref["id"], field, value, scope, definition, measured_at, at.isoformat()),
        "field": field, "value": value, "section": FIELD_SECTION.get(field, "what"), "scope": scope,
        "definition": definition, "measured_at": measured_at, "corrects": corrects, "correction": correction,
        "at": at.isoformat(), "observed_at": observed.isoformat() if observed else None, "source": ref,
    }


def fact_values(headline: str):
    text = unicodedata.normalize("NFKC", headline)
    if CONDITIONAL.search(text) or re.search(r"ではない|しなかった|されなかった|人数を否定|との誤り", text):
        return
    for fact in material_facts(text):
        kind, _, value = fact.partition(":")
        if kind == "規模":
            subject, number = value.split(":", 1)
            if re.search(re.escape(number) + r"(?:増|減|程度|に満た|以上|以下)", text.replace(",", "")):
                continue
            qualified = re.search(r"(?:約|少なくとも)[ ]*" + re.escape(number), text.replace(",", ""))
            yield COUNT_LABELS[subject], qualified.group() if qualified else number
        elif kind == "選挙結果":
            yield "選挙結果", value
        elif kind == "決定・発表" and value != "公式発表":
            yield "決定段階", value
    if not UNCERTAIN.search(text):
        for field, pattern in EXPLICIT_FIELDS.items():
            match = pattern.search(text)
            if match:
                yield field, match.group(1) if pattern.groups else match.group()
        for field, pattern in HEADLINE_CONTEXT.items():
            match = pattern.search(text)
            if match and not re.match(r"\s*(?:か|へ|を見込|と予想|と試算)", text[match.end():]):
                value = match.group().strip()
                # Keep the facility or area in the quote when the effect's scope depends on it.
                if field == "影響" and value.startswith(("全館", "全域")) and len(text) <= 120:
                    value = text
                yield field, value
            elif field == "影響" and "通行止め" in text and ROAD_SECTION.search(text) and len(text) <= 120:
                yield field, text


def headline_claims(record: Observation, settings: Settings, cutoff: datetime) -> list[dict]:
    ref = reference(record, settings)
    if (ref is None or record.published_at is None or record.published_at > cutoff
            or (record.updated_at and record.updated_at > cutoff)):
        return []
    at = record.updated_at if record.updated_at and record.published_at < record.updated_at <= cutoff else record.published_at
    text = unicodedata.normalize("NFKC", record.title)
    measured = MEASUREMENT.search(text)
    base = claim("見出し", record.title, ref, at, observed=record.fetched_at)
    subject = CENSURE_TARGET.search(text)
    typed = []
    for field, value in dict.fromkeys(fact_values(record.title)):
        scope, definition = "", ""
        if field == "決定段階" and subject and value.startswith("問責決議案"):
            scope, definition = subject.group(1), "問責決議案の手続き"
        typed.append(claim(field, value, ref, at, scope=scope, definition=definition,
                           measured_at=measured.group() if measured else "", correction=bool(CORRECTION.search(text)),
                           observed=record.fetched_at))
    return [base, *typed]


def rss_claims(record: Observation, settings: Settings, cutoff: datetime) -> list[dict]:
    feed = settings.feeds[record.feed]
    if (not record.description or not settings.description_permitted(feed)
            or not record.published_at or record.published_at > cutoff
            or (record.updated_at and record.updated_at > cutoff)):
        return []
    ref = reference(record, settings, "rss_description")
    if ref is None:
        return []
    fields = []
    description = record.description.strip()
    for sentence in re.split(r"(?<=。)\s*|\n", description):
        if len(description) == 320 and not sentence.endswith("。"):
            continue
        field, separator, value = re.split(r"([：:])", sentence, maxsplit=1) if re.search(r"[：:]", sentence) else ("", "", "")
        if not separator or field not in FIELD_SECTION:
            field, value = "発表内容", sentence
        value = value.strip().removesuffix("。")
        if value and len(value) <= 140 and not URL_TEXT.search(value):
            fields.append(claim(field, value, ref, max(record.published_at, record.updated_at or record.published_at),
                                observed=record.fetched_at))
    return fields[:4]


def verified_document(document: dict, settings: Settings, cutoff: datetime) -> list[dict]:
    grant = settings.grants.get(("primary", document.get("source")))
    if not grant or grant.kind != "primary" or not grant.allows("acquire", "save"):
        return []
    try:
        at = timestamp(document["issued_at"])
        ref = primary_reference(grant, document["link"])
        if at > cutoff or ref is None:
            return []
        facts = []
        for raw in document["facts"]:
            field, value = raw["field"], raw["value"]
            metadata = {name: raw.get(name, "") for name in ("scope", "definition", "measured_at", "corrects")}
            if (field not in FIELD_SECTION or not value.strip() or
                    any(not isinstance(text, str) or len(text) > 140 or URL_TEXT.search(text)
                        for text in (value, *metadata.values()))):
                return []
            if metadata["measured_at"]:
                metadata["measured_at"] = timestamp(metadata["measured_at"]).isoformat()
            facts.append(claim(field, value.strip(), ref, at, **metadata, correction=document.get("correction") is True))
        return facts
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def matches_story(document: dict, records: list[Observation]) -> bool:
    anchor = document.get("match", {})
    if anchor.get("article_url") and clean_url(anchor["article_url"]):
        return any(url_key(record.link) == url_key(anchor["article_url"]) for record in records)
    if title := anchor.get("title"):
        if not isinstance(title, str):
            return False
        try:
            issued = timestamp(document.get("issued_at"))
        except (TypeError, ValueError):
            return False
        release = features(title)
        for record in records:
            if not record.published_at or abs(record.published_at - issued) > timedelta(hours=30):
                continue
            headline = features(record.title)
            if conflicting(release, headline) or min(len(release.text), len(headline.text)) < 15:
                continue
            wording = SequenceMatcher(None, release.text, headline.text, autojunk=False)
            shared = release.grams & headline.grams
            if len(shared) >= 10 and wording.ratio() >= 0.80 and wording.find_longest_match().size >= 13:
                return True
        return False
    if not anchor.get("reported_at") or document.get("source") != "jma-earthquake":
        return False
    try:
        reported = timestamp(anchor["reported_at"])
    except (TypeError, ValueError):
        return False
    target = (reported.day, reported.hour, reported.minute)
    for record in records:
        if record.published_at and timedelta(0) <= record.published_at - reported <= timedelta(minutes=3):
            title = unicodedata.normalize("NFKC", record.title)
            if QUAKE_NOTICE.search(title) and target in (tuple(map(int, m)) for m in QUAKE_CLOCK.findall(title)):
                return True
    return False


def story_evidence(scored: dict, settings: Settings, documents: list[dict], cutoff: datetime, supplement: bool) -> list[dict]:
    story, main = scored["story"], scored["main"]
    articles = [main, *(a for a in story.articles if a != main)]
    pairs = [(record, article.link_record.publisher_url) for article in articles
             for record in (article.title_record, *(r for r in article.records if r != article.title_record))]
    facts = []
    for record, canonical in pairs:
        if canonical and not record.publisher_url:
            record = replace(record, publisher_url=canonical)
        facts.extend(headline_claims(record, settings, cutoff))
        if supplement:
            facts.extend(rss_claims(record, settings, cutoff))
    if supplement:
        records = [record for record, _ in pairs]
        for document in documents:
            if matches_story(document, records):
                facts.extend(verified_document(document, settings, cutoff))
    return list({fact["id"]: fact for fact in facts
                 if fact["source"]["kind"] == "headline" or fact["source"]["email"]}.values())


def conditions(fact: dict) -> tuple[str, str, str]:
    return fact["scope"], fact["definition"], fact["measured_at"]


def conflicts(facts: list[dict]) -> list[dict]:
    by_metric = defaultdict(list)
    for fact in facts:
        if fact["field"] in NUMBER_FIELDS:
            by_metric[fact["field"]].append(fact)
    result = []
    for field, items in by_metric.items():
        # Keep a publication edition per source and measurement. Wire reprints share the same origin.
        latest = {}
        for fact in sorted(items, key=lambda f: f["at"], reverse=True):
            source = fact["source"]
            latest.setdefault((source["origin"] or source["publisher"], conditions(fact)), fact)
        distinct = list(latest.values())
        if len({fact["value"] for fact in distinct}) < 2:
            continue
        bins = defaultdict(list)
        for fact in distinct:
            bins[conditions(fact)].append(fact)
        for scope, group in bins.items():
            if all(scope) and len({f["value"] for f in group}) > 1:
                result.append({"field": field, "kind": "same_conditions", "facts": group, "conditions": scope})
        if not any(item["field"] == field for item in result):
            kind = "different_conditions" if all(all(conditions(f)) for f in distinct) else "unknown_conditions"
            result.append({"field": field, "kind": kind, "facts": distinct, "conditions": ()})
    return result
