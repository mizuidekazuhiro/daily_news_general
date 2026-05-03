from scripts.run_nikkei_paper_pipeline import should_fail_fetch


def test_target_zero_success_even_if_article_count_was_positive():
    assert should_fail_fetch(target_count=0, fetch_success_count=0, allow_empty_fetch=False) is False


def test_target_positive_zero_success_fails_when_empty_not_allowed():
    assert should_fail_fetch(target_count=1, fetch_success_count=0, allow_empty_fetch=False) is True


def test_target_positive_zero_success_passes_when_empty_allowed():
    assert should_fail_fetch(target_count=1, fetch_success_count=0, allow_empty_fetch=True) is False


def test_partial_success_continues():
    assert should_fail_fetch(target_count=5, fetch_success_count=3, allow_empty_fetch=False) is False


def test_not_based_on_article_count_only_anymore():
    # Equivalent to old article_count>0 case, but with no targets after skip.
    assert should_fail_fetch(target_count=0, fetch_success_count=0, allow_empty_fetch=False) is False
