#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib now` —— 每一席回報「現在的主項目 | 步數 · 進度%」,顯示在輸入框下方 CTX 那一行的上面(the maintainer 2026-10-10)。

    aib now "AIB 停電恢復" 0/5 --new   # 開始新的主項目(啟動時間從現在算)
    aib now "AIB 停電恢復" 2/5    # 做完 2 步 / 共 5 步 ⇒ 進度：40%(由步數算);狀態預設【忙碌中】;啟動時間沿用
    aib now "等 a seat 推版" 4/5 --state reply   # 【等回應】;--state decide =【等你決定】
    aib now                       # 看自己這一席現在掛著什麼
    aib now --clear               # 沒在做事 ⇒ 顯示「📌 等待指派新工作」(從沒跑過 ⇒「📌 尚未回報主項目」)
    aib now --apply-borders [--dry-run]   # 已經開著的 Codex / Grok / Copilot 席補上底部那一行

存放:<狀態根>/state/seat-now/<席位碼>.json(結構化)+ 同名 .line(狀態列直接印的那一行)。
  以席位碼為鍵:/clear、compact 後對話 id 會換,席位碼不換;狀態列從 tmux 窗選項 @aib_seat 拿同一個碼。
  畫在哪(the maintainer 10-10「全部的 ai 席都要」):
    Claude 席  狀態列 statusline-ez2-v215.sh 跑 `aib now --statusline`(印「席位碼␟📌 行␟AI 標誌」,␟ = \\x1f),📌 在 CTX 那一行上面,
               CTX 那一行插 @席位;
    agy 席     自己的狀態列 agy/statusline-ez2-ctx.js 畫 CTX 那一行(標誌 + @席位);它只在有事件時重畫、
               不支援定時重跑 ⇒ 📌 那一行改畫在 hub 窗底部邊框(--border --now-only,tmux 每幾秒重畫);
    Codex / Grok / Copilot 席  狀態列只能選內建項目、放不了自訂字 ⇒ 開席時 hub 窗開底部邊框,
               邊框跑 `seat_now.py --border --window #{window_id} --socket #{socket_path}`。
  每一行帶「更新時間」與「啟動時間」(shown_line);窄視窗換短寫法。
這一席是誰:seat_who.self_seat_uuid()(TMUX_PANE → 窗 id → 艙 → 席位檔交叉驗)→ seat_address.uuid_tail5。
  拿不到就回 2,不猜(寫到別席名下比沒寫更糟)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import atomic_json  # noqa: E402
import seat_address  # noqa: E402
import seats_dir_lib  # noqa: E402

SUBDIR = os.path.join("state", "seat-now")
ITEM_MAX = 60          # 一行要放得下:主項目太長就截
PIN = "📌"

# 狀態欄(the maintainer 10-10「在 logo 後面加一個狀態欄,用顏色區分 [忙碌]/[待指派]/【等回應】之類」
#   「顏色要對應狀態的字意」「忙碌中應該是橙色,待指派改成【我很閒】亮灰色」)。
#   橙 = 忙碌中、黃 = 停下來等別人、紅 = 要你動作(加粗)、亮灰 = 我很閒、紫 = 不知道(沒回報)。
#   等你決定對應訊息佇列 dm kind=decide / 待人處理。
STATES = {
    "busy":       ("忙碌中",   "\x1b[38;5;208m"),
    "reply":      ("等回應",   "\x1b[38;5;220m"),
    "decide":     ("等你決定", "\x1b[1;38;5;196m"),
    "idle":       ("我很閒",   "\x1b[38;5;252m"),
    "unreported": ("未回報",   "\x1b[38;5;141m"),
}
SETTABLE_STATES = ("busy", "reply", "decide")    # idle 走 --clear;unreported 是沒有紀錄,不能設
RESET = "\x1b[0m"
ITEM_SGR = "\x1b[38;5;255m"   # 主項目(標題)白色(the maintainer 10-10「標題用白色」)
STEP_SGR = "\x1b[38;5;40m"    # 步數與進度綠色(the maintainer 10-10「步數和進度改用綠色」)
TIME_SGR = "\x1b[38;5;245m"   # 更新時間 / 啟動時間灰色(the maintainer 10-10「更新時間和啟動時間改用灰色」)


def state_tag(state, color=True):
    label, sgr = STATES.get(state, STATES["busy"])
    tag = "【%s】" % label
    return (sgr + tag + RESET) if color else tag


