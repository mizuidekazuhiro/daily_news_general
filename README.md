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

## 変更理由メモ

今回の保守改善の理由と方針は `docs/maintenance_changes_ja.md` にまとめています。
