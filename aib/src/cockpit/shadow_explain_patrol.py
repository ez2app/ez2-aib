#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""影子續跑說明的保底巡查(第 3 層;the maintainer 10-13 19:5x「如果開,所有 AI 都必要列出」,a seat 派)。

沒有回合結束 hook 的家、hook 沒裝上的席,靠這支點名:每一輪(由 tools/dm-upkeep/tick.sh 每 3 分鐘叫一次)
  1. 找 reported=false、而且已經過了一輪(ROUND_S)的推包 / 影子續跑說明(src/policy/hooks/ship_report_gate.py 的檔);
  2. 讀那一席窗格目前的畫面(dm_queue.capture_screen,去色碼):畫面上已有那幾行 ⇒ 當成貼了,改 reported=true;
  3. 沒有 ⇒ aib dm 那一席「你漏了影子續跑說明,請貼:…」,記 nagged / nag_count;
  4. 同一份第二次還漏 ⇒ 通知 the maintainer(Mac 通知),之後不再吵(notified_human)。
認不出那一席的窗(席位檔不在、艙關了)⇒ 跳過,不猜。任何錯只記 log,不丟例外。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "policy", "hooks")))

ROUND_S = 300
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _screen_of(seat):
    """回 (去色碼的畫面, hub, provider);找不到活窗 ⇒ (None, None, None)。"""
    import dm_queue as Q
    recs = Q._seat_records_all_cabins(seat)
    for wid, prov, hub, _cur in recs:
        if hub and Q.pane_is_dead(wid, hub) is False:
            s = Q.capture_screen(wid, hub)
            if s is not None:
                return _ANSI.sub("", s), hub, prov
    return None, None, None


def _hooked(provider):
    """這一席已經有第 2 層(Claude + aib-now-gate Stop/PostToolUse)⇒ 巡查不管它,交給 Stop 守門
    (審查:Claude 畫面上本來就有工具自己印的、PostToolUse 顯示的那幾行,讀畫面會誤判成已貼)。"""
    try:
        import seat_now
        return (provider or "").lower() == "claude" and seat_now.shadow_layer2("claude") == "ok"
    except Exception:  # noqa: BLE001
        return False


def seen_on_screen(screen, need, head):
    """畫面上看得出 AI 貼了:要的那幾行都在,而且第一行出現兩次以上(一次是工具自己印的、一次是回覆);
    工具那段已捲走(畫面上沒有說明標題)時,出現一次就算。"""
    import ship_report_gate as gate
    body = gate._norm(screen)
    if not need or not all(gate._norm(ln) in body for ln in need):
        return False
    if body.count(gate._norm(need[0])) >= 2:
        return True
    return not (head and gate._norm(head) in body)


def _send_dm(seat, hub, text):
    """aib dm 給那一席;位址 = <艙位碼>/<席碼>(艙位碼 = 艙 id 最後一個 - 之後四碼)。回 True/False。"""
    cabin = (hub or "").rsplit("-", 1)[-1]
    if not cabin or not re.fullmatch(r"[0-9a-z]{4}", cabin):
        return False
    aib = os.path.join(HERE, "aib")            # 審查:launchd 的 PATH 沒有 ~/.local/bin ⇒ 裸叫 aib 會找不到
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        r = subprocess.run(["bash", aib, "dm", "--to", "%s/%s" % (cabin, seat), "--file", path],
                           capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _notify_human(text):
    if os.environ.get("AIB_SHIP_NOTIFY") == "0":
        return False
    try:
        r = subprocess.run(["osascript", "-e", "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run",
                            "AIB 影子續跑說明", text], capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _all_pending(gate, now):
    """所有席還沒回報的(不分席):[(檔, 內容)]。"""
    import json
    seats = set()
    for f in gate._candidates():
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("reported") is False and d.get("pusher"):
            seats.add(d["pusher"])
    out = []
    for s in sorted(seats):
        out.extend(gate.pending(s, now))
    return out


def _busy_of(seat):
    """寄點名前先看那一席是不是閒著(a seat auto_distill_hook.pane_idle_state 的「輸入框有字」;畫面 2 分鐘沒變要兩次取樣,這裡不等 —— dm 自己遇忙會排隊)。
    拿不到那支(還沒上主線)或量不到 ⇒ 當閒(照寄;dm 本身遇到忙也會排隊)。"""
    try:
        sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "identity")))
        import auto_distill_hook as ADH
        import dm_queue as Q
        for wid, prov, hub, _cur in Q._seat_records_all_cabins(seat):
            if hub and Q.pane_is_dead(wid, hub) is False:
                _hash, typing = ADH.pane_idle_state(hub, wid, prov)   # → (畫面雜湊, 有人在打字);抓不到回 (None, True)
                return bool(typing)
    except Exception:  # noqa: BLE001
        return False
    return False


