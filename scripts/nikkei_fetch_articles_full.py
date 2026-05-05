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
INVENTORY_JSON = Path('logs/nikkei_issue_run_inventory.json')
DOM_CANDIDATES_JSONL = Path('logs/nikkei_article_dom_candidates.jsonl')

MAX_SUCCESS_ARTICLES = int(os.getenv('NIKKEI_MAX_SUCCESS_ARTICLES', os.getenv('NIKKEI_MAX_ARTICLES_TO_FETCH', '0')))
MAX_ARTICLE_ATTEMPTS = int(os.getenv('NIKKEI_MAX_ARTICLE_ATTEMPTS', '0'))
SLEEP_SECONDS = float(os.getenv('NIKKEI_FETCH_SLEEP_SECONDS', '1.0'))
MIN_LEN = int(os.getenv('NIKKEI_MIN_ARTICLE_TEXT_LENGTH', '120'))
RETRIES = int(os.getenv('NIKKEI_ARTICLE_EXTRACT_RETRIES', '3'))
GOTO_TIMEOUT = int(os.getenv('NIKKEI_ARTICLE_GOTO_TIMEOUT_MS', '25000'))
WAIT_AFTER = int(os.getenv('NIKKEI_ARTICLE_WAIT_AFTER_LOAD_MS', '800'))
BLOCK_HEAVY = os.getenv('NIKKEI_BLOCK_HEAVY_RESOURCES', 'true').lower() == 'true'
RETRY_WITHOUT_BLOCK = os.getenv('NIKKEI_RETRY_WITHOUT_RESOURCE_BLOCK_ON_FAILURE', 'true').lower() == 'true'
WAIT_FOR_CONTENT_MS = int(os.getenv('NIKKEI_WAIT_FOR_CONTENT_MS', '6000'))

SKIP_EXISTING = os.getenv('NIKKEI_SKIP_EXISTING_NOTION_URLS', 'true').lower() == 'true'
BACKFILL_EXISTING_EMPTY_BODY = os.getenv('NIKKEI_BACKFILL_EXISTING_EMPTY_BODY', 'true').lower() == 'true'
PAGE_SIZE = int(os.getenv('NIKKEI_EXISTING_URL_LOOKUP_PAGE_SIZE', '100'))
NOTION_TOKEN = os.getenv('NOTION_TOKEN', '').strip()
DB = (os.getenv('NIKKEI_ARTICLES_DB_ID', '') or os.getenv('NOTION_ARTICLE_DB_ID', '')).strip()


def extract_nikkei_ng_id(url: str) -> str:
    return (parse_qs(urlparse(url).query).get('ng') or [''])[0]


def normalize_nikkei_article_key(url: str) -> str:
    u = (url or '').strip()
    if not u:
        return ''
    ng = extract_nikkei_ng_id(u)
    if ng:
        return f'ng:{ng}'
    p = urlparse(u)
    path = (p.path or '/').rstrip('/') or '/'
    return f'{p.netloc.lower()}{path}'


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


def _extract_text_property(prop: dict) -> str:
    ptype = prop.get('type')
    if ptype == 'title':
        return ''.join(x.get('plain_text', '') for x in prop.get('title', []))
    if ptype == 'rich_text':
        return ''.join(x.get('plain_text', '') for x in prop.get('rich_text', []))
    if ptype == 'url':
        return prop.get('url') or ''
    if ptype == 'select':
        return (prop.get('select') or {}).get('name', '')
    if ptype == 'date':
        return (prop.get('date') or {}).get('start', '')
    if ptype == 'number':
        n = prop.get('number')
        return '' if n is None else str(n)
    return ''


URL_PROPERTY_CANDIDATES = ['URL', 'Url', 'url', 'Link', 'Article URL', 'Source URL']


def fetch_existing():
    if not SKIP_EXISTING:
        return set(), {}, False, {}, {'notion_existing_url_query_enabled': False, 'notion_existing_query_failed': False}
    if not NOTION_TOKEN or not DB:
        return set(), {}, True, {}, {
            'notion_existing_url_query_enabled': True,
            'notion_existing_query_failed': True,
            'notion_existing_query_error': 'missing_notion_token_or_database_id',
        }
    meta_resp = requests.get(f'https://api.notion.com/v1/databases/{DB}', headers=notion_headers(), timeout=60)
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    props = meta.get('properties', {})
    url_prop_name = next((name for name in URL_PROPERTY_CANDIDATES if name in props), '')
    if not url_prop_name:
        return set(), {}, True, props, {
            'notion_existing_url_query_enabled': True,
            'notion_existing_query_failed': True,
            'notion_existing_query_error': f'url_property_not_found candidates={URL_PROPERTY_CANDIDATES}',
        }
    cur = None
    keys = set()
    existing_map = {}
    samples = []
    while True:
        payload = {'page_size': PAGE_SIZE}
        if cur:
            payload['start_cursor'] = cur
        d = notion_req(f'https://api.notion.com/v1/databases/{DB}/query', payload)
        for it in d.get('results', []):
            item_props = it.get('properties', {})
            p = item_props.get(url_prop_name, {})
            u = ''
            if p.get('type') == 'url':
                u = p.get('url') or ''
            elif p.get('type') == 'rich_text':
                u = ''.join(x.get('plain_text', '') for x in p.get('rich_text', []))
            if u:
                keys.add(normalize_nikkei_article_key(u))
                keys.add(u)
                if len(samples) < 5:
                    samples.append(u)
                existing_map[u] = {
                    'page_id': it.get('id', ''),
                    'url': u,
                    'title': _extract_text_property(item_props.get('Name', {})) or _extract_text_property(item_props.get('Title', {})),
                    'issue_date': _extract_text_property(item_props.get('Issue Date', {})) or _extract_text_property(item_props.get('Published Date', {})),
                    'edition': _extract_text_property(item_props.get('Edition', {})),
                    'text': _extract_text_property(item_props.get('Body', {})) or _extract_text_property(item_props.get('Summary', {})) or _extract_text_property(item_props.get('Content', {})),
                    'importance_score': _extract_text_property(item_props.get('Importance Score', {})),
                    'source': 'notion_existing',
                }
        if not d.get('has_more'):
            break
        cur = d.get('next_cursor')
    return keys, existing_map, True, props, {
        'notion_existing_url_query_enabled': True,
        'notion_existing_url_count': len(existing_map),
        'notion_existing_url_sample': samples,
        'notion_existing_query_failed': False,
        'notion_existing_query_error': '',
        'notion_existing_url_property': url_prop_name,
    }


