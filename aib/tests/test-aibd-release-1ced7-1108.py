#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""11-08 PLAN-rust-preview-today C1/C2:aibd-release.yml(Rust 預覽版發版)+ 安裝說明。

R1 只由 tag aibd-v* 發版;PR 只在改這支 / docs/aibd 時跑 build + smoke(自己當探針),沒有 pull_request_target
R2 權限:頂層 contents: read;只有 draft / verify / publish 有 contents: write;每個 checkout persist-credentials: false;不碰 PyPI / release.yml
R3 兩目標(macOS Apple Silicon、Windows x64;Intel 先不發)原生 build + smoke(--locked、release、-p aibd;
   --version 精確等於「aibd <版號> <os>-<arch>」、selfcheck rc 0,用 if 判,不靠 -e 中止;uname -m 要是該格的 CPU)
R4 draft / verify / publish 只在 meta 說 publish=true;draft 拒絕碰已存在的 release、建 draft + pre-release + 不當 latest、發版說明檔不在就停
R5 跑 meta 那段 bash(假 Cargo.toml):tag 版號對得上 + 嚴格 X.Y.Z-preview.N ⇒ publish=true;對不上 / 其他 pre-release 寫法 ⇒ rc 1;
   不是 tag ⇒ publish=false
R6 verify 從 draft 下載、對 SHA256SUMS、再跑一次(--version 精確、uname -m);權杖只給下載那步,執行那步沒有
R7 先 draft → verify 全綠 → 才 publish(edit --draft=false --latest=false)並用 API 讀回 draft=false prerelease=true、不是 latest
   (11-08 三家深審 SUMMARY-deep-launch 第 1 點:verify 紅永不公開)
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
        writes = [n for n in ("meta", "build", "draft", "verify", "publish") if "contents: write" in job(n)]
        self.assertEqual(writes, ["draft", "verify", "publish"])
        cos = re.findall(r"uses: actions/checkout@v4\n(?:\s+with:\n(?:\s{10}.*\n)*)?", T)
        self.assertGreaterEqual(len(cos), 3)
        for c in cos:
            self.assertIn("persist-credentials: false", c)
        for bad in ("pypi", "twine", "self-hosted"):
            self.assertNotIn(bad, T.replace("aibd-release.yml", ""), bad)
        self.assertNotIn("aibd-release", (WF / "release.yml").read_text(encoding="utf-8"))

    def test_r3_build_and_smoke(self):
        b = job("build")
        targets = re.findall(r"(?m)^          - target: (\S+)$", b)
        self.assertEqual(targets, ["aarch64-apple-darwin", "x86_64-pc-windows-msvc"],
                         "11-08 a63ab:macOS Intel 沒有機器能驗 ⇒ 不發(之後 lipo universal 補)")
        self.assertNotIn("macos-15-intel", T)
        self.assertIn('EXPECTED_FILES: "%d"' % len(targets), job("draft"), "draft 檢查的檔數要跟矩陣一致")
        vt = re.findall(r"target: ([\w-]+), exe", job("verify"))
        self.assertEqual(vt, targets, "verify 矩陣要跟 build 一致")
        self.assertIn('cargo build --release --locked -p aibd --target "$TARGET"', b)
        self.assertEqual(re.findall(r"(?m)^            plat: (\S+)", b), ["macos-aarch64", "windows-x86_64"])
        self.assertEqual(re.findall(r"(?m)^            arch: (\S+)", b), ["arm64", "x86_64"])
        self.assertIn('[ "$out" = "aibd $VERSION $PLAT" ]', b)
        self.assertNotIn('*"$VERSION"*', T, "--version 不准用子字串比對")
        self.assertIn('[ "$m" = "$ARCH" ]', b)
        self.assertRegex(b, r'if "\$bin" selfcheck; then')
        self.assertIn("if-no-files-found: error", b)

    def test_r4_publish_guards(self):
        for n in ("draft", "verify", "publish"):
            self.assertIn("if: needs.meta.outputs.publish == 'true'", job(n))
        d = job("draft")
        self.assertIn('gh release view "$TAG"', d)
        self.assertIn("already exists", d)
        self.assertRegex(d, r'gh release create "\$TAG" --draft --prerelease --latest=false --verify-tag')
        self.assertIn("RELEASE-NOTES-${VERSION}.md", d)
        self.assertIn('[ "$n" -eq "$EXPECTED_FILES" ]', d)

    def test_r6_verify_downloads_and_checks(self):
        v = job("verify")
        self.assertIn("needs: [meta, draft]", v)
        self.assertIn('gh release download "$TAG"', v)
        self.assertIn("-c mine.sha", v)
        self.assertRegex(v, r'if "\./\$NAME" selfcheck; then')
        self.assertIn('[ "$out" = "aibd $VERSION $PLAT" ]', v)
        self.assertIn('[ "$m" = "$ARCH" ]', v)
        dl, run = v.split("- name: run what was uploaded")
        self.assertIn("GH_TOKEN", dl)
        self.assertNotIn("GH_TOKEN", run, "跑剛下載的執行檔時不給它權杖")
        self.assertNotIn("GH_TOKEN", v[:v.index("steps:")], "權杖不放 job 層 env")
        vt = re.findall(r"target: ([\w-]+), exe: \S+, plat: ([\w-]+), arch: (\w+)", v)
        self.assertEqual(vt, [("aarch64-apple-darwin", "macos-aarch64", "arm64"),
                              ("x86_64-pc-windows-msvc", "windows-x86_64", "x86_64")])

    def test_r7_draft_then_verify_then_publish(self):
        p = job("publish")
        self.assertIn("needs: [meta, verify]", p, "verify 全綠才公開")
        self.assertIn('gh release edit "$TAG" --draft=false --prerelease --latest=false', p)
        self.assertIn('[ "$got" = "draft=false prerelease=true" ]', p)
        self.assertIn('releases/latest', p)
        self.assertNotIn("gh release create", p)
        self.assertNotIn("gh release edit", job("draft") + job("verify"))
        order = [T.index("\n  %s:\n" % n) for n in ("meta", "build", "draft", "verify", "publish")]
        self.assertEqual(order, sorted(order))


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
        for bad in ("0.2.0-rc.1", "0.2.0-preview", "0.2.0-preview.1-x", "0.2-preview.1", "0.2.0-preview.a"):
            with self.subTest(bad=bad):
                rc, got, log = self.run_meta(bad, "tag", "aibd-v" + bad)
                self.assertEqual(rc, 1, "只收 X.Y.Z-preview.N:%s" % bad)
                self.assertNotIn("publish=true", got)
        rc, got, log = self.run_meta("1.12.30-preview.12", "tag", "aibd-v1.12.30-preview.12")
        self.assertEqual(rc, 0, log)
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
