#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""機器狀態列(the maintainer 2026-09-04 01:3x 直令:右方 AI 欄最上方顯示溫度與記憶體)。

**它要回答的是一個決策,不是做一個儀表板**:現在能不能起重活。所以每一格都綁一個門檻,
而門檻只住在這一支檔(`THRESHOLDS`)—— 渲染端讀它,不自己再寫一份。

★ 三條判準級的話,每一條都對應今晚踩過的一次:

1. **探測失敗的值不得等於它表示安全的那個值。** 每一格帶 `state`;讀不到就是
   `unmeasured`,渲染成「量不到」,**不留白、不回 0、不回上一次的值**。
2. **「讀取失敗」與「值合法但為 0」不得共用同一個字樣。** 讀取失敗畫成 0 會讓機器過熱時
   面板說一切正常(致命),把合法的 0 畫成「量不到」會讓那一格永遠看起來壞掉(吵),
   兩個都不對。★ **09-08 更正**:本段原本寫「`kernel_task` 平常真的是 0.0%(現查)」,
   而那次現查用的是 `top -l 1` —— **單取樣的 %CPU 結構上只可能是 0.0**。也就是說那個
   「現查」查到的是指令的產物,卻被寫成事實,再被拿來論證「0.0 是合法讀數」。
   **判準餵飽了自己,而那一段讀起來比任何一段都更像已經查證過。** 取樣改法見
   `kernel_task_cpu`;真值當日實測 10–38%。
3. **CPU 溫度這台機器讀不到。** `powermetrics` 要 sudo、`pmset -g therm` 恆空、
   `sysctl thermlevel` 不存在。唯一讀得到的是**電池**溫度,所以它的標籤逐字帶「非 CPU」,
   而 CPU 那一格誠實寫「量不到」。**不得顯示任何看起來像 CPU 溫度的數字。**

取值一律走 `run()` 這個具名縫(預設真的開子行程),臂用替身注入,不靠環境變數。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

# ── 門檻單源:渲染端與判斷端都讀這裡,不得各寫一份 ──────────────────────────
THRESHOLDS = {
    "load1_hot": 15.0,        # >= 不要起瀏覽器臂(艦長 09-04 01:3x 令)
    "load1_warn": 8.0,
    "mem_free_warn": 20.0,    # 剩餘記憶體百分比,低於算警告
    "swap_used_warn": 1.0,    # MB;非零就值得看
    "kernel_task_hot": 20.0,  # >= 代表正在降頻吸熱
    "battery_temp_warn": 40.0,
}

UNMEASURED = "量不到"
READ_FAILED = "量不到(讀取失敗)"      # 與「值合法但為 0」逐字不同
NO_BATTERY = "量不到(沒有電池)"      # 09-17:沒電池的機器(Mac mini/LIN)不是讀取失敗;READ_FAILED 只留給「有電池但讀不到」
TIMED_OUT = "量不到(逾時)"            # 第三種成因:來源還在,但它太慢
KERNEL_TASK_BUDGET_S = 0.8            # 單格上限(整輪還剩多少會再壓一次,見 _DEADLINE)
ROUND_BUDGET_S = 1.0                  # 整輪上限;★ 預算住在**輪**不住在格
_DEADLINE = None                      # sample() 設;run() 依它壓每一次外部呼叫的 timeout
_LAST_GOOD = {}                       # key -> (字樣, monotonic 時刻);只給逾時那條路用
# tmux 的 socket 根。★ 先讀 `TMUX_TMPDIR`:艦規要求真 tmux 臂在艙外殼跑(env 全洗 +
#   沙箱 TMUX_TMPDIR),而寫死這個路徑會讓那個殼裡的探針去翻**別人的**目錄 ——
#   qa a seat 09-04 02:36 就是在那個殼裡量到「6 個 socket 檔一個都連不上」。
#   讀環境之後,那個殼裡看到的就是它自己那一份(通常是空的),行為才是對的。
#   uid 也不寫死:`tmux-501` 的 501 是這台機器的 uid,不是常數。
TMUX_SOCKET_DIR = (os.environ.get("TMUX_TMPDIR")
                   or "/private/tmp/tmux-%d" % os.getuid())

