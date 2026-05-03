import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
CHECK_URL = os.getenv("NIKKEI_CHECK_URL", "https://www.nikkei.com/")

def main():
    if not STORAGE_PATH.exists():
        raise FileNotFoundError(
            f"{STORAGE_PATH} がありません。先に scripts/nikkei_login.py でログイン保存してください。"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_PATH),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=30000)

        body = page.locator("body").inner_text(timeout=10000)

        print("title:", page.title())
        print("url:", page.url)
        print("body_head:")
        print(body[:800])

        if "ワンタイムパスワード" in body or "ログイン" in body:
            print("WARNING: 未ログイン、または追加認証画面の可能性があります。")
        else:
            print("OK: ログイン済み状態で開けている可能性が高いです。")

        browser.close()

if __name__ == "__main__":
    main()
