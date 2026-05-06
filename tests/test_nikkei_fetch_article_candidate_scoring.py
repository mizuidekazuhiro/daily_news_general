from scripts.nikkei_fetch_articles_full import score_body_candidate, title_tokens_for_match, normalize_title_for_match
from src.final_report_synthesis import _clip

def test_scoring_basics():
    c=score_body_candidate('半導体投資', {'selector':'article','text':'半導体投資が進む。企業は増産へ。'})
    assert 'body_candidate_score' in c

def test_title_helpers_defined():
    assert normalize_title_for_match('ＡＢＣ  日本経済新聞')
    assert isinstance(title_tokens_for_match('半導体投資計画'), list)

def test_scoring_handles_empty_and_missing_keys():
    c1=score_body_candidate('', {'text':''})
    c2=score_body_candidate('', {})
    assert c1['body_candidate_score'] <= 0
    assert c2['body_candidate_score'] <= 0

def test_clip_zero_is_full():
    assert _clip('abcdef',0)=='abcdef'
