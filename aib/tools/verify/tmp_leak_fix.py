#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Add the missing temp-folder cleanup to tests (the ones `aib test` names as leaving things behind).

    python3 tools/verify/tmp_leak_fix.py tests/test-x.py ...          list the lines it would fix (changes nothing)
    python3 tools/verify/tmp_leak_fix.py --apply tests/test-x.py ...  fix them

Only one safe, common shape is fixed: inside a unittest.TestCase method (or a class in the same file that derives
from one), a one-line `<name> = tempfile.mkdtemp(...)` (or `pathlib.Path(tempfile.mkdtemp(...))`; <name> may be
self.x) with no rmtree / addCleanup of <name> in the same method. The next line gets
`self.addCleanup(shutil.rmtree, <name>, True)`, and `import shutil` is added at module level if missing.
Other leaks (background programs, NamedTemporaryFile(delete=False), module-level temp folders) need a person.
"""
# 10-09:aib test 總表點名「跑完留東西」的測試,用這支從源頭修。只補最安全的一種寫法;其他漏法要人看。
#   說明字串用英文:它是 --help 的輸出,工具輸出不准寫死中文(test-tools-i18n-ratchet)。
import argparse
import ast
import re
import sys
from pathlib import Path

NOTE = "   # 10-08:aib test 點名這支留暫存資料夾"


def _is_mkdtemp(v):
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "Path" and len(v.args) == 1:
        v = v.args[0]                                   # pathlib.Path(tempfile.mkdtemp(...))
    elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "Path" and len(v.args) == 1:
        v = v.args[0]                                   # Path(tempfile.mkdtemp(...))
    return (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "mkdtemp"
            and getattr(v.func.value, "id", "") == "tempfile")


def _target(t):
    if isinstance(t, ast.Name):
        return t.id
    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
        return "self." + t.attr
    return None


def _testcase_classes(tree):
    """這個檔裡(直接或經本檔其他類別)繼承 unittest.TestCase 的類別名。治具類別(沒有 addCleanup)不補。"""
    classes = {c.name: c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)}
    def base_name(b):
        return b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
    found, changed = set(), True
    while changed:
        changed = False
        for name, c in classes.items():
            if name not in found and any(base_name(b).endswith("TestCase") or base_name(b) in found for b in c.bases):
                found.add(name)
                changed = True
    return found


def plan(src):
    """回 [(插在第幾行之後, 要插的那行)]。"""
    lines = src.splitlines(keepends=True)
    out = []
    tree = ast.parse(src)
    cases = _testcase_classes(tree)
    fns = [fn for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name in cases
           for fn in c.body if isinstance(fn, ast.FunctionDef)]
    for fn in fns:
        if not fn.args.args or fn.args.args[0].arg != "self":
            continue
        body = ast.get_source_segment(src, fn) or ""
        for st in ast.walk(fn):
            if not (isinstance(st, ast.Assign) and len(st.targets) == 1 and _is_mkdtemp(st.value)):
                continue
            var = _target(st.targets[0])
            if var is None:
                continue
            if st.end_lineno != st.lineno or re.search(r"(rmtree|addCleanup)\([^)]*\b%s\b" % re.escape(var), body):
                continue
            indent = re.match(r"\s*", lines[st.lineno - 1]).group(0)
            out.append((st.lineno, "%sself.addCleanup(shutil.rmtree, %s, True)%s\n" % (indent, var, NOTE)))
    return sorted(out)


def apply(src, edits):
    lines = src.splitlines(keepends=True)
    for ln, text in sorted(edits, reverse=True):
        lines.insert(ln, text)
    if edits and not _has_shutil(src):
        tree = ast.parse(src)
        imps = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        future = [n for n in imps if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
        if future:
            at = max(n.end_lineno for n in future)          # 插在 from __future__ 之後
        elif imps:
            at = imps[0].lineno - 1                          # 插在第一個 import 之前
        else:
            at = 0
        lines.insert(at, "import shutil\n")
    return "".join(lines)


def _has_shutil(src):
    """模組層級已經有 `import shutil`(含 `import os, shutil`)?"""
    for n in ast.parse(src).body:
        if isinstance(n, ast.Import) and any(a.name == "shutil" and not a.asname for a in n.names):
            return True
    return False


def reviewed_files(root):
    """Paths named in a review receipt (acceptance/*.json). Their bytes are pinned by the receipt: never edit them here."""
    out = set()
    for f in sorted(Path(root).glob("acceptance/*.json")):
        try:
            out.update(re.findall(r'"((?:tests|src|tools)/[^"\s]+)"', f.read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tmp_leak_fix", description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    # 10-09:第 6 次推包被擋 —— 批次補到 tests/test-ledger.py,它是審過的交付物(收據記著指紋),一改就要重審
    pinned = reviewed_files(Path(__file__).resolve().parents[2])
    for f in a.files:
        p = Path(f)
        try:
            rel = str(p.resolve().relative_to(Path(__file__).resolve().parents[2]))
        except ValueError:
            rel = str(p)
        if rel in pinned:
            print("%s skipped (named in a review receipt under acceptance/)" % f)
            continue
        src = p.read_text(encoding="utf-8")
        edits = plan(src)
        print("%s %s" % (f, [ln for ln, _t in edits]))
        if a.apply and edits:
            p.write_text(apply(src, edits), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
