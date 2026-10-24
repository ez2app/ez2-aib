#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""挑受影響的測試(設計稿 docs/DESIGN-aib-verify-0926.md §3 第 1 步、§5 第 1 條)。

    影響集 = 靜態挑選(規則①②③④⑤) ∪ 掃全庫的閘(一律跑清單) ∪ 量測反查

輸出**逐支寫明為什麼被挑進來**:哪一條規則、經由哪一個檔。沒有理由的測試不得進集合 ——
「挑進來了」與「挑對了」在收據上長得一樣,理由是唯一分得開的東西。

## 規則從哪裡來(一份,不重做)

| 規則 | 內容 | 住在哪 |
|---|---|---|
| ① | 尺的原始碼裡出現**完整 repo 相對路徑** | `tools/governance/main-merge-witness.sh` 的 `git grep -l -F` |
| ② | 出現**帶邊界的檔名**(前面是 `/` 或引號,後面不是識別字元) | 同上的 `git grep -lE` |
| ③ | 尺 `import` 到被動產品模組(AST) | `consumer_scope.import_consumers` |
| ④ | 被動檔先沿「殼 source 殼」的圖**往上**展開,展開後才餵①②③ | `consumer_scope.expand_upwards` |
| ⑤ | 尺引用尺:引用到已知消費者的尺也是消費者 | `consumer_scope.close_over_tests` |

★ ①② 的**判準**只住在那支殼裡,本檔不重寫一份比對:本檔跑的是**同一條 git grep 命令**
  (同樣的 `-F` 與同一個正則、同樣的 sed 逃脫類),參數由同一個地方組出來。
  兩份比對邏輯會分岔,一條命令不會。`tests/test-verify-p2-select.py` 有一格把殼裡那兩行
  抽出來對照執行,兩邊吐的清單必須逐字相同 —— 那一格就是「沒有分岔」的守衛。

## 兩條靜態規則看不到、而本檔補上的

* **⑥ 一律跑清單** `tools/governance/witness-always-run.txt`:守的是**集合的性質**
  (全庫不變量、母體計數),不依附任何一支產品檔 ⇒ 任何從 diff 導出的消費者集合都到不了它。
  與改動無關,每一批都跑。
* **⑦ 掃全庫的閘**:一支尺用 `TESTS.glob("test-*.py")` 之類找對象,它是**每一支尺的消費者**,
  而①②⑤ 只認寫死的檔名字串,結構上一支都抓不到。
  consumer_scope 的檔頭把處置寫成規矩:**凡是改到 `tests/` 底下任何一支檔的包,都要把這一類納入跑單**。
  本檔把那條規矩執行出來,母體用**性質**圈(AST 找 `glob`/`rglob`/`glob.glob` 帶 `test-*` 樣式),
  不抄那份 09-12 的六支名單 —— 名單會過期,而過期的樣子是綠的。
  ★ 已知過寬一支:`test-b8e2-suite-results-table.py` 掃的是**治具樹**不是真的 `tests/`,
    性質圈的判準分不出來。過寬 = 多跑一支,是安全側;逐支理由裡有呼叫點行號,人看得到。

* **⑩ 不在 src|tools|bin 的改動檔**(`adapters/`、`schemas/`、`i18n/`、`acceptance/`…):
  舊版把它們丟進「其他」而不掃,grokbot a seat 09-26 用 `adapters/line.py` 證出漏挑。
  現在一律餵給①②③(是不是消費者交給規則判,不交給目錄名單判);只有④的殼展開仍限 src|tools|bin。

* **⑨ 共用治具**:`tests/` 底下不叫 `test-*.py` 的檔(`tests/aib_tmp_root.py` 這種)
  既不是產品也不是尺,舊分法把它們丟進「其他」⇒ 沒有任何規則去找誰 import 了它們。
  這裡把它們當成①②③的被動檔餵進去(理由照樣是 r1/r2/r3,`via` 是治具路徑)。

* **⑧ 量測反查**(`tools/verify/reuse_index.py`):影子追蹤器記錄每支測試**實際讀了哪些檔**;
  讀過改動檔的測試,就算①–⑦ 都沒挑到也照跑。這是**事後量**,補的是靜態挑選的漏。
  ★ 結構上的下界:只有被追蹤器量到的測試才進得了反查表(09-13 實測約 33% 量得到),
    所以它**只能補、不能取代**靜態挑選。反查表是空的(還沒跑過影子續跑)時,
    本檔照常吐①–⑦ 的集合,並在 `reverse.degraded` 寫明為什麼是空的 —— 不得靜默當成「沒有人讀過」。

## 失效方向

- **本檔算不出來**(git 不可用、consumer_scope 回 rc=2)⇒ `unmeasurable` 具名 + rc=3,
  呼叫端該做的是**全跑**,不是拿一個比較窄的集合去跑。窄的集合與「都跑過了」在收據上逐字相同。
- **單一規則漏抓**(認不得的引用寫法、變數組出來的路徑)⇒ 那支尺靜靜掉出集合。
  這是①②③⑤ 共同的下界,擋它的是⑥⑦⑧ 三層兜底與設計稿 §7 第 3 條的漏網率。

