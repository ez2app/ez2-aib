#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib-boards seat-info 過熱熱修(11-12,a63ab 01:06 定):只掃本席樹、90 秒快取、命中不跑 git。

治具:暫存夾裡一個真 git 倉(遠端是本地 bare 倉)+ 三棵工作樹:
  本席分支樹 feat/x-<席>、別席分支樹 feat/y-<別席>、路徑帶本席碼的乾淨 detached 對照樹。
  cockpit / tools/verify 用符號連結指回本倉(seat-info 用 cockpit/../.. 當倉根)。

H1 單席只掃自己的樹:dev 段只列本席分支樹;別席的樹不在,乾淨 detached 對照樹照舊略過。
   刀:拿掉 _si_own_worktrees 的席碼過濾 ⇒ 別席的樹出現 ⇒ H1 紅。
H2 快取命中不跑 git:第二次跑(90 秒內、指紋沒變)cache=hit,假 git 記錄裡沒有任何一次呼叫。
   刀:命中條件改成 < 0(永不命中)⇒ 第二次會叫 git ⇒ H2 紅。
H3 失效會重掃:命中後在本席樹再提交一次 ⇒ cache=miss、ahead 從 1 變 2。
   刀:指紋不收各樹的 HEAD / ref ⇒ 提交後仍命中、ahead 停在 1 ⇒ H3 紅。
H4 integ 段非整合席不跑:只帶 --dev ⇒ integ 是 None,假 git 記錄裡沒有 worktree list。
   刀:`if INTEG:` 改成 `if True:` ⇒ H4 紅。
H5 TTL 過了就重算:AIB_SEATINFO_CACHE_TTL=0 ⇒ 每次都 miss。
H6 QA 段同一套:--qa 只列本席的樹,第二次命中。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = REPO.parent / "extensions" / "aib-boards" / "bin" / "seat-info.py"
CABIN = "20261112-010000-7e7e"
CODE = "e9e9e"
OTHER = "b5b5b"
UUID = "aaaaaaaa-1111-4111-8111-1111111" + CODE
REAL_GIT = shutil.which("git")


