# 新聞横断ニュースのプレビュー

全国紙、地方紙、通信社、NHKの19媒体を対象とし、取得・保存の条件を確認できた経路から一般ニュースを集めます。同一記事を除き、同じ出来事をまとめ、全候補を順位付けしてから朝夕それぞれ最大30件を選びます。地域を含む国内外のニュースを対象にし、分野・媒体別の枠は設けません。

朝版は冒頭の最大3件を「朝の要点」とし、見出しと要約を同じ項目にまとめます。上位5件に「何が起きたか」「誰にどう影響するか」「次に何を見るか」を掲載します。配信用本文では6位以降も根拠のある要点を短く掲載し、各件に要点の出典と同じ出来事を扱う関連記事へのリンクを付けます。関連記事は媒体の異なる記事を優先し、1件あたり最大8本です。同じ記事へのリンクは1回表示します。夕版は訂正、重要な続報、新規・朝版未掲載の順で掲載し、最も重要な変化を該当する項目に表示します。

## 実行

リポジトリのルートで Python 3.12 以降を使います。依存は既存の要件ファイルからインストールできます。

~~~sh
python -m pip install -r requirements.txt
python -m src.newspaper_briefing --preview --edition morning
python -m src.newspaper_briefing --preview --edition evening
~~~

掲載件数は `--limit 40` のように指定できます。夕版は、朝版で実際に掲載した全件を使って差分を判定します。

朝版は `--as-of` の直近24時間です。夕版は同じJST日に先に確定した朝版の `snapshot.json` を参照します。スナップショットには掲載した出来事と本文に出た事実を保存します。旧形式のスナップショットは同日の `capture.json` を使って再生成できます。続報は事実の変化、訂正は朝版の記述の誤りを示す根拠があるものです。同じURLでも内容の変化を検出します。朝締切以前に公表済みの重要記事が朝版に入っていなければ、当初の公表時刻と「朝版未掲載」を表示します。夕版冒頭の件数は掲載する出来事ごとに数え、同じ出来事に訂正と続報があれば両方に数えます。

比較できる朝版がない夕版は、直近12時間の新着を表示し、訂正・続報の件数を判定不能とします。`--window-hours` でこの期間を1〜24時間に変更できます。取得・比較が正常で更新がなければ0件、部分障害があれば確認できた範囲で0件と表示します。

~~~powershell
$f = 'tests/fixtures/newspaper_briefing'
$out = 'artifacts/newspaper_briefing/summary-verification/fixture'
python -m src.newspaper_briefing --preview --edition morning --as-of '2026-11-24T08:00:00+09:00' --offline-input "$f/morning.json" --source-policy "$f/summary_policy.json" --primary-input "$f/primary_morning.json" --output-dir $out
python -m src.newspaper_briefing --preview --edition evening --as-of '2026-11-24T18:00:00+09:00' --offline-input "$f/evening.json" --source-policy "$f/summary_policy.json" --primary-input "$f/primary_evening.json" --snapshot "$out/2026-11-24/morning/080000/snapshot.json" --output-dir $out
~~~

`--as-of` に元の時刻、`--offline-input` に保存した `capture.json`、`--primary-input` に保存した `primary.json` を指定します。同じソース設定と朝版スナップショットを使えば、日付が変わった後も再実行できます。`--headlines-only` で補足なしの比較を作れます。保存済みの報道データに対して許可済みの一次情報だけを取得するときは、`--primary-input` に代えて `--collect-primary` を指定します。ライブ取得に過去の日時を渡した場合、取得時点のフィードを過去の締切で絞り込みます。

11月24日の実データで確認した再実行例です。

~~~powershell
$base = Join-Path $env:LOCALAPPDATA 'CodexNewspaperBriefing'
$installed = Get-Content -Raw (Join-Path $base 'install.json') | ConvertFrom-Json
$archive = Join-Path $base 'previews/2026-11-24'
$out = Join-Path $base 'verification/replay-2026-11-24'
$previewOptions = @('--preview', '--publication-mail', '--config', $installed.Config,
  '--source-policy', $installed.SourcePolicy, '--output-dir', $out)
python -m src.newspaper_briefing @previewOptions --edition morning --as-of '2026-11-24T08:55:23+09:00' --offline-input "$archive/morning/085523/capture.json"
python -m src.newspaper_briefing @previewOptions --edition evening --as-of '2026-11-24T18:00:03+09:00' --offline-input "$archive/evening/180003/capture.json" --snapshot "$out/2026-11-24/morning/085523/delivery/snapshot.json"
~~~

