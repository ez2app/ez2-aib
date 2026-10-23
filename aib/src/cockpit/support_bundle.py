#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib support:打包一份問題回報(10-24 Andy 准精簡版;契約 contracts/power.json support_receipt;動力室 UR4)。

  aib support [--json]           先看:列出會收哪幾段、每段幾筆,一個檔都不寫
  aib support --write [--json]   寫:<狀態根>/support/aib-support-<時間>.json(0600),只在本機;寄不寄由本人決定

收(全是機器狀態):aib / macOS / Python 版本、已裝外掛名稱版本、aib doctor 結果、各家 AI 上次量到有沒有登入、
艙數 / 每艙席數 / 角色、最近 20 筆不成功的代碼(dm 寄送結果、面板動作)與時間。
不收:dm / 對話內容、Vault、專案名、人設、金鑰 / token / 序號 / 授權檔、log 原文、艙號 / 席號。
寫之前:家目錄換 ~、使用者名稱換 <user>、暫存路徑換 <tmp>、艙號換 <cabin>、席號(5 碼 16 進位,含數字也含字母)換 <seat>、
像金鑰的字串換 [redacted]
(同帳號 AI 威脅守則:這只是減少誤帶,不是防線;包本來就只放機器狀態)。
要收 log 原文另開 --with-logs,要先問 Andy(還沒做)。
"""
import argparse
import getpass
import glob
import io
import json
import os
import platform
import re
import sys
import time
from contextlib import redirect_stdout

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
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|nsec1[0-9a-z]{20,}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{48,}={0,2})")
TMP = re.compile(r"/(?:private/)?var/folders/[^\s\"']+|/(?:private/)?tmp/[^\s\"']+")
CABIN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
SEAT = re.compile(r"(?<![0-9A-Za-z_])@?(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{5}(?![0-9A-Za-z_])")


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


def doctor(root):
    """借 aib doctor --json 的結果(mark=False:不是本人跑 doctor,R10 不記)。只留 id / status / detail。"""
    sys.path.insert(0, os.path.join(REPO, "src", "bootstrap"))
    try:
        import aib_doctor
        buf = io.StringIO()
        with redirect_stdout(buf):
            aib_doctor.main(["--json", "--state-root", root], mark=False)
        got = json.loads(buf.getvalue())
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__, "checks": [], "counts": {}}
    checks = [{"id": c.get("id"), "status": c.get("status"), "detail": c.get("detail")}
              for c in got.get("checks") or [] if isinstance(c, dict)]
    return {"checks": checks, "counts": got.get("counts") or {}}


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
            rows.append({"source": "dm_send", "code": oc, "at": d.get("ts") if isinstance(d.get("ts"), (int, float)) else None})
    for d in _tail_rows(os.path.join(root, "logs", "seat-panel-actions.jsonl")):
        act = d.get("action")
        if d.get("ok") is False and isinstance(act, str) and re.fullmatch(r"[a-z0-9_]{1,40}", act):
            rows.append({"source": "seat_panel", "code": act, "at": d.get("at") if isinstance(d.get("at"), (int, float)) else None})
    rows.sort(key=lambda r: r["at"] or 0)
    return rows[-FAIL_MAX:]


def redact(obj, home=None, user=None):
    """回 (遮過的物件, 遮了幾處)。家目錄 → ~、使用者名稱 → <user>、暫存路徑 → <tmp>、艙號 → <cabin>、席號 → <seat>、
    像金鑰的字串 → [redacted](只算 [redacted] 的處數)。"""
    home = os.path.expanduser("~") if home is None else home
    user = (getpass.getuser() if user is None else user) or ""
    n = [0]

    def s(v):
        if home and len(home) > 1:
            v = v.replace(home, "~")
        v = TMP.sub("<tmp>", v)
        v = CABIN.sub("<cabin>", v)
        v = SEAT.sub("<seat>", v)
        v, k = SECRET.subn("[redacted]", v)
        n[0] += k
        if len(user) >= 3:
            v = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(user), "<user>", v)
        return v

    def walk(o):
        if isinstance(o, str):
            return s(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        return o

    return walk(obj), n[0]


def collect(root=None):
    root = root or _root()
    body = {"schema": "aib-support/1", "at": int(time.time()), "versions": versions(), "plugins": plugins(),
            "doctor": doctor(root), "logins": logins(root), "cabins": cabins(root), "failures": failures(root)}
    return redact(body)


def _counts(body):
    return {"versions": len(body["versions"]), "plugins": len(body["plugins"]),
            "doctor": len(body["doctor"].get("checks") or []), "logins": len(body["logins"]),
            "cabins": len(body["cabins"]), "failures": len(body["failures"])}


def _mark(root, now):
    try:
        d = os.path.join(root, "state", "run-marks")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "support.json")
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": now}, f)
        os.replace(tmp, p)
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
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, "aib-support-%s.json" % time.strftime("%Y%m%d-%H%M%S", time.localtime(body["at"])))
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    except OSError:
        r.update(ok=False, detail="support_write_failed")
        return r, 1
    _mark(root, body["at"])
    r.update(detail="support_written", file=redact(path)[0])
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
