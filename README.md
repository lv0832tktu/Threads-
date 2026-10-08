# Threads運用

Threadsの運用方針、投稿作成、実績分析、改善を管理します。

## フォルダ
- strategy/：目的・読者・発信テーマ
- posts/：投稿案・承認済み投稿
- calendar/：投稿予定
- analytics/：実績記録
- experiments/：改善仮説と検証結果
- assets/：画像素材

## 運用手順
1. 運用方針を決める
2. 投稿案を作り、事実・表現・権利を確認する
3. 承認した投稿を手動で投稿する
4. 実績を記録し、週ごとに改善する

API接続確認と承認付きの手動投稿は、下記のシステムで実行できます。

## Threads API自動投稿システム

Python 3.12以上と標準ライブラリだけを使用します。依存パッケージのインストールは不要です。
既存の運用資料は引き続き利用できます。

### 最初の接続確認（投稿はしません）

1. この変更をGitHubの `main` に反映します。
2. Settings → Secrets and variables → Actions に既存の `THREADS_ACCESS_TOKEN` があることを確認します。値をコードやチャットへ貼り付けないでください。
3. Actions → **Threads manual operation** → **Run workflow** を開きます。
4. `operation=check` のまま実行します。投稿ID欄は空欄で構いません。
5. `Connection check passed` を確認します。失敗した場合は権限・認証・期限を確認します。

接続確認は `GET /v1.0/me?fields=id,username` のみを呼びます。
Repository Secretsはこのクラウド作業環境には自動注入されないため、実アカウントとの接続確認はGitHub Actionsで行ってください。

### 承認後の手動投稿

初期状態では投稿は無効です。スケジュール実行もありません。実際の投稿を承認した場合に限り、次の条件をすべて満たしてください。

- `posts/posts.json` の対象投稿の内容を確認し、`approved` を `true` にする。
- Repository Variablesに `THREADS_PUBLISH_ENABLED=true` を設定する。
- 手動実行で `operation=publish` を選び、`post_id` と `approved_post_id` に同じ対象IDを入力する。

IDは投稿ごとに一意で固定してください。投稿後のID変更・履歴削除は二重投稿につながります。
テキストは1〜500文字です。トークンには接続用の `threads_basic` と投稿用の `threads_content_publish` 権限が必要です。
APIはテキストコンテナ作成後、コンテナIDを指定して公開します。

### 永続履歴とエラー対応

`state/history.sqlite3` にアカウントID・投稿ID・状態・公開IDを保存し、Actionsが `main` へコミットします。
同じワークフローは並列投稿せず、API作成前に `pending` をコミット・pushします。
履歴のpushに失敗すると投稿を止めます。Actionsの書き込み権限とブランチ保護が許可する必要があります。
ブランチ保護を無断で緩めず、必要なら履歴専用ストレージを別途設計してください。

公開後に通信が切れた場合や履歴保存に失敗した場合も、保存済みの `pending` により自動再投稿を止めます。
`pending` は公開済みか未公開かを保証しません。Threads上の投稿とAPIの状態を照合してから、運用担当者が履歴を解決してください。
履歴のバックアップ・保持が必要です。Git履歴から除去したり、DBを古い状態へ戻したりしないでください。
別ワークフロー・ローカル環境から同時投稿するとGitHubの排他制御が届かないため、投稿経路はこのActionsに統一してください。

期限切れ・無効なトークンは安全なエラーとして表示します。再認証で取得した値をSecretに更新し、まず接続確認を再実行します。
生のAPIレスポンスやトークン、HTTPヘッダーはログへ出しません。公開処理の自動リトライは行いません。

### 開発と検証

```bash
cd /workspace/Threads-
python3 -m unittest discover -s tests -v
python3 -m threads_publisher check
```

最後のコマンドには安全に注入された `THREADS_ACCESS_TOKEN` が必要です。コマンド行に値を直接書かないでください。
テストは模擬APIを使い、Threadsへ実際の投稿を行いません。
`core.py` はAPIクライアント・JSON読込・履歴・承認制御を分離しています。
将来のAI生成はJSON生成層、予約投稿は実行層、分析は読取クライアントとして追加できます。
履歴キーにはアカウントIDを含みます。複数アカウント運用にはトークンの選択・安全な個別注入と実行制御の追加が必要です。
