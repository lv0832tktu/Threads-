# 公式APIを使う競合・検索サンプル分析

初期設定は無効です。この作業では実投稿・実データ取得・Actions実行・定期実行有効化を行っていません。
有料AI API・Webスクレイピング・非公開投稿へのアクセス・競合の文章転載は行いません。

## 確認できた対応と、未確認の部分

Meta公式 `fbsamples/threads_api` の2026-10-08取得版
（コミット `854fc140a37e20f6a7086cf3ee0065f99d41f646`）で以下を確認しました。

- `keyword_search`：`q`、`search_type=TOP/RECENT`、投稿フィールドを指定するGETが公式サンプルに存在。
- `profile_posts`：公開プロフィールの正確なusernameを指定するGETが公式Postman定義に存在。
- OAuth scope一覧に `threads_keyword_search`、`threads_profile_discovery`、`threads_basic` が存在。
- プロフィール探索の `profile_lookup` でプロフィール全体のlikes_count/replies_count/reposts_count/quotes_count/views_count等を扱う公式実装が存在。
- 公式の投稿フィールド一覧には投稿別反応数が見当たらないため、投稿別の数値が取れるとは保証しない。

参照：
- https://github.com/fbsamples/threads_api/blob/854fc140a37e20f6a7086cf3ee0065f99d41f646/src/index.js
- https://github.com/fbsamples/threads_api/blob/854fc140a37e20f6a7086cf3ee0065f99d41f646/postman/threads-api.postman_collection.json
- https://developers.facebook.com/docs/threads/keyword-search/
- https://developers.facebook.com/docs/threads/profile-discovery/

開発者ドキュメント本体はこの環境のネットワークで403になりました。公式サンプルは最新機能を全て示す保証がない旨を明記しています。
したがって最新のAPIバージョン、アプリ審査・アクセスレベル・個別トークンの利用可否はまだ実証していません。
検索にはthreads_keyword_search、公開プロフィール探索にはthreads_profile_discovery、基本読取にはthreads_basicを確認・必要に応じ再認証してください。
自分のInsightsには既存のthreads_manage_insightsが必要です。
権限追加だけで全アカウントのアクセスが保証されるわけではありません。承認後に読取を試し、400/403・期限切れ等は取得不可として扱います。

## 設定

`config/market.json` の初期値はenabled=false、競合・キーワード・テーマ分類はいずれも空です。
競合は `@` やURLを含めない正確なusername、キーワードは100文字以内、最大10件ずつです。
テーマはnameとtermsを設定し、本文中の分類語との一致で分類します。自然言語を完全に理解するAI分類ではありません。

```json
{
  "enabled": false,
  "timezone": "Asia/Tokyo",
  "competitors": ["公開アカウントのusernameに置き換える"],
  "keywords": ["検索する語句"],
  "themes": [{"name": "作業の工夫", "terms": ["作業", "習慣"]}],
  "search_type": "RECENT",
  "profile_summary": true,
  "max_pages": 2,
  "page_size": 25,
  "request_limit": 20,
  "weekly_only": true
}
```

上記usernameのプレースホルダーは実際のASCII usernameへ置き換える必要があります。
最大ページ数・件数・API呼び出し数を制限し、サンプル取得だけを行います。取得範囲は直近7日です。
キーワード検索の結果・TOPの順位・設定語の分類はThreads全体の流行や反応の原因を証明しません。
前週がないと増減を表示せず、前週があっても検索条件やサンプル数の影響を明記します。

競合リストや検索語自体が非公開戦略なら、公開の設定ファイルへ実値を保存しないでください。
代わりにprivate/market-config.jsonへ保存し、Actionsでは同じJSONを `THREADS_MARKET_CONFIG` Secretへ設定できます。
Secretがある場合はprivate内へ展開して公開の設定より優先します。これは任意で、値をログに出しません。

## データとレポート

取得エンドポイントは公式graph.threads.netのkeyword_search/profile_posts/profile_lookupだけです。
他人のInsightsを取得する経路、ブラウザー操作、Cookie認証、非公開投稿の回避取得はありません。
ページングのnext URLを直接開かず、固定の公式ホストへafterカーソルだけを渡します。認証付きリダイレクトも拒否します。

競合本文はメモリ内でテーマ・フック形式・文字数へ変換し、保存しません。
保存するのは投稿ID・公開日時・分類・公式投稿URL・返された数値・取得元です。同一投稿は複数検索で重複しても1件にまとめます。
取得されない投稿別反応数はnull、レポートでは「取得不可」です。0が実際に返った場合だけ0を記録します。
プロフィール全体の数値は別欄とし、投稿別・週別反応数や閲覧数と解釈しません。

`private/market-data.json` と `private/market-report.md` はGit管理対象外です。
Actionsでは既存のTHREADS_STATE_KEYで暗号化し、state/private-state.encだけを永続化します。
レポート本文は公開ログやArtifactsへ出しません。既存の投稿・予約・履歴も維持します。

レポートは日本語で、サンプル内のテーマ・フック・文字数、取得できた公開反応数、前週サンプル比較、根拠URLを含みます。
自分のprivate/insights.jsonから同時期の公開投稿を、取得時点・遅延別に集計し、文字数・フック・自分の反応率を参考比較します。
競合側の閲覧数や同じ投稿年齢での数値が不明なら、反応率ランキングは作りません。
次週テーマ候補、改善する要素、独自構成の仮説、次週21本をChatGPT Plusへ依頼する文面を生成します。
競合の表現・特徴的な言い回し・事例をコピーせず、自分の経験と確認済み根拠で文章を作るよう明記します。

## 承認後の実行方法

今回これらのAPI読み取りコマンドは実行していません。承認前にenabledをtrueにしないでください。

```bash
# 初期設定では無効と表示され、APIやキーを要求しない
python3 -m threads_publisher market
# 読み取り承認後、設定とトークン・暗号鍵を安全に注入した環境でのみ実行
python3 -m threads_publisher market --private-state --market-config private/market-config.json
# 保存済みデータからローカルレポートだけ再生成（API呼び出しなし）
python3 -m threads_publisher market-report --private-state
```

API読取にはTHREADS_ACCESS_TOKEN、暗号状態にはTHREADS_STATE_KEYを使います。有料AI APIキーは不要です。
ローカルコマンドは--persist-gitを付けなければpushしません。
正常な週次取得がある場合、同じ日本時間の週の再取得をスキップします。部分失敗は保存し、後の明示的な実行で再確認できます。
レート制限や権限不足を回避する自動リトライはしません。

追加ワークフローは `.github/workflows/market.yml`、予定は月曜08:00（日本時間）、標準ubuntu-latestです。
Actionsは `THREADS_MARKET_ENABLED=true` と設定のenabled=trueの両方がないと取得しません。
今回新規Variableは登録・有効化せず、設定のenabledもfalseのままです。
このジョブは投稿処理を呼ばず、公開許可スイッチとも独立しています。
承認後にGitHubへ反映して権限を確認し、まず手動の読取で検証してください。今回は実装・ローカルテストのみです。
