
import feedparser
import smtplib
import re
import os
import socket
import requests
import urllib.parse
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

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
        "https://news.google.com/rss/search?q=site:kallanish.com&amp;hl=en&amp;ceid=US:en"
    ],
    "BigMint": [
        "https://news.google.com/rss/search?q=BigMint&amp;hl=en&amp;ceid=US:en"
    ],
    "Fastmarkets": [
        "https://news.google.com/rss/search?q=Fastmarkets&amp;hl=en&amp;ceid=US:en"
    ],
    "Argus": [
        "https://news.google.com/rss/search?q=site:argusmedia.com&amp;hl=en&amp;ceid=US:en"
    ],
    "日経新聞": [
        "https://news.google.com/rss/search?q=site:nikkei.com+-人事+-訃報+-文化+-スポーツ&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+市場&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+企業&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+政策&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja",
        "https://news.google.com/rss/search?q=site:nikkei.com+産業&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja"
    ],
    "Bloomberg": [
        "https://news.google.com/rss/search?q=Bloomberg&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja"
    ],
    "Reuters": [
        "https://news.google.com/rss/search?q=Reuters&amp;hl=ja&amp;gl=JP&amp;ceid=JP:ja"
    ]
}

# =====================
# 重要度キーワード
# =====================
IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel","iron","scrap","rebar","製鉄","鉄鋼","高炉","電炉","ferrous"],
    "建設": ["construction","infrastructure","建設","再開発"],
    "AI": ["ai","artificial intelligence","semiconductor","半導体","生成ai"],
    "政治": ["government","policy","election","政権","政策","規制"],
    "企業": ["company","earnings","決算","m&amp;a","投資"],
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
    return re.sub("&lt;[^&lt;]+?&gt;", "", text).strip()

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
    parsed = urllib.parse.urlparse(url)
    url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return url.strip()

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
    return dt &gt;= now_jst - timedelta(hours=24)

# =====================
# 記事本文から published 取得
# =====================
def fetch_published_from_article(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        m = re.search(r'&lt;time[^&gt;]*datetime="([^"]+)"', r.text)
        if not m:
            return None
        dt = datetime.fromisoformat(m.group(1).replace("Z","")).astimezone(JST)
        return dt if is_within_24h(dt) else None
    except:
        return None

# =====================
# sitemap fetchers（50件制限）
# =====================
def fetch_bigmint_from_sitemap():
    urls = []
    try:
        r = requests.get("https://www.bigmint.co/sitemap.xml", timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        root = ET.fromstring(r.text)
        for u in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
            loc = u.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc is not None:
                urls.append(loc.text)
            if len(urls) &gt;= 50:
                break
    except:
        pass
    return urls

def fetch_kallanish_from_sitemap():
    urls = []
    try:
        r = requests.get("https://www.kallanish.com/sitemap.xml", timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        root = ET.fromstring(r.text)
        for u in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
            loc = u.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc is not None:
                urls.append(loc.text)
            if len(urls) &gt;= 50:
                break
    except:
        pass
    return urls

def fetch_fastmarkets_from_sitemap():
    urls = []
    try:
        r = requests.get("https://www.fastmarkets.com/sitemap.xml", timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        root = ET.fromstring(r.text)
        for u in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
            loc = u.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc is not None:
                urls.append(loc.text)
            if len(urls) &gt;= 50:
                break
    except:
        pass
    return urls

def fetch_argus_from_sitemap():
    urls = []
    try:
        r = requests.get("https://www.argusmedia.com/sitemap.xml", timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        root = ET.fromstring(r.text)
        for u in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
            loc = u.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc is not None:
                urls.append(loc.text)
            if len(urls) &gt;= 50:
                break
    except:
        pass
    return urls

def generate_html():
    all_articles = []
    seen = set()
    seen_links = set()   # ← 追加（重複対策）
    raw_media = {"Kallanish","BigMint","Fastmarkets","Argus"}

    # sitemap
    for media, fetcher in [
        ("BigMint", fetch_bigmint_from_sitemap),
        ("Kallanish", fetch_kallanish_from_sitemap),
        ("Fastmarkets", fetch_fastmarkets_from_sitemap),
        ("Argus", fetch_argus_from_sitemap)
    ]:
        for link in fetcher():
            if link in seen_links:
                continue
            dt = fetch_published_from_article(link)
            if not dt:
                continue
            title = link.split("/")[-1].replace("-"," ").title()
            dedup_key = re.sub(r"（.*?）|- .*?$", "", title)
            dedup_key = re.sub(r"\s+", " ", dedup_key).strip().lower()
            if "重複記事を削除します" in title:
                continue
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            seen_links.add(link)
            all_articles.append({
                "media": media,
                "title": title,
                "summary": "",
                "score": importance_score(title),
                "published": dt.strftime("%Y-%m-%d %H:%M"),
                "link": link
            })

    # RSS
    for media, feeds in MEDIA.items():
        for url in feeds:
            for e in safe_parse(url):
                title = clean(e.get("title", ""))
                if "重複記事を削除します" in title:
                    continue
                summary_raw = clean(e.get("summary", ""))
                if media == "日経新聞" and is_nikkei_noise(title, summary_raw):
                    continue
                link = normalize_link(e.get("link",""))
                if link in seen_links:
                    continue
                dedup_key = re.sub(r"（.*?）|- .*?$", "", title)
                dedup_key = re.sub(r"\s+", " ", dedup_key).strip().lower()
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                seen_links.add(link)
                all_articles.append({
                    "media": media,
                    "title": title,
                    "summary": "",
                    "score": importance_score(title + summary_raw),
                    "published": published(e),
                    "link": link
                })

    # ===== 24h &amp; 各媒体15件 =====
    final_articles = []
    for media in set(a["media"] for a in all_articles):
        media_items = []
        for a in all_articles:
            if a["media"] != media:
                continue
            try:
                dt = datetime.strptime(a["published"], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            except:
                continue
            if not is_within_24h(dt):
                continue
            media_items.append(a)

        selected = []
        for score in [3,2,1,0]:
            for a in sorted(media_items, key=lambda x:x["published"], reverse=True):
                if a["score"] == score and a not in selected:
                    selected.append(a)
                    if len(selected) &gt;= 15:
                        break
            if len(selected) &gt;= 15:
                break

        final_articles.extend(selected)

    # 翻訳はここでのみ実行
    for a in final_articles:
        if a["media"] in raw_media:
            a["summary"] = deepl_translate(a["title"])

    all_articles = sorted(final_articles, key=lambda x:(x["score"],x["published"]), reverse=True)

    body_html = "&lt;html&gt;&lt;body&gt;&lt;h2&gt;主要ニュース速報（重要度順）&lt;/h2&gt;"
    for a in all_articles:
        stars = "★"*a["score"] if a["score"] else "－"
        body_html += f"""
        &lt;div style="background:{COLOR_BG[a['score']]}; border-left:5px solid {COLOR_BORDER[a['score']]}; padding:12px;margin-bottom:14px;"&gt;
            &lt;b&gt;{a['title']}&lt;/b&gt;&lt;br&gt;
        """
        if a["summary"]:
            body_html += f"&lt;div&gt;{a['summary']}&lt;/div&gt;"
        body_html += f"""
            &lt;div style="font-size:12px;color:#555;"&gt;
                {a['media']}｜重要度:{stars}｜{a['published']}
            &lt;/div&gt;
            &lt;a href="{a['link']}"&gt;▶ 元記事&lt;/a&gt;
        &lt;/div&gt;
        """
    body_html += "&lt;/body&gt;&lt;/html&gt;"
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
