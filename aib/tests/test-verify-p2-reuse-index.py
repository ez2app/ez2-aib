#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""P2 沿用索引:反查表(含 missing/probe)、跨樹取最近一列、必跑判準的順序、lockfile 層。

跑法: python3 tests/test-verify-p2-reuse-index.py; echo $?

## 入口格(逐格內聯,不是「import 下游函式再餵它定位好的輸入」)

    test_90_cli_reverse_entry      真的用 `python3 tools/verify/reuse_index.py reverse`
                                   餵 stdin、讀 stdout JSON —— 生產頂就是這支 CLI。
    test_91_cli_decide_entry       同上,走 decide 模式。

## 還原刀(逐格內聯,每把都在本檔裡真的落下去、跑子行程、比較兩臂)

    刀 A  `decide` 的 `previous-red` 分支拿掉(改成不看 rc)
          ⇒ test_20_previous_red_never_reused 必紅。  ← 派工單指名的第二把刀
    刀 B  `add_row` 只收 open/listdir、不收 missing/probe
          ⇒ test_11_missing_probe_also_in_reverse 必紅。
    刀 C  `lock_fp` 缺欄一律判相等(拿掉「本樹有鎖檔就必跑」)
          ⇒ test_31_lock_absent_with_lockfiles_must_run 必紅。

    刀怎麼落:把產品檔讀進來、在**副本**上做字串替換、丟到暫存目錄,讓子行程 import 副本,
    子行程印出那一格的結論。落刀前先 assert 錨字串**剛好出現一次** ——
    錨被新增的同形碼變成兩次時,替換會落在別處而刀看起來像通過(09-12 的同族)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify" / "reuse_index.py"
sys.path.insert(0, str(TOOL.parent))
sys.path.insert(0, str(ROOT / "tests"))
from aib_tmp_root import aib_tmp_root  # noqa: E402
import reuse_index as ri  # noqa: E402

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def row(test="tests/test-a.py", inputs=None, **kw):
    r = {"test": test, "rc": 0, "tail": "ok", "key": "k-" + test,
         "inputs": inputs if inputs is not None else ["open:./src/a.py:aaa"],
         "input_fp": "fp-" + test, "env_fp": "env", "measured": True,
         "external": False, "why": [], "reused": False,
         "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))}   # 10-04:沒有 UTC 時間的列一律必跑(row-age-unknown)
    r.update(kw)
    return r


def write_rows(dirpath, name, rows):
    p = Path(dirpath) / name
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")
    return p


def run_cli(*args, stdin_text=""):
    # stdin 明給:tests/test-tests-subprocess-stdin-ratchet.py 的棘輪不准繼承呼叫者的 stdin。
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          input=stdin_text, capture_output=True, text=True,
                          cwd=str(ROOT), env=ENV)


def knife_copy(tmpdir, anchor, replacement, expect_hits=1):
    """把 reuse_index.py 複製到 tmpdir 並落一刀。回副本路徑。

    ★ 先數錨出現幾次。**不是** `!= 0` 而是 `== expect_hits`:
      錨變成兩次時替換會落在別處,刀沒落而格子是綠的(09-12「刀錨被從背後打破」)。
    """
    src = TOOL.read_text(encoding="utf-8")
    hits = src.count(anchor)
    # ★ 不用裸 assert:python -O 下它整行消失,刀錨數錯也照樣落刀(a seat 09-26)
    if hits != expect_hits:
        raise AssertionError("刀錨出現 %d 次,期望 %d 次:%r" % (hits, expect_hits, anchor[:60]))
    # 10-04:副本放進一份完整的 tools/verify 複本裡(旁邊有全套同層模組,例:reuse_index 會 import jsonl_safe),
    #   不再單獨一支丟在暫存目錄(test-mutation-probe-placement:副本離開 tools/ 會死在 import ⇒ 斷言恆真)。
    import shutil as _sh
    tree = Path(tmpdir) / "tools" / "verify"
    if not tree.is_dir():
        _sh.copytree(TOOL.parent, tree, ignore=_sh.ignore_patterns("__pycache__"))
    dst = tree / "reuse_index.py"
    dst.write_text(src.replace(anchor, replacement), encoding="utf-8")
    return dst


