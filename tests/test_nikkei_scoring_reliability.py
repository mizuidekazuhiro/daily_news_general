from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.nikkei_scoring_inputs import clean_headline, headline_for_article, article_type_exclusion
import scripts.nikkei_score_articles as scoring
import scripts.nikkei_save_articles_to_notion as saving


def rule(keyword='日本', weight=0, field='title'):
    return {'tag_name': 'Japan', 'rule_type': 'country', 'keywords': [keyword],
            'negative_keywords': [], 'weight': weight, 'priority': 100, 'match_field': field}


def test_publisher_not_japan_evidence():
    article = {'page_title': '海外企業が工場を建設 - 日本経済新聞',
               'source_title': '海外企業が工場を建設', 'text': '海外の工場を建設する。'}
    out = scoring.score_article(article, [rule()], 5)
    assert out['tags'] == [] and out['priority'] == 0
    assert out['page_title'] == '海外企業が工場を建設'
    assert out['raw_page_title'] == article['page_title']


def test_real_japan_reference_is_preserved():
    title = '日本への投資を拡大 - 日本経済新聞'
    assert clean_headline(title) == '日本への投資を拡大'
    out = scoring.score_article({'page_title': title, 'text': ''}, [rule()], 5)
    assert out['tags'] == ['Japan'] and out['priority'] == 100
    assert not out['body_used_for_scoring']
    assert clean_headline('日本経済新聞社が新事業 - 日本経済新聞') == '日本経済新聞社が新事業'


def test_dirty_source_card_not_used_as_title_or_saved_title():
    article = {'page_title': '海外企業、事業を拡大 - 日本経済新聞',
               'source_title': '海外企業、事業を拡大 本文にのみ設備投資が登場する。',
               'text': '本文にのみ設備投資が登場する。'}
    out = scoring.score_article(article, [rule('設備投資', 5)], 5)
    assert out['importance_score'] == 0
    assert saving.ensure_nikkei_title(article) == '海外企業、事業を拡大'
    body_score = scoring.score_article(article, [rule('設備投資', 5, 'body')], 5)
    assert body_score['importance_score'] == 5


def test_legacy_source_title_trims_only_matching_body():
    body = 'この文章は本来見出しではなく記事の冒頭本文にあたる。'
    article = {'page_title': '日本経済新聞', 'source_title': '工場を建設 ' + body, 'text': body}
    assert headline_for_article(article) == ('工場を建設', 'source_title')
    article['text'] = '一致していない別の本文である。'
    assert headline_for_article(article)[0] == '工場を建設 ' + body


@pytest.mark.parametrize('headline,body', [
    ('放送会社の買収と編集独立性', '人事の方針を表明した。コラムニストも取材した。'),
    ('企業文化を改革', '企業の文化と人事を改革する。'),
    ('人事改革で競争力向上', '人事制度の新戦略を発表した。'),
    ('スポーツ用品メーカーが買収', 'スポーツ用品の事業に投資する。'),
    ('コラムニスト向け新サービス', '文化事業の拡大を発表した。'),
])
def test_incidental_keywords_do_not_exclude_business_article(headline, body):
    out = scoring.score_article({'page_title': headline + ' - 日本経済新聞',
                                 'source_title': headline + ' ' + body, 'text': body}, [], 5)
    assert not out['exclude_candidate']


@pytest.mark.parametrize('headline', ['（人事）企業A', '人事、企業B', '【訃報】氏名',
                                      '文化 新作を紹介', '連載小説 第十回', 'コラム：日々のこと'])
def test_explicit_article_type_labels_still_excluded(headline):
    assert article_type_exclusion(headline)[0]


def test_threshold_five_max_five_no_ties_unchanged():
    items = [{'url': str(i), 'importance_score': score, 'priority': 0}
             for i, score in enumerate([0, 4.99, 5, 5, 5, 5, 5, 5, 8, 10])]
    chosen, _, _ = scoring.select_report_articles(items, 'top_importance_rank', 5, 5, False)
    assert len(chosen) == 5
    assert [item['importance_score'] for item in chosen] == [10, 8, 5, 5, 5]
    assert scoring.select_report_articles(items[:2], 'top_importance_rank', 5, 5, False)[0] == []


