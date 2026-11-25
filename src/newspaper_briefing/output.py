"""Render newsletter content and atomically publish preview artifacts."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from .editions import COLLECTION_FAILED, GROUPS
from .feeds import GOOGLE_HOST, Settings, host, timestamp, url_key
from .stories import title_text
from .summaries import SECTIONS


def source_row(article, settings: Settings) -> dict:
    chosen = article.link_record
    publisher = settings.publishers[article.publisher]
    return {
        "article_id": article.id, "publisher": publisher.id, "name": publisher.name,
        "headline": article.title, "link": chosen.link,
        "link_kind": "google" if host(chosen.link) == GOOGLE_HOST else "publisher",
        "published_at": article.published_at,
        "personal_only": any(settings.feeds[o.feed].personal_only for o in article.records),
    }


def story_row(row: dict, settings: Settings, popularity: list) -> dict:
    story, main = row["story"], row["main"]
    ordered = [main, *sorted((a for a in story.articles if a != main),
                             key=lambda a: (not bool(a.link_record.publisher_url), a.publisher, a.id))]
    popular = []
    for a in ordered:
        for item in popularity:
            if (a.publisher == item.publisher and
                    (url_key(a.link) == url_key(item.link) or title_text(a.title) == title_text(item.title))):
                popular.append({"publisher": item.publisher, "input_feed": item.feed, "link": item.link})
    return {
        "id": story.id, "title": main.title, "category": row["category"], "score": row["score"],
        "latest": row["latest"], "score_parts": {k: asdict(v) for k, v in row["parts"].items()},
        "publisher_count": row["publisher_count"], "independent_count": row["independent_count"],
        "fire_roundup": row["fire_roundup"],
        "update": "", "update_evidence": [],
        "sources": [source_row(a, settings) for a in ordered], "publisher_popularity": popular,
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", newline="\n", delete=False) as temp:
        temp_name = Path(temp.name)
        try:
            temp.write(content)
        except BaseException:
            temp.close()
            temp_name.unlink(missing_ok=True)
            raise
    try:
        temp_name.replace(path)
    finally:
        temp_name.unlink(missing_ok=True)


def write_json(path: Path, data: dict | list) -> None:
    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported type: {type(value)}")

    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=encode) + "\n")


def display_source(row: dict) -> str:
    return row["name"] + ("（Googleニュース経由）" if row["link_kind"] == "google" else "")


def md(text: str) -> str:
    return re.sub(r"([\\\x60*{}\[\]()#+.!_|~])", r"\\\1", escape(str(text), quote=False))


def md_link(row: dict) -> str:
    return f"[{md(display_source(row))}](<{row['link']}>)"


def html_link(row: dict) -> str:
    return (f'<a href="{escape(row["link"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(display_source(row))}</a>')


def local_time(value: datetime | str) -> str:
    at = timestamp(value) if isinstance(value, str) else value
    return f"{at.month}月{at.day}日 {at:%H:%M}"


def run_messages(report: dict) -> list[str]:
    result = []
    if report.get("publication_note"):
        result.append(report["publication_note"])
    if report["collection_state"] == "failed":
        result.append(f"有効な媒体フィードを取得できませんでした。{COLLECTION_FAILED}。")
    if report["collection_state"] != "ok":
        coverage = report.get("coverage", {})
        missing = "、".join(coverage.get("missing", []))
        partial = "、".join(coverage.get("partial", []))
        result.append(f"確認済み {coverage.get('available', 0)}/{coverage.get('total', 0)}媒体"
                      + (f"。未取得 {missing}" if missing else "")
                      + (f"。一部未取得 {partial}" if partial else "") + "。")
    result.append(f"対象期間 {local_time(report['window_start'])}〜{local_time(report['as_of'])}（日本時間）")
    if report["comparison"]:
        result.append(report["comparison"])
    if report.get("comparison_state") == "missing":
        result.append("続報・訂正の件数は判定できません。")
    return result


def change_statement(change: dict) -> dict:
    before, after = change["before"], change["after"]
    from_value = before["value"] if before else "朝版では未確認"
    to_value = after["value"]
    if before and before["source"]["name"] != after["source"]["name"]:
        from_value += f"（{before['source']['name']}）"
        to_value += f"（{after['source']['name']}）"
    refs = [fact["source"] for fact in (before, after) if fact]
    return {"text": f"{after['field']}　{from_value} → {to_value}",
            "sources": list({source["id"]: source for source in refs}.values())}


def issue_content(report: dict) -> list[dict]:
    items = []

    def line(text: str, *, kind: str = "paragraph", refs=(), story_id: str = ""):
        items.append({"kind": kind, "text": text, "sources": refs, "story_id": story_id})

    def citation_line(block: dict, prefix: str = "", *, kind: str = "paragraph", story_id: str = ""):
        line(prefix + block["text"], kind=kind, refs=block["sources"], story_id=story_id)

    for message in run_messages(report):
        line(message)
    stories = report["stories"]
    if report["collection_state"] == "failed":
        return items
    morning = report["edition"] == "morning"
    comparison = not morning and report["comparison_state"] == "matched"
    if comparison:
        counts = report["changes"]
        label = "確認できた範囲では" if report["collection_state"] == "partial" else ""
        line(label + "掲載 " + "・".join(f"{kind}{counts[kind]}件" for kind in GROUPS))
        if not stories:
            line("掲載する更新はありません。" if not label else "確認できた範囲では掲載する更新はありません。")
    elif not stories:
        line("対象期間に掲載できるニュースはありません。" if morning else "指定時間帯に確認できた新着はありません。")

    positioned = list(enumerate(stories, 1))
    if comparison:
        groups = [
            ("訂正", [(index, story, "訂正") for index, story in positioned
                    if any(change["kind"] == "訂正" for change in story["update_evidence"])]),
            ("重要な続報", [(index, story, "続報") for index, story in positioned
                       if any(change["kind"] == "続報" for change in story["update_evidence"])]),
            ("新規・朝版未掲載", [(index, story, story["update"]) for index, story in positioned
                           if story["update"] in {"新規", "朝版未掲載"}]),
        ]
    elif morning:
        lead_count = len(report["overview"])
        groups = [("朝の要点", [(i, s, "") for i, s in positioned[:lead_count]]),
                  ("主要ニュース", [(i, s, "") for i, s in positioned[lead_count:5]]),
                  ("そのほかのニュース", [(i, s, "") for i, s in positioned[5:]])]
    else:
        groups = [("指定時間帯の新着", [(i, s, "") for i, s in positioned])]
    titled = set()
    for group, entries in groups:
        if not entries:
            continue
        line(group, kind="section")
        for number, story, update in entries:
            story_id = story["id"]
            important = comparison and number == 1
            timing = f"［朝版未掲載・公表 {local_time(story['published_at'])}］" if update == "朝版未掲載" else ""
            if story.get("fire_roundup"):
                timing += "［複数の出来事の概況］"
            summary = story.get("summary")
            if not summary:
                citation_line(story["line"] | {"text": story["line"]["text"] + timing}, f"{number}. ",
                              kind="bullet", story_id=story_id)
                continue
            changes = story["update_evidence"]
            sections_in_headline = summary.get("sections_in_headline", ("what",) if summary.get("what_is_headline") else ())
            compact = summary.get("compact", False)
            repeated = story_id in titled
            if not repeated:
                label = SECTIONS["what"] + "　" if not compact and "what" in sections_in_headline else ""
                if important and not changes:
                    label = "最も重要な変化　"
                references = [story["title_source"]] if story.get("title_source") else ()
                line(f"{number}. {label}{story['title']}{timing}", kind="article", refs=references, story_id=story_id)
                titled.add(story_id)
            for index, change in enumerate(changes):
                if change["kind"] == update:
                    label = f"最も重要な変化（{update}）" if important and index == 0 else update
                    if repeated:
                        label = f"{number}. {label}"
                    citation_line(change_statement(change), label + "　", story_id=story_id)
            if update == "訂正" and any(change["kind"] == "続報" for change in changes):
                continue
            line(summary["status"])
            sections = {key: section for key, section in summary["sections"].items() if key not in sections_in_headline}
            if compact:
                supported = [section for section in sections.values() if section["fact_ids"]]
                if supported:
                    text = "。".join(section["text"].rstrip("。") for section in supported) + "。"
                    refs = [source for section in supported for source in section["sources"]]
                    line(text, kind="bullet", refs=refs, story_id=story_id)
            else:
                for key, section in sections.items():
                    citation_line(section, f"{SECTIONS[key]}　", kind="bullet", story_id=story_id)
            for note in summary["notes"]:
                citation_line(note, story_id=story_id)
            line("関連記事", kind="links", refs=story.get("article_links", ()), story_id=story_id)
    return items


def mail_digest(subject: str, text: str, html: str) -> str:
    return sha256(json.dumps([subject, text, html], ensure_ascii=False).encode("utf-8")).hexdigest()


def render(report: dict) -> tuple[str, dict, Counter[str]]:
    content = issue_content(report)
    subject = report["subject"]
    plain_lines, md_lines = [subject, ""], [f"# {md(subject)}", ""]
    markup = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(subject)}</title></head>",
        '<body style="margin:0;background:#f3f5f7;color:#172731;font:16px/1.6 system-ui,sans-serif">'
        '<main style="max-width:680px;margin:0 auto;background:white;padding:24px">',
        f"<h1 style='line-height:1.35'>{escape(subject)}</h1>",
    ]
    shown, links, cited = set(), set(), set()
    for item in content:
        text, kind, story = item["text"], item["kind"], item["story_id"]
        refs = []
        for ref in item["sources"]:
            publisher = ref["publisher"]
            key = story, publisher, url_key(ref["link"])
            if key in shown:
                continue
            shown.add(key)
            refs.append(ref)
            links.add(ref["link"])
            if story and ref["kind"] != "primary":
                cited.add((publisher, story))
        if kind == "links" and not refs:
            continue
        line = "・" if kind == "bullet" else ""
        plain_lines.append(line + text)
        plain_lines.extend(f"  {display_source(ref)} {ref['link']}" for ref in refs)
        if kind != "bullet":
            plain_lines.append("")
        md_prefix = {"section": "## ", "article": "### ", "bullet": "- "}.get(kind, "")
        md_lines.append(md_prefix + md(text) + ("　" + "、".join(md_link(ref) for ref in refs) if refs else ""))
        md_lines.append("")
        tag = {"section": "h2", "article": "h3"}.get(kind, "p")
        style = (' style="margin-top:28px;border-bottom:2px solid #42617a"'
                 if kind == "section" else ' style="margin:8px 0"')
        html_links = (" <small>" + "、".join(html_link(ref) for ref in refs) + "</small>") if refs else ""
        markup.append(f"<{tag}{style}>{line}{escape(text)}{html_links}</{tag}>")
    markup.append("</main></body></html>")
    text = "\n".join(plain_lines).rstrip() + "\n"
    html = "\n".join(markup) + "\n"
    mail = {
        "version": 1, "edition": report["edition"], "as_of": report["as_of"], "subject": subject,
        "text": text, "html": html, "link_count": len(links), "sha256": mail_digest(subject, text, html),
        "email_allowed": (report["collection_state"] != "failed" and not report["offline"]
                          and all(story.get("title_source") for story in report["stories"])
                          and all(ref.get("email") is True for item in content for ref in item["sources"])),
    }
    return "\n".join(md_lines), mail, Counter(publisher for publisher, _ in cited)
