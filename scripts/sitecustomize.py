"""Duplicate-mail protection for scripts executed from the scripts/ directory.

This mirrors the repository-root sitecustomize because Python started as
`python scripts/...` places scripts/ on sys.path during interpreter startup.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
from email.header import decode_header, make_header

_ORIGINAL_SENDMAIL = smtplib.SMTP.sendmail
_GUARDED_PREFIXES = (
    "主要ニュースまとめ｜",
    "日経新聞朝刊要約｜",
    "【専門紙記事一覧】",
    "鉄鋼サイト更新一覧",
)


def _decode_subject(raw_message) -> str:
    try:
        text = raw_message.decode("utf-8", errors="replace") if isinstance(raw_message, bytes) else str(raw_message)
        msg = email.message_from_string(text)
        return str(make_header(decode_header(msg.get("Subject", "")))).strip()
    except Exception:
        return ""


def _search_token(subject: str) -> str:
    if subject.startswith("鉄鋼サイト更新一覧"):
        return re.sub(r"\s*\(\d+件\)\s*$", "", subject).strip()
    return subject


def _is_guarded(subject: str) -> bool:
    return any(subject.startswith(prefix) for prefix in _GUARDED_PREFIXES)


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


def _gmail_raw_criteria(subject_token: str) -> bytes:
    token = subject_token.replace('"', "")
    raw_query = f'in:sent newer_than:2d subject:"{token}"'
    quoted_query = '"' + raw_query.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return quoted_query.encode("utf-8")


def _already_sent(subject: str) -> bool:
    user = (os.getenv("MAIL_USER") or os.getenv("MAIL_FROM") or "").strip()
    password = (os.getenv("MAIL_PASSWORD") or "").strip()
    if not user or not password:
        return False
    conn = imaplib.IMAP4_SSL(os.getenv("MAIL_IMAP_HOST", "imap.gmail.com"), int(os.getenv("MAIL_IMAP_PORT", "993")))
    try:
        conn.login(user, password)
        mailbox = _find_sent_mailbox(conn)
        if not mailbox or conn.select(mailbox, readonly=True)[0] != "OK":
            return False
        status, data = conn.search(None, "X-GM-RAW", _gmail_raw_criteria(_search_token(subject)))
        return status == "OK" and bool(data and data[0].split())
    except Exception:
        return False
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _guarded_sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
    subject = _decode_subject(msg)
    if subject and _is_guarded(subject) and _already_sent(subject):
        print(f"delivery_guard: duplicate SMTP send suppressed subject={subject!r}")
        return {}
    return _ORIGINAL_SENDMAIL(self, from_addr, to_addrs, msg, mail_options, rcpt_options)


smtplib.SMTP.sendmail = _guarded_sendmail
