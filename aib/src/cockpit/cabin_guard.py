#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cabin_guard.py —— 艙的自動接回(2026-10-10 the maintainer 第 1 項,計畫 15:5x 核准)。

要解的事:Terminal.app 當掉(10-05 ×3、10-09 18:57、10-10 00:24/00:26)或一扇視窗自己不見(10-10 13:40)時,
艙(tmux)全活,只是沒人接著;以前要人跑 regroup.sh attach,一艙一艙拉。現在艙自己發現、自己開一扇
Terminal 視窗接回來。

★ 「沒人接著」是正常狀態(§59:沒人看的艙不渲染、繼續工作;閒置收艙也以它為條件),所以**不能看到沒人就接**。
只在「剛才還有人、現在沒了、而且有壞掉的證據」才接:
  ① 轉換:上一拍 session_attached=1,這一拍 =0
  ② 去抖:等 DEBOUNCE_S 秒再看,還是 0 才算(attach -d 換視窗時會有一瞬間沒人)
  ③ 證據(二擇一):(a) Terminal 的 pid 跟上一拍不同或 Terminal 不在 = 整個 Terminal 重開/死掉;
                   (b) 最近 EXC_WINDOW_S 秒內 Terminal 記過 NSRangeException = 13:40 那種視窗自己不見
  ④ 反抖:同一艙 FLAP_WINDOW_S 內最多接 FLAP_MAX 次,超過只通知
①②成立、③不成立 ⇒ 不接(像是有人故意關掉的),只留痕 + 通知一次。
失效側一律**不接**:tmux 問不到、pgrep 失敗、log 撈不到 ⇒ 當沒證據。寧可人手動 attach 一次,不可自動開一堆視窗。

放在哪:idle_reaper.py watch 啟動時起一條 daemon 執行緒(start_thread),TICK_S 秒一拍;不開新窗、不加 pane
(fleet_patrol / machine_status 數窗的名單不用動),跟著 watch 的心跳與重生機制走。
怎麼接:重用 cabin_restart.attach_cabin(osascript 開 Terminal 視窗 `attach -d`,回讀 list-clients 確認接上)。
留痕:每個決定一行 JSON 寫 <instance home>/cockpit/auto-attach.log。
開關:EZ2_CABIN_GUARD=0 不起;AIB_GUARD_DRY_RUN=1 只留痕不開窗(整合測試走這條)。
停電/全機重開不歸它管,那是 aib restart。

風暴偵測(10-10 18:29 實踩的第三種形態「視窗殭屍」):Terminal 沒死、客戶端還附著,但那扇視窗從某一次例外起
**每一批輸出都丟 NSRangeException**(每秒 60–100 發,兩分鐘 8890 發),畫面不動=「跳掉了」。attached→無人那條抓不到它。
做法:每 STORM_CHECK_S 秒數最近 STORM_CHECK_S 秒的例外;≥ STORM_MIN 發(正常一天才 17 發)就逐艙開新視窗 attach -d
踢掉舊視窗(先踢最近有人動的那艙),每踢一艙等 STORM_SETTLE_S 秒再數,降到 < STORM_QUIET 就停手;全域 stamp 當鎖只一艙做;
STORM_FLAP_WINDOW_S 內最多踢 STORM_FLAP_MAX 艙。18:29 實跑:踢 931e 沒停(1127/10s),踢 9668 才歸零 ⇒ 所以要一艙一艙踢、每艙量一次。

第 3 項(同一條執行緒,ARCHIVE_EVERY_S 一輪):把 Terminal 的 NSRangeException 抄到 ~/.ez2-aib/logs/terminal-ime/<日>.log、
每小時計數在 state/terminal-exceptions.json,剛炸過就提醒「現在別在 Terminal 打中文」;`--stats` 印每小時表。

