#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib status --json`——遠端讀那層的酬載,**不含任何傳輸**(the maintainer 08-18 18:17 批)。

## 界線(超出即停,寫在最前面)

**不接網路、不開埠、不寫 webhook、不碰任何憑證。** 本模組只產出「此刻
艦隊長什麼樣」的一份 JSON,將來由別人拿去送——傳輸=把沒有身分驗證的
入口開到世界上,那是工單裡的阻擋級前置,不在此檔。連 `git fetch` 都不跑
(fetch 也是網路):`equals_origin` 比的是**本地 origin/main ref**,欄位
自己說明這一點。

## 三條判準(the maintainer 原文)

1. **每個欄位標來源**——今天全艦最貴的教訓是「以為在觀察,其實看的是
   過期的東西」。凡是會過期/會查不到的欄一律 {"value":…, "source":…},
   source ∈ machine | screen | none | seat_file | projection | agents |
   ledger | git_local | tmux | UNRESOLVED。
2. **main 那格必有「是否等於 origin」**——「合了三次沒推就報已合」只有
   這格擋得住(本地 main 領先 origin/main 就是沒推)。
3. **查不到寫 UNRESOLVED,不省略欄位**——欄位消失和欄位是空的,遠端
   讀的人分不出來。

## 寫入面

獨立模組;import seat_who(五碼→身份,同一份判準不重抄)與
composer_state(dim=建議=視同空框,不是卡稿)。**不動 aib 與 dm-ctl.sh**
(a seat 獨佔中)。掛法一行(由它做):
    [ "${1:-}" = "status" ] && { shift; exec "$T_PY3" "$HERE/fleet_status.py" "$@"; }

CLI:
    python3 src/cockpit/fleet_status.py [--home H] [--repo R] [--pretty]