def has_body_text(existing: dict) -> bool:
    return bool(str(existing.get('text', '')).strip())


def classify_articles(articles, keys, existing_map, skip_existing, backfill_enabled):
    skipped = []
    targets = []
    existing_with_body = []
    existing_missing_body = []
    for a in articles:
        u = a['url']
        is_existing = u in keys or normalize_nikkei_article_key(u) in keys
        if not (skip_existing and is_existing):
            targets.append(a)
            continue
        ex = existing_map.get(u, {})
        if backfill_enabled and not has_body_text(ex):
            existing_missing_body.append(a)
            targets.append(a)
        else:
            existing_with_body.append(a)
            skipped.append(a)
    return targets, skipped, existing_with_body, existing_missing_body


def detect_walls(text: str):
    login_markers = ['ログイン', '会員登録', 'ログインしてください', '日経ID']
    paid_markers = ['有料会員', '続きは会員限定', 'この記事は会員限定', '購読']
    access_markers = ['Access Denied', 'Forbidden', '403', 'Bot', '不正なアクセス']
    login = any(x in text for x in login_markers)
    paid = any(x in text for x in paid_markers)
    access = any(x in text for x in access_markers)
    evidence = {
        'login_markers': [x for x in login_markers if x in text][:5],
        'paid_markers': [x for x in paid_markers if x in text][:5],
        'access_markers': [x for x in access_markers if x in text][:5],
    }
    return login, paid, access, evidence


def looks_like_noise(text: str) -> bool:
    if not text.strip():
        return True
    noise_markers = ['メニュー', 'サイトマップ', '利用規約', 'プライバシー', 'ページが見つかりません', 'エラー']
    if len(text) < MIN_LEN and sum(m in text for m in noise_markers) >= 2:
        return True
    if re.search(r'(ログイン|会員登録).{0,120}(ログイン|会員登録)', text):
        return True
    return False


NAV_KEYWORDS = [
    '速報', 'アクセスランキング', 'トピック一覧', '人事', 'おくやみ', 'プレスリリース',
    'メディア一覧', 'NIKKEI Digital Governance', 'NIKKEI Financial', 'ビューアーで読む',
    'メニュー', 'カテゴリ', '一覧',
]


def clean_article_text(text: str) -> str:
    txt = (text or '').replace('\xa0', ' ')
    txt = re.sub(r'\r\n?', '\n', txt)
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt.strip()


def article_body_quality_metrics(text: str) -> dict:
    cleaned = clean_article_text(text)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    counts = Counter(lines)
    duplicate_line_ratio = 0.0
    if lines:
        duplicate_line_ratio = sum(v for v in counts.values() if v > 1) / len(lines)
    sentence_count_ja = len(re.findall(r'。', cleaned))
    paragraph_count = sum(1 for ln in lines if len(ln) >= 20)
    nav_keyword_hits = sum(cleaned.count(k) for k in NAV_KEYWORDS)
    link_like_line_count = sum(1 for ln in lines if re.search(r'https?://|www\.|▶|＞|→', ln))
    link_text_ratio = (link_like_line_count / len(lines)) if lines else 0.0
    return {
        'text_length': len(cleaned),
        'paragraph_count': paragraph_count,
        'sentence_count_ja': sentence_count_ja,
        'nav_keyword_hits': nav_keyword_hits,
        'duplicate_line_ratio': duplicate_line_ratio,
        'link_text_ratio': round(link_text_ratio, 3),
    }


def is_probably_navigation_text(text: str) -> bool:
    m = article_body_quality_metrics(text)
    has_article_shape = (
        m['text_length'] >= MIN_LEN
        and m['sentence_count_ja'] >= 3
        and m['paragraph_count'] >= 3
        and m['link_text_ratio'] <= 0.35
    )
    if has_article_shape:
        return False
    return (
        m['nav_keyword_hits'] >= 3
        or m['sentence_count_ja'] <= 1
        or m['link_text_ratio'] >= 0.55
        or m['duplicate_line_ratio'] >= 0.35
        or (m['paragraph_count'] <= 2 and m['text_length'] < 700)
    )


