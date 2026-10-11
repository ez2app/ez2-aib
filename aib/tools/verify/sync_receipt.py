#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""sync_receipt — 艦隊同步逐台留收據,失敗大聲回報(10-11,舊協作板 scripts/sync-receipt.py 搬回 A4)。

為什麼要有(舊板 07-22 全艦隊部署當場的三次實證,全部是**靜默失敗**):
  一台 `git pull` 失敗,輸出被導進 /dev/null,直到版本沒動才發現;
  一台 `git fetch` 真實 rc=128,但檢查指令把 exit code 接了 pipe,拿到的是 tail 的 0;
  一台安裝時正在重開機,檢查那行根本沒印出來。
共同點不是「出錯」,是「出錯沒有留下可查的紀錄」。本工具把每台的結果寫成只追加的收據,
結尾**大聲**列出失敗名單。

用法:
  sync_receipt.py record  --host H --rc N [--stage sync] [--detail "..."]
  sync_receipt.py verify  --host H [--ssh user@host] [--repo ~/ez2-aib]
        遠端唯讀取 VERSION / HEAD / 未提交檔數,記一筆。--ssh 沒給就查艦長室主機名冊(aib room hosts)。
  sync_receipt.py summary [--minutes 60]     彙總;有失敗 exit 1

收據:<狀態根>/logs/fleet-sync.jsonl(狀態根 = EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。
★ 收據寫不進去不擋部署,但一定在 stderr 說;本工具自己出例外也 exit 0(收據器永不擋部署)並大聲印。
★ verify 判定拿真實 exit code,不比對別人輸出的文案(舊板 08-03 實測:比文案會在對方改字時靜默翻面)。
  rc 拿不到(連不上、遠端太舊)不算通過:沒有證據就是沒有證據。
★ 印給人看的字走 i18n(aib.sync_receipt.*);收據裡的欄位值是資料,不翻。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import i18n_lib  # noqa: E402

RC_MEANING = {0: "ok", 1: "cd failed (repo not found)", 2: "git pull failed",
              3: "install failed", 4: "post-install verify failed"}
SSH_SAFE = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]


def T(key, params=None):
    return i18n_lib.t("aib.sync_receipt." + key, params, locale=os.environ.get("EZ2_AIB_LOCALE", "zh-TW"))


def state_root():
    return Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def ledger():
    return state_root() / "logs" / "fleet-sync.jsonl"


def append(rec):
    try:
        path = ledger()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write("sync_receipt: " + T("ledger_write_failed", {"err": repr(e)}) + "\n")


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def cmd_record(argv):
    host = opt(argv, "--host", "?")
    try:
        rc = int(opt(argv, "--rc", "0") or 0)
    except ValueError:
        sys.stderr.write("sync_receipt: " + T("not_int", {"flag": "--rc"}) + "\n")
        return 2
    append({"ts": int(time.time()), "kind": "fleet-sync", "subject": host,
            "stage": opt(argv, "--stage", "sync"), "rc": rc, "ok": rc == 0,
            "meaning": RC_MEANING.get(rc, "unknown rc %d" % rc),
            "detail": opt(argv, "--detail", ""), "via": "record"})
    return 0


