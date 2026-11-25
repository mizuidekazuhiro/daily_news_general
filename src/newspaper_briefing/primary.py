"""Read narrowly scoped public earthquake bulletins from the JMA XML service."""

from datetime import datetime
import json
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree as ET

import requests

from .evidence import primary_reference
from .feeds import JST, Settings, timestamp
from .stories import QUAKE_CLOCK, QUAKE_NOTICE


ATOM = "{http://www.w3.org/2005/Atom}"
REPORT = "{http://xml.kishou.go.jp/jmaxml1/}Report"
JMA_TITLE = "震源・震度に関する情報"
DOCUMENT_CAP = 8
LIMIT_BYTES = 2_000_000
INTENSITIES = {"1": "1", "2": "2", "3": "3", "4": "4", "5-": "5弱", "5+": "5強", "6-": "6弱", "6+": "6強", "7": "7"}
JMA_URL = "https://www.data.jma.go.jp/developer/xml/feed/eqvol_l.xml"
JMA_DOCUMENTS = "https://www.data.jma.go.jp/developer/xml/data/"


def empty_primary(state: str) -> dict:
    return {"version": 1, "documents": [], "sources": [], "state": state}


def read_primary(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (data.get("version") != 1 or not isinstance(data.get("sources"), list)
            or not isinstance(data.get("state"), str) or not isinstance(data.get("documents"), list) or not all(
        isinstance(document, dict) and isinstance(document.get("match"), dict) for document in data["documents"]
    )):
        raise ValueError("一次情報の入力ファイルが不正です")
    return data


def fetch_bytes(url: str, timeout: float) -> bytes:
    with requests.get(url, timeout=timeout, stream=True, allow_redirects=False,
                      headers={"User-Agent": "NewspaperBriefing/2.0 public information reader"}) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_content(32768):
            body.extend(chunk)
            if len(body) > LIMIT_BYTES:
                raise ValueError("一次資料が取得上限を超過")
        return bytes(body)


def fetch_xml(url: str, timeout: float) -> ET.Element:
    body = fetch_bytes(url, timeout)
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("XML の外部定義は対象外")
    return ET.fromstring(body)


def bulletin(root: ET.Element, link: str, updated: datetime) -> dict | None:
    if (root.tag != REPORT or root.findtext("{*}Control/{*}Title") != JMA_TITLE
            or root.findtext("{*}Control/{*}Status") != "通常"
            or root.findtext("{*}Control/{*}PublishingOffice") != "気象庁"):
        return None
    head = root.find("{*}Head")
    earthquake = root.find("{*}Body/{*}Earthquake")
    observation = root.find("{*}Body/{*}Intensity/{*}Observation")
    if head is None or earthquake is None or observation is None or head.findtext("{*}InfoType") not in {"発表", "訂正"}:
        return None
    reported = timestamp(head.findtext("{*}ReportDateTime", ""))
    occurred = timestamp(earthquake.findtext("{*}OriginTime", ""))
    published = max(reported, updated, timestamp(root.findtext("{*}Control/{*}DateTime", "")))
    location = earthquake.findtext("{*}Hypocenter/{*}Area/{*}Name", "")
    maximum = INTENSITIES.get(observation.findtext("{*}MaxInt"))
    event_id = head.findtext("{*}EventID", "")
    if not location or not maximum or not event_id.isascii() or not event_id.isdecimal():
        return None
    measurement = occurred.isoformat()
    facts = [
        {"field": "発表内容", "value": f"{occurred.month}月{occurred.day}日{occurred.hour}時"
         f"{occurred.minute:02d}分ごろ、{location}を震源とする地震があり、最大震度{maximum}が観測されました",
         "scope": event_id, "definition": "震源と観測震度", "measured_at": measurement},
        {"field": "最大震度", "value": maximum, "scope": "公開電文内の観測地点",
         "definition": "JMA震度階級", "measured_at": measurement},
    ]
    magnitude = earthquake.find("{*}Magnitude")
    if magnitude is not None and magnitude.get("type") == "Mj" and re.fullmatch(r"\d[.]\d", magnitude.text or ""):
        facts.append({"field": "マグニチュード", "value": magnitude.text, "scope": event_id,
                      "definition": "気象庁マグニチュード Mj", "measured_at": measurement})
    areas = [area.findtext("{*}Name", "") for area in observation.findall("{*}Pref/{*}Area")
             if INTENSITIES.get(area.findtext("{*}MaxInt")) == maximum]
    if areas and all(areas) and len(areas) <= 5:
        facts.append({"field": "影響", "value": f"{'、'.join(areas)}で震度{maximum}を観測",
                      "scope": event_id, "definition": "最大震度を観測した細分区域", "measured_at": measurement})
    return {"source": "jma-earthquake", "link": link, "issued_at": published.isoformat(),
            "match": {"reported_at": reported.isoformat(), "event_id": event_id},
            "correction": head.findtext("{*}InfoType") == "訂正", "facts": facts}


def collect_primary(articles: list, settings: Settings, start: datetime, end: datetime,
                    *, standalone: bool = False) -> dict:
    result = empty_primary("ok")
    grant = settings.grants.get(("primary", "jma-earthquake"))
    if (not grant or grant.adapter != "jma_earthquake" or grant.kind != "primary"
            or grant.url != JMA_URL or grant.ref_prefix != JMA_DOCUMENTS or not grant.allows("acquire", "save")):
        return empty_primary("not_enabled")
    summary = {"id": grant.id, "feed": grant.url, "requested": 0, "accepted": 0, "failures": []}
    result["sources"].append(summary)
    wanted = set()
    for article in articles:
        for row in article.records:
            title = unicodedata.normalize("NFKC", row.title)
            if QUAKE_NOTICE.search(title):
                wanted.update((row.published_at.date(), *map(int, match)) for match in QUAKE_CLOCK.findall(title))
    if not wanted and not standalone:
        result["state"] = "no_matching_headlines"
        return result
    try:
        entries = fetch_xml(grant.url, settings.timeout).findall(f"{ATOM}entry")
    except (requests.RequestException, ET.ParseError, ValueError) as exc:
        summary["failures"].append(type(exc).__name__)
        result["state"] = "failed"
        return result
    used = set()
    for entry in entries:
        if entry.findtext(f"{ATOM}title") != JMA_TITLE or summary["requested"] >= DOCUMENT_CAP:
            continue
        try:
            updated = timestamp(entry.findtext(f"{ATOM}updated", ""))
        except ValueError:
            continue
        if not start < updated <= end:
            continue
        if not standalone and (updated.date(), updated.day, updated.hour, updated.minute) not in wanted:
            continue
        elem = entry.find(f"{ATOM}link")
        url = elem.get("href", "") if elem is not None else ""
        if (url in used or primary_reference(grant, url) is None
                or not re.fullmatch(re.escape(JMA_DOCUMENTS) + r"\d{14}_\d+_VXSE53_\d+\.xml", url)):
            continue
        used.add(url)
        summary["requested"] += 1
        try:
            document = bulletin(fetch_xml(url, settings.timeout), url, updated)
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            summary["failures"].append(type(exc).__name__)
            continue
        if document and timestamp(document["issued_at"]) <= end:
            result["documents"].append(document)
    summary["accepted"] = len(result["documents"])
    summary["captured_at"] = datetime.now(JST).isoformat()
    if summary["failures"]:
        result["state"] = "partial" if summary["accepted"] else "failed"
    return result
