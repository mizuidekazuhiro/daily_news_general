# daily_news_general

この README は、**プログラミング初心者でも迷わず初期設定できること**を最優先に、セットアップ手順を 1 から書き直したものです。

---

## 1. このプロジェクトで何ができるか

このリポジトリは、ニュースを収集してメール配信する Python スクリプトです。

- **main job（通常配信）**: 海外・国内の一般ニュースを集めて、必要に応じてタイトル翻訳し、1通のメールで送る
- **special job（専門紙配信）**: 専門媒体（例: 鉄鋼新聞・日刊産業新聞）を媒体別ルールで抽出し、1通のメールで送る

ジョブは以下で切り替えます。

- `python news_digest.py --job main`
- `python news_digest.py --job special`
- `python news_digest.py --job all`

---

## 2. 全体構成

初心者向けに、処理の流れを先に押さえます。

1. **情報源取得**
   - main: コード内で定義された RSS（Google News 検索 RSS など）を取得
   - special: Notion DB または `config/special_news_media.json` の feed URL を取得
2. **記事抽出**
   - feed から記事一覧を読み込み
3. **日付判定**
   - main: 直近 24 時間中心で判定
   - special: 媒体ごとの `DateSourceType` / `DateGranularity` / `TargetDateMode` で判定
4. **フィルタ・重複除去**
   - 媒体別の件数上限、全体上限を適用
5. **メール送信**
   - Gmail SMTP (`smtp.gmail.com:587`) で配信

### Notion が使われる場面

Notion は **special job の媒体設定管理**に使います（有効化時のみ）。

### local config と Notion config の優先関係

special job の媒体設定は以下の優先です。

1. `NOTION_SPECIAL_NEWS_ENABLED=true` かつ Notion 読み取り成功 → **Notion 設定を採用**
2. Notion 無効・認証不足・取得失敗・DB 空 → **`config/special_news_media.json` にフォールバック**

### GitHub Actions での位置づけ

- `.github/workflows/daily.yml` が main job を定期実行
- `.github/workflows/special_news_delivery.yml` が special job を定期実行

---

## 3. 初期設定の全手順（上から順に実行）

> ここだけ上から順に進めれば初期設定できます。

### 3-1. OpenAI API キーの準備

1. OpenAI Platform にログイン（`https://platform.openai.com/`）
2. API キーを作成
3. キーを `OPENAI_API_KEY` に設定

- 用途: main job の英語タイトル翻訳
- 未設定時: 翻訳をスキップし、元タイトルのまま配信（処理は継続）

### 3-2. メール送信元の準備

1. 送信元メールアドレスを用意（例: Gmail）
2. Gmail の場合は 2 段階認証を有効化
3. **アプリパスワード**を発行
4. 以下を設定
   - `MAIL_FROM` = 送信元メールアドレス
   - `MAIL_PASSWORD` = アプリパスワード

> 注意: `MAIL_PASSWORD` は通常のログインパスワードではなく、アプリパスワードになるケースが多いです。

### 3-3. Notion API の準備（special job を Notion で管理する場合）

1. Notion の My integrations で Integration を作成
2. Integration Token を取得し `NOTION_TOKEN` に設定
3. special news 用 DB ページを開く
4. 右上 `...` → `Connections`（または `Add connections`）で作成した Integration を接続
5. DB URL から DB ID を取得し `NOTION_SPECIAL_NEWS_DB_ID` に設定

#### DB ID の見方

Notion の DB URL 例:

`https://www.notion.so/<workspace>/<32桁ID>?v=...`

この `<32桁ID>`（ハイフンあり/なしどちらでも可）を使います。

### 3-4. Notion データベースの準備

special job の DB は「媒体ごとの収集ルール」を管理します。

- main 用 DB は不要
- special 用 DB では、以下の列を用意してください

#### 最低限必要な列（special news DB）

