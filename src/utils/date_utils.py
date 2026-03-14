from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


def previous_day_jst() -> datetime:
    """special配信では「前日更新分」を採用するため、この日付を基準にする。"""
    return now_jst() - timedelta(days=1)
