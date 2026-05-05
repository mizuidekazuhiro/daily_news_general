import json, os, re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

load_dotenv()
STORAGE_PATH=Path('.storage/nikkei_storage_state.json')
OUTPUT_DIR=Path('logs'); OUTPUT_DIR.mkdir(exist_ok=True)
ENTRY_URL=os.getenv('NIKKEI_MORNING_URL','https://www.nikkei.com/paper/').strip()
EDITION=os.getenv('NIKKEI_EDITION','morning').strip(); TARGET_DATE=os.getenv('NIKKEI_TARGET_DATE','auto').strip(); REQUIRE_TODAY=os.getenv('NIKKEI_REQUIRE_TODAY','false').lower()=='true'
EXCLUDE_TITLE_REGEX=os.getenv('NIKKEI_EXCLUDE_TITLE_REGEX','').strip(); USE_DIRECT_ISSUE_URL=os.getenv('NIKKEI_USE_DIRECT_ISSUE_URL','true').lower()=='true'; ALLOW_DIRECT_FALLBACK=os.getenv('NIKKEI_ALLOW_DIRECT_FALLBACK','false').lower()=='true'; PAPER_URL_TEMPLATE=os.getenv('NIKKEI_PAPER_URL_TEMPLATE','https://www.nikkei.com/paper/{edition}/?b={date}&d=0').strip()
ENABLE_PRE=os.getenv('NIKKEI_ENABLE_PRE_TITLE_FILTER','true').lower()=='true'; PRE_REGEX=os.getenv('NIKKEI_PRE_EXCLUDE_TITLE_REGEX','').strip(); PRE_SHORT=os.getenv('NIKKEI_PRE_EXCLUDE_SHORT_TITLES','true').lower()=='true'; PRE_HR=os.getenv('NIKKEI_PRE_EXCLUDE_HR_LIKE_TITLES','true').lower()=='true'
JST=timezone(timedelta(hours=9))
EDITION_MARKERS = {"morning": "M", "evening": "E"}
DEFAULT_PAT=[r'野球',r'阪神',r'広島',r'日ハム',r'国内女子',r'国内男子',r'ゴルフ',r'大リーグ',r'競馬',r'天皇賞',r'欧州CL',r'NBA',r'J3',r'ラグビー',r'車いすラグビー',r'PO1回戦',r'首位スタート',r'決勝打',r'逆転弾',r'若冲',r'歌人',r'小説家',r'連載',r'澤田瞳子',r'江戸を隠してふところに',r'はじまりの横浜',r'熱国之巻',r'戦艦大和',r'VRでウルトラセブン',r'美術館',r'絵巻物',r'福田美術館',r'死去',r'悼む',r'訃報',r'おくやみ',r'^\d{1,2}日$',r'^市場情報$',r'^30日の相場表変更$',r'^自社株取得枠設定$']

def target_date_yyyymmdd(): return TARGET_DATE if TARGET_DATE and TARGET_DATE!='auto' else datetime.now(JST).strftime('%Y%m%d')
def build_direct_issue_url(): return PAPER_URL_TEMPLATE.format(edition=EDITION,date=target_date_yyyymmdd())
def wait_page(p):
    for s in ('domcontentloaded','load'):
        try:p.wait_for_load_state(s,timeout=15000)
        except PlaywrightTimeoutError:pass
    try:p.wait_for_load_state('networkidle',timeout=1500)
    except PlaywrightTimeoutError:pass
    p.wait_for_timeout(400)

def wait_for_url_stability(p, settle_ms=400):
    first_url=p.url
    p.wait_for_timeout(settle_ms)
    second_url=p.url
    if first_url!=second_url:
        print('url_changed_after_goto:', first_url, '->', second_url)
        try:p.wait_for_load_state('domcontentloaded',timeout=10000)
        except PlaywrightTimeoutError:pass
        p.wait_for_timeout(250)

def is_navigation_context_error(exc: Exception) -> bool:
    msg=str(exc).lower()
    return any(k in msg for k in (
        'execution context was destroyed',
        'most likely because of a navigation',
        'navigat',
        'context destroyed',
    ))

