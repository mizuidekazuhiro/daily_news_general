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
# 環境変数（GitHub Secrets）
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
# 媒体設定（15記事）
# =====================
MEDIA = {
    "日経新聞": (
        15,
        [
            "https://news.google.com/rss/search?q=site:nikkei.com+経済&hl=ja&gl=JP&ceid=JP:ja",
            "https://news.google.com/rss/search?q=site:nikkei.com+企業&hl=ja&gl=JP&ceid=JP:ja"
        ]
    ),
    "Bloomberg": (
        15,
        ["https://news.google.com/rss/search?q=Bloomberg&hl=en&gl=US&ceid=US:en"]
    ),
    "Reuters": (
        15,
        ["https://news.google.com/rss/search?q=Reuters&hl=en&gl=US&ceid=US:en"]
    ),
    "Fastmarkets": (
        15,
        ["https://news.google.com/rss/search?q=Fastmarkets&hl=en&gl=US&ceid=US:en"]
    ),
    "BigMint": (
        15,
        ["https://news.google.com/rss/search?q=BigMint&hl=en&gl=US&ceid=US:en"]
    )
}

# =====================
# 重要度キーワード
# =====================
IMPORTANT_KEYWORDS = [
    "steel","iron","scrap","rebar","dri","hbi","鉄鋼","製鉄","高炉","電炉",
    "construction","建設","インフラ","ai","artificial intelligence","半導体",
    "bank","銀行","金融","interest","rate","trade","tariff","関税",
    "india","インド","vietnam","ベトナム"
]

# =====================
# ユーティリティ
# =====================
def clean_html(text):
    return re.sub("<[^<]+?>", "", text).strip()

def importance_score(text):
    score = sum(1 for k in IMPORTANT_KEYWORDS if k.lower() in text.lower())
    return min(score, 3)

def published_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
    return "N/A"

def is_english(text):
    return re.search(r"[a-zA-Z]", text) is not None

# =====================
# DeepL翻訳（英語→日本語）
# =====================
def deepl_translate(text):
    if not text or not is_english(text):
        return text

    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            data={
                "auth_key": DEEPL_API_KEY,
                "text": text,
                "target_lang": "JA"
            },
            timeout=8
        )
        return r.json()["translations"][0]["text"]
    except Exception as e:
        print("[WARN] DeepL失敗:", e)
        return text

# =====================
# RSS安全取得
# =====================
def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except Exception as e:
        print("[WARN] RSS失敗:", url, e)
        return []

# =====================
# HTML生成
# =====================
def generate_html():
    body = """
    <html>
    <body style="font-family:'Meiryo UI','Segoe UI',sans-serif;background:#f8fafc;padding:20px;">
    <div style="max-width:900px;margin:auto;background:#ffffff;padding:24px;">
    <h2>主要ニュース速報</h2>
    <p>ニュースサマリ</p>
    <hr>
    """

    for media, (limit, feeds) in MEDIA.items():
        entries = []
        for f in feeds:
            entries.extend(safe_parse(f))

        entries = sorted(
            entries,
            key=lambda e: e.published_parsed if hasattr(e, "published_parsed") else 0,
            reverse=True
        )

        articles = []
        for e in entries:
            title = clean_html(e.get("title",""))
            summary_raw = clean_html(e.get("summary",""))
            summary = deepl_translate(summary_raw[:300])
            score = importance_score(title + summary)

            articles.append({
                "title": title,
                "summary": summary,
                "score": score,
                "published": published_date(e),
                "link": e.get("link","")
            })

            if len(articles) >= limit:
                break

        articles.sort(key=lambda x: x["score"], reverse=True)

        body += f"<h3>【{media}｜{len(articles)}件】</h3>"

        for a in articles:
            stars = "★"*a["score"] if a["score"] else "－"
            body += f"""
            <div style="margin-bottom:16px;padding:12px;border-left:4px solid #3182ce;">
              <div style="font-weight:bold">{a['title']}</div>
              <div style="margin:6px 0">{a['summary']}</div>
              <div style="font-size:12px;color:#555">
                重要度：{stars} ｜ Published：{a['published']}
              </div>
              <a href="{a['link']}" target="_blank">▶ 元記事</a>
            </div>
            """

        body += "<hr>"

    body += "</div></body></html>"
    return body

# =====================
# メール送信
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
    try:
        html = generate_html()
    except Exception as e:
        html = f"<pre>ニュース取得失敗\n{e}</pre>"

    send_mail(html)