def setup_scoring(monkeypatch, tmp_path, rules):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/nikkei_articles_full.json').write_text(json.dumps([
        {'page_title': '海外企業のニュース - 日本経済新聞', 'source_title': '海外企業のニュース',
         'url': 'https://example.com/article/1', 'text': '本文を取得している。', 'text_length': 11}
    ]), encoding='utf-8')
    monkeypatch.setenv('NIKKEI_ENABLE_SCORING', 'true')
    monkeypatch.setenv('NOTION_RULES_DB_ID', 'test-not-real')
    monkeypatch.setenv('NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT', '5')
    monkeypatch.setenv('NIKKEI_REPORT_TOP_IMPORTANCE_RANK', '5')
    monkeypatch.setenv('NIKKEI_REPORT_INCLUDE_TIES', 'false')
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path / 'step-summary.md'))
    monkeypatch.setattr(scoring, 'load_rules', lambda *args: rules)


def test_valid_no_qualifying_articles_normal_success_with_rule_snapshot(monkeypatch, tmp_path, capsys):
    rules = [rule()]
    setup_scoring(monkeypatch, tmp_path, rules)
    assert scoring.main() == 0
    summary = json.loads(scoring.SUMMARY_JSON.read_text())
    snapshot = json.loads((tmp_path / 'logs/nikkei_rules_snapshot.json').read_text())
    assert summary['report_state'] == 'no_eligible_articles'
    assert summary['report_selected_count'] == 0
    assert summary['report_min_importance_score'] == 5
    assert snapshot['rules'] == rules
    assert snapshot['rules_fingerprint'] == summary['rules_fingerprint']
    assert 'no_eligible_articles' in (tmp_path / 'step-summary.md').read_text()
    assert 'Rules may not be matching' not in capsys.readouterr().out


@pytest.mark.parametrize('rules', [[], [{'keywords': []}], [rule(weight=float('nan'))], [rule(weight=float('inf'))]])
def test_invalid_rules_are_not_normal_no_news(monkeypatch, tmp_path, rules):
    setup_scoring(monkeypatch, tmp_path, rules)
    with pytest.raises(RuntimeError, match='nikkei_scoring_rules_invalid'):
        scoring.main()
    assert not scoring.OUTPUT_JSON.exists()


def test_no_eligible_articles_does_not_send_report(monkeypatch, tmp_path):
    import scripts.run_nikkei_final_report as final
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/nikkei_articles_scored.json').write_text(json.dumps([
        {'source_title': 'test', 'url': 'https://example.com/1', 'importance_score': 4,
         'included_in_report': False, 'text': 'test'}
    ]))
    monkeypatch.setenv('NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT', '5')
    monkeypatch.setenv('NIKKEI_SEND_FINAL_REPORT_MAIL', 'true')
    monkeypatch.setenv('NIKKEI_ENABLE_FINAL_REPORT_GPT', 'true')
    # No API construction or SMTP connection may occur on a normal no-report day.
    def forbidden(*args, **kwargs):
        raise AssertionError('no qualifying articles must not trigger an external effect')
    monkeypatch.setattr(final, 'OpenAIJsonClient', forbidden)
    monkeypatch.setattr(final.smtplib, 'SMTP', forbidden)
    monkeypatch.setenv('MAIL_TO', 'test@example.com')
    assert final.main() == 0
    summary = json.loads((tmp_path / 'logs/nikkei_final_report_summary.json').read_text())
    assert summary['mail_sent'] is False
    assert summary['final_report_skip_reason'] == 'no_articles_meet_min_importance_score'


def test_script_import_path_works_from_scripts_directory(tmp_path):
    env = dict(os.environ, NIKKEI_ENABLE_SCORING='false')
    script = Path(scoring.__file__).resolve()
    completed = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env,
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_explicit_issue_card_genre_prefix_is_preserved():
    article = {"page_title": "新作映画を紹介 - 日本経済新聞",
               "source_title": "文化往来 新作映画を紹介 本文の冒頭である。", "text": "本文の冒頭である。"}
    assert scoring.score_article(article, [], 5)["exclude_reason"] == "文化"
    article["source_title"] = "新作映画を紹介 本文で文化往来について言及する。"
    assert not scoring.score_article(article, [], 5)["exclude_candidate"]
