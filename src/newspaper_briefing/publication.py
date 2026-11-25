"""Render the ranked briefing using evidence cleared for email."""

from collections import Counter
from datetime import datetime
from pathlib import Path
import re

from bs4 import BeautifulSoup
import feedparser
import requests

from .bulletins import individual_entries, select_issue_entries
from .delivery import deliver
from .editions import COMPARISON_MISSING, GROUPS, evening_entries, finalized_snapshot, morning_snapshot
from .evidence import story_evidence, verified_document
from .feeds import Settings, feed_date, plain, url_key
from .output import render, write_json, write_text
from .policy import Grant
from .primary import collect_primary, fetch_bytes
from .summaries import build_summaries, morning_overview


MLIT_FEED = "https://www.mlit.go.jp/pressrelease.rdf"
MLIT_PAGES = "https://www.mlit.go.jp/report/press/"
MLIT_RELEASE = re.compile(re.escape(MLIT_PAGES) + r"[a-z0-9_]+\.html")
DIGITAL_FEED = "https://www.digital.go.jp/rss/news.xml"
ARTICLE_LINK_LIMIT = 8
DIGITAL_SITE = "https://www.digital.go.jp/"
DIGITAL_UPDATE = re.compile(re.escape(DIGITAL_SITE) + r"(?:news|policies)(?:/[a-zA-Z0-9_-]+)+")
RESTRICTED_TEXT = re.compile(r"著作権|転載|二次利用|無断使用|提供[：:]|https?://|[\w.]+@[\w.]+")
FETCH_ERRORS = (requests.RequestException, UnicodeError, ValueError)


def press_lead(raw: bytes, link: str) -> str | None:
    page = BeautifulSoup(raw, "html.parser")
    canonical = page.find("meta", property="og:url")
    release = page.select_one("#contents.pressContents .section .clearfix")
    if (not canonical or canonical.get("content") != link or not release
            or RESTRICTED_TEXT.search(release.get_text())):
        return None
    lead = release.select_one("p.date + p")
    if not lead or lead.find(["a", "script", "iframe", "figure"]):
        return ""
    # Layout breaks are often inserted in the middle of Japanese words.
    sentences = [" ".join(line.split()) for line in lead.get_text().split("。")]
    first = sentences[0]
    return first if 15 <= len(first) <= 140 else ""


def press_documents(grant: Grant, start: datetime, end: datetime, timeout: float) -> tuple[list[dict], dict]:
    detail = {"id": grant.id, "name": grant.name, "feed": grant.url, "checked_on": grant.checked_on,
              "state": "ok", "failures": [], "feed_entries": 0, "accepted": 0}
    try:
        feed = feedparser.parse(fetch_bytes(grant.url, timeout))
        if feed.bozo or not feed.version.startswith("rss"):
            raise ValueError("報道発表RSSの形式が不正です")
    except FETCH_ERRORS as exc:
        detail.update(state="failed", failures=[type(exc).__name__])
        return [], detail
    detail["feed_entries"] = len(feed.entries)
    docs, seen = [], set()
    for entry in feed.entries:
        at = feed_date(entry.get("updated") or entry.get("published"))
        link = entry.get("link", "")
        if link.startswith("http://www.mlit.go.jp/"):
            link = "https://" + link.removeprefix("http://")
        title = plain(entry.get("title", ""), 141)
        if (not at or not start < at <= end or entry.get("author") != grant.name or link in seen
                or not MLIT_RELEASE.fullmatch(link) or not title or len(title) > 140 or RESTRICTED_TEXT.search(title)):
            continue
        seen.add(link)
        try:
            lead = press_lead(fetch_bytes(link, timeout), link)
        except FETCH_ERRORS as exc:
            detail["failures"].append(type(exc).__name__)
            continue
        if lead is None:
            continue
        docs.append({"source": grant.id, "link": link, "issued_at": at.isoformat(), "match": {"title": title},
                     "facts": [{"field": "発表内容", "value": lead or title}], "press_lead": bool(lead)})
    detail.update(accepted=len(docs), state="partial" if detail["failures"] else "ok")
    return docs, detail


