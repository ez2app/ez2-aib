#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影子續跑「該重跑卻拿到舊綠」一整類(abb89 稽核 10-14,A1–A7、D2、D3、F6)。全部治具在暫存目錄,不碰真池。

A1 接續(--resume)也要過沿用的關:期限、環境指紋、作廢列、嚴格/總開關/always-run、池裡更新的紅
   a1_* 單元(resume_blocked);a1_wired 端到端:日誌列過期 ⇒ 不接續、照跑
A2 停用跨主機時,別台的紅照樣擋(只丟別台的綠)
A3 被作廢的綠不推出去;本機作廢列推出去(縮成只打本機);別台收作廢列只認「簽署者自己的列」
A4 合池:寫一半的最後一行 ⇒ 墓碑(不靜默丟);沒有 time_utc 的墓碑不排到最前面;搬舊池時拿著它的寫列鎖
A5 「最新一列」照列時間,不照檔案 mtime
A6 墓碑帶 time_utc;沒有 time_utc 的舊墓碑不被作廢列吃掉
A7 列記下寫列機器的時區:時區不同 ⇒ 必跑;跨日判斷算上那台的日界
D2 自動壓縮:不給路徑不壓;分份子行程不壓;被租約擋下的那一輪不壓;只壓本專案的池
D3 git 沒回應(逾時)⇒ 重試、說「身分認不出」而不是「第一次跑」;新版的作廢條件舊版看不懂 ⇒ 放大成全作廢,不永久降級
F6 寫列前補換行(半列不黏住下一列);本專案池有壞行 ⇒ 自動隔離、重讀(不再每輪全跑)
"""
import contextlib
import datetime as dt
import fcntl
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "verify"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import reuse_index as ri  # noqa: E402
import shadow_tracer as st  # noqa: E402

T = "tests/test-a.py"


def _utc(sec_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - sec_ago))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="abb89-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.shadow = Path(self.tmp) / "shadow"
        self.shadow.mkdir()
        env = mock.patch.dict(os.environ, {"EZ2_VERIFY_SHADOW_DIR": str(self.shadow), "AIB_HOST_ID": "me-host"})
        env.start()
        self.addCleanup(env.stop)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        items, fp = st.recompute_input_fp(str(self.repo), ["open:./a.txt:placeholder"])
        cenv = st.child_env(self.repo, self.repo / "probe-trace")
        self.env_fp = st.env_fingerprint(cenv, repo=str(self.repo))[0]
        self.items, self.fp = items, fp

    def row(self, ago=3600, env_fp=None, **kw):
        env_fp = env_fp or self.env_fp
        r = {"test": T, "rc": 0, "tail": "ok", "inputs": list(self.items), "input_fp": self.fp,
             "env_fp": env_fp, "key": st.make_key(T, self.fp, env_fp), "measured": True,
             "external": False, "why": [], "lock_fp": ri.lock_fp(str(self.repo)), "time_utc": _utc(ago)}
        r.update(kw)
        if "key" not in kw:
            r["key"] = st.make_key(r["test"], self.fp, env_fp)
        return r

    def write(self, path, *objs):
        Path(path).write_text("".join((o if isinstance(o, str) else json.dumps(o)) + "\n" for o in objs),
                              encoding="utf-8")
        return Path(path)


# ---------------------------------------------------------------- A1
class A1Resume(_Base):
    def setUp(self):
        super().setUp()
        import test_entry
        self.te = test_entry
        self.now = dt.datetime.now(dt.timezone.utc)

    def jrow(self, ago=60, env_fp=None, **kw):
        r = {"test": T, "rc": 0, "tree": "t", "fresh": False, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "time_utc": _utc(ago), "env_fp": env_fp if env_fp is not None else "E"}
        r.update(kw)
        return r

    def blocked(self, row, idx=None, **kw):
        args = dict(strict=False, no_reuse=False, always_run=False, env_fp_now="E", idx=idx or ri.Index(),
                    now_utc=self.now, max_age_h=self.te.RESUME_MAX_AGE_H)
        args.update(kw)
        return self.te.resume_blocked(T, row, **args)

    def test_a1_fresh_matching_row_resumes(self):
        self.assertIsNone(self.blocked(self.jrow()))

    def test_a1_expired_journal_row_does_not_resume(self):
        self.assertEqual(self.blocked(self.jrow(ago=3 * 86400)), "resume-expired",
                         "同一顆 HEAD 幾天後還拿日誌裡的舊綠 ⇒ 紅藏成綠")

    def test_a1_old_journal_row_without_time_does_not_resume(self):
        r = self.jrow()
        del r["time_utc"]
        self.assertEqual(self.blocked(r), "resume-age-unknown")

    def test_a1_env_change_does_not_resume(self):
        self.assertEqual(self.blocked(self.jrow(env_fp="OTHER")), "resume-env-changed")
        self.assertEqual(self.blocked(self.jrow(), env_fp_now=None), "resume-env-changed", "環境指紋算不出來 ⇒ 不接續")

    def test_a1_void_written_after_the_journal_row_blocks(self):
        idx = ri.Index()
        idx.voids = [ri._parse_void({"void": {"test": T, "green_only": True}, "before": "x", "before_utc": _utc(0)})]
        self.assertEqual(self.blocked(self.jrow(ago=60), idx=idx), "resume-voided")
        idx.voids = [ri._parse_void({"void": {"all": True}, "before": "x", "before_utc": _utc(0)})]
        self.assertEqual(self.blocked(self.jrow(ago=60), idx=idx), "resume-voided")
        idx.voids = [ri._parse_void({"void": {"test": "tests/other.py"}, "before": "x", "before_utc": _utc(0)})]
        self.assertIsNone(self.blocked(self.jrow(ago=60), idx=idx), "別支的作廢不影響這支")

    def test_a1_strict_no_reuse_always_run_block(self):
        self.assertEqual(self.blocked(self.jrow(), strict=True), "strict")
        self.assertEqual(self.blocked(self.jrow(), no_reuse=True), "no-reuse-env")
        self.assertEqual(self.blocked(self.jrow(), always_run=True), "always-run")

    def test_a1_newer_red_in_pool_blocks(self):
        idx = ri.Index()
        idx.add_row(self.row(ago=10, rc=1))
        self.assertEqual(self.blocked(self.jrow(ago=60), idx=idx), "resume-newer-red")
        idx = ri.Index()
        idx.add_row(self.row(ago=600, rc=1))
        self.assertIsNone(self.blocked(self.jrow(ago=60), idx=idx), "更早的紅(日誌列之前)不擋")

    def test_a1_wired_expired_journal_row_reruns(self):
        spec = importlib.util.spec_from_file_location("entry0929", ROOT / "tests" / "test-aib-test-entry-0929.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        td = Path(self.tmp) / "e2e"
        td.mkdir()
        fx = m._fixture(td)
        env = m._env(td)
        argv = [sys.executable, str(fx / m.ENTRY_REL), "--fresh", "--resume", "b1", "tests/test-ok.py"]
        first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
        j = td / "shadow" / "batch-b1.jsonl"
        rows = [json.loads(x) for x in j.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertTrue(rows and rows[-1].get("time_utc") and rows[-1].get("env_fp"), "日誌列要記 UTC 時間與環境指紋")
        again = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
        self.assertIn("↻ tests/test-ok.py", again.stderr, "剛跑過、什麼都沒變 ⇒ 照樣接續:" + again.stderr[-2000:])
        old = dict(rows[-1], time_utc=_utc(3 * 86400))
        j.write_text(json.dumps(old) + "\n", encoding="utf-8")
        late = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=300)
        self.assertNotIn("↻ tests/test-ok.py", late.stderr, "日誌列已過期還接續:" + late.stderr[-2000:])
        self.assertIn("▶ tests/test-ok.py", late.stderr, late.stderr[-2000:])
        self.assertIn("resume-expired", late.stderr, "要說出為什麼不接續")


# ---------------------------------------------------------------- A2 / A5 / A6
class PoolOrder(_Base):
    def test_a2_foreign_red_still_blocks_when_cross_host_disabled(self):
        (self.shadow / ri.CROSS_HOST_FLAG).write_text("{}", encoding="utf-8")
        self.write(self.shadow / "verify-x.rows.jsonl",
                   self.row(ago=7200, host_id="me-host"),
                   self.row(ago=60, host_id="fish", env_fp="fish-env", rc=1))
        idx = ri.build_index([str(self.shadow)], repo=str(self.repo))
        self.assertTrue(idx.local_only)
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "previous-red"),
                         "停用跨主機時把別台的紅一起丟掉 ⇒ 本機的舊綠變成可沿用")

    def test_a2_foreign_green_still_dropped(self):
        (self.shadow / ri.CROSS_HOST_FLAG).write_text("{}", encoding="utf-8")
        self.write(self.shadow / "verify-x.rows.jsonl", self.row(ago=60, host_id="fish"))
        idx = ri.build_index([str(self.shadow)], repo=str(self.repo))
        self.assertEqual(idx.decide(str(self.repo), T)[0], "must-run")
        self.assertEqual(idx.foreign_host_rows, 1)

    def test_a5_newest_row_by_row_time_not_file_mtime(self):
        new = self.write(self.shadow / "verify-new.rows.jsonl", self.row(ago=60, env_fp="other-env", rc=1))
        old = self.write(self.shadow / "verify-old.rows.jsonl", self.row(ago=7200))
        os.utime(new, (time.time() - 86400, time.time() - 86400))
        os.utime(old, None)          # 隔離/拉進來的檔:整檔 mtime 變最新
        idx = ri.build_index([str(self.shadow)], repo=str(self.repo))
        self.assertEqual(idx.latest[T]["rc"], 1, "最新一列要是時間最新的那一列(紅),不是 mtime 最新的檔")
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "previous-red"))

    def test_a5_rows_without_utc_order_conservatively(self):
        green = self.row()
        del green["time_utc"]
        red = dict(self.row(rc=1))
        del red["time_utc"]
        self.assertLess(ri._order_key(green), ri._order_key(self.row(ago=10)))
        self.assertGreater(ri._order_key(red), ri._order_key(self.row(ago=0)))

    def test_a6_tombstone_survives_earlier_void(self):
        f = self.shadow / "verify-x.rows.jsonl"
        self.write(f, {"void": {"test": T}, "before": "2000-01-01 00:00:00", "before_utc": _utc(7200)},
                   self.row(ago=3600), '{"test": "%s", "rc": 1, "inp' % T)
        rep = ri.quarantine(f, apply=True)
        self.assertTrue(rep["applied"])
        tomb = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(tomb.get("time_utc"), "墓碑要帶 UTC 時間")
        idx = ri.build_index([str(self.shadow)], repo=str(self.repo))
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "unmeasured"),
                         "更早的作廢列吃掉墓碑 ⇒ 舊綠又變最新")

    def test_a6_legacy_tombstone_without_utc_is_not_voided(self):
        v = ri._parse_void({"void": {"all": True}, "before": "2999-01-01 00:00:00", "before_utc": "2999-01-01T00:00:00Z"})
        tomb = {"test": T, "rc": -1, "inputs": [], "measured": "unmeasured", "why": ["quarantined-bad-row"],
                "time": "2026-10-01 10:00"}
        self.assertFalse(ri._void_hits(v, tomb))
        self.assertTrue(ri._void_hits(v, self.row()), "綠列照樣被作廢")


# ---------------------------------------------------------------- A7
class A7Timezone(_Base):
    def test_a7_other_timezone_must_run(self):
        idx = ri.Index()
        idx.add_row(self.row(tz="XXX|XXX|-50400", tz_off_s=50400))
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "tz-changed"))
        idx = ri.Index()
        idx.add_row(self.row(tz=ri.local_tz()[0], tz_off_s=ri.local_tz()[1]))
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("reuse", "fingerprint-match"))

    def test_a7_writer_day_boundary_counts(self):
        old = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            since = dt.datetime(2026, 10, 14, 9, 30, tzinfo=dt.timezone.utc)
            now = dt.datetime(2026, 10, 14, 10, 30, tzinfo=dt.timezone.utc)
            day = {dt.date(2026, 10, 15)}
            self.assertEqual(ri.crossed_dates(day, since, now), [], "只看本機(UTC)看不到")
            self.assertEqual(ri.crossed_dates(day, since, now, extra_offsets=ri._row_offsets({"tz_off_s": 14 * 3600})),
                             [dt.date(2026, 10, 15)], "寫列那台(UTC+14)在這段時間跨過了 10-15")
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_a7_stamp_row_records_tz(self):
        out = ri.stamp_row({"test": T}, str(self.repo), lock_now="none")
        self.assertEqual((out["tz"], out["tz_off_s"]), ri.local_tz())

    def test_a7_why_text_in_every_locale(self):
        import test_entry
        for code in ("tz-changed", "identity-unknown"):
            for loc in ("zh-TW", "en", "ja", "ko"):
                with self.subTest(code=code, loc=loc), mock.patch.object(test_entry, "LOC", loc):
                    self.assertNotEqual(test_entry._why_text(code, "x"), code)


# ---------------------------------------------------------------- A3
class A3Share(_Base):
    def test_a3_voided_green_not_pushed_and_void_exported(self):
        import prewarm
        tree = str(self.repo.resolve())
        pool = self.shadow / "verify-x.rows.jsonl"
        g = self.row(ago=30, repo=tree, host_id="me-host", time="2026-10-15 10:00")
        self.write(pool, g, {"void": {"test": T, "green_only": True}, "before": "2026-10-15 10:01",
                             "before_utc": _utc(0), "reason": "tree-dirtied"})
        sent = {}

        def fake_push(me, path, repo=None):
            sent["rows"] = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()]
            return {"pushed_rows": len(sent["rows"])}
        import runner
        import pool_share
        with mock.patch.object(runner, "_pool_file", return_value=pool), \
                mock.patch.object(pool_share, "push", fake_push):
            prewarm._push_rows("@me", None, tree, since=time.time() - 120)
        rows = sent.get("rows") or []
        self.assertFalse([r for r in rows if "void" not in r], "被作廢的綠推出去 ⇒ 別台拉下來照樣沿用")
        voids = [r for r in rows if "void" in r]
        self.assertEqual(len(voids), 1, rows)
        self.assertEqual(voids[0]["void"].get("host_id"), "me-host", "推出去的作廢列只打本機的列")
        self.assertNotIn("all", voids[0]["void"])

    def test_a3_pull_accepts_only_signers_own_voids(self):
        import pool_share
        good_void = {"void": {"test": T, "host_id": "fish"}, "before": "x", "before_utc": _utc(0), "exported": True}
        evil_void = {"void": {"test": T, "host_id": "me-host"}, "before": "x", "before_utc": _utc(0), "exported": True}
        all_void = {"void": {"all": True, "host_id": "fish"}, "before": "x", "before_utc": _utc(0), "exported": True}
        rows = [good_void, evil_void, all_void]
        import hashlib
        blob = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        rec = {"repo_id": "o/r", "by": "@fish", "key_fp": "FP", "rows": rows, "count": len(rows),
               "sha256": hashlib.sha256(blob).hexdigest(), "id": "r1", "host_id": "fish"}
        plaza = SimpleNamespace(records=lambda kind: [rec], key_chains=lambda: {"@fish": ["k"]},
                                verify_record=lambda r, pins, chains: "ok")
        out = Path(self.tmp) / "pulled"
        with mock.patch.dict(os.environ, {"AIB_REPO_SHARE_ID": "o/r"}):
            summ = pool_share.pull({"@fish": "FP"}, out_dir=out, repo=str(self.repo), plaza=plaza)
        self.assertEqual(summ["skipped"].get("bad-void"), 2, summ)
        files = list(out.glob("shared-*.rows.jsonl"))
        got = [json.loads(x) for f in files for x in f.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([g["void"] for g in got], [{"test": T, "host_id": "fish"}])
        idx = ri.build_index([str(out)], repo=str(self.repo))
        self.assertEqual(len(idx.voids), 1, "收進來的作廢列 reuse_index 要讀得到")


# ---------------------------------------------------------------- A4
class A4Merge(_Base):
    def merge(self, *olds):
        import pool_merge
        new = self.shadow / "verify-new.rows.jsonl"
        tmp = Path(str(new) + ".merge.tmp")
        stats = {"bad": 0, "foreign": 0, "partial": 0, "new_id": "new"}
        pool_merge._merge_locked(new, tmp, list(olds), {None, "old", "new"}, stats)
        return [json.loads(x) for x in new.read_text(encoding="utf-8").splitlines()], stats

    def test_a4_partial_last_line_becomes_tombstone(self):
        old = self.shadow / "old.jsonl"
        old.write_text(json.dumps(self.row(ago=600)) + "\n" + '{"test": "%s", "rc": 1, "inputs": [' % T, encoding="utf-8")
        rows, stats = self.merge(old)
        self.assertEqual(stats["partial"], 1)
        self.assertTrue(ri._is_tombstone(rows[-1]) and rows[-1]["test"] == T,
                        "寫一半的最後一行可能是最新的紅,靜默丟掉 ⇒ 舊綠變最新:%r" % rows)

    def test_a4_bad_line_with_unknown_test_tombstones_every_test(self):
        old = self.shadow / "old.jsonl"
        self.write(old, self.row(ago=600), self.row(ago=500, test="tests/test-b.py"), "{garbage")
        rows, _ = self.merge(old)
        self.assertEqual(sorted(r["test"] for r in rows if ri._is_tombstone(r)), [T, "tests/test-b.py"])

    def test_a4_tombstone_without_utc_not_sorted_first(self):
        a = self.shadow / "a.jsonl"
        b = self.shadow / "b.jsonl"
        tomb = {"test": T, "rc": -1, "inputs": [], "measured": "unmeasured", "external": False,
                "why": ["quarantined-bad-row"], "time": "2026-10-15 10:00"}
        self.write(a, self.row(ago=7200), tomb)
        self.write(b, self.row(ago=3600))
        rows, _ = self.merge(a, b)
        self.assertTrue(ri._is_tombstone(rows[-1]), "沒有 time_utc 的墓碑排到最前面 ⇒ 被另一池較晚的綠蓋掉:%r"
                        % [r.get("why") for r in rows])

    def test_a4_old_pool_lock_held_while_moving(self):
        import pool_merge
        old = self.shadow / "verify-old.rows.jsonl"
        self.write(old, self.row(ago=600))
        new = self.shadow / "verify-new.rows.jsonl"
        seen = {}
        real_replace = os.replace

        def spy(src, dst):
            if Path(src) == old:
                with open(src, "ab") as f:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        seen["locked"] = False
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        seen["locked"] = True
            return real_replace(src, dst)
        with mock.patch.object(pool_merge, "candidates", return_value=("new", new, [old])), \
                mock.patch.object(pool_merge.os, "replace", spy):
            res = pool_merge.merge(str(self.shadow), str(self.repo), apply=True, out=io.StringIO())
        self.assertTrue(res["applied"])
        self.assertTrue(seen.get("locked"), "搬舊池時沒拿它的寫列鎖 ⇒ 舊版正在追加的列會掉")


# ---------------------------------------------------------------- D2
class D2Compact(_Base):
    def setUp(self):
        super().setUp()
        import test_entry
        self.te = test_entry
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test-x.py").write_text("print(1)\n", encoding="utf-8")

    def test_d2_no_paths_compacts_nothing(self):
        big = self.write(self.shadow / "verify-other.rows.jsonl", self.row())
        with mock.patch.dict(os.environ, {"AIB_POOL_COMPACT_MB": "0.0000001"}), \
                mock.patch.object(ri, "compact", side_effect=AssertionError("compacted")) as c:
            self.assertEqual(ri.auto_compact(), [])
            self.assertEqual(c.call_count, 0)
        self.assertTrue(big.exists())

    def test_d2_only_own_pool(self):
        with mock.patch.object(self.te.reuse_index, "auto_compact", return_value=[]) as ac:
            self.te._auto_compact(self.repo)
        self.assertEqual(ac.call_args[0][0], [self.te.runner._pool_file(self.repo)])

    def test_d2_shard_child_does_not_compact(self):
        out = Path(self.tmp) / "shard.json"
        with mock.patch.object(self.te.reuse_index, "auto_compact", return_value=[]) as ac, \
                mock.patch.object(self.te, "_run_shard", return_value=0):
            self.te.main(["--repo", str(self.repo), "--shard-out", str(out), "tests/test-x.py"])
        self.assertEqual(ac.call_count, 0, "分份的每個子行程各壓一次池")

    def test_d2_lease_blocked_run_does_not_compact(self):
        env = {k: v for k, v in os.environ.items() if k != "AIB_TEST_TMPDIR"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.te.reuse_index, "auto_compact", return_value=[]) as ac, \
                mock.patch.object(self.te, "_lease_now", return_value=(None, None)), \
                mock.patch.object(self.te, "_ci_or_fixture_home", return_value=True), \
                mock.patch.object(self.te, "exclusive_block", return_value=None), \
                mock.patch.object(self.te, "big_batch_lease", return_value={"why": "exclusive_held", "run_id": None,
                                                                             "waited": 0}), \
                mock.patch.object(self.te, "release_batch_lease"), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = self.te.main(["--repo", str(self.repo), "tests/test-x.py"])
        self.assertEqual(rc, self.te.RC_EXCLUSIVE)
        self.assertEqual(ac.call_count, 0, "被租約擋下來的那一輪已經改寫了池")


# ---------------------------------------------------------------- D3
class D3Identity(_Base):
    def test_d3_git_timeout_retries_and_reports(self):
        import resume_store
        calls = []

        def slow(argv, **kw):
            calls.append(kw.get("timeout"))
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        errs = []
        with mock.patch.object(resume_store.subprocess, "run", slow):
            resume_store.project_key(str(self.repo), errs)
        self.assertEqual(len(calls), 2, "逾時要重試一次,而且第一個指令失敗就不再問後面的:%r" % calls)
        self.assertGreater(calls[1], calls[0], "重試要用較長的時限")
        self.assertTrue(errs and "TimeoutExpired" in errs[0], errs)

    def test_d3_wrong_identity_says_so_not_first_run(self):
        import resume_store

        def pid(repo, errors=None):
            if errors is not None:
                errors.append("rev-parse TimeoutExpired")
            return "legacy-guess"
        with mock.patch.object(resume_store, "repo_id", pid):
            idx = ri.build_index([str(self.write(self.shadow / "verify-x.rows.jsonl",
                                                 self.row(repo_id="the-project")))], repo=str(self.repo))
        self.assertEqual(idx.decide(str(self.repo), T)[:2], ("must-run", "identity-unknown"),
                         "身分算錯被報成「第一次跑」")

    def test_d3_newer_void_condition_does_not_degrade(self):
        f = self.write(self.shadow / "verify-x.rows.jsonl", self.row(ago=7200),
                       {"void": {"env_fp_prefix": "abc"}, "before": "x", "before_utc": _utc(3600)},
                       self.row(ago=60, test="tests/test-b.py"))
        idx = ri.build_index([str(f)], repo=str(self.repo))
        self.assertEqual(idx.degraded, "", "新版的作廢條件讓舊版整份永久降級")
        self.assertNotIn(T, idx.latest, "看不懂的條件 ⇒ 放大成全作廢(到 before 為止)")
        self.assertIn("tests/test-b.py", idx.latest, "作廢時間之後的列照留")
        self.assertIs(ri._parse_void({"void": {}, "before": "x"}), False, "沒有條件的作廢列仍是壞列")


# ---------------------------------------------------------------- F6
class F6Stopped(_Base):
    def test_f6_append_after_torn_tail_keeps_new_row_whole(self):
        import jsonl_safe
        f = self.shadow / "verify-x.rows.jsonl"
        f.write_text(json.dumps(self.row()) + "\n" + '{"test": "tests/test-b.py", "rc": 1, "inp', encoding="utf-8")
        jsonl_safe.append_locked(f, (json.dumps(self.row(ago=10)) + "\n").encode("utf-8"))
        lines = f.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, "新列黏在半列後面 ⇒ 兩列一起變壞行")
        self.assertEqual(json.loads(lines[2])["test"], T)

    def test_f6_own_pool_with_bad_line_heals(self):
        import test_entry
        pool = test_entry.runner._pool_file(self.repo)
        self.write(pool, self.row(ago=3600, test="tests/test-b.py"), self.row(ago=3600),
                   '{"test": "%s", "rc": 1, "inp' % T)
        idx = ri.build_index([], repo=str(self.repo))
        self.assertTrue(idx.degraded.startswith("bad-jsonl:"), idx.degraded)
        said = []
        healed = test_entry._heal_own_pool(self.repo, idx, lambda k, p: said.append(k))
        self.assertEqual(healed.degraded, "", "本專案池的一行壞行讓每一輪都全部重跑")
        self.assertEqual(healed.decide(str(self.repo), T)[:2], ("must-run", "unmeasured"), "壞的那支照樣必跑一次")
        self.assertEqual(healed.decide(str(self.repo), "tests/test-b.py")[0], "reuse", "其他支恢復沿用")
        self.assertEqual(said, ["pool_healed"])

    def test_f6_other_pools_not_touched(self):
        import test_entry
        other = self.write(self.shadow / "shared-fish-1.rows.jsonl", "{garbage")
        before = other.read_bytes()
        idx = ri.build_index([], repo=str(self.repo))
        test_entry._heal_own_pool(self.repo, idx)
        self.assertEqual(other.read_bytes(), before, "別台收進來的池不准自動改寫")


if __name__ == "__main__":
    unittest.main(verbosity=2)
