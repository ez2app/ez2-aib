"""scripts/check-hide-words.py + scripts/hooks/pre-push (E6 L3, 4542e 11-12).

Fake names only (vendor-x, xyz-cloud): the real list is private and never in this repo.
Each test builds a throw-away git repo with a bare remote under a temp dir, writes a fake
list there (mode 600), and runs the checker the way the hook does (refs on stdin).
POSIX only: the permission rule has no meaning on Windows.
"""
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check-hide-words.py"
HOOK = ROOT / "scripts" / "hooks" / "pre-push"
spec = importlib.util.spec_from_file_location("check_hide_words", str(CHECK))
chw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chw)

ZERO = "0" * 40
# line 1 comment, 2 blank, 3 vendor-x (mixed case on purpose), 4 xyz-cloud, 5 a short entry
LIST = "# fake list\n\nVendor-X\nxyz-cloud\nabc\n"
SECRETS = ("vendor-x", "xyz-cloud")


@unittest.skipIf(os.name == "nt", "POSIX permission rule")
class HideWords(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hidewords-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(self.tmp), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        self.private = self.tmp / "private"
        self.private.mkdir(mode=0o700)
        self.list = self.private / "hide-words.txt"
        self.allow = self.private / "hide-words-allow.txt"
        self.write_private(self.list, LIST)
        self.env["EZ2_HIDE_WORDS"] = str(self.list)
        self.env["EZ2_HIDE_WORDS_ALLOW"] = str(self.allow)
        self.remote = self.tmp / "remote.git"
        self.git("init", "-q", "--bare", str(self.remote), cwd=self.tmp)
        self.repo = self.tmp / "repo"
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.tmp)
        self.git("remote", "add", "origin", str(self.remote))
        self.base = self.commit("README", "hello\n", "base")
        self.git("push", "-q", "origin", "main")

    def write_private(self, path, text, mode=0o600):
        path.write_text(text, encoding="utf-8")
        os.chmod(str(path), mode)

    def git(self, *args, cwd=None):
        p = subprocess.run(["git"] + list(args), cwd=str(cwd or self.repo), env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def commit(self, name, text, msg="change"):
        f = self.repo / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def run_check(self, remote_sha=None, ref="refs/heads/main", remote="origin"):
        head = self.git("rev-parse", "HEAD")
        remote_sha = self.base if remote_sha is None else remote_sha
        stdin = "%s %s %s %s\n" % (ref, head, ref, remote_sha)
        p = subprocess.run([sys.executable, "-I", str(CHECK), "--pre-push", remote, str(self.remote)],
                           cwd=str(self.repo), env=self.env, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return p.returncode, p.stdout + p.stderr

    def assertNoSecret(self, out):
        for s in SECRETS:
            self.assertNotIn(s, out.casefold())

    # --- the rules ---------------------------------------------------------------------------

    def test_hit_blocks_and_names_only_file_line_and_entry_number(self):
        self.commit("docs/a.md", "line one\nruns on Vendor-X today\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("docs/a.md:2 命中第 3 個詞", out)
        self.assertNoSecret(out)

    def test_clean_push_passes(self):
        self.commit("docs/a.md", "nothing to see\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertIn("hide-words ok", out)

    def test_entry_shorter_than_four_is_ignored(self):
        self.commit("a.txt", "abc abc ABC\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)

    def test_whole_word_rule(self):
        terms = chw.load_terms([(3, "Vendor-X"), (5, "abc")])
        hit = ["vendor-x", "VENDOR-X", "vendor-x.example", "vendor-x_api", "VENDOR-X_TOKEN",
               "api-vendor-x", "用vendor-x主機", "(vendor-x)", "x=vendor-x;"]
        miss = ["vendor-xyz", "myvendor-x", "vendor-x2", "2vendor-x", "abc"]
        for t in hit:
            self.assertEqual(chw.line_hits(t, terms, []), [3], t)
        for t in miss:
            self.assertEqual(chw.line_hits(t, terms, []), [], t)
        # a near-miss before a real hit on the same line still finds the real one
        self.assertEqual(chw.line_hits("vendor-xyz and vendor-x", terms, []), [3])
        self.commit("b.txt", "vendor-xyz myvendor-x vendor-x2\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)

    def test_allow_list_covers_whole_hit_only(self):
        self.write_private(self.allow, "# fake allow\nthe vendor-x protocol\n")
        self.commit("c.txt", "see The Vendor-X Protocol spec\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.commit("c.txt", "see the vendor-x protocol, and vendor-x itself\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("c.txt:1 命中第 3 個詞", out)
        self.assertNoSecret(out)

    def test_loose_list_mode_refused_without_content(self):
        for mode in (0o644, 0o640, 0o604):
            os.chmod(str(self.list), mode)
            self.commit("d.txt", "vendor-x %o\n" % mode)
            rc, out = self.run_check()
            self.assertEqual(rc, 2, out)
            self.assertIn("權限太寬", out)
            self.assertNoSecret(out)

    def test_loose_allow_mode_refused(self):
        self.write_private(self.allow, "the vendor-x protocol\n", mode=0o644)
        self.commit("d.txt", "nothing\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 2, out)
        self.assertNoSecret(out)

    def test_no_list_skips(self):
        self.list.unlink()
        self.commit("e.txt", "vendor-x\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "hide-words 略過：沒有私有清單")

    def test_path_and_message_hits_never_print_the_entry(self):
        self.commit("hosts/xyz-cloud/notes.txt", "fine\nxyz-cloud again\n", msg="move to XYZ-Cloud")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("檔名", out)
        self.assertIn(chw.HIDDEN_PATH + ":2 命中第 4 個詞", out)
        self.assertIn("commit 訊息第 1 行 命中第 4 個詞", out)
        self.assertNoSecret(out)

    def test_new_binary_file_name_is_scanned(self):
        f = self.repo / "assets" / "vendor-x-logo.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\xff")
        self.git("add", "--", "assets/vendor-x-logo.png")
        self.git("commit", "-q", "-m", "logo")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("檔名", out)
        self.assertNoSecret(out)

    def test_new_empty_file_name_is_scanned(self):
        self.commit("notes/xyz-cloud.txt", "")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertIn("命中第 4 個詞(檔名", out)
        self.assertNoSecret(out)

    def test_deleting_an_already_pushed_hit_file_passes(self):
        self.commit("notes/xyz-cloud.txt", "")
        self.git("push", "-q", "--no-verify", "origin", "main")
        pushed = self.git("rev-parse", "HEAD")
        self.git("rm", "-q", "--", "notes/xyz-cloud.txt")
        self.git("commit", "-q", "-m", "remove")
        rc, out = self.run_check(remote_sha=pushed)
        self.assertEqual(rc, 0, out)
        self.assertNoSecret(out)

    def test_word_added_then_removed_still_blocks(self):
        self.commit("f.txt", "vendor-x\n")
        self.commit("f.txt", "gone\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 1, out)
        self.assertNoSecret(out)

    def test_only_new_commits_are_read(self):
        # already on the remote (pushed before the list existed): not this push's business
        self.commit("g.txt", "vendor-x\n")
        self.git("push", "-q", "origin", "main")
        self.commit("g.txt", "vendor-x\nclean line\n")
        rc, out = self.run_check(remote_sha=self.git("rev-parse", "HEAD~1"))
        self.assertEqual(rc, 0, out)
        # pushing to a bare URL (no remote-tracking refs): the remote's old tip is what limits the read
        rc, out = self.run_check(remote_sha=self.git("rev-parse", "HEAD~1"), remote=str(self.remote))
        self.assertEqual(rc, 0, out)
        # a new branch (remote sha all zeros) reads only what no remote ref has
        self.git("switch", "-q", "-c", "topic")
        self.commit("h.txt", "clean\n")
        rc, out = self.run_check(remote_sha=ZERO, ref="refs/heads/topic")
        self.assertEqual(rc, 0, out)  # main's unpushed commit only added line 2 of g.txt

    def test_merge_reads_only_lines_in_no_parent(self):
        self.git("switch", "-q", "-c", "side")
        self.commit("m.txt", "side line\n")
        self.git("push", "-q", "origin", "side")
        self.git("switch", "-q", "main")
        self.commit("n.txt", "main line\n")
        self.git("push", "-q", "origin", "main")
        self.git("merge", "-q", "--no-edit", "side")
        rc, out = self.run_check(remote_sha=self.git("rev-parse", "HEAD~1"))
        self.assertEqual(rc, 0, out)
        # an "evil merge" line (in no parent) is read
        (self.repo / "m.txt").write_text("side line\nvendor-x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--amend", "--no-edit")
        rc, out = self.run_check(remote_sha=self.git("rev-parse", "HEAD~1"))
        self.assertEqual(rc, 1, out)
        self.assertIn("m.txt:2 命中第 3 個詞", out)

    def test_conflict_merge_keeps_a_parent_line_without_a_hit(self):
        self.commit("k.txt", "one\ntwo\n")
        self.git("push", "-q", "origin", "main")
        self.git("switch", "-q", "-c", "side")
        self.commit("k.txt", "vendor-x side\ntwo\n")  # already on the remote below: not this push's business
        self.git("push", "-q", "origin", "side")
        self.git("switch", "-q", "main")
        self.commit("k.txt", "main\ntwo\n")
        self.git("push", "-q", "origin", "main")
        old = self.git("rev-parse", "HEAD")
        subprocess.run(["git", "merge", "-q", "side"], cwd=str(self.repo), env=self.env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo / "k.txt").write_text("vendor-x side\nmain\ntwo\n", encoding="utf-8")
        self.git("add", "k.txt")
        self.git("commit", "-q", "--no-edit")
        self.assertIn("k.txt", self.git("diff-tree", "--cc", "--name-only", "HEAD"))  # the merge does show a hunk
        rc, out = self.run_check(remote_sha=old)
        self.assertEqual(rc, 0, out)

    def test_hook_blocks_a_real_push(self):
        (self.repo / "scripts").mkdir()
        shutil.copy(str(CHECK), str(self.repo / "scripts" / "check-hide-words.py"))  # untracked: the hook finds it in the tree being pushed
        hooks = self.tmp / "hooks"
        hooks.mkdir()
        os.symlink(str(HOOK), str(hooks / "pre-push"))
        self.git("config", "core.hooksPath", str(hooks))
        self.git("push", "-q", "--no-verify", "origin", "main")  # base already pushed; nothing new
        self.commit("i.txt", "uses xyz-cloud\n")
        p = subprocess.run(["git", "push", "origin", "main"], cwd=str(self.repo), env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertNotEqual(p.returncode, 0, p.stderr)
        self.assertIn("i.txt:1 命中第 4 個詞", p.stderr)
        self.assertNoSecret(p.stdout + p.stderr)
        self.list.unlink()
        p = subprocess.run(["git", "push", "origin", "main"], cwd=str(self.repo), env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("略過：沒有私有清單", p.stdout + p.stderr)

    def test_hook_without_checker_blocks(self):
        hooks = self.tmp / "hooks"
        hooks.mkdir()
        os.symlink(str(HOOK), str(hooks / "pre-push"))
        self.git("config", "core.hooksPath", str(hooks))
        self.commit("j.txt", "clean\n")
        p = subprocess.run(["git", "push", "origin", "main"], cwd=str(self.repo), env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("找不到 scripts/check-hide-words.py", p.stderr)


if __name__ == "__main__":
    unittest.main()
