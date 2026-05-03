from scripts.nikkei_fetch_articles_full import classify_articles


def _a(url='https://example.com/a', title='t'):
    return {'url': url, 'title': title}


def test_case_a_existing_no_body_backfill_enabled_included_in_fetch():
    arts=[_a()]
    keys={'https://example.com/a'}
    existing={'https://example.com/a': {'page_id':'p1','text':''}}
    targets, skipped, with_body, missing = classify_articles(arts, keys, existing, True, True)
    assert len(targets)==1
    assert skipped==[]
    assert len(missing)==1


def test_case_b_existing_with_body_skipped_and_saved_body_for_scoring():
    arts=[_a()]
    keys={'https://example.com/a'}
    existing={'https://example.com/a': {'page_id':'p1','text':'body'}}
    targets, skipped, with_body, missing = classify_articles(arts, keys, existing, True, True)
    assert targets==[]
    assert len(skipped)==1
    assert len(with_body)==1
    assert missing==[]


def test_case_c_existing_no_body_backfill_disabled_skipped():
    arts=[_a()]
    keys={'https://example.com/a'}
    existing={'https://example.com/a': {'page_id':'p1','text':''}}
    targets, skipped, with_body, missing = classify_articles(arts, keys, existing, True, False)
    assert targets==[]
    assert len(skipped)==1
    assert len(with_body)==1
    assert missing==[]


def test_case_d_new_article_is_fetched_normally():
    arts=[_a('https://example.com/new')]
    targets, skipped, with_body, missing = classify_articles(arts, set(), {}, True, True)
    assert len(targets)==1
    assert skipped==[]
    assert with_body==[]
    assert missing==[]
