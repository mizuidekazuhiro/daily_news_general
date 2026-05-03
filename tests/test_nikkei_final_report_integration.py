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
    a={"source_title":"s","body":"b","gptProcessed":True,"summary":"ss","reason_to_read":"rr","business_implications":"bb"}
    n=mod._normalize_article(a)
    assert n["title"]=="s" and n["full_text"]=="b" and n["gpt_processed_norm"] is True
    assert n["Summary"]=="ss" and n["Reason to Read"]=="rr" and n["Business Implications"]=="bb"


def test_daily_props_mail_sent_at_behavior():
    p1=mod._daily_props({"report_title":"r"},2,"h",False)
    p2=mod._daily_props({"report_title":"r"},2,"h",True)
    assert "Mail Sent At" not in p1
    assert "Mail Sent At" in p2 and "start" in p2["Mail Sent At"]["date"]


def test_notion_blocks_links():
    blocks=mod._notion_blocks({"today_key_message":"a","executive_summary":"b","cross_article_implications":"c","priority_watch_items":[]},[{"title":"t","url":"https://x"}])
    s=json.dumps(blocks, ensure_ascii=False)
    assert "https://x" in s and "A1" in s


def test_workflow_scope():
    general = Path('.github/workflows/general_news.yml').read_text(encoding='utf-8')
    assert 'run_nikkei_final_report.py' not in general
    for wf in ['.github/workflows/nikkei_morning.yml','.github/workflows/nikkei_evening.yml']:
        assert 'run_nikkei_final_report.py' in Path(wf).read_text(encoding='utf-8')
