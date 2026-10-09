#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""執行情境繼承閘(2026-08-15,the maintainer 直令「做成閘門 hook」)。

## 它擋什麼

「**執行情境要顯式指定,不能靠繼承**」這條規則已經寫在大綱與交接單裡,
而 2026-08-15 一天之內同一條規則以**五種不同的變數**各發作一次:

| # | 繼承了什麼 | 症狀 |
|---|---|---|
| 1 | `run-shell` 的工作目錄 | 新席開在未信任目錄,卡在信任提示變殭屍 |
| 2 | 測試繼承的 `EZ2_COCKPIT_STATE_DIR` | 測試讀到活艙真實選色,別人手上才紅 |
| 3 | 測試繼承的 `PYTHONPATH` | 本機綠、CI 炸在 import |
| 4 | 測試開窗時繼承的 shell 提示符號 | 本機有畫面所以綠,CI 空白所以紅 |
| 5 | 開席時繼承的 tmux socket | 開進別套指揮艙,下令的人看不到 |

**寫進文件不等於會被遵守。** 五次都是在「知道這條規則」的情況下踩的,
所以它需要一道機器閘門,不是第六次提醒。

## ★ 為什麼只擋新增的行(這個設計決定是量出來的)

實測現有碼的命中數:

    tmux 沒帶 -L            163 處
    run-shell 沒有 cd        51 處
    測試 env 沒釘隔離變數     32 處
    測試開窗沒指定命令        33 處

**共 279 處。** 一次擋下 279 處的閘門不會讓人去修,只會讓人把閘門關掉——
而被關掉的閘門比沒有閘門更糟,因為大家以為它還在守。

所以本閘只看 **staged diff 裡新增的那些行**:既有的違規不擋(它們已經在
跑,要改是另一件事),但**今天新寫的一行違規過不去**。存量與增量分開治。

## 逃生門

    AIB_CONTEXT_GATE_REASON="理由" git commit ...

理由會寫進 `docs/RETIRED.md` 的逃生門紀錄,**不是只印在畫面上**——
累積的理由清單會自己說明這條規則是不是太嚴,不需要另外做統計。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

RETIRED = "docs/RETIRED.md"

# (規則名, 適用檔案判準, 命中判準, 為什麼, 怎麼改)
RULES = [
    (
        "tmux socket 靠繼承",
        lambda p: p.startswith("src/") and p.endswith(".sh"),
        # ★ 2026-08-16 初審中-1:原式擋所有不帶 -L 的 tmux,把「pane 問自己
        #   幾何」也一起擋了——**那類本來就不該帶 -L**(帶了就問到別的
        #   server 去)。實測 src/ 底下 tmux 呼叫 150、帶 -L 22、不帶 128,
        #   其中 display/display-message/show-option(s)/list-panes 這類
        #   自問型的有 45 處,全是正當寫法。
        #   ★ 本 repo 已有裁定:**誤報率是要納管的,不是寧可錯殺**——誤報會
        #     教人把閘門關掉,而被關掉的閘門比沒有閘門更糟。
        #   ★★ 代價講清楚:白名單也會放過「真的該指定 socket 卻用了自問型
        #     指令」的情形。取捨理由=自問型指令的語意就是「這個 pane/server
        #     自己」,要跨 socket 用它本來就得改寫法;而 45 處誤報是確定的。
        re.compile(
            r"(?<![\w./-])tmux\s+"
            r"(?!-L\b)"
            r"(?!(display|display-message|show-option|show-options|"
            r"show-environment|showenv|list-panes)\b)"),
        "沒帶 -L 的 tmux 會用繼承來的 socket,可能打到別套指揮艙",
        "帶 -L,或改用既有的 hub_tmux / outer_tmux 包裝",
    ),
    (
        "run-shell 的工作目錄靠繼承",
        lambda p: p.startswith("src/") and p.endswith(".sh"),
        re.compile(r"run-shell\s+(?!.*\bcd\s)"),
        "run-shell 的工作目錄是家目錄不是 repo,而家目錄未被信任",
        "命令寫成 `cd \"$EZ2_AIB_ROOT\" && ...`",
    ),
    (
        "測試環境靠繼承",
        lambda p: p.startswith("tests/") and p.endswith(".py"),
        re.compile(r"dict\(\s*os\.environ"),
        "從 os.environ 複製會把活艙狀態一起帶進測試",
        "同一行或鄰近行釘住 EZ2_COCKPIT_STATE_DIR 等隔離變數",
    ),
    (
        "tmux 開窗沒指定命令",
        lambda p: p.startswith("tests/") and p.endswith(".py"),
        re.compile(r"[\"']new-(session|window)[\"']"),
        "不指定命令就跑預設 shell,畫面內容取決於是不是互動式",
        "顯式給一個會產生確定輸出的命令",
    ),
]

