# AI・仕事術・お金・節約の運用手順

ジャンルは4つ、投稿は毎日08:00・19:00・22:00 Asia/Tokyo、週21本です。追加の有料AI APIは使いません。キーワード検索APIは権限未承認のため無効、新しい定期実行・公開も無効のままです。

## GitHubで最初にすること

1. 開発PRのFiles changedとテスト結果を確認してmainへマージします。これだけでは投稿を許可しません。
2. Settings → Secrets and variables → Actions → Repository secretsで、既存THREADS_ACCESS_TOKENとTHREADS_STATE_KEYを確認します。値はチャット・コード・ログに貼りません。暗号化キーを新しく置き換えないでください。
3. Variablesの新規定期スイッチ（THREADS_WEEKLY_REPORT_ENABLED、THREADS_ANALYTICS_ENABLED、THREADS_TRENDS_ENABLED、THREADS_SCHEDULED_POSTS_ENABLED、THREADS_AUTO_PUBLISH_ENABLED）は承認までfalse／未設定にします。検索権限がない間はTHREADS_KEYWORD_SEARCH_TEST_ENABLED・THREADS_KEYWORD_RESEARCH_ENABLEDもfalseです。
4. 管理画面はGitHubサイト内には表示されません。自分のPCにリポジトリを取得し、ローカルで起動します。GitHub Pagesや公開Streamlitには配置しません。

## 管理画面の起動

Python 3.12以上・Linux/macOS/WSLの例です。WindowsはWSLを利用してください（排他ロックにfcntlを使います）。

```bash
git clone https://github.com/lv0832tktu/Threads-.git
cd Threads-
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-private.txt -r requirements-media.txt -r requirements-dashboard.txt
```

既存の暗号化キーの控えを安全に環境変数THREADS_STATE_KEYへ注入します。Bashなら `read -s THREADS_STATE_KEY` で画面や履歴に出さず入力し、`export THREADS_STATE_KEY` で起動プロセスへ渡せます。GitHubは登録済みSecretの値を再表示しません。控えがない場合、古い暗号化状態は新規キーで復号できません。キーを表示するActionsは作らず、安全な復号環境を整備してください。管理画面自体にはThreadsトークンが不要です。

```bash
.venv/bin/streamlit run threads_publisher/dashboard.py --server.address 127.0.0.1 --server.maxUploadSize 1
```

同じPCのブラウザで画面を開きます。外部公開・ポート転送・トンネルは禁止で、公開アドレスでの起動は拒否します。公開利用には別途認証・権限制御の設計が必要です。

## 毎週の流れ

