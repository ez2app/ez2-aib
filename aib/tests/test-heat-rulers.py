#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""熱尺校準臂。問的都是同一件事:這把尺壞掉時,會不會看起來像放行。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "heat"))
sys.path.insert(0, str(REPO / "tests"))

import rulers as R  # noqa: E402
from aib_testlib import python_source_without_docs  # noqa: E402
try:  # 第 24 項:治具不碰真機租約(AIB_HOST_LEASE_FILE 指暫存);被複製進治具樹時沒有這支模組,外層行程的 env 已隔離、子行程繼承
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    pass

SRC = (REPO / "tools" / "heat" / "rulers.py").read_text(encoding="utf-8")


def _ps(*lines: str) -> str:
    return "\n".join(lines) + "\n"


LAUNCHD = "    1     0 /sbin/launchd\t/sbin/launchd"
NODE_POS = "    42     1 node\tnode /tmp/keep-alive.js"
NODE_SUITE = " 4242     1 node\tnode /opt/src/ez2-ai/tools/test.js --full"
GREP_SUITE = " 4243     1 grep\tgrep tools/test.js"
CHROME_MAIN = (
    " 5000  4242 /X/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing\t"
    "/X/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing "
    "--user-data-dir=/tmp/p1 --headless=new"
)
CHROME_HELPER = (
    " 5001  5000 /X/Google Chrome for Testing Helper.app/Contents/MacOS/"
    "Google Chrome for Testing Helper\t"
    "/X/Google Chrome for Testing Helper --type=gpu-process"
)
AIB_REL = " 6000     1 bash\tbash /opt/src/aib/tools/aib-release.sh"
# 同一段 zsh -c 的 argv 帶純字面(席丁 誘餌治具)
ZSH_DECOY = (
    " 7000    80 zsh\tzsh -c echo Chrome for Testing; grep tools/test.js; sleep 30"
)
PRE_PUSH = " 18608 18606 /bin/sh\t/bin/sh .git/hooks/pre-push"
PRE_PUSH_PARENT = " 18606 18600 git\tgit push -u origin meeting/dev-0904"
# 量測行程自己 + 祖先,args 裡有 test.js 字面
MEASURER = "  100    90 python3\tpython3 tools/heat/rulers.py"
MEASURER_ZSH = "   90    80 zsh\tzsh -c python3 rulers.py; echo tools/test.js"
MEASURER_TMUX = "   80     1 tmux\ttmux -L aihub new-session"


class ParseUptimeTest(unittest.TestCase):
    def test_three_columns_are_1_5_15_not_magic_index(self):
        raw = " 1:45  up 2 days, 12:41, 4 users, load averages: 7.65 7.21 17.81"
        got = R.parse_uptime_loads(raw)
        self.assertEqual(got, (7.65, 7.21, 17.81))

    def test_unparseable_is_unmeasured_not_zero(self):
        c1, c15, raw = R.measure_uptime("not an uptime line")
        self.assertFalse(c1.measured)
        self.assertIsNone(c1.value)
        self.assertEqual(c1.display, R.UNMEASURED_DISPLAY)
        self.assertNotEqual(c1.display, "0")
        self.assertEqual(raw, "not an uptime line")
        self.assertFalse(c15.measured)


class KernelTaskParseTest(unittest.TestCase):
    def test_legal_zero_has_different_display_from_unmeasured(self):
        out = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        c = R.parse_kernel_task_cpu(out)
        self.assertEqual(c, 0.0)
        cell = R.measure_kernel_task(out)
        self.assertTrue(cell.measured)
        self.assertEqual(cell.value, 0.0)
        self.assertEqual(cell.display, R.LEGAL_ZERO_DISPLAY)
        self.assertNotEqual(cell.display, R.UNMEASURED_DISPLAY)

    def test_missing_row_is_unmeasured_not_zero(self):
        cell = R.measure_kernel_task("PID %CPU COMMAND\n")
        self.assertFalse(cell.measured)
        self.assertIsNone(cell.value)
        self.assertEqual(cell.display, R.UNMEASURED_DISPLAY)

    def test_busy_value(self):
        out = "PID %CPU COMMAND\n0   51.7 kernel_task\n"
        self.assertEqual(R.parse_kernel_task_cpu(out), 51.7)

    def test_timeout_parse_fail_legal_zero_wordings_are_three(self):
        td = R.kernel_timeout_display()
        fd = R.kernel_fail_display()
        self.assertNotEqual(td, R.UNMEASURED_DISPLAY)
        self.assertNotEqual(td, R.LEGAL_ZERO_DISPLAY)
        self.assertNotEqual(fd, R.UNMEASURED_DISPLAY)
        self.assertNotEqual(fd, td)
        self.assertNotEqual(fd, R.LEGAL_ZERO_DISPLAY)
        self.assertIn("逾時", td)
        self.assertNotIn("上次", td)

    def test_timeout_shows_last_and_age_not_as_current(self):
        def boom(argv, timeout=None):
            raise subprocess.TimeoutExpired(argv, timeout)
        fd, path = tempfile.mkstemp(prefix="kt-last-")
        os.close(fd)
        try:
            R._write_kernel_last(path, 0.0, 1000.0)
            cell = R.measure_kernel_task(
                last_path=path, now=1045.0, top_runner=boom,
            )
            self.assertFalse(cell.measured)
            self.assertIsNone(cell.value)
            self.assertIn("逾時", cell.display)
            self.assertIn("上次 0.0%", cell.display)
            self.assertIn("45 秒前", cell.display)
            self.assertNotEqual(cell.display, R.LEGAL_ZERO_DISPLAY)
            self.assertNotEqual(cell.display, R.UNMEASURED_DISPLAY)
            self.assertNotIn("0(合法)", cell.display)
        finally:
            os.unlink(path)

    def test_timeout_without_last_is_not_generic_unmeasured(self):
        def boom(argv, timeout=None):
            raise subprocess.TimeoutExpired(argv, timeout)
        cell = R.measure_kernel_task(
            last_path="/no/such/heat-kt-last.json", top_runner=boom,
        )
        self.assertFalse(cell.measured)
        self.assertEqual(cell.display, R.kernel_timeout_display())
        self.assertNotEqual(cell.display, R.UNMEASURED_DISPLAY)


