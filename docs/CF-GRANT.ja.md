[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 一時キー(`aib cf grant`)

サーバーに長期・広権限の Cloudflare キーを置く代わりに、`aib cf grant` は毎回 Touch ID を求め、
**1 つのゾーンだけ**、**指定した権限だけ**、**対象サーバーの IP からだけ**使え、**1〜60 分で失効する**(既定 10 分)トークンを作ります。
トークンは ssh の標準入力でサーバーに直接書き込まれ(権限 600)、値は表示・ログ・引数に一切出ません。

```
aib secret ask CF_TOKEN_MINTER                      # once: store the minter key (Touch ID)
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

- **発行用キー**:Cloudflare 管理画面で **API Tokens Write** だけのトークンを作り(発行対象のゾーンには **Zone Read** も追加)、
  `aib secret ask CF_TOKEN_MINTER` で保存します。この Mac の暗号化保管庫にだけ置き、サーバーには不要です。
- **権限**(許可リスト):`dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `routes-write` `workers-write`。
- **IP 制限**:ssh の接続先 + サーバー自身の Cloudflare 向け送信元 IPv4/IPv6 + `--ip CIDR`。見つからなければ作りません。
- `grant` と `revoke` は必ず Touch ID。省略するフラグや環境変数はありません。Mac 以外では作りません。
- サーバーに届けられなければ、作ったトークンをすぐ削除します。
- 承認(成功・拒否とも)は `~/.ez2-aib/audit/cf-grant.jsonl` に追記されます(値は含みません)。
- 指紋を待てない自動処理(証明書更新、キャッシュ削除)には、ゾーン限定・IP 制限の小さな常設トークンを別に用意してください。
