#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Who may sign aib licenses: the trusted Ed25519 public keys (safe to publish).

The matching private key stays with the maintainer only (tools/license/aib-license.py keygen keeps it
under the signer's home folder, never in a repository). To rotate: add the new public key here, ship,
re-sign licenses, then remove the old key.

DRAFT_ACCEPT_ANY is the transition switch from the old draft check ("any non-empty signature is ok").
While it is True, licenses without a real signature are still accepted, so existing installs keep
working until the maintainer has generated a key and re-signed them. A real signature that does not
verify is rejected either way. The public release refuses to export while it is True or while no key
is listed (tools/oss/export_aib.py --public).
"""
# 10-08(the maintainer 同意):草稿簽章換成真的公鑰驗證,之後授權模組可以開源。私鑰只在簽發的人手上。
TRUSTED_PUBLIC_KEYS: tuple = (
    # "ed25519:<64 hex>",   ← tools/license/aib-license.py pubkey 印出的那一行
    "ed25519:90e5afdcd8a5ed6fc71a151b329c17fb6a911c19c1c4e035221accee0c953be5",   # the maintainer 10-09 keygen(發證主鑰;私鑰在 Mac mini M4,10-16 ssh 用 pubkey 對過)
    # 10-16 the maintainer:三台 Mac 都能簽,各台自己的鑰匙(私鑰不出那台;哪台遺失只撤那一行)
    "ed25519:cb973123600961450410fc8471d833126ffcc1d97843378ddd639934faf15a3e",   # MacBook Pro 16(10-16 keygen)
)
# 10-09:私有開發倉照舊 True —— 約 30 支測試用草稿簽章的授權開付費功能。公開版(使用者實際裝的那一份)
#   由 tools/oss/export_aib.py 匯出時改成 False,只認上面那把鑰匙簽的授權。
DRAFT_ACCEPT_ANY = False   # 公開版:授權過渡期已結束,只認信任公鑰簽的授權(匯出時改)

# 線上啟用伺服器簽「啟用憑證」用的鑰匙(10-08:綁機器 + 線上啟用都要)。跟上面的發證主鑰分開:
#   伺服器一定要放一把鑰匙在線上,外洩時只撤這把、重新啟用;發證主鑰照舊只在簽發人離線的電腦上。
TRUSTED_ACTIVATION_KEYS: tuple = (
    # "ed25519:<64 hex>",   ← tools/license/activation_server.py --print-pubkey 印出的那一行
)