class ProcessParseTest(unittest.TestCase):
    def test_grep_line_is_not_a_suite_runner(self):
        text = _ps(LAUNCHD, NODE_SUITE, GREP_SUITE)
        rows = R.parse_ps_axo(text)
        runners = R.suite_runners(rows)
        self.assertEqual([p.pid for p in runners["ez2_ai_test_js"]], [4242])

    def test_chrome_mains_exclude_helpers(self):
        text = _ps(LAUNCHD, CHROME_MAIN, CHROME_HELPER)
        rows = R.parse_ps_axo(text)
        mains = R.chrome_mains(rows)
        self.assertEqual([p.pid for p in mains], [5000])
        self.assertEqual(R.chrome_profiles(mains), ["/tmp/p1"])

    def test_browser_runner_is_parent_not_chrome_count(self):
        text = _ps(LAUNCHD, NODE_SUITE, CHROME_MAIN, CHROME_HELPER)
        rows = R.parse_ps_axo(text)
        runners = R.suite_runners(rows)
        self.assertEqual([p.pid for p in runners["browser_runner"]], [4242])

    def test_self_needle_in_argv_fails_arm(self):
        text = _ps(
            LAUNCHD,
            " 9999     1 python3 rulers.py %s" % R.SELF_NEEDLE,
        )
        rows = R.parse_ps_axo(text)
        arm = R.arm_self_match(rows)
        self.assertFalse(arm.ok)
        self.assertIn("9999", arm.detail)

    def test_self_needle_absent_passes(self):
        rows = R.parse_ps_axo(_ps(LAUNCHD, NODE_SUITE))
        self.assertTrue(R.arm_self_match(rows).ok)

    def test_launchd_calibrator_catches_blind_counter(self):
        rows = R.parse_ps_axo(_ps("  5     1 zsh\tzsh -c echo hi"))
        arm = R.arm_known_alive(rows, skip_from=5)
        self.assertFalse(arm.ok)

    def test_launchd_pos_cal_after_chain_exclude_is_zero(self):
        """席丁:兩個洞。不排鏈的 launchd=1 什麼都沒證明;排鏈後=0。路徑分隔符仍會叫。"""
        rows = R.parse_ps_axo(_ps(
            LAUNCHD, MEASURER_TMUX, MEASURER_ZSH, MEASURER, NODE_POS,
        ))
        # 洞②:校準跑在另一把尺上(不排鏈)。pid -1 不在樹裡,祖先集空。
        unchained = R.count_cmd_contains(
            rows, "launchd", exclude_ancestors_of=-1,
        )
        self.assertEqual([p.pid for p in unchained], [1])
        # 洞①:校準目標在祖先鏈上。走與目標同一條管線後那個 1 消失。
        launchd_hits = R.count_cmd_contains(
            rows, "launchd", exclude_ancestors_of=100,
        )
        self.assertEqual([p.pid for p in launchd_hits], [])
        slash_hits = R.count_cmd_contains(
            rows, R.POS_CALIB_ARGS, exclude_ancestors_of=100,
        )
        self.assertTrue(slash_hits)
        self.assertTrue(R.arm_known_alive(rows, skip_from=100).ok)

    def test_decoy_in_same_argv_is_not_chrome(self):
        """誘餌在 zsh -c 的 args 裡,comm 是 zsh。認 comm 才免疫。"""
        rows = R.parse_ps_axo(_ps(LAUNCHD, ZSH_DECOY, CHROME_MAIN))
        mains = R.chrome_mains(rows)
        self.assertEqual([p.pid for p in mains], [5000])
        naive = [p for p in rows if "Chrome for Testing" in p.command]
        self.assertIn(7000, [p.pid for p in naive])

    def test_args_runner_excludes_whole_ancestor_chain(self):
        """只排 $$ 會把祖先 zsh 裡的字面算成 runner。"""
        text = _ps(
            LAUNCHD, MEASURER_TMUX, MEASURER_ZSH, MEASURER, NODE_SUITE,
        )
        rows = R.parse_ps_axo(text)
        only_self = R.count_cmd_contains(
            rows, "tools/test.js", exclude_pids=(100,), exclude_ancestors_of=-1,
        )
        # exclude_ancestors_of=-1 → 鏈={-1},不會排 90
        self.assertIn(90, [p.pid for p in only_self])
        self.assertIn(4242, [p.pid for p in only_self])
        full = R.suite_runners(rows, exclude_ancestors_of=100)
        self.assertEqual([p.pid for p in full["ez2_ai_test_js"]], [4242])

    def test_hit_lines_include_parent_and_grandparent(self):
        rows = R.parse_ps_axo(_ps(LAUNCHD, PRE_PUSH_PARENT, PRE_PUSH))
        hits = R.suite_runners(rows, exclude_ancestors_of=100)["ez2_ai_pre_push"]
        self.assertEqual([p.pid for p in hits], [18608])
        who = R.describe_hits(hits, rows)
        self.assertEqual(len(who), 1)
        self.assertIn("pid=18608", who[0])
        self.assertIn("gp=", who[0])
        self.assertIn("git", who[0])

    def test_aib_release_mention_on_own_chain_is_noise_floor(self):
        """席丙:接單源前發版 1 是自己祖先 zsh 提到檔名;排鏈後 1→0。"""
        zsh = "   90    80 zsh\tzsh -c source /tmp/aib-release.sh"
        rows = R.parse_ps_axo(_ps(LAUNCHD, MEASURER_TMUX, zsh, MEASURER))
        unchained = R.count_cmd_contains(
            rows, "aib-release.sh", exclude_ancestors_of=-1,
        )
        self.assertEqual([p.pid for p in unchained], [90])
        chained = R.suite_runners(rows, exclude_ancestors_of=100)["aib_release"]
        self.assertEqual([p.pid for p in chained], [])

    def test_only_self_and_ppid_leaves_tmux_in_chain(self):
        rows = R.parse_ps_axo(_ps(LAUNCHD, MEASURER_TMUX, MEASURER_ZSH, MEASURER))
        chain = R.ancestor_pids(rows, 100)
        self.assertEqual(chain, {100, 90, 80, 1})
        arm = R.arm_ancestor_depth(rows, start_pid=100)
        self.assertTrue(arm.ok, arm.detail)


