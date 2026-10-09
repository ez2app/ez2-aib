#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""整套對答案(10-07,給所有 aib 用戶;`aib ship crosscheck`)。

aib 一次只跑一個測試檔。有些專案的測試會互相影響(共用資料庫或全域狀態、整套才跑一次的前置設定、pytest 整場共用的
fixture、執行順序)⇒「單獨跑綠、整套跑紅」或反過來。aib 的綠燈只代表「這支單獨跑是綠的」,
而使用者的 CI 多半是整套一起跑 —— 兩邊不一致時,要讓人一眼看到,不能讓 aib 的綠被當成 CI 會綠。

做法:先用 aib test 把全部測試跑一遍(能沿用就沿用,所以便宜;跑完紀錄池裡每支最新一列就是「這棵樹單支跑的結果」),
再用專案平常的方式把整套跑一次,逐檔比紅綠。
- pytest:`-o junit_family=xunit1 --junitxml`(每格帶 file 屬性);一個檔裡任何一格 failure/error ⇒ 那個檔整套是紅。
- jest:`--json --outputFile`(每個測試檔一個結果)。
- vitest:`--reporter=json`(與 jest 同格式);mocha:`--reporter json --reporter-option output=…`(每格帶 file)。
- 其他(node:test、aib 自己的倉這種一支一支執行的腳本)⇒ 不比,照實說比不了(結束碼 4)。
結束碼:0 一致;1 有不一致;4 比不了。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def latest_verdicts(pool):
    """{測試: "green" | "red" | "skipped"}:紀錄池裡每支最新一列(time_utc 最大)的結果。封存標記、隔離墓碑、作廢列、沒有 rc 的列不算。"""
    out = {}
    try:
        fh = open(pool, encoding="utf-8", errors="replace")
    except OSError:
        return {}
    best = {}
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("test") or "rc" not in r or r.get("void"):
                continue
            why = r.get("why") or []
            if "archived-history" in why or "quarantined-bad-row" in why:
                continue
            t, when = str(r["test"]), str(r.get("time_utc") or "")
            if when >= best.get(t, ""):
                best[t] = when
                # rc 5 = pytest「一格都沒跑」(整個檔被跳過);標了沒驗的同義 —— 都不是綠也不是紅(Flask test_async 實踩)
                out[t] = "skipped" if (r.get("rc") == 5 or r.get("unverified")) else ("green" if r.get("rc") == 0 else "red")
    return out


def framework(repo, tests):
    """("pytest", python) / ("jest", 執行檔) / (None, 原因)。"""
    import runner
    import shadow_tracer as st
    sys.path.insert(0, str(HERE.parent / "release"))
    import ship_profile
    if ship_profile.declared_tests(str(repo))[1]:
        return None, "declared"           # 用設定檔宣告的命令跑的,沒有另一種「整套」跑法可比
    if tests and all(t.endswith(".py") for t in tests):
        if (Path(repo) / "tests" / "aib_testlib.py").exists() and any(Path(t).name.startswith("test-") for t in tests):
            return None, "aib-scripts"      # aib 自己的倉:每支是獨立腳本,「整套」就是一支一支跑,沒有另一種跑法可比
        py = st.test_python(Path(repo))
        if st.python_id(py)[1]:
            return "pytest", py
        return None, "no-pytest"
    if tests and all(Path(t).suffix in runner.JS_SUFFIXES for t in tests):
        name, exe = runner.js_runner(Path(repo))
        if name in ("jest", "vitest", "mocha") and exe:
            return name, exe
        return None, "js-%s" % (name or "node")
    return None, "mixed"


def pytest_suite(repo, py, tests, timeout=3600):
    """({測試檔: "green" | "red"} 或 None, 輸出最後幾行);整套跑不起來(沒有報表)⇒ None。"""
    with tempfile.TemporaryDirectory(prefix="aib-crosscheck-") as td:
        xml = Path(td) / "junit.xml"
        p = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
                            "--junitxml=%s" % xml] + list(tests), cwd=str(repo), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return parse_junit(xml), _tail(p)


