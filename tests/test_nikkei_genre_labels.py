from __future__ import annotations

import pytest
from scripts.nikkei_score_articles import score_article


@pytest.mark.parametrize('source,page', [
    ('文化往来 新作映画を紹介', '新作映画を東京で紹介 - 日本経済新聞'),
    ('人事（10月1日）A社', 'A社の異動 - 日本経済新聞'),
    ('人事(10月1日)A社', 'A社の異動 - 日本経済新聞'),
])
def test_explicit_source_genre_survives_headline_variation(source, page):
    out = score_article({'source_title': source, 'page_title': page, 'text': '記事の本文。'}, [], 5)
    assert out['exclude_candidate'] is True


def test_body_suffix_still_does_not_establish_genre():
    out = score_article({'source_title': '新事業を発表 本文で文化往来に触れた。',
                         'page_title': '新事業を東京で発表 - 日本経済新聞',
                         'text': '本文で文化往来と人事に触れた。'}, [], 5)
    assert out['exclude_candidate'] is False
