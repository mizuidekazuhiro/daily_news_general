from src.stores.state_store import ProcessedArticleStore, make_dedupe_key


def test_duplicate_skip(tmp_path):
    path = tmp_path / 'processed.json'
    s = ProcessedArticleStore(str(path))
    k = make_dedupe_key('Nikkei', normalized_url='nikkei.com/article/x')
    assert not s.seen(k)
    s.mark(k, {'ok': True})
    s.save()
    s2 = ProcessedArticleStore(str(path))
    assert s2.seen(k)


def test_force_refresh(tmp_path):
    path = tmp_path / 'processed.json'
    s = ProcessedArticleStore(str(path))
    k = make_dedupe_key('Nikkei', article_id='ABC')
    s.mark(k, {'ok': True})
    assert not s.seen(k, force_refresh=True)
