import json
import os
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

load_dotenv()

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
OUTPUT_DIR = Path("logs")
OUTPUT_DIR.mkdir(exist_ok=True)

MORNING_URL = os.getenv("NIKKEI_MORNING_URL", "").strip()


def is_login_or_challenge_page(text: str, url: str) -> bool:
    markers = [
        "ログイン",
        "ワンタイムパスワード",
        "NIKKEI ID",
        "メールアドレス",
        "パスワード",
    ]
    if "id.nikkei.com" in url:
        return True
    return any(m in text for m in markers)


def collect_links_with_retry(page, base_url: str, retries: int = 5):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            try:
                page.wait_for_load_state("load", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            # 日経側のJS遷移・広告読み込み・リダイレクトを待つ
            page.wait_for_timeout(1500 * attempt)

            links = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a')).map(a => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href || ''
                })).filter(x => x.text && x.href)
                """
            )

            normalized = []
            seen = set()
            for x in links:
                href = urljoin(base_url, x["href"])
                text = " ".join(x["text"].split())

                if not href or not text:
                    continue
                if href in seen:
                    continue

                seen.add(href)
                normalized.append({"title": text, "url": href})

            return normalized

        except PlaywrightError as e:
            last_error = e
            msg = str(e)
            if "Execution context was destroyed" in msg or "navigation" in msg:
                print(f"retry {attempt}/{retries}: navigation中のため再試行します")
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(2000)
                continue
            raise

    raise RuntimeError(f"リンク取得に失敗しました: {last_error}")


def main():
    if not MORNING_URL or "ここに" in MORNING_URL:
        raise RuntimeError(".env の NIKKEI_MORNING_URL に日経朝刊ページURLを設定してください。")

    if not STORAGE_PATH.exists():
        raise FileNotFoundError(f"{STORAGE_PATH} がありません。先にログイン保存してください。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_PATH),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )

        page = context.new_page()
        page.set_default_timeout(20000)

        print("open:", MORNING_URL)
        page.goto(MORNING_URL, wait_until="domcontentloaded", timeout=45000)

        # 初期遷移後の状態確認
        try:
            page.wait_for_load_state("load", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(3000)

        current_url = page.url
        title = page.title()
        body_text = page.locator("body").inner_text(timeout=15000)

        print("page_title:", title)
        print("page_url:", current_url)
        print("body_head:")
        print(body_text[:800])

        # デバッグ用にHTMLとスクショを保存
        (OUTPUT_DIR / "nikkei_morning_page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUTPUT_DIR / "nikkei_morning_page.png"), full_page=True)

        if is_login_or_challenge_page(body_text, current_url):
            print("WARNING: ログイン画面または追加認証画面に遷移しています。")
            print("noVNCで再ログインし、storage_state を保存し直してください。")
            browser.close()
            return

        all_links = collect_links_with_retry(page, MORNING_URL)

        all_out = OUTPUT_DIR / "nikkei_all_links.json"
        all_out.write_text(json.dumps(all_links, ensure_ascii=False, indent=2), encoding="utf-8")

        candidates = []
        seen = set()

        for item in all_links:
            href = item["url"]
            text = item["title"]

            if "nikkei.com" not in href:
                continue
            if href in seen:
                continue

            seen.add(href)

            # まずは広めに抽出。実URL構造を見て後で絞る。
            if (
                "/article/" in href
                or "/news/" in href
                or "/paper/" in href
                or "/nkd/" in href
                or "/prime/" in href
            ):
                candidates.append({"title": text, "url": href})

        out = OUTPUT_DIR / "nikkei_link_candidates.json"
        out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

        print("all_link_count:", len(all_links))
        print("candidate_count:", len(candidates))
        print("saved_all:", all_out)
        print("saved_candidates:", out)
        print("screenshot:", OUTPUT_DIR / "nikkei_morning_page.png")

        print("\n--- candidates head ---")
        for i, item in enumerate(candidates[:80], 1):
            print(f"{i}. {item['title']} | {item['url']}")

        if not candidates:
            print("\nWARNING: 候補リンクが0件です。all_linksの先頭を表示します。")
            for i, item in enumerate(all_links[:80], 1):
                print(f"{i}. {item['title']} | {item['url']}")

        browser.close()


if __name__ == "__main__":
    main()
