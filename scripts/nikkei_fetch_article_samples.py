import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

load_dotenv()

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
INPUT_PATH = Path("logs/nikkei_issue_article_links.json")
OUTPUT_PATH = Path("logs/nikkei_article_samples.json")

MAX_ARTICLES = int(os.getenv("NIKKEI_SAMPLE_ARTICLE_LIMIT", "5"))


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
            and "。" not in last
            and "、" not in last
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
                print(f"  retry extract {attempt}/{retries}: navigation中のため再試行します")
                page.wait_for_timeout(2500)
                continue
            raise

    raise RuntimeError(f"extract_article failed after retries: {last_error}")


def main():
    if not STORAGE_PATH.exists():
        raise FileNotFoundError(f"{STORAGE_PATH} がありません。")

    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"{INPUT_PATH} がありません。先に nikkei_extract_issue_links.py を実行してください。")

    articles = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    targets = articles[:MAX_ARTICLES]

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_PATH),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.set_default_timeout(20000)

        for i, item in enumerate(targets, 1):
            url = item["url"]
            print(f"[{i}/{len(targets)}] open: {item['title']}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                extracted = extract_article_with_retry(page)

                text = extracted.get("text", "")

                results.append({
                    "source_title": item.get("title", ""),
                    "url": url,
                    "page_title": extracted.get("title", ""),
                    "selector": extracted.get("selector", ""),
                    "text_length": len(text),
                    "text_head": text[:300],
                    "text": text,
                    "image_count": extracted.get("image_count", 0),
                    "images": extracted.get("images", []),
                    "status": "success",
                })

                print(
                    "  ok",
                    "selector=", extracted.get("selector", ""),
                    "text_length=", len(text),
                    "image_count=", extracted.get("image_count", 0),
                )
                print("  head:", text[:120].replace("\n", " "))

            except Exception as e:
                results.append({
                    "source_title": item.get("title", ""),
                    "url": url,
                    "status": "failed",
                    "error": str(e),
                })
                print("  failed:", e)

        browser.close()

    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", OUTPUT_PATH)


if __name__ == "__main__":
    main()
