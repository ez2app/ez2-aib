#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""量格用的執行器(10-06,開發檔 §13 C1):跑一支測試檔,在每一格開始/結束時往追蹤檔寫一筆「格界線」,
另外(--funcs)記每一格執行過這個倉裡的哪些函式。只用來量,不參與沿用判斷。

★ 刻意不放進追蹤器那幾支檔(TRACER_CODE_FILES):改那幾支,所有測試的環境指紋都變。
  格界線用跟追蹤器同一種寫法(一行 JSON 附加到 EZ2_VERIFY_TRACE),ch="cell";量格的分析端自己切,不經沿用的判斷。

用法:cell_runner.py [--order given|shuffle:<種子>] [--only 格,格] [--funcs 輸出.json] [--result 輸出.json] <測試檔>
  unittest 寫法(TestCase):格 = 類別.方法;pytest 寫法(沒繼承 TestCase 的 test_ 函式/類別):格 = pytest 的測試 ID。
  結束碼:全部綠 0;有紅 1;載入失敗 2。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import sys
import threading
import time
import unittest
from pathlib import Path


def _emit(obj):
    path = os.environ.get("EZ2_VERIFY_TRACE") or ""
    if not path:
        return
    obj = dict(obj, ch="cell", pid=os.getpid())
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


class Funcs:
    """每一格執行過的函式(只記這個倉底下、不在虛擬環境裡的檔)。只看「呼叫」事件,不看每一行,開銷小。"""

    def __init__(self, root):
        self.root = os.path.realpath(root) + os.sep
        self.cur = None
        self.by_cell = {}
        self._keep = {}

    def _want(self, filename):
        hit = self._keep.get(filename)
        if hit is None:
            real = os.path.realpath(filename) if filename and not filename.startswith("<") else ""
            hit = bool(real.startswith(self.root)) and not any(
                p in real for p in (os.sep + ".venv" + os.sep, os.sep + "venv" + os.sep, "site-packages"))
            self._keep[filename] = hit
        return hit

    def _trace(self, frame, event, arg):
        if event == "call" and self.cur is not None:
            co = frame.f_code
            if self._want(co.co_filename):
                rel = os.path.relpath(os.path.realpath(co.co_filename), self.root)
                self.by_cell.setdefault(self.cur, set()).add("%s:%s:%d" % (rel, getattr(co, "co_qualname", co.co_name),
                                                                         co.co_firstlineno))
        return None

    def start(self):
        sys.setprofile(None)
        threading.setprofile(lambda *a: None)
        sys.settrace(self._trace)
        threading.settrace(self._trace)

    def stop(self):
        sys.settrace(None)
        threading.settrace(None)

    def dump(self):
        return {k: sorted(v) for k, v in self.by_cell.items()}


class Cells:
    def __init__(self, funcs=None):
        self.funcs = funcs
        self.rows = []
        self._t = {}

    def begin(self, cell):
        _emit({"edge": "start", "cell": cell})
        if self.funcs is not None:
            self.funcs.cur = cell
        self._t[cell] = time.monotonic()

    def end(self, cell, outcome):
        took = time.monotonic() - self._t.pop(cell, time.monotonic())
        if self.funcs is not None:
            self.funcs.cur = None
        _emit({"edge": "end", "cell": cell, "outcome": outcome})
        self.rows.append({"cell": cell, "outcome": outcome, "seconds": round(took, 4)})


def _load_module(path):
    sys.path.insert(0, str(Path(path).resolve().parent))
    spec = importlib.util.spec_from_file_location("aib_cell_target", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aib_cell_target"] = mod
    spec.loader.exec_module(mod)
    return mod


def _flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def _cell_id(test):
    return "%s.%s" % (type(test).__name__, test._testMethodName)


def run_unittest(path, order, only, cells):
    _emit({"edge": "setup-start", "cell": ""})
    mod = _load_module(path)
    tests = list(_flatten(unittest.TestLoader().loadTestsFromModule(mod)))
    if only:
        tests = [t for t in tests if _cell_id(t) in only]
    if order:
        random.Random(order).shuffle(tests)

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            cells.begin(_cell_id(test))
            self._outcome_now = "pass"
            super().startTest(test)

        def addFailure(self, test, err):
            self._outcome_now = "fail"
            super().addFailure(test, err)

        def addError(self, test, err):
            self._outcome_now = "error"
            super().addError(test, err)

        def addSkip(self, test, reason):
            self._outcome_now = "skip"
            super().addSkip(test, reason)

        def addExpectedFailure(self, test, err):
            self._outcome_now = "pass"
            super().addExpectedFailure(test, err)

        def addUnexpectedSuccess(self, test):
            self._outcome_now = "fail"
            super().addUnexpectedSuccess(test)

        def stopTest(self, test):
            super().stopTest(test)
            cells.end(_cell_id(test), self._outcome_now)
    runner = unittest.TextTestRunner(resultclass=Result, verbosity=0)
    res = runner.run(unittest.TestSuite(tests))
    return 0 if res.wasSuccessful() else 1


def run_pytest(path, order, only, cells):
    import pytest

    class Plugin:
        def pytest_collection_modifyitems(self, session, config, items):
            if only:
                items[:] = [i for i in items if i.nodeid.split("::", 1)[-1] in only or i.nodeid in only]
            if order:
                random.Random(order).shuffle(items)

        def pytest_runtest_logstart(self, nodeid, location):
            cells.begin(nodeid.split("::", 1)[-1])
            self._out = "pass"

        def pytest_runtest_logreport(self, report):
            if report.failed:
                self._out = "fail" if report.when == "call" else "error"
            elif report.skipped and self._out == "pass":
                self._out = "skip"

        def pytest_runtest_logfinish(self, nodeid, location):
            cells.end(nodeid.split("::", 1)[-1], self._out)
    _emit({"edge": "setup-start", "cell": ""})
    rc = pytest.main(["-q", "-p", "no:cacheprovider", "-p", "no:randomly", str(path)], plugins=[Plugin()])
    return 0 if rc == 0 else (2 if rc in (2, 3, 4) else 1)


def is_unittest_file(text):
    return bool(re.search(r"^class\s+\w+\s*\([^)]*TestCase", text, re.M))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cell_runner")
    ap.add_argument("--order", default="given")
    ap.add_argument("--only", default="")
    ap.add_argument("--funcs", default="")
    ap.add_argument("--result", default="")
    ap.add_argument("test")
    a = ap.parse_args(argv)
    path = Path(a.test).resolve()
    order = None if a.order == "given" else a.order.split(":", 1)[-1]
    only = {x for x in a.only.split(",") if x}
    funcs = Funcs(os.getcwd()) if a.funcs else None
    cells = Cells(funcs)
    if funcs is not None:
        funcs.start()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        rc = (run_unittest if is_unittest_file(text) else run_pytest)(path, order, only, cells)
    except Exception as exc:  # noqa: BLE001 - 載入失敗照實回報
        print("cell_runner: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        rc = 2
    finally:
        if funcs is not None:
            funcs.stop()
    if a.result:
        Path(a.result).write_text(json.dumps({"rc": rc, "cells": cells.rows}, ensure_ascii=False), encoding="utf-8")
    if funcs is not None:
        Path(a.funcs).write_text(json.dumps(funcs.dump(), ensure_ascii=False), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
