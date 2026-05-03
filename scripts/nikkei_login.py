from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
LOGIN_URL = "https://www.nikkei.com/"

def main():
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo")
        page = context.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("ブラウザで日経に手動ログインしてください。")
        print("ログイン完了後、このターミナルに戻って Enter を押してください。")
        input("ログイン完了後に Enter: ")

        context.storage_state(path=str(STORAGE_PATH))
        print(f"保存しました: {STORAGE_PATH}")

        browser.close()

if __name__ == "__main__":
    main()
