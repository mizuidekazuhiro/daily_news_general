from html import escape
from typing import Dict, List


def render_nikkei_mail_html(run_date: str, morning_summary: str, important_articles: List[Dict], section_links: Dict[str, List[Dict]], logs: Dict) -> str:
    important_html = "".join(
        f"<li><a href='{escape(a['url'])}'>{escape(a['title'])}</a>"
        f"<div>重要度: {a.get('importance_score', 0)} / セクション: {escape(a.get('section','不明'))}</div></li>"
        for a in important_articles
    )
    sections = []
    for sec, items in section_links.items():
        links = "".join(f"<li><a href='{escape(i['url'])}'>{escape(i['title'])}</a></li>" for i in items)
        sections.append(f"<h4>{escape(sec)}</h4><ul>{links}</ul>")
    return f"""
    <html><body style='font-family: Meiryo UI, Meiryo, sans-serif;'>
      <h2>【朝刊サマリー】日経新聞・主要ニュース {escape(run_date)}</h2>
      <h3>朝のサマリー</h3><p>{escape(morning_summary)}</p>
      <h3>重要記事</h3><ol>{important_html}</ol>
      <h3>セクション別 全記事リンク</h3>{''.join(sections)}
      <h3>処理ログ</h3><pre>{escape(str(logs))}</pre>
    </body></html>
    """