輸出恆為 JSON(這支存在的意義就是 --json)。rc:0=有產出(殘缺艙也算,
誠實標 UNRESOLVED) / 2=用法錯。
"""
import argparse
import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "rotation"))

import seat_who as SW                                       # noqa: E402
from composer_state import composer_state                   # noqa: E402

UNRESOLVED = SW.UNRESOLVED
LEDGER_TAIL_BYTES = 1 << 20          # 每份帳本只讀尾段 1MB:狀態頁不是稽核


def _sv(value, source):
    """標來源欄位(判準一)。value 查不到=UNRESOLVED,source 同步誠實。"""
    return {"value": value, "source": source}


def _unres():
    return _sv(UNRESOLVED, UNRESOLVED)


def _ledger_last_ms(home):
    """回 {五碼: wall_ms}——每席最近一次落帳。只讀帳本尾段(見常數)。"""
    out = {}
    paths = [os.path.join(home, "ledger", "aib-ledger.jsonl")]
    inst_root = os.path.join(home, "instances")
    if os.path.isdir(inst_root):
        for n in sorted(os.listdir(inst_root)):
            paths.append(os.path.join(inst_root, n, "ledger", "aib-ledger.jsonl"))
    for p in paths:
        try:
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - LEDGER_TAIL_BYTES))
                blob = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        for ln in blob.splitlines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue        # 尾段第一行可能被切半:跳過,不是錯
            sig = ((rec.get("actor") or {}).get("sig") or "")
            wall = ((rec.get("hlc") or {}).get("wall_ms"))
            if "@" not in sig or not isinstance(wall, (int, float)):
                continue
            code = sig.split("@", 1)[1].split("/", 1)[0].lower()
            if code and (code not in out or wall > out[code]):
                out[code] = int(wall)
    return out


def _seat_entry(inst, rec, ledger_ms):
    seat_id = str(rec.get("seat_id") or "").lower()
    uuid = rec.get("session_uuid") if isinstance(rec.get("session_uuid"), str) else None
    wid = str(rec.get("window_id") or "")
    entry = {
        "seat_id": _sv(seat_id or UNRESOLVED, "seat_file"),
        "role": _sv(rec.get("role") or UNRESOLVED, "seat_file"),
        "instance": _sv(inst or "main", "path"),
        "window": _sv(wid or UNRESOLVED, "seat_file"),
    }
    ident = SW._identity(_seat_entry._home, seat_id, SW._uuid_tail5(uuid))
    entry["persona"] = _sv(ident["persona"],
                           "projection" if ident["persona"] != UNRESOLVED else UNRESOLVED)
    entry["brain_route_key"] = _sv(ident["brain_route_key"],
                                   "projection" if ident["brain_route_key"] != UNRESOLVED
                                   else UNRESOLVED)
    win_alive = SW._window_alive(inst, wid) if wid else None
    pid_alive = SW._pid_alive(rec.get("pid"))
    if win_alive is not None:
        entry["alive"] = _sv(bool(win_alive), "tmux")
    elif pid_alive is not None:
        entry["alive"] = _sv(bool(pid_alive), "pid")
    else:
        entry["alive"] = _unres()
    alive_v = entry["alive"]["value"]
    if alive_v is False:
        entry["ctx"] = _sv(UNRESOLVED, "none")     # 死席不給 CTX 裝活
        entry["composer"] = _sv(UNRESOLVED, "none")
    else:
        pct, src, note = SW._ctx_of(inst, wid, uuid)
        entry["ctx"] = _sv(pct, src)
        if note:
            entry["ctx"]["note"] = note
        rc, screen = SW._tmux(inst, "capture-pane", "-e", "-p",
                              "-t", "aihub:%s.0" % wid)
        if rc == 0:
            state, _text = composer_state(screen, provider=rec.get("provider"))
            # dim=建議=視同空框(composer_state 檔頭判準);定不出=不猜
            entry["composer"] = (_sv(state, "screen") if state is not None
                                 else _unres())
        else:
            entry["composer"] = _unres()
    last = ledger_ms.get(seat_id)
    entry["last_ledger_at"] = (_sv(SW._fmt_ts(last), "ledger") if last
                               else _unres())
    entry["last_active"] = _sv(
        SW._fmt_ts(rec.get("last_used_epoch") or rec.get("started_epoch")),
        "seat_file")
    return entry


def _git(repo, args):
    try:
        p = subprocess.run(["git", "-C", repo] + args, capture_output=True,
                           text=True, timeout=20)
        return (p.stdout.strip() if p.returncode == 0 else None)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_section(repo):
    """全部本地查詢(判準二+不接網路):equals_origin 比的是本地
    origin/main ref,欄位自帶說明,不假裝 fetch 過。"""
    main = _git(repo, ["rev-parse", "main"])
    origin = _git(repo, ["rev-parse", "origin/main"])
    if main is None:
        sec = {"main_sha": _unres(), "equals_origin": _unres()}
    else:
        sec = {"main_sha": _sv(main[:12], "git_local")}
        if origin is None:
            sec["equals_origin"] = _unres()
        else:
            sec["equals_origin"] = _sv(main == origin, "git_local")
            if main != origin:
                behind = _git(repo, ["rev-list", "--count",
                                     "main..origin/main"])
                ahead = _git(repo, ["rev-list", "--count",
                                    "origin/main..main"])
                sec["equals_origin"]["note"] = (
                    "本地 main 領先 %s、落後 %s(領先>0=有合沒推)"
                    % (ahead or "?", behind or "?"))
    sec["origin_ref_note"] = ("以本地 origin/main ref 為準,本工具不 fetch"
                              "(fetch=網路,超出讀酬載的界線)")
    unmerged = _git(repo, ["branch", "--no-merged", "main",
                           "--format=%(refname:short)"])
    sec["unmerged_branches"] = (_sv(sorted(b for b in unmerged.splitlines() if b),
                                    "git_local")
                                if unmerged is not None else _unres())
    today = time.strftime("%Y-%m-%d 00:00:00", time.localtime())
    count = _git(repo, ["rev-list", "--count", "--since", today, "main"])
    sec["commits_today"] = (_sv(int(count), "git_local")
                            if count is not None and count.isdigit() else _unres())
    return sec


def _lifecycle_over_line(seats):
    """機器 CTX(非 stale)過線的席(門檻取 `lifecycle.STAGES` 本尊)。

    ★ EH.28:原本這行 docstring 逐字列著 `50/60/75/85/90/96`。那是**第三份副本**
      (policy 一份、STAGES 一份、這裡一份),而 docstring 不會被任何測試比對 ——
      門檻改了它也不會紅,只會從此說錯話。**所以數字拿掉,只留出處。**
    screen/stale/查不到的一律不列——判準一的精神:
    不能拿不可信的值去觸發生命週期警報。"""
    try:
        import lifecycle
    except Exception:
        return _unres()
    out = []
    for s in seats:
        ctx = s.get("ctx") or {}
        pct = ctx.get("value")
        if ctx.get("source") != "machine" or ctx.get("note") \
                or not isinstance(pct, int):
            continue
        try:
            stage, _acts = lifecycle.stage_for(pct)
        except Exception:
            continue
        if stage != "ok":
            out.append({"seat_id": s["seat_id"]["value"], "ctx": pct,
                        "stage": stage})
    return _sv(sorted(out, key=lambda x: -x["ctx"]), "machine")


def build_status(home=None, repo=None):
    home = home or SW._home()
    repo = repo or os.path.abspath(os.path.join(_HERE, "..", ".."))
    _seat_entry._home = home
    ledger_ms = _ledger_last_ms(home)
    seats, broken = [], []
    seen_dirs = {i for i, _ in SW.seats_dirs(home)}
    for inst, path, rec in SW._iter_seats(home):
        seats.append(_seat_entry(inst, rec, ledger_ms))
    # 殘缺艙(判準三):instances/<N>/ 存在但 seats/ 不存在——不是零席,
    # 是「看不見」。省略它=殘缺被吞掉,遠端以為那艙不存在。
    inst_root = os.path.join(home, "instances")
    if os.path.isdir(inst_root):
        for n in sorted(os.listdir(inst_root)):
            full = os.path.join(inst_root, n)
            if os.path.isdir(full) and n not in seen_dirs:
                broken.append({"instance": n,
                               "seats": UNRESOLVED,
                               "note": "instances/%s 存在但 seats/ 目錄不在"
                                       "——看不見≠零席" % n})
    if not os.path.isdir(os.path.join(home, "seats")) and None not in seen_dirs:
        broken.append({"instance": "main", "seats": UNRESOLVED,
                       "note": "主艙 seats/ 目錄不在——看不見≠零席"})
    return {
        "schema": "aib-fleet-status/v1",
        "generated_at": _sv(time.strftime("%Y-%m-%d %H:%M:%S"), "clock"),
        "home": home,
        "seats": seats,
        "broken_instances": broken,
        "git": _git_section(repo),
        "lifecycle_over_line": _lifecycle_over_line(seats),
        "transport": "none(本工具只產酬載;讀層也需要身分,傳輸候阻擋級"
                     "前置解掉後另單)",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib status",
                                 description="艦隊狀態 JSON 酬載(唯讀、零傳輸)")
    ap.add_argument("--home", help="覆寫 EZ2_AIB_HOME(測試治具用)")
    ap.add_argument("--repo", help="git 區段的 repo(預設=本模組所在 repo)")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    doc = build_status(home=args.home, repo=args.repo)
    print(json.dumps(doc, ensure_ascii=False,
                     indent=1 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
