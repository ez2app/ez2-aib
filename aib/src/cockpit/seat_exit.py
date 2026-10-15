#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位退出留痕(大綱 §88.2 第 7 條,2026-09-11):席怎麼沒的,要寫在它自己的席位檔裡.

    python3 src/cockpit/seat_exit.py record --window @N --reason <cli-exit|window-killed|captain-close|usage-gate|unknown> [--rc N] [--evidence "..."]
    python3 src/cockpit/seat_exit.py sweep [--instance-home DIR] [--seats-dir DIR] [--hub SOCKET]

為什麼要有這一支:09-11 一席的窗被關、另一席的 CLI 自己退了,兩件都**沒有任何紀錄**——
席位檔還在、看起來像活的,而「它為什麼不在了」沒有人答得出來.

`record`:讀同一份席位檔,**原子**寫回(暫存檔 + os.replace),只加五欄、不刪檔、不動其他欄:
  exited_at_epoch / exit_reason / exit_rc / exit_evidence / exit_recorded_by
  檔不存在、不是合法 JSON、不是物件 ⇒ rc=2 且**不覆寫**(壞檔是要有人知道的事,不是要被蓋掉的事).
  record 是明確事件,已有 exited_at 也照寫(後到的事件更準);**不重寫**那條規則只屬於 sweep.

`sweep`:掃所有 w*.json,對「還沒有 exited_at」的席:
  窗不在 hub(`tmux -L <hub> list-windows -a`)⇒ 補 reason=window-gone;
  窗在但 pid 不在(kill -0 ⇒ ESRCH)⇒ 補 reason=pid-gone;evidence 寫的是哪一個查不到.
  ★ hub 打不到 ⇒ 本輪**不判 window-gone**(量不到不等於不在),只做 pid 那一半,並在摘要裡說.
  ★ 已有 exited_at 的一律不重寫(sweep 是補漏,不是覆寫事件).
  壞檔具名跳過.印一行「補了幾席」.

席位檔目錄:--seats-dir > --instance-home/seats > seats_dir_lib.seats_dir()(HUB 導出 > EZ2_AIB_INSTANCE_HOME > 狀態根/seats).
主艙與艙內同檔名同窗號,拿錯那份會得到一份格式完全正確的錯身份,所以不自己解析,走單源.

rc:0 = 做完 / 2 = 引數或檔不合法(檔不存在、壞 JSON、seats 目錄不存在、reason 不合法).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import i18n_lib  # noqa: E402
import seats_dir_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()

RECORD_REASONS = ("cli-exit", "window-killed", "captain-close", "usage-gate", "unknown")
SWEEP_REASONS = ("window-gone", "pid-gone")
EXIT_FIELDS = ("exited_at_epoch", "exit_reason", "exit_rc", "exit_evidence", "exit_recorded_by")
RECORDED_BY_RECORD = "seat_exit.py record"
RECORDED_BY_SWEEP = "seat_exit.py sweep"


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


class InputError(Exception):
    """rc=2:引數或檔不合法."""


def pid_alive(pid) -> bool:
    """EPERM=在(別人的行程),ESRCH=不在;拿不到 int 一律當「量不到」由呼叫端處理."""
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def window_file(window: str) -> str:
    num = str(window).strip().lstrip("@")
    if not num.isdigit():
        raise InputError(T("aib.seat_exit.bad_window", {"value": window}))
    return "w%s.json" % num


def resolve_seats_dir(seats_dir: str | None, instance_home: str | None) -> Path:
    if seats_dir:
        return Path(seats_dir)
    if instance_home:
        return Path(instance_home) / "seats"
    return Path(seats_dir_lib.seats_dir())


def load_seat(path: Path) -> dict:
    if not path.is_file():
        raise InputError(T("aib.seat_exit.missing", {"path": str(path)}))
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as exc:
        raise InputError(T("aib.seat_exit.corrupt", {"path": str(path), "err": str(exc)[:200]}))
    if not isinstance(rec, dict):
        raise InputError(T("aib.seat_exit.not_object", {"path": str(path)}))
    return rec


