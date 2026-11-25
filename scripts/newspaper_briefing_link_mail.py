"""Prepare the finalized briefing email and reserve its issue in the delivery ledger."""

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


JST = timezone(timedelta(hours=9))
SLOTS = {"morning": (8, 18, "朝"), "evening": (18, 24, "夕")}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def choose_edition(now: datetime) -> str:
    return next((edition for edition, (start, end, _) in SLOTS.items() if start <= now.hour < end), "")


def compose(install: dict, now: datetime, edition: str) -> dict:
    day = now.date().isoformat()
    start, end, label = SLOTS[edition]
    subject = f"新聞横断ニュース｜{label}｜{day}"
    publication = install.get("PublicationMail") is True
    paths = sorted((Path(install["Previews"]) / day / edition).glob("*/report.json"), reverse=True)
    for path in paths:
        try:
            report = read_json(path)
            at = datetime.fromisoformat(report["as_of"])
            if at.tzinfo is None:
                continue
            at = at.astimezone(JST)
            if (at.date() != now.date() or at > now or not start <= at.hour < end
                    or report["version"] != 2 or report["edition"] != edition or report["subject"] != subject
                    or report["offline"] is not False or report["collection_state"] not in {"ok", "partial"}):
                continue
            selected = path
            if publication:
                selected = path.parent / "delivery" / "report.json"
                approved = read_json(selected)
                if (any(approved[key] != report[key] for key in ("version", "subject", "edition", "as_of", "offline"))
                        or approved["collection_state"] not in {"ok", "partial"}
                        or approved["mail_sha256"] != report["publication_mail_sha256"]):
                    continue
                report = approved
            mail = read_json(selected.with_name("mail.json"))
            if (mail["version"] != 1 or mail["email_allowed"] is not True or mail["subject"] != subject
                    or mail["edition"] != edition or mail["as_of"] != report["as_of"]
                    or not all(isinstance(mail[key], str) and mail[key] for key in ("text", "html"))):
                continue
            digest = sha256(json.dumps([subject, mail["text"], mail["html"]], ensure_ascii=False).encode("utf-8")).hexdigest()
            if mail["sha256"] == report["mail_sha256"] == digest:
                return {"state": "ready", "issue_date": day, "edition": edition, "subject": subject,
                        "link_count": mail["link_count"], "html": mail["html"], "text": mail["text"]}
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            continue
    return {"state": "not_ready", "issue_date": day, "edition": edition}


def ledger_path(install: dict) -> Path:
    if install.get("MailLedger"):
        return Path(install["MailLedger"])
    return Path(install["Release"]) / "artifacts/newspaper_briefing/delivery.sqlite3"


def reserve(path: Path, issue_date: str, edition: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            issue_date TEXT NOT NULL, edition TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL,
            PRIMARY KEY (issue_date, edition, channel)
        )""")
        with connection:
            added = connection.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?, 'email', 'pending')",
                                       (issue_date, edition)).rowcount
        if added:
            return "reserved"
        return connection.execute("SELECT state FROM deliveries WHERE issue_date=? AND edition=? AND channel='email'",
                                  (issue_date, edition)).fetchone()[0]


def finish(path: Path, issue_date: str, edition: str, state: str) -> None:
    with closing(sqlite3.connect(path, timeout=15)) as connection, connection:
        changed = connection.execute(
            "UPDATE deliveries SET state=? WHERE issue_date=? AND edition=? AND channel='email' AND state='pending'",
            (state, issue_date, edition),
        ).rowcount
        if changed != 1:
            raise ValueError("No pending email reservation")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    for action in ("check", "prepare"):
        commands.add_parser(action).add_argument("--edition", choices=SLOTS)
    closing_parser = commands.add_parser("finish")
    closing_parser.add_argument("--issue-date", type=date.fromisoformat, required=True)
    closing_parser.add_argument("--edition", choices=SLOTS, required=True)
    closing_parser.add_argument("--state", choices=("sent", "unknown"), required=True)
    args = parser.parse_args(argv)
    install = read_json(args.install_root / "install.json")
    if args.command == "finish":
        finish(ledger_path(install), args.issue_date.isoformat(), args.edition, args.state)
        print(json.dumps({"state": args.state, "issue_date": args.issue_date.isoformat(), "edition": args.edition}))
        return 0

    now = datetime.now(JST)
    edition = args.edition or choose_edition(now)
    result = compose(install, now, edition) if edition else {"state": "outside_delivery_hours"}
    if args.command == "prepare" and result["state"] == "ready":
        state = reserve(ledger_path(install), result["issue_date"], result["edition"])
        result["state"] = state
        if state != "reserved":
            del result["html"], result["text"]
    elif args.command == "check" and result["state"] == "ready":
        del result["html"], result["text"]
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