## 介面

    select_changed.py --repo . --base <ref> [--head <ref>]
    select_changed.py --repo . --changed <檔|-> < 改動路徑,一行一個
      共同旗標:--rows P(反查用的影子列檔/目錄,可多次)、--no-reverse(關掉⑧,刀用)
                --json(預設就是 JSON)、--print-tests(只印測試路徑,給殼接管線)

rc:0 算出來了 / 2 參數不合法 / 3 量不到(呼叫端要全跑)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ── 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py:tools/**/*.py 的中文字面不准流到輸出口)──
# ★ 順序是承重的(照 consumer_scope.py 的同一條):本樹 insert(0)、fallback append。
_COCKPIT = Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_COCKPIT) not in sys.path:
    sys.path.insert(0, str(_COCKPIT))
_COCKPIT_FALLBACK = Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_COCKPIT_FALLBACK) not in sys.path:
    sys.path.append(str(_COCKPIT_FALLBACK))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)

_GOV = REPO_DEFAULT / "tools" / "governance"
if str(_GOV) not in sys.path:
    sys.path.insert(0, str(_GOV))

import reuse_index  # noqa: E402
# 「哪幾條規則算直接命中」只有一份,住在 planner;本檔在產生理由時就把旗標標上,
# planner 只讀旗標,不再自己從 via 推導(同一個判斷不要兩個來源;a seat 09-26 裁)。
from planner import DIRECT_HIT_RULES  # noqa: E402

PRODUCT_DIR = re.compile(r"^(?:src|tools|bin)/")
TEST_GLOB = re.compile(r"^tests/test-.*\.py$")
TEST_PATHSPEC = "tests/test-*.py"
ALWAYS_RUN_REL = "tools/governance/witness-always-run.txt"
WITNESS_REL = "tools/governance/main-merge-witness.sh"

RULES = (
    "r0-changed-test", "r1-path-literal", "r2-basename-bounded", "r3-import",
    "r5-test-closure", "r6-always-run", "r7-repo-wide-glob",
    "r8-measured-reverse",
)


class Unmeasurable(RuntimeError):
    """量不到就具名停手。回一個窄集合比回一個錯誤更貴。"""


# ── ①② 的參數組法:與 main-merge-witness.sh 逐字同源 ───────────────────────────
# 殼裡那兩行:
#   git grep -l -F "$p" -- 'tests/test-*.py'
#   git grep -lE "[/\"']$(printf '%s' "$b" | sed 's/[][\.*^$\/]/\\&/g')([^A-Za-z0-9_-]|$)" -- 'tests/test-*.py'
# sed 的字元類是 ] [ \ . * ^ $ /(逐字照抄,不多不少)。
_SED_CLASS = re.compile(r"([][\\.*^$/])")


def basename_pattern(basename):
    """②的正則。逃脫的字元集合與殼裡的 sed 一字不差。"""
    return "[/\"']" + _SED_CLASS.sub(r"\\\1", basename) + "([^A-Za-z0-9_-]|$)"


def _git(repo, args):
    return subprocess.run(["git"] + args, cwd=str(repo), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)


def _grep_lines(repo, args):
    """git grep -l 的輸出。rc=1 是「沒命中」不是錯;rc>1 才是量不到。"""
    res = _git(repo, args)
    if res.returncode not in (0, 1):
        raise Unmeasurable("git grep rc=%d: %s" % (res.returncode,
                                                   (res.stderr or "").strip()))
    return [l.strip() for l in res.stdout.splitlines() if l.strip()]


def rule12_hits(repo, product_paths):
    """{測試: [(規則, 經由哪個檔)…]}。①②各自呼叫一次,命中分開記。"""
    hits = {}
    for p in product_paths:
        for t in _grep_lines(repo, ["grep", "-l", "-F", p, "--", TEST_PATHSPEC]):
            hits.setdefault(t, []).append(("r1-path-literal", p))
        b = os.path.basename(p)
        for t in _grep_lines(repo, ["grep", "-lE", basename_pattern(b), "--",
                                    TEST_PATHSPEC]):
            hits.setdefault(t, []).append(("r2-basename-bounded", p))
    return hits


# ── ⑦ 掃全庫的閘:用性質圈 ────────────────────────────────────────────────────
_GLOB_CALLS = ("glob", "rglob", "iglob")


def _repo_rooted_names(tree):
    """模組層級常數裡,定義式提到 `__file__` 的那些名字。

    `REPO = Path(__file__).resolve().parents[1]` ⇒ `REPO` 是倉根。
    `tmp = Path(tempfile.mkdtemp())` ⇒ 不是。
    這是⑦要分的那條線:**掃的是這棵樹,還是自己造的治具目錄**。
    """
    names = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        try:
            src = ast.unparse(value)
        except Exception:  # noqa: BLE001 - 舊版 python 沒有 unparse
            continue
        if "__file__" not in src and not any(
                n in src for n in names):
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _mentions_any(src, names):
    return any(re.search(r"\b%s\b" % re.escape(n), src) for n in names)


