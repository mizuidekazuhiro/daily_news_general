"""互換レイヤー。

既存の `python news_digest.py` 呼び出しや既存テストのimport互換を維持するため、
新しい `src` 配下の実装を再公開する。
"""

from src.jobs.main_news_job import normalize_translations_payload, parse_translation_response
from src.jobs.special_news_job import extract_entries_for_target_date
from src.news_digest import main
from src.renderers.special_news_renderer import build_special_news_subject
from src.utils.mail_utils import parse_mail_recipients


if __name__ == "__main__":
    main()
