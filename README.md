# daily_news_general

特定ソースのニュースを収集し、**記事タイトル（英語）を日本語に翻訳**してメール配信するためのニュース配信プログラムです。**本文の翻訳や要約は行いません**。翻訳には DeepL ではなく **OpenAI API** を使用し、**バッチ翻訳 + キャッシュ + 重複排除**でコスト最小化を図っています。

---

## 1. 概要（何をする／しない）

### すること
- Google News の RSS から **特定ソースの記事**を取得する。
- **記事タイトルのみ**を日本語に翻訳する（英語タイトルが対象）。
- 日本語タイトルを **メール本文（HTML）として出力**する。

### しないこと
- **本文の翻訳**（タイトル以外は扱いません）。
- **要約生成**（要約は一切行いません）。

> 実装は `news_digest.py` に集約されています。【F:news_digest.py†L1-L353】

---

## 2. 全体アーキテクチャ（処理フロー）

1. **取得**：Google News RSS を読み込み  
2. **フィルタ**：媒体ごとの取得対象やノイズ除去  
3. **重複排除**：正規化タイトルで重複排除  
4. **翻訳**：英語タイトルのみ OpenAI API でバッチ翻訳  
5. **キャッシュ保存**：翻訳結果を JSON に保存  
6. **出力**：メール本文（HTML）として送信

実際のフローは `generate_html()` 内で処理されます。【F:news_digest.py†L206-L335】

```
取得 → フィルタ → 重複排除 → タイトル翻訳（OpenAIバッチ）
     → キャッシュ保存 → メール出力
```

---

## 3. コスト最小化の工夫（重要）

- **タイトルだけ送る**：本文は OpenAI に送らない。  
- **バッチ翻訳**：複数タイトルをまとめて 1 API コールにする。  
- **キャッシュ**：同一タイトルは再翻訳しない。  
- **チャンク上限**：`OPENAI_TRANSLATION_BATCH_SIZE` でバッチサイズ制御。  
- **出力上限**：`OPENAI_MAX_OUTPUT_TOKENS` で生成トークンを制限。  
- **失敗時は英語のまま継続**：API失敗時に無限リトライしない。

これらは `translate_titles_to_ja()` で実装されています。【F:news_digest.py†L133-L205】

---

## 4. セットアップ手順

### 前提
- Python 3.x（一般的に 3.9 以上を推奨）
- `pip` が利用可能

### インストール
```bash
pip install -r requirements.txt
```
`feedparser` と `openai` を使用します。【F:requirements.txt†L1-L2】

### 環境変数

#### 必須
- `OPENAI_API_KEY`：OpenAI API キー  
- `MAIL_FROM`：送信元メールアドレス  
- `MAIL_TO`：送信先メールアドレス  
- `MAIL_PASSWORD`：SMTP パスワード（Gmailの場合はアプリパスワード）

#### 任意（チューニング）
- `OPENAI_MODEL`：翻訳に使うモデル（既定: `gpt-4o-mini`）  
- `OPENAI_MAX_OUTPUT_TOKENS`：翻訳出力の上限トークン数（既定: `512`）  
- `OPENAI_TRANSLATION_BATCH_SIZE`：1回の翻訳で処理するタイトル数（既定: `30`）  
- `TITLE_TRANSLATION_CACHE_PATH`：翻訳キャッシュの保存先（既定: `data/title_translation_cache.json`）

#### 不要になったもの
- **DeepL は使用しません**（このリポジトリでは不要）。

> 変数の読み込みとデフォルト値の定義は `news_digest.py` にあります。【F:news_digest.py†L63-L92】

### .env サンプル
```env
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_OUTPUT_TOKENS=512
OPENAI_TRANSLATION_BATCH_SIZE=30
TITLE_TRANSLATION_CACHE_PATH=data/title_translation_cache.json

MAIL_FROM=you@example.com
MAIL_TO=recipient@example.com
MAIL_PASSWORD=app-password
```

---

## 5. 実行方法

### ローカル実行
```bash
python news_digest.py
```
実行すると、24時間以内のニュースを集めてメール送信します。【F:news_digest.py†L206-L353】

### GitHub Actions
現時点では **Actions 設定はありません**（将来的に追加する場合は cron の UTC/JST 差に注意してください）。

---

## 6. 出力仕様

### 記事データ構造
各記事は以下のフィールドを持ちます：

```json
{
  "media": "Reuters",
  "title": "English title",
  "title_ja": "日本語タイトル",
  "summary": "",
  "score": 2,
  "published": "2024-01-01 12:00",
  "link": "https://example.com"
}
```

> 実際に生成される構造は `generate_html()` 内で定義されています。【F:news_digest.py†L248-L263】

### メール本文
- HTML で整形
- 重要度（★）・媒体名・日時を併記
- 記事リンク付き

メール送信処理は `send_mail()` で行います。【F:news_digest.py†L337-L351】

### Notion 出力
**現状は未実装**です。必要であれば「将来オプション」として追加を検討してください。

---

## 7. キャッシュ仕様

- **キャッシュファイル**：`data/title_translation_cache.json`  
- **キー**：正規化した英語タイトル（空白の正規化）  
- **値**：翻訳済みの日本語タイトル  
- **破損時の扱い**：読み込み失敗時は警告ログを出し、空キャッシュで再生成  

キャッシュの読み書きは `load_translation_cache()` / `save_translation_cache()` で管理しています。【F:news_digest.py†L117-L145】

---

## 8. トラブルシューティング

- **OpenAIが失敗する**  
  - 翻訳が失敗したタイトルは **英語のまま**出力されます（処理停止しません）。【F:news_digest.py†L186-L195】

- **文字化け／長いタイトル**  
  - `OPENAI_MAX_OUTPUT_TOKENS` を調整してください。  

- **レート制限**  
  - バッチサイズを下げる（`OPENAI_TRANSLATION_BATCH_SIZE`）。  

- **環境変数ミス**  
  - `OPENAI_API_KEY` が未設定の場合は英語のまま継続します。  
  - `MAIL_FROM` / `MAIL_TO` / `MAIL_PASSWORD` の未設定は実行時に例外になります。  

---

## 9. 開発者向けメモ

- **設計方針**：  
  - 小さな関数に分割し、ユーティリティは独立させる。  
  - 翻訳処理・キャッシュ処理・出力処理を分離して再利用性を確保。  

- **翻訳モデルや上限の変更**：  
  - `OPENAI_MODEL` / `OPENAI_MAX_OUTPUT_TOKENS` / `OPENAI_TRANSLATION_BATCH_SIZE` を調整。  
  - 実装は `translate_titles_to_ja()` 内を確認。【F:news_digest.py†L146-L205】

---

## 付録：媒体ソース一覧

Google News RSS から以下の媒体を取得します（例）：
- Kallanish
- BigMint
- Fastmarkets
- Argus
- 日経新聞
- Bloomberg
- Reuters
- MySteel

媒体設定は `MEDIA` 定義を参照してください。【F:news_digest.py†L33-L59】
