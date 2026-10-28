#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib now` —— 每一席回報「現在的主項目 | 步數 · 進度%」,顯示在輸入框下方 CTX 那一行的上面(the maintainer 2026-10-10)。

    aib now "AIB 停電恢復" 0/5 --new   # 開始新的主項目(啟動時間從現在算)
    aib now "AIB 停電恢復" 2/5    # 做完 2 步 / 共 5 步 ⇒ 進度：40%(由步數算);狀態預設【忙碌中】;啟動時間沿用
    aib now "等 a seat 推版" 4/5 --state reply   # 【等回應】;--state decide =【等你決定】
    aib now                       # 看自己這一席現在掛著什麼
    aib now --all                 # 所有還在的席各一行;【忙碌中】超過 30 分鐘沒更新的列首標 ⚠(只讀)
    aib now --clear               # 沒在做事 ⇒ 顯示「📌 等待指派新工作」(從沒跑過 ⇒「📌 尚未回報主項目」)
    aib now --apply-borders [--dry-run]   # 已經開著的 Codex / Grok / Copilot 席補上底部那一行

存放:<狀態根>/state/seat-now/<席位碼>.json(結構化)+ 同名 .line(狀態列直接印的那一行)。
  以席位碼為鍵:/clear、compact 後對話 id 會換,席位碼不換;狀態列從 tmux 窗選項 @aib_seat 拿同一個碼。
  畫在哪(the maintainer 10-10「全部的 ai 席都要」):
    Claude 席  狀態列 statusline-ez2-v215.sh 跑 `aib now --statusline`(印「席位碼␟📌 行␟AI 標誌」,␟ = \\x1f),📌 在 CTX 那一行上面,
               CTX 那一行插 @席位;
    agy 席     自己的狀態列 agy/statusline-ez2-ctx.js 畫 CTX 那一行(標誌 + @席位);它只在有事件時重畫、
               不支援定時重跑 ⇒ 📌 那一行改畫在 hub 窗底部邊框(--border --now-only,tmux 每幾秒重畫);
    Codex / Grok / Copilot 席  hub 窗底部邊框畫主項目，預留的第一條 status row 畫 CTX，
               第二條 status row 保留艦長分頁；各行追蹤目前視窗。
  每一行帶「更新時間」(shown_line;10-17 起不畫啟動時間);窄視窗換短寫法。
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
#   10-14 語系盤查第 13 條:字改走四語鍵 aib.now.state.*(含括號,en 用 [ ]),每次畫都照 i18n_lib.resolve_locale() 取。
STATES = {
    "busy":       ("aib.now.state.busy",       "\x1b[38;5;208m"),
    "reply":      ("aib.now.state.reply",      "\x1b[38;5;220m"),
    "decide":     ("aib.now.state.decide",     "\x1b[1;38;5;196m"),
    "idle":       ("aib.now.state.idle",       "\x1b[38;5;252m"),
    "unreported": ("aib.now.state.unreported", "\x1b[38;5;141m"),
}
SETTABLE_STATES = ("busy", "reply", "decide")    # idle 走 --clear;unreported 是沒有紀錄,不能設
RESET = "\x1b[0m"
ITEM_SGR = "\x1b[38;5;255m"   # 主項目(標題)白色(the maintainer 10-10「標題用白色」)
STEP_SGR = "\x1b[38;5;40m"    # 步數與進度綠色(the maintainer 10-10「步數和進度改用綠色」)
TIME_SGR = "\x1b[38;5;245m"   # 更新時間 / 啟動時間灰色(the maintainer 10-10「更新時間和啟動時間改用灰色」)


_TR = {}


def T(key, **params):
    """狀態列的字:每次照 i18n_lib.resolve_locale() 取(切語系後下一次重畫就換);同一行程每種語系的字典只載一次。
    字典讀不到 ⇒ 印鍵名(可見即可抓),不擋狀態列。"""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def state_tag(state, color=True):
    key, sgr = STATES.get(state, STATES["busy"])
    tag = T(key)
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