def atomic_write(path: Path, rec: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stamp_exit(rec: dict, reason: str, rc, evidence: str, recorded_by: str) -> dict:
    out = dict(rec)
    out["exited_at_epoch"] = int(time.time())
    out["exit_reason"] = reason
    out["exit_rc"] = rc
    out["exit_evidence"] = evidence or ""
    out["exit_recorded_by"] = recorded_by
    return out


def window_from_pane() -> str | None:
    """--window 沒給時從 `$TMUX_PANE` 問自己的窗(★ 必帶 -t "$TMUX_PANE",裸 display-message 回的是
    current window,seat_who.py:306 那一課)。不在 tmux 裡、或 tmux 打不到 ⇒ None(量不到,不猜)。
    為什麼要有這條:開席收尾 LAUNCH_CMD 被三支形狀臂釘死「取窗恰好兩次」,掛線不能再加第三次。"""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        proc = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              stdin=subprocess.DEVNULL, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def record(args: argparse.Namespace) -> int:
    if args.reason not in RECORD_REASONS:
        raise InputError(T("aib.seat_exit.bad_reason", {
            "value": args.reason, "values": ",".join(RECORD_REASONS)}))
    window = args.window or window_from_pane()
    if not window:
        raise InputError(T("aib.seat_exit.no_window"))
    base = resolve_seats_dir(args.seats_dir, args.instance_home)
    path = base / window_file(window)
    rec = load_seat(path)
    atomic_write(path, stamp_exit(rec, args.reason, args.rc, args.evidence, RECORDED_BY_RECORD))
    print(T("aib.seat_exit.record_ok", {
        "path": str(path), "reason": args.reason,
        "rc": "-" if args.rc is None else args.rc}))
    return 0


def hub_windows(hub: str) -> list[str] | None:
    """hub 上現有的 window_id 清單;打不到回 None(量不到,不是空)."""
    if not hub:
        return None
    proc = subprocess.run(
        ["tmux", "-L", hub, "list-windows", "-a", "-F", "#{window_id}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, check=False, timeout=30,
    )
    if proc.returncode != 0:
        return None
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def sweep(args: argparse.Namespace) -> int:
    base = resolve_seats_dir(args.seats_dir, args.instance_home)
    if not base.is_dir():
        raise InputError(T("aib.seat_exit.seats_dir_missing", {"path": str(base)}))
    hub = args.hub or os.environ.get("EZ2_AIHUB_SOCKET") or os.environ.get("AIB_DM_SOCKET") or ""
    windows = hub_windows(hub)
    if windows is None:
        print(T("aib.seat_exit.hub_unreachable", {"hub": hub or "-"}), file=sys.stderr)
    scanned = filled = already = corrupt = unmeasured = 0
    for raw in sorted(glob.glob(str(base / "w*.json"))):
        path = Path(raw)
        scanned += 1
        try:
            rec = load_seat(path)
        except InputError as exc:
            corrupt += 1
            print(T("aib.seat_exit.corrupt_skipped", {"path": str(path), "err": str(exc)[:160]}),
                  file=sys.stderr)
            continue
        if "exited_at_epoch" in rec:
            already += 1
            continue
        wid = str(rec.get("window_id") or "")
        reason = evidence = None
        if windows is not None and wid and wid not in windows:
            reason = "window-gone"
            evidence = T("aib.seat_exit.evidence_window_gone", {"hub": hub, "window": wid})
        else:
            pid = rec.get("pid")
            if isinstance(pid, int) and pid > 0:
                if not pid_alive(pid):
                    reason = "pid-gone"
                    evidence = T("aib.seat_exit.evidence_pid_gone", {"value": pid})
            elif windows is None:
                unmeasured += 1
        if reason is None:
            continue
        atomic_write(path, stamp_exit(rec, reason, None, evidence, RECORDED_BY_SWEEP))
        filled += 1
        print(T("aib.seat_exit.filled_one", {"path": path.name, "reason": reason, "detail": evidence}))
    print(T("aib.seat_exit.sweep_summary", {
        "n": filled, "count": scanned, "v1": already, "v2": corrupt,
        "v3": unmeasured, "hub": hub or "-"}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="seat_exit.py", description=T("aib.seat_exit.help_description"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help=T("aib.seat_exit.help_record"))
    rec.add_argument("--window", default=None, help=T("aib.seat_exit.help_window"))
    rec.add_argument("--reason", required=True, help=T("aib.seat_exit.help_reason"))
    rec.add_argument("--rc", type=int, default=None, help=T("aib.seat_exit.help_rc"))
    rec.add_argument("--evidence", default="", help=T("aib.seat_exit.help_evidence"))
    rec.add_argument("--seats-dir", help=T("aib.seat_exit.help_seats_dir"))
    rec.add_argument("--instance-home", help=T("aib.seat_exit.help_instance_home"))
    sw = sub.add_parser("sweep", help=T("aib.seat_exit.help_sweep"))
    sw.add_argument("--instance-home", help=T("aib.seat_exit.help_instance_home"))
    sw.add_argument("--seats-dir", help=T("aib.seat_exit.help_seats_dir"))
    sw.add_argument("--hub", help=T("aib.seat_exit.help_hub"))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "record":
            return record(args)
        return sweep(args)
    except InputError as exc:
        sys.stderr.write(T("aib.seat_exit.invalid", {"detail": str(exc)}) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
