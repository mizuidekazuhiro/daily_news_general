# daily_news_general

## special-news の日付抽出ルールを Notion で管理する設計

`--job special` のみを対象に、媒体ごとの日付取得ロジックをコード分岐ではなく **Notion DB 設定**で切り替える方式に変更しました。

---

## 1. 変更概要

- special-news の媒体設定に「日付抽出ルール」を追加。
- `DateSourceType`（rss / article_html / meta / json_ld）を起点に共通抽出エンジンが日付を抽出。
- `DateParsePattern` / `DateCssSelector` / `DateTimezone` / `DateGranularity` / `TargetDateMode` / `LookbackHours` を媒体ごとに適用。
- 主抽出に失敗した場合は `FallbackDateSourceType` + `FallbackDateParsePattern` を試行。
- `DateGranularity=date` は日付単位比較、`datetime` は rolling 24h 比較に対応。
- Notion が未設定・不正・空の場合は既定値を使って安全に動作。
- main job（`--job main`）には非影響。

---

## 2. 追加した Notion プロパティ一覧

以下を Notion DB（SpecialNewsDeliveryConfig）に追加してください。

- `DateSourceType` (Select)
  - `rss`
  - `article_html`
  - `meta`
  - `json_ld`
- `DateParsePattern` (Rich text)
- `DateCssSelector` (Rich text)
- `DateTimezone` (Rich text) 例: `Asia/Tokyo`
- `DateGranularity` (Select)
  - `datetime`
  - `date`
- `TargetDateMode` (Select)
  - `rolling_24h`
  - `calendar_day`
- `LookbackHours` (Number) 例: `24`
- `FallbackDateSourceType` (Select)
  - `rss`
  - `article_html`
  - `meta`
  - `json_ld`
- `FallbackDateParsePattern` (Rich text)

既存プロパティ（MediaName / Enabled / GoogleAlertFeeds など）はそのまま利用します。

---

## 3. 既定値の仕様

Notion の値が未設定でも動くよう、以下の既定値を持ちます。

- `DateSourceType`: `rss`
- `DateParsePattern`: `""`
- `DateCssSelector`: `""`
- `DateTimezone`: `Asia/Tokyo`
- `DateGranularity`: `datetime`
- `TargetDateMode`: `rolling_24h`
- `LookbackHours`: `24`
- `FallbackDateSourceType`: `""`（未使用）
- `FallbackDateParsePattern`: `""`

### 初期プリセット（媒体名一致時）

- 日刊鉄鋼新聞
  - DateSourceType=`article_html`
  - DateParsePattern=`\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}`
  - DateGranularity=`datetime`
  - TargetDateMode=`rolling_24h`
  - DateTimezone=`Asia/Tokyo`
- 日刊産業新聞
  - DateSourceType=`article_html`
  - DateParsePattern=`\d{4}年\d{1,2}月\d{1,2}日`
  - DateGranularity=`date`
  - TargetDateMode=`calendar_day`
  - DateTimezone=`Asia/Tokyo`

> Notion 値があればプリセットより Notion を優先します。

---

## 4. 日付抽出の優先順位

1. 主抽出（`DateSourceType` + `DateParsePattern`）
2. 主抽出が失敗し、`FallbackDateSourceType` がある場合はフォールバック抽出
   - `FallbackDateParsePattern` があればそれを使用
   - 未設定なら `DateParsePattern` を再利用
3. それでも失敗した記事はスキップ

### SourceType ごとの挙動

- `rss`: feed の `published_parsed / updated_parsed / published / updated` から日時取得
- `article_html`: 記事 HTML 本文（または selector ヒット確認後）に正規表現を適用
- `meta`: meta タグの content 値群に正規表現を適用
- `json_ld`: `application/ld+json` ブロック群に正規表現を適用

---

## 5. 判定ルール（Granularity / TargetDateMode）

- `DateGranularity=datetime`
  - `TargetDateMode=rolling_24h` なら `now - LookbackHours` 〜 `now` で判定
- `DateGranularity=date`
  - 日付（YYYY-MM-DD）単位で当日一致判定
  - `TargetDateMode=calendar_day` を推奨

---

## 6. ログ出力（special-job）

各記事判定時に以下を出力します。

- media名
- DateSourceType
- DateGranularity
- TargetDateMode
- 採用した抽出元（adopted_source）
- 抽出失敗理由（extraction_failed reason）

---

## 7. 媒体追加時に Notion で何を設定すればよいか

新規媒体行を追加し、最低限以下を設定します。

1. `Enabled` = ON
2. `MediaName`
3. `GoogleAlertFeeds`（改行区切り URL）
4. `DisplayOrder`
5. `MaxItemsPerMedia`
6. 日付抽出ルール
   - `DateSourceType`
   - `DateParsePattern`（rss 以外では実質必須）
   - `DateTimezone`
   - `DateGranularity`
   - `TargetDateMode`
   - 必要なら `DateCssSelector`, `LookbackHours`, `Fallback*`

運用開始時はまず `FallbackDateSourceType=rss` を入れておくと、HTML 抽出失敗時の取りこぼしを減らせます。

---

## 8. local JSON フォールバック（Notion 無効時）

`config/special_news_media.json` でも同じ日付ルールキーを定義できます。

- `date_source_type`
- `date_parse_pattern`
- `date_css_selector`
- `date_timezone`
- `date_granularity`
- `target_date_mode`
- `lookback_hours`
- `fallback_date_source_type`
- `fallback_date_parse_pattern`

---

## 9. 実行コマンド

```bash
python news_digest.py --job special
```

- main: `python news_digest.py --job main`
- 両方: `python news_digest.py --job all`

