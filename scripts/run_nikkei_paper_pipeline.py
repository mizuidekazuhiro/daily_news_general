import json
import os
import subprocess
import sys
from pathlib import Path

LOGS = Path("logs")
LOGS.mkdir(exist_ok=True)

CLEAR_FILES = [
    "nikkei_issue_article_links.json",
    "nikkei_issue_all_links.json",
    "nikkei_issue_excluded_links.json",
    "nikkei_articles_full.jsonl",
    "nikkei_articles_full.json",
    "nikkei_articles_failed.json",
    "nikkei_articles_excluded_after_fetch.json",
]

def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

def clear_logs() -> None:
    for name in CLEAR_FILES:
        p = LOGS / name
        if p.exists():
            p.unlink()

def article_count() -> int:
    p = LOGS / "nikkei_issue_article_links.json"
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    return len(data)

def main() -> int:
    edition = os.getenv("NIKKEI_EDITION", "morning")
    allow_empty = os.getenv("NIKKEI_ALLOW_EMPTY_ISSUE", "false").lower() == "true"

    print(f"nikkei edition={edition}")
    print(f"allow_empty_issue={allow_empty}")

    clear_logs()

    run([sys.executable, "scripts/nikkei_extract_issue_links.py"])

    count = article_count()
    print(f"article_count={count}")

    if count <= 0:
        msg = f"No Nikkei {edition} issue articles found."
        if allow_empty:
            print("SKIP:", msg)
            return 0
        print("ERROR:", msg)
        return 1

    run([sys.executable, "scripts/nikkei_fetch_articles_full.py"])
    run([sys.executable, "scripts/nikkei_save_articles_to_notion.py"] )

    if os.getenv("NIKKEI_ENABLE_SCORING", "true").lower() == "true":
        run([sys.executable, "scripts/nikkei_score_articles.py"])

    if os.getenv("NIKKEI_ENABLE_NOTION_SCORE_UPDATE", "false").lower() == "true":
        run([sys.executable, "scripts/nikkei_update_notion_scores.py"])

    print("nikkei pipeline done")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
