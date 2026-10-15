#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""依現況分級並降溫(the maintainer 2026-09-04 令:要顏色判斷,也要自動依現況降溫)。

## 分四級,而每一級的動作都寫在這裡(不散在呼叫端)

    🟢 ok        面板 5s   · 重活 go
    🟡 warn      面板 15s  · 重活 go
    🔴 hot       面板 30s  · 重活 **no-go**(寫旗標,啟動器自己讀)
    🔴🔴 critical 面板 30s  · no-go + 收掉帶自動化旗標的瀏覽器行程

## 三件安全設計,每一件都有具體理由

1. **殺行程只認自動化旗標,不認數量。**
   09-04 19:2x 現查:這台機器上 24 個 Chrome 行程,**自動化特徵 0 個**——
   全部是使用者自己開的瀏覽器。**「超過 N 個就殺」會殺掉使用者的 Chrome。**
   ⇒ 判別器是 `AUTOMATION_MARKS`(headless / remote-debugging-port / enable-automation /
   user-data-dir 指到暫存區),**沒有旗標一律不動**。識別不出來就不動手,不是猜。
   ★ 這條的代價要說清楚:**它可能從來不開火**,而那與「它有在保護你」在畫面上不同 ——
   所以 `reap()` 一律回報「掃了幾個、命中幾個、收了幾個」三個數,不只回收了幾個。

2. **每一次自動行為都留痕。** 沒有留痕的自癒與沒有自癒,在現場逐字相同(v0.53 BF 教訓)。
   落點 `~/.ez2-aib/logs/cooldown.log`,一行一個 JSON。

3. **煞車繩**:`EZ2_AIB_COOLDOWN=off` 全關;`EZ2_AIB_COOLDOWN_DRYRUN=1` 只留痕不動手。
   ★ 預設是**開**(the maintainer 令),但這兩個旗標讓任何人可以當場停掉,不必改碼。

## 門檻

