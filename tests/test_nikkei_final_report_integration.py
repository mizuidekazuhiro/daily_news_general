from pathlib import Path
import json
import subprocess
import sys


def test_script_reads_nikkei_scored_json_and_generates_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "templates").mkdir()
    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "templates" / "nikkei_final_report_email.html").write_text((repo / "templates" / "nikkei_final_report_email.html").read_text(encoding="utf-8"), encoding="utf-8")
    sample = [{"title":"t1","url":"https://e/1","importance_score":10,"priority":2,"text":"x"*140},{"title":"t2","url":"https://e/2","importance_score":5,"priority":1,"text":"x"*140}]
    (tmp_path / "logs" / "nikkei_articles_scored.json").write_text(json.dumps(sample), encoding="utf-8")
    env = {"PYTHONPATH": str(repo), "NIKKEI_ENABLE_FINAL_REPORT":"true", "NIKKEI_REPORT_SELECTION_MODE":"top_importance_rank", "NIKKEI_REPORT_TOP_IMPORTANCE_RANK":"5", "NIKKEI_REPORT_INCLUDE_TIES":"true", "GENERAL_REPORT_SELECTION_MODE":"threshold"}
    cp = subprocess.run([sys.executable, str(repo / "scripts" / "run_nikkei_final_report.py")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert cp.returncode == 0
    assert (tmp_path / "logs" / "nikkei_final_report.html").exists()
    assert (tmp_path / "logs" / "nikkei_report_selection.json").exists()


def test_workflows_and_scope_files():
    general = Path(".github/workflows/general_news.yml").read_text(encoding="utf-8")
    assert "GENERAL_" not in general
    assert "run_nikkei_final_report.py" not in general
    for wf in [".github/workflows/nikkei_morning.yml", ".github/workflows/nikkei_evening.yml"]:
        s = Path(wf).read_text(encoding="utf-8")
        assert "python scripts/run_nikkei_final_report.py" in s


def test_html_links_and_wording():
    s = Path("templates/nikkei_final_report_email.html").read_text(encoding="utf-8")
    assert "商社目線の読み" not in s
