# 拡張システムの使い方

この拡張は既存の手動投稿と承認方式を維持します。今回の開発ではコードとローカルテストのみ実施しました。
実投稿、GitHub Actionsの実行、GitHubへのpush、定期公開の有効化、管理画面の外部公開は実施していません。

## 1. 何が追加されたか

| 役割 | モジュール | 責務 |
| --- | --- | --- |
| Director | director.py | テーマ・想定読者・口調・利用上限の検証 |
| Research | research.py | 人が用意した根拠・調査情報の整理。自動で真偽を保証しない |
| Writer | writer.py / ai.py | AIプロバイダー切替、フック・本文・締めの生成、利用枠予約 |
| Editor | editor.py | 構造・文字数・類似内容チェック。事実・自然さは人による承認が必要 |
| Scheduler | scheduler.py | 日本時間の予約、期限内の遅延追従、承認・履歴・件数制限 |
| Analyst | insights.py / analyst.py | 公式Insightsエンドポイントから数値取得、慎重な比較、改善候補 |

公開の責任をAIへ委譲しません。AI出力は常に `approved: false` の下書きです。
ジャンルは未指定なので `config/automation.json` の `ai.theme` と `ai.audience` は空です。
テーマ・読者が空のままAIを有効化すると、API呼び出し前に停止します。

## 2. GitHubに反映する手順（承認後）

この作業のローカルブランチは `codex/threads-automation-extension` です。既存mainの公開履歴から作成しました。
ローカルコミットを確認してから、次の操作でブランチだけを送信できます。

```bash
cd /workspace/Threads-
git log -1 --oneline
git diff origin/main...HEAD --stat
git push -u origin codex/threads-automation-extension
```

GitHubでブランチの **Compare & pull request** を開き、baseを `main` にしてPRを作成します。
コードとテストを確認してマージしてください。GitHub APIがこの環境から利用できない場合も、ブラウザーでPRを作れます。
PR作成後はSecretsを使わないテスト用Actionsが動く設計です。今回はこちらから実行していません。

マージ前後とも、新しいVariablesを `true` にしないでください。AIと自動公開の設定も初期状態では無効です。
後述の3条件が満たされるまで定期公開は実行されません。
GitHub Actionsが既存の履歴を更新している間にmainを上書きしないでください。force pushは禁止です。

## 3. 予約日時と承認

既存の投稿には日時を勝手に追加していません。`test-001` の本文・承認・履歴も保持しています。
`scheduled_at` がない投稿は手動専用で、定期公開の対象になりません。

新しい投稿を既存 `posts` 配列へ追加する例です。実際に予約する際は未来の日時に変更してください。

```json
{
  "id": "scheduled-example-001",
  "text": "ここに確認済みの投稿文を入れます。",
  "approved": false,
  "scheduled_at": "2026-10-09T08:00:00+09:00",
  "account": "default",
  "topic": "指定するジャンル",
  "variant": "baseline"
}
```

日時にはタイムゾーンが必須です。日本時間は `+09:00` です。
承認は実際の本文・根拠・表現を確認後に `true` に変更します。投稿IDは一意で固定します。
予定日時を過ぎた承認済み投稿だけが対象です。Actionsが遅れた場合は24時間以内の予定を拾い直します。
24時間を超えた予約は自動公開せず、運用担当者が日時と内容を見直してください。
この期限は `schedule.max_lateness_hours` で調整できます。許容範囲は最大168時間です。

1日・1回の上限は初期値3件です。失敗・不確定な予約もその日の利用件数に含めます。
遅延で複数の予定が重なると、同じ実行で連続公開される可能性があります。正確な時刻・間隔は保証しません。
GitHubのscheduleは15分ごとの確認であり、予約時刻ぴったりの実行を保証しません。

### 自動公開を将来有効にする際の条件

ユーザーの承認後に、次の3つをすべて有効にする必要があります。

1. `config/automation.json` の `auto_publish_enabled: true` をGitHubへ反映
2. Repository variable `THREADS_PUBLISH_ENABLED=true`
3. 新しいRepository variable `THREADS_AUTO_PUBLISH_ENABLED=true`

