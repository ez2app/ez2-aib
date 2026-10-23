#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib update auto(10-23 Andy「更新版本要自動 aib update」;@74e8b)。

S1 設定:沒檔 = 開(預設);off 寫 on:false 並拆排程;on 寫回開並裝排程;檔壞 ⇒ 當成關(unreadable);關著 check 不動
S2 排程 plist:Label、ProgramArguments = check --why schedule、StartInterval 6 小時、RunAtLoad 關、權限 0600、
   環境只帶路徑 / 語系(AIB_INSTALL_URL、token 一律不帶)
S3 非 macOS:不裝排程、不叫 launchctl;cmd_on 照樣 rc 0;開席那次 check 也不裝
S4 開發用 worktree:不裝排程、check 回 dev_tree、不更新
G1 測試租約有人拿著(真的 host_lease acquire)⇒ refused_lease、不更新
G2 安裝目錄有沒提交的改動 ⇒ refused_dirty、不更新
G3 一小時內才更新過 ⇒ too_soon;開席 10 分鐘內查過 ⇒ checked_recently(不連網路)
G4 正在跑 aib test / 推包 / git push ⇒ refused_tests / refused_shipping / refused_push;自己的 pid 不算
G5 只快轉:本機有自己的提交(歷史分叉)⇒ refused_diverged;在別的分支 ⇒ refused_branch;本機比正式版新 ⇒ up_to_date
G6 安全 ⇒ 跑一次 update(子環境沒有 AIB_INSTALL_URL)、拿租約再放掉、記 last_update、艦長室事件只帶版號、叫 room reload;
   doctor 一行「已自動更新到」;接著再查 ⇒ too_soon,過兩小時 ⇒ up_to_date