def tick(now=None, screen_of=None, send_dm=None, notify=None, busy_of=None, hooked_of=None):
    """跑一輪。回 [(席, 檔名, 動作)];動作 = seen / nagged / human / skip-no-window / wait。"""
    import ship_report_gate as gate
    now = time.time() if now is None else now
    screen_of = screen_of or _screen_of
    send_dm = send_dm or _send_dm
    notify = notify or _notify_human
    if busy_of is None and screen_of is _screen_of:
        busy_of = _busy_of
    hooked_of = hooked_of or (_hooked if screen_of is _screen_of else (lambda prov: False))
    out = []
    for path, d in _all_pending(gate, now):
        seat = d["pusher"]
        name = os.path.basename(path)
        last = float(d.get("nagged_at") or d.get("at") or 0)
        if now - last < ROUND_S or d.get("notified_human"):
            out.append((seat, name, "wait"))
            continue
        got = screen_of(seat)
        screen, hub, prov = (tuple(got) + (None,))[:3]
        if screen is None:
            out.append((seat, name, "skip-no-window"))
            continue
        if hooked_of(prov):
            out.append((seat, name, "skip-hooked"))
            continue
        need = gate.required_lines(d)
        head = (d.get("savings") or [None])[0]
        if seen_on_screen(screen, need, head if d.get("kind") != "push" else None):
            gate._update(path, {"reported": True, "reported_at": now, "reported_how": "patrol-screen"})
            out.append((seat, name, "seen"))
            continue
        if busy_of is not None and busy_of(seat):
            out.append((seat, name, "wait-busy"))     # 正在打字 / 畫面還在動:不打斷,下一輪再看
            continue
        n = int(d.get("nag_count") or 0)
        if n >= 1:
            notify("@%s 連兩次漏列影子續跑說明(%s)" % (seat, d.get("kind") or "?"))
            gate._update(path, {"notified_human": now, "nag_count": n + 1})
            out.append((seat, name, "human"))
            continue
        text = ("@%s 影子續跑巡查:你這次 %s 的影子續跑說明沒有出現在回覆裡(the maintainer 10-13:如果開影子續跑,一定要列出說明)。"
                "請把下面那段原樣貼進你的下一則回覆,數字不要改:\n%s" % (seat, d.get("kind") or "aib test",
                                                                  "\n".join(str(x) for x in d.get("savings") or [])))
        ok = send_dm(seat, hub, text)
        # 審查:寄失敗不算點名過(不然下一輪就拿「連兩次漏」去吵 the maintainer,而那一席根本沒收到)
        gate._update(path, {"nagged_at": now, "nag_count": n + 1} if ok else {"nagged_at": now, "nag_failed": True})
        out.append((seat, name, "nagged" if ok else "nag-failed"))
    if out:
        gate._log("PATROL %s" % " ".join("%s:%s:%s" % x for x in out))
    return out


if __name__ == "__main__":
    try:
        for row in tick():
            print("%s %s %s" % row)
    except Exception as e:  # noqa: BLE001 —— 巡查壞了不擋 upkeep
        print("shadow-explain-patrol: %s: %s" % (type(e).__name__, e))
    raise SystemExit(0)
