#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""全球對談的席金鑰與主人金鑰(開發文件第 5.3、5.4 節;第 22.1 節 PR-6 = 第 1 期 1.2)。

只用標準庫:私鑰 = 32 位元組亂數(1 ≤ k < n);公鑰 = secp256k1 上 k·G 的 x 座標(BIP-340 x-only);
npub = NIP-19 bech32。**這裡不簽章、不做 Schnorr、不做 NIP-44** —— 那些在轉接程式的 venv 裡交給 nostr-sdk,
它從同一把私鑰(hex)載入。為什麼產生放核心:nostr-sdk 是選用套件,CI 與沒開全球對談的機器都沒有,
金鑰規則(跟著人格走、同人格多席、封存)要在那些地方也測得到;正確性用 BIP-340 / NIP-19 的公開測試向量釘住。
★ 這裡的點乘不是常數時間:只在本機產生金鑰時算一次公鑰,不對外簽章,時間差洩不出去;簽章一律走 nostr-sdk。

存放(第 5.3 節):
  <secrets>/nostr/<persona_uid>.key            人格的主金鑰(第一個綁上的席持有)
  <secrets>/nostr/<persona_uid>-<seat5>.key    同人格第二個活席另發的一把
  <secrets>/nostr/owner.key                    主人金鑰(第 5.4 節,簽艙位名冊)
  <secrets>/nostr/retired/                     封存的搬來這裡,不刪
  資料夾 0700、檔案 0600;內容是 secret_crypto 的 enc.v1. 信封(AAD 綁 "nostr/<檔名>",換檔名解不開),
  解密鑰匙是 <secrets>/wrap.key。<secrets> = aib_secrets.secrets_dir()(EZ2_AIB_SECRETS_DIR 可改)。
對照表 ~/.ez2-aib/identity/nostr-map.json:只放公開的東西(npub、席、艙、狀態),**不放私鑰**。
★ 私鑰只有 load_secret() 會解開,呼叫端只該是轉接程式;nsec 字串這裡一律不產生、不落檔、不印。
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "bootstrap"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import secret_crypto as SC  # noqa: E402
from atomic_json import write_json  # noqa: E402

# ── secp256k1 ──
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = 3 * x1 * x1 * pow(2 * y1, P - 2, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, P - 2, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k, point=G):
    acc, add = None, point
    while k:
        if k & 1:
            acc = _add(acc, add)
        add = _add(add, add)
        k >>= 1
    return acc


def pubkey_hex(secret_hex):
    """私鑰 hex(64 碼)→ x-only 公鑰 hex。範圍不對 ⇒ ValueError。"""
    k = int(secret_hex, 16)
    if not 1 <= k < N or len(secret_hex) != 64:
        raise ValueError("secret out of range")
    return "%064x" % _mul(k)[0]


def new_secret_hex():
    while True:
        k = int.from_bytes(os.urandom(32), "big")
        if 1 <= k < N:
            return "%064x" % k


# ── NIP-19 bech32 ──
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (b >> i) & 1 else 0
    return chk


def _hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data, frm, to, pad=True):
    acc, bits, out, maxv = 0, 0, [], (1 << to) - 1
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & maxv)
    if pad and bits:
        out.append((acc << (to - bits)) & maxv)
    elif not pad and (bits >= frm or ((acc << (to - bits)) & maxv)):
        raise ValueError("bad padding")
    return out


def bech32_encode(hrp, raw):
    data = _convertbits(raw, 8, 5)
    poly = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[d] for d in data + [(poly >> 5 * (5 - i)) & 31 for i in range(6)])


def bech32_decode(text):
    """→ (hrp, bytes);格式或檢查碼不對 ⇒ ValueError。"""
    s = text.lower()
    if s != text and text.upper() != text:
        raise ValueError("mixed case")
    pos = s.rfind("1")
    if pos < 1 or len(s) - pos < 7:
        raise ValueError("bad bech32")
    hrp, data = s[:pos], [_CHARSET.find(c) for c in s[pos + 1:]]
    if -1 in data or _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("bad checksum")
    return hrp, bytes(_convertbits(data[:-6], 5, 8, pad=False))


def npub(pub_hex):
    return bech32_encode("npub", bytes.fromhex(pub_hex))


def npub_to_hex(text):
    hrp, raw = bech32_decode(text)
    if hrp != "npub" or len(raw) != 32:
        raise ValueError("not an npub")
    return raw.hex()


# ── 存放 ──
PERSONA_UID_RE = re.compile(r"^[0-9a-z]{6}$")      # card_registry.UID_RE;不合形(舊式、directed)的席不發金鑰
SEAT5_RE = re.compile(r"^[0-9a-z]{4,8}$")          # seat_address._CODE_RE
CABIN_RE = re.compile(r"^[0-9a-z]{1,4}$")
MAP_VERSION = 1


