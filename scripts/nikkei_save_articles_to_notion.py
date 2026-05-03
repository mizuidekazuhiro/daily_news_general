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
    keys=set(); pages={}; cursor=None
    while True:
        payload={'page_size':100}
        if cursor: payload['start_cursor']=cursor
        d=req('POST',f'https://api.notion.com/v1/databases/{DATABASE_ID}/query',json=payload).json()
        for it in d.get('results',[]):
            p=it.get('properties',{}).get('URL',{}); u=''
            if p.get('type')=='url': u=p.get('url') or ''
            elif p.get('type')=='rich_text': u=''.join(x.get('plain_text','') for x in p.get('rich_text',[]))
            if u:
                keys.add(u); nid=ng(u)
                if nid: keys.add(nid)
                pages[u]=it.get('id','')
        if not d.get('has_more'): break
        cursor=d.get('next_cursor')
    return keys,pages

def resolve_prop(props, names, allowed=('rich_text',)):
    for name in names:
        meta=props.get(name)
        if meta and meta.get('type') in allowed: return name
    return None

def split_blocks(text, limit=1800):
    out=[]; cur=''
    for ln in [x.strip() for x in (text or '').splitlines() if x.strip()]:
        if cur and len(cur)+len(ln)+1>limit: out.append(cur); cur=ln
        else: cur=(cur+'\n'+ln).strip()
    if cur: out.append(cur)
    return out

def append_body_blocks(page_id, text):
    chunks=split_blocks(text)
    if not chunks: return
    children=[{'object':'block','type':'heading_2','heading_2':{'rich_text':[{'type':'text','text':{'content':'Article Body'}}]} }]
    children += [{'object':'block','type':'paragraph','paragraph':{'rich_text':[{'type':'text','text':{'content':c}}]}} for c in chunks]
    req('PATCH', f'https://api.notion.com/v1/blocks/{page_id}/children', json={'children':children})

def main():
    arts=json.loads(INPUT_JSON.read_text(encoding='utf-8')) if INPUT_JSON.exists() else []
    if not arts: print('existing_url_count: 0'); print('saved: 0'); print('skipped: 0'); print('failed: 0'); print('updated_existing: 0'); return
    props=req('GET',f'https://api.notion.com/v1/databases/{DATABASE_ID}').json().get('properties',{})
    title_prop=next((k for k,v in props.items() if v.get('type')=='title'),None)
    body_prop=resolve_prop(props,['Body','Article Body','Article Text','Text','Content','本文','記事本文','Scoring Text','スコアリング用本文'])
    summary_prop=resolve_prop(props,['Summary','要約','AI Summary'])
    keys,pages=load_existing(); print('existing_url_count:',len(keys)); saved=skipped=failed=updated_existing=0; skipped_existing=0
    for a in arts:
        u=a.get('url',''); k=ng(u); page_id=a.get('page_id') or pages.get(u,''); text=(a.get('text') or '').strip(); title=(a.get('source_title') or a.get('page_title') or 'Untitled')[:2000]
        is_existing = (u in keys) or (k and k in keys) or bool(page_id)
        try:
            if is_existing and page_id:
                patch={}
                if body_prop and text: patch[body_prop]={'rich_text':[{'type':'text','text':{'content':text[:1900]}}]}
                if summary_prop and text: patch[summary_prop]={'rich_text':[{'type':'text','text':{'content':text[:1900]}}]}
                if patch: req('PATCH',f'https://api.notion.com/v1/pages/{page_id}',json={'properties':patch})
                if text and not body_prop and not summary_prop: append_body_blocks(page_id,text)
                updated_existing+=1; skipped+=1; skipped_existing+=1; continue
            if is_existing: skipped+=1; skipped_existing+=1; continue
            payload={'parent':{'database_id':DATABASE_ID},'properties':{title_prop:{'title':[{'text':{'content':title}}]},'URL':{'url':u if 'URL' in props else None}},'children':[{'object':'block','type':'paragraph','paragraph':{'rich_text':[{'type':'text','text':{'content':(text or '本文なし')[:1900]}}]}}]}
            if body_prop and text: payload['properties'][body_prop]={'rich_text':[{'type':'text','text':{'content':text[:1900]}}]}
            if summary_prop and text: payload['properties'][summary_prop]={'rich_text':[{'type':'text','text':{'content':text[:1900]}}]}
            req('POST','https://api.notion.com/v1/pages',json=payload); saved+=1
        except Exception:
            failed+=1
    print('skipped_existing_before_save:',skipped_existing); print('saved:',saved); print('skipped:',skipped); print('failed:',failed); print('updated_existing:',updated_existing)
if __name__=='__main__': main()
