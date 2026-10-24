#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Consumer-set widening by property, not by name.

What a file-name search cannot see
    Picking consumers by finding file-name strings inside a ruler's source misses
    two kinds of ruler:

    * the ones that reach the product through ``import``.
      ``import atomic_json`` contains no slash, no quote and no ``.py``, so a
      path search structurally cannot match it.
    * the ones that run one shell, while that shell sources others.
      A ruler that runs ``cockpit-full.sh`` consumes every shell it sources,
      but the source only shows the name ``cockpit-full.sh``.

Rules added here (rules 1 and 2, the path searches, live in the caller)

    rule 3  import:  a ruler whose AST imports a module whose stem matches a
                     touched product ``.py`` is a consumer.
    rule 4  shell closure: a touched file is first expanded **upwards** along
                     the "shell sources shell" graph, then the expanded set is
                     fed to rules 1, 2 and 3.
    rule 5  ruler -> ruler: a ruler that names a known consumer ruler is a
                     consumer too (upwards, like rule 4).

    ★ Upwards, not downwards. Touching ``cockpit-env.sh`` expands to every
    shell that (transitively) uses it. The other direction is wrong: touching
    ``cockpit-full.sh`` does not mean every ruler that ever touched
    ``cockpit-env.sh`` must run.

Failing is loud
    If this tool cannot answer, the consumer set comes out **narrower** than
    claimed -- a few rulers silently not run reads the same as run-and-green.
    So it exits 2 and the caller treats the set as unmeasurable (run everything).

Usage
    consumer_scope.py --expand-touched      < touched paths, one per line
    consumer_scope.py --import-consumers    < product paths, one per line
    consumer_scope.py --test-closure        < consumer test paths, one per line
    consumer_scope.py --report              < touched paths   (human summary)

exit: 0 ok / 2 cannot measure
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

# 本樹的 src/cockpit 放最前面(i18n_lib 從這裡載)。
_cockpit = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_cockpit) not in sys.path:
    sys.path.insert(0, str(_cockpit))
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
    那會讓集合寬到失去意義(跑單接近全套、一層驗證拖很久)。
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


# 規則⑤:**尺→尺** 的往上閉包。有些尺只引用**別的測試檔**、不引用任何產品檔(「尺測尺」),
# 規則①②③ 都看不到它們,而它們正是會因為別人改動而紅的那種。
#
# ★ 方向與規則④ 同:**往上**。若尺 X 引用尺 Y 而 Y 是消費者 ⇒ X 也是。反過來是錯的。
# ★ 看不到的兩類:
#   (一)用變數組出來的測試名。與①②③ 同一級的靜態近似。
#   (二)**掃全庫型的消費者** —— 一支尺用 `TESTS.glob("test-*.py")` 之類找對象,它可能是每一支尺的消費者,
#        而本規則只認寫死的檔名字串,結構上抓不到。處置不是把 glob 型塞進本規則(母體會爆炸),而是寫成規矩:
#        **凡是改到 `tests/` 底下任何一支檔的包,都要把這一類納入跑單。**
#        注意「有 glob 呼叫」不等於「母體是全部尺」:多數這類尺還會用內容再過濾,要逐支看。
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
        # ★ 刪檔:
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
