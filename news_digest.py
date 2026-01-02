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
# 媒体設定
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
        "https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja"
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja"
    ]
}

# =====================
# 重要度キーワード
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

def normalize_link(url):
    if "news.google.com" in url and "url=" in url:
        url = urllib.parse.unquote(re.sub(r".*url=", "", url))
    url = re.sub(r"&utm_.*", "", url)
    return url.strip()

def resolve_final_url(url):
    try:
        return requests.get(url, timeout=10, allow_redirects=True).url
    except:
        return url

def is_nikkei_noise(title, summary):
    noise = [
        "会社情報","与信管理","NIKKEI COMPASS",
        "会社概要","現状と将来性","業界の動向",
        "経営・財務","リスク情報","企業分析","基本情報",
        "セミナー","イベント","説明会","講演",
        "参加者募集","オンライン開催","受講料","主催",
        "キャンペーン","SALE","セール","発売",
        "初売り","無料","最大","OFF",
        "新製品","サービス開始","提供開始",
        "PR","提供","公式","【","［"
    ]
    return any(n in title or n in summary for n in noise)

def is_within_24h(entry):
    if not hasattr(entry, "published_parsed") or not entry.published_parsed:
        return False
    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return dt.astimezone(JST) >= now_jst - timedelta(hours=24)

def generate_html():
    all_articles = []
    seen = set()
    raw_media = {"Kallanish","BigMint","Fastmarkets","Argus"}

    for media, feeds in MEDIA.items():
        for url in feeds:
            for e in safe_parse(url):
                if not is_within_24h(e):
                    continue

                title = clean(e.get("title", ""))
                summary_raw = clean(e.get("summary", ""))

                if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                    continue

                final_url = normalize_link(resolve_final_url(e.get("link","")))

                dedup_key = re.sub(r"（.*?）|- .*?$", "", title)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                score = importance_score(title + summary_raw)

                if media in raw_media:
                    body = scrape_article_text(final_url)
                    summary = deepl_translate(title)
                else:
                    body = ""
                    summary = ""

                all_articles.append({
                    "media": media,
                    "title": title,
                    "summary": summary,
                    "body": body,
                    "score": score,
                    "published": published(e),
                    "link": final_url
                })

    all_articles = sorted(all_articles, key=lambda x:(x["score"],x["published"]), reverse=True)

    body_html = "<html><body><h2>主要ニュース速報（重要度順）</h2>"
    for a in all_articles:
        stars = "★"*a["score"] if a["score"] else "－"
        body_html += f"""
        <div style="background:{COLOR_BG[a['score']]};
        border-left:5px solid {COLOR_BORDER[a['score']]};
        padding:12px;margin-bottom:14px;">
        <b>{a['title']}</b><br>"""
        if a["summary"]:
            body_html += f"<div>{a['summary']}</div>"
        if a["body"]:
            body_html += f"<div style='white-space:pre-wrap;'>{a['body']}</div>"
        body_html += f"""
        <div style="font-size:12px;color:#555;">
        {a['media']}｜重要度:{stars}｜{a['published']}
        </div>
        <a href="{a['link']}">▶ 元記事</a>
        </div>"""
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