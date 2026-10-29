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
"""
import argparse
import calendar
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import unicodedata

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


class Ctx:
    """外部依賴一處注入(測試換假 helper / 時鐘 / 開機 / 機器)。"""

    def __init__(self, env=None, run=subprocess.run, helper=None, platform=None, now=None, boot=None, machine=None):
        self.env = dict(os.environ if env is None else env)
        self.run, self.helper, self.platform = run, helper, platform
        self.now = now or time.time
        self._boot, self._machine = boot, machine

    def boot(self):
        return self._boot() if callable(self._boot) else (self._boot if self._boot is not None
                                                          else boot_epoch(self.run, self.platform))

    def machine(self):
        return self._machine() if callable(self._machine) else (self._machine if self._machine is not None
                                                                else machine_id(self.run, self.platform))

    def helper_call(self, args, data=b""):
        return se_vault._call(args, self.env, data, self.run, self.helper, self.platform)


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


def window_reason(cats, dur, rekey=None, env=None):
    """指紋視窗那句話(approve_gate 登記的 site)。rekey = (舊 kid, 新 kid) ⇒ 第一行標「簽單鑰換過了」。"""
    loc = _locale(env)
    names = ("、" if loc in ("zh-TW", "ja") else ", ").join(AG.category_name(c, loc) for c in cats)
    dur_txt = _dur_name(dur, env)
    if rekey:
        return AG.reason("approve_grants.sign_rekey", {"cats": names, "dur": dur_txt, "old": rekey[0] or "-",
                                                         "new": rekey[1]}, env=env)
    return AG.reason("approve_grants.sign", {"cats": names, "dur": dur_txt}, env=env)


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


def sign(cats, dur, ctx, source=None):
    """本人按一次 ⇒ 存一張單。回 (單, why)。"""
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
    r = window_reason(cats, dur, rekey, ctx.env)
    text = AG.se_reason(r)
    if text is None:
        return None, "reason_refused:" + str(AG.refusal(r) or "too_long")
    now = int(ctx.now())
    doc = {"v": V, "kind": "window", "cats": cats, "dur": dur, "issued_utc": _utc(now),
           "expires_utc": _utc(now + DURATIONS[dur]), "boot": int(boot), "machine": machine, "kid": kid,
           "nonce": secrets.token_hex(16), "locale": _locale(ctx.env) or "", "rekey_from": rekey[0] if rekey else "",
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
    _, why = ctx.helper_call(["se-verify", "--name", KEY_NAME, "--dir", _key_dir(ctx.env), "--sig", sig],
                             canonical(doc))
    if why:
        return False, "bad_sig" if why == "bad_sig" else why
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
    if _text_sha(window_reason(cats, doc["dur"], rekey, env)) != doc.get("text_sha256"):
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


def _fmt_left(s):
    s = max(0, int(s))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def main(argv=None, out=None, ctx=None):
    """aib approve grants [--json] | --revoke <類別|all> | --extend <類別,類別> --for <時長> | --rotate-key"""
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib approve grants")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--revoke")
    g.add_argument("--extend")
    g.add_argument("--rotate-key", action="store_true")
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
    rows = [{"cats": d["cats"], "left_s": left, "expires_utc": d["expires_utc"], "nonce": d["nonce"]}
            for d, left in active(ctx)]
    if o.json:
        return emit(rows, 0)
    for r in rows:
        out.write("%s  %s  %s\n" % (_fmt_left(r["left_s"]), ",".join(r["cats"]), r["nonce"][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
