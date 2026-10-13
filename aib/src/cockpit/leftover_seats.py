#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄第 3 頁「離艙」:已經不在任何還活著的 AIB 艙裡、但還沒收乾淨的席位(the maintainer 09-30)。

三種(kind):
  record     艙已經不在(或窗已不在),席位檔還在 seats/,程式也不在跑 —— 只剩紀錄。
  running    同上,但席位檔記的 pid 還活著,而且那支程式看起來就是這一家的 CLI(不是 pid 被別人重用)。
  dead_pane  艙還在,窗也還在,但窗格已經死掉(Pane is dead)。

<X> 做的事(close):
  record     把席位檔搬進 seats/retired/(seat_retire.retire,不刪)。
  running    先對那一個 pid 送 TERM(只殺它,一律正數 pid,不殺整組),再像 record 一樣搬。
  dead_pane  kill-window 那一窗,再搬席位檔。
  ★ 動手前一律重掃一次,當下已經不是離艙(例如艙又起來了)就拒絕,不照舊快照做。
  ★ 同一個五碼若還有活席在用(同一段對話在新艙續開),佇列目錄不搬(keep_queue),只搬席位檔。
<全收> 只收 record 與 dead_pane;running 要逐席按 <X>(那可能是人在用的終端)。

用法(畫面層只讀快取,真的掃在背景跑):
  leftover_seats.py produce                        掃一次,JSON 印到 stdout(背景更新快取用)
  leftover_seats.py render --width W --row-offset N 第 3 頁的字;順便寫 ai-rowmap.json 給滑鼠
  leftover_seats.py summary                        一行摘要(1.AI 頁用),沒有就空
  leftover_seats.py close --key <艙>|<@窗> [--dry-run]
  leftover_seats.py close-all [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if os.path.join(HERE, "render") not in sys.path:
    sys.path.append(os.path.join(HERE, "render"))       # render_lib / right_fold_api(寬度、捲動)

CACHE_NAME = "leftover-seats.json"
CLOSING_NAME = "leftover-closing"   # 按了確定收席 ⇒ mouse-ctl 寫;收完再碰一次(mtime=收完的時刻)
CLOSING_TEXT = "正在關閉舊席位中，請稍等！完成後會自動恢復到同頁面"   # the maintainer 09-30 指定字
TTL_S = int(os.environ.get("AIB_LEFTOVER_TTL_S") or 30)
MIN_AGE_S = 60              # 剛寫的席位檔不算(開席中,窗可能還沒掛上)
CLOSE_ACTION = "leftover-close"


def close_enabled():
    """★ 10-02(the maintainer 預設裁):v0.70.0 先**只顯示**離艙席、不能收 —— 三輪凍結審 4 個重大有 3 個在這一頁
    (收掉會砍程式、關窗、搬席位檔與信箱,錯一次就是砍到活席或丟信)。修完下一版再開。
    AIB_LEFTOVER_CLOSE=1 才開(測試用它照樣驗收掉的邏輯)。"""
    return os.environ.get("AIB_LEFTOVER_CLOSE", "0") == "1"
CLOSE_ALL_ACTION = "leftover-close-all"
KIND_LABEL = {"record": "只剩紀錄", "running": "還在跑", "dead_pane": "死窗"}
_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\|@\d{1,6}$")


def aib_home():
    return os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")


def instances_root():
    return os.environ.get("EZ2_AIB_INSTANCE_ROOT") or os.path.join(aib_home(), "instances")


_CABIN_RESOLVE = os.path.join(HERE, "cabin_resolve.sh")
_HUB_NAMES = {}


def _hub_name(inst):
    """那一艙的 tmux socket 名,向單源 `cabin_resolve.sh --hub` 要,不在這裡自己拼
    (test-cabin-resolution R8:拼一次就是第二個真相源;seat_who._hub_name 同一個做法)。
    一艙查一次、進快取。解析器不收這個名字(非法字元等)⇒ None,呼叫端當成連不上,不猜一個 socket。"""
    if inst not in _HUB_NAMES:
        try:
            p = subprocess.run(["bash", _CABIN_RESOLVE, "--hub", inst], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            _HUB_NAMES[inst] = p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
        except (OSError, subprocess.TimeoutExpired):
            return None              # 10-02:逾時不是答案,不進快取(下一輪再問);呼叫端當「不確定」
    return _HUB_NAMES[inst]


def _tmux(inst, *args):
    hub = _hub_name(inst)
    if not hub:
        raise OSError("cabin_resolve.sh --hub %s: no socket name" % inst)
    return subprocess.run(["tmux", "-L", hub, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=5)


_GONE_MARKERS = ("no server running", "error connecting to", "No such file or directory")


def cabin_state(inst):
    """("live", {@窗: …}) / ("gone", None) / ("unknown", None)。
    ★ 10-02(a seat,r19 全範圍審 M1,the maintainer 列急件):以前任何失敗都回 None,scan 把 None 當「艙已關」,
      list-panes 逾時(機器忙,5 秒)或解析不到 socket 名的那一輪,活艙的席位全被列成離艙,
      「全部關閉」就把活席退役、連信箱一起搬走。現在只有 tmux 明確說伺服器不在才算 gone;
      逾時/解析失敗/其他錯 = unknown,呼叫端整艙跳過,不猜。"""
    try:
        p = _tmux(inst, "list-panes", "-a", "-F", "#{window_id} #{pane_dead} #{pane_pid}")
    except subprocess.TimeoutExpired:
        return "unknown", None
    except OSError:
        return "unknown", None
    if p.returncode != 0:
        err = (p.stderr or "")
        return ("gone", None) if any(m in err for m in _GONE_MARKERS) else ("unknown", None)
    return "live", _parse_panes(p.stdout)


def _parse_panes(stdout):
    out = {}
    for ln in stdout.splitlines():
        parts = ln.split()
        if len(parts) == 3:
            try:
                out.setdefault(parts[0], {"dead": parts[1] == "1", "pid": int(parts[2])})
            except ValueError:
                continue
    return out


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_command(pid):
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _is_seat_process(cmd, provider, uuid):
    """pid 上的程式是不是**這一席**:指令要有 provider 名,而且要有這一席完整的 session_uuid。
    ★ 10-02 第二輪凍結審 MAJOR:以前只看 provider 字串 ⇒ macOS 重用 pid 時,死艙舊席記的 pid
      可能已經是別艙的活 claude,按 <X> 就把它砍了。看不到 uuid(或這家的指令列不帶 uuid)⇒ 當不是,
      只收席位檔、不殺(寧可少砍,不可錯砍)。"""
    return bool(provider) and bool(uuid) and provider in cmd and uuid in cmd


def _code(rec):
    try:
        import seat_address
        c = seat_address.code_of(rec)
    except Exception:  # noqa: BLE001
        c = None
    return c or ""


def _seat_files(inst_dir):
    return sorted(glob.glob(os.path.join(inst_dir, "seats", "w*.json")))


def scan(now=None):
    """所有離艙席(list of dict),與活席用到的五碼集合。"""
    now = time.time() if now is None else now
    rows, live_codes = [], set()
    for inst_dir in sorted(glob.glob(os.path.join(instances_root(), "*"))):
        files = _seat_files(inst_dir)
        if not files:
            continue
        inst = os.path.basename(inst_dir)
        state, panes = cabin_state(inst)
        if state == "unknown":
            # 量不到就不列:列了「全部關閉」會把活席當離艙退役(M1)。
            # ★ 10-02 凍結審 MAJOR:跳過的同時,這艙的五碼一律當活的進 live_codes ——
            #   否則別艙同碼的舊席被關時 keep_queue 為假,這艙活席的信箱會被搬走(保守:最多多留一個信箱)。
            for f in files:
                try:
                    with open(f, encoding="utf-8") as fh:
                        rec = json.load(fh)
                except (OSError, ValueError):
                    continue
                code = _code(rec) if isinstance(rec, dict) else ""
                if code:
                    live_codes.add(code)
            continue
        for f in files:
            m = re.match(r"^w(\d+)\.json$", os.path.basename(f))
            if not m:
                continue
            wid = "@" + m.group(1)
            try:
                with open(f, encoding="utf-8") as fh:
                    rec = json.load(fh)
                age = now - os.stat(f).st_mtime
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            code = _code(rec)
            pane = (panes or {}).get(wid)
            if panes is not None and pane and not pane["dead"]:
                if code:
                    live_codes.add(code)
                continue                                      # 活席:不是離艙
            if age < MIN_AGE_S:
                continue
            try:
                pid = int(rec.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            provider = str(rec.get("provider") or "")
            kind = "dead_pane" if pane and pane["dead"] else "record"
            uuid = str(rec.get("session_uuid") or "")
            if kind == "record" and _pid_alive(pid) and _is_seat_process(_pid_command(pid), provider, uuid):
                kind = "running"
            rows.append({"key": "%s|%s" % (inst, wid), "cabin": inst, "cabin4": inst.rsplit("-", 1)[-1][-4:],
                         "cabin_live": panes is not None, "window": wid, "provider": provider,
                         "role": str(rec.get("role") or ""), "code": code, "pid": pid, "kind": kind,
                         "uuid": uuid, "seat_file": f})
    return rows, live_codes


def produce():
    rows, live = scan()
    return {"at": time.time(), "rows": rows, "live_codes": sorted(live)}


# ---------------------------------------------------------------- cache (畫面層只讀這裡)
def _state_dir():
    return os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""


def cache_path():
    d = _state_dir()
    return os.path.join(d, CACHE_NAME) if d else None


def spawn_refresh(path):
    lock = path + ".refresh"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - os.stat(lock).st_mtime < 120:
                return
            os.remove(lock)
        except OSError:
            return
        return spawn_refresh(path)
    except OSError:
        return
    tmp = "%s.%d" % (path, os.getpid())
    kick = os.path.join(os.path.dirname(path), "right-kick")
    # 盤點完順手踢右欄一下:收席後的那一頁不用等 5 秒心跳才回來(the maintainer 09-30「完成後自動恢復」)。
    # ★ 10-03:不再經 /bin/sh(影子續跑看不進 sh ⇒ 畫右欄的測試被判「量不到」)。
    #   子行程直接是 python:produce --to 寫 tmp、os.replace 換上、成功才踢、失敗刪 tmp、一定刪鎖。
    #   同 machine_status_row.spawn_refresh:不加 start_new_session。
    import atomic_refresh
    atomic_refresh.spawn(atomic_refresh.python_argv(
        os.path.abspath(__file__), "produce", "--to", path, "--tmp", tmp, "--lock", lock,
        "--kick", kick), lock)


def produce_to(path, tmp=None, lock=None, kick=None):
    """`produce --to`:背景盤點的子行程本體。成功換上快取後才寫 kick(秒數,同原本的 date +%s)。"""
    import atomic_refresh

    def write(fh):
        fh.write(json.dumps(produce(), ensure_ascii=False) + "\n")
        return 0

    def after():
        if kick:
            with open(kick, "w", encoding="utf-8") as fh:
                fh.write("%d\n" % int(time.time()))

    tmp = tmp or "%s.%d" % (path, os.getpid())
    return atomic_refresh.produce_to(path, tmp, write, lock=lock, after=after)


def read_cached(now=None):
    """(資料或 None, 是否舊了);舊了或沒有就背景更新。"""
    path = cache_path()
    if not path:
        return None, True
    now = time.time() if now is None else now
    data = None
    try:
        age = now - os.stat(path).st_mtime
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        age = None
    stale = age is None or age < 0 or age > TTL_S
    if stale:
        spawn_refresh(path)
    return (data if isinstance(data, dict) else None), stale


# ---------------------------------------------------------------- render
def _vw(s):
    try:
        from render_lib import visible_width
        return visible_width(s)
    except Exception:  # noqa: BLE001
        import unicodedata
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _clip(s, w):
    if _vw(s) <= w:
        return s
    out = ""
    for ch in s:
        if _vw(out + ch) > max(1, w - 1):
            return out + "…"
        out += ch
    return out


def departed_count(data):
    """(離艙席數, 舊席位檔數)。10-13 清查補強 1:以前直接數列數(68),同一個五碼留在好幾艙的重複算、
    已經在別艙續開還活著的五碼也算 ⇒ 跟專案清查對不上。席數 = 不同五碼、而且不在 live_codes(沒五碼的列各算一席);
    列數照舊是「可以收的席位檔」。"""
    rows = (data or {}).get("rows") or []
    live = set((data or {}).get("live_codes") or [])
    codes, nocode = set(), 0
    for r in rows:
        c = r.get("code")
        if not c:
            nocode += 1
        elif c not in live:
            codes.add(c)
    return len(codes) + nocode, len(rows)


def _count_text(data):
    seats, files = departed_count(data)
    if files == seats:
        return "離艙未收 %d 席" % seats
    if seats == 0:
        return "舊席位檔 %d 個(那幾席已在別艙續開)" % files
    return "離艙未收 %d 席(%d 檔)" % (seats, files)     # 審查:右欄窄,長說明會被截掉;說明寫在第 3 頁內文


def summary_line(data):
    rows = (data or {}).get("rows") or []
    if not rows:
        return ""
    return _count_text(data) + " → 看第 3 頁"


SIDE = 2          # 左右各留兩格,與分頁列「  1.AI」、1.AI 頁的通知區同一條邊(the maintainer 09-30「右左邊要留白、對齊」)


def _wrap_parts(parts, width, indent):
    """把幾段字照寬度排成幾行,一段不拆;排不下就換行。每行都帶 indent。"""
    out, cur = [], ""
    for part in parts:
        piece = part if not cur else " · " + part
        if cur and _vw(indent + cur + piece) > width:
            out.append(indent + cur)
            cur = part
        else:
            cur += piece
    if cur:
        out.append(indent + cur)
    return [_clip(x, width) for x in out]


def closing_pending(data):
    """收席還沒完成、或收完之後的新清單還沒盤點出來 ⇒ True(畫 CLOSING_TEXT,不畫舊清單也不畫「量測中」)。
    新清單的盤點時刻晚於記號 ⇒ 記號拿掉,回到原本那一頁(捲動位置在 right-scroll.json,沒動過)。"""
    d = _state_dir()
    if not d:
        return False
    marker = os.path.join(d, CLOSING_NAME)
    try:
        mark = os.stat(marker).st_mtime
    except OSError:
        return False
    if time.time() - mark > 120:          # 兩分鐘還沒回來:別永遠卡在這句,照常畫(有資料畫資料,沒有說量測中)
        try:
            os.remove(marker)
        except OSError:
            pass
        return False
    try:
        at = float((data or {}).get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    if data is not None and at > mark:
        try:
            os.remove(marker)
        except OSError:
            pass
        return False
    path = cache_path()
    if path and data is not None:
        spawn_refresh(path)               # 手上這份是收完之前盤的:再盤一次(鎖擋重複),不等 30 秒過期
    return True


def render(width, row_offset, data, stale=False, height=0):
    """回 (lines, rowmap)。每一行左邊從第 SIDE 欄起、右邊留 SIDE 欄;按鈕靠右對齊在同一欄。"""
    width = max(20, int(width))
    inner = width - SIDE                  # 右邊留白之後,字最多到這一欄(不含)
    ind, ind2 = " " * SIDE, " " * (SIDE + 2)
    lines, rowmap = [""], []          # 分頁列下面空一行(the maintainer 09-30);點擊表的列號照 len(lines) 算,自動跟著移

    def with_button(left, btn, link):
        left = _clip(left, inner - _vw(btn) - 1)
        x0 = inner - _vw(btn)
        link = dict(link, start=x0, end=x0 + _vw(btn))
        rowmap.append({"line": row_offset + len(lines), "links": [link]})
        lines.append(left + " " * (x0 - _vw(left)) + btn)

    if closing_pending(data):
        if _vw(ind + CLOSING_TEXT) <= inner:
            return lines + [ind + CLOSING_TEXT], []
        head, _, tail = CLOSING_TEXT.partition("！")          # 放不下就在「！」後面換行,不截字
        return lines + [_clip(ind + head + "！", inner), _clip(ind + tail, inner)], []
    if data is None:
        return lines + [ind + "量測中…(離艙席在背景盤點)"], []
    rows = data.get("rows") or []
    if not rows:
        return lines + _wrap_parts(["沒有離艙未收的席位。"], inner, ind) + [""] + \
            _wrap_parts(["艙已經關掉、但席位紀錄或窗格還留著的,會列在這裡"]
                       + (["每一席右邊有 <X> 可以收掉"] if close_enabled() else []), inner, ind), []
    closable = [r for r in rows if r["kind"] in ("record", "dead_pane")]
    head = ind + _count_text(data)
    can_close = close_enabled()
    if closable and can_close:
        with_button(head, "<全收>", {"action": CLOSE_ALL_ACTION, "count": len(closable)})
    else:
        lines.append(_clip(head, inner))
    if not can_close:
        lines.extend(_wrap_parts(["這一版先只顯示,不能在這裡收掉", "下一版開放"], inner, ind))
    lines.append("")                  # 說明行上方空一行(the maintainer 09-30)
    lines.extend(_wrap_parts(["只剩紀錄=程式已結束", "死窗=窗還在但死了", "還在跑=要逐席收"], inner, ind))
    by_cabin = {}
    for r in rows:
        by_cabin.setdefault(r["cabin"], []).append(r)
    lines.append("")
    header_rows = len(lines)          # 以上固定不捲;以下(各艙與各席)捲動,右上 ▲、右下 ▼ 同 2.AI用量(the maintainer 09-30)
    for cabin in sorted(by_cabin, reverse=True):
        rs = by_cabin[cabin]
        when = cabin[4:8] + "-" + cabin[9:13] if len(cabin) >= 13 else cabin
        lines.append(_clip(ind + "%s 艙(%s,%s)" % (rs[0]["cabin4"], when, "還開著" if rs[0]["cabin_live"] else "已關"), inner))
        for r in rs:
            code = ("@" + r["code"]) if r["code"] else "(無五碼)"
            text = ind2 + "%s %s %s %s" % (r["window"], r["provider"] or "?", code, KIND_LABEL[r["kind"]])
            if not can_close:
                lines.append(_clip(text, inner))
                continue
            with_button(text, "<X>",
                        {"action": CLOSE_ACTION, "key": r["key"],
                         "label": "%s %s %s" % (r["cabin4"], ("@" + r["code"]) if r["code"] else r["window"],
                                                KIND_LABEL[r["kind"]])})
    if stale:
        lines.append("")
        lines.append(ind + "(更新中…)")
    if height:
        lines, rowmap = _viewport(lines, rowmap, row_offset, header_rows, height, width)
    return lines, rowmap


def _scroll_offset():
    try:
        with open(os.path.join(_state_dir(), "right-scroll.json"), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    try:
        from right_fold_api import viewport
    except ImportError:
        from render.right_fold_api import viewport
    return viewport.tab_offset(state, "left")


def _save_limit(limit):
    """捲到底的上限寫進 .right-scroll-limit.json 的 left,滾輪與 ▼ 才知道在哪裡停(同 1.AI / 2.AI用量)。"""
    d = _state_dir()
    if not d:
        return
    path = os.path.join(d, ".right-scroll-limit.json")
    try:
        with open(path, encoding="utf-8") as fh:
            limits = json.load(fh)
    except (OSError, ValueError):
        limits = {}
    if not isinstance(limits, dict):
        limits = {}
    if limits.get("left") == limit:
        return
    limits["left"] = limit
    tmp = "%s.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(limits, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _viewport(lines, rowmap, row_offset, header_rows, height, width):
    """用 2.AI用量 同一支 fit_usage_viewport:右上 ▲、右下 ▼,點一下三列;列上的 <X> 跟著可見的那一列走。"""
    try:
        from render_lib import fit_usage_viewport
    except ImportError:
        return lines, rowmap
    total = max(1, int(height) - row_offset)
    rel = [dict(e, line=e["line"] - row_offset) for e in rowmap]
    body_n = max(0, len(lines) - header_rows)
    room = total - header_rows
    _save_limit(max(0, body_n - room) if room > 0 else 0)
    out, mapped = fit_usage_viewport(lines, rel, header_rows=header_rows, total_rows=total,
                                     offset=_scroll_offset(), width=width, bar_arrows=True, arrow_tab="left")
    return out, [dict(e, line=e["line"] + row_offset) for e in mapped]


def write_rowmap(rowmap):
    d = _state_dir()
    if not d:
        return
    path = os.path.join(d, "ai-rowmap.json")
    tmp = "%s.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rowmap, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- close
def _log(entry):
    d = _state_dir() or os.path.join(aib_home(), "state")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "leftover-close.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(entry, at=time.strftime("%Y-%m-%dT%H:%M:%S%z")), ensure_ascii=False) + "\n")
    except OSError:
        pass


def close_one(key, dry_run=False, rows=None, live_codes=None):
    """回 (ok, 一句白話)。"""
    if not close_enabled():
        _log({"key": key, "result": "refused", "why": "本版停用收掉"})
        return False, "這一版先停用收掉離艙席(只顯示),什麼都沒動"
    if not _KEY_RE.fullmatch(key or ""):
        return False, "位址格式不對:%r" % key
    if rows is None:
        rows, live_codes = scan()
    row = next((r for r in rows if r["key"] == key), None)
    if row is None:
        _log({"key": key, "result": "refused", "why": "重掃後已不是離艙"})
        return False, "%s 現在已經不是離艙席(可能艙又起來了),沒有動它" % key
    inst, wid = row["cabin"], row["window"]
    did = []
    if row["kind"] == "running":
        cmd = _pid_command(row["pid"])
        if row["pid"] <= 0 or not _is_seat_process(cmd, row["provider"], row.get("uuid", "")):
            return False, "pid %d 看起來已經不是那一席的程式(指令裡沒有這一席的對話編號),沒有殺" % row["pid"]
        if not dry_run:
            try:
                os.kill(row["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                return False, "殺不掉 pid %d(%s)" % (row["pid"], exc)
        did.append("TERM pid %d" % row["pid"])
    elif row["kind"] == "dead_pane":
        if not dry_run:
            try:
                _tmux(inst, "kill-window", "-t", wid)
            except (OSError, subprocess.TimeoutExpired):
                pass
        did.append("kill-window %s" % wid)
    keep_queue = bool(row["code"]) and row["code"] in (live_codes or set())
    import seat_retire
    res = seat_retire.retire(wid, seats=os.path.join(instances_root(), inst, "seats"), by="leftover-page",
                             dry_run=dry_run, keep_queue=keep_queue)
    did.append("席位檔→retired" + ("(佇列留給活席)" if keep_queue else ""))
    _log({"key": key, "kind": row["kind"], "result": "dry-run" if dry_run else "closed", "did": did,
          "seat_file": res.get("seat_file"), "queue_dir": res.get("queue_dir")})
    return True, "%s @%s:%s" % (row["cabin4"], row["code"] or wid, ",".join(did))


def close_all(dry_run=False):
    if not close_enabled():
        return 0
    rows, live = scan()
    n = 0
    for r in rows:
        if r["kind"] in ("record", "dead_pane"):
            ok, _ = close_one(r["key"], dry_run=dry_run, rows=rows, live_codes=live)
            n += 1 if ok else 0
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("produce")
    pr.add_argument("--to", default="")      # 有就自己寫快取(tmp → os.replace),不印到 stdout
    pr.add_argument("--tmp", default="")
    pr.add_argument("--lock", default="")
    pr.add_argument("--kick", default="")
    r = sub.add_parser("render")
    r.add_argument("--width", type=int, default=44)
    r.add_argument("--row-offset", type=int, default=2)
    r.add_argument("--height", type=int, default=0)
    sub.add_parser("summary")
    c = sub.add_parser("close")
    c.add_argument("--key", required=True)
    c.add_argument("--dry-run", action="store_true")
    ca = sub.add_parser("close-all")
    ca.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "produce":
        if a.to:
            return produce_to(a.to, tmp=a.tmp or None, lock=a.lock or None, kick=a.kick or None)
        print(json.dumps(produce(), ensure_ascii=False))
    elif a.cmd == "render":
        data, stale = read_cached()
        lines, rowmap = render(a.width, a.row_offset, data, stale=stale and data is not None, height=a.height)
        write_rowmap(rowmap)
        print("\n".join(lines))
    elif a.cmd == "summary":
        data, _ = read_cached()
        s = summary_line(data)
        if s:
            print(s)
    elif a.cmd == "close":
        ok, msg = close_one(a.key, dry_run=a.dry_run)
        print(msg)
        return 0 if ok else 3
    elif a.cmd == "close-all":
        print(close_all(dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
