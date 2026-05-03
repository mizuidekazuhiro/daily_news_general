import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

STORAGE_PATH = Path('.storage/nikkei_storage_state.json')
INPUT_PATH = Path('logs/nikkei_issue_article_links.json')
OUTPUT_JSON = Path('logs/nikkei_articles_full.json')
FAILED_JSON = Path('logs/nikkei_articles_failed.json')
FAILED_DIR = Path('logs/nikkei_failed_articles')

MAX_ARTICLES = int(os.getenv('NIKKEI_MAX_ARTICLES_TO_FETCH', '0'))
SLEEP_SECONDS = float(os.getenv('NIKKEI_FETCH_SLEEP_SECONDS', '1.0'))
MIN_LEN = int(os.getenv('NIKKEI_MIN_ARTICLE_TEXT_LENGTH', '120'))
RETRIES = int(os.getenv('NIKKEI_ARTICLE_EXTRACT_RETRIES', '3'))
GOTO_TIMEOUT = int(os.getenv('NIKKEI_ARTICLE_GOTO_TIMEOUT_MS', '25000'))
WAIT_AFTER = int(os.getenv('NIKKEI_ARTICLE_WAIT_AFTER_LOAD_MS', '800'))
BLOCK_HEAVY = os.getenv('NIKKEI_BLOCK_HEAVY_RESOURCES', 'true').lower() == 'true'
RETRY_WITHOUT_BLOCK = os.getenv('NIKKEI_RETRY_WITHOUT_RESOURCE_BLOCK_ON_FAILURE', 'true').lower() == 'true'

SKIP_EXISTING = os.getenv('NIKKEI_SKIP_EXISTING_NOTION_URLS', 'true').lower() == 'true'
PAGE_SIZE = int(os.getenv('NIKKEI_EXISTING_URL_LOOKUP_PAGE_SIZE', '100'))
NOTION_TOKEN = os.getenv('NOTION_TOKEN', '').strip()
DB = (os.getenv('NIKKEI_ARTICLES_DB_ID', '') or os.getenv('NOTION_ARTICLE_DB_ID', '')).strip()


def extract_nikkei_ng_id(url: str) -> str:
    return (parse_qs(urlparse(url).query).get('ng') or [''])[0]


def normalize_nikkei_article_key(url: str) -> str:
    return extract_nikkei_ng_id(url) or url.strip()


def notion_headers():
    return {'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': '2022-06-28', 'Content-Type': 'application/json'}


def notion_req(url, payload):
    while True:
        r = requests.post(url, headers=notion_headers(), json=payload, timeout=60)
        if r.status_code == 429:
            time.sleep(int(r.headers.get('Retry-After', '2')))
            continue
        r.raise_for_status()
        return r.json()


def fetch_existing():
    if not (SKIP_EXISTING and NOTION_TOKEN and DB):
        return set(), False
    meta = requests.get(f'https://api.notion.com/v1/databases/{DB}', headers=notion_headers(), timeout=60).json()
    props = meta.get('properties', {})
    if 'URL' not in props:
        print('WARNING: URL property missing; disable skip existing')
        return set(), False
    cur = None
    keys = set()
    while True:
        payload = {'page_size': PAGE_SIZE}
        if cur:
            payload['start_cursor'] = cur
        d = notion_req(f'https://api.notion.com/v1/databases/{DB}/query', payload)
        for it in d.get('results', []):
            p = it.get('properties', {}).get('URL', {})
            u = ''
            if p.get('type') == 'url':
                u = p.get('url') or ''
            elif p.get('type') == 'rich_text':
                u = ''.join(x.get('plain_text', '') for x in p.get('rich_text', []))
            if u:
                keys.add(normalize_nikkei_article_key(u))
                keys.add(u)
        if not d.get('has_more'):
            break
        cur = d.get('next_cursor')
    return keys, True


def detect_walls(text: str):
    login_markers = ['ログイン', '会員登録', '無料会員', '続きは会員限定', 'この記事は会員限定']
    paid_markers = ['有料会員限定', 'この記事は有料会員限定', '購読', '日経電子版を購読']
    return any(x in text for x in login_markers), any(x in text for x in paid_markers)


def select_text_with_candidates(page):
    script = """() => {
      const title = document.querySelector('h1')?.innerText?.trim() || document.title || '';
      const cands = [
        ['article','article'],
        ['main','main'],
        ['data-track-article-body','[data-track-article-body]'],
        ['cmn-section','.cmn-section'],
        ['articleBody','.articleBody'],
        ['article-body','.article-body'],
        ['body-class','.body'],
      ];
      const out = [];
      for (const [name, sel] of cands) {
        const txt = Array.from(document.querySelectorAll(sel)).map(x => x.innerText || '').join('\n').trim();
        out.push({selector: name, text: txt, text_length: txt.length});
      }
      const h1 = document.querySelector('h1');
      let h1Parent = '';
      if (h1) {
        const p = h1.closest('article,main,section,div');
        h1Parent = (p?.innerText || '').trim();
      }
      out.push({selector: 'h1_parent', text: h1Parent, text_length: h1Parent.length});
      let bodyText = (document.body?.innerText || '').trim();
      bodyText = bodyText.replace(/メニュー[\s\S]{0,400}?ログイン/g, '');
      out.push({selector: 'document_body_fallback', text: bodyText, text_length: bodyText.length});
      return {title, candidates: out};
    }"""
    data = page.evaluate(script)
    candidates = data.get('candidates', [])
    filtered = [c for c in candidates if c.get('text_length', 0) > 0]
    best = max(filtered, key=lambda x: x.get('text_length', 0)) if filtered else {'selector': 'none', 'text': '', 'text_length': 0}
    return data.get('title', ''), candidates, best


