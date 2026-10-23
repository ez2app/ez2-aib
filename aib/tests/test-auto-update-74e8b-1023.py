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
G6 安全 ⇒ 跑一次 update(子環境只帶白名單:沒有 AIB_* / GIT_* / BASH_ENV…)、拿租約再放掉、記 last_update、艦長室事件只帶版號、叫 room reload;
   doctor 一行「已自動更新到」;接著再查 ⇒ too_soon,過兩小時 ⇒ up_to_date
G7 落後太多(大 / 中版號不同,或差 5 個小版以上)⇒ refused_far_behind;連不上來源 ⇒ offline;AIB_AUTO_UPDATE=0 ⇒ 什麼都不做
P1 外掛簽章驗不過 ⇒ 不列進落後清單、不裝;install_plugin 遇到沒簽的直接 rc 3、不叫 aib_ext.install
P2 簽章驗過、版本較舊 ⇒ 列進落後清單;核心不用更新時由 check 裝;install_plugin 一律 allow_unsigned=False
P3 核心有更新時,外掛交給一行安裝(新版程式)的 aib ext auto,舊碼不自己裝
X1 不自動更新清單:預設有 hostguard(沒設定檔、on / off 之後都還在);add / list / remove 一般外掛不用證明;
   拿掉 hostguard 要本人證明,沒過 rc 2、清單不變;名稱不合法 rc 2
X2 付費外掛來源 = aib-plugins main(測試用本機 bare 倉):簽章驗過、版本較舊 ⇒ git archive 出那個資料夾、走 aib ext install
   真的換上新版;hostguard 在清單裡 ⇒ 有新簽章版也不換、status 列為排除;簽章驗不過 ⇒ 不裝、列為簽章沒過
X3 核心這次有更新 ⇒ 付費外掛等下一次查(新碼在跑時才裝);外掛來源連不上 ⇒ 照樣做核心那部分,status 說來源連不上
X4 git archive 出來的包有跑出資料夾的路徑 / 連結 ⇒ 不裝
R1 時鐘跳過:狀態檔時間戳在未來(含 9999999999)⇒ 不算數、照常查與更新,status 標 clock_skew;下一次正常就清掉
R2 狀態檔 JSON 合法但型別不對(字串 / 清單 / 整數 last_result)⇒ check 不當掉、照常跑並重寫成乾淨的檔;
   status / doctor 一律不當掉;狀態檔根本不是 dict 也一樣
R3 鎖檔位置是資料夾 ⇒ 回 busy,不丟例外
R4 付費外掛暫存資料夾:裝的那步丟例外也會清掉;既有的 stage-<pid> 連結不會被沿用
A1 stage_paid 的路徑過濾(舊 Python 沒有 tarfile data filter 時是唯一防線):假的 git archive 吐
   demo/../../evil、/abs/evil、other/evil 三種包 ⇒ 都回 None,哪裡都沒寫出檔
A2 核心與外掛來源的 git fetch:環境一定帶 GIT_SSH_COMMAND(BatchMode=yes、ForwardAgent=no、ClearAllForwardings=yes)
   與 GIT_TERMINAL_PROMPT=0
