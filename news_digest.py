import feedparser
import smtplib
import re
import os
import socket
import requests
import urllib.parse
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

# =====================
# タイムアウト設定
# =====================
socket.setdefaulttimeout(10)

# =====================
# メール設定
# =====================
MAIL_FROM = os.environ["MAIL_FROM"]
MAIL_TO = os.environ["MAIL_TO"]
MAIL_PASSWORD = os.environ["MAIL_PASSWORD"]
DEEPL_API_KEY = os.environ["DEEPL_API_KEY"]
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# =====================
# JST
# =====================
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)

# =====================
# 媒体設定（★パターン②）
# =====================
MEDIA = {
    "Kallanish": [
        "https://news.google.com/rss/search?q=site:kallanish.com&hl=en&ceid=US:en"
    ],
    "BigMint": [
        "https://news.google.com/rss/search?q=BigMint&hl=en&ceid=US:en"
    ],
    "Fastmarkets": [
        "https://news.google.com/rss/search?q=Fastmarkets&hl=en&ceid=US:en"
    ],
    "Argus": [
        "https://news.google.com/rss/search?q=site:argusmedia.com&hl=en&ceid=US:en"
    ],
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+-人事+-訃報+-文化+-スポーツ&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+市場&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+産業&hl=ja&gl=JP&ceid=JP:ja"
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Bloomberg&hl=en&ceid=US:en"
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=Reuters&hl=en&ceid=US:en"
    ]
}

# =====================
# 重要度キーワード
# =====================
IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel","iron","scrap","rebar","H形鋼","H Beam","製鉄","鉄鋼","高炉","電炉","ferrous"],
    "建設": ["construction","infrastructure","建設","ゼネコン"],
    "AI": ["ai","artificial intelligence","semiconductor","半導体","生成ai","Data Center","データセンター"],
    "企業": ["m&a","買収","商社","三菱商事","住友商事","伊藤忠商事","丸紅","三井物産"],
    "通商": ["trade","tariff","sanction","関税","AD"],
    "重点国": ["india","indian","インド","vietnam","ベトナム","Bangladesh","バングラデシュ"]
}

# =====================
# 色分け
# =====================
COLOR_BG = {3:"#fff5f5",2:"#fffaf0",1:"#f0f9ff",0:"#ffffff"}
COLOR_BORDER = {3:"#c53030",2:"#dd6b20",1:"#3182ce",0:"#d0d7de"}

# =====================
# ユーティリティ
# =====================
def clean(text):
    return re.sub("<[^<]+?>", "", text).strip()

def importance_score(text):
    text = text.lower()
    score = 0

    for words in IMPORTANT_KEYWORDS.values():
        for w in words:
            if w.isascii() and w.isalpha():
                if re.search(rf"\b{re.escape(w)}\b", text):
                    score += 1
            else:
                if w in text:
                    score += 1

    return min(score, 3)

def published(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
    return "N/A"

def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except:
        return []

def deepl_translate(text):
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={
                "auth_key": DEEPL_API_KEY,
                "text": text,
                "target_lang": "JA"
            },
            timeout=10
        )
        return r.json()["translations"][0]["text"]
    except:
        return text

def normalize_link(url):
    if "news.google.com" in url and "url=" in url:
        url = urllib.parse.unquote(re.sub(r".*url=", "", url))
    return re.sub(r"&utm_.*", "", url)

def is_nikkei_noise(title, summary):
    noise = [
        "会社情報","与信管理","NIKKEI COMPASS",
        "会社概要","現状と将来性","業界の動向",
        "経営・財務","リスク情報","企業分析","基本情報",
        "セミナー","イベント","説明会","講演","参加者募集",
        "オンライン開催","受講料","主催",
        "キャンペーン","SALE","セール","発売","初売り",
        "無料","最大","OFF",
        "新製品","サービス開始","提供開始",
        "PR","提供","公式","【","［"
    ]
    return any(n in title or n in summary for n in noise)

def is_within_24h(dt):
    return dt >= now_jst - timedelta(hours=24)

def normalize_title(title):
    t = title.lower()
    t = re.sub(r"（.*?）|\(.*?\)", "", t)
    t = re.sub(r"-.*$", "", t)
    t = re.sub(r"(reuters|bloomberg|yahoo|msn|dメニュー|ロイター)", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def is_japanese(text):
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))

# =====================
# generate
# =====================
def generate_html():
    final_articles = []

    for media, feeds in MEDIA.items():
        collected = []
        buffer_zero = []
        seen = set()
        offset = 0
        exhausted = False

        while len(collected) < 15 and not exhausted:
            found_recent = False

            for url in feeds:
                entries = safe_parse(url)
                slice_entries = entries[offset:offset+15]

                if not slice_entries:
                    continue

                for e in slice_entries:
                    title = clean(e.get("title", ""))
                    summary_raw = clean(e.get("summary", ""))
                    link = normalize_link(e.get("link",""))

                    try:
                        dt = datetime.strptime(
                            published(e), "%Y-%m-%d %H:%M"
                        ).replace(tzinfo=JST)
                    except:
                        continue

                    if not is_within_24h(dt):
                        continue

                    found_recent = True

                    if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                        continue

                    key = normalize_title(title)
                    if key in seen:
                        continue
                    seen.add(key)

                    score = importance_score(title + summary_raw)

                    item = {
                        "media": media,
                        "title": title,
                        "summary": "",
                        "score": score,
                        "published": published(e),
                        "link": link
                    }

                    if score >= 1:
                        collected.append(item)
                    else:
                        buffer_zero.append(item)

            if not found_recent:
                exhausted = True

            offset += 15

        for a in sorted(buffer_zero, key=lambda x:x["published"], reverse=True):
            if len(collected) >= 15:
                break
            collected.append(a)

        final_articles.extend(collected)

    # ★翻訳ルール（英語のみ）
    for a in final_articles:
        if a["media"] in {"Kallanish","BigMint","Fastmarkets","Argus","Reuters","Bloomberg"}:
            if not is_japanese(a["title"]):
                a["summary"] = deepl_translate(a["title"])

    body_html = """
    <html>
    <body style="
        font-family:
            'Meiryo UI',
            'Meiryo',
            'Yu Gothic',
            'Hiragino Kaku Gothic ProN',
            sans-serif;
    ">
    <h2>主要ニュース速報（重要度順）</h2>
    """

    for a in sorted(final_articles, key=lambda x:(x["score"],x["published"]), reverse=True):
        stars = "★"*a["score"] if a["score"] else "－"
        body_html += f"""
        <div style="background:{COLOR_BG[a['score']]};
                    border-left:5px solid {COLOR_BORDER[a['score']]};
                    padding:12px;margin-bottom:14px;">
            <b>{a['title']}</b><br>
        """
        if a["summary"]:
            body_html += f"<div>{a['summary']}</div>"
        body_html += f"""
            <div style="font-size:12px;color:#555;">
                {a['media']}｜重要度:{stars}｜{a['published']}
            </div>
            <a href="{a['link']}">▶ 元記事</a>
        </div>
        """
    body_html += "</body></html>"
    return body_html

def send_mail(html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = f"主要ニュースまとめ｜{now_jst.strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(MAIL_FROM, MAIL_PASSWORD)
        s.send_message(msg)

if __name__ == "__main__":
    send_mail(generate_html())