# ── 熱度尺單源(降溫節能艦長 a seat 09-04 02:0x 裁)────────────────────────────
# CPU 溫度**讀得到**(IOHID UsagePage 0xFF00 Usage 5 的 PMU tdie,免 sudo),
# 所以本檔**不得**再寫「量不到」,也不得拿電池冒充。取法住在 `tools/heat/rulers.py`,
# 這裡 import 它,**不抄第二份** —— 抄出來的那份會過期,而過期的樣子跟正確的一模一樣。
# 解析順序具名:`EZ2_HEAT_RULERS`(合入前指到那棵樹)> 本樹 `tools/heat/rulers.py`。
# 兩個都沒有時,相關的格誠實說「尺還沒合入」,**不是**回 0、也不是回一個看起來像溫度的數。
HEAT_RULERS_ENV = "EZ2_HEAT_RULERS"
_RULERS = None
_RULERS_WHY = ""
_RULERS_TR = {}


def _rulers_why(key, params=None):
    """「量不到」的原因字走 i18n(10-15 多語第二批:它會出現在降溫通知的「要人決定」那一行);字典讀不到印鍵名。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _RULERS_TR.get(loc) or _RULERS_TR.setdefault(loc, i18n_lib.Translator(locale=loc))
        return tr.t("aib.machine.rulers." + key, params)
    except Exception:  # noqa: BLE001
        return "aib.machine.rulers." + key


def rulers():
    """回 (模組, 原因);模組是 None 時原因逐字說明為什麼。"""
    global _RULERS, _RULERS_WHY
    if _RULERS is not None or _RULERS_WHY:
        return _RULERS, _RULERS_WHY
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    # ★ 2026-09-04:明確指定卻指不到,**具名失敗,不靜默換一支**。
    #   舊行為是掉回本樹那支 —— 在尺還沒合入時看不出差別,而尺進 main 之後
    #   `EZ2_HEAT_RULERS=/nonexistent` 會**靜默用本樹的尺回真值**,
    #   於是「我指定了一支尺」與「我指定的那支不存在」得到相同的輸出。
    #   實測後果:tests/test-pane-right-status.py::test_a1b 造不出「沒有尺」那個條件,
    #   由綠轉紅 —— 那支臂的前提在尺合入的那一刻過期,而它是對的,過期的是世界。
    _env = os.environ.get(HEAT_RULERS_ENV) or ""
    if _env and not os.path.isfile(_env):
        _RULERS_WHY = _rulers_why("missing_path", {"env": HEAT_RULERS_ENV, "path": _env})
        return None, _RULERS_WHY
    cands = [_env,
             os.path.join(os.path.dirname(os.path.dirname(here)), "tools", "heat", "rulers.py")]
    for path in cands:
        if not path or not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("aib_heat_rulers", path)
        mod = importlib.util.module_from_spec(spec)
        # ★ 先掛進 sys.modules 再 exec:`@dataclass` 要靠 sys.modules[__module__] 解析,
        #   少這一行會炸成 `'NoneType' object has no attribute '__dict__'` ——
        #   而那個訊息一個字都沒提到 dataclass 或 sys.modules。
        sys.modules[spec.name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:                      # noqa: BLE001 具名失敗,不靜默
            _RULERS_WHY = _rulers_why("load_failed", {"err": exc})
            return None, _RULERS_WHY
        _RULERS = mod
        return _RULERS, ""
    _RULERS_WHY = _rulers_why("not_in_tree", {"env": HEAT_RULERS_ENV})
    return None, _RULERS_WHY


def _from_ruler_cell(key, label, cell):
    """把 rulers.Cell 轉成本列的格。**measured=False 一律是量不到,不得補一個值。**"""
    if not getattr(cell, "measured", False):
        return _unmeasured(key, label, getattr(cell, "why", "") or "")
    return _cell(key, label, cell.value, cell.display, "ok",
                 detail=getattr(cell, "raw", "") or "")


def run(argv, timeout=5):
    """具名縫:所有外部取值只從這裡出去。回 (rc, stdout);任何失敗回 (None, "")。

    ★ 失效側回 `None` 不回 0/空字串當正常值 —— 呼叫端據此標 unmeasured。
    ★ 預算住在**輪**不住在格:整輪還剩多少,就壓這一次呼叫最多能等多久。
      09-04 實測 load 37.67 時 `top -l 1 -pid 0` 一格就吃掉 0.586s,而它自己的上限是 0.8s
      —— 0.8 + 其餘七格 ≈ 超過整輪 1.0s。**兩個常數各自合理而加起來不成立**,
      而失效形狀是整輪超時的那一格是「最後被叫到的那一格」,跟誰慢無關。
    """
    if _DEADLINE is not None:
        left = _DEADLINE - time.monotonic()
        if left <= 0.02:
            return None, ""
        timeout = min(timeout, left)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    if p.returncode != 0:
        return None, p.stdout or ""
    return p.returncode, p.stdout


# 每一格靠哪一支外部工具取值。**單源**:面板與臂都讀這裡,不各抄一份。
# ★ 09-04 04:1x 現查:照艦規的洗殼(env -i + PATH=/opt/homebrew/bin:/usr/bin:/bin)裡,
#   `sysctl` 與 `ioreg` 住在 /usr/sbin,**不在那個 PATH 上** ⇒ 那兩格會誠實回「量不到」。
#   而「工具不在 PATH」與「工具跑了但失敗」是兩件事,字樣要分得開:
#   前者是這個殼的性質,後者才值得去看產品。
SOURCE_BIN = {"load1": "uptime", "mem": "memory_pressure", "swap": "sysctl",
              "kernel_task": "top", "battery": "ioreg", "heavy": "ps", "seats": "tmux"}


def source_available(key):
    """那一格的工具在不在 PATH。回 None 表示這一格沒有登記工具。"""
    name = SOURCE_BIN.get(key)
    if not name:
        return None
    return shutil.which(name) is not None


def _why(key, ran_but_failed):
    if source_available(key) is False:
        return "PATH 上找不到 %s" % SOURCE_BIN[key]
    return ran_but_failed


def _cell(key, label, value, text, state, detail=""):
    return {"key": key, "label": label, "value": value, "text": text,
            "state": state, "detail": detail}


def _unmeasured(key, label, why):
    """讀不到就是讀不到。★ 這是本檔唯一產生 READ_FAILED 的地方。"""
    return _cell(key, label, None, READ_FAILED, "unmeasured", why)


def load1(runner=run):
    """1 分鐘負載。**剖語意不剖欄序**(a seat 09-04 02:0x)。

    ★ 我原本用 `sysctl -n vm.loadavg` 抓「第 2 個小數」,那是 **5 分鐘**那一欄 ——
      規格寫的「第 2 欄」是 awk 欄序(`{` 是第 1 欄),兩種數法在同一句話下各自成立,
      而我實作的那一種**標著 1 分鐘卻報 5 分鐘**。整條路上沒有任何一格會紅。
      改走 `uptime` 原始行,由熱度尺單源剖(它的正規表示式抓 `load averages:` 後三個數)。
    """
    mod, why = rulers()
    if mod is None:
        return _unmeasured("load1", "負載", why)
    cell, _load15, _raw = mod.measure_uptime()
    out = _from_ruler_cell("load1", "負載", cell)
    if out["state"] == "ok" and out["value"] >= THRESHOLDS["load1_hot"]:
        out["state"] = "hot"
    elif out["state"] == "ok" and out["value"] >= THRESHOLDS["load1_warn"]:
        out["state"] = "warn"
    return out


def mem_free_pct(runner=run):
    rc, out = runner(["memory_pressure", "-Q"])
    if rc is None:
        return _unmeasured("mem", "記憶體", _why("mem", "memory_pressure 取不到"))
    m = re.search(r"free percentage:\s*([0-9]+)%", out)
    if not m:
        return _unmeasured("mem", "記憶體", "找不到 free percentage 那一行")
    v = float(m.group(1))
    return _cell("mem", "記憶體", v, "free %d%%" % v,
                 "warn" if v < THRESHOLDS["mem_free_warn"] else "ok")


def swap_used_mb(runner=run):
    rc, out = runner(["sysctl", "-n", "vm.swapusage"])
    if rc is None:
        return _unmeasured("swap", "swap", _why("swap", "sysctl 取不到"))
    m = re.search(r"used\s*=\s*([0-9.]+)M", out)
    if not m:
        return _unmeasured("swap", "swap", "找不到 used 欄")
    v = float(m.group(1))
    return _cell("swap", "swap", v, "%.2fM" % v,
                 "warn" if v >= THRESHOLDS["swap_used_warn"] else "ok")


def kernel_task_cpu(runner=run):
    """kernel_task 的 %CPU。**直接問 pid 0**,不用 `top -o cpu` 取前 N。

    ★ 艦長 09-04 實測:排序取前 12 的寫法,在 kernel_task 不忙時它根本不在名單裡,
      於是那一格顯示「量不到」—— 而**成因是「它不在前 N」不是「讀不到」**,
      兩者在版面上逐字相同。這裡直接指名 pid 0,那個成因就不存在。
    """
    # ★ `top -l 1` 是這一列最貴的一格:現查閒時 0.345s,而高負載時我量到整輪 0.950s
    #   —— 逼近整輪 1.0s 的上限,**而最需要這一列的時刻正是負載高的時刻**。
    #   所以給它自己的預算:逾時就誠實回「量不到(逾時)」,不讓整輪被它拖破上限。
    #   ★ 逾時是**第三種成因**,與「讀取失敗」「合法的 0」都不共用字樣。
    t0 = time.monotonic()
    rc, out = runner(["top", "-l", "2", "-s", "0", "-pid", "0"],
                     timeout=KERNEL_TASK_BUDGET_S)
    if rc is None:
        elapsed = time.monotonic() - t0
        # leftover 不夠 0.8s 時 run() 會提前停;等過 0.2s 也是逾時,不是「top 不在」。
        slow = elapsed >= 0.2
        cell = _unmeasured("kernel_task", "熱節流",
                           "逾時 %.1fs" % elapsed if slow
                           else _why("kernel_task", "top 取不到"))
        if slow:
            # ★ 艦長 09-04 01:56 裁:兩個都做,但字樣要分得開。
            #   單獨的「量不到」讓你在最需要它的時刻什麼都沒有;
            #   單獨的「沿用上次」會讓過期值冒充現值。
            #   ⇒ 兩段並列:失效值與正常值在版面上分得開,而資訊沒有被丟掉。
            cell["text"] = "量不到(逾時 %.1fs)" % elapsed
            last = _LAST_GOOD.get("kernel_task")
            if last:
                cell["text"] += " · 上次 %s 於 %d 秒前" % (
                    last[0], int(time.monotonic() - last[1]))
        return cell
    # ★ 2026-09-08(the maintainer 一句「核心程序 CPU 0% 有可能 0% 嗎」問出來的):
    #   `top -l 1` 的**第一個取樣沒有前一幀可以相減**,所以 kernel_task 的 %CPU
    #   結構上只可能是 0.0。配對實驗(同一條 `top -l 2`,只差取樣序號,5 次):
    #     第一取樣 0.0 0.0 0.0 0.0 0.0 / 第二取樣 10.2 10.3 19.4 12.7 16.1
    #   ⇒ 那一格顯示的 0.0% **不是讀數是產物**,而 `kernel_task_hot = 20.0`
    #     這條分支因此恆假:降頻從上線起沒有開火過(同日還量到 23.4 / 35.7 / 37.9)。
    #   ★ 代價量過:`-l 2 -s 0` 0.64–0.68s(對照 `-l 2` 預設間隔 1.75s、`-l 1` 0.345s),
    #     仍在 KERNEL_TASK_BUDGET_S=0.8 之內;超過就走既有的逾時那條路,誠實回量不到。
    #   ★ **只有一個取樣時一律不採信**:那正是舊行為,而它的值恆為 0.0 ——
    #     「單取樣」與「機器真的閒著」在版面上逐字相同,所以這裡回量不到不回 0。
    rows = [line.split() for line in out.splitlines()
            if line.strip().startswith("0") and "kernel_task" in line]
    rows = [r for r in rows if len(r) >= 3]
    if not rows:
        return _unmeasured("kernel_task", "熱節流", "沒有 pid 0 那一列")
    if len(rows) < 2:
        return _unmeasured("kernel_task", "熱節流",
                           "只有 1 個取樣(單取樣的 %CPU 恆為 0.0,不採信)")
    row = rows[-1]
    try:
        v = float(row[2])
    except ValueError:
        return _unmeasured("kernel_task", "熱節流", "%%CPU 欄不是數字:%r" % row[2])
    # ★ v == 0.0 是**合法讀數**,狀態 ok、字樣是「0.0%」——不是 READ_FAILED。
    _LAST_GOOD["kernel_task"] = ("%.1f%%" % v, time.monotonic())
    return _cell("kernel_task", "熱節流", v, "%.1f%%" % v,
                 "hot" if v >= THRESHOLDS["kernel_task_hot"] else "ok")


def battery_temp_c(runner=run):
    """★ 這是**電池**溫度,不是 CPU。標籤逐字帶「非 CPU」,由 render 保證。"""
    rc, out = runner(["ioreg", "-r", "-c", "AppleSmartBattery"])
    if rc is None:
        return _unmeasured("battery", "電池溫度(非 CPU)", _why("battery", "ioreg 取不到"))
    m = re.search(r'"Temperature"\s*=\s*(-?[0-9]+)', out)
    if not m:
        # ★ 09-17(艦長 a seat,LIN Mac mini 證詞 1 紅):沒電池的機器 ioreg 跑得起來但沒有 AppleSmartBattery /
        #   沒有 Temperature 欄——這是「沒有電池」,不是「有電池但讀不到」。之前走 READ_FAILED,test-pane-right-status
        #   b1 就把它讀成「資料源正常卻報讀取失敗」。READ_FAILED 只留給 ioreg 取不到(rc None)那條。
        why = ("沒有電池(ioreg 沒有 AppleSmartBattery 物件)" if not out.strip()
               else "沒有電池溫度(ioreg 有 AppleSmartBattery 但沒有 Temperature 欄)")
        return _cell("battery", "電池溫度(非 CPU)", None, NO_BATTERY, "unmeasured", why)
    v = int(m.group(1)) / 100.0
    return _cell("battery", "電池溫度(非 CPU)", v, "%.1f°C(非 CPU)" % v,
                 "warn" if v >= THRESHOLDS["battery_temp_warn"] else "ok")


def cpu_temp():
    """CPU die 溫度。**讀得到**:IOHID UsagePage 0xFF00 Usage 5 的 PMU tdie,免 sudo。

    ★ 先前這一格寫死「量不到」,理由是我逐項試過 powermetrics/pmset/sysctl 都不行 ——
      **「我試過的路都不通」與「這台機器讀不到」是兩件事**,而我把前者寫成了後者。
      a seat 現量 53.5°C 把它推翻了。取法不抄,import 單源。
    """
    mod, why = rulers()
    if mod is None:
        return _unmeasured("cpu_temp", "CPU 溫度", why)
    return _from_ruler_cell("cpu_temp", "CPU 溫度", mod.read_cpu_die_temps())


def _own_chain(runner=run):
    """自己 + 祖先鏈的 pid 集合。

    ★ 艦長 09-04 01:4x 實測的錯:他的偵測器指令列**自己帶著那個檔名字面**,於是數到自己那兩層
      殼,回報「有整套在跑」而真值是 0。★ 失效方向是**恆有**,而「恆有」讓那道閘永遠關著
      —— **永遠關著不會紅**。所以這裡先把自己那條鏈算出來,數的時候整條排掉。
    """
    rc, out = runner(["ps", "-eo", "pid=,ppid="])
    if rc is None:
        return set()
    parent = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
            parent[int(f[0])] = int(f[1])
    chain, cur = set(), os.getpid()
    while cur and cur not in chain:
        chain.add(cur)
        cur = parent.get(cur, 0)
    return chain


SUITE_MARK = "tools/" + "test.js"      # 組出來的,不讓它以純字面待在任何指令列上


# ★ 正校準的目標**不能挑 pid 1 那種祖先**:排整條祖先鏈(規格的另一條)會把 launchd
#   一起排掉,於是正校準在治具上恆為 0,而在真機器上只是「剛好還有別的同名行程」才非 0。
#   兩條規矩會互相咬,而咬出來的形狀是「尺不會叫」——那正是負校準要抓的反面。
#   改用路徑分隔符:任何行程的指令列都有它,而它不專屬於任何一條祖先鏈。
def heavy_work(runner=run):
    """第 5 格「**重活**」(a seat 09-04 02:0x 裁,取代原本的「整套輪數」)。

    ★ 欄位名不叫 test.js:**本倉沒有 `tools/test.js`**,那是 `~/ez2-ai` 的整套。
      這一格數的是這台機器上的三項重活:ez2-ai 的整套 / 本倉的 `aib-release.sh` /
      Chrome for Testing 主行程的父 node(profile 數當副尺)。
    ★ 取法與門檻**全部讀熱度尺單源**(`tools/heat/rulers.py`),這裡不再寫第二份:
      它剖 ps 的欄位而不是 `ps | grep`,自數與校準由它自己的臂守著。
      我先前那份三數法(正/負校準、排祖先鏈、母體行數)留在 git 史裡,不留第二份實作 ——
      **抄出來的那份會過期,而過期的樣子跟正確的一模一樣。**
    """
    mod, why = rulers()
    if mod is None:
        return _unmeasured("heavy", "重活", why)
    cell, rows = mod.measure_ps()
    if not getattr(cell, "measured", False):
        return _unmeasured("heavy", "重活", getattr(cell, "why", "") or "ps 量不到")
    # (b) 排掉**整條祖先鏈**,不是排 $$ 與 $PPID:單源現量本席是 6 層
    #     (python→zsh→grok→席 zsh→tmux→launchd)。這裡把「我」交給尺,由它走鏈。
    # (b) 排掉**整條祖先鏈**,不是排 $$ 與 $PPID:單源現量本席 6 層
    #     (python→zsh→grok→席 zsh→tmux→launchd)。這裡把「我」交給尺,由它走鏈。
    me = os.getpid()
    groups = mod.suite_runners(rows, exclude_ancestors_of=me)
    mains = mod.chrome_mains(rows)          # (a) 行程名型走 comm,不走 args
    profiles = mod.chrome_profiles(mains)
    # ★ 三數法(a seat 09-04 02:2x 定版,取代先前所有版本):同一次呼叫三個數,缺一不算。
    #   正校準(一定存在)證明尺會叫;負校準(一定不存在)**就是雜訊底,要減掉**,證明尺不亂叫。
    #   ★ 這一條**推翻**了前一版「負對照不是 0 就作廢重拍、不可相減」——正本擁有者已移交,
    #     以現任擁有者的定版為準;我不留第二種行為,免得兩種都在庫裡而沒人知道哪個算數。
    #   ★ 閘看**修正後**那個數,不看未減的目標數。
    # ★ 要哪些名字**由單源自己宣告**(`PANEL_REQUIRED_NAMES`,a seat 09-04 03:5x 加的):
    #   我原本在這裡自己抄一份清單,那就是第二份實作 —— 尺加了新名字我不會知道,
    #   而「我抄的那份剛好夠用」與「我抄的那份過期了」在版面上逐字相同。
    #   它不在(舊版尺)才退回我自己那份最小集,並且**具名**說是退回來的。
    need = getattr(mod, "PANEL_REQUIRED_NAMES", None) or (
        "measure_ps", "suite_runners", "chrome_mains", "chrome_profiles",
        "count_cmd_contains", "count_controls", "describe_hits", "ancestor_pids",
        "POS_CALIB_NEEDLE", "NEG_CALIB_NEEDLE")
    missing = [n for n in need if not hasattr(mod, n)]
    # ★ 這支尺**還沒合入本樹**,是別席正在寫的活檔(09-04 02:24 它的 `count_triple` 就在我
    #   兩次跑之間消失,換成 `count_controls`)。少一個名字要**具名說量不到**,不得讓面板
    #   整支炸掉 —— 一個會拋例外的狀態列,在最需要它的時刻正好什麼都不顯示。
    if missing:
        return _unmeasured("heavy", "重活", "單源缺少:%s" % ",".join(missing))
    pos = mod.count_cmd_contains(rows, mod.POS_CALIB_NEEDLE, exclude_ancestors_of=me)
    neg = mod.count_cmd_contains(rows, mod.NEG_CALIB_NEEDLE, exclude_ancestors_of=me)
    if not pos:
        # 正校準 0 = 尺此刻不會叫。它回的任何數都不帶資訊,不得當成讀數。
        return _unmeasured("heavy", "重活", "正校準 0(ruler-broken)")
    if neg:
        # ★ 負對照**必須 =0**(a seat 02:26 定版 v4)。它不是 0 就代表這一次呼叫被污染,
        #   而**不得拿它去減出一個「經過校準」的數字** —— 那種數字比未校準的更可信,更貴。
        #   ⇒ 這一次的讀數作廢,具名說為什麼。
        return _unmeasured("heavy", "重活",
                           "負對照 %d(必須 0)⇒ 本次呼叫被污染" % len(neg))
    # 四個對照缺一不算(v4):正對照 >0、負對照 =0(上面兩道)、誘餌對照(臂)、鏈長(detail)。
    # ★ 閘用**目標數**,不用目標減負 —— 單源 `count_controls` 的契約逐字如此。
    controls = getattr(mod, "count_controls", None)
    triples, hits, total = [], [], 0
    for key, label in (("ez2_ai_test_js", "整套"), ("aib_release", "發版"),
                       ("browser_runner", "瀏覽器")):
        rows_hit = groups.get(key, [])
        if controls is not None:
            tgt, p, n = controls(rows_hit, pos, neg)
        else:
            tgt, p, n = len(rows_hit), len(pos), len(neg)
        total += tgt
        triples.append("%s %d 目標=%d 正=%d 負=%d" % (label, tgt, tgt, p, n))
        # (d) 逐行認出每一筆是誰(pid/comm/父/祖父/args)—— 交給單源的 describe_hits,
        #     不自己拼第二種格式。今晚兩次都是靠讀那幾行才知道命中的是自己。
        for line in mod.describe_hits(rows_hit, rows):
            hits.append("%s %s" % (label, line))
    text = " ".join(triples)
    chain = len(mod.ancestor_pids(rows, me))
    detail = "%s 母體 %d 行 鏈長 %d profile %d 雜訊底 %d | %s" % (
        time.strftime("%H:%M:%S"), len(rows), chain, len(profiles), len(neg),
        " || ".join(hits) or "無命中")
    return _cell("heavy", "重活", total, text, "warn" if total else "ok", detail=detail)


def live_seats(runner=run, socket_dir=TMUX_SOCKET_DIR, seat_reader=None, hub=None):
    """這一艙還活著的 AI 窗數。**四腿**,每一腿對應一個實際踩過的多算成因:

    1. 只數 `aihub-*` socket,且**排除 watch/lobby 窗** —— 艦長掃了含 default 的所有 socket,
       把外殼的 cockpit 與 war-room 窗算成席,30 對 18。
    2. **socket 檔存在不等於艙活著** —— 我現查 6 個檔只有 2 個 `list-sessions` 成功
       (今晚被回收的四艙 socket 檔還留著)。所以逐個真的連連看。
    3. **不讀席位登記當現況**:登記不會過期(7ba0 的 w2.json 記著一個死了 24 小時的席),
       所以母體來自活體窗(至少一個 `pane_dead=0` 的非輔助 pane)。
    4. **pid 活著不等於那個 pid 還是那一席** —— pm a seat 現查:instance 14 的 w5.json
       記 pid 1107,而那個 pid 現在跑的是 `familycircled`,已跑 2 天 12 小時(pid 被系統回收)。
       所以每一席還要問「那個 pid 現在跑的是不是它」,分辨器是 **ps 的命令列**不是計數。

    畫面寫「AI N 位」,指的是**這一艙**的 AI 窗,不是機器上所有 leftover 艙的 pane
    加總。2026-09-11 現查:約 50 個 leftover 艙仍連得上,加總 60,而人看的那一艙只有 3 隻。
    有 `EZ2_AIHUB_SOCKET`(或 `hub=`)就只數那一個;同一個 AI 窗有兩個 pane
    (對話 + 右欄)只算 1 位。
    """
    if hub is None:
        hub = os.environ.get("EZ2_AIHUB_SOCKET") or ""
    try:
        names = [n for n in sorted(os.listdir(socket_dir)) if n.startswith("aihub-")]
    except OSError:
        return _unmeasured("seats", "活席", "socket 目錄讀不到")
    if hub:
        if hub in names or os.path.exists(os.path.join(socket_dir, hub)):
            names = [hub]
        else:
            return _unmeasured("seats", "活席",
                               "這一艙的 socket %s 不在 %s" % (hub, socket_dir))
    total = 0
    live_cabins = 0
    for sock in names:
        rc, _ = runner(["tmux", "-L", sock, "list-sessions"])
        if rc is None:
            continue                      # 腿 2:socket 檔還在但連不上 = 死艙
        live_cabins += 1
        rc, out = runner(["tmux", "-L", sock, "list-panes", "-a", "-F",
                          "#{pane_dead}|#{window_name}|#{pane_pid}|#{window_id}"])
        if rc is None:
            continue
        windows = {}
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) < 3:
                continue
            dead, wname, pid = parts[0], parts[1], parts[2]
            win_key = parts[3] if len(parts) >= 4 else wname
            if dead != "0":               # 腿 3:活體 pane 才算
                continue
            if wname in ("watch", "lobby", "cockpit", "war-room"):
                continue                  # 腿 1:輔助窗不是席
            windows.setdefault(win_key, []).append((wname, pid))
        for _win_key, panes in windows.items():
            if seat_reader is None:
                total += 1
                continue
            alive = False
            for wname, pid in panes:
                if seat_reader(sock, wname, pid):
                    alive = True
                    break
            if alive:
                total += 1
    if not names:
        # 目錄在、但一個 aihub-* 都沒有:這是**量到的 0 艙**,不是量不到 ——
        # 但要把「我看的是哪一個根」寫出來,否則 0 席在版面上與看錯地方同形。
        return _cell("seats", "活席", 0, "0 席 / 0 艙", "ok",
                     detail="socket 根 %s 沒有 aihub-*" % socket_dir)
    if live_cabins == 0:
        # ★ 一個 socket 都連不上時,「艙全死了」與「tmux 根本叫不動」給同一個 0。
        #   分不出來就不得回 0 —— 回 0 會讓面板在自己瞎掉的時候說一切正常。
        return _unmeasured("seats", "活席", "%d 個 socket 檔全部連不上" % len(names))
    return _cell("seats", "活席", total, "%d 席 / %d 艙" % (total, live_cabins), "ok")


def pid_is_still_that_seat(pid, started_epoch, runner=run):
    """腿 4 的分辨器:那個 pid 現在跑的是不是當初那一席。

    ★ 用 `etime`(已跑多久)不用 `lstart`(起始時刻的字串)。09-04 拿真資料問時,
      `lstart` 回的是**本地語系**的 `二  9/ 1 13:24:42 2026` —— 英文格式的 strptime
      吃不下,於是回 None:**結論(不算成活席)是對的,理由卻變成「量不到」**。
      `etime` 的格式是 `[[dd-]hh:]mm:ss`,與語系無關。
    回 False = 這個 pid 已經不是它了;回 None = 量不到 —— **量不到不得當成 True**
    (那會把被回收的 pid 算成活席)。
    """
    rc, out = runner(["ps", "-o", "etime=", "-p", str(pid)])
    if rc is None or not out.strip():
        return None
    raw = out.strip()
    days = 0
    if "-" in raw:
        d, _, raw = raw.partition("-")
        if not d.isdigit():
            return None
        days = int(d)
    parts = raw.split(":")
    if not all(p.strip().isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        return None
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.insert(0, 0)
    elapsed = days * 86400 + nums[0] * 3600 + nums[1] * 60 + nums[2]
    begun = time.time() - elapsed
    # 席位登記之後才出生的行程,不可能是當初那一席(容 5 秒量測誤差)。
    return begun <= float(started_epoch) + 5


# 活席在 kernel_task 前面:top -l 2 最貴,但畫面有畫降溫機制、沒畫重活/電池,
# 所以 kernel_task 要排在那兩格之前,否則 0.3s 的 ps 一跑,剩下不夠 0.8s,
# 降溫機制每輪都被略過。剩下不到 0.25s 才略過,讓 top 用剩餘時間試。
CELLS = (load1, mem_free_pct, swap_used_mb, live_seats, kernel_task_cpu,
         battery_temp_c, heavy_work)
CELL_MIN_S = {kernel_task_cpu: 0.25}
BUDGET_SKIPPED = "量不到(整輪預算用完)"
# 預算用完時仍要寫這一格真正的 key:畫面用 key 找格子,上次的值也靠 key 接。
# 寫函式名(kernel_task_cpu)的話,降溫機制會畫成 ?,上一筆也接不上。
CELL_META = {
    load1: ("load1", "負載"),
    mem_free_pct: ("mem", "記憶體"),
    swap_used_mb: ("swap", "swap"),
    live_seats: ("seats", "活席"),
    battery_temp_c: ("battery", "電池溫度(非 CPU)"),
    heavy_work: ("heavy", "重活"),
    kernel_task_cpu: ("kernel_task", "熱節流"),
}


def sample(runner=run):
    """取一輪。回 (cells, elapsed_seconds) —— **秒數也是產出**:
    為了顯示熱而讓面板變成熱源,是本件最大的失敗模式,所以它要能被斷言。

    ★ 整輪預算由這裡強制:超時之後**剩下的格誠實說「整輪預算用完」**,不是硬跑完再道歉。
      不得回上一次的值冒充現值(kernel_task 那格的「上次 X 於 N 秒前」是另一段字樣,不混用)。
    """
    global _DEADLINE
    t0 = time.monotonic()
    _DEADLINE = t0 + ROUND_BUDGET_S
    cells = []
    try:
        for fn in CELLS:
            left = _DEADLINE - time.monotonic()
            need = CELL_MIN_S.get(fn, 0)
            if left <= 0 or left < need:
                key, label = CELL_META.get(fn, (fn.__name__, fn.__name__))
                # 與 READ_FAILED 分字樣:資料源還在,是這一輪時間不夠。
                cells.append(_cell(key, label, None, BUDGET_SKIPPED, "unmeasured",
                                   "整輪預算用完"))
                continue
            cells.append(fn(runner=runner))
    finally:
        _DEADLINE = None
    cells.insert(4, cpu_temp())
    return cells, time.monotonic() - t0


def render(cells):
    """單行文字。門檻只在 THRESHOLDS,這裡不重寫判斷。"""
    mark = {"ok": "·", "warn": "!", "hot": "!!", "unmeasured": "?"}
    return " ".join("%s%s %s" % (mark.get(c["state"], "?"), c["label"], c["text"])
                    for c in cells)


if __name__ == "__main__":
    rows, took = sample()
    print(render(rows))
    print("一輪取值 %.3fs" % took)
