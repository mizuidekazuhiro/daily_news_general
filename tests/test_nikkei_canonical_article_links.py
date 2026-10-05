import json
from pathlib import Path

import pytest

from scripts import nikkei_extract_issue_links as extract
from scripts import nikkei_fetch_articles_full as fetch
from scripts import nikkei_save_articles_to_notion as save
from scripts import nikkei_score_articles as score

ARTICLE_ID = 'DGXZQOCD294ZZ0Z20C26A7000000'
NEW = f'https://www.nikkei.com/paper/20261006M101/MM80000/article/{ARTICLE_ID}'
OLD = f'https://www.nikkei.com/paper/article/?b=20261006&ng={ARTICLE_ID}'


def test_failed_run_urls_are_extracted_with_issue_metadata(monkeypatch):
    # Exact URLs from run 37386971012 artifact 11379401429; no article text stored.
    urls = json.loads((Path(__file__).parent / 'fixtures/nikkei_20261006_article_urls.json').read_text())
    monkeypatch.setattr(extract, 'ENABLE_PRE', False)
    links = [{'url': url, 'title': f'Fixture headline {i}'} for i, url in enumerate(urls)]
    articles, excluded, raw = extract.extract_issue_articles(
        links, 'https://www.nikkei.com/paper/20261006M101', '20261006', 'morning')
    assert raw == len(articles) == 172
    assert excluded == []
    assert all(a['issue_date'] == '20261006' and a['edition'] == 'morning' for a in articles)
    assert len({score.extract_article_id(a['url']) for a in articles}) == 172


@pytest.mark.parametrize('url', [NEW, NEW + '/', NEW + '?n_cid=test', OLD,
                                f'https://www.nikkei.com/article/{ARTICLE_ID}/'])
def test_identity_survives_fetch_save_and_score(url):
    assert extract.article_id(url) == ARTICLE_ID
    assert fetch.extract_nikkei_ng_id(url) == ARTICLE_ID
    assert fetch.normalize_nikkei_article_key(url) == f'ng:{ARTICLE_ID}'
    assert save.ng(url) == ARTICLE_ID
    assert score.extract_article_id(url) == ARTICLE_ID


def test_legacy_and_new_links_dedupe_before_fetch(monkeypatch):
    monkeypatch.setattr(extract, 'ENABLE_PRE', False)
    links = [{'url': url, 'title': 'Test headline'} for url in [NEW, OLD, NEW + '/']]
    articles, excluded, raw = extract.extract_issue_articles(links, '', '20261006', 'morning')
    assert raw == 3
    assert len(articles) == 1
    assert articles[0]['url'] == NEW
    assert excluded == []
    keys = {fetch.normalize_nikkei_article_key(OLD)}
    existing_map = {OLD: {'page_id': 'existing', 'text': 'Existing body'}}
    pending, skipped, existing, backfill = fetch.classify_articles(
        articles, keys, existing_map, True, True)
    assert pending == []
    assert len(skipped) == 1
    assert len(existing) == 1
    assert backfill == []
    assert existing_map[NEW]['page_id'] == 'existing'


@pytest.mark.parametrize('url', [
    NEW.replace('20261006', '20261005'),
    NEW.replace('M101', 'E101'),
    NEW.replace('M101', 'M999'),
    NEW.replace('www.nikkei.com', 'www.nikkei.com.example.com'),
    f'https://www.nikkei.com/article/{ARTICLE_ID}/',
    'https://www.nikkei.com/paper/20261006M101',
])
def test_non_issue_links_rejected(url):
    assert not extract.is_article(url, '20261006', 'morning')


@pytest.mark.parametrize('issue_id', ['E101', 'M201'])
def test_evening_article_links(issue_id):
    url = NEW.replace('M101', issue_id)
    assert extract.is_article(url, '20261006', 'evening')
    assert not extract.is_article(url, '20261006', 'morning')
