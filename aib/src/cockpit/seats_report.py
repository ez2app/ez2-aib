#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""seats_report.py — 開席之後的驗證表(`aib <N>sits` 的完成定義)。

★ 本檔存在的理由只有一句:**`aib` 回 rc=0 不是開席證據。**
  rc=0 只說「指令跑完了」。席位真的成立要三件事同時為真:
    1. 窗還在(tmux 那一格沒死)
    2. 席位檔寫出來了(instances/<N>/seats/w<窗>.json)
    3. **席位檔裡有 session_uuid** —— 缺了它,seat_status 判 NO_UUID,
       右欄那一席就戴 ❓(未編制),壓過角色圖示。

  第 3 件是唯一會**在開席後才慢慢成立**的:非 claude 的 provider 沒有
  `--session-id` 可帶,身份要靠 seat-detect 的 artifact 探針事後回填,而
  artifact 通常在開席後才生出來(codex 的 rollout jsonl、agy 的 presence
  lock)。所以本檔會被批次工具重複呼叫,直到落檔或到上限。

★ provider 中立:本檔不認得任何一個 provider 的名字,也不該認得。它只問
  「這一格有沒有身份」。新 AI 接進來時本檔零改動——會需要改的是探針那一層
  (seat-detect.py)與登記簿,不是這裡。

CLI:
  seats_report.py --table          印表;全部有身份 rc=0,有缺 rc=4
  seats_report.py --missing-uuid   只印還沒落檔的角色名(空=全好)
rc:0 全數成立 / 2 環境錯(拿不到艙或席位目錄)/ 4 有席缺身份
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import seat_uuid_backfill  # noqa: E402  ED.13 時間窗:pending vs overdue
import seats_dir_lib  # noqa: E402  席位目錄單源(EH.2/ED.1)

_UUID_FULL_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def hub():
    h = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if not h:
        sys.exit("seats_report: EZ2_AIHUB_SOCKET 未設——不猜艙。")
    return h


def seats_dir(h):
    """★ 委派 seats_dir_lib(hub 直接傳入):與 aib:284 aib_seats_dir 同一順位
    HUB 導出 > INSTANCE_HOME > 狀態根;ED.1 後 HUB 分支也掛狀態根之下。
    這裡原本逐字重述一份——兩處分岔的形狀是「工具說沒有這一席,而畫面上它好好的」,
    所以收進單源,不再各寫各的。"""
    return seats_dir_lib.seats_dir(hub=h)


def live_windows(h):
    """活窗 → (role, provider);讀 tmux 窗變數,那是 aib 開席時寫進去的。"""
    try:
        out = subprocess.run(
            ["tmux", "-L", h, "list-windows", "-t", "aihub", "-F",
             "#{window_id}\t#{@aib_role}\t#{@aib_provider}"],
            capture_output=True, text=True, env={
                k: v for k, v in os.environ.items() if k != "TMUX"})
    except FileNotFoundError:
        sys.exit("seats_report: 找不到 tmux")
    if out.returncode != 0:
        sys.exit("seats_report: 讀不到艙 %s:%s" % (h, out.stderr.strip()))
    live = {}
    for ln in out.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and parts[0]:
            live[parts[0]] = (parts[1], parts[2])
    return live


