#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""不穩定測試帳本 + 窄放行判定(推包管道 P1,docs/DESIGN-ship-merge-queue-1011.md §4 P1;the maintainer 10-11 23:5x 核准、a seat 派)。

為什麼:10-11 晚上一支紅在收尾(暫存夾刪不乾淨)、重跑就綠、跟改動無關的測試,讓整包 50 分鐘白跑。
ship.refuse 早就會重跑、算出「時紅時綠」,但算完還是不推。本檔補一條**很窄**的放行規則。

一支新紅**三條全中**才放行(judge):
  1. 在同一份程式上重跑是綠的(呼叫端 ship.refuse 已算好,傳 rerun_green=True);
  2. 有不穩定紀錄(帳本裡過去 HISTORY_DAYS 天、**別的包**標過時紅時綠 ≥ 1 次),**或**每一個錯誤都是收尾錯誤
     (is_cleanup_error:清暫存夾時資料夾刪不掉;不是字面看到 tearDown/rmtree 就算);
     ★ 10-12 審查:同一包推第二次不准拿自己第一次留下的那筆當紀錄 —— 包用 patch 指紋認(疊到新主線、編號變了也同一個);
  3. 這一包改的檔**沒有**被它讀到(讀檔追蹤 reuse_index 量得到、且反查不到)。量不到 = 當成有讀到。
另有上限:同一支 7 天內已被標過 2 次 ⇒ 這次是第 3 次 ⇒ **不放行**,必須修。

帳本:<狀態根>/state/ship/flaky-ledger.jsonl(狀態根 = EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib),只追加。
每次「重跑綠」都記一筆(放行或不放行都記,why 寫原因),上限與「有紀錄」都數這些筆。
寫不進去不擋判定,但判定照「沒有紀錄」算(保守:少一條放行理由)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HISTORY_DAYS = 30          # 條件 2「有不穩定紀錄」往回看幾天
LIMIT_DAYS = 7             # 上限的窗
LIMIT_PRIOR = 2            # 窗內已有幾次 ⇒ 這次就不放(第 3 次)
# 收尾錯誤 = 最後那行例外是「資料夾刪不掉」,而且呼叫鏈裡有暫存夾清理(10-12 審查:舊版字面看到 tearDown/rmtree 就算,太寬)
#   第二輪審查:例外行要從頭對(OSError: [Errno 39/66] Directory not empty),呼叫鏈要是 tempfile.py / shutil.py 自己的框,
#   測試原始碼那行 `with tempfile.TemporaryDirectory()` 不算。
CLEANUP_EXC_RE = re.compile(r"^OSError: \[Errno (?:39|66)\] Directory not empty\b")
CLEANUP_FRAME_RE = re.compile(r'File ".*(?:tempfile|shutil)\.py", line \d+, in '
                              r'(?:__exit__|cleanup|_rmtree\w*|rmtree|_rmtree_safe_fd\w*|_rmtree_unsafe|onexc|onerror)\b')
CHAINED_RE = re.compile(r"During handling of the above exception|The above exception was the direct cause")
TALLY_RE = re.compile(r"^FAILED \((.*)\)\s*$")
SEP_EQ = re.compile(r"^={20,}$")
SEP_DASH = re.compile(r"^-{20,}$")
SEAT_RE = re.compile(r"(?<![0-9a-f])@([0-9a-f]{5})(?![0-9a-f])")