class SeatRowTest(unittest.TestCase):
    def test_pane_dead_not_command_name(self):
        live = {
            "session": "aihub", "window_id": "@20", "window_name": "dev1",
            "pane_dead": "0", "pane_pid": "84450",
        }
        dead = dict(live, pane_dead="1")
        zsh_looking = dict(live, window_name="zsh")  # 窗名不是判活條件
        watch = dict(live, window_name="watch")
        cockpit = dict(live, window_name="cockpit")
        self.assertTrue(R.is_live_seat_row(live))
        self.assertFalse(R.is_live_seat_row(dead))
        self.assertTrue(R.is_live_seat_row(zsh_looking))
        self.assertFalse(R.is_live_seat_row(watch))
        self.assertFalse(R.is_live_seat_row(cockpit))
        self.assertTrue(R.is_inner_hub_socket("aihub-20260901-201858-90b5"))
        self.assertFalse(R.is_inner_hub_socket("default"))
        self.assertFalse(R.is_inner_hub_socket("bfprobe"))

    def test_outer_shell_windows_are_not_seats(self):
        def runner(argv):
            sock = argv[2]  # tmux -L <sock> ...
            if sock == "default":
                out = (
                    "aib-90b5\t@28\t回報上色\t0\t33718\n"
                    "aib-90b5\t@10\trails\t0\t61865\n"
                    "aib-90b5\t@4\tcockpit\t0\t98275\n"
                )
            else:
                out = "aihub\t@27\troot-grok\t0\t76405\n"
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        cell = R.measure_live_seats(
            ["aihub-20260901-201858-90b5", "default"], runner=runner,
        )
        self.assertTrue(cell.measured)
        self.assertEqual(cell.value, 1, cell)

    def test_socket_files_exist_but_none_live_is_unmeasured(self):
        def runner(argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
        files, live, names = R.measure_sockets(["ghost"], runner=runner)
        self.assertEqual(files.value, 1)
        self.assertFalse(live.measured)
        self.assertEqual(live.display, R.UNMEASURED_DISPLAY)
        self.assertEqual(names, [])

    def test_pid_alive_but_command_replaced_is_not_the_seat(self):
        rows = R.parse_ps_axo(_ps(" 1107     1 /usr/libexec/familycircled"))
        self.assertTrue(R.pid_cmd_matches(1107, "familycircled", rows))
        self.assertFalse(R.pid_cmd_matches(1107, "8d43e844-d682-4fc6-8c79-82d0dbfbe4f6", rows))
        self.assertFalse(R.pid_cmd_matches(99999, "familycircled", rows))

    def test_same_count_different_who_is_not_the_same_19(self):
        """兩個 19 不是同一個 19。naive 把回收 pid 算進去,strict 刷掉。"""
        uuid = "56f727bd-6ae0-4e25-9310-64f7dfb5e20f"
        real_uuid = "bb8233c1-3e55-49e4-9344-25a7f615d3eb"
        rows = R.parse_ps_axo(_ps(
            " 1107     1 familycircled\t/System/Library/.../familycircled",
            " 76405    1 zsh\tzsh -c provider-launch.sh grok %s" % real_uuid,
        ))
        recs = [
            {"path": "instances/14/seats/w5.json", "pid": 1107,
             "session_uuid": uuid, "role": "qa", "window_id": "@5"},
            {"path": "instances/90b5/seats/w27.json", "pid": 76405,
             "session_uuid": real_uuid, "role": "root", "window_id": "@27"},
        ]
        naive, strict, fake = R.census_seat_jsons(recs, rows)
        self.assertEqual(len(naive), 2)
        self.assertEqual(len(strict), 1)
        self.assertEqual(len(fake), 1)
        self.assertIn("假活", fake[0])
        self.assertIn("familycircled", fake[0])
        self.assertIn(real_uuid[-5:], strict[0])
        self.assertIn("@5", fake[0])
        self.assertNotIn("@@5", fake[0])

    def test_two_nineteens_are_not_the_same_event(self):
        """席癸 01:4x 的 19=18真+1假;席丙 的 19=19真。數字相同、who 不同。
        合併兩個 19 會把 pid 回收那條必紅臂刪掉。"""
        fake_uuid = "56f727bd-6ae0-4e25-9310-64f7dfb5e20f"
        reals_a = ["aaaaaaaa-aaaa-aaaa-aaaa-0000000000%02d" % i for i in range(18)]
        reals_b = ["bbbbbbbb-bbbb-bbbb-bbbb-0000000000%02d" % i for i in range(19)]

        def build(uuids, extra=None):
            recs = []
            lines = []
            pid = 2000
            for u in uuids:
                pid += 1
                recs.append({
                    "path": "cab/seats/w.json", "pid": pid,
                    "session_uuid": u, "role": "dev", "window_id": "@1",
                })
                lines.append(" %5d     1 zsh\tzsh launch %s" % (pid, u))
            if extra:
                recs.append(extra[0])
                lines.append(extra[1])
            return recs, R.parse_ps_axo(_ps(*lines))

        extra = (
            {"path": "instances/14/seats/w5.json", "pid": 1107,
             "session_uuid": fake_uuid, "role": "qa", "window_id": "@5"},
            "  1107     1 familycircled\t/System/Library/familycircled",
        )
        recs_a, rows_a = build(reals_a, extra)
        recs_b, rows_b = build(reals_b, None)
        na, sa, fa = R.census_seat_jsons(recs_a, rows_a)
        nb, sb, fb = R.census_seat_jsons(recs_b, rows_b)
        self.assertEqual(len(na), 19)
        self.assertEqual(len(nb), 19)
        self.assertEqual(len(sa), 18)
        self.assertEqual(len(sb), 19)
        self.assertEqual(len(fa), 1)
        self.assertEqual(len(fb), 0)
        self.assertEqual(len(na), len(nb))
        self.assertFalse(R.same_count_same_event(na, nb))
        self.assertFalse(R.same_count_same_event(sa, sb))
        self.assertIn("familycircled", fa[0])
        self.assertIn("instances/14/seats/w5.json", fa[0])

    def test_snapshot_keeps_fake_alive_red_and_does_not_merge_counts(self):
        uuid = "56f727bd-6ae0-4e25-9310-64f7dfb5e20f"
        real_uuid = "bb8233c1-3e55-49e4-9344-25a7f615d3eb"
        recs = [
            {"path": "instances/14/seats/w5.json", "pid": 1107,
             "session_uuid": uuid, "role": "qa", "window_id": "@5"},
            {"path": "instances/90b5/seats/w27.json", "pid": 76405,
             "session_uuid": real_uuid, "role": "root", "window_id": "@27"},
        ]
        snap = R.snapshot(
            uptime_raw=" 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00",
            top_out="PID %CPU COMMAND\n0   0.0  kernel_task\n",
            ps_text=_ps(
                LAUNCHD, NODE_POS,
                "  1107     1 familycircled\t/System/Library/familycircled",
                " 76405     1 zsh\tzsh launch %s" % real_uuid,
            ),
            probe_tmux=False, read_temp=False, seat_records=recs,
        )
        naive = snap.cells["seat_pid_naive"]
        strict = snap.cells["seat_pid_strict"]
        self.assertEqual(naive.value, 2)
        self.assertEqual(strict.value, 1)
        self.assertIn("只問pid在", naive.display)
        self.assertIn("是那個席", strict.display)
        self.assertNotEqual(naive.display, strict.display)
        self.assertEqual(R.cell_tone("seat_pid_naive", naive), "nogo")
        self.assertEqual(R.cell_tone("seat_pid_strict", strict), "go")
        self.assertNotEqual(snap.verdict, "ruler-broken")
        a5 = [a for a in snap.arms if a.id == "A5"][0]
        self.assertTrue(a5.ok)
        self.assertIn("必紅臂", a5.detail)
        self.assertIn("instances/14/seats/w5.json", a5.detail)
        human = R.format_human(snap)
        self.assertIn("who ", human)
        self.assertIn("familycircled", human)
        self.assertIn("只問pid在", human)


class VerdictTest(unittest.TestCase):
    def _snap(
        self, uptime: str, top: str, extra_ps: str = "",
        undercount_calibrated: bool | None = None,
    ) -> R.Snapshot:
        ps = _ps(LAUNCHD, NODE_POS, extra_ps) if extra_ps else _ps(LAUNCHD, NODE_POS)
        return R.snapshot(
            uptime_raw=uptime,
            top_out=top,
            ps_text=ps,
            probe_tmux=False,
            read_temp=False,
            undercount_calibrated=undercount_calibrated,
        )

    def test_load1_high_is_nogo_load15_high_is_not(self):
        # 1 分低、15 分高:往回看的指標不當閘。
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 2.00 30.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = self._snap(up, top, undercount_calibrated=True)
        self.assertEqual(snap.verdict, "go", snap.reasons)
        self.assertEqual(snap.cells["load15"].value, 30.0)
        chrome = snap.cells["chrome_mains"]
        self.assertTrue(chrome.triple_complete())
        self.assertGreater(int(chrome.pos_cal), 0)
        self.assertEqual(int(chrome.neg_cal), 0)

        up_hot = " 1:00  up 1 day, 1 user, load averages: 16.00 2.00 1.00"
        snap2 = self._snap(up_hot, top)
        self.assertEqual(snap2.verdict, "no-go")
        self.assertTrue(any("第一個 load=" in r for r in snap2.reasons), snap2.reasons)

    def test_kernel_throttle_is_nogo(self):
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   21.0 kernel_task\n"
        snap = self._snap(up, top)
        self.assertEqual(snap.verdict, "no-go")
        self.assertTrue(any("kernel_task=" in r for r in snap.reasons))

    def test_chrome_mains_occupy_the_window(self):
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = self._snap(up, top, extra_ps=CHROME_MAIN)
        self.assertEqual(snap.verdict, "no-go")
        self.assertTrue(any("瀏覽器窗口" in r for r in snap.reasons))
        self.assertEqual(snap.cells["chrome_mains"].value, 1)
        self.assertEqual(snap.cells["chrome_mains"].target, 1)
        self.assertEqual(snap.cells["chrome_mains"].neg_cal, 0)

    def test_unmeasured_load_is_nogo_not_go(self):
        snap = self._snap("garbage", "PID %CPU COMMAND\n0   0.0  kernel_task\n")
        self.assertNotEqual(snap.verdict, "go")
        self.assertEqual(snap.cells["load1"].display, R.UNMEASURED_DISPLAY)


class TripleCountTest(unittest.TestCase):
    def test_bait_comm_immune_naive_args_not(self):
        """誘餌對照:有 vs 完全沒有。comm 讀數不變;naive args 變大。"""
        rows = R.parse_ps_axo(_ps(LAUNCHD, NODE_POS))
        a, b, ok = R.in_memory_bait(rows, lambda r: len(R.chrome_mains(r)))
        self.assertTrue(ok)
        self.assertEqual(a, b)
        na, nb, nok = R.in_memory_bait(
            rows, lambda r: len(R.count_args_naive(r, R.CHROME_COMM_MARK)),
        )
        self.assertFalse(nok)
        self.assertEqual(nb, na + 1)

    def test_neg_control_zero_does_not_prove_immunity(self):
        """負字面只住在被濾掉的 grep 時,負對照=0 而 args 目標仍被污染。"""
        decoys = [
            " 701%d    80 zsh\tzsh -c echo Chrome for Testing" % i
            for i in (1, 2, 3)
        ]
        grep_neg = " 8888     1 grep\tgrep %s" % R.NEG_CALIB_NEEDLE
        rows = R.parse_ps_axo(_ps(LAUNCHD, NODE_POS, grep_neg, *decoys))
        t = R.count_args_naive(rows, R.CHROME_COMM_MARK)
        n = len(R.count_cmd_contains(rows, R.NEG_CALIB_NEEDLE, exclude_ancestors_of=-1))
        self.assertEqual(len(t), 3)
        self.assertEqual(n, 0)
        self.assertEqual(len(R.chrome_mains(rows)), 0)

    def test_bait_repeat_does_not_increase_process_count(self):
        """污染單位是行程數不是字面次數。同一 argv 加 5 次誘餌仍是 1 個行程。"""
        one = R.Proc(pid=7, ppid=1, comm="zsh", command="zsh -c echo " + ("Chrome for Testing " * 5))
        self.assertEqual(len(R.count_args_naive([one], R.CHROME_COMM_MARK)), 1)
        self.assertEqual(len(R.chrome_mains([one])), 0)

    def test_missing_pos_cal_is_ruler_broken_not_go(self):
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD),
            probe_tmux=False, read_temp=False,
        )
        self.assertEqual(snap.verdict, "ruler-broken")
        self.assertNotEqual(snap.verdict, "go")

    def test_chrome_zero_is_undercount_unmeasured_not_go(self):
        """四個對照只抓高估。正本 UNDERCOUNT_CALIBRATED=False,0 不得當再起一輪。"""
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD, NODE_POS),
            probe_tmux=False, read_temp=False,
        )
        self.assertFalse(R.UNDERCOUNT_CALIBRATED)
        self.assertEqual(snap.verdict, "no-go", snap.reasons)
        self.assertTrue(any("未經低估對照" in r for r in snap.reasons), snap.reasons)
        self.assertNotEqual(snap.verdict, "go")
        c = snap.cells["chrome_mains"]
        self.assertEqual(c.value, 0)
        self.assertIn(R.UNDERCOUNT_ZERO_DISPLAY, c.display)
        self.assertNotEqual(c.display, R.LEGAL_ZERO_DISPLAY)
        self.assertNotIn(R.UNMEASURED_DISPLAY, c.display)
        self.assertEqual(R.cell_tone("chrome_mains", c), "broken")
        self.assertNotEqual(R.UNDERCOUNT_ZERO_DISPLAY, R.LEGAL_ZERO_DISPLAY)
        self.assertNotEqual(R.UNDERCOUNT_ZERO_DISPLAY, R.UNMEASURED_DISPLAY)
        a14 = [a for a in snap.arms if a.id == "A14"][0]
        self.assertTrue(a14.ok)
        self.assertIn("未量", a14.detail)

    def test_go_cell_prints_controls_and_chain(self):
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD, NODE_POS),
            probe_tmux=False, read_temp=False,
            undercount_calibrated=True,
        )
        self.assertEqual(snap.verdict, "go", snap.reasons)
        c = snap.cells["chrome_mains"]
        self.assertIn("目標=", c.display)
        self.assertIn("正=", c.display)
        self.assertIn("負=", c.display)
        self.assertEqual(c.value, c.target)
        self.assertIn("ancestor_chain", snap.cells)
        self.assertGreater(int(c.source_n), 0)
        self.assertIn("母體=", c.display)
        self.assertGreater(int(snap.cells["ps"].source_n), 0)
        self.assertTrue(snap.observed_at)
        # 正校準不得跨尺比較。A 型 node、B 型路徑分隔符,兩個都必須 >0,不必相等。
        self.assertGreater(int(snap.cells["ez2_ai_test_js"].pos_cal), 0)
        human = R.format_human(snap)
        self.assertIn("raw_uptime:", human)
        self.assertNotIn("第 N 欄", human)
        self.assertNotIn("第 2 欄", human)

    def test_grep_c_rc_zero_hits_is_not_broken(self):
        self.assertEqual(R.grep_c_status(0), "hits")
        self.assertEqual(R.grep_c_status(1), "zero")
        self.assertEqual(R.grep_c_status(2), "broken")
        self.assertNotEqual(R.grep_c_status(1), "broken")

    def test_empty_pipe_grep_c_looks_like_normal_zero(self):
        """v4 洞:空輸入 → grep -c 值=0 rc=1,與正常零筆逐字相同。"""
        proc = subprocess.run(
            ["grep", "-c", "node"],
            input="", capture_output=True, text=True,
        )
        self.assertEqual(proc.stdout.strip(), "0")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(R.grep_c_status(proc.returncode), "zero")
        ok, n, why = R.source_assert("", 0)
        self.assertFalse(ok)
        self.assertEqual(n, 0)
        self.assertIn("empty", why)

    def test_population_has_no_remembered_normal_size(self):
        """母體沒有『正常 380 行』這種寫死值。2 行也是合法母體;0 行才是壞。"""
        ok, n, _ = R.source_assert("a\nb\n", 0)
        self.assertTrue(ok)
        self.assertEqual(n, 2)
        src = (REPO / "tools" / "heat" / "rulers.py").read_text(encoding="utf-8")
        self.assertNotIn("正常 380", src)
        self.assertNotIn("NORMAL_PS", src)

    def test_count_from_source_three_states(self):
        """有=數字;零筆=0;來源壞=None。PIPESTATUS 不是修法。"""
        ok, hits, n, _ = R.count_comm_from_source(
            "Chrome for Testing\n" * 10, 0, R.CHROME_COMM_MARK,
        )
        self.assertTrue(ok)
        self.assertEqual(hits, 10)
        self.assertEqual(n, 10)
        ok0, hits0, n0, _ = R.count_comm_from_source("node\n" * 5, 0, R.CHROME_COMM_MARK)
        self.assertTrue(ok0)
        self.assertEqual(hits0, 0)
        self.assertEqual(n0, 5)
        bad, hits_b, n_b, why = R.count_comm_from_source("", 1, R.CHROME_COMM_MARK)
        self.assertFalse(bad)
        self.assertIsNone(hits_b)
        self.assertNotEqual(hits_b, 0)
        self.assertIn("rc=", why)
        arm = R.arm_source_end_failure()
        self.assertTrue(arm.ok, arm.detail)

    def test_empty_source_is_unmeasured_not_zero(self):
        cell, rows = R.measure_ps("")
        self.assertFalse(cell.measured)
        self.assertEqual(cell.display, R.UNMEASURED_DISPLAY)
        self.assertEqual(rows, [])
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text="",
            probe_tmux=False, read_temp=False,
        )
        self.assertEqual(snap.verdict, "ruler-broken")
        self.assertNotEqual(snap.verdict, "go")
        self.assertIsNone(cell.value)
        human = R.format_human(snap)
        self.assertIn("量不到", human)
        self.assertNotIn("ps: 0", human)


