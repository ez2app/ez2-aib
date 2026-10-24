#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Consumer-set widening by property, not by name (item 53).

What the merge witness could not see
    It picks consumers by **finding file-name strings inside the ruler's source**
    (rules 1 and 2 in tools/governance/main-merge-witness.sh). Two kinds of ruler
    are therefore invisible:

    * the ones that reach the product through ``import``.
      ``import atomic_json`` contains no slash, no quote and no ``.py`` --
      rules 1 and 2 structurally cannot match it. Measured 2026-09-12:
      **19 of the 74 blind rulers are this shape**, and
      ``src/cockpit/dm_queue.py`` -- one of the four hot files -- had
      **1** consumer.
    * the ones that run one shell, while that shell sources others.
      ``src/cockpit/cockpit-env.sh`` is sourced by **33** shells; a ruler that
      runs ``cockpit-full.sh`` consumes all twelve shells it sources, and the
      witness sees only the seven characters ``cockpit-full.sh``.

Two rules are added here; rules 1 and 2 are not touched

    rule 3  import:  a ruler whose AST imports a module whose stem matches a
                     touched product ``.py`` is a consumer.
    rule 4  shell closure: a touched file is first expanded **upwards** along
                     the "shell sources shell" graph, then the expanded set is
                     fed to rules 1, 2 and 3.

    ★ Upwards, not downwards. Touching ``cockpit-env.sh`` expands to every
    shell that (transitively) uses it. The other direction is wrong: touching
    ``cockpit-full.sh`` does not mean every ruler that ever touched
    ``cockpit-env.sh`` must run.

Where rules 1 and 2 live
    **Still only in the shell script.** This tool deliberately does not
    reimplement them: it prints the *expanded touched paths*, and the script's
    existing loop applies rules 1 and 2 to them. One copy, not two.

Failing is loud
    If this tool cannot answer, the consumer set comes out **narrower** than the
    receipt claims -- and narrow is the exact disease being fixed here: a few
    rulers silently not run reads, on the receipt, the same as run-and-green.
    So the witness stops (rc=6). The escape hatch is explicit and named:
    ``AIB_CONSUMER_SCOPE_LEGACY=1`` runs rules 1 and 2 only and stamps the
    receipt ``scope=legacy``. A person may choose it; a person may not be
    unaware of it.

Usage
    consumer_scope.py --expand-touched      < touched paths, one per line
    consumer_scope.py --import-consumers    < product paths, one per line
    consumer_scope.py --test-closure        < consumer test paths, one per line
    consumer_scope.py --report              < touched paths   (human summary)

