# SPDX-License-Identifier: Apache-2.0
"""11-08 PROPOSAL-19:只改文件的 PR 只跑文件檢查(aib/tools/release/docs_only.py + ci.yml 的 docs job)。

守:
- 白名單(根目錄 docs/**.md、SECURITY*.md、CODE_OF_CONDUCT*.md),其他都算程式;
- main 推送 / 混合改動 / 算不出來 / 0 個檔 ⇒ false(寧可多跑);改名兩邊都要在名單內;
- 文件檢查:相對連結、空檔、CRLF(隱形字元交給 main 那支測試,照 MD_SCAN_TESTS 直接跑);刪除 / 改名整份跑連結;
- **守門**:程式 / 測試裡寫到名單內檔名 ⇒ 紅(之後有人讀 docs/ 底下的檔,要把那個檔移出名單);
- ci.yml:docs job、smoke / fresh-machine needs docs、!cancelled() + 上游沒過就判紅、只改文件換 ubuntu 不跑。
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "aib" / "tools" / "release" / "docs_only.py"
spec = importlib.util.spec_from_file_location("docs_only", TOOL)
do = importlib.util.module_from_spec(spec)
spec.loader.exec_module(do)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "LC_ALL": "C"}

# 守門要掃的地方(程式 / 測試 / 設定;不掃 .md 本身)
SCAN_ROOTS = ("tests/", "aib/tests/", "aib/src/", "aib/tools/", "aib/policy/", "aib/i18n/", "crates/", "xtask/",
              "scripts/", "bin/", "extensions/", ".github/", "install.sh", "pyproject.toml")
# 已看過、確定不是「讀這個檔」的提及(只是註解指路)。新增一條要寫原因。
SCAN_ALLOW = {
    ("aib/src/cockpit/se_vault.py", "CF-GRANT.md"): "docstring 裡「見 docs/CF-GRANT.md」,不讀檔",
}
SCAN_SKIP = {"aib/tools/release/docs_only.py", "aib/tests/test-docs-only-ci-1ced7-1108.py"}


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd)] + list(a), capture_output=True, text=True, check=True,
                          env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL).stdout.strip()


def cli(*a, cwd=None):
    p = subprocess.run([sys.executable, "-B", str(TOOL)] + list(a), capture_output=True, text=True,
                       cwd=cwd, stdin=subprocess.DEVNULL, timeout=60, env=dict(os.environ, **GIT_ENV))
    return p.returncode, p.stdout, p.stderr


def mentions(text, names):
    """text 裡提到哪些名單內檔名(三種寫法都靠檔名字面抓:'docs/X.md'、Path / "docs" / "X.md"、join("docs", "X.md"))。"""
    return sorted(n for n in names if n in text)


class Whitelist(unittest.TestCase):
    def test_docs(self):
        for p in ("docs/RELEASE-OPERATIONS.md", "docs/adr/0007-pty-conpty.md", "docs/migration/x.md",
                  "SECURITY.md", "SECURITY.ja.md", "CODE_OF_CONDUCT.md"):
            with self.subTest(p=p):
                self.assertTrue(do.is_doc(p))

    def test_not_docs(self):
        for p in ("README.md", "README.zh-TW.md", "CHANGELOG.md", "CHANGELOG.ja.md", "CONTRIBUTING.md",
                  "aib/docs/CTX-THRESHOLDS.md", "aib/docs/templates/SEAT-ONBOARD.md", "aib/docs/tmux-hook-indexes.md",
                  "aib/skills/x/SKILL.md", "aib/src/cockpit/README.md", ".github/workflows/ci.yml", "docs/x.json",
                  "docs/img.png", "aib/i18n/en.json", "sub/SECURITY.md", "docs/../README.md", "/docs/x.md", "",
                  "docsx/a.md", "tests/docs/a.md"):
            with self.subTest(p=p):
                self.assertFalse(do.is_doc(p))


class Decide(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(do.docs_only("push", ["docs/a.md"])[0], False)          # main 推送永遠 false
        self.assertEqual(do.docs_only("pull_request", None)[0], False)           # 讀不到
        self.assertEqual(do.docs_only("pull_request", [])[0], False)             # 0 個檔
        self.assertEqual(do.docs_only("pull_request", ["docs/a.md", "aib/x.py"])[0], False)   # 混合
        self.assertEqual(do.docs_only("pull_request", ["docs/a.md", "SECURITY.md"])[0], True)

    def _repo(self, td):
        r = Path(td) / "r"
        r.mkdir()
        git(r, "init", "-q", "-b", "main")
        (r / "docs").mkdir()
        (r / "aib" / "docs").mkdir(parents=True)
        (r / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
        (r / "aib" / "docs" / "t.md").write_text("# t\n", encoding="utf-8")
        (r / "x.py").write_text("x = 1\n", encoding="utf-8")
        git(r, "add", "-A")
        git(r, "commit", "-qm", "base")
        return r, git(r, "rev-parse", "HEAD")

    def test_cli_on_a_real_repo(self):
        with tempfile.TemporaryDirectory() as td:
            r, base = self._repo(td)
            git(r, "checkout", "-qb", "b")
            (r / "docs" / "a.md").write_text("# a\n\nmore\n", encoding="utf-8")
            git(r, "commit", "-qam", "docs")
            head = git(r, "rev-parse", "HEAD")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", head, "--root", str(r))
            self.assertEqual((rc, out.strip()), (0, "docs_only=true"))
            rc, out, _ = cli("decide", "--event", "push", "--base", base, "--head", head, "--root", str(r))
            self.assertEqual(out.strip(), "docs_only=false")
            # 算不出來(base 不存在)⇒ false
            rc, out, err = cli("decide", "--event", "pull_request", "--base", "0" * 40, "--head", head, "--root", str(r))
            self.assertEqual((rc, out.strip()), (0, "docs_only=false"))
            self.assertIn("could not list", err)
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", "", "--head", head, "--root", str(r))
            self.assertEqual(out.strip(), "docs_only=false")
            # 混合
            (r / "x.py").write_text("x = 2\n", encoding="utf-8")
            git(r, "commit", "-qam", "code")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.strip(), "docs_only=false")

    def test_rename_out_of_a_non_doc_dir_is_not_docs_only(self):
        with tempfile.TemporaryDirectory() as td:
            r, base = self._repo(td)
            git(r, "mv", "aib/docs/t.md", "docs/t.md")
            git(r, "commit", "-qm", "mv")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.strip(), "docs_only=false", "改名要把舊路徑也算進來(--no-renames)")


class Check(unittest.TestCase):
    def _run(self, files):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            (r / "docs" / "sub").mkdir(parents=True)
            (r / "docs" / "here.md").write_text("# here\n", encoding="utf-8")
            git(r, "init", "-q", "-b", "main")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            for rel, data in files.items():
                p = r / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            git(r, "add", "-A")
            git(r, "commit", "-qm", "change")
            return cli("check", "--base", base, "--head", "HEAD", "--root", str(r))

    def test_good_file_passes(self):
        body = ("# ok\n\n[here](here.md) [up](../docs/here.md#x) [web](https://example.com) [anchor](#top)\n"
                "```\n[not a link](missing.md)\n```\n").encode()
        rc, out, _ = self._run({"docs/sub/../ok.md": body})
        self.assertEqual(rc, 0, out)

    def test_each_problem_is_red(self):
        cases = {
            "broken relative link": b"# x\n\n[gone](nope.md)\n",
            "CR line endings": b"# x\r\n\r\nline\r\n",
            "empty": b"\n\n",
        }
        for want, data in cases.items():
            with self.subTest(want=want):
                rc, out, _ = self._run({"docs/bad.md": data})
                self.assertEqual(rc, 1, out)
                self.assertIn(want, out)

    def test_md_scan_tests_list_is_complete(self):
        # 043dd #504 High / a63ab:只改文件時直接跑 main 上掃 .md 的測試,不自己重寫。
        # 「讀 git ls-files 又看 .md」的測試都要在 MD_SCAN_TESTS(之後新增一支這種測試卻沒列進來 ⇒ 紅)
        found = []
        for f in sorted((ROOT / "aib" / "tests").glob("test-*.py")) + sorted((ROOT / "tests").glob("test_*.py")):
            rel = f.relative_to(ROOT).as_posix()
            if rel == "aib/tests/test-docs-only-ci-1ced7-1108.py":
                continue
            t = f.read_text(encoding="utf-8")
            if "ls-files" in t and re.search(r"[\"']\.md[\"']", t):
                found.append(rel)
        self.assertEqual(sorted(do.MD_SCAN_TESTS), found)
        for t in do.MD_SCAN_TESTS:
            self.assertTrue((ROOT / t).is_file(), t)
        rc, out, _ = cli("md-tests")
        self.assertEqual((rc, out.split()), (0, list(do.MD_SCAN_TESTS)))

    def test_a_delete_rechecks_every_whitelisted_doc(self):
        # a63ab 裁示「有刪除 / 改名就整份跑」:沒改到的白名單文件裡本來就斷的連結也要報
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            (r / "docs").mkdir(parents=True)
            (r / "docs" / "gone.md").write_text("# gone\n", encoding="utf-8")
            (r / "docs" / "other.md").write_text("# other\n\n[x](missing.md)\n", encoding="utf-8")
            git(r, "init", "-q", "-b", "main")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            git(r, "rm", "-q", "docs/gone.md")
            git(r, "commit", "-qm", "rm")
            rc, out, _ = cli("check", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(rc, 1, out)
            self.assertIn("docs/other.md:3: broken relative link missing.md", out)

    def test_deleting_a_doc_that_another_md_links_to_is_red(self):
        # 043dd #504 Medium:刪 / 改名文件,別的 md 連過來的連結斷了要查得到
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            (r / "docs").mkdir(parents=True)
            (r / "docs" / "gone.md").write_text("# gone\n", encoding="utf-8")
            (r / "docs" / "keep.md").write_text("# keep\n\n[g](gone.md#x)\n", encoding="utf-8")
            (r / "README.md").write_text("# r\n\n[g](docs/gone.md)\n[already broken](nope.md)\n", encoding="utf-8")
            git(r, "init", "-q", "-b", "main")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            git(r, "rm", "-q", "docs/gone.md")
            git(r, "commit", "-qm", "rm")
            rc, out, _ = cli("check", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(rc, 1, out)
            self.assertIn("docs/keep.md:3: broken relative link gone.md#x", out)
            self.assertIn("README.md:3: links to deleted file docs/gone.md", out)
            self.assertNotIn("nope.md", out, "白名單外的 md 只報連到這次刪掉的檔,本來就斷的不算")

    def test_check_refuses_a_change_that_is_not_docs_only(self):
        rc, out, _ = self._run({"docs/ok.md": b"# ok\n", "x.py": b"x = 1\n"})
        self.assertEqual(rc, 1)
        self.assertIn("not docs-only", out)


class Guard(unittest.TestCase):
    """程式 / 測試寫到名單內的檔名 ⇒ 紅;這種檔不能算「純文件」。"""

    def test_scanner_catches_the_three_path_forms(self):
        names = {"RELEASE-OPERATIONS.md"}
        for text in ('open("docs/RELEASE-OPERATIONS.md")', 'ROOT / "docs" / "RELEASE-OPERATIONS.md"',
                     'os.path.join(ROOT, "docs", "RELEASE-OPERATIONS.md")'):
            with self.subTest(text=text):
                self.assertEqual(mentions(text, names), ["RELEASE-OPERATIONS.md"])

    def test_no_code_or_test_reads_a_whitelisted_doc(self):
        files = git(ROOT, "ls-files").splitlines()
        docs = [f for f in files if do.is_doc(f)]
        self.assertTrue(docs, "名單內一個檔都沒有:白名單或 ls-files 壞了")
        names = {os.path.basename(f) for f in docs}
        hits = []
        for f in files:
            if not f.startswith(SCAN_ROOTS) or f.endswith(".md") or f in SCAN_SKIP:
                continue
            try:
                text = (ROOT / f).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for n in mentions(text, names):
                if (f, n) not in SCAN_ALLOW:
                    hits.append("%s mentions %s" % (f, n))
        self.assertEqual(hits, [], "這些檔被程式 / 測試提到:程式真的讀它 ⇒ 加進 docs_only.py 的 DOC_EXCLUDE(寫誰讀);"
                                   "只有註解提到 ⇒ 加進這裡的 SCAN_ALLOW(寫原因)")

    def test_a_doc_that_code_reads_is_excluded_and_the_exclusion_is_still_read(self):
        """a63ab #510:docs/perf/timers.md 被 timers.rs 的測試讀 ⇒ 不算純文件;排除名單每一條都要真的還有人提到。"""
        self.assertFalse(do.is_doc("docs/perf/timers.md"))
        self.assertEqual(do.docs_only("pull_request", ["docs/perf/timers.md"])[0], False)
        self.assertTrue(do.is_doc("docs/perf/other.md"))
        files = git(ROOT, "ls-files").splitlines()
        for ex in do.DOC_EXCLUDE:
            with self.subTest(ex=ex):
                self.assertIn(ex, files, "排除名單裡的檔不在倉裡:拿掉這一條")
                name = os.path.basename(ex)
                readers = [f for f in files if f.startswith(SCAN_ROOTS) and not f.endswith(".md")
                           and f not in SCAN_SKIP and name in mentions((ROOT / f).read_text(encoding="utf-8",
                                                                                         errors="replace"), {name})]
                self.assertTrue(readers, "沒有程式再讀 %s:移回白名單" % ex)

    def test_allow_list_entries_still_exist(self):
        for (f, n), why in SCAN_ALLOW.items():
            with self.subTest(f=f):
                self.assertTrue(why)
                self.assertIn(n, (ROOT / f).read_text(encoding="utf-8"), "SCAN_ALLOW 那條已經不在了,拿掉")


