[English](https://github.com/ez2app/ez2-aib/blob/main/CONTRIBUTING.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CONTRIBUTING.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CONTRIBUTING.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CONTRIBUTING.ko.md)

# コントリビューション

aib はこのリポジトリで開発されています（0.1.7 から）。プルリクエストはここでレビューされてマージされ、
すべてのリリースは `main` から作られます。

- **Issue を歓迎します** — バグ、質問、アイデアなど。実行した `aib` コマンドとその出力を添えてください。
- **プルリクエストを歓迎します。** 最新の `main` からブランチを作ってください。メンテナーがマージする前に、
  6 つの必須チェック（Linux と macOS、Python 3.9・3.12・3.14）がすべて通る必要があります。テスト付きの小さく
  焦点を絞った変更が最もレビューしやすくなります。PR を開いている間に `main` が進んだら、`main` を
  あなたのブランチにマージしてチェックを再実行してください。
- **リリース**はメンテナーが行います。`pyproject.toml` と `aib/VERSION` のバージョンと各 `CHANGELOG` の先頭の節を
  更新し、リリースワークフローを実行します。ワークフローはパッケージを検査してから PyPI へアップロードします。
- コード内のコメントは歴史的な経緯から主に繁体字中国語で書かれています。Issue や PR は英語でも問題ありません。
