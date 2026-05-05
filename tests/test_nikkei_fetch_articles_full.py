from scripts.nikkei_fetch_articles_full import (
    classify_empty_body_reason,
    is_probably_navigation_text,
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


def test_navigation_classifier_accepts_japanese_article_shape():
    text = "\n".join(
        [
            "日本企業の投資計画が拡大している。需要回復を見据えて工場の増設が進む。",
            "政府の統計でも設備投資の増勢が確認された。地域経済にも波及効果が広がる。",
            "市場関係者は慎重ながらも回復基調が続くとみる。企業収益の改善が背景にある。",
            "海外需要の持ち直しも追い風となる。輸出関連の企業で増産体制の準備が進む。",
        ]
    )
    assert is_probably_navigation_text(text) is False


def test_navigation_classifier_rejects_link_list_text():
    text = "\n".join(
        [
            "アクセスランキング",
            "市場ニュース一覧",
            "https://example.com/a",
            "https://example.com/b",
            "ログイン 会員登録 購読",
        ]
    )
    assert is_probably_navigation_text(text) is True