def ssh_target(host, given=None):
    """--ssh 明給優先;否則查艦長室主機名冊(config.json hosts[])。找不到回 None。"""
    if given:
        return given
    try:
        cfg = json.loads((state_root() / "state" / "captain-room" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for h in cfg.get("hosts") or []:
        if isinstance(h, dict) and h.get("name") == host and h.get("ssh"):
            return str(h["ssh"])
    return None


def remote(target, script):
    ssh_bin = os.environ.get("AIB_SYNC_RECEIPT_SSH") or "ssh"     # 測試指向假的連線程式
    try:
        p = subprocess.run([ssh_bin, *SSH_SAFE, target, script], capture_output=True, text=True,
                           timeout=90, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except Exception as e:
        return -1, "ERR=%r" % (e,)


def cmd_verify(argv):
    host = opt(argv, "--host", "?")
    target = ssh_target(host, opt(argv, "--ssh"))
    repo = opt(argv, "--repo", "~/ez2-aib")
    if not target or not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.:_-]+", target):
        sys.stderr.write("sync_receipt: " + T("no_target", {"host": host}) + "\n")
        return 2
    if not re.fullmatch(r"[A-Za-z0-9._/~-]+", repo):
        sys.stderr.write("sync_receipt: " + T("repo_bad") + "\n")
        return 2
    rc, out = remote(target, (
        'cd %s 2>/dev/null || { echo "CDRC=1"; exit 0; }; echo "CDRC=0"; '
        'echo "VER=$(head -1 VERSION 2>/dev/null)"; '
        'h=$(git rev-parse --short HEAD 2>/dev/null); echo "HEADRC=$?"; echo "HEAD=$h"; '
        'd=$(git status --porcelain 2>/dev/null); echo "DIRTYRC=$?"; '
        'echo "DIRTY=$(printf %%s "$d" | grep -c .)"' % repo))
    got = {}
    for line in out.splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and k in ("CDRC", "VER", "HEAD", "HEADRC", "DIRTYRC", "DIRTY"):
            got[k.lower()] = v.strip()
    why = []
    if rc != 0:
        why.append(T("why_conn", {"rc": rc}))
    if got.get("cdrc") != "0":
        why.append(T("why_no_repo", {"repo": repo}) if got.get("cdrc") == "1" else T("why_no_reply"))
    if got.get("cdrc") == "0":
        if got.get("headrc") != "0" or not got.get("head"):
            why.append(T("why_head"))
        if got.get("dirtyrc") != "0":
            why.append(T("why_status"))
        elif got.get("dirty") not in ("", "0"):
            why.append(T("why_dirty", {"n": got.get("dirty")}))
        if not got.get("ver"):
            why.append(T("why_no_version"))
    ok = not why
    append({"ts": int(time.time()), "kind": "fleet-verify", "subject": host, "stage": "verify",
            "ok": ok, "rc": 0 if ok else 1, "version": got.get("ver", ""), "head": got.get("head", ""),
            "dirty": got.get("dirty", ""), "detail": "; ".join(why), "via": "verify"})
    print("  %s %-9s v%-8s %-10s %s" % ("✓" if ok else "✗", host, got.get("ver", "?"), got.get("head", "?"),
                                        T("clean") if ok else "; ".join(why)))
    return 0 if ok else 1


def cmd_summary(argv):
    try:
        minutes = int(opt(argv, "--minutes", "60") or 60)
    except ValueError:
        sys.stderr.write("sync_receipt: " + T("not_int", {"flag": "--minutes"}) + "\n")
        return 2
    since = int(time.time()) - minutes * 60
    rows = []
    try:
        with ledger().open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ts", 0) >= since:
                    rows.append(r)
    except FileNotFoundError:
        print(T("no_rows_ever", {"path": ledger()}))
        return 0
    if not rows:
        print(T("no_rows_recent", {"m": minutes}))
        return 0
    bad = [r for r in rows if not r.get("ok")]
    ok_hosts = sorted({r.get("subject", "?") for r in rows if r.get("ok")})
    print("")
    print(T("summary_head", {"m": minutes, "n": len(rows)}))
    print(T("summary_ok_hosts", {"n": len(ok_hosts), "hosts": " ".join(ok_hosts) or "-"}))
    if bad:
        print(T("summary_bad_head", {"n": len(bad)}))
        for r in bad:
            print("    ✗ %-9s [%s] rc=%s %s %s" % (r.get("subject", "?"), r.get("stage", "?"), r.get("rc", "?"),
                                                  r.get("meaning", ""), r.get("detail", "")))
        print(T("ledger_at", {"path": ledger()}))
        return 1
    print(T("all_ok", {"path": ledger()}))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmds = {"record": cmd_record, "verify": cmd_verify, "summary": cmd_summary}
    if not argv or argv[0] not in cmds:
        sys.stderr.write(T("usage") + "\n")
        return 2
    return cmds[argv[0]](argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write("sync_receipt: " + T("crashed", {"err": repr(e)}) + "\n")
        sys.exit(0)
