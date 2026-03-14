# daily_news_general

既存の「主要ニュース配信」に加えて、新機能 **「専門紙記事一覧メール配信」** を追加しました。  
この新機能は **Google Alert 起点**で記事を取得し、**前日更新分のみ**を毎朝 5:00（Asia/Tokyo）に **既存配信とは完全に別宛先・別件名・別HTMLテンプレート** で配信します。

---

## 1. この機能が何をするか

- 鉄鋼新聞・産業新聞（初期対象）の記事を Google Alert フィードから取得
- 実行日の前日（JST）に更新された記事だけを抽出
- 媒体ごとにタイトル+リンクを見やすい HTML メールで配信
- 対象 0 件でも「対象記事はありませんでした」を明記して送信

### 既存ニュース配信との違い

- 既存: `python news_digest.py --job main`
- 新機能: `python news_digest.py --job special`
- 同時実行: `python news_digest.py --job all`

> 既存配信ロジック・送信先とは分離されています。

---

## 2. システム構成

- 取得元: Google Alert RSS（媒体ごとの `alert_feeds`）
- 設定管理:
  - 優先: Notion DB（操作パネル用途のみ）
  - フォールバック: `config/special_news_media.json`
- メール描画: `templates/special_news_email.html`
- 送信: 既存 SMTP 共通処理（件名/宛先/本文は別）

### データの流れ

1. 実行開始（JST で前日を対象日として計算）
2. Notion DB から媒体設定を取得（失敗時は明確にエラー）
3. Notion 無効時はローカル設定 JSON を使用
4. Google Alert フィードを媒体ごとに読み込み
5. 前日更新分にフィルタ、重複排除、件数制限
6. 専門紙専用 HTML テンプレートで本文生成
7. 専門紙専用宛先（To/Cc/Bcc）へ送信

---

## 3. Google Alert をどう参照するか

- 本機能では、媒体設定ごとに `alert_feeds`（Google Alert RSS URL）を保持
- その RSS エントリの `published_parsed / updated_parsed` を JST に変換
- 対象日（前日）に一致する記事のみ採用

---

## 4. Notion は何のために使うか

### 用途（保存するもの）
- 配信 ON/OFF
- 媒体有効/無効
- 媒体名
- Google Alert 識別子（任意）
- Google Alert RSS URL 群
- 表示順
- 件名プレフィックス
- 1媒体あたり最大掲載件数
- 全体最大掲載件数

### Notion に保存しないもの
- 記事本文・記事データ
- 配信結果
- 実行ログ

---

## 5. 必要な環境変数一覧

### 既存配信用（main）
- `MAIL_FROM`
- `MAIL_TO`
- `MAIL_PASSWORD`

### 専門紙配信用（special）
- `SPECIAL_NEWS_MAIL_TO`（必須。未設定ならエラー終了）
- `SPECIAL_NEWS_MAIL_CC`（任意、カンマ区切り）
- `SPECIAL_NEWS_MAIL_BCC`（任意、カンマ区切り）
- `SPECIAL_NEWS_MAIL_SUBJECT_PREFIX`（既定: `【専門紙記事一覧】`）
- `SPECIAL_NEWS_CONFIG_PATH`（既定: `config/special_news_media.json`）
- `SPECIAL_NEWS_TEMPLATE_PATH`（既定: `templates/special_news_email.html`）
- `SPECIAL_NEWS_MAX_ITEMS_TOTAL`（既定: `50`）
- `SPECIAL_NEWS_DEFAULT_MAX_ITEMS_PER_MEDIA`（既定: `20`）

### Notion 操作パネル用
- `NOTION_TOKEN`
- `NOTION_SPECIAL_NEWS_DB_ID`
- `NOTION_SPECIAL_NEWS_ENABLED`（`true`/`false`、既定 `true`）

---

## 6. Notion DB の作成方法（初期スキーマ案）

DB名例: `SpecialNewsDeliveryConfig`

### 必須プロパティ
- `Enabled` (Checkbox): 媒体の有効/無効
- `MediaName` (Title): 媒体名（鉄鋼新聞など）
- `GoogleAlertIds` (Multi-select): Google Alert 識別子
- `GoogleAlertFeeds` (Rich text): RSS URL を改行区切りで記載
- `DisplayOrder` (Number): 表示順
- `MaxItemsPerMedia` (Number): 媒体ごとの最大掲載件数
- `SubjectPrefix` (Rich text): 件名プレフィックス
- `DeliveryEnabled` (Checkbox): 全体配信 ON/OFF
- `MaxItemsTotal` (Number): 全媒体合計の最大件数

### サンプルレコード
- Enabled: ✅
- MediaName: 鉄鋼新聞
- GoogleAlertIds: steel-news-alert
- GoogleAlertFeeds: `https://www.google.com/alerts/feeds/.../steel-news-alert`
- DisplayOrder: 1
- MaxItemsPerMedia: 20
- SubjectPrefix: 【専門紙記事一覧】
- DeliveryEnabled: ✅
- MaxItemsTotal: 50

---

## 7. 配信スケジュール

GitHub Actions で毎日 JST 5:00 に実行（UTC 20:00）。

- Workflow: `.github/workflows/special_news_delivery.yml`
- Cron: `0 20 * * *`

---

## 8. ローカル確認方法

```bash
pip install -r requirements.txt
python news_digest.py --job special
```

確認ポイント:
- 対象日ログ（前日/JST）
- 媒体ごとの取得件数ログ
- フィルタ後件数ログ
- 宛先マスクログ
- 送信成否ログ

---

## 9. 本番運用方法

1. Secrets 設定（SMTP/宛先/Notion）
2. Notion DB を作成して `NOTION_SPECIAL_NEWS_DB_ID` を設定
3. 必要なら `config/special_news_media.json` をフォールバックとして管理
4. 毎朝 5:00 の定期実行で監視

---

## 10. テスト方法

```bash
pytest -q
python -m py_compile news_digest.py
```

---

## 11. よくあるエラー

- `SPECIAL_NEWS_MAIL_TO is required`
  - To 未設定。環境変数を設定してください。
- Notion 設定取得失敗
  - `NOTION_TOKEN` / DB 権限 / DB ID を確認
- Google Alert 取得失敗
  - RSS URL の誤り、期限切れ、アクセス制限を確認
- メール送信失敗
  - `MAIL_FROM` / `MAIL_PASSWORD` / SMTP 制限を確認

---

## 12. 宛先変更方法

- `SPECIAL_NEWS_MAIL_TO`
- `SPECIAL_NEWS_MAIL_CC`
- `SPECIAL_NEWS_MAIL_BCC`

を変更するだけで既存配信へ影響なく反映されます。

---

## 13. 媒体を追加する方法

### Notion 運用時
1. DB に新しい行を追加
2. `MediaName`, `GoogleAlertFeeds`, `DisplayOrder`, `MaxItemsPerMedia` を設定
3. `Enabled` を ON

### JSON 運用時
`config/special_news_media.json` の `media` に1オブジェクト追加。

> 媒体ロジックは媒体非依存で共通処理されるため、設定追加のみで拡張可能です。

---

## 14. HTMLメールの見た目を変更する方法

- `templates/special_news_email.html` を編集
- 媒体セクションや記事リストはプレースホルダ `{media_sections}` に注入

---

## 15. 変更ファイル

- 実装: `news_digest.py`
- 新規テンプレート: `templates/special_news_email.html`
- 新規媒体設定: `config/special_news_media.json`
- スケジュール: `.github/workflows/special_news_delivery.yml`
- テスト: `tests/test_special_news.py`

