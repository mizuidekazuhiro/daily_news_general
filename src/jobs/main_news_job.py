import json
import logging
import os
import re
import socket
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
from openai import OpenAI

from src.config.job_config import (
    MAIN_NEWS_MAIL_TO,
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MODEL,
    OPENAI_RESPONSE_TRUNCATE_CHARS,
    OPENAI_TRANSLATION_BATCH_SIZE,
    TITLE_TRANSLATION_CACHE_PATH,
)
from src.utils.date_utils import JST, now_jst
from src.utils.mail_utils import parse_mail_recipients, send_html_email

socket.setdefaulttimeout(10)

MEDIA = {
    "Kallanish": ["https://news.google.com/rss/search?q=site:kallanish.com&hl=en&ceid=US:en"],
    "BigMint": ["https://news.google.com/rss/search?q=BigMint&hl=en&ceid=US:en"],
    "Fastmarkets": ["https://news.google.com/rss/search?q=Fastmarkets&hl=en&ceid=US:en"],
    "Argus": ["https://news.google.com/rss/search?q=site:argusmedia.com&hl=en&ceid=US:en"],
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+-人事+-訃報+-文化+-スポーツ&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+市場&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+産業&hl=ja&gl=JP&ceid=JP:ja",
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Bloomberg&hl=en&ceid=US:en",
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Reuters&hl=en&ceid=US:en",
    ],
    "MySteel": ["https://news.google.com/rss/search?q=steel+mysteel&hl=en&ceid=US:en"],
}

IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel", "iron", "scrap", "rebar", "H形鋼", "H Beam", "製鉄", "鉄鋼", "高炉", "電炉", "ferrous"],
    "建設": ["construction", "infrastructure", "建設", "ゼネコン"],
    "AI": ["ai", "artificial intelligence", "semiconductor", "半導体", "生成ai", "Data Center", "データセンター"],
    "企業": ["m&a", "買収", "商社", "三菱商事", "住友商事", "伊藤忠商事", "丸紅", "三井物産"],
    "通商": ["trade", "tariff", "sanction", "関税", "AD"],
    "重点国": ["india", "indian", "インド", "vietnam", "ベトナム", "Bangladesh", "バングラデシュ"],
}

COLOR_BG = {3: "#fff5f5", 2: "#fffaf0", 1: "#f0f9ff", 0: "#ffffff"}
COLOR_BORDER = {3: "#c53030", 2: "#dd6b20", 1: "#3182ce", 0: "#d0d7de"}


def clean(text):
    return re.sub("<[^<]+?>", "", text).strip()


def get_published_datetime(entry):
    parsed = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        parsed = entry.published_parsed
    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
        parsed = entry.updated_parsed
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(JST)
        except (TypeError, ValueError):
            return None
    return None


def normalize_link(url):
    if "news.google.com" in url and "url=" in url:
        url = urllib.parse.unquote(re.sub(r".*url=", "", url))
    return re.sub(r"&utm_.*", "", url)


def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except Exception:
        return []


def importance_score(text):
    text = text.lower()
    score = 0
    for words in IMPORTANT_KEYWORDS.values():
        for w in words:
            if w.isascii() and w.isalpha():
                if re.search(rf"\b{re.escape(w)}\b", text):
                    score += 1
            elif w in text:
                score += 1
    return min(score, 3)


def published(entry):
    published_dt = get_published_datetime(entry)
    if not published_dt:
        return "N/A"
    return published_dt.strftime("%Y-%m-%d %H:%M")


def is_nikkei_noise(title, summary):
    noise = ["会社情報", "与信管理", "NIKKEI COMPASS", "会社概要", "現状と将来性", "業界の動向", "経営・財務", "リスク情報", "企業分析", "基本情報", "セミナー", "イベント", "説明会", "講演", "参加者募集", "オンライン開催", "受講料", "主催", "キャンペーン", "SALE", "セール", "発売", "初売り", "無料", "最大", "OFF", "新製品", "サービス開始", "提供開始", "PR", "提供", "公式", "【", "［"]
    return any(n in title or n in summary for n in noise)


