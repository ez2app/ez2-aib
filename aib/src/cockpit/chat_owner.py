#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""主人金鑰加密 + 機器配對 + 改清單(開發文件第 37.7 節 K2;設計 v0.3 第 14 節,@abb89 10-18 11:29 / 11:32 准)。只用標準庫。

- 主人金鑰加密:`aib chat owner protect` 之後主人私鑰只以密語加密落地(chat_backup 同一套:PBKDF2-SHA256 60 萬次 +
  XChaCha20-Poly1305),本機包裝金鑰那份刪掉。AI 席讀得到檔、讀不到密語 ⇒ 簽不出機器清單。
  密語至少 12 字元;本機已有的 chat_backup 用這組密語解得開 ⇒ 拒(請本人換一組;不另存任何東西來比對)。
- 界線(照實寫):同一使用者帳號底下的 AI 席理論上錄得到鍵盤輸入(例 tmux pipe-pane)⇒ 密語只從 /dev/tty 讀、關回顯,
  說明建議在**沒有 AI 席的終端機視窗**輸入。解開的私鑰 Python 不能保證清掉記憶體 ⇒ 只能盡量縮短它在記憶體裡的時間。
- 配對碼 = 機器 npub 公鑰的 sha256 前 16 碼(指紋,64 bits)+ 一次性 nonce 8 碼(base32,40 bits),顯示成 6 組 4 碼。
  配對請求(新機器用機器金鑰簽)只帶 nonce 的雜湊。簽章者指紋對不上配對碼 ⇒ 直接不理、不算錯;
  只有本人打錯 nonce 才算一次,同一台錯 3 次作廢;10 分鐘過期;nonce 只用一次。
