# ChatGPT Plus・手動競合・許可済み公式フィードでの週次改善

OpenAI／Groqなどの有料AI APIを呼びません。自分の実績は公式Threads Insights、競合は人が登録した公開URL／CSV、トレンドは明示的に利用許可を確認した公式RSS／Atom／JSONフィードを使います。HTMLページのスクレイピング、認証・アクセス制限の回避、他者本文のコピーは行いません。

## 現在の安全設定

`config/keywords.json.enabled=false` と `config/market.json.enabled=false` を維持します。週次Actionsからkeyword_searchのステップを除きました。手動検索テストも `THREADS_KEYWORD_SEARCH_TEST_ENABLED=true` がなければ検索を実行しません（diagnose-configは通信なし）。検索権限の承認前には有効にしないでください。Variables／Secretsの値は今回変更していません。

`config/trend_sources.json` も初期無効・配信元空です。新しいscheduleは追加せず、既存の無効化された日曜週報へ任意ステップだけ用意しました。`THREADS_WEEKLY_REPORT_ENABLED`、`THREADS_ANALYTICS_ENABLED`、`THREADS_TRENDS_ENABLED`、予約公開スイッチは承認まで未設定／falseを維持します。

## 競合の公開URL／CSVを手動登録

Git管理対象外のprivate配下に置きます。URLを渡してもプログラムは開きません。公開ページを自分で確認した数値だけを記載します。

URLリスト例 `private/competitors.txt`：

```text
https://www.threads.net/@public/post/ABC
```

CSV例 `private/competitors.csv`：

```csv
url,theme,observed_at,characters,hook,likes,replies,reposts,quotes
https://www.threads.net/@public/post/ABC,ChatGPT,2026-10-08T20:00:00+09:00,120,質問型,0,,,
```

未取得は空欄です。0は確認できた0に限ります。閲覧数を競合の公開反応から推測しません。JSON observations配列も使えます。本文・ユーザー名・独自の個人情報欄は取り込みません。公開URL自体には公開アカウント名が含まれることがあります。

```bash
.venv/bin/python -m threads_publisher import-competitors --input private/competitors.csv --private-state
```

既存の `THREADS_STATE_KEY` を安全に環境へ注入します。同じURLと観察時刻は追加せず、不正なバッチは追加しません。URLだけの場合、観察登録日時を記録し、文字数・反応は未知です。投稿日時を推測しません。テーマは設定済みキーワードかAI／仕事術／お金／節約／就活／転職などの分類を使います。未知の分類は公開週報で「未分類」です。

`--private-state` は既存暗号文を復元してから追加し、暗号化状態を更新します。Gitへ暗号文を保存する必要がある場合は `--persist-git` を追加します。平文CSVや本文をコミットしません。mainへの書き込みが許可される必要があり、ブランチ保護を無断で解除しません。

## 許可されたRSS・公式情報

`config/trend_sources.json` に次の形式で設定します。これは形式例で、exampleドメインへの接続や許可を意味しません。

```json
{
  "enabled": false,
  "daily_request_limit": 3,
  "allowed_hosts": ["official.example"],
  "sources": [{
    "url": "https://official.example/feed.xml",
    "kind": "rss",
    "official_source": true,
    "permission_confirmed": true,
    "permission_reference": "https://official.example/terms"
  }]
}
```

公式配信元であること、規約・RSS利用条件・再利用可能範囲を運用者が確認し、根拠URLを記録してからpermission_confirmedをtrueにします。公開ページが存在することだけでは取得許可になりません。明確な許可がなければ登録せず手動で参考資料を確認します。許可の法的有効性をプログラムが判定するものではありません。

rss／atomはUTF-8 XMLフィード、jsonは公式に許可された `{"items":[{"title":"...","url":"https://...","published_at":"タイムゾーン付きISO日時"}]}` 形式の配信に限定します。一般HTMLを取得して本文を抽出する機能はありません。記事本文・リンク先・画像は自動取得しません。

取得はHTTPSの完全一致許可ホストだけ、1配信元1日1回、日最大3回（設定上限5）、1応答1MiB、1フィード50件です。実行前に利用枠を暗号化永続化し、失敗・中断も枠を消費します。DNSの非公開IP、リダイレクト、認証URL、クエリ付きURL、圧縮応答、XML実体・DTDを拒否します。Threadsトークン・Cookieは送りません。URLの配信元と記事リンク、利用許可URLのホストも許可リストへ入れます。

承認後の手動取得コマンドは `trends --private-state`。週次Actionsから行うには、ソースenabledと `THREADS_TRENDS_ENABLED` と週報スイッチをそれぞれ明示的に有効にします。今回取得・有効化はしていません。

## 自分のInsights・週報

既存の公式Insights、24h／72h／7dと直近日次・週次取得、テーマ・時間帯・文字数・閲覧数・反応率の比較を維持します。自身のAPIにはthreads_basic・threads_manage_insightsを確認します。未対応指標は未知です。反応率は `(likes + replies + reposts + quotes) / views`、全値既知かつviews>0のみ計算します。親とツリー返信の閲覧数を合算しません。

実API取得は承認後だけ行います。保存済みデータから通信せずレポートを作るには：

```bash
.venv/bin/python -m threads_publisher career-report --private-state
```

毎週の出力：

- reports/weekly/YYYY-MM-DD.md・json・csv
- reports/weekly/YYYY-MM-DD-prompt.md
- reports/latest.md・json・csv・latest-prompt.md

月曜0時〜日曜21時未満を対象に、観測時刻、未取得、少数データ、対象外の遅延投稿を明示します。日本語CSVは取得不可を記載し、表計算の式として解釈される文字列を無効化します。Markdown／CSVは本文や内部アカウントIDを出さず、暗号化された詳細から公開可能な集計を作ります。手動競合と許可済み公式フィードの根拠URLを含みます。RSSのキーワード一致は流行の証明ではなく検証する仮説です。同じ週は生成済みデータを再利用します。

latest-prompt.mdをChatGPT Plusへ渡し、通常文章7本・画像7本・ツリー7本を作成します。時間は08:00／19:00／22:00 Asia/Tokyo。元の予約・取り込み・人の承認・画像権利とSHA256確認・暗号化・二重投稿防止はそのままです。画像作成もChatGPT Plusで行い、公開URLの設置は承認後です。

## 費用・未検証部分

追加の有料AI契約は不要です。標準GitHub runnerと既存無料Pythonライブラリを使います。非公開リポジトリのActions枠・画像配信帯域を超える場合は費用が生じ得ます。RSS／公式APIの現在の許可・無料枠・実接続を保証しません。今回は模擬フィード・模擬APIと一時ファイルだけで検証し、実データ取得はしていません。
