import feedparser
import smtplib
import re
import os
import socket
import requests
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
# 媒体設定（★順序変更のみ）
# =====================
MEDIA = {
    "Kallanish": [
        "https://news.google.com/rss/search?q=Kallanish&hl=en&ceid=US:en"
    ],
    "BigMint": [
        "https://news.google.com/rss/search?q=BigMint&hl=en&ceid=US:en"
    ],
    "Fastmarkets": [
        "https://news.google.com/rss/search?q=Fastmarkets&hl=en&ceid=US:en"
    ],
    "Argus": [
        "https://news.google.com/rss/search?q=Argus+Media&hl=en&ceid=US:en"
    ],
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+-人事+-訃報+-文化+-スポーツ&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+市場&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+産業&hl=ja&gl=JP&ceid=JP:ja"
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja"
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja"
    ]
}

# =====================
# 重要度キーワード（変更なし）
# =====================
IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel","iron","scrap","rebar","製鉄","鉄鋼","高炉","電炉"],
    "建設": ["construction","infrastructure","建設","再開発"],
    "AI": ["ai","artificial intelligence","semiconductor","半導体","生成ai"],
    "政治": ["government","policy","election","政権","政策","規制"],
    "企業": ["company","earnings","決算","m&a","投資"],
    "通商": ["trade","tariff","sanction","関税","制裁"],
    "重点国": ["india","indian","インド","vietnam","ベトナム"]
}

# =====================
# 色分け（変更なし）
# =====================
COLOR_BG = {3:"#fff5f5",2:"#fffaf0",1:"#f0f9ff",0:"#ffffff"}
COLOR_BORDER = {3:"#c53030",2:"#dd6b20",1:"#3182ce",0:"#d0d7de"}

# =====================
# ユーティリティ
# =====================
def clean(text):
    return re.sub("<[^<]+?>", "", text).strip()

def is_english(text):
    return re.search(r"[A-Za-z]", text) is not None

def importance_score(text):
    text = text.lower()
    return min(sum(w in text for v in IMPORTANT_KEYWORDS.values() for w in v), 3)

def published(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
    return "N/A"

def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except:
        return []

def scrape_article_text(url):
    try:
        r = requests.get(url, timeout=10)
        html = r.text
        html = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.S)
        html = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.S)
        text = re.sub("<[^<]+?>", "", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:4000]
    except:
        return ""

# =====================
# HTML生成（★原文貼付追加）
# =====================
def generate_html():
    media_articles = {}
    seen = set()
    raw_media = {"Kallanish","BigMint","Fastmarkets","Argus"}

    for media, feeds in MEDIA.items():
        articles = []

        for url in feeds:
            for e in safe_parse(url):
                if not is_within_24h(e):
                    continue

                title = clean(e.get("title", ""))
                summary_raw = clean(e.get("summary", ""))
                key = media + title
                if key in seen:
                    continue
                seen.add(key)

                score = importance_score(title + summary_raw)

                if media in raw_media:
                    summary = scrape_article_text(e.get("link",""))
                else:
                    summary = deepl_translate(summary_raw[:1000]) if is_english(title) else summary_raw[:300]

                articles.append({
                    "title": title,
                    "summary": summary,
                    "score": score,
                    "published": published(e),
                    "link": e.get("link","")
                })

        media_articles[media] = sorted(articles, key=lambda x:(x["score"],x["published"]), reverse=True)[:15]

    body = "<html><body><h2>主要ニュース速報</h2>"
    for media, articles in media_articles.items():
        body += f"<h3>【{media}｜{len(articles)}件】</h3>"
        for a in articles:
            stars = "★"*a["score"] if a["score"] else "－"
            body += f"""
            <div style="background:{COLOR_BG[a['score']]};
            border-left:5px solid {COLOR_BORDER[a['score']]};
            padding:12px;margin-bottom:14px;">
            <b>{a['title']}</b><br>
            <div style="white-space:pre-wrap;">{a['summary']}</div>
            <div style="font-size:12px;color:#555;">
            {media}｜重要度:{stars}｜{a['published']}
            </div>
            <a href="{a['link']}">▶ 元記事</a>
            </div>"""
        body += "<hr>"
    body += "</body></html>"
    return body

# =====================
# 24時間判定（変更なし）
# =====================
def is_within_24h(entry):
    if not hasattr(entry, "published_parsed") or not entry.published_parsed:
        return False
    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return dt.astimezone(JST) >= now_jst - timedelta(hours=24)

# =====================
# DeepL（変更なし）
# =====================
def deepl_translate(text):
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={"auth_key":DEEPL_API_KEY,"text":text,"target_lang":"JA"},
            timeout=10
        )
        return r.json()["translations"][0]["text"]
    except:
        return text

# =====================
# メール送信（変更なし）
# =====================
def send_mail(html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = f"主要ニュースまとめ｜{now_jst.strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(MAIL_FROM, MAIL_PASSWORD)
        s.send_message(msg)

# =====================
# 実行
# =====================
if __name__ == "__main__":
    send_mail(generate_html())