"""
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "bootstrap"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import chat_devices as DV  # noqa: E402
import nostr_keys as NK  # noqa: E402
import secret_crypto as SC  # noqa: E402
from atomic_json import write_json  # noqa: E402

VERSION = 1
AAD = b"aib-owner-seal/v1"
KDF = "pbkdf2-sha256"
ITERATIONS = 600000
MIN_PASS = 12
PASS_MAX_FAILS = 5              # 主人密語連錯 5 次鎖 30 秒,之後每錯一次翻倍,上限 1 小時(@abb89 10-18 H2;同 #144)
PASS_LOCK_S = 30
PASS_LOCK_MAX_S = 3600
PAIR_D = "aib-pair"
PAIR_TTL_S = 600
PAIR_MAX_FAILS = 3
EXTEND_EVERY_S = 7 * 86400
_B32 = "abcdefghijkmnpqrstuvwxyz23456789"     # 去掉容易看錯的 l / o / 0 / 1


class OwnerError(ValueError):
    """原因碼:short_passphrase / same_as_backup / bad_passphrase / not_sealed / sealed / no_owner / bad_file /
    no_tty / bad_code / no_request / expired / burned / used / too_many / was_revoked / already_listed / not_listed / is_host /
    pass_locked。"""


# ── 主人金鑰加密 ──

def sealed_path(env=None):
    return NK.keys_dir(env) / "owner.sealed"


def is_sealed(env=None):
    return sealed_path(env).exists()


def _kdf(passphrase, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def seal(secret_hex, passphrase, npub, iterations=ITERATIONS):
    """主人私鑰 ⇒ 加密記錄 dict(不落檔)。"""
    if len(passphrase or "") < MIN_PASS:
        raise OwnerError("short_passphrase")
    salt = os.urandom(16)
    nonce, ct, tag = SC._encrypt_bytes(_kdf(passphrase, salt, iterations), secret_hex.encode("ascii"), AAD)
    return {"v": VERSION, "kdf": KDF, "iter": iterations, "salt": salt.hex(), "nonce": nonce.hex(),
            "ct": base64.b64encode(ct).decode("ascii"), "tag": tag.hex(), "npub": npub}


def unseal(rec, passphrase):
    """加密記錄 + 密語 ⇒ 私鑰 hex;錯密語 ⇒ bad_passphrase;檔壞 ⇒ bad_file。解出來的公鑰要等於記錄上的 npub。"""
    try:
        it = int(rec.get("iter") or 0)
        if rec.get("v") != VERSION or rec.get("kdf") != KDF or not 100000 <= it <= 10000000:
            raise ValueError("version")
        key = _kdf(passphrase or "", bytes.fromhex(rec["salt"]), it)
        nonce, ct, tag = bytes.fromhex(rec["nonce"]), base64.b64decode(rec["ct"]), bytes.fromhex(rec["tag"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OwnerError("bad_file")
    try:
        secret = SC._decrypt_bytes(key, nonce, ct, tag, AAD).decode("ascii")
    except SC.CryptoError:
        raise OwnerError("bad_passphrase")
    if NK.npub(NK.pubkey_hex(secret)) != rec.get("npub"):
        raise OwnerError("bad_file")
    return secret


def read_sealed(env=None):
    try:
        return json.loads(sealed_path(env).read_text(encoding="ascii"))
    except FileNotFoundError:
        raise OwnerError("not_sealed")
    except (OSError, ValueError):
        raise OwnerError("bad_file")


def check_not_backup_pass(passphrase, backups):
    """本機已有的 chat_backup 用這組密語解得開 ⇒ same_as_backup(主人密語要跟備份密語不同)。"""
    import chat_backup as CB
    for p in backups or ():
        try:
            CB._open(p, passphrase)
        except CB.BackupError:
            continue
        raise OwnerError("same_as_backup")


def protect(passphrase, env=None, backups=(), iterations=ITERATIONS):
    """把本機主人金鑰改成密語加密:寫 owner.sealed ⇒ 用同一組密語解一次驗得回來 ⇒ 才刪包裝金鑰那份 owner.key。
    回主人 npub。任何一步不對 ⇒ 不刪原檔。"""
    m = NK.read_map(env)
    if not m.get("owner"):
        raise OwnerError("no_owner")
    if is_sealed(env):
        raise OwnerError("sealed")
    if len(passphrase or "") < MIN_PASS:
        raise OwnerError("short_passphrase")
    check_not_backup_pass(passphrase, backups)
    npub = m["owner"]["npub"]
    secret = NK.load_secret("owner", env)
    rec = seal(secret, passphrase, npub, iterations)
    secret = None
    p = sealed_path(env)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (json.dumps(rec) + "\n").encode("ascii"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        if NK.npub(NK.pubkey_hex(unseal(read_sealed(env), passphrase))) != npub:
            raise OwnerError("bad_file")
    except OwnerError:
        p.unlink()
        raise
    (NK.keys_dir(env) / "owner.key").unlink()
    return npub


def pass_fails_path(env=None):
    return NK.secrets_dir(env) / "owner-pass-fails.json"


def open_owner(passphrase, env=None, now=None):
    """要簽清單時:密語解開主人私鑰(呼叫端用完就丟,盡量縮短在記憶體裡的時間)。
    連錯 PASS_MAX_FAILS 次起遞增鎖(讀-判-解-寫整段在 fcntl 鎖底下,併發猜繞不過);鎖住時對的也不收(pass_locked)。
    界線(照實寫):計數檔同帳號的程式刪得掉;真正的防線是密語強度 + 60 萬次 PBKDF2。"""
    import fcntl
    now = time.time() if now is None else now
    p = pass_fails_path(env)
    NK._mkdir_private(NK.secrets_dir(env))
    fd = os.open(str(p) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            n, until = int(st.get("n") or 0), float(st.get("until") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            n, until = 0, 0.0
        if now < until:
            raise OwnerError("pass_locked")
        try:
            secret = unseal(read_sealed(env), passphrase)
        except OwnerError as err:
            if str(err) != "bad_passphrase":
                raise
            n += 1
            lock = min(PASS_LOCK_MAX_S, PASS_LOCK_S * 2 ** (n - PASS_MAX_FAILS)) if n >= PASS_MAX_FAILS else 0
            write_json(str(p), {"v": 1, "n": n, "until": now + lock if lock else 0})
            os.chmod(str(p), 0o600)
            raise
        if n:
            write_json(str(p), {"v": 1, "n": 0, "until": 0})
        return secret
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_passphrase(prompt, tty_path="/dev/tty"):
    """只從控制終端讀、關回顯;沒有終端 ⇒ no_tty(不從 stdin 管線 / 參數 / 環境變數拿)。"""
    import termios
    try:
        fd = os.open(tty_path, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        raise OwnerError("no_tty")
    try:
        if not os.isatty(fd):
            raise OwnerError("no_tty")
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, new)     # 先關回顯(並丟掉提示出現前就打進來的字)再印提示
            os.write(fd, prompt.encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                ch = os.read(fd, 1)
                if not ch:
                    break
                buf += ch
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, old)
            os.write(fd, b"\n")
        return buf.decode("utf-8", "replace").rstrip("\r\n")
    finally:
        os.close(fd)


# ── 配對碼 ──

def fingerprint(npub):
    """機器 npub 的公鑰 32 bytes 取 sha256 前 16 碼 hex。"""
    return hashlib.sha256(bytes.fromhex(NK.npub_to_hex(npub))).hexdigest()[:16]


def new_nonce():
    return "".join(_B32[b % len(_B32)] for b in os.urandom(8))


def nonce_hash(nonce):
    return hashlib.sha256(("aib-pair/" + nonce).encode("ascii")).hexdigest()[:32]


def pair_code(npub, nonce):
    """6 組 4 碼:指紋 16 碼 + nonce 8 碼,用 - 隔開。"""
    s = fingerprint(npub) + nonce
    return "-".join(s[i:i + 4] for i in range(0, 24, 4))


def parse_code(code):
    """本人打的配對碼 ⇒ (指紋, nonce);格式不對 ⇒ bad_code。大小寫、空白、- 都不計較。"""
    s = "".join(ch for ch in str(code or "").lower() if ch in "0123456789abcdefghijklmnopqrstuvwxyz")   # 中文 isalnum 也是真
    fp, nonce = s[:16], s[16:]
    if len(s) != 24 or any(c not in "0123456789abcdef" for c in fp) or any(c not in _B32 for c in nonce):
        raise OwnerError("bad_code")
    return fp, nonce


def pair_d(npub):
    return "%s:%s" % (PAIR_D, fingerprint(npub))


def pair_request(owner, device_npub, nonce, name, at=None):
    """新機器發的配對請求內容(用機器金鑰簽,d = aib-pair:<指紋>)。nonce 本身不上中繼站。"""
    return {"v": VERSION, "owner": owner, "device": device_npub, "nonce_hash": nonce_hash(nonce), "name": name,
            "at": int(time.time() if at is None else at)}


def pick_request(owner, code, requests, now):
    """本人打的配對碼 + 中繼站抓到的請求 [(簽章者 npub, 內容)] ⇒ (請求 dict, 原因)。
    原因:ok / no_request(沒有指紋相符的請求;**簽章者指紋對不上的一律不理、不算錯**)/ bad_nonce(本人打錯 nonce,算一次)/
    expired。"""
    fp, nonce = parse_code(code)
    mine = []
    for signer, content in requests:
        try:
            rec = json.loads(content) if isinstance(content, (str, bytes)) else content
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("v") != VERSION or rec.get("owner") != owner:
            continue
        if rec.get("device") != signer or fingerprint(signer) != fp:
            continue                                     # (a):不是這台簽的 ⇒ 不理、不算
        if not isinstance(rec.get("at"), int):
            continue
        mine.append(rec)
    if not mine:
        return None, "no_request"
    rec = max(mine, key=lambda r: r["at"])
    if not (rec["at"] - DV.CLOCK_SKEW_S <= now <= rec["at"] + PAIR_TTL_S):
        return rec, "expired"
    if rec.get("nonce_hash") != nonce_hash(nonce):
        return rec, "bad_nonce"
    return rec, "ok"


def pair_state_path(root):
    return Path(root) / "pair-state.json"


def _read_state(root):
    try:
        st = json.loads(pair_state_path(root).read_text(encoding="utf-8"))
        if isinstance(st, dict):
            return {"used": dict(st.get("used") or {}), "fails": dict(st.get("fails") or {})}
    except (OSError, ValueError):
        pass
    return {"used": {}, "fails": {}}


def _write_state(root, st):
    p = pair_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(p), st)
    os.chmod(str(p), 0o600)


def check_pair(root, owner, code, requests, now, mark=True):
    """配對前的整段判斷 ⇒ 驗過的請求;不行 ⇒ OwnerError(原因碼)。打錯 nonce 才記一次,同一台第 3 次起作廢。
    mark=False ⇒ 先不記「已用」(本人證明過了再 mark_used;本人取消 Touch ID 不會把配對碼用掉)。"""
    import fcntl
    p = pair_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        st = _read_state(root)
        fp, _nonce = parse_code(code)
        if st["fails"].get(fp, 0) >= PAIR_MAX_FAILS:
            raise OwnerError("burned")
        rec, why = pick_request(owner, code, requests, now)
        if why == "bad_nonce":
            st["fails"][fp] = st["fails"].get(fp, 0) + 1
            _write_state(root, st)
            raise OwnerError("burned" if st["fails"][fp] >= PAIR_MAX_FAILS else "bad_code")
        if why != "ok":
            raise OwnerError(why)
        if rec["nonce_hash"] in st["used"]:
            raise OwnerError("used")
        if mark:
            _mark(root, st, rec["nonce_hash"], now)
        return rec
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _mark(root, st, nh, now):
    st["used"][nh] = int(now)
    st["used"] = {k: v for k, v in st["used"].items() if now - v < 30 * 86400}
    _write_state(root, st)


def mark_used(root, nh, now):
    """配對簽好之後才記 nonce 已用(同一個請求只收一次)。已記過 ⇒ used。"""
    import fcntl
    fd = os.open(str(pair_state_path(root)) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        st = _read_state(root)
        if nh in st["used"]:
            raise OwnerError("used")
        _mark(root, st, nh, now)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ── 本人證明(改清單、加密主人金鑰、off 撤主人名冊)──

def prove(reason, env, platform=None, touchid=None, touchid_available=None, attest=None, root=None):
    """Mac ⇒ 只准 Touch ID(AIB_TOUCHID=0 / 叫不出來一律拒,不退別的路;同 #144);非 Mac ⇒ 終端機三條件(human_attest)。
    回 None = 過;否則原因碼。之後呼叫端照樣要本人輸密語(真正的防線是密語)。
    touchid / touchid_available / attest / platform 只給測試換掉(不讀環境變數決定)。"""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        import touchid_approve as TA
        if str(env.get("AIB_TOUCHID", "")).strip() == "0":
            return "touchid_off"
        if not (touchid_available() if touchid_available else TA.available(platform=platform, environ=env)):
            return "touchid_unavailable"
        lock = Path(root or NK.secrets_dir(env)) / "locks" / "touchid-approve.lock"
        with TA.one_at_a_time(lock) as gate:
            res = (touchid or TA.ask)(TA.clean(reason, 120)) if gate.held else {"ok": False, "why": "busy"}
        return None if res.get("ok") is True else "touchid_" + str(res.get("why") or "failed").split(":")[0]
    import human_attest as HA
    res = (attest or (lambda: HA.attest(pid=os.getppid(), env=env)))()
    return None if res.get("human") else "not_human"


# ── 改清單(持主人金鑰那台簽;簽與發在 chat_nostr)──

def own_list_path(root):
    return Path(root) / "devices-own.json"


def read_own_list(root, owner):
    """這台上次簽出去的清單;沒有 ⇒ 一份 seq 0 的空清單(第一次簽成 seq 1)。"""
    try:
        rec = json.loads(own_list_path(root).read_text(encoding="utf-8"))
        if isinstance(rec, dict) and rec.get("owner") == owner and isinstance(rec.get("seq"), int):
            return rec
    except (OSError, ValueError):
        pass
    return {"v": DV.VERSION, "owner": owner, "devices": [], "revoked": [], "seq": 0}


def next_list(cur, now, add=None, revoke=None):
    """cur ⇒ 新清單(seq+1、at = now、valid_until = now + 30 天)。add = {npub, name};revoke = 機器碼或指紋或 npub。"""
    devices = [dict(d) for d in cur.get("devices") or []]
    revoked = [dict(r) for r in cur.get("revoked") or []]
    if add:
        if add["npub"] in {r["npub"] for r in revoked}:
            raise OwnerError("was_revoked")             # 停用過的那把不再收回(那台要重新 join 產新金鑰)
        if add["npub"] in {d["npub"] for d in devices}:
            raise OwnerError("already_listed")
        if len(devices) >= DV.MAX_DEVICES:
            raise OwnerError("too_many")
        if add.get("host") and any(d.get("host") for d in devices):
            raise OwnerError("already_listed")
        devices.append(dict({"npub": add["npub"], "name": add.get("name"), "added_at": int(now)},
                            **({"host": True} if add.get("host") else {})))
    if revoke:
        hit = [d for d in devices if revoke in (d["npub"], DV.device_code(d["npub"]), fingerprint(d["npub"]))]
        if not hit:
            raise OwnerError("not_listed")
        if any(d.get("host") for d in hit):
            raise OwnerError("is_host")                  # 持主人金鑰那台不能停用(它自己的名冊就不被認了)
        devices = [d for d in devices if d not in hit]
        revoked += [{"npub": d["npub"], "at": int(now)} for d in hit]
    return DV.build_list(cur["owner"], devices, revoked[-DV.MAX_REVOKED:], seq=int(cur.get("seq") or 0) + 1, at=now)


def verified_base(local, relay):
    """下一次簽清單的底(@abb89 10-18 H1):本機 devices-own.json 沒簽章、同帳號 AI 改得到 ⇒ 不能直接當底。
    relay = 中繼站抓回、已驗主人簽章的那份(沒有 ⇒ None)。回 (底, verified)。
    有 relay ⇒ 用它、seq = max(relay, 本機) + 1 由 next_list 加;沒有 relay 而本機也沒簽過(seq 0)⇒ 空清單,verified;
    沒有 relay 但本機說簽過 ⇒ 用本機、verified False(呼叫端要把整份清單列給本人看)。"""
    if relay:
        base = {k: relay[k] for k in ("v", "owner", "devices", "revoked", "seq") if k in relay}
        base["seq"] = max(int(relay.get("seq") or 0), int(local.get("seq") or 0))
        return base, True
    if not int(local.get("seq") or 0):
        return dict(local, devices=[], revoked=[]), True
    return local, False


def list_diff(prev, new):
    """兩份清單的差異 ⇒ {added:[(名稱, 指紋)], removed:[…], total}(給本人看:多了誰、少了誰)。"""
    a = {d["npub"]: d for d in prev.get("devices") or []}
    b = {d["npub"]: d for d in new.get("devices") or []}
    row = lambda d: (d.get("name") or "-", fingerprint(d["npub"]))   # noqa: E731
    return {"added": [row(b[n]) for n in b if n not in a], "removed": [row(a[n]) for n in a if n not in b],
            "total": len(b)}


def save_own_list(root, rec):
    write_json(str(own_list_path(root)), rec)
    os.chmod(str(own_list_path(root)), 0o600)


def extend_due(root, owner, now):
    """要不要提醒本人延長(上次簽超過 7 天);沒簽過清單 ⇒ False。"""
    rec = read_own_list(root, owner)
    return bool(rec.get("seq")) and now - int(rec.get("at") or 0) >= EXTEND_EVERY_S
