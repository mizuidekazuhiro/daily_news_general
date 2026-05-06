import json, os, time, re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import requests
from dotenv import load_dotenv
load_dotenv()
NOTION_TOKEN=os.getenv('NOTION_TOKEN','').strip(); DATABASE_ID=(os.getenv('NIKKEI_ARTICLES_DB_ID','') or os.getenv('NOTION_ARTICLE_DB_ID','')).strip()
INPUT_JSON=Path('logs/nikkei_articles_full.json'); FAILED_LOG_JSON=Path('logs/nikkei_save_failed.json'); NOTION_VERSION='2022-06-28'
SUMMARY_SOURCE_FIELDS=['summary','description','meta_description','body_summary']
PROP_CANDS={
'title':['Name','Title','記事名','記事タイトル'],
'url':['URL','Url','url','Link','Article URL','Source URL'],'issue':['Issue Date','Issued Date','Published Date'],'edition':['Edition'],
'source':['Source','Media','媒体'],'fetch':['Fetch Status'],'full':['Full Text Status','FullText Status','Body Status','Extraction Status'],
'text_len':['Text Length'],'img_count':['Image Count'],'gpt':['GPT Processed'],'has_image':['Has Image'],'has_chart':['Has Chart'],
'img_url':['Image URL'],'img_cap':['Image Caption'],'body':['Body','Article Body','Article Text','Text','Content','本文','記事本文'],'summary':['Summary','要約','AI Summary']}

def headers(): return {'Authorization':f'Bearer {NOTION_TOKEN}','Notion-Version':NOTION_VERSION,'Content-Type':'application/json'}
def req(method,url,**kwargs):
 r=requests.request(method,url,headers=headers(),timeout=60,**kwargs); 
 if r.status_code==429: time.sleep(int(r.headers.get('Retry-After','2'))); return req(method,url,**kwargs)
 r.raise_for_status(); return r

def ng(url): return (parse_qs(urlparse(url).query).get('ng') or [''])[0]
def clean_text(t): return (t or '').strip()

def ensure_nikkei_title(a):
    title = clean_text(a.get('title') or a.get('headline') or a.get('source_title') or a.get('page_title') or a.get('h1_text'))
    if title:
        return title
    u = clean_text(a.get('url'))
    nid = ng(u)
    return f"Untitled Nikkei Article - {nid or 'unknown'}"

def clean_nikkei_body_text(text):
    text = (text or '').replace('<br>', '\n')
    before = len(text)
    removed = 0
    samples = []
    subs = [r'企業での記事共有や会議資料への転載・複製[^【\n]*', r'朝夕刊や電子版ではお伝えしきれない情報をお届けします。?[^【\n]*', r'日経の記事利用サービス', r'javascript:void\\(0\\)', r'詳しくはこちら']
    lines=[]
    for raw in text.splitlines():
        line=raw.strip()
        if not line:
            continue
        original=line
        for pat in subs:
            line2=re.sub(pat,'',line).strip()
            if line2!=line:
                removed += 1
                if len(samples)<5: samples.append(pat)
            line=line2
        if line in {'共有','文字サイズ','小','中','大','保存','印刷 翻訳','検索する','その他','［有料会員限定］','[有料会員限定]'}:
            removed += 1
            continue
        lines.append(line)
    text='\n'.join(lines)
    text=re.sub(r'\n{3,}','\n\n',text).strip()
    print(f"text_length_before_clean: {before}")
    print(f"text_length_after_clean: {len(text)}")
    print(f"removed_boilerplate_count: {removed}")
    print(f"removed_boilerplate_samples: {samples}")
    return text

def is_nav(t):
 x=clean_text(t); kws=['速報','アクセスランキング','トピック一覧','人事','おくやみ','プレスリリース','メディア一覧','ビューアーで読む']
 return sum(x.count(k) for k in kws)>=3