def _pane_facts():
    """(窗 id, socket 路徑, 窗寬):一次問 tmux。狀態列每次刷新原本為這三個值問 3 次(10-25 合成 1 次)。
    跟 seat_who.self_pane_facts 同一條規矩:必須帶 -t "$TMUX_PANE"。不在 tmux / 問不到 ⇒ None(呼叫端走原路)。"""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        import subprocess
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane,
                              "#{window_id}\t#{socket_path}\t#{pane_width}"],
                             capture_output=True, text=True, timeout=2, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = (out.stdout or "").strip().split("\t") if out.returncode == 0 else []
    if len(parts) != 3 or not parts[0].strip() or not parts[1].strip():
        return None
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def self_code(environ=None, facts=None):
    environ = os.environ if environ is None else environ
    try:
        import seat_who
        uuid = seat_who.seat_uuid_for_window(facts[0], facts[1]) if facts else seat_who.self_seat_uuid()
    except Exception:          # noqa: BLE001 —— 量不到就回 None,不讓整支掛掉
        uuid = None
    if uuid:
        return seat_address.uuid_tail5(uuid)
    return code_for_session(*(environ.get(k) for k in SESSION_ENVS))


def self_provider(code=None, facts=None):
    try:
        import seat_who
        wid, sock = facts[:2] if facts else seat_who.self_pane_facts()
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


def claude_clamps_256(env=None):
    """Claude Code 在 tmux 裡會把 24-bit 色自己降成 256 色(粗算:Claude 橘 #D97757 變 174 偏粉),
    除非設了 CLAUDE_CODE_TMUX_TRUECOLOR(Claude Code 原始碼:TMUX && level>2 ⇒ level=2)。
    這時改給色表自己指定的 256 色號(Claude = 173),畫出來才是原廠色(the maintainer 10-14「CTX 狀態列的標誌也改原廠色」)。"""
    env = os.environ if env is None else env
    return bool(env.get("TMUX")) and not env.get("CLAUDE_CODE_TMUX_TRUECOLOR")


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


