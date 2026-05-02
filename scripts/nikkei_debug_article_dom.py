import json
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

STORAGE_PATH = Path(".storage/nikkei_storage_state.json")
INPUT_PATH = Path("logs/nikkei_issue_article_links.json")
OUTPUT_PATH = Path("logs/nikkei_article_dom_debug.json")

def wait_page(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(5000)

def main():
    articles = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

    # 先頭の春秋などは特殊な可能性があるため、5番目の記事を使う
    target = articles[4]
    url = target["url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(STORAGE_PATH),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        wait_page(page)

        data = page.evaluate(
            """
            () => {
              const rows = [];
              const els = Array.from(document.querySelectorAll('body *'));

              for (const el of els) {
                const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length < 80) continue;

                rows.push({
                  tag: el.tagName,
                  className: typeof el.className === 'string' ? el.className : '',
                  id: el.id || '',
                  role: el.getAttribute('role') || '',
                  aria: el.getAttribute('aria-label') || '',
                  dataAttrs: Array.from(el.attributes)
                    .filter(a => a.name.startsWith('data-'))
                    .slice(0, 8)
                    .map(a => `${a.name}=${a.value}`),
                  textLength: text.length,
                  textHead: text.slice(0, 300)
                });
              }

              rows.sort((a, b) => b.textLength - a.textLength);
              return {
                title: document.title,
                url: location.href,
                bodyLength: document.body.innerText.length,
                rows: rows.slice(0, 80)
              };
            }
            """
        )

        OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        print("title:", data["title"])
        print("url:", data["url"])
        print("bodyLength:", data["bodyLength"])
        print("saved:", OUTPUT_PATH)
        print("--- candidates ---")
        for i, r in enumerate(data["rows"][:30], 1):
            print(f"{i}. len={r['textLength']} tag={r['tag']} id={r['id']} class={r['className'][:80]}")
            print("   ", r["textHead"][:200])

        browser.close()

if __name__ == "__main__":
    main()
