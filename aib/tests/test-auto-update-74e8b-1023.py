#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib update auto(10-23 Andy「更新版本要自動 aib update」;@74e8b)。

S1 設定:沒檔 = 開(預設);off 寫 on:false 並拆排程;on 寫回開並裝排程;檔壞 ⇒ 當成關(unreadable);關著 check 不動
S2 排程 plist:Label、ProgramArguments = check --why schedule、StartInterval 6 小時、RunAtLoad 關、權限 0600、
   環境只帶路徑 / 語系(AIB_INSTALL_URL、token 一律不帶)
S3 非 macOS:不裝排程、不叫 launchctl(on / off 都不叫);cmd_on 照樣 rc 0;開席那次 check 也不裝
S4 開發用 worktree:不裝排程、check 回 dev_tree、不更新
G1 測試租約有人拿著(真的 host_lease acquire)⇒ refused_lease、不更新
G2 安裝目錄有沒提交的改動 ⇒ refused_dirty、不更新
G3 一小時內才更新過 ⇒ too_soon;開席 10 分鐘內查過 ⇒ checked_recently(不連網路)
G4 正在跑 aib test / 推包 / git push ⇒ refused_tests / refused_shipping / refused_push;自己的 pid 不算
G5 只快轉:本機有自己的提交(歷史分叉)⇒ refused_diverged;在別的分支 ⇒ refused_branch;本機比正式版新 ⇒ up_to_date
G6 安全 ⇒ 跑一次 update(子環境只帶白名單:沒有 AIB_* / GIT_* / BASH_ENV…)、拿租約再放掉、記 last_update、艦長室事件只帶版號、叫 room reload;
   doctor 一行「已自動更新到」;接著再查 ⇒ too_soon,過兩小時 ⇒ up_to_date
G7 落後太多(大 / 中版號不同,或差 5 個小版以上;環境的 AIB_CLEAN_GAP 不算)⇒ refused_far_behind;連不上來源 ⇒ offline;AIB_AUTO_UPDATE=0 ⇒ 什麼都不做
G11 自動路徑跑已 fetch 標籤裡的 install.sh(不連網站);標籤裡不是公開版安裝程式 ⇒ rc 5 什麼都不跑;子行程 AIB_CLEAN_GAP = 不整份重裝
G12 最新 v* 標籤不在 origin/main 歷史上 ⇒ refused_source
G13 逾時整個行程群組砍掉(孫行程不留),回 124
P1 外掛簽章驗不過 ⇒ 不列進落後清單、不裝;install_plugin 遇到沒簽的直接 rc 3、不叫 aib_ext.install
P2 簽章驗過、版本較舊 ⇒ 列進落後清單;核心不用更新時由 check 裝;install_plugin 一律 allow_unsigned=False
P3 核心有更新時,外掛交給一行安裝(新版程式)的 aib ext auto,舊碼不自己裝
X1 不自動更新清單:預設有 hostguard(沒設定檔、on / off 之後都還在);add / list / remove 一般外掛不用證明;
   拿掉 hostguard 要本人證明,沒過 rc 2、清單不變;名稱不合法 rc 2
X2 付費外掛來源 = aib-plugins main(測試用本機 bare 倉):簽章驗過、版本較舊 ⇒ 只列名(plugins_manual_behind)、不換、不拿租約,
   status / doctor 提示本人;hostguard 在清單裡 ⇒ status 列為排除;簽章驗不過 ⇒ 列為簽章沒過
X3 核心這次有更新、下一次也一樣:付費外掛一直只列名;外掛來源連不上 ⇒ 照樣做核心那部分,status 說來源連不上
R1 時鐘跳過:狀態檔時間戳在未來(含 9999999999)⇒ 不算數、照常查與更新,status 標 clock_skew;下一次正常就清掉
R2 狀態檔 JSON 合法但型別不對(字串 / 清單 / 整數 last_result)⇒ check 不當掉、照常跑並重寫成乾淨的檔;
   status / doctor 一律不當掉;狀態檔根本不是 dict 也一樣
R3 鎖檔位置是資料夾 ⇒ 回 busy,不丟例外
A2 核心與外掛來源的 git fetch:環境一定帶 GIT_SSH_COMMAND(BatchMode=yes、ForwardAgent=no、ClearAllForwardings=yes)
   與 GIT_TERMINAL_PROMPT=0