def secrets_dir(env=None):
    env = os.environ if env is None else env
    explicit = env.get("EZ2_AIB_SECRETS_DIR")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "secrets"


def keys_dir(env=None):
    return secrets_dir(env) / "nostr"


def map_path(env=None):
    env = os.environ if env is None else env
    explicit = env.get("AIB_NOSTR_MAP")
    if explicit:
        return Path(explicit)
    return Path(env.get("HOME") or os.path.expanduser("~")) / ".ez2-aib" / "identity" / "nostr-map.json"


def _mkdir_private(d):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)


def _key_stem(persona_uid, seat5=None):
    if not PERSONA_UID_RE.match(persona_uid or ""):
        raise ValueError("bad persona_uid")
    if seat5 is None:
        return persona_uid
    if not SEAT5_RE.match(seat5 or ""):
        raise ValueError("bad seat5")
    return "%s-%s" % (persona_uid, seat5)


def _write_key(stem, env=None):
    """產生一把、加密落檔(O_EXCL:檔已在就不蓋)。回 npub。私鑰在這個函式外看不到。"""
    d = keys_dir(env)
    _mkdir_private(secrets_dir(env))
    _mkdir_private(d)
    secret = new_secret_hex()
    pub = pubkey_hex(secret)
    env_blob = SC.encrypt_value(secret, "nostr/" + stem, str(secrets_dir(env)))
    secret = None
    fd = os.open(str(d / (stem + ".key")), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (env_blob + "\n").encode("ascii"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return npub(pub)


def store_secret(stem, secret, env=None, replace=False):
    """還原備份用(第 27.6 節):把一把已知的私鑰加密落檔。replace=False 時檔已在就 FileExistsError(不蓋)。
    回 npub。私鑰不印、不進例外訊息。"""
    if not re.fullmatch(r"[0-9a-f]{64}", secret or ""):
        raise ValueError("bad secret")
    if stem != "owner" and not re.fullmatch(r"[0-9a-z]{6}(-[0-9a-z]{4,8})?", stem or ""):
        raise ValueError("bad stem")
    d = keys_dir(env)
    _mkdir_private(secrets_dir(env))
    _mkdir_private(d)
    pub = pubkey_hex(secret)
    env_blob = SC.encrypt_value(secret, "nostr/" + stem, str(secrets_dir(env)))
    path = d / (stem + ".key")
    tmp = d / (".%s.key.tmp" % stem)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (env_blob + "\n").encode("ascii"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    if replace:
        os.replace(str(tmp), str(path))
    else:
        try:
            os.link(str(tmp), str(path))
        finally:
            tmp.unlink()
    return npub(pub)


def load_secret(stem, env=None):
    """**只給轉接程式用**:解開一把金鑰 → 私鑰 hex。不印、不落檔、不進例外訊息。"""
    raw = (keys_dir(env) / (stem + ".key")).read_text(encoding="ascii").strip()
    return SC.decrypt_value(raw, "nostr/" + stem, str(secrets_dir(env)))


def read_map(env=None):
    try:
        rec = json.loads(map_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict) or rec.get("v") != MAP_VERSION:
        rec = {"v": MAP_VERSION, "owner": None, "seats": []}
    rec.setdefault("seats", [])
    return rec


def _save_map(rec, env=None):
    p = map_path(env)
    _mkdir_private(p.parent)
    write_json(str(p), rec)
    os.chmod(str(p), 0o600)


def ensure_owner(env=None, now=None):
    """主人金鑰:沒有才產。回 npub。"""
    rec = read_map(env)
    if rec.get("owner") and (keys_dir(env) / "owner.key").is_file():
        return rec["owner"]["npub"]
    if (keys_dir(env) / "owner.key").is_file():
        raise RuntimeError("owner.key exists but is not in the map")   # 不猜:不重產、不覆蓋
    rec["owner"] = {"npub": _write_key("owner", env), "created_at": int(time.time() if now is None else now)}
    _save_map(rec, env)
    return rec["owner"]["npub"]


def sync_seats(live, env=None, now=None):
    """依活席清單發 / 轉 / 封存金鑰(第 5.3 節)。live = [{seat5, cabin, persona_uid}, …]。回本輪動作清單。

    規則:
      - 人格的主金鑰(<persona_uid>.key)跟著人格走:持有它的席不在活席清單(換腦、換席)⇒ 交給這個人格現在的活席,npub 不變。
      - 同一個人格同時有兩個以上活席:第一個(已持有主金鑰的那席)留著;其他席各發一把 <persona_uid>-<seat5>.key。
      - 活席清單裡沒有、手上只有「第二把」的席 ⇒ 那把封存(搬 retired/、對照表標 retired),不刪。
        主金鑰不因為暫時沒有活席而封存(人格還在,下次綁上照用);人格封存另走 retire_persona()。
    """
    now = int(time.time() if now is None else now)
    rec = read_map(env)
    seats = rec["seats"]
    acts = []
    live = [s for s in live if PERSONA_UID_RE.match(s.get("persona_uid") or "") and SEAT5_RE.match(s.get("seat5") or "")]
    live_by_seat = {s["seat5"]: s for s in live}
    active = [e for e in seats if e.get("status") == "active"]
    # 1. 第二把:席不活了 ⇒ 封存
    for e in active:
        if e.get("extra") and e["seat5"] not in live_by_seat:
            _retire_entry(e, env, now)
            acts.append(("retired", e["persona_uid"], e["seat5"]))
    # 2. 每一個活席
    for s in sorted(live, key=lambda x: x["seat5"]):
        pu, s5 = s["persona_uid"], s["seat5"]
        mine = [e for e in seats if e.get("status") == "active" and e["seat5"] == s5]
        if any(e["persona_uid"] == pu for e in mine):
            for e in mine:
                if e["persona_uid"] == pu and e.get("cabin") != s.get("cabin"):
                    e["cabin"] = s.get("cabin")
            continue
        main = next((e for e in seats if e.get("status") == "active" and e["persona_uid"] == pu and not e.get("extra")), None)
        if main is None:
            entry = {"persona_uid": pu, "seat5": s5, "cabin": s.get("cabin"), "key": _key_stem(pu),
                     "npub": _write_key(_key_stem(pu), env), "created_at": now, "status": "active", "extra": False}
            seats.append(entry)
            acts.append(("created", pu, s5))
        elif main["seat5"] not in live_by_seat or live_by_seat[main["seat5"]]["persona_uid"] != pu:
            acts.append(("moved", pu, s5, main["seat5"]))
            main.update(seat5=s5, cabin=s.get("cabin"), moved_at=now)
        else:
            stem = _key_stem(pu, s5)
            entry = {"persona_uid": pu, "seat5": s5, "cabin": s.get("cabin"), "key": stem,
                     "npub": _write_key(stem, env), "created_at": now, "status": "active", "extra": True}
            seats.append(entry)
            acts.append(("created_extra", pu, s5))
    _save_map(rec, env)
    return acts


def _retire_entry(e, env, now):
    d = keys_dir(env)
    _mkdir_private(d / "retired")
    src = d / (e["key"] + ".key")
    if src.is_file():
        dst = d / "retired" / ("%s.%d.key" % (e["key"], now))
        os.rename(str(src), str(dst))
    e.update(status="retired", retired_at=now)


def retire_persona(persona_uid, env=None, now=None):
    """人格封存:這個人格的所有金鑰(主金鑰與第二把)都封存。回封存幾把。"""
    now = int(time.time() if now is None else now)
    rec = read_map(env)
    n = 0
    for e in rec["seats"]:
        if e.get("status") == "active" and e["persona_uid"] == persona_uid:
            _retire_entry(e, env, now)
            n += 1
    _save_map(rec, env)
    return n


def live_seats(home=None, env=None):
    """這台機器上綁著的席(各艙 seats/w*.json,不含 retired/)⇒ [{seat5, cabin, persona_uid, name, role}]。
    席碼用 seat_address.code_of;人格看 identity/seat-display.json 的 display[<席碼>].persona_uid
    (bindings.json 只收有第二大腦路由的席,display 才是全部)。沒綁人格的席不列。"""
    import seat_who
    from seat_address import code_of
    if home is None and env is not None:
        # 跟呼叫端同一個根(測試沙盒不准讀到真的 ~/.ez2-aib):state_root 沒設環境變數時用 expanduser,
        #   那讀的是本行程的 HOME 不是 env 的 ⇒ 這裡照 env 的 HOME 自己推。
        home = env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME") or (
            os.path.join(env["HOME"], ".ez2-aib") if env.get("HOME") else None)
    home = home or seat_who._home()
    try:
        disp = json.loads((Path(home) / "identity" / "seat-display.json").read_text(encoding="utf-8")).get("display") or {}
    except (OSError, ValueError, AttributeError):
        disp = {}
    out, seen = [], set()
    for inst, _path, rec in seat_who._iter_seats(home):
        code = code_of(rec)
        pu = (disp.get(code) or {}).get("persona_uid") if code else None
        if not code or code in seen or not isinstance(pu, str):
            continue
        seen.add(code)
        out.append({"seat5": code, "cabin": inst.rsplit("-", 1)[-1] if inst else None, "persona_uid": pu,
                    "name": (disp.get(code) or {}).get("display_name"), "role": rec.get("role")})   # 名冊用(1.3)
    return out


def public_view(env=None):
    """給 status / 名冊用:只有公開欄位。"""
    rec = read_map(env)
    return {"owner": (rec.get("owner") or {}).get("npub"),
            "seats": [{k: e.get(k) for k in ("persona_uid", "seat5", "cabin", "npub", "status", "extra")}
                      for e in rec["seats"]]}


def fingerprint(text):
    """測試用:私鑰之類的東西只比對雜湊,不印原文。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