def _scope_repo_names(scope_node, base_names):
    """在一個函式(或模組)裡,把「從倉根算出來的」名字往下傳一層。

    ★★ 09-26 量到的:`tests/test-shell-hygiene.py` 寫的是

            def shell_scripts(root=None):
                root = Path(root) if root is not None else REPO
                for path in sorted(root.rglob("*.sh")):

      接收者是區域變數 `root`,只看接收者那一行會判「不是倉根」⇒ **整支漏掉**,
      而它正是 09-08 讓 main 變紅還被推上去的那一支。
      所以要追一層:函式裡指派給區域名、而右手邊提到倉根名或 `__file__` 的,
      那個區域名也算倉根。
    ★ 只追一層、只看指派,不做真的資料流分析。再繞一層(存進 dict、經過函式回傳)
      就看不到 —— 那是本判準的下界,失效方向是**漏**(往窄),寫在這裡。
    """
    names = set(base_names)
    for node in ast.walk(scope_node):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        try:
            src = ast.unparse(value)
        except Exception:  # noqa: BLE001
            continue
        if "__file__" not in src and not _mentions_any(src, names):
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _receiver_is_repo_rooted(node, repo_names):
    """`X.rglob(...)` 的 X 是不是從倉根算出來的。"""
    recv = getattr(node.func, "value", None)
    if recv is None:
        return False
    try:
        src = ast.unparse(recv)
    except Exception:  # noqa: BLE001
        return False
    if "__file__" in src:
        return True
    return _mentions_any(src, repo_names)


def glob_sites(repo, tests):
    """{測試: [(行號, 呼叫名, 字面樣式)…]}:AST 上帶字面樣式的 glob/rglob/iglob,
    **而且接收者是從倉根算出來的**。

    ★ 用**性質**圈不用名字圈:名單會過期,而過期的樣子是綠的。
    ★ 兩條靜態近似(都是下界,都寫在這裡):
      (一)組出來的樣式(變數、f-string)看不到 —— 與①②同一級。
      (二)接收者判不出是倉根就當**不是**。這條會漏:例如根是函式參數傳進來的。
           反過來(判不出就當是)會讓每一支走治具目錄的尺都變成全庫閘 ——
           實量 09-26(938b202e 那一顆,母體 708):不分接收者是 72 支,分了是 29 支,差的那些是
           `rglob('*')` 掃自己 mkdtemp 出來的安裝目錄。
    """
    out = {}
    for rel in tests:
        try:
            body = (Path(repo) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            # ★ 讀不到就跳過 = 往**窄**的方向失效:讀不到的那支可能正是掃全庫的閘,
            #   而少一支閘與「這一批沒有閘要跑」在收據上逐字相同。
            #   與下面 SyntaxError 那條同一個處置:停手,讓呼叫端去全跑。
            raise Unmeasurable("unreadable test: %s: %s" % (rel, err))
        # ★ 便宜的前篩:AST 認的是 `X.glob(...)`/`X.rglob(...)`/`iglob(...)`,
        #   三種寫法在原始碼裡一定含 `glob(` 這五個字。沒有就不必解析
        #   —— 解析 708 支要 6 秒,前篩之後只剩幾十支。
        #   ★ 這是**字面**前篩,而被篩掉的那支若真的有 glob 就會漏。
        #     它安全是因為 AST 端本來就只認帶字面樣式的直接呼叫:
        #     組出來的名字(getattr、字串拼)AST 一樣看不到,前篩沒有讓下界變低。
        if "glob(" not in body:
            continue
        try:
            tree = ast.parse(body)
        except SyntaxError:
            # 尺自己語法壞掉不是本工具的事,但**不得**當成「它不是掃全庫的閘」。
            raise Unmeasurable("unparsable test: %s" % rel)
        module_names = _repo_rooted_names(tree)
        # 每個函式自己一份(區域指派只在那個函式裡算數)
        scopes = [(f, _scope_repo_names(f, module_names))
                  for f in ast.walk(tree)
                  if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
        sites = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name not in _GLOB_CALLS or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant)
                    and isinstance(first.value, str)):
                continue
            # 找最內層、包住這個呼叫的函式
            names = module_names
            best = None
            for fn, fn_names in scopes:
                if (getattr(fn, "lineno", 0) <= node.lineno
                        <= getattr(fn, "end_lineno", 0)):
                    if best is None or fn.lineno > best[0].lineno:
                        best = (fn, fn_names)
            if best is not None:
                names = best[1]
            if not _receiver_is_repo_rooted(node, names):
                continue
            sites.append((node.lineno, name, first.value))
        if sites:
            out[rel] = sites
    return out


def _pattern_matches(pattern, changed_path):
    """一個 glob 樣式吃不吃得到這個改動檔。

    ★ 樣式的**根**是執行期算出來的(`REPO / "docs"`、`Path(__file__).parent`…),
      靜態讀不到。所以這裡只拿樣式的**最後一段**比檔名 —— 這是下界:
      `rglob("*.sh")` 會命中任何 .sh,包含它其實沒在掃的那些(過寬,安全側);
      而根本身的限制看不到(例如只掃 docs/ 的那一個會被算成掃全庫)。
      **不得**反過來用根去縮:縮掉的那支與「它本來就不相干」同形。
    """
    tail = pattern.rsplit("/", 1)[-1]
    if not tail:
        return False
    return fnmatch.fnmatch(os.path.basename(changed_path), tail)