def statusline_logo(provider):
    """Claude Code 狀態列用的標誌:tmux 裡(claude_clamps_256)把 24-bit 色換成色表自己指定的 256 色號;
    tmux 自己畫的框線不經 Claude Code,照樣用 logo_of 的 24-bit。"""
    logo = logo_of(provider)
    if not logo or not claude_clamps_256():
        return logo
    try:
        sys.path.insert(0, os.path.join(HERE, "render"))
        from render_lib import ENGINE_LETTER_COLORS  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return logo
    table = {tuple(rgb): c256 for rgb, c256 in (ENGINE_LETTER_COLORS.get(str(provider or "").lower()) or ())}   # 只查這個引擎自己的(同色不同號的引擎不互相蓋)

    def to256(m):
        c256 = table.get((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        return "\x1b[38;5;%dm" % c256 if c256 is not None else m.group(0)
    return re.sub(r"\x1b\[38;2;(\d+);(\d+);(\d+)m", to256, logo)


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
    return T("aib.now.started", when=_stamp(started_at) if started_at else T("aib.now.unknown_time"))


def times_text(at, started_at):
    """| 更新時間:…。更新時間就是新不新的證據,不再另標「N 小時前」。
    10-17 the maintainer「只顯示更新時間」:啟動時間照記(--new / --started 照用),只是不畫在狀態列上。"""
    return T("aib.now.updated", when=_stamp(at) if at else T("aib.now.unknown_time"))


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
    return T("aib.now.steps", value="%d/%d" % (done, total), pct=progress_pct(done, total))


def line_of(item, done, total, state="busy", color=False, started_at=None, at=None):
    """📌【狀態】主項目 | 2/5 STEP > 進度:40% | 更新時間:10-10 17:05"""
    return "%s%s%s | %s | %s" % (PIN, state_tag(state, color), item, steps_text(done, total),
                                 times_text(at, started_at))


def legacy_line_of(item, pct, state="busy", color=False):
    """步數上線前留下的紀錄只有自己估的 %:照實標「自估」,不裝成照步數算的。"""
    return "%s%s%s | %s | %s" % (PIN, state_tag(state, color), item, T("aib.now.self_pct", pct=pct),
                                 times_text(None, None))


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


# aib now --clear:這一席自己說沒事做(狀態欄【我很閒】)= aib.now.idle_text;
# 從沒跑過 aib now:不知道在做什麼 —— 不能說成「沒事做」(the maintainer 10-10 問 a seat)= aib.now.unreported_text。
#   舊名 IDLE_TEXT / UNREPORTED_TEXT 照讀得到(模組 __getattr__,當下語系)。
def idle_text():
    return T("aib.now.idle_text")


def unreported_text():
    return T("aib.now.unreported_text")


def __getattr__(name):
    if name == "IDLE_TEXT":
        return idle_text()
    if name == "UNREPORTED_TEXT":
        return unreported_text()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def clear(code, *, state_root=None, now=None):
    """這一席沒在做事:留一筆 idle 紀錄(畫成「📌 等待指派新工作」),不是刪掉 ——
    刪掉會跟「從沒回報過」同形,而那兩種的意思相反(一個是閒著、一個是不知道)。"""
    d = now_dir(state_root)
    os.makedirs(d, exist_ok=True)
    rec = {"idle": True, "at": int(now if now is not None else time.time()), "code": code}
    atomic_json.write_json(os.path.join(d, code + ".json"), rec)
    tmp = os.path.join(d, ".%s.line.%d" % (code, os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%s%s%s\n" % (PIN, state_tag("idle", False), idle_text()))
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
STALE_MARK = "⚠"
def gate_missing_mark():
    """ ⚠狀態檢查沒裝(字走 i18n:aib.now.gate_missing;每次重畫照當下語系取)。"""
    return " ⚠" + T("aib.now.gate_missing")


GATE_MISSING_SHORT = " ⚠"            # 視窗太窄放不下整句時(第二輪審查)   # 10-12:Claude 席狀態列自查 aib-now-gate 有沒有掛上(沒裝 = 規定靠記性,要看得到)


def now_gate_missing(settings_path=None):
    """Claude 的全域 settings.json 裡有沒有 aib-now-gate 的 Stop hook,而且那支檔案還在。
    回 True = 確定沒裝好(沒那條、或 test -f 指的檔不在 ⇒ hook 會安靜放行);讀不到 settings ⇒ False(不亂叫)。"""
    if not os.path.isfile(os.path.join(HERE, "..", "policy", "hooks", "aib-now-gate.py")):
        return False          # 這份程式沒帶這支閘(例如公開版)⇒ 裝不起來,叫了也修不好(審查第 4 條)
    path = settings_path or os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return False
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    if not isinstance(hooks, dict):
        return True
    for g in hooks.get("Stop") or []:
        for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
            cmd = str((h or {}).get("command") or "") if isinstance(h, dict) else ""
            if "aib-now-gate.py" in cmd and cmd.rstrip().endswith(" stop"):
                import shlex
                try:
                    words = shlex.split(cmd.split("||", 1)[0])     # test -f '<路徑>'(路徑有空格時帶引號)
                except ValueError:
                    words = []
                script = words[2] if len(words) >= 3 and words[:2] == ["test", "-f"] else ""
                return bool(script) and not os.path.isfile(script)
    return True
SHADOW_L2_MARK = {"ok": " 🪞② ✓", "missing": " 🪞② ✗"}   # 10-15 the maintainer:②✓ 太近 ⇒ 中間空一格   # patrol 的字走 i18n(shadow_l2_mark)


def shadow_l2_mark(l2):
    if l2 == "patrol":
        return " 🪞② " + T("aib.now.shadow_patrol")
    return SHADOW_L2_MARK.get(l2, "")


# 10-17 the maintainer:等級(Lv、星 / 月 / 日、冠)也顯示在狀態列。核心不算等級 —— 算的是擴充(艦長室),
#   它把每一席的快照寫到 <狀態根>/state/seat-rank/<席碼>.json:{"lv": 42, "rank": {"kind": "sun", "n": 2}, "crowns": [...], "at": 秒}。
#   沒裝擴充 / 沒有快照 / 超過一天沒更新 / 讀不懂 ⇒ 不顯示(不猜、不留舊的)。
RANK_SUBDIR = os.path.join("state", "seat-rank")
RANK_IC = {"star": "⭐", "moon": "🌙", "sun": "☀️"}
RANK_MAX_AGE_S = 86400
RANK_N_MAX = 10
CROWNS_MAX = 5


RES_MAX = 4
RES_LABEL_MAX = 6


def _res_label(x):
    """資源名稱由擴充給(核心不認得玩法用詞):去掉控制字元與分隔符號、最多 6 字。"""
    t = "".join(ch for ch in str(x) if ch.isprintable() and ch not in "|/\\&\x1f").strip()   # & 與 \ :殼的 ${x/…/…} 取代字串在 bash 5.2 會解讀它們
    return t[:RES_LABEL_MAX]


def rank_text(code, *, state_root=None, now=None):
    """'5⭐ 2👑 30木材/12石塊' 或 ''(10-17 the maintainer:等級寫成 n⭐、n🌙、n☀️、n👑,後面接目前獲得的資源)。
    快照多帶 "res": [["木材", 30], ["石塊", 12], …](照擴充給的順序;最多 4 種,其餘略過);沒有 res ⇒ 只畫等級。"""
    if not code:
        return ""
    path = os.path.join(state_root or seats_dir_lib.state_root()[0], RANK_SUBDIR, code + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
        lv, rank, at = int(snap["lv"]), snap["rank"], float(snap["at"])
        kind, n = rank["kind"], int(rank["n"])
        crowns = snap.get("crowns") or []
        res = snap.get("res") or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ""
    now = now if now is not None else time.time()
    if not (1 <= lv <= 100) or kind not in RANK_IC or not (1 <= n <= RANK_N_MAX) or not isinstance(crowns, list) \
            or not (0 <= now - at <= RANK_MAX_AGE_S):
        return ""
    parts = ["%d%s" % (n, RANK_IC[kind])]
    if crowns:
        parts.append("%d👑" % min(len(crowns), CROWNS_MAX))
    items = []
    for it in res if isinstance(res, list) else []:
        try:
            label, qty = _res_label(it[0]), int(it[1])
        except (TypeError, ValueError, IndexError):
            continue
        if label and 0 <= qty <= 10 ** 9:
            items.append("%d%s" % (qty, label))
        if len(items) >= RES_MAX:
            break
    if items:
        parts.append("/".join(items))
    return " ".join(parts)


def rank_mark(code, *, state_root=None, now=None):
    """狀態列舊版殼(沒帶 --rank-field)的退路:等級接在 📌 行尾端 ' | 5⭐ 30木材/12石塊'。"""
    t = rank_text(code, state_root=state_root, now=now)
    return " | " + t if t else ""


def shadow_layer2(provider, settings_path=None, hook_path=None):
    """10-13 the maintainer:狀態列顯示這一席有沒有裝上「影子續跑說明」第 2 層(回合結束 hook)。
    claude ⇒ settings 裡 aib-now-gate 的 Stop 與 PostToolUse(post-tool)都在 ⇒ ok;缺 ⇒ missing。
    其他家 ⇒ a seat 的回合結束掛點(src/identity/auto_distill_hook.py)有接 ship_report_gate ⇒ ok;沒接 ⇒ patrol(只靠第 3 層巡查)。
    讀不到 / 不認得 ⇒ None(不顯示,不亂叫)。"""
    prov = (provider or "").strip().lower()
    if not prov:
        return None
    if prov == "claude":
        if not os.path.isfile(os.path.join(HERE, "..", "policy", "hooks", "aib-now-gate.py")):
            return None        # 審查:公開版沒帶這支閘 ⇒ 裝不起來,不顯示(不然永遠 ✗)
        if now_gate_missing(settings_path):
            return "missing"
        path = settings_path or os.path.expanduser("~/.claude/settings.json")
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            return None
        for g in ((cfg.get("hooks") or {}).get("PostToolUse") or []) if isinstance(cfg, dict) else []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                cmd = str((h or {}).get("command") or "") if isinstance(h, dict) else ""
                if "aib-now-gate.py" in cmd and cmd.rstrip().endswith(" post-tool"):
                    return "ok"
        return "missing"
    try:
        with open(hook_path or os.path.join(HERE, "..", "identity", "auto_distill_hook.py"), encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return None            # 審查:沒有掛點那支檔(還沒上主線 / 公開版)⇒ 不顯示,不讓每一席永遠掛著「巡查」
    return "ok" if "ship_report_gate" in src else "patrol"


STALE_BUSY_S = 30 * 60   # aib now --all:【忙碌中】超過這麼久沒更新 ⇒ 標 ⚠(10-12 a seat;等回應 / 等你決定本來就可能等很久,不標)


def is_stale_busy(rec, now=None):
    """這筆紀錄是不是「說在忙、卻很久沒回報」。只看畫面上會顯示成【忙碌中】的:idle / 沒回報 / 等回應 / 等你決定都不算。
    跟 shown_line 同一套判法(審查):狀態值不認得 ⇒ 畫成忙碌中 ⇒ 這裡也算;沒有主項目 ⇒ 畫成【未回報】⇒ 不算。"""
    if not rec or rec.get("idle") or not rec.get("item") or (rec.get("state") or "busy") in ("reply", "decide"):
        return False
    try:
        at = int(rec.get("at") or 0)
    except (TypeError, ValueError):
        return False
    now = now if now is not None else time.time()
    return bool(at) and now - at > STALE_BUSY_S


def overview(*, state_root=None, records=None, now=None):
    """aib now --all:所有還在的席各一列 (碼, 那一行, 是否過期);過期的排前面,其餘照碼排。
    那一行就是 shown_line(各席自己狀態列上的同一行;過期的那行自己也帶 📌⚠,the maintainer 10-12),列首 ⚠ 讓總覽好掃。"""
    now = now if now is not None else time.time()
    rows = []
    for c in live_codes(records):
        rec = read(c, state_root=state_root)
        rows.append((c, shown_line(c, state_root=state_root, now=now, color=False), is_stale_busy(rec, now)))
    rows.sort(key=lambda r: (not r[2], r[0]))
    return rows


def _parts(code, rec, color, now):
    """(開頭 📌【狀態】, 主項目, 完整尾巴, 短尾巴);短尾巴給窄視窗(the maintainer 10-10「窄視窗改成短的寫法」)。"""
    if not rec:
        return "%s%s" % (PIN, state_tag("unreported", color)), unreported_text(), "", ""
    if rec.get("idle"):
        return "%s%s" % (PIN, state_tag("idle", color)), idle_text(), "", ""
    st = rec.get("state") or "busy"
    head = "%s%s" % (PIN, state_tag(st, color))   # 📌【狀態】主項目:中間不留空(the maintainer 10-10)
    if is_stale_busy(rec, now):
        # the maintainer 10-12「加」:【忙碌中】超過 30 分鐘沒更新 ⇒ 📌⚠【忙碌中】…(只加這一個記號;更新時間照舊,不另標 N 小時前)
        head = "%s%s%s" % (PIN, STALE_MARK, state_tag(st, color))
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
        full = " | %s | %s" % (T("aib.now.self_pct", pct=pct), tint(times_text(at, None)))
        short = " | %s" % T("aib.now.self_pct_short", pct=pct)
    stamps = []
    if at:
        stamps.append(T("aib.now.updated_short", when=_stamp(at, now, short=True)))
    if stamps:
        short += " | " + tint(" | ".join(stamps))
    return head, item, full, short


def shown_line(code, *, state_root=None, now=None, color=True, width=None):
    """畫面上那一行:📌【狀態】主項目 | 2/5 STEP > 進度:40% | 更新時間:10-10 17:05。
    沒回報 ⇒ 📌【未回報】尚未回報主項目;--clear ⇒ 📌【我很閒】等待指派新工作。
    新舊看「更新時間」;【忙碌中】超過 30 分鐘沒更新 ⇒ 📌⚠【忙碌中】(the maintainer 10-12)。沒有席位碼 ⇒ ""(不是席,不畫)。
    width(那一席視窗寬):完整那行放不下 ⇒ 短寫法(📌【狀態】主項目 | 2/5 > 40% | 更新 17:05 | 啟動 16:04);
    還放不下 ⇒ 主項目截短加「…」,步數、% 與更新時間不截。"""
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


def _self_width(facts=None):
    """這一席視窗的寬,扣掉狀態列左邊的縮排與右邊留白;量不到 ⇒ None(照完整那行畫)。"""
    if facts:
        return max(20, int(facts[2]) - 4) if facts[2].isdigit() else None
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


def border_line(wid, sock, *, provider="", state_root=None, now=None, width=None, now_only=False, ctx_only=False):
    """Codex / Grok / Copilot 席(自己的狀態列放不了自訂字)的底部邊框:模型 | @席位 | 💧 Ez2 CTX n% · 📌【狀態】主項目 | …。"""
    # Engines with their own context line keep the reserved status row empty.
    if ctx_only and (not provider or provider in STATUSLINE_ENGINES + NOW_ONLY_ENGINES):
        return ""
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
    if ctx_only:
        return ansi_to_tmux(ctx_part)
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
    extra = " --now-only"
    return " #(%s %s --border%s --window #{window_id} --socket #{socket_path} --width #{pane_width} --provider %s)" % (
        shlex.quote(py), shlex.quote(os.path.abspath(__file__)), extra, shlex.quote(provider))


def context_status_format(python=None):
    """The second seat row: context for the active window, above captain tabs."""
    import shlex
    import shutil
    py = python or shutil.which("python3") or sys.executable
    row = "#[align=left]  #(%s %s --border --ctx-only --window #{window_id} --socket #{socket_path} --width #{window_width} --provider '#{@aib_provider}')" % (
        shlex.quote(py), shlex.quote(os.path.abspath(__file__)))
    # During an upgrade, older launchers can still create combined borders.
    # Show the second context row only after that window has a task-only border.
    return "#{?#{m:*--now-only*,#{pane-border-format}},%s,}" % row


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
            out.write("✗ %s\n" % T("aib.now.borders_unreachable", hub=hub))
            unreachable += 1
            continue
        if not dry_run:
            if (tmux(hub, "set-option", "-t", "aihub", "status", "2") is None
                    or tmux(hub, "set-option", "-t", "aihub", "status-format[0]", context_status_format()) is None):
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
                                                T("aib.now.borders_dry_row") if dry_run else ""))
            done += ok
    out.write(T("aib.now.borders_done", n=done, dry=T("aib.now.borders_dry") if dry_run else "",
                count=unreachable) + "\n")
    return done, unreachable


def usage_line():
    return "aib now: %s\n" % T("aib.now.usage")


def main(argv=None, *, code=None, out=sys.stdout, err=sys.stderr, state_root=None, gate_check=None):
    ap = argparse.ArgumentParser(prog="aib now", description=T("aib.now.help.desc"))
    ap.add_argument("item", nargs="?", help=T("aib.now.help.item"))
    ap.add_argument("steps", nargs="?", help=T("aib.now.help.steps"))
    ap.add_argument("--clear", action="store_true", help=T("aib.now.help.clear"))
    ap.add_argument("--new", action="store_true",
                    help=T("aib.now.help.new"))
    ap.add_argument("--started", metavar="'MM-DD HH:MM'", help=T("aib.now.help.started"))
    ap.add_argument("--state", choices=SETTABLE_STATES, default="busy",
                    help=T("aib.now.help.state"))
    ap.add_argument("--seat", metavar="SEAT", default="",
                    help=T("aib.now.help.seat"))
    ap.add_argument("--statusline", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--tail-field", action="store_true", help=argparse.SUPPRESS)   # 10-28 Andy 工作單 S:mod 狀態段(未讀 dm、🌐 語系)另成第 5 欄,殼接在 CTX 那一行尾端;要連 --rank-field 一起帶
    ap.add_argument("--rank-field", action="store_true", help=argparse.SUPPRESS)   # 10-17:等級另成第 4 欄(殼把它放到 CTX 那一行 @席位 後面);沒帶 ⇒ 接在 📌 行尾   # 狀態列用:印「席位碼<TAB>📌 行」
    ap.add_argument("--border", action="store_true", help=argparse.SUPPRESS)       # Codex 等席底部邊框用
    ap.add_argument("--now-only", action="store_true", help=argparse.SUPPRESS)     # agy:邊框只畫 📌
    ap.add_argument("--ctx-only", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--context-status-format", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--border-hub", default="", help=argparse.SUPPRESS)
    ap.add_argument("--session", default="", help=argparse.SUPPRESS)              # 狀態列遞來的對話編號(沒有 TMUX_PANE 時用)
    ap.add_argument("--window", default="", help=argparse.SUPPRESS)
    ap.add_argument("--socket", default="", help=argparse.SUPPRESS)
    ap.add_argument("--provider", default="", help=argparse.SUPPRESS)
    ap.add_argument("--width", type=int, default=0, help=argparse.SUPPRESS)   # 視窗寬(邊框由 tmux 帶;狀態列自己量)
    ap.add_argument("--border-format", metavar="PROVIDER", help=argparse.SUPPRESS)   # 開席時 aib 拿格式用
    ap.add_argument("--apply-borders", action="store_true",
                    help=T("aib.now.help.apply_borders"))
    ap.add_argument("--dry-run", action="store_true", help=T("aib.now.help.dry_run"))
    ap.add_argument("--all", action="store_true",
                    help=T("aib.now.help.all"))
    ap.add_argument("--sweep", action="store_true",
                    help=T("aib.now.help.sweep"))
    ap.add_argument("--beat-age", metavar="SEAT",
                    help=T("aib.now.help.beat_age"))
    # 10-14:負的步數(-1/3)在 Python 3.13 以前會被 argparse 當成不認得的選項而直接結束 ⇒ 先當成步數寫錯,回用法、rc 2
    if any(re.fullmatch(r"-\d+(?:\.\d+)?/\d+", a or "") for a in (sys.argv[1:] if argv is None else argv)):
        err.write(usage_line())
        return 2
    args = ap.parse_args(argv)
    if args.beat_age:
        got = beat_age(args.beat_age.strip().lstrip("@").lower(), state_root=state_root)
        if got is None:
            out.write(T("aib.now.no_beat") + "\n")
            return 1
        out.write("%d %s\n" % got)
        return 0
    if args.all:
        if args.item is not None or args.clear:
            err.write("aib now: %s\n" % T("aib.now.all_read_only"))
            return 2
        rows = overview(state_root=state_root)
        if not rows:
            out.write(T("aib.now.all_none") + "\n")
            return 1
        for c, line, stale in rows:
            out.write("%s @%s %s\n" % ("⚠" if stale else " ", c, line))
        n = sum(1 for r in rows if r[2])
        out.write(T("aib.now.all_total", n=len(rows),
                    detail=T("aib.now.all_stale", n=n, min=STALE_BUSY_S // 60) if n else T("aib.now.all_fresh"))
                  + "\n")
        return 0
    if args.sweep:
        gone = sweep_dead(state_root=state_root)
        out.write((T("aib.now.swept", n=len(gone), codes=" ".join("@" + c for c in gone)) if gone
                   else T("aib.now.swept_none")) + "\n")
        return 0
    if args.context_status_format:
        out.write(context_status_format() + "\n")
        return 0
    if args.border_format:
        out.write(border_format(args.border_format) + "\n")
        return 0
    if args.apply_borders:
        _done, bad = apply_borders(hubs=[args.border_hub] if args.border_hub else None,
                                  dry_run=args.dry_run, out=out)
        return 1 if bad else 0
    if args.border:
        out.write(border_line(args.window, args.socket, provider=args.provider, state_root=state_root,
                              width=args.width or None, now_only=args.now_only, ctx_only=args.ctx_only))
        return 0
    if args.statusline:
        facts = None if code else _pane_facts()
        c = code or self_code(facts=facts) or (code_for_session(args.session) if args.session else None) or ""
        if c:
            beat(c, "statusline", state_root=state_root)
        prov = args.provider or (self_provider(c, facts=facts) if c else "")
        w = args.width or (_self_width(facts) if c else None)
        line, r_text = "", ""
        if c:
            # gate_check 預設:正式跑(state_root=None)才查真的 settings.json;測試注入 state_root 時不查,
            #   不然同一格測試在裝了 / 沒裝的機器上結果不同(10-12 審查)。
            missing = (state_root is None if gate_check is None else gate_check) and now_gate_missing()
            mark = gate_missing_mark() if missing else ""
            if missing and w:
                sys.path.insert(0, os.path.join(HERE, "render"))
                from render_lib import visible_width
                if w - visible_width(mark) < 40:
                    mark = GATE_MISSING_SHORT
                w = max(10, w - visible_width(mark))
            l2 = shadow_layer2(prov) if (state_root is None if gate_check is None else gate_check) and c else None
            l2_mark = shadow_l2_mark(l2)
            if l2_mark and w:
                sys.path.insert(0, os.path.join(HERE, "render"))
                from render_lib import visible_width
                w = max(10, w - visible_width(l2_mark))
            r_text = rank_text(c, state_root=state_root)
            r_mark = "" if args.rank_field else (" | " + r_text if r_text else "")
            if r_mark and w:
                sys.path.insert(0, os.path.join(HERE, "render"))
                from render_lib import visible_width
                if w - visible_width(r_mark) < 40:    # 窄窗:等級讓位給主項目
                    r_mark = ""
                else:
                    w = max(10, w - visible_width(r_mark))
            line = shown_line(c, state_root=state_root, width=w)
            line += r_mark
            if missing:
                line += mark
            line += l2_mark
        fields = [c, line, statusline_logo(prov) if c else ""]
        if args.rank_field:
            fields.append(r_text if c else "")
        if args.rank_field and args.tail_field:
            # 10-28 Andy「這個 aib 太多行了,我希望縮成一行」:mod 的狀態段併進 CTX 那一行尾巴(statusline_tail.py)。
            #   帶了 --tail-field 才是新版殼 ⇒ 才記「這次畫出了哪幾個 mod 的段」,mod 自己那段畫出過才收掉自己那一行(#401 R1)。
            tail = ""
            if c and args.session:
                try:
                    import statusline_tail
                    parts = statusline_tail.tail_parts(args.session, state_root=state_root)
                    tail = statusline_tail.JOIN.join(t for _, t in parts)
                    statusline_tail.mark_shown(args.session, [m for m, _ in parts], state_root=state_root)
                except Exception:  # noqa: BLE001 —— 尾巴讀不到不能讓狀態列掛掉
                    tail = ""
            fields.append(tail)
        out.write(SEP.join(fields) + "\n")
        return 0
    if args.seat and not code:
        want = args.seat.strip().lstrip("@").lower()
        if not known_code(want):
            err.write("aib now: %s\n" % T("aib.now.seat_unknown", code=want))
            return 2
        code = want
    code = code or self_code()
    if not code:
        # 10-10 實例:Claude 的背景對話掛在共用背景服務底下,沒有 TMUX_PANE;往上找上層程式會認成
        #   啟動那個服務的別席(a seat 的背景對話被認成 a seat)⇒ 不猜,請它自己報碼。
        err.write("aib now: %s\n" % T("aib.now.who_unknown"))
        return 2
    sweep_dead(state_root=state_root)          # 順手掃:回報的席越多,死席殘影留得越短
    if args.clear:
        clear(code, state_root=state_root)
        out.write("@%s %s\n" % (code, shown_line(code, state_root=state_root, color=False)))
        return 0
    if args.item is None:
        rec = read(code, state_root=state_root)
        if not rec:
            out.write(T("aib.now.show_unset", code=code, line=shown_line(code, state_root=state_root, color=False))
                      + "\n")
            return 0
        out.write(T("aib.now.show_line", line=shown_line(code, state_root=state_root, color=False),
                    when=time.strftime("%m-%d %H:%M", time.localtime(rec.get("at") or 0))) + "\n")
        return 0
    item = clean_item(args.item)
    steps = parse_steps(args.steps)
    if not item or steps is None:
        err.write(usage_line())
        return 2
    started = None
    if args.started:
        started = parse_started(args.started)
        if started is None:
            err.write("aib now: %s\n" % T("aib.now.started_bad"))
            return 2
    rec = write(code, item, steps[0], steps[1], state=args.state, state_root=state_root, started_at=started,
                new=args.new)
    out.write("@%s %s\n" % (code, line_of(item, steps[0], steps[1], args.state, started_at=rec["started_at"], at=rec["at"])))
    nudge = dm_stale_nudge(code) if state_root is None else ""
    if nudge:
        out.write(nudge + "\n")
    return 0


def dm_stale_nudge(code, env=None, now=None):
    """Andy 10-25 20:1x:待審區有進行中逾 4 小時沒寫等什麼的信 ⇒ 換階段時順便提醒一行(只在寫 📌 時查,狀態列不查)。"""
    try:
        import dm_queue
        b = dm_queue.brief_payload(code, env=env, now=now)
        n = int(b.get("stale_wait") or 0)
        m = int(b.get("wait_overdue") or 0)     # 0.1.61:寫了等什麼、逾 4 小時沒動
    except Exception:  # noqa: BLE001 —— 提醒讀不到不影響 aib now
        return ""
    lines = ([T("aib.now.dm_stale_wait", n=n, code=code)] if n else []) + \
        ([T("aib.now.dm_wait_overdue", n=m, code=code)] if m else [])
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
