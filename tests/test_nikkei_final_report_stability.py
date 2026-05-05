import json
from pathlib import Path

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
