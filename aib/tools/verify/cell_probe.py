#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""量一支測試的「格」(10-06,開發檔 §13 C1;aib test --cells)。只量,不寫沿用紀錄、不改任何判斷。

跑兩次:
  1. 照原順序,在追蹤下跑(cell_runner 寫格界線)+ 記每格執行過的函式 ⇒
     每格:秒數、結果、自己讀的檔(扣掉「共用」:第一格之前與格與格之間讀的)、執行過的函式;
  2. 打亂順序再跑一次 ⇒ 哪幾格結果變了(= 暗中相依)。
另外看原始碼:setUpClass / setUpModule / 非單格範圍的 pytest fixture ⇒ 有共用狀態。
量的結果存 <紀錄池目錄>/cells/<測試>.json,給之後的估算(C2)用。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
import shadow_tracer as st  # noqa: E402

STATE_RE = re.compile(r"\b(setUpClass|tearDownClass|setUpModule|tearDownModule)\b"
                      r"|@pytest\.fixture\([^)]*scope\s*=\s*[\"'](module|session|class|package)[\"']")


def _path_of(item):
    """'open:./x.py:sha' 這類輸入字串 → 路徑(認不得 ⇒ 整串)。"""
    parts = str(item).split(":")
    return parts[1] if len(parts) >= 3 else str(item)


def split_by_cells(events):
    """事件 → {"": 共用(第一格之前、格與格之間), 格: [事件]} 與格的順序。只認格界線,其餘照順序歸到當下那一格。"""
    segs, order, cur = {"": []}, [], ""
    for ev in events:
        if ev.get("ch") == "cell":
            if ev.get("edge") == "start":
                cur = ev.get("cell") or ""
                segs.setdefault(cur, [])
                order.append(cur)
            elif ev.get("edge") in ("end", "setup-start"):
                cur = ""
            continue
        segs.setdefault(cur, []).append(ev)
    return segs, order


def _inputs(events, repo, hooked=False):
    """hooked:整支的追蹤確實掛上了(掛上的記號只在第一格之前出現一次,切開後每格看不到 ⇒ 不因此判量不到)。"""
    items, _fp, unmeasured, external, why = st.inputs_from_events(events, repo=str(repo))
    why = [w for w in (why or []) if not (hooked and w == "no-hook")]
    # 只留倉裡的路徑(./開頭):倉外的(上層目錄、python 安裝)不會被這個倉的改動碰到,算格用不到
    paths = {_path_of(i) for i in items}
    return sorted(p for p in paths if p.startswith("./")), bool((unmeasured or external) and why), why[:3]


def _run(repo, test, env, order="given", funcs=None):
    trace = Path(tempfile.mkstemp(prefix="aib-cells-", suffix=".jsonl")[1])
    result = Path(tempfile.mkstemp(prefix="aib-cells-", suffix=".json")[1])
    try:
        tenv = st.child_env(Path(repo), trace, env)
        cmd = [st.test_python(str(repo), env), str(HERE / "cell_runner.py"), "--order", order, "--result", str(result)]
        if funcs:
            cmd += ["--funcs", str(funcs)]
        p = subprocess.run(cmd + [str(Path(repo) / test)], cwd=str(repo), env=tenv, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=3600)
        try:
            res = json.loads(result.read_text(encoding="utf-8") or "{}")
        except ValueError:
            res = {}
        return p.returncode, res.get("cells", []), st.load_trace(trace), (p.stdout + p.stderr)[-2000:]
    finally:
        # 10-09:追蹤檔旁邊還有 .ldp(安全網紀錄)、.t0(起跑標記),以前每格量一次留兩個在使用者的暫存區
        for f in (trace, result, Path(str(trace) + ".ldp"), Path(str(trace) + ".t0")):
            try:
                f.unlink()
            except OSError:
                pass


def probe(repo, test, shuffle_seed="1", environ=None):
    repo = Path(repo)
    home = Path(tempfile.mkdtemp(prefix="aib-test-home-cells-", dir=runner.TMP_ROOT))
    ftmp = Path(tempfile.mkstemp(prefix="aib-cells-funcs-", suffix=".json")[1])
    try:
        env = runner.isolated_test_env(home, repo)
        if environ:
            env.update(environ)
        rc, rows, events, tail = _run(repo, test, env, funcs=ftmp)
        try:
            funcs = json.loads(ftmp.read_text(encoding="utf-8") or "{}")
        except ValueError:
            funcs = {}
        segs, order = split_by_cells(events)
        shared, shared_unmeasured, shared_why = _inputs(segs.get("", []), repo)
        hooked = "no-hook" not in shared_why
        cells = []
        by_name = {r["cell"]: r for r in rows}
        for name in order:
            own, unm, why = _inputs(segs.get(name, []), repo, hooked)
            r = by_name.get(name, {})
            cells.append({"cell": name, "seconds": r.get("seconds"), "outcome": r.get("outcome"),
                          "own": [x for x in own if x not in shared], "unmeasured": unm, "why": why,
                          "funcs": funcs.get(name, [])})
        rc2, rows2, _ev2, _tail2 = _run(repo, test, env, order="shuffle:%s" % shuffle_seed)
        first = {r["cell"]: r["outcome"] for r in rows}
        moved = sorted(r["cell"] for r in rows2 if first.get(r["cell"]) not in (None, r["outcome"]))
        try:
            src = (repo / test).read_text(encoding="utf-8", errors="replace")
        except OSError:
            src = ""
        static = sorted({m.group(1) or ("fixture-scope-" + m.group(2)) for m in STATE_RE.finditer(src)})
        total = sum(c["seconds"] or 0 for c in cells)
        out = {"test": test, "rc": rc, "cells": cells, "shared": shared, "shared_unmeasured": shared_unmeasured,
               "shared_why": shared_why, "order_dependent": moved, "static_state": static, "seconds": round(total, 3),
               "separable": bool(cells) and not moved and not static and not shared_unmeasured
               and not any(c["unmeasured"] for c in cells),
               "tail": tail if rc not in (0, 1) else ""}
        save(repo, test, out)
        return out
    finally:
        shutil.rmtree(home, ignore_errors=True)
        try:
            ftmp.unlink()
        except OSError:
            pass


def save(repo, test, out):
    try:
        d = Path(runner._pool_file(Path(repo)).parent) / "cells"
        d.mkdir(parents=True, exist_ok=True)
        (d / (hashlib.sha256(test.encode()).hexdigest()[:16] + ".json")).write_text(
            json.dumps(out, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def affected(out, changed_paths, by="files"):
    """改了這些路徑(或函式所在的檔)⇒ 會碰到哪幾格;碰到共用的 ⇒ 全部格。"""
    changed = {p.lstrip("./") for p in changed_paths}
    names = [c["cell"] for c in out["cells"]]
    if by == "files":
        if any(s.lstrip("./") in changed for s in out["shared"]):
            return names
        return [c["cell"] for c in out["cells"] if any(o.lstrip("./") in changed for o in c["own"])]
    return [c["cell"] for c in out["cells"] if any(f.split(":", 1)[0] in changed for f in c["funcs"])]
