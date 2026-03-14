"""ジョブ別の設定を集約するモジュール。

main 配信 / special 配信で使う環境変数を明示し、
初心者でも「どの値がどのジョブ用か」を追えるようにしています。
"""

import os

# 共通メール設定（SMTP）
MAIL_FROM = os.getenv("MAIL_FROM", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# main 配信用
MAIN_NEWS_MAIL_TO = os.getenv("MAIL_TO", "")

# special 配信用
SPECIAL_NEWS_MAIL_TO = os.getenv("SPECIAL_NEWS_MAIL_TO", "")
SPECIAL_NEWS_MAIL_CC = os.getenv("SPECIAL_NEWS_MAIL_CC", "")
SPECIAL_NEWS_MAIL_BCC = os.getenv("SPECIAL_NEWS_MAIL_BCC", "")
SPECIAL_NEWS_MAIL_SUBJECT_PREFIX = os.getenv("SPECIAL_NEWS_MAIL_SUBJECT_PREFIX", "【専門紙記事一覧】")
SPECIAL_NEWS_CONFIG_PATH = os.getenv("SPECIAL_NEWS_CONFIG_PATH", os.path.join("config", "special_news_media.json"))
SPECIAL_NEWS_TEMPLATE_PATH = os.getenv("SPECIAL_NEWS_TEMPLATE_PATH", os.path.join("templates", "special_news_email.html"))
SPECIAL_NEWS_MAX_ITEMS_TOTAL = int(os.getenv("SPECIAL_NEWS_MAX_ITEMS_TOTAL", "50"))
SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA = int(os.getenv("SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA", "20"))

# Notion（special 配信の設定読み取り用）
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_SPECIAL_NEWS_DB_ID = os.getenv("NOTION_SPECIAL_NEWS_DB_ID", "")
NOTION_SPECIAL_NEWS_ENABLED = os.getenv("NOTION_SPECIAL_NEWS_ENABLED", "true").lower() == "true"

# 翻訳（main 配信）
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06")
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "512"))
OPENAI_TRANSLATION_BATCH_SIZE = int(os.getenv("OPENAI_TRANSLATION_BATCH_SIZE", "30"))
TITLE_TRANSLATION_CACHE_PATH = os.getenv("TITLE_TRANSLATION_CACHE_PATH", os.path.join("data", "title_translation_cache.json"))
OPENAI_RESPONSE_TRUNCATE_CHARS = int(os.getenv("OPENAI_RESPONSE_TRUNCATE_CHARS", "500"))

JOB_MAIN = "main"
JOB_SPECIAL = "special"
JOB_ALL = "all"
JOB_CHOICES = [JOB_MAIN, JOB_SPECIAL, JOB_ALL]