A3 本機比正式版新(main 上多一個自己的提交)⇒ up_to_date,不往回退、不更新
A4 紀錄檔是連結 ⇒ 不寫、連結指的檔一個位元組都不動;太大的一般紀錄檔只留後半、從整行開始、換掉的是檔本身
A5 解開付費外掛時磁碟滿(extractall 丟 ENOSPC)⇒ 回 None、不留 stage-*、記一行原因;check 照跑回 plugins_failed
A6 付費外掛 / 附帶外掛的說明檔是合法 JSON 但不是物件(清單)⇒ 跳過那一個並記原因,其他照常
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
import tarfile
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
        # 測試的「公開來源」= 本機 bare 倉(make_repos 建在這個位置);真的公開網址另由 test_g8 驗
        p2 = mock.patch.object(AU, "PUBLIC_SOURCE", str(self.td / "origin.git"))
        p2.start()
        self.addCleanup(p2.stop)

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
                    ext_base=self.td / "noext", room=self.fake_room, dev_tree=False, lease_state=lambda e, n: None,
                    plugin_fetch=lambda e: None)
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
        # 開席那條整份繼承真人終端機的環境:install.sh 認的改來源 / 換安裝程式 / 換公鑰變數、bash 的 BASH_ENV、git 的 GIT_* 一律不帶
        bad = {"AIB_INSTALL_URL": "file:///tmp/evil-installer.sh", "AIB_PACKAGE_INSTALLER": "/tmp/evil.sh",
               "AIB_PACKAGE_BUNDLE_DIR": "/tmp/evil", "AIB_PACKAGE_BASE": "file:///tmp/evil", "AIB_PACKAGE_INSTALLER_URL": "file:///tmp/e",
               "AIB_PACKAGE_INSTALLER_SHA256": "0" * 64, "AIB_ALLOWED_SIGNERS": "/tmp/evil-signers", "AIB_SOURCE": "file:///tmp/evil.git",
               "AIB_DIR": "/tmp/elsewhere", "AIB_BREW_INSTALL_URL": "file:///tmp/b.sh", "AIB_CLEAN_GAP": "99999",
               "BASH_ENV": "/tmp/evil-bashenv", "ENV": "/tmp/evil-env", "GIT_SSH_COMMAND": "evil", "GIT_DIR": "/tmp/x",
               "PYTHONPATH": "/tmp/evil-py", "DYLD_INSERT_LIBRARIES": "/tmp/e.dylib", "GH_TOKEN": "ghp_secret"}
        env = dict(self.env, SSH_AUTH_SOCK="/tmp/agent.sock", LC_ALL="zh_TW.UTF-8", HTTPS_PROXY="http://127.0.0.1:3128", **bad)
        res = self.run_check(env=env)
        self.assertEqual(res["result"], "updated", res)
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(sorted(set(bad) & set(self.updates[0])), [])
        self.assertEqual([k for k in self.updates[0] if k.startswith(("AIB_", "GIT_", "PYTHON", "DYLD_", "LD_"))], [])
        for k in ("SSH_AUTH_SOCK", "LC_ALL", "HTTPS_PROXY", "HOME", "EZ2_AIB_STATE_ROOT"):
            self.assertEqual(self.updates[0].get(k), env[k], k)
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

    def test_g8_source_like_install_sh(self):
        """來源判法跟 install.sh 一樣:insteadOf 把 https 寫成 git@ 仍是公開版 ⇒ 目標是最新 v* 標籤(不是 origin/main);
        別的倉 / fork ⇒ 不是公開版。"""
        for u in ("https://github.com/ez2app/ez2-aib.git", "git@github.com:EZ2App/ez2-aib.git", "ssh://git@github.com:22/ez2app/ez2-aib",
                  "http://github.com/ez2app/ez2-aib/", "git://github.com/ez2app/ez2-aib.git"):
            self.assertEqual(AU.norm_src(u), "https://github.com/ez2app/ez2-aib", u)
        self.assertNotEqual(AU.norm_src("git@github.com:someone/ez2-aib.git"), AU.norm_src(AU.PUBLIC_SOURCE))
        self.make_repos()
        with mock.patch.object(AU, "PUBLIC_SOURCE", "https://github.com/ez2app/ez2-aib.git"):
            git(self.inst, "remote", "set-url", "origin", "git@github.com:ez2app/ez2-aib.git")
            self.assertTrue(AU.public_install(self.inst))
            self.assertEqual(AU.release_ref(self.inst), "v0.1.1")
            git(self.inst, "remote", "set-url", "origin", "git@github.com:someone/ez2-aib.git")
            self.assertFalse(AU.public_install(self.inst))

    def test_g9_not_public_install_refused_without_running(self):
        """私有 / 自己的倉(install.sh same_source 一定回 2)⇒ refused_source,不叫 aib update、不每小時撞。"""
        self.make_repos()
        with mock.patch.object(AU, "PUBLIC_SOURCE", "https://github.com/ez2app/ez2-aib.git"):
            res = self.run_check()
            self.assertEqual(res["result"], "refused_source", res)
            self.assertEqual(self.run_check(now=NOW + 7200)["result"], "refused_source")
        self.assertEqual(self.updates, [])
        self.assertEqual(self.leases, [])

    def test_g10_update_rc0_but_head_unchanged_not_updated(self):
        """aib update 回 0 但 HEAD 沒動(例:main 比標籤新、或安裝程式沒快轉)⇒ no_change,不記成 updated、1 小時內不再跑。"""
        self.make_repos()
        res = self.run_check(run_update=lambda e, r: self.updates.append(e) or 0)
        self.assertEqual(res["result"], "no_change", res)
        self.assertNotIn("last_update", AU.read_status(self.env))
        self.assertEqual(RE.read(self.env)[0], [])
        self.assertEqual(self.run_check(now=NOW + 60, run_update=lambda e, r: self.updates.append(e) or 0)["result"], "too_soon")
        self.assertEqual(len(self.updates), 1)

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

    def test_p2b_signed_but_not_installed_or_excluded_skipped(self):
        """附帶的包簽過、但這台沒裝過 ⇒ 不列、不裝;裝過但在不自動更新清單 ⇒ 也不裝。"""
        base, d = self.ext()
        fresh = base / "fresh-skill"
        fresh.mkdir()
        (fresh / "aib-ext.json").write_text(json.dumps({"name": "fresh-skill", "kind": "skill", "version": "2.0.0",
                                                         "requires_aib": "0.0.1", "files": {}, "signature": "ed25519:x"}))
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            self.assertEqual([r["name"] for r in AU.plugins_behind(base)], ["demo-skill"])
            self.make_repos(latest=None)
            res = self.run_check(ext_base=base)
            self.assertEqual(res["result"], "updated", res)
            self.assertEqual(self.installs, [str(d)])
            self.assertNotIn(str(fresh), self.installs)
            self.installs.clear()
            AU.write_excludes(["hostguard", "demo-skill"], self.env)
            res = self.run_check(ext_base=base, now=NOW + 7200)
            self.assertNotIn("demo-skill", res.get("plugins_behind", []))
            self.assertEqual(self.installs, [])

    def test_p3_core_update_leaves_plugins_to_installer(self):
        base, d = self.ext()
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            self.make_repos()
            res = self.run_check(ext_base=base)
        self.assertEqual(res["result"], "updated")
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(self.installs, [])


