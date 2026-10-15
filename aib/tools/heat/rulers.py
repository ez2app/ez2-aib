#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""能不能起重活——一把帶自校準的尺。

今晚兩次錯誤的放行/擋人,都不是判斷力問題,是每人手搓一把沒校準的尺
(交接第五節 12 條)。這支是那 12 條的可執行版。

席甲 09-04 收線令:計數法不再開新格、不再跑新實驗。正本擁有者 席乙。
雜訊底是呼叫者的性質,不是機器的性質,也不是指令的性質——沒有跨呼叫者的正解。
每個呼叫者為自己跑一次誘餌對照,結果只對自己有效。
A 型行程名走 ps -Ao comm= 單欄,是「這個呼叫者」過關,不是全艦唯一過關。
B 型 args 走快照獨立一次呼叫 + 排整條祖先鏈 + 不生比對子行程。
正校準必須經過與目標完全相同的那條管線(含排鏈)。
四個對照只抓高估。chrome=0 在低估對照未量之前不得當再起一輪;起重活走窗口制。
機制對照問的是欄位裝不裝得下目標那種字串;前三個對照都預設尺看得到目標。
已關與未證分兩欄寫,不要只寫已關。
來源與計數分兩步,不用管線:來源壞不得吐數字。kernel_task 逾時並列上次與年齡。
維護優先保每條規則旁邊那個可辨認的失效長相,不是只保規則字面。
席甲 v6 是最後一份補丁;此後真撞到才補。

艦隊級正本還沒有已提交的 markdown。可執行正本就是本檔(開源版位置 aib/tools/heat/rulers.py)。

用法:
    python3 tools/heat/rulers.py
    python3 tools/heat/rulers.py --json
    python3 tools/heat/rulers.py --color   # 人眼:終端才上色
    python3 tools/heat/rulers.py --no-color

