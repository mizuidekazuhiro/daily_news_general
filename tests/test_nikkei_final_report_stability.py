import json
from pathlib import Path
import importlib.util

from src.final_report_synthesis import validate_final_report_errors
from src.report_selection import SelectionConfig, select_articles


def mk(scores, exclude_idx=None):
    exclude_idx = exclude_idx or set()
    return [
        {"title": f"t{i}", "importance_score": s, "priority": 0, "url": f"https://x/{i}", "exclude_candidate": i in exclude_idx}
        for i, s in enumerate(scores, 1)
    ]


def test_validate_required_keys_success():
    rep = {"report_title": "a", "today_key_message": "b", "executive_summary": "c", "cross_article_implications": "d", "article_sections": []}
    assert validate_final_report_errors(rep, 0) == []


def test_validate_required_keys_missing_and_keys_visible():
    rep = {"foo": 1}
    errs = validate_final_report_errors(rep, 0)
    assert "missing_report_title" in errs


def test_exclude_candidate_removed_and_log_count_consistent():
    sel, log = select_articles(mk([10, 9, 8, 7, 6, 5], exclude_idx={1}), SelectionConfig(mode="top_importance_rank", top_rank=5, include_ties=False))
    assert all(x["title"] != "t1" for x in sel)
    assert log["report_selected_count"] == len(log["selected_article_titles"]) == len(log["selected_article_scores"])


def test_recover_missing_article_sections_from_input():
    spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    in_articles = [
        {"ref_id": "A1", "title": "t1", "url": "https://x/1", "importance_score": 9, "text_excerpt": "本文1"},
        {"ref_id": "A2", "title": "t2", "url": "https://x/2", "importance_score": 8, "text_excerpt": "本文2"},
    ]
    recovered = mod._build_article_sections_from_input(in_articles)
    assert len(recovered) == 2
    assert [x["ref_id"] for x in recovered] == ["A1", "A2"]
    assert [x["url"] for x in recovered] == ["https://x/1", "https://x/2"]


def test_core_four_plus_recovered_sections_pass_validation():
    spec = importlib.util.spec_from_file_location("nikkei_final", Path("scripts/run_nikkei_final_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    in_articles = [
        {"ref_id": "A1", "title": "t1", "url": "https://x/1", "importance_score": 9, "text_excerpt": "本文1"},
    ]
    rep = {
        "report_title": "r",
        "today_key_message": "k",
        "executive_summary": "e",
        "cross_article_implications": "c",
    }
    rep["article_sections"] = mod._build_article_sections_from_input(in_articles)
    assert validate_final_report_errors(rep, len(in_articles)) == []
