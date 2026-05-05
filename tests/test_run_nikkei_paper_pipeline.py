from scripts.run_nikkei_paper_pipeline import decide_fetch_outcome


def test_target_zero_success_even_if_article_count_was_positive():
    decision, _ = decide_fetch_outcome(
        target_count=0,
        fetch_success_count=0,
        fetch_failed_count=0,
        existing_url_skip_count=58,
        allow_empty_fetch=False,
    )
    assert decision == "skip_no_targets"


def test_skip_existing_present_zero_success_continues():
    decision, _ = decide_fetch_outcome(
        target_count=1,
        fetch_success_count=0,
        fetch_failed_count=1,
        existing_url_skip_count=57,
        allow_empty_fetch=False,
    )
    assert decision == "continue"


def test_target_positive_zero_success_fails_when_empty_not_allowed_and_no_existing_skips():
    decision, _ = decide_fetch_outcome(
        target_count=58,
        fetch_success_count=0,
        fetch_failed_count=58,
        existing_url_skip_count=0,
        allow_empty_fetch=False,
    )
    assert decision == "fail"


def test_target_positive_zero_success_passes_when_empty_allowed():
    decision, _ = decide_fetch_outcome(
        target_count=58,
        fetch_success_count=0,
        fetch_failed_count=58,
        existing_url_skip_count=0,
        allow_empty_fetch=True,
    )
    assert decision == "continue"


def test_partial_success_continues():
    decision, _ = decide_fetch_outcome(
        target_count=5,
        fetch_success_count=3,
        fetch_failed_count=2,
        existing_url_skip_count=0,
        allow_empty_fetch=False,
    )
    assert decision == "continue"


def test_target_zero_is_skip_reason_no_targets():
    decision, reason = decide_fetch_outcome(
        target_count=0,
        fetch_success_count=0,
        fetch_failed_count=0,
        existing_url_skip_count=0,
        allow_empty_fetch=False,
    )
    assert decision == "skip_no_targets"
    assert "already exist" in reason
