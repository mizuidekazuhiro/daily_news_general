import feedparser
import smtplib
import re
import os
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

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
# 媒体設定（最新30件）
# =====================
MEDIA = {
    "日経新聞": (30, ["https://www.nikkei.com/rss/search"]),
    "Bloomberg": (30, ["https://www.bloomberg.com/feed"]),
    "Reuters": (30, ["https://www.reuters.com/rssFeed/topNews"]),
    "S&P Global": (30, ["https://www.spglobal.com/commodityinsights/en/rss-feed"]),
    "東洋経済": (30, ["https://toyokeizai.net/list/feed/rss"]),
    "日経ビジネス": (30, ["https://business.nikkei.com/rss"])
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
# 週次振り返り生成
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
# HTML全体生成
# =====================
def generate_html():
    body = """
    <html>
    <body style="font-family:'Meiryo UI','Segoe UI',sans-serif;
                 background:#f8fafc;padding:20px;">
    <div style="max-width:900px;margin:auto;background:#ffffff;padding:24px;">
      <h2 style="color:#0f2a44;">主要ニュース速報（業務判断用）</h2>
      <p style="color:#555;">
        RSSベース最新30件取得／重要度★★★を最上段表示<br>
        ニュースサマリ
      </p>
      <hr style="border:1px solid #e2e8f0;">
    """

    if IS_MONDAY:
        body += """
        <div style="background:#f1f5f9;border-left:6px solid #0f2a44;
                    padding:16px;margin-bottom:24px;">
          <h3 style="margin-top:0;color:#0f2a44;">📊 先週1週間の振り返り</h3>
        """

    for media, (count, feeds) in MEDIA.items():
        entries = []
        for url in feeds:
            entries.extend(feedparser.parse(url).entries)

        entries = sorted(
            entries,
            key=lambda e: e.published_parsed if hasattr(e, "published_parsed") else 0,
            reverse=True
        )[:count]

        if IS_MONDAY:
            total, important, titles = weekly_review(entries)
            if total > 0:
                body += f"""
                <div style="margin-bottom:12px;">
                  <strong>{media}</strong><br>
                  ・掲載記事数：{total}件<br>
                  ・重要記事（★★★）：{important}件
                """
                if titles:
                    body += "<br>・主なトピック："
                    for t in titles:
                        body += f"<br>　- {t}"
                body += "</div>"

    if IS_MONDAY:
        body += "</div><hr style='border:1px solid #e2e8f0;'>"

    for media, (count, feeds) in MEDIA.items():
        entries = []
        for url in feeds:
            entries.extend(feedparser.parse(url).entries)

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

        body += f"<h3 style='color:#1a365d;'>【{media}｜最新{len(entries)}件】</h3>"

        if top_articles:
            body += "<h4 style='color:#c53030;'>★★★ 重要記事</h4>"
            body += render_articles(top_articles, highlight=True)

        body += "<h4 style='color:#4a5568;'>その他の記事</h4>"
        body += render_articles(other_articles)
        body += "<hr style='border:1px solid #edf2f7;'>"

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

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(MAIL_FROM, MAIL_PASSWORD)
        server.send_message(msg)

# =====================
# 実行
# =====================
if __name__ == "__main__":
    send_mail(generate_html())
