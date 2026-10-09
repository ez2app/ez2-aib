#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席碼表(O1,大綱 §85.5,2026-09-10):每一次 `aib <provider> <mode> <role>` 一份 JSONL。

為什麼要有它:今天「開席慢」的每一個秒數都是拿 tmux 時戳跟檔案 mtime 事後拼出來的,
`instances/<艙>/cockpit/aib-open.log` 存在但是空的。沒有碼表,修好了也會靜靜退回去,
而下一個說「aib 慢」的人手上還是沒有數字(§74 那個 128 秒流傳了好幾天才被量出來是錯的)。

三格(合議 85.4-4):
  pressed → window_built     人按下 Enter 到多視窗建好
  window_built → switch      多視窗建好到人的畫面被切過去(O3 的判準:≤ 2 秒)
  switch → can_type          切過去到那一席真的可以打字(人感覺到的等待)
另外記 provider_first_frame(格子第一次有內容)與 ready(開席單回執,由 seat_onboard 的帳本另算,本檔不重抄)。

檔:$EZ2_AIB_INSTANCE_HOME/cockpit/open-attempts/<attempt_id>.jsonl,0600,每行一事件:
  {"attempt","event","wall_ms","mono_ms","provider","role","window","pid","note"}
  wall_ms 給人看、mono_ms 給算差(同一次開機內跨行程可比;time.monotonic 在 macOS 走 mach_absolute_time)。
★ 每 attempt 一檔不是單一 log:同艙多席同開會交錯(合議 85.4-7)。
★ 本檔**不擋開席**:任何失敗只在 stderr 說一句;aib 端用 `|| true` 接,但那一句要印。
★ 不記 prompt、不記憑證;note 只准短字串。

CLI:
  open_attempt.py start   --home DIR --provider P --role R [--pressed-ms MS] [--new]   → stdout: attempt_id
  open_attempt.py mark    --home DIR --attempt ID --event E [--window @N] [--note S]
  open_attempt.py watch   --home DIR --attempt ID --window @N --provider P [--timeout S] [--interval S] [--detach]
  open_attempt.py summary --home DIR [--json]      (或 --all-cabins ROOT)
