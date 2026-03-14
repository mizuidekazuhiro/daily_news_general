# daily_news_general

このリポジトリには、**2つの配信ジョブ**があります。

- `main` : 既存の主要ニュース配信
- `special` : 専門紙記事一覧配信（JST基準で前日更新分）

`all` は **main → special の順**で連続実行します。

---

## 1. 何が分離されたか

今回のリファクタリングで、`news_digest.py` に混在していた責務を `src/` 配下に分離しました。

- CLIの分岐（どのジョブを実行するか）
- main配信の取得・整形・送信
- special配信の取得・前日判定・整形・送信
- 設定値（環境変数）

> 目的は「main用コード」と「special用コード」を、ファイル名だけで追えるようにすることです。

---

## 2. 実行方法

```bash
python -m src.news_digest --job main
python -m src.news_digest --job special
python -m src.news_digest --job all
```

互換のため、従来どおり `python news_digest.py --job ...` も利用できます。

---

## 3. ディレクトリ構成（主要部分）

```text
src/
  news_digest.py                    # CLIエントリーポイント（薄い分岐のみ）
  config/
    job_config.py                   # job名・環境変数・既定値
  jobs/
    main_news_job.py                # main配信ロジック
    special_news_job.py             # special配信ロジック（JST前日判定を含む）
  renderers/
    main_news_renderer.py           # main配信の表示系（件名補助）
    special_news_renderer.py        # special配信HTML/件名生成
  utils/
    date_utils.py                   # JST・前日計算
    mail_utils.py                   # メール送信/宛先パース

news_digest.py                      # 互換レイヤー（既存呼び出しのため）
templates/special_news_email.html   # special配信メールテンプレート
config/special_news_media.json      # special配信の媒体設定（Notion未使用時）
```

---

## 4. main と special の違い

### main 配信
- RSS取得、重要度判定、翻訳、既存フォーマットでのHTML生成
- 既存宛先（`MAIL_TO`）へ送信
- 実装の主な入口: `src/jobs/main_news_job.py`

### special 配信
- Google Alertフィード取得
- **JSTで前日更新分のみ抽出**（日次レポートのため）
- 専用宛先・専用件名・専用HTMLテンプレートで送信
- 実装の主な入口: `src/jobs/special_news_job.py`

---

## 5. 必要な環境変数

### main用
- `MAIL_FROM`
- `MAIL_TO`
- `MAIL_PASSWORD`

### special用
- `SPECIAL_NEWS_MAIL_TO`（必須）
- `SPECIAL_NEWS_MAIL_CC`（任意）
- `SPECIAL_NEWS_MAIL_BCC`（任意）
- `SPECIAL_NEWS_MAIL_SUBJECT_PREFIX`（既定 `【専門紙記事一覧】`）
- `SPECIAL_NEWS_CONFIG_PATH`（既定 `config/special_news_media.json`）
- `SPECIAL_NEWS_TEMPLATE_PATH`（既定 `templates/special_news_email.html`）
- `SPECIAL_NEWS_MAX_ITEMS_TOTAL`（既定 `50`）
- `SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA`（既定 `20`）

### Notion（special設定の操作パネル）
- `NOTION_TOKEN`
- `NOTION_SPECIAL_NEWS_DB_ID`
- `NOTION_SPECIAL_NEWS_ENABLED`（既定 `true`）

---

## 6. 見た目を直したいとき

- main配信の見た目を調整したい: `src/jobs/main_news_job.py`（HTML生成部分）
- special配信の見た目を調整したい: `src/renderers/special_news_renderer.py` と `templates/special_news_email.html`

---

## 7. スモークテスト

```bash
pytest -q
python -m py_compile news_digest.py src/news_digest.py src/jobs/main_news_job.py src/jobs/special_news_job.py
```

確認ポイント:
- `--job main` が既存宛先/件名で送信される
- `--job special` がJST前日分のみを対象に送信される
- `--job all` で main → special の順に実行される
