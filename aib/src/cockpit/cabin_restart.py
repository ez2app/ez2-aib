#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib restart —— 列出 AIB 艙(在線的 + 上次開機時還在的),每艙的 AI 席、AI 種類、最後活動,並且一鍵恢復。
(the maintainer 2026-10-09 19:2x 起陸續直令:列出在線的艙與席 → 艙與艙之間要有間隔、上方有恢復指令、點了就恢復
 → 停電/重開機也要能用 → 「原來有幾個艙、每一艙對應的 AI 席都要正確重載」)

為什麼要有這支:10-09 18:47 艙 4f8d 畫面卡住,看起來像艙關了,差一點就走到搬席(殺窗+resume)。

兩種「恢復」,不是同一件事:
  在線的艙  tmux 還活著,只是沒人看(畫面卡、視窗被關)⇒ 開一個新的 Terminal 視窗 `tmux attach -d`。不搬、不重開。
  斷掉的艙  停電/重開機:tmux 全滅,但對話檔、席位檔、人格與大腦綁定都還在家目錄 ⇒
            **用原本的艙號**把艙開回來,每一席**用原本的席碼**同碼復活(--resume 同一段對話)。
            一艙對一艙、一席對一席;dm 位址(艙位碼/席碼)因此不變。
            10-09 19:3x 實測:測試艙 6220 整艙 kill-server 後,`AIB_REVIVE_SESSION_UUID=<uuid> aib claude dgrs root <原艙號>`
            開回同艙號、同席碼 a seat,問它停電前記的暗號答得出來。

★ 「斷掉的艙有哪些、每艙有誰」不靠事後猜,靠**快照**:每一艙的 watch 窗每一輪(預設 5 分鐘)
  把在線的艙與席寫進 <狀態根>/state/cabin-snapshots/boot-<開機時間>.json。
  **按開機分檔**:重開機後一開新艙,新艙的 watch 寫的是新的開機檔,不會蓋掉停電前那一份。
  恢復時讀「上一次開機」的最後一份,那就是停電前一刻的樣子。
  (拿席位檔當母體會撈出幾十個早就收掉的艙的墓碑:10-09 實測 26 個艙目錄只有 3 個活。)
★ 最後活動:在線的艙用 tmux window_activity;斷掉的艙用對話檔最後修改時間(拿不到就用快照時刻)。
★ 重開機後席位檔記的 pid 可能被別的程式重用 ⇒ 持有判準(seat_liveness)把「開席早於這次開機」的席當成已死。
★ 復活前每一席先查:對話檔在不在、工作資料夾在不在。缺一樣就不硬開,標「只能開新席」並照原樣列出,
  不是開到一半才失敗。對話檔不在、但有綁人格的席:開新席後用 bind_one.py 移植人格與大腦(同 regroup)。
★ 列表只查不動;恢復一定先列計畫、真人按一次確認(--yes 才跳過)。

用法:
  aib restart                        列表;在 AIB 艙裡會跳出可以點的選單,不在艙裡就問編號
  aib restart --no-menu              只印列表(不跳選單、不問)
  aib restart --json                 機器讀
  aib restart --restore <艙碼>       恢復那一艙(在線 ⇒ 接回來;斷掉 ⇒ 原艙號重開、每席同碼復活)
  aib restart --restore <艙碼> --yes 不問確認
  aib restart --restore-all          停電/重開機後:上次開機時的艙全部恢復(一艙對一艙,問一次)
  (恢復中途斷掉:再跑同一個指令,已經回來的席標 ✅ 跳過,不會開第二個)
  aib restart snapshot               寫一次快照(watch 每輪會自動做;這個是手動補)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seat_address  # noqa: E402
import seat_liveness  # noqa: E402
import seat_pool  # noqa: E402
import seats_dir_lib  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))
_CABIN_RESOLVE = os.path.join(HERE, "cabin_resolve.sh")
_HUB_PREFIX = "aihub-"
_WIN_FMT = "#{window_id}\t#{window_index}\t#{window_name}\t#{window_activity}\t#{@aib_role}"
SNAPSHOT_SUBDIR = os.path.join("state", "cabin-snapshots")
SNAPSHOT_KEEP = 10                      # 只留最近 10 次開機的快照
SNAPSHOT_MIN_INTERVAL_S = 60            # 多艙的 watch 同一分鐘內只寫一次
SNAPSHOT_DISABLE_ENV = "EZ2_AIB_RESTART_SNAPSHOT"   # =0 關掉 watch 的自動快照
RULE = "─" * 72


# ───────────────────────────── 量 ─────────────────────────────

def _session_of(cabin):
    """外層座艙 session 名,向單源 cabin_resolve.sh --session 要,不在這裡自己拼(R8)。"""
    return _resolve("--session", cabin)


def _hub_of(cabin):
    return _resolve("--hub", cabin)


