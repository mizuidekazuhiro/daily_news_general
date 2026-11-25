from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path

import pytest

from scripts import newspaper_briefing_link_mail as mail
from scripts import newspaper_briefing_smtp as smtp
from src.newspaper_briefing.output import mail_digest


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def installed(tmp_path):
    return {"Release": str(tmp_path / "release"), "Previews": str(tmp_path / "previews")}


def publish(path, edition="morning", *, offline=False, state="ok", permission=True):
    evening = edition == "evening"
    report = {
        "version": 2,
        "edition": edition, "subject": f"新聞横断ニュース｜{'夕' if evening else '朝'}｜2026-11-24",
        "as_of": "2026-11-24T18:00:03+09:00" if evening else "2026-11-24T08:00:03+09:00",
        "offline": offline, "collection_state": state,
    }
    package = {"version": 1, "edition": edition, "subject": report["subject"], "as_of": report["as_of"],
               "text": "市が堤防を点検した。架空新聞 https://example.invalid/report",
               "html": '<p>市が堤防を点検した。<a href="https://example.invalid/report">架空新聞</a></p>',
               "link_count": 1, "email_allowed": permission}
    package["sha256"] = mail_digest(package["subject"], package["text"], package["html"])
    report["mail_sha256"] = package["sha256"]
    write_json(path, report)
    write_json(path.with_name("mail.json"), package)
    return package


def test_uses_finalized_message_without_rewriting_the_body(installed):
    path = Path(installed["Previews"]) / "2026-11-24/morning/080003/report.json"
    package = publish(path)
    now = datetime.fromisoformat("2026-11-24T08:10:00+09:00")
    message = mail.compose(installed, now, "morning")
    assert message["state"] == "ready" and message["link_count"] == 1
    assert message["html"] == package["html"] and message["text"] == package["text"]
    write_json(path.with_name("mail.json"), package | {"html": "未検証の本文"})
    assert mail.compose(installed, now, "morning")["state"] == "not_ready"


def test_evening_waits_for_same_day_live_output(installed):
    root = Path(installed["Previews"])
    publish(root / "2026-11-24/morning/080003/report.json")
    publish(root / "2026-11-24/evening/180003/report.json", "evening", offline=True)
    publish(root / "2026-11-24/evening/180005/report.json", "evening", state="failed")
    publish(root / "2026-11-24/evening/180007/report.json", "evening", permission=False)
    now = datetime.fromisoformat("2026-11-24T18:10:00+09:00")
    assert mail.choose_edition(now) == "evening"
    assert mail.compose(installed, now, "evening")["state"] == "not_ready"
    publish(root / "2026-11-24/evening/180006/report.json", "evening", state="partial")
    assert mail.compose(installed, now, "evening")["state"] == "ready"
    next_day = datetime.fromisoformat("2026-11-25T18:10:00+09:00")
    assert mail.compose(installed, next_day, "evening")["state"] == "not_ready"


def test_only_one_claim_and_no_retry_after_unknown(installed):
    path = mail.ledger_path(installed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: mail.reserve(path, "2026-11-24", "morning"), range(2)))
    assert set(states) == {"reserved", "pending"}
    mail.finish(path, "2026-11-24", "morning", "unknown")
    assert mail.reserve(path, "2026-11-24", "morning") == "unknown"
    assert mail.reserve(path, "2026-11-24", "evening") == "reserved"
    with pytest.raises(ValueError, match="No pending"):
        mail.finish(path, "2026-11-24", "morning", "sent")


def test_recurring_install_rejects_unmatched_package_and_uses_shared_ledger(installed, tmp_path):
    installed.update(PublicationMail=True, MailLedger=str(tmp_path / "shared/ledger.sqlite3"))
    path = Path(installed["Previews"]) / "2026-11-24/morning/080003/report.json"
    publish(path)
    package = publish(path.parent / "delivery/report.json")
    now = datetime.fromisoformat("2026-11-24T08:10:00+09:00")
    assert mail.compose(installed, now, "morning")["state"] == "not_ready"
    write_json(path, mail.read_json(path) | {"publication_mail_sha256": package["sha256"]})
    assert mail.compose(installed, now, "morning")["state"] == "ready"
    initial = mail.ledger_path(installed)
    assert mail.reserve(initial, "2026-11-24", "morning") == "reserved"
    assert mail.ledger_path(installed | {"Release": str(tmp_path / "replacement")}) == initial
    assert mail.reserve(initial, "2026-11-24", "morning") == "pending"


@pytest.mark.parametrize("mode", ["preview", "offline", "tampered", "sent", "unknown"])
def test_scheduled_smtp_checks_finalized_preview_and_never_repeats_sends(installed, tmp_path, monkeypatch, mode):
    root = Path(installed["Previews"])
    generic = root / "2026-11-24/morning/080003/report.json"
    finalized = generic.parent / "delivery/report.json"
    fake_now = datetime.fromisoformat("2026-11-24T08:10:00+09:00")
    offline = mode == "offline"
    publish(generic, offline=offline)
    package = publish(finalized, offline=offline)
    write_json(generic, mail.read_json(generic) | {"publication_mail_sha256": package["sha256"]})
    if mode == "tampered":
        write_json(finalized.with_name("mail.json"), package | {"html": "改変された本文"})
    calls = []

    def prepare(message, text, html):
        assert message["subject"] == package["subject"]
        assert (text, html) == (package["text"], package["html"])
        calls.append("prepared")

        def send():
            calls.append("smtp")
            if mode == "unknown":
                raise TimeoutError

        return send

    monkeypatch.setattr(smtp, "prepare_email", prepare)
    ledger = tmp_path / "email.sqlite3"
    if mode == "preview":
        assert smtp.deliver_preview(root, "morning", fake_now) == "ready"
    elif mode in {"offline", "tampered"}:
        assert smtp.deliver_preview(root, "morning", fake_now, ledger=ledger) == "not_ready"
    else:
        assert smtp.deliver_preview(root, "morning", fake_now, ledger=ledger) == mode
        assert smtp.deliver_preview(root, "morning", fake_now, ledger=ledger) == "duplicate"
        assert calls.count("smtp") == 1
        return
    assert calls == [] and not ledger.exists()