def is_paper_index_title(title: str) -> bool:
    t = (title or '').strip()
    return bool(re.search(r'朝刊・夕刊(\s*\d+月\d+日.*付)?', t))




def normalize_title_for_match(title: str) -> str:
    t = (title or '').strip()
    t = re.sub(r'\s*-\s*日本経済新聞\s*$', '', t)
    t = t.replace('　', ' ')
    t = re.sub(r'\s+', '', t)
    t = re.sub(r"[「」『』【】\[\]（）()〈〉《》“”\"'・…‥,，、。.!！?？:：;；/／\\|-]", '', t)
    return t


def title_tokens_for_match(title: str) -> list[str]:
    norm = normalize_title_for_match(title)
    return [tok for tok in re.findall(r'[\w぀-ヿ㐀-鿿]+', norm) if len(tok) >= 2]


def article_title_match_result(source_title: str, page_title: str) -> dict:
    src = normalize_title_for_match(source_title)
    page = normalize_title_for_match(page_title)
    src_tokens = title_tokens_for_match(source_title)
    matched_tokens = [tok for tok in src_tokens if tok in page]
    partial = bool(src and page and (src in page or page in src))
    token_match_count = len(matched_tokens)
    token_match_ratio = (token_match_count / len(src_tokens)) if src_tokens else 0.0
    matched = partial or token_match_count >= 2 or token_match_ratio >= 0.4
    return {
        'matched': matched,
        'partial_match': partial,
        'source_norm': src,
        'page_norm': page,
        'source_token_count': len(src_tokens),
        'matched_tokens': matched_tokens,
        'token_match_count': token_match_count,
        'token_match_ratio': round(token_match_ratio, 3),
    }


def text_headline_alignment(title: str, text: str) -> bool:
    if not title or not text:
        return False
    t = normalize_title_for_match(title)
    head = normalize_title_for_match((text or '')[:280])
    if not t or not head:
        return False
    return t[:20] in head or head[:20] in t or sum(1 for tok in title_tokens_for_match(title) if tok in head) >= 2


def title_body_overlap(title: str, text: str) -> bool:
    if not title or not text:
        return False
    body = normalize_title_for_match(text)
    if not body:
        return False
    tokens = title_tokens_for_match(title)
    if not tokens:
        return False
    matched = [tok for tok in tokens if tok in body]
    ratio = len(matched) / len(tokens)
    return len(matched) >= 2 or ratio >= 0.4


def validate_article_body(text: str, page_title: str = '', source_title: str = '', h1_text: str = '', article_url: str = '') -> tuple[bool, str]:
    cleaned = clean_article_text(text)
    if not cleaned:
        return False, 'empty_text'

    metrics = article_body_quality_metrics(cleaned)
    title_match = article_title_match_result(source_title, page_title)
    headline_align = text_headline_alignment(source_title or page_title, cleaned)
    strong_body = (
        metrics['text_length'] >= MIN_LEN
        and metrics['sentence_count_ja'] >= 2
        and metrics['paragraph_count'] >= 2
    )
    title_overlap = title_body_overlap(source_title or page_title, cleaned)
    likely_article = strong_body and (title_match['matched'] or headline_align or title_overlap)
    paper_like_title = is_paper_index_title(page_title)
    paper_like_h1 = is_paper_index_title(h1_text)
    url_is_article = '/paper/article/' in (article_url or '')

    if paper_like_title and not url_is_article and not likely_article:
        return False, 'paper_index_page_title'

    nav_like = is_probably_navigation_text(cleaned)
    if nav_like and not likely_article:
        return False, 'navigation_like_text'

    if len(cleaned) < MIN_LEN:
        return False, 'too_short'

    if source_title and page_title and not title_match['matched']:
        if metrics['sentence_count_ja'] < 3:
            return False, 'title_mismatch_with_low_quality'

    # h1_textは診断用途のみ。単独で失敗にしない。
    _ = paper_like_h1
    return True, ''


def extract_from_embedded_json(page):
    script = r'''() => {
      const out = {articleBody: '', headline: '', datePublished: '', source: ''};
      const pick = (obj) => {
        if (!obj || typeof obj !== 'object') return;
        const body = obj.articleBody || obj.description || obj.text || '';
        if (!out.articleBody && typeof body === 'string' && body.trim().length > 0) out.articleBody = body.trim();
        const hl = obj.headline || obj.name || obj.title || '';
        if (!out.headline && typeof hl === 'string') out.headline = hl.trim();
        const dt = obj.datePublished || obj.dateCreated || '';
        if (!out.datePublished && typeof dt === 'string') out.datePublished = dt.trim();
      };
      for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const parsed = JSON.parse(node.textContent || '{}');
          const arr = Array.isArray(parsed) ? parsed : [parsed];
          for (const it of arr) pick(it);
          if (out.articleBody) { out.source = 'json_ld'; return out; }
        } catch (_) {}
      }
      const nextNode = document.querySelector('script#__NEXT_DATA__');
      if (nextNode?.textContent) {
        try {
          const parsed = JSON.parse(nextNode.textContent);
          const text = JSON.stringify(parsed);
          const m = text.match(/"articleBody":"([^"]{80,})"/);
          if (m) out.articleBody = m[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
          out.source = 'next_data';
          return out;
        } catch (_) {}
      }
      return out;
    }'''
    return page.evaluate(script)