def digital_documents(grant: Grant, start: datetime, end: datetime, timeout: float) -> tuple[list[dict], dict]:
    detail = {"id": grant.id, "name": grant.name, "feed": grant.url, "state": "ok", "failures": [], "accepted": 0}
    try:
        feed = feedparser.parse(fetch_bytes(grant.url, timeout))
        if feed.bozo or not feed.version.startswith("rss"):
            raise ValueError("新着・更新RSSの形式が不正です")
    except FETCH_ERRORS as exc:
        detail.update(state="failed", failures=[type(exc).__name__])
        return [], detail
    docs = []
    for entry in feed.entries:
        at = feed_date(entry.get("published"))
        link = entry.get("link", "")
        title = plain(entry.get("title", ""), 141)
        if (not at or not start < at <= end or not DIGITAL_UPDATE.fullmatch(link) or not title
                or len(title) > 140 or RESTRICTED_TEXT.search(title)):
            continue
        docs.append({"source": grant.id, "link": link, "issued_at": at.isoformat(), "match": {"title": title},
                     "facts": [{"field": "発表内容", "value": title}]})
    detail.update(feed_entries=len(feed.entries), accepted=len(docs))
    return docs, detail


def collect_publication(settings: Settings, start: datetime, end: datetime) -> dict:
    sources, documents = [], []
    jma = settings.grants.get(("primary", "jma-earthquake"))
    if jma and jma.allows("acquire", "save", "email"):
        observed = collect_primary([], settings, start, end, standalone=True)
        sources.extend(detail | {"state": observed["state"], "name": jma.name} for detail in observed["sources"])
        documents.extend(observed["documents"])
    press = settings.grants.get(("primary", "mlit-press"))
    if (press and press.adapter == "mlit_press" and press.allows("acquire", "save", "email")
            and press.url == MLIT_FEED and press.ref_prefix == MLIT_PAGES and press.name == "国土交通省"):
        releases, detail = press_documents(press, start, end, settings.timeout)
        sources.append(detail)
        documents.extend(releases)
    agency = settings.grants.get(("primary", "digital-updates"))
    if (agency and agency.adapter == "digital_updates" and agency.allows("acquire", "save", "email")
            and agency.url == DIGITAL_FEED and agency.ref_prefix == DIGITAL_SITE and agency.name == "デジタル庁"):
        updates, detail = digital_documents(agency, start, end, settings.timeout)
        sources.append(detail)
        documents.extend(updates)
    return {"sources": sources, "documents": documents}


def article_links(facts: list[dict]) -> list[dict]:
    by_publisher, seen = {}, set()
    for fact in facts:
        if fact["field"] != "見出し":
            continue
        ref = fact["source"]
        key = url_key(ref["link"])
        if key not in seen:
            by_publisher.setdefault(ref["publisher"], []).append(ref)
            seen.add(key)
    preferred = [refs[0] for refs in by_publisher.values()]
    additional = [ref for refs in by_publisher.values() for ref in refs[1:]]
    return (preferred + additional)[:ARTICLE_LINK_LIMIT]


def publication_rows(ranked: list[dict], documents: list[dict], settings: Settings, end: datetime,
                     *, supplement: bool) -> list[dict]:
    documents = [doc for doc in documents if verified_document(doc, settings, end)]
    entries = []
    for entry in ranked:
        scored = entry["scored"]
        facts = [fact for fact in story_evidence(scored, settings, documents, end, supplement=supplement)
                 if fact["source"]["email"]]
        base = next((fact for fact in facts if fact["field"] == "見出し"), None)
        if base is None:
            base = next((fact for fact in facts if fact["field"] == "発表内容"), None)
        if base is None:
            continue
        source = base["source"]
        row = {"id": scored["story"].id, "title": base["value"], "title_source": source, "rank": entry["row"]["rank"],
               "category": entry["row"]["category"], "score": scored["score"], "published_at": entry["published_at"],
               "update": "", "update_evidence": [], "article_links": article_links(facts)}
        entries.append({"scored": scored, "row": row, "published_at": entry["published_at"], "facts": facts})
    return entries