CPU 溫度的門檻**不在 machine_status.THRESHOLDS 裡**(它只有電池的),所以在這裡補;
其餘一律引用它的,不複製 —— 兩份門檻各自漂移的失效形狀是「兩個地方都對,合起來錯」。
"""
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import machine_status as ms  # noqa: E402
import flap_guard  # noqa: E402


def _ui(key, params=None):
    """畫面/通知字走 i18n(C13 10-15:「CPU 溫度量不到」以前寫死中文,英文介面也照印);字典壞了退回鍵名。"""
    try:
        import i18n_lib
        return i18n_lib.t(key, params, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001 —— 印不出字不能讓降溫停
        return key

OK, WARN, HOT, CRIT, UNKNOWN = "ok", "warn", "hot", "critical", "unknown"
ORDER = {OK: 0, WARN: 1, HOT: 2, CRIT: 3}
# machine_status._from_ruler_cell() gives a successfully measured CPU die cell
# exactly this state; every other or missing state is insufficient authority
# for a destructive thermal action.
CPU_TEMP_MEASURED_STATES = frozenset((OK,))

# CPU 晶粒溫度門檻(machine_status.THRESHOLDS 只有電池的;這裡補,不去改它的表)
CPU_WARN_C = 75.0
CPU_HOT_C = 85.0
CPU_CRIT_C = 90.0
LOAD_CRIT = 25.0

# 面板輪詢秒數:熱了就放慢。★ warn 不放太慢——盯艦隊時反應變鈍會被當成面板壞了。
POLL_S = {OK: 5, WARN: 10, HOT: 30, CRIT: 30}

MARK = {OK: "🟢", WARN: "🟡", HOT: "🔴", CRIT: "🔴🔴"}
LABEL = {OK: "正常", WARN: "偏高", HOT: "過熱", CRIT: "危險"}

# ★ 只有帶這些旗標的行程才可能被收。使用者自己開的瀏覽器一個都不帶。
AUTOMATION_MARKS = (
    "--headless",
    "--remote-debugging-port",
    "--enable-automation",
    "--user-data-dir=/tmp/",
    "--user-data-dir=/var/folders/",
    "puppeteer",
    "playwright",
)
BROWSER_HINTS = ("chrome", "chromium", "firefox", "headless_shell")
KEEP_ALIVE = 0          # critical 時保留幾個自動化行程(0=全收)
MAX_KILL_PER_ROUND = 40  # 一輪最多收幾個,避免一次掃掉整台


def log_path():
    d = os.path.expanduser("~/.ez2-aib/logs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return os.path.join(d, "cooldown.log")


def log(event):
    path = log_path()
    if not path:
        return
    event = dict(event)
    event.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _num(cell):
    """從 machine_status 的一格取數值;量不到回 None(不回 0)。"""
    if not isinstance(cell, dict):
        return None
    if cell.get("state") == "unmeasured":
        return None
    v = cell.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


def _first_number(by, *keys):
    """回第一個有量到的值；合法 0 不能被當成沒有。"""
    for key in keys:
        value = _num(by.get(key))
        if value is not None:
            return value
    return None


def _first_measured_number(by, states, *keys):
    """回有 producer 合法 state 的第一個有限數值，否則 None。"""
    for key in keys:
        cell = by.get(key)
        if isinstance(cell, dict) and cell.get("state") in states:
            value = _num(cell)
            if value is not None:
                return value
    return None


def load_state(cells):
    """負載分級；只影響輪詢與重工作准入，永不授權 reap。"""
    by = {c.get("key") or c.get("label"): c for c in cells if isinstance(c, dict)}
    reasons = []
    lv = OK
    load = _first_number(by, "load1", "負載")
    if load is not None:
        if load >= LOAD_CRIT:
            lv = CRIT
            reasons.append("load1 %.2f ≥ %.0f" % (load, LOAD_CRIT))
        elif load >= ms.THRESHOLDS["load1_hot"]:
            lv = HOT
            reasons.append("load1 %.2f ≥ %.0f" % (load, ms.THRESHOLDS["load1_hot"]))
        elif load >= ms.THRESHOLDS["load1_warn"]:
            lv = WARN
            reasons.append("load1 %.2f ≥ %.0f" % (load, ms.THRESHOLDS["load1_warn"]))
    return lv, reasons


def thermal_state(cells):
    """可授權熱危險處置的分級，只認實測 CPU 溫度。

    kernel_task 是核心程序的 CPU 使用率，不是溫度或降頻百分比；負載與
    kernel_task 均不得以理由文字或等級旁路取得 reap 授權。量不到溫度時
    回 unknown，不把缺證據寫成正常，且 apply 不會因此清理程序。
    """
    by = {c.get("key") or c.get("label"): c for c in cells if isinstance(c, dict)}
    cpu = _first_measured_number(by, CPU_TEMP_MEASURED_STATES,
                                 "cpu_temp", "CPU 溫度")
    if cpu is None:
        return UNKNOWN, [_ui("aib.cool.reason.temp_unknown")]
    if cpu >= CPU_CRIT_C:
        return CRIT, ["CPU %.1f°C ≥ %.0f" % (cpu, CPU_CRIT_C)]
    if cpu >= CPU_HOT_C:
        return HOT, ["CPU %.1f°C ≥ %.0f" % (cpu, CPU_HOT_C)]
    if cpu >= CPU_WARN_C:
        return WARN, ["CPU %.1f°C ≥ %.0f" % (cpu, CPU_WARN_C)]
    return OK, []


def level_of(cells):
    """回 (等級, 逐條理由)。

    ★ 只有三格參與升級:**load1 / CPU 溫度 / 熱節流**。
      swap 與記憶體**只上色不升級**,理由是量出來的:這台機器 swap 常態 3.9–4.1GB,
      而 machine_status 的 `swap_used_warn` 是 **1 MB** ⇒ 那一格**永遠是 warn**。
      拿它算等級的話,降溫等級會永遠停在 🟡 —— **一個永遠亮著的警告不帶資訊**,
      而且會讓面板永遠跑在放慢的輪詢上。
      ⇒ 分開:每一格的顏色用 machine_status 自己的 state(不重寫它的門檻);
        **要不要降溫**只看會隨熱度變的那三格。

    ★ 量不到的格**不參與升級**——未量不是綠也不是紅(那是那一組觀測的性質)。
    """
    by = {c.get("key") or c.get("label"): c for c in cells if isinstance(c, dict)}
    reasons = []
    lv = OK

    def bump(to, why):
        nonlocal lv
        reasons.append(why)
        if ORDER[to] > ORDER[lv]:
            lv = to

    load_lv, load_reasons = load_state(cells)
    thermal_lv, thermal_reasons = thermal_state(cells)
    for to, why in ((load_lv, why) for why in load_reasons):
        bump(to, why)
    if thermal_lv in ORDER:
        for to, why in ((thermal_lv, why) for why in thermal_reasons):
            bump(to, why)

    kt = _first_number(by, "kernel_task", "熱節流")
    if kt is not None and kt >= ms.THRESHOLDS["kernel_task_hot"]:
        bump(HOT, _ui("aib.cool.reason.throttle", {"value": "%.1f%%" % kt,
                                                   "limit": "%.0f" % ms.THRESHOLDS["kernel_task_hot"]}))

    return lv, reasons


def scan_automation_procs(runner=None):
    """回 [(pid, cmdline)]。★ 三個數都要回,呼叫端才分得出「沒命中」與「沒掃」。"""
    run = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=5).stdout)
    try:
        out = run(["ps", "-eo", "pid=,command="])
    except Exception:
        return None, 0          # None = 掃不到,與「掃了但 0 命中」不同
    scanned = 0
    hits = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if not any(h in low for h in BROWSER_HINTS):
            continue
        scanned += 1
        if any(m in line for m in AUTOMATION_MARKS):
            m = re.match(r"^(\d+)\s+(.*)$", line)
            if m:
                hits.append((int(m.group(1)), m.group(2)))
    return hits, scanned


def reap(dry_run=False, runner=None, killer=None):
    """critical 時收掉自動化瀏覽器行程。回三個數:掃了/命中/收了。"""
    hits, scanned = scan_automation_procs(runner=runner)
    if hits is None:
        log({"action": "reap", "result": "scan_failed"})
        return {"scanned": None, "matched": None, "killed": 0, "why": "掃不到行程表"}
    keep = KEEP_ALIVE
    targets = hits[keep:][:MAX_KILL_PER_ROUND]
    killed = []
    if not dry_run:
        kill = killer or (lambda pid: os.kill(pid, 15))
        for pid, cmd in targets:
            try:
                kill(pid)
                killed.append(pid)
            except (OSError, ProcessLookupError):
                pass
    log({"action": "reap", "dry_run": dry_run, "scanned": scanned,
         "matched": len(hits), "killed": len(killed), "pids": killed,
         "samples": [c[:120] for _, c in targets[:3]]})
    return {"scanned": scanned, "matched": len(hits),
            "killed": len(killed) if not dry_run else 0,
            "why": "" if hits else "掃到 %d 個瀏覽器行程,帶自動化旗標的 0 個 ⇒ 不動手" % scanned}


def _state_dir():
    """座艙狀態目錄(同 machine_status_row 的理由)。★ **沒有 fallback,這是刻意的**。

    原本寫成「讀 env,讀不到就 expanduser 一個寫死的預設狀態目錄」,
    而 `tests/test-seats-dir-resolution.py::test_no_new_resolver_skips_hub_derivation`
    當場抓到:那個 fallback 在多艙環境會**退回主艙**,拿到一個
    「格式正確的錯答案」—— 快取寫進 A 艙、面板從 B 艙讀,而兩邊都不會紅。
    面板自己走的是 `os.environ['EZ2_COCKPIT_STATE_DIR']`(缺就報錯),這裡跟它一致:
    **拿不到就回 None,由呼叫端說「量不到」,不猜一個。**

    ★ 上面那段刻意**不寫出那個預設路徑的字面**:那支臂的偵測器讀的是「剝掉註解後的碼」,
      而 docstring 是字串常數不是註解 —— 我第一版在這裡逐字寫出那個路徑,
      **等於把偵測器要找的樣式寫進它會讀到的地方**,於是它把這支檔判成一個解析器。
      「談論它」與「做它」在那把尺上逐字相同。
    """
    d = os.environ.get("EZ2_COCKPIT_STATE_DIR")
    return d if d else None


def notice_path():
    state = _state_dir()
    return os.path.join(state, "cooldown-notice.json") if state else None


def _rulers():
    """借 machine_status 的尺解析器,不自己開第二條路。"""
    try:
        return ms.rulers()
    except Exception as err:
        return None, str(err)


def culprits():
    """現在是誰在吃資源。回 (清單, 說明);量不到回 (None, 原因)。

    ★ 用熱度尺的 `measure_ps`,不自己 `ps | grep`:它剖欄位、自帶正負校準,
      而我抄一份出來的那份會過期,**過期的樣子跟正確的一模一樣**。
    """
    mod, why = _rulers()
    if mod is None:
        return None, why or "熱度尺不在"
    try:
        cell, rows = mod.measure_ps()
        if not getattr(cell, "measured", False):
            return None, getattr(cell, "why", "") or "ps 量不到"
        me = os.getpid()
        # ★★ 2026-09-04 自錯,寫下來:`suite_runners` 回的是
        #   `{"ez2_ai_test_js": [...], "ez2_ai_pre_push": [...], "aib_release": [...],
        #     "browser_runner": [...]}` —— **四個分類的 dict**,不是行程清單。
        #   我原本寫 `len(suites)`,數到的是**分類數 4**,而四個 list 全是空的:
        #   實際在跑的重活是 **0 個**。
        #   ⇒ 那個 4 看起來完全合理,沒有人會質疑它,而它讓降溫機制送出兩次
        #     「正在跑:整套/發版 4 個」的錯誤通知給艦長。
        #   ⇒ 判準:拿別人的回傳值算數量之前,先印出它的型別與內容。
        #     `len()` 對 dict 與 list 都不會報錯,而兩者答的是不同的問題。
        suites = mod.suite_runners(rows, exclude_ancestors_of=me)
        chrome = mod.chrome_mains(rows)
        out = []
        if isinstance(suites, dict):
            for name, procs in suites.items():
                n = len(procs or [])
                if n:
                    out.append((name, n))
        elif suites:
            out.append(("整套/發版", len(suites)))
        n_chrome = len(chrome or [])
        if n_chrome:
            out.append(("瀏覽器", n_chrome))
        return out, ""
    except Exception as err:
        return None, "熱度尺呼叫失敗:%s" % err


# 這些不是我們能動的,只報不動手(使用者的瀏覽器 / 系統的視窗伺服器)
NOT_OURS = ("Google Chrome", "Firefox", "firefox", "WindowServer", "Terminal",
            "coreaudiod", "plugin-container")


def top_cpu(n=3, runner=None):
    """依指令名彙總 CPU,回前 n 名 [(名字, 百分比, 幾個行程, 是不是我們的)]。

    ★ 09-04 20:4x 量到的事實,寫在這裡當這支函式存在的理由:
      面板迴圈自己只佔 21%,而它吐出的重繪讓 **Terminal 43% + WindowServer 36%**。
      **畫的成本是算的成本的四倍**,而降溫原本完全看不到那兩個 —— 它只認得
      自己發動的重活(整套/發版/瀏覽器臂),於是熱源在別處時它只能說「要人看一眼」。
    ★ 一律**只報不動手**:名單裡有使用者的瀏覽器與系統行程,自動收掉它們是不能接受的。
    """
    run = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=8).stdout)
    try:
        # ★ 用 `comm=`(執行檔路徑)不用 `command=`(含引數):
        #   09-04 自錯 —— 拿 command 用空白切第一段,而
        #   `/Applications/Google Chrome.app/...` **路徑本身含空白** ⇒ 切出來是 `Google`,
        #   NOT_OURS 的 "Google Chrome" 比不到 ⇒ **使用者的瀏覽器被標成「我們的」**。
        #   那個標籤決定「可不可以動手」,標錯的代價是往危險那一側。
        out = run(["ps", "-eo", "pcpu=,comm="])
    except Exception:
        return None
    agg = {}
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, cmd = line.partition(" ")
        try:
            pct = float(head)
        except ValueError:
            continue
        name = (cmd.strip().rsplit("/", 1)[-1] or "?")
        cur = agg.setdefault(name, [0.0, 0])
        cur[0] += pct
        cur[1] += 1
    rows = sorted(((k, v[0], v[1]) for k, v in agg.items()),
                  key=lambda r: -r[1])[:n]
    return [(k, pct, cnt, not any(x in k for x in NOT_OURS)) for k, pct, cnt in rows]


def plan(level, reasons, reap_result=None):
    """依現況擬降溫動作。回 (已做的, 需要人決定的, 只是說明的)。

    ★ 分成三袋是刻意的:**我自己做得到的**與**要人拍板的**在畫面上必須分開,
      混在一起會讓人以為問題已經被處理掉了,而其實它在等他。

    ★ 第三袋 `fyi` 是 2026-09-07 補的,修的是本函式**自己檔頭那條規則沒有套完**:
      下面那句「把一個不需要決定的事丟給人,那種通知看幾次之後就會被整類忽略
      —— 連真的那次一起」原本只套用在 🟢。但「機器熱、而熱源不是我們的」是
      **同一個形狀**:這一段的碼自己剛剛才判定沒有任何我們的重活可以停,
      然後把它寫進「需要決定」。
      實測 2026-09-07 07:19–08:21 的 62 分鐘裡叫了 5 次,五次的第一名各不相同
      (fileproviderd / deleted_helper / mds_stores / mdworker_shared / coreduetd),
      五次都不是我們的、五次都沒有任何動作可做,而同期 `pmset -g therm` 從頭到尾
      沒有記錄過任何 thermal 或 performance warning。
      ⇒ 那不是「需要決定」,是「狀況說明」。放進 fyi,`notify_captain` 那道
      「沒有需要人決定的事就不送」的閘就會自己接住它 —— 閘一直都在,
      是我們一直餵給它一個不該進來的值。

    ★ 刻意**不動** `level_of()`:等級偏不偏高是另一個問題(而且往嚴的那一側偏
      比往鬆的那一側安全)。本次只改「要不要因此叫人」,不改「算不算熱」。
      把兩件事一起改,之後就分不出是哪一個讓通知變少。
    """
    did, need, fyi = [], [], []
    lv = level
    # ★ 正常時**什麼都不報**:🟢 之下「有 4 個整套在跑」不是問題,是正常工作。
    #   在正常狀態把它寫成「要不要停由人決定」,是把一個不需要決定的事丟給人,
    #   而那種通知看幾次之後就會被整類忽略 —— 連真的那次一起。
    if ORDER[lv] < ORDER[WARN]:
        return did, need, fyi
    who, why = culprits()
    if ORDER[lv] >= ORDER[WARN]:
        fyi.append("資源限制條件:面板輪詢 %d 秒" % POLL_S[lv])
    if ORDER[lv] >= ORDER[HOT]:
        fyi.append("資源限制條件:新大工作須讀 no-go 旗標")
    if who is None:
        need.append("查不出是誰在吃資源(%s)" % why)
    elif not who:
        if ORDER[lv] >= ORDER[HOT]:
            # ★ 熱、但沒有任何我認得的重活 ⇒ 不是我發動的,**但要指名是誰**。
            #   原本這裡只寫「要人看一眼」——那句話把一個查得到的答案丟回給人。
            top = top_cpu(3)
            if top is None:
                # ★ 這一條**留在 need**:量不到就是「不知道」,而讓「不知道」
                #   變成要處理的事,正是本庫的判準。它與下面那條不同 ——
                #   下面那條是「知道,而且知道沒事可做」。
                need.append("無法量到目前 CPU 使用來源")
            else:
                desc = "、".join("%s %.0f%%(%d 個)%s" % (k, pct, cnt, "" if ours else " ←不是我們的")
                                for k, pct, cnt, ours in top)
                fyi.append("目前 CPU 使用最高:" + desc)
    else:
        desc = "、".join("%s %d 個" % (k, n) for k, n in who)
        if lv == CRIT:
            # `critical` grants an attempt, not a successful cleanup.  In
            # particular a dry run and a scan with zero matches must not be
            # rendered as an action that already happened.
            killed = reap_result.get("killed", 0) if isinstance(reap_result, dict) else 0
            if isinstance(killed, int) and not isinstance(killed, bool) and killed > 0:
                did.append("收掉自動化瀏覽器行程 %d 個" % killed)
            need.append("仍在跑:%s" % desc)
        else:
            need.append("正在跑:%s —— 要不要停由人決定" % desc)
    return did, need, fyi


def write_notice(level, reasons, did, need, fyi=None, load_level=None, thermal_level=None):
    """落一份給最下一行讀的通知。★ 一律落檔(含 ok),讀的人才分得出
    「現在正常」與「這個機制沒在跑」——後者的畫面是空白,而空白會被讀成正常。"""
    path = notice_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"level": level, "mark": MARK[level], "label": LABEL[level],
                       "load_level": load_level, "thermal_level": thermal_level,
                       "reasons": reasons, "did": did, "need": need,
                       "fyi": fyi or [],
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                      fh, ensure_ascii=False)
    except OSError:
        pass


CAPTAIN_COOLDOWN_S = 900   # 同一件事 15 分鐘內不重複通知艦長


def notice_is_fresh(notice, ttl_s, now=None):
    """Return whether a notice's own timestamp is current enough to render.

    A missing, malformed, or future timestamp is not proof of freshness.  The
    caller supplies the machine-status cache TTL so this view does not invent
    a second expiry policy.
    """
    if not isinstance(notice, dict) or not isinstance(ttl_s, (int, float)):
        return False
    stamp = notice.get("at")
    if not isinstance(stamp, str):
        return False
    try:
        then = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return False
    age = (time.time() if now is None else now) - then
    return 0 <= age <= ttl_s


def captain_seat():
    """誰是第一艦長:讀右欄自己算好的 rowmap 找 👑,不另寫一套判定。

    ★ 「第一艦長」在碼裡不是欄位,是 pane-right-ai.sh 算出來畫在畫面上的 👑。
      我重寫一份判定的話,會出現「畫面說 A、通知寄給 B」——今天已經有過
      同一席在兩個畫面被標成不同角色的實例。
    """
    state = _state_dir()
    if not state:
        return None
    try:
        with open(os.path.join(state, "ai-rowmap.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return None
    for row in rows:
        d = row.get("detail") or {}
        if d.get("mark") == "👑":
            label = (d.get("label") or "").lstrip("@").rstrip("*")
            if label:
                return label
    return None


def system_notices_path():
    """第 70 項 P6 前半:系統通知的家。與佇列同一個狀態根(seats_dir_lib.state_root:EZ2_AIB_STATE_ROOT >
    EZ2_AIB_HOME > ~/.ez2-aib)底下 state/system-notices.jsonl;待審框頂一行讀它。量不到根回 None。"""
    try:
        import seats_dir_lib
        root, _src = seats_dir_lib.state_root(os.environ)
    except Exception:  # noqa: BLE001
        return None
    if not root:
        return None
    return os.path.join(root, "state", "system-notices.jsonl")


def culprit_details():
    """誰在吃資源——逐行程:[{name, pid, ppid, comm, command, parent_command}];量不到回 None。
    OPT §二 5:通知要印**父行程與參數**(`--check`、假版號一看是治具);分不出就印量不到,不猜。"""
    mod, why = _rulers()
    if mod is None:
        return None
    try:
        cell, rows = mod.measure_ps()
        if not getattr(cell, "measured", False):
            return None
        suites = mod.suite_runners(rows, exclude_ancestors_of=os.getpid())
        by_pid = {}
        for r in rows or []:
            try:
                by_pid[int(getattr(r, "pid"))] = r
            except (TypeError, ValueError, AttributeError):
                continue
        out = []
        if isinstance(suites, dict):
            for name, procs in suites.items():
                for pr in procs or []:
                    parent = by_pid.get(int(getattr(pr, "ppid", 0) or 0))
                    out.append({"name": name, "pid": int(pr.pid), "ppid": int(pr.ppid),
                                "comm": getattr(pr, "comm", "") or "",
                                "command": (getattr(pr, "command", "") or "")[:200],
                                "parent_command": ((getattr(parent, "command", "") or "")[:200]) if parent else None})
        return out
    except Exception:  # noqa: BLE001
        return None


def notify_captain(level, need):
    """要人決定時通知——**寫系統通知列,不進 dm 佇列**(第 70 項 P6 前半;OPT §一病 4、§二 5)。
    ★ 09-12 那天三則「aib_release N 個在跑」全是治具,各占待審區一列;通知與信同一條佇列同一個框,
      分不出治具與真發版。現在通知落 state/system-notices.jsonl,待審框頂一行讀它,不占待審列;
      通知帶逐行程的父行程與參數,分不出的印「量不到」。節流、留痕、失敗不影響降溫本身,三件照舊。

    ★ 09-05:原本有一個 `sender="cooldown"` 參數,**收了卻從來沒有人讀** ——
      想具名的意圖在,載體不存在,於是抬頭一直是 `unattested:unknown`。
      現在載體在 `src/cockpit/sender_origin.py`:dm 抬頭的名字由**行程祖先鏈**
      推導,本函式什麼都不必傳(傳了反而變成自報,那正是要避免的)。
      所以這個參數拿掉,不留一個沒有作用的旋鈕。
    """
    if not need:
        return {"sent": False, "why": "沒有需要人決定的事"}
    seat = captain_seat()          # 通知不再寄給誰,只記「當時畫面上的艦長是誰」;找不到照樣落通知
    _np = notice_path()
    if not _np:
        return {"sent": False, "why": "座艙狀態目錄不明"}
    stamp = os.path.join(os.path.dirname(_np), ".cooldown-notified")
    try:
        if time.time() - os.stat(stamp).st_mtime < CAPTAIN_COOLDOWN_S:
            return {"sent": False, "why": "節流中(%d 秒內已通知過)" % CAPTAIN_COOLDOWN_S}
    except OSError:
        pass
    path = system_notices_path()
    if not path:
        log({"action": "notify", "sent": False, "why": "狀態根不明,系統通知沒地方落"})
        return {"sent": False, "why": "狀態根不明"}
    details = culprit_details()
    row = {"ts": int(time.time()), "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "cooldown",
           "level": level, "need": list(need), "for_seat": seat,
           # `level` may have risen because of load.  Do not put a thermal verdict in
           # a captain notification unless the message has thermal evidence to do so.
           "culprits": details,                 # None = 量不到是不是治具(不猜)
           "throttle_s": CAPTAIN_COOLDOWN_S, "pid": os.getpid()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        with open(stamp, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except Exception as err:  # noqa: BLE001
        log({"action": "notify", "sent": False, "seat": seat, "why": str(err)})
        return {"sent": False, "why": str(err)}
    log({"action": "notify", "sent": True, "seat": seat, "channel": "system-notices", "path": path, "need": need})
    return {"sent": True, "seat": seat, "channel": "system-notices", "path": path}


def enabled():
    return (os.environ.get("EZ2_AIB_COOLDOWN") or "on").lower() != "off"


def flag_path():
    state = _state_dir()
    return os.path.join(state, "heavy-work-nogo") if state else None


# 10-10 the maintainer「守衛互推偵測」:旗標沒有遲滯 —— 負載在 HOT 門檻附近時,每 20 秒寫一次、刪一次,
#   重活一下被擋、一下放行。10 分鐘內翻滿 6 次 ⇒ 先留著旗標(往安全的那邊停),等負載真的穩下來
#   (翻轉紀錄滑出 10 分鐘)才刪。紀錄在 <狀態根>/flap/cooldown-nogo.jsonl(flap_guard)。
NOGO_FLAP_ACTION = "cooldown-nogo"
NOGO_FLAP_LIMIT = 6
NOGO_FLAP_WINDOW_S = 10 * 60


def set_nogo_flag(path, hot, text, now=None):
    """寫或刪 heavy-work-nogo。回 on / off / kept(本來就是那樣)/ hold(翻太多次,先留著)/ error。"""
    try:
        existed = os.path.exists(path)
        if hot:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            if existed:
                return "kept"
            flap_guard.record(NOGO_FLAP_ACTION, path, note="on", now=now)
            return "on"
        if not existed:
            return "kept"
        if flap_guard.held(NOGO_FLAP_ACTION, path, NOGO_FLAP_LIMIT, NOGO_FLAP_WINDOW_S, now=now,
                           why="降溫旗標在門檻附近來回"):
            return "hold"
        os.remove(path)
        flap_guard.record(NOGO_FLAP_ACTION, path, note="off", now=now)
        return "off"
    except OSError:
        return "error"


def live_cabin(environ=None, home=None):
    """這一輪是不是真的座艙在畫(10-08)。★ 活右欄每一幀也是 EZ2_ONESHOT,所以不能拿 ONESHOT 判;
    真座艙一定帶 EZ2_AIB_INSTANCE,而且家目錄底下有那個艙的資料夾。測試洗掉艙變數、換了 HOME ⇒ 兩條都不成立。"""
    env = os.environ if environ is None else environ
    inst = (env.get("EZ2_AIB_INSTANCE") or "").strip()
    if not inst or "/" in inst:
        return False
    home = home or os.path.expanduser("~")      # 家目錄不從 env 字典讀(HOME 不是艙變數;test-cabin-single-source-names)
    return os.path.isdir(os.path.join(home, ".ez2-aib", "instances", inst))


def apply(cells, dry_run=None):
    """算等級 → 落旗標 → 需要時收行程。回一份可直接印的 dict。"""
    lv, reasons = level_of(cells)
    load_lv, load_reasons = load_state(cells)
    thermal_lv, thermal_reasons = thermal_state(cells)
    dry = bool(int(os.environ.get("EZ2_AIB_COOLDOWN_DRYRUN") or 0)) if dry_run is None else dry_run
    out = {"level": lv, "mark": MARK[lv], "label": LABEL[lv],
           "poll_s": POLL_S[lv], "reasons": reasons, "reap": None,
           "load_level": load_lv, "load_reasons": load_reasons,
           "thermal_level": thermal_lv, "thermal_reasons": thermal_reasons}
    if not enabled():
        out["label"] = LABEL[lv] + "(降溫已關)"
        return out
    # 10-08:不是真的座艙(測試、截幀、治具)⇒ 只算等級。不落旗標、不收行程、不寫通知、不發 dm 給艦長 ——
    #   以前測試跑右欄時照這台真機器的負載收「自動化瀏覽器」、寫 ~/.ez2-aib/logs/cooldown.log(測試刪掉暫存目錄後
    #   又被建回來,留下 empty-home),機器忙(推前檢查)時還會真的通知艦長。
    if not live_cabin():
        return out
    path = flag_path()
    if not path:
        out["label"] = LABEL[lv] + "(座艙狀態目錄不明,旗標未落)"
        return out
    out["flag"] = set_nogo_flag(path, ORDER[lv] >= ORDER[HOT], "%s %s\n" % (lv, "; ".join(reasons)))
    if thermal_lv == CRIT:
        out["reap"] = reap(dry_run=dry)
    did, need, fyi = plan(lv, reasons, out["reap"])
    out["did"], out["need"], out["fyi"] = did, need, fyi
    # fyi 也要落進通知:不叫人不等於不留痕,否則「沒事」與「這個機制沒在跑」同形。
    write_notice(lv, reasons, did, need, fyi, load_lv, thermal_lv)
    if ORDER[lv] >= ORDER[HOT] and need:
        out["notified"] = notify_captain(lv, need)
    return out


def main(argv=None):
    cells, _took = ms.sample()
    res = apply(cells)
    print("%s %s · 面板 %ds" % (res["mark"], res["label"], res["poll_s"]))
    for r in res["reasons"]:
        print("  理由 %s" % r)
    if res["reap"]:
        print("  收行程 %s" % json.dumps(res["reap"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