class StaticGuardTest(unittest.TestCase):
    def test_measurement_path_does_not_pipe_grep(self):
        code = python_source_without_docs(SRC)
        self.assertNotIn("ps | grep", code)
        # 「pgrep -c」這個字面含「grep -c」,所以先剝掉校準臂那一處再查。
        stripped = code.replace('["pgrep", "-c", "launchd"]', "")
        stripped = stripped.replace("pgrep -c", "")
        self.assertNotIn("grep -c", stripped)
        self.assertIn('["pgrep", "-c", "launchd"]', SRC)
        self.assertNotIn("FAIL_CLOSED_COUNT", SRC)
        code = python_source_without_docs(SRC)
        self.assertNotIn("|| echo 0", code)
        self.assertNotIn("|| echo 999", code)
        self.assertNotIn("PIPESTATUS", code)
        self.assertEqual(R.UNMEASURED_DISPLAY, "量不到")
        self.assertNotIn("comm=,args=", code)
        self.assertNotIn("pid=,ppid=,comm=,args=", code)
        self.assertIn('["ps", "-Ao", "comm="]', SRC)
        self.assertNotIn('["ps", "-ax"', code)
        # 席辛 補丁一:截斷成因是同輸出有 args=。不是旗標、不是機器、不是多欄。
        self.assertIn("一旦同一次輸出要求", SRC)
        self.assertNotIn("兩台機器", SRC)
        self.assertNotIn("兩種失效", SRC)
        self.assertNotIn("多欄輸出時 comm 被截", SRC)
        self.assertNotIn("-Ao 與 -ax -o 讀數相同", SRC)
        # 席丁:-ax 與 -A 列舉的不是同一組;正校準必須走同一條管線。
        self.assertIn("換一個 ps 形式就是換一把尺", SRC)
        self.assertIn("正校準必須經過與目標完全相同", SRC)
        self.assertIn("標成推,不要升級", SRC)
        # 席壬 第一節撤回:那個 30 不可引用。第二節補丁提案不入定版。
        self.assertIn("不要引用 01:2x 那個 30", SRC)
        self.assertNotIn("成因至少有兩種", SRC)
        self.assertIn("UNDERCOUNT_CALIBRATED = False", SRC)
        self.assertIn("席乙 03:27:54", SRC)
        self.assertFalse(R.UNDERCOUNT_CALIBRATED)
        # 席戊:機制對照;已關/未證分欄;沒有已提交 markdown 正本路徑。
        self.assertIn("機制對照", SRC)
        self.assertIn("已關 vs 未證", SRC)
        self.assertIn("B 型低估 未證", SRC)
        self.assertIn("只有兩個數字時任何解釋", SRC)
        self.assertIn("還沒有已提交的 markdown", SRC)
        self.assertNotIn("docs/CANON-counting-rulers.md", SRC)
        self.assertIn("sys.modules[spec.name]=mod", SRC)
        self.assertIn("那個差是他的雜訊底,不是真發版", SRC)
        self.assertTrue(hasattr(R, "count_controls"))
        self.assertFalse(hasattr(R, "count_triple"))
        for name in R.PANEL_REQUIRED_NAMES:
            self.assertTrue(hasattr(R, name), name)
        self.assertIn("閘用目標數,不用目標減負", SRC)
        self.assertNotEqual(R.UNDERCOUNT_ZERO_DISPLAY, R.LEGAL_ZERO_DISPLAY)
        self.assertNotEqual(R.UNDERCOUNT_ZERO_DISPLAY, R.UNMEASURED_DISPLAY)

    def test_thresholds_are_single_source(self):
        self.assertEqual(R.THRESHOLDS["load1_nogo"], 15.0)
        self.assertEqual(R.THRESHOLDS["kernel_task_nogo"], 20.0)
        self.assertEqual(R.THRESHOLDS["kernel_task_timeout_s"], 0.8)
        self.assertIn("THRESHOLDS", SRC)
        # 渲染與判斷都讀同一格,不得在 format_human 再寫死一份數字當判斷。
        self.assertIn("THRESHOLDS[\"load1_nogo\"]", SRC.replace("'", '"') or SRC)


