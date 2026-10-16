#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球對談金鑰備份與還原(開發文件第 27.6 節)。只用標準庫。

為什麼:主人與各席的 Nostr 私鑰只在這台機器(`~/.ez2-aib/secrets/nostr/`,用本機包裝金鑰加密)。
機器壞了 ⇒ 身分全失,好友要重加、名冊要重發。

備份檔(0600,JSON):{"v":1, "kdf":"pbkdf2-sha256", "iter", "salt", "nonce", "ct", "tag"}
  - 通關密語 ⇒ PBKDF2-HMAC-SHA256(60 萬次,OWASP 建議值)⇒ 32 位元組金鑰 ⇒ XChaCha20-Poly1305(secret_crypto 那一套)加密整份內容。
    不用 scrypt:macOS 內建 Python 3.9 的 hashlib 沒有 scrypt(10-16 測出來);PBKDF2 每一版都有。
  - 內容:對照表(nostr-map.json)、每一把私鑰(以 stem 為鍵)、好友 / 我的其他機器名單、主人資料。
  - 檔裡沒有明文私鑰;通關密語不落檔、不進 log、不從命令列參數拿(只從終端機或 --passphrase-stdin)。
還原:
  - 本機沒有主人金鑰 ⇒ 全部寫回。
  - 本機主人金鑰跟備份同一把 ⇒ 只補本機沒有的金鑰與名單項目(不蓋)。
  - 不同一把 ⇒ 不動任何檔(owner_differs);要換就 --replace,而且全球對談要先關(aib chat off 會撤舊名冊)。
"""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "bootstrap"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402
import secret_crypto as SC  # noqa: E402

VERSION = 1
AAD = b"aib-chat-backup/v1"
KDF = "pbkdf2-sha256"
ITERATIONS = 600000
MIN_PASS = 8


class BackupError(ValueError):
    """原因碼:bad_passphrase / short_passphrase / exists / bad_file / owner_differs / chat_on / no_keys。"""


def _kdf(passphrase, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def _stems(m):
    out = []
    if m.get("owner"):
        out.append("owner")
    out += [e["key"] for e in m["seats"] if e.get("key")]
    return out


def backup(path, passphrase, env=None):
    """寫備份檔(O_EXCL:檔已在就不蓋)。回備份了幾把金鑰。"""
    if len(passphrase or "") < MIN_PASS:
        raise BackupError("short_passphrase")
    m = NK.read_map(env)
    if not m.get("owner"):
        raise BackupError("no_keys")
    keys = {st: NK.load_secret(st, env) for st in _stems(m)}
    body = {"v": VERSION, "map": m, "keys": keys, "friends": CR.read_friends(env), "mine": CR.read_friends(env, "mine"),
            "profile": CR.read_profile(env)}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    keys = body = None
    salt = os.urandom(16)
    nonce, ct, tag = SC._encrypt_bytes(_kdf(passphrase, salt, ITERATIONS), raw, AAD)
    raw = None
    rec = {"v": VERSION, "kdf": KDF, "iter": ITERATIONS, "salt": salt.hex(), "nonce": nonce.hex(),
           "ct": base64.b64encode(ct).decode("ascii"), "tag": tag.hex(), "owner": m["owner"]["npub"]}
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BackupError("exists")
    try:
        os.write(fd, (json.dumps(rec) + "\n").encode("ascii"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return len(_stems(m))


def _open(path, passphrase):
    try:
        rec = json.loads(Path(path).read_text(encoding="ascii"))
        it = int(rec.get("iter") or 0)
        if rec.get("v") != VERSION or rec.get("kdf") != KDF or not 100000 <= it <= 10000000:
            raise ValueError("version")
        key = _kdf(passphrase or "", bytes.fromhex(rec["salt"]), it)
        nonce, ct, tag = bytes.fromhex(rec["nonce"]), base64.b64decode(rec["ct"]), bytes.fromhex(rec["tag"])
    except (OSError, ValueError, KeyError, TypeError):
        raise BackupError("bad_file")
    try:
        raw = SC._decrypt_bytes(key, nonce, ct, tag, AAD)      # Poly1305 標籤不對 ⇒ CryptoError(錯密語或檔被改)
    except SC.CryptoError:
        raise BackupError("bad_passphrase")
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise BackupError("bad_file")
    if not isinstance(body, dict) or body.get("v") != VERSION or not isinstance(body.get("keys"), dict):
        raise BackupError("bad_file")
    return body


def restore(path, passphrase, env=None, replace=False, chat_on=False):
    """還原。回 {"owner": npub, "keys": 寫了幾把, "mode": "fresh"|"merge"|"replace"}。出錯不動任何檔。"""
    body = _open(path, passphrase)
    bm = body["map"]
    b_owner = (bm.get("owner") or {}).get("npub")
    if not b_owner or "owner" not in body["keys"] or NK.npub(NK.pubkey_hex(body["keys"]["owner"])) != b_owner:
        raise BackupError("bad_file")
    for st in _stems(bm):
        if st not in body["keys"]:
            raise BackupError("bad_file")
    lm = NK.read_map(env)
    l_owner = (lm.get("owner") or {}).get("npub")
    if l_owner and l_owner != b_owner:
        if not replace:
            raise BackupError("owner_differs")
        if chat_on:
            raise BackupError("chat_on")
        mode = "replace"
    else:
        mode = "merge" if l_owner else "fresh"
    n = 0
    for st in _stems(bm):
        exists = (NK.keys_dir(env) / (st + ".key")).is_file()
        if exists and mode == "merge":
            continue
        NK.store_secret(st, body["keys"][st], env, replace=(mode == "replace" or exists))
        n += 1
    if mode == "merge":
        have = {e["seat5"] + "|" + (e.get("persona_uid") or "") for e in lm["seats"]}
        lm["seats"] += [e for e in bm["seats"] if e["seat5"] + "|" + (e.get("persona_uid") or "") not in have]
        NK._save_map(lm, env)
    else:
        NK._save_map(bm, env)
    for kind in ("friends", "mine"):
        for f in body.get(kind) or []:
            try:
                CR.add_friend(f["owner"], f.get("name"), env, kind, source=f.get("source"))
            except (CR.RosterError, KeyError, TypeError):
                pass
    if body.get("profile") and not CR.read_profile(env):
        CR._save_profile(dict(body["profile"]), env)
    body = None
    return {"owner": b_owner, "keys": n, "mode": mode}
