[English](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/CF-GRANT.ko.md)

# Temporary Cloudflare keys (`aib cf grant`)

Keep no long-lived, high-privilege Cloudflare key on servers. `aib cf grant` asks for Touch ID every time, then mints a token that is
**limited to one zone**, **limited to the permissions you name**, **usable only from the target server's IPs**, and **expires after 1–10 minutes** (default 10).
The token lives only in memory and goes to the server over ssh stdin (mode 600). It never touches this Mac's disk, is never printed, logged or passed as an argument.

```
aib secret ask CF_TOKEN_MINTER                      # once: seal the minter key into this Mac's Secure Enclave
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --dry-run
aib cf grant ez2.app dns-write --push eu01:/etc/ez2-cloud/grant.env --ttl 10m --var CF_API_TOKEN
aib cf list
aib cf revoke <token_id>
```

## What the defence actually is

- **The minter key is sealed in this Mac's Secure Enclave only.** Create a Cloudflare token with only **API Tokens Write** (plus **Zone Read**
  on the zones you will grant for, so the zone can be looked up), then run `aib secret ask CF_TOKEN_MINTER` yourself. The value is encrypted with a
  Secure Enclave key (CryptoKit `SecureEnclave.P256`, access control `biometryCurrentSet`) into `~/.ez2-aib/secrets/se/CF_TOKEN_MINTER.sealed`.
- **Every use of the minter makes the system itself ask for Touch ID.** Unsealing needs a key agreement inside the Secure Enclave, and that key
  requires biometrics on every use. macOS / the Secure Enclave enforce this, not an aib check. A copied sealed file is useless: only this Mac's Secure
  Enclave plus your fingerprint can open it. Changing enrolled fingerprints invalidates it; run `aib secret ask CF_TOKEN_MINTER` again.
- **aib writes the prompt text**: zone, permissions, TTL and target host. Read it before you touch. One approval mints one token.
- **The minter is never allowed in a file.** The key store (`cloudflare.env`, including LINE fill-in) refuses `CF_TOKEN_MINTER`; if a file already
  holds that line, `grant` / `list` / `revoke` refuse (`minter_in_file`). Seal it as above, then delete that line.
- **The audit log is a record, not a defence.** Each attempt that reaches the unseal step (approved or refused) appends a line to
  `~/.ez2-aib/audit/cf-grant.jsonl` (no values).

## Other rules

- **Permissions** (allow-list): `dns-read` `dns-write` `cache-purge` `zone-read` `waf-write` `ssl-write` `zone-settings-read` `zone-settings-write` `routes-write` `workers-write` `redirect-read` `redirect-write` `bulk-redirect-read` `rule-lists-read`.
- **IP lock**: the address ssh connects to + the server's own IPv4/IPv6 egress to Cloudflare + any `--ip CIDR`. If none can be found, nothing is minted.
- There is no flag or environment variable that skips Touch ID; not a Mac ⇒ nothing is minted. `AIB_TOUCHID=0` only turns it off.
- `--dry-run` checks arguments and the IP lock and prints the plan. Looking up the zone needs the minter (= a fingerprint), so dry-run does not call Cloudflare.
- If the token cannot be delivered, it is deleted at once.
- Automated jobs that cannot wait for a fingerprint (certificate renewal, cache purge) should get their own small, zone-limited, IP-locked token; this command is for changes a person approves.
- First use builds a small helper with the Command Line Tools `swiftc` (`~/.ez2-aib/bin/aib-sehelper`, source in `aib/src/cockpit/sehelper/`);
  it is rebuilt only when the source changes. Without `swiftc` you are told to install the Command Line Tools.

## Remaining risk (stated plainly)

- **Programs in the same account can change user-writable code.** `~/.ez2-aib/bin/aib-sehelper` and aib itself live where your account can write.
  An AI in the same account that replaces them can, at the moment you **approve one legitimate request**, also take the minter unsealed for it.
  The Secure Enclave stops "getting it without your fingerprint"; it cannot stop "you touched, but the program had been swapped". Follow-up: install the
  helper in a root-owned location and check its hash against a signed manifest before running it (not in this version; for the integrator to decide).
- The minter can mint tokens of any permission; its safety rests entirely on the Secure Enclave gate above. As a second layer, give the minter an IP
  restriction in the Cloudflare dashboard (only your Mac's / office's egress).

## Rollout runbook

Three stages. Stage ① is done by the owner alone; every step of stage ③ touches production and waits for the owner's go-ahead.

### ① Create the minter and seal it into the Secure Enclave (owner only; no AI involved)
1. Cloudflare dashboard → My Profile → API Tokens → Create Custom Token:
   - Permissions: User → **API Tokens: Edit**; Zone → **Zone: Read** (the zones you will grant for, or All zones).
   - Recommended: Client IP Address Filtering = this Mac's egress IP (skip if it is not fixed).
2. Run it yourself in a terminal (in Claude Code with `!`, one line, no leading space): `!aib secret ask CF_TOKEN_MINTER`
   Paste into the masked box → fingerprint. The value goes from the box into the sealed Secure Enclave file only; never into the chat or any `.env`.
3. `aib cf list` asks for a fingerprint and lists the account's tokens ⇒ the minter works.

### ② Real-device test (owner present to touch the sensor)
1. `aib cf grant <zone> zone-read --push <host>:/root/cf-grant-test.env --ttl 2m --why "rollout test" --dry-run`: check the plan; the IP lock must include the target host's IPv4 / IPv6.
2. Run again without `--dry-run`: the dialog must name the zone, permission, 2m and target host; approve.
3. On the target host with that token: `/user/tokens/verify` is active; reading the zone works; reading DNS is refused.
4. After 2 minutes verify again ⇒ expired; `aib cf list` shows it expired.
5. Run grant once more and press Cancel ⇒ no token is created and the audit log gets a refused entry.
6. Delete the test file on the target host (single target), record and report the results.

### ③ Retire the old broad keys (production; one step at a time, each with the owner's go-ahead)
First move unattended jobs that cannot wait for a fingerprint (certificate renewal, cache purge, …) to zone-limited, IP-locked small tokens, then:
1. If the local vault still has an old minter line ⇒ the owner deletes that line.
2. Global key and broad tokens on servers: back up, then remove them from the config files; old backup copies in the same directory hold the same keys and must be handled too.
3. Revoke the broad token in the Cloudflare dashboard; the owner **Changes / Rolls** the global key (it cannot just be disabled).
4. Within 24 hours check the logs of certificate renewal, cache purge, domain audit and similar jobs.