class DarwinLiveTest(unittest.TestCase):
    """真機探測。不碰 tmux。"""

    @unittest.skipUnless(sys.platform == "darwin", "IOHID 只在 Darwin")
    def test_cpu_die_is_measured_or_explicitly_unmeasured(self):
        cell = R.read_cpu_die_temps()
        if cell.measured:
            self.assertIsInstance(cell.value, float)
            self.assertGreater(cell.value, 0.0)
            self.assertLess(cell.value, 150.0)
            self.assertNotEqual(cell.display, R.UNMEASURED_DISPLAY)
        else:
            self.assertEqual(cell.display, R.UNMEASURED_DISPLAY)
            self.assertIsNone(cell.value)
            self.assertTrue(cell.why)

    @unittest.skipUnless(sys.platform == "darwin", "top -pid 0 是 macOS 形狀")
    def test_kernel_task_pid0_is_a_number(self):
        cell = R.measure_kernel_task()
        if cell.measured:
            self.assertIsInstance(cell.value, float)
            if cell.value == 0.0:
                self.assertEqual(cell.display, R.LEGAL_ZERO_DISPLAY)
            else:
                self.assertNotEqual(cell.display, R.UNMEASURED_DISPLAY)
        else:
            self.assertIsNone(cell.value)
            self.assertNotEqual(cell.display, R.LEGAL_ZERO_DISPLAY)
            self.assertNotEqual(cell.display, "0")

    @unittest.skipUnless(sys.platform == "darwin", "pgrep -c 失效形是 macOS")
    def test_pgrep_c_is_nonzero_rc(self):
        arm = R.arm_pgrep_c()
        self.assertTrue(arm.ok, arm.detail)
        proc = subprocess.run(
            ["pgrep", "-c", "launchd"], capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "真機 ps comm/args")
    def test_live_decoy_zsh_not_counted_as_chrome(self):
        decoy = subprocess.Popen(
            ["zsh", "-c", "sleep 8; echo Chrome for Testing"],
        )
        try:
            ps_cell, rows = R.measure_ps()
            self.assertTrue(ps_cell.measured, ps_cell.why)
            mains = R.chrome_mains(rows)
            self.assertNotIn(decoy.pid, [p.pid for p in mains])
            naive = [p for p in rows if "Chrome for Testing" in p.command]
            self.assertTrue(
                any(p.pid == decoy.pid for p in naive),
                "治具沒把字面送進 decoy 的 args,本臂無資訊",
            )
        finally:
            decoy.kill()
            decoy.wait(timeout=2)


