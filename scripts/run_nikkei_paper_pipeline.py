import json, os, subprocess, sys, time
from pathlib import Path
LOGS=Path('logs'); LOGS.mkdir(exist_ok=True)
CLEAR=['nikkei_issue_article_links.json','nikkei_issue_all_links.json','nikkei_issue_excluded_links.json','nikkei_articles_full.json','nikkei_articles_failed.json','nikkei_articles_excluded_after_fetch.json','nikkei_articles_skipped_existing.json','nikkei_articles_scored.json']
def run(cmd):
    print('run_start:', ' '.join(cmd)); t=time.monotonic(); subprocess.run(cmd,check=True); dt=time.monotonic()-t; print('run_end_seconds:',round(dt,1)); return dt
def count(path):
    p=LOGS/path
    return len(json.loads(p.read_text(encoding='utf-8'))) if p.exists() else 0

def main():
    for n in CLEAR:
        p=LOGS/n
        if p.exists(): p.unlink()
    total=time.monotonic()
    t1=run([sys.executable,'scripts/nikkei_extract_issue_links.py'])
    c=count('nikkei_issue_article_links.json'); print('article_count=',c)
    if c<=0 and os.getenv('NIKKEI_ALLOW_EMPTY_ISSUE','false').lower()!='true': return 1
    t2=run([sys.executable,'scripts/nikkei_fetch_articles_full.py'])
    t3=run([sys.executable,'scripts/nikkei_save_articles_to_notion.py'])
    t4=t5=0.0
    if os.getenv('NIKKEI_ENABLE_SCORING','true').lower()=='true': t4=run([sys.executable,'scripts/nikkei_score_articles.py'])
    if os.getenv('NIKKEI_ENABLE_NOTION_SCORE_UPDATE','false').lower()=='true': t5=run([sys.executable,'scripts/nikkei_update_notion_scores.py'])
    scored=count('nikkei_articles_scored.json'); top=0
    if scored: top=max(x.get('importance_score',0) for x in json.loads((LOGS/'nikkei_articles_scored.json').read_text(encoding='utf-8')))
    print('step_extract_issue_links_seconds:',round(t1,1)); print('step_fetch_articles_seconds:',round(t2,1)); print('step_save_notion_seconds:',round(t3,1)); print('step_score_articles_seconds:',round(t4,1)); print('step_update_notion_scores_seconds:',round(t5,1)); print('pipeline_total_seconds:',round(time.monotonic()-total,1)); print('scored_article_count:',scored); print('top_importance_score:',top)
    return 0
if __name__=='__main__': raise SystemExit(main())