def git(cwd, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run([REAL_GIT, "-C", str(cwd), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True, env=env).stdout.strip()


class SeatInfoHeat(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-seatheat-")
        self.addCleanup(td.cleanup)
        self.tmp = Path(os.path.realpath(td.name))
        seats = self.tmp / "instances" / CABIN / "seats"
        seats.mkdir(parents=True)
        (seats / "w5.json").write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": UUID, "role": "dev",
                                                  "window_id": "@5", "cabin_id": CABIN, "started_epoch": 1}))
        # 倉 + 遠端
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README").write_text("x\n")
        git(self.repo, "add", "README")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        (self.repo / "aib" / "src").mkdir(parents=True)
        (self.repo / "aib" / "tools").mkdir(parents=True)
        os.symlink(REPO / "src" / "cockpit", self.repo / "aib" / "src" / "cockpit")
        os.symlink(REPO / "tools" / "verify", self.repo / "aib" / "tools" / "verify")
        (self.repo / ".git" / "info" / "exclude").write_text("aib/\n")
        wt = self.tmp / "wt"
        self.own = wt / f"x-{CODE}"
        git(self.repo, "worktree", "add", "-q", "-b", f"feat/x-{CODE}", str(self.own))
        (self.own / "a.txt").write_text("a\n")
        git(self.own, "add", "a.txt")
        git(self.own, "commit", "-q", "-m", "own 1")
        self.other = wt / f"y-{OTHER}"
        git(self.repo, "worktree", "add", "-q", "-b", f"feat/y-{OTHER}", str(self.other))
        (self.other / "b.txt").write_text("b\n")
        git(self.other, "add", "b.txt")
        git(self.other, "commit", "-q", "-m", "other 1")
        self.ref = wt / f"ref-{CODE}"
        git(self.repo, "worktree", "add", "-q", "--detach", str(self.ref), "main")
        # 記錄 git 呼叫的假 git(之後再接真 git)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "git-calls.log"
        (self.bin / "git").write_text(f'#!/bin/sh\necho "$*" >> "{self.log}"\nexec "{REAL_GIT}" "$@"\n')
        (self.bin / "git").chmod(0o755)

    def run_probe(self, *flags, extra=None, log_git=False):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "AIB_", "CLAUDE_CODE_", "EZ2_", "CODEX_"))}
        base.update({"EZ2_AIB_STATE_ROOT": str(self.tmp), "HOME": str(self.tmp),
                     "EZ2_COCKPIT_DIR": str(self.repo / "aib" / "src" / "cockpit"),
                     "EZ2_VERIFY_SHADOW_DIR": str(self.tmp / "shadow"),
                     "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        if log_git:
            base["PATH"] = f"{self.bin}{os.pathsep}{base.get('PATH', '')}"
            self.log.write_text("")
        base.update(extra or {})
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), UUID, *flags], env=base, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def git_calls(self):
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()] if self.log.exists() else []

    def paths(self, got):
        return sorted(os.path.realpath(t["path"]) for t in got["dev"]["trees"])

    def test_h1_only_own_trees(self):
        got = self.run_probe("--dev")
        self.assertEqual(got["code"], CODE)
        self.assertEqual(got["dev"]["err"], [])
        self.assertEqual(self.paths(got), [str(self.own)])
        t = got["dev"]["trees"][0]
        self.assertEqual((t["branch"], t["ahead"], t["behind"]), (f"feat/x-{CODE}", 1, 0))

    def test_h2_cache_hit_runs_no_git(self):
        first = self.run_probe("--dev")
        self.assertEqual(first["dev"]["cache"], "miss")
        self.assertTrue((self.tmp / "state" / "seat-info-cache" / f"{CODE}.json").is_file())
        second = self.run_probe("--dev", log_git=True)
        self.assertEqual(second["dev"]["cache"], "hit")
        self.assertGreaterEqual(second["dev"]["cache_age_s"], 0)
        self.assertEqual(first["dev"]["cache_age_s"], 0)
        self.assertEqual(self.git_calls(), [])
        self.assertEqual(self.paths(second), self.paths(first))

    def test_h3_new_commit_invalidates(self):
        self.run_probe("--dev")
        self.assertEqual(self.run_probe("--dev")["dev"]["cache"], "hit")
        (self.own / "c.txt").write_text("c\n")
        git(self.own, "add", "c.txt")
        git(self.own, "commit", "-q", "-m", "own 2")
        got = self.run_probe("--dev")
        self.assertEqual(got["dev"]["cache"], "miss")
        self.assertEqual(got["dev"]["trees"][0]["ahead"], 2)
        self.assertEqual(got["dev"]["trees"][0]["subject"], "own 2")

    def test_h4_integ_not_run_for_dev_seat(self):
        got = self.run_probe("--dev", log_git=True)
        self.assertIsNone(got["integ"])
        self.assertFalse([c for c in self.git_calls() if "worktree list" in c], self.git_calls())

    def test_h5_ttl_zero_always_recomputes(self):
        env = {"AIB_SEATINFO_CACHE_TTL": "0"}
        self.run_probe("--dev", extra=env)
        self.assertEqual(self.run_probe("--dev", extra=env)["dev"]["cache"], "miss")

    def test_h6_qa_same_rules(self):
        shadow = self.tmp / "shadow"
        shadow.mkdir()
        sys.path.insert(0, str(REPO / "tools" / "verify"))
        import reuse_index  # noqa: E402
        rid = reuse_index.repo_identity(str(self.repo / "aib"))
        rows = [{"test": "aib/tests/t1.py", "repo": str(p), "rc": 0, "measured": m, "time_utc": "2099-01-01T00:00:00Z"}
                for p, m in ((self.own, False), (self.other, False))]
        (shadow / f"verify-{rid}.rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        got = self.run_probe("--qa")
        self.assertEqual(got["qa"]["cache"], "miss")
        self.assertEqual([u["branch"] for u in got["qa"]["unverified"]], [f"feat/x-{CODE}"])
        self.assertEqual(got["qa"]["unverified"][0]["unmeasured"], 1)
        self.assertEqual(self.run_probe("--qa")["qa"]["cache"], "hit")


if __name__ == "__main__":
    unittest.main()
