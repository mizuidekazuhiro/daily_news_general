import json, os, re, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
load_dotenv()
STORAGE_PATH=Path('.storage/nikkei_storage_state.json'); INPUT_PATH=Path('logs/nikkei_issue_article_links.json'); OUTPUT_JSON=Path('logs/nikkei_articles_full.json'); FAILED_JSON=Path('logs/nikkei_articles_failed.json')
MAX_ARTICLES=int(os.getenv('NIKKEI_MAX_ARTICLES_TO_FETCH','0')); SLEEP_SECONDS=float(os.getenv('NIKKEI_FETCH_SLEEP_SECONDS','1.0')); MIN_LEN=int(os.getenv('NIKKEI_MIN_ARTICLE_TEXT_LENGTH','120')); RETRIES=int(os.getenv('NIKKEI_ARTICLE_EXTRACT_RETRIES','3')); GOTO_TIMEOUT=int(os.getenv('NIKKEI_ARTICLE_GOTO_TIMEOUT_MS','25000')); WAIT_AFTER=int(os.getenv('NIKKEI_ARTICLE_WAIT_AFTER_LOAD_MS','800')); BLOCK_HEAVY=os.getenv('NIKKEI_BLOCK_HEAVY_RESOURCES','true').lower()=='true'
SKIP_EXISTING=os.getenv('NIKKEI_SKIP_EXISTING_NOTION_URLS','true').lower()=='true'; PAGE_SIZE=int(os.getenv('NIKKEI_EXISTING_URL_LOOKUP_PAGE_SIZE','100')); NOTION_TOKEN=os.getenv('NOTION_TOKEN','').strip(); DB=(os.getenv('NIKKEI_ARTICLES_DB_ID','') or os.getenv('NOTION_ARTICLE_DB_ID','')).strip()

def extract_nikkei_ng_id(url:str)->str: return (parse_qs(urlparse(url).query).get('ng') or [''])[0]
def normalize_nikkei_article_key(url:str)->str: return extract_nikkei_ng_id(url) or url.strip()
def notion_headers(): return {'Authorization':f'Bearer {NOTION_TOKEN}','Notion-Version':'2022-06-28','Content-Type':'application/json'}
def notion_req(url,payload):
    while True:
        r=requests.post(url,headers=notion_headers(),json=payload,timeout=60)
        if r.status_code==429: time.sleep(int(r.headers.get('Retry-After','2'))); continue
        r.raise_for_status(); return r.json()
def fetch_existing():
    if not (SKIP_EXISTING and NOTION_TOKEN and DB): return set(),False
    meta=requests.get(f'https://api.notion.com/v1/databases/{DB}',headers=notion_headers(),timeout=60).json(); props=meta.get('properties',{})
    if 'URL' not in props: print('WARNING: URL property missing; disable skip existing'); return set(),False
    cur=None; keys=set()
    while True:
        payload={'page_size':PAGE_SIZE};
        if cur: payload['start_cursor']=cur
        d=notion_req(f'https://api.notion.com/v1/databases/{DB}/query',payload)
        for it in d.get('results',[]):
            p=it.get('properties',{}).get('URL',{});u=''
            if p.get('type')=='url': u=p.get('url') or ''
            elif p.get('type')=='rich_text': u=''.join(x.get('plain_text','') for x in p.get('rich_text',[]))
            if u: keys.add(normalize_nikkei_article_key(u)); keys.add(u)
        if not d.get('has_more'): break
        cur=d.get('next_cursor')
    return keys,True
def should_exclude_by_body(title,text):
    if '人事記事をもっと見る' in text:return True,'hr_article_marker'
    if re.search(r'.*\s[^\s]{2,6}氏$',title or '') and any(x in text for x in ['人事','就任','社長','会長','役員']) and len(text)<600 and not any(k in text for k in ['M&A','投資','決算','設備投資','資本提携','能力増強','合弁','買収','TOB','インタビュー']): return True,'hr_short_executive_article'
    return False,''
def extract(page):
    page.wait_for_timeout(WAIT_AFTER)
    return page.evaluate("""() => {const t=document.querySelector('h1')?.innerText||document.title||''; const b=document.querySelector('article,main,.cmn-section')?.innerText||''; return {title:t,text:b};} """)

def main():
    arts=json.loads(INPUT_PATH.read_text(encoding='utf-8')) if INPUT_PATH.exists() else []
    if MAX_ARTICLES>0: arts=arts[:MAX_ARTICLES]
    keys,enabled=fetch_existing(); skipped=[]
    before=len(arts)
    if enabled:
        keep=[]
        for a in arts:
            if a['url'] in keys or normalize_nikkei_article_key(a['url']) in keys: skipped.append(a)
            else: keep.append(a)
        arts=keep
    print('existing_notion_url_count:',len(keys)); print('target_count_before_existing_skip:',before); print('skip_existing_count:',len(skipped)); print('target_count_after_existing_skip:',len(arts)); print('target_count:',len(arts))
    Path('logs/nikkei_articles_skipped_existing.json').write_text(json.dumps(skipped,ensure_ascii=False,indent=2),encoding='utf-8')
    if not arts: OUTPUT_JSON.write_text('[]',encoding='utf-8'); FAILED_JSON.write_text('[]',encoding='utf-8'); return
    res=[]; fail=[]
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True); c=b.new_context(storage_state=str(STORAGE_PATH),locale='ja-JP',timezone_id='Asia/Tokyo')
        if BLOCK_HEAVY: c.route('**/*', lambda route,req: route.abort() if req.resource_type in {'image','media','font','stylesheet'} else route.continue_())
        page=c.new_page()
        for i,a in enumerate(arts,1):
            ok=False
            for t in range(RETRIES):
                try:
                    page.goto(a['url'],wait_until='domcontentloaded',timeout=GOTO_TIMEOUT)
                    d=extract(page); text=(d.get('text') or '').strip()
                    if len(text)<MIN_LEN: raise RuntimeError('too_short')
                    ex,r=should_exclude_by_body(a.get('title',''),text)
                    if ex: fail.append({'status':'excluded','exclude_reason':r,'url':a['url'],'source_title':a.get('title','')}); ok=True; break
                    res.append({'status':'success','source_title':a.get('title',''),'url':a['url'],'issue_url':a.get('issue_url',''),'issue_date':a.get('issue_date',''),'edition':a.get('edition',''),'page_title':d.get('title',''),'text_length':len(text),'text':text})
                    ok=True; break
                except Exception as e:
                    if str(e)=='too_short' and t<RETRIES-1: continue
                    if t==RETRIES-1: fail.append({'status':'too_short' if 'too_short' in str(e) else 'failed','url':a['url'],'source_title':a.get('title',''),'error':str(e)})
            time.sleep(SLEEP_SECONDS)
        b.close()
    OUTPUT_JSON.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8'); FAILED_JSON.write_text(json.dumps(fail,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('logs/nikkei_articles_excluded_after_fetch.json').write_text(json.dumps([x for x in fail if x.get('status')=='excluded'],ensure_ascii=False,indent=2),encoding='utf-8')
    print('success_count:',len(res)); print('failed_count:',len([x for x in fail if x.get("status")!="excluded"])); print('excluded_after_fetch_count:',len([x for x in fail if x.get("status")=="excluded"]))
if __name__=='__main__': main()