# 規則 3 的豁免:同一段裡已經釘了隔離變數就不算違規
ISOLATION_HINT = re.compile(r"EZ2_COCKPIT_STATE_DIR|PYTHONPATH")
# 規則 4 的豁免:同一行後面已經有命令字串
HAS_COMMAND = re.compile(r"new-(session|window)[\"'],.*[\"'][^\"']*\s")


def staged_added_lines():
    """回 [(檔名, 檔內行號, 內容), ...],只含**新增**的行。

    用 diff 而不是整檔,是本閘能不能存在的前提(見模組說明)。
    """
    out = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color"],
        capture_output=True, text=True).stdout
    added, path, lineno = [], None, 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if path:
                added.append((path, lineno, line[1:]))
            lineno += 1
    return added


def is_comment(text):
    s = text.strip()
    return s.startswith("#") or s.startswith("//")


# 豁免視窗:命中行往前 2 行、往後 10 行。
# ★ 為什麼是「往後多」:Python 的慣用寫法是先複製再覆寫,隔離變數在**後面**
#   幾行(`env = dict(os.environ)` → `env.update({...})`)。往前留 2 行是給
#   「先寫好隔離字典再複製」那種倒過來的寫法。
_ISO_BEFORE, _ISO_AFTER = 2, 10
_staged_blob_cache = {}


def staged_file_lines(path):
    """該檔**暫存版**的全部行(快取)。取不到=空清單。

    ★ 2026-08-16 初審高-3:原本的豁免只看**新增的那一行**,而模組說明寫的是
      「同一行**或鄰近行**」——說明比程式碼寬鬆。實測本 repo tests/ 底下
      `dict(os.environ)` 共 70 行,同一行帶隔離變數的只有 3 行,
      **也就是 96% 的既有正當寫法只要重新新增就會被擋**。
    ★ 讀暫存版而不是只看新增行:隔離變數可能是**既有的 context 行**
      (在一個已經釘好隔離的函式裡新增一行複製),那種情形不該被擋。
    """
    if path in _staged_blob_cache:
        return _staged_blob_cache[path]
    out = subprocess.run(["git", "show", ":%s" % path],
                         capture_output=True, text=True)
    lines = out.stdout.splitlines() if out.returncode == 0 else []
    _staged_blob_cache[path] = lines
    return lines


def isolation_nearby(path, lineno):
    """命中行附近有沒有釘住隔離變數。lineno 是 1-based。"""
    lines = staged_file_lines(path)
    if not lines:
        return False
    lo = max(0, lineno - 1 - _ISO_BEFORE)
    hi = min(len(lines), lineno + _ISO_AFTER)
    return any(ISOLATION_HINT.search(l) for l in lines[lo:hi])


