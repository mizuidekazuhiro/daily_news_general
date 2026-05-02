import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

load_dotenv()

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
OUTPUT_DIR = Path("logs")
OUTPUT_DIR.mkdir(exist_ok=True)

ENTRY_URL = os.getenv("NIKKEI_MORNING_URL", "https://www.nikkei.com/paper/").strip()
EDITION = os.getenv("NIKKEI_EDITION", "morning").strip()  # morning / evening
TARGET_DATE = os.getenv("NIKKEI_TARGET_DATE", "auto").strip()
REQUIRE_TODAY = os.getenv("NIKKEI_REQUIRE_TODAY", "false").lower() == "true"

USE_DIRECT_ISSUE_URL = os.getenv("NIKKEI_USE_DIRECT_ISSUE_URL", "true").lower() == "true"
PAPER_URL_TEMPLATE = os.getenv(
    "NIKKEI_PAPER_URL_TEMPLATE",
    "https://www.nikkei.com/paper/{edition}/?b={date}&d=0",
).strip()

EXCLUDE_TITLE_REGEX = os.getenv("NIKKEI_EXCLUDE_TITLE_REGEX", "").strip()
ALLOW_DIRECT_FALLBACK = os.getenv("NIKKEI_ALLOW_DIRECT_FALLBACK", "false").lower() == "true"

JST = timezone(timedelta(hours=9))


def target_date_yyyymmdd() -> str:
    if TARGET_DATE and TARGET_DATE != "auto":
        return TARGET_DATE
    return datetime.now(JST).strftime("%Y%m%d")


def build_direct_issue_url() -> str:
    return PAPER_URL_TEMPLATE.format(
        edition=EDITION,
        date=target_date_yyyymmdd(),
    )


