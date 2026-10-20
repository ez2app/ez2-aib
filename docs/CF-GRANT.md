[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Temporary Cloudflare keys (`aib cf grant`)

Instead of leaving a long-lived, broad Cloudflare key on a server, `aib cf grant` asks for Touch ID every time and then
creates a token that only works for **one zone**, **only the permissions you name**, **only from the target server's IP
addresses**, and **expires after 1–60 minutes** (10 by default). The token is written straight to the server over ssh
stdin (file mode 600); its value is never printed, logged or passed as an argument.

```
aib secret ask CF_TOKEN_MINTER                      # once: store the minter key (Touch ID)
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

- **Minter key**: in the Cloudflare dashboard create a token with only **API Tokens Write** (add **Zone Read** on the zones
  you will grant for, so the zone can be looked up), then store it with `aib secret ask CF_TOKEN_MINTER`. It stays in this
  Mac's encrypted vault; no server needs it.
- **Permissions** (allow-list): `dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `routes-write` `workers-write`.
- **IP lock**: the address ssh connects to, plus the server's own outgoing IPv4/IPv6 as Cloudflare sees it, plus any `--ip CIDR`.
  If no address can be found, nothing is created.
- **Touch ID** is required for `grant` and `revoke`; there is no flag or variable to skip it. Not on a Mac ⇒ nothing is created.
- If the token cannot be delivered to the server it is deleted immediately.
- Every approval (granted or refused) is appended to `~/.ez2-aib/audit/cf-grant.jsonl` (no values).
- Background jobs that cannot wait for a fingerprint (certificate renewal, cache purges) should get their own small,
  IP-locked long-lived token instead; this command is for changes a person approves.