| 列名 | 型の例 | 意味 |
|---|---|---|
| `MediaName` | Title / Rich text | 媒体名 |
| `Enabled` | Checkbox | その媒体を有効化 |
| `GoogleAlertFeeds` | Rich text | RSS URL（改行区切りで複数可） |
| `GoogleAlertIds` | Multi-select など | 件名表示用ID（任意） |
| `DisplayOrder` | Number | メール内の表示順 |
| `MaxItemsPerMedia` | Number | 媒体ごとの上限件数 |
| `DeliveryEnabled` | Checkbox | 配信全体ON/OFF |
| `MaxItemsTotal` | Number | 全媒体合計の上限件数 |
| `SubjectPrefix` | Rich text | 件名プレフィックス |
| `DateSourceType` | Select | 日付取得元（`rss`/`article_html`/`meta`/`json_ld`/`url`） |
| `DateParsePattern` | Rich text | 日付抽出用正規表現 |
| `DateCssSelector` | Rich text | HTML抽出時のセレクタ |
| `DateTimezone` | Rich text | 例: `Asia/Tokyo` |
| `DateGranularity` | Select | `datetime` または `date` |
| `TargetDateMode` | Select | `rolling_24h` または `calendar_day` |
| `LookbackHours` | Number | rolling 判定窓（時間） |
| `FallbackDateSourceType` | Select | 主抽出失敗時の代替 source |
| `FallbackDateParsePattern` | Rich text | 代替時の正規表現 |

#### 入力例（推奨）

| MediaName | Enabled | GoogleAlertFeeds | DateSourceType | DateCssSelector | DateGranularity | TargetDateMode | DateParsePattern |
|---|---:|---|---|---|---|---|---|
| 日刊産業新聞 | ✅ | `https://news.google.com/rss/search?q=site:sangyo-times.jp ...` | `url` | *(空欄)* | `date` | `calendar_day` | `news-t(\\d{4})(\\d{2})(\\d{2})\\d+\\.html` |
| 日刊鉄鋼新聞 | ✅ | `https://news.google.com/rss/search?q=site:tekko.co.jp ...` | `article_html` | `time.article-header__published` | `datetime` | `rolling_24h` | `\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}|\\d{4}/\\d{1,2}/\\d{1,2}\\s+\\d{1,2}:\\d{2}` |

### 3-5. GitHub Secrets / Variables の設定

1. GitHub リポジトリを開く
2. `Settings` → `Secrets and variables` → `Actions`
3. `New repository secret` で機密値を追加
4. `Variables` には機密でない値のみ追加

#### 推奨整理

- **Secrets**（必須）
  - `OPENAI_API_KEY`
  - `MAIL_FROM`
  - `MAIL_PASSWORD`
  - `MAIL_TO`
  - `SPECIAL_NEWS_MAIL_TO`
  - `SPECIAL_NEWS_MAIL_CC`（必要時）
  - `SPECIAL_NEWS_MAIL_BCC`（必要時）
  - `NOTION_TOKEN`
  - `NOTION_SPECIAL_NEWS_DB_ID`
  - `NOTION_SPECIAL_NEWS_ENABLED`
- **Variables**（機密ではない運用値）
  - `OPENAI_MODEL`
  - `SPECIAL_NEWS_MAIL_SUBJECT_PREFIX`
  - `SPECIAL_NEWS_MAX_ITEMS_TOTAL`
  - `SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA`

---

## 4. 環境変数一覧（完全版）

> このリポジトリのコードで参照される env を全列挙しています。

