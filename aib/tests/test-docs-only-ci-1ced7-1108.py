# SPDX-License-Identifier: Apache-2.0
"""11-08 PROPOSAL-19:只改文件的 PR 只跑文件檢查(aib/tools/release/docs_only.py + ci.yml 的 docs job)。

守:
- 白名單(根目錄 docs/**.md、SECURITY*.md、CODE_OF_CONDUCT*.md),其他都算程式;
- main 推送 / 混合改動 / 算不出來 / 0 個檔 ⇒ false(寧可多跑);改名兩邊都要在名單內;
- 文件檢查:相對連結、空檔、CRLF(隱形字元交給 main 那支測試,照 MD_SCAN_TESTS 直接跑);刪除 / 改名整份跑連結;
- **守門**:程式 / 測試裡寫到名單內檔名 ⇒ 紅(之後有人讀 docs/ 底下的檔,要把那個檔移出名單);
- ci.yml:docs job、smoke / fresh-machine needs docs、!cancelled() + 上游沒過就判紅、只改文件換 ubuntu 不跑。
- 11-09 rust_only(Andy「只動 Rust 就跳過」,a63ab 派):crates/**、Cargo.toml、Cargo.lock(+ 白名單文件);
  main 推送 / 混合 / 算不出來 / 動到 .github/** ⇒ false;讀 crates/ / Cargo.* 的測試都要在 RUST_READ_TESTS;
  Rust 範圍每一條都要被 aibd.yml 的 pull_request paths 蓋到(Rust 照樣有人測);
  workflow 的判斷片段實跑(舊版腳本只印 docs_only、印錯 ⇒ false)。
- 11-09 aibd.yml(Andy「Rust 兩格改成必過」):沒有 paths 過濾、每張 PR 都觸發;changes job 用 rust-ci 判斷,
  只有整行 rust_ci=false 才換 ubuntu 只印一行;main 推送 / 算不出 / 0 檔 / 動 .github/** ⇒ 真跑;每一步真跑的步驟都卡 RUN。
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
            self.assertEqual((rc, out.splitlines()), (0, ["docs_only=true", "rust_only=false"]))
            rc, out, _ = cli("decide", "--event", "push", "--base", base, "--head", head, "--root", str(r))
            self.assertEqual(out.splitlines()[0], "docs_only=false")
            # 算不出來(base 不存在)⇒ false
            rc, out, err = cli("decide", "--event", "pull_request", "--base", "0" * 40, "--head", head, "--root", str(r))
            self.assertEqual((rc, out.splitlines()), (0, ["docs_only=false", "rust_only=false"]))
            self.assertIn("could not list", err)
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", "", "--head", head, "--root", str(r))
            self.assertEqual(out.splitlines()[0], "docs_only=false")
            # 混合
            (r / "x.py").write_text("x = 2\n", encoding="utf-8")
            git(r, "commit", "-qam", "code")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.splitlines()[0], "docs_only=false")

    def test_rename_out_of_a_non_doc_dir_is_not_docs_only(self):
        with tempfile.TemporaryDirectory() as td:
            r, base = self._repo(td)
            git(r, "mv", "aib/docs/t.md", "docs/t.md")
            git(r, "commit", "-qm", "mv")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.splitlines()[0], "docs_only=false", "改名要把舊路徑也算進來(--no-renames)")


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
        self.assertIn("neither docs-only nor Rust-only", out)


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


RUST_MENTION = re.compile(r"crates/|[\"']crates[\"']|Cargo\.(?:toml|lock)|[\"']\.rs[\"']")


class RustOnly(unittest.TestCase):
    def test_rules(self):
        T = lambda *f: do.rust_only("pull_request", list(f))[0]
        self.assertFalse(do.rust_only("push", ["crates/aibd/src/main.rs"])[0], "main 推送一律照跑")
        self.assertFalse(do.rust_only("pull_request", None)[0])
        self.assertFalse(do.rust_only("pull_request", [])[0])
        self.assertTrue(T("crates/aibd/src/main.rs"))
        self.assertTrue(T("Cargo.toml", "Cargo.lock", "crates/x/Cargo.toml"))
        self.assertTrue(T("crates/aibd/src/main.rs", "docs/adr/0009.md"), "加白名單文件可以")
        self.assertFalse(T("docs/a.md"), "只有文件 = docs_only 的事,不是 rust_only")
        for other in ("aib/x.py", "README.md", "xtask/src/main.rs", "clippy.toml", "rust-toolchain.toml",
                      "sub/Cargo.toml", "scripts/check-sys-deps.sh", "docs/perf/timers.md", ".github/workflows/aibd.yml",
                      ".github/workflows/ci.yml", "crates/../aib/x.py", "/crates/a.rs", "cratesx/a.rs"):
            with self.subTest(other=other):
                self.assertFalse(T("crates/aibd/src/main.rs", other))

    def test_github_is_never_light_even_if_the_rust_range_is_widened(self):
        # aibd.yml 的 paths 本來就含 .github/workflows/aibd.yml;之後有人照它放寬 Rust 範圍也不能跳過 Python
        saved = do.RUST_PREFIXES
        try:
            do.RUST_PREFIXES = saved + (".github/",)
            self.assertFalse(do.rust_only("pull_request", ["crates/a.rs", ".github/workflows/aibd.yml"])[0])
            self.assertFalse(do.rust_only("pull_request", [".github/workflows/ci.yml"])[0])
        finally:
            do.RUST_PREFIXES = saved

    def test_cli_on_a_real_repo(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            (r / "crates" / "c" / "src").mkdir(parents=True)
            (r / "docs").mkdir()
            (r / "crates" / "c" / "src" / "lib.rs").write_text("pub fn f() {}\n", encoding="utf-8")
            (r / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
            git(r, "init", "-q", "-b", "main")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            (r / "crates" / "c" / "src" / "lib.rs").write_text("pub fn f() -> u8 { 1 }\n", encoding="utf-8")
            (r / "docs" / "a.md").write_text("# a\n\n[ok](a.md)\n", encoding="utf-8")
            git(r, "commit", "-qam", "rust")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual((rc, out.splitlines()), (0, ["docs_only=false", "rust_only=true"]))
            rc, out, _ = cli("decide", "--event", "push", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.splitlines(), ["docs_only=false", "rust_only=false"])
            rc, out, err = cli("decide", "--event", "pull_request", "--base", "0" * 40, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.splitlines(), ["docs_only=false", "rust_only=false"])
            # 文件檢查照樣查這次改到的文件
            rc, out, _ = cli("check", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(rc, 0, out)
            (r / "docs" / "a.md").write_text("# a\n\n[gone](nope.md)\n", encoding="utf-8")
            git(r, "commit", "-qam", "bad link")
            rc, out, _ = cli("check", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(rc, 1, out)
            self.assertIn("broken relative link nope.md", out)
            # 加一個 .github 檔 ⇒ 不再 rust_only,check 也拒跑
            (r / ".github").mkdir()
            (r / ".github" / "x.yml").write_text("x: 1\n", encoding="utf-8")
            (r / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "gh")
            rc, out, _ = cli("decide", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.splitlines(), ["docs_only=false", "rust_only=false"])
            rc, out, _ = cli("check", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(rc, 1, out)

    def test_every_test_that_reads_crates_or_cargo_is_listed(self):
        found = []
        for f in sorted((ROOT / "aib" / "tests").glob("test-*.py")) + sorted((ROOT / "tests").glob("test_*.py")):
            rel = f.relative_to(ROOT).as_posix()
            if RUST_MENTION.search(f.read_text(encoding="utf-8")):
                found.append(rel)
        self.assertEqual(sorted(do.RUST_READ_TESTS), found,
                         "讀 crates/ 或 Cargo.* 的測試要列進 docs_only.py RUST_READ_TESTS(只改 Rust 時直接跑);"
                         "不再讀的拿掉")
        for t in do.RUST_READ_TESTS:
            self.assertTrue((ROOT / t).is_file(), t)
        rc, out, _ = cli("rust-tests")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), list(do.RUST_READ_TESTS) + [t for t in do.MD_SCAN_TESTS if t not in do.RUST_READ_TESTS])

    def test_scanner_catches_the_path_forms(self):
        for text in ('ROOT / "crates" / "aibd"', 'open("crates/aibd/Cargo.toml")', "read('Cargo.lock')",
                     'p.endswith(".rs")'):
            with self.subTest(text=text):
                self.assertTrue(RUST_MENTION.search(text))

    def test_aibd_workflow_covers_the_rust_range(self):
        # rust_only 跳過 Python 的前提:這些檔一改,aibd.yml 一定真跑 Rust 兩格
        for f in do.RUST_FILES:
            self.assertTrue(do.rust_ci("pull_request", [f])[0], f)
            self.assertIn(f, do.RUST_CI_PATHS)
        for pre in do.RUST_PREFIXES:
            self.assertIn(pre + "**", do.RUST_CI_PATHS)
            self.assertTrue(do.rust_ci("pull_request", [pre + "a/b.rs"])[0], pre)


class RustCI(unittest.TestCase):
    """aibd.yml changes job 的判斷:True = 真跑 Rust 兩格。"""

    def test_rules(self):
        R = lambda *f: do.rust_ci("pull_request", list(f))[0]
        self.assertTrue(do.rust_ci("push", ["aib/x.py"])[0], "main 推送一律真跑")
        self.assertTrue(do.rust_ci("pull_request", None)[0], "算不出來 ⇒ 真跑")
        self.assertTrue(do.rust_ci("pull_request", [])[0])
        for f in (".github/workflows/ci.yml", ".github/CODEOWNERS", ".github/workflows/aibd.yml"):
            with self.subTest(f=f):
                self.assertTrue(R(f), "動到 .github/** ⇒ 真跑")
                self.assertTrue(R("aib/x.py", f))
        for f in do.RUST_CI_PATHS:
            with self.subTest(f=f):
                self.assertTrue(R("aib/x.py", f.replace("**", "a/b")), "混合 ⇒ 真跑")
        for f in ("aib/x.py", "docs/a.md", "README.md", "scripts/aibd-api-smoke.py", "cratesx/a.rs",
                  "sub/Cargo.toml", "tests/test_x.py"):
            with self.subTest(f=f):
                self.assertFalse(R(f), "沒動 Rust 路徑 ⇒ 換 ubuntu 印一行")

    def test_path_list_forms(self):
        self.assertEqual(len(set(do.RUST_CI_PATHS)), len(do.RUST_CI_PATHS))
        for pat in do.RUST_CI_PATHS:
            with self.subTest(pat=pat):
                body = pat[:-3] if pat.endswith("/**") else pat
                self.assertTrue(body and not any(c in body for c in "*?[]!{}"), "只准完整檔名或「目錄/**」")
        files = git(ROOT, "ls-files").splitlines()
        for pat in do.RUST_CI_PATHS:
            with self.subTest(exists=pat):
                hit = [f for f in files if f == pat or (pat.endswith("/**") and f.startswith(pat[:-2]))]
                self.assertTrue(hit, "名單裡的路徑倉裡沒有:%s" % pat)
                self.assertTrue(all(do.in_rust_ci(f) for f in hit))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            (r / "crates").mkdir(parents=True)
            (r / "crates" / "a.rs").write_text("x\n", encoding="utf-8")
            (r / "x.py").write_text("x = 1\n", encoding="utf-8")
            git(r, "init", "-q", "-b", "main")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "base")
            base = git(r, "rev-parse", "HEAD")
            (r / "x.py").write_text("x = 2\n", encoding="utf-8")
            git(r, "commit", "-qam", "py")
            rc, out, _ = cli("rust-ci", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual((rc, out.strip()), (0, "rust_ci=false"))
            rc, out, _ = cli("rust-ci", "--event", "push", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.strip(), "rust_ci=true")
            rc, out, _ = cli("rust-ci", "--event", "pull_request", "--base", "0" * 40, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.strip(), "rust_ci=true")
            (r / "crates" / "a.rs").write_text("y\n", encoding="utf-8")
            git(r, "commit", "-qam", "rs")
            rc, out, _ = cli("rust-ci", "--event", "pull_request", "--base", base, "--head", "HEAD", "--root", str(r))
            self.assertEqual(out.strip(), "rust_ci=true")


class RustCIPathsCoverScripts(unittest.TestCase):
    """aibd.yml 會跑的 scripts/* 改了也要跑 Rust 兩格,不然只改刀工具的 PR 兩格必過直接回綠 = 刀沒跑(1ced7 11-09)。"""

    SCRIPT = re.compile(r"(?<![\w./-])(?:\./)?(scripts/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*-]+)*)")

    def referenced(self, text):
        return {m.rstrip("/.") for m in self.SCRIPT.findall(text)}

    def test_every_script_aibd_yml_runs_is_in_rust_ci_paths(self):
        seen, todo = set(), self.referenced(aibd_text())
        while todo:                                   # aibd.yml → 它呼叫的腳本 → 腳本再呼叫的
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            f = ROOT / p
            if f.is_file() and f.suffix in (".sh", ".py", ""):
                todo |= self.referenced(f.read_text(encoding="utf-8", errors="replace"))
        self.assertTrue(seen, "aibd.yml 一個 scripts/ 都沒抓到 = 這支測試壞了")
        for p in sorted(seen):
            with self.subTest(p=p):
                self.assertTrue(do.in_rust_ci(p), "%s 被 aibd.yml 用到,卻不在 RUST_CI_PATHS" % p)

    def test_knife_tools_are_listed(self):
        for p in ("scripts/knife.py", "scripts/knives/win-job.json"):
            with self.subTest(p=p):
                self.assertTrue(do.in_rust_ci(p))
                self.assertTrue(do.rust_ci("pull_request", [p])[0], "只改刀工具 ⇒ 真跑 Rust 兩格")

    def test_the_scan_sees_a_knife_call(self):
        # 刀:aibd.yml 換成 #535 那種寫法也要抓得到
        self.assertIn("scripts/knife.py",
                      self.referenced("k() { python3 -I scripts/knife.py crates/x.rs \"$1\" -- cargo test; }"))
        self.assertIn("scripts/check-sys-deps.sh", self.referenced("          ./scripts/check-sys-deps.sh --self-test"))


def aibd_text():
    return (ROOT / ".github" / "workflows" / "aibd.yml").read_text(encoding="utf-8")


def aibd_job(name):
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)" % re.escape(name), aibd_text())
    return m.group(1)


class AibdWorkflow(unittest.TestCase):
    def test_no_paths_filter_runs_on_every_pr_and_main_push(self):
        t = aibd_text()
        on = re.search(r"(?ms)^on:\n(.*?)(?=^\S)", t).group(1)
        self.assertNotIn("paths", on, "必過格:不能有 paths / paths-ignore(沒觸發的必過格會一直等)")
        self.assertRegex(on, r"(?m)^  pull_request:\s*$")
        self.assertRegex(on, r"(?m)^  push:\n    branches: \[main\]$")

    def test_changes_job_and_cells(self):
        c = aibd_job("changes")
        self.assertIn('git show "$BASE:$tool"', c, "判斷腳本優先用 base 的版本")
        self.assertIn("rust-ci --event", c)
        self.assertIn("|| out=rust_ci=true", c, "腳本失敗 ⇒ 真跑")
        r = aibd_job("rust")
        self.assertRegex(r, r"(?m)^    needs: changes$")
        self.assertRegex(r, r"(?m)^    if: \$\{\{ !cancelled\(\) \}\}$")
        self.assertIn("os: [macos-latest, windows-latest]", r, "格名 rust (macos-latest) / rust (windows-latest) 不改")
        self.assertIn("runs-on: ${{ needs.changes.outputs.run == 'false' && 'ubuntu-latest' || matrix.os }}", r)
        self.assertIn("RUN: ${{ needs.changes.outputs.run != 'false' }}", r)
        self.assertIn("if: needs.changes.result != 'success'", r)
        steps = re.split(r"(?m)^      - ", r.split("    steps:\n", 1)[1])[1:]
        self.assertGreater(len(steps), 8)
        gate, note, real = steps[0], steps[1], steps[2:]
        self.assertIn("exit 1", gate)
        self.assertIn("if: env.RUN != 'true'", note)
        for st in real:
            with self.subTest(step=st.splitlines()[0]):
                self.assertRegex(st, r"(?m)^        if: env\.RUN == 'true'( &&|$)", "真跑的步驟都要卡 RUN")

    def run_snippet(self, out):
        c = aibd_job("changes")
        i = c.index("run=true")
        j = c.index('| tee -a "$GITHUB_OUTPUT"', i) + len('| tee -a "$GITHUB_OUTPUT"')
        snip = "\n".join(ln.strip() for ln in c[i:j].splitlines())
        with tempfile.TemporaryDirectory() as td:
            gho = Path(td) / "gho"
            gho.write_text("", encoding="utf-8")
            p = subprocess.run(["bash", "-eo", "pipefail", "-c", snip], capture_output=True, text=True,
                               env=dict(os.environ, out=out, GITHUB_OUTPUT=str(gho)), timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            return gho.read_text(encoding="utf-8").strip()

    def test_snippet_only_a_whole_false_line_skips(self):
        for out, want in (("rust_ci=false", "run=false"), ("rust_ci=true", "run=true"), ("", "run=true"),
                          ("rust_ci=falsey", "run=true"), (" rust_ci=false", "run=true"),
                          ("usage: docs_only ...", "run=true"), ("x\nrust_ci=false\n", "run=false")):
            with self.subTest(out=out):
                self.assertEqual(self.run_snippet(out), want)


def decide_snippet():
    t = job_text("docs")
    i = t.index("docs=false; rust=false")
    j = t.index('| tee -a "$GITHUB_OUTPUT"', i) + len('| tee -a "$GITHUB_OUTPUT"')
    return "\n".join(ln.strip() for ln in t[i:j].splitlines())


class DecideSnippetRuns(unittest.TestCase):
    """ci.yml docs job 判斷輸出的那幾行,用 bash 實跑。"""

    def run_snippet(self, out):
        with tempfile.TemporaryDirectory() as td:
            gho = Path(td) / "gho"
            gho.write_text("", encoding="utf-8")
            r = subprocess.run(["bash", "-eo", "pipefail", "-c", decide_snippet()], capture_output=True, text=True,
                               env=dict(os.environ, out=out, GITHUB_OUTPUT=str(gho)), timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return dict(ln.split("=", 1) for ln in gho.read_text(encoding="utf-8").splitlines())

    def test_cases(self):
        F = {"docs_only": "false", "rust_only": "false", "light": "false"}
        cases = {
            "docs_only=false\nrust_only=true": {"docs_only": "false", "rust_only": "true", "light": "true"},
            "docs_only=true\nrust_only=false": {"docs_only": "true", "rust_only": "false", "light": "true"},
            "docs_only=true\nrust_only=true": {"docs_only": "true", "rust_only": "false", "light": "true"},
            "docs_only=false": F,                            # base 還是舊版腳本:只印一行
            "docs_only=false\nrust_only=truex": F,
            "docs_only=false\n rust_only=true": F,
            "rust_only=true\n": {"docs_only": "false", "rust_only": "true", "light": "true"},
            "": F,
            "garbage": F,
        }
        for out, want in cases.items():
            with self.subTest(out=out):
                self.assertEqual(self.run_snippet(out), want)


def job_text(name):
    t = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)" % re.escape(name), t)
    return m.group(1)


def step_script(job, marker):
    """job 裡含 marker 那一步的 run: | 內容(去掉縮排)。"""
    t = job_text(job)
    i = t.index(marker)
    j = t.index("run: |\n", i) + len("run: |\n")
    lines = []
    for ln in t[j:].splitlines():
        if ln.strip() and not ln.startswith("          "):
            break
        lines.append(ln[10:])
    return "\n".join(lines) + "\n"


FAKE_PY = """#!/bin/sh
# fake python3: `docs_only.py <list>` acts per $LIST_MODE; any other call is a test run (logged, exit $TEST_RC)
case "$1" in
  *docs_only.py)
    case "$LIST_MODE" in
      fail) echo boom >&2; exit 1 ;;
      empty) exit 0 ;;
      missing) echo "no/such/test.py" ;;
      partial) echo "$T1"; exit 1 ;;
      blank) printf ' \t \n\t\n' ;;
      *) echo "$T1"; echo "$T2" ;;
    esac ;;
  *) echo "$1" >> "$RAN"; exit "${TEST_RC:-0}" ;;
esac
"""


class ListStepsRun(unittest.TestCase):
    """bb7dc #538 High:列清單的指令失敗 / 空 / 列了不存在的檔 ⇒ 那一步紅;實跑 docs job 的兩個步驟。"""

    def run_step(self, marker, mode, test_rc=0, shell=("bash", "-e")):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fb = td / "bin"
            fb.mkdir()
            (fb / "python3").write_text(FAKE_PY, encoding="utf-8")
            (fb / "python3").chmod(0o755)
            (td / "aib" / "tools" / "release").mkdir(parents=True)
            (td / "aib" / "tools" / "release" / "docs_only.py").write_text("", encoding="utf-8")
            for n in ("t1.py", "t2.py"):
                (td / n).write_text("", encoding="utf-8")
            ran = td / "ran.log"
            env = dict(os.environ, PATH="%s:%s" % (fb, os.environ.get("PATH", "")), LIST_MODE=mode,
                       TEST_RC=str(test_rc), RAN=str(ran), T1="t1.py", T2="t2.py")
            p = subprocess.run(list(shell) + ["-c", step_script("docs", marker)], cwd=str(td), env=env,
                               capture_output=True, text=True, timeout=30)
            got = ran.read_text(encoding="utf-8").split() if ran.exists() else []
            return p.returncode, got, p.stdout + p.stderr

    def test_both_steps(self):
        for marker in ("name: tests main runs over .md files", "name: Python tests that read crates/ or Cargo.*"):
            with self.subTest(step=marker, case="ok"):
                rc, got, log = self.run_step(marker, "ok")
                self.assertEqual((rc, got), (0, ["t1.py", "t2.py"]), log)
            with self.subTest(step=marker, case="a test fails"):
                rc, got, log = self.run_step(marker, "ok", test_rc=1)
                self.assertNotEqual(rc, 0, log)
                self.assertEqual(got, ["t1.py", "t2.py"], "一支紅也要跑完其他支")
            # GitHub 的預設是 bash -e;不靠 -e 也要紅(之後有人改 shell / 加 set +e 不會變假綠)
            for shell in (("bash", "-e"), ("bash",)):
                for mode in ("fail", "empty", "missing", "partial", "blank"):
                    with self.subTest(step=marker, case=mode, shell=" ".join(shell)):
                        rc, got, log = self.run_step(marker, mode, shell=shell)
                        self.assertNotEqual(rc, 0, "清單 %s ⇒ 要紅,不能零支跑完當綠\n%s" % (mode, log))
                        self.assertEqual(got, [], log)


class Workflow(unittest.TestCase):
    def test_docs_job(self):
        t = job_text("docs")
        self.assertIn("runs-on: ubuntu-latest", t)
        self.assertIn("fetch-depth: 0", t)
        self.assertIn('git show "$BASE:$tool"', t, "判斷腳本優先用 base 的版本")
        self.assertIn("|| out=docs_only=false", t, "腳本失敗 ⇒ false")
        self.assertIn("docs=false; rust=false", t, "沒有整行 =true 就當 false(DecideSnippetRuns 實跑)")
        self.assertIn("docs_only.py check", t)
        self.assertIn("if: steps.d.outputs.light == 'true'", t)
        self.assertIn("if: steps.d.outputs.docs_only == 'true'", t)
        self.assertIn("docs_only.py rust-tests", t, "只改 Rust 時照清單直接跑讀 crates/ 的測試")
        self.assertIn("if: steps.d.outputs.rust_only == 'true'", t)
        self.assertIn("light: ${{ steps.d.outputs.light }}", t)
        self.assertIn("docs_only.py md-tests", t, "只改文件時照清單直接跑 main 掃 .md 的測試")
        self.assertIn('python3 "$t" || {', t)

    def test_required_cells_depend_on_docs_and_propagate_failure(self):
        for job in ("smoke", "fresh-machine"):
            with self.subTest(job=job):
                t = job_text(job)
                self.assertRegex(t, r"(?m)^    needs: \[tip, docs\]$")
                self.assertRegex(t, r"(?m)^    if: \$\{\{ !cancelled\(\) \}\}$", "docs 紅時也要跑到判紅那步")
                self.assertIn("if: needs.tip.result != 'success' || needs.docs.result != 'success'", t)
                self.assertIn("exit 1", t)
                runs_on = re.search(r"(?m)^    runs-on: (.*)$", t).group(1)
                run_cell = re.search(r"(?m)^      RUN_CELL: (.*)$", t).group(1)
                self.assertIn("needs.docs.outputs.light", runs_on)
                self.assertIn("needs.docs.outputs.light != 'true'", run_cell)
                self.assertNotIn("needs.docs.outputs.docs_only ==", t, "條件只看 light(docs_only 或 rust_only)")
                self.assertNotIn("needs.docs.outputs.docs_only !=", t)
                self.assertIn("docs-only or Rust-only change", t)


if __name__ == "__main__":
    unittest.main()
