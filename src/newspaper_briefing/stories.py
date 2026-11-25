"""Group reports conservatively and score the evidence available in the feeds."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import math
import re
import unicodedata

from .feeds import Capture, Observation, Settings, WIRE_CREDIT, text_key, url_key, window_state


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


def ranked_time(record: Observation, cutoff: datetime) -> datetime:
    if record.updated_at and record.published_at < record.updated_at <= cutoff and material_facts(record.title):
        return record.updated_at
    return record.published_at


@dataclass(frozen=True)
class Article:
    id: str
    records: tuple[Observation, ...]
    title_record: Observation
    link_record: Observation

    @property
    def title(self) -> str:
        return self.title_record.title

    @property
    def publisher(self) -> str:
        return self.title_record.publisher

    @property
    def link(self) -> str:
        return self.link_record.link

    @property
    def published_at(self) -> datetime:
        return min(o.published_at for o in self.records)

    @property
    def keys(self) -> set[str]:
        return {f"{self.publisher}:{key}" for record in self.records for key in identity_keys(record)}


def identity_keys(record: Observation) -> set[str]:
    urls = {f"url:{url_key(value)}" for value in (record.publisher_url, record.original_url) if value}
    if record.guid:
        urls.add(f"guid:{record.guid}")
    if len(title_text(record.title)) >= 8:
        urls.add(f"title:{title_text(record.title)}")
    return urls


def deduplicate(rows: list[Observation], settings: Settings, cutoff: datetime) -> list[Article]:
    by_key, parent = {}, list(range(len(rows)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for index, row in enumerate(rows):
        for key in sorted(identity_keys(row)):
            cache_key = row.publisher, key
            parent[root(index)] = root(by_key.setdefault(cache_key, index))
    grouped = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[root(index)].append(row)

    def quality(row: Observation) -> tuple:
        return (bool(row.publisher_url), settings.feeds[row.feed].kind in {"official", "editorial"},
                settings.feeds[row.feed].primary, row.published_at, row.feed, row.link)

    articles = []
    for group in grouped.values():
        records = tuple(sorted(group, key=quality, reverse=True))
        has_primary = any(settings.feeds[o.feed].primary for o in records)
        eligible_titles = [o for o in records if settings.feeds[o.feed].primary or not has_primary]
        title_record = max(eligible_titles, key=lambda o: (ranked_time(o, cutoff), quality(o), o.title))
        link_record = max(records, key=quality)
        articles.append(Article(
            stable_id("a-", title_record.publisher, url_key(link_record.link)),
            records, title_record, link_record,
        ))
    return articles


def select_articles(capture: Capture, settings: Settings, start: datetime, end: datetime):
    rows, undated, popularity = [], [], []
    counts = Counter()
    successful = any(not r.failure and settings.feeds[r.feed].primary for r in capture.feeds)
    for record in capture.observations:
        feed = settings.feeds[record.feed]
        state = window_state(record, start, end)
        if (feed.kind != "popularity" and state == "outside_window" and record.updated_at
                and start < record.updated_at <= end and material_facts(record.title)):
            state = "in_window"
        counts["feed", record.feed, state] += 1
        if feed.kind == "popularity":
            if state == "in_window":
                popularity.append(record)
            continue
        counts["publisher", record.publisher, state] += 1
        if feed.kind == "discovery":
            counts["publisher", record.publisher, "discovery_received"] += 1
        if state == "in_window":
            rows.append(record)
        elif state == "undated":
            undated.append(record)
    articles = deduplicate(rows, settings, end)
    for article in articles:
        counts["publisher", article.publisher, "articles"] += 1
        if settings.feeds[article.title_record.feed].kind == "discovery":
            counts["publisher", article.publisher, "discovery_only"] += 1
    return successful, articles, undated, popularity, counts


def title_text(title: str) -> str:
    clean = WIRE_CREDIT.sub("", unicodedata.normalize("NFKC", title))
    clean = re.sub(r"[【\[(](?:速報|続報|独自|写真|写真特集\d+/\d+|印刷用|図解|動画)[】\])]", "", clean)
    clean = re.sub(r"\s*\|\s*(?:全国ニュース|地域ニュース)$", "", clean)
    return text_key(clean)


EVENT_TYPES = {
    "fire": r"火災|全焼|延焼|出火",
    "quake": r"地震|震度|揺れ|津波|震源",
    "storm": r"台風|大雨|洪水|浸水|線状降水帯|豪雨|大雪|暴風雪|雪(?:の)?影響",
    "election": r"知事選|市長選|大統領選|議会選|選挙|開票|当選",
    "arrest": r"逮捕|強盗|窃盗|詐欺|書類送検|起訴",
    "collision": r"衝突|追突|ひき逃げ|脱線|墜落",
    "court": r"判決|提訴|訴訟|裁判|最高裁|地裁",
    "sport": r"優勝|準優勝|決勝|選手権|リーグ戦|大相撲",
    "discovery": r"世界初|新種|新物質|観測成功|実験成功",
    "decision": r"可決|採決|法案|施行|閣議決定|正式決定",
}
LOCATIONS = re.compile(
    r"(北海道|東京都|京都府|大阪府|神奈川県|沖縄県|"
    r"(?<![一-龯々])[一-龯々]{2,4}(?:県|市|町|村|区)(?=で|に|の|は|、|・|や|と|長選|議選|付近|内|周辺|[ :：]|$))"
)
PREFECTURES = re.compile(
    r"(?<![一-龯々])(北海道|青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|埼玉|千葉|東京|神奈川|新潟|富山|石川|福井|"
    r"山梨|長野|岐阜|静岡|愛知|三重|滋賀|京都|大阪|兵庫|奈良|和歌山|鳥取|島根|岡山|広島|山口|徳島|香川|愛媛|"
    r"高知|福岡|佐賀|長崎|熊本|大分|宮崎|鹿児島|沖縄)(?=[ 　、・:：]|県|府|都|で|$)"
)
PREFECTURE_MUNICIPALITY = re.compile(r"(?:[都府県])?\s*([一-龯々]{2,6}[市町村区])"
                                     r"(?=で|に|の|は|、|・|や|と|付近|内|周辺|[ :：]|$)")
CITY_NAMES = re.compile(
    r"(?<![一-龯々])(札幌|仙台|横浜|名古屋|神戸|松山|那覇|"
    r"北九州|千葉|新潟|静岡|浜松|堺|岡山|広島|熊本)(?=市|で|の|[ 　、・:：]|$)"
)
POLICY_TARGET = re.compile(r"(?<![一-龯々ァ-ヺー])([一-龯々ァ-ヺー]{2,12})(?=の(?:関税|輸出規制|補助金|追加制裁))")
EVENT_DATES = re.compile(r"\d{1,2}月\d{1,2}日")
QUAKE_NOTICE = re.compile(r"(?:震度(?:速報|順)?|震源(?:・震度)?)\s*=\s*気象庁発表")
QUAKE_CLOCK = re.compile(r"\s*(\d{1,2})日\s*(\d{1,2})時\s*(\d{1,2})分")
FIRE_PROPERTY = re.compile(r"集合住宅|住宅|倉庫|工場|店舗|車両|アパート|学校")
FIRE_ROUNDUP = re.compile(r"火災(?:が)?\s*(?:[2-9]|[1-9][0-9]+)\s*件|"
                          r"(?:[2-9]|[1-9][0-9]+)\s*(?:件の|か所で|カ所で)火災")
FIRE_ANNIVERSARY = re.compile(r"(?:火災から|発生(?:から)?)(\d{1,2})年(?![度月\d])")
FIRE_AREA_LEAD = re.compile(r"^\s*[【「]?([一-龯々ァ-ヺー]{2,14}?)(?=\s+(?:\d+人(?:が)?死亡|死者\d+人)"
                            r"|(?:の)?火災(?:[、,\s]|$))")
DISEASE_NAME = re.compile(r"[鳥豚]?インフル(?:エンザ(?:ウイルス)?)?|[ァ-ヺA-Z0-9ー]{2,16}(?:ウイルス|感染症|熱)")
COUNTRY_ABBREVIATIONS = "米英仏独韓中露豪加印"
OUTBREAK_COUNTRY = re.compile(
    rf"(?<![一-龯々ァ-ヺー])([{COUNTRY_ABBREVIATIONS}](?:国)?|[一-龯々]{{2,8}}国)(?=で|の|[,、\s])"
)
INFECTION_COUNTS = re.compile(rf"感染者[はが: ]*(\d+)人|(\d+)人(?:が(?:{DISEASE_NAME.pattern})?(?:に)?|の)?感染")
CAPITAL_NAME = re.compile(r"(?<![一-龯々ァ-ヺー])([ァ-ヺー]{2,16}(?:国)?|[一-龯々]{1,8})首都")
ATTACK_ACTOR = re.compile(r"攻撃[\s/,、]+([ァ-ヺー]{2,16})(?:派|軍|勢力)|"
                          r"([ァ-ヺー]{2,16})(?:派|軍|勢力)?(?:が|の|による)?(?:再び|再度)?攻撃")
ATTACK_TARGET = re.compile(r"(?<![A-Z0-9ァ-ヺー])(?:[A-Z0-9]{2,16}|[ァ-ヺー]{2,12})(?:タワー|空港|基地)")
SNOW = re.compile(r"大雪|暴風雪|降雪|積雪|雪(?:が|で|の影響)")
OUTAGE_OTHER_CAUSE = re.compile(r"計画停電|(?:点検|工事)(?:で|に伴う|のため)|設備(?:の)?故障|誤操作|落雷|火災")
OUTAGE_CONTINUES = re.compile(r"停電.{0,10}(?:続く|継続|解消されず|長引)|[0-9]+日(?:間|以上)停電")
EXECUTIVE_ROLE = re.compile(r"CEO|社長|会長|代表取締役", re.I)
EXECUTIVE_MOVE = re.compile(r"復帰|辞任|就任|解任|再任|退任")
NAMED_PERSON = re.compile(r"([一-龯々ァ-ヺー]{2,8})氏")
MARKET_LEAD = re.compile(r"^(?:NY株|NYダウ|ダウ平均|米国株|日経平均|東証|為替|円相場|NY原油).{0,32}(?:反発|反落|上昇|下落|急騰|高|安|割れ)")
ACCESS_VENUE = re.compile(r"[一-龯々ァ-ヺー]{2,14}(?:正殿|博物館|美術館|資料館|記念館)")
PUBLIC_ACCESS = re.compile(r"一般公開|再開館")
INCIDENT_TIME = re.compile(r"((?:午前|午後)?\d{1,2}時(?:\d{1,2}分)?)(?:ごろ|頃)?(?:に|の)?(?:発生|出火)")
ELECTION_KEYS = re.compile(r"(?<![一-龯々])[一-龯々]{2,7}(?:市長選|知事選|町長選|村長選)")
RAIL_ACCIDENT = re.compile(r"([一-龯々ァ-ヺA-Z]{2,14}駅)(?:で|の).{0,30}(?:人身事故|はねられ|ひかれ)")
RAIL_VICTIMS = re.compile(r"(男女|男性|女性)(?:[0-9]{1,3}代(?:くらい)?の)?([0-9]+人).{0,28}(死亡|重傷)")
ROAD = re.compile(r"([一-龯々ァ-ヺ]{2,10})道(?=[ 、]|通行止め|で|の)")
ROAD_SECTION = re.compile(r"([一-龯々ァ-ヺA-Z0-9]+(?:IC|JCT))\s*[―‐−ー～~－–—-]\s*"
                          r"([一-龯々ァ-ヺA-Z0-9]+(?:IC|JCT))\s*(?:[(（]|間の)(上り|下り|上下線)(?:線)?(?:[)）]|が|で|$)")
CENSURE_TARGET = re.compile(r"(?<![一-龯々])([一-龯々]{1,4})(?:文科相|文部科学相|大臣|担当相)"
                            r"(?:への|の|に対する)?(?=問責決議案)")
CONDITIONAL = re.compile(r"訓練|誤報|シミュレーション|の想定|の場合|との想定|もしも|仮に|フィクション")


@dataclass(frozen=True)
class Features:
    text: str
    grams: frozenset[str]
    types: frozenset[str]
    places: frozenset[str]
    municipalities: frozenset[str]
    dates: frozenset[str]
    election: frozenset[str]
    ages: frozenset[str]
    policy_targets: frozenset[str]
    quake_times: frozenset[tuple[int, ...]]
    fire_properties: frozenset[str]
    casualties: frozenset[str]
    incident_times: frozenset[str]
    executive_move: str
    people: frozenset[str]
    katakana_names: frozenset[str]
    venues: frozenset[str]
    rail_incident: tuple[str, ...]
    road_incident: tuple[str, ...]
    censure_target: str
    fire_roundup: bool
    outage: str
    outage_regions: frozenset[str]
    fire_anniversaries: frozenset[tuple[str, str, str]]
    outbreak: tuple[str, ...]
    attack: tuple[str, ...]
    attack_targets: frozenset[str]


def outbreak_signature(title: str, facts: tuple[str, ...]) -> tuple[str, ...]:
    illness = DISEASE_NAME.search(title)
    country = OUTBREAK_COUNTRY.search(title)
    counts = {before or after for before, after in INFECTION_COUNTS.findall(title)}
    deaths = {fact.rsplit(":", 1)[-1] for fact in facts if fact.startswith("規模:死亡:")}
    if not illness or not country or len(counts) != 1 or len(deaths) != 1:
        return ()
    place = country.group(1)
    if len(place) == 2 and place.removesuffix("国") in COUNTRY_ABBREVIATIONS:
        place = place.removesuffix("国")
    pathogen = re.sub(r"(?<=インフル)エンザ(?:ウイルス)?$", "", illness.group())
    return place, pathogen, counts.pop(), deaths.pop()


def attack_signature(title: str, facts: tuple[str, ...]) -> tuple[str, ...]:
    capital = CAPITAL_NAME.search(title)
    actor = ATTACK_ACTOR.search(title)
    impacts = tuple(fact for fact in facts if fact.startswith(("規模:けが:", "規模:死亡:", "規模:死傷:")))
    if not capital or not actor or not impacts:
        return ()
    return capital.group(1).removesuffix("国"), actor.group(1) or actor.group(2), *impacts


def features(title: str) -> Features:
    text = title_text(title)
    normalized = unicodedata.normalize("NFKC", title)
    grams = frozenset(text[i:i + 3] for i in range(len(text) - 2) if not re.fullmatch(r"[ぁ-ゖ]{3}", text[i:i + 3]))
    kinds = frozenset(key for key, pattern in EVENT_TYPES.items() if re.search(pattern, normalized))
    locations = LOCATIONS.findall(normalized)
    cities = CITY_NAMES.findall(normalized)
    prefecture_matches = list(PREFECTURES.finditer(normalized))
    prefectures = frozenset(match.group(1) for match in prefecture_matches)
    locations.extend(town.group(1) for match in prefecture_matches
                     if (town := PREFECTURE_MUNICIPALITY.match(normalized, match.end())))
    municipalities = [place.removesuffix("市") for place in locations if place[-1] in "市町村区"]
    market = MARKET_LEAD.search(normalized)
    moves = EXECUTIVE_MOVE.findall(normalized) if not market and EXECUTIVE_ROLE.search(normalized) else []
    venues = ACCESS_VENUE.findall(normalized) if PUBLIC_ACCESS.search(normalized) else []
    if market:
        kinds |= {"market"}
    if moves:
        kinds |= {"executive"}
    if venues:
        kinds |= {"public_access"}
    facts = material_facts(normalized) if "fire" in kinds or "感染" in normalized or "攻撃" in normalized else ()
    attack = attack_signature(normalized, facts) if "攻撃" in normalized else ()
    fire_counts = {fact.rsplit(":", 1)[-1] for fact in facts
                   if "fire" in kinds
                   if fact.startswith(("規模:死亡:", "規模:けが:", "規模:行方不明:"))}
    fire_roundup = bool(FIRE_ROUNDUP.search(normalized))
    anniversaries = frozenset()
    if "fire" in kinds and not fire_roundup and (elapsed := FIRE_ANNIVERSARY.search(normalized)):
        lead = FIRE_AREA_LEAD.search(normalized)
        areas = set(municipalities + cities) or ({lead.group(1)} if lead and not FIRE_PROPERTY.search(lead.group(1)) else set())
        anniversaries = frozenset((area, elapsed.group(1), fact.rsplit(":", 1)[-1])
                                   for area in areas for fact in facts if fact.startswith("規模:死亡:"))
    if "fire" in kinds and not CONDITIONAL.search(normalized):
        fire_counts.update(f"{number}人" for number in re.findall(r"(\d+)遺体(?:発見|が|を|、|$)", normalized))
    outage = ""
    if "停電" in normalized and not re.search(r"想定|備え|訓練", normalized):
        if OUTAGE_OTHER_CAUSE.search(normalized):
            outage = "other"
        elif SNOW.search(normalized):
            outage = "snow"
        elif OUTAGE_CONTINUES.search(normalized):
            outage = "ongoing"
    rail, victims = RAIL_ACCIDENT.search(normalized), RAIL_VICTIMS.search(normalized)
    station = re.sub(r"^(?:JR|南海|近鉄|東武|西武|京王|阪急|阪神)", "", rail.group(1)) if rail else ""
    road, segment = ROAD.search(normalized), ROAD_SECTION.search(normalized)
    road_key = (road.group(1).removesuffix("自動車"), *sorted(segment.groups()[:2]), segment.group(3)) if (
        road and segment and "通行止め" in normalized) else ()
    censure = CENSURE_TARGET.search(normalized)
    return Features(
        text, grams, kinds,
        frozenset(locations + cities) | prefectures, frozenset(municipalities + cities),
        frozenset(EVENT_DATES.findall(normalized)),
        frozenset(ELECTION_KEYS.findall(normalized)), frozenset(re.findall(r"\d{1,3}歳", normalized)),
        frozenset(POLICY_TARGET.findall(normalized)),
        frozenset(tuple(map(int, match)) for match in QUAKE_CLOCK.findall(normalized)) if QUAKE_NOTICE.search(normalized) else frozenset(),
        frozenset("住宅" if prop == "集合住宅" else prop for prop in FIRE_PROPERTY.findall(normalized)) if "fire" in kinds else frozenset(),
        frozenset(fire_counts), frozenset(INCIDENT_TIME.findall(normalized)),
        moves[-1] if moves else "", frozenset(NAMED_PERSON.findall(normalized)) if moves else frozenset(),
        frozenset(re.findall(r"[ァ-ヺー]{4,}", normalized)) if moves else frozenset(),
        frozenset(venue.removeprefix("新") if venue.endswith("城正殿") else venue for venue in venues),
        (station, *victims.groups()) if station and victims else (),
        road_key,
        censure.group(1) if censure else "",
        fire_roundup, outage, prefectures if outage else frozenset(), anniversaries,
        outbreak_signature(normalized, facts) if "感染" in normalized else (),
        attack, frozenset(ATTACK_TARGET.findall(normalized)) if attack else frozenset(),
    )


def disjoint_known(first: frozenset, second: frozenset) -> bool:
    return bool(first and second and first.isdisjoint(second))


def conflicting(left: Features, right: Features) -> bool:
    return ((disjoint_known(left.types, right.types) and not (left.road_incident and left.road_incident == right.road_incident))
            or disjoint_known(left.places, right.places)
            or disjoint_known(left.municipalities, right.municipalities)
            or disjoint_known(left.dates, right.dates) or disjoint_known(left.election, right.election)
            or disjoint_known(left.policy_targets, right.policy_targets) or disjoint_known(left.quake_times, right.quake_times)
            or disjoint_known(left.incident_times, right.incident_times)
            or disjoint_known(left.fire_properties, right.fire_properties)
            or disjoint_known(left.fire_anniversaries, right.fire_anniversaries)
            or (left.outbreak and right.outbreak and left.outbreak[:2] != right.outbreak[:2])
            or (left.attack and right.attack and left.attack[:2] != right.attack[:2])
            or disjoint_known(left.attack_targets, right.attack_targets)
            or ("fire" in left.types & right.types and left.fire_roundup != right.fire_roundup)
            or (left.outage and right.outage and left.outage != right.outage and "other" in (left.outage, right.outage))
            or disjoint_known(left.outage_regions, right.outage_regions)
            or (left.road_incident and right.road_incident and left.road_incident != right.road_incident)
            or (left.rail_incident and right.rail_incident and left.rail_incident[0] != right.rail_incident[0])
            or (left.censure_target and right.censure_target and left.censure_target != right.censure_target)
            or (left.executive_move and right.executive_move and left.executive_move != right.executive_move)
            or (len(left.ages) == len(right.ages) == 1 and disjoint_known(left.ages, right.ages)))


def fire_match(left: Features, right: Features) -> bool:
    return bool(left.fire_properties & right.fire_properties and left.municipalities & right.municipalities
                and left.casualties & right.casualties and not (left.fire_roundup or right.fire_roundup))


def snow_outage_match(left: Features, right: Features) -> bool:
    modes = {left.outage, right.outage}
    return bool(left.outage_regions & right.outage_regions and
                "snow" in modes and modes <= {"snow", "ongoing"})


def outage_scope(items) -> frozenset[str] | None:
    regions = [item.outage_regions for item in items if item.outage in {"snow", "ongoing"} and item.outage_regions]
    return frozenset.intersection(*regions) if regions else None


def within_event_interval(left: Features, right: Features, delta: timedelta) -> bool:
    if left.rail_incident and right.rail_incident or left.road_incident and right.road_incident:
        return abs(delta) <= timedelta(hours=8)
    if fire_match(left, right):
        return abs(delta) <= timedelta(hours=24)
    if left.attack and left.attack == right.attack:
        return abs(delta) <= timedelta(hours=12)
    if left.fire_anniversaries & right.fire_anniversaries or left.outbreak and left.outbreak == right.outbreak:
        return abs(delta) <= timedelta(hours=36)
    return True


def same_named_event(left: Features, right: Features) -> bool:
    executive = (left.executive_move and left.executive_move == right.executive_move
                 and left.people & right.people and left.katakana_names & right.katakana_names)
    transport = ((left.rail_incident and left.rail_incident == right.rail_incident)
                 or (left.road_incident and left.road_incident == right.road_incident))
    return bool(transport or (left.censure_target and left.censure_target == right.censure_target)
                or executive or left.venues & right.venues or left.election & right.election
                or left.quake_times & right.quake_times or left.fire_anniversaries & right.fire_anniversaries
                or (left.outbreak and left.outbreak == right.outbreak)
                or (left.attack and left.attack == right.attack)
                or fire_match(left, right) or snow_outage_match(left, right))


def event_keys(item: Features) -> set[tuple]:
    keys = {(kind, value) for kind, values in (("venue", item.venues), ("election", item.election),
                                               ("quake", item.quake_times), ("outage", item.outage_regions))
            for value in values}
    keys.update((kind, value) for kind, value in (("rail", item.rail_incident), ("road", item.road_incident),
                                                  ("censure", item.censure_target), ("outbreak", item.outbreak),
                                                  ("attack", item.attack)) if value)
    keys.update(("fire", city, prop, affected) for city in item.municipalities
                for prop in item.fire_properties for affected in item.casualties)
    keys.update(("fire_anniversary", *anniversary) for anniversary in item.fire_anniversaries)
    keys.update(("executive", item.executive_move, name, organization)
                for name in item.people for organization in item.katakana_names)
    return keys


def similarity(left: Features, right: Features) -> float:
    if conflicting(left, right):
        return 0
    if left.text == right.text and len(left.text) >= 8:
        return 1
    if same_named_event(left, right):
        return 0.85
    same_fire_context = left.fire_properties & right.fire_properties and left.municipalities & right.municipalities
    shared = len(left.grams & right.grams)
    if shared < 5:
        return 0
    overlap = 2 * shared / (len(left.grams) + len(right.grams))
    longest = SequenceMatcher(None, left.text, right.text, autojunk=False).find_longest_match().size
    if same_fire_context and longest >= 9 and shared >= 7:
        return max(0.50, overlap)
    same_outage = ("storm" in left.types & right.types and left.places & right.places
                   and "停電" in left.text and "停電" in right.text)
    if same_outage and longest >= 9 and shared >= 7:
        return max(0.50, overlap)
    if longest >= 7 and overlap >= 0.48:
        return overlap
    if longest >= 10 and shared >= 8 and overlap >= 0.31 and left.types & right.types and left.places & right.places:
        return overlap
    return 0


@dataclass
class Story:
    id: str
    articles: list[Article]

    def facts(self, cutoff: datetime) -> dict[str, tuple[datetime, Observation]]:
        facts = {}
        for article in self.articles:
            for record in article.records:
                at = ranked_time(record, cutoff)
                for fact in material_facts(record.title):
                    if fact not in facts or at < facts[fact][0]:
                        facts[fact] = at, record
        return facts


def cluster(articles: list[Article], settings: Settings) -> list[Story]:
    by_gram, by_event = defaultdict(set), defaultdict(set)
    groups = []
    fingerprints = []
    articles = sorted(articles, key=lambda a: (not settings.feeds[a.title_record.feed].primary, a.published_at, a.id))
    for article in articles:
        item = features(article.title)
        keys = event_keys(item)
        candidates = set().union(*(by_gram[g] for g in item.grams))
        candidates.update(set().union(*(by_event[key] for key in keys)))
        matches, named_matches = [], []
        for index in candidates:
            group = fingerprints[index]
            if outage_scope([item, *group]) == frozenset():
                continue
            if any(conflicting(item, other) or not within_event_interval(
                    item, other, article.published_at - existing.published_at)
                   for other, existing in zip(group, groups[index])):
                continue
            named_count = sum(same_named_event(item, other) for other in group)
            if named_count:
                named_matches.append((named_count, -index))
                continue
            scores = [similarity(item, other) for other in group]
            if all(scores):
                matches.append((sum(scores) / len(scores), index))
        matches.sort(reverse=True)
        chosen = None
        if named_matches:
            if not item.fire_properties or len(named_matches) == 1:
                chosen = -max(named_matches)[1]
        elif matches and (len(matches) == 1 or matches[0][0] - matches[1][0] > 0.05):
            chosen = matches[0][1]
        if chosen is not None:
            index = chosen
            groups[index].append(article)
            fingerprints[index].append(item)
        else:
            index = len(groups)
            groups.append([article])
            fingerprints.append([item])
        for gram in item.grams:
            by_gram[gram].add(index)
        for key in keys:
            by_event[key].add(index)
    return [Story(stable_id("s-", group[0].id), group) for group in groups]


COUNT_TERMS = {
    "死亡": r"死亡者?|死者|遺体(?:を?発見)?", "けが": r"負傷者?|けが人?", "死傷": r"死傷者?",
    "行方不明": r"(?:行方)?不明者?|(?:と)?連絡(?:が)?取れず", "避難": r"避難者?", "全壊": r"全壊",
}
COUNT_VALUE = r"(?:計|少なくとも|約)?([0-9][0-9,]*万?(?:人|棟|世帯))"
COUNT_FACTS = {
    key: (re.compile(rf"(?:{terms})[はが: ]*{COUNT_VALUE}"),
          re.compile(rf"{COUNT_VALUE}(?:が|の|の住宅が)?(?:{terms})"))
    for key, terms in COUNT_TERMS.items()
}
RESULT_FACT = re.compile(r"(?:[一-龯々ァ-ヺー]{1,12}氏[がはの]?)?(?:当選確実|当選|落選)(?!者|候補|する|めざ|を目指|の条件)")
DECISION_FACTS = {
    "閣議決定": r"閣議(?:で)?決定", "正式決定": r"正式(?:に)?決定", "可決": r"可決",
    "成立": r"(?:法案|法律|改正法|条例|予算)(?:が)?成立", "公式発表": r"公式(?:に)?発表",
    "津波警報の発表": r"津波警報(?:を)?発表", "津波警報の解除": r"津波警報(?:を)?解除",
    "避難指示の発令": r"避難指示(?:を)?発令", "避難指示の解除": r"避難指示(?:を)?解除",
    "問責決議案の提出方針で一致": r"問責決議案.{0,12}?提出(?:の)?方針で一致",
}
CORRECTION = re.compile(r"^(?:[【\[(]訂正[】\])]|訂正[:： ])|と訂正(?:した|$)")


def material_facts(title: str) -> tuple[str, ...]:
    title = unicodedata.normalize("NFKC", title)
    if CONDITIONAL.search(title):
        return ()
    facts = [f"規模:{key}:{match.replace(',', '')}" for key, patterns in COUNT_FACTS.items()
             for pattern in patterns for match in pattern.findall(title)]
    if re.search(EVENT_TYPES["election"], title) and not re.search(r"へ(?:の|$)|可能性|目指|に向け|予想|情勢", title):
        facts.extend(f"選挙結果:{text_key(m.group())}" for m in RESULT_FACT.finditer(title))
    for result, pattern in DECISION_FACTS.items():
        for m in re.finditer(pattern, title):
            if not re.match(r"\s*(?:へ|方針|予定|見通し|か[？?]?$|を目指|を求め|の可能性)", title[m.end():]):
                facts.append(f"決定・発表:{result}")
    if CORRECTION.search(title):
        facts.append(f"訂正:{title_text(title)}")
    return tuple(sorted(set(facts)))


CATEGORIES = (
    ("災害", r"地震|震度|津波|台風|豪雨|洪水|土砂災害|避難指示|線状降水帯|大雨警報|豪雪|噴火|雪.{0,20}停電"),
    ("スポーツ", r"優勝|選手権|プレーオフ|リーグ戦|W杯|ワールドカップ|大リーグ|大相撲|甲子園|大谷翔平|Jリーグ|フィギュア"),
    ("科学", r"宇宙|研究チーム|研究所|天文|新種|気候変動|科学|論文|ワクチン|治験|観測衛星|承認申請"),
    ("文化", r"世界遺産|文化財|正殿|伝統芸能|展覧会|文学賞|映画|美術|音楽|演劇|歌舞伎|博物館|コンサート|芸術"),
    ("経済", r"日銀|物価|賃金|円相場|為替|企業|株|決算|利上げ|利下げ|経済|景気|市場|倒産|料金改定|値上げ|原油|有価証券報告書|CEO"),
    ("国際", r"国連|ICC|紛争|停戦|戦争|米国|米大統領|中国(?!地方|新聞|電力)|ロシア|ウクライナ|イスラエル|ガザ|韓国|EU|NATO"),
    ("政治", r"国会|政府|首相|大臣|閣議|参院|衆院|選挙|知事選|市長選|補選|法案|議会|外交|内閣|与党|野党"),
)
# Each tier requires a consequence stated in the feed, such as a warning, service change or enacted decision.
IMPACT = (
    (4, "住民の安全・生命への重大な影響", r"(?:気象庁|自治体|県|市).{0,32}(?:大津波警報|津波警報|特別警報|緊急安全確保)|(?:震度[67]|大津波警報).{0,24}(?:観測|発表|発令)|緊急安全確保を発令"),
    (4, "広い範囲に適用される制度・金融政策の決定", r"(?:日銀|日本銀行).{0,28}(?:利上げ|利下げ).{0,14}決定|(?:国会|衆院|参院).{0,28}(?:法案|予算).{0,16}(?:可決|成立)|(?:全国|全世帯).{0,35}(?:義務化|支給決定|制度開始)"),
    (3, "人命や住民の移動・生活への具体的な影響", r"(?:[1-9]\d+|[1-9]\d*万)人(?:が)?(?:死亡|死傷|避難|けが|負傷)|死者[はが ]?\d{2,}人|避難指示を発令|(?:鉄道|新幹線|空港|電力|水道).{0,20}(?:全線運休|全面停止|大規模停電|断水)|(?:断水|停電).{0,15}(?:[1-9]\d{3,}|[1-9]\d*,\d{3})戸.{0,8}影響|(?:停戦|和平合意).{0,20}(?:発効|成立|合意)|運航を全面停止"),
    (3, "患者・利用者への新たな制度や治療の適用", r"(?:厚労省|厚生労働省).{0,24}(?:承認|接種開始)|新薬.{0,15}正式承認|補助(?:金|制度).{0,14}(?:創設|支給決定)"),
    (2, "地域の安全や公共サービスへの影響", r"(?:津波注意報|震度[45]|避難所を開設|\d+人(?:が)?(?:死亡|負傷|けが)|住宅.{0,12}全焼|運転見合わせ|通行止め|断水|閉校|運休|停電|通学路.{0,12}変更|料金.{0,10}値上げ|避難バス.{0,12}運行)"),
    (2, "研究・文化・競技における具体的な成果", r"世界記録.{0,8}(?:更新|樹立)|金メダル.{0,8}(?:獲得|取得)|世界遺産(?:へ|に)登録(?:決定)?|世界初.{0,20}(?:成功|確認)|(?:新種|新物質).{0,12}(?:確認|発見)"),
    (2, "文化施設の公開・来訪者の利用に関する具体的な変更", r"(?:正殿|文化財|史跡|城跡|博物館|美術館).{0,15}(?:一般公開(?:が|を)?(?:再開|始ま|開始)|再開館)"),
    (1, "地域・当事者に具体的な結果が出た出来事", r"選(?:挙|.{0,14})当選|当選確実|正式決定|可決|施設.{0,10}(?:開設|閉鎖)|サービス.{0,8}(?:開始|終了)|優勝を決め|有罪判決"),
)
UNCERTAIN_IMPACT = re.compile(r"決定(?:へ|か|の見通し|なら)|発令(?:する場合|へ|を想定)|可能性を想定|との仮定"
                              r"|(?:人|棟|世帯)(?:死亡|死傷|不明|超|以上)?(?:か(?:[、。?,\s]|$)|の可能性)")
OPINION = re.compile(r"^[【\[]?(?:社説|論説|コラム|寄稿|天声人語|余録|編集手帳|正論|オピニオン)"
                     r"|[【\[](?:社説|論説|コラム|寄稿|正論|オピニオン)[】\]]")


@dataclass(frozen=True)
class ScorePart:
    points: float
    maximum: int
    reasons: tuple[dict, ...]
    missing: bool


def impact_reason(article: Article) -> tuple[int, str, Observation]:
    result = 0, "", article.title_record
    for record in article.records:
        text = unicodedata.normalize("NFKC", record.title)
        if CONDITIONAL.search(text) or UNCERTAIN_IMPACT.search(text) or OPINION.search(text):
            continue
        for tier, reason, pattern in IMPACT:
            if tier > result[0] and re.search(pattern, text):
                result = tier, reason, record
    return result


def independent_origins(story: Story) -> dict[str, Observation]:
    # Identical headlines may be unattributed copies. Give variants of the same article at most one vote.
    confirmed, used_titles = {}, set()
    article_sources = sorted(story.articles, key=lambda a: (not bool(a.link_record.publisher_url), a.id))
    for article in article_sources:
        origins = {o.reporting_group for o in article.records if o.reporting_group}
        aliases = {title_text(o.title) for o in article.records}
        if len(origins) != 1 or any(o.origin_basis == "配信元表記に不一致" for o in article.records):
            continue
        if not aliases & used_titles:
            group = next(iter(origins))
            confirmed.setdefault(group, next(o for o in article.records if o.reporting_group == group))
        used_titles.update(aliases)
    return confirmed


def assess(story: Story, settings: Settings, cutoff: datetime) -> dict:
    def evidence(record: Observation, note: str) -> dict:
        return {"publisher": record.publisher, "link": record.link, "title": record.title, "reason": note}

    impacts = [(impact_reason(a), a.id) for a in story.articles]
    (level, reason, record), _ = max(impacts, key=lambda item: (item[0][0], bool(item[0][2].publisher_url), item[1]))
    origins = independent_origins(story)
    editorials = {}
    for a in story.articles:
        for o in a.records:
            if settings.feeds[o.feed].kind == "editorial":
                editorials.setdefault(settings.publishers[o.publisher].reporting_group, o)
    first_at = min(a.published_at for a in story.articles)
    facts = story.facts(cutoff)
    latest_fact = max(facts.values(), default=None, key=lambda value: value[0])
    latest = max(first_at, latest_fact[0]) if latest_fact else first_at
    hours = (cutoff - latest).total_seconds() / 3600
    recent_record = latest_fact[1] if latest_fact and latest_fact[0] >= first_at else min(
        (o for a in story.articles for o in a.records), key=lambda o: o.published_at)
    weights = settings.weights
    parts = {
        "impact": ScorePart(round(weights["impact"] * level / 4, 2), weights["impact"],
                            (evidence(record, reason),) if level else (), not level),
        "breadth": ScorePart(round(weights["breadth"] * (1 - math.pow(0.65, len(origins))), 2),
                             weights["breadth"], tuple(evidence(o, group) for group, o in sorted(origins.items())), not origins),
        "recency": ScorePart(round(weights["recency"] * max(0, 1 - hours / 24), 2), weights["recency"],
                              (evidence(recent_record, latest.isoformat()),), False),
        "editorial": ScorePart(round(weights["editorial"] * min(1, len(editorials) / 2), 2),
                                weights["editorial"], tuple(evidence(o, settings.feeds[o.feed].title) for o in editorials.values()),
                                not editorials),
    }
    main = max(story.articles, key=lambda a: (
        impact_reason(a)[0], bool(a.link_record.publisher_url), not OPINION.search(a.title), a.published_at, a.id,
    ))
    category = next((label for label, pattern in CATEGORIES if re.search(pattern, unicodedata.normalize("NFKC", main.title))), "社会")
    return {
        "story": story, "main": main, "category": category, "latest": latest, "parts": parts,
        "fire_roundup": bool(FIRE_ROUNDUP.search(unicodedata.normalize("NFKC", main.title))),
        "score": round(sum(part.points for part in parts.values()), 2),
        "publisher_count": len({a.publisher for a in story.articles}),
        "independent_count": len(origins),
    }


def rank(stories: list[Story], settings: Settings, cutoff: datetime) -> list[dict]:
    scored = [assess(story, settings, cutoff) for story in stories]
    return sorted(scored, key=lambda row: (-row["score"], -row["parts"]["impact"].points,
                                           -row["latest"].timestamp(), row["story"].id))
