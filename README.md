# daily_news_general

ニュース収集メールを **GitHub Actions + Python** で毎日送るためのリポジトリです。  
初心者向けに、まず「最短で動かす手順」を先に書いています。

---

## このリポジトリでできること

- **main job**: 一般ニュース（RSS）を集めてメール送信
- **special job**: 専門媒体ニュースを Notion / JSON 設定で抽出してメール送信
- **direct-site job**: 直接サイト巡回で更新記事を抽出してメール送信

---

## 3つのジョブの違い

| job | 実行スクリプト | 主な用途 | 設定ソース |
|---|---|---|---|
| main | `python news_digest.py --job main` | 定常ニュース配信 | `news_digest.py` 内の `MEDIA` |
| special | `python news_digest.py --job special` | 専門媒体配信 | Notion DB（有効時）→ `config/special_news_media.json` |
| direct-site | `python direct_site_updates.py` | RSSを使わないサイト更新監視 | Notion DB（有効時）→ `config/direct_site_watchers.json` |

> 補足: `--job all` は main + special を連続実行します。

---

## 最短セットアップ（5ステップ）

1. Python 3.11 を用意
2. 依存関係をインストール
   ```bash
   pip install -r requirements.txt
   ```
3. `.env.example` をコピーして `.env` を作成
   ```bash
   cp .env.example .env
   ```
4. `.env` に値を入れる（最低限は `MAIL_FROM`, `MAIL_PASSWORD`, 各 job の宛先）
5. ローカルで1回実行
   ```bash
   python news_digest.py --job main
   ```

---

## 必須環境変数（先にここだけ）

### 共通（メール送信）

- `MAIL_FROM`: 送信元メールアドレス
- `MAIL_PASSWORD`: SMTP 認証用パスワード（Gmail はアプリパスワード推奨）

### main job

- `MAIL_TO`: 配信先（カンマ / セミコロン / 改行区切り）

### special job

- `SPECIAL_NEWS_MAIL_TO`（必要に応じて `SPECIAL_NEWS_MAIL_CC`, `SPECIAL_NEWS_MAIL_BCC`）
- `NOTION_SPECIAL_NEWS_ENABLED=true` にする場合は `NOTION_TOKEN`, `NOTION_SPECIAL_NEWS_DB_ID`

### direct-site job

- `DIRECT_SITE_MAIL_TO`（必要に応じて `DIRECT_SITE_MAIL_CC`, `DIRECT_SITE_MAIL_BCC`）
- `NOTION_DIRECT_SITES_ENABLED=true` にする場合は `NOTION_TOKEN`, `NOTION_DIRECT_SITES_DB_ID`

### 翻訳（任意）

- `OPENAI_API_KEY`（未設定でも main job は動作継続。英語タイトル翻訳だけスキップ）

---

## ローカル実行方法

```bash
# 一般ニュース
python news_digest.py --job main

# 専門媒体
python news_digest.py --job special

# 両方
python news_digest.py --job all

# 直接サイト更新
python direct_site_updates.py
```

---

## GitHub Actions 実行ファイル一覧

- `.github/workflows/daily.yml`（main）
- `.github/workflows/special_news_delivery.yml`（special）
- `.github/workflows/direct_site_updates.yml`（direct-site）

### secrets / vars の使い分け

- **Secrets**: メール認証情報・APIトークン・宛先など機密情報
- **Variables (vars)**: モデル名や件名プレフィックス、Notion利用ON/OFFなど非機密設定

---

## よくある失敗

1. **宛先が空**
   - main: `MAIL_TO` が空なら安全にスキップ（warning ログ）
   - special/direct-site: To/CC/BCC が全て空ならスキップ

2. **Notionの列名不一致**
   - 列名はコードと一致させる必要があります

3. **Notionに100件以上あり、古い実装で欠落**
   - 現在はページネーション対応済みで全件読込します

4. **翻訳APIキー未設定**
   - 翻訳だけスキップし、配信処理は続行します

