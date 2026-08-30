"""Duplicate-mail protection for scripts executed from scripts/.

Python started as `python scripts/...` loads this sitecustomize from scripts/.
The Gmail Sent lookup is shared with check_mail_delivery_guard.py.
"""
from __future__ import annotations

import email
import re
import smtplib
from email.header import decode_header, make_header

from check_mail_delivery_guard import already_sent

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


def _guard_token(subject: str) -> str:
    if subject.startswith("鉄鋼サイト更新一覧"):
        return re.sub(r"\s*\(\d+件\)\s*$", "", subject).strip()
    if subject.startswith("【専門紙記事一覧】"):
        match = re.match(r"^(【専門紙記事一覧】\d{4}-\d{2}-\d{2})", subject)
        if match:
            return match.group(1)
    return subject


def _is_guarded(subject: str) -> bool:
    return any(subject.startswith(prefix) for prefix in _GUARDED_PREFIXES)


def _guarded_sendmail(self, from_addr, to_addrs, msg, mail_options=(), rcpt_options=()):
    subject = _decode_subject(msg)
    if subject and _is_guarded(subject):
        try:
            if already_sent(_guard_token(subject)):
                print(f"delivery_guard: duplicate SMTP send suppressed subject={subject!r}")
                return {}
        except Exception:
            pass
    return _ORIGINAL_SENDMAIL(self, from_addr, to_addrs, msg, mail_options, rcpt_options)


smtplib.SMTP.sendmail = _guarded_sendmail
