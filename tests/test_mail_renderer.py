from src.outputs.mail_sender import render_nikkei_mail_html


def test_mail_contains_links_and_sections():
    html = render_nikkei_mail_html(
        '2026-05-02',
        '要約',
        [{'title': '記事1', 'url': 'https://www.nikkei.com/a', 'section': '企業', 'importance_score': 90}],
        {'企業': [{'title': '記事1', 'url': 'https://www.nikkei.com/a'}]},
        {'count': 1},
    )
    assert "<a href='https://www.nikkei.com/a'>記事1</a>" in html
    assert 'セクション別 全記事リンク' in html
    assert '本文全文' not in html
