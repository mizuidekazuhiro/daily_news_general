import json
import os
import re
import time
from collections import Counter
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
SUMMARY_JSON = Path('logs/nikkei_fetch_summary.json')

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
    login_markers = ['ログイン', '会員登録', 'ログインしてください', '日経ID']
    paid_markers = ['有料会員', '続きは会員限定', 'この記事は会員限定', '購読']
    access_markers = ['Access Denied', 'Forbidden', '403', 'Bot', '不正なアクセス']
    return (
        any(x in text for x in login_markers),
        any(x in text for x in paid_markers),
        any(x in text for x in access_markers),
    )


def looks_like_noise(text: str) -> bool:
    if not text.strip():
        return True
    noise_markers = ['メニュー', 'サイトマップ', '利用規約', 'プライバシー', 'ページが見つかりません', 'エラー']
    if len(text) < MIN_LEN and sum(m in text for m in noise_markers) >= 2:
        return True
    if re.search(r'(ログイン|会員登録).{0,120}(ログイン|会員登録)', text):
        return True
    return False


def select_text_with_candidates(page):
    script = r'''() => {
      const title = document.querySelector('h1')?.innerText?.trim() || document.title || '';
      const cands = [
        ['div.cmn-section.cmn-indent','div.cmn-section.cmn-indent'],
        ['article','article'],
        ['main','main'],
        ['.cmn-section','.cmn-section'],
        ['[data-track-article-body]','[data-track-article-body]'],
        ['.articleBody','.articleBody'],
        ['.article-body','.article-body']
      ];
      const out = [];
      for (const [name, sel] of cands) {
        const txt = Array.from(document.querySelectorAll(sel)).map(x => x.innerText || '').join('\n').trim();
        out.push({selector: name, text: txt, text_length: txt.length, preview: txt.slice(0, 240)});
      }
      let bodyText = (document.body?.innerText || '').trim();
      bodyText = bodyText.replace(/メニュー[\s\S]{0,400}?ログイン/g, '');
      out.push({selector: 'document.body.innerText fallback', text: bodyText, text_length: bodyText.length, preview: bodyText.slice(0, 240)});
      return {title, candidates: out, pageText: bodyText};
    }'''
    data = page.evaluate(script)
    candidates = data.get('candidates', [])
    valid = [c for c in candidates if c.get('text_length', 0) > 0 and not looks_like_noise(c.get('text', ''))]
    best = max(valid, key=lambda x: x.get('text_length', 0)) if valid else {'selector': 'none', 'text': '', 'text_length': 0}
    return data.get('title', ''), candidates, best, data.get('pageText', '')


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
    raw_arts = json.loads(INPUT_PATH.read_text(encoding='utf-8')) if INPUT_PATH.exists() else []
    arts = list(raw_arts)
    raw_article_count = len(raw_arts)
    if MAX_ARTICLES > 0:
        arts = arts[:MAX_ARTICLES]
    article_count_after_pre_filter = len(arts)
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
    existing_url_skip_count = len(skipped)
    target_count = len(arts)
    Path('logs/nikkei_articles_skipped_existing.json').write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding='utf-8')

    res, fail = [], []
    retry_without_resource_block_success = False
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
                selector_logs = []
                try:
                    page.goto(a['url'], wait_until='domcontentloaded', timeout=GOTO_TIMEOUT)
                    page.wait_for_timeout(WAIT_AFTER)
                    page_title, selector_logs, best, page_text = select_text_with_candidates(page)
                    text = (best.get('text') or '').strip()
                    login_wall, paid_wall, access_denied = detect_walls(text + '\n' + page_text)
                    empty_body = len(text) == 0
                    too_short = len(text) < MIN_LEN
                    ex, r = should_exclude_by_body(a.get('title', ''), text)
                    if ex:
                        fail.append({'status': 'excluded', 'exclude_reason': r, 'url': a['url'], 'source_title': a.get('title', '')})
                        ok = True
                        break
                    if looks_like_noise(text) and too_short:
                        raise RuntimeError('noise_or_empty_body')
                    if too_short and not (len(text) >= 40 and not login_wall and not paid_wall and not access_denied):
                        raise RuntimeError('too_short')
                    status = 'success_short' if too_short else 'success'
                    if RETRY_WITHOUT_BLOCK and (not use_block) and t == attempts - 1:
                        print('retry_without_resource_block_success: true')
                        retry_without_resource_block_success = True
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
                        page_text = ''
                        login_wall, paid_wall, access_denied = False, False, False
                        try:
                            page_title, selector_logs, best, page_text = select_text_with_candidates(page)
                            selector_used = best.get('selector', '')
                            text = (best.get('text') or '').strip()
                            login_wall, paid_wall, access_denied = detect_walls(text + '\n' + page_text)
                        except Exception:
                            selector_logs = []
                        html_path, png_path, txt_path = save_failure_artifacts(page, i)
                        is_timeout = isinstance(e, PlaywrightTimeoutError)
                        fail.append({
                            'status': 'failed', 'title': a.get('title', ''), 'url': a['url'], 'source_title': a.get('title', ''), 'attempt_count': attempts,
                            'final_page_url': page.url if page else '', 'final_url': page.url if page else '', 'error_type': type(e).__name__, 'error_message': str(e),
                            'text_length': len(text), 'body_length': len(text), 'page_title': page_title, 'body_text_preview': text[:500],
                            'selector_used': selector_used, 'selector_candidates': selector_logs,
                            'selector_lengths': {c.get('selector', ''): int(c.get('text_length', 0)) for c in selector_logs if c.get('selector')},
                            'is_login_wall_detected': login_wall, 'is_paid_article_wall_detected': paid_wall,
                            'is_access_denied_detected': access_denied, 'is_timeout': is_timeout,
                            'is_empty_body': len(text) == 0, 'is_too_short': 0 < len(text) < MIN_LEN,
                            'block_heavy_resources': BLOCK_HEAVY, 'resource_block_enabled': use_block,
                            'retried_without_resource_block': RETRY_WITHOUT_BLOCK,
                            'final_attempt_without_resource_block': RETRY_WITHOUT_BLOCK and (not use_block) and t == attempts - 1,
                            'wait_strategy_used': {'wait_until': 'domcontentloaded', 'wait_after_ms': WAIT_AFTER, 'goto_timeout_ms': GOTO_TIMEOUT},
                            'screenshot_path': png_path, 'html_path': html_path, 'text_path': txt_path,
                            'reason': 'empty_body' if len(text) == 0 else ('too_short' if 0 < len(text) < MIN_LEN else type(e).__name__),
                            'status_code': None,
                        })
                finally:
                    c.close()
            time.sleep(SLEEP_SECONDS)
            if not ok:
                pass
        b.close()

    OUTPUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    FAILED_JSON.write_text(json.dumps(fail, ensure_ascii=False, indent=2), encoding='utf-8')
    only_failed = [x for x in fail if x.get('status') == 'failed']
    reason_counts = Counter((x.get('reason') or x.get('error_type') or 'unknown') for x in only_failed)
    fetch_success_count = len(res)
    fetch_failed_count = len(only_failed)
    login_wall_count = sum(1 for x in only_failed if x.get('is_login_wall_detected'))
    paid_wall_count = sum(1 for x in only_failed if x.get('is_paid_article_wall_detected'))
    access_denied_count = sum(1 for x in only_failed if x.get('is_access_denied_detected'))
    timeout_count = sum(1 for x in only_failed if x.get('is_timeout'))
    empty_body_count = sum(1 for x in only_failed if x.get('is_empty_body'))
    summary = {
        'raw_article_count': raw_article_count,
        'pre_excluded_count': max(raw_article_count - article_count_after_pre_filter, 0),
        'article_count_after_pre_filter': article_count_after_pre_filter,
        'article_count': article_count_after_pre_filter,
        'existing_url_skip_count': existing_url_skip_count,
        'target_count': target_count,
        'fetch_success_count': fetch_success_count,
        'fetch_failed_count': fetch_failed_count,
        'empty_body_count': empty_body_count,
        'login_wall_count': login_wall_count,
        'paid_wall_count': paid_wall_count,
        'access_denied_count': access_denied_count,
        'timeout_count': timeout_count,
        'too_short_count': sum(1 for x in only_failed if x.get('is_too_short')),
        'failure_reason_counts': dict(reason_counts),
        'failed_json_path': str(FAILED_JSON),
        'failed_artifacts_dir': str(FAILED_DIR),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('raw_article_count:', summary['raw_article_count'])
    print('pre_excluded_count:', summary['pre_excluded_count'])
    print('article_count_after_pre_filter:', summary['article_count_after_pre_filter'])
    print('article_count:', summary['article_count'])
    print('existing_url_skip_count:', summary['existing_url_skip_count'])
    print('target_count:', summary['target_count'])
    print('success_count:', summary['fetch_success_count'])
    print('failed_count:', summary['fetch_failed_count'])
    print('failure_reason_counts:', dict(reason_counts))
    print('login_wall_count:', login_wall_count)
    print('paid_wall_count:', paid_wall_count)
    print('access_denied_count:', access_denied_count)
    print('timeout_count:', timeout_count)
    print('too_short_count:', sum(1 for x in only_failed if x.get('is_too_short')))
    print('empty_body_count:', empty_body_count)
    print('failed_json_path:', summary['failed_json_path'])
    print('failed_artifacts_dir:', summary['failed_artifacts_dir'])


if __name__ == '__main__':
    main()
