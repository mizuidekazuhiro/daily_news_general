from __future__ import annotations

import html
import os
import re
import smtplib
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def _split_recipients(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]", value or "") if x.strip()]


def _run_url() -> str:
    server = _env("GITHUB_SERVER_URL", "https://github.com")
    repo = _env("GITHUB_REPOSITORY")
    run_id = _env("GITHUB_RUN_ID")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _send() -> None:
    sender = _env("MAIL_FROM")
    user = _env("MAIL_USER") or sender
    password = _env("MAIL_PASSWORD")
    recipients = _split_recipients(_env("SPECIAL_NEWS_FAILURE_MAIL_TO"))
    if not recipients and sender:
        recipients = [sender]
    if not sender or not user or not password or not recipients:
        print("special_news_failure_mail_skipped: missing sender/password/recipient")
        return

    now = datetime.now(JST)
    run_url = _run_url()
    subject = f"[失敗] 専門紙ニュース配信 | {now.strftime('%Y-%m-%d')}"
    body = "\n".join(
        [
            "専門紙ニュース配信のGitHub Actionsが失敗しました。",
            "",
            f"Workflow: {_env('GITHUB_WORKFLOW', 'special-news-delivery')}",
            f"Repository: {_env('GITHUB_REPOSITORY', 'unknown')}",
            f"Ref: {_env('GITHUB_REF_NAME', _env('GITHUB_REF', 'unknown'))}",
            f"Commit: {_env('GITHUB_SHA', 'unknown')}",
            f"Run ID: {_env('GITHUB_RUN_ID', 'unknown')}",
            f"Run URL: {run_url or 'unknown'}",
            "",
            "Run URLを開き、失敗したステップを確認してください。",
        ]
    )

    escaped = html.escape(body).replace("\n", "<br>\n")
    msg = MIMEText(f"<html><body>{escaped}</body></html>", "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP(_env("MAIL_HOST", "smtp.gmail.com"), int(_env("MAIL_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(sender, recipients, msg.as_string())
    print(f"special_news_failure_mail_sent: true recipients={len(recipients)}")


def main() -> int:
    try:
        _send()
    except Exception:
        print("special_news_failure_mail_sent: false")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