class ColorTest(unittest.TestCase):
    def _go(self) -> R.Snapshot:
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        return R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD, NODE_POS),
            probe_tmux=False, read_temp=False,
            undercount_calibrated=True,
        )

    def test_plain_has_zero_ansi(self):
        h = R.format_human(self._go(), color=False)
        self.assertNotIn("\033", h)

    def test_color_paints_go_green_not_broken_magenta(self):
        snap = self._go()
        h = R.format_human(snap, color=True)
        self.assertIn(R.ANSI["go"], h)
        self.assertIn(R.ANSI["reset"], h)
        self.assertEqual(R.verdict_tone(snap.verdict), "go")
        miss = R._cell_miss("x", "why")
        self.assertEqual(R.cell_tone("load1", miss), "broken")
        self.assertNotEqual(R.ANSI["broken"], R.ANSI["go"])
        self.assertNotEqual(R.ANSI["broken"], R.ANSI["nogo"])

    def test_nogo_load_is_red_unmeasured_is_not_green(self):
        up = " 1:00  up 1 day, 1 user, load averages: 16.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD, NODE_POS),
            probe_tmux=False, read_temp=False,
        )
        self.assertEqual(R.verdict_tone(snap.verdict), "nogo")
        self.assertEqual(R.cell_tone("load1", snap.cells["load1"]), "nogo")
        h = R.format_human(snap, color=True)
        self.assertIn(R.ANSI["nogo"], h)
        empty = R.snapshot(
            uptime_raw=up, top_out=top, ps_text="",
            probe_tmux=False, read_temp=False,
        )
        self.assertEqual(R.cell_tone("ps", empty.cells["ps"]), "broken")
        hb = R.format_human(empty, color=True)
        self.assertIn(R.ANSI["broken"], hb)
        self.assertNotIn(
            R.ANSI["go"] + R.UNMEASURED_DISPLAY, hb,
        )

    def test_want_color_respects_no_color(self):
        old = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertFalse(R.want_color(None, sys.stdout))
            self.assertTrue(R.want_color(True, sys.stdout))
        finally:
            if old is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = old


class CliSmokeTest(unittest.TestCase):
    def test_json_injected_snapshot_roundtrip_keys(self):
        up = " 1:00  up 1 day, 1 user, load averages: 1.00 1.00 1.00"
        top = "PID %CPU COMMAND\n0   0.0  kernel_task\n"
        snap = R.snapshot(
            uptime_raw=up, top_out=top, ps_text=_ps(LAUNCHD, NODE_POS),
            probe_tmux=False, read_temp=False,
        )
        d = snap.as_dict()
        self.assertIn("verdict", d)
        self.assertIn("raw_uptime", d)
        self.assertEqual(d["cells"]["load1"]["value"], 1.0)
        human = R.format_human(snap)
        self.assertIn("raw_uptime:", human)
        self.assertIn("A7", human)
        self.assertIn("A15", human)
        self.assertIn("A10a", human)


if __name__ == "__main__":
    os.environ.setdefault("PYTHON_COLORS", "0")
    unittest.main()