さらに投稿の `approved: true`、有効な日時、トークン、書き込み権限、未投稿の履歴が必要です。
既存の `THREADS_PUBLISH_ENABLED` だけがtrueでも自動公開は始まりません。
新規スイッチは未登録または `false` のままにしてください。自動停止には新規スイッチを `false` にするのが簡単です。
実行中の公開処理はスイッチを変更しても途中で撤回できる保証がありません。
既存の手動投稿は従来どおり、投稿IDと承認IDを指定して行えます。

## 4. AI下書き生成

標準ライブラリのみでOpenAIとGroqに対応しています。管理画面を使わなければ追加のPython依存はありません。
設定例は `config/automation.json` にあります。

- `ai.enabled`: 初期false。手動生成を行う場合も必要
- `ai.provider`: `openai` または `groq`
- `ai.model`: 選択したサービスに対応するモデル名
- `ai.api_key_env`: OpenAIなら `OPENAI_API_KEY`、Groqなら `GROQ_API_KEY`
- `ai.theme`, `ai.audience`, `ai.tone`: 投稿テーマ・想定読者・口調
- `ai.daily_drafts`: 毎日の最大生成件数。初期3件
- `daily_slots`: `08:00`, `19:00`, `22:00`（Asia/Tokyo）

生成後はその時点より未来の空いている投稿枠を割り当てます。今日の枠が埋まっている場合は翌日以降へ繰り越します。
枠の指定は下書きへの予約情報付与であり、承認にはなりません。
毎日3件は保証数ではなく上限です。API失敗、内容重複、利用上限では少なくなります。

`research/sources.json` の `sources` に調べた情報を入れられます。
各項目は `summary` が必須で、出典URL・確認日などを任意に追加できます。最大10件、summaryは各1,000文字までです。
個人情報・機密情報・APIキーを入れないでください。指定データはAIサービスへ送信されます。
自動Web検索や完全な事実検証は未実装です。モデルへの指示と形式検査だけで誤情報を防げるとは扱いません。

利用ログは `state/ai_usage.sqlite3` に保存します。日付・リクエストID・状態・生成投稿IDを記録し、キーは保存しません。
初期上限は日3リクエスト・月90リクエスト・出力900トークン/リクエストです。
失敗・不確定・途中停止でも確保した枠を消費します。API呼び出し前にGitへ利用枠を保存します。
モデルに渡す入力全体を20,000文字までに制限し、改善データは要約した提案だけを渡します。
これらは回数・入力・出力の上限で、円単位の厳密な請求上限ではありません。サービス側でも予算上限を設定してください。

承認後のGitHubでの生成には `THREADS_AI_GENERATION_ENABLED=true` も必要です。
手動操作は **Threads automation** → `operation=generate`、定期生成は同じスイッチで有効になります。
scheduleを15分ごとに確認しても、永続化された日次上限で何度も課金生成しない設計です。
AIのキーが未設定でも既存手動投稿・予約・分析機能は独立して動作します。

## 5. Insightsの取得と改善

対象は公式Threads APIの投稿別Insightsです。候補指標は `views`, `likes`, `replies`, `reposts`, `quotes` です。
`threads_basic` に加えて `threads_manage_insights` 権限の確認・追加認証が必要です。
APIの権限・アカウント・投稿種類・バージョンによって取得できる指標は変わります。

今回の開発ではMetaへの実接続は行っていません。公開済み投稿の読み取り確認で、指標が取得できるかを検証してください。
指標は個別に問い合わせ、非対応・権限不足のものを `unavailable` として保存します。欠測を0として扱いません。
対応する追加指標は公式資料で確認後、 `insights.metrics` に名前を追加できます（最大10指標）。
追加指標はJSONと管理画面の一覧で確認でき、反応率計算には基本の4反応指標だけを使います。
取得エラー、期限切れ、レート制限は自動投稿の再試行につなげません。

参照する公式資料：
- https://developers.facebook.com/docs/threads/insights/
- https://developers.facebook.com/docs/threads/get-started/long-lived-tokens/

`THREADS_INSIGHTS_ENABLED=true` を承認後に設定し、**Threads automation** の `operation=insights` で読み取りを検証できます。
新しく公開された投稿は24時間・72時間・7日後に取得します。
遅延時は実際の取得日時・公開後経過時間を記録します。過去の時点の数値を復元したとは扱いません。
結果は `analytics/insights.json` に、アカウント、投稿ID、公開ID、本文、公開日時、取得日時、指標を紐づけて保存します。
取得済みチェックポイントは重複保存しません。部分取得のスナップショットも保存後は自動再取得しません。

