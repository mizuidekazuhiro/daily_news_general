from pathlib import Path
import importlib.util
import json

spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_env_defaults_on_empty(monkeypatch):
    monkeypatch.setenv("X_BOOL", "")
    monkeypatch.setenv("X_INT", "")
    monkeypatch.setenv("X_FLOAT", "")
    assert mod._env_bool("X_BOOL", True) is True
    assert mod._env_int("X_INT", 7) == 7
    assert mod._env_float("X_FLOAT", 0.2) == 0.2


def test_normalize_fields():
    body = "本文テキストのサンプルです。"
    a = {"source_title": "s", "body": body, "summary": "ss", "reason_to_read": "rr", "business_implications": "bb"}
    n = mod._normalize_article(a)
    assert n["title"] == "s" and n["full_text"] == body
    assert n["Summary"] == "ss" and n["Reason to Read"] == "rr" and n["Business Implications"] == "bb"


def test_report_label_prefixes_are_removed():
    rep = mod._normalize_report_labels({
        "today_key_message": "今日の結論：投資が加速している。",
        "executive_summary": "背景・文脈：政策支援が続く。",
    })
    assert rep["today_key_message"] == "投資が加速している。"
    assert rep["executive_summary"] == "政策支援が続く。"


def test_split_recipients(monkeypatch):
    monkeypatch.setenv("MAIL_CC", "a@example.com,b@example.com\nc@example.com")
    assert mod._split_recipients("MAIL_CC") == ["a@example.com", "b@example.com", "c@example.com"]


def test_missing_scored_file_fails_when_mail_required(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIKKEI_SEND_FINAL_REPORT_MAIL", "true")
    assert mod.main() == 1
    summary = json.loads((tmp_path / "logs/nikkei_final_report_summary.json").read_text(encoding="utf-8"))
    assert summary["mail_sent"] is False
    assert summary["exit_code"] == 1


def test_missing_scored_file_ok_when_mail_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIKKEI_SEND_FINAL_REPORT_MAIL", "false")
    assert mod.main() == 0


def test_workflow_scope_and_single_schedule_retry():
    general = Path('.github/workflows/general_news.yml').read_text(encoding='utf-8')
    assert 'run_nikkei_final_report.py' not in general
    morning = Path('.github/workflows/nikkei_morning.yml').read_text(encoding='utf-8')
    evening = Path('.github/workflows/nikkei_evening.yml').read_text(encoding='utf-8')
    for wf in [morning, evening]:
        assert 'run_nikkei_final_report.py' in wf
        assert 'Run Nikkei pipeline with publication retries' in wf
        assert 'sleep 1200' in wf
    assert '17 21 * * 0-6' in morning
    assert '17,37,57 21' not in morning
    assert '47 6 * * 0-6' in evening
    assert '7,27 7' not in evening