def job_text(name):
    t = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)" % re.escape(name), t)
    return m.group(1)


class Workflow(unittest.TestCase):
    def test_docs_job(self):
        t = job_text("docs")
        self.assertIn("runs-on: ubuntu-latest", t)
        self.assertIn("fetch-depth: 0", t)
        self.assertIn('git show "$BASE:$tool"', t, "判斷腳本優先用 base 的版本")
        self.assertIn("|| out=docs_only=false", t, "腳本失敗 ⇒ false")
        self.assertIn("*) out=docs_only=false", t, "輸出不是 docs_only=true 就當 false")
        self.assertIn("docs_only.py check", t)
        self.assertIn("if: steps.d.outputs.docs_only == 'true'", t)
        self.assertIn("docs_only.py md-tests", t, "只改文件時照清單直接跑 main 掃 .md 的測試")
        self.assertIn('python3 "$t" || {', t)

    def test_required_cells_depend_on_docs_and_propagate_failure(self):
        for job in ("smoke", "fresh-machine"):
            with self.subTest(job=job):
                t = job_text(job)
                self.assertRegex(t, r"(?m)^    needs: \[tip, docs\]$")
                # 11-08 DECISION-24(#512):後面可再接「只改標題的 edited 不跑」那句,開頭仍要是 !cancelled()
                self.assertRegex(t, r"(?m)^    if: \$\{\{ !cancelled\(\) (?:&& [^\n]*)?\}\}$", "docs 紅時也要跑到判紅那步")
                self.assertNotRegex(t, r"(?m)^    if: [^\n]*(?:always\(\)|\|\|)", "if 只能再加 && 條件,不能用 || / always() 把 !cancelled() 蓋掉")
                self.assertIn("if: needs.tip.result != 'success' || needs.docs.result != 'success'", t)
                self.assertIn("exit 1", t)
                runs_on = re.search(r"(?m)^    runs-on: (.*)$", t).group(1)
                run_cell = re.search(r"(?m)^      RUN_CELL: (.*)$", t).group(1)
                self.assertIn("needs.docs.outputs.docs_only", runs_on)
                self.assertIn("needs.docs.outputs.docs_only != 'true'", run_cell)
                self.assertIn("docs-only change", t)


if __name__ == "__main__":
    unittest.main()