G7 落後太多(大 / 中版號不同,或差 5 個小版以上)⇒ refused_far_behind;連不上來源 ⇒ offline;AIB_AUTO_UPDATE=0 ⇒ 什麼都不做
P1 外掛簽章驗不過 ⇒ 不列進落後清單、不裝;install_plugin 遇到沒簽的直接 rc 3、不叫 aib_ext.install
P2 簽章驗過、版本較舊 ⇒ 列進落後清單;核心不用更新時由 check 裝;install_plugin 一律 allow_unsigned=False
P3 核心有更新時,外掛交給一行安裝(新版程式)的 aib ext auto,舊碼不自己裝
I1 四語字典同鍵、沒有 !!;aib update auto status / --json 走得通(子行程,暫存狀態根)
"""
import importlib
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import auto_update as AU  # noqa: E402
import room_events as RE  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_NOSYSTEM": "1"}
NOW = 1_800_000_000.0


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=60)
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


class FakeCtl:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "bootstrap" and self.fail:
            return 1, "Input/output error"
        return 0, ""


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-autoupd-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.home = self.td / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "EZ2_AIB_HOME": str(self.td / "state"),
                    "EZ2_AIB_LOCALE": "zh-TW", "AIB_HOST_LEASE_FILE": str(self.td / "lease" / "host-lease.json")}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.updates, self.leases, self.rooms, self.installs = [], [], [], []

    # 假的「一行安裝」:跟 install.sh 一樣只快轉到最新 v* 標籤
    def fake_update(self, env, root):
        self.updates.append(dict(env))
        tag = git(root, "tag", "-l", "v[0-9]*", "--sort=-v:refname").splitlines()[0]
        git(root, "merge", "-q", "--ff-only", tag)
        return 0

    def fake_lease(self, action, rid, env):
        self.leases.append(action)
        return 0

    def fake_room(self, env):
        self.rooms.append(1)
        return "same"

    def fake_install(self, folder, out):
        self.installs.append(folder)
        return 0

    def run_check(self, **kw):
        args = dict(env=self.env, root=self.inst, now=NOW, platform="linux", home=self.home, rows=[],
                    do_fetch=AU.fetch, run_update=self.fake_update, lease=self.fake_lease, install=self.fake_install,
                    ext_base=self.td / "noext", room=self.fake_room, dev_tree=False, lease_state=lambda e, n: None)
        args.update(kw)
        return AU.check(**args)

    def make_repos(self, have="0.1.1", latest="0.1.2"):
        """origin(bare)+ 安裝目錄 inst(在 main、停在 v<have>);origin 再多一個 v<latest>。"""
        src, bare, self.inst = self.td / "src", self.td / "origin.git", self.td / "inst"
        src.mkdir()
        git(src, "-c", "init.defaultBranch=main", "init", "-q")
        (src / "aib").mkdir()
        (src / "aib" / "VERSION").write_text(have + "\n", encoding="utf-8")
        (src / "README").write_text("x\n", encoding="utf-8")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "v" + have)
        git(src, "tag", "v" + have)
        git(self.td, "clone", "-q", "--bare", str(src), str(bare))
        git(self.td, "clone", "-q", str(bare), str(self.inst))
        if latest:
            (src / "aib" / "VERSION").write_text(latest + "\n", encoding="utf-8")
            git(src, "commit", "-q", "-am", "v" + latest)
            git(src, "tag", "v" + latest)
            git(src, "push", "-q", str(bare), "main", "--tags")
        return src, bare


class Setting(Base):
    def test_s1_default_on_off_on_unreadable(self):
        self.assertEqual(AU.read_setting(self.env), (True, "default"))
        ctl, out = FakeCtl(), io.StringIO()
        AU.agent_plist_path(self.home).parent.mkdir(parents=True)
        AU.agent_plist_path(self.home).write_text("x")
        self.assertEqual(AU.cmd_off(self.env, out=out, home=self.home, launchctl=ctl), 0)
        self.assertEqual(AU.read_setting(self.env), (False, "set"))
        self.assertFalse(AU.agent_plist_path(self.home).exists())
        self.assertEqual(ctl.calls[0][0], "bootout")
        self.assertIn("自動更新已關掉", out.getvalue())
        self.assertEqual(AU.check(env=self.env, now=NOW)["result"], "off")
        out = io.StringIO()
        self.assertEqual(AU.cmd_on(self.env, out=out, home=self.home, platform="darwin", launchctl=ctl, dev_tree=False), 0)
        self.assertEqual(AU.read_setting(self.env), (True, "set"))
        self.assertTrue(AU.agent_plist_path(self.home).is_file())
        self.assertIn("bootstrap", [c[0] for c in ctl.calls])
        AU.setting_path(self.env).write_text("{broken", encoding="utf-8")
        self.assertEqual(AU.read_setting(self.env), (False, "unreadable"))
        self.assertEqual(AU.check(env=self.env, now=NOW)["result"], "unreadable")
        p = AU.status_payload(self.env, home=self.home, platform="darwin")
        self.assertEqual((p["on"], p["source"], p["schedule"]), (False, "unreadable", "installed"))
        self.assertIn("讀不懂", "\n".join(AU.status_lines(p)))


class Agent(Base):
    def test_s2_plist(self):
        env = dict(self.env, AIB_INSTALL_URL="https://evil.invalid/i.sh", GH_TOKEN="ghp_secret", X_CAPTAIN_TOKEN="cap-secret")
        d = AU.agent_plist(env, python="/py", script="/s/auto_update.py")
        self.assertEqual(d["Label"], "com.ez2.aib-auto-update")
        self.assertEqual(d["ProgramArguments"], ["/py", "/s/auto_update.py", "check", "--why", "schedule"])
        self.assertEqual(d["StartInterval"], 6 * 3600)
        self.assertIs(d["RunAtLoad"], False)
        self.assertNotIn("KeepAlive", d)
        ev = d["EnvironmentVariables"]
        self.assertTrue(set(ev) <= {"HOME", "LANG", "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME", "EZ2_AIB_LOCALE", "PATH"}, ev)
        self.assertIn("/opt/homebrew/bin", ev["PATH"].split(":"))
        blob = json.dumps(d)
        for s in ("evil.invalid", "ghp_secret", "cap-secret"):
            self.assertNotIn(s, blob)
        self.assertEqual(d["StandardOutPath"], str(Path(self.env["EZ2_AIB_STATE_ROOT"]) / "logs" / "auto-update.log"))
        plistlib.dumps(d)
        ctl = FakeCtl()
        kind, path = AU.install_agent(env, home=self.home, platform="darwin", launchctl=ctl, dev_tree=False)
        self.assertEqual(kind, "installed")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with open(path, "rb") as fh:
            self.assertEqual(plistlib.load(fh)["StartInterval"], 21600)
        self.assertEqual([c[0] for c in ctl.calls], ["bootout", "bootstrap"])
        ctl2 = FakeCtl(fail=True)
        self.assertEqual(AU.install_agent(env, home=self.home, platform="darwin", launchctl=ctl2, dev_tree=False)[0], "failed")

    def test_s3_non_mac(self):
        ctl, out = FakeCtl(), io.StringIO()
        self.assertEqual(AU.install_agent(self.env, home=self.home, platform="linux", launchctl=ctl)[0], "mac_only")
        self.assertEqual(AU.cmd_on(self.env, out=out, home=self.home, platform="linux", launchctl=ctl), 0)
        self.assertIn("不是 Mac", out.getvalue())
        self.assertFalse(AU.agent_plist_path(self.home).exists())
        self.assertEqual(ctl.calls, [])
        self.make_repos()
        res = self.run_check(why="seat-open", platform="linux", launchctl=ctl)
        self.assertNotIn("agent", res)
        self.assertEqual(ctl.calls, [])
        self.assertEqual(AU.status_payload(self.env, home=self.home, platform="linux")["schedule"], "not_mac")

    def test_s3b_seat_open_on_mac_installs_missing_agent(self):
        self.make_repos(latest=None)
        ctl = FakeCtl()
        res = self.run_check(why="seat-open", platform="darwin", launchctl=ctl)
        self.assertEqual(res["agent"], "installed")
        self.assertTrue(AU.agent_plist_path(self.home).is_file())
        self.assertEqual(res["result"], "up_to_date")

    def test_s4_dev_tree(self):
        ctl = FakeCtl()
        self.assertEqual(AU.install_agent(self.env, home=self.home, platform="darwin", launchctl=ctl, dev_tree="/wt/x")[0],
                         "dev_tree")
        self.assertFalse(AU.agent_plist_path(self.home).exists())
        self.make_repos()
        res = self.run_check(why="seat-open", platform="darwin", launchctl=ctl, dev_tree="/wt/x")
        self.assertEqual(res["result"], "dev_tree")
        self.assertEqual((self.updates, ctl.calls), ([], []))


class Gates(Base):
    def test_g1_lease_held(self):
        self.make_repos()
        tool = REPO / "tools" / "governance" / "host_lease.py"
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        r = subprocess.run([sys.executable, str(tool), "acquire", "--owner", "someone", "--what", "tests", "--run-id", "r1",
                            "--pid", str(sleeper.pid)], capture_output=True, text=True, env=dict(os.environ),
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        res = self.run_check(lease_state=AU.lease_busy)
        self.assertEqual(res["result"], "refused_lease")
        self.assertEqual(self.updates, [])
        self.assertEqual(AU.read_status(self.env)["last_result"], "refused_lease")
        # 租約檔壞掉 ⇒ 一樣當有人在用
        Path(self.env["AIB_HOST_LEASE_FILE"]).write_text("{bad", encoding="utf-8")
        self.assertEqual(AU.lease_busy(self.env, NOW), "lease")
        # 放掉 ⇒ 可以更新
        Path(self.env["AIB_HOST_LEASE_FILE"]).unlink()
        self.assertIsNone(AU.lease_busy(self.env, time.time()))

    def test_g2_dirty(self):
        self.make_repos()
        (self.inst / "README").write_text("changed by hand\n", encoding="utf-8")
        res = self.run_check()
        self.assertEqual(res["result"], "refused_dirty")
        self.assertEqual(self.updates, [])
        self.assertEqual((self.inst / "README").read_text(encoding="utf-8"), "changed by hand\n")
        # 沒追蹤的檔不算改動(install.sh 同規則)
        git(self.inst, "checkout", "-q", "--", "README")
        (self.inst / "untracked.txt").write_text("x")
        self.assertEqual(self.run_check(now=NOW + 7200)["result"], "updated")

    def test_g3_too_soon_and_checked_recently(self):
        self.make_repos()
        AU.write_status({"last_attempt": int(NOW - 600)}, self.env)
        called = []
        res = self.run_check(do_fetch=lambda r, e: called.append(1) or True)
        self.assertEqual(res["result"], "too_soon")
        self.assertEqual((self.updates, called), ([], []))
        AU.write_status({"last_check": int(NOW - 60)}, self.env)
        res = self.run_check(why="seat-open", do_fetch=lambda r, e: called.append(1) or True)
        self.assertEqual(res["result"], "checked_recently")
        self.assertEqual(called, [])
        self.assertEqual(self.run_check(why="schedule")["result"], "updated")   # 排程那次不受 10 分鐘限制

    def test_g4_busy_procs(self):
        self.make_repos()
        for cmd, want in (("/usr/bin/python3 /Users/x/ez2-aib/bin/aib test tests/test-a.py", "refused_tests"),
                          ("python3 /x/aib/tools/verify/test_entry.py --all", "refused_tests"),
                          ("/usr/bin/python3 /Users/x/.local/bin/aib ship push", "refused_shipping"),
                          ("git -C /Users/x/wt push origin feat", "refused_push"),
                          ("git push -q", "refused_push")):
            with self.subTest(cmd=cmd):
                res = self.run_check(rows=[(424242, cmd)])
                self.assertEqual(res["result"], want)
        self.assertEqual(self.updates, [])
        self.assertIsNone(AU.busy_procs([(os.getpid(), "aib test x")]))
        self.assertIsNone(AU.busy_procs([(1, "vim notes-about-git-push.txt"), (2, "aib testimonials")]))

    def test_g5_ff_only(self):
        src, bare = self.make_repos()
        (self.inst / "mine.txt").write_text("local\n")
        git(self.inst, "add", "mine.txt")
        git(self.inst, "commit", "-q", "-m", "local commit")
        before = git(self.inst, "rev-parse", "HEAD")
        res = self.run_check()
        self.assertEqual(res["result"], "refused_diverged")
        self.assertEqual((self.updates, git(self.inst, "rev-parse", "HEAD")), ([], before))
        git(self.inst, "reset", "-q", "--hard", "v0.1.1")
        git(self.inst, "checkout", "-q", "-b", "feature")
        self.assertEqual(self.run_check()["result"], "refused_branch")
        git(self.inst, "checkout", "-q", "main")
        git(self.inst, "merge", "-q", "--ff-only", "origin/main")       # 本機已經比 v0.1.1 新 / 等於正式版 ⇒ 不往回退
        self.assertEqual(self.run_check()["result"], "up_to_date")
        self.assertEqual(self.updates, [])

    def test_g6_update_success(self):
        self.make_repos()
        env = dict(self.env, AIB_INSTALL_URL="file:///tmp/evil-installer.sh")
        res = self.run_check(env=env)
        self.assertEqual(res["result"], "updated", res)
        self.assertEqual(len(self.updates), 1)
        self.assertNotIn("AIB_INSTALL_URL", self.updates[0])
        self.assertEqual(self.leases, ["acquire", "release"])
        self.assertEqual(self.rooms, [1])
        self.assertEqual((self.inst / "aib" / "VERSION").read_text().strip(), "0.1.2")
        st = AU.read_status(self.env)
        self.assertEqual((st["last_update"]["from"], st["last_update"]["to"]), ("0.1.1", "0.1.2"))
        rows, _ = RE.read(self.env)
        self.assertEqual([(r["kind"], r["from"], r["to"]) for r in rows], [("aib_updated", "0.1.1", "0.1.2")])
        self.assertEqual(set(rows[0]), {"v", "id", "at", "kind", "from", "to"})
        status, line = AU.doctor_line(self.env)
        self.assertEqual(status, "ok")
        self.assertIn("aib 已自動更新到 0.1.2", line)
        log = AU.log_path(self.env).read_text(encoding="utf-8")
        self.assertIn("result=updated", log)
        self.assertNotIn("evil-installer", log)
        self.assertEqual(self.run_check(now=NOW + 60)["result"], "too_soon")
        self.assertEqual(self.run_check(now=NOW + 7200)["result"], "up_to_date")
        self.assertEqual(len(self.updates), 1)
        # 事件只收像版號的字
        self.assertIsNone(RE.note_aib_updated("0.1.2", "\x1b[31mhi", env=self.env))

    def test_g6b_update_fails(self):
        self.make_repos()
        res = self.run_check(run_update=lambda e, r: self.updates.append(e) or 5)
        self.assertEqual((res["result"], res["update_rc"]), ("failed", 5))
        self.assertEqual(self.leases, ["acquire", "release"])
        self.assertNotIn("last_update", AU.read_status(self.env))
        self.assertEqual(RE.read(self.env)[0], [])

    def test_g6c_lease_acquire_refused(self):
        self.make_repos()
        res = self.run_check(lease=lambda a, r, e: 4 if a == "acquire" else 0)
        self.assertEqual(res["result"], "refused_lease")
        self.assertEqual(self.updates, [])

    def test_g7_far_offline_env(self):
        self.assertTrue(AU.far_behind("0.1.1", "0.2.0"))
        self.assertTrue(AU.far_behind("0.1.1", "0.1.6"))
        self.assertFalse(AU.far_behind("0.1.1", "0.1.5"))
        self.assertFalse(AU.far_behind("0.1.9", "0.1.2"))
        self.assertFalse(AU.far_behind("dev", "0.1.2"))
        self.make_repos(have="0.1.1", latest="0.2.0")
        self.assertEqual(self.run_check()["result"], "refused_far_behind")
        self.assertEqual(self.run_check(do_fetch=lambda r, e: False)["result"], "offline")
        self.assertEqual(self.run_check(env=dict(self.env, AIB_AUTO_UPDATE="0"))["result"], "disabled_env")
        self.assertEqual(self.updates, [])

    def test_g8_not_git(self):
        (self.td / "plain").mkdir()
        self.inst = self.td / "plain"
        self.assertEqual(self.run_check()["result"], "not_git")


class Plugins(Base):
    def ext(self, version="1.0.1", have="1.0.0", signature="ed25519:not-a-real-signature"):
        base = self.td / "ext"
        d = base / "demo-skill"
        d.mkdir(parents=True)
        (d / "aib-ext.json").write_text(json.dumps({"name": "demo-skill", "kind": "skill", "version": version,
                                                     "requires_aib": "0.0.1", "files": {}, "signature": signature}))
        inst = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "skills" / "demo-skill"
        inst.mkdir(parents=True)
        (inst / "aib-ext.json").write_text(json.dumps({"name": "demo-skill", "kind": "skill", "version": have}))
        return base, d

    def test_p1_unsigned_never_installed(self):
        base, d = self.ext()
        self.assertEqual(AU.plugins_behind(base), [])
        self.make_repos(latest=None)
        res = self.run_check(ext_base=base)
        self.assertEqual(res["result"], "up_to_date")
        self.assertEqual(self.installs, [])
        import aib_ext
        with mock.patch.object(aib_ext, "install") as inst:
            self.assertEqual(AU.install_plugin(str(d), io.StringIO()), 3)
            inst.assert_not_called()

    def test_p2_signed_behind_installed(self):
        base, d = self.ext()
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            rows = AU.plugins_behind(base)
            self.assertEqual([(r["name"], r["have"], r["want"]) for r in rows], [("demo-skill", "1.0.0", "1.0.1")])
            self.make_repos(latest=None)
            res = self.run_check(ext_base=base)
            self.assertEqual(res["result"], "updated", res)
            self.assertEqual(self.installs, [str(d)])
            self.assertEqual(self.leases, ["acquire", "release"])
            self.assertEqual(AU.read_status(self.env)["last_plugins"][0]["to"], "1.0.1")
            with mock.patch.object(aib_ext, "install", return_value=0) as inst:
                self.assertEqual(AU.install_plugin(str(d), io.StringIO()), 0)
                self.assertIs(inst.call_args.kwargs["allow_unsigned"], False)
            # 安裝那步回非 0(例:真的簽章 / 雜湊檢查沒過)⇒ plugins_failed
            self.installs.clear()
            res = self.run_check(ext_base=base, now=NOW + 7200, install=lambda f, o: 3)
            self.assertEqual(res["result"], "plugins_failed")

    def test_p3_core_update_leaves_plugins_to_installer(self):
        base, d = self.ext()
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            self.make_repos()
            res = self.run_check(ext_base=base)
        self.assertEqual(res["result"], "updated")
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(self.installs, [])


class Cli(Base):
    def aib(self, *args):
        return subprocess.run([sys.executable, str(REPO.parent / "bin" / "aib"), "update", "auto"] + list(args),
                              capture_output=True, text=True, env=dict(os.environ), stdin=subprocess.DEVNULL, timeout=60)

    def test_i1_i18n_and_cli(self):
        dicts = {}
        for loc in ("en", "zh-TW", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            dicts[loc] = {k: v for k, v in d.items() if k.startswith("aib.auto_update.") or ".check.auto_update." in k}
        self.assertGreater(len(dicts["en"]), 40)
        for loc, d in dicts.items():
            self.assertEqual(set(d), set(dicts["en"]), loc)
        for r in ("off", "unreadable", "busy", "not_git", "dev_tree", "checked_recently", "too_soon", "offline", "no_ref",
                  "up_to_date", "failed", "updated", "plugins_failed", "disabled_env") + tuple(
                "refused_" + w for w in ("lease", "tests", "shipping", "push", "dirty", "diverged", "branch", "far_behind")):
            self.assertIn("aib.auto_update.why." + r, dicts["en"], r)
        r = self.aib("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("自動更新:開(預設)", r.stdout)
        self.assertNotIn("!!", r.stdout)
        r = self.aib("status", "--json")
        self.assertEqual(json.loads(r.stdout)["on"], True)
        # off / on 走子行程會叫真的 launchctl(本人的登入網域)⇒ 這裡不跑;S1 用假的 launchctl 測過
        AU.write_setting(False, self.env)
        self.assertEqual(json.loads(self.aib("status", "--json").stdout)["on"], False)
        self.assertIn("自動更新:關", self.aib("status").stdout)


if __name__ == "__main__":
    unittest.main()
