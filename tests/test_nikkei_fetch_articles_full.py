from scripts.nikkei_fetch_articles_full import (
    classify_empty_body_reason,
    is_paper_index_title,
    should_stop_attempting,
    validate_article_body,
)


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


def test_validate_rejects_navigation_text():
    nav_text = "アクセスランキング\nトピック一覧\n速報\nおくやみ\nプレスリリース"
    ok, reason = validate_article_body(nav_text, page_title="経済", source_title="テスト記事")
    assert ok is False
    assert reason in {"navigation_like_text", "too_short"}


def test_validate_rejects_paper_index_title():
    assert is_paper_index_title("朝刊・夕刊 5月4日（月）付")
    ok, reason = validate_article_body("これは十分な長さ。文章です。" * 20, page_title="朝刊・夕刊 5月4日（月）付", source_title="個別記事")
    assert ok is False
    assert reason == "paper_index_page_title"


def test_validate_accepts_normal_article_text():
    text = "\n".join([f"日本企業が設備投資を拡大する。景気回復への期待が高まっている。第{i}段落。" for i in range(1, 12)])
    ok, reason = validate_article_body(text, page_title="日本企業、設備投資を拡大", source_title="日本企業、設備投資を拡大")
    assert ok is True
    assert reason == ""
