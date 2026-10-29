[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 一時キー(`aib cf grant`)

サーバーに長期・高権限の Cloudflare キーを置きません。`aib cf grant` は毎回 Touch ID を求めてから、
**1 つのゾーンだけ**・**指定した権限だけ**・**対象サーバーの IP からだけ使え**・**1〜10 分で失効する**(既定 10 分)トークンをその場で発行します。
トークンはメモリ上だけにあり、ssh の stdin でサーバーへ直接書き込みます(権限 600)。この Mac のディスクには残らず、表示・ログ・引数にも出ません。

```
aib secret ask CF_TOKEN_MINTER                      # once: seal the minter key into this Mac's Secure Enclave
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

## 防御線は何か

- **マスターキーはこの Mac の Secure Enclave にだけ封入します。** Cloudflare で **API Tokens Write** だけ(発行するゾーンには **Zone Read** も)の
  トークンを作り、本人が `aib secret ask CF_TOKEN_MINTER` を実行します。値は Secure Enclave の鍵(CryptoKit `SecureEnclave.P256`、
  アクセス制御 `biometryCurrentSet`)で暗号化され `~/.ez2-aib/secrets/se/CF_TOKEN_MINTER.sealed` に保存されます。
- **マスターキーを使うたびに、システム自身が Touch ID を求めます。** 開封には Secure Enclave 内での鍵合意が必要で、その鍵は毎回の生体認証を要求します。
  これは macOS / Secure Enclave が強制するもので、aib 独自のチェックではありません。封入ファイルを持ち出しても、この Mac の Secure Enclave と本人の指紋がなければ開けません。
  指紋の登録を変えると無効になるので、もう一度 `aib secret ask CF_TOKEN_MINTER` を実行してください。
- **ダイアログの文面は aib が組み立てます**:ゾーン・権限・TTL・対象ホスト。よく読んでから触れてください。1 回の承認で 1 本だけ発行します。
- **マスターキーをファイルに置くことは許しません。** キー金庫(`cloudflare.env`、LINE 補填を含む)は `CF_TOKEN_MINTER` を拒否します。
  すでにその行があるファイルがあれば `grant` / `list` / `revoke` は拒否します(`minter_in_file`)。上の方法で封入し、その行を削除してください。
- **監査ログは記録であって防御線ではありません。** 開封の段階まで進んだ試行(承認・拒否とも)は `~/.ez2-aib/audit/cf-grant.jsonl` に 1 行追記されます(値は含みません)。

## その他のルール

- **権限**(許可リスト):`dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `zone-settings-read` `zone-settings-write` `routes-write` `workers-write` `redirect-read` `redirect-write` `bulk-redirect-read` `rule-lists-read` `analytics-read`。
- **IP ロック**:ssh の接続先アドレス + サーバー自身の Cloudflare への出口 IPv4/IPv6 + 追加の `--ip CIDR`。1 つも見つからなければ発行しません。
- Touch ID を飛ばすフラグや環境変数はありません。Mac でなければ発行しません。`AIB_TOUCHID=0` はオフにするだけです。
- `--dry-run` は引数と IP ロックを確認して計画を表示します。ゾーンの照会にはマスターキー(= 指紋)が要るため、dry-run では Cloudflare を呼びません。
- サーバーへ届けられなければ、発行したトークンをすぐ削除します。
- 指紋を待てない自動処理(証明書更新・キャッシュ削除)には、ゾーン限定・IP ロックの小さなトークンを別に渡してください。このコマンドは人が承認する変更用です。
- 初回は Command Line Tools の `swiftc` で小さな補助プログラム(`~/.ez2-aib/bin/aib-sehelper`、ソースは `aib/src/cockpit/sehelper/`)をビルドします。
  ソースが変わったときだけ再ビルドします。`swiftc` がなければ Command Line Tools のインストールを案内します。

## 残るリスク(ありのままに)

- **同じアカウントのプログラムは、ユーザーが書き込めるコードを変えられます。** `~/.ez2-aib/bin/aib-sehelper` と aib 本体は本人が書き込める場所にあります。
  同じアカウントの AI がそれらを差し替えると、本人が**正当な要求を 1 回承認した**ときに、その回に開封されたマスターキーを持ち出せます。
  Secure Enclave が防ぐのは「本人の指紋なしに取ること」で、「本人は触れたが、動いたプログラムが差し替えられていた」は防げません。今後の案:補助プログラムを root 所有の場所に置き、
  実行前に署名済みマニフェストのハッシュと照合する(この版では未実装、統合担当が判断)。
- マスターキーは任意の権限のトークンを発行できます。その安全性は上の Secure Enclave の関門だけに依存します。二重目として、Cloudflare 側でマスターキーに IP 制限(自分の Mac / 事務所の出口のみ)を付けてください。

## 導入手順書

3 段階。① は本人のみが操作し、③ の各手順は本番環境なので 1 つずつ本人の了承を待つ。

### ① マスター鍵を作り Secure Enclave に封入(本人のみ、AI は関与しない)
1. Cloudflare ダッシュボード → My Profile → API Tokens → Create Custom Token:
   - 権限:User → **API Tokens: Edit**、Zone → **Zone: Read**(発行対象のゾーン、または All zones)。
   - 推奨:Client IP Address Filtering = この Mac の送信元 IP(固定でなければ省略)。
2. 本人が端末で実行(Claude Code では `!`、1 行、先頭に空白なし):`!aib secret ask CF_TOKEN_MINTER`
   伏せ字欄に貼り付け → 指紋。値は入力欄から Secure Enclave の封入ファイルへ入るだけで、会話や `.env` には入らない。
3. `aib cf list` が指紋を求めアカウントのトークンを一覧表示できれば、マスター鍵は使える。

### ② 実機テスト(本人が指紋を押す)
1. `aib cf grant <zone> zone-read --push <host>:/root/cf-grant-test.env --ttl 2m --why "導入テスト" --dry-run`:計画を確認。IP ロックに対象ホストの IPv4 / IPv6 が含まれること。
2. `--dry-run` を外して実行:ダイアログにゾーン・権限・2m・対象ホストが書かれていること。承認する。
3. 対象ホストでそのトークンを使う:`/user/tokens/verify` が active、ゾーンの読み取りは成功、DNS の読み取りは拒否。
4. 2 分後に再度 verify ⇒ 失効。`aib cf list` で期限切れが見える。
5. もう一度 grant を実行しキャンセル ⇒ トークンは作られず、監査ログに拒否が 1 件増える。
6. 対象ホストのテストファイルを削除(単一対象)し、結果を記録・報告する。

### ③ 古い広範囲の鍵を撤去(本番、1 つずつ本人の了承を得て)
指紋を待てない自動ジョブ(証明書更新、キャッシュ削除など)を先にゾーン限定・IP ロックの小さなトークンへ移してから:
1. ローカル金庫に古いマスター鍵の行が残っていれば ⇒ 本人がその行を削除。
2. サーバー上の global key と広範囲トークン:バックアップしてから設定ファイルから削除。同じディレクトリの古いバックアップにも同じ鍵があるので一緒に処理する。
3. Cloudflare で広範囲トークンを失効させ、global key は本人が **Change / Roll**(無効化だけはできない)。
4. 24 時間以内に証明書更新・キャッシュ削除・ドメイン監査などのログを確認。