def now_dir(state_root=None):
    return os.path.join(state_root or seats_dir_lib.state_root()[0], SUBDIR)


SESSION_ENVS = ("AIB_RESUME_SESSION_UUID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
# CODEX_THREAD_ID:Codex 放進每個工具行程的對話 id,= 它席位檔的 session_uuid(dm-ctl codex_thread_env 同一條證據);
#   10-10 實測 Codex 席(a seat)殼裡沒有 TMUX_PANE,也沒有上面兩個 Claude 的變數。


def _seat_records():
    import glob
    root = seats_dir_lib.state_root()[0]
    for f in glob.glob(os.path.join(root, "instances", "*", "seats", "w*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and not rec.get("retired"):
            yield rec


def code_for_session(*uuids):
    """對話編號 ⇒ 席位碼:拿去所有艙的席位檔比 session_uuid,**恰好一席**對得上才用。
    10-10 a seat 實測:有的 Claude 席,Bash 工具起的殼沒有 TMUX / TMUX_PANE,窗號那條路查不到。
    背景對話的對話編號是它自己新開的,對不到任何席 ⇒ 照舊拒收(不會認成啟動它的那一席)。"""
    for u in uuids:
        u = (u or "").strip().lower()
        if not u:
            continue
        hits = {seat_address.code_of(r) for r in _seat_records() if str(r.get("session_uuid") or "").lower() == u}
        hits.discard(None)
        if len(hits) == 1:
            return hits.pop()
    return None


def self_code(environ=None):
    environ = os.environ if environ is None else environ
    try:
        import seat_who
        uuid = seat_who.self_seat_uuid()
    except Exception:          # noqa: BLE001 —— 量不到就回 None,不讓整支掛掉
        uuid = None
    if uuid:
        return seat_address.uuid_tail5(uuid)
    return code_for_session(*(environ.get(k) for k in SESSION_ENVS))


def self_provider(code=None):
    try:
        import seat_who
        wid, sock = seat_who.self_pane_facts()
        rec = seat_who.seat_record_for_window(wid, sock)
    except Exception:          # noqa: BLE001
        rec = None
    if not rec and code:
        rec = next((r for r in _seat_records() if seat_address.code_of(r) == code), None)
    return str((rec or {}).get("provider") or "")


def known_code(code):
    """--seat 自報的碼要真的有席位檔(任一艙、沒退席),否則不收 —— 寫到不存在的席名下,畫面上永遠不會有人看到。"""
    import glob
    if not seat_address.uuid_tail5(code) == code:
        return False
    root = seats_dir_lib.state_root()[0]
    for f in glob.glob(os.path.join(root, "instances", "*", "seats", "w*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and seat_address.code_of(rec) == code and not rec.get("retired"):
            return True
    return False


def logo_of(provider):
    """右欄同一個 AI 標誌(Nerd Font 圖示 + 品牌色,render_lib.render_engine_badge icon 態)。
    the maintainer 10-10「最前面要加上對應的 AI logo,比照右方 ai 席的 logo」。字型缺字 / 沒登記 ⇒ ""(不退字母:後面就是模型名)。"""
    if not provider:
        return ""
    try:
        sys.path.insert(0, os.path.join(HERE, "render"))
        import render_lib as R
        import engine_badge_mode as M
        if not M.badge_inputs(icons=R.ENGINE_ICONS, tty=True)["icon_available"].get(provider):
            return ""
        return R.render_engine_badge(provider, truecolor=True, mode="icon")
    except Exception:          # noqa: BLE001 —— 狀態列每次刷新都跑,畫不出圖就不畫
        return ""


SEP = "\x1f"   # --statusline 欄位分隔:不能用 tab —— bash read 會把連續 tab 併成一個,空的 📌 欄會讓標誌跑到錯的欄


def clean_item(text):
    item = " ".join(str(text or "").split())          # 換行、tab、連續空白收成一格空白:狀態列只有一行
    if len(item) > ITEM_MAX:
        item = item[:ITEM_MAX - 1] + "…"
    return item


STEPS_MAX = 99


def parse_steps(text):
    """「做完幾步/總共幾步」⇒ (done, total);不合(只給一個數字、0 步、做完比總數多)⇒ None。
    the maintainer 10-10「改成步數,後面再依步數給出客觀的 %數」:百分比不再自己填 —— 以前各席憑感覺估
    (a seat 自己填 85%,照步數其實是 40%)。"""
    try:
        a, b = str(text).strip().split("/")
        done, total = int(a), int(b)
    except (TypeError, ValueError):
        return None
    return (done, total) if 1 <= total <= STEPS_MAX and 0 <= done <= total else None


def progress_pct(done, total):
    return int(round(100.0 * done / total))


def _stamp(epoch, now=None, short=False):
    """完整寫法一律 MM-DD HH:MM;短寫法今天只寫 HH:MM,跨天才加日期。"""
    if short:
        now = now if now is not None else time.time()
        same = time.strftime("%m-%d", time.localtime(now)) == time.strftime("%m-%d", time.localtime(epoch))
        return time.strftime("%H:%M" if same else "%m-%d %H:%M", time.localtime(epoch))
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


def started_text(started_at):
    """啟動時間(the maintainer 10-10)。舊紀錄沒有 ⇒「不明」,不編一個時間。"""
    return "啟動時間：%s" % (_stamp(started_at) if started_at else "不明")


def times_text(at, started_at):
    """| 更新時間:… | 啟動時間:…(the maintainer 10-10 定的順序)。更新時間就是新不新的證據,不再另標「N 小時前」。"""
    return "更新時間：%s | %s" % (_stamp(at) if at else "不明", started_text(started_at))


def parse_started(text, now=None):
    """--started "10-10 16:04" / "16:04"(今天)⇒ epoch;不合 ⇒ None。"""
    now = now if now is not None else time.time()
    t = (text or "").strip()
    year = time.localtime(now).tm_year
    for fmt, prefix in (("%Y-%m-%d %H:%M", "%d-" % year), ("%Y-%m-%d %H:%M", time.strftime("%Y-%m-%d ", time.localtime(now)))):
        try:
            return int(time.mktime(time.strptime(prefix + t, fmt)))
        except ValueError:
            continue
    return None


def steps_text(done, total):
    """2/5 STEP > 進度:40%(the maintainer 10-10 定的寫法)。"""
    return "%d/%d STEP > 進度:%d%%" % (done, total, progress_pct(done, total))


def line_of(item, done, total, state="busy", color=False, started_at=None, at=None):
    """📌【狀態】主項目 | 2/5 STEP > 進度:40% | 更新時間:10-10 17:05 | 啟動時間:10-10 16:04"""
    return "%s%s%s | %s | %s" % (PIN, state_tag(state, color), item, steps_text(done, total),
                                 times_text(at, started_at))


def legacy_line_of(item, pct, state="busy", color=False):
    """步數上線前留下的紀錄只有自己估的 %:照實標「自估」,不裝成照步數算的。"""
    return "%s%s%s | 進度：%d%%(自估) | %s" % (PIN, state_tag(state, color), item, pct, times_text(None, None))


def write(code, item, done, total, *, state="busy", state_root=None, now=None, started_at=None, new=False):
    """啟動時間:加 new(aib now --new)才重新計時;改主項目文字、步數、狀態都沿用(the maintainer 10-10「--new 才重新計時」——
    以前一改字就當新項目,a seat 自己改字就把 16:04 洗成 17:46)。上一筆是【我很閒】(--clear)⇒ 接的一定是新工作,
    自動重新計時。--started 可更正。舊紀錄沒有這欄 ⇒ 用它上次回報的時間(最早量得到的),不是「現在」。"""
    d = now_dir(state_root)
    os.makedirs(d, exist_ok=True)
    at = int(now if now is not None else time.time())
    if started_at is None:
        prev = read(code, state_root=state_root) or {}
        if new or not prev or prev.get("idle"):
            started_at = at
        else:
            started_at = prev.get("started_at") or prev.get("at") or at
    rec = {"item": item, "steps_done": done, "steps_total": total, "pct": progress_pct(done, total),
           "state": state if state in SETTABLE_STATES else "busy",
           "started_at": int(started_at), "at": at, "code": code}
    atomic_json.write_json(os.path.join(d, code + ".json"), rec)
    tmp = os.path.join(d, ".%s.line.%d" % (code, os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(line_of(item, done, total, rec["state"], started_at=rec["started_at"], at=rec["at"]) + "\n")
    os.replace(tmp, os.path.join(d, code + ".line"))
    return rec


def read(code, *, state_root=None):
    try:
        with open(os.path.join(now_dir(state_root), code + ".json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


IDLE_TEXT = "等待指派新工作"         # aib now --clear:這一席自己說沒事做(狀態欄【我很閒】)
UNREPORTED_TEXT = "尚未回報主項目"    # 從沒跑過 aib now:不知道在做什麼 —— 不能說成「沒事做」(the maintainer 10-10 問 a seat)


def clear(code, *, state_root=None, now=None):
    """這一席沒在做事:留一筆 idle 紀錄(畫成「📌 等待指派新工作」),不是刪掉 ——
    刪掉會跟「從沒回報過」同形,而那兩種的意思相反(一個是閒著、一個是不知道)。"""
    d = now_dir(state_root)
    os.makedirs(d, exist_ok=True)
    rec = {"idle": True, "at": int(now if now is not None else time.time()), "code": code}
    atomic_json.write_json(os.path.join(d, code + ".json"), rec)
    tmp = os.path.join(d, ".%s.line.%d" % (code, os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%s%s%s\n" % (PIN, state_tag("idle", False), IDLE_TEXT))
    os.replace(tmp, os.path.join(d, code + ".line"))
    return True


BEAT_EVERY_S = 60     # 心跳最多一分鐘寫一次:Claude 狀態列每 15 秒重跑,不必每次都動磁碟


def beat(code, src, *, state_root=None, now=None):
    """心跳:<碼>.beat 寫 {"at", "src", "code"};跟主項目的 at(那一席自己跑 aib now 才變)分開。
    the maintainer 10-11「守望官心跳照你建議另寫心跳欄」:用回報時間當心跳,安靜做事的席 30 分鐘就被判離線。
    src = statusline(Claude 狀態列定時重跑 ⇒ 程式活著才會跳)/ border(tmux 底框重畫 ⇒ 證明窗還在,較弱)。
    寫不進去就算了:心跳不能讓狀態列掛掉。"""
    at = int(now if now is not None else time.time())
    path = os.path.join(now_dir(state_root), code + ".beat")
    try:
        if at - int(os.path.getmtime(path)) < BEAT_EVERY_S:
            return False
    except OSError:
        pass
    try:
        os.makedirs(now_dir(state_root), exist_ok=True)
        atomic_json.write_json(path, {"at": at, "src": src, "code": code})
        os.utime(path, (at, at))
    except (OSError, ValueError):
        return False
    return True


def beat_age(code, *, state_root=None, now=None):
    """(距上次心跳幾秒, 來源) 或 None(從沒跳過)。守望官「在線」判斷用這個,不用主項目的 at。"""
    try:
        with open(os.path.join(now_dir(state_root), code + ".beat"), encoding="utf-8") as fh:
            rec = json.load(fh)
        at = int(rec["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return max(0, int(now if now is not None else time.time()) - at), str(rec.get("src") or "")


def live_codes(records=None):
    """還在的席:任一艙有席位檔、沒退席(retired)、沒收工(exited_at_epoch)。"""
    recs = _seat_records() if records is None else records
    codes = {seat_address.code_of(r) for r in recs
             if isinstance(r, dict) and not r.get("retired") and not r.get("exited_at_epoch")}
    codes.discard(None)
    return codes


def sweep_dead(*, state_root=None, records=None):
    """掃掉已經不在的席的 📌 紀錄(<碼>.json / .line),回被掃掉的碼。
    10-11 a seat 抓到:10-10 開來驗底框的 26c93 / 4601e / dd4e1 關掉後紀錄還在,艦長室網頁把死席當【忙碌中】。
    ★ 一席都認不到(state root 讀錯、席位檔目錄不見)⇒ 一筆都不掃:寧可留殘影,不把全艦的紀錄洗掉。"""
    live = live_codes(records)
    if not live:
        return []
    d = now_dir(state_root)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    gone = sorted({n.rsplit(".", 1)[0] for n in names
                   if n.endswith((".json", ".line", ".beat")) and not n.startswith(".")
                   and seat_address.uuid_tail5(n.rsplit(".", 1)[0]) == n.rsplit(".", 1)[0]} - live)
    for c in gone:
        for ext in (".json", ".line", ".beat"):
            try:
                os.remove(os.path.join(d, c + ext))
            except OSError:
                pass
    return gone


STALE_S = 3600


def _parts(code, rec, color, now):
    """(開頭 📌【狀態】, 主項目, 完整尾巴, 短尾巴);短尾巴給窄視窗(the maintainer 10-10「窄視窗改成短的寫法」)。"""
    if not rec:
        return "%s%s" % (PIN, state_tag("unreported", color)), UNREPORTED_TEXT, "", ""
    if rec.get("idle"):
        return "%s%s" % (PIN, state_tag("idle", color)), IDLE_TEXT, "", ""
    st = rec.get("state") or "busy"
    head = "%s%s" % (PIN, state_tag(st, color))   # 📌【狀態】主項目:中間不留空(the maintainer 10-10)
    item = rec["item"]
    started, at = rec.get("started_at"), rec.get("at")
    tint = (lambda x: TIME_SGR + x + RESET) if color else (lambda x: x)
    if rec.get("steps_total"):
        done, total = int(rec["steps_done"]), int(rec["steps_total"])
        pct = progress_pct(done, total)
        green = (lambda x: STEP_SGR + x + RESET) if color else (lambda x: x)
        full = " | %s | %s" % (green(steps_text(done, total)), tint(times_text(at, started)))
        short = " | %s" % green("%d/%d > %d%%" % (done, total, pct))
    else:
        pct = int(rec.get("pct") or 0)
        full = " | 進度：%d%%(自估) | %s" % (pct, tint(times_text(at, None)))
        short = " | %d%%(自估)" % pct
    stamps = []
    if at:
        stamps.append("更新 %s" % _stamp(at, now, short=True))
    if started:
        stamps.append("啟動 %s" % _stamp(started, now, short=True))
    if stamps:
        short += " | " + tint(" | ".join(stamps))
    return head, item, full, short


def shown_line(code, *, state_root=None, now=None, color=True, width=None):
    """畫面上那一行:📌【狀態】主項目 | 2/5 STEP > 進度:40% | 更新時間:10-10 17:05 | 啟動時間:10-10 16:04。
    沒回報 ⇒ 📌【未回報】尚未回報主項目;--clear ⇒ 📌【我很閒】等待指派新工作。
    新舊看「更新時間」。沒有席位碼 ⇒ ""(不是席,不畫)。
    width(那一席視窗寬):完整那行放不下 ⇒ 短寫法(📌【狀態】主項目 | 2/5 > 40% | 更新 17:05 | 啟動 16:04);
    還放不下 ⇒ 主項目截短加「…」,步數、% 與啟動時間不截。"""
    if not code:
        return ""
    now = now if now is not None else time.time()
    rec = read(code, state_root=state_root)
    try:
        head, item, full, short = _parts(code, rec, color, now)
    except (KeyError, TypeError, ValueError):
        head, item, full, short = _parts(code, None, color, now)
    stale = ""   # 10-10 改由「更新時間」表達新舊(the maintainer 定的格式),不再另標「N 小時前」
    paint = (lambda x: ITEM_SGR + x + RESET) if color else (lambda x: x)
    line = "%s%s%s%s" % (head, paint(item), full, stale)
    if not width:
        return line
    sys.path.insert(0, os.path.join(HERE, "render"))
    from render_lib import visible_width, trunc
    if visible_width(line) <= width:
        return line
    line = "%s%s%s%s" % (head, paint(item), short, stale)
    if visible_width(line) <= width:
        return line
    room = width - visible_width("%s%s%s" % (head, short, stale))
    item = trunc(item, max(4, room))
    return "%s%s%s%s" % (head, paint(item), short, stale)


def _self_width():
    """這一席視窗的寬,扣掉狀態列左邊的縮排與右邊留白;量不到 ⇒ None(照完整那行畫)。"""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        import subprocess
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_width}"],
                             capture_output=True, text=True, timeout=2, stdin=subprocess.DEVNULL).stdout.strip()
        return max(20, int(out) - 4) if out.isdigit() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def ansi_to_tmux(text):
    """終端機顏色碼 ⇒ tmux 樣式:底部邊框是 tmux 畫的,#() 吐出來的 \\x1b[…m 它不認,會整串印出來。"""
    def conv(m):
        parts = m.group(1).split(";") if m.group(1) else ["0"]
        if parts in (["0"], [""]):
            return "#[default]"
        bold = ""
        if parts and parts[0] == "1":
            bold, parts = ",bold", parts[1:]
        if len(parts) >= 3 and parts[:2] == ["38", "5"] and bold:
            return "#[fg=colour%d%s]" % (int(parts[2]), bold)
        if len(parts) >= 5 and parts[:2] == ["38", "2"]:
            return "#[fg=#%02x%02x%02x]" % tuple(int(x) for x in parts[2:5])
        if len(parts) >= 3 and parts[:2] == ["38", "5"]:
            return "#[fg=colour%d]" % int(parts[2])
        return ""
    return _SGR_RE.sub(conv, text)


def code_for_window(wid, sock):
    try:
        import seat_who
        uuid = seat_who.seat_uuid_for_window(wid, sock)
    except Exception:          # noqa: BLE001
        uuid = None
    return seat_address.uuid_tail5(uuid) if uuid else None


def _gauge(wid, sock):
    """那一窗現任 session 的 CTX 量測(~/.ez2/ctx/<session>.pct);量不到回 {}。/clear 後換源同右欄(ctx_by_seat)。"""
    try:
        import seat_who
        import ctx_by_seat
        uuid = seat_who.seat_uuid_for_window(wid, sock)
        if not uuid:
            return {}
        inst = seat_who.instance_of_socket(sock)
        live, _rebound, _note = ctx_by_seat.resolve_live_session(uuid, instance=inst, window=wid)
        with open(os.path.join(os.path.expanduser("~/.ez2/ctx"), "%s.pct" % live), encoding="utf-8") as fh:
            g = json.load(fh)
        return g if isinstance(g, dict) else {}
    except Exception:          # noqa: BLE001 —— 邊框每幾秒跑一次,量不到就少畫,不能噴錯
        return {}


def border_line(wid, sock, *, provider="", state_root=None, now=None, width=None, now_only=False):
    """Codex / Grok / Copilot 席(自己的狀態列放不了自訂字)的底部邊框:模型 | @席位 | 💧 Ez2 CTX n% · 📌【狀態】主項目 | …。"""
    code = code_for_window(wid, sock)
    if not code:
        return ""
    beat(code, "border", state_root=state_root, now=now)
    if now_only:
        return ansi_to_tmux(shown_line(code, state_root=state_root, now=now, color=True, width=width))
    sys.path.insert(0, os.path.join(HERE, "render"))
    import panel_chrome as PC
    g = _gauge(wid, sock)
    pct = g.get("pct")
    try:
        pct = int(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct = None
    tier = g.get("tier") or PC._ctx_tier(pct)
    icon = PC._CTX_ICONS.get(tier, "❓")
    model = g.get("model") or provider or "?"
    ctx = "%s Ez2 CTX ?" % icon if pct is None else "%s Ez2 CTX %d%% %s" % (icon, pct, PC._ctx_bar(pct, tier))
    logo = logo_of(provider)
    ctx_part = "%s%s | @%s | %s" % (logo + "  " if logo else "", model, code, ctx)
    room = None
    if width:
        sys.path.insert(0, os.path.join(HERE, "render"))
        from render_lib import visible_width
        room = max(20, width - visible_width(ctx_part) - 6)
    parts = [ctx_part, shown_line(code, state_root=state_root, now=now, color=True, width=room)]   # 同狀態列:CTX 在前   # 空兩格:圖示畫得比一格寬
    return ansi_to_tmux("  ·  ".join(x for x in parts if x))


STATUSLINE_ENGINES = ("claude",)   # Claude 由自己的狀態列畫(會定時重跑),不開邊框
NOW_ONLY_ENGINES = ("agy",)         # agy 的狀態列只在有事件時重畫、不支援定時重跑(1.3.3 實查)⇒ 邊框只畫 📌,
                                    # CTX 那一行留在它自己的狀態列(the maintainer 10-10「agy 也加底部那一行」)


def border_format(provider, python=None):
    """hub 窗底部邊框的 tmux 格式(開席 aib_apply_now_border 與 apply_borders 共用這一份)。"""
    import shlex
    import shutil
    # PATH 上的 python3(例 /opt/homebrew/bin/python3)是穩定的連結;sys.executable 是 python3.14 實體,升版就失效
    py = python or shutil.which("python3") or sys.executable
    extra = " --now-only" if provider in NOW_ONLY_ENGINES else ""
    return " #(%s %s --border%s --window #{window_id} --socket #{socket_path} --width #{pane_width} --provider %s)" % (
        shlex.quote(py), shlex.quote(os.path.abspath(__file__)), extra, shlex.quote(provider))


def apply_borders(*, hubs=None, tmux=None, code_for=None, dry_run=False, out=sys.stdout):
    """已經開著的 Codex / Grok / Copilot / agy 席補上底部邊框(the maintainer 10-10「要,做補上現有席的指令」「agy 也加」)。
    只動查得到席位檔的窗(大廳、watch 窗不動);Claude 不動(它的狀態列會定時重跑)。回 (補了幾席, 量不到幾艙)。"""
    import seat_pool
    tmux = tmux or seat_pool._tmux
    code_for = code_for or code_for_window
    hubs = seat_pool.live_cabins() if hubs is None else hubs
    done = unreachable = 0
    for hub in hubs:
        listing = tmux(hub, "list-windows", "-a", "-F", "#{window_id}\t#{@aib_provider}")
        if listing is None:
            out.write("✗ %s 問不到(tmux 沒回話),跳過\n" % hub)
            unreachable += 1
            continue
        for row in listing.splitlines():
            wid, _, prov = row.partition("\t")
            prov = prov.strip()
            if not wid or not prov or prov in STATUSLINE_ENGINES:
                continue
            code = code_for(wid, hub)
            if not code:
                continue
            if not dry_run:
                ok = tmux(hub, "set-option", "-w", "-t", wid, "pane-border-status", "bottom") is not None
                ok = ok and tmux(hub, "set-option", "-w", "-t", wid, "pane-border-format",
                                 border_format(prov)) is not None
            else:
                ok = True
            out.write("%s %s %s @%s(%s)%s\n" % ("✅" if ok else "✗", hub, wid, code, prov,
                                                " —— dry-run,沒動" if dry_run else ""))
            done += ok
    out.write("補了 %d 席%s;問不到 %d 艙\n" % (done, "(dry-run)" if dry_run else "", unreachable))
    return done, unreachable


def main(argv=None, *, code=None, out=sys.stdout, err=sys.stderr, state_root=None):
    ap = argparse.ArgumentParser(prog="aib now", description="回報這一席現在的主項目與步數(進度 % 由步數算),顯示在 CTX 那一行上面")
    ap.add_argument("item", nargs="?", help="主項目(一句話)")
    ap.add_argument("steps", nargs="?", help="做完幾步/總共幾步,例 2/5(進度 %% 由它算,不自己填)")
    ap.add_argument("--clear", action="store_true", help="沒在做事 ⇒ 【我很閒】")
    ap.add_argument("--new", action="store_true",
                    help="開始一個新的主項目 ⇒ 啟動時間重新計時(不加:改字、改步數、改狀態都沿用原本的啟動時間)")
    ap.add_argument("--started", metavar="'MM-DD HH:MM'", help="更正啟動時間")
    ap.add_argument("--state", choices=SETTABLE_STATES, default="busy",
                    help="busy=【忙碌中】(預設)/ reply=【等回應】(等別席回信或交件)/ decide=【等你決定】")
    ap.add_argument("--seat", metavar="席位碼", default="",
                    help="認不出自己是哪一席時(例:Claude 背景對話沒有 TMUX_PANE)自己報席位碼;要有那一席的席位檔")
    ap.add_argument("--statusline", action="store_true", help=argparse.SUPPRESS)   # 狀態列用:印「席位碼<TAB>📌 行」
    ap.add_argument("--border", action="store_true", help=argparse.SUPPRESS)       # Codex 等席底部邊框用
    ap.add_argument("--now-only", action="store_true", help=argparse.SUPPRESS)     # agy:邊框只畫 📌
    ap.add_argument("--session", default="", help=argparse.SUPPRESS)              # 狀態列遞來的對話編號(沒有 TMUX_PANE 時用)
    ap.add_argument("--window", default="", help=argparse.SUPPRESS)
    ap.add_argument("--socket", default="", help=argparse.SUPPRESS)
    ap.add_argument("--provider", default="", help=argparse.SUPPRESS)
    ap.add_argument("--width", type=int, default=0, help=argparse.SUPPRESS)   # 視窗寬(邊框由 tmux 帶;狀態列自己量)
    ap.add_argument("--border-format", metavar="PROVIDER", help=argparse.SUPPRESS)   # 開席時 aib 拿格式用
    ap.add_argument("--apply-borders", action="store_true",
                    help="已經開著的 Codex / Grok / Copilot 席補上底部那一行(不用重開)")
    ap.add_argument("--dry-run", action="store_true", help="跟 --apply-borders 一起用:只列出會補哪些席")
    ap.add_argument("--sweep", action="store_true",
                    help="掃掉已經不在的席(沒有席位檔、已退席或已收工)留下的 📌 紀錄;每次 aib now 回報也會順手掃")
    ap.add_argument("--beat-age", metavar="席位碼",
                    help="那一席離上次心跳幾秒(Claude 狀態列 / 底框定時跳,跟主項目的更新時間分開);從沒跳過印「沒有心跳」、回 1")
    args = ap.parse_args(argv)
    if args.beat_age:
        got = beat_age(args.beat_age.strip().lstrip("@").lower(), state_root=state_root)
        if got is None:
            out.write("沒有心跳\n")
            return 1
        out.write("%d %s\n" % got)
        return 0
    if args.sweep:
        gone = sweep_dead(state_root=state_root)
        out.write(("已掃掉 %d 席的 📌 紀錄:%s\n" % (len(gone), " ".join("@" + c for c in gone))) if gone
                  else "沒有要掃的 📌 紀錄\n")
        return 0
    if args.border_format:
        out.write(border_format(args.border_format) + "\n")
        return 0
    if args.apply_borders:
        _done, bad = apply_borders(dry_run=args.dry_run, out=out)
        return 1 if bad else 0
    if args.border:
        out.write(border_line(args.window, args.socket, provider=args.provider, state_root=state_root,
                              width=args.width or None, now_only=args.now_only))
        return 0
    if args.statusline:
        c = code or self_code() or (code_for_session(args.session) if args.session else None) or ""
        if c:
            beat(c, "statusline", state_root=state_root)
        prov = args.provider or (self_provider(c) if c else "")
        w = args.width or (_self_width() if c else None)
        out.write(SEP.join([c, shown_line(c, state_root=state_root, width=w) if c else "",
                            logo_of(prov) if c else ""]) + "\n")
        return 0
    if args.seat and not code:
        want = args.seat.strip().lstrip("@").lower()
        if not known_code(want):
            err.write("aib now: 找不到席位碼 @%s 的席位檔,不寫。\n" % want)
            return 2
        code = want
    code = code or self_code()
    if not code:
        # 10-10 實例:Claude 的背景對話掛在共用背景服務底下,沒有 TMUX_PANE;往上找上層程式會認成
        #   啟動那個服務的別席(a seat 的背景對話被認成 a seat)⇒ 不猜,請它自己報碼。
        err.write("aib now: 量不到這一席是誰(沒有 TMUX_PANE、對話編號也對不到唯一一席,例如背景對話)。"
                  "自己報碼:aib now --seat <你的席位碼> \"主項目\" 做完幾步/總共幾步,不寫。\n")
        return 2
    sweep_dead(state_root=state_root)          # 順手掃:回報的席越多,死席殘影留得越短
    if args.clear:
        clear(code, state_root=state_root)
        out.write("@%s %s\n" % (code, shown_line(code, state_root=state_root, color=False)))
        return 0
    if args.item is None:
        rec = read(code, state_root=state_root)
        if not rec:
            out.write("@%s %s。設定:aib now \"主項目\" 做完幾步/總共幾步 [--state reply|decide]\n"
                      % (code, shown_line(code, state_root=state_root, color=False)))
            return 0
        out.write("%s(%s 更新)\n" % (shown_line(code, state_root=state_root, color=False),
                                    time.strftime("%m-%d %H:%M", time.localtime(rec.get("at") or 0))))
        return 0
    item = clean_item(args.item)
    steps = parse_steps(args.steps)
    if not item or steps is None:
        err.write("aib now: 用法 aib now \"主項目\" 做完幾步/總共幾步,例:aib now \"AIB 停電恢復\" 2/5"
                  "(進度 % 由步數算,不自己填)\n")
        return 2
    started = None
    if args.started:
        started = parse_started(args.started)
        if started is None:
            err.write("aib now: --started 要寫成 'MM-DD HH:MM' 或 'HH:MM'(今天),例:--started '10-10 16:04'\n")
            return 2
    rec = write(code, item, steps[0], steps[1], state=args.state, state_root=state_root, started_at=started,
                new=args.new)
    out.write("@%s %s\n" % (code, line_of(item, steps[0], steps[1], args.state, started_at=rec["started_at"], at=rec["at"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