| 変数名 | 必須/任意 | 用途 | 例 | 取得元 | 対象ジョブ | 未設定時の挙動 |
|---|---|---|---|---|---|---|
| `OPENAI_API_KEY` | 任意（main実用上は推奨必須） | タイトル翻訳API認証 | `sk-xxxx` | OpenAI Platform | main / special(all実行時にmain側) | 翻訳せず元タイトルで継続 |
| `OPENAI_MODEL` | 任意 | 利用モデル名 | `gpt-4o-mini` | OpenAIドキュメント | main | 既定値 `gpt-4o-2024-08-06` |
| `OPENAI_MAX_OUTPUT_TOKENS` | 任意 | 翻訳レスポンス上限 | `512` | 任意設定 | main | 既定値 `512` |
| `OPENAI_TRANSLATION_BATCH_SIZE` | 任意 | 翻訳バッチ件数 | `30` | 任意設定 | main | 既定値 `30` |
| `OPENAI_RESPONSE_TRUNCATE_CHARS` | 任意 | ログ出力時の切り詰め文字数 | `500` | 任意設定 | main | 既定値 `500` |
| `TITLE_TRANSLATION_CACHE_PATH` | 任意 | 翻訳キャッシュファイル位置 | `data/title_translation_cache.json` | 任意設定 | main | 既定パスを使用 |
| `MAIL_FROM` | 必須 | SMTPログインユーザー/送信元 | `bot@example.com` | メールサービス/Gmail | both | 未設定だとSMTP認証失敗の可能性が高い |
| `MAIL_PASSWORD` | 必須 | SMTPログインパスワード（アプリPW推奨） | `abcd efgh ijkl mnop` | メールサービス | both | 未設定だとSMTP認証失敗 |
| `MAIL_TO` | mainで必須 | main配信先 | `team@example.com,lead@example.com` | 自組織の配信先 | main | 空なら main 配信スキップ |
| `SPECIAL_NEWS_MAIL_TO` | specialで実質必須 | special To 宛先 | `special@example.com` | 自組織の配信先 | special | To/CC/BCC 全て空なら配信スキップ |
| `SPECIAL_NEWS_MAIL_CC` | 任意 | special CC 宛先 | `cc1@example.com;cc2@example.com` | 自組織の配信先 | special | 空ならCCなし |
| `SPECIAL_NEWS_MAIL_BCC` | 任意 | special BCC 宛先 | `audit@example.com` | 自組織の配信先 | special | 空ならBCCなし |
| `SPECIAL_NEWS_MAIL_SUBJECT_PREFIX` | 任意 | special件名プレフィックス | `【専門紙記事一覧】` | 任意設定 | special | 既定値 `【専門紙記事一覧】` |
| `SPECIAL_NEWS_CONFIG_PATH` | 任意 | local媒体設定JSONのパス | `config/special_news_media.json` | リポジトリ内ファイル | special | 既定パスを使用 |
| `SPECIAL_NEWS_TEMPLATE_PATH` | 任意 | specialメールHTMLテンプレート | `templates/special_news_email.html` | リポジトリ内ファイル | special | 既定パスを使用 |
| `SPECIAL_NEWS_MAX_ITEMS_TOTAL` | 任意 | special全体上限件数 | `50` | 任意設定 | special | 既定値 `50` |
| `SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA` | 任意 | 媒体ごと既定上限 | `20` | 任意設定 | special | 既定値 `20` |
| `SPECIAL_NEWS_WINDOW_HOURS` | 任意 | datetime判定の既定窓 | `24` | 任意設定 | special | 既定値 `24` |
| `NOTION_SPECIAL_NEWS_ENABLED` | 任意（Notion運用時は必須） | Notion設定を使うか | `true` | GitHub Secrets/ローカルenv | special | 未設定/不正値は既定 `False` 扱い（local設定を使用） |
| `NOTION_TOKEN` | Notion運用時必須 | Notion API認証 | `secret_xxx` | Notion integration | special | 無いとNotion読込不可→localへフォールバック |
| `NOTION_SPECIAL_NEWS_DB_ID` | Notion運用時必須 | special設定DBのID | `0123456789abcdef...` | Notion DB URL | special | 無いとNotion読込不可→localへフォールバック |

---

## 5. special-news の設定方法（独立ガイド）

### special-news job で何が起きるか

- 媒体設定を読み込む（Notion or local JSON）
- feed を取得し、記事ごとに日付抽出
- `TargetDateMode` に従って採用判定
- 媒体別に上限適用し、1通のHTMLメール送信

### 媒体ごとの設定を Notion で持つ考え方

- 1 行 = 1 媒体
- 日付抽出ルールを列で管理
- コード変更なしで媒体別チューニング可能

### 媒体別推奨設定

#### 日刊産業新聞（推奨）

- `DateSourceType=url`
- `DateGranularity=date`
- `TargetDateMode=calendar_day`
- `DateCssSelector` は空欄

#### 鉄鋼新聞（推奨）

- `DateSourceType=article_html`
- `DateCssSelector=time.article-header__published`
- `DateGranularity=datetime`
- `TargetDateMode=rolling_24h`
- HTML 抽出が弱い場合でも、実装側に JSON-LD (`NewsArticle.datePublished`) フォールバックがあります

### 媒体設定の入力例

| MediaName | FeedUrl（GoogleAlertFeeds） | AlertId（GoogleAlertIds） | DateSourceType | DateCssSelector | DateGranularity | TargetDateMode |
|---|---|---|---|---|---|---|
| 日刊産業新聞 | `https://news.google.com/rss/search?q=site:sangyo-times.jp ...` | `sangyo` | `url` | *(空欄)* | `date` | `calendar_day` |
| 日刊鉄鋼新聞 | `https://news.google.com/rss/search?q=site:tekko.co.jp ...` | `tekko` | `article_html` | `time.article-header__published` | `datetime` | `rolling_24h` |