def safe_collect_anchor_links(page, max_attempts=5, sleep_seconds=1.0):
    links=[]
    for attempt in range(1,max_attempts+1):
        print(f'collect_links_attempt: {attempt}/{max_attempts} current_url={page.url}')
        try:page.wait_for_load_state('domcontentloaded',timeout=10000)
        except PlaywrightTimeoutError:pass
        try:page.wait_for_load_state('load',timeout=8000)
        except PlaywrightTimeoutError:pass
        try:page.wait_for_load_state('networkidle',timeout=1500)
        except PlaywrightTimeoutError:pass

        try:
            links=page.evaluate("""() => Array.from(document.querySelectorAll('a')).map(a=>({text:(a.innerText||a.textContent||'').trim(),href:a.href||''})).filter(x=>x.text&&x.href)""")
            print('collect_links_count:', len(links))
            if links:
                return links
            if attempt<max_attempts:
                page.wait_for_timeout(int(sleep_seconds*1000))
        except PlaywrightError as e:
            if is_navigation_context_error(e) and attempt<max_attempts:
                print('collect_links_retry_reason: navigation_context_destroyed')
                page.wait_for_timeout(int(sleep_seconds*1000))
                continue
            raise
    return links

def save_collect_links_diagnostics(page):
    html_path=OUTPUT_DIR/'nikkei_issue_links_failed.html'
    png_path=OUTPUT_DIR/'nikkei_issue_links_failed.png'
    url_path=OUTPUT_DIR/'nikkei_issue_links_failed_url.txt'
    txt_path=OUTPUT_DIR/'nikkei_issue_links_failed_text.txt'
    url_path.write_text(page.url or '',encoding='utf-8')
    try: html_path.write_text(page.content(),encoding='utf-8')
    except Exception as e: html_path.write_text(f'failed_to_dump_html: {e}',encoding='utf-8')
    try: page.screenshot(path=str(png_path),full_page=True)
    except Exception as e: txt_path.write_text(f'failed_to_capture_screenshot: {e}\n',encoding='utf-8')
    try:
        body_text=page.evaluate("""() => (document.body && document.body.innerText) ? document.body.innerText : ''""")
        txt_path.write_text(body_text,encoding='utf-8')
    except Exception as e:
        with txt_path.open('a',encoding='utf-8') as f: f.write(f'failed_to_dump_text: {e}\n')
    print('collect_links_failed_artifacts:', str(url_path), str(html_path), str(png_path), str(txt_path))

def collect_links(p,b):
    wait_page(p)
    links=safe_collect_anchor_links(p,max_attempts=5,sleep_seconds=1.0)
    out=[];seen=set()
    for x in links:
        h=urljoin(b,x['href']);t=' '.join(x['text'].split())
        if h and t and h not in seen: seen.add(h); out.append({'title':t,'url':h})
    return out
def get_b(url):
    v=parse_qs(urlparse(url).query).get('b')or[]; return v[0] if v else ''
def is_article(url):
    p=urlparse(url);q=parse_qs(p.query); return 'nikkei.com' in p.netloc and p.path=='/paper/article/' and 'ng' in q

def expected_edition_marker(edition: str) -> str:
    return EDITION_MARKERS.get((edition or "").strip().lower(), "")

def find_detected_edition_ids(text: str, issue_date: str) -> list[str]:
    if not text or not issue_date:
        return []
    pat = re.compile(rf"editionID=({re.escape(issue_date)}[ME][0-9A-Za-z]+)", re.IGNORECASE)
    return list(dict.fromkeys(m.group(1) for m in pat.finditer(text)))

def edition_mismatch_summary(*, expected_edition: str, issue_date: str, detected_ids: list[str], issue_url: str, direct_issue_url: str) -> dict:
    marker = expected_edition_marker(expected_edition)
    detected_id = detected_ids[0] if detected_ids else ""
    detected_marker = detected_id[8:9] if len(detected_id) >= 9 else ""
    mismatch = bool(marker and detected_marker and marker != detected_marker)
    return {
        "edition_check_result": "edition_mismatch" if mismatch else "ok",
        "skip_reason": "edition_mismatch" if mismatch else "",
        "expected_edition": expected_edition,
        "expected_edition_marker": marker,
        "detected_edition_id": detected_id,
        "detected_edition_marker": detected_marker,
        "opened_issue_url": issue_url,
        "direct_issue_url": direct_issue_url,
        "skip_final_report": mismatch,
        "mail_skipped_reason": "edition_mismatch" if mismatch else "",
    }

def pre_exclude(title):
    t=title or ''
    if EXCLUDE_TITLE_REGEX and re.search(EXCLUDE_TITLE_REGEX,t): return 'title_regex'
    if not ENABLE_PRE: return ''
    pats=DEFAULT_PAT + ([PRE_REGEX] if PRE_REGEX else [])
    for pat in pats:
        try:
            if re.search(pat,t): return 'pre_title_regex'
        except re.error: pass
    if PRE_HR and (re.search(r'.*\s[^\s]{2,6}氏$',t) or re.search(r'社長\s.*氏',t) or re.search(r'CEOに.*氏',t)): return 'pre_hr_like_title'
    if PRE_SHORT and len(t)<=6: return 'pre_short_title'
    return ''

