from datetime import datetime, timedelta, timezone

from scripts.nikkei_extract_issue_links import edition_mismatch_summary, resolve_target_date
from scripts.nikkei_score_articles import dedupe_articles, extract_article_id
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
JST = timezone(timedelta(hours=9))


def test_morning_detected_m_continue():
    s = edition_mismatch_summary(expected_edition="morning", issue_date="20260505", detected_ids=["20260505M101"], issue_url="u", direct_issue_url="d")
    assert s["edition_check_result"] == "ok"


def test_morning_detected_e_stop():
    s = edition_mismatch_summary(expected_edition="morning", issue_date="20260505", detected_ids=["20260505E101"], issue_url="u", direct_issue_url="d")
    assert s["edition_check_result"] == "edition_mismatch"


def test_evening_detected_e_continue():
    s = edition_mismatch_summary(expected_edition="evening", issue_date="20260505", detected_ids=["20260505E101"], issue_url="u", direct_issue_url="d")
    assert s["edition_check_result"] == "ok"


def test_evening_detected_m_stop():
    s = edition_mismatch_summary(expected_edition="evening", issue_date="20260505", detected_ids=["20260505M101"], issue_url="u", direct_issue_url="d")
    assert s["edition_check_result"] == "edition_mismatch"


def test_scheduled_evening_delayed_past_midnight_uses_previous_date():
    now = datetime(2026, 8, 28, 2, 55, tzinfo=JST)
    assert resolve_target_date(target_date="auto", edition="evening", now_jst=now, event_name="schedule") == "20260827"


def test_scheduled_evening_after_nominal_time_uses_current_date():
    now = datetime(2026, 8, 28, 16, 0, tzinfo=JST)
    assert resolve_target_date(target_date="auto", edition="evening", now_jst=now, event_name="schedule") == "20260828"


def test_scheduled_morning_delayed_across_midnight_uses_previous_date_before_nominal_time():
    now = datetime(2026, 8, 28, 5, 55, tzinfo=JST)
    assert resolve_target_date(target_date="auto", edition="morning", now_jst=now, event_name="schedule") == "20260827"


def test_manual_auto_keeps_actual_jst_date():
    now = datetime(2026, 8, 28, 2, 55, tzinfo=JST)
    assert resolve_target_date(target_date="auto", edition="evening", now_jst=now, event_name="workflow_dispatch") == "20260828"


def test_explicit_target_date_is_never_adjusted():
    now = datetime(2026, 8, 28, 2, 55, tzinfo=JST)
    assert resolve_target_date(target_date="20260820", edition="evening", now_jst=now, event_name="schedule") == "20260820"


def test_article_id_dedupe_between_ng_and_article_path():
    assert extract_article_id("https://www.nikkei.com/paper/article/?b=20260505&ng=DGKKZO96014190R00C26A5BC8000") == "DGKKZO96014190R00C26A5BC8000"
    assert extract_article_id("https://www.nikkei.com/article/DGKKZO96014190R00C26A5BC8000/") == "DGKKZO96014190R00C26A5BC8000"
    deduped = dedupe_articles([
        {"url": "https://www.nikkei.com/paper/article/?b=20260505&ng=DGKKZO96014190R00C26A5BC8000", "text": "new body", "source": "fetched"},
        {"url": "https://www.nikkei.com/article/DGKKZO96014190R00C26A5BC8000/", "text": "", "source": "notion_existing"},
    ])
    assert len(deduped) == 1
    assert deduped[0]["text"] == "new body"


def test_mail_default_enabled(monkeypatch):
    monkeypatch.delenv("NIKKEI_SEND_FINAL_REPORT_MAIL", raising=False)
    assert mod._env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL", True) is True


def test_morning_workflow_serializes_and_rechecks_delivery():
    workflow = Path(".github/workflows/nikkei_morning.yml").read_text(encoding="utf-8")
    assert "group: nikkei-morning" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "id: delivery_guard" in workflow
    assert "python scripts/check_mail_delivery_guard.py --kind nikkei_morning" in workflow
    assert "steps.delivery_guard.outputs.skip != 'true'" in workflow


def test_early_dispatch_skips_when_morning_workflow_is_active():
    workflow = Path(".github/workflows/nikkei_morning_early_dispatch.yml").read_text(encoding="utf-8")
    assert "id: active_guard" in workflow
    assert "gh run list" in workflow
    assert "--workflow nikkei_morning.yml" in workflow
    assert "select(.status != \"completed\")" in workflow
    assert "steps.active_guard.outputs.skip != 'true'" in workflow