def repo_wide_gates(repo, tests, changed):
    """{測試: [理由…]}:枚舉某一類檔的尺,而這次的改動裡有它吃得到的檔。

    ★★ 09-26 量到的洞(a seat 手挑 49 支裡我漏掉的那支就是它):
      舊版只認「枚舉**測試**」的閘(樣式像 `test-*`),於是
      `tests/test-shell-hygiene.py` 這種 `REPO.rglob("*.sh")` ——
      **每一支殼檔的消費者** —— 一支都抓不到。
      而這正是 09-08 讓 main 變紅還被推上去的那一族:
      推前跑的七支消費者是用**名字** grep 的,唯一會紅的那支是用**性質**圈的。
      修法不是把它加進名單(名單會過期),是把判準從「枚舉測試」放大成
      「枚舉任何一類檔,而這次改到了那一類」。
    """
    out = {}
    for rel, sites in glob_sites(repo, tests).items():
        why = []
        for lineno, name, pattern in sites:
            hit = [c for c in changed if _pattern_matches(pattern, c)]
            if hit:
                # 理由欄是機器讀的資料(進 JSON、測試用正則比),不是畫面文案 ⇒ 用 ASCII
                why.append("%s:%d:%s(%r) hits %s%s"
                           % (rel, lineno, name, pattern, hit[0],
                              (" (+%d more)" % (len(hit) - 1)) if len(hit) > 1 else ""))
        if why:
            out[rel] = why
    return out


def always_run_list(repo):
    """⑥ 一律跑清單。缺檔 ⇒ 空名單 + 具名一句(靜默的空名單與「都跑過了」逐字相同)。"""
    p = Path(repo) / ALWAYS_RUN_REL
    if not p.is_file():
        return [], "missing:%s" % ALWAYS_RUN_REL
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # 檔在卻讀不到 ⇒ 名單以空處理會讓一整類必跑臂消失,而消失的樣子是綠的。
        raise Unmeasurable("unreadable always-run list: %s" % err)
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names, ""


# ── 排序訊號:改動區塊裡被增刪的識別字(大綱第 256 項)──────────────────────
# ★★ 這是**排序訊號,不是挑選規則**(a seat 09-26 裁)。
#   它比檔名準(a seat 手挑那 49 支就是用它挑的),但沒有人量過它漏多少 ——
#   拿沒量過漏網率的判準去把集合改窄,等於用一個更準的猜法換掉一個已知很寬的猜法,
#   而「更準」這兩個字今天沒有數字。所以只用來排順序:排錯了只是慢,挑錯了會漏。
#: 只取**定義式**的名字,不取增刪行裡的每一個 token。
#:   09-26 實量:取每一個 token ⇒ 423 個識別字、708 支尺裡 697 支命中 ⇒ 分辨力等於零,
#:   而且 66 秒。a seat 手挑那 49 支用的是十幾個函式名/變數名/檔名片段,不是全部 token。
#:   「更準的訊號」與「命中所有人的訊號」在輸出上都是一堆命中,差別要用數字看。
_DEF_PATTERNS = (
    re.compile(r"\bdef\s+([A-Za-z_]\w{3,})"),              # python 函式
    re.compile(r"\bclass\s+([A-Za-z_]\w{3,})"),            # python 類別
    re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{3,})\s*(?::[^=]+)?="),  # 常數/環境變數
    re.compile(r"^\s*(?:function\s+)?([A-Za-z_]\w{2,})\s*\(\)"),   # 殼函式
    re.compile(r"\b([A-Za-z_][\w-]{3,}\.(?:py|sh|json|md|txt))\b"),  # 檔名片段
)
# ★ **不收**小寫區域變數的指派(`body =`、`lines =`、`code =`)。
#   09-26 實量:收了之後 120 個識別字命中 604/708 支 —— 排序訊號給每個人同一個名次
#   就等於沒有排序,而畫面上它照常在跑。a seat 手挑用的是
#   aib_post_frame_work / LAUNCH_CMD / AIB_ONBOARD / open_menu_popup 這一類,
#   全是定義出來的名字,不是隨手的區域變數。
#: 一個識別字命中超過這個比例的尺,就沒有分辨力(`assert`、`python3`…)。
#: 用**量**圈不用手寫停用字表:停用字表會過期,而過期的樣子是「排序看起來照常在跑」。
IDENT_TOO_COMMON_SHARE = 0.10
#: 識別字上限。超過就截斷(排序變粗,不影響挑不挑),並在輸出寫 `truncated`。
IDENT_MAX = 500