def wait_page(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(3500)


def collect_links(page, base_url: str, retries: int = 5):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            wait_page(page)

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

        except PlaywrightError as e:
            last_error = e
            msg = str(e)
            if "Execution context was destroyed" in msg or "navigation" in msg:
                print(f"retry collect_links {attempt}/{retries}: navigation中のため再試行")
                page.wait_for_timeout(2500)
                continue
            raise

    raise RuntimeError(f"collect_links failed after retries: {last_error}")


def get_b_param(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    vals = qs.get("b") or []
    return vals[0] if vals else ""


def is_paper_article_url(url: str) -> bool:
    parsed = urlparse(url)

    if "nikkei.com" not in parsed.netloc:
        return False

    if parsed.path != "/paper/article/":
        return False

    qs = parse_qs(parsed.query)
    return "ng" in qs


def page_matches_requested_edition(page_title: str, page_url: str) -> bool:
    page_title = page_title or ""
    page_url = page_url or ""

    if f"/paper/{EDITION}/" in page_url:
        return True

    if EDITION == "morning" and "朝刊" in page_title:
        return True

    if EDITION == "evening" and "夕刊" in page_title:
        return True

    return False


def should_exclude_article(title: str, url: str) -> bool:
    title = title or ""

    if EXCLUDE_TITLE_REGEX:
        try:
            if re.search(EXCLUDE_TITLE_REGEX, title):
                return True
        except re.error as e:
            print(f"WARNING: invalid NIKKEI_EXCLUDE_TITLE_REGEX: {e}")

    return False


def extract_articles_from_links(links, issue_url: str):
    articles = []
    excluded = []
    seen_keys = set()

    for item in links:
        url = item["url"]
        title = item["title"]

        if not is_paper_article_url(url):
            continue

        qs = parse_qs(urlparse(url).query)
        article_key = qs.get("ng", [url])[0]

        if article_key in seen_keys:
            continue

        seen_keys.add(article_key)

        record = {
            "title": title,
            "url": url,
            "issue_url": issue_url,
            "issue_date": get_b_param(url) or get_b_param(issue_url),
            "edition": EDITION,
        }

        if should_exclude_article(title, url):
            record["exclude_reason"] = "title_regex"
            excluded.append(record)
            continue

        articles.append(record)

    excluded_out = OUTPUT_DIR / "nikkei_issue_excluded_links.json"
    excluded_out.write_text(json.dumps(excluded, ensure_ascii=False, indent=2), encoding="utf-8")

    print("excluded_count:", len(excluded))
    print("excluded_saved:", excluded_out)

    return articles


def save_outputs(articles, links, page, issue_url):
    out = OUTPUT_DIR / "nikkei_issue_article_links.json"
    out.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")

    all_out = OUTPUT_DIR / "nikkei_issue_all_links.json"
    all_out.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")

    page.screenshot(path=str(OUTPUT_DIR / "nikkei_issue_page.png"), full_page=True)

    print("article_count:", len(articles))
    print("saved:", out)
    print("saved_all:", all_out)
    print("issue_url:", issue_url)

    print("\n--- article links head ---")
    for i, item in enumerate(articles[:100], 1):
        print(f"{i}. {item['title']} | {item['url']}")


def find_issue_url_from_entry(links):
    target_path = f"/paper/{EDITION}/"
    target_date = target_date_yyyymmdd()

    candidates = []

    for item in links:
        url = item["url"]
        if target_path not in url:
            continue

        b = get_b_param(url)
        if not b:
            continue

        candidates.append({
            "date": b,
            "url": url,
            "title": item.get("title", ""),
        })

    if not candidates:
        return None, []

    exact = [x for x in candidates if x["date"] == target_date]
    if exact:
        exact.sort(key=lambda x: x["url"])
        return exact[0]["url"], candidates

    if REQUIRE_TODAY:
        return None, candidates

    past = [x for x in candidates if x["date"] <= target_date]
    if past:
        past.sort(key=lambda x: x["date"], reverse=True)
        return past[0]["url"], candidates

    candidates.sort(key=lambda x: x["date"], reverse=True)
    return candidates[0]["url"], candidates


def open_issue_and_extract(page, issue_url: str) -> bool:
    print("open issue:", issue_url)
    page.goto(issue_url, wait_until="domcontentloaded", timeout=45000)
    wait_page(page)

    issue_title = page.title()
    issue_page_url = page.url

    print("issue_title:", issue_title)
    print("issue_page_url:", issue_page_url)

    if not page_matches_requested_edition(issue_title, issue_page_url):
        print("ERROR: requested edition と実際のページが一致しません。")
        print("requested_edition:", EDITION)
        print("actual_title:", issue_title)
        print("actual_url:", issue_page_url)
        return False

    body_text = page.locator("body").inner_text(timeout=15000)
    print("body_head:")
    print(body_text[:600])

    links = collect_links(page, issue_url)
    articles = extract_articles_from_links(links, issue_url)

    save_outputs(articles, links, page, issue_url)
    return len(articles) > 0


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

        # 1. 原則：朝刊/夕刊のURLを直接生成して開く
        if USE_DIRECT_ISSUE_URL:
            issue_url = build_direct_issue_url()
            ok = open_issue_and_extract(page, issue_url)
            if ok:
                browser.close()
                return

            if REQUIRE_TODAY:
                print("ERROR: direct issue URL で記事が取得できませんでした。")
                browser.close()
                return

        # 2. fallback：/paper/ から探す
        print("open entry:", ENTRY_URL)
        page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45000)
        wait_page(page)

        entry_title = page.title()
        entry_url = page.url
        entry_links = collect_links(page, ENTRY_URL)

        (OUTPUT_DIR / "nikkei_entry_all_links.json").write_text(
            json.dumps(entry_links, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("entry_title:", entry_title)
        print("entry_url:", entry_url)
        print("entry_all_link_count:", len(entry_links))

        issue_url, candidates = find_issue_url_from_entry(entry_links)

        if issue_url:
            print("found issue_url:", issue_url)
            ok = open_issue_and_extract(page, issue_url)
            browser.close()
            return

        direct_articles = extract_articles_from_links(entry_links, entry_url)

        if direct_articles and ALLOW_DIRECT_FALLBACK and page_matches_requested_edition(entry_title, entry_url):
            print("WARNING: issue_urlは見つかりませんでしたが、現在ページが指定editionと一致するため直接抽出します。")
            save_outputs(direct_articles, entry_links, page, entry_url)
            browser.close()
            return

        if direct_articles:
            print("WARNING: direct_articles は見つかりましたが、朝刊・夕刊混在防止のため使用しません。")

        print("ERROR: 朝刊/夕刊の紙面URLも記事リンクも見つかりませんでした。")
        print("target_date:", target_date_yyyymmdd())
        print("edition:", EDITION)
        print("require_today:", REQUIRE_TODAY)

        if candidates:
            print("available_issue_dates:", sorted({x["date"] for x in candidates}, reverse=True)[:20])
        else:
            print("available_issue_dates: none")

        browser.close()


if __name__ == "__main__":
    main()