class Exclude(Base):
    def test_x1_default_and_commands(self):
        self.assertEqual(AU.read_excludes(self.env), ["hostguard"])
        ctl = FakeCtl()
        AU.cmd_off(self.env, out=io.StringIO(), home=self.home, launchctl=ctl)
        AU.cmd_on(self.env, out=io.StringIO(), home=self.home, platform="linux", launchctl=ctl)
        self.assertEqual(AU.read_excludes(self.env), ["hostguard"])
        asked = []

        def deny(reason, env, platform=None):
            asked.append(reason)
            return "touchid", "touchid_cancel"
        out = io.StringIO()
        self.assertEqual(AU.cmd_exclude("add", "captain-room", env=self.env, out=out, prove=deny), 0)
        self.assertEqual(AU.read_excludes(self.env), ["captain-room", "hostguard"])
        self.assertEqual(AU.cmd_exclude("remove", "captain-room", env=self.env, out=out, prove=deny), 0)
        self.assertEqual(asked, [])
        self.assertEqual(AU.cmd_exclude("remove", "hostguard", env=self.env, out=out, prove=deny), 2)
        self.assertEqual(AU.read_excludes(self.env), ["hostguard"])
        self.assertIn("hostguard", asked[0])
        self.assertIn("Touch ID", out.getvalue())
        self.assertEqual(AU.cmd_exclude("add", "../x", env=self.env, out=out), 2)
        out = io.StringIO()
        AU.cmd_exclude("list", env=self.env, out=out, as_json=True)
        self.assertEqual(json.loads(out.getvalue())["exclude"], ["hostguard"])
        self.assertEqual(AU.cmd_exclude("remove", "hostguard", env=self.env, out=io.StringIO(),
                                        prove=lambda r, e, platform=None: ("touchid", None)), 0)
        self.assertEqual(AU.read_excludes(self.env), [])
        self.assertEqual(AU.read_setting(self.env), (True, "set"))     # 動清單不會改到開關


