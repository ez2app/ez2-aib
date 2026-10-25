#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat tidy(10-25 @044e0,Andy 准「先做第一組」):孤兒常駐只通知、已合併舊工作樹封存。
全在暫存狀態根 + 暫存 git 倉(真 git、假遠端)+ 假行程表跑;不碰真的 ~/wt.noindex、不殺任何行程。
T1 工作樹狀態：已合併 ⇒ archivable;沒合 ⇒ not_merged;有改動 ⇒ dirty(node_modules 捷徑不算);行程 cwd 在裡面 ⇒ busy;
   lock 過 ⇒ locked;2 小時內動過 ⇒ recent;主人 = 樹名裡的五碼席碼;不是 git 工作樹的資料夾 / 捷徑不列
T2 封存:--all-mine 只搬自己席 archivable 的;搬到 wt-<席>-merged-<日期>/,同名加 -2;prune 後 git 不再列;
   紀錄檔 0600;別席的編號 ⇒ not_mine;不認得的 ⇒ candidate_changed;--dry-run 不動;席碼 / 編號格式錯 ⇒ bad_candidate
T3 執行前重掃：列出時可封存、封存前變 dirty ⇒ dirty,不搬
T4 孤兒常駐：自己 uid + 父行程 1 + 腳本在工作樹根底下 + 活過 1 小時才算;子孫數 / CPU 加總;
   別的 uid、父行程不是 1、identity 對不上、太年輕、腳本不在根底下、-c 跑的 ⇒ 不算;輸出只有檔名、沒有路徑
T5 通知：同主人合成一封;6 小時內同一支不重發;認不出主人 ⇒ owner_unknown 不寄;寄失敗 ⇒ notify_failed 且不記;試跑不記
T5b popup:全部(含認不出主人)合成一則本機通知、不寄 dm;dm 與 popup 各自去重;AppleScript 字串只走 argv;
    dm 地址補艙位碼(席位外光席碼會被擋);找不到活的艙 ⇒ 不叫 aib dm
T6 busy 也看正在跑的檔案(常駐腳本 cwd 在家目錄、樹裡的檔案還在跑);lsof / ps 量不到 ⇒ 全部當 busy
T7 CLI:`aib room heat tidy --json` 走 room_heat_tidy;archive 沒帶 --seat / 兩種都帶 ⇒ 用法 rc 2
T1b stash 清單有這條分支存的 ⇒ stashed;node_modules 是真資料夾(不是捷徑)⇒ dirty
T2b 搬前寫 <名>.RESTORE.txt(0600,放樹旁邊);照它的 git 指令做一次 ⇒ 樹回來、分支對
T1c r1:有子模組 ⇒ submodule;detached 存的 stash(第一父 = 頭碼)⇒ stashed;樹名有控制字元 ⇒ bad_name
T2c r1:封存只刪自己那棵的登記,別棵失聯樹的登記還在;分支被刪了照還原檔(-B)還原得回來
T9 判合併前 fetch 一次:別台合進 main、本機 origin/main 還舊 ⇒ fetch=False 是 not_merged、預設 fetch 後 archivable
T8 隱私:--json 不出現全路徑、內部欄位(_path / _common)
刀(複本 + run_guarded):K1 不看合併 ⇒ T1 紅;K2 不核主人 ⇒ T2 紅;K3 identity 不核父行程 1(ps 之後被重用)⇒ T4 紅;K4 不去重 ⇒ T5 紅;
K5 不看正在跑的檔案 ⇒ T6 紅;K6 不看 stash ⇒ T1 紅;K7 不寫還原檔 ⇒ T2 紅;K8 不 fetch ⇒ T9 紅;K9 dm 地址只寫席碼 ⇒ T5b 紅;K10 不看子模組 / K13 stash 不比第一父 / K14 不擋怪樹名 ⇒ T1 紅;
K11 改回全倉 prune / K12 還原不帶 -B ⇒ T2 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import io
import json
import os
import shutil
import stat
import subprocess
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
import room_heat_tidy as TD  # noqa: E402

