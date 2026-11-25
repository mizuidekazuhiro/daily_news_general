"""Opt-in delivery with a local, persistent reservation for each date and channel."""

from contextlib import closing
from email.message import EmailMessage
from email.utils import parseaddr
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
from uuid import UUID

import requests

from .output import SECTIONS, display_source, run_messages


PREFIX = "NEWSPAPER_BRIEFING_"
SMTP_CONNECTION_SETTINGS = ("SMTP_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD")


def required_env(name: str) -> str:
    value = os.getenv(PREFIX + name, "").strip()
    if not value:
        raise ValueError(f"{PREFIX}{name} が未設定")
    return value


def mailbox(value: str) -> str:
    if parseaddr(value)[1] != value or not re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", value):
        raise ValueError("専用のメールアドレスを確認してください")
    return value


def prepare_email(report: dict, content: str, html: str):
    to = [mailbox(value.strip()) for value in required_env("SMTP_TO").split(",")]
    if any(os.getenv(PREFIX + name, "").strip() for name in SMTP_CONNECTION_SETTINGS):
        sender = mailbox(required_env("SMTP_FROM"))
        server = required_env("SMTP_HOST")
        port = int(os.getenv(PREFIX + "SMTP_PORT", "587"))
        user = required_env("SMTP_USERNAME")
        password = required_env("SMTP_PASSWORD")
    else:
        # The existing news workflows authenticate to Gmail with this account.
        user = os.getenv("MAIL_FROM", "").strip()
        password = os.getenv("MAIL_PASSWORD", "").strip()
        if not (user and password):
            raise ValueError("MAIL_FROM と MAIL_PASSWORD が必要です")
        sender = mailbox(user)
        server, port = "smtp.gmail.com", 587
    message = EmailMessage()
    message["Subject"] = report["subject"]
    message["From"] = sender
    message["To"] = ", ".join(to)
    message.set_content(content)
    message.add_alternative(html, subtype="html")

    def send():
        with smtplib.SMTP(server, port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            if smtp.send_message(message, from_addr=sender, to_addrs=to):
                raise RuntimeError("一部宛先の配信結果を確認できません")

    return send


NOTION_API = "https://api.notion.com/v1/"


def notion_request(method: str, resource: str, token: str, **kwargs) -> dict:
    response = requests.request(method, NOTION_API + resource, timeout=15, allow_redirects=False,
                                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03"},
                                **kwargs)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Notion HTTP {response.status_code}")
    return response.json()


def rich(text: str, url: str = "") -> dict:
    value = {"content": text}
    if url:
        value["link"] = {"url": url}
    return {"type": "text", "text": value}


def block(kind: str, *content: dict) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": list(content)}}


def prepare_notion(report: dict):
    token = required_env("NOTION_TOKEN")
    database_id = str(UUID(required_env("NOTION_DATABASE_ID")))
    database = notion_request("GET", f"databases/{database_id}", token)
    sources = [str(UUID(s["id"])) for s in database["data_sources"]]
    configured = os.getenv(PREFIX + "NOTION_DATA_SOURCE_ID", "").strip()
    source_id = str(UUID(configured)) if configured else sources[0] if len(sources) == 1 else ""
    if source_id not in sources:
        raise ValueError("専用DBに属するデータソースIDが必要です")
    data_source = notion_request("GET", f"data_sources/{source_id}", token)
    title_property = next((n for n, p in data_source["properties"].items() if p["type"] == "title"), "")
    if not title_property:
        raise ValueError("専用DBのタイトルプロパティを確認できません")
    children = [block("paragraph", rich(message)) for message in run_messages(report)]
    for story in report["stories"]:
        children.append(block("heading_3", rich(story["title"])))
        if "summary" in story:
            for label, fields in story["summary"]["sections"].items():
                citations = [rich(f" {display_source(s)}", s["link"]) for s in fields["sources"][:8]]
                children.append(block("paragraph", rich(f"{SECTIONS[label]}　{fields['text']}"), *citations))
        children.append(block("paragraph", *(rich(f" {display_source(s)}", s["link"]) for s in story["sources"][:12])))
    payload = {"parent": {"type": "data_source_id", "data_source_id": source_id},
               "properties": {title_property: {"type": "title", "title": [rich(report["subject"])]}}, "children": children}

    def write():
        created = notion_request("POST", "pages", token, json=payload)
        if created.get("object") != "page" or not created.get("id"):
            raise RuntimeError("Notion ページ作成を確認できません")

    return write


def reserve_and_send(path: Path, key: tuple[str, str, str], send) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            issue_date TEXT NOT NULL, edition TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL,
            PRIMARY KEY (issue_date, edition, channel)
        )""")
        with db:
            claim = db.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?, 'pending')", key)
        if not claim.rowcount:
            existing = db.execute(
                "SELECT state FROM deliveries WHERE issue_date=? AND edition=? AND channel=?", key
            ).fetchone()[0]
            return {"state": "duplicate", "previous_state": existing}
        try:
            send()
            result = {"state": "sent"}
        except Exception as exc:
            result = {"state": "unknown", "reason": type(exc).__name__}
        try:
            with db:
                db.execute("UPDATE deliveries SET state=? WHERE issue_date=? AND edition=? AND channel=?",
                           (result["state"], *key))
        except sqlite3.Error:
            return {"state": "unknown", "reason": "ledger_write_failed"}
        return result


def deliver(report: dict, ledger: Path, mail: dict, enabled: set[str]):
    results = dict.fromkeys(("email", "notion"), {"state": "preview"})
    notion_has_supplement = any(
        ref["kind"] != "headline" for story in report["stories"]
        for section in story.get("summary", {}).get("sections", {}).values() for ref in section["sources"]
    )
    for channel in sorted(enabled):
        if (report["offline"] or report["collection_state"] == "failed" or not mail["email_allowed"]
                or (channel == "notion" and notion_has_supplement)):
            results[channel] = {"state": "blocked", "reason": "収集状態・入力・利用条件を確認してください"}
            continue
        try:
            prepare = (prepare_email(report, mail["text"], mail["html"]) if channel == "email" else prepare_notion(report))
            key = (report["as_of"].date().isoformat(), report["edition"], channel)
            results[channel] = reserve_and_send(ledger, key, prepare)
        except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error, requests.RequestException) as exc:
            # Exception text from remote clients or settings can contain secrets.
            results[channel] = {"state": "blocked", "reason": type(exc).__name__}
    return results
