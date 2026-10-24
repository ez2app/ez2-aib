#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat advice(10-25 @044e0,RULING-room-heat-local-opt 第 2 張):七條散熱建議,只顯示。
全在暫存 HOME / 狀態根 + 假行程表 / 假 launchctl 跑;不讀真的對話紀錄、不叫真的 launchctl。
A1 工作樹:家目錄最上層、.git 是檔、不在 .noindex ⇒ 算;.git 是資料夾 / symlink、.noindex 底下 ⇒ 不算;
   24 小時內有寫看 gitdir 的 index;席碼從資料夾名取
A2 hook:最近 3 次都 ≥ timeout×0.98 ⇒ hook_timeout_kill;中位 > 1 秒 ⇒ hook_slow;設定裡已拿掉 ⇒ hook_slow + removed;
   最新一筆 summary 沒有的 hook 不列;標籤只給 statusMessage 或腳本檔名
A3 只讀尾巴(D3):尾巴外面的舊紀錄不算;合計上限照扣
A4 transcript_huge:> 500 MB(稀疏檔,不佔磁碟)
A5 launchd(D2):macOS 才看、只給 Label;其他平台 ⇒ skipped unsupported_platform 且不叫 launchctl
A6 test_parallel(同席 > 1、全機 > 3;認不出席位不算同席)/ browser_tab_hot(兩次取樣都 > 80%)
A7 快取 60 秒:第二次不重掃;快取檔被改 ⇒ code 不在表上的丟掉、名字再洗、hint 重翻;過 60 秒重掃
A8 隱私(D4 / E1 / E6):輸出不含指令列 / 路徑 / token;hint 不夾名字;別人的檔、單行 > 1 MB 不讀
A9 CLI:`advice --json` / 文字模式
刀(複本 + run_guarded):K1 改成全讀 ⇒ A3 紅;K2 快取拿掉 ⇒ A7 紅;K3 標籤給整串指令 ⇒ A8 紅;K4 不濾最新 summary ⇒ A2 紅。
"""
import io
import json
import os
import plistlib
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import room_heat as RH  # noqa: E402
import room_heat_advice as RA  # noqa: E402

ME = os.getuid()
NOW = time.time()
SID = "11111111-2222-4333-8444-5555555abcde"
SLOW = "python3 '/Users/u/.claude/slow-gate.py' --token=abc123"
GATE = "Sync Ez2 mechanical essence"
FAST = "test -f /Users/u/aib/src/x.py || exit 0; exec python3 /Users/u/aib/src/policy/hooks/fast-gate.py stop"
GONE = "python3 /Users/u/.claude/removed-gate.py"


def summary(hooks):
    return json.dumps({"type": "system", "subtype": "stop_hook_summary", "cwd": "/Users/u/secret",
                       "hookInfos": [{"command": c, "durationMs": ms} for c, ms in hooks]}, ensure_ascii=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="aib-heat-advice-"))
        self.addCleanup(shutil.rmtree, str(self.home), True)
        self.state = self.home / "state"
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.state), "HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        (self.home / ".claude" / "projects" / "-x").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": SLOW, "timeout": 10},
            {"type": "command", "command": "python3 sync.py", "timeout": 15, "statusMessage": GATE},
            {"type": "command", "command": FAST, "timeout": 10}]}]}}))

    def transcript(self, lines, sid=SID, mtime=None):
        p = self.home / ".claude" / "projects" / "-x" / (sid + ".jsonl")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    def worktree(self, name, index_age, gitdir_is_file=True):
        d = self.home / name
        d.mkdir(parents=True)
        g = self.home / ".gitdirs" / name.replace("/", "_")
        g.mkdir(parents=True)
        (g / "index").write_text("x")
        t = NOW - index_age
        os.utime(g / "index", (t, t))
        os.utime(g, (t, t))
        if gitdir_is_file:
            (d / ".git").write_text("gitdir: %s\n" % g)
        else:
            (d / ".git").mkdir()

    def collect(self, rows=None, rows2=None, platform="linux", run=None, **kw):
        seq = [rows if rows is not None else [], rows2 if rows2 is not None else (rows or [])]
        return RA.collect(now=NOW, home=str(self.home), platform=platform, list_procs=lambda: seq.pop(0),
                          classify=kw.pop("classify", lambda r: {}), run=run, sleep=lambda s: None, **kw)

    def codes(self, r):
        return [(i["code"], i["owner"]) for i in r["items"]]


class Worktrees(Base):
    def test_a1_worktrees(self):
        self.worktree("wt-chat-d07f9", 3600)
        self.worktree("old-tree-ee4f8", 3 * 86400)
        self.worktree("wt.noindex/inner-044e0", 60)
        self.worktree("plainrepo", 60, gitdir_is_file=False)
        (self.home / "linked").mkdir()
        os.symlink(str(self.home / ".gitdirs"), str(self.home / "linked" / ".git"))
        r = self.collect()
        w = [i for i in r["items"] if i["code"] == "worktree_not_noindex"]
        self.assertEqual(len(w), 1, r)
        m = w[0]["metric"]
        self.assertEqual((m["count"], m["recent_24h"], m["seats"], m["sample"]),
                         (2, 1, {"d07f9": 1, "ee4f8": 1}, ["wt-chat-d07f9"]))
        self.assertEqual(w[0]["severity"], "warn")
        self.assertIn("2 個", w[0]["hint"])


class Hooks(Base):
    def test_a2_hooks(self):
        lines = [summary([(SLOW, 9990), (GATE, 1500), (FAST, 90), (GONE, 3000), ("python3 /x/dropped.py", 5000)])
                 for _ in range(4)]
        lines.append(summary([(SLOW, 9995), (GATE, 1600), (FAST, 95), (GONE, 3100)]))
        self.transcript(lines)
        r = self.collect()
        got = {(i["code"], i["metric"].get("label")): i for i in r["items"]}
        kill = got[("hook_timeout_kill", "slow-gate.py")]
        self.assertEqual((kill["owner"], kill["metric"]["timeout_ms"], kill["severity"]), ("abcde", 10000, "hot"))
        slow = got[("hook_slow", GATE)]
        self.assertEqual((slow["metric"]["median_ms"], slow["metric"]["timeout_ms"]), (1500, 15000))
        gone = got[("hook_slow", "removed-gate.py")]
        self.assertEqual(gone["metric"]["removed"], 1)
        self.assertIn("開新對話", gone["hint"])
        self.assertNotIn(("hook_slow", "dropped.py"), got, "最新 summary 已經沒有的 hook 還被列出來")
        self.assertFalse([k for k in got if k[1] == "fast-gate.py"])
        self.assertEqual(r["items"][0]["code"], "hook_timeout_kill", "hot 沒排第一")

    def test_a2b_idle_transcript_ignored(self):
        self.transcript([summary([(SLOW, 9990)])] * 4, mtime=NOW - 3 * 3600)
        self.assertEqual(self.codes(self.collect()), [])

    def test_a3_tail_only(self):
        old = [summary([(SLOW, 9990), (FAST, 5)])] * 50
        filler = [json.dumps({"type": "user", "message": "x" * 900})] * 300
        new = [summary([(SLOW, 50), (FAST, 5)])] * 5
        p = self.transcript(old + filler + new)
        self.assertGreater(p.stat().st_size, 200 * 1024)
        items, _ = RA.check_transcripts(str(self.home / ".claude" / "projects"), str(self.home / ".claude" / "settings.json"),
                                        NOW, tail_per_file=64 * 1024, tail_total=1 << 20)
        self.assertEqual(items, [], "尾巴外的舊紀錄被讀進來了(全讀)")
        items, _ = RA.check_transcripts(str(self.home / ".claude" / "projects"), str(self.home / ".claude" / "settings.json"),
                                        NOW, tail_per_file=64 * 1024, tail_total=0)
        self.assertEqual(items, [], "合計上限 0 還在讀")

    def test_a4_huge(self):
        p = self.transcript([summary([(FAST, 5)])])
        with open(p, "r+b") as fh:
            fh.truncate(600 * 1024 * 1024)
        items, _ = RA.check_transcripts(str(self.home / ".claude" / "projects"), str(self.home / ".claude" / "settings.json"),
                                        NOW, tail_per_file=4096)
        self.assertEqual([(i["code"], i["owner"], i["metric"]["mb"]) for i in items], [("transcript_huge", "abcde", 600)])


class Launchd(Base):
    def test_a5_launchd(self):
        la = self.home / "Library" / "LaunchAgents"
        la.mkdir(parents=True)
        for lab in ("app.ez2.healthprobe", "app.ez2.ok", "app.ez2.running"):
            with open(la / (lab + ".plist"), "wb") as fh:
                plistlib.dump({"Label": lab, "ProgramArguments": ["/Users/u/secret.sh", "--token=abc"]}, fh)
        (la / "broken.plist").write_text("not a plist")
        out = "PID\tStatus\tLabel\n-\t1\tapp.ez2.healthprobe\n-\t0\tapp.ez2.ok\n123\t-15\tapp.ez2.running\n-\t78\tcom.apple.x\n"
        r = self.collect(platform="darwin", run=lambda cmd: out)
        self.assertEqual([(i["code"], i["metric"]) for i in r["items"]],
                         [("launchd_failing", {"label": "app.ez2.healthprobe", "exit_code": 1})])
        called = []
        r = self.collect(platform="linux", run=lambda cmd: called.append(cmd) or out)
        self.assertEqual((called, r["skipped"]), ([], [{"code": "launchd_failing", "detail": "unsupported_platform"}]))


def row(pid, ppid, cpu, name, args, uid=ME):
    return (pid, ppid, uid, cpu, 1024, name, args)


class Procs(Base):
    def test_a6_tests_and_browser(self):
        rows = [row(10, 1, 1, "claude", "claude"), row(11, 10, 50, "python3", "python3 /w/bin/aib test tests/a.py"),
                row(12, 11, 50, "python3", "python3 /w/bin/aib test tests/a.py --child"),
                row(13, 10, 50, "python3", "python3 /w/bin/aib test tests/b.py"),
                row(20, 1, 50, "python3", "python3 /w/bin/aib test x"), row(21, 1, 50, "python3", "python3 /w/bin/aib test y"),
                row(30, 1, 95, "plugin-container", "/Applications/Firefox.app/plugin-container -childID 3 secret"),
                row(31, 1, 95, "Google Chrome He", "/A/Google Chrome Helper (Renderer).app/x --type=renderer"),
                row(32, 1, 99, "plugin-container", "/x/plugin-container", uid=ME + 1)]
        rows2 = [r if r[0] != 31 else row(31, 1, 20, r[5], r[6]) for r in rows]
        cls = {11: ("aib_test", "d07f9"), 13: ("aib_test", "d07f9"), 20: ("aib_test", "?"), 21: ("aib_test", "?")}
        r = self.collect(rows=rows, rows2=rows2, classify=lambda rr: cls)
        tp = [i for i in r["items"] if i["code"] == "test_parallel"]
        self.assertEqual([(i["owner"], i["metric"]["runs"]) for i in tp], [("d07f9", 2), ("", 4)])
        br = [i for i in r["items"] if i["code"] == "browser_tab_hot"]
        self.assertEqual([(i["metric"]["program"], i["metric"]["cpu"]) for i in br], [("plugin-container", 95.0)])


class Cache(Base):
    def test_a7_cache(self):
        n = {"c": 0}

        def fake_collect(now=None, **kw):
            n["c"] += 1
            return {"schema": "aib-heat-advice/v1", "state": "ok", "t": round(now, 1), "unmeasured": [], "skipped": [],
                    "items": [{"code": "launchd_failing", "severity": "warn", "owner": "",
                               "metric": {"label": "app.x", "exit_code": 1}, "hint": "h"}]}
        with mock.patch.object(RA, "collect", fake_collect):
            r1 = RA.advice(now=1000.0)
            r2 = RA.advice(now=1030.0)
            self.assertEqual((n["c"], r1["cached"], r2["cached"]), (1, False, True), "60 秒內又重掃")
            path = Path(RH.state_dir()) / RA.CACHE_FILE
            evil = json.loads(path.read_text())
            evil["items"] += [{"code": "rm_rf", "severity": "hot", "owner": "", "metric": {}},
                              {"code": "hook_slow", "severity": "warn", "owner": "a/b\x1b[2J",
                               "metric": {"label": "/Users/u/x.py --token=abc\x1b]0;", "median_ms": 5, "cmd": ["/Users/u"]},
                               "hint": "/Users/u/secret"}]
            path.write_text(json.dumps(evil))
            r3 = RA.advice(now=1040.0)
            self.assertEqual([i["code"] for i in r3["items"]], ["launchd_failing", "hook_slow"])
            blob = json.dumps(r3, ensure_ascii=False)
            for leak in ("/Users", "\x1b", "\\u001b"):
                self.assertNotIn(leak, blob)
            r4 = RA.advice(now=1061.0)
            self.assertEqual((n["c"], r4["cached"]), (2, False), "過 60 秒沒重掃")


class Privacy(Base):
    def test_a8_privacy(self):
        self.worktree("wt-x-d07f9", 60)
        self.transcript([summary([(SLOW, 9990), (FAST, 2000)])] * 4)
        la = self.home / "Library" / "LaunchAgents"
        la.mkdir(parents=True)
        with open(la / "a.plist", "wb") as fh:
            plistlib.dump({"Label": "app.x", "ProgramArguments": ["/Users/u/token.sh"]}, fh)
        rows = [row(30, 1, 95, "plugin-container", "/Users/u/Firefox/plugin-container --token=abc")]
        r = self.collect(rows=rows, platform="darwin", run=lambda c: "-\t2\tapp.x\n")
        self.assertEqual(sorted({i["code"] for i in r["items"]}),
                         ["browser_tab_hot", "hook_slow", "hook_timeout_kill", "launchd_failing", "worktree_not_noindex"])
        blob = json.dumps(r, ensure_ascii=False)
        for leak in ("/Users", "token", "abc123", "secret", str(self.home)):
            self.assertNotIn(leak, blob)
        for i in r["items"]:
            for name in (i["metric"].get("label"), i["metric"].get("program")):
                if name:
                    self.assertNotIn(name, i["hint"], "E6:hint 夾了名字")

    def test_a8b_line_cap_and_owner(self):
        big = summary([(SLOW, 9990)]).replace('"cwd": "/Users/u/secret"', '"pad": "%s"' % ("x" * (1024 * 1024 + 10)))
        self.assertEqual(RA.hook_durations(big + "\n"), [], "E1:單行 > 1 MB 沒丟")
        p = self.transcript([summary([(SLOW, 9990)])] * 4)
        with mock.patch.object(os, "getuid", lambda: os.stat(p).st_uid + 1):
            self.assertEqual(RA.read_tail(str(p), 4096), "", "E1:別人的檔也讀了")


class Cli(Base):
    def test_a9_cli(self):
        fake = {"schema": "aib-heat-advice/v1", "state": "ok", "t": 1.0, "cached": False,
                "items": [{"code": "transcript_huge", "severity": "warn", "owner": "74e8b", "metric": {"mb": 1708},
                           "hint": RA.hint_of("transcript_huge", {"mb": 1708})}],
                "unmeasured": [{"code": "hook_slow", "detail": "measure_failed"}],
                "skipped": [{"code": "launchd_failing", "detail": "unsupported_platform"}]}
        with mock.patch.object(RA, "advice", lambda **k: fake):
            out = io.StringIO()
            self.assertEqual(RH.main(["advice", "--json"], out=out), 0)
            self.assertEqual(json.loads(out.getvalue())["items"][0]["metric"]["mb"], 1708)
            out = io.StringIO()
            RH.main(["advice"], out=out)
            t = out.getvalue()
            for s in ("1 條", "@74e8b", "1708 MB", "量不到:hook_slow", "不支援:launchd_failing"):
                self.assertIn(s, t)


KNIVES = [
    ("K1", "src/cockpit/room_heat_advice.py", "        start = max(0, st.st_size - limit)\n", "        start = 0\n",
     "Hooks.test_a3_tail_only"),
    ("K2", "src/cockpit/room_heat_advice.py", "CACHE_S = 60\n", "CACHE_S = 0\n", "Cache.test_a7_cache"),
    ("K3", "src/cockpit/room_heat_advice.py", "    scripts = SCRIPT_RE.findall(cmd or \"\")\n",
     "    return cmd\n    scripts = SCRIPT_RE.findall(cmd or \"\")\n", "Privacy.test_a8_privacy"),
    ("K4", "src/cockpit/room_heat_advice.py", "    return [(c, ms) for c, ms in out if c in latest]\n", "    return out\n",
     "Hooks.test_a2_hooks"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
