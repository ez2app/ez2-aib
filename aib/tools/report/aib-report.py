#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the canonical seven-section AIB report from structured JSON.

Usage:
    python3 tools/report/aib-report.py report.json
    python3 tools/report/aib-report.py - < report.json
    python3 tools/report/aib-report.py --show report.json   # 人眼入口:stdout 是終端才上色
    python3 tools/report/aib-report.py --replay             # 回放 latest.plain.txt(不寫收據)

R2(2026-08-29):純文字是唯一的資料層。每次成功渲染都把同一份位元組落到
``latest.plain.txt``(與 ``render.jsonl`` 同一把鎖內);``--show`` 只在 stdout
是 TTY 時上色並做 Sweep-Once,其餘一律純文字,零 ANSI。上色永遠發生在 lint 與
收據**之後**——report-lint 對帶 ANSI 的文字會硬擋「缺分隔線」。

The first line always comes from ``tools/hdr/aib-hdr.py``.  The complete
rendered report is then passed through ``report-lint.sh`` before anything is
written to stdout.  Invalid input returns 2; a lint rejection returns 1.  In
both cases stdout stays empty, so callers cannot paste a partial report.
"""

import argparse
import datetime
import fcntl
import hashlib
import io
import json
import math
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 十三條地基逐字列著「i18n 是地基」。
# ★★ 順序是承重的(33f17 2026-09-09 23:3x 抓到):連續兩次 insert(0) 會讓
#   **後 insert 的排前面** ⇒ 主庫會贏本樹,而 i18n_lib 的字典根綁 __file__
#   ⇒ 載到主庫那份就讀主庫的 i18n/*.json,本樹的新鍵不存在。
#   ★ 我這裡沒中是**運氣**:主庫那條本來就在 sys.path,第二次 insert 被跳過。
#   ⇒ 本樹用 insert(0)、fallback 用 append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402
# ★ 89a-甲(艦長 09-12 19:2x 裁):暫存根不寫死 macOS 專屬路徑。
#   a CI machine 自架 Linux runner 上 /private/tmp **存在但進不去**(errno 13),
#   寫死它會在那台機器上建不出暫存目錄,而失敗看起來像被測物壞了。
#   規則只有一份,住在 tests/aib_tmp_root.py(它有自己的 h0–h7 七格臂)。
#   ★ 方向反了(tools → tests)是已知的債,登在第 89c 項:規則搬 src/、兩邊 import。
#   ★ 順序照本檔上面那段的教訓:本樹 insert(0)、fallback append。
_tests = pathlib.Path(__file__).resolve().parents[2] / "tests"
if str(_tests) not in sys.path:
    sys.path.insert(0, str(_tests))
_tests_fallback = pathlib.Path.home() / "ez2-aib" / "tests"
if str(_tests_fallback) not in sys.path:
    sys.path.append(str(_tests_fallback))
from aib_tmp_root import aib_tmp_root  # noqa: E402


LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)



HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HEADER_TOOL = ROOT / "tools" / "hdr" / "aib-hdr.py"
LINT_TOOL = HERE / "report-lint.sh"
REPORT_RECEIPT_DIR = pathlib.Path(
    os.environ.get("EZ2_AIB_HOME")
    or (pathlib.Path.home() / ".ez2-aib")) / "state" / "aib-report"
ID_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*|T-[A-Za-z0-9.]+)$")
# F/§10.12: human_quote must be an event uid (evt:…), not free text.
EVENT_UID_RE = re.compile(r"^evt:[A-Za-z0-9:_@.-]{1,120}$")
# seat / task_uid (align seat_task_fields UID_RE; seat allows @五碼[/provider])
TASK_UID_RE = re.compile(r"^[A-Za-z0-9:._@-]{4,128}$")
SEAT_RE = re.compile(
    r"^(@[0-9a-f]{5}(/[A-Za-z0-9_-]+)?|[0-9a-f]{5}|the maintainer|未派)$")
TREE_MAX_DEPTH = 4  # 總綱 › 大項 › 子項 › Task
PROGRESS_AUTO = "auto"
STATUS_ENUM_FILE = HERE / "status-enum.json"
PLAIN_RECEIPT_FILE = REPORT_RECEIPT_DIR / "latest.plain.txt"
# ── R3 更正十一:樹狀子項兩行、每行 ≤ 顯示寬度上限(the maintainer 08-29 14:2x;REPLY-SPEC 更正十一)──
#   ★ 兩個常數各只住這裡一格;lint 讀本檔的字面,不另抄。執行期讀模組全域(臂會改它們再渲染)。
REPORT_LINE_CELLS = 100             # 每行顯示寬度上限(cell);the maintainer 嫌寬窄只改這格
BOX_CELLS = 1                       # 框線字 │├╰╭ 的顯示寬度(ambiguous width;the maintainer 的 pane 實證 1 格)
_BOX_CHARS = frozenset("│├╰╭")
NBSP = "\u00a0"

# ── R4 憑據指令真執行(規格 SPEC-R4-evidence-exec-9615c-0829 @ 60448bd6)──
#   判準顆 a seat 8c007d6a 先落,本檔是產品顆(RULING-B23e §一:判準先行要有時戳證據)。
#   ★ 這些常數各只住這裡一格。臂要證「拿掉白名單必紅」時,在候選樹上 sed 掉它再跑
#     —— 產品不為測試長出任何注入點(root3 02:3x 裁:那是突變測試的需求,不是產品需求)。
EVIDENCE_ARROW = "\u21d2"                  # ⇒:作者憑據與執行結果的分界,輸入端拒收它
EVIDENCE_TIMEOUT_S = 20                    # 單條逾時;旋鈕只能改它,沒有任何值能讓執行靜默消失
EVIDENCE_TOTAL_BUDGET_S = 120              # 總預算硬上限;每條起跑前檢查 ⇒ 真實上限 = 120 + 單條逾時
EVIDENCE_HEAD_CELLS = 40                   # stdout/stderr 首行截斷顯示格(用 display_cells,不是字元數)
#   ★5 c6411 02:5x 裁:白名單縮到【唯讀 git 子命令】。規格 §二 說資料層零副作用,
#   §三 卻放行整個 `git ` —— 而 `git fetch` / `git gc` 都會寫 .git,黑名單擋不到。
#   縮法不是發明:規格對 tmux 本來就只放行 display|list-|capture-pane,這裡只是補一致。
EVIDENCE_GIT_READONLY = (                  # 唯讀 git 子命令(第一道牆的 git 分支)
    "log", "show", "status", "rev-parse", "ls-remote", "grep", "diff",
    "merge-base",
)
EVIDENCE_ALLOW_PREFIXES = (                # 第一道牆:前綴白名單(git 另走唯讀子命令判定)
    "python3 tests/", "python3 tools/report/", "bash tools/report/",
    "grep ", "ls ", "cat ",
)
EVIDENCE_TMUX_READONLY = re.compile(       # tmux 只准唯讀子命令(規格 §三 逐字)
    r"^tmux\s+-L\s+\S+\s+(display|list-|capture-pane)")
#   第二道牆:黑名單。★ 用【詞元】比對不是子字串 —— "rm" 是 "--format" 的子字串,
#   照子字串比會讓 `git log --format=%H` 這種最自然的憑據指令被靜默拒絕,
#   而版面上長得像「這條指令不安全」。`>` `>>` 不是詞,維持子字串。
EVIDENCE_DENY_TOKENS = ("rm", "mv", "kill", "push", "commit", "--apply")
#   ★ c6411 05:1x 裁(一般規則,取代規格 §三 的字元清單那句):
#     黑名單一律在**詞元層**比對;重導向用**語法**判,不用字元出現。
#     它先裁「剝掉尖括號佔位符」,幾分鐘後作廢——因為 `cat < a.txt > b.txt`
#     的 `< a.txt >` 整段長得就像佔位符,剝掉之後那個 > 跟著消失。
#     ★ 修誤拒的代價不能是漏擋:誤拒會吵(有人來抱怨),漏擋不會。
EVIDENCE_REDIRECT_HEAD = re.compile(r"^\d*>>?")
#   ★2 c6411 02:5x 裁:⇒ 單一形狀,拒絕/逾時/off/預算一律帶 · HH:MM。
#   兩道牆用兩個不同字面:寫成同一串時,讀的人分不出是哪一道擋的。
EVIDENCE_REFUSED_TEXT = "拒絕執行(不在白名單)"
EVIDENCE_DENIED_TEXT = "拒絕執行(黑名單)"
EVIDENCE_OFF_TEXT = "未執行(off)"
EVIDENCE_BUDGET_TEXT = "未執行(總時長)"
_EVIDENCE_WARNED = set()                   # 具名告警每種只吵一次


def display_cells(text):
    """顯示欄寬:框線=BOX_CELLS、全形/寬=2、組合字=0、其餘 1。不是字元數。"""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if ch in _BOX_CHARS:
            total += BOX_CELLS
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            total += 2
        else:
            total += 1
    return total


def wrap_cells(text, width):
    """以顯示欄寬折行:先在空白處折;單一 token 超寬才在字元邊界硬折。回傳段列(至少一段)。"""
    if width < 4:
        width = 4
    segs, cur = [], ""
    for tok in text.split(" "):
        while display_cells(tok) > width:
            head, acc = "", 0
            for ch in tok:
                if acc + display_cells(ch) > width:
                    break
                head += ch
                acc += display_cells(ch)
            if cur:
                segs.append(cur)
                cur = ""
            segs.append(head)
            tok = tok[len(head):]
        if cur and display_cells(cur + " " + tok) > width:
            segs.append(cur)
            cur = tok
        else:
            cur = (cur + " " + tok) if cur else tok
    segs.append(cur)
    return segs


# ── R2b 流光與羽翼呼吸(規格 docs/reports/SPEC-R2b-shimmer-cap-5c59e-0829.md;the maintainer 選甲)──
#   ★ 總時長 = SWEEP_PASSES × SWEEP_SECONDS × 4 條 = 18 s。嫌長只改這兩格(或環境變數),不散在別處。
#   ★ 幀數固定 45/趟:秒數只改每幀等待,不改幀數——這樣 pty 對帳臂在 SECONDS=0 時幀數仍可數。
#   ★ 呼吸掛在流光的幀上(相位=幀序號/30),不是第二條時間軸;PASSES=0 時整條直接定格。
SWEEP_FPS = 30
SWEEP_FRAMES_PER_PASS = 45          # 1.5 s × 30 fps
SWEEP_PASSES = 3                    # 環境 EZ2_AIB_SWEEP_PASSES;0 = 不動畫
SWEEP_SECONDS = 1.5                 # 環境 EZ2_AIB_SWEEP_SECONDS;每趟秒數
SWEEP_SIGMA = 1.5                   # 環境 EZ2_AIB_SWEEP_SIGMA;光帶高斯寬(字)
SWEEP_HI = (255, 252, 225)          # 光帶中心的暖白金
SWEEP_BOLD_K = 0.6                  # k 超過此值的標題字加粗
BREATH_PERIOD_S = 3.0               # 羽翼呼吸週期
BREATH_AMP = 0.50                   # ★ 幅度那一格(規格 §六:15% 是 HTML 光暈版的值,終端看不見)
BREATH_DEFAULT_ON = True            # 環境 EZ2_AIB_WING_BREATH=off 關


def load_status_marks():
    """狀態符唯一碼系。★ 單源在 status-enum.json,這裡不留第二份拷貝。

    失效方向是刻意的:讀不到或形狀不對就爆,不落回任何內建預設。
    一份「找不到單源就用內建值頂著」的後備,會讓單源被刪掉之後
    一切照常運作——那是 report-lint.sh:64 那份拷貝的成因。
    """
    with io.open(str(STATUS_ENUM_FILE), encoding="utf-8") as handle:
        loaded = json.load(handle)
    marks = loaded.get("marks")
    if not isinstance(marks, dict) or not marks:
        raise RuntimeError("status-enum.json 的 marks 不是非空物件")
    for key, mark in marks.items():
        if not isinstance(key, str) or not isinstance(mark, str) or not mark:
            raise RuntimeError("status-enum.json 的 marks 有非字串項:%r" % (key,))
    separators = loaded.get("separators")
    if not isinstance(separators, dict) or len(separators) != 4:
        raise RuntimeError("status-enum.json 的 separators 不是四條")
    return (marks, loaded.get("retired") or {},
            loaded.get("rejected_keys") or {}, separators)


STATUS_MARK, RETIRED_MARK, REJECTED_KEY, SEPARATORS = load_status_marks()


def load_separator_aliases():
    """輸入端 alias(二字版 + 被取代的舊定版整行)→ 段身份;只給 --replay/--show 認出舊分隔線上色用。
    ★ 產出端永遠印 SEPARATORS 的值;這張表不進 render_report。"""
    with open(STATUS_ENUM_FILE, encoding="utf-8") as fh:
        loaded = json.load(fh)
    aliases = loaded.get("separator_aliases") or {}
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}


SEPARATOR_ALIASES = load_separator_aliases()


def _is_separator_line(line):
    return line in SEPARATORS.values() or line in SEPARATOR_ALIASES


def _split_separator(line):
    """任一條(現行或 alias)分隔線 → (羽翼前綴, 標題, 羽翼後綴);以第一個/最後一個 ``~~`` 為界。
    現行定版的結果與 ``_wings()`` 一致;舊定版(無空白)也能拆,所以 --replay 舊檔一樣上色。"""
    head = line.index("~~") + 2
    tail = line.rindex("~~")
    return line[:head], line[head:tail], line[tail:]


def separator(label):
    """四段分隔線的字面**引用**規格導出的機器形,不在這裡自存一份。

    ★ the maintainer 2026-08-27 15:3x 親裁字面定版;艦長 15:38 裁「規格是正本,不各存副本」。
    ★ 缺 label 就 KeyError —— 失效方向刻意朝紅:悄悄印出一條不對的分隔線,
      與印對了在版面上幾乎分不出來。
    """
    return SEPARATORS[label]


def explain_status(value, path):
    """把「這一格的狀態不合法」講成人看得懂、機器點得到名字的一句話。

    ★ 判準 31cb256e R1 逐字要求「訊息點名那個符」:只擋不點名不算數。
    ★ 三條路徑各自有自己的話,因為它們是三種不同的錯:
      退役符(有明確去處)/ unknown(故意不給出口)/ 沒填(SPEC §一-3 要求每格帶符)。
    """
    if value is None:
        return ("%s.status 沒填。SPEC §一-3 逐字要求「每個 Task 帶狀態符」,"
                "沒填不是一種狀態。合法輸入鍵:%s"
                % (path, ", ".join(sorted(STATUS_MARK))))
    if value in REJECTED_KEY:
        return ("%s.status = %r 一律拒收 —— %s。"
                "請改填一個真的狀態,或用 waiting_prereq 表示卡前置。"
                % (path, value, REJECTED_KEY[value]))
    if value in RETIRED_MARK:
        return ("%s.status = %r 是已退役的狀態符,新產出不合法;現在請用 %s。"
                "★ 舊符可以被讀懂,不等於可以被寫出來。"
                % (path, value, RETIRED_MARK[value]))
    return ("%s.status = %r 不是合法輸入鍵。唯一碼系在 tools/report/status-enum.json;"
            "合法鍵:%s" % (path, value, ", ".join(sorted(STATUS_MARK))))
def _wings():
    """羽翼字面由四條定版分隔線的共同前綴/後綴**導出**,不另抄一份。"""
    values = list(SEPARATORS.values())
    prefix = os.path.commonprefix(values)
    suffix = os.path.commonprefix([v[::-1] for v in values])[::-1]
    if not prefix or not suffix:
        raise RuntimeError("status-enum.json 的四條分隔線沒有共同羽翼")
    return prefix, suffix


def _truecolor():
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _fg(rgb, index256):
    """真彩用 38;2;r;g;b,否則退 256 色。兩種都被 ESC[..m 剝得掉(判準一不受後備影響)。"""
    if _truecolor():
        return "\x1b[38;2;%d;%d;%dm" % rgb
    return "\x1b[38;5;%dm" % index256


GOLD_START = ((255, 224, 102), 220)
GOLD_END = ((245, 159, 0), 214)
WING = ((110, 118, 129), 243)          # 預設=曜石灰(the maintainer 未選;不選=這個)
HEART = ((252, 211, 77), 221)
RESET = "\x1b[0m"

# A6:羽翼色參數化。只有羽翼字元吃它;標題插值、♡、正文、幀數一概不動。
#   形:EZ2_AIB_WING_RGB="r,g,b" 或 "r,g,b/idx256",全部 0..255 整數。
#   非法 ⇒ 用預設**並在 stderr 具名告警一次**(靜默退預設=失效值等於安全值)。
#   資料層(pipe / latest.plain.txt / render.jsonl)不經過這裡:它只在上色路徑被呼叫。
WING_ENV = "EZ2_AIB_WING_RGB"
WING_INVALID_WARNING = "羽翼色參數非法,已用預設"
_WING_CACHE = []

# 修訂三(the maintainer 14:0x「玫瑰 c,其他全保留」):具名配色 = (基色, 呼吸峰);數字形的峰一律金。
#   名字大小寫不分;未知名字=非法 ⇒ 預設 + 上面那一行告警(字面不變)。
NAMED_PALETTES = {
    "obsidian": ((110, 118, 129), (245, 159, 0)),
    "platinum": ((203, 213, 225), (255, 255, 255)),
    "space": ((2, 132, 199), (56, 189, 248)),
    "emerald": ((16, 185, 129), (110, 231, 183)),
    "amethyst": ((139, 92, 246), (196, 181, 253)),
    "bronze": ((146, 96, 48), (230, 170, 90)),
    "ice": ((125, 211, 252), (240, 253, 255)),
    "sakura": ((236, 72, 153), (251, 207, 232)),
    "cyan": ((6, 182, 212), (165, 243, 252)),
}
# 收官段玫瑰(凍結規格 §參.3 落地;預設開,EZ2_AIB_CLOSING_ROSE=off 關):
#   第 4 條羽翼呼吸峰改玫瑰、♡ 也呼吸(玫瑰↔淺粉),定格 ♡=玫瑰;前三條不受影響。
CLOSING_ROSE_ENV = "EZ2_AIB_CLOSING_ROSE"
ROSE = (244, 63, 94)
ROSE_HEART_PEAK = (255, 140, 160)
CLOSING_SEGMENT = "收尾"


def _nearest_256(rgb):
    """xterm 256 色就近索引:6x6x6 色立方(16..231)+ 24 階灰(232..255);不用 0..15(各終端自訂)。"""
    levels = (0, 95, 135, 175, 215, 255)
    best = None
    for ri, r in enumerate(levels):
        for gi, g in enumerate(levels):
            for bi, b in enumerate(levels):
                d = (r - rgb[0]) ** 2 + (g - rgb[1]) ** 2 + (b - rgb[2]) ** 2
                if best is None or d < best[0]:
                    best = (d, 16 + 36 * ri + 6 * gi + bi)
    for k in range(24):
        v = 8 + 10 * k
        d = sum((v - c) ** 2 for c in rgb)
        if d < best[0]:
            best = (d, 232 + k)
    return best[1]


def _parse_wing(value):
    """回 ((r,g,b), idx256, 呼吸峰 rgb);非法回 None。未設回預設(曜石灰、峰=金)。"""
    if value is None:
        return WING + (GOLD_END[0],)
    name = value.strip().lower()
    if name in NAMED_PALETTES:
        base, peak = NAMED_PALETTES[name]
        return base, _nearest_256(base), peak
    body, sep, idx_text = value.partition("/")
    parts = body.split(",")
    if len(parts) != 3:
        return None
    try:
        rgb = tuple(int(part.strip()) for part in parts)
        idx = int(idx_text.strip()) if sep else None
    except ValueError:
        return None
    if any(not 0 <= c <= 255 for c in rgb):
        return None
    if idx is None:
        idx = _nearest_256(rgb)
    elif not 0 <= idx <= 255:
        return None
    return rgb, idx, GOLD_END[0]


def _sweep_controls():
    """回 (passes, seconds, sigma, breath_on)。每個行程解析一次;非法值退預設並 stderr 具名告警**各一行**。"""
    if _SWEEP_CACHE:
        return _SWEEP_CACHE[0]

    def read(name, default, cast, ok):
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = cast(raw.strip())
        except (TypeError, ValueError):
            value = None
        if value is None or not ok(value):
            print(T("aib.report.bad_env",
                    {"name": name, "raw": repr(raw), "default": repr(default)}), file=sys.stderr)
            return default
        return value

    passes = read("EZ2_AIB_SWEEP_PASSES", SWEEP_PASSES, int, lambda v: v >= 0)
    seconds = read("EZ2_AIB_SWEEP_SECONDS", SWEEP_SECONDS, float, lambda v: v >= 0)
    sigma = read("EZ2_AIB_SWEEP_SIGMA", SWEEP_SIGMA, float, lambda v: v > 0)
    breath = read("EZ2_AIB_WING_BREATH", BREATH_DEFAULT_ON,
                  lambda v: {"on": True, "off": False}[v.lower()], lambda v: True)
    rose = read(CLOSING_ROSE_ENV, True,
                lambda v: {"on": True, "off": False}[v.lower()], lambda v: True)
    _SWEEP_CACHE.append((passes, seconds, sigma, breath, rose))
    return _SWEEP_CACHE[0]


_SWEEP_CACHE = []


def _blend(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def colour_separator_frame(line, center, sigma, breath_k):
    """一幀:標題柔光帶(高斯,中心 ``center`` 字、寬 ``sigma``)+ 羽翼呼吸(``breath_k`` 0..1)。

    ★ 與 ``colour_separator`` 同一個形狀:每個字元 = SGR + 字 + RESET,不加不減任何字元。
    ★ breath_k = 0 且 center 在標題外時,羽翼/♡ 的位元組與定格逐位元組同(R2b-3/7/9 釘這個)。
    """
    prefix, title, suffix = _split_separator(line)
    n = len(title)
    out = []
    for i, ch in enumerate(title):
        t = 0.0 if n <= 1 else 1.0 - abs(2.0 * i / (n - 1) - 1.0)
        base = _lerp(GOLD_START[0], GOLD_END[0], t)
        k = math.exp(-((i - center) / sigma) ** 2)
        idx = GOLD_START[1] if k > 0.5 else (GOLD_END[1] if t > 0.5 else GOLD_START[1])
        bold = "\x1b[1m" if k > SWEEP_BOLD_K else ""
        out.append(_fg(_blend(base, SWEEP_HI, k), idx) + bold + ch + RESET)
    wing_rgb, wing_idx, peak_rgb = _wing_palette()
    heart_rgb, heart_idx = HEART
    if _segment_of(line) == CLOSING_SEGMENT and _closing_rose_on():
        peak_rgb = ROSE                                                    # 羽翼往玫瑰
        heart_rgb = _blend(ROSE, ROSE_HEART_PEAK, BREATH_AMP * breath_k)   # ♡ 也呼吸
    breath_rgb = _blend(wing_rgb, peak_rgb, BREATH_AMP * breath_k)

    def wing(text):
        # 前三條 ♡ 不呼吸;ASCII 空白照定格形狀包 SGR+RESET 但**不吃呼吸**(R2b-13);羽翼字呼吸。
        return "".join(
            (_fg(heart_rgb, heart_idx) + c + RESET) if c == "♡" else
            (_fg(wing_rgb, wing_idx) + c + RESET) if c == " " else
            (_fg(breath_rgb, wing_idx) + c + RESET)
            for c in text)
    return wing(prefix) + "".join(out) + wing(suffix)


def _wing_palette():
    """(基色 rgb, idx256, 呼吸峰 rgb);每個行程只解析一次,告警也只印一次。"""
    if not _WING_CACHE:
        parsed = _parse_wing(os.environ.get(WING_ENV))
        if parsed is None:
            print(WING_INVALID_WARNING, file=sys.stderr)
            parsed = WING + (GOLD_END[0],)
        _WING_CACHE.append(parsed)
    return _WING_CACHE[0]


def _wing_colour():
    """羽翼基色 (rgb, idx256)——定格與 A6 用的那一格。"""
    rgb, idx, _peak = _wing_palette()
    return rgb, idx


def _closing_rose_on():
    return _sweep_controls()[4]


def _segment_of(line):
    """分隔線(現行或 alias)→ 段身份;不是分隔線回 None。"""
    for seg, value in SEPARATORS.items():
        if line == value:
            return seg
    return SEPARATOR_ALIASES.get(line)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def colour_separator(line, highlight=None):
    """把一條**整行等於定版字面**的分隔線上色;不是定版字面就原樣回傳。

    ★ 剝色後必須逐位元組等於輸入:這裡只加 SGR,不加不減任何一個字元。
    """
    if not _is_separator_line(line):
        return line
    prefix, title, suffix = _split_separator(line)
    n = len(title)
    out = []
    for i, ch in enumerate(title):
        if highlight is not None and i == highlight:
            out.append(_fg(*GOLD_START) + "\x1b[1m" + ch + RESET)
            continue
        t = 0.0 if n <= 1 else 1.0 - abs(2.0 * i / (n - 1) - 1.0)   # 兩端 START、中央 END
        rgb = _lerp(GOLD_START[0], GOLD_END[0], t)
        idx = GOLD_END[1] if t > 0.5 else GOLD_START[1]
        out.append(_fg(rgb, idx) + ch + RESET)

    heart = HEART
    if _segment_of(line) == CLOSING_SEGMENT and _closing_rose_on():
        heart = (ROSE, HEART[1])          # 收官段定格 ♡=玫瑰(修訂三 9.1)

    def wing(text):
        return "".join(
            (_fg(*heart) + c + RESET) if c == "♡" else (_fg(*_wing_colour()) + c + RESET)
            for c in text)
    return wing(prefix) + "".join(out) + wing(suffix)


def show_report(text, write, sleep):
    """R2b:每條分隔線 ``passes`` 趟柔光帶由左掠到右(每趟 45 幀),羽翼同幀呼吸,然後定格。

    ★ 只用 ``\r`` 重寫**當前行**;零垂直游標移動、零清屏、零 alt-screen——
      這是「pane 對帳臂」的全部機制:capture-pane 讀到的是定格後的格子內容。
    ★ 寫與睡都走注入的 ``write``/``sleep``,不開執行緒、不排計時器;
      回傳之後不會再有任何一次寫。這是 R0-8 的判準,不是實作細節。
    ★ 呼吸相位 = 幀序號 / 30(掛在流光的幀上);PASSES=0 時零幀、直接定格——兩個開關一條時間軸。
    寫的形狀(給計數用):動畫幀 = 以 ``\r`` 開頭、不含 ``\n``;定格 = 以 ``\r``
    開頭、以 ``\n`` 結尾;其他行 = 原文 + ``\n``。
    """
    passes, seconds, sigma, breath, _rose = _sweep_controls()
    frames = SWEEP_FRAMES_PER_PASS
    delay = seconds / frames
    for line in text.split("\n"):
        if not _is_separator_line(line):
            write(line + "\n")
            continue
        n = len(_split_separator(line)[1])
        g = 0
        for _ in range(passes):
            for k in range(frames):
                center = -2.0 * sigma + (n - 1 + 4.0 * sigma) * k / (frames - 1)
                breath_k = ((1.0 - math.cos(2.0 * math.pi * (g / SWEEP_FPS) / BREATH_PERIOD_S)) / 2.0
                            if breath else 0.0)
                write("\r" + colour_separator_frame(line, center, sigma, breath_k))
                sleep(delay)
                g += 1
        write("\r" + colour_separator(line) + "\n")


# ★ R0-6 拆除縫:設成 None 就是「拔掉視覺渲染器」。資料層(純文字、lint、收據、
#   latest.plain.txt)一格都不經過它;--show 在 None 時退成純文字。
VISUAL_RENDERER = show_report


HAS_DIGIT = re.compile(r"[0-9]")
PROGRESS_TEXT_MAX_CHARS = 100
SUMMARY_MAX_CHARS = 150


class InputError(ValueError):
    pass


def require_dict(value, path):
    if not isinstance(value, dict):
        raise InputError("%s must be an object" % path)
    return value


def require_text(value, path, max_chars=None):
    if not isinstance(value, str) or not value.strip():
        raise InputError("%s must be non-empty text" % path)
    text = value.strip()
    if "\n" in text or "\r" in text:
        raise InputError("%s must be one line" % path)
    if max_chars is not None and len(text) > max_chars:
        raise InputError("%s must be at most %d characters" % (path, max_chars))
    return text


def require_id(value, path):
    ident = require_text(value, path)
    if not ID_RE.fullmatch(ident):
        raise InputError("%s must use ASCII contract/task numbering" % path)
    return ident


def validate(payload):
    payload = require_dict(payload, "report")
    allowed = {
        "title", "tree", "progress_text", "stars", "progress", "summary",
        "reflection", "footer", "notes",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise InputError("unknown report fields: %s" % ", ".join(unknown))

    out = {
        "title": require_text(payload.get("title"), "title"),
        "progress_text": require_text(
            payload.get("progress_text"), "progress_text",
            max_chars=PROGRESS_TEXT_MAX_CHARS),
        "summary": require_text(
            payload.get("summary"), "summary", max_chars=SUMMARY_MAX_CHARS),
        "reflection": require_text(payload.get("reflection"), "reflection"),
    }
    stars = payload.get("stars")
    if not isinstance(stars, int) or isinstance(stars, bool) or not 1 <= stars <= 5:
        raise InputError("stars must be an integer from 1 to 5")
    out["stars"] = stars

    # ★ 白話註解(2026-09-10 the maintainer:「專業字旁可以 [註xx],最下方要白話解譯」)
    #   選填;沒有它時輸出逐位元組與以前相同(釘著:test-plain-language-notes)。
    #   形狀:[{"term": "母體", "plain": "這次要檢查的範圍"}, ...],編號照順序 1..N。
    #   對得上對不上(文裡有沒有 [註N]、解釋是不是白話)不在這裡判 —— 那是
    #   jargon_gate 對**渲染後的純文字**判的,兩邊各判一次就會有兩套標準。
    notes = payload.get("notes", [])
    if not isinstance(notes, list):
        raise InputError("notes must be a list of {term, plain}")
    out_notes = []
    for i, raw_note in enumerate(notes):
        note = require_dict(raw_note, "notes[%d]" % i)
        unknown = sorted(set(note) - {"term", "plain"})
        if unknown:
            raise InputError("notes[%d] has unknown fields: %s" % (i, ", ".join(unknown)))
        out_notes.append({
            "term": require_text(note.get("term"), "notes[%d].term" % i),
            "plain": require_text(note.get("plain"), "notes[%d].plain" % i),
        })
    out["notes"] = out_notes

    tree = payload.get("tree")
    if not isinstance(tree, list) or not tree:
        raise InputError("tree must contain at least one major item")
    seen = set()
    majors = []
    for i, raw_major in enumerate(tree):
        majors.append(_validate_tree_node(
            raw_major, "tree[%d]" % i, parent_id=None, depth=1, seen=seen,
            is_major=True))
    out["tree"] = majors

    progress = payload.get("progress")
    if progress == PROGRESS_AUTO:
        done, total = count_leaf_progress(majors)
        # 分母=葉子數;0 葉子 → 0%(不是 None:呼叫端明確要 auto)
        out["progress"] = (int(round(100.0 * done / total)) if total else 0)
        out["progress_auto"] = True
        out["progress_leaf"] = {"done": done, "total": total}
    elif progress is not None and (
            not isinstance(progress, int) or isinstance(progress, bool)
            or not 0 <= progress <= 100):
        raise InputError(
            "progress must be null, an integer from 0 to 100, or %r"
            % PROGRESS_AUTO)
    else:
        out["progress"] = progress
        out["progress_auto"] = False

    footer = require_dict(payload.get("footer"), "footer")
    required_footer = {
        "dispatched", "executed", "human_decision", "waiting_update", "next",
        "patrol_minutes", "patrol",
    }
    missing = sorted(required_footer - set(footer))
    unknown = sorted(set(footer) - required_footer)
    if missing:
        raise InputError("footer is missing: %s" % ", ".join(missing))
    if unknown:
        raise InputError("footer has unknown fields: %s" % ", ".join(unknown))
    dispatched = footer.get("dispatched")
    if not isinstance(dispatched, int) or isinstance(dispatched, bool) or dispatched < 0:
        raise InputError("footer.dispatched must be a non-negative integer")
    patrol_minutes = footer.get("patrol_minutes")
    if (not isinstance(patrol_minutes, int) or isinstance(patrol_minutes, bool)
            or patrol_minutes < 0):
        raise InputError("footer.patrol_minutes must be a non-negative integer")
    out["footer"] = {
        "dispatched": dispatched,
        "executed": require_text(footer.get("executed"), "footer.executed"),
        "human_decision": require_text(
            footer.get("human_decision"), "footer.human_decision"),
        "waiting_update": require_text(
            footer.get("waiting_update"), "footer.waiting_update"),
        "next": require_text(footer.get("next"), "footer.next"),
        "patrol_minutes": patrol_minutes,
        "patrol": require_text(footer.get("patrol"), "footer.patrol"),
    }
    return out


def _require_event_uid(value, path):
    """human_quote: event uid only (evt:…)。自由文字一律拒。"""
    text = require_text(value, path)
    if not EVENT_UID_RE.fullmatch(text):
        raise InputError(
            "%s must be an event uid matching %s (got free text %r)"
            % (path, EVENT_UID_RE.pattern, text))
    return text


def _optional_drift(value, path):
    if value is None:
        return None
    return require_text(value, path)


def _optional_seat(value, path):
    if value is None:
        return None
    text = require_text(value, path)
    if not SEAT_RE.fullmatch(text):
        raise InputError(
            "%s must look like @五碼[/provider] or the maintainer/未派 (got %r)"
            % (path, text))
    return text


def _optional_task_uid(value, path):
    if value is None:
        return None
    text = require_text(value, path)
    if not TASK_UID_RE.fullmatch(text):
        raise InputError("%s must be a task uid shape (got %r)" % (path, text))
    return text


def _derive_status(children):
    """上層狀態從葉子推:全 done→done;任一 in_progress/blocked→in_progress;否則 pending。"""
    if not children:
        return "pending"
    statuses = []
    for ch in children:
        if ch.get("children"):
            statuses.append(_derive_status(ch["children"]))
        else:
            statuses.append(ch.get("status") or "pending")
    if statuses and all(s == "done" for s in statuses):
        return "done"
    if any(s in ("in_progress", "blocked") for s in statuses):
        return "in_progress"
    if any(s == "waiting_prereq" for s in statuses):
        return "waiting_prereq"
    if any(s == "not_doing" for s in statuses) and all(
            s in ("not_doing", "done") for s in statuses):
        return "not_doing"
    return "pending"


def _validate_leaf_fields(child, cpath, cid):
    """Leaf: status + evidence (+ optional seat/task_uid)."""
    unknown = sorted(set(child) - {
        "id", "title", "status", "evidence", "evidence_command",
        "seat", "task_uid", "human_quote", "drift", "children",
    })
    if unknown:
        raise InputError("%s has unknown fields: %s" %
                         (cpath, ", ".join(unknown)))
    status = child.get("status")
    if status not in STATUS_MARK:
        raise InputError(explain_status(status, cpath))
    evidence = require_text(child.get("evidence"), cpath + ".evidence")
    for _field, _value in (("evidence", evidence),
                           ("evidence_command", child.get("evidence_command"))):
        if isinstance(_value, str) and EVIDENCE_ARROW in _value:
            raise InputError(
                "%s.%s 不得含 %s(U+21D2):它是 R4 執行結果段的分界字元,"
                "出現在作者憑據裡會讓「剝掉 %s 段」變成兩種都說得通的操作"
                % (cpath, _field, EVIDENCE_ARROW, EVIDENCE_ARROW))
    command = child.get("evidence_command")
    if HAS_DIGIT.search(evidence):
        if not isinstance(command, str) or not command.strip():
            raise InputError(
                "%s.evidence 有數字(%r)卻沒有 evidence_command。"
                "一個沒有產生指令的數字,與「我抄來的」逐字同形。"
                % (cpath, evidence))
    elif command is not None and not (
            isinstance(command, str) and command.strip()):
        raise InputError("%s.evidence_command 要嘛不填,要嘛是非空字串" % cpath)
    node = {
        "id": cid,
        "title": require_text(child.get("title"), cpath + ".title"),
        "status": status,
        "evidence": evidence,
        "evidence_command": (command.strip()
                             if isinstance(command, str) and command.strip()
                             else None),
        "children": [],
    }
    seat = _optional_seat(child.get("seat"), cpath + ".seat")
    if seat is not None:
        node["seat"] = seat
    task_uid = _optional_task_uid(child.get("task_uid"), cpath + ".task_uid")
    if task_uid is not None:
        node["task_uid"] = task_uid
    # leaf 上的 human_quote/drift 也允許(設計「每一層都帶」);缺省不寫入
    if "human_quote" in child:
        node["human_quote"] = _require_event_uid(
            child.get("human_quote"), cpath + ".human_quote")
    if "drift" in child:
        node["drift"] = _optional_drift(child.get("drift"), cpath + ".drift")
    return node


def _validate_tree_node(raw, path, parent_id, depth, seen, is_major=False):
    """Recursive tree node. depth 1..TREE_MAX_DEPTH. Majors may omit status."""
    if depth > TREE_MAX_DEPTH:
        raise InputError(
            "%s exceeds tree max depth %d (總綱›大項›子項›Task)"
            % (path, TREE_MAX_DEPTH))
    node_in = require_dict(raw, path)
    major_extra = {"human_quote", "drift"}
    leaf_extra = {"status", "evidence", "evidence_command", "seat", "task_uid"}
    allowed = {"id", "title", "children"} | major_extra | leaf_extra
    unknown = sorted(set(node_in) - allowed)
    if unknown:
        raise InputError("%s has unknown fields: %s" % (path, ", ".join(unknown)))

    nid = require_id(node_in.get("id"), path + ".id")
    if nid in seen:
        raise InputError("duplicate id: %s" % nid)
    seen.add(nid)
    if parent_id is not None:
        if not (nid.startswith(parent_id + ".") or nid.startswith("T-" + parent_id)):
            raise InputError("%s must be a child of %s" % (nid, parent_id))

    children_raw = node_in.get("children")
    has_children = isinstance(children_raw, list) and len(children_raw) > 0

    if is_major and not has_children:
        raise InputError("%s.children must contain at least one item" % path)

    if has_children:
        if depth >= TREE_MAX_DEPTH:
            raise InputError(
                "%s cannot have children: already at max depth %d"
                % (path, TREE_MAX_DEPTH))
        clean_children = []
        for j, raw_child in enumerate(children_raw):
            clean_children.append(_validate_tree_node(
                raw_child, "%s.children[%d]" % (path, j),
                parent_id=nid, depth=depth + 1, seen=seen, is_major=False))
        node = {
            "id": nid,
            "title": require_text(node_in.get("title"), path + ".title"),
            "children": clean_children,
        }
        # Intermediate: status optional (derived at render if absent)
        if "status" in node_in:
            status = node_in.get("status")
            if status not in STATUS_MARK:
                raise InputError(explain_status(status, path))
            node["status"] = status
        if is_major or "human_quote" in node_in:
            if "human_quote" in node_in:
                node["human_quote"] = _require_event_uid(
                    node_in.get("human_quote"), path + ".human_quote")
        if "drift" in node_in:
            node["drift"] = _optional_drift(node_in.get("drift"), path + ".drift")
        # seat/task_uid on non-leaf ignored? Allow for Task-shaped mid nodes.
        if "seat" in node_in:
            node["seat"] = _optional_seat(node_in.get("seat"), path + ".seat")
        if "task_uid" in node_in:
            node["task_uid"] = _optional_task_uid(
                node_in.get("task_uid"), path + ".task_uid")
        return node

    # Leaf (non-major without children, or child without children)
    return _validate_leaf_fields(node_in, path, nid)


def iter_leaves(nodes):
    """Yield leaf dicts in tree order."""
    for n in nodes:
        kids = n.get("children") or []
        if kids:
            yield from iter_leaves(kids)
        else:
            yield n


def count_leaf_progress(nodes):
    """Return (done_count, total_leaves). done = status == 'done'."""
    leaves = list(iter_leaves(nodes))
    done = sum(1 for leaf in leaves if leaf.get("status") == "done")
    return done, len(leaves)



def generate_header():
    result = subprocess.run(
        [sys.executable, str(HEADER_TOOL)], capture_output=True, text=True,
        timeout=30, env=os.environ.copy())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError("aib-hdr.py failed: %s" % detail)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    # 包 B:aib-hdr 印兩行(身分抬頭 + 頂行)。回報第一行仍只用身分抬頭。
    if not lines:
        raise RuntimeError("aib-hdr.py did not emit exactly one line")
    if len(lines) > 2:
        raise RuntimeError("aib-hdr.py emitted too many lines: %d" % len(lines))
    return lines[0]


FOOTER_LABELS = ("已執行:", "真人判斷:", "等待更新:", "再下一步:", "下次巡查:")   # 收官五行的固定標籤(逐字)


def wrap_prose(text, width=None):
    """R5-wrap:散文段(摘要/反思)每行 ≤ width 顯示格,續行齊左緣(0 縮排)。
    ★ 只折超寬的行;不超寬的行逐位元組原樣(既有短散文的 golden 不動)。
    ★ width 預設在**呼叫時**讀 REPORT_LINE_CELLS(不用 def 時綁定):「the maintainer 嫌寬窄只改這格」要在執行期生效,
      qa L8/L13 改常數後折行必須跟著動(08-30 12:3x 抓到 def 時綁定的版本)。"""
    if width is None:
        width = REPORT_LINE_CELLS
    out = []
    for para in text.split("\n"):
        out.extend(wrap_cells(para, width) if display_cells(para) > width else [para])
    return out


def wrap_labelled(line, width=None):
    """R5-wrap:收官帶標籤的行(真人判斷:/等待更新:/再下一步:/下次巡查:/時間行的 已執行:)。
    首行保留標籤(stop hook 與 lint 認的是首行行首);續行縮排到**該固定標籤後的首字**那一格,
    標籤欄保持乾淨。找不到標籤就當段落折(縮排 0)。"""
    if width is None:
        width = REPORT_LINE_CELLS
    if display_cells(line) <= width:
        return [line]
    # ★ qa a seat 11:39:不得取「第一個冒號」——時間行先有 HH:mm,會把尺釘到時間戳。
    #   逐字匹配固定標籤,縮排到該標籤後首字;時間與已交派前綴留在首行。
    head, indent, label = "", 0, ""
    for candidate in FOOTER_LABELS:
        idx = line.find(candidate)
        if idx >= 0:
            label = candidate
            head = line[:idx + len(label)]
            indent = display_cells(head)
            break
    if not head:
        return wrap_cells(line, width)            # 沒標籤:當段落折
    # ★ the maintainer 09-07:標籤前面還掛著前綴(時間行的「… · 已交派 N 席 · 」)時,超寬就在**欄位邊界**斷,
    #   不要吊在標籤後面。舊法把續行縮到第 45 格,於是「GSC / 匯出 7 個 csv」被拆成兩段而且離左緣很遠。
    #   斷成兩行之後,「已執行:」與其他四個標籤一樣站在行首,續行也只縮 5 格。
    #   ★ 只有**超寬**的行會走到這裡(上面已經 early return),所以短行逐位元組不動。
    prefix = line[:len(head) - len(label)].rstrip()
    if prefix.endswith("·"):
        prefix = prefix[:-1].rstrip()
    if prefix:
        return ([prefix] if display_cells(prefix) <= width else wrap_cells(prefix, width)) \
               + wrap_labelled(line[len(prefix):].lstrip(" ·"), width)
    if indent >= width - 4:
        return wrap_cells(line, width)            # 標籤本身就太寬:當段落折
    # ★ 只折標籤**之後**的正文;首行=標籤+第一段。不能對整行 wrap_cells——
    #   窄寬度時它會在標籤前的空白處折,把「已執行:」推到續行(qa L11 在 60 格抓到;100 格時剛好沒露出)。
    tail = line[len(head):].lstrip(" ")
    chunks = wrap_cells(tail, width - indent)
    return [head + chunks[0]] + [" " * indent + seg for seg in chunks[1:]]


def _evidence_warn(key, message):
    """具名告警一次。靜默退預設 = 失效值等於安全值(本檔 A6 既有慣例)。"""
    if key not in _EVIDENCE_WARNED:
        _EVIDENCE_WARNED.add(key)
        print(message, file=sys.stderr)


def evidence_timeout_s():
    """旋鈕 EZ2_AIB_EVIDENCE_TIMEOUT_S。★ 未設與解析失敗走【同一條路徑】——
    分成兩條的話,「解析失敗」就變成第二個關閉開關,而它長得不像開關。"""
    raw = os.environ.get("EZ2_AIB_EVIDENCE_TIMEOUT_S")
    if raw is None or raw.strip() == "":
        return EVIDENCE_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value <= 0:
        _evidence_warn("timeout", "EZ2_AIB_EVIDENCE_TIMEOUT_S 非法(%r),已用預設 %ds"
                       % (raw, EVIDENCE_TIMEOUT_S))
        return EVIDENCE_TIMEOUT_S
    return value


def evidence_exec_enabled():
    """★ c6411 2026-08-31 05:0x 裁:預設 **OFF**。

    理由是母體,不是實作:⇒ 段預設開的話,**所有既有報告的憑據行都變形**,
    而合入樹上有 13 支消費者對憑據行做過斷言(git grep -l -e aib-report.py
    -e report-lint.sh -- tests,現算聯集),其中 5 支的前提會過期。
    ★ 我收緊的是憑據行的形狀,母體就是「誰對憑據行做過斷言」——
      不是「誰用了 evidence_command」。第一版我搞錯了母體。
    預設 OFF ⇒ 那 13 支逐支不變綠;R4 自己的臂用旋鈕 ON 跑。
    前提遷移(R4-c)由非產品作者做,遷完再翻預設 ON,同批。
    """
    return _evidence_mode() == "on"


def _evidence_mode():
    """三態,不是兩態。★ 這一格是裁定的必要推論,我把它寫出來免得它被當成我自作主張:

      未設(預設) -> "dormant":**完全不印 ⇒ 段**,憑據行與 R4 之前逐位元組相同
      "on"       -> 真執行
      "off"      -> 印 `⇒ 未執行(off) · HH:MM`(規格 E5:「關掉要看得見」)

    為什麼不能只有兩態:規格 E5 的 off 是【看得見的關】,它仍然印 ⇒ ——
    憑據行照樣變形,13 支消費者照樣紅。所以「預設 OFF」若解成 E5 的 off,
    裁定要保護的東西一格都保護不到。
    ★ 「關掉執行」與「R4 尚未啟用」是兩件事,而它們在兩態設計裡同形。
    """
    raw = os.environ.get("EZ2_AIB_EVIDENCE_EXEC", "").strip().lower()
    if raw == "on":
        return "on"
    if raw == "off":
        return "off"
    return "dormant"


def evidence_is_allowed(command):
    cmd = command.strip()
    if EVIDENCE_TMUX_READONLY.match(cmd):
        return True
    if cmd.startswith("git "):
        sub = cmd.split()[1] if len(cmd.split()) > 1 else ""
        return sub in EVIDENCE_GIT_READONLY
    return cmd.startswith(EVIDENCE_ALLOW_PREFIXES)


def _evidence_is_placeholder(tok):
    """`<各檔>` 這種佔位符不是重導向。條件:單一詞元、以 < 開頭 > 結尾、內部無尖括號。"""
    return (len(tok) >= 2 and tok.startswith("<") and tok.endswith(">")
            and "<" not in tok[1:-1] and ">" not in tok[1:-1])


def evidence_hits_denylist(command):
    """詞元層比對。危險字元同時是別種語法的一部分時,子字串比對**必然**過度命中——
    今晚同一形狀出現三次:`rm` 在 `--fo[rm]at`、`grep -c format`、`>` 在 `<各檔>`。
    """
    for part in re.split(r"[;|&]+", command):
        try:
            toks = shlex.split(part)
        except ValueError:
            toks = part.split()          # shlex 失敗退回 split,不丟例外
        for tok in toks:
            if tok in EVIDENCE_DENY_TOKENS:
                return True
            if EVIDENCE_REDIRECT_HEAD.match(tok):
                return True              # `>` `>out` `>>x` `2>/dev/null`
            if _evidence_is_placeholder(tok):
                continue
            if ">" in tok:
                return True
    return False


def evidence_head(text):
    """首行,截到 EVIDENCE_HEAD_CELLS 顯示格,截斷加 …。空字串回空字串。"""
    if not text:
        return ""
    line = text.splitlines()[0] if text.splitlines() else ""
    if display_cells(line) <= EVIDENCE_HEAD_CELLS:
        return line
    out = ""
    for char in line:
        if display_cells(out + char) > EVIDENCE_HEAD_CELLS - 1:   # 留一格給 …
            break
        out += char
    return out + "…"


def _evidence_stamped(text):
    """非執行類的 ⇒ 段也帶時刻(★2 裁)。時刻答的是「這份報是什麼時候決定不跑的」,
    不是「指令跑了多久」—— 沒有時刻的那一種,與「這一格根本沒被走到」同形。"""
    return "%s · %s" % (text, time.strftime("%H:%M"))


def run_evidence_command(command, budget_left):
    """跑一條憑據指令,回 (⇒ 段文字, 花掉的秒數)。

    ★ 永不丟例外:執行出事要在版面上看得見,不是讓整份報表炸掉。
    ★ 永不回 None/空字串:沒有 ⇒ 段的續行=這條指令沒被跑(規格 §一「永不靜默」)。
    ★ 只有真的跑過的那一種形狀才帶時刻 —— 沒跑過的東西沒有「跑的時刻」。
    """
    if not evidence_exec_enabled():
        return _evidence_stamped(EVIDENCE_OFF_TEXT), 0.0
    # ★ 黑名單先判。`git push` 兩道牆都擋得下,但回「不在白名單」會【低報】它:
    #   讀的人以為只是沒登記,而真正的事實是它被具名列為危險。
    #   兩道牆同時成立時,要報那個說得更多的。
    if evidence_hits_denylist(command):
        return _evidence_stamped(EVIDENCE_DENIED_TEXT), 0.0
    if not evidence_is_allowed(command):
        return _evidence_stamped(EVIDENCE_REFUSED_TEXT), 0.0
    if budget_left <= 0:
        return _evidence_stamped(EVIDENCE_BUDGET_TEXT), 0.0
    timeout = evidence_timeout_s()
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["/bin/bash", "-c", command], capture_output=True, text=True,
            timeout=timeout, cwd=str(ROOT), env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return (_evidence_stamped("逾時 %ds" % timeout),
                time.monotonic() - started)
    except OSError as exc:
        return ("rc=-1 · %s · %s"
                % (evidence_head(str(exc)), time.strftime("%H:%M")),
                time.monotonic() - started)
    spent = time.monotonic() - started
    head = evidence_head(proc.stdout) or evidence_head(proc.stderr)
    return ("rc=%d · %s · %s" % (proc.returncode, head, time.strftime("%H:%M")),
            spent)


def evidence_pass(majors):
    """整份報表跑一次,依樹狀順序共用同一個總預算。回 {id(leaf): ⇒ 段文字}。"""
    out = {}
    budget = float(EVIDENCE_TOTAL_BUDGET_S)
    for leaf in iter_leaves(majors):
        if _evidence_mode() == "dormant":
            continue                     # R4 未啟用:一個 ⇒ 都不印,舊報逐位元組不變
        command = leaf.get("evidence_command")
        if not command:
            continue                     # 沒有指令的子項不印 ⇒,舊報不得因此變紅
        text, spent = run_evidence_command(command, budget)
        budget -= spent
        out[id(leaf)] = text
    return out


def render_tree(majors):
    """更正十一 + F/§10.12:遞迴最多四層。兩層時輸出與舊版逐位元組相同。

    ★ 樹線:非末兄弟續 │、末兄弟用 U+00A0;子項 ├▸/╰▸。
    ★ 每行 ≤ REPORT_LINE_CELLS;大項/節點標題超寬 ⇒ RuntimeError。
    ★ 新欄缺省(無 human_quote/drift/seat/task_uid、無第三層)⇒ 舊報不變。
    """
    lines = []
    arrows = evidence_pass(majors)

    def suffix_bits(node, is_major_line):
        bits = []
        if "human_quote" in node:
            bits.append("原話 %s" % node["human_quote"])
        if "drift" in node and node["drift"] is not None:
            bits.append("偏了沒:%s" % node["drift"])
        if "seat" in node:
            bits.append(node["seat"])
        if "task_uid" in node:
            bits.append(node["task_uid"])
        return ((" · " + " · ".join(bits)) if bits else "")

    def prefix_for(ancestors_last):
        """Leading indent for a node whose ancestors_last encodes each ancestor's last-ness.

        depth-1 legacy (must stay byte-identical):
          non-last major → "  │  "; last major → NBSP*3 (no leading ASCII spaces).
        deeper: extend with "│"+2×fill or 3×fill per additional ancestor.
        ★ 末大項分支的縮排格一律 U+00A0(lint 更正十一);非末大項用普通空白。
        """
        if not ancestors_last:
            return "  "
        major_last = ancestors_last[0]
        if major_last:
            base = NBSP * 3
            fill = NBSP
        else:
            base = "  │  "
            fill = " "
        if len(ancestors_last) == 1:
            return base
        parts = [base]
        for was_last in ancestors_last[1:]:
            # "│  " but spaces must be the branch filler (NBSP under last major)
            parts.append(("│" + fill * 2) if not was_last else (fill * 3))
        return "".join(parts)

    def cont_filler(ancestors_last):
        # Filler follows the major (root of this branch), matching legacy last-major NBSP.
        if not ancestors_last:
            return " "
        return NBSP if ancestors_last[0] else " "

    def walk(nodes, ancestors_last):
        """ancestors_last: tuple of bool, one per ancestor (True=that ancestor was last)."""
        depth = len(ancestors_last)  # 0 for majors
        for i, node in enumerate(nodes):
            last = i == len(nodes) - 1
            kids = node.get("children") or []
            if depth == 0:
                connector = "╭▸" if i == 0 else ("╰▸" if last else "├▸")
                extra = suffix_bits(node, True)
                major_line = "  %s %s %s%s" % (
                    connector, node["id"], node["title"], extra)
                if display_cells(major_line) > REPORT_LINE_CELLS:
                    raise RuntimeError(
                        "大項 %s 標題超過 REPORT_LINE_CELLS=%d 顯示格(%d):標題要短,不自動折"
                        % (node["id"], REPORT_LINE_CELLS, display_cells(major_line)))
                lines.append(major_line)
            else:
                child_prefix = prefix_for(ancestors_last)
                child_connector = "╰▸" if last else "├▸"
                if kids:
                    # intermediate: show mark if present or derived
                    st = node.get("status") or _derive_status(kids)
                    mark = STATUS_MARK[st]
                else:
                    mark = STATUS_MARK[node["status"]]
                extra = suffix_bits(node, False)
                head = "%s%s %s %s " % (
                    child_prefix, child_connector, mark, node["id"])
                first = head + node["title"] + extra
                if display_cells(first) > REPORT_LINE_CELLS:
                    raise RuntimeError(
                        "子項 %s 標題超過 REPORT_LINE_CELLS=%d 顯示格(%d):標題要短,不自動折"
                        % (node["id"], REPORT_LINE_CELLS, display_cells(first)))
                lines.append(first)
                if not kids:
                    # leaf evidence lines
                    evidence = node["evidence"]
                    if node.get("evidence_command"):
                        evidence = "%s;指令 %s" % (
                            evidence, node["evidence_command"])
                        if id(node) in arrows:
                            evidence = "%s %s %s" % (
                                evidence, EVIDENCE_ARROW, arrows[id(node)])
                    filler = cont_filler(ancestors_last)
                    cont_lead = child_prefix + (
                        "│" if not last else filler * BOX_CELLS)
                    cont_prefix = cont_lead + filler * (
                        display_cells(head) - display_cells(cont_lead))
                    for seg in wrap_cells(
                            "(%s)" % evidence,
                            REPORT_LINE_CELLS - display_cells(cont_prefix)):
                        lines.append(cont_prefix + seg)
            if kids:
                walk(kids, ancestors_last + (last,))
            if depth == 0 and not last:
                lines.append("  │")

    walk(majors, ())
    return lines



def _load_gate():
    """jargon_gate 用指名路徑載入,不動 sys.path。
    ★ 讀不到 ⇒ 回 None,算繪照常、不加註、stderr 出聲:閘自己壞掉不准癱瘓它守的東西。"""
    try:
        import importlib.util
        gate = ROOT / "src" / "cockpit" / "jargon_gate.py"
        spec = importlib.util.spec_from_file_location("jargon_gate_for_notes", str(gate))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:                       # noqa: BLE001
        sys.stderr.write("WARN 讀不到 jargon_gate(%s),本份回報不自動加註\n" % type(exc).__name__)
        return None


_GATE = _load_gate()


#: 開關(the maintainer 09-09 14:0x 原話「這個註的功能是可以開關的」):EZ2_AIB_NOTES=off ⇒ 不掛、不印。
#: 關掉時輸出與沒有這個功能之前逐位元組相同(test-plain-language-notes 釘著)。
_DONE = set()


def _notes_on():
    """呼叫時才讀:有測試把本檔 import 進來在行程內反覆呼叫 render_report,import 時讀會鎖死。"""
    return os.environ.get("EZ2_AIB_NOTES", "on").lower() != "off"


def _annotate(text, order):
    return _GATE.auto_annotate(text, order, done=_DONE) if (_GATE and _notes_on()) else text


def render_report(data, header):
    stamp = " ".join(header.split()[:3])
    try:
        header_time = datetime.datetime.strptime(
            " ".join(header.split()[:2]), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise RuntimeError("aib-hdr.py emitted an unparseable time: %s" % exc)
    progress = ("進度未建" if data["progress"] is None
                else "進度 %d%%" % data["progress"])
    stars = "★" * data["stars"] + "☆" * (5 - data["stars"])
    footer = data["footer"]
    patrol_time = (header_time + datetime.timedelta(
        minutes=footer["patrol_minutes"])).strftime("%H:%M:%S")
    lines = [header, "", "【%s】" % data["title"], ""]
    lines.extend(render_tree(data["tree"]))
    # ★ R5-wrap(cap 08-30 06:0x 派):散文段折到 REPORT_LINE_CELLS。進度說明**不折**(lint 硬擋八:
    #   單行 ≤100 字,是字數規則);抬頭、分隔線不折;樹狀已由 R3 折。lint 對散文仍 warning-only(甲案不動)。
    # ★ 專業字自動加註(09-10 the maintainer:「專業字旁可以 [註xx],最下方要白話解譯」):
    #   散文段每一個名單上的術語接 [註N],N 跨欄位共用;白話從字典抄,不重翻。
    #   樹狀區不動 —— 它的每一行都被 R3 折行與多支測試釘著位元組。
    order = []
    _DONE.clear()
    manual = (data.get("notes") or []) if _notes_on() else []

    prose_fields = [data["progress_text"], data["summary"], data["reflection"],
                    footer["executed"], footer["human_decision"], footer["waiting_update"],
                    footer["next"], footer["patrol"]]
    for t in prose_fields:                      # 第一遍:只收字典詞的出現順序
        _annotate(t, order)
    _DONE.clear()                               # 第二遍要真的掛,所以「掛過」要歸零
    base = len(order)

    def A(t):
        t = _annotate(t, order)                 # 第二遍:真的改寫(order 已收齊,N 穩定)
        # 手填的臨時詞(字典外)也自動掛,編號接在字典詞後面;寫的人不用自己算 N。
        for i, note in enumerate(manual, base + 1):
            if note["term"] in t and ("%s[註" % note["term"]) not in t:
                t = t.replace(note["term"], "%s[註%d]" % (note["term"], i))
        return t
    lines.extend(["", separator("進度"), "", "（進度說明）", A(data["progress_text"]),
                  "", separator("摘要"), "", "%s %s" % (stars, progress)])
    lines.extend(wrap_prose(A(data["summary"])))
    lines.extend(["", separator("反思"), ""])
    lines.extend(wrap_prose(A(data["reflection"])))
    lines.extend(["", separator("收尾"), ""])
    for footer_line in (
            "%s · 已交派 %d 席 · 已執行:%s" % (stamp, footer["dispatched"], A(footer["executed"])),
            "真人判斷:%s" % A(footer["human_decision"]),
            "等待更新:%s" % A(footer["waiting_update"]),
            "再下一步:%s" % A(footer["next"]),
            "下次巡查:%s(約 %d 分鐘後)之後 %s" % (patrol_time, footer["patrol_minutes"], A(footer["patrol"]))):
        lines.extend(wrap_labelled(footer_line))
    # 名詞解釋:收尾段最底下。自動抓到的先、手填的(notes 欄,字典外的臨時詞)接著編號。
    # 兩者都空 ⇒ 一行都不印,舊回報位元組不變(test-plain-language-notes 釘著)。
    block = _GATE.notes_block(order) if (_GATE and order) else []
    if manual and not block:
        block = [_GATE.notes_head() if _GATE else "【名詞解釋】"]
    for i, note in enumerate(manual, base + 1):
        block.append("[註%d] %s:%s" % (i, note["term"], note["plain"]))
    for b in block:
        lines.extend(wrap_prose(b))
    lines.append("")
    return "\n".join(lines)


def lint_report(text):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="aib-report-",
                suffix=".txt", dir=os.environ.get("TMPDIR", aib_tmp_root()),
                delete=False) as handle:
            handle.write(text)
            path = handle.name
        return subprocess.run(
            ["/bin/bash", str(LINT_TOOL), path], capture_output=True,
            text=True, timeout=30, env=os.environ.copy())
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _write_plain_receipt(report_bytes):
    """latest.plain.txt:與 render.jsonl 末筆 report_sha256 相同的那份位元組。

    原子寫(tmp + replace),0600;呼叫端已持 render.jsonl 的 flock,所以
    「末筆收據」與「latest」指的是同一次渲染。多寫者語意=最後寫者勝,耐久的是 ledger。
    """
    tmp = PLAIN_RECEIPT_FILE.with_name(PLAIN_RECEIPT_FILE.name + ".%d.tmp" % os.getpid())
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(report_bytes)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(PLAIN_RECEIPT_FILE))


def _append_render_receipt(report_bytes):
    """Bind the exact post-header body before any outer wrapper can alter it."""
    header, separator, body = report_bytes.partition(b"\n")
    if not header or not separator or not body:
        raise RuntimeError("rendered report has no complete header/body boundary")
    try:
        REPORT_RECEIPT_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
        if REPORT_RECEIPT_DIR.is_symlink():
            raise OSError("receipt directory is a symlink")
        path = REPORT_RECEIPT_DIR / "render.jsonl"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("receipt ledger is not a single-link regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            receipt = {
                "schema": "ez2-aib/report-render-receipt/v1",
                "receipt_id": "rr_" + uuid.uuid4().hex,
                "recorded_at_ms": int(time.time() * 1000),
                "header_sha256": hashlib.sha256(header).hexdigest(),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
                "byte_count": len(report_bytes),
            }
            payload = (json.dumps(
                receipt, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")) + "\n").encode("utf-8")
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            _write_plain_receipt(report_bytes)
        finally:
            os.close(fd)
    except OSError as exc:
        raise RuntimeError("cannot persist report body receipt: %s" % exc)


def load_input(path):
    try:
        raw = sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, ValueError) as exc:
        raise InputError("cannot read JSON input: %s" % exc)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="render and lint the canonical seven-section AIB report")
    parser.add_argument("input", nargs="?", help="JSON input path, or - for stdin")
    parser.add_argument("--show", action="store_true",
                        help="人眼入口:stdout 是終端才上色+Sweep-Once;不是終端就純文字並在 stderr 講一句")
    parser.add_argument("--replay", action="store_true",
                        help="回放 latest.plain.txt(不 lint、不寫收據);stdout 是終端才上色")
    parser.add_argument("--emit-report-json", action="store_true",
                        help="F:驗證後吐正規化總回報 JSON(progress:auto 已折算);"
                             "不渲染、不 lint。給 aib project show --report 接(寫集避 A/E)")
    args = parser.parse_args(argv)
    if args.replay:
        if args.input is not None:
            print(T("aib.report.replay_no_input"), file=sys.stderr)
            return 2
        try:
            encoded = PLAIN_RECEIPT_FILE.read_bytes()
        except OSError as exc:
            print(T("aib.report.no_replay", {"err": exc}), file=sys.stderr)
            return 2
        return _emit(encoded, show=True)
    if args.input is None:
        parser.error("需要輸入檔(或 - 讀 stdin)")
    try:
        data = validate(load_input(args.input))
        if args.emit_report_json:
            # 輸出給下游(project show --report);去掉內部旗標亦可讀
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0
        header = generate_header()
        report = render_report(data, header)
    except InputError as exc:
        print("INPUT_ERROR: %s" % exc, file=sys.stderr)
        return 2
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("RENDER_ERROR: %s" % exc, file=sys.stderr)
        return 2

    lint = lint_report(report)
    diagnostics = (lint.stdout or "") + (lint.stderr or "")
    if diagnostics:
        print(diagnostics.rstrip(), file=sys.stderr)
    if lint.returncode != 0:
        return 1
    encoded = report.encode("utf-8")
    try:
        _append_render_receipt(encoded)
    except RuntimeError as exc:
        print("RENDER_ERROR: %s" % exc, file=sys.stderr)
        return 2
    return _emit(encoded, show=args.show)


def _emit(encoded, show):
    """唯一的 stdout 出口。上色的三個前提缺一即純文字:要了 --show、stdout 是 TTY、渲染器沒被拔。"""
    if show and sys.stdout.isatty() and VISUAL_RENDERER is not None:
        VISUAL_RENDERER(encoded.decode("utf-8").rstrip("\n"),
                        write=lambda s: (sys.stdout.write(s), sys.stdout.flush()),
                        sleep=time.sleep)
        return 0
    if show:
        print(T("aib.report.not_a_tty"), file=sys.stderr)
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