---


## direct-site の Notion 設定メモ（推奨）

Notion の `ArticleUrlPattern` は、記号や改行が混ざると意図せず壊れることがあります。
以下のような「そのまま貼れる形」を推奨します。

- SteelOrbis: `/steel-news/.+-[0-9]+[.]htm`
- Kallanish: `/en/news/steel/.+/article-details/.+`

`ListPageUrls` は 1行1URL / 改行 / カンマ / セミコロン / Markdownリンク（`[title](url)`）に対応します。

---
## 変更理由メモ

今回の保守改善の理由と方針は `docs/maintenance_changes_ja.md` にまとめています。

## Nikkeiソース統合（MVP）
- `config/sources.yml` に日経ソース設定を追加。
- デフォルトで本文全文保存 (`NIKKEI_SAVE_FULL_TEXT`) と画像保存 (`NIKKEI_SAVE_IMAGES`) はOFF。
- Playwrightログインは `NIKKEI_SESSION_STATE_JSON` 優先、失効時は `NIKKEI_EMAIL` / `NIKKEI_PASSWORD` を利用する方針。
- GitHub Actions: `.github/workflows/general_news.yml` を `workflow_dispatch` と JST朝定期実行で追加。
- Notion保存先は `NOTION_DAILY_NEWS_DB_ID`, `NOTION_ARTICLE_DB_ID` を利用。
- 著作権・契約上不明な場合は本文全文や画像本体を保存せず、URL/見出し/抜粋/要約のみ保存する運用。

---

## Nikkei記事スコアリング（Rules DB読み取り専用）

日経朝刊・夕刊向けスコアリングは、既存Notion Rules DBを**読み取り専用**で利用します。  
**Rules DB自体の更新（追加・編集・スキーマ変更）は行いません。**

### Rules DB
- DB ID: `.env` / GitHub Secrets の `NOTION_RULES_DB_ID` を使用（コードへの固定値埋め込み禁止）
- 列: `TagName`, `Enabled`, `RuleType`, `Keywords`, `NegativeKeywords`, `MatchField`, `Weight`, `Priority`, `Notes`
- `RuleType` は `country / sector / importance` を全て読み込みます。

### Weight と Priority の違い
- `Weight`: `importance_score` の本体（合計値）
- `Priority`: **importance_scoreには加算しない**
- `Priority` は同点時の並び順・表示優先度の補助指標として使用

### スコア計算ルール
- `Enabled=true` のみ対象
- `Keywords` 一致: `+Weight`
- `NegativeKeywords` 一致: `-abs(Weight)`
- 同一ルールで複数キーワード一致しても原則1回加点/減点
- ソート順: `importance_score desc`, `priority desc`, `text_length desc`

### 実行方法
```bash
python scripts/nikkei_score_articles.py
```

入力: `logs/nikkei_articles_full.json`  
出力: `logs/nikkei_articles_scored.json`

### デバッグ確認
```bash
python - <<'PY'
import json
from pathlib import Path
p = Path("logs/nikkei_articles_scored.json")
data = json.loads(p.read_text(encoding="utf-8"))
print("count:", len(data))
print("--- top 20 ---")
for i, x in enumerate(data[:20], 1):
    print(i, x.get("importance_score"), x.get("priority"), x.get("source_title"))
    print(" tags:", x.get("tags"))
    print(" reason:", x.get("reason_to_read"))
PY
```

### パイプライン環境変数
- `NIKKEI_ENABLE_SCORING=true`
- `NIKKEI_ENABLE_NOTION_SCORE_UPDATE=false`（デフォルト）
- `NIKKEI_RULES_SOURCE=notion`
- `NIKKEI_RULES_FILTER_RULE_TYPES=country,sector,importance`
- `NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT=5`
- `NOTION_RULES_DB_ID=<your_notion_rules_db_id>`（必須）