### よくある誤設定

- Feed URL が Google Alert ではなく通常ページ URL
- Alert ID が空/意図しない値
- `DateGranularity=date` なのに `TargetDateMode=rolling_24h` にしている
- `DateCssSelector` が誤っており HTML から日付を拾えない
- Notion の列名が実装名と違う（特に `GoogleAlertFeeds`, `DateCssSelector`）

---

## 6. 実行方法

### ローカル実行

```bash
python news_digest.py --job main
python news_digest.py --job special
python news_digest.py --job all
```

### 事前準備（ローカル）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 7. ログの見方

special-news でよく見るログの意味です。

- `config source=notion / local` : 設定の取得元
- `fetched` : feed から取得できた記事数
- `filtered` : 日付判定後に残った件数
- `date-extract` 相当情報 : `DateSourceType` と `adopted_source`
- `decision` : `accepted` / `target_date_mismatch` / `out_of_window`
- `extraction_failed` : 日付抽出失敗（記事は除外）

### 正常系ログ例

```text
Special-news config source=notion media_count=2 delivery_enabled=True max_items_total=50
Special-news media=日刊鉄鋼新聞 feed=... fetch=success fetched=12
Special-news media=日刊鉄鋼新聞 ... adopted_source=article_html article_dt=... decision=accepted
Special-news email delivered successfully; total_items=8
```

### 異常系ログ例

```text
Failed to fetch Notion special-news config: timed out; fallback to local config
Special-news media=日刊産業新聞 ... extraction_failed reason=no_date_match
Special-news recipients are empty; skip delivery
```

---

## 8. トラブルシューティング（症状別）

### 1) Notion config fetch timeout

- 原因: Notion API通信失敗・一時的タイムアウト
- 確認ポイント: `NOTION_TOKEN` / `NOTION_SPECIAL_NEWS_DB_ID` / ネットワーク
- 修正方法: 値を再設定し再実行。継続する場合は local config で一旦運用

### 2) `fallback to local config` になった

- 原因: Notion無効、認証不足、DB空、取得失敗
- 確認ポイント: `NOTION_SPECIAL_NEWS_ENABLED=true` か、DBに行があるか
- 修正方法: Notion有効化・DB共有・DB行追加

### 3) Google Alert feed が 0 件

- 原因: 検索条件が狭い/更新がない
- 確認ポイント: feed URL をブラウザで開き記事があるか
- 修正方法: クエリ緩和、複数 feed 追加

### 4) `parse warning bozo` が出る

- 原因: feed XML が不正気味
- 確認ポイント: 同一feedで継続発生するか
- 修正方法: feed変更を検討。warningでも entries 取得できる場合あり

### 5) メールが送れない

- 原因: SMTP認証失敗、宛先空、送信制限
- 確認ポイント: `MAIL_FROM` / `MAIL_PASSWORD` / 宛先 env
- 修正方法: Gmailはアプリパスワードを再発行

### 6) `NOTION_SPECIAL_NEWS_ENABLED` が効かない

- 原因: 値が `true/1/yes/on` 以外
- 確認ポイント: 余計な空白・表記ゆれ
- 修正方法: `true` を明示設定

### 7) DB ID が間違っている

- 原因: page ID と DB ID を混同
- 確認ポイント: DB URL の 32 桁 ID か
- 修正方法: DBページURLから再取得

### 8) Notion の列名不足で設定が読めない

- 原因: 列名が実装名と不一致
- 確認ポイント: `MediaName`, `Enabled`, `GoogleAlertFeeds`, `Date*` 系が一致しているか
- 修正方法: 列名を README の表に合わせる

### 9) 日刊産業新聞が 0 件になる

- 原因: URLの日付パターン不一致
- 確認ポイント: `DateSourceType=url` と `DateParsePattern` が合っているか
- 修正方法: 実URLに合わせて正規表現調整

### 10) 鉄鋼新聞が 0 件になる

- 原因: CSSセレクタ不一致、記事HTML変更
- 確認ポイント: `DateCssSelector=time.article-header__published`
- 修正方法: セレクタ更新、必要なら fallback source 追加

### 11) `total_items=0` になる

