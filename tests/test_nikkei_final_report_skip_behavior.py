from pathlib import Path
import importlib.util
import json

spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_missing_scored_json_fails_when_mail_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIKKEI_SEND_FINAL_REPORT_MAIL", "true")
    assert mod.main() == 1
    summary = json.loads(Path("logs/nikkei_final_report_summary.json").read_text(encoding="utf-8"))
    assert summary["final_report_skipped"] is True
    assert summary["final_report_skip_reason"] == "missing_scored_articles_json"


def test_skip_json_blocks_final_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/nikkei_paper_pipeline_skip.json").write_text(json.dumps({"skip_final_report": True, "skip_reason": "edition_mismatch"}), encoding="utf-8")
    assert mod.main() == 0
    summary = json.loads(Path("logs/nikkei_final_report_summary.json").read_text(encoding="utf-8"))
    assert summary["mail_sent"] is False
    assert summary["final_report_skip_reason"] == "edition_mismatch"


def test_empty_scored_json_skips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/nikkei_articles_scored.json").write_text("[]", encoding="utf-8")
    assert mod.main() == 0
    summary = json.loads(Path("logs/nikkei_final_report_summary.json").read_text(encoding="utf-8"))
    assert summary["final_report_skip_reason"] == "no_articles_meet_min_importance_score"



def test_pipeline_skip_blocks_stale_scored_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/nikkei_articles_scored.json').write_text('invalid stale input')
    (tmp_path / 'logs/nikkei_paper_pipeline_skip.json').write_text(json.dumps({
        'skip_final_report': True, 'skip_reason': 'no_articles_for_edition'}))
    monkeypatch.setattr(mod, 'OpenAIJsonClient', lambda **kw: (_ for _ in ()).throw(AssertionError('GPT must not run')))
    assert mod.main() == 0
    summary = json.loads(Path('logs/nikkei_final_report_summary.json').read_text())
    assert summary['mail_sent'] is False
    assert summary['final_report_skip_reason'] == 'no_articles_for_edition'


def test_invalid_pipeline_skip_marker_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/nikkei_paper_pipeline_skip.json').write_text('invalid')
    assert mod.main() == 1
    summary = json.loads(Path('logs/nikkei_final_report_summary.json').read_text())
    assert summary['mail_send_allowed'] is False
    assert summary['final_report_skip_reason'] == 'invalid_pipeline_skip_json'


def test_false_skip_marker_does_not_hide_missing_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('NIKKEI_SEND_FINAL_REPORT_MAIL', 'true')
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/nikkei_paper_pipeline_skip.json').write_text('{"skip_final_report": false}')
    assert mod.main() == 1