class PaidSource(Base):
    def paid_repo(self, plugins):
        """aib-plugins 樣子的 bare 倉:{名: 新版號};這台裝的是 1.0.0。回 bare 倉路徑。"""
        import aib_ext
        src, bare = self.td / "plugsrc", self.td / "aib-plugins.git"
        src.mkdir()
        git(src, "-c", "init.defaultBranch=main", "init", "-q")
        for name, ver in plugins.items():
            d = src / name
            d.mkdir()
            (d / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": ver}))
            (d / "m.py").write_text("VERSION = %r\n" % ver)
            (d / "aib-ext.json").write_text(json.dumps({"name": name, "kind": "plugin", "version": ver, "requires_aib": "0.0.1",
                                                         "files": aib_ext._hashes(d), "signature": "ed25519:test"}))
            inst = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / name
            inst.mkdir(parents=True)
            (inst / "aib-ext.json").write_text(json.dumps({"name": name, "kind": "plugin", "version": "1.0.0"}))
            (inst / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": "1.0.0"}))
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "plugins")
        git(self.td, "clone", "-q", "--bare", str(src), str(bare))
        self.env["AIB_AUTO_UPDATE_PLUGIN_SOURCE"] = str(bare)
        os.environ["AIB_AUTO_UPDATE_PLUGIN_SOURCE"] = str(bare)
        return bare

    def installed_version(self, name):
        return json.loads((Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / name / "aib-ext.json").read_text())["version"]

    def test_x2_signed_paid_updated_excluded_kept(self):
        self.paid_repo({"demo-paid": "1.0.3", "hostguard": "2.0.0"})
        self.make_repos(latest=None)
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), \
                mock.patch.object(aib_ext, "reload_room_after_install", return_value=None):
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
        self.assertEqual(res["result"], "updated", res)
        self.assertEqual(res["plugin_source"], "ok")
        self.assertEqual(res["plugins_behind"], ["demo-paid"])
        self.assertEqual(res["plugins_excluded"], ["hostguard"])
        self.assertEqual(self.installed_version("demo-paid"), "1.0.3")
        self.assertEqual(self.installed_version("hostguard"), "1.0.0")          # 有新簽章版也不換
        p = AU.status_payload(self.env, platform="linux")
        self.assertEqual((p["excluded"], p["plugins_excluded"]), (["hostguard"], ["hostguard"]))
        self.assertIn("hostguard", "\n".join(AU.status_lines(p)))
        self.assertFalse(list((Path(self.env["EZ2_AIB_STATE_ROOT"]) / "cache" / "auto-update").glob("stage-*")))

    def test_x2b_signature_failure_not_installed(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        self.make_repos(latest=None)
        res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)   # 真的 signed_ok:假簽章驗不過
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res["plugins_unsigned"], ["demo-paid"])
        self.assertEqual(self.installed_version("demo-paid"), "1.0.0")
        self.assertEqual(self.leases, [])
        # 就算落後清單被動了手腳,裝的那一步自己也驗簽章:驗不過 rc 3、不叫 aib_ext.install
        cache = AU.plugin_cache(self.env)
        folder = AU.stage_paid(cache, "demo-paid", self.env)
        import aib_ext
        with mock.patch.object(aib_ext, "install") as inst:
            self.assertEqual(AU.install_plugin(str(folder), io.StringIO()), 3)
            inst.assert_not_called()

    def test_x3_core_first_then_paid_and_offline(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        self.make_repos()
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=self.fake_install)
            self.assertEqual(res["result"], "updated")
            self.assertEqual((len(self.updates), self.installs), (1, []))
            res = self.run_check(now=NOW + 7200, plugin_fetch=AU.fetch_plugin_source, install=self.fake_install)
            self.assertEqual(res["result"], "updated")
            self.assertEqual((len(self.updates), len(self.installs)), (1, 1))
        res = self.run_check(now=NOW + 99999, plugin_fetch=lambda e: None)
        self.assertEqual((res["result"], res["plugin_source"]), ("up_to_date", "offline"))
        self.assertIn("aib-plugins", "\n".join(AU.status_lines(AU.status_payload(self.env, platform="linux"))))

    def test_x4_stage_refuses_escaping_paths(self):
        bare = self.paid_repo({"demo-paid": "1.0.3"})
        src = self.td / "plugsrc"
        os.symlink("/etc/hosts", src / "demo-paid" / "link")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "link")
        git(src, "push", "-q", str(bare), "main")
        cache = AU.fetch_plugin_source(self.env)
        self.assertIsNotNone(cache)
        self.assertIsNone(AU.stage_paid(cache, "demo-paid", self.env))
        # 要是自己那行檢查擋的(不是新版 Python 的 data filter 代擋):舊 Python(3.9)沒有 data filter,那行是唯一防線
        self.assertIn("demo-paid skipped: archive has a path outside the folder or a link", AU.log_path(self.env).read_text(encoding="utf-8"))
        self.assertEqual(list((AU.state_root(self.env) / "cache" / "auto-update").glob("stage-*")), [])

    def test_x4b_stage_refuses_inner_relative_link(self):
        """夾內相對連結(data filter 會放行)⇒ 一樣不解;另把 data filter 拿掉(模擬 3.9)再跑一次。"""
        bare = self.paid_repo({"demo-paid": "1.0.3"})
        src = self.td / "plugsrc"
        os.symlink("aib-ext.json", src / "demo-paid" / "inner")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "inner link")
        git(src, "push", "-q", str(bare), "main")
        cache = AU.fetch_plugin_source(self.env)
        self.assertIsNone(AU.stage_paid(cache, "demo-paid", self.env))
        with mock.patch.object(AU.tarfile, "data_filter", None, create=True):
            delattr(AU.tarfile, "data_filter")
            self.assertIsNone(AU.stage_paid(cache, "demo-paid", self.env))
        self.assertEqual(list((AU.state_root(self.env) / "cache" / "auto-update").glob("stage-*")), [])
        self.assertEqual(AU.log_path(self.env).read_text(encoding="utf-8").count("archive has a path outside the folder or a link"), 2)