1. **はじめに**で既存暗号化状態を読み込みます。
2. **競合登録**で公開投稿URL、テーマ、確認日時、フック、文字数、確認できた反応数を入力します。CSV／JSON／URLリストもアップロードできます。未取得値は空欄、確認した0だけ0。同じURLと確認日時は重複登録しません。URLを自動で開かず、本文を保存しません。
3. **情報源**で、公式RSS・Atom・JSONと利用条件を確認できた配信元だけ登録します。登録しても収集は無効です。[情報源調査](source-research.md)の候補は未確認なので自動登録していません。編集は削除して確認済み内容で再登録します。
4. 自分のInsights取得は、承認後にGitHub Actionsの既存分析ジョブで行います。threads_basic・threads_manage_insightsを確認します。読み取りでも今回の開発では実行していません。GitHubのActions → Threads daily analytics → Run workflowでconfirm_readをチェックすると、その回だけGET取得します。チェックなしは取得しません。週次レポートはfetch_insights=falseなら保存済みデータのみ、trueなら先に取得します。これらの手動操作で定期Variablesを変更する必要はありません。実取得は承認後だけ行ってください。
5. **レポート・ChatGPT**で最新の保存済みデータから通信せず週報を更新します（同じ週の内容も明示的に再作成）。テーマ・時間帯・文字数・冒頭形式・閲覧・反応率を比較し、未取得を推測しません。レポートのコード欄のコピーボタンでlatest-prompt.mdをChatGPT Plusへ渡します。
6. ChatGPTで通常文章21本（初期設定）を作ります。混合形式が必要なら文章7・画像7・ツリー7を選びます。AI・仕事術・お金・節約をバランスよく、金融情報には公式出典と確認日を付けます。利益保証・誇張・架空の実体験を避けます。
7. **投稿案・承認**で21本のJSONと翌週の開始日を指定して取り込みます。全件未承認・予約なしです。本文は日本語のテキスト欄で編集し、ツリーは各返信を編集します。画像URL・SHA256などは追加のJSON欄を使います。編集すると承認・予約が解除されます。
8. 内容・根拠・権利・日時を確認し、チェックを入れて一件ずつ承認します。**予約・書き出し**で承認済みだけ予定日時で予約します。旧12:00／20:00の案は自動変更しないため、予定案を希望時刻へ変更して再承認してください。
9. Secret用JSONを作り、4ファイルを保存します。GitHub Repository secretsのTHREADS_SCHEDULE_JSON、THREADS_SCHEDULE_JSON_2、_3、_4へ対応する内容を設定します。未使用ファイルも空JSONに更新し、以前の予約を残さないでください。private配下の本文・CSV・画像・JSONを公開Gitへコミットしません。
10. 実公開・定期運用の有効化は別途承認と少数での動作確認後に行います。管理画面から公開・Actions実行・Secrets更新・自動公開のON操作はできません。

管理画面で競合登録した暗号化状態と情報源設定はローカルにあります。Actionsへ反映するには、state/private-state.encと確認済みconfig/trend_sources.jsonだけを差分確認してGitHubへコミットします。ほかの実行者との競合を避け、直前にGitHubの最新を取得してください。暗号化DBを古い版へ戻したり、同じIDを別の投稿経路から再送したりしないでください。下書きはprivateに留まり、承認・予約済みだけSecretsへ渡します。

## 時間・集計と制約

config/posting_schedule.jsonを新時刻へ変更し、通常文章でも1日3枠を使えるようにしました。各枠1件、1日最大3件、ツリーは管理上1本。空枠・未承認はスキップします。GitHub Actionsには遅延があり、正確な分単位の公開は保証しません。希望時刻は固定し、時間帯比較は改善仮説として扱い、無断で時刻変更しません。

日曜21時の週報は日曜22時投稿より前です。前週日曜21〜24時を今回へ持ち越し、今週の同時間帯は次週へ回すことで集計の欠落を避けます。carried_overを記録し、親・返信を分離、最新測定一件だけを使います。反応率は(いいね＋返信＋リポスト＋引用)/閲覧数。全値が既知で閲覧>0の場合のみ計算し、sharesを重複加算しません。

RSSの話題変化は観測した公式記事のキーワード件数の前週比較で、市場全体の流行ではありません。前週未観測は取得不可です。少数データ・因果関係未検証・測定時刻を明記します。定期処理は同じ週の生成済みレポートを再利用します。画面の更新ボタンは最新の保存済みデータで同じファイルを再作成し、重複ファイルは増やしません。CLIではcareer-report --refresh-report --private-stateで同じ操作ができます。

無料のローカルStreamlitと標準GitHub runnerを利用します。非公開リポジトリはActions分数・容量の超過で費用が発生し得るためSettingsで使用量・支出上限を確認します。15分ごとの予約確認は月約2,880回です。不要な定期処理を有効にせず、有料AI契約・有料分析サービス・有料クラウドは追加しません。公式配信と新しい本番投稿の実接続は未検証です。

投稿JSONの1件の構造例です（実際の取り込みは21件を用意します）。日時を省略すると開始日から3枠ずつ割り当てます。

```json
{"posts":[{"id":"week-2026-10-12-01","post_type":"text","category":"AI","theme":"AI活用","keywords":["AI活用"],"text":"人間が確認した投稿本文","approved":false,"approval_status":"pending_approval"}]}
```