def select_text_with_candidates(page):
    script = r'''() => {
      const removalSelectors = [
        'header','footer','nav','aside','script','style','noscript',
        '[role="navigation"]','.breadcrumb','.breadcrumbs','.related','.recommend',
        '[class*="ranking"]','[class*="share"]','[class*="sns"]','[class*="advert"]',
        '[class*="ad-"]','[class*="paid"]','[class*="subscription"]'
      ];
      for (const sel of removalSelectors) {
        document.querySelectorAll(sel).forEach(n => n.remove());
      }
      const sanitize = (node) => {
        if (!node) return null;
        const c = node.cloneNode(true);
        for (const sel of removalSelectors) c.querySelectorAll(sel).forEach(n => n.remove());
        return c;
      };
      const paragraphFallback = () => {
        const navWords = ['メニュー', 'ランキング', '関連記事', '購読', '会員登録', 'ログイン', 'シェア', '一覧'];
        const seen = new Set();
        const out = [];
        for (const p of document.querySelectorAll('p')) {
          const text = (p.innerText || '').replace(/\s+/g, ' ').trim();
          if (!text || text.length < 20) continue;
          if (seen.has(text)) continue;
          seen.add(text);
          const anchorTextLen = Array.from(p.querySelectorAll('a')).map(a => (a.innerText || '').trim().length).reduce((a, b) => a + b, 0);
          const ratio = text.length ? (anchorTextLen / text.length) : 0;
          if (ratio >= 0.6) continue;
          if (navWords.filter(w => text.includes(w)).length >= 2 && text.length < 120) continue;
          out.push(text);
        }
        return out;
      };
      const h1Text = document.querySelector('h1')?.innerText?.trim() || '';
      const title = document.title || h1Text || '';
      const cands = [
        // Nikkei paper article body candidates. Keep these before broad wrappers.
        ['div.cmn-section.cmn-indent','div.cmn-section.cmn-indent'],
        ['section.cmn-section.cmn-indent','section.cmn-section.cmn-indent'],
        ['.cmn-section.cmn-indent','.cmn-section.cmn-indent'],
        ['.cmn-section','.cmn-section'],

        // Structured article body candidates.
        ['[data-track-article-body]','[data-track-article-body]'],
        ['[itemprop="articleBody"]','[itemprop="articleBody"]'],
        ['[class*="article-body"]','[class*="article-body"]'],
        ['[class*="articleBody"]','[class*="articleBody"]'],

        // Broad fallbacks. Use only if body-specific selectors fail.
        ['article','article'],
        ['main','main'],
        ['[class*="article"]','[class*="article"]'],
        ['[class*="content"]','[class*="content"]'],
      ];
      const paragraphTexts = paragraphFallback();
      if (paragraphTexts.length > 0) {
        cands.push(['paragraph_fallback', '__paragraph_fallback__']);
      }
      const out = [];
      for (const [name, sel] of cands) {
        if (sel === '__paragraph_fallback__') {
          const txt = paragraphTexts.join('\n\n').trim();
          out.push({selector: name, text: txt, text_length: txt.length, preview: txt.slice(0, 240), paragraph_count: paragraphTexts.length, link_text_ratio: 0});
          continue;
        }
        const nodes = Array.from(document.querySelectorAll(sel)).map(sanitize).filter(Boolean);
        const txt = nodes.map(x => x.innerText || '').join('\n').trim();
        let linkLen = 0;
        for (const n of nodes) {
          n.querySelectorAll('a').forEach(a => linkLen += (a.innerText || '').trim().length);
        }
        const linkRatio = txt.length ? (linkLen / txt.length) : 0;
        out.push({selector: name, text: txt, text_length: txt.length, preview: txt.slice(0, 240), paragraph_count: txt ? txt.split('\n').filter(x => x.trim().length >= 20).length : 0, link_text_ratio: Number(linkRatio.toFixed(3))});
      }
      let bodyText = (sanitize(document.body)?.innerText || '').trim();
      bodyText = bodyText.replace(/メニュー[\s\S]{0,400}?ログイン/g, '');
      out.push({selector: 'document.body.innerText fallback', text: bodyText, text_length: bodyText.length, preview: bodyText.slice(0, 240), paragraph_count: bodyText ? bodyText.split('\n').filter(x => x.trim().length >= 20).length : 0, link_text_ratio: 0});
      const snippets = {};
      for (const sel of ['article','main','section','body']) {
        const node = document.querySelector(sel);
        snippets[sel] = (sanitize(node)?.outerHTML || '').slice(0, 3000);
      }
      return {title, h1Text, candidates: out, pageText: bodyText, snippets, readyState: document.readyState, locationHref: location.href, paragraphCount: paragraphTexts.length};
    }'''
    data = page.evaluate(script)
    candidates = data.get('candidates', [])
    # Prefer Nikkei's known paper article body container first.
    # Historical good saves used div.cmn-section.cmn-indent as the clean body selector.
    # Fall back to paragraph_fallback only when the structured body container is absent.
    preferred = [
        'div.cmn-section.cmn-indent',
        '.cmn-section',
        '[data-track-article-body]',
        '[itemprop="articleBody"]',
        '[class*="article-body"]',
        '[class*="articleBody"]',
        'paragraph_fallback',
        'article',
        'main',
        '[class*="article"]',
        '[class*="content"]',
    ]
    best = {'selector': 'none', 'text': '', 'text_length': 0}
    for c in candidates:
        print(
            "dom_candidate:",
            c.get('selector'),
            "text_length=",
            c.get('text_length', 0),
            "paragraph_count=",
            c.get('paragraph_count', 0),
            "link_text_ratio=",
            c.get('link_text_ratio', 0),
            "preview=",
            (c.get('preview') or '').replace("\n", " ")[:160],
        )
    cand_map = {c.get('selector'): c for c in candidates}
    for key in preferred:
        c = cand_map.get(key)
        if c and c.get('text_length', 0) > 0 and c.get('link_text_ratio', 0) <= 0.6 and not looks_like_noise(c.get('text', '')):
            best = c
            break
    return data, data.get('title', ''), candidates, best, data.get('pageText', '')


