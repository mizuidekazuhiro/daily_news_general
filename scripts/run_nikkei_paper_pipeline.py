import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOGS = Path('logs')
LOGS.mkdir(exist_ok=True)
CLEAR = [
    'nikkei_issue_article_links.json', 'nikkei_issue_all_links.json', 'nikkei_issue_excluded_links.json',
    'nikkei_articles_full.json', 'nikkei_articles_failed.json', 'nikkei_articles_excluded_after_fetch.json',
    'nikkei_articles_skipped_existing.json', 'nikkei_articles_scored.json'
]

def run(cmd):
    print('run_start:', ' '.join(cmd))
    t = time.monotonic()
    subprocess.run(cmd, check=True)
    dt = time.monotonic() - t
    print('run_end_seconds:', round(dt, 1))
    return dt

def read_count(name):
    p = LOGS / name
    return len(json.loads(p.read_text(encoding='utf-8'))) if p.exists() else 0

def read_scored_top():
    p = LOGS / 'nikkei_articles_scored.json'
    if not p.exists():
        return 0, 0
    rows = json.loads(p.read_text(encoding='utf-8'))
    top = max((x.get('importance_score', 0) for x in rows), default=0)
    return len(rows), top

def read_rules_loaded_count():
    scored = LOGS / 'nikkei_articles_scored.json'
    if not scored.exists():
        return 0
    return 1

def main():
    for n in CLEAR:
        p = LOGS / n
        if p.exists():
            p.unlink()

    total = time.monotonic()
    warning_count = 0

    t1 = run([sys.executable, 'scripts/nikkei_extract_issue_links.py'])
    article_count_after_pre_filter = read_count('nikkei_issue_article_links.json')
    print('article_count_after_pre_filter:', article_count_after_pre_filter)
    if article_count_after_pre_filter <= 0 and os.getenv('NIKKEI_ALLOW_EMPTY_ISSUE', 'false').lower() != 'true':
        return 1

    t2 = run([sys.executable, 'scripts/nikkei_fetch_articles_full.py'])
    t3 = run([sys.executable, 'scripts/nikkei_save_articles_to_notion.py'])
    t4 = t5 = 0.0
    if os.getenv('NIKKEI_ENABLE_SCORING', 'true').lower() == 'true':
        t4 = run([sys.executable, 'scripts/nikkei_score_articles.py'])
    if os.getenv('NIKKEI_ENABLE_NOTION_SCORE_UPDATE', 'false').lower() == 'true':
        t5 = run([sys.executable, 'scripts/nikkei_update_notion_scores.py'])

    fetch_success_count = read_count('nikkei_articles_full.json')
    fetch_failed_count = read_count('nikkei_articles_failed.json')
    saved_count = fetch_success_count
    scored_article_count, top_importance_score = read_scored_top()
    fetch_success_rate = (fetch_success_count / article_count_after_pre_filter * 100.0) if article_count_after_pre_filter else 0.0
    min_threshold = float(os.getenv('NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT', '5'))
    if scored_article_count > 0 and top_importance_score < min_threshold:
        warning_count += 1
        print(f'WARNING: top_importance_score={top_importance_score} is below report threshold={min_threshold}. Rules may not be matching.')

    print('step_extract_issue_links_seconds:', round(t1, 1))
    print('step_fetch_articles_seconds:', round(t2, 1))
    print('step_save_notion_seconds:', round(t3, 1))
    print('step_score_articles_seconds:', round(t4, 1))
    print('step_update_notion_scores_seconds:', round(t5, 1))
    print('pipeline_total_seconds:', round(time.monotonic() - total, 1))
    print('fetch_success_count:', fetch_success_count)
    print('fetch_failed_count:', fetch_failed_count)
    print('fetch_success_rate:', round(fetch_success_rate, 2))
    print('saved_count:', saved_count)
    print('scored_article_count:', scored_article_count)
    print('top_importance_score:', top_importance_score)
    print('rules_loaded_count:', read_rules_loaded_count())
    print('warning_count:', warning_count)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