def should_exclude_by_body(title, text):
    if '人事記事をもっと見る' in text:
        return True, 'hr_article_marker'
    if re.search(r'.*\s[^\s]{2,6}氏$', title or '') and any(x in text for x in ['人事', '就任', '社長', '会長', '役員']) and len(text) < 600 and not any(k in text for k in ['M&A', '投資', '決算', '設備投資', '資本提携', '能力増強', '合弁', '買収', 'TOB', 'インタビュー']):
        return True, 'hr_short_executive_article'
    return False, ''


def save_failure_artifacts(page, idx: int):
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    base = FAILED_DIR / str(idx)
    html_path = f'{base}.html'
    png_path = f'{base}.png'
    txt_path = f'{base}.txt'
    try:
        Path(html_path).write_text(page.content(), encoding='utf-8')
    except Exception:
        pass
    try:
        page.screenshot(path=png_path, full_page=True)
    except Exception:
        pass
    try:
        Path(txt_path).write_text(page.inner_text('body'), encoding='utf-8')
    except Exception:
        pass
    return html_path, png_path, txt_path


def main():
    arts = json.loads(INPUT_PATH.read_text(encoding='utf-8')) if INPUT_PATH.exists() else []
    if MAX_ARTICLES > 0:
        arts = arts[:MAX_ARTICLES]
    keys, enabled = fetch_existing()
    skipped = []
    if enabled:
        keep = []
        for a in arts:
            if a['url'] in keys or normalize_nikkei_article_key(a['url']) in keys:
                skipped.append(a)
            else:
                keep.append(a)
        arts = keep
    Path('logs/nikkei_articles_skipped_existing.json').write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding='utf-8')

    res, fail = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for i, a in enumerate(arts, 1):
            attempts = RETRIES + (1 if RETRY_WITHOUT_BLOCK else 0)
            ok = False
            for t in range(attempts):
                use_block = BLOCK_HEAVY and not (RETRY_WITHOUT_BLOCK and t == attempts - 1)
                c = b.new_context(storage_state=str(STORAGE_PATH), locale='ja-JP', timezone_id='Asia/Tokyo')
                if use_block:
                    c.route('**/*', lambda route, req: route.abort() if req.resource_type in {'image', 'media', 'font', 'stylesheet'} else route.continue_())
                page = c.new_page()
                try:
                    page.goto(a['url'], wait_until='domcontentloaded', timeout=GOTO_TIMEOUT)
                    page.wait_for_timeout(WAIT_AFTER)
                    page_title, selector_logs, best = select_text_with_candidates(page)
                    text = (best.get('text') or '').strip()
                    login_wall, paid_wall = detect_walls(text)
                    ex, r = should_exclude_by_body(a.get('title', ''), text)
                    if ex:
                        fail.append({'status': 'excluded', 'exclude_reason': r, 'url': a['url'], 'source_title': a.get('title', '')})
                        ok = True
                        break
                    if len(text) < MIN_LEN and not login_wall and not paid_wall and len(text) >= 40:
                        status = 'success_short'
                    elif len(text) < MIN_LEN:
                        raise RuntimeError('too_short')
                    else:
                        status = 'success'
                    res.append({
                        'status': status, 'source_title': a.get('title', ''), 'url': a['url'], 'issue_url': a.get('issue_url', ''),
                        'issue_date': a.get('issue_date', ''), 'edition': a.get('edition', ''), 'page_title': page_title,
                        'text_length': len(text), 'text': text, 'selector_used': best.get('selector', ''), 'selector_candidates': selector_logs,
                    })
                    ok = True
                    break
                except Exception as e:
                    if t == attempts - 1:
                        page_title = ''
                        selector_used = ''
                        text = ''
                        login_wall, paid_wall = False, False
                        try:
                            page_title, selector_logs, best = select_text_with_candidates(page)
                            selector_used = best.get('selector', '')
                            text = (best.get('text') or '').strip()
                            login_wall, paid_wall = detect_walls(text)
                        except Exception:
                            selector_logs = []
                        html_path, png_path, txt_path = save_failure_artifacts(page, i)
                        fail.append({
                            'status': 'failed', 'url': a['url'], 'source_title': a.get('title', ''), 'attempt_count': attempts,
                            'final_page_url': page.url if page else '', 'error_type': type(e).__name__, 'error_message': str(e),
                            'text_length': len(text), 'page_title': page_title, 'body_text_preview': text[:500],
                            'is_login_wall_detected': login_wall, 'is_paid_article_wall_detected': paid_wall,
                            'selector_used': selector_used, 'selector_candidates': selector_logs,
                            'screenshot_path': png_path, 'html_path': html_path, 'text_path': txt_path,
                            'block_heavy_resources': use_block,
                        })
                finally:
                    c.close()
            time.sleep(SLEEP_SECONDS)
            if not ok:
                pass
        b.close()

    OUTPUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    FAILED_JSON.write_text(json.dumps(fail, ensure_ascii=False, indent=2), encoding='utf-8')
    print('target_count:', len(arts))
    print('success_count:', len(res))
    print('failed_count:', len([x for x in fail if x.get('status') != 'excluded']))


if __name__ == '__main__':
    main()