def _resolve(flag, cabin):
    try:
        p = subprocess.run(["bash", _CABIN_RESOLVE, flag, cabin], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout.strip()
    return out if p.returncode == 0 and out else None


def _outer(tmux, session):
    """(座艙在不在, [接著的終端機])。問不到算不在。tmux 回 None = 問不到(seat_pool._tmux 的約定)。"""
    if not session:
        return False, []
    if tmux("default", "has-session", "-t", session) is None:
        return False, []
    out = tmux("default", "list-clients", "-t", session, "-F", "#{client_tty}") or ""
    return True, [ln.strip() for ln in out.splitlines() if ln.strip()]


def _seat_records(state_root, cabin):
    """那一艙的席位檔 → {window_id: 席位檔};同一窗有多份取 born_at 最新的。retired/ 不算。"""
    out = {}
    for path in sorted(glob.glob(os.path.join(state_root, "instances", cabin, "seats", "w*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        win = rec.get("window_id")
        if not win:
            continue
        old = out.get(win)
        if old is None or (rec.get("born_at") or 0) >= (old.get("born_at") or 0):
            out[win] = rec
    return out


def _parse_windows(stdout):
    rows = []
    for ln in (stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 5:
            continue
        wid, idx, name, act, role = parts[:5]
        try:
            act_i = int(act)
        except ValueError:
            act_i = 0
        rows.append({"window_id": wid, "index": idx, "name": name, "activity": act_i, "role_tag": role})
    return rows


def _tmux_env():
    # tmux 自己設的,不是艙單源要洗的名字;另包一層,_here_cabin 才不會把它算進艙解析讀取集
    return os.environ.get("TMUX") or ""


def _here_cabin():
    hub = os.environ.get("EZ2_AIHUB_SOCKET") or ""
    tmux_env = _tmux_env()
    if not hub and tmux_env:
        hub = os.path.basename(tmux_env.split(",", 1)[0])   # 同 regroup.sh:$TMUX 的 socket 路徑
    return hub[len(_HUB_PREFIX):] if hub.startswith(_HUB_PREFIX) else ""


def collect(*, cabins=None, tmux=None, session_of=None, state_root=None, here_cabin=None):
    """盤一次在線的艙。所有外部依賴都可注入(測試用);生產全部現查。
    tmux(socket, *args) 回 stdout 或 None(問不到);session_of(艙) 回外層 session 名或 None。"""
    tmux = tmux or seat_pool._tmux
    session_of = session_of or _session_of
    sockets = seat_pool.live_cabins() if cabins is None else list(cabins)
    root = state_root or seats_dir_lib.state_root()[0]
    if here_cabin is None:
        here_cabin = _here_cabin()
    live, unreachable = [], []
    for socket in sockets:
        if not socket.startswith(_HUB_PREFIX):
            continue
        cabin = socket[len(_HUB_PREFIX):]
        listing = tmux(socket, "list-windows", "-F", _WIN_FMT)
        if listing is None:
            unreachable.append(cabin)       # socket 檔殘留或 tmux 問不到:不猜
            continue
        recs = _seat_records(root, cabin)
        seats = []
        for w in _parse_windows(listing):
            if w["role_tag"].startswith("sys."):
                continue                    # watch 之類的系統窗不是 AI 席
            rec = recs.get(w["window_id"]) or {}
            seats.append({
                "window": w["index"], "window_id": w["window_id"], "window_name": w["name"],
                "code": seat_address.code_of(rec) or "",
                "provider": rec.get("provider") or "",
                "role": rec.get("role") or w["role_tag"] or "",
                "mode": rec.get("mode") or "",
                "model": rec.get("model_requested") or "",
                "uuid": rec.get("session_uuid") or "",
                "cwd": rec.get("cwd") or "",
                "seat_id": rec.get("seat_id") or "",
                "last_activity": w["activity"],
            })
        session = session_of(cabin)
        outer_ok, clients = _outer(tmux, session)
        live.append({
            "cabin": cabin, "session": session or "", "cockpit_alive": outer_ok,
            "clients": clients, "here": cabin == here_cabin,
            "last_activity": max([s["last_activity"] for s in seats] or [0]),
            "seats": seats,
        })
    live.sort(key=lambda c: c["last_activity"], reverse=True)
    return {"cabins": live, "unreachable": unreachable}


# ───────────────────────────── 快照(停電/重開機用) ─────────────────────────────

def snapshot_dir(state_root=None):
    return os.path.join(state_root or seats_dir_lib.state_root()[0], SNAPSHOT_SUBDIR)


def _snap_path(sdir, boot):
    return os.path.join(sdir, "boot-%d.json" % int(boot))


def write_snapshot(*, state_root=None, boot=None, now=None, data=None, min_interval_s=SNAPSHOT_MIN_INTERVAL_S):
    """把在線的艙與席寫進這次開機的快照檔。回寫入的路徑;沒寫回 None。
    ★ 沒有任何在線的艙 ⇒ 不寫(不拿空的蓋掉最後一份有內容的)。
    ★ 原子寫(暫存檔 + rename):停電可能剛好發生在寫的那一刻,舊的那份不能因此變成半截。"""
    boot = seat_liveness.boot_epoch() if boot is None else boot
    if not boot:
        return None
    now = time.time() if now is None else now
    sdir = snapshot_dir(state_root)
    path = _snap_path(sdir, boot)
    try:
        if min_interval_s and now - os.path.getmtime(path) < min_interval_s:
            return None
    except OSError:
        pass
    data = collect(state_root=state_root) if data is None else data
    if not data.get("cabins"):
        return None
    payload = {"schema": 1, "boot": int(boot), "written_at": int(now),
               "cabins": [{k: v for k, v in c.items() if k not in ("here", "clients")} for c in data["cabins"]]}
    os.makedirs(sdir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".snap-", dir=sdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    for old in sorted(glob.glob(os.path.join(sdir, "boot-*.json")))[:-SNAPSHOT_KEEP]:
        try:
            os.unlink(old)
        except OSError:
            pass
    return path


def previous_boot_snapshot(*, state_root=None, boot=None):
    """上一次開機的最後一份快照(dict),沒有回 None。壞檔跳過、改用更早的那一次。"""
    boot = seat_liveness.boot_epoch() if boot is None else boot
    sdir = snapshot_dir(state_root)
    cands = []
    for p in glob.glob(os.path.join(sdir, "boot-*.json")):
        m = re.search(r"boot-(\d+)\.json$", p)
        if m and (not boot or int(m.group(1)) < int(boot)):
            cands.append((int(m.group(1)), p))
    for _b, p in sorted(cands, reverse=True):
        try:
            with open(p, encoding="utf-8") as fh:
                snap = json.load(fh)
            if isinstance(snap, dict) and isinstance(snap.get("cabins"), list):
                snap["path"] = p
                return snap
        except (OSError, ValueError):
            continue
    return None


def transcript_path(provider, uuid, cwd, home=None):
    """那一席的對話檔(找得到回路徑;找不到回 ""; 這一家沒有本機對話檔回 None)。同 regroup.sh 的判法。"""
    home = home or os.path.expanduser("~")
    if not uuid:
        return ""
    if provider == "claude":
        p = os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd or ""), uuid + ".jsonl")
        return p if os.path.exists(p) else ""
    if provider == "grok":
        p = os.path.join(home, ".grok", "sessions", quote(cwd or "", safe=""), uuid)
        return p if os.path.exists(p) else ""
    if provider == "codex":
        hits = glob.glob(os.path.join(home, ".codex", "sessions", "**", "*" + uuid + "*"), recursive=True)
        return hits[0] if hits else ""
    return None                         # bot 家與 API 家沒有本機對話檔的概念,照可接回處理


def seat_plan(seat, home=None):
    """一席要走哪一條:revive(同碼復活)/ new(只能開新席)/ skip(不動),附人話理由。"""
    prov, uuid, cwd = seat.get("provider"), seat.get("uuid"), seat.get("cwd")
    if not prov:
        return "skip", "沒有席位紀錄,不知道是哪一家"
    if "AIB Desktop Bot" in (cwd or ""):
        return "skip", "桌面機器人自己的席,不從這裡開"
    if cwd and not os.path.isdir(cwd):
        return "new", "工作資料夾已經不在(%s),接不回原對話" % cwd
    if not uuid:
        return "new", "席位檔沒有對話編號"
    tp = transcript_path(prov, uuid, cwd, home)
    if tp == "":
        return "new", "找不到對話檔"
    return "revive", "同碼復活(接回原對話)"


def dead_cabins(live, snap, *, home=None):
    """上次開機時在、現在不在線的艙(依快照裡的順序),每席附上要走哪一條。
    ★ 也包含「只回來一部分」的艙:恢復到一半斷掉時,那一艙已經活了、但少幾席 ——
      只看「艙在不在」的話,缺的那幾席永遠不會再出現在清單上(10-09 實測 --restore-all 重跑時發現)。
      這種艙只列缺的席(依席碼比對),標 partial。在線艙沒給 seats 欄 ⇒ 量不到,當成完整、不列。"""
    if not snap:
        return []
    live_by_id = {c["cabin"]: c for c in live}
    out = []
    for c in snap.get("cabins", []):
        partial = False
        want = c.get("seats", [])
        if c.get("cabin") in live_by_id:
            lc = live_by_id[c["cabin"]]
            if lc.get("seats") is None:
                continue
            have = {s.get("code") for s in lc["seats"] if s.get("code")}
            want = [s for s in want if s.get("code") and s["code"] not in have]
            if not want:
                continue
            partial = True
        seats = []
        for s in want:
            s = dict(s)
            s["plan"], s["why"] = seat_plan(s, home)
            tp = transcript_path(s.get("provider"), s.get("uuid"), s.get("cwd"), home) if s.get("uuid") else ""
            try:
                s["last_activity"] = int(os.path.getmtime(tp)) if tp else s.get("last_activity") or 0
            except OSError:
                pass
            seats.append(s)
        out.append({"cabin": c.get("cabin"), "session": c.get("session") or "", "partial": partial,
                    "last_activity": max([s.get("last_activity") or 0 for s in seats] or [0]),
                    "seats": seats, "snapshot_at": snap.get("written_at") or 0})
    return out


# ───────────────────────────── 畫 ─────────────────────────────

def _when(epoch, now):
    if not epoch:
        return "量不到"
    hhmm = time.strftime("%m-%d %H:%M", time.localtime(epoch))
    ago = max(0, int(now - epoch))
    if ago < 60:
        rel = "剛剛"
    elif ago < 3600:
        rel = "%d 分前" % (ago // 60)
    elif ago < 86400:
        rel = "%d 小時 %d 分前" % (ago // 3600, ago % 3600 // 60)
    else:
        rel = "%d 天前" % (ago // 86400)
    return "%s(%s)" % (hhmm, rel)


def _short(cabin):
    return (cabin or "")[-4:]


def restore_hint(c):
    """艙上方那一行:恢復指令(或為什麼不用恢復)。"""
    if c.get("dead") and c.get("partial"):
        return "▶ 恢復:aib restart --restore %s   (把缺的席補回原艙,同碼復活)" % _short(c["cabin"])
    if c.get("dead"):
        return "▶ 恢復:aib restart --restore %s   (原艙號開回來,每席同碼復活)" % _short(c["cabin"])
    if c.get("here"):
        return "📍 你就在這艙,不用恢復"
    if not c.get("cockpit_alive"):
        return "▶ 恢復:aib claude dgrs root %s   (座艙畫面重組,AI 都還在)" % c["cabin"]
    if c.get("clients"):
        return "▶ 恢復:aib restart --restore %s --force   (⚠ 已經有人在看,會把那個視窗踢掉)" % _short(c["cabin"])
    return "▶ 恢復:aib restart --restore %s   (開一個新的 Terminal 視窗接回來)" % _short(c["cabin"])


def menu_items(view):
    """(編號, 艙, 標籤) —— 選單與編號共用同一份,不各算一次。"""
    items, n = [], 0
    for c in view["cabins"] + view.get("dead", []):
        if c.get("here"):
            continue
        n += 1
        kind = "斷掉" if c.get("dead") else ("有人在看" if c.get("clients") else "在線")
        items.append((n, c, "%s · %d 席 · %s" % (_short(c["cabin"]), len(c["seats"]), kind)))
    return items


def _seat_line(s, now):
    who = "@" + s["code"] if s.get("code") else "(沒有席位紀錄)"
    line = "   %-3s %-8s %-14s %-10s 最後活動 %s" % (s.get("window", ""), who, s.get("provider") or "?",
                                                   s.get("role") or "-", _when(s.get("last_activity"), now))
    if s.get("plan") and s["plan"] != "revive":
        line += "  ⚠ %s" % s.get("why", "")
    return line


def render(view, now=None):
    now = time.time() if now is None else now
    nums = {id(c): n for n, c, _ in menu_items(view)}
    out = []

    def block(c, head):
        out.append(RULE)
        out.append("")
        n = nums.get(id(c))
        out.append(("[%d] " % n if n else "    ") + restore_hint(c))
        out.append(head)
        for s in c["seats"]:
            out.append(_seat_line(s, now))
        out.append("")

    cabins = view["cabins"]
    if view.get("stale_note"):
        out.append(view["stale_note"])
        out.append("")
    out.append("在線的 AIB 艙 %d 個(最近有動的排前面)" % len(cabins) if cabins else "沒有在線的 AIB 艙。")
    for c in cabins:
        tags = []
        if c["here"]:
            tags.append("📍 你在這艙")
        if not c["cockpit_alive"]:
            tags.append("座艙畫面不在,AI 還在")
        elif c["clients"]:
            tags.append("有人接著:" + " ".join(c["clients"]))
        else:
            tags.append("沒有人接著")
        block(c, "■ 艙 %s · %d 席 · 最後活動 %s · %s"
              % (c["cabin"], len(c["seats"]), _when(c["last_activity"], now), " · ".join(tags)))
    if cabins:
        out.append(RULE)
    if view.get("dead"):
        out.append("")
        out.append("上次開機時還在、現在斷掉的艙 %d 個(停電/重開機前一刻的快照 %s)"
                   % (len(view["dead"]), _when(view["dead"][0].get("snapshot_at"), now)))
        if len(view["dead"]) > 1:
            out.append("▶ 全部一次恢復:aib restart --restore-all")
        for c in view["dead"]:
            state = ("🧩 只回來一部分,還少下面 %d 席" % len(c["seats"])) if c.get("partial") else "🔌 斷掉了"
            block(c, "■ 艙 %s · %d 席 · 最後活動 %s · %s"
                  % (c["cabin"], len(c["seats"]), _when(c["last_activity"], now), state))
        out.append(RULE)
    elif view.get("snapshot_note"):
        out.append("")
        out.append(view["snapshot_note"])
    if view.get("unreachable"):
        out.append("")
        out.append("問不到的艙(socket 還在但 tmux 沒回話,可能是殘留):" + " ".join(view["unreachable"]))
    return "\n".join(out)


SNAPSHOT_STALE_S = 15 * 60


def snapshot_staleness(live, *, state_root=None, boot=None, now=None):
    """這次開機的快照多久沒更新。艙還開著卻超過 15 分鐘沒寫 ⇒ watch 可能掛了,停電就會恢復成舊的樣子。回警告字或 ""。"""
    if not live:
        return ""
    boot = seat_liveness.boot_epoch() if boot is None else boot
    if not boot:
        return ""
    now = time.time() if now is None else now
    try:
        with open(_snap_path(snapshot_dir(state_root), boot), encoding="utf-8") as fh:
            at = json.load(fh).get("written_at") or 0
    except (OSError, ValueError):
        return "⚠ 這次開機還沒有艙的記錄(watch 每 5 分鐘記一次;剛開機或剛更新是正常的,過 15 分鐘還這樣就是 watch 沒在記)。現在停電會救不回這些艙。"
    age = int(now - at)
    if age > SNAPSHOT_STALE_S:
        return "⚠ 艙的記錄已經 %d 分鐘沒更新(watch 可能掛了)。現在停電,恢復出來會是 %s 的樣子。手動補一份:aib restart snapshot" % (
            age // 60, _when(at, now))
    return ""


def build_view(*, state_root=None, boot=None, home=None):
    data = collect(state_root=state_root)
    snap = previous_boot_snapshot(state_root=state_root, boot=boot)
    dead = dead_cabins(data["cabins"], snap, home=home)
    for c in dead:
        c["dead"] = True
    note = ""
    if not snap:
        note = "(沒有上次開機的快照:這台第一次用這個功能,或上次開機時沒有開過 AIB 艙。停電後要靠它才知道原本有哪些艙。)"
    dead_ids = {c["cabin"] for c in dead}
    unreachable = [u for u in data["unreachable"] if u not in dead_ids]   # 斷掉的艙已列在下面那區,不重複
    return {"cabins": data["cabins"], "dead": dead, "unreachable": unreachable, "snapshot_note": note,
            "stale_note": snapshot_staleness(data["cabins"], state_root=state_root, boot=boot)}


# ───────────────────────────── 恢復 ─────────────────────────────

def _find(view, code):
    code = (code or "").strip().lstrip("@")
    hits = [c for c in view["cabins"] + view.get("dead", []) if c["cabin"] == code or c["cabin"].endswith(code)]
    dead_ids = {c["cabin"] for c in hits if c.get("dead")}
    return [c for c in hits if c.get("dead") or c["cabin"] not in dead_ids]   # 只回來一部分的艙:恢復指的是補缺的席


def _open_terminal(cmd):
    script = 'tell application "Terminal" to do script %s' % json.dumps(cmd)
    try:
        return subprocess.run(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'],
                              capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def attach_cabin(c, *, out=sys.stdout):
    """開新的 Terminal 視窗 `tmux attach -d`,回讀 list-clients 確認真的接上(同 regroup.sh attach)。"""
    session = c.get("session") or _session_of(c["cabin"])
    tmux_bin = subprocess.run(["sh", "-c", "command -v tmux"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip() or "tmux"
    cmd = "%s -L default attach -d -t %s" % (tmux_bin, shlex.quote(session))
    if not _open_terminal(cmd):
        out.write("✗ 艙 %s:叫不動 Terminal(osascript 失敗,可能是沒給自動化權限)。自己開視窗打:%s\n" % (c["cabin"], cmd))
        return False
    for _ in range(10):
        time.sleep(0.5)
        cl = (seat_pool._tmux("default", "list-clients", "-t", session, "-F", "#{client_tty}") or "").split()
        if cl:
            out.write("✅ 艙 %s 接回來了:新 Terminal 視窗 %s\n" % (c["cabin"], " ".join(cl)))
            return True
    out.write("✗ 艙 %s:開了 Terminal 視窗,但 5 秒內沒看到它接上。自己打:%s\n" % (c["cabin"], cmd))
    return False


def _aib_bin():
    p = subprocess.run(["sh", "-c", "command -v aib"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    return [p] if p else ["bash", os.path.join(HERE, "aib")]


_CABIN_ENV_DROP = ("TMUX", "TMUX_PANE", "EZ2_AIHUB_SOCKET", "EZ2_AIB_INSTANCE", "EZ2_AIB_INSTANCE_HOME",
                   "EZ2_AIB_INSTANCE_TOKEN", "EZ2_TMUX_SESSION", "AIB_DM_SOCKET", "AIB_RESUME_SESSION_UUID",
                   "AIB_RESUME_UUID", "AIB_REVIVE_SESSION_UUID")


def _child_env(extra):
    """開席子行程的環境:從呼叫者(可能是別艙的席)複製,拿掉會把新席綁回呼叫者那一艙的變數。
    ★ 不改 os.environ(艙洗環境守門:不准 pop 本行程環境),逐支子行程給一份。"""
    env = {k: v for k, v in os.environ.items() if k not in _CABIN_ENV_DROP}
    env["AIB_NO_ATTACH"] = "1"
    env.update(extra)
    return env


def _bot_binding(prov, uuid, state_root):
    """重開 bot 席時要帶回去的綁定:(環境變數名, 值)。
    10-11 a seat(第 15 件第 1 步):以前拿 5 碼席碼去找 bridge/<碼>/status.json,但橋的資料夾是完整 session uuid
    ⇒ 永遠讀不到,艙重開從沒把 bot 綁定帶回去;codexbot 的狀態檔欄位也不叫 agent_id。改走 bot_binding.bound_id。"""
    try:
        import bot_caps
        benv = bot_caps.caps(prov).bind_env or ""
    except Exception:  # noqa: BLE001 不是 bot 家
        benv = ""
    if not benv:
        return "", ""
    try:
        import bot_binding
        return benv, bot_binding.bound_id(prov, uuid, base=state_root)[0]
    except Exception:  # noqa: BLE001
        return benv, ""


def _persona_of(code, state_root):
    idd = os.path.join(state_root, "identity")

    def load(name):
        try:
            with open(os.path.join(idd, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}
    b = (load("bindings.json").get("bindings") or {}).get(code) or {}
    if not b.get("persona_uid"):
        b = (load("seat-display.json").get("display") or {}).get(code) or {}
    return (b.get("persona_uid") or "", b.get("route_key") or "") if isinstance(b, dict) else ("", "")


def _transplant(cabin, prov, role, old_code, root, out):
    """對話救不回來而開了新席:把舊席的人格與大腦移植過去(唯一合法寫入器 bind_one.py,同 regroup.sh ⑥)。"""
    persona, route = _persona_of(old_code, root)
    if not persona:
        return
    best = None
    for f in glob.glob(os.path.join(root, "instances", cabin, "seats", "w*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get("provider") == prov and d.get("role") == role and not d.get("exited_at_epoch"):
            if best is None or (d.get("started_epoch") or 0) > (best.get("started_epoch") or 0):
                best = d
    nid = (best or {}).get("seat_id") or ""
    if not nid:
        out.write("  🔴 量不到新席 seat_id,人格 %s 沒移植(手動:bind_one.py bind --seat <id> --card %s)\n" % (persona, persona))
        return
    cmd = [sys.executable, os.path.join(REPO, "src", "identity", "bind_one.py"), "bind", "--seat", nid, "--card", persona,
           "--actor", "restart@%s/aib" % old_code, "--actor-kind", "system", "--state-root", root]
    if route:
        cmd += ["--brain", route]
    p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-1:] or [""]
    out.write("  %s 移植人格 %s%s rc=%d %s\n" % ("✅" if p.returncode == 0 else "🔴", persona,
                                              ("+大腦 " + route) if route else "", p.returncode, tail[0]))


def _ledger_path(root, cabin):
    return os.path.join(root, "instances", cabin, "cockpit", "restart-progress.json")


def _ledger_load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _ledger_mark(path, code, what, snap_at):
    """記「這一次停電的恢復」做到哪一席。綁快照時刻:下一次停電(另一份快照)不能沿用這份帳。"""
    d = _ledger_load(path)
    d[code] = {"done": what, "at": int(time.time()), "snap": snap_at}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _already_back(h, cabin):
    """這段對話已經被**這一艙**的席拿著 ⇒ 上一輪恢復已經把它開回來了(重跑不算出問題)。"""
    regs = h.get("registry") or []
    return bool(regs) and all((r.get("cabin") or "") == cabin for r in regs)


def _codex_register(hub, cabin, root, out):
    """codex 開起來時席位檔 session_uuid 是空的(AIB_SESSION_ID_PROVIDERS 沒有它),dm 會投不進去;
    watch 300 秒才回填 ⇒ 這裡直接補一道(同 regroup.sh ⑤)。"""
    listing = seat_pool._tmux(hub, "list-windows", "-F", "#{window_id} #{window_name}") or ""
    wins = [ln.split()[0] for ln in listing.splitlines() if ln.strip().endswith("-codex")]
    if not wins:
        out.write("  ⚠ 找不到 codex 的窗,席碼沒補登記(watch 會在 5 分鐘內自己補)\n")
        return
    env = _child_env({"EZ2_AIHUB_SOCKET": hub, "EZ2_AIB_INSTANCE": cabin,
                      "EZ2_AIB_INSTANCE_HOME": os.path.join(root, "instances", cabin)})
    p = subprocess.run([sys.executable, os.path.join(HERE, "seat-detect.py"), "--window", wins[-1], "--apply"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
    tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-1:] or [""]
    out.write("  %s codex 席碼補登記 rc=%d %s\n" % ("✅" if p.returncode == 0 else "⚠", p.returncode, tail[0]))


def revive_cabin(c, *, state_root=None, out=sys.stdout, log_dir=None, wait_s=30):
    """斷掉的艙:原艙號開回來,快照裡的每一席照窗序同碼復活。回 (成功席數, 出問題席數)。"""
    root = state_root or seats_dir_lib.state_root()[0]
    cabin = c["cabin"]
    hub = _hub_of(cabin)
    if not hub:
        out.write("✗ 量不到艙 %s 的 hub 名(cabin_resolve.sh --hub 不收),不猜,停手。\n" % cabin)
        return 0, len(c["seats"])
    log_dir = log_dir or os.path.join(root, "instances", cabin, "cockpit")
    os.makedirs(log_dir, exist_ok=True)
    # ② 舊席位檔補退出痕(hub 打不到時 sweep 只看 pid;重開機後 pid 被重用的那種交給持有判準的開機時間規則)
    subprocess.run([sys.executable, os.path.join(HERE, "seat_exit.py"), "sweep",
                    "--instance-home", os.path.join(root, "instances", cabin), "--hub", hub],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    ok = bad = 0
    ledger = _ledger_path(root, cabin)
    done = _ledger_load(ledger)
    for s in sorted(c["seats"], key=lambda s: int(s.get("window") or 0)):
        code, prov, plan = s.get("code") or "?", s.get("provider") or "", s.get("plan")
        out.write("\n▶ @%s(%s/%s,窗 %s)\n" % (code, prov or "?", s.get("role") or "-", s.get("window")))
        if plan == "skip":
            out.write("  跳過:%s\n" % s.get("why"))
            continue
        uuid = s.get("uuid") or ""
        if plan == "new" and code in done and done[code].get("snap") == c.get("snapshot_at"):
            out.write("  ✅ 上一輪已經開過新席了(%s),不再開第二個\n" % time.strftime("%H:%M", time.localtime(done[code]["at"])))
            ok += 1
            continue
        if plan == "revive":
            free = False
            for _ in range(max(1, wait_s // 2)):
                h = seat_liveness.holders(uuid)
                if not seat_liveness.is_held(h):
                    free = True
                    break
                if _already_back(h, cabin):
                    break
                time.sleep(2)
            if not free and _already_back(h, cabin):
                out.write("  ✅ 已經回來了(上一輪恢復開好的),跳過\n")
                ok += 1
                continue
            if not free:
                out.write("  🔴 這段對話還有人拿著(%s),不硬開,這一席跳過\n" % seat_liveness.describe(h))
                bad += 1
                continue
        win_names = (seat_pool._tmux(hub, "list-windows", "-F", "#{window_name}") or "").split()
        again = ["--again"] if any(re.fullmatch(r"%s[0-9]*-%s" % (re.escape(s.get("role") or "root"), re.escape(prov)), n)
                                  for n in win_names) else []
        model = s.get("model") or ""
        margs = ["--model", model] if model and model != prov else []
        extra = {}
        benv, bval = _bot_binding(prov, uuid, root)
        if benv and bval:
            extra[benv] = bval
        if plan == "revive":
            extra["AIB_REVIVE_SESSION_UUID"] = uuid
        cwd = s.get("cwd") if s.get("cwd") and os.path.isdir(s["cwd"]) else REPO
        cmd = _aib_bin() + [prov, s.get("mode") or "dgrs", s.get("role") or "root", cabin] + margs + again
        log = os.path.join(log_dir, "restart-%s.log" % code)
        with open(log, "w", encoding="utf-8") as fh:
            try:
                rc = subprocess.run(cmd, cwd=cwd, env=_child_env(extra), stdin=subprocess.DEVNULL,
                                    stdout=fh, stderr=subprocess.STDOUT, timeout=180).returncode
            except subprocess.TimeoutExpired:
                rc = 124
        if rc != 0:
            out.write("  🔴 開席失敗 rc=%d(紀錄 %s)\n" % (rc, log))
            bad += 1
            continue
        out.write("  ✅ %s rc=0%s\n" % ("同碼復活" if plan == "revive" else "開新席(%s)" % s.get("why"),
                                       ",帶回機器人綁定" if extra.get(benv) else ""))
        if benv and not bval:
            out.write("  ⚠ 機器人綁定量不到(bridge/%s/status.json 沒有 agent_id),要問人\n" % code)
        if prov == "codex":
            time.sleep(3)
            _codex_register(hub, cabin, root, out)
        if plan == "new":
            _transplant(cabin, prov, s.get("role") or "root", code, root, out)
        _ledger_mark(ledger, code, plan, c.get("snapshot_at"))
        ok += 1
        time.sleep(3)
    return ok, bad


def restore(code, *, yes=False, force=False, attach=True, out=sys.stdout, view=None, ask=input):
    view = view or build_view()
    hits = _find(view, code)
    if not hits:
        out.write("找不到艙 %s。先打 aib restart 看清單。\n" % code)
        return 2
    if len(hits) > 1:
        out.write("艙碼 %s 對到不只一艙:%s,請寫長一點。\n" % (code, " ".join(c["cabin"] for c in hits)))
        return 2
    c = hits[0]
    if c.get("here"):
        out.write("📍 你就在艙 %s 裡,不用恢復。\n" % c["cabin"])
        return 0
    if not c.get("dead"):
        if not c.get("cockpit_alive"):
            out.write("艙 %s 的座艙畫面不在、AI 還在:打 aib claude dgrs root %s 重組座艙(不用搬)。\n" % (c["cabin"], c["cabin"]))
            return 1
        if c.get("clients") and not force:
            out.write("艙 %s 已經有人在看(%s)。接回來會把那個視窗踢掉;確定要的話加 --force。\n"
                      % (c["cabin"], " ".join(c["clients"])))
            return 1
        if c.get("clients") and not yes:
            try:
                a = ask("⚠ 艙 %s 正在 %s 被看著,接回來會把那個視窗踢掉。打 y 確認:" % (c["cabin"], " ".join(c["clients"])))
            except EOFError:
                a = ""
            if a.strip().lower() not in ("y", "yes"):
                out.write("取消,什麼都沒動。\n")
                return 1
        if not attach:
            out.write("(--no-attach:艙 %s 活著,沒開視窗。要看:tmux attach -d -t %s)\n" % (c["cabin"], c.get("session")))
            return 0
        return 0 if attach_cabin(c, out=out) else 1
    return restore_dead([c], yes=yes, attach=attach, out=out, ask=ask)


def restore_dead(cabins, *, yes=False, attach=True, out=sys.stdout, ask=input):
    """一艙或多艙斷掉的艙:全部計畫先列出來、問一次,再照快照順序一艙一艙開回來(一艙對一艙)。"""
    total = sum(len(c["seats"]) for c in cabins)
    out.write("要恢復斷掉的艙 %d 個(都用原艙號開回來),共 %d 席:\n" % (len(cabins), total))
    for c in cabins:
        out.write("\n  ■ 艙 %s\n" % c["cabin"])
        for s in sorted(c["seats"], key=lambda s: int(s.get("window") or 0)):
            out.write("     窗 %-3s @%-6s %-14s %-8s → %s\n" % (s.get("window"), s.get("code") or "?",
                                                           s.get("provider") or "?", s.get("role") or "-", s.get("why")))
    if not yes:
        try:
            a = ask("\n開始恢復?打 y 確認,其他鍵取消:").strip().lower()
        except EOFError:
            a = ""
        if a not in ("y", "yes"):
            out.write("取消,什麼都沒動。\n")
            return 1
    all_ok = True
    for c in cabins:
        out.write("\n══════ 艙 %s ══════\n" % c["cabin"])
        ok, bad = revive_cabin(c, out=out)
        out.write("\n════ 艙 %s:成功 %d 席,出問題 %d 席 ════\n" % (c["cabin"], ok, bad))
        all_ok = all_ok and bad == 0
        if ok and attach:
            attach_cabin(dict(c, session=_session_of(c["cabin"])), out=out)
        elif ok:
            out.write("(--no-attach:沒開視窗。要看:tmux attach -d -t %s)\n" % _session_of(c["cabin"]))
    out.write("\n提醒:接回來的 AI 不知道剛剛斷過。請跟它們說一聲「剛剛中斷過,先檢查做到一半的東西」。\n")
    out.write("中途斷掉可以再跑一次同一個指令:已經回來的席會標 ✅ 跳過,不會開第二個。\n")
    return 0 if all_ok else 1


def restore_all(*, yes=False, attach=True, out=sys.stdout, view=None, ask=input):
    view = view or build_view()
    if not view.get("dead"):
        out.write("沒有斷掉的艙要恢復。%s\n" % (view.get("snapshot_note") or ""))
        return 0
    return restore_dead(view["dead"], yes=yes, attach=attach, out=out, ask=ask)


def show_menu(view):
    """在 AIB 艙裡:跳出 tmux 選單,點一艙就在彈出視窗裡跑恢復(看得到過程、可以按 y 確認)。回 True=選單有跳出來。"""
    items = menu_items(view)
    if not items or not os.environ.get("TMUX"):
        return False
    me = shlex.join(_aib_bin())
    args = ["display-menu", "-T", " 要恢復哪一艙? ", "-x", "C", "-y", "C"]
    for n, c, label in items[:9]:
        force = " --force" if (not c.get("dead") and c.get("clients")) else ""
        run = "%s restart --restore %s%s; echo; printf '按 Enter 關閉'; read _" % (me, c["cabin"], force)
        args += [("⚠ " if force else "") + label, str(n), "display-popup -E -w 90%% -h 80%% %s" % shlex.quote(run)]
    dead = view.get("dead") or []
    if len(dead) > 1:
        run = "%s restart --restore-all; echo; printf '按 Enter 關閉'; read _" % me
        args += ["全部斷掉的艙一次恢復(%d 艙)" % len(dead), "a", "display-popup -E -w 90%% -h 80%% %s" % shlex.quote(run)]
    args += ["", "取消", "q", ""]          # 空名字 = 分隔線(tmux 只吃一個空參數)
    try:
        return subprocess.run(["tmux"] + args, stdin=subprocess.DEVNULL, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ask_number(view, *, out=sys.stdout, ask=input):
    items = menu_items(view)
    if not items:
        return 0
    try:
        many = len(view.get("dead") or []) > 1
        a = ask("要恢復哪一艙?打編號%s(直接 Enter 離開):" % (",打 a 全部斷掉的艙一次恢復" if many else "")).strip()
    except EOFError:
        return 0
    if not a:
        return 0
    if a.lower() == "a" and len(view.get("dead") or []) > 1:
        return restore_all(out=out, view=view, ask=ask)
    pick = [c for n, c, _ in items if str(n) == a]
    if not pick:
        out.write("沒有編號 %s。\n" % a)
        return 2
    c = pick[0]
    return restore(c["cabin"], force=bool(c.get("clients")) and not c.get("dead"), out=out, view=view, ask=ask)


# ───────────────────────────── 開機選單 ─────────────────────────────
# the maintainer 10-10「開機選單也加」:登入後上次開機時的艙有沒回來的,才開一個 Terminal 跑 aib restart(只顯示選單,不自己恢復)。
# 只做 macOS(launchd 的 LaunchAgent,登入時跑一次);拆掉:aib restart uninstall-boot。

BOOT_LABEL = "com.ez2.aib-boot-restore"
BOOT_DELAY_S = 20                       # 等登入、網路、Terminal 都起來
BOOT_LOG_NAME = "boot-restore-menu.log"
_BOOT_PATH_EXTRA = ("/opt/homebrew/bin", "/usr/local/bin")   # launchd 的 PATH 沒有 Homebrew ⇒ 找不到 tmux


def boot_plist_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", BOOT_LABEL + ".plist")


def _boot_log_path(state_root=None):
    return os.path.join(state_root or seats_dir_lib.state_root()[0], "logs", BOOT_LOG_NAME)


def boot_plist(*, python=None, script=None, path_env=None, log=None):
    """LaunchAgent 內容(dict,給 plistlib)。PATH 用安裝當下的 PATH:登入時 launchd 只給 /usr/bin:/bin。"""
    parts = [d for d in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep) if d]
    for extra in (os.path.join(os.path.expanduser("~"), ".local", "bin"),) + _BOOT_PATH_EXTRA:
        if extra not in parts:
            parts.append(extra)
    return {"Label": BOOT_LABEL,
            "ProgramArguments": [python or sys.executable, script or os.path.abspath(__file__), "boot-check"],
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": os.pathsep.join(parts)},
            "StandardOutPath": log or _boot_log_path(),
            "StandardErrorPath": log or _boot_log_path()}


def _launchctl(*args):
    try:
        p = subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stderr or p.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _dev_worktree(here=HERE):
    """這支程式在開發 worktree 裡 ⇒ 回那棵樹的路徑;正本樹、不是 git 樹、量不到 ⇒ None。
    同 global_bind_gate.sh:開機設定指到 worktree,worktree 一刪,開機那一步就壞了。"""
    def git(*a):
        try:
            p = subprocess.run(["git", "-C", here] + list(a), capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
            return p.stdout if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    top = (git("rev-parse", "--show-toplevel") or "").strip()
    lst = git("worktree", "list", "--porcelain") or ""
    canon = next((ln[len("worktree "):] for ln in lst.splitlines() if ln.startswith("worktree ")), "")
    if not top or not canon:
        return None
    return None if os.path.realpath(top) == os.path.realpath(canon) else top


def install_boot(*, home=None, platform=None, launchctl=_launchctl, out=sys.stdout, plist=None, dev_tree=None):
    import plistlib
    if (platform or sys.platform) != "darwin":
        out.write("開機選單目前只支援 macOS(launchd);這台不裝。\n")
        return 2
    dev = _dev_worktree() if dev_tree is None else dev_tree
    if dev and os.environ.get("AIB_ALLOW_FOREIGN_BINDINGS") != "1":
        out.write("✗ 這支是開發樹 %s 裡的程式;開機設定要指向正本樹,請用正本的 aib 裝。要硬裝:AIB_ALLOW_FOREIGN_BINDINGS=1\n"
                  % dev)
        return 1
    path = boot_plist_path(home)
    data = plist or boot_plist()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(data["StandardOutPath"]), exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)
    domain = "gui/%d" % os.getuid()
    launchctl("bootout", "%s/%s" % (domain, BOOT_LABEL))          # 舊的先拿掉(沒裝過會失敗,不管)
    rc, err = launchctl("bootstrap", domain, path)
    if rc != 0:
        out.write("✗ 寫好 %s,但 launchctl bootstrap 失敗:%s\n" % (path, err))
        return 1
    out.write("✅ 開機選單裝好了:%s\n" % path)
    out.write("   登入後等 %d 秒,上次開機時的艙有沒回來的,才開一個 Terminal 跑 aib restart(只顯示選單,不自己恢復)。\n"
              % BOOT_DELAY_S)
    out.write("   紀錄:%s;拆掉:aib restart uninstall-boot\n" % data["StandardOutPath"])
    return 0


def uninstall_boot(*, home=None, launchctl=_launchctl, out=sys.stdout):
    path = boot_plist_path(home)
    launchctl("bootout", "gui/%d/%s" % (os.getuid(), BOOT_LABEL))
    if os.path.exists(path):
        os.remove(path)
        out.write("✅ 開機選單拆掉了:%s\n" % path)
    else:
        out.write("本來就沒裝開機選單(%s 不在)\n" % path)
    return 0


def boot_check(*, delay=None, view=None, opener=None, out=sys.stdout, now=None):
    """LaunchAgent 登入時跑的那一步。回 0 一律(登入流程不該因為它報錯)。"""
    if delay is None:
        try:
            delay = float(os.environ.get("AIB_BOOT_MENU_DELAY", BOOT_DELAY_S))
        except ValueError:
            delay = BOOT_DELAY_S
    if delay > 0:
        time.sleep(delay)
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(now))
    try:
        v = view if view is not None else build_view()
    except Exception as exc:                # noqa: BLE001 —— 量不到就不跳,寫一行紀錄
        out.write("%s 量不到艙(%s),不跳選單\n" % (stamp, exc))
        return 0
    dead = v.get("dead") or []
    if not dead:
        out.write("%s 上次開機的艙都在,或沒有快照 ⇒ 不跳選單\n" % stamp)
        return 0
    cmd = " ".join(shlex.quote(a) for a in _aib_bin() + ["restart"])
    ok = (opener or _open_terminal)(cmd)
    out.write("%s 上次開機有 %d 個艙沒回來(%s)⇒ 開 Terminal 跑 %s:%s\n"
              % (stamp, len(dead), " ".join(c["cabin"] for c in dead), cmd, "成功" if ok else "osascript 失敗"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib restart",
                                 description="列出 AIB 艙(在線的 + 上次開機時還在的)、每艙的 AI 席、種類與最後活動,並恢復")
    ap.add_argument("action", nargs="?", choices=["snapshot", "install-boot", "uninstall-boot", "boot-check"],
                    help="snapshot = 手動寫一次快照;install-boot / uninstall-boot = 裝/拆開機選單(macOS);"
                         "boot-check = 開機選單登入時跑的那一步")
    ap.add_argument("--json", action="store_true", help="印 JSON")
    ap.add_argument("--restore", metavar="艙碼", help="恢復那一艙(艙號後四碼即可)")
    ap.add_argument("--restore-all", action="store_true", help="停電/重開機後:上次開機時的艙全部恢復(一艙對一艙)")
    ap.add_argument("--yes", action="store_true", help="恢復斷掉的艙時不問確認")
    ap.add_argument("--force", action="store_true", help="已經有人在看的艙也接(會踢掉那個視窗)")
    ap.add_argument("--no-menu", action="store_true", help="只印列表,不跳選單、不問編號")
    ap.add_argument("--no-attach", action="store_true", help="恢復斷掉的艙之後不開 Terminal 視窗")
    args = ap.parse_args(argv)
    if args.action == "snapshot":
        p = write_snapshot(min_interval_s=0)
        print(p or "沒寫:沒有在線的艙,或量不到開機時間")
        return 0 if p else 1
    if args.action == "install-boot":
        return install_boot()
    if args.action == "uninstall-boot":
        return uninstall_boot()
    if args.action == "boot-check":
        return boot_check()
    if args.restore_all:
        return restore_all(yes=args.yes, attach=not args.no_attach)
    if args.restore:
        return restore(args.restore, yes=args.yes, force=args.force, attach=not args.no_attach)
    view = build_view()
    if args.json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0
    print(render(view))
    if args.no_menu or not sys.stdin.isatty():
        return 0
    if show_menu(view):
        return 0
    return ask_number(view)


if __name__ == "__main__":
    sys.exit(main())
