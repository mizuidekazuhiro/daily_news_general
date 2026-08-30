from __future__ import annotations

import argparse
import email
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
IMAP_HOST = os.getenv("MAIL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("MAIL_IMAP_PORT", "993"))


def _today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def _subject_token(kind: str) -> str:
    today = _today_jst()
    if kind == "main":
        return f"主要ニュースまとめ｜{today}"
    if kind == "special":
        prefix = (os.getenv("SPECIAL_NEWS_MAIL_SUBJECT_PREFIX") or "【専門紙記事一覧】").strip()
        return f"{prefix}{today}"
    if kind == "direct":
        prefix = (os.getenv("DIRECT_SITE_MAIL_SUBJECT_PREFIX") or "鉄鋼サイト更新一覧").strip()
        return f"{prefix} {today}"
    if kind == "nikkei_morning":
        return f"日経新聞朝刊要約｜{today}"
    raise ValueError(f"unsupported kind: {kind}")


def _find_sent_mailbox(conn: imaplib.IMAP4_SSL) -> str | None:
    status, rows = conn.list()
    if status != "OK" or not rows:
        return None
    for row in rows:
        text = row.decode("utf-8", errors="replace") if isinstance(row, bytes) else str(row)
        if "\\Sent" not in text:
            continue
        match = re.match(r'^\((?P<flags>.*?)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.+)$', text)
        if match:
            return match.group("name").strip()
    return None


def _decode_subject(header_bytes: bytes) -> str:
    try:
        msg = email.message_from_bytes(header_bytes)
        return str(make_header(decode_header(msg.get("Subject", "")))).strip()
    except Exception:
        return ""


def _recent_sent_subjects(conn: imaplib.IMAP4_SSL, limit: int = 250) -> list[str]:
    since = (datetime.now(JST) - timedelta(days=2)).strftime("%d-%b-%Y")
    status, data = conn.search(None, "SINCE", since)
    if status != "OK" or not data:
        return []
    message_ids = data[0].split()[-limit:]
    subjects: list[str] = []
    for message_id in reversed(message_ids):
        status, parts = conn.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not parts:
            continue
        header_bytes = b""
        for part in parts:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                header_bytes += part[1]
        subject = _decode_subject(header_bytes)
        if subject:
            subjects.append(subject)
    return subjects


def already_sent(subject_token: str) -> bool:
    user = (os.getenv("MAIL_USER") or os.getenv("MAIL_FROM") or "").strip()
    password = (os.getenv("MAIL_PASSWORD") or "").strip()
    if not user or not password:
        print("delivery_guard: credentials missing; fail-open")
        return False

    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        conn.login(user, password)
        mailbox = _find_sent_mailbox(conn)
        if not mailbox:
            print("delivery_guard: sent mailbox not found; fail-open")
            return False
        status, _ = conn.select(mailbox, readonly=True)
        if status != "OK":
            print("delivery_guard: sent mailbox select failed; fail-open")
            return False
        for subject in _recent_sent_subjects(conn):
            if subject.startswith(subject_token):
                return True
        return False
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _write_github_output(skip: bool, token: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"skip={'true' if skip else 'false'}\n")
        f.write(f"subject_token={token}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=["main", "special", "direct", "nikkei_morning"])
    args = parser.parse_args()

    token = _subject_token(args.kind)
    try:
        skip = already_sent(token)
    except Exception as exc:
        print(f"delivery_guard: check failed ({type(exc).__name__}: {exc}); fail-open")
        skip = False

    _write_github_output(skip, token)
    print(f"delivery_guard kind={args.kind} subject_token={token!r} already_sent={skip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