def load_existing():
 keys=set(); pages={}; cur=None
 while True:
  p={'page_size':100};
  if cur: p['start_cursor']=cur
  d=req('POST',f'https://api.notion.com/v1/databases/{DATABASE_ID}/query',json=p).json()
  for it in d.get('results',[]):
   props=it.get('properties',{}); pid=it.get('id','')
   for n,v in props.items():
    if v.get('type') not in {'url','rich_text'}: continue
    u=(v.get('url') or '') if v.get('type')=='url' else ''.join(x.get('plain_text','') for x in v.get('rich_text',[]))
    if u:
      keys.add(u); pages[u]=pid; nid=ng(u)
      if nid: keys.add(nid); keys.add(f'ng:{nid}'); pages[nid]=pid; pages[f'ng:{nid}']=pid
  if not d.get('has_more'): break
  cur=d.get('next_cursor')
 return keys,pages

def find_prop(props,cands):
 for n in cands:
  if n in props: return n
 return None

def set_prop(meta,val):
 t=meta.get('type')
 if val is None: return None
 if t=='title': return {'title':[{'type':'text','text':{'content':str(val)[:1900]}}]}
 if t=='rich_text': return {'rich_text':[{'type':'text','text':{'content':str(val)[:1900]}}]}
 if t=='select': return {'select':{'name':str(val)[:100]}}
 if t=='multi_select': return {'multi_select':[{'name':str(x)[:100]} for x in (val if isinstance(val,list) else [val]) if str(x).strip()]}
 if t=='number': return {'number':float(val)}
 if t=='checkbox': return {'checkbox':bool(val)}
 if t=='date': return {'date':{'start':str(val)}}
 if t=='url': return {'url':str(val)}
 return None

def get_summary_text(a):
 for k in SUMMARY_SOURCE_FIELDS:
  v=clean_text(a.get(k,''));
  if v: return v
 return ''



def split_blocks(text, limit=1800):
    out=[]; cur=''
    for ln in [x.strip() for x in (text or '').splitlines() if x.strip()]:
        if cur and len(cur)+len(ln)+1>limit: out.append(cur); cur=ln
        else: cur=(cur+'\n'+ln).strip()
    if cur: out.append(cur)
    return out

def append_body_blocks(page_id,text,title=''):
    chunks=split_blocks(text)
    if not chunks: return 0

    children=[
        {
            "object":"block",
            "type":"heading_2",
            "heading_2":{
                "rich_text":[{"type":"text","text":{"content":"タイトル"}}]
            }
        }
    ]

    if title:
        children.append({
            "object":"block",
            "type":"heading_3",
            "heading_3":{
                "rich_text":[{"type":"text","text":{"content":str(title)[:1900]}}]
            }
        })

    children.append({
        "object":"block",
        "type":"heading_2",
        "heading_2":{
            "rich_text":[{"type":"text","text":{"content":"本文"}}]
        }
    })

    children += [
        {
            "object":"block",
            "type":"paragraph",
            "paragraph":{
                "rich_text":[{"type":"text","text":{"content":c[:1900]}}]
            }
        }
        for c in chunks
    ]

    req('PATCH',f'https://api.notion.com/v1/blocks/{page_id}/children',json={'children':children[:100]})
    return len(children)

def list_page_children(page_id):
    out=[]; cur=None
    while True:
        url=f'https://api.notion.com/v1/blocks/{page_id}/children?page_size=100'
        if cur:
            url += f'&start_cursor={cur}'
        data=req('GET',url).json()
        out.extend(data.get('results',[]))
        if not data.get('has_more'):
            break
        cur=data.get('next_cursor')
    return out

def _heading_text(block):
    btype=block.get('type')
    if btype not in {'heading_1','heading_2','heading_3'}:
        return ''
    return ''.join(x.get('plain_text','') for x in block.get(btype,{}).get('rich_text',[])).strip()

def has_body_heading(page_id):
    for b in list_page_children(page_id):
        if _heading_text(b) in {'記事本文','タイトル','本文'}:
            return True
    return False

