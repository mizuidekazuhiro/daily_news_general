import feedparser
import smtplib
import re
import os
import socket
import requests
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

# =====================
# タイムアウト
# =====================
socket.setdefaulttimeout(10)

# =====================
# Secrets
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
# Media RSS
# =====================
MEDIA = {
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+政治&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+金融&hl=ja&gl=JP&ceid=JP:ja",
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=en&gl=US&ceid=US:en"
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&hl=en&gl=US&ceid=US:en"
    ],
    "Fastmarkets": [
        "https://news.google.com/rss/search?q=Fastmarkets&hl=en&gl=US&ceid=US:en"
    ],
    "BigMint": [
        "https://news.google.com/rss/search?q=BigMint&hl=en&gl=US&ceid=US:en"
    ],
}

# =====================
# 日経ノイズ除外（★追加）
# =====================
NIKKEI_NOISE_KEYWORDS = [
    "NIKKEI COMPASS",
    "会社情報",
    "与信管理",
    "人事",
    "訃報",
    "文化",
    "スポーツ",
    "有限会社",
    "代表取締役",
    "資本金",
    "設立",
]

# =====================
# 重要度キーワード（変更なし）
# =====================
IMPORTANT_KEYWORDS = [
    "steel","iron","scrap","rebar","製鉄","鉄鋼","高炉","電炉",
    "construction","infrastructure","建設","土木","再開発",
    "ai","artificial intelligence","半導体","gpu","data center",
    "bank","banking","loan","融資","金利","市場","株式","債券",
    "government","policy","election","trade","tariff","制裁",
    "india","indian","インド","vietnam","ベトナム",
]

# =====================
# Utils
# =====================
def clean(text):
    return re.sub("<[^<]+?>", "", text).strip()

def importance_score(text):
    t = text.lower()
    score = sum(1 for k in IMPORTANT_KEYWORDS if k in t)
    return min(score, 3)

def deepl_translate(text):
    if not text:
        return ""
    r = requests.post(
        "https://api-free.deepl.com/v2/translate",
        data={
            "auth_key": DEEPL_API_KEY,
            "text": text,
            "source_lang": "EN",
            "target_lang": "JA",
        },
        timeout=10
    )
    return r.json()["translations"][0]["text"]

def is_english(text):
    return bool(re.search(r"[A-Za-z]", text))

def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except:
        return []

def published(e):
    if hasattr(e, "published_parsed"):
        return datetime(*e.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
    return "N/A"

def is_nikkei_noise(title, summary):
    text = title + summary
    return any(k in text for k in NIKKEI_NOISE_KEYWORDS)

# =====================
# HTML color
# =====================
COLOR = {
    3: "#fff5f5",
    2: "#fffaf0",
    1: "#f0f8ff",
    0: "#f8fafc"
}

# =====================
# Generate HTML
# =====================
def generate_html():
    articles = []

    for media, feeds in MEDIA.items():
        for url in feeds:
            for e in safe_parse(url):
                title = clean(e.get("title", ""))
                summary_raw = clean(e.get("summary", ""))

                # ★ 日経ノイズ除外
                if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                    continue

                score = importance_score(title + summary_raw)

                summary = (
                    deepl_translate(summary_raw[:1000])
                    if is_english(title)
                    else summary_raw[:300]
                )

                articles.append({
                    "media": media,
                    "title": title,
                    "summary": summary,
                    "score": score,
                    "published": published(e),
                    "link": e.get("link", "")
                })

    articles = sorted(
        articles,
        key=lambda x: (x["score"], x["published"]),
        reverse=True
    )[:15]

    body = "<html><body><h2>主要ニュース速報</h2><p>ニュースサマリ</p>"

    for a in articles:
        stars = "★" * a["score"] if a["score"] else "－"
        body += f"""
        <div style="background:{COLOR[a['score']]};
                    padding:12px;margin-bottom:12px;
                    border-left:4px solid #c53030;">
          <b>{a['title']}</b><br>
          <div>{a['summary']}</div>
          <div style="font-size:12px;">
            {a['media']}｜重要度:{stars}｜{a['published']}
          </div>
          <a href="{a['link']}">▶ 元記事</a>
        </div>
        """

    return body + "</body></html>"

# =====================
# Mail
# =====================
def send_mail(html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = f"主要ニュース｜{now_jst.strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(MAIL_FROM, MAIL_PASSWORD)
        s.send_message(msg)

# =====================
# Run
# =====================
if __name__ == "__main__":
    send_mail(generate_html())