A3 本機比正式版新(main 上多一個自己的提交)⇒ up_to_date,不往回退、不更新
A4 紀錄檔是連結 ⇒ 不寫、連結指的檔一個位元組都不動;太大的一般紀錄檔只留後半、從整行開始、換掉的是檔本身
A6 付費外掛 / 附帶外掛的說明檔是合法 JSON 但不是物件(清單)⇒ 跳過那一個並記原因,其他照常
I1 四語字典同鍵、沒有 !!;aib update auto status / --json 走得通(子行程,暫存狀態根)
J1 --json 每條路(on / off / exclude / status / check 含例外)只印一個 JSON;launchctl 換假的
J2 開席那次 detach 後先等 60–90 秒(可注入)再查;排程那次不等
U1 aib uninstall 一併拆自動更新排程
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


def NO_CATALOG(env, excluded):
    """預設:ez2.page 沒這組目錄端點 ⇒ 走 aib-plugins git(既有測試);目錄那條另在 test-auto-update-catalog-d07f9-1025.py。"""
    return {"via": "fallback", "behind": [], "skipped": [], "unsigned": [], "problem": "plugin_unsupported"}


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
    def fake_update(self, env, root, ref=None):
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
                    plugin_fetch=lambda e: None, catalog=NO_CATALOG)
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
        self.assertEqual(AU.cmd_off(self.env, out=out, home=self.home, platform="darwin", launchctl=ctl), 0)
        self.assertEqual(AU.read_setting(self.env), (False, "set"))
        self.assertFalse(AU.agent_plist_path(self.home).exists())
        self.assertEqual(ctl.calls[0][0], "bootout")
        lctl = FakeCtl()                                   # Linux 沒有 launchctl:不叫、排程檔照刪
        AU.agent_plist_path(self.home).write_text("x")
        self.assertEqual(AU.cmd_off(self.env, out=io.StringIO(), home=self.home, platform="linux", launchctl=lctl), 0)
        self.assertEqual(lctl.calls, [])
        self.assertFalse(AU.agent_plist_path(self.home).exists())
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
        self.assertEqual(AU.cmd_off(self.env, out=io.StringIO(), home=self.home, launchctl=ctl, platform="linux"), 0)
        self.assertEqual(ctl.calls, [])                                     # Linux 關掉也不叫 launchctl

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
        # 租約模組量不到(丟例外)⇒ 當有人在用
        with mock.patch.object(AU, "_host_lease", side_effect=RuntimeError("boom")):
            self.assertEqual(AU.lease_busy(self.env, time.time()), "lease")

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
        self.assertEqual(sorted(set(bad) & set(self.updates[0])), ["GIT_SSH_COMMAND"])
        self.assertEqual(self.updates[0]["GIT_SSH_COMMAND"], AU.SSH_HARDENED)   # 換成加固版(insteadOf 改寫成 ssh 時 install.sh 不會自己補)
        for o in ("BatchMode=yes", "ForwardAgent=no", "ClearAllForwardings=yes"):
            self.assertIn(o, AU.SSH_HARDENED)
        self.assertEqual(self.updates[0]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual([k for k in self.updates[0] if k.startswith(("AIB_", "GIT_", "PYTHON", "DYLD_", "LD_"))
                          and k not in ("GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT", "AIB_EXT_AUTO_GUARD")], [])
        self.assertEqual(self.updates[0]["AIB_EXT_AUTO_GUARD"], "1")      # 新版 aib ext auto 走不蓋使用者檔那條
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
        res = self.run_check(run_update=lambda e, r, ref=None: self.updates.append(e) or 0)
        self.assertEqual(res["result"], "no_change", res)
        self.assertNotIn("last_update", AU.read_status(self.env))
        self.assertEqual(RE.read(self.env)[0], [])
        self.assertEqual(self.run_check(now=NOW + 60, run_update=lambda e, r, ref=None: self.updates.append(e) or 0)["result"], "too_soon")
        self.assertEqual(len(self.updates), 1)

    def test_g6d_run_core_update_child_env(self):
        # 第二道防線:run_core_update 自己也只帶白名單 + 加固 ssh(就算呼叫端傳進整份環境);AIB_CLEAN_GAP 一律換成不整份重裝
        self.make_repos()
        seen = []
        def fake_run(argv, fh, env, cwd, timeout):
            seen.append((argv, env))
            return 0
        dirty = dict(self.env, AIB_INSTALL_URL="file:///tmp/evil.sh", GIT_SSH_COMMAND="ssh -o ForwardAgent=yes", BASH_ENV="/tmp/e",
                     AIB_CLEAN_GAP="5")
        with mock.patch.object(AU, "installer_at", return_value="echo " + AU.PUBLIC_MARK):
            self.assertEqual(AU.run_core_update(dirty, self.inst, "v0.1.2", run=fake_run), 0)
        argv, env = seen[0]
        self.assertEqual(argv[:2] + argv[3:], ["/bin/bash", "-c", "aib-install", "--dir", str(self.inst)])
        self.assertNotIn("AIB_INSTALL_URL", env)
        self.assertNotIn("BASH_ENV", env)
        self.assertEqual(env["GIT_SSH_COMMAND"], AU.SSH_HARDENED)
        self.assertEqual(env["AIB_CLEAN_GAP"], str(AU.NO_CLEAN_GAP))

    def test_g11_installer_from_fetched_tag_not_website(self):
        """[A-H1] 自動路徑跑已 fetch 標籤裡的 install.sh(不連網站下載);標籤裡不是公開版安裝程式 ⇒ rc 5、什麼都不跑。"""
        src, bare = self.make_repos()
        marker = self.td / "ran.txt"
        (src / "install.sh").write_text('# %s\nprintf "%%s|%%s\\n" "$*" "$AIB_CLEAN_GAP" > %s\n' % (AU.PUBLIC_MARK, marker),
                                        encoding="utf-8")
        git(src, "add", "install.sh")
        git(src, "commit", "-q", "-m", "installer")
        git(src, "tag", "v0.1.3")
        git(src, "push", "-q", str(bare), "main", "--tags")
        AU.fetch(self.inst, self.env)
        self.assertIsNone(AU.installer_at(self.inst, "v0.1.2"))            # 那個標籤沒有 install.sh
        self.assertEqual(AU.run_core_update(self.env, self.inst, "v0.1.2"), 5)
        self.assertFalse(marker.exists())
        self.assertEqual(AU.run_core_update(self.env, self.inst, "v0.1.3"), 0)
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), "--dir %s|%d" % (self.inst, AU.NO_CLEAN_GAP))
        self.assertNotIn("curl", AU.installer_at(self.inst, "v0.1.3"))
        (src / "install.sh").write_text("echo not-public\n", encoding="utf-8")   # 沒有公開版記號 ⇒ 不當安裝程式
        git(src, "commit", "-q", "-am", "x")
        git(src, "tag", "v0.1.4")
        git(src, "push", "-q", str(bare), "main", "--tags")
        AU.fetch(self.inst, self.env)
        self.assertIsNone(AU.installer_at(self.inst, "v0.1.4"))

    def test_g12_tag_must_be_on_official_main(self):
        """[A-M2] 最新 v* 標籤不在 origin/main 的歷史上(另外推的標籤)⇒ refused_source、不跑。"""
        src, bare = self.make_repos()
        git(src, "checkout", "-q", "-b", "side", "v0.1.1")
        (src / "aib" / "VERSION").write_text("0.1.3\n", encoding="utf-8")
        git(src, "commit", "-q", "-am", "side")
        git(src, "tag", "v0.1.3")
        git(src, "push", "-q", str(bare), "--tags")
        res = self.run_check()
        self.assertEqual(res["result"], "refused_source", res)
        self.assertEqual((self.updates, self.leases), ([], []))

    def test_g13_run_group_timeout_kills_whole_group(self):
        """[A-L1] 逾時 ⇒ 整個行程群組 TERM / KILL,孫行程也不留;回 124。"""
        pidf = self.td / "child.pid"
        with open(os.devnull, "w") as fh:
            rc = AU._run_group(["/bin/sh", "-c", "sleep 30 & echo $! > %s; wait" % pidf], fh, dict(self.env), self.td, 1)
        self.assertEqual(rc, 124)
        pid = int(pidf.read_text().strip())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("grandchild %d still alive" % pid)

    def test_g6b_update_fails(self):
        self.make_repos()
        res = self.run_check(run_update=lambda e, r, ref=None: self.updates.append(e) or 5)
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
        self.assertEqual(self.run_check(env=dict(self.env, AIB_CLEAN_GAP="99999"))["result"], "refused_far_behind")   # [A-M1] 父行程不讀環境
        self.assertEqual(self.run_check(do_fetch=lambda r, e: False)["result"], "offline")
        self.assertEqual(self.run_check(env=dict(self.env, AIB_AUTO_UPDATE="0"))["result"], "disabled_env")
        self.assertEqual(self.updates, [])

    def test_g9b_hold_offline_or_local_only_is_normal(self):
        src, bare = self.make_repos()
        (src / AU.HOLD_FILE).write_text("x\n", encoding="utf-8")
        git(src, "add", AU.HOLD_FILE)
        git(src, "commit", "-q", "-m", "hold")              # 沒推上 origin ⇒ 不算
        self.assertEqual(self.run_check(do_fetch=lambda r, e: False)["result"], "offline")   # 連不上 ⇒ 照一般離線，不重試
        self.assertEqual(self.updates, [])
        self.assertEqual(self.run_check()["result"], "updated")
        self.assertEqual(len(self.updates), 1)

    def test_g4b_manual_update_running_is_busy(self):
        self.make_repos()
        res = self.run_check(rows=[(424242, "/usr/bin/python3 /Users/x/.local/bin/aib update")])
        self.assertEqual(res["result"], "refused_updating")
        self.assertEqual(self.updates, [])
        self.assertIsNone(AU.busy_procs([(424243, "/usr/bin/python3 /Users/x/.local/bin/aib update auto")]))
        self.assertIsNone(AU.busy_procs([(424244, "vim aib updates.txt")]))

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
        (inst / "aib-ext.json").write_text(json.dumps({"name": "demo-skill", "kind": "skill", "version": have, "files": {}}))
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
        AU.cmd_off(self.env, out=io.StringIO(), home=self.home, platform="linux", launchctl=ctl)
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
    def paid_repo(self, plugins, meta=None):
        """aib-plugins 樣子的 bare 倉:{名: 新版號};這台裝的是 1.0.0。meta={名: 倉裡 aib-ext.json 要蓋掉的欄位}。回 bare 倉路徑。"""
        import aib_ext
        src, bare = self.td / "plugsrc", self.td / "aib-plugins.git"
        src.mkdir()
        git(src, "-c", "init.defaultBranch=main", "init", "-q")
        for name, ver in plugins.items():
            d = src / name
            d.mkdir()
            (d / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": ver}))
            (d / "m.py").write_text("VERSION = %r\n" % ver)
            (d / "aib-ext.json").write_text(json.dumps(dict({"name": name, "kind": "plugin", "version": ver, "requires_aib": "0.0.1",
                                                              "files": aib_ext._hashes(d), "signature": "ed25519:test"},
                                                             **((meta or {}).get(name) or {}))))
            inst = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / name
            inst.mkdir(parents=True)
            (inst / "plugin.json").write_text(json.dumps({"name": name, "module": "m.py", "version": "1.0.0"}))
            (inst / "aib-ext.json").write_text(json.dumps({"name": name, "kind": "plugin", "version": "1.0.0",
                                                           "files": aib_ext._hashes(inst)}))   # 已裝那份跟自己的 files 一致 = 沒被改過
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "plugins")
        git(self.td, "clone", "-q", "--bare", str(src), str(bare))
        self.env["AIB_AUTO_UPDATE_PLUGIN_SOURCE"] = str(bare)
        os.environ["AIB_AUTO_UPDATE_PLUGIN_SOURCE"] = str(bare)
        return bare

    def installed_version(self, name):
        return json.loads((Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / name / "aib-ext.json").read_text())["version"]

    def test_x2_signed_paid_listed_not_installed(self):
        """[A-M3] 付費外掛有新簽章版 ⇒ 只列名(plugins_manual_behind)、不換、不拿租約;hostguard 在排除清單照列排除;status / doctor 提示本人。"""
        self.paid_repo({"demo-paid": "1.0.3", "hostguard": "2.0.0"})
        self.make_repos(latest=None)
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), mock.patch.object(aib_ext, "install") as inst:
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
            inst.assert_not_called()
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res["plugin_source"], "ok")
        self.assertEqual((res["plugins_behind"], res["plugins_manual_behind"]), ([], ["demo-paid"]))
        self.assertEqual(res["plugins_excluded"], ["hostguard"])
        self.assertEqual((self.installed_version("demo-paid"), self.installed_version("hostguard")), ("1.0.0", "1.0.0"))
        self.assertEqual((self.leases, self.installs), ([], []))
        p = AU.status_payload(self.env, platform="linux")
        self.assertEqual((p["excluded"], p["plugins_excluded"], p["plugins_manual_behind"]), (["hostguard"], ["hostguard"], ["demo-paid"]))
        text = "\n".join(AU.status_lines(p))
        self.assertIn("hostguard", text)
        self.assertIn("demo-paid", text)
        self.assertIn("aib plugins install", text)
        self.assertEqual(AU.doctor_line(self.env)[0], "note")
        self.assertIn("demo-paid", AU.doctor_line(self.env)[1])
        self.assertFalse(hasattr(AU, "stage_paid"))

    def _paid_check_signed(self):
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), \
                mock.patch.object(aib_ext, "reload_room_after_install", return_value=None), \
                mock.patch.object(AU, "install_plugin", wraps=AU.install_plugin) as inst:
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
        return res, inst

    def _state_dirs(self):
        root = Path(self.env["EZ2_AIB_STATE_ROOT"])
        return sorted(str(p.relative_to(root)) for sub in ("plugins", "skills") if (root / sub).is_dir()
                      for p in (root / sub).iterdir())

    def test_x6_official_hold_pauses_core_and_plugins(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        src, bare = self.make_repos()
        (src / AU.HOLD_FILE).write_text("維護中\n", encoding="utf-8")
        git(src, "add", AU.HOLD_FILE)
        git(src, "commit", "-q", "-m", "hold")
        git(src, "push", "-q", str(bare), "main")
        before = git(self.inst, "rev-parse", "HEAD")
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), \
                mock.patch.object(aib_ext, "reload_room_after_install", return_value=None):
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
            self.assertEqual(res["result"], "paused", res)
            # 本機環境變數 / 本機檔都推不動:暫停只看遠端官方 main
            res = self.run_check(env=dict(self.env, AIB_AUTO_UPDATE="1", AIB_AUTO_UPDATE_FORCE="1"),
                                 plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)
            self.assertEqual(res["result"], "paused", res)
        self.assertEqual((self.updates, git(self.inst, "rev-parse", "HEAD")), ([], before))
        self.assertEqual(self.installed_version("demo-paid"), "1.0.0")
        self.assertIn("aib.auto_update.why.paused", json.loads((REPO / "i18n" / "en.json").read_text(encoding="utf-8")))

    def test_x2c_source_name_mismatch_not_installed(self):
        # 倉裡 demo-paid/ 的 manifest 寫別的名字 ⇒ 不算落後、不裝(裝的那步用 manifest 名，會變成裝到沒裝過的外掛)
        self.paid_repo({"demo-paid": "1.0.3"}, meta={"demo-paid": {"name": "evil"}})
        self.make_repos(latest=None)
        res, _ = self._paid_check_signed()
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res.get("plugins_behind") or [], [])
        self.assertEqual(self._state_dirs(), ["plugins/demo-paid"])
        self.assertEqual(self.installed_version("demo-paid"), "1.0.0")

    def test_x2d_source_kind_not_plugin_not_installed(self):
        self.paid_repo({"demo-paid": "1.0.3"}, meta={"demo-paid": {"kind": "skill"}})
        self.make_repos(latest=None)
        res, _ = self._paid_check_signed()
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res.get("plugins_behind") or [], [])
        self.assertEqual(self._state_dirs(), ["plugins/demo-paid"])

    def test_x2e_same_or_older_version_not_reinstalled(self):
        self.paid_repo({"demo-paid": "1.0.0", "other": "0.9.0"})
        self.make_repos(latest=None)
        res, _ = self._paid_check_signed()
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res.get("plugins_behind") or [], [])
        self.assertEqual((self.installed_version("demo-paid"), self.installed_version("other")), ("1.0.0", "1.0.0"))
        self.assertEqual(self.leases, [])

    def test_x5_user_files_in_plugin_not_overwritten(self):
        # 付費外掛只列名 ⇒ 使用者自己加 / 改的檔原樣在;附帶外掛改過的照舊列 plugins_modified(見 X5E)
        self.paid_repo({"demo-paid": "1.0.3", "other": "1.0.3"})
        root = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins"
        (root / "demo-paid" / "user-notes.txt").write_text("mine", encoding="utf-8")
        (root / "other" / "m.py").write_text("VERSION = 'edited by me'\n", encoding="utf-8")
        self.make_repos(latest=None)
        res, _ = self._paid_check_signed()
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(sorted(res["plugins_manual_behind"]), ["demo-paid", "other"])
        self.assertEqual((root / "demo-paid" / "user-notes.txt").read_text(encoding="utf-8"), "mine")
        self.assertIn("edited by me", (root / "other" / "m.py").read_text(encoding="utf-8"))
        self.assertEqual((self.installed_version("demo-paid"), self.installed_version("other")), ("1.0.0", "1.0.0"))

    def test_x5b_keep_dirs_filter(self):
        # 宣告的 keep 夾只收一層、安全的名字(附帶外掛換版時用;換版本身見 X5D)
        self.assertEqual(AU.keep_dirs({"keep": [".venv", "../x", "a/b", "..", "aib-ext.json", 3]}), [".venv"])

    def test_x5c_enabled_state_kept(self):
        # 停用的外掛換版後仍停用;啟用過哪幾家就只帶那幾家
        import aib_ext
        self.paid_repo({"demo-paid": "1.0.3"})
        folder = self.td / "plugsrc" / "demo-paid"
        calls = []
        fake = lambda f, **kw: calls.append(kw) or 0
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), mock.patch.object(aib_ext, "install", side_effect=fake):
            self.assertEqual(AU.install_plugin(str(folder), io.StringIO()), 0)
            aib_ext._write_state("demo-paid", {"enabled": {"codex": {"files": []}}})
            self.assertEqual(AU.install_plugin(str(folder), io.StringIO()), 0)
        self.assertEqual([(c["enable_engines"], c["engines"]) for c in calls], [(False, None), (True, ["codex"])])
        self.assertTrue(all(c["allow_unsigned"] is False for c in calls))

    def test_x5d_keep_dir_wins_over_same_name_in_new_version(self):
        # 新版包自己也帶同名的 keep 夾 ⇒ 以使用者那份為準、不丟;沒裝成 ⇒ 搬回原處;暫存夾不留
        import aib_ext
        dst = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins" / "demo"
        (dst / "data").mkdir(parents=True)
        (dst / "data" / "user.db").write_text("USERDATA", encoding="utf-8")

        def new_version(folder, **kw):
            shutil.rmtree(dst)
            (dst / "data").mkdir(parents=True)
            (dst / "data" / "defaults.json").write_text("{}", encoding="utf-8")
            return 0

        m = {"name": "demo", "kind": "plugin", "keep": ["data"]}
        with mock.patch.object(aib_ext, "install", side_effect=new_version):
            self.assertEqual(aib_ext.install_keeping("x", m, dst, out=io.StringIO()), 0)
        self.assertEqual(sorted(p.name for p in (dst / "data").iterdir()), ["user.db"])
        self.assertEqual((dst / "data" / "user.db").read_text(encoding="utf-8"), "USERDATA")
        with mock.patch.object(aib_ext, "install", return_value=3):
            self.assertEqual(aib_ext.install_keeping("x", m, dst, out=io.StringIO()), 3)
        self.assertEqual((dst / "data" / "user.db").read_text(encoding="utf-8"), "USERDATA")
        self.assertEqual([p.name for p in dst.parent.iterdir() if p.name.startswith(".keep-")], [])

    def test_x5e_bundled_auto_guarded_when_run_by_auto_update(self):
        # 核心自動更新跑 aib update ⇒ install.sh 用新版 aib ext auto 換附帶外掛:帶 AIB_EXT_AUTO_GUARD=1 時
        # 使用者加 / 改過檔的不換、停用的不打開;沒帶(本人手動 aib update)照舊
        import aib_ext
        ext = self.td / "extensions"
        root = Path(self.env["EZ2_AIB_STATE_ROOT"]) / "plugins"
        for name in ("bund", "quiet"):
            d = ext / name
            d.mkdir(parents=True)
            (d / "m.py").write_text("V = 2\n")
            (d / "aib-ext.json").write_text(json.dumps({"name": name, "kind": "plugin", "version": "1.1.0",
                                                        "files": aib_ext._hashes(d), "signature": "ed25519:test"}))
            inst = root / name
            inst.mkdir(parents=True)
            (inst / "m.py").write_text("V = 1\n")
            (inst / "aib-ext.json").write_text(json.dumps({"name": name, "kind": "plugin", "version": "1.0.0",
                                                           "files": aib_ext._hashes(inst)}))
        (root / "bund" / "notes.txt").write_text("mine", encoding="utf-8")       # 使用者自己加的檔
        calls = []
        fake = lambda f, **kw: calls.append((Path(f).name, kw.get("enable_engines", True))) or 0
        guard = {"AIB_EXT_AUTO_GUARD": "1"}
        out = io.StringIO()
        with mock.patch.object(aib_ext, "signed_ok", return_value=True), mock.patch.object(aib_ext, "install", side_effect=fake):
            aib_ext.auto_install(out=out, src=ext, env=guard)
            self.assertEqual(calls, [("quiet", False)])          # bund 不換;quiet 停用中 ⇒ 換了也不打開
            self.assertIn("bund", out.getvalue())
            calls.clear()
            aib_ext.auto_install(out=io.StringIO(), src=ext, env={})
            self.assertEqual([c[0] for c in calls], ["bund", "quiet"])
        self.assertEqual((root / "bund" / "notes.txt").read_text(encoding="utf-8"), "mine")
        e = AU._child_env({"AIB_EXT_AUTO": "0", "AIB_EXT_AUTO_GUARD": "0", "PATH": "/usr/bin"})
        self.assertEqual((e["AIB_EXT_AUTO"], e["AIB_EXT_AUTO_GUARD"]), ("0", "1"))   # 本人的全關照帶;守門一律開

    def test_x2b_signature_failure_not_installed(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        self.make_repos(latest=None)
        res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=AU.install_plugin)   # 真的 signed_ok:假簽章驗不過
        self.assertEqual(res["result"], "up_to_date", res)
        self.assertEqual(res["plugins_unsigned"], ["demo-paid"])
        self.assertEqual(self.installed_version("demo-paid"), "1.0.0")
        self.assertEqual(self.leases, [])
        # 就算落後清單被動了手腳,裝的那一步自己也驗簽章:驗不過 rc 3、不叫 aib_ext.install
        folder = self.td / "plugsrc" / "demo-paid"
        import aib_ext
        with mock.patch.object(aib_ext, "install") as inst:
            self.assertEqual(AU.install_plugin(str(folder), io.StringIO()), 3)
            inst.assert_not_called()

    def test_x3_core_update_paid_still_listed_and_offline(self):
        self.paid_repo({"demo-paid": "1.0.3"})
        self.make_repos()
        import aib_ext
        with mock.patch.object(aib_ext, "signed_ok", return_value=True):
            res = self.run_check(plugin_fetch=AU.fetch_plugin_source, install=self.fake_install)
            self.assertEqual((res["result"], res["plugins_manual_behind"]), ("updated", ["demo-paid"]))
            self.assertEqual((len(self.updates), self.installs), (1, []))
            res = self.run_check(now=NOW + 7200, plugin_fetch=AU.fetch_plugin_source, install=self.fake_install)
            self.assertEqual((res["result"], res["plugins_manual_behind"]), ("up_to_date", ["demo-paid"]))
            self.assertEqual((len(self.updates), self.installs), (1, []))     # 下一次查也不換付費外掛
        res = self.run_check(now=NOW + 99999, plugin_fetch=lambda e: None)
        self.assertEqual((res["result"], res["plugin_source"]), ("up_to_date", "offline"))
        self.assertIn("aib-plugins", "\n".join(AU.status_lines(AU.status_payload(self.env, platform="linux"))))


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