def read_seats(sdir):
    recs = {}
    if not os.path.isdir(sdir):
        return recs
    for base in sorted(os.listdir(sdir)):
        if not (base.startswith("w") and base.endswith(".json")):
            continue
        try:
            with open(os.path.join(sdir, base), encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        wid = rec.get("window_id")
        if isinstance(wid, str):
            recs[wid] = rec
    return recs


def collect():
    h = hub()
    live = live_windows(h)
    recs = read_seats(seats_dir(h))
    rows = []
    for wid, (role, provider) in sorted(live.items(),
                                        key=lambda kv: int(kv[0][1:])):
        # ★ 「有 @aib_role」不足以判定它是席。實測 aihub-16 的 @1(watch 窗)
        #   帶著 role=`sys.watch` 而沒有 provider,於是第一版把它算成一席、
        #   再報成「缺 session_uuid」——**一個誤報,而且方向最壞**:它讓一份
        #   健康的艙看起來永遠有一席壞著,下一個人就會學會忽略這張表。
        #   判準改成「provider 有值」:aib 開席時 role 與 provider 是同一段
        #   寫進去的(aib:430-431),真席兩者必然成對;非席的窗兩者都沒有。
        if not provider:
            continue
        rec = recs.get(wid) or {}
        uuid = rec.get("session_uuid")
        rows.append({
            "window": wid,
            "role": role,
            "provider": provider or (rec.get("provider") or "?"),
            "seat_id": rec.get("seat_id") or "--",
            "has_seat_file": bool(rec),
            "uuid_ok": isinstance(uuid, str) and bool(_UUID_FULL_RE.match(uuid)),
            "uuid": uuid if isinstance(uuid, str) else None,
            "rec": rec,
        })
    return h, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--missing-uuid", action="store_true")
    ap.add_argument("--require-new", metavar="JSON",
                    help="這次真的開了的席:{role: 開席前的 uuid 或 null}。"
                         "這些席只有拿到**與開席前不同**的 uuid 才算完成。")
    a = ap.parse_args()
    h, rows = collect()
    # ★★ qa(codex,獨立第二引擎)08-21 二審判退,本段是修法:
    #   原本的完成定義是「席位檔有 session_uuid」——而**欄位存在不證明它是
    #   這次開席產生的**。配上 instance 槽位重用、或單純是重開一個舊窗號,
    #   一個十天前留下的 uuid 就會讓這次開席看起來成功。
    #   那是一個**恆真判準**:它在缺陷還在時也是綠的。
    #   修法:呼叫端把「這次真的開了哪些席、它們開席前的 uuid 是什麼」傳進來,
    #   這些席必須拿到**不同的** uuid 才算完成。
    #   ★ 只對「這次開的」要求變號:被跳過的活席本來就不該換 uuid,
    #     對它們要求變號會製造假紅。兩者必須分得開。
    baseline = {}
    if a.require_new:
        try:
            baseline = json.loads(a.require_new)
        except ValueError:
            print("seats_report: --require-new 不是合法 JSON,拒絕判定",
                  file=sys.stderr)
            return 2
    for r in rows:
        r["needs_new"] = r["role"] in baseline
        if r["needs_new"]:
            r["fresh"] = r["uuid_ok"] and r["uuid"] != baseline.get(r["role"])
        else:
            r["fresh"] = r["uuid_ok"]
    missing = [r for r in rows if not r["fresh"]]

    if a.missing_uuid:
        if missing:
            print(" ".join("%s(%s)" % (r["role"], r["provider"])
                           for r in missing))
        return 0

    now = int(time.time())
    window_s = seat_uuid_backfill.settle_window_s()
    print("艙 %s — %d 席(uuid 時間窗 %ds)" % (h, len(rows), window_s))
    print("  %-5s %-10s %-8s %-7s %-9s %s"
          % ("窗", "角色", "引擎", "五碼", "席位檔", "身份"))
    for r in rows:
        if r["fresh"]:
            ident = "已落檔"
        elif r["uuid_ok"]:
            ident = "★ uuid 與開席前相同,不是本次的證據"
        else:
            # ED.13:缺 uuid 分兩種,差別是「還在等」與「該有而沒有」。
            #   pending 不是壞——artifact 還沒生出來,aib idle watch 每輪回填;
            #   overdue 才是要人看的那一格。兩者在右欄都戴 ❓(顯示不改)。
            phase, elapsed = seat_uuid_backfill.uuid_phase(r["rec"], now, window_s)
            why = r["rec"].get("session_uuid_pending")
            why = ("「%s」" % why) if isinstance(why, str) and why.strip() else ""
            if phase == seat_uuid_backfill.PHASE_PENDING:
                ident = ("缺 session_uuid(pending%s:開席 %ds,窗 %ds 內;"
                         "watch 每輪回填)→ 右欄暫戴 ❓" % (why, elapsed, window_s))
            else:
                ident = ("★ 缺 session_uuid(overdue:%s,已過 %ds 時間窗)→ 右欄戴 ❓"
                         % ("開席 %ds" % elapsed if elapsed is not None
                            else "started_epoch 缺", window_s))
        print("  %-5s %-10s %-8s %-7s %-9s %s"
              % (r["window"], r["role"], r["provider"], r["seat_id"],
                 "有" if r["has_seat_file"] else "缺", ident))
    if missing:
        print("")
        print("seats_report: %d 席身份未落檔。**這不是它們壞了**——" % len(missing))
        print("  非 claude 的 provider 靠 seat-detect 的 artifact 探針事後回填,")
        print("  artifact 還沒生出來時探針就是量不到。可稍後重跑:")
        print("    EZ2_AIHUB_SOCKET=%s python3 %s/seat-detect.py --all --apply"
              % (h, os.path.dirname(os.path.abspath(__file__))))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