保存済みの一次情報を含めるときは、それぞれのコマンドに `--primary-input` と該当する `primary.json` を指定します。

## 保存ファイル

既定の保存先は `artifacts/newspaper_briefing/YYYY-MM-DD/<morning|evening>/HHMMSS/` です。

| ファイル | 内容 |
| --- | --- |
| `report.md`、`report.txt`、`report.html`、`report.json` | メール本文、根拠、要約の状態、収集と比較の状態。JSONに配送結果も記録 |
| `mail.json` | 確定したプレーンテキストとHTML、件名、配信可否、本文のハッシュ |
| `diagnostics.json` | 媒体・入力フィード別の取得元、取得時刻、HTTP状態、取得・期間内・重複除外後・本文掲載への寄与の件数、公開時刻不明、取得失敗、フィードの頭打ちの可能性 |
| `ranking.json` | 16位以下も含む全候補の順位、4項目の点数と上限、根拠リンクと欠測、掲載媒体数、確認済み独立報道数 |
| `capture.json`、`undated.json` | 再実行用の正規化メタデータ、公開日時が確認できなかった候補 |
| `primary.json` | 使用条件を確認した一次情報の正規化済み事実と取得結果 |
| `snapshot.json` | 夕版の比較に使う、朝版で実際に掲載した出来事と事実 |

一部の取得先が失敗してもプレビューを生成します。媒体別収集フィードが全滅した場合は診断と失敗表示を保存し、終了コード `2` を返します。補助フィードのみ成功しても同様です。

媒体別の `received` は各媒体のフィードから取得した件数と、Googleニュース日本版トップで媒体を確認できた件数の合計です。`in_window` は期間内の候補件数で、確認済みの実質更新を含みます。`articles` は同一記事を除いた後の件数、`ranked_stories` はその媒体の記事が入った順位付け済みの出来事件数です。`selected_stories` は掲載対象に選ばれた出来事件数、`published_stories` は実際に本文にその媒体の出典が載った出来事件数です。同じ出来事に同じ媒体から複数の出典があっても、その媒体への寄与は1件です。夕版に引用する朝版の出典も掲載への寄与に含めます。

`publication_stage` の `published` は本文に寄与した媒体、`selection` は候補が順位付けに進み本文に出典が載らなかった媒体、`collection` は取得・期間・公開時刻の判定までで順位に進まなかった媒体です。公開時刻不明は `undated`、取得失敗は各 `sources` の `failure`、フィードの頭打ちの疑いは `possible_cap` で確認できます。

## 取得先と判定

`--config` でソース設定を切り替えられます。既定の [媒体設定](../config/newspaper_briefing.json) に、掲載媒体・地域・確認済みドメインと表示名・取材元グループ・公式フィード・検索クエリを定義しています。媒体の主サイトとRSSの表示名が一致した候補を採用し、関連サービス、求人、広告を除外します。公式RSSにある記事とGoogleニュースRSSの記事は、取得できたURL、GUID、正規化した見出しを使って突き合わせます。出版社リンクが確認できない場合は、取得したGoogleニュースリンクに「Googleニュース経由」と表示します。

`collection` で接続タイムアウト、再試行回数、並列数を設定できます。以前の設定ファイルに残っている `max_articles` は読み込み時に無視します。検索結果が件数上限の目安（既定100件）に達した媒体では、`supplemental_terms` に設定した検索語で補完できます。1媒体3語まで、全体の追加リクエスト数は `max_supplemental_feeds`（既定2、上限12）で制限します。この上限は追加検索の回数に適用します。初期設定は、2026年11月24日の照合で西日本新聞の「災害」で15件、朝日新聞の「科学」で30件の候補増を確認した組み合わせです。

### 利用条件の記録

[ソース利用条件](../config/newspaper_briefing_source_policy.json) は取得経路のIDとURL、確認日、根拠URL、取得・保存・AI入力・メール掲載の可否、制約を用途別に管理します。`--source-policy` で切り替えます。各媒体の説明文は、公式RSSの経路に取得と保存の許可があり、個人用途制限と媒体設定を含めメール掲載が許可される場合にメール本文へ使えます。記事本文の自動取得は無効です。GoogleニュースRSSの関連記事一覧は保存や要約に使いません。