class Robust(Base):
    def write_raw_status(self, d):
        p = AU.status_path(self.env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d), encoding="utf-8")

    def test_r1_clock_skew(self):
        self.make_repos()
        self.write_raw_status({"last_attempt": 9999999999, "last_check": NOW + 3600, "last_result_at": NOW + 99999})
        res = self.run_check(why="seat-open")
        self.assertEqual(res["result"], "updated", res)
        self.assertTrue(res["clock_skew"])
        p = AU.status_payload(self.env, platform="linux", now=NOW)
        self.assertTrue(p["clock_skew"])
        self.assertIn("時鐘跳過", "\n".join(AU.status_lines(p)))
        self.assertEqual(AU.read_status(self.env, NOW)["last_attempt"], int(NOW))
        self.assertEqual(self.run_check(now=NOW + 7200)["result"], "up_to_date")
        self.assertFalse(AU.status_payload(self.env, platform="linux", now=NOW + 7200)["clock_skew"])
        # 5 分鐘以內的小誤差照舊算數
        self.write_raw_status({"last_attempt": NOW + 100})
        self.assertEqual(self.run_check(now=NOW)["result"], "too_soon")

    def test_r2_bad_types(self):
        self.make_repos()
        bad = {"last_attempt": "x", "last_check": [1], "last_result": 5, "last_result_at": "y", "have": 3,
               "plugins_behind": "demo", "last_update": "x", "last_plugins": [1, "a"], "plugin_source": {"a": 1}}
        self.write_raw_status(bad)
        for fn in (lambda: AU.status_payload(self.env, platform="linux"), lambda: AU.doctor_line(self.env)):
            fn()
        AU.status_lines(AU.status_payload(self.env, platform="linux"))
        res = self.run_check()
        self.assertEqual(res["result"], "updated", res)
        raw = json.loads(AU.status_path(self.env).read_text(encoding="utf-8"))
        self.assertIsInstance(raw["last_attempt"], int)
        self.assertEqual(raw["last_result"], "updated")
        self.assertEqual(raw.get("last_plugins", []), [])
        for d in ([1, 2], "str", 7, {"last_update": {"to": 1, "at": "x"}, "last_result": "Bad Result!"},
                  {"last_result_at": True, "last_check": float("nan")}):
            self.write_raw_status(d)
            p = AU.status_payload(self.env, platform="linux")
            AU.status_lines(p)
            AU.doctor_line(self.env)
            self.assertIsNone(p["last_update"])
        self.write_raw_status({"last_result": 5})
        r = subprocess.run([sys.executable, str(REPO.parent / "bin" / "aib"), "update", "auto", "status"],
                           capture_output=True, text=True, env=dict(os.environ), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_r3_lock_is_directory(self):
        self.make_repos()
        lock = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "state" / AU.LOCK
        lock.mkdir(parents=True)
        self.assertEqual(self.run_check()["result"], "busy")
        self.assertEqual(self.updates, [])

    def test_r4_stage_cleanup(self):
        PaidSource.paid_repo(self, {"demo-paid": "1.0.3"})
        self.make_repos(latest=None)
        base = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "cache" / "auto-update"
        base.mkdir(parents=True, exist_ok=True)
        decoy = self.td / "decoy"
        decoy.mkdir()
        os.symlink(str(decoy), str(base / ("stage-%d" % os.getpid())))

        def boom(folder, out):
            raise RuntimeError("install blew up")
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            with self.assertRaises(RuntimeError):
                self.run_check(plugin_fetch=AU.fetch_plugin_source, install=boom)
        self.assertEqual(self.leases, ["acquire", "release"])
        self.assertEqual(sorted(p.name for p in base.glob("stage-*")), ["stage-%d" % os.getpid()])   # 只剩那個沒被碰的連結
        self.assertEqual(list(decoy.iterdir()), [])


class Audit(Base):
    def test_a1_stage_path_filter(self):
        base = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "cache" / "auto-update"
        for bad in ("demo/../../evil", "/abs/evil", "other/evil"):
            with self.subTest(member=bad):
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w") as tf:
                    for name, data in (("demo/ok.txt", b"ok"), (bad, b"evil")):
                        ti = tarfile.TarInfo(name)
                        ti.size = len(data)
                        tf.addfile(ti, io.BytesIO(data))
                fake = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout=buf.getvalue(), stderr=b""))
                with mock.patch.object(AU.subprocess, "run", fake):
                    self.assertIsNone(AU.stage_paid(self.td / "cache.git", "demo", self.env))
                self.assertEqual(fake.call_args.args[0][3:], ["archive", "--format=tar", "main", "demo"])
                self.assertEqual(list(base.glob("stage-*")), [])
                self.assertEqual([p for p in self.td.rglob("evil")], [])
                # 要是自己的路徑檢查擋下的(新版 Python 的 data filter 也會擋,但 3.9 沒有)
                self.assertIn("path outside the folder", AU.log_path(self.env).read_text(encoding="utf-8"))
                AU.log_path(self.env).unlink()
                self.assertFalse(Path("/abs/evil").exists())

    def test_a2_fetch_is_hardened(self):
        calls = []

        def fake_git(root, *args, env=None, timeout=30):
            calls.append((args, dict(env or {})))
            return 0, ""
        with mock.patch.object(AU, "_git", fake_git):
            AU.fetch(self.td, self.env)
            AU.fetch_plugin_source(dict(self.env, AIB_AUTO_UPDATE_PLUGIN_SOURCE="git@example.invalid:x/y.git"))
        fetches = [(a, e) for a, e in calls if "fetch" in a]
        self.assertEqual(len(fetches), 2, calls)
        for args, env in fetches:
            ssh = env.get("GIT_SSH_COMMAND", "")
            for opt in ("BatchMode=yes", "ForwardAgent=no", "ClearAllForwardings=yes"):
                self.assertIn(opt, ssh, args)
            self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0", args)

    def test_a3_local_ahead_no_downgrade(self):
        self.make_repos()
        git(self.inst, "fetch", "-q", "--tags", "origin")
        git(self.inst, "merge", "-q", "--ff-only", "v0.1.2")
        (self.inst / "mine.txt").write_text("local\n")
        git(self.inst, "add", "mine.txt")
        git(self.inst, "commit", "-q", "-m", "local ahead")
        before = git(self.inst, "rev-parse", "HEAD")
        self.assertEqual(self.run_check()["result"], "up_to_date")
        self.assertEqual((self.updates, self.leases, git(self.inst, "rev-parse", "HEAD")), ([], [], before))


