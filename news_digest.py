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

# =====================
# 媒体設定（Google News RSS）
# =====================
MEDIA = {
    "日経新聞": (
        30,
        [
            "https://news.google.com/rss/search?q=日本経済新聞+経済&hl=ja&gl=JP&ceid=JP:ja",
            "https://news.google.com/rss/search?q=日本経済新聞+企業&hl=ja&gl=JP&ceid=JP:ja",
            "https://news.google.com/rss/search?q=日本経済新聞+金融&hl=ja&gl=JP&ceid=JP:ja",
            "https://news.google.com/rss/search?q=日本経済新聞+政策&hl=ja&gl=JP&ceid=JP:ja"
        ]
    ),
    "Bloomberg": (
        30,
        ["https://news.google.com/rss/search?q=Bloomberg&hl=ja&gl=JP&ceid=JP:ja"]
    ),
    "Reuters": (
        30,
        ["https://news.google.com/rss/search?q=Reuters&hl=ja&gl=JP&ceid=JP:ja"]
    ),
    "Fastmarkets": (  # ★追加
        30,
        ["https://news.google.com/rss/search?q=Fastmarkets+steel+metal+raw+materials&hl=en&gl=US&ceid=US:en"]
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
    "鉄鋼": [
        "steel", "iron", "scrap", "rebar", "hbi", "dri",
        "製鉄", "鉄鋼", "高炉", "電炉", "スクラップ",
        "automotive", "construction", "infrastructure",
        "橋梁", "建材"
    ],
    "建設": [
        "construction", "infrastructure", "real estate",
        "housing", "property", "developer",
        "建設", "土木", "不動産", "住宅", "再開発",
        "公共事業", "インフラ"
    ],
    "AI": [
        "ai", "artificial intelligence", "machine learning",
        "semiconductor", "chip", "gpu", "data center",
        "生成ai", "半導体", "データセンター",
        "nvidia", "openai"
    ],
    "銀行": [
        "bank", "banking", "lender", "financial institution",
        "融資", "貸出", "銀行", "金融機関",
        "credit", "loan", "default"
    ],
    "政治": [
        "government", "policy", "administration",
        "政権", "政策", "法案", "規制",
        "election", "geopolitical", "military", "conflict"
    ],
    "企業": [
        "company", "corp", "firm",
        "決算", "業績", "m&a", "acquisition",
        "investment", "joint venture", "subsidiary"
    ],
    "金融": [
        "market", "interest", "rate", "yield",
        "fx", "currency", "bond", "equity",
        "金融", "金利", "市場", "株式", "債券"
    ],
    "通商": [
        "trade", "tariff", "sanction",
        "export", "import", "quota",
        "輸出", "輸入", "関税", "制裁"
    ],
    "重点国": [
        "india", "indian", "インド",
        "vietnam", "ベトナム", "viet"
    ]
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

# =====================
# RSS安全取得
# =====================
def safe_parse(url):
    try:
        return feedparser.parse(url).entries
    except Exception:
        return []

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
            <a href="{a['link']}" target="_blank">▶ 元記事を読む</a>
          </div>
        </div>
        """
    return html

# =====================
# HTML生成
# =====================
def generate_html():
    body = """
    <html><body style="font-family:'Meiryo UI','Segoe UI',sans-serif;">
    <div style="max-width:900px;margin:auto;">
    <h2>主要ニュース速報</h2>
    <p>ニュースサマリ</p><hr>
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

        articles = []
        for e in entries:
            title = clean_html(e.get("title", ""))
            summary = simple_summary(e)
            score = importance_score(title + summary)

            articles.append({
                "title": title,
                "summary": summary,
                "score": score,
                "published": published_date(e),
                "link": e.get("link", "")
            })

        # 重要度 → 新しさ順
        articles = sorted(
            articles,
            key=lambda x: (x["score"], x["published"]),
            reverse=True
        )

        body += f"<h3>【{media}｜最新{len(articles)}件】</h3>"
        body += render_articles([a for a in articles if a["score"] == 3], highlight=True)
        body += render_articles([a for a in articles if a["score"] < 3])
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