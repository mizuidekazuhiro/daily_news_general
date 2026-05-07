from __future__ import annotations

import html
import json
import os
import re
import smtplib
import traceback
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _split_recipients(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]", value or "") if x.strip()]


def _read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_read_error": str(e)}


def _tail(path: str, limit: int = 6000) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-limit:]


def _summarize_json(label: str, data: Any) -> str:
    if data is None:
        return ""
    try:
        dumped = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        dumped = str(data)
    return f"\n## {label}\n{dumped[:5000]}"


def _github_run_url() -> str:
    failed_url = _env("FAILED_RUN_URL")
    if failed_url:
        return failed_url
    server = _env("GITHUB_SERVER_URL", "https://github.com")
    repo = _env("GITHUB_REPOSITORY")
    run_id = _env("GITHUB_RUN_ID")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _workflow_name() -> str:
    return _env("FAILED_WORKFLOW_NAME") or _env("GITHUB_WORKFLOW", "GitHub Actions")


def _build_subject() -> str:
    workflow = _workflow_name()
    edition = _env("NIKKEI_EDITION")
    date = _env("NIKKEI_TARGET_DATE", "auto")
    label = "朝刊" if edition == "morning" else "夕刊" if edition == "evening" else edition or ""
    label_part = f" {label}" if label else ""
    conclusion = _env("FAILED_RUN_CONCLUSION", "failure")
    return f"[失敗] {workflow}{label_part} | {conclusion} | target={date}"


def _build_body() -> str:
    run_url = _github_run_url()
    lines = [
        "日経新聞レポートのGitHub Actionsが失敗しました。",
        "",
        f"Workflow: {_workflow_name()}",
        f"Conclusion: {_env('FAILED_RUN_CONCLUSION', 'unknown')}",
        f"Repository: {_env('GITHUB_REPOSITORY', 'unknown')}",
        f"Branch/Ref: {_env('FAILED_HEAD_BRANCH') or _env('GITHUB_REF_NAME', _env('GITHUB_REF', 'unknown'))}",
        f"Commit: {_env('FAILED_HEAD_SHA') or _env('GITHUB_SHA', 'unknown')}",
        f"Run ID: {_env('FAILED_RUN_ID') or _env('GITHUB_RUN_ID', 'unknown')}",
        f"Run URL: {run_url or 'unknown'}",
        f"Edition: {_env('NIKKEI_EDITION', 'unknown')}",
        f"Target date: {_env('NIKKEI_TARGET_DATE', 'auto')}",
        "",
        "まずRun URLを開き、失敗ステップとUploadされた nikkei-debug-logs を確認してください。",
    ]

    summaries = [
        ("nikkei_fetch_summary.json", _read_json("logs/nikkei_fetch_summary.json")),
        ("nikkei_score_summary.json", _read_json("logs/nikkei_score_summary.json")),
        ("nikkei_final_report_summary.json", _read_json("logs/nikkei_final_report_summary.json")),
        ("nikkei_save_failed.json", _read_json("logs/nikkei_save_failed.json")),
        ("nikkei_articles_failed.json", _read_json("logs/nikkei_articles_failed.json")),
    ]
    for label, data in summaries:
        section = _summarize_json(label, data)
        if section:
            lines.append(section)

    for path in ["logs/nikkei_pipeline_error.txt", "logs/nikkei_final_report_error.txt"]:
        tail = _tail(path)
        if tail:
            lines.append(f"\n## {path} tail\n" + tail)

    return "\n".join(lines)


def _send_mail(subject: str, body_text: str) -> None:
    sender = _env("MAIL_FROM")
    user = _env("MAIL_USER") or sender
    password = _env("MAIL_PASSWORD")
    to = _split_recipients(_env("NIKKEI_FAILURE_MAIL_TO") or _env("MAIL_TO"))
    cc = _split_recipients(_env("NIKKEI_FAILURE_MAIL_CC") or _env("MAIL_CC"))
    bcc = _split_recipients(_env("NIKKEI_FAILURE_MAIL_BCC") or _env("MAIL_BCC"))
    recipients = to + cc + bcc

    if not sender or not user or not password or not recipients:
        print("failure_mail_skipped: missing MAIL_FROM/MAIL_PASSWORD/recipient")
        return

    escaped = html.escape(body_text).replace("\n", "<br>\n")
    body_html = (
        "<html><body style='font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; "
        f"font-size: 14px; line-height: 1.6;'><pre style='white-space: pre-wrap;'>{escaped}</pre></body></html>"
    )
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ",".join(to)
    if cc:
        msg["Cc"] = ",".join(cc)

    with smtplib.SMTP(_env("MAIL_HOST", "smtp.gmail.com"), int(_env("MAIL_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(sender, recipients, msg.as_string())
    print(f"failure_mail_sent: true recipients={len(recipients)}")


def main() -> int:
    if not _env_bool("NIKKEI_FAILURE_MAIL_ENABLED", True):
        print("failure_mail_skipped: disabled")
        return 0
    try:
        _send_mail(_build_subject(), _build_body())
        return 0
    except Exception:
        print("failure_mail_sent: false")
        traceback.print_exc()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