class Audit2(Base):
    def test_a4_log_symlink_and_truncate(self):
        lp = AU.log_path(self.env)
        lp.parent.mkdir(parents=True)
        target = self.td / "precious.txt"
        target.write_bytes(b"x" * 360 * 1024)
        os.symlink(str(target), str(lp))
        # 兩道各自都要擋得住:只剩 lstat 檢查(沒有 O_NOFOLLOW)、只剩 O_NOFOLLOW(lstat 檢查失效)
        for patch in (mock.patch.object(AU.os, "O_NOFOLLOW", 0, create=True),
                      mock.patch.object(AU.stat, "S_ISREG", return_value=True)):
            with self.subTest(patch=patch.attribute):
                with patch:
                    AU.log("hello", self.env)
                self.assertTrue(lp.is_symlink())
                self.assertEqual(target.read_bytes(), b"x" * 360 * 1024)
        lp.unlink()
        lines = b"".join(b"line %06d\n" % i for i in range(40000))
        self.assertGreater(len(lines), AU.LOG_MAX)
        lp.write_bytes(lines)
        AU.log("tail-marker", self.env)
        data = lp.read_bytes()
        self.assertLessEqual(len(data), AU.LOG_MAX // 2 + 100)
        self.assertTrue(data.startswith(b"line "))
        self.assertIn(b"line 039999\n", data)
        self.assertTrue(data.rstrip().endswith(b"tail-marker"))
        self.assertFalse(lp.is_symlink())

    def test_a5_extract_disk_full(self):
        import errno
        PaidSource.paid_repo(self, {"demo-paid": "1.0.3"})
        cache = AU.fetch_plugin_source(self.env)
        base = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "cache" / "auto-update"
        with mock.patch.object(tarfile.TarFile, "extractall", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            self.assertIsNone(AU.stage_paid(cache, "demo-paid", self.env))
            self.assertEqual(list(base.glob("stage-*")), [])
            self.assertIn("extract failed", AU.log_path(self.env).read_text(encoding="utf-8"))
            self.make_repos(latest=None)
            import aib_ext
            with mock.patch.object(aib_ext, "signed_ok", return_value=True):
                res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
        self.assertEqual(res["result"], "plugins_failed", res)
        self.assertEqual(list(base.glob("stage-*")), [])

    def test_a6_non_object_manifest(self):
        PaidSource.paid_repo(self, {"demo-paid": "1.0.3", "zz-broken": "1.0.3"})
        (Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / "zz-broken" / "aib-ext.json").write_text("[1, 2]")
        cache = AU.fetch_plugin_source(self.env)
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            behind, _skip, _uns = AU.paid_behind(cache, (), self.env)
        self.assertEqual([b["name"] for b in behind], ["demo-paid"])
        self.assertIn("zz-broken skipped", AU.log_path(self.env).read_text(encoding="utf-8"))
        ext = self.td / "ext" / "listy"
        ext.mkdir(parents=True)
        (ext / "aib-ext.json").write_text('["not", "an", "object"]')
        self.assertEqual(AU.plugins_behind(self.td / "ext"), [])
        self.assertEqual(AU.install_plugin(str(ext), io.StringIO()), 3)


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
