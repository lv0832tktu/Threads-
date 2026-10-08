# ChatGPT Plusだけを使う週次運用

ChatGPT Plusの画面で文章を作り、Codexで予約登録します。Plus契約はOpenAI API料金やAPIキーを含みません。
この構成ではChatGPTへの自動アクセス・スクレイピングをせず、OpenAI/Groq APIにもリクエストしません。
`generate` コマンドとWriterのAPI生成は無効化されています。AI用キーは不要です。

## 毎週の流れ

1. 前週のレポートをChatGPTへ貼り付け、ジャンル・読者・口調を指定して21本の投稿文を作ります。
2. Codexに内容の整理・予約登録を依頼するか、JSONを `private/week.json` に保存します。
3. 一括登録して本文と日時を確認・編集します。初期承認は全件falseです。
4. 個別に承認し、承認済みだけを書き出します。
5. GitHub Secretへ承認済みJSONを保存します。Gitには投稿案を追加しません。
6. ユーザーによる動作確認・公開許可の後だけ、予約公開の3つのスイッチを有効にします。

この変更では実投稿、Actionsの手動実行、定期公開有効化、有料API利用を行っていません。

## 週21本の入力形式

`private/` はGit管理対象外です。入力ファイルや個人情報を公開リポジトリへ置かないでください。
ファイルは {"posts":[...]} 形式で、配列内に21件の `{ "id":"一意のID", "text":"投稿文" }` を入れます。
文字列21件の配列も受け付けます。その場合は開始日・アカウント・順番から固定IDを作ります。
21件を7日間の08:00・19:00・22:00（Asia/Tokyo）へ順番に割り当てます。

```bash
cd /workspace/Threads-
mkdir -p private
# ChatGPTで作ったJSONをprivate/week.jsonへ保存した後に実行
python3 -m threads_publisher import-week --start-date 2026-10-12
```

開始日は実際に使う週の年月日に変更してください。保存先は `private/posts.json` です。
同じID・同じ内容の再取り込みは追加せず、確認済みの承認状態も保持します。
ID衝突、同じ文章、既存枠との衝突、旧投稿との重複、21件以外、500文字超は拒否し、既存データを上書きしません。
類似表現まで完全に同一内容と判定できる保証はないため、人による内容確認も必要です。

本文を `private/edited-text.txt` に保存してから編集します。編集すると承認はfalseに戻ります。

```bash
python3 -m threads_publisher edit-draft --post-id weekly-default-2026-10-12-01
python3 -m threads_publisher approve-draft --post-id weekly-default-2026-10-12-01
python3 -m threads_publisher export-approved
```

入力でIDを指定した場合は、そのIDを使います。全件一括自動承認はしません。
編集・承認・差し戻し・日時変更には既存のローカル管理画面も利用できます。
画面はprivate/posts.jsonがあればそちらを表示し、外部バインドは拒否します。

## 必要なSecrets（有料APIキーは不要）

GitHub → Settings → Secrets and variables → Actions → Secretsで保存します。

| Secret | 用途 |
|---|---|
| THREADS_ACCESS_TOKEN | 既存のThreads認証。Insightsにはthreads_manage_insights権限を要確認 |
| THREADS_SCHEDULE_JSON | private/approved-posts.jsonの内容。承認済み予約だけを保存 |
| THREADS_STATE_KEY | 履歴・分析・レポートを認証付き暗号化する鍵 |

Secretの値はチャット、コード、ログへ貼り付けないでください。
承認済みJSONはローカルでファイルを開いてGitHub Secret入力欄へ保存します。
GitHub CLIの認証・APIアクセスが利用できる管理端末では、次のstdin入力も使えます。

```bash
gh secret set THREADS_SCHEDULE_JSON --repo lv0832tktu/Threads- < private/approved-posts.json
```

GitHub Secretは48KB制限です。書き出し機能は45KBを上限にしています。
公開後も同じIDは履歴によりスキップするため、毎週Secretの内容を入れ替えられます。
公開済み投稿を別IDで再登録しないでください。

無料ライブラリ `cryptography` をインストールし、管理端末で鍵をファイルへ生成します。
鍵を画面へ表示するコマンドにはしていません。

```bash
python3 -m venv .venv-private
.venv-private/bin/python -m pip install -r requirements-private.txt
.venv-private/bin/python -c 'from cryptography.fernet import Fernet; from pathlib import Path; Path("private/state-key.txt").write_bytes(Fernet.generate_key())'
# GitHub CLIが使える端末の場合のみ
gh secret set THREADS_STATE_KEY --repo lv0832tktu/Threads- < private/state-key.txt
```

鍵は安全な保管先へバックアップし、ローカル鍵ファイルのアクセスも制限してください。
履歴が作られた後で鍵を作り直すと復元できません。鍵の更新には別途移行作業が必要です。
鍵がない・暗号データが破損・復元できない場合、公開処理は停止します。

## 予約公開と遅延対応