exit: 0 ok / 2 cannot measure (the witness turns this into rc=6)
"""
from __future__ import annotations

import argparse
import ast
import collections
import os
import pathlib
import re
import subprocess
import sys

# ★ 順序是承重的:本樹 insert(0)、fallback append(見 tools/dispatch/render-dispatch.py)。
_cockpit = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_cockpit) not in sys.path:
    sys.path.insert(0, str(_cockpit))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"      # 跟 tools/verify/select_changed.py 同一條(本樹優先)
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = i18n_lib.resolve_locale()


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)

PRODUCT_DIR = re.compile(r"^(?:src|tools|bin)/")
TEST_GLOB = re.compile(r"^tests/test-.*\.py$")
# 殼裡的「呼叫/載入另一支」。看得到的只有寫死的那一種:`. x.sh` / `source x.sh` /
# `bash …/x.sh` / `python3 …/y.py`。★ eval、變數組出來的路徑一律看不到 ——
# 這與規則①② 一樣是靜態近似,不是 shell 的語法樹。
CALL_RE = re.compile(
    r"(?:^|[\s.;(`\"'])(?:\.|source|bash|sh|python3|python)\s+[^\n]{0,160}?"
    r"([A-Za-z0-9_.-]+\.(?:sh|py))")
SHELL_EXT = (".sh",)


class Unmeasurable(RuntimeError):
    """量不到就停手。回一個窄集合比回一個錯誤更貴。"""


def repo_files(repo):
    res = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                         text=True)
    if res.returncode != 0:
        raise Unmeasurable(T("aib.cscope.git_failed",
                             {"rc": res.returncode,
                              "err": (res.stderr or "").strip()}))
    rows = res.stdout.splitlines()
    if not rows:
        raise Unmeasurable(T("aib.cscope.git_empty"))
    return rows


def read(repo, rel):
    try:
        with open(os.path.join(repo, rel), encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def is_shellish(repo, rel):
    """殼檔:`.sh`,或沒有副檔名而第一行是 shebang。"""
    if rel.endswith(SHELL_EXT):
        return True
    if os.path.splitext(rel)[1]:
        return False
    body = read(repo, rel)
    return bool(body and body.startswith("#!"))


def call_graph(repo, products):
    """{呼叫者: {被呼叫者}}。只看產品檔之間的邊。"""
    by_name = collections.defaultdict(list)
    for rel in products:
        by_name[os.path.basename(rel)].append(rel)
    edges = collections.defaultdict(set)
    for rel in products:
        if not is_shellish(repo, rel):
            continue
        body = read(repo, rel)
        if body is None:
            continue
        for m in CALL_RE.finditer(body):
            for target in by_name.get(m.group(1), ()):
                if target != rel:
                    edges[rel].add(target)
    return edges


def expand_upwards(touched, edges):
    """被動檔 → 所有(遞移)會用到它的殼,連同被動檔本身。

    ★ 方向是**往上**。往下展開會把「動一支殼就跑遍它下游的所有尺」變成常態,
    那是 BJ 那次過寬的病(289/368、一層驗證 45 分鐘)。
    """
    users = collections.defaultdict(set)
    for caller, callees in edges.items():
        for callee in callees:
            users[callee].add(caller)
    out = set(touched)
    queue = list(touched)
    while queue:
        cur = queue.pop()
        for up in users.get(cur, ()):
            if up not in out:
                out.add(up)
                queue.append(up)
    return out


def module_stems(paths):
    """被動產品 .py 的模組主幹名。`__init__` 不收(它對不到任何一支模組)。"""
    out = set()
    for rel in paths:
        if not rel.endswith(".py"):
            continue
        stem = os.path.basename(rel)[:-3]
        if stem and stem != "__init__":
            out.add(stem)
    return out


def imported_names(repo, rel):
    """尺裡 import 的頂層名字。

    ★ **`from . import x` / `from .mod import y`(相對匯入)看不到**:
    `ImportFrom.module` 在相對匯入裡是 None 或只有子模組名,對不到產品檔的主幹名。
    今天 tests/ 底下沒有套件化的相對匯入,所以這一格現在不咬人;
    哪天有了,那支尺會**靜靜掉出集合**,而掉出去與「本來就不相干」同形。
    (qa@b83ab 09-12 注入審 §3)
    """
    body = read(repo, rel)
    if body is None:
        return set()
    try:
        tree = ast.parse(body)
    except SyntaxError:
        # 尺自己語法壞掉不是本工具的事,但**不得**當成「沒有 import」。
        raise Unmeasurable(T("aib.cscope.unparsable", {"path": rel}))
    got = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                got.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            got.add(node.module.split(".")[0])
    return got


def import_consumers(repo, touched, tests):
    """規則③:import 到被動模組的尺。回 {尺: {對到哪幾支產品}}。"""
    stems = module_stems(touched)
    if not stems:
        return {}
    by_stem = collections.defaultdict(list)
    for rel in touched:
        if rel.endswith(".py"):
            by_stem[os.path.basename(rel)[:-3]].append(rel)
    out = {}
    for rel in tests:
        hit = imported_names(repo, rel) & stems
        if hit:
            out[rel] = {p for s in hit for p in by_stem[s]}
    return out


# 規則⑤(第 61 項,2026-09-12):**尺→尺** 的往上閉包。
# 量到的事實:74 支盲區裡有 18 支只引用**別的測試檔**、不引用任何產品檔 ——
# 它們是「尺測尺」(例:某支釘子的 docstring 自己寫著「引擎偵測寫死時,那三支 dm
# 測試全綠」)。規則①②③ 全都看不到它們,而它們正是會因為別人改動而紅的那種。
#
# ★★ **下面這幾個數字都是 2026-09-12 07:0x 量的,基底 433e4f97(本項的母顆)。**
#    它們住在註解裡,而**註解不會紅** —— main 每多幾支測試它們就偏幾支,沒有任何一格會叫。
#    (qa @bb222 09-12 14:28 用同一個正則重算:尺總數 528、邊 156,各比下面多 3,
#     那 3 支正好是當天新上 main 的三支測試。**兩個數都對,差在基底。**)
#    ⇒ 讀到對不上的時候,第一件事是看基底不是懷疑誰數錯;要用這些數字之前先重量。
#
# 現查:525 支尺裡 76 支引用別支尺(153 條邊);規則⑤ 救得到 14 支盲區。
# 爆炸半徑最大的一支被 12 支引用 —— 是十幾支的量級,不是 BJ 那次 289/368。
# ★ 「救得到 14 支盲區」那個 14 要有盲區清單才算得出來(`coverage-blindspots.py`),
#   qa 14:28 明說它沒有清單所以**沒有重現那一個數**;其餘四個數它都重現了。
#
# ★ 方向與規則④ 同:**往上**。若尺 X 引用尺 Y 而 Y 是消費者 ⇒ X 也是。
#   反過來(動 X 就跑遍它引用的所有 Y)是錯的。
# ★ 看不到的(兩類,**第二類今天有實例了**):
#   (一)用變數組出來的測試名。與①②③ 同一級的靜態近似。
#   (二)**掃全庫型的消費者** —— 一支尺用 `TESTS.glob("test-*.py")` 之類找對象,
#        它是**每一支尺的消費者**,而本規則只認寫死的檔名字串,結構上一支都抓不到。
#        ★★ 實例(qa @bb222 09-12 17:3x 追出來,**在此之前我把這一類寫成「邊界存在但實例是 0」,
#        那句作廢**):`tests/test-b21-release-gate-and-stamp.py` 第 91 行 `TESTS.glob("test-*.py")`,
#        它在 live34 紅了一輪而整輪 STOP —— 而用檔名字面圈的引用圖看不到它。
#        ⇒ **處置不是把 glob 型塞進本規則**(母體會爆炸),而是寫成規矩:
#        **凡是改到 `tests/` 底下任何一支檔的包,都要把這一類納入跑單。**
#        把它從「看不見的洞」變成「寫下來的規矩」。
#        ★ **這一類有 6 支(9 個呼叫點),但它們不是同一種** ——
#          我 09-12 17:3x 掃出清單、qa @bb222 17:4x 逐支驗出**各自的過濾條件**。
#          **「有 glob 呼叫」不等於「母體是全部尺」**,我第一版就是這樣圈寬了:
#
#            用**內容**過濾(檔案要含某個字面才進母體):
#              test-b21-release-gate-and-stamp.py(行 91)   要含 `aib_real_tmux`
#              test-host-lease-isolation-24.py(行 36)       要含那兩支拿鎖腳本名
#              test-provider-stub-names.py(行 143)          要含 `REAL_TMUX_ALLOWED` 或 `aib_real_tmux`
#            用**三個條件同時**成立:
#              test-mutation-probe-placement.py(行 262)     要同時有 read_text、write_text、source/src.replace
#            **真的吃全部**(rglob 無過濾):
#              test-no-dead-tests.py(行 32)                  **六支裡只有這一支**
#            **根本不屬這一類**(qa 17:42 查掉的):
#              test-b8e2-suite-results-table.py               它掃的是**治具樹**不是真的 `tests/`;
#                證據在它自己的斷言訊息逐字:`assertNotEqual(len(on_disk), 0, "夾具樹裡一支測試都沒有")`。
#                ★ 我先前把它列進來並標「判不準」—— **那行證據就在我 grep 過的輸出裡,我看到了
#                卻沒讀出「夾具樹」三個字的意義**。標「判不準」比亂猜好,但我本來查得到底。
#
#          ★ 這個清單是**用正則掃出來的下界**:用變數組出來的 glob 樣式看不到。
#          ★★ **比「有 6 支」更值得記的一句**(qa 17:4x):`test-no-dead-tests.py` 自己的註解
#            記著它 08-10 因為 `glob` 只掃第一層而漏掉子目錄,所以改成 `rglob` ——
#            **那支尺踩過「母體圈太窄」,而它現在是六支裡唯一圈得最寬的。**
#            踩過的人把判準修到最寬,沒踩過的還停在窄的。
TEST_REF_RE = re.compile(r"[\"'](test-[A-Za-z0-9_.-]+\.py)[\"']")


def test_reference_graph(repo, tests):
    """{尺: 它引用的尺集合}。只認寫死的檔名字串常數。"""
    by_name = {os.path.basename(t): t for t in tests}
    edges = collections.defaultdict(set)
    for rel in tests:
        body = read(repo, rel)
        if body is None:
            continue
        for m in TEST_REF_RE.finditer(body):
            tgt = by_name.get(m.group(1))
            if tgt and tgt != rel:
                edges[rel].add(tgt)
    return edges


def close_over_tests(seed, edges):
    """seed(已知消費者)→ 加上所有(遞移)引用到 seed 的尺。"""
    users = collections.defaultdict(set)
    for user, refs in edges.items():
        for ref in refs:
            users[ref].add(user)
    out = set(seed)
    queue = list(seed)
    while queue:
        cur = queue.pop()
        for up in users.get(cur, ()):
            if up not in out:
                out.add(up)
                queue.append(up)
    return out


def _stdin_paths():
    rows = [l.strip() for l in sys.stdin.read().splitlines()]
    return [r for r in rows if r]


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="consumer_scope",
        description=T("aib.cscope.cli_desc"))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--expand-touched", action="store_true")
    mode.add_argument("--import-consumers", action="store_true")
    mode.add_argument("--test-closure", action="store_true")
    mode.add_argument("--report", action="store_true")
    p.add_argument("--repo", default=".")
    args = p.parse_args(argv)

    try:
        given = _stdin_paths()
        if not given:
            raise Unmeasurable(T("aib.cscope.no_stdin"))
        rows = repo_files(args.repo)
        products = [r for r in rows if PRODUCT_DIR.match(r)]
        tests = [r for r in rows if TEST_GLOB.match(r)]
        # ★ 刪檔顆(qa@b83ab 09-12 注入審 §1,我造了一顆刪檔的顆實際重現過):
        #   被刪的產品檔**在 diff 裡、不在候選樹的 ls-files 裡**。舊版把它併進
        #   「不是產品檔」⇒ Unmeasurable ⇒ 殼 exit 6 ⇒ **每一顆刪檔的顆都會被擋住**。
        #   它自己一類:不停手、不展開(它已經不在了,沒有下游可算)、但**要說出來**,
        #   否則「刪掉的沒算」與「算過了沒事」同形。
        product_set = set(products)
        if args.test_closure:
            # 尺路徑形狀錯與候選樹缺檔是兩件事。後者可能是刪檔、改名或名單拼錯;
            # 不展開缺檔,但呼叫端必須保留原始 CONS,讓證詞具名記成 unmeasured。
            # 本入口只吐新增項,不能用 stdout 取代原始消費者集合。
            not_tests = sorted({g for g in given if not TEST_GLOB.fullmatch(g)})
            if not_tests:
                raise Unmeasurable(T("aib.cscope.not_test_path",
                                     {"names": "\u3001".join(not_tests)}))
            test_set = set(tests)
            seed = set(given) & test_set
            missing = sorted(set(given) - test_set)
            if missing:
                print(T("aib.cscope.missing_tests",
                        {"n": len(missing), "names": "\u3001".join(missing)}),
                      file=sys.stderr)
            closed = close_over_tests(
                sorted(seed),
                test_reference_graph(args.repo, tests))
            extra = sorted(closed - seed)
            for rel in extra:
                print(rel)
            print(T("aib.cscope.closure",
                    {"n": len(extra), "given": len(seed)}), file=sys.stderr)
            print(T("aib.cscope.population"), file=sys.stderr)
            return 0
        not_product = [g for g in given if not PRODUCT_DIR.match(g)]
        if not_product:
            raise Unmeasurable(T("aib.cscope.not_product_path",
                                 {"names": "\u3001".join(not_product[:5])}))
        deleted = sorted(g for g in given if g not in product_set)
        given = [g for g in given if g in product_set]
        if deleted:
            print(T("aib.cscope.deleted",
                    {"n": len(deleted), "names": "\u3001".join(deleted[:5])}),
                  file=sys.stderr)
        # 機器可讀的一行,給殼抄進收據(人讀的那一行在上面,兩者不共用格式)。
        print("aib-cscope-deleted=%d" % len(deleted), file=sys.stderr)
        # 全部都是刪掉的檔 ⇒ 沒有東西可展開,但**這不是錯誤**:殼那邊原本就有
        # 「本批不含碼類路徑」那條路,交給它。這裡回空集合並已經印過那兩行。
        edges = call_graph(args.repo, products)
        expanded = expand_upwards(given, edges)

        if args.expand_touched:
            for rel in sorted(expanded):
                print(rel)
            print(T("aib.cscope.expanded",
                    {"given": len(given), "expanded": len(expanded),
                     "delta": len(expanded) - len(given)}), file=sys.stderr)
            print(T("aib.cscope.population"), file=sys.stderr)
            return 0

        if args.import_consumers:
            hits = import_consumers(args.repo, given, tests)
            for rel in sorted(hits):
                print(rel)
            print(T("aib.cscope.imports", {"n": len(hits)}), file=sys.stderr)
            print(T("aib.cscope.population"), file=sys.stderr)
            return 0

        hits = import_consumers(args.repo, sorted(expanded), tests)
        print(T("aib.cscope.report",
                {"given": len(given), "expanded": len(expanded),
                 "n": len(hits)}))
        for rel in sorted(hits):
            print("  %s  ← import %s"
                  % (rel, "、".join(sorted(os.path.basename(x)
                                           for x in hits[rel]))))
        return 0
    except Unmeasurable as err:
        # ★ 逐字修法 + 逃生門,兩件都印。人可以選,不能不知道。
        print(T("aib.cscope.unmeasurable", {"why": err}), file=sys.stderr)
        print(T("aib.cscope.howto"), file=sys.stderr)
        print(T("aib.cscope.escape"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