class Audit(Base):
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
                  "up_to_date", "failed", "updated", "plugins_failed", "disabled_env", "paused") + tuple(
                "refused_" + w for w in ("lease", "tests", "updating", "shipping", "push", "dirty", "diverged", "branch", "far_behind")):
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

    def main_json(self, *argv):
        out = io.StringIO()
        rc = AU.main(list(argv) + ["--json"], out=out)
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, (argv, out.getvalue()))
        return rc, json.loads(lines[0])

    def test_j1_json_one_object_every_path(self):
        """[B-M2] --json:on / off / exclude add / remove / list / status / check(含丟例外)每次只印一個 JSON;launchctl 換假的。"""
        ctl = FakeCtl()
        with mock.patch.object(AU, "_launchctl", ctl), mock.patch.object(AU.sys, "platform", "darwin"), \
                mock.patch.object(AU, "dev_worktree", return_value=None):
            rc, d = self.main_json("off")
            self.assertEqual((rc, d["action"], d["rc"]), (0, "off", 0))
            rc, d = self.main_json("on")
            self.assertEqual((d["action"], d["rc"]), ("on", rc))
            self.assertTrue(d["lines"])
        self.assertTrue(ctl.calls and all(c[0] in ("bootout", "bootstrap") for c in ctl.calls))
        rc, d = self.main_json("exclude", "add", "demo")
        self.assertEqual((rc, d["action"]), (0, "exclude_add"))
        rc, d = self.main_json("exclude", "remove", "demo")
        self.assertEqual((rc, d["action"]), (0, "exclude_remove"))
        rc, d = self.main_json("exclude", "list")
        self.assertIn("exclude", d)
        rc, d = self.main_json("status")
        self.assertIn("on", d)
        with mock.patch.object(AU, "check", side_effect=RuntimeError("boom")):
            rc, d = self.main_json("check")
        self.assertEqual((rc, d["result"], d["error"]), (1, "error", "RuntimeError"))
        with mock.patch.object(AU, "check", return_value={"result": "up_to_date"}):
            rc, d = self.main_json("check")
        self.assertEqual((rc, d), (0, {"result": "up_to_date"}))

    def test_j2_seat_open_waits_before_check(self):
        """[B-L] 開席那次 detach 後先等(60–90 秒,可注入)再查;排程那次不等。"""
        waits, order = [], []
        with mock.patch.object(AU, "_detach", lambda: order.append("detach")), \
                mock.patch.object(AU, "check", side_effect=lambda why: order.append("check:" + why) or {"result": "off"}):
            AU.main(["check", "--why", "seat-open", "--detach"], out=io.StringIO(), sleep=lambda s: order.append("sleep") or waits.append(s))
            AU.main(["check", "--why", "schedule", "--detach"], out=io.StringIO(), sleep=lambda s: waits.append(s))
            AU.main(["check", "--why", "seat-open", "--detach"], out=io.StringIO(), sleep=waits.append, wait_s=0)
        self.assertEqual(order[:3], ["detach", "sleep", "check:seat-open"])
        self.assertEqual(len(waits), 2)
        self.assertTrue(60 <= waits[0] <= 90, waits)
        self.assertEqual(waits[1], 0)

    def test_u1_uninstall_removes_auto_update_agent(self):
        """[A-L2] aib uninstall 一併列出 / 拆自動更新排程(plist)。"""
        sys.path.insert(0, str(REPO / "tools" / "support"))
        import uninstall as UN
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        plist = agents / (AU.AGENT_LABEL + ".plist")
        plist.write_bytes(b"<plist/>")
        self.assertEqual(UN.AUTO_UPDATE_LABEL, AU.AGENT_LABEL)
        pkg = self.td / "devcopy" / "aib"
        pkg.mkdir(parents=True)
        remove = UN.plan(self.home, pkg, False)[0]
        self.assertIn(plist, [p for p, _ in remove])


if __name__ == "__main__":
    unittest.main()