以下をすべて有効にするまで定期公開は始まりません。

- config/automation.jsonのauto_publish_enabled=true
- Repository variable THREADS_PUBLISH_ENABLED=true
- Repository variable THREADS_AUTO_PUBLISH_ENABLED=true

今回は設定ファイルはfalse、新規Variableの登録・有効化も行いません。
予定時刻を過ぎた承認済み投稿だけを対象とし、日時のない旧投稿は自動公開しません。
Actionsは標準 `ubuntu-latest` ランナーで15分ごとに確認します。予定ぴったりの実行は保証されません。
遅延は初期24時間以内で追従し、それ以上は公開せず確認待ちです。1日・1回の上限は3件です。
手動・定期処理は同じconcurrencyグループで競合を防ぎます。
公開前に予約履歴を永続化し、公開結果が不明でも二重投稿を止めます。pending履歴を削除して再投稿しないでください。

新しい予約公開は `--private-state --from-secret --persist-git` を使います。
SecretのJSONはprivate内だけに展開し、Gitへ保存するのは `state/private-state.enc` の暗号文だけです。
投稿本文・分析・未完了履歴・レポートはこの暗号化状態へ保存します。鍵は含めません。
初回は旧state/history.sqlite3をコピーして引き継ぎます。旧ファイルは変更しません。
Gitへのpushが失敗すると公開前に停止します。ブランチ保護とActionsの書き込み権限が必要です。
暗号化ファイルや鍵を削除・初期化すると二重投稿防止が失われます。元に戻してから運用を再開してください。
既存の手動投稿ワークフローと旧履歴は維持します。未公開の新規案は旧posts/posts.jsonへ追加しないでください。
既存Git履歴にすでに記録されたデータを非公開化する機能ではありません。

## 無料のThreads分析と週次レポート

OpenAI/Groqのキーは不要です。Threads公式Insights APIの読み取りは既存トークンを使います。
閲覧数・いいね・返信・リポスト・引用を候補として個別に取得し、対応しない指標は未知として記録します。
公開24時間・72時間・7日後を基本に、遅延時は実際の取得日時を記録します。
欠測を0とみなさず、Pythonで反応率・取得時点・遅延・文字数・時間帯を比較します。

分析を動作確認後に許可する場合だけ、Variable THREADS_INSIGHTS_ENABLED=trueを設定します。
このスイッチは予約公開のスイッチとは独立です。分析だけを有効にしても公開はしません。
Insights権限・トークンの有効性は実読み取りで検証する必要があり、この作業では実APIを呼んでいません。

同じ定期ワークフローが週次レポートを日本時間の週ごとに1回生成します。
手動のoperation=reportでは再生成できます。分析未取得ならデータ不足と表示します。
レポートは本文・アカウントIDを除いた集計と、ChatGPTへ相談する文章です。
少数データでは有効性を断定しません。ChatGPTへ送る前に運用担当者が内容を確認してください。
レポートも暗号化状態内に保存し、公開ログ・公開Artifactsへ出力しません。

ダウンロードしたmainの暗号化状態と安全に注入したTHREADS_STATE_KEYがあるローカル環境で復元できます。

```bash
.venv-private/bin/python -m threads_publisher report --private-state
```

このコマンドはAPIを呼ばず、private/weekly-report.mdへレポートを作ります。`--persist-git`を付けなければpushしません。
生成ファイルをローカルで開き、ChatGPTへ貼り付けて次週21本を作ります。

## 追加料金を避けるための注意

OpenAI/Groqへの自動リクエスト・キー注入・定期生成ジョブを削除しました。
有料APIや外部ホスティング契約は不要です。暗号化ライブラリも無料です。
ChatGPT Plus自体の既存契約は必要です。PlusをAPIの利用権としては扱いません。
GitHub標準ランナーでも契約・リポジトリ種別によって無料枠があります。
有効化前にBillingのActions利用枠・支出上限を確認し、追加請求を許可しない設定にしてください。
無料枠を超える場合はジョブ頻度を下げるか停止します。「絶対に無料」とは保証できません。
新たな有料サービスを導入する場合は事前に相談する必要があり、今回は導入していません。

## GitHubへの反映と検証

ブランチはcodex/threads-automation-extensionです。今回変更をpushし、可能ならmain向けPRを作ります。
レビュー後のマージと定期公開の有効化は別です。新規公開スイッチはfalseのままにしてください。
テストは標準ライブラリと無料の暗号化依存を使い、模擬APIだけで実行します。

```bash
.venv-private/bin/python -m unittest discover -s tests -v
```

PR作成ができない場合は次のページでbase=main、compare=codex/threads-automation-extensionを確認します。
https://github.com/lv0832tktu/Threads-/compare/main...codex/threads-automation-extension?expand=1

private/はgitignore対象です。`git add -f private`、GitHubコメント・Issueへの本文貼り付け、公開Artifactsへのアップロードは禁止です。