def probe_with(module_path, snippet):
    """用副本(或正本)跑一小段,回 (rc, stdout)。子行程 import 的是 module_path 那一支。"""
    # ★ 順序是承重的:**後 insert(0) 的排在前面**。刀副本要在最前面,
    #   正本的目錄只是為了讓副本 import 得到 shadow_tracer。第一版寫反了,
    #   兩臂都 import 到正本 ⇒ 刀看起來像沒效(而那正是「刀根本沒落」的樣子)。
    #   10-08:再墊一層本樹的 src/cockpit(最低優先)—— 副本裡的 reuse_index 找畫面文字模組,以前靠家目錄底下剛好有私有倉
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            % (str(ROOT / "src" / "cockpit"), str(TOOL.parent), str(Path(module_path).parent))) + snippet
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, cwd=str(ROOT), env=ENV)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class ReverseIndex(unittest.TestCase):
    def test_10_open_paths_land_in_reverse(self):
        idx = ri.Index()
        idx.add_row(row("tests/test-a.py", ["open:./src/cockpit/aib:sha1",
                                            "open:/opt/py/os.py:sha2"]))
        # repo 底下的 `./x` 進表;倉外的絕對路徑不進(對不到 git diff 吐的路徑)
        self.assertEqual(sorted(idx.reverse), ["src/cockpit/aib"])
        self.assertEqual(idx.tests_reading(["src/cockpit/aib"]),
                         {"src/cockpit/aib": ["tests/test-a.py"]})

    def test_11_missing_probe_also_in_reverse(self):
        """刀 B 的格:探測過一個**不存在**的路徑,這次把它建出來 ⇒ 那支測試也受影響。

        只收「讀過的檔」會整類漏掉,而漏掉的樣子是綠的。
        """
        idx = ri.Index()
        idx.add_row(row("tests/test-m.py", ["missing:./src/newfile.py"]))
        idx.add_row(row("tests/test-p.py", ["probe:./src/other.py:missing"]))
        self.assertEqual(idx.tests_reading(["src/newfile.py"]),
                         {"src/newfile.py": ["tests/test-m.py"]})
        self.assertEqual(idx.tests_reading(["src/other.py"]),
                         {"src/other.py": ["tests/test-p.py"]})

    def test_12_item_path_shapes(self):
        # kind:path:suffix → 路徑是最後一個冒號之前(路徑自己可能含冒號)
        self.assertEqual(ri.item_path("open:./a/b:c:sha"), ("open", "./a/b:c"))
        self.assertEqual(ri.item_path("missing:./a/b"), ("missing", "./a/b"))
        self.assertEqual(ri.item_path("probe:./a:file"), ("probe", "./a"))
        # 沒有路徑語意的三類
        for it in ("argv:[\"x\"]", "selfwrite:<tmp1>/x:sha", "handoffdir:<tmp1>:sha"):
            self.assertIsNone(ri.item_path(it)[1], it)

    def test_13_latest_row_wins_across_trees(self):
        """跨樹:兩份列檔(不同 tree sha),同一支測試取**比較新**的那一列。"""
        tmp = tempfile.mkdtemp(prefix="p2-ri-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        old = write_rows(tmp, "aaa.rows.jsonl", [row("tests/test-a.py", rc=1)])
        new = write_rows(tmp, "bbb.rows.jsonl", [row("tests/test-a.py", rc=0)])
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        idx = ri.build_index([tmp])
        self.assertEqual(idx.latest["tests/test-a.py"]["rc"], 0)
        self.assertEqual(len(idx.files), 2)


    def test_14_same_mtime_final_rows_file_wins(self):
        """同一輪的 `X.round2.rows.jsonl` 與 `X.rows.jsonl` 是同一秒寫出來的。

        只用 mtime 排,誰贏每次都可能不同 —— 而不同的樣子是綠的。
        收尾那一份(沒有 .round)才是那一輪的結論,必須贏。
        """
        tmp = tempfile.mkdtemp(prefix="p2-ri-tie-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        rnd = write_rows(tmp, "abc.round2.rows.jsonl",
                         [row("tests/test-a.py", rc=1)])
        fin = write_rows(tmp, "abc.rows.jsonl",
                         [row("tests/test-a.py", rc=0)])
        os.utime(rnd, (5000, 5000))
        os.utime(fin, (5000, 5000))      # 逐位元組相同的 mtime
        for _ in range(5):               # 不是碰巧對一次
            idx = ri.build_index([tmp])
            self.assertEqual(idx.latest["tests/test-a.py"]["rc"], 0)
            self.assertTrue(idx.files[-1].endswith("abc.rows.jsonl"))


    def test_15_foreign_rows_dropped_unknown_origin_counted(self):
        """★ 列裡的 `./x` 沒有說是**哪一棵樹**的 x。

        影子目錄預設是全機共用的,裡面可能躺著別棵樹(治具倉、別的工作樹)的列
        ⇒ 反查會給出假命中,而假命中與真命中同形。
        兩個來源兩個名字:寫明是別棵樹的丟掉並計數;沒寫的收下但計數並報出來。
        """
        tmp = tempfile.mkdtemp(prefix="p2-ri-org-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        mine = str(Path(ROOT).resolve())
        write_rows(tmp, "x.rows.jsonl", [
            dict(row("tests/test-here.py", ["open:./src/a.py:s"]), repo=mine),
            dict(row("tests/test-elsewhere.py", ["open:./src/a.py:s"]),
                 repo="/somewhere/else"),
            row("tests/test-legacy.py", ["open:./src/a.py:s"]),   # 沒有 repo 欄
        ])
        idx = ri.build_index([tmp], repo=str(ROOT))
        self.assertEqual(idx.foreign_rows, 1)
        self.assertEqual(idx.unknown_origin_rows, 1)
        self.assertEqual(idx.tests_reading(["src/a.py"]),
                         {"src/a.py": ["tests/test-here.py",
                                       "tests/test-legacy.py"]})
        self.assertFalse(idx.rows_default_dir)

    def test_16_default_shadow_dir_is_flagged(self):
        """沒帶 --rows 就讀全機共用的預設目錄。那件事要看得見,不是預設值默默生效。"""
        idx = ri.build_index([], repo=str(ROOT))
        self.assertTrue(idx.rows_default_dir)

    def test_17_stamp_row_writes_both_fields(self):
        tmp = tempfile.mkdtemp(prefix="p2-ri-st-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        out = ri.stamp_row(row("tests/test-a.py"), tmp)
        self.assertEqual(out["repo"], str(Path(tmp).resolve()))
        self.assertEqual(out["lock_fp"], "none")
        self.assertIs(ri.stamp_lock_fp, ri.stamp_row)   # 舊名字還通


class MustRunOrder(unittest.TestCase):
    def _idx(self, r):
        idx = ri.Index()
        idx.add_row(r)
        return idx

    def test_20_previous_red_never_reused(self):
        """★ 派工單指名的刀 B 對應格:上一輪紅的永遠不沿用(設計稿 §5 第 4 條)。"""
        idx = self._idx(row("tests/test-a.py", rc=1))
        verdict, reason, _d = idx.decide(str(ROOT), "tests/test-a.py")
        self.assertEqual(verdict, "must-run")
        self.assertEqual(reason, "previous-red")

    def test_21_unmeasured_and_external_must_run(self):
        for kw, want in ((dict(measured="unmeasured"), "unmeasured"),
                         (dict(external=True), "external"),
                         (dict(timed_out=True), "previous-timeout"),
                         (dict(rc=True), "previous-rc-absent"),
                         (dict(rc=None), "previous-rc-absent")):
            idx = self._idx(row("tests/test-a.py", **kw))
            v, reason, _d = idx.decide(str(ROOT), "tests/test-a.py")
            self.assertEqual((v, reason), ("must-run", want), kw)

    def test_22_no_previous_must_run(self):
        idx = ri.Index()
        idx.add_row(row("tests/test-other.py"))
        self.assertEqual(idx.decide(str(ROOT), "tests/test-a.py")[:2],
                         ("must-run", "no-previous"))

    def test_23_test_modified_must_run(self):
        idx = self._idx(row("tests/test-a.py"))
        self.assertEqual(
            idx.decide(str(ROOT), "tests/test-a.py",
                       changed=["tests/test-a.py"])[:2],
            ("must-run", "test-modified"))

    def test_24_changed_input_must_run(self):
        idx = self._idx(row("tests/test-a.py", ["open:./src/cockpit/aib:sha"]))
        v, reason, detail = idx.decide(str(ROOT), "tests/test-a.py",
                                       changed=["src/cockpit/aib"])
        self.assertEqual((v, reason), ("must-run", "input-changed"))
        self.assertIn("src/cockpit/aib", detail)

    def test_25_degraded_index_is_all_must_run(self):
        """索引量不到 ⇒ 每一支都必跑。回一份窄的索引比回錯誤更貴。"""
        idx = ri.build_index([str(Path(aib_tmp_root()) / "definitely-not-here-p2")])
        self.assertTrue(idx.degraded)
        self.assertEqual(idx.decide(str(ROOT), "tests/test-a.py")[:2],
                         ("must-run", "index-degraded"))


class Shrink(unittest.TestCase):
    """「少讀了東西」與「檔內容改了」是兩個理由,不得貼同一個標籤。"""

    def _tree(self):
        tmp = tempfile.mkdtemp(prefix="p2-shrink-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        (Path(tmp) / "a.txt").write_text("one\n", encoding="utf-8")
        return tmp

    def _row_for(self, tmp, inputs):
        import shadow_tracer as st
        items, fp = st.recompute_input_fp(tmp, inputs)
        env = st.child_env(Path(tmp), Path("/tmp/.ez2-verify-reuse-probe"))
        env_fp, _raw = st.env_fingerprint(env, repo=tmp)
        r = row("tests/test-a.py", inputs=items)
        r["input_fp"], r["env_fp"] = fp, env_fp
        r["key"] = st.make_key("tests/test-a.py", fp, env_fp)
        r["lock_fp"] = ri.lock_fp(tmp)
        return r

    def test_26_content_change_is_not_called_a_shrink(self):
        """★ 改了檔的內容 ⇒ 理由要是 input-fp-changed,不是 fingerprint-shrink。

        整串比(`open:X:舊sha` vs `open:X:新sha`)的話,每一次內容改動都會被貼上
        「縮短」的標籤,而縮短要人去查的是「誰把量測弄不見了」—— 查錯方向。
        """
        tmp = self._tree()
        idx = ri.Index()
        idx.add_row(self._row_for(tmp, ["open:./a.txt:placeholder"]))
        self.assertEqual(idx.decide(tmp, "tests/test-a.py")[:2],
                         ("reuse", "fingerprint-match"), "基準這一步就該准沿用")
        (Path(tmp) / "a.txt").write_text("two\n", encoding="utf-8")
        self.assertEqual(idx.decide(tmp, "tests/test-a.py")[:2],
                         ("must-run", "input-fp-changed"))

    def test_27_a_dropped_input_is_a_shrink(self):
        """量測真的少了一項(重算把它濾掉)⇒ 這才叫縮短。"""
        tmp = self._tree()
        base = self._row_for(tmp, ["open:./a.txt:placeholder"])
        # 手動塞一項**重算一定會濾掉**的衍生檔(is_derived:__pycache__/*.pyc)
        base = dict(base)
        base["inputs"] = list(base["inputs"]) + ["open:./__pycache__/x.pyc:deadbeef"]
        idx = ri.Index()
        idx.add_row(base)
        verdict, reason, detail = idx.decide(tmp, "tests/test-a.py")
        self.assertEqual((verdict, reason), ("must-run", "fingerprint-shrink"))
        self.assertIn("x.pyc", detail)


class LockLayer(unittest.TestCase):
    def test_30_no_lockfiles_is_the_none_sentinel(self):
        tmp = tempfile.mkdtemp(prefix="p2-lock-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(ri.lock_fp(tmp), "none")
        (Path(tmp) / "package-lock.json").write_text("{}", encoding="utf-8")
        self.assertNotEqual(ri.lock_fp(tmp), "none")

    def test_31_lock_absent_with_lockfiles_must_run(self):
        """刀 C 的格:本樹**有**鎖檔而舊列沒有 lock_fp 欄 ⇒ 當時是什麼不知道 ⇒ 必跑一次。"""
        tmp = tempfile.mkdtemp(prefix="p2-lock2-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        (Path(tmp) / "package-lock.json").write_text('{"v":1}', encoding="utf-8")
        idx = ri.Index()
        # inputs 留空:重算指紋一定對得上,才逼得出「只剩鎖檔這一層在擋」
        r = row("tests/test-a.py", inputs=[])
        import shadow_tracer as st
        _items, fp = st.recompute_input_fp(tmp, [])
        env = st.child_env(Path(tmp), Path("/tmp/.ez2-verify-reuse-probe"))
        env_fp, _raw = st.env_fingerprint(env, repo=tmp)
        r["input_fp"], r["env_fp"] = fp, env_fp
        r["key"] = st.make_key("tests/test-a.py", fp, env_fp)
        idx.add_row(r)
        self.assertEqual(idx.decide(tmp, "tests/test-a.py")[:2],
                         ("must-run", "lock-fp-changed"))
        # 同一列補上正確的 lock_fp ⇒ 這一層不再擋,判 reuse(證明剛剛擋的真的是這一層)
        r2 = ri.stamp_row(r, tmp)
        idx2 = ri.Index()
        idx2.add_row(r2)
        self.assertEqual(idx2.decide(tmp, "tests/test-a.py")[:2],
                         ("reuse", "fingerprint-match"))


class Entry(unittest.TestCase):
    """入口格:真的跑生產頂 CLI,不是 import 下游函式。"""

    def test_90_cli_reverse_entry(self):
        tmp = tempfile.mkdtemp(prefix="p2-cli-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        write_rows(tmp, "t1.rows.jsonl",
                   [row("tests/test-a.py", ["open:./src/cockpit/aib:sha"])])
        r = run_cli("reverse", "--repo", str(ROOT), "--rows", tmp,
                    stdin_text="src/cockpit/aib\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["tests"], ["tests/test-a.py"])
        self.assertEqual(got["reverse_hits"]["src/cockpit/aib"], ["tests/test-a.py"])

    def test_91_cli_decide_entry(self):
        tmp = tempfile.mkdtemp(prefix="p2-cli2-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        write_rows(tmp, "t1.rows.jsonl", [row("tests/test-a.py", rc=1)])
        r = run_cli("decide", "--repo", str(ROOT), "--rows", tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["counts"]["must-run"], 1)
        self.assertEqual(got["decisions"][0]["reason"], "previous-red")

    def test_92_empty_rows_dir_is_degraded_rc3(self):
        tmp = tempfile.mkdtemp(prefix="p2-cli3-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        r = run_cli("stats", "--repo", str(ROOT), "--rows", tmp)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["degraded"], "no-rows-files")


class Knives(unittest.TestCase):
    """刀逐格內聯:在副本上落刀,子行程跑同一格,兩臂結論必須相反。"""

    def test_80_knife_a_previous_red_branch_removed(self):
        tmp = tempfile.mkdtemp(prefix="p2-knifeA-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        anchor = '        if rc != 0:\n            return "must-run", "previous-red", "rc=%d" % rc\n'
        cut = knife_copy(tmp, anchor, '        if False:\n            return "must-run", "previous-red", "rc=%d" % rc\n')
        snippet = (
            "import reuse_index as ri, json\n"
            "idx = ri.Index()\n"
            "idx.add_row({'test':'t','rc':1,'measured':True,'external':False,"
            "'inputs':[],'input_fp':'x','env_fp':'y','key':'k'})\n"
            "print(json.dumps(idx.decide(%r,'t')[:2]))\n" % str(ROOT))
        rc_cut, out_cut = probe_with(cut, snippet)
        self.assertEqual(rc_cut, 0, out_cut)
        self.assertNotIn("previous-red", out_cut,
                         "刀落下去了,previous-red 卻還在 —— 刀沒落或落錯地方")
        # 對照臂:正本必須給 previous-red(沒有對照臂就分不出「刀有效」與「本來就不會紅」)
        rc_ok, out_ok = probe_with(TOOL, snippet)
        self.assertEqual(rc_ok, 0, out_ok)
        self.assertIn("previous-red", out_ok)

    def test_81_knife_b_reverse_drops_missing_and_probe(self):
        tmp = tempfile.mkdtemp(prefix="p2-knifeB-", dir=aib_tmp_root())
        self.addCleanup(shutil.rmtree, tmp, True)
        anchor = "            self.reverse.setdefault(rel, set()).add(test)\n"
        cut = knife_copy(
            tmp, anchor,
            "            if kind in ('open', 'listdir'):\n"
            "                self.reverse.setdefault(rel, set()).add(test)\n")
        snippet = (
            "import reuse_index as ri, json\n"
            "idx = ri.Index()\n"
            "idx.add_row({'test':'t','rc':0,'measured':True,'external':False,"
            "'inputs':['missing:./src/new.py']})\n"
            "print(json.dumps(idx.tests_reading(['src/new.py'])))\n")
        rc_cut, out_cut = probe_with(cut, snippet)
        self.assertEqual(rc_cut, 0, out_cut)
        self.assertEqual(out_cut.strip(), "{}",
                         "刀落下去了,missing 卻還在反查表裡")
        rc_ok, out_ok = probe_with(TOOL, snippet)
        self.assertEqual(rc_ok, 0, out_ok)
        self.assertIn("src/new.py", out_ok)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main(verbosity=2)
