from __future__ import annotations

import argparse
import imaplib
import os
import re
from datetime import datetime
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
        if not match:
            continue
        return match.group("name").strip()
    return None


def _gmail_raw_criteria(subject_token: str) -> bytes:
    token = subject_token.replace('"', "")
    raw_query = f'in:sent newer_than:2d subject:"{token}"'
    quoted_query = '"' + raw_query.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return quoted_query.encode("utf-8")


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
        status, data = conn.search(None, "X-GM-RAW", _gmail_raw_criteria(subject_token))
        if status != "OK" or not data:
            print("delivery_guard: Gmail search failed; fail-open")
            return False
        return bool(data[0].split())
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