def write_publication(path: Path, report: dict, ranked: list[dict], settings: Settings,
                      snapshot: Path | None, output: Path, limit: int,
                      *, stored_primary: dict | None = None, supplement: bool = True,
                      email_ledger: Path | None = None, use_ai: bool = False) -> tuple[str, dict, Counter[str]]:
    end = report["as_of"]
    morning = report["edition"] == "morning"
    snapshot_path = snapshot.parent if snapshot and snapshot.is_file() else snapshot
    if snapshot_path and snapshot_path.name != "delivery":
        snapshot_path = snapshot_path / "delivery"
    baseline = None if morning else morning_snapshot(snapshot_path, output, end, settings, publication=True)
    state = report["collection_state"]
    collected = stored_primary if stored_primary is not None else collect_publication(settings, report["window_start"], end)
    eligible = (publication_rows(ranked, collected["documents"], settings, end, supplement=supplement)
                if state != "failed" else [])

    def evidence(entry: dict) -> list[dict]:
        return entry["facts"]

    changes = dict.fromkeys(GROUPS)
    if morning:
        entries = select_issue_entries(eligible, evidence, limit)
    else:
        entries, changes = evening_entries(eligible, baseline if eligible else None, end, evidence, state, limit=limit)
    ai_state = build_summaries(entries, evidence, enabled=use_ai, all_stories=True)
    for entry in entries:
        row = entry["row"]
        summary = row.get("summary")
        if summary and row["line"]["text"] == row["title"]:
            summary["sections_in_headline"] = ["what"]
    selected = [entry["row"] for entry in entries]
    available = report["coverage"]["available"]
    total = report["coverage"]["total"]
    note = (f"対象{total}媒体のうち{available}媒体を取得し、候補{len(ranked)}件を順位付けしました。"
            f"メール掲載の根拠を確認できたのは{len(eligible)}件です。"
            "新聞横断ニュースが根拠資料を基に作成しました。")
    if not eligible and state != "failed":
        note += "掲載できる根拠を得られず、配信を見合わせます。"
    comparison_state = "morning" if morning else "collection_failed" if state == "failed" else (
        "insufficient_evidence" if not eligible else
        "matched" if baseline else "missing"
    )
    edition = report | {
        "publication_note": note,
        "comparison": "" if morning else f"同日朝版メール {baseline['as_of']} の掲載内容との差分" if baseline
        else f"{COMPARISON_MISSING}。",
        "comparison_state": comparison_state,
        "baseline_as_of": baseline["as_of"] if baseline else None,
        "changes": changes, "stories": selected, "story_count": len(eligible),
        "excluded_for_evidence": len(ranked) - len(eligible),
        "overview": morning_overview(entries) if morning else [], "primary_sources": collected["sources"],
        "top_categories": dict(Counter(row["category"] for row in selected)), "ai_state": ai_state,
    }
    md, mail, cited = render(edition)
    mail["email_allowed"] = bool(eligible) and mail["email_allowed"]
    write_text(path / "report.md", md)
    write_text(path / "report.html", mail["html"])
    write_text(path / "report.txt", mail["text"])
    write_json(path / "mail.json", mail)
    if morning and eligible:
        write_json(path / "snapshot.json", finalized_snapshot(individual_entries(entries), end))
    elif morning:
        (path / "snapshot.json").unlink(missing_ok=True)
    if email_ledger is not None:
        edition["delivery"] = deliver(edition, email_ledger, mail, {"email"})
    write_json(path / "report.json", edition | {"mail_sha256": mail["sha256"]})
    return mail["sha256"], edition["delivery"], cited
