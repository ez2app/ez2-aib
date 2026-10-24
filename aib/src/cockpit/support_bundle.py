#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib support:打包一份問題回報(10-24 Andy 准精簡版;契約 contracts/power.json support_receipt;動力室 UR4)。

  aib support [--json]           先看:列出會收哪幾段、每段幾筆,一個檔都不寫
  aib support --write [--json]   寫:<狀態根>/support/aib-support-<時間>.json(0600),只在本機;寄不寄由本人決定

收(全是機器狀態):aib / macOS / Python 版本、已裝外掛名稱版本、aib doctor 裡只讀又只有機器資料的幾項
(DOCTOR_PURE:Python / git / 狀態根權限 / schema / 各家 AI 工具在不在)、各家 AI 上次量到有沒有登入、
艙數 / 每艙席數 / 角色、最近 20 筆不成功的代碼(dm 寄送結果、面板動作)與時間。
10-24 雙審第 1 輪:整套 doctor 不借 —— 它會建探針檔、可能重產角色卡清單、連 ez2.page 寫快取,
說明還會帶交接檔名 / 席位檔欄位 / 客戶主機名 IP / 艙短號 / 佇列名。
不收:dm / 對話內容、Vault、專案名、人設、金鑰 / token / 序號 / 授權檔、log 原文、艙號 / 席號。
寫之前:家目錄換 ~、使用者名稱換 <user>、暫存路徑換 <tmp>、艙號換 <cabin>、席號(5 碼 16 進位,寧可多遮)換 <seat>、
像金鑰的字串換 [redacted];字典的鍵也照遮
(同帳號 AI 威脅守則:這只是減少誤帶,不是防線;包本來就只放機器狀態)。
要收 log 原文另開 --with-logs,要先問 Andy(還沒做)。
"""
import argparse
import getpass
import glob
import json
import math
import os
import platform
import re
import secrets
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

SECTIONS = ("versions", "plugins", "doctor", "logins", "cabins", "failures")
FAIL_MAX = 20
TAIL_BYTES = 256 * 1024
DM_OK = frozenset(("queued", "delivered", "delivered_direct", "read", "done"))
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|sk_(?:live|test)_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|nsec1[0-9a-z]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|glpat-[0-9A-Za-z_-]{16,}"
    r"|hf_[A-Za-z0-9]{20,}|bearer\s+\S{16,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{48,}={0,2})", re.I)
TMP = re.compile(r"/(?:private/)?var/folders/[^\s\"']+|/(?:private/)?tmp/[^\s\"']+", re.I)
CABIN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}", re.I)
SEAT = re.compile(r"(?<![0-9a-z])(?:[@_]|w)?[0-9a-f]{5}(?![0-9a-z])", re.I)
EXCLUDED = ("messages", "vault", "project_names", "persona", "secrets", "licenses", "log_text", "cabin_seat_ids")
DOCTOR_PURE = ("python_version", "git_present", "state_root_perms", "schemas_readable")
# detail 只留不帶路徑、不帶錯誤原文的幾項;其他(工具路徑、git 路徑、失敗的錯誤訊息)一律 None(10-24 雙審第 2 輪:
# PATH 裡的專案夾會從執行檔路徑漏出專案名;schema 錯誤原文截半的家目錄躲過遮罩)
DETAIL_KEEP = ("python_version", "state_root_perms", "provider_optional", "provider_any")
VERSION_RE = re.compile(r"[0-9A-Za-z.+_-]{1,40}")
NAME_RE = re.compile(r"[a-z0-9]{1,24}")
READ_MAX = 1024 * 1024


def _root():
    return seats_dir_lib.state_root()[0]


def _open_regular(path):
    """開檔後才 fstat(不先 stat 再 open):不跟連結、FIFO / 裝置不等、不讀 ⇒ 回 None。"""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(st.st_mode):           # 先判再 fdopen:資料夾 fdopen 會炸、fd 沒人關
        os.close(fd)
        return None, st
    return os.fdopen(fd, "rb"), st


def _tail_rows(path):
    try:
        f, _st = _open_regular(path)
        if f is None:
            return []
        with f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines()[1 if size > TAIL_BYTES else 0:]:
        try:
            d = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def versions():
    try:
        with open(os.path.join(REPO, "VERSION"), encoding="utf-8") as f:
            aib = f.read().strip()[:40]
    except OSError:
        aib = None
    return {"aib": aib, "os": platform.system(), "macos": platform.mac_ver()[0] or None,
            "machine": platform.machine(), "python": platform.python_version()}


def plugins():
    """已裝外掛的種類 / 名稱 / 版本 / 有沒有簽章。角色卡包(persona)不收(人設不收);
    名稱不合包名規則、版本不像版本號 ⇒ "?"(manifest 是外掛自己寫的,10-24 雙審第 2 輪)。"""
    try:
        import aib_ext
        rows = aib_ext.installed()
    except Exception:  # noqa: BLE001 - 外掛表讀不到不拖垮整份回報
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("kind") == "persona":
            continue
        name, ver = r.get("name"), r.get("version")
        out.append({"kind": r.get("kind") if isinstance(r.get("kind"), str) else "?",
                    "name": name if isinstance(name, str) and aib_ext.name_ok(name) else "?",
                    "version": ver if isinstance(ver, str) and VERSION_RE.fullmatch(ver) else "?",
                    "signed": r.get("signed") is True})
    return out


def _providers():
    try:
        with open(os.path.join(HERE, "providers.json"), encoding="utf-8") as f:
            rows = json.load(f).get("providers") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [p["id"] for p in rows if isinstance(p, dict) and p.get("status") == "supported"
            and isinstance(p.get("id"), str) and re.fullmatch(r"[a-z0-9]{1,24}", p["id"])]


def doctor(root):
    """aib doctor 裡只讀、說明只有機器資料的幾項(DOCTOR_PURE + 各家 AI 工具在不在);不叫 main / run_all
    (見檔頭:整套會寫檔、連網、說明帶專案名)。只留 id / status / detail。"""
    sys.path.insert(0, os.path.join(REPO, "src", "bootstrap"))
    try:
        import aib_doctor as D
        got = [D.check_python(), D.check_git(), D.check_state_root_perms(root), D.check_schemas(REPO)]
        got += D.provider_checks(_providers(), False)
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__, "checks": [], "counts": {}}
    checks = [{"id": c.get("id"), "status": c.get("status"), "detail": _detail(c)}
              for c in got if isinstance(c, dict)
              and (c.get("id") in DOCTOR_PURE or str(c.get("id")).startswith("provider_"))]
    counts = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {"checks": checks, "counts": counts}


def _detail(c):
    cid = str(c.get("id"))
    if cid in DETAIL_KEEP or cid.startswith("provider_login_") or (cid == "schemas_readable" and c.get("status") == "ok"):
        return c.get("detail")
    return None


def _read_json(path):
    """只讀一般檔(FIFO / 裝置 / 連結到怪東西不開)、最多 READ_MAX;壞掉 / 太深 ⇒ None。"""
    try:
        f, st = _open_regular(path)
        if f is None:
            return None
        with f:
            if st.st_size > READ_MAX:
                return None
            return json.loads(f.read(READ_MAX + 1).decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _at(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def logins(root):
    """各家 AI 上次量到有沒有登入(power_login 的快取;不叫 CLI)。"""
    d = _read_json(os.path.join(root, "state", "power-login.json"))
    return {k: v.get("v") for k, v in (d.items() if isinstance(d, dict) else [])
            if isinstance(k, str) and NAME_RE.fullmatch(k) and isinstance(v, dict) and isinstance(v.get("v"), bool)}


def cabins(root):
    """每艙幾席、什麼角色;不帶艙號、席號、專案。"""
    out = []
    for cab in sorted(glob.glob(os.path.join(root, "instances", "*", "seats"))):
        roles = []
        for p in glob.glob(os.path.join(cab, "*.json")):
            d = _read_json(p)
            if not isinstance(d, dict):
                continue
            role = d.get("role")
            roles.append(role if isinstance(role, str) and re.fullmatch(r"[a-z0-9_-]{1,32}", role) else "?")
        out.append({"seats": len(roles), "roles": sorted(roles)})
    return out


def failures(root):
    """最近不成功的代碼:dm 寄送結果不是成功的、面板動作 ok false 的;只留代碼和時間。"""
    rows = []
    for d in _tail_rows(os.path.join(root, "logs", "dm-send.jsonl")):
        oc = d.get("outcome")
        if isinstance(oc, str) and oc not in DM_OK and re.fullmatch(r"[a-z0-9_]{1,40}", oc):
            rows.append({"source": "dm_send", "code": oc, "at": _at(d.get("ts"))})
    for d in _tail_rows(os.path.join(root, "logs", "seat-panel-actions.jsonl")):
        act = d.get("action")
        if d.get("ok") is False and isinstance(act, str) and re.fullmatch(r"[a-z0-9_]{1,40}", act):
            rows.append({"source": "seat_panel", "code": act, "at": _at(d.get("at"))})
    rows.sort(key=lambda r: r["at"] or 0)
    return rows[-FAIL_MAX:]


# 這些欄位的值與所有鍵是固定代碼(doctor id、狀態、失敗來源 / 代碼、外掛種類、格式名),不做使用者名替換(家目錄 / 金鑰 / 艙席照遮):
# 使用者叫 root / git / python 時才不會把 state_root_perms、git_present、python_version 換壞(#341 審查 L1)
STRUCT_VALUE_KEYS = frozenset(("id", "status", "source", "code", "kind", "schema"))


def redact(obj, home=None, user=None):
    """回 (遮過的物件, 遮了幾處)。家目錄 → ~、使用者名稱 → <user>、暫存路徑 → <tmp>、艙號 → <cabin>、席號 → <seat>、
    像金鑰的字串 → [redacted](只算 [redacted] 的處數)。"""
    home = os.path.expanduser("~") if home is None else home
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - Linux 容器 uid 不在 passwd、又沒 USER ⇒ 不遮使用者名,不讓整份失敗
            user = ""
    user = user or ""
    n = [0]

    home = home.rstrip("/")

    def s(v, names=True):
        if len(home) > 1:
            v = re.sub(re.escape(home) + r"(?=/|$|[\s\"'])", "~", v, flags=re.I)
        v = TMP.sub("<tmp>", v)
        v, k = SECRET.subn("[redacted]", v)        # 先遮金鑰:長十六進位不要先被席號吃掉一段
        n[0] += k
        v = CABIN.sub("<cabin>", v)
        v = SEAT.sub("<seat>", v)
        if names and len(user) >= 3:           # 底線不算字的一部分:andylin_fail 也要遮(10-25 稽核);太短的名字遮了會誤傷一般字
            v = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(user), "<user>", v, flags=re.I)
        return v

    def walk(o, key=None):
        if isinstance(o, str):
            return s(o, names=key not in STRUCT_VALUE_KEYS)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):                # 鍵是固定結構(段名 / versions 鍵 / 供應商代碼)⇒ 不換使用者名，其他照遮
            return {(s(k, names=False) if isinstance(k, str) else k): walk(v, k) for k, v in o.items()}
        return o

    return walk(obj), n[0]


def show_path(path, home=None):
    """回條給本機本人看的路徑:只把家目錄換成 ~(要找得到檔;不進回報包,不用整條遮)。"""
    home = os.path.expanduser("~") if home is None else home
    if home and len(home) > 1 and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return path


def collect(root=None):
    root = root or _root()
    body = {"schema": "aib-support/1", "at": int(time.time()), "versions": versions(), "plugins": plugins(),
            "doctor": doctor(root), "logins": logins(root), "cabins": cabins(root), "failures": failures(root)}
    return redact(body)


def _counts(body):
    return {"versions": len(body["versions"]), "plugins": len(body["plugins"]),
            "doctor": len(body["doctor"].get("checks") or []), "logins": len(body["logins"]),
            "cabins": len(body["cabins"]), "failures": len(body["failures"])}


def _write_private(path, text):
    """新開一個只有本人能讀的暫存檔(O_EXCL + 不跟符號連結 + 0600)寫好再換名;失敗把暫存檔收掉。"""
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _private_dir(d):
    if os.path.islink(d):                      # 指向別處的連結 ⇒ 不寫,也不去 chmod 別人的夾
        raise OSError("support dir is a symlink")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)                         # 夾本來就在(可能是 0755)也收緊


def _mark(root, now):
    try:
        d = os.path.join(root, "state", "run-marks")
        os.makedirs(d, mode=0o700, exist_ok=True)
        _write_private(os.path.join(d, "support.json"), json.dumps({"at": now}))
    except OSError:
        pass


def run(write=False, root=None):
    """回 (回條, rc)。沒 --write ⇒ 只列會收什麼,一個檔都不寫。"""
    root = root or _root()
    try:
        body, redacted = collect(root)
    except Exception:  # noqa: BLE001 - 任何一段炸了都回回條,不印 traceback
        return {"ok": False, "detail": "support_collect_failed", "file": None, "sections": list(SECTIONS), "counts": {},
                "redacted": 0, "excluded": list(EXCLUDED), "sent": False}, 1
    r = {"ok": True, "detail": "support_preview", "file": None, "sections": list(SECTIONS), "counts": _counts(body),
         "redacted": redacted, "excluded": list(EXCLUDED), "sent": False}
    if not write:
        return r, 0
    d = os.path.join(root, "support")
    try:
        text = json.dumps(body, ensure_ascii=False, indent=1, allow_nan=False)
        _private_dir(d)
        stem = os.path.join(d, "aib-support-%s" % time.strftime("%Y%m%d-%H%M%S", time.localtime(body["at"])))
        path, i = stem + ".json", 1
        while os.path.lexists(path):           # 同一秒再寫一次 ⇒ -2、-3…,不蓋掉上一份
            i += 1
            path = "%s-%d.json" % (stem, i)
        _write_private(path, text)
    except (OSError, ValueError):
        r.update(ok=False, detail="support_write_failed")
        return r, 1
    _mark(root, body["at"])
    r.update(detail="support_written", file=show_path(path))
    return r, 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib support")
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv[1:] if argv and argv[0] == "support" else argv)
    except SystemExit:
        return 2
    r, rc = run(write=a.write)
    print(json.dumps(r, ensure_ascii=False) if a.json else json.dumps(r, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