def state_root():
    return Path(os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def ledger_path():
    return state_root() / "state" / "ship" / "flaky-ledger.jsonl"


def rows(path=None):
    p = Path(path) if path else ledger_path()
    out = []
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("test"):
                    out.append(d)
    except OSError:
        pass
    return out


def record(test, sha, allowed, why, summary="", now=None, path=None, pkg=""):
    """追加一筆。回 True/False(寫不寫得進去);寫不進去由呼叫端決定要不要說。pkg = 包的 patch 指紋。"""
    p = Path(path) if path else ledger_path()
    row = {"ts": int(now if now is not None else time.time()), "test": test, "sha": sha, "pkg": pkg,
           "allowed": bool(allowed), "why": why, "summary": (summary or "")[-400:]}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def marks(test, days, now=None, path=None, exclude_pkg=""):
    """過去 days 天這支被標「時紅時綠」幾次。exclude_pkg 有給 ⇒ 不數那一包自己留的(同一包重推)。"""
    since = (now if now is not None else time.time()) - days * 86400
    return sum(1 for r in rows(path) if r.get("test") == test and r.get("ts", 0) >= since
               and not (exclude_pkg and r.get("pkg") == exclude_pkg))


def _error_blocks(text):
    """unittest 的每一個 ERROR:/FAIL: 區塊(====== 開頭、----- 之後是 traceback,到下一個 ===== 或 ----- 為止)。"""
    blocks, cur, state = [], None, ""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if SEP_EQ.match(s):
            cur, state = None, "head"
            continue
        if state == "head" and (s.startswith("ERROR: ") or s.startswith("FAIL: ")):
            cur = {"kind": s.split(":", 1)[0], "lines": []}
            blocks.append(cur)
            state = "title"
            continue
        if cur is not None and SEP_DASH.match(s):
            if state == "title":
                state = "body"
                continue
            cur, state = None, ""
            continue
        if cur is not None and state == "body":
            cur["lines"].append(ln)
    return blocks


def _tally(text):
    """unittest 結尾那行 FAILED (failures=F, errors=E) ⇒ (F, E);沒有那行 ⇒ None。"""
    for ln in reversed((text or "").splitlines()):
        m = TALLY_RE.match(ln.strip())
        if m:
            parts = [kv.strip() for kv in m.group(1).split(",") if kv.strip()]
            got = dict(kv.split("=", 1) for kv in parts if "=" in kv)
            # 只認這四種;「unexpected successes」之類會讓整支紅、卻沒有錯誤區塊 ⇒ 認不得就不算(第三輪審查)
            if len(got) != len(parts) or set(got) - {"failures", "errors", "skipped", "expected failures"}:
                return None
            try:
                return int(got.get("failures", 0)), int(got.get("errors", 0))
            except ValueError:
                return None
    return None


def is_cleanup_error(text):
    """**每一個**錯誤區塊都是收尾錯誤才算:是 ERROR(不是 FAIL 斷言)、最後那行例外是資料夾刪不掉、
    呼叫鏈經過 tempfile/shutil 的清理框,而且**沒有串接例外**(真錯誤先發生、清理又炸時,最後一行是清理錯誤,
    真錯誤在「During handling of the above exception」上面 —— 第二輪審查實測)。
    還要對得上結尾那行計數:failures=0、errors = 看到的區塊數(輸出被截掉頭時,前面的真錯誤區塊會不見;
    計數對不上就不算)。一個區塊都認不出(不是 unittest 的輸出)⇒ 不算(保守)。"""
    blocks = _error_blocks(text)
    if not blocks:
        return False
    tally = _tally(text)
    if tally is None or tally[0] != 0 or tally[1] != len(blocks):
        return False
    for b in blocks:
        if b["kind"] != "ERROR":
            return False
        if any(CHAINED_RE.search(ln) for ln in b["lines"]):
            return False
        if sum(1 for ln in b["lines"] if ln.startswith("Traceback (most recent call last)")) != 1:
            return False
        body = [ln for ln in b["lines"] if ln.strip()]
        last = next((ln for ln in reversed(body) if not ln.startswith((" ", "\t"))), "")
        if not CLEANUP_EXC_RE.match(last.strip()):
            return False
        if not any(CLEANUP_FRAME_RE.search(ln) for ln in body):
            return False
    return True


def reads_changed(idx, test, changed):
    """這支測試讀過這一包改的任一檔嗎:True / False / None(量不到 ⇒ 呼叫端當 True)。
    idx = reuse_index.build_index(...) 的結果(推前檢查剛跑完,最新一列就是這一輪量到的)。"""
    if idx is None or getattr(idx, "degraded", ""):
        return None
    row = idx.latest.get(test)
    if not row or row.get("measured") is not True or row.get("external"):
        return None
    if test in changed:
        return True
    return any(test in idx.reverse.get(c, ()) for c in changed)


def judge(test, rerun_green, output, reads, now=None, path=None, pkg=""):
    """回 (allowed, why)。why 是給人看的一句(英文代碼,呼叫端翻)。順序就是判準順序。
    pkg = 這一包的 patch 指紋:「有不穩定紀錄」不數這一包自己留的;上限照樣全數(同一包推三次也會停)。"""
    if not rerun_green:
        return False, "rerun-red"
    prior = marks(test, LIMIT_DAYS, now, path)
    if prior >= LIMIT_PRIOR:
        return False, "limit"
    if reads is None:
        return False, "reads-unmeasured"
    if reads:
        return False, "reads-changed"
    if pkg and marks(test, HISTORY_DAYS, now, path, exclude_pkg=pkg) >= 1:   # 認不出是哪一包 ⇒ 不拿紀錄當理由
        return True, "history"
    if is_cleanup_error(output):
        return True, "cleanup"
    return False, "no-evidence"


def package_id(repo, base):
    """這一包(base...HEAD)的 patch 指紋:git patch-id --stable。疊到新主線、編號變了也一樣。取不到 ⇒ ""。"""
    # -U0:不帶上下文行 —— 主線之後改到附近幾行時,上下文會變、指紋跟著變,同一包又被當成別包(第二輪審查實測)
    d = subprocess.run(["git", "-C", str(repo), "diff", "-U0", "%s...HEAD" % base], capture_output=True,
                       stdin=subprocess.DEVNULL)
    if d.returncode != 0 or not d.stdout:
        return ""
    p = subprocess.run(["git", "-C", str(repo), "patch-id", "--stable"], input=d.stdout, capture_output=True)
    return p.stdout.decode("utf-8", "replace").split()[0] if p.returncode == 0 and p.stdout.split() else ""


def changed_files(repo, base):
    """這一包改了哪些檔(base...HEAD,--no-renames 舊路徑也算)。git 失敗或一個都沒有 ⇒ None(量不到,呼叫端不放行)。"""
    p = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--no-renames", "%s...HEAD" % base],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    files = [x for x in p.stdout.splitlines() if x.strip()] if p.returncode == 0 else []
    return set(files) or None


def author_seat(repo, test):
    """最近改這支測試的那一顆提交訊息裡的席碼(@xxxxx)。找不到 ⇒ None。"""
    p = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B", "--", test], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    m = SEAT_RE.search(p.stdout or "") if p.returncode == 0 else None
    return m.group(1) if m else None


def seat_address(code):
    """席碼 → <艙位碼>/<席碼>(最新那份席位檔所在的艙)。找不到 ⇒ None(不猜艙)。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "cockpit"))
    try:
        import seat_address as sa
    except Exception:  # noqa: BLE001
        return None
    best = None
    for f in (state_root() / "instances").glob("*/seats/w*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if sa.code_of(d) != code:
            continue
        key = float(d.get("started_epoch") or 0)
        if best is None or key > best[0]:
            best = (key, str(d.get("cabin_id") or f.parent.parent.name))
    if not best:
        return None
    return "%s/%s" % (best[1].rsplit("-", 1)[-1], code)


def notify(addr, text):
    """aib dm 給作者。回 rc(送不出去不擋推包)。AIB_FLAKY_DM_BIN 給測試換成假的。"""
    cmd = os.environ.get("AIB_FLAKY_DM_BIN") or "aib"
    try:
        return subprocess.run([cmd, "dm", addr, text], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=120).returncode
    except (OSError, subprocess.SubprocessError):
        return -1