### GitHub Secrets
- 既存: `NOTION_TOKEN`, `NOTION_ARTICLE_DB_ID`, `NIKKEI_SESSION_STATE_JSON`
- 追加: `NOTION_RULES_DB_ID`（必須。値はSecretsで管理し、READMEやコードへ実値を記載しない）

### 注意
- 既存Rules DBはgeneral news側でも使われる可能性があるため、日経処理では読み取りのみ。
- `.env`, `logs/`, `.storage/`, Cookie, Token, Session JSON はコミット禁止。

## Nikkei pipeline speed/scoring notes
- `NIKKEI_TARGET_DATE=auto` means the pipeline uses current JST date by default.
- Direct issue URL is opened first (`/paper/{edition}/?b=YYYYMMDD&d=0`), and `/paper/` is only fallback.
- First run can be long because full text is fetched and stored; later runs are faster with existing URL skip.
- Key speed envs: `NIKKEI_SKIP_EXISTING_NOTION_URLS`, `NIKKEI_ENABLE_PRE_TITLE_FILTER`, `NIKKEI_BLOCK_HEAVY_RESOURCES`, `NIKKEI_MIN_ARTICLE_TEXT_LENGTH`.
- Manual workflow inputs are kept (target_date, max_articles, skip_existing, pre_title_filter, block_heavy_resources, enable_scoring).
- Existing Rules DB is read-only. `Weight` contributes to `importance_score`; `Priority` is only tiebreak/display order (not added to score).
- Rule types `country/sector/importance` are all loaded via `NIKKEI_RULES_FILTER_RULE_TYPES`.
- Scoring output: `logs/nikkei_articles_scored.json`.

### 20件テスト
```bash
NIKKEI_TARGET_DATE=20260501 \
NIKKEI_REQUIRE_TODAY=false \
NIKKEI_MAX_ARTICLES_TO_FETCH=20 \
NIKKEI_SKIP_EXISTING_NOTION_URLS=false \
NIKKEI_ENABLE_SCORING=true \
python scripts/run_nikkei_paper_pipeline.py
```

### 既存URLスキップ確認
```bash
NIKKEI_TARGET_DATE=20260501 \
NIKKEI_REQUIRE_TODAY=false \
NIKKEI_MAX_ARTICLES_TO_FETCH=0 \
NIKKEI_SKIP_EXISTING_NOTION_URLS=true \
NIKKEI_ENABLE_SCORING=true \
python scripts/run_nikkei_paper_pipeline.py
```

## 二段階GPTレポート（案B, Nikkei専用）
- 1段階目: 選定記事のみを記事単位で enrichment（Summary / Reason to Read / Business Implications）。
- 2段階目: 記事単位の保存済み結果だけを材料に final report synthesis を実施。
- 効果: 課金削減、冪等性、再実行耐性、Notionへの記事別示唆の蓄積。

### 手動実行
```bash
python scripts/run_nikkei_final_report.py
```

### 主要ログ
- `logs/nikkei_report_selection.json`
- `logs/nikkei_article_enrichment_summary.json`
- `logs/nikkei_article_enrichment_failed.json`
- `logs/nikkei_final_report_summary.json`
- `logs/nikkei_final_report_failed.json`

### 必要なSecrets/Variables
- Secrets: `OPENAI_API_KEY`, `NOTION_TOKEN`, `NOTION_DAILY_NEWS_DB_ID`, `NOTION_ARTICLE_DB_ID`, `MAIL_FROM`, `MAIL_PASSWORD`, `MAIL_TO`, `MAIL_CC`, `MAIL_BCC`
- Variables: `NIKKEI_*` 系（Nikkei workflowで利用）。この二段階レポートはNikkei pipeline専用で、一般RSS/special/direct-siteには適用しません。入力は `logs/nikkei_articles_scored.json` 固定です。

- `NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL=false`（推奨）: final report GPT失敗でfallback生成時は、デフォルトでメール送信しません。trueでのみfallbackメール送信を許可。