def parse_junit(path):
    """pytest xunit1 報表 ⇒ {測試檔: "green" | "red" | "skipped"};讀不到 ⇒ None。"""
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError):
        return None
    out = {}
    for case in root.iter("testcase"):
        f = case.get("file")
        if not f:
            continue
        f = os.path.normpath(f).replace(os.sep, "/")
        if case.find("failure") is not None or case.find("error") is not None:
            out[f] = "red"
        elif case.find("skipped") is not None:
            out.setdefault(f, "skipped")          # 整個檔每格都跳過 ⇒ 留在 skipped;有一格真的跑過 ⇒ 下面改成 green
        elif out.get(f) != "red":
            out[f] = "green"
    return out


def jest_suite(repo, exe, tests, timeout=3600):
    with tempfile.TemporaryDirectory(prefix="aib-crosscheck-") as td:
        js = Path(td) / "jest.json"
        p = subprocess.run([exe, "--coverage=false", "--no-watchman", "--json", "--outputFile=%s" % js, "--runTestsByPath"]
                           + list(tests), cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        return parse_jest(js, repo), _tail(p)


def parse_jest(path, repo):
    """jest --json 報表 ⇒ {測試檔: "green" | "red" | "skipped"};讀不到 ⇒ None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for r in data.get("testResults") or []:
        name = r.get("name") or ""
        rel = os.path.relpath(name, str(Path(repo).resolve())).replace(os.sep, "/") if os.path.isabs(name) else name
        res = r.get("assertionResults") or []
        if r.get("status") == "failed":
            out[rel] = "red"
        elif res and all(x.get("status") in ("pending", "skipped", "todo", "disabled") for x in res):
            out[rel] = "skipped"
        else:
            out[rel] = "green"
    return out


def vitest_suite(repo, exe, tests, timeout=3600):
    """vitest 的 json 報表與 jest 同一種格式。"""
    with tempfile.TemporaryDirectory(prefix="aib-crosscheck-") as td:
        js = Path(td) / "vitest.json"
        p = subprocess.run([exe, "run", "--pool=forks", "--coverage.enabled=false", "--reporter=json", "--outputFile=%s" % js]
                           + list(tests), cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        return parse_jest(js, repo), _tail(p)


def mocha_suite(repo, exe, tests, timeout=3600):
    """mocha:json 報表寫到檔案(測試自己往畫面印的東西不會混進來)。"""
    with tempfile.TemporaryDirectory(prefix="aib-crosscheck-") as td:
        js = Path(td) / "mocha.json"
        p = subprocess.run([exe, "--reporter", "json", "--reporter-option", "output=%s" % js] + list(tests), cwd=str(repo),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        return parse_mocha(js, repo), _tail(p)


def parse_mocha(path, repo):
    """mocha json 報表 ⇒ {測試檔: "green" | "red" | "skipped"};每格帶 file。讀不到 ⇒ None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    root = str(Path(repo).resolve())

    def rel(f):
        f = str(f or "")
        return os.path.relpath(f, root).replace(os.sep, "/") if os.path.isabs(f) else f
    out = {}
    for t in data.get("pending") or []:
        out.setdefault(rel(t.get("file")), "skipped")
    for t in data.get("passes") or []:
        if out.get(rel(t.get("file"))) != "red":
            out[rel(t.get("file"))] = "green"
    for t in data.get("failures") or []:
        out[rel(t.get("file"))] = "red"
    out.pop("", None)
    return out


SUITES = {"pytest": pytest_suite, "jest": jest_suite, "vitest": vitest_suite, "mocha": mocha_suite}


def _tail(p, n=8):
    text = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    return "\n".join(text[-n:])


def compare(aib, suite, tests):
    """[(測試, aib 單支, 整套)],只列不一致的;任一邊沒有結果的另外回。"""
    diff, missing = [], []
    for t in tests:
        a, s = aib.get(t), suite.get(t)
        if a is None or s is None:
            missing.append((t, a, s))
        elif a != s:
            diff.append((t, a, s))
    return diff, missing


def record(repo, n, diff, missing, path=None):
    path = Path(path) if path else Path.home() / ".ez2-aib" / "crosscheck.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"repo": str(Path(repo).resolve()), "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "tests": n, "diff": diff, "missing": [m[0] for m in missing]}, ensure_ascii=False) + "\n")
    except OSError:
        pass