ME = os.getuid()
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
LATER = 3 * 3600


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   env={**os.environ, **GIT_ENV})


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-heat-tidy-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        (self.d / "home").mkdir()
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.d / "state"), "HOME": str(self.d / "home"), **GIT_ENV})
        p.start()
        self.addCleanup(p.stop)
        self.wt = self.d / "wt"
        self.backup = self.d / "backup"
        self.wt.mkdir()

    def make_repo(self):
        """假遠端 + 主倉;回主倉路徑。main 上有 merged-* 兩條分支合進去,open-* 沒合。"""
        bare, main = self.d / "origin.git", self.d / "main"
        git(self.d, "init", "-q", "--bare", "-b", "main", str(bare))
        git(self.d, "clone", "-q", str(bare), str(main))
        git(main, "commit", "-q", "--allow-empty", "-m", "base")
        git(main, "push", "-q", "origin", "HEAD:main")
        git(main, "checkout", "-q", "-b", "open-x", "main")        # 在子模組那條合進來之前分出去
        git(main, "commit", "-q", "--allow-empty", "-m", "open")
        git(main, "checkout", "-q", "main")
        for b in ("merged-a", "merged-b", "merged-c", "merged-d", "merged-e", "merged-f", "merged-g", "merged-h",
                  "merged-j", "merged-k", "merged-l", "merged-i"):     # 帶子模組的那條最後合(別條不會繼承到)
            git(main, "checkout", "-q", "-b", b, "main")
            if b == "merged-i":                                  # 這條分支帶子模組設定檔
                (main / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = ../x.git\n')
                git(main, "add", ".gitmodules")
            git(main, "commit", "-q", "--allow-empty", "-m", b)
            git(main, "checkout", "-q", "main")
            git(main, "merge", "-q", "--no-ff", "-m", "收 " + b, b)
        git(main, "push", "-q", "origin", "main")
        git(main, "fetch", "-q", "origin")
        return main

    def add_tree(self, main, name, branch):
        git(main, "worktree", "add", "-q", str(self.wt / name), branch)
        return self.wt / name

    def scan(self, now_off=LATER, cwds=frozenset()):
        return TD.worktrees(root=str(self.wt), cwds=set(cwds), now=time.time() + now_off)


class Trees(Base):
    def setUp(self):
        super().setUp()
        main = self.make_repo()
        self.main = main
        self.add_tree(main, "a-044e0", "merged-a")
        self.add_tree(main, "x-044e0", "open-x")
        t = self.add_tree(main, "dirty-044e0", "merged-b")
        (t / "new.txt").write_text("x")
        t = self.add_tree(main, "nm-044e0", "merged-c")
        os.symlink(str(self.d), str(t / "node_modules"))
        self.busy = self.add_tree(main, "busy-044e0", "merged-d")
        self.add_tree(main, "lock-044e0", "merged-e")
        git(main, "worktree", "lock", str(self.wt / "lock-044e0"))
        self.add_tree(main, "other-abb89", "merged-f")
        t = self.add_tree(main, "stash-044e0", "merged-g")
        (t / "w.txt").write_text("w")
        git(t, "add", "w.txt")
        git(t, "stash", "-q")
        t = self.add_tree(main, "nmdir-044e0", "merged-h")
        (t / "node_modules").mkdir()
        (t / "node_modules" / "x").write_text("x")
        self.add_tree(main, "sub-044e0", "merged-i")
        git(main, "worktree", "add", "-q", "--detach", str(self.wt / "det-044e0"), "merged-j")
        t = self.wt / "det-044e0"
        (t / "d.txt").write_text("d")
        git(t, "add", "d.txt")
        git(t, "stash", "-q")                                    # detached 存的:訊息是「WIP on (no branch)」
        lost = self.add_tree(main, "lost-044e0", "merged-k")
        self.lost_to = self.d / "elsewhere" / "lost-044e0"
        self.lost_to.parent.mkdir()
        os.rename(str(lost), str(self.lost_to))                  # 別席暫時搬開的樹:登記還在、資料夾不在原處
        self.add_tree(main, "bad\x07-044e0", "merged-l")
        (self.wt / "plain").mkdir()
        os.symlink(str(self.wt / "a-044e0"), str(self.wt / "link-044e0"))

    def by_name(self, trees):
        return {w["name"]: w for w in trees}

    def test_t1_states(self):
        got = self.by_name(self.scan(cwds={str(self.busy / "sub")}))
        self.assertEqual(sorted(got), ["a-044e0", "bad-044e0", "busy-044e0", "det-044e0", "dirty-044e0", "lock-044e0", "nm-044e0",
                                       "nmdir-044e0", "other-abb89", "stash-044e0", "sub-044e0", "x-044e0"])
        self.assertEqual(got["sub-044e0"]["reason"], "submodule")
        self.assertEqual(got["det-044e0"]["reason"], "stashed")
        self.assertEqual(got["bad-044e0"]["reason"], "bad_name")
        self.assertEqual(got["stash-044e0"]["reason"], "stashed")
        self.assertEqual(got["nmdir-044e0"]["reason"], "dirty")         # node_modules 是真資料夾 ⇒ 不是捷徑 ⇒ 算改動
        self.assertEqual(got["a-044e0"]["state"], "archivable")
        self.assertIsNone(got["a-044e0"]["reason"])
        self.assertEqual(got["nm-044e0"]["state"], "archivable")
        self.assertEqual(got["x-044e0"]["reason"], "not_merged")
        self.assertFalse(got["x-044e0"]["merged"])
        self.assertEqual(got["dirty-044e0"]["reason"], "dirty")
        self.assertEqual(got["busy-044e0"]["reason"], "busy")
        self.assertEqual(got["lock-044e0"]["reason"], "locked")
        self.assertEqual(got["other-abb89"]["owner"], "abb89")
        self.assertEqual(got["a-044e0"]["owner"], "044e0")
        self.assertEqual(got["a-044e0"]["branch"], "merged-a")
        recent = self.by_name(self.scan(now_off=0))
        self.assertEqual(recent["a-044e0"]["reason"], "recent")
        self.assertEqual(TD.owner_of_tree("int0155-abb89"), "abb89")
        self.assertEqual(TD.owner_of_tree("aib-plugins-wt-74e8b-lows147"), "74e8b")
        self.assertEqual(TD.owner_of_tree("ez2-ai-wt-baseline0827"), "unknown")

    def test_t2_archive(self):
        scan = lambda: self.scan(cwds={str(self.busy)})   # noqa: E731
        trees = self.by_name(scan())
        now = time.mktime((2026, 10, 25, 12, 0, 0, 0, 0, -1))
        kw = dict(scan_fn=scan, now=now, backup_root=str(self.backup))
        dry = TD.archive(all_mine=True, seat="044e0", dry_run=True, **kw)
        self.assertEqual(sorted(r["name"] for r in dry["results"]), ["a-044e0", "nm-044e0"])
        self.assertTrue(all(r["detail"] == "would_archive" for r in dry["results"]))
        self.assertTrue((self.wt / "a-044e0").exists())
        dest = self.backup / "wt-044e0-merged-20261025"
        dest.mkdir(parents=True)
        (dest / "a-044e0").mkdir()                           # 同名已有 ⇒ 加 -2
        r = TD.archive(ids=[trees["other-abb89"]["id"], "0" * 16], seat="044e0", **kw)
        self.assertEqual([x["detail"] for x in r["results"]], ["not_mine", "candidate_changed"])
        self.assertFalse(r["ok"])
        self.assertTrue((self.wt / "other-abb89").exists())
        r = TD.archive(all_mine=True, seat="044e0", **kw)
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(x["detail"] for x in r["results"]), ["archived", "archived"])
        self.assertTrue((dest / "a-044e0-2" / ".git").exists())
        restore = dest / "a-044e0-2.RESTORE.txt"
        self.assertTrue(restore.is_file(), "搬之前要先寫還原檔")
        self.assertEqual(stat.S_IMODE(restore.stat().st_mode), 0o600)
        self.assertTrue((dest / "nm-044e0.RESTORE.txt").exists())
        self.assertTrue((dest / "nm-044e0").exists())
        self.assertFalse((self.wt / "a-044e0").exists())
        self.assertTrue((self.wt / "x-044e0").exists() and (self.wt / "dirty-044e0").exists())
        listed = subprocess.run(["git", "-C", str(self.main), "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
        paths = [l[len("worktree "):] for l in listed.splitlines() if l.startswith("worktree ")]
        self.assertNotIn(str(self.wt / "a-044e0"), paths)        # 比整條路徑:「bad\a-044e0」裡也有 a-044e0 這串字(ubuntu CI 踩過)
        self.assertNotIn(str(self.wt / "nm-044e0"), paths)
        listed = subprocess.run(["git", "-C", str(self.main), "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
        self.assertIn("lost-044e0", listed)                     # 別棵失聯樹的登記不准被清(不跑全倉 prune)
        git(self.main, "branch", "-D", "merged-a")              # 分支被刪了也要還原得回來(-B)
        cmd = [l for l in restore.read_text().splitlines() if l.startswith("git ")][0]
        rr = subprocess.run(cmd, shell=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV})   # 照還原檔做一次
        self.assertEqual(rr.returncode, 0, rr.stderr)
        self.assertTrue((self.wt / "a-044e0" / ".git").is_file())
        head = subprocess.run(["git", "-C", str(self.wt / "a-044e0"), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(head.strip(), "merged-a")
        log = Path(RH.state_dir()) / "heat-tidy.jsonl"
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        recs = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertEqual({x["name"] for x in recs}, {"a-044e0", "nm-044e0"})
        self.assertNotIn(str(self.d), log.read_text())
        self.assertEqual(TD.archive(all_mine=True, seat="044E0", **kw)["detail"], "bad_candidate")
        self.assertEqual(TD.archive(ids=["../x"], seat="044e0", **kw)["detail"], "bad_candidate")

    def test_t3_rescan_before_move(self):
        trees = self.by_name(self.scan())
        (self.wt / "a-044e0" / "late.txt").write_text("y")
        r = TD.archive(ids=[trees["a-044e0"]["id"]], seat="044e0", scan_fn=self.scan, backup_root=str(self.backup))
        self.assertEqual(r["results"][0]["detail"], "dirty")
        self.assertTrue((self.wt / "a-044e0").exists())
        self.assertFalse(self.backup.exists())

    def test_t8_privacy(self):
        s = TD.snapshot(orphan_fn=lambda: [], tree_fn=self.scan)
        txt = json.dumps(s, ensure_ascii=False)
        self.assertNotIn(str(self.d), txt)
        self.assertNotIn("_path", txt)
        self.assertNotIn("_common", txt)
        self.assertEqual(s["archivable"], 4)
        mine = TD.snapshot(seat="abb89", orphan_fn=lambda: [], tree_fn=self.scan)
        self.assertEqual([w["name"] for w in mine["worktrees"]], ["other-abb89"])


class Fetch(Base):
    def test_t9_fetch_before_judging(self):
        main = self.make_repo()
        self.add_tree(main, "x-044e0", "open-x")
        git(main, "push", "-q", "origin", "open-x")
        other = self.d / "other"
        git(self.d, "clone", "-q", str(self.d / "origin.git"), str(other))
        git(other, "fetch", "-q", "origin", "open-x:open-x")
        git(other, "merge", "-q", "--no-ff", "-m", "收 open-x", "open-x")
        git(other, "push", "-q", "origin", "HEAD:main")        # 別台合進去了,本機的 origin/main 還是舊的
        kw = dict(root=str(self.wt), cwds=set(), now=time.time() + LATER)
        self.assertEqual(TD.worktrees(fetch=False, **kw)[0]["reason"], "not_merged")
        self.assertEqual(TD.worktrees(**kw)[0]["state"], "archivable")
        self.assertEqual(TD.GIT_ENV["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", TD.GIT_ENV["GIT_SSH_COMMAND"])


class Orphans(Base):
    def rows_and_ident(self):
        wt = str(self.wt)
        now = 10_000_000.0
        rows = [
            (101, 1, ME, 2.0, 1, "Python", "/usr/bin/python3 %s/int0155-abb89/aib/src/cockpit/chat_bridge.py run --token SEKRIT" % wt),
            (102, 101, ME, 1.5, 1, "Python", "/usr/bin/python3 %s/int0155-abb89/aib/src/cockpit/chat_nostr.py serve" % wt),
            (103, 102, ME, 0.5, 1, "sleep", "sleep 99"),
            (201, 1, ME, 1.0, 1, "Python", "/usr/bin/python3 /opt/elsewhere/daemon.py"),
            (202, 1, ME, 1.0, 1, "Python", "/usr/bin/python3 %s/young-d07f9/x.py" % wt),
            (203, 1, 0, 1.0, 1, "Python", "/usr/bin/python3 %s/root-d07f9/x.py" % wt),
            (204, 77, ME, 1.0, 1, "Python", "/usr/bin/python3 %s/child-d07f9/x.py" % wt),
            (205, 1, ME, 1.0, 1, "Python", "/usr/bin/python3 -c import x %s/c-d07f9/x.py" % wt),
            (206, 1, ME, 1.0, 1, "Python", "/usr/bin/python3 %s/swap-d07f9/x.py" % wt),
            (207, 1, ME, 1.0, 1, "node", "/usr/bin/node %s/plain/x.js" % wt),
        ]
        start = {101: now - 36000, 202: now - 60, 206: now - 36000, 207: now - 7200, 203: now - 36000, 205: now - 36000}
        def ident(pid):
            if pid == 206:
                return (ME, 999, "x", start[pid])               # 讀完 ps 之後被重用 / 換了父行程
            if pid not in start:
                return None
            return (ME if pid != 203 else 0, 1, "%d.000000" % start[pid], start[pid])
        return rows, ident, now

    def test_t4_orphans(self):
        rows, ident, now = self.rows_and_ident()
        got = TD.orphans(rows=rows, root=str(self.wt), uid=ME, ident=ident, now=now)
        self.assertEqual([o["pid"] for o in got], [101, 207])
        o = got[0]
        self.assertEqual((o["owner"], o["tree"], o["program"], o["children"]), ("abb89", "int0155-abb89", "chat_bridge.py", 2))
        self.assertAlmostEqual(o["cpu"], 4.0)
        self.assertEqual(o["age_s"], 36000)
        self.assertEqual(got[1]["owner"], "unknown")
        txt = json.dumps(got)
        self.assertNotIn("SEKRIT", txt)
        self.assertNotIn(str(self.wt), txt)
        with mock.patch.object(RH, "list_procs", return_value=None):
            self.assertIsNone(TD.orphans(root=str(self.wt)))

    def test_t5_notify(self):
        found = [{"id": "a" * 16, "pid": 1, "owner": "abb89", "tree": "t-abb89", "program": "p.py", "age_s": 7200, "cpu": 1.0, "children": 0},
                 {"id": "b" * 16, "pid": 2, "owner": "abb89", "tree": "t-abb89", "program": "q.py", "age_s": 7200, "cpu": 1.0, "children": 1},
                 {"id": "c" * 16, "pid": 3, "owner": "unknown", "tree": "x", "program": "r.py", "age_s": 7200, "cpu": 1.0, "children": 0}]
        sent = []
        send = lambda owner, text: sent.append((owner, text)) or True   # noqa: E731
        dry = TD.notify(dry_run=True, scan_fn=lambda: found, send=send, now=1000.0)
        self.assertEqual(sent, [])
        self.assertEqual(sorted(r["detail"] for r in dry["results"]), ["owner_unknown", "would_notify", "would_notify"])
        r = TD.notify(scan_fn=lambda: found, send=send, now=1000.0)
        self.assertTrue(r["ok"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "abb89")
        self.assertIn("daemon_orphan ×2", sent[0][1])
        self.assertIn("p.py", sent[0][1])
        r = TD.notify(scan_fn=lambda: found, send=send, now=1000.0 + 3600)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sorted(x["detail"] for x in r["results"]), ["already_notified", "already_notified", "owner_unknown"])
        TD.notify(scan_fn=lambda: found, send=send, now=1000.0 + TD.NOTIFY_EVERY_S + 1)
        self.assertEqual(len(sent), 2)
        bad = TD.notify(scan_fn=lambda: [dict(found[0], id="d" * 16)], send=lambda o, t: False, now=1000.0)
        self.assertEqual(bad["results"][0]["detail"], "notify_failed")
        self.assertFalse(bad["ok"])
        self.assertNotIn("d" * 16, TD._read_notified())
        self.assertEqual(TD.notify(scan_fn=lambda: None, send=send)["detail"], "scan_failed")

    def test_t5b_popup_and_address(self):
        found = [{"id": "a" * 16, "pid": 1, "owner": "abb89", "tree": "t-abb89", "program": "p.py", "age_s": 7200, "cpu": 1.0, "children": 0},
                 {"id": "c" * 16, "pid": 3, "owner": "unknown", "tree": "x", "program": "r.py", "age_s": 7200, "cpu": 1.0, "children": 0}]
        pops = []
        pop = lambda title, body: pops.append((title, body)) or True   # noqa: E731
        r = TD.notify(scan_fn=lambda: found, popup=pop, now=1000.0, via="popup", send=lambda o, t: self.fail("popup 不該寄 dm"))
        self.assertEqual(len(pops), 1)                                      # 全部合成一則,含認不出主人的
        self.assertIn("2", pops[0][0])
        self.assertIn("@unknown", pops[0][1])
        self.assertEqual({x["detail"] for x in r["results"]}, {"notified"})
        TD.notify(scan_fn=lambda: found, popup=pop, now=1000.0 + 60, via="popup")
        self.assertEqual(len(pops), 1)                                      # 6 小時去重
        sent = []
        TD.notify(scan_fn=lambda: found, send=lambda o, t: sent.append(o) or True, now=1000.0 + 60)
        self.assertEqual(sent, ["abb89"])                                   # dm 跟 popup 各自去重
        calls = []
        run = lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0)   # noqa: E731
        evil = 'x" & (do shell script "touch /tmp/pwn") & "'
        self.assertTrue(TD._popup("T", evil, platform="darwin", run=run))
        script = " ".join(calls[0][:calls[0].index("end run") + 1])
        self.assertNotIn(evil, script)                                      # 字串只走 argv,不拼進 AppleScript
        self.assertEqual(calls[0][-2:], ["T", evil])
        self.assertFalse(TD._popup("T", "b", platform="win32", run=run))
        import dm_address
        with mock.patch.object(dm_address, "lookup", return_value=[("aihub-20261001-000000-dead", False)]):
            self.assertIsNone(TD.address_of("abb89"))                       # 只有關掉的艙 ⇒ 不寄
        with mock.patch.object(dm_address, "lookup", return_value=[("aihub-20261014-133230-6ddf", True)]):
            self.assertEqual(TD.address_of("abb89"), "6ddf/abb89")          # 光席碼在席位外會被 aib dm 擋(10-25 實測)
        with mock.patch.object(dm_address, "lookup", return_value=[]), mock.patch.object(TD.subprocess, "run") as m:
            self.assertFalse(TD._send_dm("abb89", "x"))
            m.assert_not_called()


class Busy(Base):
    def test_t6_running_files_count_as_busy(self):
        tree = self.wt / "t-044e0"
        lsof = lambda cmd, timeout=None: "p1\nn%s\n" % self.d   # noqa: E731
        rows = [(9, 1, ME, 0.0, 1, "Python", "/usr/bin/python3 %s/aib/src/x.py run" % tree),
                (10, 1, 0, 0.0, 1, "Python", "/usr/bin/python3 %s/other/y.py" % self.wt)]
        paths = TD._busy_cwds(run=lsof, rows=rows)
        self.assertIn(str(self.d), paths)
        self.assertIn(os.path.realpath("%s/aib/src/x.py" % tree), paths)
        self.assertNotIn(os.path.realpath("%s/other/y.py" % self.wt), paths)
        self.assertIsNone(TD._busy_cwds(run=lambda cmd, timeout=None: "", rows=rows))
        with mock.patch.object(RH, "list_procs", return_value=None):
            self.assertIsNone(TD._busy_cwds(run=lsof))


class Cli(Base):
    def test_t7_cli(self):
        out = io.StringIO()
        snap = {"schema": "aib-heat-tidy/v1", "state": "ok", "orphans": [], "worktrees": [], "archivable": 0}
        with mock.patch.object(TD, "snapshot", return_value=snap) as m:
            self.assertEqual(RH.main(["tidy", "--json", "--seat", "044e0"], out=out), 0)
        m.assert_called_once_with(seat="044e0")
        self.assertEqual(json.loads(out.getvalue())["schema"], "aib-heat-tidy/v1")
        for argv in (["tidy", "archive", "--all-mine"], ["tidy", "archive", "a" * 16, "--all-mine", "--seat", "044e0"]):
            out = io.StringIO()
            self.assertEqual(RH.main(argv, out=out), 2)
            self.assertIn("用法", out.getvalue())
        with mock.patch.object(TD, "archive", return_value={"ok": True, "results": []}) as m:
            self.assertEqual(RH.main(["tidy", "archive", "--all-mine", "--seat", "044e0", "--dry-run"], out=io.StringIO()), 0)
        m.assert_called_once_with(ids=[], all_mine=True, seat="044e0", dry_run=True)


KNIVES = [
    ("K1", "src/cockpit/room_heat_tidy.py", "    merged = git(path, \"merge-base\", \"--is-ancestor\", head, \"origin/main\")[0] == 0\n",
     "    merged = True\n", "Trees.test_t1_states"),
    ("K2", "src/cockpit/room_heat_tidy.py", "        elif w[\"owner\"] != seat:\n", "        elif False:\n",
     "Trees.test_t2_archive"),
    ("K3", "src/cockpit/room_heat_tidy.py", "        if not idt or idt[0] != uid or idt[1] != 1:\n", "        if not idt or idt[0] != uid:\n",
     "Orphans.test_t4_orphans"),
    ("K4", "src/cockpit/room_heat_tidy.py", "        if o[\"id\"] in seen:\n", "        if False:\n",
     "Orphans.test_t5_notify"),
    ("K5", "src/cockpit/room_heat_tidy.py", "            paths.update(os.path.realpath(t) for t in RR._argv(r[6]) if t.startswith(\"/\"))\n",
     "            pass\n", "Busy.test_t6_running_files_count_as_busy"),
    ("K6", "src/cockpit/room_heat_tidy.py", "\"stashed\" if stashed else ", "", "Trees.test_t1_states"),
    ("K7", "src/cockpit/room_heat_tidy.py", "            _write_restore(dest + \".RESTORE.txt\", w, dest)\n", "", "Trees.test_t2_archive"),
    ("K8", "src/cockpit/room_heat_tidy.py", "                git(path, \"fetch\", \"-q\", \"origin\", \"main\", timeout=60)\n", "                pass\n",
     "Fetch.test_t9_fetch_before_judging"),
    ("K9", "src/cockpit/room_heat_tidy.py", "    return \"%s/%s\" % (m.group(1), owner) if m else None\n", "    return owner\n",
     "Orphans.test_t5b_popup_and_address"),
    ("K10", "src/cockpit/room_heat_tidy.py", "\"submodule\" if submodule else ", "", "Trees.test_t1_states"),
    ("K11", "src/cockpit/room_heat_tidy.py", "        _drop_admin(w)\n", "        git(w[\"_common\"], \"worktree\", \"prune\")\n", "Trees.test_t2_archive"),
    ("K12", "src/cockpit/room_heat_tidy.py", "\"-B %s %s %s\" % (_q(w[\"_branch\"]), _q(w[\"_path\"]), _q(w[\"_head\"]))",
     "\"%s %s\" % (_q(w[\"_path\"]), _q(w[\"_branch\"]))", "Trees.test_t2_archive"),
    ("K13", "src/cockpit/room_heat_tidy.py", "    return first == head or (", "    return (", "Trees.test_t1_states"),
    ("K14", "src/cockpit/room_heat_tidy.py", "        if name != RH.safe_name(name, 255):\n", "        if False:\n", "Trees.test_t1_states"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=180)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
