from src.sources.nikkei_parser import normalize_nikkei_url, extract_article_id, make_body_excerpt, classify_section_from_text


def test_url_normalize():
    assert normalize_nikkei_url('HTTPS://www.nikkei.com/article/ABCD1234/?n_cid=xx') == 'https://www.nikkei.com/article/ABCD1234'


def test_extract_article_id():
    assert extract_article_id('https://www.nikkei.com/article/DGXZQOUC123ABC/') == 'DGXZQOUC123ABC'


def test_excerpt():
    text = 'a' * 300
    assert make_body_excerpt(text, 20).endswith('…')


def test_section_classify():
    assert classify_section_from_text('日銀と為替の動向') == '金融'
