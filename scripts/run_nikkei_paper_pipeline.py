import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOGS = Path("logs")


def run(cmd: list[str]) -> float:
    print("run_start:", " ".join(cmd))
    start = time.monotonic()
    subprocess.run(cmd, check=True)
    sec = time.monotonic() - start
    print("run_end_seconds:", round(sec, 1))
    return sec


def read_count(file_name: str) -> int:
    p = LOGS / file_name
    if not p.exists():
        return 0
    return len(json.loads(p.read_text(encoding="utf-8")))


def read_score_summary() -> tuple[int, float, int, dict]:
    summary_path = LOGS / "nikkei_score_summary.json"
    if not summary_path.exists():
        return 0, 0.0, 0, {}
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    scored = int(s.get("scored_article_count", 0))
    top = float(s.get("max_importance_score", 0.0))
    threshold = float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5"))
    warning = 1 if scored > 0 and top < threshold else 0
    return scored, top, warning, s


def read_fetch_summary() -> dict:
    p = LOGS / "nikkei_fetch_summary.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def decide_fetch_outcome(
    *,
    target_count: int,
    fetch_success_count: int,
    fetch_failed_count: int,
    existing_url_skip_count: int,
    allow_empty_fetch: bool,
) -> tuple[str, str]:
    if target_count == 0:
        return "skip_no_targets", "all URLs already exist in Notion"
    if fetch_success_count > 0:
        return "continue", "at least one fetch target succeeded"
    if existing_url_skip_count > 0:
        return "continue", "all new targets failed but existing URL skips were present"
    if allow_empty_fetch:
        return "continue", "NIKKEI_ALLOW_EMPTY_FETCH=true"
    return "fail", f"fetch_success_count=0 while target_count={target_count} failed_count={fetch_failed_count}"


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    pipeline_start = time.monotonic()

    step_extract = run([sys.executable, "scripts/nikkei_extract_issue_links.py"])
    article_count = read_count("nikkei_issue_article_links.json")
    if article_count <= 0 and os.getenv("NIKKEI_ALLOW_EMPTY_ISSUE", "false").lower() != "true":
        print("article_count is zero and NIKKEI_ALLOW_EMPTY_ISSUE=false")
        return 1

    step_fetch = run([sys.executable, "scripts/nikkei_fetch_articles_full.py"])

    fetch_summary = read_fetch_summary()
    fetch_success_count = int(fetch_summary.get("fetch_success_count", read_count("nikkei_articles_full.json")))
    fetch_failed_count = int(fetch_summary.get("fetch_failed_count", read_count("nikkei_articles_failed.json")))
    target_count = int(fetch_summary.get("target_count", article_count))
    max_success_articles = int(fetch_summary.get("max_success_articles", 0))
    max_article_attempts = int(fetch_summary.get("max_article_attempts", 0))
    attempted_count = int(fetch_summary.get("attempted_count", target_count))
    remaining_unattempted_count = int(fetch_summary.get("remaining_unattempted_count", 0))
    reason_counts = fetch_summary.get("failure_reason_counts", {})
    empty_body_count = int(fetch_summary.get("empty_body_count", 0))
    failed_json_path = str(LOGS / "nikkei_articles_failed.json")
    failed_artifacts_dir = str(LOGS / "nikkei_failed_articles")
    existing_url_skip_count = int(fetch_summary.get("existing_url_skip_count", 0))
    too_short_count = int(fetch_summary.get("too_short_count", 0))
    timeout_count = int(fetch_summary.get("timeout_count", 0))
    allow_empty_fetch = os.getenv("NIKKEI_ALLOW_EMPTY_FETCH", "false").lower() == "true"
    final_decision, final_reason = decide_fetch_outcome(
        target_count=target_count,
        fetch_success_count=fetch_success_count,
        fetch_failed_count=fetch_failed_count,
        existing_url_skip_count=existing_url_skip_count,
        allow_empty_fetch=allow_empty_fetch,
    )
    if final_decision == "skip_no_targets":
        print("INFO: target_count=0 because all URLs already exist in Notion. Skip fetch/save without error.")
        step_save = 0.0
    elif final_decision == "fail":
        print(f"article_count: {article_count}")
        print(f"existing_url_skip_count: {existing_url_skip_count}")
        print(f"target_count: {target_count}")
        print(f"max_success_articles: {max_success_articles}")
        print(f"max_article_attempts: {max_article_attempts}")
        print(f"attempted_count: {attempted_count}")
        print(f"remaining_unattempted_count: {remaining_unattempted_count}")
        print(f"fetch_success_count: {fetch_success_count}")
        print(f"fetch_failed_count: {fetch_failed_count}")
        print(f"failure_reason_counts: {reason_counts}")
        print(f"empty_body_count: {empty_body_count}")
        print(f"too_short_count: {too_short_count}")
        print(f"timeout_count: {timeout_count}")
        print(f"failed_json_path: {failed_json_path}")
        print(f"failed_artifacts_dir: {failed_artifacts_dir}")
        print(f"final_decision: {final_decision}")
        print(f"final_decision_reason: {final_reason}")
        print(f"ERROR: fetch_success_count=0 while target_count={target_count}. failed_count={fetch_failed_count} reason_counts={reason_counts}. See {failed_json_path}")
        return 1
    else:
        if target_count > 0 and fetch_success_count == 0 and existing_url_skip_count > 0:
            print("WARN: all new fetch targets failed, but existing_url_skip_count>0. Continue without saving new articles.")
            step_save = 0.0
        else:
            if target_count > 0 and fetch_failed_count > 0:
                print(f"WARN: partial fetch failure. target_count={target_count} success_count={fetch_success_count} failed_count={fetch_failed_count}. Continue with successful articles.")
            step_save = run([sys.executable, "scripts/nikkei_save_articles_to_notion.py"])

    step_score = 0.0
    step_update = 0.0
    scoring_enabled = os.getenv("NIKKEI_ENABLE_SCORING", "true").lower() == "true"
    notion_update_enabled = os.getenv("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "false").lower() == "true"

    if scoring_enabled:
        step_score = run([sys.executable, "scripts/nikkei_score_articles.py"])
        if notion_update_enabled:
            step_update = run([sys.executable, "scripts/nikkei_update_notion_scores.py"])

    scored_article_count, top_importance_score, warning_count, score_summary = read_score_summary()

    print(f"article_count: {article_count}")
    print(f"existing_url_skip_count: {existing_url_skip_count}")
    print(f"target_count: {target_count}")
    print(f"max_success_articles: {max_success_articles}")
    print(f"max_article_attempts: {max_article_attempts}")
    print(f"attempted_count: {attempted_count}")
    print(f"remaining_unattempted_count: {remaining_unattempted_count}")
    print(f"fetch_success_count: {fetch_success_count}")
    print(f"fetch_failed_count: {fetch_failed_count}")
    print(f"failure_reason_counts: {reason_counts}")
    print(f"empty_body_count: {empty_body_count}")
    print(f"too_short_count: {too_short_count}")
    print(f"timeout_count: {timeout_count}")
    print(f"failed_json_path: {failed_json_path}")
    print(f"failed_artifacts_dir: {failed_artifacts_dir}")
    print(f"issue_inventory_count: {int(fetch_summary.get('issue_inventory_count', 0))}")
    print(f"inventory_existing_in_notion_count: {int(fetch_summary.get('inventory_existing_in_notion_count', 0))}")
    print(f"inventory_fetched_new_count: {int(fetch_summary.get('inventory_fetched_new_count', 0))}")
    print(f"inventory_failed_count: {int(fetch_summary.get('inventory_failed_count', 0))}")
    print(f"scoring_input_new_count: {int(score_summary.get('scoring_input_new_count', 0))}")
    print(f"scoring_input_existing_count: {int(score_summary.get('scoring_input_existing_count', 0))}")
    print(f"scoring_input_title_only_count: {int(score_summary.get('scoring_input_title_only_count', 0))}")
    print(f"scoring_input_total_count: {int(score_summary.get('scoring_input_total_count', 0))}")
    print(f"final_decision: {final_decision}")
    print(f"final_decision_reason: {final_reason}")
    print(f"scored_article_count: {scored_article_count}")
    print(f"top_importance_score: {top_importance_score}")
    print(f"warning_count: {warning_count}")
    print(f"step_extract_seconds: {round(step_extract, 1)}")
    print(f"step_fetch_seconds: {round(step_fetch, 1)}")
    print(f"step_save_seconds: {round(step_save, 1)}")
    print(f"step_score_seconds: {round(step_score, 1)}")
    print(f"step_update_scores_seconds: {round(step_update, 1)}")
    print(f"pipeline_total_seconds: {round(time.monotonic() - pipeline_start, 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