rc:0 / 2 參數或檔案錯 / 3 watch 逾時或 pane 死(事件已留痕)
"""
import argparse
import fcntl
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SUBDIR = os.path.join("cockpit", "open-attempts")
EVENTS = ("pressed", "start", "cabin_ready", "window_built", "switch",
          "provider_first_frame", "can_type", "ready", "pane_dead", "watch_timeout",
          # 09-14(the maintainer「先開席、右方顯示完成才載入下一步」):三個不改階段的事件。
          #   right_painted    = 右欄 ai-rowmap.json 第一次出現這個窗(note 帶 waited_ms 或 timeout)
          #   right_painted_ok = 那一列五項全對(open_right_check.py);note 帶 waited_ms,或 timeout_s=N missing=…,或 right_pane_off
          #   ensure_start / ensure_done = cockpit-full.sh --ensure 起訖;艙已在時它排在 right_painted_ok 之後
          #   menu_shown = 開席單三選一選單跳出(第三包,先登記事件名)
          #   menu_choice = 選了哪一項(note=1|2|3|timeout)
          "right_painted", "right_painted_ok", "ensure_start", "ensure_done", "menu_shown", "menu_choice")
DEFAULT_WATCH_TIMEOUT_S = 600
DEFAULT_WATCH_INTERVAL_S = 0.5
PANE_DEAD_POLLS = 5
NOTE_MAX = 120


def attempts_dir(home):
    return os.path.join(home, SUBDIR)


def attempt_path(home, attempt_id):
    if not attempt_id or "/" in attempt_id or attempt_id.startswith("."):
        raise ValueError("attempt id 不合法:%r" % (attempt_id,))
    return os.path.join(attempts_dir(home), attempt_id + ".jsonl")


def now_pair():
    return int(time.time() * 1000), int(time.monotonic() * 1000)


def append_event(home, attempt_id, event, provider=None, role=None, window=None,
                 note=None, wall_ms=None, mono_ms=None):
    """追加一行。flock 獨佔:aib 主鏈與脫離的 watch 會同時寫同一檔。"""
    if event not in EVENTS:
        raise ValueError("未知事件 %r(合法:%s)" % (event, ",".join(EVENTS)))
    w, m = now_pair()
    row = {"attempt": attempt_id, "event": event,
           "wall_ms": int(wall_ms) if wall_ms is not None else w,
           "mono_ms": int(mono_ms) if mono_ms is not None else m,
           "provider": provider, "role": role, "window": window, "pid": os.getpid()}
    if note:
        row["note"] = str(note)[:NOTE_MAX]
    path = attempt_path(home, attempt_id)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return row


def mark_ready_for_window(home, window, note=None):
    """生產消費者:seat_onboard 的回執守望看到「已就緒」時,把 ready 記到**這個窗最近一次**開席的 attempt。
    找法:attempts 目錄裡含 window 事件列、且還沒有 ready 的最新一份。找不到回 None(老席/沒碼表的開席),不 raise。"""
    if not home or not window:
        return None
    d = attempts_dir(home)
    if not os.path.isdir(d):
        return None
    best = None   # (那個窗最後一次被建好的 wall_ms, attempt_id, provider)
    for fn in os.listdir(d):
        if not fn.endswith(".jsonl"):
            continue
        rows = read_events(os.path.join(d, fn))
        wins = [r for r in rows if r.get("window") == str(window)]
        if not wins or any(r["event"] == "ready" for r in rows):
            continue
        # 「最近一次」用事件時間,不用檔名排序(檔名不保證可比:名字排序會把 older-… 排在日期前面)。
        latest = max(int(r.get("wall_ms") or 0) for r in wins)
        prov = next((r.get("provider") for r in rows if r.get("provider")), None)
        if best is None or latest > best[0]:
            best = (latest, fn[:-6], prov)
    if best is None:
        return None
    return append_event(home, best[1], "ready", best[2], window=window, note=note)


def read_events(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("event") in EVENTS:
                rows.append(d)
    return rows


# ---- start / mark -----------------------------------------------------------

def cmd_start(args):
    attempt_id = args.attempt or ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]))
    if args.pressed_ms is not None:
        # 按下 Enter 的 wall 時刻由 aib 在第一行量(它沒有 monotonic);mono 用「現在減去 wall 差」推回去,
        # 兩個時鐘在同一台機器同一次開機內同速,誤差是那幾十毫秒的 python 啟動,可接受。
        w, m = now_pair()
        append_event(args.home, attempt_id, "pressed", args.provider, args.role,
                     wall_ms=args.pressed_ms, mono_ms=m - (w - int(args.pressed_ms)))
    append_event(args.home, attempt_id, "start", args.provider, args.role,
                 note=("new-cabin" if args.new else None))
    print(attempt_id)
    return 0


def cmd_mark(args):
    append_event(args.home, args.attempt, args.event, args.provider, args.role,
                 window=args.window, note=args.note)
    return 0


# ---- watch ------------------------------------------------------------------

BOUND_TMUX = ""   # watch 子行程由 --tmux-bin 綁定;不走環境變數(艙導向名字有單源,不新增)


class TmuxGone(RuntimeError):
    """綁定的 tmux 可執行檔不見了(治具收掉、或路徑失效)——守望結束,不改用別支 tmux。"""


def _tmux_bin():
    """守望用的 tmux **在起跑那一刻綁定成絕對路徑**,之後不再走 PATH。

    09-11 b21 真紅:守望是脫離行程,呼叫端(測試治具)收掉假 tmux 之後它還在輪詢,
    `tmux` 這個名字就解析到 PATH 上下一支——真的那支,對一個不存在的艙 socket
    下 capture-pane。對活艙的呼叫次數判準看的是 argv,不看有沒有出事。
    """
    if BOUND_TMUX:
        return BOUND_TMUX
    named = os.environ.get("AIB_DM_TMUX") or "tmux"
    found = shutil.which(named) if not os.path.isabs(named) else named
    return found or named


def _tmux_capture(window):
    hub = os.environ.get("EZ2_AIHUB_SOCKET") or "aihub"
    binary = _tmux_bin()
    cmd = [binary, "-L", hub, "capture-pane", "-p", "-t", str(window)]
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL)
    except OSError:
        raise TmuxGone(binary)
    return proc.stdout if proc.returncode == 0 else None


def _can_type(screen, provider):
    """可打字=畫面尾端有那一家的輸入提示行。判準沿用 composer_state.find_composer_line(單源),
    它認 ❯(claude/grok)與 ›(codex);找不到模組時退回只認這兩個字元,並在 stderr 說一句。"""
    try:
        import composer_state
        # 09-14(the maintainer:copilot 席碼表量不到「能打字」,今天四席全 600 秒逾時):copilot 沒有提示字元,
        #   它的輸入框是畫框(╻▄▄…/╹▀▀…),composer_state 早有 find_copilot_box,這裡一直沒接。
        if provider == "copilot":
            box = composer_state.find_copilot_box(screen)
            return bool(box and box[0])
        # ★ 它回的是 (行, 行號) 或 (None, None)——**永遠是 tuple**,拿整個回傳值判 `is not None`
        #   會把「載入中…」也判成可打字(第一版就是這樣錯的,真畫面全 True 而看不出來)。
        line, _lineno = composer_state.find_composer_line(screen, provider)
        return line is not None
    except Exception as err:  # noqa: BLE001
        print("open_attempt: composer_state 不可用(%s),退回字元判準" % type(err).__name__, file=sys.stderr)
        tail = [ln for ln in screen.splitlines() if ln.strip()][-6:]
        return any(ln.lstrip().startswith(("❯", "›")) for ln in tail)


def watch_loop(home, attempt_id, window, provider, timeout_s, interval_s,
               capture=_tmux_capture, sleep=time.sleep, clock=time.monotonic,
               dead_polls=PANE_DEAD_POLLS):
    """→ "can_type" | "watch_timeout" | "pane_dead"。first_frame 在第一次看到非空畫面時記。"""
    deadline = clock() + max(0, timeout_s)
    misses = 0
    first_frame_seen = False
    while True:
        try:
            text = capture(window)
        except TmuxGone:
            # 綁定的 tmux 不見了 = 呼叫端的世界已經收掉(治具 teardown、艙已拆)。
            # 這裡**不落任何事件**:home 可能正在被刪,寫下去會把目錄重新長出來、
            # 讓 rmtree 撞「Directory not empty」。安靜結束,rc 由呼叫端讀。
            return "tmux_gone"
        if text is None:
            misses += 1
            if dead_polls > 0 and misses >= dead_polls:
                append_event(home, attempt_id, "pane_dead", provider, window=window)
                return "pane_dead"
        else:
            misses = 0
            if not first_frame_seen and text.strip():
                first_frame_seen = True
                append_event(home, attempt_id, "provider_first_frame", provider, window=window)
            if first_frame_seen and _can_type(text, provider):
                append_event(home, attempt_id, "can_type", provider, window=window)
                return "can_type"
        if clock() >= deadline:
            append_event(home, attempt_id, "watch_timeout", provider, window=window,
                         note="timeout_s=%s first_frame=%s" % (timeout_s, first_frame_seen))
            return "watch_timeout"
        sleep(max(0.05, interval_s))


def cmd_watch(args):
    if args.detach:
        cmd = [sys.executable, os.path.abspath(__file__), "watch", "--home", args.home,
               "--attempt", args.attempt, "--window", args.window, "--provider", args.provider,
               "--timeout", str(args.timeout), "--interval", str(args.interval)]
        cmd += ["--tmux-bin", _tmux_bin()]   # 起跑那一刻綁定成絕對路徑,脫離後不再走 PATH
        try:
            subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 0
        except OSError as err:
            print("open_attempt: watch 起不來(%s)——這一次開席沒有 can_type 讀數" % err, file=sys.stderr)
            return 2
    global BOUND_TMUX
    if getattr(args, "tmux_bin", None):
        BOUND_TMUX = args.tmux_bin
    why = watch_loop(args.home, args.attempt, args.window, args.provider, args.timeout, args.interval)
    return 0 if why == "can_type" else 3


# ---- summary ----------------------------------------------------------------

def deltas(rows):
    """一份 attempt → {格名: 秒};缺事件的格不出現(量不到就不給數字)。"""
    by = {}
    for r in rows:
        by.setdefault(r["event"], r["mono_ms"])   # 同事件取第一次
    out = {}
    def d(a, b, name):
        if a in by and b in by:
            out[name] = round((by[b] - by[a]) / 1000.0, 3)
    d("pressed", "window_built", "pressed→window_built")
    d("window_built", "switch", "window_built→switch")
    d("switch", "provider_first_frame", "switch→first_frame")
    d("switch", "can_type", "switch→can_type")
    d("pressed", "can_type", "pressed→can_type")
    d("pressed", "right_painted", "pressed→right_painted")
    d("pressed", "right_painted_ok", "pressed→right_painted_ok")
    d("window_built", "ensure_start", "window_built→ensure_start")
    d("switch", "ready", "switch→ready")
    for e in ("pane_dead", "watch_timeout"):
        if e in by:
            out["ended"] = e
    return out


ORDER_RULE = (("window_built", "right_painted_ok"), ("right_painted_ok", "ensure_start"),
              ("ensure_start", "menu_shown"))


def order_violations(rows):
    """09-14 順序律(pm 18:0x 裁):艙已在時(ensure_start 帶 note=deferred)必須
    window_built < right_painted_ok < ensure_start < menu_shown(menu_shown 可缺:未確認的席不跳選單)。
    回違規清單 [(前, 後, 差毫秒)];沒有 deferred 的 ensure_start(新艙 cabin-first)不適用,回空。
    ★ 只看 mono_ms(同一次開機內可比),同事件取第一次。"""
    by, note = {}, {}
    for r in rows:
        if not isinstance(r, dict) or r.get("event") not in EVENTS:
            continue
        by.setdefault(r["event"], r.get("mono_ms"))
        note.setdefault(r["event"], r.get("note"))
    if note.get("ensure_start") != "deferred":
        return []
    out = []
    for a, b in ORDER_RULE:
        # 同一毫秒不算違規(連續兩筆 append 常落在同一 ms);只有「後者嚴格早於前者」才是。
        if a in by and b in by and isinstance(by[a], int) and isinstance(by[b], int) and by[b] < by[a]:
            out.append((a, b, by[b] - by[a]))
    if "right_painted_ok" not in by and "ensure_start" in by:
        out.append(("right_painted_ok", "ensure_start", None))   # 沒等右欄判完就跑了 ensure
    return out


def _pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 3)


def summarize(homes):
    per = []
    for home in homes:
        d = attempts_dir(home)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".jsonl"):
                rows = read_events(os.path.join(d, fn))
                if rows:
                    prov = next((r.get("provider") for r in rows if r.get("provider")), None)
                    entry = {"attempt": fn[:-6], "provider": prov, **deltas(rows)}
                    viol = order_violations(rows)
                    if viol:
                        entry["order_violations"] = ["%s→%s" % (a, b) for a, b, _ in viol]
                    per.append(entry)
    cells = {}
    for a in per:
        for k, v in a.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.setdefault(k, []).append(v)
    stats = {k: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95),
                 "max": max(v)} for k, v in cells.items()}
    return {"attempts": per, "cells": stats,
            "order_violations": sum(1 for a in per if a.get("order_violations"))}


def cmd_summary(args):
    homes = [args.home] if args.home else []
    if args.all_cabins:
        root = args.all_cabins
        if os.path.isdir(root):
            homes += [os.path.join(root, n) for n in sorted(os.listdir(root))]
    res = summarize(homes)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if not res["attempts"]:
        print("open_attempt: 沒有任何開席紀錄(量不到,不是 0 秒)")
        return 0
    print("開席碼表:%d 次(母體=%s)" % (len(res["attempts"]), ", ".join(homes)))
    for k, s in res["cells"].items():
        print("  %-26s n=%-3d p50=%6.2fs  p95=%6.2fs  max=%6.2fs" % (k, s["n"], s["p50"], s["p95"], s["max"]))
    print("  順序違規(艙已在卻沒等右欄先畫就跑 ensure):%d 次" % res["order_violations"])
    return 0


# ---- main -------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="開席碼表(O1)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--home", required=True); s.add_argument("--provider", required=True)
    s.add_argument("--role", required=True); s.add_argument("--pressed-ms", type=int); s.add_argument("--new", action="store_true")
    s.add_argument("--attempt")
    m = sub.add_parser("mark"); m.add_argument("--home", required=True); m.add_argument("--attempt", required=True)
    m.add_argument("--event", required=True, choices=EVENTS); m.add_argument("--window"); m.add_argument("--note")
    m.add_argument("--provider"); m.add_argument("--role")
    w = sub.add_parser("watch"); w.add_argument("--home", required=True); w.add_argument("--attempt", required=True)
    w.add_argument("--window", required=True); w.add_argument("--provider", required=True)
    w.add_argument("--timeout", type=float, default=DEFAULT_WATCH_TIMEOUT_S)
    w.add_argument("--interval", type=float, default=DEFAULT_WATCH_INTERVAL_S); w.add_argument("--detach", action="store_true")
    w.add_argument("--tmux-bin", default="")
    u = sub.add_parser("summary"); u.add_argument("--home"); u.add_argument("--all-cabins"); u.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        return {"start": cmd_start, "mark": cmd_mark, "watch": cmd_watch, "summary": cmd_summary}[args.cmd](args)
    except (OSError, ValueError) as err:
        print("open_attempt: %s" % err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
