import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

load_dotenv()

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
INPUT_PATH = Path("logs/nikkei_issue_article_links.json")
OUTPUT_JSONL = Path("logs/nikkei_articles_full.jsonl")
OUTPUT_JSON = Path("logs/nikkei_articles_full.json")
FAILED_JSON = Path("logs/nikkei_articles_failed.json")

MAX_ARTICLES = int(os.getenv("NIKKEI_MAX_ARTICLES_TO_FETCH", "0"))  # 0なら全件
SLEEP_SECONDS = float(os.getenv("NIKKEI_FETCH_SLEEP_SECONDS", "1.5"))
EXCLUDE_BODY_REGEX = os.getenv("NIKKEI_EXCLUDE_BODY_REGEX", "").strip()


def wait_page(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(3000)


def should_exclude_by_body(title: str, text: str) -> tuple[bool, str]:
    title = title or ""
    text = text or ""

    # 1. 日経の人事記事ページに出る典型表現
    if "人事記事をもっと見る" in text:
        return True, "hr_article_marker"

    # 2. 人事記事の典型形式
    # 例：
    # 三井住友銀行
    # （5月1日）
    # ▽ 米州営業第三 ...
    has_hr_bullet = re.search(r"(^|\n)\s*▽\s*", text, flags=re.MULTILINE) is not None
    has_effective_date = re.search(r"（\d{1,2}月\d{1,2}日(?:付)?）", text) is not None

    # 3. 会社名だけの記事タイトルっぽいもの
    short_company_title = (
        len(title) <= 40
        and not any(ch in title for ch in "、。！？!?「」（）()：:・")
    )

    # 4. 本文中に辞令形式の「▽」が複数ある場合は人事記事らしい
    hr_bullet_count = len(re.findall(r"(^|\n)\s*▽\s*", text, flags=re.MULTILINE))

    if short_company_title and has_effective_date and has_hr_bullet:
        return True, "hr_article_company_pattern"

    if has_effective_date and hr_bullet_count >= 2:
        return True, "hr_article_multiple_bullets"

    # 5. 本文先頭が会社名 + 日付 + 辞令形式なら除外
    head = text[:600]
    if re.search(r"（\d{1,2}月\d{1,2}日(?:付)?）", head) and re.search(r"▽\s*", head):
        if short_company_title or hr_bullet_count >= 1:
            return True, "hr_article_head_pattern"

    # 6. 環境変数による追加除外
    if EXCLUDE_BODY_REGEX:
        try:
            if re.search(EXCLUDE_BODY_REGEX, text, flags=re.MULTILINE):
                return True, "body_regex"
        except re.error as e:
            print(f"WARNING: invalid NIKKEI_EXCLUDE_BODY_REGEX: {e}")

    return False, ""

def clean_article_text(text: str, title: str = "") -> str:
    text = text or ""
    title = title or ""

    text = text.replace("\u3000", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    remove_patterns = [
        r"Myニュースでまとめ読み",
        r"保存\s+共有\s+印刷\s+翻訳\s+その他",
        r"保存\s+共有\s+印刷\s+その他",
        r"保存\s+共有\s+印刷",
        r"［有料会員限定］",
        r"\d+文字",
    ]

    for pat in remove_patterns:
        text = re.sub(pat, "", text)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # 末尾に混ざる「春秋を」「記事タイトルを」などのリンク断片を除去
    while lines:
        last = lines[-1].strip()
        title_clean = title.strip()

        if title_clean and last in {
            f"{title_clean}を",
            f"{title_clean}へ",
            f"{title_clean}はこちら",
        }:
            lines.pop()
            continue

        if (
            len(last) <= 40
            and ("。" not in last)
            and ("、" not in last)
            and (last.endswith("を") or last.endswith("へ") or last.endswith("はこちら"))
        ):
            lines.pop()
            continue

        break

    return "\n\n".join(lines).strip()


def extract_article_with_retry(page, retries: int = 5):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            wait_page(page)

            data = page.evaluate(
                """
                () => {
                  const title =
                    document.querySelector('.cmn-article_title')?.innerText?.trim()
                    || document.querySelector('h1')?.innerText?.trim()
                    || document.title
                    || '';

                  const selectors = [
                    'div.cmn-section.cmn-indent',
                    '.cmn-section.cmn-indent',
                    '.cmn-section',
                    'article',
                    'main'
                  ];

                  let best = '';
                  let bestSelector = '';

                  for (const sel of selectors) {
                    const els = Array.from(document.querySelectorAll(sel));
                    for (const el of els) {
                      const txt = (el.innerText || el.textContent || '').trim();
                      if (txt && txt.length > best.length) {
                        best = txt;
                        bestSelector = sel;
                      }
                    }
                    if (best && bestSelector === 'div.cmn-section.cmn-indent') {
                      break;
                    }
                  }

                  const images = Array.from(document.querySelectorAll('img')).map(img => ({
                    alt: img.alt || '',
                    src: img.currentSrc || img.src || ''
                  })).filter(x => x.src);

                  return {
                    title,
                    text: best,
                    selector: bestSelector,
                    image_count: images.length,
                    images: images.slice(0, 20)
                  };
                }
                """
            )

            data["text"] = clean_article_text(data.get("text", ""), data.get("title", ""))
            return data

        except PlaywrightError as e:
            last_error = e
            msg = str(e)
            if "Execution context was destroyed" in msg or "navigation" in msg:
                print(f"  retry extract {attempt}/{retries}: navigation中のため再試行")
                page.wait_for_timeout(2500)
                continue
            raise

    raise RuntimeError(f"extract_article failed after retries: {last_error}")


def load_existing_urls():
    if not OUTPUT_JSONL.exists():
        return set()

    urls = set()
    for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if item.get("url"):
                urls.add(item["url"])
        except Exception:
            continue
    return urls


def main():
    if not STORAGE_PATH.exists():
        raise FileNotFoundError(f"{STORAGE_PATH} がありません。")

    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"{INPUT_PATH} がありません。先に nikkei_extract_issue_links.py を実行してください。")

    articles = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

    if MAX_ARTICLES > 0:
        articles = articles[:MAX_ARTICLES]

    done_urls = load_existing_urls()
    results = []
    failures = []
    excluded_after_fetch = []

    if OUTPUT_JSONL.exists():
        for line in OUTPUT_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except Exception:
                    pass

    print("target_count:", len(articles))
    print("already_done:", len(done_urls))
    print("output_jsonl:", OUTPUT_JSONL)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_PATH),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.set_default_timeout(20000)

        for i, item in enumerate(articles, 1):
            url = item["url"]

            if url in done_urls:
                print(f"[{i}/{len(articles)}] skip already done: {item.get('title', '')[:60]}")
                continue

            print(f"[{i}/{len(articles)}] open: {item.get('title', '')[:80]}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                extracted = extract_article_with_retry(page)

                text = extracted.get("text", "")
                source_title = item.get("title", "")

                should_exclude, exclude_reason = should_exclude_by_body(source_title, text)
                if should_exclude:
                    excluded_record = {
                        "status": "excluded",
                        "exclude_reason": exclude_reason,
                        "source_title": source_title,
                        "url": url,
                        "issue_url": item.get("issue_url", ""),
                        "issue_date": item.get("issue_date", ""),
                        "edition": item.get("edition", ""),
                        "page_title": extracted.get("title", ""),
                        "selector": extracted.get("selector", ""),
                        "text_length": len(text),
                    }
                    excluded_after_fetch.append(excluded_record)
                    print("  excluded:", exclude_reason, source_title[:80])
                    continue

                record = {
                    "status": "success",
                    "source_title": source_title,
                    "url": url,
                    "issue_url": item.get("issue_url", ""),
                    "issue_date": item.get("issue_date", ""),
                    "edition": item.get("edition", ""),
                    "page_title": extracted.get("title", ""),
                    "selector": extracted.get("selector", ""),
                    "text_length": len(text),
                    "text": text,
                    "image_count": extracted.get("image_count", 0),
                    "images": extracted.get("images", []),
                }

                with OUTPUT_JSONL.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

                results.append(record)
                done_urls.add(url)

                print(
                    "  ok",
                    "selector=", record["selector"],
                    "text_length=", record["text_length"],
                    "image_count=", record["image_count"],
                )

            except Exception as e:
                failure = {
                    "status": "failed",
                    "source_title": item.get("title", ""),
                    "url": url,
                    "error": str(e),
                }
                failures.append(failure)
                print("  failed:", str(e)[:300])

            time.sleep(SLEEP_SECONDS)

        browser.close()

    OUTPUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    FAILED_JSON.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("logs/nikkei_articles_excluded_after_fetch.json").write_text(json.dumps(excluded_after_fetch, ensure_ascii=False, indent=2), encoding="utf-8")

    print("saved_jsonl:", OUTPUT_JSONL)
    print("saved_json:", OUTPUT_JSON)
    print("failed_json:", FAILED_JSON)
    print("success_count:", len(results))
    print("failed_count:", len(failures))
    print("excluded_after_fetch_count:", len(excluded_after_fetch))


if __name__ == "__main__":
    main()
