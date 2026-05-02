import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
OUTPUT_DIR = Path("logs")
OUTPUT_DIR.mkdir(exist_ok=True)

ENTRY_URL = os.getenv("NIKKEI_MORNING_URL", "https://www.nikkei.com/paper/").strip()
EDITION = os.getenv("NIKKEI_EDITION", "morning").strip()  # morning / evening


def wait_page(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(2500)


def collect_links(page, base_url: str, retries: int = 5):
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

            # 日経側のJS遷移・表示切替を待つ
            page.wait_for_timeout(1500 * attempt)

            links = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a')).map(a => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href || ''
                })).filter(x => x.text && x.href)
                """
            )

            out = []
            seen = set()

            for x in links:
                href = urljoin(base_url, x["href"])
                text = " ".join(x["text"].split())

                if not href or not text:
                    continue
                if href in seen:
                    continue

                seen.add(href)
                out.append({"title": text, "url": href})

            return out

        except Exception as e:
            last_error = e
            msg = str(e)

            if "Execution context was destroyed" in msg or "navigation" in msg:
                print(f"retry collect_links {attempt}/{retries}: navigation中のため再試行します")
                page.wait_for_timeout(2500)
                continue

            raise

    raise RuntimeError(f"collect_links failed after retries: {last_error}")


def find_issue_url(links):
    target_path = f"/paper/{EDITION}/"

    for item in links:
        if target_path in item["url"]:
            return item["url"]

    return None


def is_paper_article_url(url: str) -> bool:
    parsed = urlparse(url)

    if "nikkei.com" not in parsed.netloc:
        return False

    if parsed.path != "/paper/article/":
        return False

    qs = parse_qs(parsed.query)

    # ng は記事ID、b は紙面日付
    if "ng" not in qs:
        return False

    return True


def extract_issue_date(url: str):
    qs = parse_qs(urlparse(url).query)
    vals = qs.get("b") or []
    return vals[0] if vals else ""


def main():
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

        print("open entry:", ENTRY_URL)
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45000)
        wait_page(page)

        entry_links = collect_links(page, ENTRY_URL)
        issue_url = find_issue_url(entry_links)

        if not issue_url:
            print("ERROR: 朝刊/夕刊の紙面URLが見つかりませんでした。")
            print("entry_title:", page.title())
            print("entry_url:", page.url)
            print("entry_links_head:")
            for i, item in enumerate(entry_links[:80], 1):
                print(f"{i}. {item['title']} | {item['url']}")
            browser.close()
            return

        print("issue_url:", issue_url)

        page.goto(issue_url, wait_until="domcontentloaded", timeout=45000)
        wait_page(page)

        issue_title = page.title()
        issue_page_url = page.url
        body_text = page.locator("body").inner_text(timeout=15000)

        print("issue_title:", issue_title)
        print("issue_page_url:", issue_page_url)
        print("body_head:")
        print(body_text[:600])

        links = collect_links(page, issue_url)

        articles = []
        seen_article_keys = set()

        for item in links:
            url = item["url"]
            title = item["title"]

            if not is_paper_article_url(url):
                continue

            qs = parse_qs(urlparse(url).query)
            article_key = qs.get("ng", [url])[0]

            if article_key in seen_article_keys:
                continue

            seen_article_keys.add(article_key)

            articles.append({
                "title": title,
                "url": url,
                "issue_url": issue_url,
                "issue_date": extract_issue_date(url) or extract_issue_date(issue_url),
                "edition": EDITION,
            })

        out = OUTPUT_DIR / "nikkei_issue_article_links.json"
        out.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")

        all_out = OUTPUT_DIR / "nikkei_issue_all_links.json"
        all_out.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")

        page.screenshot(path=str(OUTPUT_DIR / "nikkei_issue_page.png"), full_page=True)

        print("article_count:", len(articles))
        print("saved:", out)
        print("saved_all:", all_out)

        print("\n--- article links head ---")
        for i, item in enumerate(articles[:100], 1):
            print(f"{i}. {item['title']} | {item['url']}")

        browser.close()


if __name__ == "__main__":
    main()
