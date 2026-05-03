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


def read_score_summary() -> tuple[int, float, int]:
    summary_path = LOGS / "nikkei_score_summary.json"
    if not summary_path.exists():
        return 0, 0.0, 0
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    scored = int(s.get("scored_article_count", 0))
    top = float(s.get("max_importance_score", 0.0))
    threshold = float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "5"))
    warning = 1 if scored > 0 and top < threshold else 0
    return scored, top, warning


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    pipeline_start = time.monotonic()

    step_extract = run([sys.executable, "scripts/nikkei_extract_issue_links.py"])
    article_count = read_count("nikkei_issue_article_links.json")
    if article_count <= 0 and os.getenv("NIKKEI_ALLOW_EMPTY_ISSUE", "false").lower() != "true":
        print("article_count is zero and NIKKEI_ALLOW_EMPTY_ISSUE=false")
        return 1

    step_fetch = run([sys.executable, "scripts/nikkei_fetch_articles_full.py"])
    step_save = run([sys.executable, "scripts/nikkei_save_articles_to_notion.py"])

    step_score = 0.0
    step_update = 0.0
    scoring_enabled = os.getenv("NIKKEI_ENABLE_SCORING", "true").lower() == "true"
    notion_update_enabled = os.getenv("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "false").lower() == "true"

    if scoring_enabled:
        step_score = run([sys.executable, "scripts/nikkei_score_articles.py"])
        if notion_update_enabled:
            step_update = run([sys.executable, "scripts/nikkei_update_notion_scores.py"])

    fetch_success_count = read_count("nikkei_articles_full.json")
    fetch_failed_count = read_count("nikkei_articles_failed.json")
    scored_article_count, top_importance_score, warning_count = read_score_summary()

    print(f"article_count: {article_count}")
    print(f"fetch_success_count: {fetch_success_count}")
    print(f"fetch_failed_count: {fetch_failed_count}")
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