def hunk_identifiers(repo, base, head=""):
    """`git diff -U0` 的增刪行裡**被定義**的名字。回 sorted list。

    只看 `+`/`-` 開頭的內容行,不看 `+++`/`---` 檔頭,也不看 `@@` 行。

    ★★ 這是**排序訊號,不是挑選規則**(a seat 09-26 裁)。
      它比檔名準,但沒有人量過它漏多少 —— 拿沒量過漏網率的判準去把集合改窄,
      等於用一個更準的猜法換掉一個已知很寬的猜法,而「更準」今天沒有數字。
      排錯了只是慢,挑錯了會漏。
    ★ 下界:只認上面五種寫法。`obj.attr = x`、`setattr`、殼裡用變數組出來的名字看不到。
    """
    args = ["diff", "-U0", "--relative", base] + ([head] if head else [])   # 子專案外有改動時 changed_from_git 已判量不到
    res = _git(repo, args)
    if res.returncode != 0:
        raise Unmeasurable("git diff -U0 rc=%d: %s"
                           % (res.returncode, (res.stderr or "").strip()))
    got = set()
    for line in res.stdout.splitlines():
        if not line or line[0] not in "+-":
            continue
        if line.startswith(("+++", "---")):
            continue
        body = line[1:]
        for pat in _DEF_PATTERNS:
            got.update(pat.findall(body))
    return sorted(g for g in got if g)


