[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Cloudflare 臨時金鑰(`aib cf grant`)

不在伺服器上留長期、大權限的 Cloudflare 金鑰:`aib cf grant` 每次都先要 Touch ID,再現開一把 token ——
**只限一個網域**、**只限你點名的權限**、**只能從目的伺服器的 IP 用**、**1–60 分鐘後自動失效**(預設 10 分鐘)。
token 經 ssh 的 stdin 直接寫到伺服器(權限 600),值不印、不寫 log、不當參數。

```
aib secret ask CF_TOKEN_MINTER                      # once: store the minter key (Touch ID)
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

- **母鑰**:在 Cloudflare 後台建一把只有 **API Tokens Write** 的 token(要核發的網域另加 **Zone Read**,才查得到網域),
  再用 `aib secret ask CF_TOKEN_MINTER` 存起來。它只放在這台 Mac 的加密金鑰櫃,伺服器都不需要它。
- **權限**(白名單):`dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `routes-write` `workers-write`。
- **IP 鎖**:ssh 連的那個位址 + 伺服器自己對 Cloudflare 的出口 IPv4/IPv6 + 你另給的 `--ip CIDR`。一個都找不到就不建。
- `grant` 與 `revoke` 一定要 Touch ID,沒有任何略過的旗標或環境變數;不是 Mac ⇒ 不建。
- 送不到伺服器 ⇒ 立刻把剛建的 token 刪掉。
- 每次核准(成功或被拒)都追加一行到 `~/.ez2-aib/audit/cf-grant.jsonl`(不含值)。
- 等不了指紋的自動工作(續憑證、清快取)請另給一把限網域、鎖 IP 的小 token;這個指令是給「有人核准」的變更用的。