旧履歴の公開日時は推測しません。既存1件には公開日時・本文がないため通常取得ではスキップします。
読み取りを承認した後、安全にトークンを注入した環境で次のコマンドを使うと、公式メディア情報から旧履歴を補完できます。

```bash
python3 -m threads_publisher insights --backfill-metadata --persist-git
```

この操作は公開を行いませんが、APIへの読み取りと履歴のコミット・pushを行います。
実行時点ですでにチェックポイントを過ぎていれば、その時点の数値を遅延取得として記録します。

`improve` は数値の集計と助言を `experiments/improvement.json` に保存します。追加のAI課金はありません。
閲覧数に対する反応率は `(いいね + 返信 + リポスト + 引用) / 閲覧数` です。
閲覧数が0または欠測のときは比率を計算しません。反応指標が欠けた場合は完全な反応率を未知とします。
アカウント・取得時点・遅延条件を分け、文字数・フック形式・テーマ・日本時間帯・改善識別子を比較します。
同じ条件で5投稿未満では有効性を断定しません。5投稿以上でも観察上の関連で、因果関係の証明ではありません。
改善候補には安定した実験IDを付け、次のAI生成が候補と実験IDを引き継ぎます。
改善レポートを使った次の生成にはAI料金が発生します。提案を採用した投稿も必ず未承認です。

## 6. ローカル管理画面