- 原因: 全媒体で日付判定に落ちる/配信対象なし
- 確認ポイント: `decision=target_date_mismatch` や `extraction_failed`
- 修正方法: DateGranularity と TargetDateMode の組み合わせを見直す

---

## 9. 最低限の動作確認チェックリスト

- [ ] `OPENAI_API_KEY` を設定した
- [ ] `MAIL_FROM` / `MAIL_PASSWORD` を設定した
- [ ] main 用の `MAIL_TO` を設定した
- [ ] special 用の `SPECIAL_NEWS_MAIL_TO`（必要なら CC/BCC）を設定した
- [ ] `NOTION_TOKEN` を設定した（Notion運用時）
- [ ] DB を Integration に共有した（Notion運用時）
- [ ] `NOTION_SPECIAL_NEWS_DB_ID` が正しい
- [ ] special news DB の行が `Enabled=True`
- [ ] feed URL が有効
- [ ] ローカルで `python news_digest.py --job special` が通る
- [ ] GitHub Actions の手動実行（workflow_dispatch）で成功した

---

## 10. 保守運用ルール

- 媒体追加時: まず Notion DB に行追加（feed・日付ルール）
- 日付判定の微調整: Notion の `Date*` 列を修正
- コード変更が必要なケース:
  - 新しい日付取得方式を追加したい
  - Notion列仕様そのものを変更したい
  - メールフォーマットを変えたい
- README 更新が必要なケース:
  - env が増減した
  - Notion 列名を変更した
  - workflow の Secrets 方針を変更した

---

## `.env.example`（コピペ用）

```dotenv
# =========================
# 共通（メール）
# =========================
MAIL_FROM=bot@example.com
MAIL_PASSWORD=abcd efgh ijkl mnop

# main job 宛先（カンマ/セミコロン/改行区切り可）
MAIL_TO=team@example.com,lead@example.com

# =========================
# main job（翻訳）
# =========================
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_OUTPUT_TOKENS=512
OPENAI_TRANSLATION_BATCH_SIZE=30
OPENAI_RESPONSE_TRUNCATE_CHARS=500
TITLE_TRANSLATION_CACHE_PATH=data/title_translation_cache.json

# =========================
# special job（宛先）
# =========================
SPECIAL_NEWS_MAIL_TO=special@example.com
SPECIAL_NEWS_MAIL_CC=manager@example.com
SPECIAL_NEWS_MAIL_BCC=audit@example.com
SPECIAL_NEWS_MAIL_SUBJECT_PREFIX=【専門紙記事一覧】

# =========================
# special job（設定ソース）
# =========================
NOTION_SPECIAL_NEWS_ENABLED=true
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxx
NOTION_SPECIAL_NEWS_DB_ID=0123456789abcdef0123456789abcdef

# Notionが使えない時のローカル設定
SPECIAL_NEWS_CONFIG_PATH=config/special_news_media.json

# special job（テンプレート/件数制御）
SPECIAL_NEWS_TEMPLATE_PATH=templates/special_news_email.html
SPECIAL_NEWS_MAX_ITEMS_TOTAL=50
SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA=20
SPECIAL_NEWS_WINDOW_HOURS=24
```

---

## GitHub Secrets 設定例（推奨分類）

### Secrets に入れる（機密）

- `OPENAI_API_KEY`
- `MAIL_FROM`
- `MAIL_PASSWORD`
- `MAIL_TO`
- `SPECIAL_NEWS_MAIL_TO`
- `SPECIAL_NEWS_MAIL_CC`
- `SPECIAL_NEWS_MAIL_BCC`
- `NOTION_TOKEN`
- `NOTION_SPECIAL_NEWS_DB_ID`
- `NOTION_SPECIAL_NEWS_ENABLED`

### Variables に入れる（非機密）

- `OPENAI_MODEL` (`gpt-4o-mini` など)
- `SPECIAL_NEWS_MAIL_SUBJECT_PREFIX` (`【専門紙記事一覧】` など)
- `SPECIAL_NEWS_MAX_ITEMS_TOTAL` (`50` など)
- `SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA` (`20` など)
- `SPECIAL_NEWS_WINDOW_HOURS` (`24` など)

---

## 補足（workflow との整合）

- main workflow は `daily.yml`
- special workflow は `special_news_delivery.yml`
- 既存 workflow に `DEEPL_API_KEY` の記載がありますが、現行コードでは参照していません（設定しても未使用です）

