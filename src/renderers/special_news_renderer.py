from html import escape
from pathlib import Path

from src.config.job_config import SPECIAL_NEWS_MAIL_SUBJECT_PREFIX, SPECIAL_NEWS_TEMPLATE_PATH


def render_special_news_html(target_date, media_results, total_items):
    template = Path(SPECIAL_NEWS_TEMPLATE_PATH).read_text(encoding="utf-8")
    section_html = []
    for media in media_results:
        items = media["items"]
        lis = "".join(
            f'<li><a href="{escape(i["link"])}">{escape(i["title"])}</a><span class="meta">（{escape(i["published"])}）</span></li>'
            for i in items
        )
        if not lis:
            lis = "<li>対象記事はありませんでした。</li>"
        section_html.append(
            f"<section><h3>{escape(media['media_name'])}</h3><p class='count'>件数: {len(items)}件</p><ol>{lis}</ol></section>"
        )

    if not section_html:
        section_html.append("<section><h3>対象媒体</h3><p>対象記事はありませんでした。</p></section>")

    return template.format(
        target_date=target_date.strftime("%Y-%m-%d"),
        total_items=total_items,
        media_sections="\n".join(section_html),
    )


def build_special_news_subject(target_date, media_results):
    media_names = "・".join(m["media_name"] for m in media_results)
    return f"{SPECIAL_NEWS_MAIL_SUBJECT_PREFIX}{target_date.strftime('%Y-%m-%d')}更新分（{media_names}）"