def main():
    reason = os.environ.get("AIB_CONTEXT_GATE_REASON")
    if reason:
        # ★★ 2026-08-16 初審高-2:原本無論記錄成功與否都印「理由已記入」
        #   並放行。實測(乾淨 repo 無 docs/RETIRED.md):零寫入,而畫面照樣
        #   說已記入 —— **宣稱與現況分離,而宣稱寫得比現況更讓人放心。**
        #
        #   為什麼改成**記不了帳就不放行**(而不是「記不了就出個警告仍放行」):
        #   這道逃生門的全部價值,建立在「理由會累積成清單、清單自己會說話」
        #   ——模組說明白紙黑字寫著「不需要另外做統計」。記錄消失之後那個機制
        #   沒有了,而**放行照舊**,於是逃生門變成純粹的關閉開關。
        #   ★ 一句話:**逃生門記不了帳,就不該是逃生門。**
        ok, detail = _log_override(reason)
        if not ok:
            print("", file=sys.stderr)
            print("✖ 執行情境閘:逃生門**無法記錄理由**,因此不予放行",
                  file=sys.stderr)
            print("  原因:%s" % detail, file=sys.stderr)
            print("  這道逃生門的價值在於理由會累積成清單;記不了帳的放行",
                  file=sys.stderr)
            print("  等於一個沒有痕跡的關閉開關,所以這裡 fail-closed。",
                  file=sys.stderr)
            print("", file=sys.stderr)
            return 2
        print("⚠ 執行情境閘已放行", file=sys.stderr)
        print("  理由已記入 %s:%s" % (RETIRED, reason), file=sys.stderr)
        return 0

    hits = []
    for path, lineno, text in staged_added_lines():
        if is_comment(text):
            continue          # 註解裡寫到那個字是正常的,不是違規
        for name, applies, pattern, why, how in RULES:
            if not applies(path):
                continue
            if not pattern.search(text):
                continue
            if name == "測試環境靠繼承" and (
                    ISOLATION_HINT.search(text)
                    or isolation_nearby(path, lineno)):
                continue
            if name == "tmux 開窗沒指定命令" and HAS_COMMAND.search(text):
                continue
            hits.append((path, lineno, text.strip()[:64], name, why, how))

    if not hits:
        return 0

    print("", file=sys.stderr)
    print("✖ 執行情境繼承閘擋下這次 commit", file=sys.stderr)
    print("", file=sys.stderr)
    print("  「執行情境要顯式指定,不能靠繼承」——這條規則 2026-08-15 一天",
          file=sys.stderr)
    print("  之內以五種不同的變數各發作一次,所以它現在是閘門不是提醒。",
          file=sys.stderr)
    print("", file=sys.stderr)
    for path, lineno, text, name, why, how in hits:
        print("  %s:%d" % (path, lineno), file=sys.stderr)
        print("    %s" % text, file=sys.stderr)
        print("    ↑ %s——%s" % (name, why), file=sys.stderr)
        print("    改法:%s" % how, file=sys.stderr)
        print("", file=sys.stderr)
    print("  只擋**新增的行**;既有的違規不在本閘範圍(存量另案)。",
          file=sys.stderr)
    print("  真的要放行:AIB_CONTEXT_GATE_REASON=\"理由\" git commit ...",
          file=sys.stderr)
    print("  (理由會被記檔,不是只印在畫面上)", file=sys.stderr)
    print("", file=sys.stderr)
    return 1


def _log_override(reason):
    """把逃生門理由追加進 RETIRED.md → (成功?, 說明)。

    ★ 每一條失敗路徑都**回報**,不再靜默 return —— 呼叫端據此 fail-closed。
      原版三條路徑都可能靜默失敗:repo 根取不到、檔案不存在、寫入或
      `git add` 失敗,而畫面一律說「已記入」。
    """
    import time
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        return False, "取不到 repo 根(git rev-parse --show-toplevel 失敗)"
    p = Path(top) / RETIRED
    if not p.exists():
        return False, "%s 不存在,無處記錄(請先建立該檔)" % p
    who = (os.environ.get("EZ2_SESSION_SIG") or os.environ.get("USER")
           or "unknown")
    row = ("| %s | %s | 執行情境閘:%s |\n"
           % (time.strftime("%Y-%m-%d %H:%M"), who, reason))
    try:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(row)
    except OSError as err:
        return False, "寫入 %s 失敗:%s" % (p, err)
    add = subprocess.run(["git", "add", str(p)], cwd=top,
                         capture_output=True, text=True)
    if add.returncode != 0:
        # 已經寫進檔案但沒進暫存區:那筆記錄不會跟著這次 commit 進版控,
        # 下一個人 review 時看不到 —— 一樣算沒記到。
        return False, ("已寫入 %s 但 git add 失敗(rc=%d):%s"
                       % (p, add.returncode, add.stderr.strip()[:120]))
    return True, str(p)


if __name__ == "__main__":
    sys.exit(main())
