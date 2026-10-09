# 就活・転職を含む週21本の運用

この拡張は既存の手動テキスト投稿と公開履歴を保持します。実APIへの接続、投稿、画像公開、定期ジョブの有効化は今回のローカル検証に含みません。有料AI APIの生成は無効で、APIキーは不要です。

## 開発環境と最初の確認

Python 3.12以上で次を実行します。

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-private.txt -r requirements-media.txt
.venv/bin/python -m unittest discover -s tests -v
. .venv/bin/activate
```

接続確認はREADMEの既存手動Actionsの `operation=check` を使用します。新しい投稿形式の本番検証は、承認後に少数の投稿で行います。ローカルテストは模擬APIと一時ファイルを使い、投稿・実データ取得をしません。

## 投稿の作成、取り込み、承認、予約

`prompts/weekly-content-generation.md` をChatGPT Plusに貼り、最新の週報と翌週月曜日の日付を指定します。通常文章7件、画像またはカルーセル7件、ツリー7件を作ります。就活・転職とAI・仕事術・お金のテーマを組み合わせ、出典・確認日・権利・誇張を人間が確認します。

本文と画像は公開リポジトリへ置かず、Git管理対象外の `private/week.json` と `private/images/` に保存します。画像はChatGPTで作成し、承認後に自分の無料ホスティングへ配置します。このコードは自動アップロードやPages有効化を行いません。[画像準備ガイド](media-hosting.md) を参照してください。

最低限のテキスト投稿の入力例（実際の週次入力は21件必要です）：

```json
{"posts":[{"post_id":"2026-10-12-text","post_type":"text","category":"就活","theme":"企業研究","keywords":["企業研究"],"publish_datetime":"2026-10-12T08:00:00+09:00","text":"人間が確認した投稿本文","approval_status":"pending_approval","approved":false}]}
```

画像は `image_urls`、`alt_text`、人間が確認する `rights_confirmed` と確認済み画像の `image_sha256` 配列を追加します。ハッシュ未設定の画像は承認できません。カルーセルは2〜20枚、画像投稿は1枚です。ツリーは `thread_items` に3〜7件の `{"text":"..."}` を指定し、`text` は先頭と同一にします。各本文は500文字以下。`sources` に出典URLと確認日を記録できます。APIの現行上限を満たすことは公開前に別途確認してください。

```bash
python -m threads_publisher import-batch --input private/week.json --start-date 2026-10-12
python -m threads_publisher approve-draft --post-id 2026-10-12-text
python -m threads_publisher schedule-draft --post-id 2026-10-12-text
python -m threads_publisher export-approved
```

取り込み時に外部入力の承認は無効化されます。21件・7日連続・各日3形式・設定時刻、ID、同じ日時、同一内容を検証し、一つでも不正ならバッチを追加しません。CSVも対応し、配列の列はJSON文字列を入れます。承認と予約は投稿ごとに別操作です。承認後の編集は承認と予約を解除し、承認ハッシュにより直接改変も拒否します。

編集は `private/changes.json` に変更項目を書き、`edit-record --post-id ID --changes-file private/changes.json`。差し戻しは `reject-draft --post-id ID`。画像の権利を確認した後は編集で `rights_confirmed: true` を設定し、再承認します。

書き出しは `private/approved-posts.json` と `-2.json`、`-3.json`、`-4.json` に最大45,000バイトずつ分割します。対応するSecrets `THREADS_SCHEDULE_JSON`、`THREADS_SCHEDULE_JSON_2`、`_3`、`_4` を更新します。不要な分割にも空の `{"posts":[]}` を設定し、以前の予約を残さないでください。Secretの値はチャット、ログ、Gitへ貼らないでください。予定がない枠はスキップします。

予定時刻は `config/posting_schedule.json` の08:00テキスト、12:00画像、20:00ツリー（Asia/Tokyo）です。旧 `import-week` は従来のテキスト専用08:00・19:00・22:00として保持しており、新しい混合形式には `import-batch` を使います。

## 永続化と失敗復旧

新しい形式の投稿は暗号化状態を必須にし、本文・予約・実績は `state/private-state.enc` に保存します。`THREADS_STATE_KEY` は既存キーを再利用し、変更・紛失・DB巻き戻しを避けます。新規キーの作成は `cryptography.fernet.Fernet.generate_key()` をローカルで使用し、安全なSecrets設定画面へ保存してください。

作成前、公開前、公開ID確定後に状態を永続化します。Gitへのpushが失敗したら次のAPI処理を止めます。ツリーは確定した投稿IDへの返信を順番に作り、管理上1本です。公開前と分かっている既存コンテナは、承認後の手動 `publish --resume --private-state --from-secret --post-id ID --approved-post-id ID --persist-git` で再開できます。公開結果不明の `publishing` は再開拒否し、Threads上の実投稿と照合して専門的な手動復旧が必要です。履歴削除や新IDでの再送は行わないでください。

予約時刻の遅れは初期24時間以内に追随します。型付き投稿は予定日の各形式1件、実行日最大3件に制限します。失敗・不明な処理も枠を消費します。GitHub Actionsは正確な分単位の実行や待機ジョブの全件実行を保証しません。

## 分析と週報

[キーワード調査](keyword-research.md) は既存語に就活・転職43語を追加し、正規化して重複整理します。初期は無効です。公開検索は少量巡回し、日3回・週20回までの独自制限を永続化します。公式検索のアクセス範囲・権限・回数上限はアプリ側で確認が必要で、接続済みトークンが検索権限を持つ保証はありません。

自分のInsightsは閲覧、いいね、返信、リポスト、引用を取得し、対応時のみsharesなどを保存します。旧24時間・72時間・7日チェックポイントも維持します。日次・週次の最新値は過去8日以内の投稿要素を新しい順に最大100件取得します。上限で未収集の指標は推測せず、API認証・レート制限エラー時はその実行の追加取得を止めます。親とツリー返信は分離し、閲覧数を単純合算しません。反応率は `(likes + replies + reposts + quotes) / views`。全指標が既知でviews>0の場合のみ計算し、sharesは重複の可能性から分子に含めません。競合の閲覧数や非公開指標は推測しません。

週報は月曜日00:00〜日曜日20:59台（終了21:00を含まない）を対象に、実行時の最新観測一件を使用します。締切後の観測、未測定、未集計、少数データを明記します。日曜21〜24時に遅延して公開された投稿は指定の週次期間外です。確認済みの対象外本数とこの集計の空白を明示し、翌週に自動加算はしません。同じ週の再実行は既存レポートを再利用します。実績取得に失敗した場合も保存済みデータで週報を作り、測定時刻・取得不可を明示します。遅延して翌日に実行されても前の日曜締めを選択します。

`reports/weekly/YYYY-MM-DD.md` と `.json`、`reports/latest.md` と `.json` は公開可能な集計に絞った平文です。未公開本文、内部ID、アカウントIDは出しません。分類・キーワードも設定済みの語だけに制限します。競合URLは公開投稿の根拠であり、その公開プロフィール名を含むことがあります。詳細実績・観測履歴は暗号化します。

APIを呼ばずローカルの保存済み実績から作る場合：

```bash
python -m threads_publisher career-report --private-state
```

翌週は `reports/latest.md` をChatGPTへ渡し、仮説・根拠URL・形式別成果を参考に再び21本を作成します。現在データが少ない場合、改善案は検証する仮説であり成果保証ではありません。

## GitHub反映と手動設定

コードの差分とテストを確認後、開発ブランチをpushしmain向けPRを作成、レビューしてマージします。今回コード変更だけでVariablesを有効化しません。混合形式の本番確認は承認後、既存の手動Actionsで `operation=publish-private` と同じ `post_id` / `approved_post_id` を指定します。定期スイッチを有効にする必要はありません。既知の未公開コンテナの再開は `resume-private` で、結果不明の公開は拒否します。既存 `THREADS_ACCESS_TOKEN`、暗号化の `THREADS_STATE_KEY`、承認済み予約Secretsを用意します。画像配信ホストを `config/automation.json` の `media.allowed_hosts` に登録します。[ワークフロー設定](operation-workflows.md) に専用Variablesを記載しています。

定期公開・分析・日曜週報はいずれも初期無効です。動作確認と承認後に必要なものだけ有効化します。Actionsはcontents:writeを状態保存ジョブに限り使用し、全ワークフロー共通の排他グループで直列化します。mainのブランチ保護がbotのpushを禁止する場合は保存できず停止します。無断で保護を解除せず、別の永続ストレージなどを再検討してください。

失敗通知はGitHub Actionsの失敗表示、実行概要とGitHubの通知設定を使用します。トークン・HTTP本文を表示しません。外部メールやSlackは送信しません。トークン更新は手動で行い、`token-status` は設定した期限の確認のみです。期限不明時は推測せず再認証の手順を案内します。

## 費用と対応限界

ChatGPT Plus以外のAI API契約は不要です。無料ライブラリと標準ubuntu runnerを使い、有料SNS分析・有料クラウドは導入しません。GitHubの公開リポジトリの標準runnerは一般に無料、非公開リポジトリはプランの分数・ストレージ枠があり、超過時は料金が発生し得ます。15分ごとの有効化は月約2,880回なので、不要な運用を有効化しないでください。Actionsの使用量・課金上限をSettingsで確認します。

画像ホストの無料枠・帯域には制限があり、無料で永久に無制限の保存を保証しません。GitHub Pagesを候補にする場合、公式の公開サイト容量1GB、推奨リポジトリ1GB、月帯域100GBのソフト上限など現行条件を確認します。公開権利・非公開原稿漏洩に注意し、自動配置はしません。

公式ドキュメントはこの環境で直接取得が403となったため、Meta公式サンプルで画像・カルーセル・reply_to_id・状態確認の実装形式を確認しています。権限レビュー、現行の画像条件、実際の各エンドポイントの受付は未検証です。キーワード検索 `threads_keyword_search`、公開プロフィール探索 `threads_profile_discovery`、自身の実績 `threads_manage_insights`、投稿 `threads_content_publish` と `threads_basic` を公式アプリ画面で確認してください。競合プロフィールで頻度・図解・ツリー関係が取得できない場合は取得不可とし、公開情報の手動入力で代替します。

今回の開発ブランチは `codex/career-multiformat-system` です。GitHubへpush済みの場合、次の比較画面でmain向けPRを作成できます。

https://github.com/lv0832tktu/Threads-/compare/main...codex/career-multiformat-system?expand=1

画面で差分とテスト結果を確認し、Create pull requestを選びます。マージはレビュー後に行い、マージ前後とも新しい定期Variablesはfalseのまま維持してください。
