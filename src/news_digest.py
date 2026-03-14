"""CLIエントリーポイント。

- mainジョブ: 既存の主要ニュース配信
- specialジョブ: 専門紙記事一覧配信（JST前日分）
- allジョブ: main → special の順で連続実行
"""

import argparse
import logging
import socket

from src.config.job_config import JOB_ALL, JOB_CHOICES, JOB_MAIN, JOB_SPECIAL
from src.jobs.main_news_job import run_main_news_delivery
from src.jobs.special_news_job import run_special_news_delivery

socket.setdefaulttimeout(10)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=JOB_CHOICES, default=JOB_MAIN)
    return parser.parse_args()


def main():
    args = parse_args()

    if args.job == JOB_MAIN:
        run_main_news_delivery()
    elif args.job == JOB_SPECIAL:
        run_special_news_delivery()
    elif args.job == JOB_ALL:
        # all は main を先に実行し、その後 special を実行する。
        run_main_news_delivery()
        run_special_news_delivery()


if __name__ == "__main__":
    main()