def main():
    if not STORAGE_PATH.exists(): raise FileNotFoundError(STORAGE_PATH)
    excluded=[]; fallback_entry_used=False
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True); c=b.new_context(storage_state=str(STORAGE_PATH),locale='ja-JP',timezone_id='Asia/Tokyo'); page=c.new_page(); page.set_default_timeout(20000)
        issue_url=build_direct_issue_url(); links=[]
        print(f'use_direct_issue_url: {str(USE_DIRECT_ISSUE_URL).lower()}'); print('direct_issue_url:',issue_url)
        if USE_DIRECT_ISSUE_URL:
            try:
                print('open_issue_directly: true'); print('open_issue_url:',issue_url); page.goto(issue_url,wait_until='domcontentloaded',timeout=45000); print('page.goto_after_url:',page.url); wait_for_url_stability(page); links=collect_links(page,issue_url)
            except Exception:
                if not ALLOW_DIRECT_FALLBACK: raise
                print('open_issue_directly: false')
        if not links:
            if USE_DIRECT_ISSUE_URL and not ALLOW_DIRECT_FALLBACK:
                save_collect_links_diagnostics(page)
                raise RuntimeError('No links collected from direct issue URL and direct fallback is disabled.')
            fallback_entry_used=True; print('open entry:',ENTRY_URL); page.goto(ENTRY_URL,wait_until='domcontentloaded',timeout=45000); print('page.goto_after_url:',page.url); wait_for_url_stability(page); entry_links=collect_links(page,ENTRY_URL)
            cand=[x['url'] for x in entry_links if f'/paper/{EDITION}/' in x['url'] and get_b(x['url'])]
            if cand: issue_url=cand[0]; print('open_issue_url:',issue_url); page.goto(issue_url,wait_until='domcontentloaded',timeout=45000); print('page.goto_after_url:',page.url); wait_for_url_stability(page); links=collect_links(page,issue_url)
        issue_date = target_date_yyyymmdd()
        page_text = (page.content() or "") + "\n" + (page.evaluate("""() => (document.body && document.body.innerText) ? document.body.innerText : ''""") or "")
        link_text = "\n".join([f"{x.get('title','')}\n{x.get('url','')}" for x in links])
        detected_ids = find_detected_edition_ids(f"{page_text}\n{link_text}", issue_date)
        edition_check = edition_mismatch_summary(expected_edition=EDITION, issue_date=issue_date, detected_ids=detected_ids, issue_url=page.url, direct_issue_url=build_direct_issue_url())
        print(f"expected_edition: {edition_check['expected_edition']}")
        print(f"detected_edition_id: {edition_check['detected_edition_id']}")
        print(f"edition_check_result: {edition_check['edition_check_result']}")
        if edition_check["edition_check_result"] == "edition_mismatch":
            (OUTPUT_DIR/'nikkei_issue_article_links.json').write_text("[]", encoding='utf-8')
            (OUTPUT_DIR/'nikkei_issue_all_links.json').write_text(json.dumps(links,ensure_ascii=False,indent=2),encoding='utf-8')
            (OUTPUT_DIR/'nikkei_issue_excluded_links.json').write_text("[]", encoding='utf-8')
            (OUTPUT_DIR/'nikkei_issue_skip_summary.json').write_text(json.dumps(edition_check, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"skip_reason: {edition_check['skip_reason']}")
            print('article_count: 0')
            b.close(); return
        print(f'fallback_entry_used: {str(fallback_entry_used).lower()}'); print('final_issue_links_count:',len(links))
        raw=0; arts=[]; seen=set()
        for i in links:
            if not is_article(i['url']): continue
            raw+=1; ng=parse_qs(urlparse(i['url']).query).get('ng',[i['url']])[0]
            if ng in seen: continue
            seen.add(ng)
            rec={'title':i['title'],'url':i['url'],'issue_url':issue_url,'issue_date':get_b(i['url']) or get_b(issue_url),'edition':EDITION}
            reason=pre_exclude(i['title'])
            if reason: rec['exclude_reason']=reason; excluded.append(rec)
            else: arts.append(rec)
        (OUTPUT_DIR/'nikkei_issue_article_links.json').write_text(json.dumps(arts,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUTPUT_DIR/'nikkei_issue_all_links.json').write_text(json.dumps(links,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUTPUT_DIR/'nikkei_issue_excluded_links.json').write_text(json.dumps(excluded,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUTPUT_DIR/'nikkei_issue_skip_summary.json').write_text(json.dumps(edition_check, ensure_ascii=False, indent=2), encoding='utf-8')
        print('raw_article_count:',raw); print('pre_excluded_count:',len(excluded)); print('article_count_after_pre_filter:',len(arts)); print('article_count:',len(arts))
        b.close()
if __name__=='__main__': main()