| 経路 | 確認日 | 検証での扱い |
| --- | --- | --- |
| Googleニュースの媒体別検索と日本版トップ | 2026-11-26 | 機械可読な指示でRSS経路を許可する記載がなく、現在の条件での定期取得・保存や第三者コンテンツのAI入力・メール掲載は未確認。保存済み入力を送信なしの再現に使用。[Google 利用規約](https://policies.google.com/terms?hl=ja&gl=JP) · [機械可読な指示](https://news.google.com/robots.txt) |
| NHK公式RSSの8フィード | 2026-11-26 | 個人用プレビューの候補検出で見出し・時刻・リンクを使用。公式案内で再配信・再提供が許可されていないため、メールに載せる事実は別の許可済み資料から確認。[NHKのRSS案内](https://www.nhk.or.jp/toppage/rss/index.html) |
| 沖縄タイムス公式RSS | 2026-11-26 | 取得・保存・AI入力・メール掲載の条件を確認できるまで、定期取得を保留。[沖縄タイムス公式サイト](https://www.okinawatimes.co.jp/) |
| 琉球新報公式RSS | 2026-11-26 | 公式案内の総合RSSは現在トップページへリダイレクトし、RSSを取得できない。メール掲載は未確認。[琉球新報のRSS案内](https://ryukyushimpo.jp/pages/entry-164983.html) |
| 時事ドットコムのアクセスランキング | 2026-11-26 | 利用条件の確認までは定期取得を保留。許可済みの取得データがある場合は閲覧人気の補助指標として使用。[時事ドットコム公式サイト](https://www.jiji.com/) |
| 気象庁の震源・震度情報 | 2026-11-24 | 通常運用の公表済みXMLの観測事実に限定。自動取得・抽出項目の保存・出典と加工の明示付きメール掲載を可とし、AI入力は無効。[気象庁XMLの取得案内](https://xml.kishou.go.jp/xmlpull.html) · [注意事項](https://xml.kishou.go.jp/considerationforxml.pdf) · [気象庁サイトの利用条件](https://www.jma.go.jp/jma/kishou/info/coment.html) |
| 国土交通省・デジタル庁 | 2026-11-26 | 確認した公式RSSの見出し・時刻・URLと、対象となる国土交通省の局所的な冒頭記述を使用。対応するニュースがある場合に出典と作成主体を明示して掲載。[国土交通省の利用条件](https://www.mlit.go.jp/link.html) · [デジタル庁の利用条件](https://www.digital.go.jp/copyright-policy) |
| 外務省・中国運輸局 | 2026-11-26 | 公表済みの一次資料で確認した事実を `--primary-input` の検証データで使用。自動巡回は無効。[外務省の利用条件](https://www.mofa.go.jp/mofaj/annai/legalmatters/index.html) · [中国運輸局の利用条件](https://wwwtb.mlit.go.jp/chugoku/txt/copyright.html) |

気象庁の資料は、見出しに気象庁発表の種別と日時があり、公開時刻が一致する場合に紐づけます。それ以外の一次資料は、時期と見出しが強く一致する場合か、確認した記事URLとの対応がある場合に使います。メディアの取得状況と一次情報の取得結果は `diagnostics.json` で確認できます。

初期配点は影響40、確認済み独立報道30、新しさ20、各社の編集上の扱い10です。`weights` で合計100点の範囲で変更できます。影響は見出しに明記された警報、生活への具体的影響、制度決定、研究・文化・競技での成果などで判定します。独立報道数の加点は逓減し、明示された通信社原稿・同じ取材グループの重複をまとめます。掲載元しか確認できない検索結果では、独立報道の加点を保留します。各社が編集する主要フィードにある記事にのみ編集点を付けます。同点の並びは影響点、最終更新日時、IDの順です。補足資料は順位確定後に使います。

同じ出来事の候補は、見出しに加えて、施設・道路区間・発生場所・被害規模・発生時刻などで探します。雪による停電は継続報道と現地の影響を、共通する地域の範囲でまとめます。明記された市町村や停電原因が異なる場合は別件として扱います。広域報道が複数の地域を取り上げていても、地域が異なる個別の停電を一律にまとめません。複数の火災を集計した記事には「複数の出来事の概況」と表示します。

震度1〜2の観測発表や時刻中心の定型速報が複数あるときは、本文に「地震速報まとめ」として1件で掲載します。確認できた震源・時刻・最大震度をまとめ、各速報に出典を付けます。被害、警報、震度3以上の観測、訂正などのニュースは個別に扱います。朝夕比較用のスナップショットには、まとめに載せた個々の速報を記録します。

## AI要約と配信

既定の要約は、ソースに紐づく確認済みの事実から生成します。見出しだけの場合は媒体に帰属させ、未確認の影響や予定を明記します。数字が異なる場合は数値・発表主体・集計条件を対応させます。同条件で値が異なるときは「数字が一致していない」と表示します。

`--ai` は用途を確認したソースでのみ、既存の構造化JSONクライアントを使います。通常の統合後に残った候補を全体から探し、暫定順位が高い出来事から最大60組を比較します。AIには許可済みの見出し、公開時刻、両方の見出しに実際に含まれる共通箇所を渡します。場所や記事の種類を抽出できない記事も、見出しの共通部分が十分あれば候補になります。返されたペアIDと具体的な共通箇所を照合し、グループ全体で場所・時点・対象の矛盾がないことを確かめます。3本以上の記事も同じ出来事としてまとめます。取得経路の利用条件と媒体の `allow_ai` の両方を確認します。接続や検証に失敗した場合はルール判定で続行し、`diagnostics.json` の `event_matching_ai` に状態・対象ペア数・統合数を残します。

専用の接続設定は `NEWSPAPER_BRIEFING_OPENAI_API_KEY` と `NEWSPAPER_BRIEFING_OPENAI_MODEL` です。

配信用本文では、掲載する全件について、媒体や発表者の根拠の中からAIが要点を選びます。照合は20組ずつ、要約は10件ずつ処理します。AIが返した出来事と事実の識別子を照合し、保存済みの事実と検証済みの参照URLを取り出して本文を組み立てます。要約できなかった項目は「要約は掲載できませんでした」とし、確認した見出しと記事リンクを載せます。オフライン時はAIを無効にします。

プレビューは配送クライアントへ制御を渡しません。`--preview` と配送フラグの併用はエラーになります。メールを有効化する構成では、掲載する見出し・説明のそれぞれに用途を確認した許可と、媒体の `allow_distribution` が必要です。既定のポリシーでは媒体見出しの配信許可を付与していません。

| 明示フラグ | 専用設定 |
| --- | --- |
| `--send-email` | `NEWSPAPER_BRIEFING_SMTP_TO`（カンマ区切り）、一般ニュース・日経版と共通の `MAIL_FROM` と `MAIL_PASSWORD`。Gmailの587番ポートでSTARTTLSを使用 |
| `--write-notion` | `NEWSPAPER_BRIEFING_NOTION_TOKEN`、`NEWSPAPER_BRIEFING_NOTION_DATABASE_ID`。DB内に複数のデータソースがある場合は `NEWSPAPER_BRIEFING_NOTION_DATA_SOURCE_ID` も指定 |

GitHub Actionsから配信を実行する場合は、その送信ステップに既存の `${{ secrets.MAIL_FROM }}` と `${{ secrets.MAIL_PASSWORD }}` を同名の環境変数で渡します。宛先は新聞横断版の `NEWSPAPER_BRIEFING_SMTP_TO` を指定します。個別のSMTPを使用する場合は `NEWSPAPER_BRIEFING_SMTP_HOST`、`NEWSPAPER_BRIEFING_SMTP_FROM`、`NEWSPAPER_BRIEFING_SMTP_USERNAME`、`NEWSPAPER_BRIEFING_SMTP_PASSWORD` をまとめて指定します。`NEWSPAPER_BRIEFING_SMTP_PORT` は任意で既定値は587です。

件名は「新聞横断ニュース｜朝または夕｜YYYY-MM-DD」です。実行環境ごとに専用の `artifacts/newspaper_briefing/delivery.sqlite3` にJST日付・朝夕・チャネルを記録します。出力先を変えても配信記録は共通です。配信処理が中断した場合は同じキーの再送を止めます。結果が `pending` または `unknown` の場合は実際の到達状況を先に確認してください。配信を要求して遮断された場合や、結果を確認できなかった場合は終了コード `3` を返します。

## Gmail連携用の確定本文

リポジトリ内の `scripts/newspaper_briefing_link_mail.py` は同日のライブ出力と `mail.json` の日時・件名・本文ハッシュ・配信可否を照合し、プレビューと同じ確定本文を返します。`prepare` は日付・朝夕・メールで送信枠を確保し、`finish` はGmailの結果を `sent` または `unknown` として専用台帳に記録します。送信済み、処理中、結果不明の号は同じ枠からの再送を止めます。

`--publication-mail` を付けると、順位付け済みのニュースのうち掲載条件を確認できた根拠があるものを `delivery/` に出力します。順位は元の19媒体向け集計から引き継ぎます。根拠資料がある場合、本文はその資料により確認できた事実と出典で構成します。配信用の夕版は、同日朝の `delivery/snapshot.json` を比較に使います。

```powershell
python -m src.newspaper_briefing --preview --publication-mail --edition morning
python -m src.newspaper_briefing --preview --publication-mail --edition evening
```

SMTPで配信する構成では、利用条件を確認したフィードと専用宛先を設定して `--publication-mail --send-email` を使います。十分な根拠を得られなかった実行は送信保留として記録し、夕版の更新件数は判定不能とします。Gmail連携側で `PublicationMail: true` を選ぶ場合は、同じ実行の `delivery/report.json` と `delivery/mail.json` を照合します。`MailLedger` で連携用の送信記録をリリースの外に指定できます。

契約済みの記事配信経路を追加する際は、契約が対象とする媒体・URLと、自動取得・保存・要約の配信・外部AI入力の可否を利用条件の記録に反映します。媒体掲載の認証情報はランタイムから渡します。配信先を指定して運用する前に、保存済みデータのプレビューと新規取得の診断を確認します。

契約の確認を利用者から受け取った場合は、確認日・対象経路・許可用途を利用者専用の `--source-policy` に記録します。参照できる公開資料のURLは `basis_urls`、利用者による確認は `user_confirmation` に記録します。配信を許可する媒体は、その環境の `--config` にある `allow_distribution` で指定します。契約に基づく設定は利用環境に保存します。

## GitHub Actionsでの朝夕メール

[新聞横断ニュース専用の配信Workflow](../.github/workflows/newspaper_briefing_mail.yml) は日本時間8:10、18:10に実行し、それぞれ8:00、18:00を締切にします。GitHub Actionsが収集と本文生成を実行し、既存のGmail SMTP設定で配信します。専用のリポジトリ変数 `NEWSPAPER_BRIEFING_EMAIL_ENABLED` が `true` のときに配信ジョブを実行します。

| 配信用Secrets | 内容 |
| --- | --- |
| `MAIL_FROM`、`MAIL_PASSWORD` | 既存のGmail SMTP設定 |
| `OPENAI_API_KEY`（Secret）、`OPENAI_MODEL`（リポジトリ変数） | 既存のOpenAI設定。新聞横断版の出来事の照合と要点の選定に使用 |
| `NEWSPAPER_BRIEFING_SMTP_TO` | 新聞横断ニュースの宛先 |
| `NEWSPAPER_BRIEFING_CONFIG_JSON`、`NEWSPAPER_BRIEFING_SOURCE_POLICY_JSON` | 配信用の媒体設定と、取得経路ごとの利用条件。各JSONファイルの内容を指定 |
| `NEWSPAPER_BRIEFING_SNAPSHOT_KEY` | 朝の比較基準専用に生成したランダムな32バイトの鍵を16進表記で保存 |

収集・本文生成は `--preview --publication-mail --ai` で実行します。送信ステップは確定した本文と配信可否を照合します。同日同版は配信の予約を記録してからSMTPへ接続し、送信結果が不明な場合も予約がある号の再実行で再送しません。朝版の送信を確認した後、夕版で使う比較基準を暗号化して2日間保持します。本文と取得データは実行ランナー内で扱います。

手動実行では `edition` を指定します。`send` の既定値は `false` で、実フィードを使った送信なしの確認に利用できます。手動で送信する場合は配信変数を有効にし、`send: true` を指定します。同じ日に定時実行の予約がある場合は、手動実行でも再送を止めます。

## 手動Workflowとテスト

[手動プレビューWorkflow](../.github/workflows/newspaper_briefing_preview.yml) は、架空RSSで朝版と夕版を実行し、レポートと診断をアーティファクトに保存します。実フィードでのプレビューは上記のローカルCLIから実行します。

~~~sh
python -m pip install pytest
python -m pytest -q tests/test_newspaper_briefing.py tests/test_newspaper_briefing_summary.py tests/test_newspaper_briefing_link_mail.py tests/test_newspaper_briefing_publication.py
python -m pytest -q --ignore-glob='tests/*_live.py'
~~~

Windowsで既存のテスト一式を実行する際は、`tzdata` と、使用するPythonおよびGit BashがPATHにある環境を用意します。