def is_within_24h(dt):
    return dt >= datetime.now(JST) - timedelta(hours=24)


def normalize_title(title):
    t = title.lower()
    t = re.sub(r"（.*?）|\(.*?\)", "", t)
    t = re.sub(r"-.*$", "", t)
    t = re.sub(r"(reuters|bloomberg|yahoo|msn|dメニュー|ロイター)", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_japanese(text):
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))


def normalize_cache_key(title):
    return re.sub(r"\s+", " ", title.strip())


def load_translation_cache(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_translation_cache(path, cache):
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    tmp_path.replace(cache_path)


def truncate_for_log(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def estimate_max_output_tokens(titles):
    total_chars = sum(len(title) for title in titles)
    estimated_tokens = max(128, total_chars // 2)
    return max(OPENAI_MAX_OUTPUT_TOKENS, min(2048, estimated_tokens))


def normalize_translations_payload(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "translations" in payload:
            translations = payload["translations"]
            if isinstance(translations, list):
                return translations
            if isinstance(translations, dict):
                payload = translations
            else:
                return None
        if all(isinstance(k, str) and k.isdigit() for k in payload.keys()):
            ordered_keys = sorted(payload.keys(), key=lambda x: int(x))
            return [payload[key] for key in ordered_keys]
    return None


def parse_translation_response(content, expected_len):
    payload = json.loads(content)
    translations = normalize_translations_payload(payload)
    if not isinstance(translations, list) or len(translations) != expected_len:
        raise ValueError("Invalid translation response format.")
    return translations


def is_valid_translation(source, translated):
    return isinstance(translated, str) and translated.strip() and translated.strip() != source.strip()


def build_translation_messages(titles):
    return [
        {"role": "system", "content": "あなたは翻訳エンジン。入力配列を日本語の配列に翻訳して返す。固有名詞/企業名/略語は原則保持し、過度な意訳を避ける。タイトルなので短く自然な日本語にする。入力順序を必ず維持する。出力は{\"translations\":[...]}の厳密JSONのみ。"},
        {"role": "user", "content": json.dumps(titles, ensure_ascii=False)},
    ]


def build_response_format(use_schema):
    if use_schema:
        return {"type": "json_schema", "json_schema": {"name": "translation_response", "schema": {"type": "object", "additionalProperties": False, "properties": {"translations": {"type": "array", "items": {"type": "string"}}}, "required": ["translations"]}}}
    return {"type": "json_object"}


def request_translations(client, titles, model, use_schema):
    response_format = build_response_format(use_schema)
    response = client.chat.completions.create(model=model, messages=build_translation_messages(titles), temperature=0, max_tokens=estimate_max_output_tokens(titles), response_format=response_format)
    return response


def translate_titles_to_ja(titles, client_factory=OpenAI):
    if not titles:
        return []
    cache = load_translation_cache(TITLE_TRANSLATION_CACHE_PATH)
    keys = [normalize_cache_key(title) for title in titles]
    translations = [cache.get(k) for k in keys]
    missing_idx = [i for i, t in enumerate(translations) if t is None]

    if missing_idx and os.getenv("OPENAI_API_KEY"):
        client = client_factory()
        for start in range(0, len(missing_idx), OPENAI_TRANSLATION_BATCH_SIZE):
            batch_idx = missing_idx[start:start + OPENAI_TRANSLATION_BATCH_SIZE]
            batch_titles = [titles[i] for i in batch_idx]
            batch_translations = None
            content = ""
            try:
                response = request_translations(client, batch_titles, OPENAI_MODEL, use_schema=True)
                content = response.choices[0].message.content
                batch_translations = parse_translation_response(content, len(batch_titles))
            except Exception:
                try:
                    response = request_translations(client, batch_titles, OPENAI_MODEL, use_schema=False)
                    content = response.choices[0].message.content
                    batch_translations = parse_translation_response(content, len(batch_titles))
                except Exception as exc:
                    logging.warning("OpenAI translation failed after fallback: %s; raw_content=%s", exc, truncate_for_log(content, OPENAI_RESPONSE_TRUNCATE_CHARS))
                    continue
            for idx, translated in zip(batch_idx, batch_translations):
                source = titles[idx]
                if is_valid_translation(source, translated):
                    translations[idx] = translated
                    cache[keys[idx]] = translated
                else:
                    translations[idx] = source

    for i, t in enumerate(translations):
        if t is None:
            translations[i] = titles[i]
    save_translation_cache(TITLE_TRANSLATION_CACHE_PATH, cache)
    return translations


def generate_main_news_html():
    final_articles = []
    for media, feeds in MEDIA.items():
        collected, buffer_zero, seen = [], [], set()
        offset = 0
        exhausted = False
        while len(collected) < 15 and not exhausted:
            found_recent = False
            for url in feeds:
                entries = safe_parse(url)
                slice_entries = entries[offset:offset + 15]
                if not slice_entries:
                    continue
                for e in slice_entries:
                    title = clean(e.get("title", ""))
                    summary_raw = clean(e.get("summary", ""))
                    link = normalize_link(e.get("link", ""))
                    dt = get_published_datetime(e)
                    if not dt or not is_within_24h(dt):
                        continue
                    found_recent = True
                    if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                        continue
                    key = normalize_title(title)
                    if key in seen:
                        continue
                    seen.add(key)
                    item = {"media": media, "title": title, "title_ja": "", "summary": "", "score": importance_score(title + summary_raw), "published": published(e), "link": link}
                    (collected if item["score"] >= 1 else buffer_zero).append(item)
            if not found_recent:
                exhausted = True
            offset += 15
        for a in sorted(buffer_zero, key=lambda x: x["published"], reverse=True):
            if len(collected) >= 15:
                break
            collected.append(a)
        final_articles.extend(collected)

    target_media = {"Kallanish", "BigMint", "Fastmarkets", "Argus", "MySteel", "Reuters", "Bloomberg"}
    to_translate = [a["title"] for a in final_articles if a["media"] in target_media and not is_japanese(a["title"])]
    indices = [i for i, a in enumerate(final_articles) if a["media"] in target_media and not is_japanese(a["title"])]
    for a in final_articles:
        if not a["title_ja"]:
            a["title_ja"] = a["title"]
    if to_translate:
        for idx, translated in zip(indices, translate_titles_to_ja(to_translate)):
            final_articles[idx]["title_ja"] = translated

    body_html = """
    <html><body style="font-family:'Meiryo UI','Meiryo','Yu Gothic','Hiragino Kaku Gothic ProN',sans-serif;">
    <h2>主要ニュース速報（重要度順）</h2>
    """
    for a in sorted(final_articles, key=lambda x: (x["score"], x["published"]), reverse=True):
        stars = "★" * a["score"] if a["score"] else "－"
        display_title = a.get("title_ja") or a["title"]
        body_html += f"<div style='background:{COLOR_BG[a['score']]};border-left:5px solid {COLOR_BORDER[a['score']]};padding:12px;margin-bottom:14px;'><b>{display_title}</b><br>"
        if not is_japanese(a["title"]):
            body_html += f"<div style='font-size:12px;color:#666;font-style:italic;margin-top:4px;'>🇬🇧 EN: {a['title']}</div>"
        body_html += f"<div style='font-size:12px;color:#555;'>{a['media']}｜重要度:{stars}｜{a['published']}</div><a href='{a['link']}'>▶ 元記事</a></div>"
    body_html += "</body></html>"
    return body_html


def send_main_news_email(html):
    send_html_email(
        html=html,
        subject=f"主要ニュースまとめ｜{now_jst().strftime('%Y-%m-%d')}",
        to_list=parse_mail_recipients(MAIN_NEWS_MAIL_TO),
    )


def run_main_news_delivery():
    send_main_news_email(generate_main_news_html())