def identifier_hits(repo, tests, idents):
    """{測試: [命中的識別字…]} + 被判為「太常見」而丟掉的那些。

    回 (hits, dropped)。`dropped` 要印出來:一個識別字被丟掉就代表這次排序
    少了一條訊號,而少一條訊號與「它本來就沒命中」在輸出上逐字相同。
    """
    if not idents:
        return {}, []
    if len(idents) > IDENT_MAX:
        # 大改動會吐出上千個識別字,合併正則會變慢而且沒有分辨力。
        # 截斷是**排序**變粗,不影響挑不挑;但要說出來,不得靜默。
        idents = sorted(idents)[:IDENT_MAX]
    # ★ 一條合併的正則,不是 N 條各掃一遍:N 條對 708 支尺是 66 秒(09-26 實量)。
    combined = re.compile(r"\b(%s)\b"
                          % "|".join(re.escape(i) for i in sorted(idents)))
    raw = {}
    for rel in tests:
        try:
            body = (Path(repo) / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # ★ 這裡跳過是對的,而規則七同樣的情形要停手 —— **兩件事**:
            #   規則七決定**跑不跑**,讀不到就會少挑一支(漏);
            #   本段只決定**先跑誰**,讀不到只是那一支排後面(慢)。
            #   同一個觀測值兩個處置,所以兩邊各寫一次理由,不共用一段碼。
            continue
        hit = set(combined.findall(body))
        if hit:
            raw[rel] = sorted(hit)
    n = max(1, len(tests))
    counts = {}
    for hit in raw.values():
        for i in hit:
            counts[i] = counts.get(i, 0) + 1
    dropped = sorted(i for i, c in counts.items()
                     if c > n * IDENT_TOO_COMMON_SHARE)
    drop = set(dropped)
    hits = {}
    for rel, hit in raw.items():
        keep = sorted(set(hit) - drop)
        if keep:
            hits[rel] = keep
    return hits, dropped


def changed_from_git(repo, base, head):
    # --no-renames(10-11 a seat):改名預設只印新路徑 ⇒ 把 tools/x.py 搬成 docs/x.py,舊路徑的消費者(import 它的測試)
    #   一支都挑不到,而漏挑的樣子是綠的。拆成「刪舊 + 加新」,兩條路徑都進規則①~⑤。
    res = _git(repo, ["diff", "--name-only", "--no-renames", base] + ([head] if head else []))
    if res.returncode != 0:
        raise Unmeasurable("git diff rc=%d: %s" % (res.returncode,
                                                   (res.stderr or "").strip()))
    return to_subproject(repo, [l.strip() for l in res.stdout.splitlines() if l.strip()])


def to_subproject(repo, paths):
    """10-24:--repo 指到倉裡的子專案(開源版的 aib/)⇒ 倉根相對路徑換成子專案相對(跟 ls-files 同一個座標)。
    子專案外也有改動 ⇒ 量不到(呼叫端跑全套)—— 丟掉外面的路徑會變成挑到 0 支的假綠(#314 審查 A1)。在倉根 = 原樣。"""
    pre = _git(repo, ["rev-parse", "--show-prefix"])
    prefix = (pre.stdout or "").strip() if pre.returncode == 0 else ""
    if not prefix:
        return paths
    outside = [p for p in paths if not p.startswith(prefix)]
    if outside:
        raise Unmeasurable("changed outside subproject %s: %d: %s" % (prefix, len(outside), ", ".join(outside[:5])))
    return [p[len(prefix):] for p in paths]


def tree_sha(repo):
    """這棵樹的識別值。回 (值, 來源)。

    ★ 抽查的種子吃它:同一棵樹重跑抽同一批、換一棵樹抽不同批。
      算不出來就明說 `unknown`,讓 planner 把「種子沒吃到樹」寫進輸出 ——
      靜靜退回固定種子的話,每一顆都會抽到同一批,而抽查看起來照常在跑。
    """
    if reuse_index.st is not None:
        try:
            return reuse_index.st.tree_sha(Path(repo)), "shadow_tracer.tree_sha"
        except Exception:  # noqa: BLE001
            pass
    res = _git(repo, ["rev-parse", "HEAD"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip(), "git-rev-parse-head(ignores-uncommitted)"
    return "unknown", "unavailable"


def repo_tests(repo):
    res = _git(repo, ["ls-files", "--", TEST_PATHSPEC])
    if res.returncode != 0:
        raise Unmeasurable("git ls-files rc=%d" % res.returncode)
    return [l.strip() for l in res.stdout.splitlines() if TEST_GLOB.match(l.strip())]


def normalize_changed(repo, changed):
    """把改動路徑統一成 repo 相對形。回 (正規化後, 丟不掉的那些)。

    ★ `./src/x.py` 與絕對路徑看起來都很正常,但 `PRODUCT_DIR` 對不上 ⇒ 它們會掉進
      「其他」而**不餵給任何規則**,結果是一個比較窄的集合而沒有人叫。
      倉外的路徑另記一類:那不是能正規化的東西,是呼叫端給錯了,要說出來。
    """
    root = Path(repo).resolve()
    out, outside = [], []
    for c in changed:
        c = str(c).strip()
        if not c:
            continue
        if c.startswith("./"):
            c = c[2:]
        if os.path.isabs(c):
            try:
                c = str(Path(c).resolve().relative_to(root))
            except ValueError:
                outside.append(c)
                continue
        out.append(c)
    return sorted(set(out)), sorted(set(outside))


def select(repo, changed, rows=None, use_reverse=True, idents=None):
    """回一份完整的 JSON 可序列化結果。任何一步量不到就丟 Unmeasurable。"""
    repo = str(Path(repo).resolve())
    changed, outside = normalize_changed(repo, changed)
    if outside:
        # 倉外的路徑不是「沒有消費者」,是呼叫端給錯了。窄的集合與「都跑過了」同形。
        raise Unmeasurable(T("aib.vp2.err.outside_repo",
                             {"names": "、".join(outside[:5])}))
    tests_all = repo_tests(repo)
    present = set(tests_all)

    products = [c for c in changed if PRODUCT_DIR.match(c)]
    changed_tests = [c for c in changed if TEST_GLOB.match(c)]
    # ★ `tests/` 底下**不叫 test-*.py** 的那些是共用治具(`tests/aib_tmp_root.py`
    #   這種)。它們既不是產品也不是尺,舊分法把它們丟進 others ⇒ **沒有任何規則
    #   去找誰 import 了它們**,而漏掉的樣子是綠的。
    #   `tests/aib_tmp_root.py` 今天被幾十支尺 import;改它而不跑那些尺,
    #   正是「跑單漏掉 importer」那一族。
    #   處置:把它們當成①②③的被動檔餵進去(理由欄照樣寫 r1/r2/r3,`via` 是治具路徑),
    #   **不**進④的殼展開(它不是殼)。
    helpers = [c for c in changed
               if c.startswith("tests/") and not TEST_GLOB.match(c)
               and not c.endswith("/")]
    others = [c for c in changed
              if not PRODUCT_DIR.match(c) and not TEST_GLOB.match(c)
              and c not in set(helpers)]

    why = {}   # test -> list[(rule, via)]

    def add(test, rule, via):
        why.setdefault(test, [])
        if (rule, via) not in why[test]:
            why[test].append((rule, via))

    # ── ⓪ 改到的測試自己 ──────────────────────────────────────────────
    for t in changed_tests:
        add(t, "r0-changed-test", t)

    # ── ④ 殼 source 殼往上展開,展開後才餵①②③ ─────────────────────
    expanded, expand_note, deleted = products, "", []
    if products:
        try:
            import consumer_scope as cs
        except Exception as err:  # noqa: BLE001
            raise Unmeasurable("consumer_scope import: %s: %s"
                               % (type(err).__name__, err))
        try:
            ls = cs.repo_files(repo)
            prods_in_tree = [r for r in ls if cs.PRODUCT_DIR.match(r)]
            edges = cs.call_graph(repo, prods_in_tree)
            # 刪檔顆:被刪的產品檔在 diff 裡、不在候選樹的 ls-files 裡。
            # 不停手、不展開(它已經不在了,沒有下游可算),但**要說出來**。
            alive = [p for p in products if p in set(prods_in_tree)]
            deleted = [p for p in products if p not in set(prods_in_tree)]
            expanded = sorted(cs.expand_upwards(alive, edges))
            if deleted:
                expand_note = "deleted:%d:%s" % (len(deleted),
                                                 "、".join(deleted[:5]))
        except cs.Unmeasurable as err:
            raise Unmeasurable("consumer_scope: %s" % err)

    expanded_from = sorted(set(expanded) - set(products))

    # ①②③ 吃的是**每一支不是 test-*.py 的改動檔**:展開後的產品檔 + 共用治具 + 其他。
    # ★★ 09-26 grokbot a seat 審出來的(有證據的漏挑):舊版只掃 src|tools|bin 與治具,
    #   `adapters/line.py` 被歸成「其他」⇒ 規則一二三不掃 ⇒ 關掉反查時
    #   `test-social-snapshots-line-readonly.py`、`test-social-provider-adapters.py`
    #   都進不了選集,而同一個路徑直接餵給 rule12_hits 會命中這兩支。
    #   同一個洞還有 schemas/ policy/ i18n/ skills/ themes/ acceptance/ deploy/。
    #   ⇒ 修法**不是**把那幾個目錄加進 PRODUCT_DIR(目錄名單會過期,過期的樣子是綠的),
    #   而是用性質圈:「是不是消費者」交給規則一二三去判 —— 有尺提到它,它就有消費者;
    #   沒有尺提到它,掃了也是零命中。掃描的成本只是幾條 git grep。
    #   PRODUCT_DIR 只剩一個用途:決定誰進規則四的殼展開(那張圖只畫 src|tools|bin)。
    # ★ 10-11 a seat:被刪的產品檔(含改名的舊路徑)不展開(④沒有下游可算),但**照樣餵①②③** ——
    #   寫著它路徑、import 它的那些尺正是要跑的(它們現在多半會紅)。以前整個丟掉 ⇒ 改名/刪檔一支都挑不到。
    scanned = sorted(set(expanded) | set(deleted) | set(helpers) | set(others))

    # ── ①② ────────────────────────────────────────────────────────────
    if scanned:
        for t, pairs in rule12_hits(repo, scanned).items():
            for rule, via in pairs:
                # 改到的尺自己會被自己的檔名命中,那不是「它是消費者」的理由
                if t == via:
                    continue
                add(t, rule, via)

    # ── ③ import ──────────────────────────────────────────────────────
    if scanned:
        # 10-16 @abb89:import 要自己一段 —— 跟呼叫同一個 try 時,模組不在 ⇒ 下面 except cs.… 先撞 UnboundLocalError
        try:
            import consumer_scope as cs
        except Exception as err:  # noqa: BLE001
            raise Unmeasurable("consumer_scope import: %s: %s"
                               % (type(err).__name__, err))
        try:
            hits3 = cs.import_consumers(repo, scanned, tests_all)
        except cs.Unmeasurable as err:
            raise Unmeasurable("import_consumers: %s" % err)
        except Exception as err:  # noqa: BLE001
            raise Unmeasurable("import_consumers: %s: %s"
                               % (type(err).__name__, err))
        for t, prods in hits3.items():
            for p in sorted(prods):
                if t == p:
                    continue
                add(t, "r3-import", p)

    # ── ⑥ 一律跑清單(與改動無關)──────────────────────────────────
    always, always_note = always_run_list(repo)
    for t in always:
        add(t, "r6-always-run", ALWAYS_RUN_REL)

    # ── ⑦ 掃全庫的閘:枚舉某一類檔,而這次改到了那一類 ─────────────────
    gates = repo_wide_gates(repo, tests_all, changed)
    for t, sites in gates.items():
        add(t, "r7-repo-wide-glob", sites[0])

    # ── ⑤ 尺引用尺:吃的是**已經算出來的**消費者集合 ────────────────
    # ★ import 放在用得到它的分支裡:改動全在 docs/ 時 products 是空的,
    #   上面那條 import 根本沒走過,這裡無條件 import 會丟 ImportError 而不是
    #   Unmeasurable —— 呼叫端看到的是 traceback,不是「量不到 ⇒ 全跑」。
    seed = sorted(t for t in why if t in present)
    if seed:
        try:
            import consumer_scope as cs2
            closed = cs2.close_over_tests(
                seed, cs2.test_reference_graph(repo, tests_all))
        except Exception as err:  # noqa: BLE001
            raise Unmeasurable("test_closure: %s: %s"
                               % (type(err).__name__, err))
        for t in sorted(set(closed) - set(seed)):
            add(t, "r5-test-closure", "seed")

    # ── ⑧ 量測反查(事後量,補靜態的漏)───────────────────────────────
    reverse_info = {"used": bool(use_reverse), "hits": {}, "added": [],
                    "degraded": "not-used" if not use_reverse else ""}
    if use_reverse:
        idx = reuse_index.build_index(rows or [], repo=repo)
        reverse_info["rows_files"] = len(idx.files)
        reverse_info["tests_indexed"] = len(idx.latest)
        reverse_info["paths_indexed"] = len(idx.reverse)
        reverse_info["degraded"] = idx.degraded
        # ★ 反查的命中有多可信,取決於那些列是不是這一棵樹量的。
        #   沒帶 --rows 就讀全機共用的預設目錄,那裡可能有別棵樹的列 ⇒ 假命中。
        reverse_info["rows_default_dir"] = idx.rows_default_dir
        reverse_info["foreign_rows"] = idx.foreign_rows
        reverse_info["unknown_origin_rows"] = idx.unknown_origin_rows
        hits = idx.tests_reading(changed)
        reverse_info["hits"] = hits
        before = set(why)
        for path, tests_hit in hits.items():
            for t in tests_hit:
                add(t, "r8-measured-reverse", path)
        reverse_info["added"] = sorted(set(why) - before)

    # ── 排序訊號:改動區塊的識別字(**不進 why,不影響挑不挑**)─────────────
    ident_hits, ident_dropped = identifier_hits(repo, tests_all, idents or [])
    # 量測用,不改行為:有多少支「命中識別字卻沒被挑進來」。
    # 這個數就是「把識別字當挑選規則會多挑幾支」,a seat 要的窄集合對照從它開始。
    ident_only = sorted(set(ident_hits) - set(why))

    # ── 收尾:名單裡有、候選樹沒有的路徑要具名,不得靜默丟掉 ─────────────
    missing = sorted(t for t in why if t not in present)
    # ★ direct 旗標(a seat p2 第 2 輪第二次審出,a seat 裁重大):
    #   planner 原本自己用「via 在 changed 裡」判直接命中。但規則一二三掃的是 scanned
    #   (= 改動檔 + 向上展開的殼 + 治具 + 其他),改到的是被 source 的殼時 via 是展開出來
    #   的那支(例如 aib),不在 changed ⇒ 第三道不觸發 ⇒ 真的直接用到改動碼的尺被判沿用。
    #   現在由知道全貌的這一端標:規則屬於直接三條,且 via 是這次**影響得到的檔**
    #   (scanned 集合;r0 的 via 是改到的尺自己,也在改動裡)。
    direct_set = set(scanned) | set(changed)
    selected = []
    for t in sorted(why):
        selected.append({
            "test": t,
            "in_tree": t in present,
            "why": [{"rule": r, "via": v,
                     "direct": (r in DIRECT_HIT_RULES and v in direct_set)}
                    for r, v in why[t]],
            # 訊號只排順序,不影響挑不挑(設計稿 §6 鐵則的同一條)
            "signals": {"hunk_identifiers": ident_hits.get(t, [])},
        })
    # ★ 數的是**支數**不是命中次數:一支尺被①經由兩個檔命中仍然只是一支。
    #   舊寫法數的是 (規則, 經由) 對數,而欄位名叫 by_rule —— 名字說支數、值是次數,
    #   兩個意思相同的句子而數字不同(第 96 項量尺同一條:原因以列計不以次計)。
    by_rule = {}
    for t in why:
        for r in {r for r, _v in why[t]}:
            by_rule[r] = by_rule.get(r, 0) + 1
    sha, sha_from = tree_sha(repo)
    return {
        "repo": repo,
        "tree_sha": sha,
        "changed_outside_repo": outside,
        "tree_sha_from": sha_from,
        "changed": changed,
        # other = 不在 src|tools|bin、不是尺也不是治具的改動檔。
        # ★ 它們**照樣**餵給規則一二三(grokbot 09-26),只是不進規則四的殼展開。
        "changed_classes": {"product": products, "tests": changed_tests,
                            "test_helper": helpers, "other": others},
        "expanded_product": expanded,
        "expanded_from_shell_source": expanded_from,
        "expand_note": expand_note,
        "always_run": {"list": always, "note": always_note},
        "repo_wide_gates": gates,
        "reverse": reverse_info,
        "identifiers": {
            "n": len(idents or []),
            "truncated": len(idents or []) > IDENT_MAX,
            "dropped_too_common": ident_dropped,
            "hit_tests": len(ident_hits),
            "hit_but_not_selected": ident_only,
        },
        "selected": selected,
        "missing_from_tree": missing,
        "counts": {
            "tests_in_tree": len(tests_all),
            "selected": len(selected),
            "by_rule": by_rule,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="select_changed")
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="")
    ap.add_argument("--changed", default="",
                    help=T("aib.vp2.help.changed"))
    ap.add_argument("--rows", action="append", default=[])
    ap.add_argument("--no-reverse", action="store_true",
                    help=T("aib.vp2.help.no_reverse"))
    ap.add_argument("--no-identifiers", action="store_true",
                    help=T("aib.vp2.help.no_identifiers"))
    ap.add_argument("--identifiers-from", default="",
                    help=T("aib.vp2.help.identifiers_from"))
    ap.add_argument("--print-tests", action="store_true")
    args = ap.parse_args(argv)
    repo = str(Path(args.repo).resolve())
    try:
        if args.base:
            changed = changed_from_git(repo, args.base, args.head)
        elif args.changed == "-":
            changed = [l.strip() for l in sys.stdin.read().splitlines()
                       if l.strip()]
        elif args.changed:
            changed = [l.strip() for l in
                       Path(args.changed).read_text(encoding="utf-8").splitlines()
                       if l.strip()]
        else:
            print(T("aib.vp2.err.need_base"), file=sys.stderr)
            return 2
        idents = []
        if args.base and not args.no_identifiers:
            idents = hunk_identifiers(repo, args.base, args.head)
        elif args.identifiers_from:
            idents = [l.strip() for l in
                      Path(args.identifiers_from).read_text(
                          encoding="utf-8").splitlines() if l.strip()]
        out = select(repo, changed, rows=args.rows,
                     use_reverse=not args.no_reverse, idents=idents)
    except Unmeasurable as err:
        print(json.dumps({"unmeasurable": str(err)}, ensure_ascii=False))
        print(T("aib.vp2.err.unmeasurable", {"why": err}), file=sys.stderr)
        return 3
    if args.print_tests:
        for row in out["selected"]:
            print(row["test"])
        return 0
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
