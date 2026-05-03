import json
import os
import re
from pathlib import Path
from statistics import mean
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

INPUT_JSON = Path('logs/nikkei_articles_full.json')
OUTPUT_JSON = Path('logs/nikkei_articles_scored.json')
NOTION_VERSION = '2022-06-28'
NOTION_TOKEN = os.getenv('NOTION_TOKEN', '').strip()
ENABLE_SCORING = os.getenv('NIKKEI_ENABLE_SCORING', 'true').lower() == 'true'
ALLOW_DEFAULT_FALLBACK = os.getenv('NIKKEI_ALLOW_DEFAULT_RULES_DB_FALLBACK', 'false').lower() == 'true'
DEFAULT_RULES_DB_ID = '2eddec27c9aa80818f6aceda3258fef0'
RAW_RULES_DB_ID = os.getenv('NOTION_RULES_DB_ID', '').strip()
RULES_DB_ID = RAW_RULES_DB_ID or (DEFAULT_RULES_DB_ID if ALLOW_DEFAULT_FALLBACK else '')
RULE_TYPES = {x.strip().lower() for x in os.getenv('NIKKEI_RULES_FILTER_RULE_TYPES', 'country,sector,importance').split(',') if x.strip()}
MIN_REPORT_SCORE = float(os.getenv('NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT', '5'))

LOW_VALUE_WORDS = ['おくやみ', '訃報', '叙勲', '将棋', '囲碁', '競馬', '連載小説', '文化', 'スポーツ']

def split_keywords(raw: str) -> list[str]:
    if not raw:
        return []
    text = re.sub(r"\s+OR\s+", "\n", str(raw).replace('\r\n', '\n'), flags=re.IGNORECASE)
    text = re.sub(r"[\n,、;|　]+", "\n", text)
    return [x.strip() for x in text.split('\n') if x.strip()]

def notion_headers():
    if not NOTION_TOKEN:
        raise RuntimeError('NOTION_TOKEN が未設定です')
    return {'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': NOTION_VERSION, 'Content-Type': 'application/json'}

def parse_rich_text(prop: dict[str, Any]) -> str:
    t = prop.get('type')
    if t in {'title', 'rich_text'}:
        return ''.join(x.get('plain_text', '') for x in prop.get(t, []))
    if t == 'select':
        return (prop.get('select') or {}).get('name', '')
    if t == 'multi_select':
        return ','.join((x or {}).get('name', '') for x in prop.get('multi_select', []))
    if t == 'number':
        n = prop.get('number')
        return '' if n is None else str(n)
    if t == 'checkbox':
        return 'true' if prop.get('checkbox') else 'false'
    return ''

def load_rules():
    if ENABLE_SCORING and not RULES_DB_ID:
        raise RuntimeError('NOTION_RULES_DB_ID is required when NIKKEI_ENABLE_SCORING=true')
    rules, cursor = [], None
    while True:
        payload = {'page_size': 100}
        if cursor:
            payload['start_cursor'] = cursor
        r = requests.post(f'https://api.notion.com/v1/databases/{RULES_DB_ID}/query', headers=notion_headers(), json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        for item in data.get('results', []):
            p = item.get('properties', {})
            enabled = parse_rich_text(p.get('Enabled', {})).lower() == 'true' or (p.get('Enabled', {}).get('checkbox') is True)
            rule_type = parse_rich_text(p.get('RuleType', {})).strip().lower()
            if not enabled or (RULE_TYPES and rule_type not in RULE_TYPES):
                continue
            w = parse_rich_text(p.get('Weight', {})).strip()
            pr = parse_rich_text(p.get('Priority', {})).strip()
            rules.append({'tag_name': parse_rich_text(p.get('TagName', {})).strip(),'rule_type': rule_type,'match_field': (parse_rich_text(p.get('MatchField', {})).strip().lower() or 'both'),'weight': float(w) if w else 0.0,'priority': int(float(pr)) if pr else 0,'keywords': split_keywords(parse_rich_text(p.get('Keywords', {}))),'negative_keywords': split_keywords(parse_rich_text(p.get('NegativeKeywords', {})))})
        if not data.get('has_more'):
            break
        cursor = data.get('next_cursor')
    return rules

def add_unique(lst, value):
    if value and value not in lst:
        lst.append(value)

def main() -> int:
    if not INPUT_JSON.exists():
        raise FileNotFoundError(f'{INPUT_JSON} がありません')
    articles = json.loads(INPUT_JSON.read_text(encoding='utf-8'))
    rules = load_rules()

    by_type = {}
    for r in rules:
        by_type[r['rule_type']] = by_type.get(r['rule_type'], 0) + 1
    print('rules_db_id:', RULES_DB_ID)
    print('loaded_rules_count:', len(rules))
    print('loaded_rules_count_by_type:', json.dumps(by_type, ensure_ascii=False))
    print('enabled_rules_count:', len(rules))
    print('total_keyword_count:', sum(len(r.get('keywords', [])) for r in rules))
    print('sample_rule_names:', json.dumps([r.get('tag_name', '') for r in rules[:10]], ensure_ascii=False))

    out = []
    any_match_count = 0
    for a in articles:
        src = str(a.get('source_title') or '')
        pt = str(a.get('page_title') or '')
        body = str(a.get('text') or '')
        title_text = f'{src}\n{pt}'.lower()
        both_text = f'{src}\n{pt}\n{body}'.lower()
        score = 0.0
        priority = 0
        tags = []
        matched = []
        neg = []
        for rule in rules:
            target = title_text if rule['match_field'] == 'title' else both_text
            mk = next((kw for kw in rule['keywords'] if kw.lower() in target), None)
            mn = next((kw for kw in rule['negative_keywords'] if kw.lower() in target), None)
            if mk:
                score += rule['weight']
                priority = max(priority, rule['priority'])
                add_unique(tags, rule['tag_name'])
                add_unique(matched, rule['tag_name'])
            if mn:
                score -= abs(rule['weight'])
                add_unique(neg, f"{rule['tag_name']}:{mn}")
        if matched:
            any_match_count += 1
        a.update({'importance_score': score, 'priority': priority, 'tags': tags, 'matched_rules': matched, 'negative_matches': neg, 'is_low_value': any(w in (src+pt+body) for w in LOW_VALUE_WORDS)})
        out.append(a)

    out.sort(key=lambda x: (x.get('importance_score', 0), x.get('priority', 0), x.get('text_length', 0)), reverse=True)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    scores = [x.get('importance_score', 0.0) for x in out]
    mx = max(scores) if scores else 0.0
    mn = min(scores) if scores else 0.0
    avg = mean(scores) if scores else 0.0
    print('articles_with_any_match:', any_match_count)
    print('max_importance_score:', mx)
    print('min_importance_score:', mn)
    print('avg_importance_score:', round(avg, 3))
    if ENABLE_SCORING and out and mx < MIN_REPORT_SCORE:
        print(f'WARNING: top_importance_score={mx} is below report threshold={MIN_REPORT_SCORE}. Rules may not be matching.')
    print(f'scored {len(out)} articles -> {OUTPUT_JSON}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