結束碼: 0=go, 2=no-go(熱/佔窗/或 0 未經低估對照), 3=尺自己壞了(校準臂紅)。
量不到不是 0。人眼色:綠=可放行、紅=不要起、洋紅=量不到/尺壞/未經低估對照。
三色不得共用。JSON 與管線零 ANSI。NO_COLOR 關掉上色。
"""
from __future__ import annotations
# 用 importlib.spec_from_file_location 載本檔:必須先 sys.modules[spec.name]=mod
# 再 exec。否則 @dataclass 解析炸成 'NoneType' object has no attribute '__dict__',
# 那個訊息一個字都沒提到 dataclass(席丙 02:13)。

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

# 門檻只住這裡。渲染端與判斷端都讀它,不得再抄一份。
THRESHOLDS = {
    "load1_nogo": 15.0,          # uptime 行 load averages 後第一個數;>= 不要起瀏覽器臂
    "kernel_task_nogo": 20.0,    # %CPU;>= 代表正在降頻吸熱
    "kernel_task_timeout_s": 0.8,  # top -l 1 在高負載最需要它時會超過;逾時不是 3s 那格
    "wall_ms_budget": 1000.0,    # 尺自己變成熱源 = 最大失敗模式
}

# 校準臂 A6 的針。只出現在本檔字面,不該出現在別人的 argv。
SELF_NEEDLE = "HEAT_RULER_SELF_NEEDLE_9f3c"
# 負對照:保證不存在的樣式。必須 =0,且只在與目標同一組 argv 時才有意義。
# 不得當萬用鑰匙,不得拿來減出「經過校準」的錯數字。
# 席丁:「別席殼帶目標字面不帶負校準字面 ⇒ 減完仍偏高」沒有量到,標成推,不要升級。
NEG_CALIB_NEEDLE = "HEAT_RULER_NEG_CALIB_e7a1"
# 正對照:必須經過與目標完全相同的那條管線(含排鏈)。只證明尺沒死,對高估是盲的。
# 兩個洞,不要併成一句:①校準目標在祖先鏈上 ②校準跑在另一把尺上(不排鏈)。
# A 型(comm 單欄)用執行檔名;B 型不得用祖先鏈上的東西(launchd/tmux),改用路徑分隔符。
POS_CALIB_COMM = "node"     # A 型(comm 單欄)的針:一個執行檔名
POS_CALIB_ARGS = "/"  # 路徑分隔符:指令列幾乎都有,不專屬任一祖先鏈
# ★ 2026-09-07:別名從 A 型的 `node` 改指 B 型的路徑分隔符。
#   `POS_CALIB_NEEDLE` **唯一的消費者**是 `machine_status.heavy_work()`,而它把這個針
#   餵給 `count_cmd_contains` —— 那是 **B 型 args 管線**。上面第 67 行那句話
#   (「A 型用執行檔名;B 型不得用祖先鏈上的東西,改用路徑分隔符」)本來就寫著答案,
#   只是別名指錯了一邊。
#   ★ 症狀不是紅,是**由機器狀態決定的紅**:`node` 沒在跑時正校準=0 ⇒
#     `heavy_work()` 回「正校準 0(ruler-broken)」⇒ 面板重活格量不到。
#     09-07 翻轉實測:起一個 node → `tests/test-pane-right-status.py` rc=0;
#     收掉那個 node → rc=1。**那支臂的顏色由機器上有沒有 node 決定,
#     不是由被測性質決定。**
#   ★ 為什麼不用 `launchd`(A7 那段註解裡那個字):兩個理由,都是量出來的。
#     ① 真的那一支是 `/sbin/launchd`(pid 1),它在**每一條祖先鏈上** ⇒ 排鏈後恆 0
#        (09-07 實量:`/sbin/launchd` 排鏈後 0)。這正是 `arm_known_alive` 檔頭那個洞①。
#     ② 裸字 `launchd` 排鏈後**是 10**,但那 10 個全是**別的 daemon 把 `-launchd`
#        當旗標傳**(usbmuxd -launchd、corebrightnessd --launchd、biometrickitd --launchd…),
#        沒有一個是 launchd。那個非零來自一個與被校準性質無關的東西,
#        而且隨 macOS 版本會變 —— **綠得對不上理由的綠**。
#   ★ 它什麼時候會不在(紅線一要求說得出來):`/` 只有在「排鏈之後剩下的每一個行程
#     指令列都沒有路徑分隔符」時才會是 0。09-07 實量:母體 725,不含 `/` 的 16 個
#     (kernel/daemon 的裸名字如 `endpointsecurityd`、`autofsd`),含 `/` 的 709。
#     要讓它變 0,得整台機器只剩那種行程 —— 而 ps 整個讀不到時,`measure_ps` 早一步
#     就回「量不到」了,不會走到這一格。
POS_CALIB_NEEDLE = POS_CALIB_ARGS  # 消費者是 B 型 args 管線(見上)
# 機制對照(席戊):問欄位裝不裝得下目標那種字串。正/負/誘餌都預設尺看得到目標。
# 代理要一定在跑且與目標同形。席戊 讀數:comm 抓 'Google Drive.app'=7,
# 加尾巴 '.NOPE'=0。沒有這一格,目標不在場的 0 與「欄位裝不下」逐字相同。
# node 是短名字,證不了 comm 裝得下 Chrome for Testing(18 字)。不現在換針。

UNMEASURED_DISPLAY = "量不到"
LEGAL_ZERO_DISPLAY = "0(合法)"  # 與「量不到」不得同形
KERNEL_LAST_PATH = os.path.expanduser(
    "~/.ez2-aib/state/heat-kernel-task-last.json")
# 四個對照只抓高估。恆回 0 的尺會通過今晚每一格。與合法零、量不到都不得同形。
UNDERCOUNT_ZERO_DISPLAY = "0(未經低估對照)"
# 再量條件=下次真有 Chrome 在跑時,A 法與 B 法各列舉一次並對數(比 pid 集合,不比兩個數字)。
# 01:56-01:57 條件曾成立(席己 comm 目標 41 / 席甲 30,數字不是 pid 集合)。
# 席乙 03:27:54 列舉:A=0 B=0 pid 空,Chrome 已不在場。這一格未過。在那之前不得改 True。
# 已關 vs 未證(分兩欄,不要只寫已關):
#   A 型低估 已關(席庚 那個呼叫者,40 支真 Chrome,時間序列 0→25→40→18→0)
#   A 型低估 未證(本尺呼叫者 03:27:54 場已空)
#   B 型機制對照 未證;B 型低估 未證(母數=1 太小)
# 「那個數字不可信」不等於「當時是 0」;「這一格量到了」不等於「這一族關掉了」。
UNDERCOUNT_CALIBRATED = False
# 行程名型目標認 comm 單欄。這是本呼叫者誘餌對照過關的 A 型,不是跨呼叫者唯一正解。
CHROME_COMM_MARK = "Chrome for Testing"

# 人眼色單源。量不到不得用「可放行」的綠——否則探測失效與安全同形。
# 綠=go / 紅=no-go / 洋紅=尺壞或量不到 / 灰=只供人看不當閘。
TONES = ("go", "nogo", "broken", "info")
ANSI = {
    "go": "\033[32m",
    "nogo": "\033[31m",
    "broken": "\033[35m",
    "info": "\033[90m",
    "reset": "\033[0m",
}


@dataclass
class Cell:
    key: str
    measured: bool
    value: Any
    display: str
    raw: str | None = None
    why: str | None = None
    hits: list[str] = field(default_factory=list)
    target: int | None = None
    pos_cal: int | None = None
    neg_cal: int | None = None
    chain_len: int | None = None
    source_n: int | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def triple_complete(self) -> bool:
        return (
            self.measured
            and self.target is not None
            and self.pos_cal is not None
            and self.neg_cal is not None
            and self.source_n is not None
            and self.source_n > 0
        )


@dataclass
class Arm:
    id: str
    ok: bool
    detail: str


@dataclass
class Snapshot:
    verdict: str
    reasons: list[str]
    cells: dict[str, Cell]
    arms: list[Arm]
    wall_ms: float
    raw_uptime: str
    observed_at: str = ""
    thresholds: dict[str, float] = field(default_factory=lambda: dict(THRESHOLDS))

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "cells": {k: v.as_dict() for k, v in self.cells.items()},
            "arms": [asdict(a) for a in self.arms],
            "wall_ms": self.wall_ms,
            "raw_uptime": self.raw_uptime,
            "observed_at": self.observed_at,
            "thresholds": self.thresholds,
        }


def _cell_ok(
    key: str,
    value: Any,
    display: str,
    raw: str | None = None,
    hits: list[str] | None = None,
    target: int | None = None,
    pos_cal: int | None = None,
    neg_cal: int | None = None,
    chain_len: int | None = None,
    source_n: int | None = None,
    why: str | None = None,
) -> Cell:
    return Cell(
        key=key, measured=True, value=value, display=display,
        raw=raw, why=why, hits=list(hits or []),
        target=target, pos_cal=pos_cal, neg_cal=neg_cal,
        chain_len=chain_len, source_n=source_n,
    )


def _cell_miss(key: str, why: str, display: str | None = None) -> Cell:
    # 量不到永遠不是 0、不是上一筆、不是空白。逾時有自己的字樣,不得共用。
    return Cell(
        key=key,
        measured=False,
        value=None,
        display=display or UNMEASURED_DISPLAY,
        why=why,
    )


def _run(argv: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# A8 負載:貼原始 uptime 那一行;1 分欄是三個 load 的第一個,不是「第 2 欄」。
# 席丙 寫「sysctl 第 2 欄 = 1 分鐘」是把 `{` 算成第 1 欄才碰巧對。
# 尺認語意(1/5/15 分),原始行是人類對帳用的尺面。
# ---------------------------------------------------------------------------
_LOAD_RE = re.compile(
    r"load averages?:\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)",
    re.I,
)
_SYSCTL_LOAD_RE = re.compile(r"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)")


def parse_uptime_loads(raw: str) -> tuple[float, float, float] | None:
    m = _LOAD_RE.search(raw)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def measure_uptime(raw: str | None = None) -> tuple[Cell, Cell, str]:
    if raw is None:
        try:
            proc = _run(["uptime"], timeout=1.0)
        except (OSError, subprocess.TimeoutExpired) as e:
            miss = _cell_miss("load1", "uptime 失敗:%s" % e)
            return miss, _cell_miss("load15", miss.why or ""), ""
        if proc.returncode != 0:
            miss = _cell_miss("load1", "uptime rc=%s stderr=%s" % (
                proc.returncode, (proc.stderr or "").strip()[:80]))
            return miss, _cell_miss("load15", miss.why or ""), proc.stdout or ""
        raw = proc.stdout.strip()
    loads = parse_uptime_loads(raw)
    if loads is None:
        miss = _cell_miss("load1", "uptime 行剖不出三欄 load: %r" % raw[:120])
        return miss, _cell_miss("load15", miss.why or ""), raw
    load1, load5, load15 = loads
    return (
        _cell_ok("load1", load1, "%.2f" % load1, raw=raw),
        _cell_ok("load15", load15, "%.2f" % load15, raw=raw),
        raw,
    )


# ---------------------------------------------------------------------------
# A10 kernel_task:直接問 pid 0,不得 top 排序取前 N。
# 0.0% 是合法零,與「量不到」必須不同字樣。
# v6:逾時是第三種成因,字樣與剖不出、top 失敗都不同。逾時並列上次與其年齡,
# 兩段字樣完全不同——單獨「量不到」在最需要時什麼都沒有;單獨「沿用上次」
# 會讓過期值冒充現值。失效值不是現值。
# ---------------------------------------------------------------------------
_TOP_CPU_RE = re.compile(r"^\s*0\s+([0-9.]+)\b", re.M)


def kernel_timeout_display() -> str:
    return "量不到(逾時 %.1fs)" % THRESHOLDS["kernel_task_timeout_s"]


def kernel_fail_display() -> str:
    return "量不到(top 失敗)"


def _read_kernel_last(path: str) -> tuple[float, float] | None:
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        return float(d["value"]), float(d["epoch"])
    except (KeyError, TypeError, ValueError):
        return None


def _write_kernel_last(path: str, value: float, epoch: float) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"value": value, "epoch": epoch}, f)
    os.replace(tmp, path)


def kernel_task_sample_count(top_out: str) -> int:
    """`top_out` 裡有幾列 kernel_task。

    ★ 2026-09-08 第二審(Gemini)抓到的分裂:`machine_status.py` 已經改成
      「只有一個取樣就判量不到」,而這裡沒有 —— 於是同一份只有一列的輸出,
      那邊說量不到、這邊把第一取樣的 **0.0 當成合法讀數**。
      **兩支尺對同一份輸入給不同答案,而不同的那一邊正好是會騙人的那一邊。**
    """
    n = 0
    for line in top_out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "0" and "kernel_task" in line:
            n += 1
    return n


def parse_kernel_task_cpu(top_out: str) -> float | None:
    # macOS `top -l 1 -pid 0 -stats pid,cpu,command` 資料列:
    # PID %CPU COMMAND
    # 0   0.0  kernel_task
    # ★ 取**最後一列**:兩取樣時第一列恆為 0.0(沒有前一幀可以相減),
    #   只有一列時行為與舊版逐字相同。
    hit = None
    for line in top_out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "0" and "kernel_task" in line:
            hit = parts
    if hit is not None:
        try:
            return float(hit[1])
        except ValueError:
            return None
    m = _TOP_CPU_RE.search(top_out)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _kernel_timeout_cell(last_path: str | None, now: float) -> Cell:
    base = kernel_timeout_display()
    last = _read_kernel_last(last_path) if last_path else None
    if last is None:
        return _cell_miss("kernel_task", "逾時", display=base)
    value, epoch = last
    age = max(0, int(now - epoch))
    display = "%s · 上次 %.1f%% 於 %d 秒前" % (base, value, age)
    return _cell_miss("kernel_task", "逾時", display=display)


def measure_kernel_task(
    top_out: str | None = None,
    *,
    last_path: str | None = None,
    now: float | None = None,
    top_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> Cell:
    live = top_out is None
    path = last_path if last_path is not None else (
        KERNEL_LAST_PATH if live else None)
    tnow = time.time() if now is None else now
    timeout_s = THRESHOLDS["kernel_task_timeout_s"]
    if top_out is None:
        # ★ 09-08:`-l 1` 的第一取樣沒有前一幀可以相減 ⇒ kernel_task 的 %CPU
        #   結構上只可能是 0.0,而 kernel_task_nogo=20.0 因此恆假。改取兩個取樣、
        #   用第二個(`-s 0` 讓間隔為 0:實測 0.64–0.68s,對照預設間隔 1.75s)。
        argv = ["top", "-l", "2", "-s", "0", "-pid", "0",
                "-stats", "pid,cpu,command"]
        try:
            runner = top_runner or _run
            proc = runner(argv, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return _kernel_timeout_cell(path, tnow)
        except OSError as e:
            return _cell_miss(
                "kernel_task", "top 失敗:%s" % e,
                display=kernel_fail_display(),
            )
        if proc.returncode != 0:
            return _cell_miss(
                "kernel_task",
                "top rc=%s stderr=%s" % (
                    proc.returncode, (proc.stderr or "").strip()[:80]),
                display=kernel_fail_display(),
            )
        # ★ 只有一個取樣時,%CPU 結構上恆為 0.0 —— 那是指令的產物不是機器的狀態。
        #   注入字串的契約不動(既有臂餵單列),只有**活體**這條路要求兩個取樣。
        if kernel_task_sample_count(proc.stdout or "") < 2:
            return _cell_miss(
                "kernel_task", "只有 1 個取樣(單取樣的 %CPU 恆為 0.0,不採信)",
                display=kernel_fail_display(),
            )
        top_out = proc.stdout
    cpu = parse_kernel_task_cpu(top_out)
    if cpu is None:
        return _cell_miss("kernel_task", "top -pid 0 輸出剖不出 %CPU")
    display = LEGAL_ZERO_DISPLAY if cpu == 0.0 else "%.1f" % cpu
    if path:
        try:
            _write_kernel_last(path, cpu, tnow)
        except OSError:
            pass
    return _cell_ok("kernel_task", cpu, display, raw="pid 0")


# ---------------------------------------------------------------------------
# 行程清單:用 ps 剖欄,禁止 ps|grep。A6 自 match 靠這條路消失。
# ---------------------------------------------------------------------------
# 行程清單。Chrome 認 comm(執行檔名),runner 認 args 且排整條祖先鏈。
# 括號技巧只讓 grep 自己的樣式不匹配;同一段 zsh -c 裡別行的純字面仍污染 args。
# ---------------------------------------------------------------------------
@dataclass
class Proc:
    pid: int
    ppid: int
    command: str
    comm: str = ""


def parse_ps_axo(text: str) -> list[Proc]:
    """每行 `pid ppid comm<TAB>command`。沒有 TAB 時 comm 取 command 第一個欄位。"""
    rows: list[Proc] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rest = parts[2]
        if "\t" in rest:
            comm, command = rest.split("\t", 1)
        else:
            command = rest
            comm = rest.split(None, 1)[0] if rest else ""
        rows.append(Proc(pid=pid, ppid=ppid, command=command, comm=comm))
    return rows


def _ps_comm_only() -> tuple[list[str], int]:
    """A 型唯一合法來源:`ps -Ao comm=` 單欄。不得附加任何其他欄。

    規則比必要的嚴(pid=,comm= 兩欄 comm 最長仍未截),單欄仍是安全的。
    席辛 01:57:截斷的成因只有一個——一旦同一次輸出要求 args=,comm 就被
    截到 16。長於 16 的樣式(Chrome for Testing 是 18)從此恆為 0。
    截斷這件事上 -Ao 與 -ax -o 的 comm 最長相同;不是旗標形式、不是
    「有第二欄就被截」。寫成機器相依會讓人量不到就跳過,而加上 args=
    任何人都會重現。讀整行的人看不到截斷:args 污染是吵的,截斷是靜的,
    靜的那個先被相信。
    席丁 01:56:他這台兩種形式都不截(最長都 205),但行數 645 vs 636 差 9
    —— -ax 與 -A 列舉的不是同一組行程。換一個 ps 形式就是換一把尺,
    目標數本身不可互相比較(席己 補丁三從正校準延伸到目標數)。
    定版寫死 -Ao:截斷複驗得出時理由是避開截斷;複驗不出時理由是母體固定。
    席丁 01:4x 的 ps -ax -o comm= 作廢。
    席壬 撤回不是提案:不要引用 01:2x 那個 30(ps -eo pid,comm | grep -ci
    交替:兩欄不是單欄,且分不出 Chrome 與 puppeteer)。01:45 之後那個 0
    四對照齊,對高估可引用;依收線令 §四仍是未經低估對照的 0,不得當再起一輪。
    """
    proc = _run(["ps", "-Ao", "comm="], timeout=2.0)
    ok, n, why = source_assert(proc.stdout, proc.returncode)
    if not ok:
        raise RuntimeError("ps -Ao comm= %s n=%d" % (why, n))
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return lines, n


def _ps_args_procs() -> list[Proc]:
    """B 型:pid/ppid/command,不含 comm 欄。比對在 Python 裡做,不生 grep 子行程。"""
    proc = _run(["ps", "-Ao", "pid=", "-o", "ppid=", "-o", "command="], timeout=2.0)
    ok, n, why = source_assert(proc.stdout, proc.returncode)
    if not ok:
        raise RuntimeError("ps args %s n=%d" % (why, n))
    rows: list[Proc] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        token = cmd.split(None, 1)[0] if cmd else ""
        rows.append(Proc(pid=pid, ppid=ppid, command=cmd, comm=token))
    return rows


def _live_ps_text() -> str:
    """測試治具仍走 parse_ps_axo。活量 A 型 comm 單欄、B 型另取 args,不合欄。"""
    comms, _n = _ps_comm_only()
    args_rows = _ps_args_procs()
    # 治具格式:pid ppid comm<TAB>command。comm 來自單欄對照不上 pid 時留空。
    # 活量閘用 comms,不靠這份合表的 comm。
    out = []
    for p in args_rows:
        out.append("%d %d %s\t%s" % (p.pid, p.ppid, p.comm.replace("\t", " "), p.command))
    if not out and comms:
        raise RuntimeError("ps args 空而 comm 母體=%d" % len(comms))
    return "\n".join(out) + "\n"


def measure_ps(text: str | None = None) -> tuple[Cell, list[Proc]]:
    if text is None:
        try:
            text = _live_ps_text()
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
            return _cell_miss("ps", "ps 失敗:%s" % e), []
    ok, n, why = source_assert(text, 0)
    if not ok:
        return _cell_miss("ps", why), []
    rows = parse_ps_axo(text)
    if not rows:
        return _cell_miss("ps", "ps 剖完是空的(母體=%d)" % n), []
    return _cell_ok(
        "ps", len(rows), "%d 母體=%d" % (len(rows), n), source_n=n,
    ), rows


def ancestor_pids(rows: list[Proc], start_pid: int) -> set[int]:
    """從 start 走到 pid 1。只排 $$ 與 $PPID 會漏 tmux 與本席(今晚實測 6 層)。"""
    by_pid = {p.pid: p for p in rows}
    out: set[int] = set()
    cur: int | None = start_pid
    while cur and cur not in out:
        out.add(cur)
        node = by_pid.get(cur)
        if node is None or node.ppid == cur:
            break
        cur = node.ppid
    return out


def describe_hits(hits: list[Proc], rows: list[Proc], limit: int = 12) -> list[str]:
    """計數格要能逐行認出是誰。今晚兩次都是靠讀 ps 行才知道命中的是自己。"""
    by_pid = {p.pid: p for p in rows}
    lines = []
    for p in hits[:limit]:
        parent = by_pid.get(p.ppid)
        gp = by_pid.get(parent.ppid) if parent is not None else None
        lines.append(
            "pid=%d comm=%s ppid=%d(%s) gp=%s args=%s" % (
                p.pid,
                p.comm[:40],
                p.ppid,
                (parent.comm[:30] if parent else "?"),
                (gp.comm[:30] if gp else "?"),
                p.command[:80],
            )
        )
    if len(hits) > limit:
        lines.append("... +%d" % (len(hits) - limit))
    return lines


def count_cmd_contains(
    rows: list[Proc],
    needle: str,
    exclude_pids: Iterable[int] = (),
    exclude_ancestors_of: int | None = None,
) -> list[Proc]:
    """args 型目標。樣式會出現在量測殼自己的 argv 上,所以要排整條祖先鏈。
    正校準必須走這同一條(含排鏈)。不排鏈的 launchd=1 什麼都沒證明。"""
    skip = set(exclude_pids)
    if exclude_ancestors_of is None:
        exclude_ancestors_of = os.getpid()
    skip |= ancestor_pids(rows, exclude_ancestors_of)
    not_runner = {"grep", "rg", "ugrep"}
    hit = []
    for p in rows:
        if p.pid in skip:
            continue
        if os.path.basename(p.comm) in not_runner:
            continue
        if needle in p.command:
            hit.append(p)
    return hit


def count_by_comm(rows: list[Proc], needle: str) -> list[Proc]:
    """行程名型。誘餌在 args 裡,comm 仍是 zsh/grep。"""
    hits = []
    for p in rows:
        comm = p.comm or ""
        base = os.path.basename(comm)
        if needle == base or needle in comm:
            hits.append(p)
    return hits


def count_args_naive(rows: list[Proc], needle: str) -> list[Proc]:
    """不排祖先、不濾 grep。只給治具看污染量,不當放行條件。"""
    return [p for p in rows if needle in p.command]


def count_controls(
    target_hits: list[Proc],
    pos_hits: list[Proc],
    neg_hits: list[Proc],
) -> tuple[int, int, int]:
    """正/負對照與目標同一次呼叫。閘用目標數,不用目標減負。
    負≠0 是本次呼叫被污染,讀數作廢,不得減出一個看起來經過校準的數。
    02:21 若寫過相減或 count_triple,以本函為準作廢。碼是正本。"""
    return len(target_hits), len(pos_hits), len(neg_hits)


# 面板 import 必備名字。合入本樹之前改名要 dm 使用端
# (席丙 02:26:兩次跑之間 count_triple 消失,狀態列 AttributeError,最需要時什麼都不顯示)。
PANEL_REQUIRED_NAMES = (
    "measure_ps", "suite_runners", "chrome_mains", "chrome_profiles",
    "count_cmd_contains", "count_controls", "describe_hits", "ancestor_pids",
    "POS_CALIB_NEEDLE", "NEG_CALIB_NEEDLE", "read_cpu_die_temps",
)


def bait_immune(count_without: int, count_with: int) -> bool:
    """誘餌對照:有 vs 完全沒有。讀數不變=免疫;變大=數到自己。
    單位是「argv 含該字面的行程數」,加次數不會讓讀數變大。"""
    return count_with == count_without


def in_memory_bait(
    rows: list[Proc],
    count_fn,
    literal: str | None = None,
) -> tuple[int, int, bool]:
    """誘餌對照範本:一個 argv 帶字面、一個不帶,讀同一把尺,讀數必須逐字相同。
    活量預設用記憶體假行程,不衍生真子行程——每一輪快照起兩個行程會讓尺變熱源。
    """
    mark = CHROME_COMM_MARK if literal is None else literal
    decoy = Proc(
        pid=999001, ppid=1, comm="zsh",
        command="zsh -c echo %s" % mark,
    )
    without = count_fn(rows)
    with_bait = count_fn(list(rows) + [decoy])
    return without, with_bait, bait_immune(without, with_bait)


def grep_c_status(rc: int) -> str:
    """grep -c:0=有命中,1=零筆(正常結果),>=2=來源壞掉。不得用 -ne 0,也不得 || echo 0/999。
    ★ 管線裡這個分類不夠:來源壞掉時 grep 吃到空輸入,一樣 rc=1 值=0。"""
    if rc == 0:
        return "hits"
    if rc == 1:
        return "zero"
    return "broken"


def count_comm_from_source(
    src: str, src_rc: int, needle: str,
) -> tuple[bool, int | None, int, str]:
    """來源與計數分兩步,不用管線、不用 PIPESTATUS。
    來源壞時命中數字是 None(不吐數字);合法零筆是 0。兩者不得同形。
    v6:N=$(ps|grep -c) 的 RC 是管線最後一段,ps 死=值0 rc1,與正常零筆逐字相同。
    PIPESTATUS 活不過命令替換,合法零筆會假紅。"""
    ok, n, why = source_assert(src, src_rc)
    if not ok:
        return False, None, n, why
    hits = sum(1 for ln in src.splitlines() if needle in ln.strip())
    return True, hits, n, why


def arm_source_end_failure() -> Arm:
    """樣式四對照看不見來源端失敗。空+rc1 必須擋且不吐數字。"""
    ok, hits, n, why = count_comm_from_source("", 1, CHROME_COMM_MARK)
    if ok or hits is not None:
        return Arm("A15", False, "來源壞被當成通過 hits=%s n=%s" % (hits, n))
    return Arm(
        "A15",
        True,
        "來源端失敗不進計數、不吐數字(%s);樣式四對照看不見這一格" % why,
    )


def source_assert(raw: str, rc: int) -> tuple[bool, int, str]:
    """來源與計數分兩步。空輸入或來源非 0,擋,不進計數。
    母體大小是第五個對照:0 行是尺壞了,而它不會出現在 grep 的 rc 裡。
    ★ 只印這一瞬間量到的行數,不得寫死「正常 N 行」(估值寫在量測旁邊更難被質疑)。"""
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    n = len(lines)
    if rc != 0:
        return False, n, "source rc=%s" % rc
    if n == 0:
        return False, 0, "source empty"
    return True, n, "ok"


def chrome_comm_names(comms: list[str]) -> list[str]:
    """A 型閘:只看 comm 單欄字串。"""
    out = []
    for c in comms:
        if CHROME_COMM_MARK not in c:
            continue
        if "Helper" in c or "crashpad" in c.lower():
            continue
        out.append(c)
    return out


def comm_needle_hits(comms: list[str], needle: str) -> list[str]:
    hits = []
    for c in comms:
        base = os.path.basename(c)
        if needle == base or needle in c:
            hits.append(c)
    return hits


def chrome_mains(rows: list[Proc]) -> list[Proc]:
    """行程名型目標走 comm,不走 args。Helper / crashpad / 量測殼的誘餌都進不了 comm。"""
    out = []
    for p in rows:
        comm = p.comm or ""
        if CHROME_COMM_MARK not in comm:
            continue
        if "Helper" in comm or "crashpad" in comm.lower():
            continue
        if "--type=" in p.command:
            continue
        out.append(p)
    return out


def chrome_profiles(mains: list[Proc]) -> list[str]:
    found = []
    for p in mains:
        m = re.search(r"--user-data-dir=(\S+)", p.command)
        if m:
            found.append(m.group(1))
    seen = set()
    uniq = []
    for x in found:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


# ---------------------------------------------------------------------------
# 「真的在跑這支腳本」與「argv 只是提到這個檔名」(缺陷二十,2026-09-07)
#
# 舊取法是 `count_cmd_contains(rows, "aib-release.sh")` —— **比的是指令列含不含
# 這個字面**。於是任何一個讀這支檔的指令都會被算成「正在跑發版」:
# 現場重現 `zsh -c 'sleep 25; : tools/aib-release.sh'`(`:` 是 no-op)立刻回 1;
# 活的執行帳本裡 `sed -n '60,140p' tools/aib-release.sh` 這種讀檔指令一大把。
# ★ 通知印的是「**正在跑**」—— 那是比量測強的宣稱。
#
# ★ 修的是**性質**不是名單:再排掉一個 `zsh` 只會讓舊數字自己變小像修好了,
#   而下一種寫法照樣繞過(那是往 any-of 名單加項目)。
#   性質只有兩條 —— 一支殼腳本**只有兩種方式會真的被執行**:
#     (a) 直接執行它(argv0 就是它;帶 shebang 時 ps 顯示 `/bin/bash <它>`)
#     (b) 交給殼當**腳本運算元**(`bash <它> …`)
#   argv 裡其他位置出現這個名字,都是**別人在談論它**,不是在跑它。
#   實量三種形狀(09-07,真的把行程起起來看 ps):
#     /bin/bash ./tools/aib-release.sh --release 9.9.9   → 跑
#     bash tools/aib-release.sh --check-fast             → 跑
#     zsh -c sleep 20; : tools/aib-release.sh            → 只是提到
#
# ★ 分不出來的時候**算它在跑**,不是丟掉:丟掉的方向會把一個真的在跑的發版
#   變不見,而那與「沒有人在跑」逐字相同;算進去只是多問人一次。
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "busybox"}
# 只在前面包一層、不改變「誰被執行」的程式。不是黑名單,是「透明包裝」這個性質。
_WRAPPERS = {"env": 0, "nohup": 0, "nice": 0, "stdbuf": 0, "caffeinate": 0,
             "time": 0, "timeout": 1}     # 值=該包裝自己吃掉幾個位置參數
# 直譯器:argv0 不是腳本,腳本站在它後面的第一個位置參數。
# ★ 為什麼要有這一類:`node tools/test.js` 的 argv0 是 node,不是 test.js ——
#   少了這一類,真的在跑的那一支會被判成 MENTION,而那一格就會**恆報 0**。
#   0 的意思是「沒有人在跑」,正是各種「在飛就不要動」的閘拿來放行的值。
#   多報只是吵人,少報會讓閘在該擋的時候放行(2026-09-13 第 132 項)。
# 值 = 那個直譯器「後面接的是程式碼或模組名,不是腳本檔」的旗標。
_INTERPRETERS = {
    "node": {"-e", "--eval", "-p", "--print"},
    "python": {"-c", "-m"},
    "python3": {"-c", "-m"},
    "ruby": {"-e"},
    "perl": {"-e", "-E"},
}

RUN, MENTION, UNKNOWN = "run", "mention", "unknown"


def script_invocation_shape(command: str, script_basename: str) -> str:
    """argv 是在**跑**這支腳本,還是只是**提到**它。→ RUN / MENTION / UNKNOWN。

    ★ 這裡不做字面排除。判準是位置:argv0 是它,或它站在殼的腳本運算元位置。
    ★ 已具名的盲區:`ps` 把 `-c` 的參數去引號後空白接起來,所以本函式用空白切詞
      —— 路徑裡有空白的腳本會切壞(本倉沒有這種路徑,有的話這一格要重寫)。
    """
    tokens = command.split()
    if not tokens:
        return UNKNOWN
    i = 0
    while i < len(tokens):
        head = os.path.basename(tokens[i])
        if "=" in tokens[i] and not tokens[i].startswith("-") and "/" not in tokens[i].split("=")[0]:
            i += 1                      # VAR=VAL 前綴
            continue
        if head in _WRAPPERS:
            i += 1 + _WRAPPERS[head]
            continue
        break
    if i >= len(tokens):
        return UNKNOWN
    if os.path.basename(tokens[i]) == script_basename:
        return RUN                      # (a) 直接執行
    head = os.path.basename(tokens[i])
    if head in _INTERPRETERS:
        code_flags = _INTERPRETERS[head]
        j = i + 1
        while j < len(tokens) and tokens[j].startswith("-") and tokens[j] != "--":
            if tokens[j] in code_flags or tokens[j].split("=")[0] in code_flags:
                return MENTION          # -c/-m/-e 之後是**程式碼或模組名**,不是腳本運算元
            j += 1
        if j < len(tokens) and tokens[j] == "--":
            j += 1
        if j < len(tokens) and os.path.basename(tokens[j]) == script_basename:
            return RUN                  # (c) 直譯器的腳本運算元
        return MENTION
    if head not in _SHELLS:
        return MENTION                  # 別的程式在讀它/談論它
    j = i + 1
    while j < len(tokens) and tokens[j].startswith("-") and tokens[j] != "--":
        if "c" in tokens[j].lstrip("-"):
            return MENTION              # -c 之後是**命令字串**,不是腳本運算元
        j += 1
    if j < len(tokens) and tokens[j] == "--":
        j += 1
    if j < len(tokens) and os.path.basename(tokens[j]) == script_basename:
        return RUN                      # (b) 殼的腳本運算元
    return MENTION


def running_script(
    rows: list[Proc],
    script_basename: str,
    exclude_ancestors_of: int | None = None,
) -> list[Proc]:
    """真的在跑這支腳本的行程。`count_cmd_contains` 先收窄母體,再問形狀。

    ★ 保留 `count_cmd_contains` 的排鏈與 grep 排除 —— 那兩件仍然要做,
      本函式只多問一句「這是跑還是提到」。UNKNOWN 留在結果裡(見上面那段)。
    """
    mentions = count_cmd_contains(
        rows, script_basename, exclude_ancestors_of=exclude_ancestors_of,
    )
    return [p for p in mentions
            if script_invocation_shape(p.command, script_basename) != MENTION]


# ---------------------------------------------------------------------------
# 登記制(T1-1,艦長 09-14 派;實作 tools/governance/run_registry.py)
#
# 「本倉的重活在不在跑」不再問 ps 字面,問**登記檔**:證詞與發版拿租約時就留了 pid/起始時刻/what,
# 整批測試/掃描用 `run_registry.py run` 包一層。09-14 12:21 現場:證詞每一列 concurrent_running_batches=2,
# 那 2 個是它自己的兩層 zsh 呼叫殼(祖先,排子孫排不到)——機器上零個旁人。一天五次同形。
# ★ 兩條規則(各一格守著,tests/test-run-registry-t11.py):
#   1. 登記檔有一筆活的而這裡回空 ⇒ 紅。
#   2. ps 有字面但登記檔沒有 ⇒ **不得**算成有人在跑 —— 這一條把上面 running_script 那段
#      「分不出來算它在跑」的方向翻過來。理由:舊方向讓通知的資訊量趨近零並訓練所有人忽略它;
#      而該被算到的都有接點(租約 / run 包裝),沒接點的**外倉**重活(node tools/test.js、pre-push)
#      仍走形狀判準,見 suite_runners。
# ★ 回的是 Proc:登記的 pid 對回 rows 裡那一行(要拿它的 ppid/comm/args 印給人看)。登記活著但 rows 裡
#   沒有那個 pid(ps 抽樣與登記之間死了)⇒ 不算,那不是「有人在跑」。排鏈:登記 pid 在呼叫者祖先鏈上
#   (自己的登記)一樣排掉。
# ★ 登記檔讀不到 ⇒ 回 None(量不到),**不回空 list**:空 list 是「量到零個」,那是放行值。
_REGISTRY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "governance")
_REGISTRY_CACHE: list = []


def _registry_module():
    """tools/governance/run_registry.py 不在 sys.path 上,把那個目錄加進去後**用名字 import**(不是按路徑 spec 載入):
    test-host-lease-isolation-24d 的閉包用 AST 追 import 名字,run_registry 讀共享機器狀態(登記 + 租約),
    這裡用名字 import 才會讓本檔進它的閉包、讓 import 本檔的尺被要求搬走租約落點。按路徑載入它就看不見。
    工具不在 ⇒ None(每次重看,治具會搬樹)。"""
    if not os.path.isfile(os.path.join(_REGISTRY_DIR, "run_registry.py")):
        return None
    if _REGISTRY_CACHE:
        return _REGISTRY_CACHE[0]
    if _REGISTRY_DIR not in sys.path:
        sys.path.insert(0, _REGISTRY_DIR)
    import run_registry  # noqa: PLC0415
    _REGISTRY_CACHE.append(run_registry)
    return run_registry


def registered_runs(
    rows: list[Proc],
    exclude_ancestors_of: int | None = None,
) -> dict[str, list[Proc]] | None:
    """登記檔裡**活著**的批,按 kind 分組、對回 ps 那一行。量不到(工具不在 / 登記檔讀不到)⇒ None。"""
    reg = _registry_module()
    if reg is None:
        return None
    live, _stale, _why = reg.collect(os.environ, exclude_chain_of=None)
    if live is None:
        return None
    skip_from = os.getpid() if exclude_ancestors_of is None else exclude_ancestors_of
    chain = ancestor_pids(rows, skip_from)
    by_pid = {p.pid: p for p in rows}
    out: dict[str, list[Proc]] = {}
    for e in live:
        try:
            pid = int(e.get("pid"))
        except (TypeError, ValueError):
            continue
        if pid in chain or pid not in by_pid:
            continue
        out.setdefault(str(e.get("kind") or "other"), []).append(by_pid[pid])
    return out


def suite_runners(
    rows: list[Proc],
    exclude_ancestors_of: int | None = None,
) -> dict[str, list[Proc]]:
    """跨兩倉的重活。外倉兩格憑形狀(祖父鏈 pre-push/git push),本倉兩格憑**登記**(T1-1)。"""
    skip_from = os.getpid() if exclude_ancestors_of is None else exclude_ancestors_of
    reg = registered_runs(rows, exclude_ancestors_of=skip_from)
    # ★ 登記量不到 ⇒ aib_release / registered 兩格**空**——但 registered_runs 已回 None 具名;
    #   這裡不退回 ps 字面(退回去就是兩個來源同一格,誰也不知道數字是誰量的)。
    reg = reg or {}
    return {
        # ★ 2026-09-13(第 132 項)三格統一問形狀。在這之前只有 aib_release 問,
        #   而這兩格是字面判準 ⇒ `zsh -c '... && node tools/test.js'` 這種**父殼**
        #   會跟它底下真的在跑的那一支一起被算進來,一支真跑報成 2。
        #   19:01 的資源通知就是這樣問「要不要停」,照它停會砍掉別席正在跑的發版測試。
        "ez2_ai_test_js": running_script(
            rows, "test.js", exclude_ancestors_of=skip_from,
        ),
        "ez2_ai_pre_push": running_script(
            rows, "pre-push", exclude_ancestors_of=skip_from,
        ),
        # ★ 2026-09-14(T1-1)這一格改讀**登記**:kind=release 的活登記(發版拿租約 owner=release@…)。
        #   歷史:席丙 接單源前是 ps 字面(自己的祖先 zsh 提到檔名就算 1;接上排整條祖先鏈之後同一格 1→0,
        #   那個差是他的雜訊底,不是真發版);09-07 缺陷二十改問形狀
        #   (`sed -n … tools/aib-release.sh` 不算);09-14 五次假陽(治具的 sleep 腳本叫 aib-release.sh、
        #   Firefox default-release、呼叫殼)後改登記。`running_script` 仍在,外倉兩格用。
        "aib_release": list(reg.get("release", [])),
        # 本倉其餘登記的批(witness / tests / scan / other),通知會逐 kind 印出來。
        "registered": [p for k, ps in reg.items() if k != "release" for p in ps],
        "browser_runner": [
            p for p in rows
            if p.pid in {m.ppid for m in chrome_mains(rows)}
            and p.pid not in ancestor_pids(rows, skip_from)
        ],
    }


# ---------------------------------------------------------------------------
# A7 macOS pgrep 沒有 -c。用它當放行條件會 rc=2,`|| echo 0` 把失效值變成通過值。
# 這支臂必須看到非 0 結束碼;校準「一定存在的目標」用 launchd,不用 node。
# ---------------------------------------------------------------------------
def arm_pgrep_c() -> Arm:
    try:
        proc = _run(["pgrep", "-c", "launchd"], timeout=1.0)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Arm("A7", False, "pgrep 本身跑不起來:%s" % e)
    # Darwin: 應為 usage error,rc != 0。若 rc=0,這台的 pgrep 語意變了,尺要重寫。
    if sys.platform == "darwin":
        if proc.returncode == 0:
            return Arm(
                "A7",
                False,
                "macOS pgrep -c 竟然 rc=0(輸出=%r);失效值不能再當 0 用" % (proc.stdout.strip()[:40],),
            )
        return Arm("A7", True, "pgrep -c 在 Darwin 非 0(rc=%s),沒被當成通過" % proc.returncode)
    # 非 Darwin: -c 可能合法。臂記「本平台不適用」,不算紅。
    return Arm("A7", True, "非 Darwin,pgrep -c 不列入失效形(rc=%s)" % proc.returncode)


def arm_known_alive(rows: list[Proc], skip_from: int | None = None) -> Arm:
    """B 型正校準必須走與目標同一條管線(含排鏈)。
    兩個洞:①launchd 在祖先鏈上,排鏈後恆 0;②不排鏈的正校準=1 跑在另一把尺上,
    那個 1 什麼都沒證明。路徑分隔符:指令列幾乎都有,不專屬任一祖先鏈。"""
    start = os.getpid() if skip_from is None else skip_from
    hits = count_cmd_contains(rows, POS_CALIB_ARGS, exclude_ancestors_of=start)
    if not hits:
        return Arm("A7b", False, "排鏈後路徑分隔符命中 0——尺不會叫")
    return Arm("A7b", True, "B 型正校準用 %r 排鏈後 %d(不用 launchd)" % (
        POS_CALIB_ARGS, len(hits)))


def arm_self_match(rows: list[Proc]) -> Arm:
    hits = count_cmd_contains(rows, SELF_NEEDLE)
    if hits:
        return Arm(
            "A6",
            False,
            "針 %s 出現在 pid %s 的 argv——計數器數到自己" % (
                SELF_NEEDLE, ",".join(str(p.pid) for p in hits[:5])),
        )
    return Arm("A6", True, "針不在祖先鏈外的 argv(排整條鏈,不是只排 $$)")


def arm_ancestor_depth(rows: list[Proc], start_pid: int | None = None) -> Arm:
    start = os.getpid() if start_pid is None else start_pid
    if start not in {p.pid for p in rows}:
        return Arm("A6b", True, "本快照沒有量測行程自己(治具),不查鏈長")
    n = len(ancestor_pids(rows, start))
    if n < 3:
        return Arm("A6b", False, "祖先鏈只有 %d 層,只排 $$ 與 $PPID 會漏" % n)
    return Arm("A6b", True, "祖先鏈 %d 層" % n)


# ---------------------------------------------------------------------------
# A1 裸 tmux ls 只讀 $TMUX 那一個 socket。要逐檔連連看,檔在 ≠ 艙活。
# ---------------------------------------------------------------------------
def tmux_socket_names(dir_listing: Iterable[str] | None = None) -> list[str]:
    names: list[str] = []
    if dir_listing is None:
        uid = os.getuid()
        dirs = [
            "/private/tmp/tmux-%d" % uid,
            "/tmp/tmux-%d" % uid,
        ]
        seen = set()
        for d in dirs:
            try:
                listing = os.listdir(d)
            except OSError:
                continue
            for name in listing:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
        return names
    return list(dir_listing)


def probe_tmux_socket(
    name: str,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    timeout: float = 0.4,
) -> bool:
    argv = ["tmux", "-L", name, "list-sessions"]
    try:
        if runner is None:
            proc = _run(argv, timeout=timeout)
        else:
            proc = runner(argv)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def measure_sockets(
    names: list[str] | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[Cell, Cell, list[str]]:
    all_names = tmux_socket_names() if names is None else names
    live = [n for n in all_names if probe_tmux_socket(n, runner=runner)]
    files_cell = _cell_ok("socket_files", len(all_names), str(len(all_names)))
    if all_names and not live:
        return (
            files_cell,
            _cell_miss("live_sockets", "socket 檔在但一個都連不上"),
            live,
        )
    return (
        files_cell,
        _cell_ok("live_sockets", len(live), str(len(live)), raw=",".join(live)),
        live,
    )


def arm_tmux_current_vs_all(live: list[str]) -> Arm:
    tmux_env = os.environ.get("TMUX") or ""
    current = ""
    if tmux_env:
        # TMUX=/private/tmp/tmux-501/aihub-...,pid,0
        sock_path = tmux_env.split(",", 1)[0]
        current = os.path.basename(sock_path)
    if current and live and current not in live:
        return Arm("A1", False, "$TMUX 的 %s 不在活 socket 清單 %s" % (current, live[:6]))
    if current and len(live) > 1:
        return Arm(
            "A1",
            True,
            "裸 tmux ls 只會看到 %s;真活 socket=%d。尺用逐個連,沒用裸 ls" % (
                current, len(live)),
        )
    return Arm("A1", True, "活 socket %d;現在 TMUX=%s" % (len(live), current or "UNSET"))


# ---------------------------------------------------------------------------
# A2/A3 活席:pane_dead,不是 pane_current_command。不 capture-pane 濾思考動畫。
# 外殼 cockpit/war-room/lobby 與 watch 不算席。
# ---------------------------------------------------------------------------
_NOT_SEAT_NAMES = ("watch", "cockpit", "war-room", "lobby")


def parse_window_lines(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        rows.append({
            "session": parts[0],
            "window_id": parts[1],
            "window_name": parts[2],
            "pane_dead": parts[3],
            "pane_pid": parts[4],
        })
    return rows


def is_inner_hub_socket(name: str) -> bool:
    """活席只數內艙 aihub-* 。default 上的 cockpit/rails/回報上色是外殼,不是席。"""
    return name.startswith("aihub-")


def is_live_seat_row(row: dict[str, str]) -> bool:
    name = row.get("window_name") or ""
    if name.lower() in _NOT_SEAT_NAMES:
        return False
    if row.get("pane_dead") != "0":
        return False
    return True


def measure_live_seats(
    live_sockets: list[str],
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> Cell:
    fmt = "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_dead}\t#{pane_pid}"
    live_n = 0
    who: list[str] = []
    why_bits = []
    for sock in live_sockets:
        if not is_inner_hub_socket(sock):
            continue
        argv = ["tmux", "-L", sock, "list-windows", "-a", "-F", fmt]
        try:
            proc = runner(argv) if runner else _run(argv, timeout=0.6)
        except (OSError, subprocess.TimeoutExpired) as e:
            why_bits.append("%s timeout/err %s" % (sock, e))
            continue
        if proc.returncode != 0:
            why_bits.append("%s rc=%s" % (sock, proc.returncode))
            continue
        for row in parse_window_lines(proc.stdout):
            if is_live_seat_row(row):
                live_n += 1
                who.append("%s %s %s pid=%s" % (
                    sock, row.get("window_id"), row.get("window_name"),
                    row.get("pane_pid")))
    if live_sockets and why_bits and live_n == 0:
        return _cell_miss("live_seats", ";".join(why_bits)[:200])
    return _cell_ok("live_seats", live_n, str(live_n), hits=who)


def pid_is_that_seat(proc: Proc | None, session_uuid: str) -> bool:
    """pid 在不在不夠。回收後同一號會跑 familycircled,命令列沒有 session_uuid。"""
    if proc is None or not session_uuid:
        return False
    return session_uuid in proc.command


def same_count_same_event(who_a: list[str], who_b: list[str]) -> bool:
    """兩個相同數字不是同一件事。v4 §三-4 逐行認出每一筆不是除錯欄,
    是判斷兩個相同讀數是不是同一件事的唯一欄位。不同會被追、相同不會有人查。"""
    return list(who_a) == list(who_b)


def census_seat_jsons(
    records: list[dict[str, Any]],
    rows: list[Proc],
) -> tuple[list[str], list[str], list[str]]:
    """席位檔普查。naive=只問 pid 在不在;strict=那個 pid 現在跑的是那個席。
    兩個相同的數字比兩個不同的更危險——不同會被追、相同不會有人查為什麼一樣。
    who 列是判斷兩個相同讀數是不是同一件事的唯一欄位;合併兩個 19 會把
    pid 回收那條必紅臂(假活)刪掉。席甲 v6 收回「兩個 19 都對」。
    席戊 反例:ps|grep shell-snapshots=4 對 pgrep -f=1,以為 pgrep 少看;
    列舉後 3 筆是自我污染,pgrep 正好是那 2 列真的。只有兩個數字時任何解釋
    都是猜,列舉之後就不必猜。"""
    by_pid = {p.pid: p for p in rows}
    naive_who: list[str] = []
    strict_who: list[str] = []
    fake_who: list[str] = []
    for rec in records:
        pid = rec.get("pid")
        uuid = rec.get("session_uuid") or ""
        tail = uuid[-5:] if len(uuid) >= 5 else ""
        win = rec.get("window_id") or ""
        if win and not str(win).startswith("@"):
            win = "@" + str(win)
        label = "%s pid=%s role=%s %s" % (
            rec.get("path", "?"), pid, rec.get("role"), win)
        proc = by_pid.get(int(pid)) if pid is not None else None
        if proc is None:
            continue
        naive_who.append(label + " cmd=%s" % proc.command[:60])
        if pid_is_that_seat(proc, uuid):
            strict_who.append("%s uuid=%s" % (label, tail))
        else:
            fake_who.append("%s 假活 cmd=%s" % (label, proc.command[:60]))
    return naive_who, strict_who, fake_who


def load_seat_records(paths: list[str] | None = None) -> list[dict[str, Any]]:
    if paths is None:
        paths = glob.glob(os.path.expanduser(
            "~/.ez2-aib/instances/*/seats/w*.json"))
    out = []
    for path in paths:
        try:
            d = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict):
            continue
        rec = dict(d)
        rec["path"] = path
        out.append(rec)
    return out


def pid_cmd_matches(pid: int, expect_substr: str, rows: list[Proc]) -> bool:
    """A5:pid 在不在不夠,要問那個 pid 現在跑的是不是那個席。"""
    for p in rows:
        if p.pid == pid:
            return expect_substr in p.command
    return False


# ---------------------------------------------------------------------------
# CPU 晶粒溫度:IOHID UsagePage 0xFF00 Usage 5,PMU tdie*。免 sudo。
# 前一班寫「免權限讀不到」已過時;量不到才準寫量不到,不得用電池冒充。
# ---------------------------------------------------------------------------
def read_cpu_die_temps() -> Cell:
    if sys.platform != "darwin":
        return _cell_miss("cpu_die_c", "非 Darwin,沒有 IOHID 溫度頁")
    try:
        temps = _iokit_tdie_temps()
    except Exception as e:  # 探測失敗要具名,不得回 0
        return _cell_miss("cpu_die_c", "IOHID 例外:%s" % e)
    if not temps:
        return _cell_miss("cpu_die_c", "IOHID 有服務但沒有合格 PMU tdie 讀數")
    mx = max(temps)
    avg = sum(temps) / len(temps)
    return _cell_ok(
        "cpu_die_c",
        round(mx, 2),
        "%.1f" % mx,
        raw="max=%.2f avg=%.2f n=%d" % (mx, avg, len(temps)),
    )


def _iokit_tdie_temps() -> list[float]:
    from ctypes import (
        CDLL,
        POINTER,
        Structure,
        byref,
        c_bool,
        c_char_p,
        c_double,
        c_int,
        c_int32,
        c_long,
        c_uint32,
        c_ulong,
        c_void_p,
        create_string_buffer,
        util,
    )

    cf_path = util.find_library("CoreFoundation")
    if not cf_path:
        raise RuntimeError("找不到 CoreFoundation")
    cf = CDLL(cf_path)
    iokit = CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

    class CFDictKeyCB(Structure):
        _fields_ = [
            ("version", c_long), ("retain", c_void_p), ("release", c_void_p),
            ("copyDescription", c_void_p), ("equal", c_void_p), ("hash", c_void_p),
        ]

    class CFDictValCB(Structure):
        _fields_ = [
            ("version", c_long), ("retain", c_void_p), ("release", c_void_p),
            ("copyDescription", c_void_p), ("equal", c_void_p),
        ]

    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
    cf.CFNumberCreate.restype = c_void_p
    cf.CFNumberCreate.argtypes = [c_void_p, c_int, c_void_p]
    cf.CFDictionaryCreate.restype = c_void_p
    cf.CFDictionaryCreate.argtypes = [
        c_void_p, POINTER(c_void_p), POINTER(c_void_p), c_long, c_void_p, c_void_p,
    ]
    cf.CFArrayGetCount.restype = c_long
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
    cf.CFStringGetCString.restype = c_bool
    cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
    cf.CFRelease.argtypes = [c_void_p]

    iokit.IOHIDEventSystemClientCreate.restype = c_void_p
    iokit.IOHIDEventSystemClientCreate.argtypes = [c_void_p]
    iokit.IOHIDEventSystemClientSetMatching.argtypes = [c_void_p, c_void_p]
    iokit.IOHIDEventSystemClientCopyServices.restype = c_void_p
    iokit.IOHIDEventSystemClientCopyServices.argtypes = [c_void_p]
    iokit.IOHIDServiceClientCopyProperty.restype = c_void_p
    iokit.IOHIDServiceClientCopyProperty.argtypes = [c_void_p, c_void_p]
    iokit.IOHIDServiceClientCopyEvent.restype = c_void_p
    iokit.IOHIDServiceClientCopyEvent.argtypes = [c_void_p, c_int32, c_int32, c_int32]
    iokit.IOHIDEventGetFloatValue.restype = c_double
    iokit.IOHIDEventGetFloatValue.argtypes = [c_void_p, c_int32]

    kCFStringEncodingUTF8 = 0x08000100
    kCFNumberIntType = 9
    kIOHIDEventTypeTemperature = 15
    temp_field = kIOHIDEventTypeTemperature << 16

    key_cbs = CFDictKeyCB.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
    val_cbs = CFDictValCB.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

    def cfstr(s: str) -> Any:
        return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)

    def cfnum(n: int) -> Any:
        v = c_int(n)
        return cf.CFNumberCreate(None, kCFNumberIntType, byref(v))

    keys = (c_void_p * 2)(cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage"))
    vals = (c_void_p * 2)(cfnum(0xFF00), cfnum(5))
    matching = cf.CFDictionaryCreate(
        None, keys, vals, 2, byref(key_cbs), byref(val_cbs),
    )
    if not matching:
        raise RuntimeError("CFDictionaryCreate 回 NULL")
    client = iokit.IOHIDEventSystemClientCreate(None)
    if not client:
        raise RuntimeError("IOHIDEventSystemClientCreate 回 NULL")
    iokit.IOHIDEventSystemClientSetMatching(client, matching)
    services = iokit.IOHIDEventSystemClientCopyServices(client)
    if not services:
        raise RuntimeError("CopyServices 回 NULL")
    n = cf.CFArrayGetCount(services)
    temps: list[float] = []
    seen_names: set[str] = set()
    for i in range(n):
        svc = cf.CFArrayGetValueAtIndex(services, i)
        prod = iokit.IOHIDServiceClientCopyProperty(svc, cfstr("Product"))
        buf = create_string_buffer(256)
        name = "unknown"
        if prod and cf.CFStringGetCString(prod, buf, 256, kCFStringEncodingUTF8):
            name = buf.value.decode()
        ev = iokit.IOHIDServiceClientCopyEvent(svc, kIOHIDEventTypeTemperature, 0, 0)
        if ev:
            t = float(iokit.IOHIDEventGetFloatValue(ev, temp_field))
            if name.startswith("PMU tdie") and 0.0 < t < 150.0:
                # 同一顆感測器 HID 會列兩次;同名只留一次。
                if name not in seen_names:
                    seen_names.add(name)
                    temps.append(t)
            cf.CFRelease(ev)
        if prod:
            cf.CFRelease(prod)
    cf.CFRelease(services)
    return temps


# A11 du 量配置區塊、copytree 複製位元組。熱路徑不做,避免尺自己變熱源。
def arm_skip_du() -> Arm:
    return Arm(
        "A11",
        True,
        "熱路徑不做 du;那一格成本會讓尺變成熱源。要量位元組另開慢臂",
    )


def _verdict(
    cells: dict[str, Cell],
    arms: list[Arm],
    undercount_calibrated: bool = False,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    failed_arms = [a for a in arms if not a.ok]
    if failed_arms:
        reasons.append("ruler-broken:" + ",".join(a.id for a in failed_arms))
        return "ruler-broken", reasons

    load1 = cells.get("load1")
    if not load1 or not load1.measured:
        reasons.append("uptime 的 load 剖不出來")
        return "no-go", reasons
    if float(load1.value) >= THRESHOLDS["load1_nogo"]:
        reasons.append("uptime 第一個 load=%.2f >= %.0f" % (
            float(load1.value), THRESHOLDS["load1_nogo"]))

    src = cells.get("ps")
    if src is None or not src.measured or not src.source_n:
        reasons.append("ruler-broken:來源量不到或母體=0(空輸入被判成正常零筆)")
        return "ruler-broken", reasons

    kt = cells.get("kernel_task")
    if not kt or not kt.measured:
        reasons.append("kernel_task 量不到")
        return "no-go", reasons
    if float(kt.value) >= THRESHOLDS["kernel_task_nogo"]:
        reasons.append("kernel_task=%.1f >= %.0f" % (float(kt.value), THRESHOLDS["kernel_task_nogo"]))

    for key in ("chrome_mains", "ez2_ai_test_js", "ez2_ai_pre_push", "aib_release"):
        c = cells.get(key)
        if c is None or not c.measured:
            continue
        if not c.triple_complete():
            reasons.append("ruler-broken:%s 缺三數" % key)
            return "ruler-broken", reasons
        if int(c.pos_cal) <= 0:
            reasons.append("ruler-broken:%s 正對照=0(尺沒死這格是盲的)" % key)
            return "ruler-broken", reasons
        if int(c.neg_cal) != 0:
            reasons.append("ruler-broken:%s 負對照=%s 必須為 0且不得拿來減" % (
                key, c.neg_cal))
            return "ruler-broken", reasons

    mains = cells.get("chrome_mains")
    if mains and mains.measured and int(mains.value) >= 1:
        reasons.append(
            "瀏覽器窗口佔用中 comm目標=%s 正=%s 負=%s" % (
                mains.value, mains.pos_cal, mains.neg_cal)
        )

    if reasons:
        return "no-go", reasons
    if not undercount_calibrated:
        return "no-go", [
            "計數 0 未經低估對照,不得當再起一輪;起重活走窗口制(席甲|席乙 給)",
        ]
    return "go", ["uptime 第一個 load、kernel_task、瀏覽器窗口(comm)都在門檻內"]


def snapshot(
    *,
    uptime_raw: str | None = None,
    top_out: str | None = None,
    ps_text: str | None = None,
    socket_names: list[str] | None = None,
    tmux_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    read_temp: bool = True,
    probe_tmux: bool = True,
    seat_records: list[dict[str, Any]] | None = None,
    undercount_calibrated: bool | None = None,
) -> Snapshot:
    t0 = time.perf_counter()
    observed_at = time.strftime("%Y-%m-%d %H:%M:%S")
    cells: dict[str, Cell] = {}
    arms: list[Arm] = []
    undercount_ok = (
        UNDERCOUNT_CALIBRATED if undercount_calibrated is None
        else undercount_calibrated
    )

    load1, load15, raw_uptime = measure_uptime(uptime_raw)
    cells["load1"] = load1
    cells["load15"] = load15
    # A8:15 分欄印出來但不當放行條件。
    arms.append(Arm(
        "A8",
        True,
        "負載貼 uptime 整行;閘用 load averages 後第一個數,後兩個印但不當閘",
    ))

    cells["kernel_task"] = measure_kernel_task(top_out)
    td = kernel_timeout_display()
    fd = kernel_fail_display()
    a10_ok = (
        LEGAL_ZERO_DISPLAY != UNMEASURED_DISPLAY
        and td != UNMEASURED_DISPLAY
        and td != LEGAL_ZERO_DISPLAY
        and fd != UNMEASURED_DISPLAY
        and fd != td
        and fd != LEGAL_ZERO_DISPLAY
    )
    arms.append(Arm(
        "A10a",
        a10_ok,
        "kernel_task 三種量不到字樣互異:剖不出 / 逾時 / top失敗,皆≠合法零",
    ))
    arms.append(Arm(
        "A10b",
        "上次" not in td and "上次" not in LEGAL_ZERO_DISPLAY,
        "逾時字樣本身不含上次;上次是並列的第二段,不冒充現值",
    ))
    arms.append(Arm(
        "A10c",
        True,
        "逾時並列上次與其年齡(無上次則只印逾時);現值與上次字樣不同",
    ))
    arms.append(arm_source_end_failure())

    comms: list[str] = []
    if ps_text is None:
        try:
            comms, src_n = _ps_comm_only()
            rows = _ps_args_procs()
            if not rows:
                ps_cell = _cell_miss("ps", "ps args 空")
            else:
                ps_cell = _cell_ok(
                    "ps", len(rows), "%d 母體=%d" % (len(rows), src_n),
                    source_n=src_n,
                )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
            ps_cell = _cell_miss("ps", "ps 失敗:%s" % e)
            rows = []
    else:
        ps_cell, rows = measure_ps(ps_text)
        comms = [p.comm for p in rows]
    cells["ps"] = ps_cell
    if ps_cell.measured:
        arms.append(arm_self_match(rows))
        arms.append(arm_ancestor_depth(rows))
        skip_from = os.getpid()
        arms.append(arm_known_alive(rows, skip_from=skip_from))
        chrome_names = chrome_comm_names(comms)
        mains = chrome_mains(rows)
        profiles = chrome_profiles(mains)
        runners = suite_runners(rows)
        pos_comm_n = comm_needle_hits(comms, POS_CALIB_COMM)
        neg_comm_n = comm_needle_hits(comms, NEG_CALIB_NEEDLE)
        chain = ancestor_pids(rows, skip_from)
        pos_args = count_cmd_contains(
            rows, POS_CALIB_ARGS, exclude_ancestors_of=skip_from,
        )
        neg_args = count_cmd_contains(
            rows, NEG_CALIB_NEEDLE, exclude_ancestors_of=skip_from,
        )
        # A 型目標用 comm 單欄命中;who 印 comm 字串,不拿 args 合表冒充 comm。
        mains_a = [
            Proc(pid=0, ppid=0, command="", comm=c) for c in chrome_names
        ]
        pos_comm = [Proc(pid=0, ppid=0, command="", comm=c) for c in pos_comm_n]
        neg_comm = [Proc(pid=0, ppid=0, command="", comm=c) for c in neg_comm_n]

        def control_cell(key: str, target_hits: list[Proc], method: str) -> Cell:
            if method == "comm":
                t, p, n = count_controls(target_hits, pos_comm, neg_comm)
                clen = None
            else:
                t, p, n = count_controls(target_hits, pos_args, neg_args)
                clen = len(chain)
            who = describe_hits(target_hits, rows)
            extra = " 鏈=%d" % clen if clen is not None else ""
            srcn = ps_cell.source_n
            display = "%d 目標=%d 正=%d 負=%d 母體=%s%s" % (
                t, t, p, n, srcn, extra)
            return _cell_ok(
                key, t, display,
                raw=("; ".join(who) if who else None),
                hits=who, target=t, pos_cal=p, neg_cal=n,
                chain_len=clen, source_n=srcn,
            )

        cells["chrome_mains"] = control_cell("chrome_mains", mains_a, "comm")
        cells["chrome_profiles"] = _cell_ok(
            "chrome_profiles", len(profiles), str(len(profiles)),
            raw=",".join(profiles[:8]) or None,
        )
        cells["ez2_ai_test_js"] = control_cell(
            "ez2_ai_test_js", runners["ez2_ai_test_js"], "args-chain",
        )
        cells["ez2_ai_pre_push"] = control_cell(
            "ez2_ai_pre_push", runners["ez2_ai_pre_push"], "args-chain",
        )
        cells["aib_release"] = control_cell(
            "aib_release", runners["aib_release"], "args-chain",
        )
        cells["browser_runners"] = control_cell(
            "browser_runners", runners["browser_runner"], "args-chain",
        )
        cells["ancestor_chain"] = _cell_ok(
            "ancestor_chain", len(chain), str(len(chain)),
            raw=",".join(str(x) for x in sorted(chain)),
        )
        chrome_c = cells["chrome_mains"]
        if int(chrome_c.value) == 0 and not undercount_ok:
            chrome_c.display = chrome_c.display.replace(
                "0 目標=", UNDERCOUNT_ZERO_DISPLAY + " 目標=", 1)
            chrome_c.why = (
                "四個對照只抓高估;再量條件=下次真有 Chrome 在跑,"
                "A 法與 B 法各列舉一次並對數"
            )
        _w, _b, bait_ok = in_memory_bait(rows, lambda r: len(chrome_mains(r)))
        if (
            chrome_c.triple_complete()
            and int(chrome_c.pos_cal) > 0
            and int(chrome_c.neg_cal) == 0
            and bait_ok
        ):
            arms.append(Arm(
                "A13",
                True,
                "五對照:chrome comm 目標=%s 正=%s 負=%s 母體=%s 鏈=%d 誘餌免疫" % (
                    chrome_c.target, chrome_c.pos_cal, chrome_c.neg_cal,
                    chrome_c.source_n, len(chain)),
            ))
        else:
            arms.append(Arm(
                "A13",
                False,
                "四對照缺:正=%s 負=%s 誘餌免疫=%s(負對照不得單獨當免疫)" % (
                    chrome_c.pos_cal, chrome_c.neg_cal, bait_ok),
            ))
        if undercount_ok:
            arms.append(Arm("A14", True, "低估對照已量(測試縫;正本 UNDERCOUNT_CALIBRATED=False)"))
        else:
            arms.append(Arm(
                "A14",
                True,
                "低估對照未量;四個對照只抓高估。再量條件=下次真有 Chrome 在跑時 A/B 各列舉一次。"
                "在那之前 0 不得當再起一輪",
            ))
        arms.append(Arm(
            "A9",
            True,
            "A 型 Chrome=comm 單欄(本呼叫者誘餌過關,非跨呼叫者唯一正解);"
            "B 型 runner=排整條祖先鏈並印是誰",
        ))
        recs = seat_records
        if recs is None:
            recs = [] if ps_text is not None else load_seat_records()
        naive_w, strict_w, fake_w = census_seat_jsons(recs, rows)
        naive_why = (
            "假活=%d;兩個相同數字比兩個不同的更危險,要對 who 列" % len(fake_w)
            if fake_w else
            "naive=strict 仍要對 who 列才知道是不是同一件事"
        )
        cells["seat_pid_naive"] = _cell_ok(
            "seat_pid_naive", len(naive_w),
            "%d 只問pid在" % len(naive_w),
            hits=naive_w, why=naive_why,
        )
        cells["seat_pid_strict"] = _cell_ok(
            "seat_pid_strict", len(strict_w),
            "%d 是那個席 naive=%d 假活=%d" % (
                len(strict_w), len(naive_w), len(fake_w)),
            hits=strict_w + fake_w,
            why="who 列是判斷兩個相同讀數是不是同一件事的唯一欄位",
        )
        if fake_w:
            arms.append(Arm(
                "A5",
                True,
                "pid 在≠那個席:naive=%d strict=%d 假活=%d。%s。"
                "兩個相同數字比兩個不同的更危險;who 列是唯一欄位,合併會刪掉這條必紅臂"
                % (len(naive_w), len(strict_w), len(fake_w),
                   "; ".join(fake_w[:3])),
            ))
        else:
            arms.append(Arm(
                "A5",
                True,
                "naive=strict=%d;兩個相同數字仍要對 who 列才知道是不是同一件事"
                % len(strict_w),
            ))
    else:
        arms.append(Arm("A6", False, ps_cell.why or "ps 量不到,自 match 臂無法跑"))
        arms.append(Arm("A6b", False, "ps 量不到,祖先鏈無法跑"))
        arms.append(Arm("A7b", False, "沒有 ps 列,launchd 校準無法跑"))
        cells["chrome_mains"] = _cell_miss("chrome_mains", "ps 量不到")
        cells["chrome_profiles"] = _cell_miss("chrome_profiles", "ps 量不到")
        cells["ez2_ai_test_js"] = _cell_miss("ez2_ai_test_js", "ps 量不到")
        cells["ez2_ai_pre_push"] = _cell_miss("ez2_ai_pre_push", "ps 量不到")
        cells["aib_release"] = _cell_miss("aib_release", "ps 量不到")
        cells["browser_runners"] = _cell_miss("browser_runners", "ps 量不到")
        cells["ancestor_chain"] = _cell_miss("ancestor_chain", "ps 量不到")
        cells["seat_pid_naive"] = _cell_miss("seat_pid_naive", "ps 量不到")
        cells["seat_pid_strict"] = _cell_miss("seat_pid_strict", "ps 量不到")

    arms.append(arm_pgrep_c())
    arms.append(arm_skip_du())

    if probe_tmux:
        file_cell, live_cell, live = measure_sockets(socket_names, runner=tmux_runner)
        cells["socket_files"] = file_cell
        cells["live_sockets"] = live_cell
        arms.append(arm_tmux_current_vs_all(live))
        cells["live_seats"] = measure_live_seats(live, runner=tmux_runner)
        arms.append(Arm(
            "A2",
            True,
            "活席用 pane_dead=0,窗名排除 watch/cockpit/war-room/lobby",
        ))
        arms.append(Arm("A4", True, "活席不讀席位登記(登記不會過期)"))
    else:
        cells["socket_files"] = _cell_miss("socket_files", "probe_tmux=0")
        cells["live_sockets"] = _cell_miss("live_sockets", "probe_tmux=0")
        cells["live_seats"] = _cell_miss("live_seats", "probe_tmux=0")
        arms.append(Arm("A1", True, "本快照跳過 tmux(測試注入)"))
        arms.append(Arm("A2", True, "本快照跳過 tmux(測試注入)"))
        arms.append(Arm("A4", True, "本快照跳過席位登記"))

    if read_temp:
        cells["cpu_die_c"] = read_cpu_die_temps()
    else:
        cells["cpu_die_c"] = _cell_miss("cpu_die_c", "read_temp=0")

    wall_ms = (time.perf_counter() - t0) * 1000.0
    if wall_ms > THRESHOLDS["wall_ms_budget"]:
        arms.append(Arm(
            "A12",
            False,
            "尺耗時 %.0fms > %.0fms,自己變成熱源" % (
                wall_ms, THRESHOLDS["wall_ms_budget"]),
        ))
    else:
        arms.append(Arm("A12", True, "尺耗時 %.0fms" % wall_ms))

    verdict, reasons = _verdict(cells, arms, undercount_ok)
    return Snapshot(
        verdict=verdict,
        reasons=reasons,
        cells=cells,
        arms=arms,
        wall_ms=round(wall_ms, 1),
        raw_uptime=raw_uptime,
        observed_at=observed_at,
    )


def cell_tone(key: str, cell: Cell) -> str:
    """格子的狀態色。量不到一律 broken,不得走 go。"""
    if not cell.measured:
        return "broken"
    if key == "load1":
        try:
            return "nogo" if float(cell.value) >= THRESHOLDS["load1_nogo"] else "go"
        except (TypeError, ValueError):
            return "broken"
    if key == "load15":
        return "info"
    if key == "kernel_task":
        try:
            return "nogo" if float(cell.value) >= THRESHOLDS["kernel_task_nogo"] else "go"
        except (TypeError, ValueError):
            return "broken"
    if key == "chrome_mains":
        try:
            n = int(cell.value)
        except (TypeError, ValueError):
            return "broken"
        if n >= 1:
            return "nogo"
        if cell.why and "未經低估" in cell.why:
            return "broken"
        if cell.display and UNDERCOUNT_ZERO_DISPLAY in cell.display:
            return "broken"
        return "go"
    if key in ("ez2_ai_test_js", "ez2_ai_pre_push",
               "aib_release", "browser_runners"):
        try:
            return "nogo" if int(cell.value) >= 1 else "go"
        except (TypeError, ValueError):
            return "broken"
    if key == "live_sockets":
        try:
            return "go" if int(cell.value) >= 1 else "info"
        except (TypeError, ValueError):
            return "broken"
    if key == "cpu_die_c":
        return "info"
    if key == "seat_pid_naive":
        # 只問 pid 在不在。有假活時紅——數字碰巧等於真值時更危險,不能畫成綠。
        if cell.why and "假活" in cell.why:
            return "nogo"
        return "info"
    if key == "seat_pid_strict":
        return "go"
    return "info"


def verdict_tone(verdict: str) -> str:
    if verdict == "go":
        return "go"
    if verdict == "no-go":
        return "nogo"
    return "broken"


def paint(text: str, tone: str, enable: bool) -> str:
    if not enable:
        return text
    if tone not in ANSI or tone == "reset":
        return text
    return "%s%s%s" % (ANSI[tone], text, ANSI["reset"])


def want_color(ns_color: bool | None, stream) -> bool:
    """--no-color 永遠關;--color 永遠開;否則 TTY 且沒有 NO_COLOR 才開。"""
    if ns_color is False:
        return False
    if ns_color is True:
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def format_human(snap: Snapshot, color: bool = False) -> str:
    vt = verdict_tone(snap.verdict)
    lines = [
        "observed_at: %s" % (snap.observed_at or UNMEASURED_DISPLAY),
        "verdict: %s" % paint(snap.verdict, vt, color),
        "reasons: %s" % paint("; ".join(snap.reasons), vt, color),
        "raw_uptime: %s" % (snap.raw_uptime or UNMEASURED_DISPLAY),
        "wall_ms: %.1f (budget %.0f)" % (snap.wall_ms, THRESHOLDS["wall_ms_budget"]),
        "thresholds: uptime 第一個 load>=%.0f kernel_task>=%.0f" % (
            THRESHOLDS["load1_nogo"], THRESHOLDS["kernel_task_nogo"]),
        "--- cells ---",
    ]
    for key in (
        "load1", "load15", "kernel_task", "cpu_die_c",
        "ps",
        "chrome_mains", "chrome_profiles", "browser_runners",
        "ez2_ai_test_js", "ez2_ai_pre_push", "aib_release",
        "ancestor_chain",
        "socket_files", "live_sockets", "live_seats",
        "seat_pid_naive", "seat_pid_strict",
    ):
        c = snap.cells.get(key)
        if not c:
            continue
        extra = ""
        if c.raw:
            extra = " raw=%s" % c.raw
        if c.why:
            extra += " why=%s" % c.why
        tone = cell_tone(key, c)
        shown = paint("%s%s" % (c.display, extra), tone, color)
        lines.append("  %s: %s" % (key, shown))
        for h in c.hits:
            lines.append("    who %s" % h)
    lines.append("--- arms ---")
    for a in snap.arms:
        mark = "ok" if a.ok else "RED"
        tone = "go" if a.ok else "broken"
        lines.append("  %s %s %s" % (
            a.id, paint(mark, tone, color), a.detail))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="校準過的「能不能起重活」尺")
    p.add_argument("--json", action="store_true")
    p.add_argument("--color", action="store_true", help="強制上色(管線預設不上色)")
    p.add_argument("--no-color", action="store_true", help="強制不上色")
    p.add_argument("--no-tmux", action="store_true", help="跳過活艙探測(艙內測用)")
    p.add_argument("--no-temp", action="store_true")
    p.add_argument(
        "--calibrate", action="store_true",
        help="加印 comm/naive-args 誘餌對照(有 vs 沒有,不衍生真行程)",
    )
    ns = p.parse_args(argv)
    snap = snapshot(probe_tmux=not ns.no_tmux, read_temp=not ns.no_temp)
    if ns.calibrate:
        # 用快照當下的 ps 再做一次記憶體內誘餌,不另起行程。
        _ps_cell, rows = measure_ps()
        if _ps_cell.measured:
            a, b, ok = in_memory_bait(rows, lambda r: len(chrome_mains(r)))
            print("bait comm: without=%d with=%d immune=%s" % (a, b, ok), file=sys.stderr)
            na, nb, nok = in_memory_bait(
                rows, lambda r: len(count_args_naive(r, CHROME_COMM_MARK)),
            )
            print(
                "bait naive-args: without=%d with=%d immune=%s" % (na, nb, nok),
                file=sys.stderr,
            )
    if ns.json:
        doc = snap.as_dict()
        doc["tones"] = {
            "verdict": verdict_tone(snap.verdict),
            "cells": {k: cell_tone(k, c) for k, c in snap.cells.items()},
        }
        sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    else:
        flag = False if ns.no_color else (True if ns.color else None)
        colored = want_color(flag, sys.stdout)
        sys.stdout.write(format_human(snap, color=colored))
    if snap.verdict == "go":
        return 0
    if snap.verdict == "ruler-broken":
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
