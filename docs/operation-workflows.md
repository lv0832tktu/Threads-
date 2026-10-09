# 分離した運用ワークフロー

新しい定期処理は初期無効です。今回Actionsは実行していません。
既存の手動接続確認・手動公開は保持し、旧 `automation.yml` の定期起動だけを外しました。
同じ処理を旧・新ワークフローが同時に開始することを防ぎます。

| ファイル | 日本時間 | 必要なRepository variable |
| --- | --- | --- |
| scheduled-posts.yml | 15分ごとに期限内の予約を確認 | THREADS_SCHEDULED_POSTS_ENABLED=true |
| daily-analytics.yml | 毎日06:30 | THREADS_ANALYTICS_ENABLED=true |
| weekly-report.yml | 日曜21:00 | THREADS_WEEKLY_REPORT_ENABLED=true |

公開にはさらに `THREADS_PUBLISH_ENABLED=true`、`THREADS_AUTO_PUBLISH_ENABLED=true`、設定の
`config/automation.json` と `config/posting_schedule.json` 両方の
`auto_publish_enabled: true`、投稿の承認と本文一致の承認ハッシュが必要です。
新しいVariablesは未設定またはfalseを維持してください。
予定枠は `config/posting_schedule.json` の08:00、19:00、22:00（形式自由、初期は通常文章21本）です。
画像とカルーセルは同じ1枠として扱い、型付き予約は `publish_status: scheduled` と設定枠の日時一致が必要で、
予定した日本日付ごとに各枠1件を上限とします。
pendingや失敗した予約も枠を使用するため、自動で繰り返し公開しません。
旧形式の型なし投稿は互換性を維持し、既存の1日3件制限に従います。

Actionsの予定時刻は厳密保証ではありません。予約は従来の遅延許容期間内で確認します。
全部の公開・私的履歴保存処理は `threads-publication` concurrencyグループで直列化し、
実行中のジョブを新しいジョブでキャンセルしません。待機ジョブの順序や全件実行は保証されません。

## 必要なSecrets

- THREADS_ACCESS_TOKEN：既存認証。Insightsと公開検索に必要な現行権限を確認します。
- THREADS_STATE_KEY：私的履歴の暗号化キー。既存キーを破棄・変更しないでください。
- THREADS_SCHEDULE_JSON：承認済み予約を含む `{"posts": [...]}`。
- THREADS_SCHEDULE_JSON_2、_3、_4：予約量がSecretのサイズを超える場合の任意の分割。形式は同じです。

予約JSONは本文やメディアURLを含みます。公開リポジトリやActionsログへ直接載せないでください。
予約の分割は同じ投稿IDを重複させません。画像URLはMetaがアクセスできる公開HTTPSが必要です。

## 分析とレポート

毎日処理は最新の自分のInsightsを読み取り、週次処理は週次観測として最新値を取得します。対象は過去8日以内・最大100投稿要素です。週次の取得失敗は実行概要に記録し、週報には保存済み観測の測定時刻と未取得指標を残します。
週次の公開キーワード調査は追加の `THREADS_KEYWORD_RESEARCH_ENABLED=true` と
`config/keywords.json` の `enabled: true` が両方必要です。
権限がない場合はスクレイピングや非公式サービスへ切り替えません。
APIが公開検索の反応指標を返さなければ不明と表示します。
週次レポートは公開可能な集計だけを `reports/` に平文で保存します。詳細実績・観測履歴は暗号化状態に保存します。
有料AI APIの呼び出しはありません。ChatGPT Plusへ貼り付ける生成依頼を作ります。

コードをGitHubへ反映するだけで新しい定期公開は有効になりません。
有効化・実公開・新しいワークフローの実行は、ユーザーの承認を受けてから行います。
