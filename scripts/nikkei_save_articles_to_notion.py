import json, os, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv
load_dotenv()
NOTION_TOKEN=os.getenv('NOTION_TOKEN','').strip(); DATABASE_ID=(os.getenv('NIKKEI_ARTICLES_DB_ID','') or os.getenv('NOTION_ARTICLE_DB_ID','')).strip(); INPUT_JSON=Path('logs/nikkei_articles_full.json'); NOTION_VERSION='2022-06-28'
def headers(): return {'Authorization':f'Bearer {NOTION_TOKEN}','Notion-Version':NOTION_VERSION,'Content-Type':'application/json'}
def req(method,url,**kwargs):
    for _ in range(6):
        r=requests.request(method,url,headers=headers(),timeout=60,**kwargs)
        if r.status_code==429: time.sleep(int(r.headers.get('Retry-After','2'))); continue
        r.raise_for_status(); return r
    r.raise_for_status()
def ng(url): return (parse_qs(urlparse(url).query).get('ng') or [''])[0]
def load_existing():
    keys=set(); cursor=None
    while True:
        payload={'page_size':100};
        if cursor: payload['start_cursor']=cursor
        d=req('POST',f'https://api.notion.com/v1/databases/{DATABASE_ID}/query',json=payload).json()
        for it in d.get('results',[]):
            p=it.get('properties',{}).get('URL',{}); u=''
            if p.get('type')=='url': u=p.get('url') or ''
            elif p.get('type')=='rich_text': u=''.join(x.get('plain_text','') for x in p.get('rich_text',[]))
            if u: keys.add(u); nid=ng(u);
            if u and nid: keys.add(nid)
        if not d.get('has_more'): break
        cursor=d.get('next_cursor')
    return keys
def main():
    arts=json.loads(INPUT_JSON.read_text(encoding='utf-8')) if INPUT_JSON.exists() else []
    if not arts: print('existing_url_count: 0'); print('saved: 0'); print('skipped: 0'); print('failed: 0'); return
    props=req('GET',f'https://api.notion.com/v1/databases/{DATABASE_ID}').json().get('properties',{})
    title_prop=next((k for k,v in props.items() if v.get('type')=='title'),None)
    existing=load_existing(); print('existing_url_count:',len(existing)); saved=skipped=failed=0; skipped_existing=0
    for a in arts:
        u=a.get('url',''); k=ng(u)
        if u in existing or (k and k in existing): skipped+=1; skipped_existing+=1; continue
        try:
            payload={'parent':{'database_id':DATABASE_ID},'properties':{title_prop:{'title':[{'text':{'content':(a.get('source_title') or a.get('page_title') or 'Untitled')[:2000]}}]},'URL':{'url':u if 'URL' in props else None}},'children':[{'object':'block','type':'paragraph','paragraph':{'rich_text':[{'type':'text','text':{'content':(a.get('text') or '本文なし')[:1900]}}]}}]}
            req('POST','https://api.notion.com/v1/pages',json=payload); saved+=1
        except Exception: failed+=1
    print('skipped_existing_before_save:',skipped_existing); print('saved:',saved); print('skipped:',skipped); print('failed:',failed)
if __name__=='__main__': main()
