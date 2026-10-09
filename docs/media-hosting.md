# 画像の準備と公開URL

このシステムは画像を自動アップロードしません。Threadsが取得できる公開HTTPS URLが必要です。ローカル検証はアップロードやThreads投稿を行いません。

## ローカルで確認する

```bash
python -m pip install -r requirements-media.txt
python -c "from threads_publisher.media import validate_image_file; print(validate_image_file('private/images/example.png'))"
```

未公開画像は、リポジトリに含めず、`private/images/`などGit管理対象外の場所で保管してください。公開前に著作権、利用許諾、個人情報を確認し、投稿の画像メタデータに `rights_confirmed: true` を記録します。ファイル名の変更ではプライバシーは保護できません。EXIF位置情報なども公開前に除去・確認してください。

`private/images.json` に `{"image_files":[{"url":"https://自分の配信元/画像.png","file":"private/images/画像.png"}]}` を作り、`python scripts/prepare_images.py --manifest private/images.json` でローカル検証できます。結果のSHA256を投稿の `image_sha256` 配列へURL順に入れます。画像の承認には権利確認とこのハッシュが必須です。公開前にURLから取得した画像が一致しなければ停止します。画像公開後に再圧縮するホストの場合は、確認した配信画像のハッシュを使い再承認してください。自動アップロードは行いません。

## 無料で始める選択肢

既存の静的Webサイト、GitHub Pages、無料枠のある静的ホスティングなどが利用できます。GitHub Pagesにはアカウントや公開・非公開リポジトリの条件があります。利用するサービスの現在の無料枠、転送量、画像利用規約を確認してください。必須の有料ストレージはありません。

画像URLを公開すると、誰でも画像を閲覧・保存できる可能性があります。ホスティングへの配置、Pagesの有効化、外部公開は利用者の承認後に行ってください。この開発作業では公開しません。認証が必要なページ、ブラウザ上の画像表示ページ、期限の短い署名URLではなく、画像ファイルを直接返す安定したHTTPS URLを使います。

設定の `media.allowed_hosts` に画像配信元のホスト名だけを登録します。例：`["your-name.github.io"]`。パスやワイルドカードを指定しません。自分が管理する配信元だけを許可してください。サービスのリダイレクトURLは使わず、最終URLを登録してください。

## ローカルの検証条件

`validate_image_file(path)` と `validate_image_url(url, allow_hosts)` は、MIME、バイト数、幅、高さ、SHA-256を返します。Pillowが未導入の場合、画像検証は明確なエラーになります。既存テキスト投稿にPillowは不要です。

この実装は安全側の独自事前条件としてJPEG/PNG、最大8 MiB、縦横それぞれ320～10000ピクセル、最大4000万画素、縦横比0.1～10、非アニメーション画像に制限します。拡張子やHTTP Content-Typeだけに頼らず、画像署名・構造・デコードを確認します。これらはMetaの受付保証や公式上限の断定ではありません。Threads公式ドキュメントとアプリ権限・現在のAPIバージョンによる制約を公開前に確認してください。

公式確認先：
- [Threads画像投稿](https://developers.facebook.com/docs/threads/posts/)
- [Threads API](https://developers.facebook.com/docs/threads/)

公式ドキュメントの取得にアクセス制限がある場合は、開発者アカウントで公式ページを確認してください。カルーセルの件数や仕様は投稿側で検証する必要があります。

公開URLの確認はGETを行いますが、Threadsへの投稿は行いません。HTTPSのみ、設定されたホストのみ許可し、DNSが返すすべてのアドレスがpublicであることを確認します。接続先IPを固定してDNS再解決によるprivateアドレスへの変更を防ぎ、証明書は元のホスト名で検証します。リダイレクト、認証情報入りURL、独自ポートを拒否します。APIトークンやAuthorizationヘッダーを画像配信元へ送りません。レスポンスは最大8 MiB+1バイトだけ読み取り、エラーにURLや秘密情報を含めません。