```bash
python3 -m venv .venv-dashboard
.venv-dashboard/bin/python -m pip install -r requirements-dashboard.txt
.venv-dashboard/bin/streamlit run threads_publisher/dashboard.py \
  --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

予定一覧・日時変更・下書き承認/差し戻し・履歴・指標グラフ・件数・自動公開設定を操作できます。
変更はローカルファイルへ保存されます。GitHubにコミットして反映するまでActionsには適用されません。
画面から実投稿、Actions実行、Secrets操作はできません。認証済みのローカルマシン利用者を運用担当者とします。

この版はローカル専用です。明示的なloopback以外のバインドを拒否します。
ポート転送・トンネル・Streamlit Cloud等への外部公開は行わないでください。
外部公開する場合は別途、認証付きゲートウェイ、閲覧者と運用担当者の権限分離、CSRF対策、監査ログを設計・検証してから公開する必要があります。
認証なしの公開管理画面は提供しません。

## 7. 安全な運用、通知とトークン

公開前に `pending` をGitHubへ保存し、結果不明でも同じアカウントと投稿IDの再投稿を止めます。
失敗・不確定な公開は `error_kind` を記録し、pendingを残します。安全な自動再投稿はできません。
Threads上の投稿とコンテナ状態を確認してから運用担当者が処理してください。履歴を削除して再実行しないでください。

手動・定期ワークフローは共通の `threads-publication` concurrencyグループを利用します。
GitHubのconcurrencyは同時実行を防ぎますが、全pending実行のFIFOを保証しません。後続の定期確認で遅延分を拾います。
ローカルは別途ファイルロックで同時操作を止めます。複数端末での公開はこのロックの対象外なので、公開経路はActionsに統一します。
状態のpush競合・権限不足・ブランチ保護は失敗として停止し、force pushや自動履歴破棄はしません。

GitHubの読み取りチェック・PRテストは `contents: read`、履歴を保存するジョブだけ `contents: write` です。
Actionsは公式v6へ更新し、Node.js 24使用と固定コミットSHAを確認しました。
GitHub-hosted `ubuntu-latest` を対象とします。self-hostedを使う場合はrunner v2.327.1以上、コンテナ内Git認証を使う場合はv2.329.0以上が必要です。
今回GitHub上で新しいワークフローを動かしての検証はしていません。

エラーの生レスポンス・認証ヘッダー・トークンはログへ出しません。認証情報付きリダイレクトも拒否します。
公開APIの自動リトライはしません。読み取りのレート制限は次回以降の実行で回復を待ちます。
失敗時はActionsのジョブ概要に固定メッセージを出します。
GitHubの **Settings → Notifications → Actions** でワークフロー失敗メールを設定してください。
この版はSlackやメールサービスのAPIを呼ばず、GitHub標準の通知を利用します。

`accounts.default.token_expires_at` に、発行時に確認した期限をタイムゾーン付きISO形式で記録できます。
`python3 -m threads_publisher token-status` はAPIに接続せず残日数を確認します。未設定なら期限不明と表示します。
期限7日前以内では長期トークン更新を案内し、期限切れでは再認証を案内します。
この日時だけでトークン有効性は保証されないので、接続確認と組み合わせてください。
長期トークンへの交換・更新はMeta公式の現在の条件を確認し、安全な管理環境で行います。
新しいトークンをRepository Secretへ置き換えてから、まず接続確認してください。
アプリシークレットや新しいトークンをJSON・Git・Actionsログへ保存しないでください。
自動更新・GitHub Secret更新をする管理者権限はこの版には追加していません。

## 8. Secrets・Variables・費用

GitHubのリポジトリ **Settings → Secrets and variables → Actions** に設定します。
**Secrets**は秘密の値、**Variables**はON/OFFなどの公開可能な設定です。

| 種類 | 名前 | 用途と初期状態 |
| --- | --- | --- |
| Secret | THREADS_ACCESS_TOKEN | 既存のThreads認証。変更不要。Insights用権限は要確認 |
| Secret | OPENAI_API_KEY | OpenAI生成を使う場合のみ必要。今回は未登録でよい |
| Secret | GROQ_API_KEY | Groq生成を使う場合のみ必要。両サービスのキーは不要 |
| Variable | THREADS_PUBLISH_ENABLED | 既存の手動投稿用。既存設定を勝手に変更しない |
| Variable | THREADS_AUTO_PUBLISH_ENABLED | 新規。未登録またはfalse。承認まではtrueにしない |
| Variable | THREADS_AI_GENERATION_ENABLED | 新規。未登録またはfalse。有効化するとAI課金が発生し得る |
| Variable | THREADS_INSIGHTS_ENABLED | 新規。未登録またはfalse。読み取り・定期分析を許可 |

AIキーは選んだサービスの管理画面で発行し、GitHubのSecret入力欄に直接保存します。チャットに貼り付けないでください。
`GITHUB_TOKEN` はActionsが提供し、通常追加不要です。これはThreadsトークンとは別です。
Secretsの値はクラウド開発環境へ自動注入されません。ローカルでAPIを試すには別途安全な注入が必要です。

追加料金が発生し得る部分：
- AIモデルAPI：入力・出力・モデルに応じた従量料金。プロバイダー側の上限設定を併用します。
- GitHub Actions：リポジトリ種別や契約の無料枠を超える実行時間。15分ごとの有効化は実行回数を増やします。
- 管理画面のホスティング：外部公開する別構成を将来用意する場合の費用。今回はローカル起動のみです。

Threads APIの請求条件・利用制限はMetaの最新資料で確認してください。料金・レート上限の保証はしていません。

## 9. 複数アカウントへの拡張

`accounts` に別名と `token_env`、任意の `expected_user_id` を追加し、投稿に同じ `account` 別名を設定できます。
CLIは `--account secondary` で選択します。トークン変数名は `THREADS_ACCESS_TOKEN` で始めます。
履歴キーは実際のThreadsアカウントIDと投稿IDです。Insightsも接続したアカウントだけを取得します。
`expected_user_id` はトークンの取り違え防止に利用します。別名と実IDを混同しないでください。

この版のActionsとAI自動生成はdefaultアカウントのみを対象としています。
複数アカウントをActionsで回すには対応Secretの注入とmatrix/入力、各アカウントの上限・運用ルールの追加が必要です。
追加アカウントの認証や自動公開は行っていません。

## 10. ローカル検証

```bash
python3 -m unittest discover -s tests -v
python3 -m threads_publisher schedule --dry-run
python3 -m threads_publisher schedule
python3 -m threads_publisher generate
python3 -m threads_publisher token-status
```

初期設定のscheduleとgenerateは無効と表示し、トークンやキーを要求せず終了します。
`--dry-run` は予定一覧を確認するだけで、履歴を予約せずAPIを呼びません。
有効化後は同じコマンドが公開や有料AI生成を行い得ます。承認前にスイッチを変更しないでください。
テストは一時ファイルと模擬APIを使い、実アカウントへ投稿しません。