def wait_for_article_content(page):
    deadline = time.time() + (WAIT_FOR_CONTENT_MS / 1000)
    while time.time() < deadline:
        try:
            l1 = page.locator('article').first.inner_text(timeout=700).strip()
        except Exception:
            l1 = ''
        try:
            l2 = page.locator('main').first.inner_text(timeout=700).strip()
        except Exception:
            l2 = ''
        try:
            l3 = page.inner_text('body', timeout=700).strip()
        except Exception:
            l3 = ''
        if max(len(l1), len(l2), len(l3)) >= max(120, MIN_LEN // 2):
            return True
        page.wait_for_timeout(400)
    return False


def classify_empty_body_reason(text, page_text, login_like, selector_logs, used_block):
    if login_like:
        return 'empty_body_login_like_text'
    if not selector_logs:
        return 'empty_body_dom_missing'
    if len((page_text or '').strip()) > 400 and len((text or '').strip()) == 0:
        return 'empty_body_page_text_present_but_selector_failed'
    if used_block and len((page_text or '').strip()) < 80:
        return 'empty_body_resource_block_suspected'
    if len((text or '').strip()) == 0:
        return 'empty_body_dom_missing'
    return 'empty_body_unknown'


def should_stop_attempting(attempted_count: int, max_article_attempts: int) -> bool:
    return max_article_attempts > 0 and attempted_count >= max_article_attempts


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
    debug_json_path = f'{base}.debug.json'
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
    return html_path, png_path, txt_path, debug_json_path


def split_text_blocks(text: str, limit: int = 1800):
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    if not lines:
        return []
    chunks = []
    cur = ''
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit and cur:
            chunks.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def main():
    raw_arts = json.loads(INPUT_PATH.read_text(encoding='utf-8')) if INPUT_PATH.exists() else []
    arts = list(raw_arts)
    raw_article_count = len(raw_arts)
    max_success_articles = MAX_SUCCESS_ARTICLES
    max_article_attempts = MAX_ARTICLE_ATTEMPTS
    if max_success_articles > 0 and max_article_attempts == 0:
        max_article_attempts = max(20, max_success_articles * 5)
    notion_diag = {}
    try:
        keys, existing_map, enabled, props, notion_diag = fetch_existing()
    except Exception as e:
        keys, existing_map, enabled, props = set(), {}, True, {}
        notion_diag = {
            'notion_existing_url_query_enabled': SKIP_EXISTING,
            'notion_existing_query_failed': True,
            'notion_existing_query_error': f'{type(e).__name__}: {e}',
        }
    print('notion_existing_url_query_enabled:', notion_diag.get('notion_existing_url_query_enabled', False))
    print('notion_existing_url_count:', notion_diag.get('notion_existing_url_count', 0))
    print('notion_existing_url_sample:', notion_diag.get('notion_existing_url_sample', []))
    print('notion_existing_query_failed:', notion_diag.get('notion_existing_query_failed', False))
    print('notion_existing_query_error:', notion_diag.get('notion_existing_query_error', ''))
    print('notion_article_db_id_present:', bool(DB))
    if max_article_attempts == 0:
        print('max_article_attempts: 0 (unbounded by setting; bounded by target_count)')
    if SKIP_EXISTING and notion_diag.get('notion_existing_query_failed'):
        print('ERROR: skip existing enabled but Notion existing URL query failed:', notion_diag.get('notion_existing_query_error', 'unknown'))
        summary = {'existing_url_skip_count': 0, 'target_count': len(arts), **notion_diag, 'notion_article_db_id_present': bool(DB)}
        SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        return 1
    if SKIP_EXISTING and not notion_diag.get('notion_existing_query_failed') and notion_diag.get('notion_existing_url_count', 0) == 0:
        print('WARN: skip existing enabled but no existing URLs loaded from Notion')
    arts, skipped, existing_with_body, existing_missing_body = classify_articles(
        arts, keys, existing_map, SKIP_EXISTING, BACKFILL_EXISTING_EMPTY_BODY
    )
    existing_url_skip_count = len(skipped)
    pre_excluded_count = len(raw_arts) - len(arts)
    target_count = len(arts)
    compare_logs = []
    for idx, a in enumerate(raw_arts[:5]):
        raw = a.get('url', '')
        norm = normalize_nikkei_article_key(raw)
        match = raw in keys or norm in keys
        matched_norm = ''
        if match:
            matched_norm = norm if norm in keys else raw
        compare_logs.append({
            'issue_article_url_raw': raw,
            'normalized_issue_url': norm,
            'normalized_notion_existing_url': matched_norm,
            'match': match,
        })
    print('url_normalization_match_samples:', json.dumps(compare_logs, ensure_ascii=False))
    Path('logs/nikkei_articles_skipped_existing.json').write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding='utf-8')
    inventory = []
    for a in skipped + existing_missing_body:
        ex = existing_map.get(a['url'], {})
        inventory.append({'url': a['url'], 'title': a.get('title', ''), 'status': 'existing_in_notion', 'page_id': ex.get('page_id', ''), 'has_existing_body': bool(ex.get('text')), 'source': 'notion_existing', 'notion_existing': ex})

    res, fail = [], []
    dom_candidate_logs = []
    retry_without_resource_block_success = False
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        attempted_count = 0
        for i, a in enumerate(arts, 1):
            if should_stop_attempting(attempted_count, max_article_attempts):
                break
            if max_success_articles > 0 and len(res) >= max_success_articles:
                break
            attempted_count += 1
            attempts = RETRIES + (1 if RETRY_WITHOUT_BLOCK else 0)
            ok = False
            for t in range(attempts):
                use_block = BLOCK_HEAVY and not (RETRY_WITHOUT_BLOCK and t == attempts - 1)
                context = None
                context = b.new_context(storage_state=str(STORAGE_PATH), locale='ja-JP', timezone_id='Asia/Tokyo')
                if use_block:
                    def _route_handler(route, req):
                        try:
                            if req.resource_type in {'image', 'media', 'font'}:
                                route.abort()
                            else:
                                route.continue_()
                        except Exception as route_err:
                            print(f'route_handler_warning: {type(route_err).__name__}: {route_err}')
                    context.route('**/*', _route_handler)
                page = context.new_page()
                extract_data = {'snippets': {}, 'readyState': '', 'locationHref': ''}
                selector_logs = []
                try:
                    page.goto(a['url'], wait_until='domcontentloaded', timeout=GOTO_TIMEOUT)
                    page.wait_for_timeout(WAIT_AFTER)
                    wait_for_article_content(page)
                    extract_data, page_title, selector_logs, best, page_text = select_text_with_candidates(page)
                    h1_text = extract_data.get('h1Text', '')
                    embedded = extract_from_embedded_json(page)
                    text = clean_article_text(best.get('text') or '')
                    extractor_name = best.get('selector', '')
                    if embedded.get('articleBody'):
                        text = embedded.get('articleBody', '').strip()
                        extractor_name = f"embedded_json:{embedded.get('source','unknown')}"
                    valid_body, rejection_reason = validate_article_body(text, page_title=page_title, source_title=a.get('title', ''), h1_text=h1_text, article_url=a.get('url', ''))
                    if extractor_name in {'[class*="content"]', 'document.body.innerText fallback'}:
                        valid_body = False
                        rejection_reason = 'disallowed_selector'
                    candidate_diag = []
                    for candidate in selector_logs:
                        metrics = article_body_quality_metrics(candidate.get('text', ''))
                        candidate_diag.append({
                            'selector': candidate.get('selector', ''),
                            **metrics,
                            'preview': (candidate.get('preview', '') or '')[:240],
                        })
                    login_wall, paid_wall, access_denied, wall_evidence = detect_walls(text + '\n' + page_text)
                    empty_body = len(text) == 0
                    too_short = len(text) < MIN_LEN
                    ex, r = should_exclude_by_body(a.get('title', ''), text)
                    if ex:
                        fail.append({'status': 'excluded', 'exclude_reason': r, 'url': a['url'], 'source_title': a.get('title', '')})
                        ok = True
                        break
                    if not valid_body:
                        raise RuntimeError(f'invalid_article_body:{rejection_reason}')
                    if looks_like_noise(text) and too_short:
                        raise RuntimeError('noise_or_empty_body')
                    if too_short and not (len(text) >= 40 and not login_wall and not paid_wall and not access_denied):
                        raise RuntimeError('too_short')
                    status = 'success_short' if too_short else 'success'
                    if RETRY_WITHOUT_BLOCK and (not use_block) and t == attempts - 1:
                        print('retry_without_resource_block_success: true')
                        retry_without_resource_block_success = True
                    record = {
                        'status': status, 'source_title': a.get('title', ''), 'url': a['url'], 'issue_url': a.get('issue_url', ''),
                        'issue_date': a.get('issue_date', ''), 'edition': a.get('edition', ''), 'page_title': page_title,
                        'text_length': len(text), 'text': text, 'selector_used': extractor_name, 'selector_candidates': selector_logs,
                    }
                    print(f"[article] index={i} url={a['url']} final_url={page.url} title={page_title} extracted_text_length={len(text)} selected_extractor_name={extractor_name} failure_reason=")
                    dom_candidate_logs.append({
                        'url': a['url'], 'final_url': page.url, 'source_title': a.get('title', ''), 'page_title': page_title, 'h1_text': h1_text,
                        'selector_used': extractor_name, 'selected_text_length': len(text), 'selected_preview': text[:240], 'extraction_status': 'success',
                        'rejection_reason': '', 'candidates': candidate_diag,
                    })
                    ex = existing_map.get(a['url'], {})
                    if ex.get('page_id'):
                        record['source'] = 'backfill_existing'
                        record['page_id'] = ex.get('page_id')
                        inventory.append({'url': a['url'], 'title': a.get('title', ''), 'status': 'backfilled_existing', 'page_id': ex.get('page_id', ''), 'has_existing_body': True, 'source': 'backfill_existing'})
                    else:
                        inventory.append({'url': a['url'], 'title': a.get('title', ''), 'status': 'fetched_new', 'page_id': '', 'has_existing_body': False, 'source': 'fetch_new'})
                    res.append(record)
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
                            extract_data, page_title, selector_logs, best, page_text = select_text_with_candidates(page)
                            selector_used = best.get('selector', '')
                            text = (best.get('text') or '').strip()
                            login_wall, paid_wall, access_denied, wall_evidence = detect_walls(text + '\n' + page_text)
                        except Exception:
                            selector_logs = []
                            wall_evidence = {}
                        html_path, png_path, txt_path, debug_json_path = save_failure_artifacts(page, i)
                        failure_reason = 'empty_body' if len(text) == 0 else ('too_short' if 0 < len(text) < MIN_LEN else type(e).__name__)
                        if 'invalid_article_body:' in str(e):
                            failure_reason = str(e).split(':', 1)[1]
                        h1_text = (extract_data or {}).get('h1Text', '')
                        candidate_diag = []
                        for candidate in selector_logs:
                            metrics = article_body_quality_metrics(candidate.get('text', ''))
                            candidate_diag.append({'selector': candidate.get('selector', ''), **metrics, 'preview': (candidate.get('preview', '') or '')[:240]})
                        if failure_reason == 'empty_body':
                            failure_reason = classify_empty_body_reason(text, page_text, login_wall, selector_logs, use_block)
                        Path(debug_json_path).write_text(json.dumps({
                            'final_url': page.url if page else '',
                            'page_title': page_title,
                            'body_inner_text_head_1000': (page_text or '')[:1000],
                            'html_snippets': (extract_data or {}).get('snippets', {}),
                            'document_ready_state': (extract_data or {}).get('readyState', ''),
                            'location_href': (extract_data or {}).get('locationHref', ''),
                            'selector_lengths': {candidate.get('selector', ''): int(candidate.get('text_length', 0)) for candidate in selector_logs if candidate.get('selector')},
                        }, ensure_ascii=False, indent=2), encoding='utf-8')
                        is_timeout = isinstance(e, PlaywrightTimeoutError)
                        ex = existing_map.get(a['url'], {})
                        fail.append({
                            'status': 'failed', 'title': a.get('title', ''), 'url': a['url'], 'source_title': a.get('title', ''), 'attempt_count': attempts,
                            'final_page_url': page.url if page else '', 'final_url': page.url if page else '', 'error_type': type(e).__name__, 'error_message': str(e),
                            'text_length': len(text), 'body_length': len(text), 'page_title': page_title, 'body_text_preview': text[:500],
                            'selector_used': selector_used, 'selector_candidates': selector_logs,
                            'selector_lengths': {candidate.get('selector', ''): int(candidate.get('text_length', 0)) for candidate in selector_logs if candidate.get('selector')},
                            'is_login_wall_detected': login_wall, 'is_paid_article_wall_detected': paid_wall,
                            'is_access_denied_detected': access_denied, 'is_timeout': is_timeout,
                            'is_empty_body': len(text) == 0, 'is_too_short': 0 < len(text) < MIN_LEN,
                            'block_heavy_resources': BLOCK_HEAVY, 'resource_block_enabled': use_block,
                            'retried_without_resource_block': RETRY_WITHOUT_BLOCK,
                            'final_attempt_without_resource_block': RETRY_WITHOUT_BLOCK and (not use_block) and t == attempts - 1,
                            'retry_without_resource_block_success': retry_without_resource_block_success,
                            'wait_strategy_used': {'wait_until': 'domcontentloaded', 'wait_after_ms': WAIT_AFTER, 'goto_timeout_ms': GOTO_TIMEOUT},
                            'screenshot_path': png_path, 'html_path': html_path, 'text_path': txt_path, 'artifact_html_path': html_path, 'artifact_screenshot_path': png_path,
                            'page_id': ex.get('page_id', ''), 'existing_page': bool(ex.get('page_id')),
                            'reason': failure_reason,
                            'failure_reason': failure_reason,
                            'selected_extractor_name': selector_used,
                            'extracted_text_length': len(text),
                            'extracted_text_head_1000': text[:1000],
                            'paragraph_count': article_body_quality_metrics(text).get('paragraph_count', 0),
                            'link_text_ratio': article_body_quality_metrics(text).get('link_text_ratio', 0),
                            'paid_wall_detected': paid_wall,
                            'login_wall_detected': login_wall,
                            'wall_detection_evidence': wall_evidence,
                            'h1_text': h1_text,
                            'validation_metrics': article_body_quality_metrics(text),
                            'article_title_match_result': article_title_match_result(a.get('title', ''), page_title),
                            'status_code': None,
                            'debug_json_path': debug_json_path,
                        })
                        dom_candidate_logs.append({
                            'url': a['url'], 'final_url': page.url if page else '', 'source_title': a.get('title', ''), 'page_title': page_title,
                            'h1_text': h1_text, 'selector_used': selector_used, 'selected_text_length': len(text), 'selected_preview': text[:240],
                            'extraction_status': 'failed', 'rejection_reason': failure_reason, 'candidates': candidate_diag,
                        })
                        print(f"[article] index={i} url={a['url']} final_url={page.url if page else ''} title={page_title} extracted_text_length={len(text)} selected_extractor_name={selector_used} failure_reason={failure_reason}")
                        reason = 'failed_timeout' if is_timeout else ('failed_access_denied' if access_denied else ('failed_empty_body' if len(text) == 0 else 'failed_other'))
                        inventory.append({'url': a['url'], 'title': a.get('title', ''), 'status': reason, 'page_id': '', 'has_existing_body': False, 'source': 'fetch_failed', 'final_url': page.url if page else '', 'artifact_path': {'html': html_path, 'screenshot': png_path, 'text': txt_path}})
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception as close_err:
                            print(f"context_close_warning: {type(close_err).__name__}: {close_err}")
            time.sleep(SLEEP_SECONDS)
            if not ok:
                pass
        b.close()

    OUTPUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    DOM_CANDIDATES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    DOM_CANDIDATES_JSONL.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in dom_candidate_logs) + ('\n' if dom_candidate_logs else ''), encoding='utf-8')
    FAILED_JSON.write_text(json.dumps(fail, ensure_ascii=False, indent=2), encoding='utf-8')
    INVENTORY_JSON.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    only_failed = [x for x in fail if x.get('status') == 'failed']
    reason_counts = Counter((x.get('reason') or x.get('failure_reason') or x.get('error_type') or 'unknown') for x in only_failed)
    fetch_success_count = len(res)
    fetch_failed_count = len(only_failed)
    remaining_unattempted_count = max(target_count - attempted_count, 0)
    login_wall_count = sum(1 for x in only_failed if x.get('is_login_wall_detected'))
    paid_wall_count = sum(1 for x in only_failed if x.get('is_paid_article_wall_detected'))
    access_denied_count = sum(1 for x in only_failed if x.get('is_access_denied_detected'))
    timeout_count = sum(1 for x in only_failed if x.get('is_timeout'))
    empty_body_count = sum(1 for x in only_failed if x.get('is_empty_body'))
    backfill_success_count = sum(1 for x in res if x.get('source') == 'backfill_existing')
    backfill_failed_count = sum(1 for x in only_failed if x.get('existing_page'))
    summary = {
        'raw_article_count': raw_article_count,
        'pre_excluded_count': pre_excluded_count,
        'article_count_after_pre_filter': len(arts),
        'article_count': len(arts),
        'existing_url_skip_count': existing_url_skip_count,
        'target_count': target_count,
        'max_success_articles': max_success_articles,
        'max_article_attempts': max_article_attempts,
        'attempted_count': attempted_count,
        'remaining_unattempted_count': remaining_unattempted_count,
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
        'issue_inventory_count': len(inventory),
        'inventory_existing_in_notion_count': sum(1 for x in inventory if x.get('status') == 'existing_in_notion'),
        'inventory_fetched_new_count': sum(1 for x in inventory if x.get('status') == 'fetched_new'),
        'inventory_failed_count': sum(1 for x in inventory if str(x.get('status', '')).startswith('failed_')),
        'existing_with_body_count': len(existing_with_body),
        'existing_missing_body_count': len(existing_missing_body),
        'backfill_existing_empty_body_enabled': BACKFILL_EXISTING_EMPTY_BODY,
        'backfill_target_count': len(existing_missing_body),
        'backfill_success_count': backfill_success_count,
        'backfill_failed_count': backfill_failed_count,
        'backfill_updated_existing_page_count': backfill_success_count,
        'retry_without_resource_block_success': retry_without_resource_block_success,
        'notion_article_db_id_present': bool(DB),
        **notion_diag,
        'url_normalization_match_samples': compare_logs,
    }
    if target_count > 0 and fetch_success_count == 0 and empty_body_count == fetch_failed_count:
        summary['systemic_empty_body_failure'] = True
        systemic_reason = 'unknown'
        if login_wall_count == fetch_failed_count and fetch_failed_count > 0:
            systemic_reason = 'login_state_invalid'
        elif access_denied_count == fetch_failed_count and fetch_failed_count > 0:
            systemic_reason = 'resource_block_side_effect'
        elif paid_wall_count == fetch_failed_count and fetch_failed_count > 0:
            systemic_reason = 'selector_mismatch'
        else:
            systemic_reason = 'article_dom_changed'
        summary['systemic_failure_reason'] = systemic_reason
    else:
        summary['systemic_empty_body_failure'] = False
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('raw_article_count:', summary['raw_article_count'])
    print('pre_excluded_count:', summary['pre_excluded_count'])
    print('article_count_after_pre_filter:', summary['article_count_after_pre_filter'])
    print('article_count:', summary['article_count'])
    print('existing_url_skip_count:', summary['existing_url_skip_count'])
    print('max_success_articles:', summary['max_success_articles'])
    print('max_article_attempts:', summary['max_article_attempts'])
    print('attempted_count:', summary['attempted_count'])
    print('target_count:', summary['target_count'])
    print('remaining_unattempted_count:', summary['remaining_unattempted_count'])
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
