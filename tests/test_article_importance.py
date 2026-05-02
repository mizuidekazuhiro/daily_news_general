from src.classifiers.article_importance import score_article, rank_important_articles


def test_positive_scoring():
    res = score_article('三井物産がインドでデータセンター投資', '企業')
    assert res.importance_score >= 30


def test_low_scoring_lifestyle():
    res = score_article('スポーツと芸能の話題')
    assert res.importance_score <= 5


def test_rank_order():
    rows = [
        {'title': 'スポーツ速報', 'section': '社会'},
        {'title': 'M&Aと為替の重要ニュース', 'section': '企業'},
    ]
    ranked = rank_important_articles(rows, limit=2)
    assert ranked[0]['importance_score'] >= ranked[1]['importance_score']
