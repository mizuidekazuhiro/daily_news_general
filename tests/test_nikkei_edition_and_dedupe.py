from scripts.nikkei_extract_issue_links import edition_mismatch_summary
from scripts.nikkei_score_articles import dedupe_articles, extract_article_id
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


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


def test_article_id_dedupe_between_ng_and_article_path():
    assert extract_article_id("https://www.nikkei.com/paper/article/?b=20260505&ng=DGKKZO96014190R00C26A5BC8000") == "DGKKZO96014190R00C26A5BC8000"
    assert extract_article_id("https://www.nikkei.com/article/DGKKZO96014190R00C26A5BC8000/") == "DGKKZO96014190R00C26A5BC8000"
    deduped = dedupe_articles([
        {"url": "https://www.nikkei.com/paper/article/?b=20260505&ng=DGKKZO96014190R00C26A5BC8000", "text": "new body", "source": "fetched"},
        {"url": "https://www.nikkei.com/article/DGKKZO96014190R00C26A5BC8000/", "text": "", "source": "notion_existing"},
    ])
    assert len(deduped) == 1
    assert deduped[0]["text"] == "new body"


def test_mail_default_enabled():
    assert mod.DEFAULTS["NIKKEI_SEND_FINAL_REPORT_MAIL"] is True
