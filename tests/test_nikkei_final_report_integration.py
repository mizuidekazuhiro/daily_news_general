from pathlib import Path
import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

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


@pytest.fixture
def generated_report():
    return {
        "report_title": "朝刊ブリーフ",
        "today_key_message": "新規設備の需要が強まる一方、調達費が上がっている。",
        "executive_summary": "受注増加と金利上昇が設備計画に影響している。",
        "integrated_insights": ["設備の発注が増えた。", "資金調達費が上がった。", "一部の稼働計画が後ろ倒しになった。"],
        "article_sections": [
            {"ref_id": f"A{i}", "title": f"記事{i}", "url": f"https://example.invalid/{i}",
             "what_happened": "設備計画を見直した。"}
            for i in range(1, 6)
        ],
        "watchlist": [],
    }


def _mock_report_client(generated_report):
    create = Mock(return_value=SimpleNamespace(
        output_text=json.dumps(generated_report, ensure_ascii=False), status="completed",
    ))
    return SimpleNamespace(client=SimpleNamespace(responses=SimpleNamespace(create=create)))


def test_missing_cross_article_implications_sends_normal_report_without_retry(tmp_path, monkeypatch, generated_report):
    template = Path("templates/nikkei_final_report_email.html").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    Path("templates").mkdir()
    Path("templates/nikkei_final_report_email.html").write_text(template, encoding="utf-8")
    Path("logs").mkdir()
    articles = [{**item, "importance_score": 8, "issue_date": "20261105"}
                for item in generated_report["article_sections"]]
    Path("logs/nikkei_articles_scored.json").write_text(json.dumps(articles), encoding="utf-8")
    client = _mock_report_client(generated_report)
    smtp = MagicMock()
    monkeypatch.setattr(mod, "OpenAIJsonClient", Mock(return_value=client))
    monkeypatch.setattr(mod.smtplib, "SMTP", smtp)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("NIKKEI_EDITION", "morning")
    monkeypatch.setenv("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", "5")
    monkeypatch.setenv("NIKKEI_REPORT_INCLUDE_TIES", "false")
    monkeypatch.setenv("NIKKEI_SEND_FINAL_REPORT_MAIL", "true")
    monkeypatch.setenv("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL", "true")
    monkeypatch.setenv("MAIL_FROM", "test@example.invalid")
    monkeypatch.setenv("MAIL_TO", "test@example.invalid")

    assert mod.main() == 0
    client.client.responses.create.assert_called_once()
    smtp.return_value.__enter__.return_value.sendmail.assert_called_once()
    summary = json.loads(Path("logs/nikkei_final_report_summary.json").read_text(encoding="utf-8"))
    assert summary["final_report_gpt_success"] is True
    assert summary["fallback_used"] is False
    assert summary["final_report_validation_errors"] == []
    html = Path("logs/nikkei_final_report.html").read_text(encoding="utf-8")
    assert generated_report["today_key_message"] in html
    assert all(item in html for item in generated_report["integrated_insights"])
    assert html.count('class="article-card"') == 5


@pytest.mark.parametrize("existing", [None, "  ", "元の横断分析"])
def test_generate_report_preserves_or_recovers_cross_article_implications(generated_report, existing):
    generated_report["cross_article_implications"] = existing
    client = _mock_report_client(generated_report)
    parsed, _, _, errors, _ = mod._generate_report(client, {"articles": generated_report["article_sections"]})
    assert errors == []
    expected = "元の横断分析" if existing == "元の横断分析" else "\n".join(generated_report["integrated_insights"])
    assert parsed["cross_article_implications"] == expected


@pytest.mark.parametrize("insights", [[], ["有効な分析", "  ", "別の分析"], ["有効な分析", 1, "別の分析"], "分析"])
def test_generate_report_does_not_recover_invalid_insights(generated_report, insights):
    generated_report["cross_article_implications"] = "  "
    generated_report["integrated_insights"] = insights
    client = _mock_report_client(generated_report)
    _, _, _, errors, _ = mod._generate_report(client, {"articles": generated_report["article_sections"]})
    assert errors == ["missing_cross_article_implications"]


def test_generate_report_still_rejects_incorrect_article_count(generated_report):
    articles = generated_report["article_sections"]
    generated_report["article_sections"] = articles[:-1]
    client = _mock_report_client(generated_report)
    _, _, _, errors, _ = mod._generate_report(client, {"articles": articles})
    assert errors == ["article_sections_count_4_expected_5"]


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
    assert ('17 21 * * 0-6' in morning) or ('17 21 * * *' in morning)
    assert '17,37,57 21' not in morning
    assert '47 6 * * 0-6' in evening
    assert '7,27 7' not in evening
