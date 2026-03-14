"""main配信のHTML描画責務。

main配信の見た目を変更したい場合はこのモジュールを起点に読む。
"""


def build_main_news_subject(date_text: str) -> str:
    return f"主要ニュースまとめ｜{date_text}"
