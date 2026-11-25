"""Check or send a finalized newspaper briefing without printing its contents."""

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3

from scripts.newspaper_briefing_link_mail import JST, SLOTS, choose_edition, compose
from src.newspaper_briefing.delivery import prepare_email, reserve_and_send


def deliver_preview(previews: Path, edition: str, now: datetime, *, ledger: Path | None = None) -> str:
    if choose_edition(now) != edition:
        return "outside_delivery_hours"
    message = compose({"Previews": previews, "PublicationMail": True}, now, edition)
    if message["state"] != "ready" or ledger is None:
        return message["state"]
    try:
        send = prepare_email(message, message["text"], message["html"])
        result = reserve_and_send(ledger, (message["issue_date"], edition, "email"), send)
    except (ValueError, OSError, sqlite3.Error):
        return "blocked"
    return result["state"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previews", required=True, type=Path)
    parser.add_argument("--edition", required=True, choices=SLOTS)
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    if args.send_email != bool(args.ledger):
        parser.error("--send-email と --ledger は併せて指定してください")
    state = deliver_preview(args.previews, args.edition, datetime.now(JST), ledger=args.ledger)
    print(f"Newspaper briefing email: {state}")
    return 0 if state == ("sent" if args.send_email else "ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
