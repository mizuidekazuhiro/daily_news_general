from scripts.nikkei_fetch_articles_full import classify_empty_body_reason, should_stop_attempting


def test_empty_body_reason_selector_failed_when_page_text_exists():
    reason = classify_empty_body_reason('', 'x' * 500, False, [{'selector': 'article', 'text_length': 0}], False)
    assert reason == 'empty_body_page_text_present_but_selector_failed'


def test_empty_body_reason_resource_block_suspected():
    reason = classify_empty_body_reason('', '', False, [{'selector': 'article', 'text_length': 0}], True)
    assert reason == 'empty_body_resource_block_suspected'


def test_max_article_attempts_limit():
    assert should_stop_attempting(3, 3) is True
    assert should_stop_attempting(2, 3) is False
    assert should_stop_attempting(999, 0) is False
