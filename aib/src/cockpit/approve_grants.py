#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""本人核准時間窗的授權單(TID-2b,@e339d 10-29;草稿 DRAFT-TID2-window-grants-e339d-1029,@aeb40 13:43 裁 Q1–Q3)。

按一次 Touch ID / 登入密碼 ⇒ Secure Enclave 的簽單鑰簽一張單:類別、時長、到期、這台機器、這次開機、隨機碼、
指紋視窗那句話的雜湊。之後要自動同意時(TID-2c 才接 ask)驗這張單;這張(2b)只有簽 / 驗 / 列 / 撤銷 / CLI,
approve_gate.ask() 完全不讀授權單(測試有一格證明)。

  - 簽單鑰:aib-sehelper grant-create(ACL userPresence = 指紋或登入密碼;換了指紋不失效 —— 能新增指紋的人本來就有
    登入密碼,不算降級,@aeb40 Q1)。公鑰一律由 se-pub 從 SE 鑰本體重算,驗簽走 se-verify;檔裡的 public_key 欄位不採信。
  - 鑰檔 GRANT_KEY.sekey 是本機檔:同帳號 AI 能換成一把不帶 ACL 的 SE 鑰(#419 R1 M1)⇒ se-pub / se-verify 每次先探
    (不准互動的試簽,只認「需要使用者互動」),簽得出來 = weak_key ⇒ 沒有有效單。門檻因此回到「改 / 換 helper 程式碼」
    (已定案的邊界:AI 改核心程式碼本來就擋不住)。
  - kid = 公鑰的 sha256 前 16 碼。第一次登記在 grant-kid.json;跟登記的不同 ⇒ 視窗第一行「簽單鑰換過了(舊 → 新)」
    並記事件。照實寫:換一把真的要本人的鑰 = 要騙到一次指紋才簽得出單;登記檔是本機檔,只供提示。
  - 視窗字由 approve_gate.reason() 依類別 + 時長組;單上的 text_sha256 = 那句話的雜湊,驗單時重組再比 ⇒ **走核心路徑簽的單**,
    視窗字 = 單上類別 / 時長。helper 也會擋「--reason 的雜湊 ≠ 單上 text_sha256」(直接叫 helper 自帶無害字 ⇒ 不簽);
    再往下(自己編 helper)跟上一條同邊界,不在這裡的保證內(#419 R1 L1)。字太長 ⇒ 直接拒、不截(呼叫端拆兩張)。
  - 高風險類別不准進單(簽、驗兩處都擋);時長只能是 DURATIONS 裡的,而且 ≤ 每個類別的 max_window(D5)。
  - 撤銷清單、事件、kid 登記都是本機檔 ⇒ 只防誤操作;真正收回 = 換簽單鑰(--rotate-key,按一次指紋)。
失敗 / 量不到 / 讀不懂 ⇒ 一律當沒有有效單(之後 TID-2c 退回一次一按),不放行。

TID-2c(#428,Andy 10-29 20:5x 裁 H1):ask() 的時間窗**先關**。本機驗單的結果信的是 helper binary(狀態根 bin/,
原始碼雜湊相同就沿用)與狀態根裡的單 / lockwatch.json,而 EZ2_AIB_STATE_ROOT 換得掉狀態根 ⇒ 不算防線。
這裡的簽 / 驗 / 選單 / lockwatch 都留著,等「系統錨定驗單」那題做完才由 approve_gate 接回。

系統錨定驗單第 1 張(10-30):helper 只認 anchored_helper.EXE(root 擁有的固定路徑、往上每層 root 擁有不可寫、
stamp = 原始碼雜湊),不吃 EZ2_AIB_STATE_ROOT / PATH、不退回 <狀態根>/bin;沒裝 / 舊版 ⇒ 驗單、簽單一律不過。
安裝本人跑:aib approve grants --helper status 印出的那一行(sudo,AI 不代跑)。

系統錨定驗單第 2 張(10-30):「簽單之後鎖過螢幕 ⇒ 單作廢」改看系統日誌(unified log)裡 loginwindow 的鎖屏 / 解鎖事件,
不再信 lockwatch.json(本機檔,AI 自己寫一份就算「沒鎖過」)。loginwindow 的路徑受 SIP 保護,同帳號行程寫不出
processImagePath = loginwindow 的紀錄;清不掉、改不了等級(log erase / log config 要 root)。查詢上限 3 秒
(Andy 10-29 21:1x Q2),超過 / 失敗 / 讀不懂 ⇒ 當鎖過。時間一律 UTC(#433 R1 H1:不吃呼叫端 TZ);另查 since 之前
要還有 loginwindow 紀錄(#433 R1 M1:灌 log 擠掉鎖屏段 ⇒ log_uncovered)。時間窗仍關著(approve_gate._default_window),
另一張才接回;接回前要先有 macOS build 白名單(#433 R1 M2,規格補記)。
"""
import argparse
import calendar
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import unicodedata

import anchored_helper
import approve_gate as AG
import se_vault

KEY_NAME = "GRANT_KEY"
DURATIONS = {"15m": 900, "30m": 1800, "1h": 3600, "3h": 3 * 3600, "24h": 24 * 3600}
_NONCE = re.compile(r"^[0-9a-f]{32}$")
V = 1


def _dir(env):
    return os.path.join(se_vault.se_dir(env), "grants")


def _key_dir(env):
    return se_vault.se_dir(env)


def canonical(doc):
    """簽的位元組:NFC、鍵排序、無空白、原字(不跳脫)。"""
    def norm(x):
        if isinstance(x, str):
            return unicodedata.normalize("NFC", x)
        if isinstance(x, list):
            return [norm(i) for i in x]
        if isinstance(x, dict):
            return {norm(k): norm(v) for k, v in x.items()}
        return x
    return json.dumps(norm(doc), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def kid_of(pub_b64):
    return hashlib.sha256((pub_b64 or "").encode("ascii", "replace")).hexdigest()[:16]


def boot_epoch(run=subprocess.run, platform=None):
    """這次開機的秒數(kern.boottime 的 sec;不用推算的值,免得抖 1 秒)。量不到 ⇒ None。"""
    platform = sys.platform if platform is None else platform
    try:
        if platform == "darwin":
            out = run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"sec\s*=\s*(\d+)", out or "")
            return int(m.group(1)) if m else None
        with open("/proc/stat", encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("btime "):
                    return int(ln.split()[1])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


def machine_id(run=subprocess.run, platform=None):
    """這台機器(IOPlatformUUID / machine-id)的雜湊前 16 碼。量不到 ⇒ None。"""
    platform = sys.platform if platform is None else platform
    raw = ""
    try:
        if platform == "darwin":
            out = run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                      timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', out or "")
            raw = m.group(1) if m else ""
        else:
            with open("/etc/machine-id", encoding="utf-8") as fh:
                raw = fh.read().strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16] if raw else None


def screen_locked_now(run=subprocess.run, platform=None):
    """ioreg 的 CGSSessionScreenIsLocked;不是 Mac / 讀不到 ⇒ True(量不到當鎖著)。"""
    if (sys.platform if platform is None else platform) != "darwin":
        return True
    try:
        import plistlib
        raw = run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, timeout=5).stdout
        users = plistlib.loads(raw).get("IOConsoleUsers") or []
    except Exception:  # noqa: BLE001
        return True
    return any(isinstance(u, dict) and u.get("CGSSessionScreenIsLocked") is True for u in users)


class Ctx:
    """外部依賴一處注入(測試換假 helper / 時鐘 / 開機 / 機器 / 系統日誌)。"""

    def __init__(self, env=None, run=subprocess.run, helper=None, platform=None, now=None, boot=None, machine=None,
                 screen_locked=None, anchored=None, syslog=None, os_build=None):
        self.env = dict(os.environ if env is None else env)
        self.run, self.helper, self.platform = run, helper, platform
        self.now = now or time.time
        self._boot, self._machine = boot, machine
        self._screen_locked = screen_locked
        self._anchored = anchored            # 只給測試換掉 anchored_helper.check
        self._syslog = syslog                # 只給測試換掉 syslog_locks(since, now) -> (事件秒數清單, None) | (None, why)
        self._os_build = os_build            # 只給測試換掉 os_build() -> build 字串 | None

    def os_build(self):
        if self._os_build is not None:
            return self._os_build() if callable(self._os_build) else self._os_build
        return os_build(self.run, self.platform)

    def syslog(self, since, now):
        if self._syslog is not None:
            return self._syslog(since, now)
        return syslog_locks(since, now, platform=self.platform)

    def screen_locked(self):
        """現在是不是鎖屏中;量不到 ⇒ True(當鎖著,不自動同意)。"""
        if self._screen_locked is not None:
            return self._screen_locked() if callable(self._screen_locked) else bool(self._screen_locked)
        return screen_locked_now(self.run, self.platform)

    def boot(self):
        return self._boot() if callable(self._boot) else (self._boot if self._boot is not None
                                                          else boot_epoch(self.run, self.platform))

    def machine(self):
        return self._machine() if callable(self._machine) else (self._machine if self._machine is not None
                                                                else machine_id(self.run, self.platform))

    def helper_exe(self):
        """(路徑, None) 或 (None, why)。正式路徑只認 root 擁有的固定路徑那支(系統錨定驗單第 1 張);
        不吃 EZ2_AIB_STATE_ROOT / PATH,也不退回 <狀態根>/bin 那支。helper= 只給測試注入。"""
        if self.helper:
            return self.helper, None
        exe, why = (self._anchored or anchored_helper.check)(platform=self.platform)
        return (exe, None) if exe else (None, "helper_" + str(why))

    def helper_call(self, args, data=b""):
        exe, why = self.helper_exe()
        if not exe:
            return None, why
        return se_vault._call(args, self.env, data, self.run, exe, self.platform)


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(t)))


def _parse_utc(s):
    try:
        return calendar.timegm(time.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return None


def check_scope(cats, dur):
    """回 refusal 代碼或 None。類別要都登記過、不是高風險、不重複;時長要在 DURATIONS、≤ 每類 max_window。"""
    if not isinstance(cats, (list, tuple)) or not cats or len(set(cats)) != len(cats):
        return "bad_cats"
    if dur not in DURATIONS:
        return "bad_duration"
    for c in cats:
        spec = AG.CATEGORIES.get(c)
        if spec is None:
            return "unknown_category"
        if spec["high_risk"]:
            return "high_risk"
        if DURATIONS[dur] > spec["max_window"]:
            return "over_max_window"
    return None


def window_reason(cats, dur, rekey=None, env=None, action=""):
    """指紋視窗那句話(approve_gate 登記的 site)。rekey = (舊 kid, 新 kid) ⇒ 第一行標「簽單鑰換過了」。
    action = 這次要核准的那句話(彈窗選了時長 ⇒ 同一次指紋核准這件 + 簽單;TID-2c),放在句首。"""
    loc = _locale(env)
    names = ("、" if loc in ("zh-TW", "ja") else ", ").join(AG.category_name(c, loc) for c in cats)
    facts = {"cats": names, "dur": _dur_name(dur, env)}
    if action:
        facts["action"] = action
    if rekey:
        facts.update(old=rekey[0] or "-", new=rekey[1])
    site = {(False, False): "approve_grants.sign", (True, False): "approve_grants.sign_with",
            (False, True): "approve_grants.sign_rekey", (True, True): "approve_grants.sign_with_rekey"}[(bool(action), bool(rekey))]
    return AG.reason(site, facts, env=env)


def _locale(env):
    try:
        import i18n_lib
        return i18n_lib.resolve_locale(env=env)
    except Exception:  # noqa: BLE001
        return None


def _dur_name(dur, env):
    try:
        import i18n_lib
        t = i18n_lib.t("aib.approve_grants.dur." + dur, None, locale=_locale(env))
    except Exception:  # noqa: BLE001
        t = ""
    return t if isinstance(t, str) and t and not t.startswith("!!") else dur


def _text_sha(r):
    return hashlib.sha256(str(r).encode("utf-8")).hexdigest()


def current_kid(ctx):
    """從 SE 鑰本體重算的公鑰 ⇒ kid;沒鑰 / 量不到 ⇒ (None, why)。"""
    out, why = ctx.helper_call(["se-pub", "--name", KEY_NAME, "--dir", _key_dir(ctx.env)])
    if why:
        return None, why
    try:
        res = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeError):
        return None, "helper_failed"
    if isinstance(res, dict) and res.get("ok") is True and res.get("owner_acl") != "probed":
        return None, "old_helper"            # 沒探 ACL 的舊 helper ⇒ 不認(#419 R2-L1)
    pub = res.get("public_key") if isinstance(res, dict) and res.get("ok") is True else None
    return (kid_of(pub), None) if isinstance(pub, str) and pub else (None, "helper_failed")


def _kid_path(env):
    return os.path.join(_key_dir(env), "grant-kid.json")


def registered_kid(env):
    try:
        with open(_kid_path(env), encoding="utf-8") as f:
            k = json.load(f).get("kid")
        return k if isinstance(k, str) and re.fullmatch(r"[0-9a-f]{16}", k) else None
    except (OSError, ValueError, AttributeError):
        return None


def _private_dir(d):
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)                   # 已存在的目錄也修(makedirs 不動既有目錄的權限)


def _write_json(path, obj):
    _private_dir(os.path.dirname(path))
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


def _append(path, line):
    _private_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _event(env, row):
    _append(os.path.join(_dir(env), "events.jsonl"),
            json.dumps(dict(row, at=_utc(time.time())), ensure_ascii=False, sort_keys=True))


def ensure_key(ctx):
    """沒有簽單鑰就建(建鑰不要指紋;簽才要)。回 (kid, why)。"""
    kid, why = current_kid(ctx)
    if kid:
        return kid, None
    if why != "not_found":
        return None, why
    _, why = ctx.helper_call(["grant-create", "--name", KEY_NAME, "--dir", _key_dir(ctx.env)])
    return (None, why) if why else current_kid(ctx)


def sign(cats, dur, ctx, source=None, action=""):
    """本人按一次 ⇒ 存一張單。回 (單, why)。action = 同一次指紋要核准的那件事(句子放在視窗句首)。"""
    cats = sorted(cats or ())            # 視窗字跟單上同一個順序(驗單時照單上順序重組)
    no = check_scope(cats, dur)
    if no:
        return None, no
    boot, machine = ctx.boot(), ctx.machine()
    if not boot or not machine:
        return None, "unmeasured"
    kid, why = ensure_key(ctx)
    if not kid:
        return None, why
    old = registered_kid(ctx.env)
    rekey = (old, kid) if old != kid and old is not None else None
    r = window_reason(cats, dur, rekey, ctx.env, action=str(action or ""))
    text = AG.se_reason(r)
    if text is None:
        return None, "reason_refused:" + str(AG.refusal(r) or "too_long")
    now = int(ctx.now())
    doc = {"v": V, "kind": "window", "cats": cats, "dur": dur, "issued_utc": _utc(now),
           "expires_utc": _utc(now + DURATIONS[dur]), "boot": int(boot), "machine": machine, "kid": kid,
           "nonce": secrets.token_hex(16), "locale": _locale(ctx.env) or "", "rekey_from": rekey[0] if rekey else "",
           "action": str(action or ""),
           "text_sha256": _text_sha(r), "source": str(source or "")[:64]}
    out, why = ctx.helper_call(["se-sign", "--name", KEY_NAME, "--dir", _key_dir(ctx.env), "--reason", text],
                               canonical(doc))
    if why:
        return None, why
    sig = out.decode("ascii", "replace").strip()
    if not re.fullmatch(r"[A-Za-z0-9+/=]{16,256}", sig):
        return None, "helper_failed"
    _write_json(os.path.join(_dir(ctx.env), doc["nonce"] + ".json"), {"doc": doc, "sig": sig})
    if old != kid:
        _write_json(_kid_path(ctx.env), {"kid": kid, "at": _utc(now)})
        if rekey:
            _event(ctx.env, {"event": "rekey", "old": old, "new": kid})
    _event(ctx.env, {"event": "sign", "nonce": doc["nonce"], "cats": doc["cats"], "dur": dur})
    return doc, None


def revoked(env):
    out = set()
    try:
        with open(os.path.join(_dir(env), "revoked.jsonl"), "rb") as f:
            for raw in f:
                try:
                    n = json.loads(raw).get("nonce")
                except (ValueError, AttributeError):
                    continue
                if isinstance(n, str) and _NONCE.match(n):
                    out.add(n)
    except OSError:
        pass
    return out


def verify(grant, cat, ctx, kid=None, revoked_set=None):
    """這張單現在能不能讓類別 cat 自動同意。回 (True, None) 或 (False, why)。任何一項量不到 / 對不上 ⇒ False。"""
    if not isinstance(grant, dict) or not isinstance(grant.get("doc"), dict) or not isinstance(grant.get("sig"), str):
        return False, "corrupt"
    doc, sig = grant["doc"], grant["sig"]
    if doc.get("v") != V or doc.get("kind") != "window" or not _NONCE.match(str(doc.get("nonce"))):
        return False, "corrupt"
    out, why = ctx.helper_call(["se-verify", "--name", KEY_NAME, "--dir", _key_dir(ctx.env), "--sig", sig],
                               canonical(doc))
    if why:
        return False, "bad_sig" if why == "bad_sig" else why
    try:
        if json.loads(out.decode("utf-8")).get("owner_acl") != "probed":
            return False, "old_helper"
    except (ValueError, UnicodeError, AttributeError):
        return False, "helper_failed"
    if kid is None:
        kid, why = current_kid(ctx)
        if not kid:
            return False, why
    if doc.get("kid") != kid:
        return False, "kid_mismatch"
    cats = doc.get("cats")
    if check_scope(cats, doc.get("dur")):
        return False, "scope:" + check_scope(cats, doc.get("dur"))
    if cat not in cats:
        return False, "category"
    env = dict(ctx.env, EZ2_AIB_LOCALE=doc.get("locale") or "") if doc.get("locale") else ctx.env
    rf = doc.get("rekey_from")
    if not isinstance(rf, str):
        return False, "corrupt"
    rekey = (rf, doc["kid"]) if rf else None          # 換鑰後第一張:視窗第一行有「簽單鑰換過了」,重組也要同一句
    action = doc.get("action", "")
    if not isinstance(action, str):
        return False, "corrupt"
    if _text_sha(window_reason(cats, doc["dur"], rekey, env, action=action)) != doc.get("text_sha256"):
        return False, "text_mismatch"
    issued, expires, now = _parse_utc(doc.get("issued_utc")), _parse_utc(doc.get("expires_utc")), int(ctx.now())
    if issued is None or expires is None or expires - issued != DURATIONS[doc["dur"]]:
        return False, "bad_time"
    if not issued <= now < expires:
        return False, "expired" if now >= expires else "not_yet"
    boot, machine = ctx.boot(), ctx.machine()
    if not boot or doc.get("boot") != int(boot):
        return False, "boot"
    if not machine or doc.get("machine") != machine:
        return False, "machine"
    if doc["nonce"] in (revoked(ctx.env) if revoked_set is None else revoked_set):
        return False, "revoked"
    return True, None


def load_all(env):
    out = []
    try:
        names = sorted(os.listdir(_dir(env)))
    except OSError:
        return out
    for n in names:
        if not (n.endswith(".json") and _NONCE.match(n[:-5])):
            continue
        try:
            with open(os.path.join(_dir(env), n), encoding="utf-8") as f:
                g = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(g, dict):
            out.append(g)
    return out


def active(ctx):
    """有效的單:[(單 doc, 剩餘秒)]。每張每個類別都驗(只要有一類過就列)。"""
    kid, _ = current_kid(ctx)
    if not kid:
        return []
    rv = revoked(ctx.env)
    out = []
    for g in load_all(ctx.env):
        doc = g.get("doc") if isinstance(g.get("doc"), dict) else {}
        cats = doc.get("cats") if isinstance(doc.get("cats"), list) else []
        if any(verify(g, c, ctx, kid=kid, revoked_set=rv)[0] for c in cats):
            out.append((doc, _parse_utc(doc["expires_utc"]) - int(ctx.now())))
    return out


def revoke(target, ctx):
    """撤銷(不用指紋;只防誤操作)。target = 類別或 all。回撤銷的張數。"""
    n = 0
    path = os.path.join(_dir(ctx.env), "revoked.jsonl")
    for doc, _left in active(ctx):
        if target == "all" or target in doc["cats"]:
            _append(path, json.dumps({"nonce": doc["nonce"], "at": _utc(ctx.now())}))
            n += 1
    if n:
        _event(ctx.env, {"event": "revoke", "target": target, "count": n})
    return n


def rotate_key(ctx):
    """換簽單鑰 = 舊單全部失效(真正的收回)。舊鑰檔改名留著,不刪。新鑰第一次簽單時本人按指紋。"""
    old = os.path.join(_key_dir(ctx.env), KEY_NAME + ".sekey")
    if os.path.isfile(old):
        os.replace(old, old + ".retired-%d" % int(ctx.now()))
    kid, why = ensure_key(ctx)
    if kid:
        _event(ctx.env, {"event": "rotate", "new": kid})
    return kid, why



# ───────────── TID-2c:鎖屏作廢 + 自動同意(系統錨定驗單第 2 張:看系統日誌)─────────────
BEAT_MAX_S = 90                  # (舊)lockwatch 心跳上限;鎖屏判斷已改看系統日誌,lockwatch 只剩 --lockwatch on / off
LOCKWATCH_LABEL = "com.ez2.aib-lockwatch"
LOG = "/usr/bin/log"
LOGINWINDOW = "/System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow"
# 10-30 實機:鎖屏時 loginwindow 記 -[LWShieldWindowController _createShieldWindowIfNeeded],解鎖時一串 -[LWScreenLock …];
# 平常(沒鎖)這兩類一筆都沒有(24 小時內只在真的鎖 / 解鎖那兩個時段出現)。多算 = 多按一次,少算才危險 ⇒ 兩類都算。
LOCK_PREDICATE = ('processImagePath == "%s" AND (eventMessage BEGINSWITH "-[LWScreenLock " OR '
                  'eventMessage BEGINSWITH "-[LWShieldWindowController _createShieldWindowIfNeeded]")' % LOGINWINDOW)
LOG_BUDGET_S = 3.0               # Andy 10-29 21:1x Q2:查詢上限 3 秒,超過 ⇒ 當鎖過
LOG_SPAN_S = 3 * 3600            # 一支 log show 查 3 小時(實測 24 小時一支 ~5 秒、切 8 段並行 ~2 秒)
LOG_SPANS_MAX = 8                # 24 小時(最長時長)= 8 段;再長 ⇒ log_span(不查,當鎖過)
# #433 R1 M1 覆蓋檢查:since 之前 30 分鐘內要查得到至少一筆 loginwindow 紀錄(10-30 實機:30 小時內每小時 ≥ 31 筆,
# 存在比 Persist 久的存檔)。日誌由舊到新整檔輪替 ⇒ since 之前的還在 = since 之後的都還在;時間戳由 logd 打、
# loginwindow 路徑受 SIP 保護 ⇒ AI 灌 log 擠掉鎖屏段之後補不回「since 之前的 loginwindow 紀錄」⇒ log_uncovered。
# #433 R2 L1:覆蓋只算 Default 等級的 logEvent(跟鎖屏事件同行程、同等級 ⇒ 同一個保存區;Error / Fault /
# activity 存法不同,不能拿來證明鎖屏那段還在)。predicate 先篩等級,Python 端再驗一次(activity 事件沒有 messageType)。
COVER_PREDICATE = 'processImagePath == "%s" AND messageType == default' % LOGINWINDOW
COVER_S = 1800
# #433 R1 M2:鎖屏判斷靠 loginwindow 的訊息字串,macOS 一改字就會靜靜查無(= 當沒鎖過)⇒ 只認實機驗過的 build。
# 名單跟著簽章發版走(改它 = 改核心程式碼,已定案邊界);不在名單 ⇒ os_unverified(窗不生效,一次一按)。
# 驗一個 build:本人鎖屏 / 解鎖一次,唯讀 log show 確認 LOCK_PREDICATE 兩類事件都查得到,再把 build 加進來。
VERIFIED_BUILDS = frozenset({
    "24G830",                    # 10-30 Mac Pro 實機:鎖屏 _createShieldWindowIfNeeded、解鎖 LWScreenLock 都查得到
})
SW_VERS = "/usr/bin/sw_vers"
_BUILD = re.compile(r"^[0-9A-Za-z]{3,16}$")


def os_build(run=subprocess.run, platform=None):
    """macOS build(sw_vers -buildVersion,SIP 保護的絕對路徑、env 只給 PATH)。不是 Mac / 讀不到 / 格式不對 ⇒ None。"""
    if (platform or sys.platform) != "darwin":
        return None
    try:
        p = run([SW_VERS, "-buildVersion"], capture_output=True, text=True, timeout=5,
                env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    b = (p.stdout or "").strip() if p.returncode == 0 else ""
    return b if _BUILD.match(b) else None


def lockwatch_path(env):
    return os.path.join(_key_dir(env), "lockwatch.json")


def _log_utc(t):
    """一律 UTC 帶 +0000(#433 R1 H1:本地字串照呼叫端 TZ 算、log 照系統時區解讀 ⇒ TZ=AAA-24 起點晚 16 小時漏查)。"""
    return time.strftime("%Y-%m-%d %H:%M:%S+0000", time.gmtime(int(t)))


def _log_ts(s):
    """ndjson 的 timestamp(例 2026-10-29 12:48:44.123456+0800)⇒ 秒;讀不懂 ⇒ None。"""
    try:
        return int(datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S.%f%z").timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def syslog_locks(since, now, popen=subprocess.Popen, platform=None, clock=time.monotonic, budget=LOG_BUDGET_S):
    """[since, now] 之間 loginwindow 的鎖屏 / 解鎖事件(秒數清單)。回 (清單, None) 或 (None, why);
    why ∈ log_not_mac / log_clock / log_span / log_failed / log_timeout / log_unreadable / log_uncovered —— 呼叫端一律當鎖過。
    切成 ≤ 8 段 + 1 支覆蓋檢查並行查,全部要在 budget 秒內回來;/usr/bin/log 絕對路徑(SIP 保護)、env 只給 PATH、時間一律 UTC。"""
    if (platform or sys.platform) != "darwin":
        return None, "log_not_mac"
    since, now = int(since), int(now)
    if now < since:
        return None, "log_clock"
    n = max(1, -(-(now - since) // LOG_SPAN_S))
    if n > LOG_SPANS_MAX:
        return None, "log_span"
    edges = [since + (now - since) * i // n for i in range(n + 1)]
    deadline = clock() + budget
    procs, events, covered = [], [], False
    spans = [(a, b + 1, LOCK_PREDICATE) for a, b in zip(edges, edges[1:])]     # 每段多查 1 秒(邊界重疊,不漏)
    spans.append((since - COVER_S, since, COVER_PREDICATE))                    # 最後一支 = 覆蓋檢查
    try:
        for a, b, pred in spans:
            procs.append(popen([LOG, "show", "--start", _log_utc(a), "--end", _log_utc(b), "--style", "ndjson",
                                "--no-info", "--no-debug", "--no-signpost", "--predicate", pred],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               env={"PATH": "/usr/bin:/bin"}))
        for k, p in enumerate(procs):
            cover = k == len(procs) - 1
            left = deadline - clock()
            if left <= 0:
                return None, "log_timeout"
            out, _ = p.communicate(timeout=left)
            if p.returncode != 0:
                return None, "log_failed"
            for line in (out or b"").splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue                          # 開頭的 Filtering… / 結尾摘要不是事件
                if not isinstance(d, dict) or "eventMessage" not in d:
                    continue
                if d.get("processImagePath") != LOGINWINDOW:
                    continue
                ts = _log_ts(d.get("timestamp"))
                if ts is None:
                    return None, "log_unreadable"
                if not cover:
                    events.append(ts)
                elif ts <= since and d.get("messageType") == "Default" and d.get("eventType") == "logEvent":
                    covered = True
        if not covered:
            return None, "log_uncovered"
        return events, None
    except subprocess.TimeoutExpired:
        return None, "log_timeout"
    except (OSError, subprocess.SubprocessError):
        return None, "log_failed"
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(timeout=2)
                except subprocess.SubprocessError:
                    pass


def lock_state(ctx, since):
    """回 (since 之後最後一次鎖屏 / 解鎖事件的秒數,沒有 = 0, None) 或 (None, why)。
    現在鎖著 / 量不到 / 這版 macOS 沒驗過 / 系統日誌查不到 / 超過 3 秒 ⇒ why(當鎖過、窗作廢)。
    不讀 lockwatch.json(本機檔,AI 寫得出來)。"""
    if ctx.screen_locked():
        return None, "screen_locked"
    if ctx.os_build() not in VERIFIED_BUILDS:
        return None, "os_unverified"
    events, why = ctx.syslog(int(since), int(ctx.now()))
    if why:
        return None, why
    return (max(events) if events else 0), None


def _probe(ctx):
    """doctor / --lockwatch status 用:同一套查法,只是不看「現在鎖著沒」(問的是查不查得到)。"""
    return Ctx(env=ctx.env, run=ctx.run, now=ctx.now, platform=ctx.platform, screen_locked=False,
               syslog=ctx._syslog, os_build=ctx._os_build)


def find_valid(cat, ctx):
    """類別 cat 現在有沒有有效單。回 (單 doc, None) 或 (None, why)。高風險 / 沒窗的類別一律 None(程式這一處)。"""
    spec = AG.CATEGORIES.get(cat)
    if not spec or spec["high_risk"] or spec["max_window"] <= 0:
        return None, "no_window_category"
    kid, why = current_kid(ctx)
    if not kid:
        return None, why
    rv = revoked(ctx.env)
    why, ok = "no_grant", []
    for g in load_all(ctx.env):
        good, w = verify(g, cat, ctx, kid=kid, revoked_set=rv)
        if not good:
            why = w if why == "no_grant" and w not in ("category",) else why
            continue
        issued = _parse_utc(g["doc"]["issued_utc"])
        if issued is not None:
            ok.append((issued, g["doc"]))
    if not ok:
        return None, why
    issued, doc = max(ok, key=lambda x: x[0])
    # 只查最新那張簽發之後:它之後鎖過 ⇒ 更早的單也都在鎖之前簽 ⇒ 全部作廢;沒鎖過 ⇒ 用它。時段最短、查得最快。
    last_lock, why = lock_state(ctx, issued)
    if why:
        return None, why
    if issued > last_lock:
        return doc, None
    return None, "locked_since"          # 鎖過螢幕之後,鎖之前簽的單作廢(D3)


def record_use(ctx, doc, site, cat):
    """每次自動同意記一列(艦長室查帳頁之後讀;本機檔 = 只供查帳,不是防線)。"""
    _append(os.path.join(_dir(ctx.env), "grant-uses.jsonl"),
            json.dumps({"at": _utc(ctx.now()), "nonce": doc["nonce"], "site": str(site or "")[:64], "cat": cat},
                       ensure_ascii=False, sort_keys=True))


def lockwatch_plist(ctx, exe):
    return {"Label": LOCKWATCH_LABEL, "ProgramArguments": [exe, "lockwatch", "--name", KEY_NAME, "--dir", _key_dir(ctx.env)],
            "KeepAlive": True, "RunAtLoad": True, "ProcessType": "Background",
            "EnvironmentVariables": {"HOME": ctx.env.get("HOME") or os.path.expanduser("~")}}


def lockwatch_plist_path(home):
    return os.path.join(home, "Library", "LaunchAgents", LOCKWATCH_LABEL + ".plist")


def install_lockwatch(ctx, launchctl, home):
    """把 lockwatch 裝成 LaunchAgent(跟 auto_update 同一套 bootstrap 寫法)。只由明確的指令叫
    (aib approve grants --lockwatch on;launchctl / home 一律由呼叫端給,測試給假的)——簽單、彈窗都不會順手裝。
    沒裝 ⇒ lock_state 一律 no_lockwatch(不自動同意,回一次一按)。回 (installed|mac_only|failed, 說明)。"""
    if (ctx.platform or sys.platform) != "darwin":
        return "mac_only", ""
    exe, why = ctx.helper_exe()
    if not exe:
        return "failed", why
    import plistlib
    path = lockwatch_plist_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        plistlib.dump(lockwatch_plist(ctx, exe), fh)
    os.replace(path + ".tmp", path)
    domain = "gui/%d" % os.getuid()
    launchctl("bootout", "%s/%s" % (domain, LOCKWATCH_LABEL))
    rc, err = launchctl("bootstrap", domain, path)
    return ("installed", path) if rc == 0 else ("failed", err)


def remove_lockwatch(ctx, launchctl, home):
    """拿掉 lockwatch(之後時間窗一律不生效)。回是否刪了 plist。"""
    if (ctx.platform or sys.platform) == "darwin":
        launchctl("bootout", "gui/%d/%s" % (os.getuid(), LOCKWATCH_LABEL))
    path = lockwatch_plist_path(home)
    if os.path.isfile(path):
        os.unlink(path)
        return True
    return False


def _real_launchctl(*args):
    try:
        p = subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stderr or p.stdout).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def doctor_line(ctx=None):
    """(ok|note, 代碼)。aib doctor:有單但 helper 沒好 / 系統日誌查不到 ⇒ note(窗用不到,一律一次一按)。唯讀。"""
    ctx = ctx or Ctx()
    if not load_all(ctx.env):
        return "ok", "no_grants"
    _, why = ctx.helper_exe()
    if why:                                  # 沒裝 / 舊版 / 擁有者不對 ⇒ 驗單一律不過(Q1:update 改了 helper ⇒ 窗自動關)
        return "note", why
    _, why = lock_state(_probe(ctx),
                        int(ctx.now()) - 60)
    return ("ok", "syslog_ok") if why is None else ("note", why)


def _t(key, params=None, env=None):
    try:
        import i18n_lib
        t = i18n_lib.t(key, params or None, locale=_locale(env))
    except Exception:  # noqa: BLE001
        t = ""
    return t if isinstance(t, str) and t and not t.startswith("!!") else ""


CHOOSE_JXA = """
var app = Application.currentApplication(); app.includeStandardAdditions = true;
var items = __ITEMS__;
var r = app.chooseFromList(items, {withTitle: "aib", withPrompt: __PROMPT__, defaultItems: [items[0]],
                                   okButtonName: __OK__, cancelButtonName: __CANCEL__});
JSON.stringify(r === false ? null : r[0]);
"""


def durations_for(cat):
    spec = AG.CATEGORIES.get(cat)
    if not spec or spec["high_risk"] or spec["max_window"] <= 0:
        return []
    return [d for d, sec in sorted(DURATIONS.items(), key=lambda kv: kv[1]) if sec <= spec["max_window"]]


def choose_prompt(r, ctx):
    """選單上的字:這次要核准的話 + D2 照實寫 + 目前有效的單(類別 · 剩餘)。全由核心組、清過。"""
    loc_env = ctx.env
    rows = ["%s %s" % (",".join(AG.category_name(c, _locale(loc_env)) for c in d["cats"]), _fmt_left(left))
            for d, left in active(ctx)]
    return AG.clean_full(str(r)) + "\n\n" + _t("aib.approve_grants.choose.note",
                                                  {"cat": AG.category_name(r.category, _locale(loc_env))}, loc_env) + \
        "\n" + _t("aib.approve_grants.choose.active", {"list": "; ".join(rows) if rows else
                                                       _t("aib.approve_grants.choose.none", None, loc_env)}, loc_env)


def choose_window(r, ctx, run=subprocess.run):
    """彈時長選單(只這次 / 時長)。回 "once" / 時長代碼 / "cancel"。讀不懂 / 失敗 ⇒ "once"(退回一次一按)。
    選單是 AI 點得到的(System Events)⇒ 不是防線:真正的時長寫在之後的指紋視窗字裡,本人在那裡看得到。"""
    durs = durations_for(r.category)
    if not durs or (ctx.platform or sys.platform) != "darwin":
        return "once"
    labels = [_t("aib.approve_grants.choose.once", None, ctx.env) or "once"] + [_dur_name(d, ctx.env) for d in durs]
    script = (CHOOSE_JXA.replace("__ITEMS__", json.dumps(labels))
              .replace("__PROMPT__", json.dumps(choose_prompt(r, ctx)))
              .replace("__OK__", json.dumps(_t("aib.approve_grants.choose.ok", None, ctx.env) or "OK"))
              .replace("__CANCEL__", json.dumps(_t("aib.approve_grants.choose.cancel", None, ctx.env) or "Cancel")))
    try:
        p = run(["/usr/bin/osascript", "-l", "JavaScript", "-"], input=script, capture_output=True, text=True, timeout=75)
        got = json.loads((p.stdout or "").strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else "once"
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return "once"
    if got is None:
        return "cancel"
    return dict(zip(labels, ["once"] + durs)).get(got, "once")


class GateWindow:
    """approve_gate.ask() 用的時間窗(TID-2c)。每個方法失敗都由 ask 收成「一次一按」。
    正式路徑目前不建這個(approve_gate._default_window 回 None,#428 H1);等系統錨定驗單做完才接回。"""

    def __init__(self, ctx=None, run=subprocess.run):
        self.ctx = ctx or Ctx()
        self.run = run

    def find(self, cat):
        return find_valid(cat, self.ctx)

    def use(self, doc, site, cat):
        record_use(self.ctx, doc, site, cat)

    def choose(self, r):
        # helper 不在系統位置 / 系統日誌查不到 ⇒ 開了窗也不會生效 ⇒ 不出時長選單,直接只這次
        if self.ctx.helper_exe()[1] is not None or lock_state(self.ctx, int(self.ctx.now()) - 60)[1] is not None:
            return "once"
        return choose_window(r, self.ctx, self.run)

    def sign(self, r, dur):
        return sign([r.category], dur, self.ctx, source=r.site, action=str(r))

def _fmt_left(s):
    s = max(0, int(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def approve_lockwatch_on(ctx, touchid=None):
    """裝 lockwatch 會讓時間窗生效 ⇒ 本人按一次(類別 window_grant = 高風險,一次一按;@aeb40 16:36)。
    回 (True, None) 或 (False, why)。不是 Mac / 關掉 / 叫不出 ⇒ 拒。touchid 只給測試注入。"""
    if touchid is None:
        no = AG.ready(ctx.env, platform=ctx.platform)
        if no:
            return False, no
    r = AG.reason("approve_grants.lockwatch_on", {}, env=ctx.env)
    res = AG.ask(r, AG.state_root_default(ctx.env), touchid=touchid)
    return (True, None) if res.get("ok") is True else (False, str(res.get("why") or "failed"))


def main(argv=None, out=None, ctx=None, launchctl=None, home=None, touchid=None):
    """aib approve grants [--json] | --revoke <類別|all> | --extend <類別,類別> --for <時長> | --rotate-key"""
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib approve grants")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--revoke")
    g.add_argument("--extend")
    g.add_argument("--rotate-key", action="store_true")
    g.add_argument("--lockwatch", choices=("on", "off", "status"))
    g.add_argument("--helper", choices=("status",))
    ap.add_argument("--for", dest="dur", choices=sorted(DURATIONS))
    ap.add_argument("--json", action="store_true")
    try:
        o = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    ctx = ctx or Ctx()

    def emit(d, rc):
        out.write(json.dumps(d, ensure_ascii=False) + "\n")
        return rc

    if o.helper:                             # 唯讀:固定路徑 helper 裝了沒;沒裝 ⇒ 給本人在終端跑的那一行(AI 不代跑)
        exe, why = ctx.helper_exe()
        return emit({"ok": exe is not None, "path": anchored_helper.EXE, "why": why,
                     "install": None if exe else anchored_helper.install_command()}, 0)
    if o.lockwatch:
        lc, hm = launchctl or _real_launchctl, home or os.path.expanduser("~")
        if o.lockwatch == "on":
            ok, why = approve_lockwatch_on(ctx, touchid)
            if not ok:
                return emit({"ok": False, "why": why}, 2)
            kind, info = install_lockwatch(ctx, lc, hm)
            return emit({"ok": kind == "installed", "result": kind, "info": info}, 0 if kind == "installed" else 2)
        if o.lockwatch == "off":
            return emit({"ok": True, "removed": remove_lockwatch(ctx, lc, hm)}, 0)
        _, why = lock_state(_probe(ctx),
                            int(ctx.now()) - 60)            # 鎖屏判斷看系統日誌(第 2 張);這裡回它查不查得到
        return emit({"ok": why is None, "why": why, "source": "syslog"}, 0)
    if o.revoke:
        return emit({"ok": True, "revoked": revoke(o.revoke, ctx)}, 0)
    if o.rotate_key:
        kid, why = rotate_key(ctx)
        return emit({"ok": bool(kid), "kid": kid, "why": why}, 0 if kid else 2)
    if o.extend:
        if not o.dur:
            return emit({"ok": False, "why": "need_for"}, 2)
        doc, why = sign([c.strip() for c in o.extend.split(",") if c.strip()], o.dur, ctx, source="cli")
        return emit({"ok": True, "nonce": doc["nonce"], "expires_utc": doc["expires_utc"]} if doc
                    else {"ok": False, "why": why}, 0 if doc else 2)
    auto = "off" if AG._default_window() is None else "on"     # #428 R2 L1:正式路徑時間窗關著 ⇒ 單在也不會自動同意
    rows = [{"cats": d["cats"], "left_s": left, "expires_utc": d["expires_utc"], "nonce": d["nonce"], "auto_approve": auto}
            for d, left in active(ctx)]
    if o.json:
        return emit(rows, 0)
    for r in rows:
        out.write("%s  %s  %s  auto_approve:%s\n" % (_fmt_left(r["left_s"]), ",".join(r["cats"]), r["nonce"][:8], r["auto_approve"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
