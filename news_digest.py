import feedparser
import smtplib
import re
import os
import socket
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

# =====================
# タイムアウト設定（②採用）
# =====================
socket.setdefaulttimeout(10)

# =====================
# メール設定（GitHub Secrets）
# =====================
MAIL_FROM = os.environ["MAIL_FROM"]
MAIL_TO = os.environ["MAIL_TO"]
MAIL_PASSWORD = os.environ["MAIL_PASSWORD"]

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# =====================
# JST判定
# =====================
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)
IS_MONDAY = now_jst.weekday() == 0  # 月曜

# =====================
# 媒体設定（Google News RSS）
# =====================
MEDIA = {
    "日経新聞": (
        30,
        ["https://news.google.com/rss/search?q=site:nikkei.com+-NIKKEI+COMPASS+-会社情報+-与信管理+-人事+-訃報+-文化+-スポーツ&hl=ja&gl=JP&ceid=JP:ja"]
    ),

    "Bloomberg": (
        30,
        ["https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja"]
    ),
    "Reuters": (
        30,
        ["https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja"]
    ),
    "東洋経済": (
        30,
        ["https://toyokeizai.net/list/feed/rss"]
    )
}

# =====================
# 重要度キーワード
# =====================
IMPORTANT_KEYWORDS = {
    "鉄鋼": ["steel", "iron", "scrap", "鉄鋼", "製鉄", "高炉", "スクラップ"],
    "政治": ["government", "policy", "政権", "法案", "規制", "election"],
    "企業": ["company", "corp", "企業", "決算", "m&a", "investment", "jv"],
    "金融": ["market", "interest", "rate", "金融", "金利", "市場"],
    "通商": ["trade", "tariff", "sanction", "輸出", "輸入", "関税", "制裁"]
}

# =====================
# ユーティリティ
# =====================
def clean_html(text):
    return re.sub("<[^<]+?>", "", text).strip()

def importance_score(text):
    score = 0
    text = text.lower()
    for words in IMPORTANT_KEYWORDS.values():
        for w in words:
            if w in text:
                score += 1
    return min(score, 3)

def simple_summary(entry):
    summary = clean_html(entry.get("summary", ""))
    return summary[:200] + "…" if len(summary) > 200 else summary

def published_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
    return "N/A"

def is_within_last_week(entry):
    if not hasattr(entry, "published_parsed"):
        return False
    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return published >= datetime.now(timezone.utc) - timedelta(days=7)

# =====================
# RSS安全取得（失敗しても止まらない）
# =====================
def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except Exception as e:
        print(f"[WARN] RSS取得失敗: {url} / {e}")
        return []

# =====================
# 週次振り返り
# =====================
def weekly_review(entries):
    total = 0
    important = 0
    titles = []

    for e in entries:
        if not is_within_last_week(e):
            continue

        title = clean_html(e.get("title", ""))
        summary = clean_html(e.get("summary", ""))
        score = importance_score(title + summary)

        total += 1
        if score == 3:
            important += 1
            if len(titles) < 3:
                titles.append(title)

    return total, important, titles

# =====================
# HTML描画
# =====================
def render_articles(articles, highlight=False):
    bg = "#fff5f5" if highlight else "#ffffff"
    border = "#c53030" if highlight else "#d0d7de"

    html = ""
    for a in articles:
        stars = "★" * a["score"] if a["score"] > 0 else "－"
        html += f"""
        <div style="margin-bottom:18px;padding:12px;
                    background:{bg};border-left:4px solid {border};">
          <div style="font-weight:bold;color:#1a365d;">{a['title']}</div>
          <div style="margin:6px 0;color:#333;">{a['summary']}</div>
          <div style="font-size:12px;color:#555;">
            重要度：{stars} ｜ Published：{a['published']}
          </div>
          <div style="font-size:12px;">
            <a href="{a['link']}" target="_blank" style="color:#1a73e8;">
              ▶ 元記事を読む
            </a>
          </div>
        </div>
        """
    return html

# =====================
# HTML生成
# =====================
def generate_html():
    body = """
    <html>
    <body style="font-family:'Meiryo UI','Segoe UI',sans-serif;
                 background:#f8fafc;padding:20px;">
    <div style="max-width:900px;margin:auto;background:#ffffff;padding:24px;">
      <h2 style="color:#0f2a44;">主要ニュース速報</h2>
      <p style="color:#555;">ニュースサマリ</p>
      <hr>
    """

    for media, (count, feeds) in MEDIA.items():
        entries = []
        for url in feeds:
            entries.extend(safe_parse(url))

        entries = sorted(
            entries,
            key=lambda e: e.published_parsed if hasattr(e, "published_parsed") else 0,
            reverse=True
        )[:count]

        top_articles, other_articles = [], []

        for e in entries:
            title = clean_html(e.get("title", ""))
            summary = simple_summary(e)
            score = importance_score(title + summary)

            article = {
                "title": title,
                "summary": summary,
                "score": score,
                "published": published_date(e),
                "link": e.get("link", "")
            }

            if score == 3:
                top_articles.append(article)
            else:
                other_articles.append(article)

        body += f"<h3>【{media}｜最新{len(entries)}件】</h3>"

        if top_articles:
            body += "<h4>★★★ 重要記事</h4>"
            body += render_articles(top_articles, highlight=True)

        body += render_articles(other_articles)
        body += "<hr>"

    body += "</div></body></html>"
    return body

# =====================
# メール送信（必ず実行）
# =====================
def send_mail(html):
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = f"主要ニュースまとめ｜{now_jst.strftime('%Y-%m-%d')}"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(MAIL_FROM, MAIL_PASSWORD)
        server.send_message(msg)

# =====================
# 実行
# =====================
if __name__ == "__main__":
    html = generate_html()
    send_mail(html)
