#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""11-08 PLAN-rust-preview-today C1/C2:aibd-release.yml(Rust 預覽版發版)+ 安裝說明。

R1 只由 tag aibd-v* 發版;PR 只在改這支 / docs/aibd 時跑 build + smoke(自己當探針),沒有 pull_request_target
R2 權限:頂層 contents: read;只有 publish 有 contents: write;每個 checkout persist-credentials: false;不碰 PyPI / release.yml
R3 三目標原生 build + smoke(--locked、release、-p aibd;--version 要含版號、selfcheck rc 0,用 if 判,不靠 -e 中止)
R4 publish / verify 只在 meta 說 publish=true;publish 拒絕蓋掉已存在的 release、只建 pre-release、發版說明檔不在就停
R5 跑 meta 那段 bash(假 Cargo.toml):tag 版號對得上 + 有 pre-release 段 ⇒ publish=true;對不上 / 沒有 -xxx ⇒ rc 1;
   不是 tag ⇒ publish=false
R6 verify 從 release 頁重下載、對 SHA256SUMS、再跑一次
D1 安裝說明(英 / 繁中):先對 SHA256SUMS 才執行、寫明沒簽章、標草稿(對客字要 Andy 看)
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
T = (WF / "aibd-release.yml").read_text(encoding="utf-8")


def job(name):
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)" % re.escape(name), T)
    return m.group(1)


def step_script(job_name, step_id):
    blk = job(job_name)
    i = blk.index("id: %s" % step_id)
    lines = blk[i:].splitlines()
    k = next(n for n, ln in enumerate(lines) if ln.strip() == "run: |")
    ind = len(lines[k + 1]) - len(lines[k + 1].lstrip())
    out = []
    for ln in lines[k + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < ind:
            break
        out.append(ln[ind:])
    return "\n".join(out) + "\n"


class Static(unittest.TestCase):
    def test_r1_triggers(self):
        self.assertRegex(T, r'(?m)^    tags: \["aibd-v\*"\]$')
        self.assertNotIn("pull_request_target", T)
        self.assertNotIn("workflow_dispatch", T, "只認 tag,不准手按發版")
        self.assertIn('- ".github/workflows/aibd-release.yml"', T)

    def test_r2_permissions_and_scope(self):
        head = T[:T.index("\njobs:")]
        self.assertRegex(head, r"(?m)^permissions:\n  contents: read$")
        writes = [n for n in ("meta", "build", "publish", "verify") if "contents: write" in job(n)]
        self.assertEqual(writes, ["publish"])
        cos = re.findall(r"uses: actions/checkout@v4\n(?:\s+with:\n(?:\s{10}.*\n)*)?", T)
        self.assertGreaterEqual(len(cos), 3)
        for c in cos:
            self.assertIn("persist-credentials: false", c)
        for bad in ("pypi", "twine", "self-hosted"):
            self.assertNotIn(bad, T.replace("aibd-release.yml", ""), bad)
        self.assertNotIn("aibd-release", (WF / "release.yml").read_text(encoding="utf-8"))

    def test_r3_build_and_smoke(self):
        b = job("build")
        for tgt in ("aarch64-apple-darwin", "x86_64-apple-darwin", "x86_64-pc-windows-msvc"):
            self.assertIn("target: %s" % tgt, b)
        self.assertIn('cargo build --release --locked -p aibd --target "$TARGET"', b)
        self.assertIn('case "$out" in *"$VERSION"*)', b)
        self.assertRegex(b, r'if "\$bin" selfcheck; then')
        self.assertIn("if-no-files-found: error", b)

    def test_r4_publish_guards(self):
        for n in ("publish", "verify"):
            self.assertIn("if: needs.meta.outputs.publish == 'true'", job(n))
        p = job("publish")
        self.assertIn('gh release view "$TAG"', p)
        self.assertIn("already exists", p)
        self.assertIn("--prerelease", p)
        self.assertIn("--verify-tag", p)
        self.assertIn("RELEASE-NOTES-${VERSION}.md", p)
        self.assertIn('[ "$n" -eq 3 ]', p)

    def test_r6_verify_downloads_and_checks(self):
        v = job("verify")
        self.assertIn('gh release download "$TAG"', v)
        self.assertIn("-c mine.sha", v)
        self.assertRegex(v, r'if "\./\$NAME" selfcheck; then')


@unittest.skipUnless(sys.version_info >= (3, 11), "meta uses tomllib (Python 3.11+, as on ubuntu-latest)")
class Meta(unittest.TestCase):
    def run_meta(self, version, ref_type, ref_name):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "Cargo.toml").write_text('[workspace.package]\nversion = "%s"\n' % version, encoding="utf-8")
            out = Path(d, "out")
            env = dict(os.environ, REF_TYPE=ref_type, REF_NAME=ref_name, GITHUB_OUTPUT=str(out))
            r = subprocess.run(["bash", "-e", "-c", step_script("meta", "v")], cwd=d, env=env,
                               capture_output=True, text=True, timeout=30)
            got = out.read_text(encoding="utf-8") if out.exists() else ""
        return r.returncode, got, r.stdout + r.stderr

    def test_r5(self):
        rc, got, log = self.run_meta("0.2.0-preview.1", "tag", "aibd-v0.2.0-preview.1")
        self.assertEqual(rc, 0, log)
        self.assertIn("publish=true", got)
        self.assertIn("version=0.2.0-preview.1", got)
        rc, got, log = self.run_meta("0.2.0-preview.1", "tag", "aibd-v0.2.0-preview.2")
        self.assertEqual(rc, 1, log)
        self.assertNotIn("publish=true", got)
        rc, got, log = self.run_meta("0.2.0", "tag", "aibd-v0.2.0")
        self.assertEqual(rc, 1, "正式版號不准走這支(只發 pre-release)")
        rc, got, log = self.run_meta("0.2.0-preview.1", "branch", "feat/x")
        self.assertEqual(rc, 0, log)
        self.assertIn("publish=false", got)


class Docs(unittest.TestCase):
    def test_d1_install_notes(self):
        for f in ("INSTALL.md", "INSTALL.zh-TW.md"):
            with self.subTest(f=f):
                t = (ROOT / "docs" / "aibd" / f).read_text(encoding="utf-8")
                self.assertIn("SHA256SUMS", t)
                self.assertIn("shasum -a 256 -c", t)
                self.assertIn("Get-FileHash", t)
                self.assertTrue("DRAFT" in t or "草稿" in t, "對客字:Andy 看過才公開")
                self.assertTrue("not signed" in t or "沒有簽章" in t)
                i_check, i_run = t.index("shasum -a 256 -c"), t.index("--version")
                self.assertLess(i_check, i_run, "先對雜湊才執行")


if __name__ == "__main__":
    unittest.main()