單獨跑(除錯):python3 cabin_guard.py --session aib-2026…-931e --cabin 2026…-931e [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUTER_SOCKET = "default"
TICK_S = 5
DEBOUNCE_S = 10
EXC_WINDOW_S = 90
FLAP_MAX = 3
FLAP_WINDOW_S = 600
LOG_NAME = "auto-attach.log"

ATTACH, SKIP, NOTIFY = "attach", "skip", "notify"


# ── 純判斷(可測):輸入都是量到的事實,量不到用 None ─────────────────────────────────
def decide(prev_attached, now_attached, prev_term_pid, now_term_pid, exc_recent, reattach_times, now):
    """回 (action, why)。action ∈ {attach, skip, notify}。

    prev/now_attached:上一拍/這一拍有沒有人(True/False/None=量不到)。
    prev/now_term_pid:Terminal.app 的 pid(None=沒在跑或量不到;量不到與沒在跑在這裡刻意同待遇——
      只有「上一拍量到、這一拍不同」才算重開,None→None 不算)。
    exc_recent:最近 EXC_WINDOW_S 秒內 Terminal 有沒有記 NSRangeException(True/False/None=撈不到)。
    reattach_times:這艙最近接過的時間戳(epoch 秒)。
    """
    if prev_attached is None or now_attached is None:
        return SKIP, "量不到附著(tmux 問不到),不動"
    if now_attached:
        return SKIP, "有人看"
    if not prev_attached:
        return SKIP, "本來就沒人(§59 detached 是正常態)"
    recent = [t for t in reattach_times if now - t <= FLAP_WINDOW_S]
    if len(recent) >= FLAP_MAX:
        return NOTIFY, "反抖:%d 分鐘內已接 %d 次,停手" % (FLAP_WINDOW_S // 60, len(recent))
    if prev_term_pid is not None and now_term_pid != prev_term_pid:
        return ATTACH, ("Terminal 重開過(pid %s→%s)" % (prev_term_pid, now_term_pid)) if now_term_pid else \
                       ("Terminal 不在了(pid %s→無)" % prev_term_pid)
    if exc_recent:
        return ATTACH, "最近 %d 秒內 Terminal 記了 NSRangeException(視窗自己不見那種)" % EXC_WINDOW_S
    return NOTIFY, "剛才有人、現在沒了,但沒有壞掉的證據 ⇒ 像是有人關掉的,不自動接"


# ── 探針(可注入) ─────────────────────────────────────────────────────────────────
def probe_attached(session):
    try:
        p = subprocess.run(["tmux", "-L", OUTER_SOCKET, "display-message", "-p", "-t", session, "#{session_attached}"],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    s = p.stdout.strip()
    if not s.isdigit():
        return None
    return int(s) > 0


def probe_term_pid():
    try:
        p = subprocess.run(["pgrep", "-x", "Terminal"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    pids = [int(x) for x in p.stdout.split() if x.isdigit()]
    return min(pids) if pids else None


def probe_exc_recent(window_s=EXC_WINDOW_S):
    """★ 一定用 /usr/bin/log:zsh 的內建 log 會撈到空的(10-10 實踩)。撈不到回 None。"""
    try:
        p = subprocess.run(["/usr/bin/log", "show", "--last", "%ds" % int(window_s), "--style", "compact",
                            "--predicate", 'process == "Terminal" AND eventMessage CONTAINS "NSRangeException"'],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return any("NSRangeException" in ln for ln in p.stdout.splitlines())


def do_attach(session, cabin, dry_run=False):
    if dry_run:
        return True, "dry-run:沒開窗"
    try:
        import cabin_restart  # noqa: E402
        import io
        buf = io.StringIO()
        ok = cabin_restart.attach_cabin({"cabin": cabin, "session": session}, out=buf)
        return bool(ok), buf.getvalue().strip()
    except Exception as exc:  # noqa: BLE001
        return False, "attach_cabin 例外:%s" % exc


def do_notify(text):
    """盡力而為:macOS 通知 + 每個有人看的座艙狀態列各顯示一次。失敗不吵。"""
    try:
        subprocess.run(["osascript", "-e", 'display notification %s with title "Ez2 AIB"' % json.dumps(text)],
                       capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        p = subprocess.run(["tmux", "-L", OUTER_SOCKET, "list-clients", "-F", "#{client_tty}"],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        for tty in p.stdout.split():
            subprocess.run(["tmux", "-L", OUTER_SOCKET, "display-message", "-c", tty, "-d", "5000", text],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except Exception:
        pass


# ── 第 3 項:量 + 留證據(2026-10-10 the maintainer 核准)──────────────────────────────────────
# 統一日誌只保留約 10 小時(10-09 18:47 的證據就是這樣沒的)。守衛每 ARCHIVE_EVERY_S 秒把 Terminal 的
# NSRangeException 行抄到 ~/.ez2-aib/logs/terminal-ime/<日>.log,維護每小時計數(state/terminal-exceptions.json),
# 剛炸過就提醒「現在別在 Terminal 打中文」(CUE_EVERY_S 內只提醒一次)。N 艙只要一艙做:全域 stamp 當鎖。
# 不進 machine_status 的格子:它整輪預算 1 秒,撈 log 要 1 秒以上。看數字用 `cabin_guard.py --stats`。
STORM_CHECK_S = 15          # 10-11 the maintainer 第 3 點:30→15 秒一看(三場風暴頭 5 秒都只有 3–15 發,太早判會漏;15 秒 + 分格規則折衷)
STORM_MIN = 30              # 15 秒 ≥30 發(正常一天 17 發、零星一段 1–11 發)
STORM_BIN_S = 5             # 分格:連續兩個 5 秒格各 ≥ STORM_BIN_MIN 也算風暴(18:29 那場頭 10 秒 15+48 就會中)
STORM_BIN_MIN = 8
STORM_KICK_ALL_MAX = 3      # 10-11 the maintainer 第 2 點:附著的艙 ≤3 就一次全踢——「最近有人動的先踢」猜了兩次錯兩次(殭屍視窗你還在往裡打字,看起來最活躍)
STORM_SETTLE_S = 10
STORM_QUIET = 5
STORM_FLAP_MAX = 3
STORM_FLAP_WINDOW_S = 600
STORM_STAMP = os.path.join(os.path.expanduser(os.environ.get("EZ2_AIB_STATE_ROOT") or "~/.ez2-aib"), "state", "terminal-storm.stamp")

ARCHIVE_EVERY_S = 300
ARCHIVE_LOOKBACK_S = 420        # 比一輪多兩分鐘,輪子慢了也不漏;重複的靠時間戳去掉
CUE_EVERY_S = 600
AIB_HOME = os.path.expanduser(os.environ.get("EZ2_AIB_STATE_ROOT") or "~/.ez2-aib")
ARCHIVE_DIR = os.path.join(AIB_HOME, "logs", "terminal-ime")
EXC_STATE = os.path.join(AIB_HOME, "state", "terminal-exceptions.json")
ARCHIVE_STAMP = os.path.join(AIB_HOME, "state", "terminal-ime-archive.stamp")
_EXC_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\S*\s+\S+\s+Terminal\[(\d+):[0-9a-f]+\].*NSRangeException")


def fetch_terminal_log(window_s=ARCHIVE_LOOKBACK_S):
    """★ /usr/bin/log(zsh 內建 log 會撈到空的)。撈不到回 None,不回空字串(空字串 = 真的沒有)。"""
    try:
        p = subprocess.run(["/usr/bin/log", "show", "--last", "%ds" % int(window_s), "--style", "compact",
                            "--predicate", 'process == "Terminal" AND eventMessage CONTAINS "NSRangeException"'],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


def parse_exception_lines(text):
    """只取每發的那一行標頭(`NSRangeException <redacted reason>`),不取後面的堆疊;回 [(ts, pid, line)] 依 ts 排。"""
    out = []
    for ln in (text or "").splitlines():
        if "<redacted" not in ln:
            continue
        m = _EXC_RE.match(ln)
        if m:
            out.append((m.group(1), int(m.group(2)), ln.rstrip()))
    out.sort()
    return out


def load_exc_state(path=EXC_STATE):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("hours", {}); d.setdefault("last_ts", ""); d.setdefault("total", 0)
            return d
    except (OSError, ValueError):
        pass
    return {"hours": {}, "last_ts": "", "total": 0, "last_cue": 0}


def save_exc_state(state, path=EXC_STATE):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _seen_in_archive(adir, days):
    """那幾天的日誌檔裡已經抄過的時間戳集合(去重用;檔不在=空集合)。"""
    seen = set()
    for day in days:
        try:
            with open(os.path.join(adir, day + ".log"), encoding="utf-8") as f:
                for ln in f:
                    m = _EXC_RE.match(ln)
                    if m:
                        seen.add(m.group(1))
        except OSError:
            pass
    return seen


def ingest(state, events, adir=None):
    """把沒抄過的事件計進 state;回新事件清單。
    ★ 去重對「那一天日誌檔裡已有的時間戳」,不是對 last_ts 單調比較:10-10 23:1x 實踩——先抄了 23:08 的風暴,
      再想回填 18:29 的(--lookback 5 小時)全被 last_ts 擋掉,回填永遠做不到。日誌檔一天最多幾千行,掃一次很便宜。"""
    days = sorted({e[0][:10] for e in events})
    seen = _seen_in_archive(adir, days) if adir else set()
    last = state.get("last_ts") or ""
    new = [e for e in events if e[0] not in seen and (adir or e[0] > last)]
    for ts, _pid, _ln in new:
        h = ts[:13]                                   # "YYYY-MM-DD HH"
        state["hours"][h] = int(state["hours"].get(h, 0)) + 1
        state["total"] = int(state.get("total", 0)) + 1
        if ts > (state.get("last_ts") or ""):
            state["last_ts"] = ts
    # 只留最近 7 天的小時格,檔案不長大
    keys = sorted(state["hours"])
    if len(keys) > 24 * 7:
        for k in keys[:-24 * 7]:
            state["hours"].pop(k, None)
    return new


def _stamp_fresh(path, every_s, now):
    """stamp 內容是上一輪的 epoch 秒(不用檔案 mtime:呼叫端的 now 可注入,測試才對得上)。"""
    try:
        with open(path, encoding="utf-8") as f:
            last = float((f.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return False
    return 0 <= now - last < every_s - 60


def archive_once(*, fetch=fetch_terminal_log, now=None, notify=None, home=None, force=False):
    """一輪:全域 stamp 當鎖(別艙剛做過就跳過)→ 撈 → 抄 → 計數 → 剛炸過就提醒。回 (新事件數, 為什麼/None)。"""
    now = time.time() if now is None else now
    adir = os.path.join(home, "logs", "terminal-ime") if home else ARCHIVE_DIR
    spath = os.path.join(home, "state", "terminal-exceptions.json") if home else EXC_STATE
    stamp = os.path.join(home, "state", "terminal-ime-archive.stamp") if home else ARCHIVE_STAMP
    if not force and _stamp_fresh(stamp, ARCHIVE_EVERY_S, now):
        return 0, "別艙剛做過"
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(int(now)))
    except OSError:
        pass
    text = fetch(ARCHIVE_LOOKBACK_S)
    if text is None:
        return 0, "log 撈不到"
    state = load_exc_state(spath)
    new = ingest(state, parse_exception_lines(text), adir=adir)
    if new:
        try:
            os.makedirs(adir, exist_ok=True)
            by_day = {}
            for ts, _pid, ln in new:
                by_day.setdefault(ts[:10], []).append(ln)
            for day, lines in by_day.items():
                with open(os.path.join(adir, day + ".log"), "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except OSError:
            pass
        if notify and now - float(state.get("last_cue") or 0) >= CUE_EVERY_S:
            notify("Terminal 剛記了 %d 發 NSRangeException(最近一發 %s):現在別在 Terminal 打中文,先把東西存了" % (len(new), new[-1][0][11:19]))
            state["last_cue"] = now
    save_exc_state(state, spath)
    return len(new), None


def stats_text(state, now=None, hours=24):
    """每小時幾發,最近 N 小時;沒有的小時印 0。給 `--stats`。"""
    now = time.time() if now is None else now
    rows = []
    for i in range(hours - 1, -1, -1):
        t = time.localtime(now - i * 3600)
        k = time.strftime("%Y-%m-%d %H", t)
        rows.append("%s  %3d" % (k, int(state.get("hours", {}).get(k, 0))))
    total = sum(int(state.get("hours", {}).get(time.strftime("%Y-%m-%d %H", time.localtime(now - i * 3600)), 0)) for i in range(hours))
    rows.append("最近 %d 小時合計 %d 發;有紀錄以來 %d 發;最後一發 %s" % (hours, total, int(state.get("total", 0)), state.get("last_ts") or "無"))
    return "\n".join(rows)


# ── 風暴偵測 ─────────────────────────────────────────────────────────────────────
def probe_exc_lines(window_s):
    """最近 window_s 秒 Terminal 的 NSRangeException 標頭行(每發一行);撈不到回 None。★ /usr/bin/log。"""
    try:
        p = subprocess.run(["/usr/bin/log", "show", "--last", "%ds" % int(window_s), "--style", "compact",
                            "--predicate", 'process == "Terminal" AND eventMessage CONTAINS "NSRangeException"'],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return [ln for ln in p.stdout.splitlines() if "<redacted" in ln or "NSRangeException <" in ln]


def probe_exc_count(window_s):
    """最近 window_s 秒幾發;撈不到回 None。"""
    lines = probe_exc_lines(window_s)
    return None if lines is None else len(lines)


def bin_counts(lines, window_s, bin_s, now=None):
    """把標頭行依時間戳分到 window_s/bin_s 個格(最舊在前);時間戳讀不到的行算進最新一格。"""
    now = time.time() if now is None else now
    n = max(1, int(window_s // bin_s))
    bins = [0] * n
    for ln in lines or []:
        m = _EXC_RE.match(ln)
        t = None
        if m:
            try:
                t = time.mktime(time.strptime(m.group(1)[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                t = None
        age = (now - t) if t is not None else 0
        i = n - 1 - int(age // bin_s)
        bins[max(0, min(n - 1, i))] += 1
    return bins


def is_storm(lines, window_s=STORM_CHECK_S, bin_s=STORM_BIN_S, now=None):
    """風暴判定(10-11 第 3 點):總數 ≥ STORM_MIN,或連續兩格各 ≥ STORM_BIN_MIN。lines=None(撈不到)⇒ (None, …)。"""
    if lines is None:
        return None, []
    bins = bin_counts(lines, window_s, bin_s, now)
    if len(lines) >= STORM_MIN:
        return True, bins
    for a, b in zip(bins, bins[1:]):
        if a >= STORM_BIN_MIN and b >= STORM_BIN_MIN:
            return True, bins
    return False, bins


def probe_attached_sessions():
    """[(session, client_activity_epoch)] 有人接著的座艙 session,最近有人動的排前面;問不到回 None。"""
    try:
        p = subprocess.run(["tmux", "-L", OUTER_SOCKET, "list-clients", "-F", "#{client_session}\t#{client_activity}"],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    best = {}
    for ln in p.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) != 2 or not parts[0].startswith("aib-"):
            continue
        try:
            act = int(parts[1])
        except ValueError:
            act = 0
        best[parts[0]] = max(best.get(parts[0], 0), act)
    return sorted(best.items(), key=lambda kv: -kv[1])


def kick_session(session, dry_run=False):
    """開新視窗 attach -d(踢掉殭屍視窗)。回 (ok, detail)。"""
    cabin = session[4:] if session.startswith("aib-") else session
    return do_attach(session, cabin, dry_run=dry_run)


def storm_once(*, count=probe_exc_count, lines_fn=None, sessions=probe_attached_sessions, kick=kick_session, sleep=time.sleep,
               now=None, log=None, notify=None, kicks=None, dry_run=False, stamp=STORM_STAMP, force=False):
    """一輪:數 → 沒風暴就回 ("quiet", n) → 有:鎖 → 逐艙踢、每艙等 STORM_SETTLE_S 秒再數 → 回 (結果, 踢了幾艙)。
    kicks:呼叫端持有的最近踢過時間戳(反抖)。失效側:數不到/問不到艙 ⇒ 不動。"""
    now = time.time() if now is None else now
    log = log or (lambda **r: None)
    lines = lines_fn(STORM_CHECK_S) if lines_fn else None
    if lines_fn:
        storm, bins = is_storm(lines, now=now)
        n = None if lines is None else len(lines)
    else:                                   # 舊式注入:只給總數
        n = count(STORM_CHECK_S); storm, bins = (None, []) if n is None else (n >= STORM_MIN, [])
    if n is None:
        return "unmeasurable", 0
    if not storm:
        return "quiet", n
    if not force and _stamp_fresh(stamp, STORM_CHECK_S * 2 + 60, now):   # _stamp_fresh 會先扣 60 秒 ⇒ 實際鎖 60 秒
        log(event="storm-skip", why="別艙剛處理過", count=n)
        return "locked", n
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(int(now)))
    except OSError:
        pass
    recent = [t for t in (kicks or []) if now - t <= STORM_FLAP_WINDOW_S]
    if len(recent) >= STORM_FLAP_MAX:
        log(event="storm-notify", why="反抖:%d 分鐘內已踢 %d 艙,停手" % (STORM_FLAP_WINDOW_S // 60, len(recent)), count=n)
        if notify:
            notify("Terminal 例外風暴(%d 發/%d 秒)還在,但 10 分鐘內已踢 %d 艙,停手;請手動開新視窗 attach -d" % (n, STORM_CHECK_S, len(recent)))
        return "flap", n
    sess = sessions()
    if not sess:
        log(event="storm-notify", why="有風暴但問不到有人接著的艙", count=n)
        return "no-sessions", n
    kicked = 0
    if len(sess) <= STORM_KICK_ALL_MAX:
        # 第 2 點:艙少就一次全踢,不猜哪扇壞(猜錯一次多等 10 秒,而且猜了兩次錯兩次)
        for session, _act in sess:
            ok, detail = kick(session, dry_run)
            kicked += 1
            if kicks is not None:
                kicks.append(now)
            log(event="storm-kick", session=session, attach_ok=ok, attach_detail=detail, count_before=n, dry_run=dry_run, mode="all")
        sleep(STORM_SETTLE_S)
        m = count(STORM_SETTLE_S)
        log(event="storm-after-kick", session="*", count_after=m)
        if m is None or m < STORM_QUIET:
            if notify:
                notify("Terminal 例外風暴(%d 發)— 已把 %d 個艙換到新視窗,風暴停了" % (n, kicked))
            return "stopped", kicked
    else:
        for session, _act in sess:
            ok, detail = kick(session, dry_run)
            kicked += 1
            if kicks is not None:
                kicks.append(now)
            log(event="storm-kick", session=session, attach_ok=ok, attach_detail=detail, count_before=n, dry_run=dry_run, mode="one-by-one")
            sleep(STORM_SETTLE_S)
            m = count(STORM_SETTLE_S)
            log(event="storm-after-kick", session=session, count_after=m)
            if m is None or m < STORM_QUIET:
                if notify:
                    notify("Terminal 例外風暴(%d 發)— 已把艙 %s 換到新視窗,風暴停了" % (n, session[-4:]))
                return "stopped", kicked
            if kicked >= STORM_FLAP_MAX:
                break
    if notify:
        notify("Terminal 例外風暴踢了 %d 艙還沒停;請手動處理" % kicked)
    return "persist", kicked


# ── 守衛本體 ───────────────────────────────────────────────────────────────────────
class Guard:
    def __init__(self, session, cabin, log_path, *, dry_run=False, tick_s=TICK_S, debounce_s=DEBOUNCE_S,
                 attached=None, term_pid=None, exc_recent=None, attach=None, notify=None, sleep=time.sleep, clock=time.time):
        self.session, self.cabin, self.log_path, self.dry_run = session, cabin, log_path, dry_run
        self.tick_s, self.debounce_s = tick_s, debounce_s
        self._attached = attached or (lambda: probe_attached(session))
        self._term_pid = term_pid or probe_term_pid
        self._exc_recent = exc_recent or probe_exc_recent
        self._attach = attach or (lambda: do_attach(session, cabin, dry_run=dry_run))
        self._notify = notify or do_notify
        self._sleep, self._clock = sleep, clock
        self.prev_attached = None
        self.prev_term_pid = None
        self.reattach_times = []
        self.events = []        # 測試用:本程序內的決定紀錄
        self.archive_every_s = ARCHIVE_EVERY_S
        self.storm_check_s = STORM_CHECK_S
        self._next_storm = 0.0
        self.storm_kicks = []
        self._storm = lambda: storm_once(lines_fn=probe_exc_lines, log=self.log, notify=self._notify, kicks=self.storm_kicks, dry_run=self.dry_run)
        self._next_archive = 0.0
        self._archive = lambda: archive_once(notify=self._notify)   # 第 3 項:量 + 留證據(可注入)

    def log(self, **row):
        row.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        row.setdefault("cabin", self.cabin)
        self.events.append(row)
        try:
            d = os.path.dirname(self.log_path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def tick(self):
        """一拍。回 (action, why);沒有轉換時回 (skip, …) 且不寫 log(不要每 5 秒一行)。"""
        now_att = self._attached()
        now_pid = self._term_pid()
        prev_att, prev_pid = self.prev_attached, self.prev_term_pid
        self.prev_attached, self.prev_term_pid = now_att, now_pid
        if not (prev_att is True and now_att is False):
            return SKIP, "沒有轉換"
        # ② 去抖
        self._sleep(self.debounce_s)
        again = self._attached()
        if again is not False:
            self.log(event="skip", why="去抖期間回來了(attach -d 換視窗之類)", attached_prev=prev_att, attached_now=again)
            self.prev_attached = again
            return SKIP, "去抖期間回來"
        now = self._clock()
        exc = self._exc_recent() if (prev_pid is not None and now_pid == prev_pid) else None   # 只有「沒重開」才需要撈 log
        action, why = decide(prev_att, False, prev_pid, now_pid, exc, self.reattach_times, now)
        row = dict(event=action, why=why, attached_prev=prev_att, attached_now=False,
                   term_pid_prev=prev_pid, term_pid_now=now_pid, exc_recent=exc, dry_run=self.dry_run)
        if action == ATTACH:
            self.reattach_times.append(now)
            ok, detail = self._attach()
            row.update(attach_ok=ok, attach_detail=detail)
            self.log(**row)
            self._notify("艙 %s 自動接回%s:%s" % (self.cabin[-4:], "" if ok else "失敗", why))
            return action, why
        self.log(**row)
        if action == NOTIFY:
            self._notify("艙 %s 沒人接著,沒自動接:%s。要接:regroup.sh attach --only %s" % (self.cabin[-4:], why, self.cabin[-4:]))
        return action, why

    def run(self, max_ticks=None):
        n = 0
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 守衛不得死
                self.log(event="error", why="tick 例外:%s" % exc)
            try:
                now = self._clock()
                if now >= self._next_storm:
                    self._next_storm = now + self.storm_check_s
                    self._storm()
            except Exception as exc:  # noqa: BLE001 風暴偵測失敗也不得死
                self.log(event="error", why="storm 例外:%s" % exc)
            try:
                now = self._clock()
                if now >= self._next_archive:
                    self._next_archive = now + self.archive_every_s
                    self._archive()
            except Exception as exc:  # noqa: BLE001 抄 log 失敗也不得死
                self.log(event="error", why="archive 例外:%s" % exc)
            n += 1
            if max_ticks and n >= max_ticks:
                return
            self._sleep(self.tick_s)


def _session_for(cabin):
    try:
        p = subprocess.run(["bash", os.path.join(HERE, "cabin_resolve.sh"), "--session", cabin],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        s = p.stdout.strip()
        if p.returncode == 0 and s:
            return s
    except Exception:
        pass
    return None   # R8:量不到就說量不到,不自己拼座艙名


def _switches(env):
    """守衛自己的兩個開關(關掉 / 只留痕不開窗)。★ 另包一支小函式,不跟艙名字同函式讀:
    test-cabin-single-source-names 的判別式是「同一個函式裡讀到艙單源名字,那函式讀的每個 env 都算艙消費者」,
    這兩個開關不是艙變數(開席時不該被洗),所以不能跟 EZ2_AIB_INSTANCE 同函式讀(10-10 推前檢查紅,a seat 退回)。"""
    return env.get("EZ2_CABIN_GUARD", "1") == "0", env.get("AIB_GUARD_DRY_RUN") == "1"


def start_thread(env=None):
    """給 idle_reaper watch 用:量得到艙才起;起不來回 (False, 為什麼),不拋。"""
    env = os.environ if env is None else env
    off, dry = _switches(env)
    if off:
        return False, "EZ2_CABIN_GUARD=0"
    cabin = env.get("EZ2_AIB_INSTANCE") or ""
    home = env.get("EZ2_AIB_INSTANCE_HOME") or ""
    if not cabin or cabin == "main" or not home:
        return False, "不是艙(EZ2_AIB_INSTANCE/HOME 空或 main)"
    session = env.get("EZ2_TMUX_SESSION") or _session_for(cabin)
    if not session:
        return False, "量不到座艙 session(cabin_resolve.sh --session %s 失敗)" % cabin
    g = Guard(session, cabin, os.path.join(home, "cockpit", LOG_NAME), dry_run=dry)
    t = threading.Thread(target=g.run, name="cabin-guard", daemon=True)
    t.start()
    return True, "cabin-guard 起了:%s(%s)" % (session, "dry-run" if g.dry_run else "live")


def main(argv=None):
    ap = argparse.ArgumentParser(description="艙的自動接回守衛")
    ap.add_argument("--session", default="")
    ap.add_argument("--cabin", default="")
    ap.add_argument("--log", default=None)
    ap.add_argument("--once", action="store_true", help="只拍一下就退出")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tick", type=float, default=TICK_S)
    ap.add_argument("--stats", action="store_true", help="印最近 24 小時每小時幾發 NSRangeException(第 3 項的尺)")
    ap.add_argument("--archive-once", action="store_true", help="現在抄一輪 log 並計數(不等 5 分鐘;跳過別艙剛做過的鎖)")
    ap.add_argument("--storm-once", action="store_true", help="現在數一次例外風暴,有就逐艙踢(跳過鎖;配 --dry-run 只留痕)")
    ap.add_argument("--lookback", type=int, default=ARCHIVE_LOOKBACK_S, help="跟 --archive-once 用:往回撈幾秒(做基線時給大一點,例如 36000)")
    a = ap.parse_args(argv)
    if a.stats:
        print(stats_text(load_exc_state()))
        return 0
    if a.storm_once:
        r, k = storm_once(lines_fn=probe_exc_lines, notify=do_notify, dry_run=a.dry_run, force=True)
        print(json.dumps({"result": r, "n": k}, ensure_ascii=False))
        return 0
    if a.archive_once:
        n, why = archive_once(fetch=lambda w: fetch_terminal_log(a.lookback), notify=None, force=True)
        print(json.dumps({"new": n, "why": why}, ensure_ascii=False))
        return 0
    log = a.log or os.path.join(os.environ.get("EZ2_AIB_INSTANCE_HOME") or os.path.expanduser("~/.ez2-aib"), "cockpit", LOG_NAME)
    if not a.session or not a.cabin:
        ap.error("--session 與 --cabin 必填(--stats / --archive-once 除外)")
    g = Guard(a.session, a.cabin, log, dry_run=a.dry_run, tick_s=a.tick)
    if a.once:
        print(json.dumps({"attached": g._attached(), "term_pid": g._term_pid()}, ensure_ascii=False))
        return 0
    g.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