def delete_existing_body_blocks(page_id):
    children=list_page_children(page_id)
    deleting=False
    deleted=0
    start_headings={'記事本文','タイトル','本文'}
    stop_headings={'メタデータ'}

    for b in children:
        t=_heading_text(b)

        if t in start_headings and not deleting:
            deleting=True
        elif deleting and t in stop_headings:
            break

        if deleting:
            req('PATCH',f"https://api.notion.com/v1/blocks/{b['id']}",json={'archived':True})
            deleted += 1

    print(f"deleted_existing_body_blocks: {deleted}")
    return deleted

def main():
 arts=json.loads(INPUT_JSON.read_text()) if INPUT_JSON.exists() else []
 props=req('GET',f'https://api.notion.com/v1/databases/{DATABASE_ID}').json().get('properties',{})
 skip_existing = os.getenv("NIKKEI_SKIP_EXISTING_NOTION_URLS", "true").strip().lower() in {"1", "true", "yes", "on"}
 if skip_existing:
  keys,pages=load_existing()
 else:
  keys,pages=set(),{}
 print('skip_existing_notion_urls:', skip_existing)
 print('existing_url_count:',len(keys)); fails=[]; stats={k:0 for k in ['metadata_written_count','source_written','fetch_status_written','full_text_status_written','text_length_written','image_count_written','has_image_written','has_chart_written']}; missing=[]; skipped=[]
 mapn={k:find_prop(props,v) for k,v in PROP_CANDS.items()}
 for a in arts:
  u=a.get('url','').strip(); title=ensure_nikkei_title(a); text=clean_nikkei_body_text(a.get('text','')); extraction=a.get('extraction_status','success')
  rejected=is_nav(text) or extraction=='failed'; full='saved' if text and not rejected else ('failed' if extraction=='failed' else 'rejected_navigation_text')
  clean='' if rejected else text; summary=get_summary_text(a); pid=a.get('page_id') or pages.get(u) or pages.get(ng(u)) or pages.get(f"ng:{ng(u)}")
  payload={}; fields={'title':title,'url':u,'issue':a.get('issue_date'),'edition':a.get('edition'),'source':'Nikkei','fetch':a.get('status') or extraction or 'success','full':full,'text_len':len(clean) if clean else 0,'img_count':a.get('image_count',0) or 0,'gpt':False,'has_image':bool((a.get('image_count',0) or 0)>0 or a.get('image_url')),'has_chart':bool(a.get('has_chart',False)),'img_url':a.get('image_url',''),'img_cap':a.get('image_caption','')}
  print(f"title_saved_to_notion: {title}")
  if clean and mapn['body']: fields['body']=clean
  if summary and mapn['summary']: fields['summary']=summary
  for key,val in fields.items():
   pn=mapn.get(key)
   if not pn: missing.append(key); continue
   patch=set_prop(props[pn],val)
   if patch is None: skipped.append(pn); continue
   payload[pn]=patch
  try:
   if pid:
    req('PATCH',f'https://api.notion.com/v1/pages/{pid}',json={'properties':payload})
    if clean:
        if has_body_heading(pid):
            delete_existing_body_blocks(pid)
        append_body_blocks(pid,clean,title)
   else:
    created=req('POST','https://api.notion.com/v1/pages',json={'parent':{'database_id':DATABASE_ID},'properties':payload}).json()
    if clean and created.get('id'): append_body_blocks(created['id'],clean,title)
  except Exception as e: fails.append({'url':u,'error':str(e)})
 FAILED_LOG_JSON.write_text(json.dumps(fails,ensure_ascii=False,indent=2),encoding='utf-8') if fails else None
 print('metadata_written_count:',len(arts)); print('source_written:',len(arts)); print('fetch_status_written:',len(arts)); print('full_text_status_written:',len(arts)); print('text_length_written:',len(arts)); print('image_count_written:',len(arts)); print('has_image_written:',len(arts)); print('has_chart_written:',len(arts)); print('missing_metadata_properties:',sorted(set(missing))); print('skipped_type_mismatch_properties:',sorted(set(skipped)))
if __name__=='__main__': main()
