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
    r"|hf_[A-Za-z0-9]{20,}|bearer\s+\S{16,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{48,}={0,2})", re.I)
TMP = re.compile(r"/(?:private/)?var/folders/[^\s\"']+|/(?:private/)?tmp/[^\s\"']+", re.I)
CABIN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}", re.I)
SEAT = re.compile(r"(?<![0-9a-z])(?:[@_]|w)?[0-9a-f]{5}(?![0-9a-z])", re.I)
DOCTOR_PURE = ("python_version", "git_present", "state_root_perms", "schemas_readable")


def _root():
    return seats_dir_lib.state_root()[0]


def _tail_rows(path):
    try:
        with open(path, "rb") as f:
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
        except ValueError:
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
    try:
        import aib_ext
        rows = aib_ext.installed()
    except Exception:  # noqa: BLE001 - 外掛表讀不到不拖垮整份回報
        return []
    return [{"kind": r.get("kind"), "name": r.get("name"), "version": r.get("version"), "signed": bool(r.get("signed"))}
            for r in rows if isinstance(r, dict)]


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
    checks = [{"id": c.get("id"), "status": c.get("status"), "detail": c.get("detail")}
              for c in got if isinstance(c, dict)
              and (c.get("id") in DOCTOR_PURE or str(c.get("id")).startswith("provider_"))]
    counts = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {"checks": checks, "counts": counts}


def _at(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def logins(root):
    """各家 AI 上次量到有沒有登入(power_login 的快取;不叫 CLI)。"""
    try:
        with open(os.path.join(root, "state", "power-login.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v.get("v") for k, v in (d.items() if isinstance(d, dict) else [])
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("v"), bool)}


def cabins(root):
    """每艙幾席、什麼角色;不帶艙號、席號、專案。"""
    out = []
    for cab in sorted(glob.glob(os.path.join(root, "instances", "*", "seats"))):
        roles = []
        for p in glob.glob(os.path.join(cab, "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    role = json.load(f).get("role")
            except (OSError, ValueError, AttributeError):
                continue
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


def redact(obj, home=None, user=None):
    """回 (遮過的物件, 遮了幾處)。家目錄 → ~、使用者名稱 → <user>、暫存路徑 → <tmp>、艙號 → <cabin>、席號 → <seat>、
    像金鑰的字串 → [redacted](只算 [redacted] 的處數)。"""
    home = os.path.expanduser("~") if home is None else home
    user = (getpass.getuser() if user is None else user) or ""
    n = [0]

    home = home.rstrip("/")

    def s(v):
        if len(home) > 1:
            v = re.sub(re.escape(home) + r"(?=/|$|[\s\"'])", "~", v, flags=re.I)
        v = TMP.sub("<tmp>", v)
        v, k = SECRET.subn("[redacted]", v)        # 先遮金鑰:長十六進位不要先被席號吃掉一段
        n[0] += k
        v = CABIN.sub("<cabin>", v)
        v = SEAT.sub("<seat>", v)
        if len(user) >= 3:
            v = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(user), "<user>", v, flags=re.I)
        return v

    def walk(o):
        if isinstance(o, str):
            return s(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {(s(k) if isinstance(k, str) else k): walk(v) for k, v in o.items()}
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
    body, redacted = collect(root)
    r = {"ok": True, "detail": "support_preview", "file": None, "sections": list(SECTIONS), "counts": _counts(body),
         "redacted": redacted, "excluded": ["messages", "vault", "project_names", "persona", "secrets", "licenses",
                                           "log_text", "cabin_seat_ids"], "sent": False}
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
