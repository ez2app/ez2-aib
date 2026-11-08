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
R7 先 draft → verify 全綠 → 才 publish(PATCH draft=false、make_latest=false)並用 API 讀回 draft=false prerelease=true、不是 latest
R8 draft 一律用 release id + API 找(建立後依 tag_name + draft 濾出恰一筆 id 傳下去;verify 用 asset API 下載、publish 用 id PATCH / 讀回),
   不靠 gh 用 tag 找 draft 的後備查找(a63ab 11-08 22:00:那條路沒實測)
   (11-08 三家深審 SUMMARY-deep-launch 第 1 點:verify 紅永不公開)
R9 build 與 verify 都跑 API 冒煙(scripts/aibd-api-smoke.py)與 sessions 冒煙(scripts/aibd-sessions-smoke.py,11-09 起 --require(定案 #19);
   verify 的冒煙在沒權杖的執行步、用 checkout 拿腳本,執行檔仍來自 draft)(a63ab 11-09 PLAN-preview2)
D1 安裝說明(英 / 繁中):先對 SHA256SUMS 才執行、寫明沒簽章、標草稿(對客字要 Andy 看)
D2 macOS 指令實跑(假 curl、暫存 HOME):sha 對 ⇒ 裝好、跑兩次;sha 不符 / SHA256SUMS 沒這行 / 下載失敗 / 下載失敗但留著上次的舊檔 ⇒ 沒裝、沒執行
   (bb7dc 11-08 High:舊稿分行寫,sha 失敗仍 chmod / mv / 執行)
D4 三份 macOS 區塊沒有 `#` 註解:macOS Terminal 是互動式 zsh,interactivecomments 預設關 ⇒ `V=… # 註解` 會把 V 變空(11-08 1ced7 實測)
D5 實跑:互動式 zsh(-f,明確 unsetopt interactivecomments)貼上三份 macOS 區塊的設定行 ⇒ V / F 不得為空、不准 command not found(a63ab 11-08 23:20)
D3 Windows 指令順序:ErrorActionPreference Stop 在最前;找不到行 ⇒ 刪檔停;比對過才 Unblock-File / 執行
"""
import hashlib
import os
import re
import shlex
import shutil
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
        self.assertIn("already exist", d)
        self.assertRegex(d, r'gh release create "\$TAG" --draft --prerelease --latest=false --verify-tag')
        self.assertIn("RELEASE-NOTES-${VERSION}.md", d)
        self.assertIn('[ "$n" -eq "$EXPECTED_FILES" ]', d)

    def test_r6_verify_downloads_and_checks(self):
        v = job("verify")
        self.assertIn("needs: [meta, draft]", v)
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
        self.assertIn('-X PATCH "repos/$GH_REPO/releases/$RID" -F draft=false -F prerelease=true -f make_latest=false', p)
        self.assertIn('[ "$got" = "draft=false prerelease=true tag=$TAG" ]', p)
        self.assertIn('releases/latest', p)
        self.assertNotIn('|| true', p, "讀不到 latest 不准當成確認")
        self.assertIn('*"HTTP 404"*) latest=""', p)
        self.assertNotIn("gh release create", p)
        self.assertNotIn("PATCH", job("draft") + job("verify"))
        self.assertIn("needs: [meta, draft, verify]", p, "verify 全綠才公開")
        order = [T.index("\n  %s:\n" % n) for n in ("meta", "build", "draft", "verify", "publish")]
        self.assertEqual(order, sorted(order))


    def test_r8_draft_found_by_release_id_not_by_tag(self):
        for bad in ("gh release view", "gh release download", "gh release edit", "releases/tags/"):
            self.assertNotIn(bad, T, "draft 不准靠 tag 查找:%s" % bad)
        d = job("draft")
        self.assertIn("release_id: ${{ steps.mk.outputs.release_id }}", d)
        self.assertIn('echo "release_id=$ids" >> "$GITHUB_OUTPUT"', d)
        self.assertIn("rel_ids true", d)
        self.assertIn("expected exactly one draft", d)
        for n in ("verify", "publish"):
            self.assertIn("RID: ${{ needs.draft.outputs.release_id }}", job(n))
            self.assertIn('[ -n "$RID" ]', job(n))
        v = job("verify")
        self.assertIn('repos/$GH_REPO/releases/$RID/assets', v)
        self.assertIn('-H "Accept: application/octet-stream" "repos/$GH_REPO/releases/assets/$aid"', v)


    def test_r9_api_and_sessions_smoke_in_build_and_verify(self):
        b, v = job("build"), job("verify")
        self.assertIn('-I scripts/aibd-api-smoke.py "dist/${NAME}"', b)
        self.assertIn('-I scripts/aibd-sessions-smoke.py --require "dist/${NAME}"', b)  # 定案 #19:API 合了 ⇒ 必過
        self.assertLess(b.index("selfcheck rc 0"), b.index("aibd-api-smoke.py"))
        self.assertLess(b.index("aibd-sessions-smoke.py"), b.index("upload-artifact"), "冒煙沒過不准上傳")
        dl, run = v.split("- name: run what was uploaded")
        self.assertIn('-I ../scripts/aibd-api-smoke.py "./$NAME"', run, "冒煙跑的是從 draft 下載的那份")
        self.assertIn('-I ../scripts/aibd-sessions-smoke.py --require "./$NAME"', run)  # 定案 #19
        self.assertNotIn("GH_TOKEN", run)
        self.assertLess(v.index("uses: actions/checkout@v4"), v.index("gh api"), "checkout 會清工作區:要在下載之前")
        self.assertIn('- "scripts/aibd-*-smoke.py"', T, "改冒煙腳本的 PR 也要跑這支 workflow")
        for script in ("aibd-api-smoke.py", "aibd-sessions-smoke.py"):
            with self.subTest(script=script):
                self.assertTrue((ROOT / "scripts" / script).is_file(), "workflow 叫的腳本要在倉裡:%s" % script)


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



def block(f, lang):
    t = (ROOT / "docs" / "aibd" / f).read_text(encoding="utf-8")
    return re.search(r"```%s\n(.*?)```" % lang, t, re.S).group(1)


FAKE_CURL = """#!/bin/sh
# fake curl: `curl -fLO URL...` copies basename(URL) from $SRC; missing file => rc 22 like curl -f
for a in "$@"; do case "$a" in -*) ;; *) n=${a##*/}; [ -f "$SRC/$n" ] || exit 22; cp "$SRC/$n" "./$n" ;; esac; done
"""


@unittest.skipUnless(shutil.which("shasum") and shutil.which("bash"), "needs shasum + bash")
class InstallRuns(unittest.TestCase):
    NAME = "aibd-0.2.0-preview.1-aarch64-apple-darwin"

    def run_block(self, f, sums, stale=False):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home, src, fbin = d / "home", d / "src", d / "fbin"
            for p in (home / "Downloads", src, fbin):
                p.mkdir(parents=True)
            mark = d / "ran.log"
            if stale:      # last attempt left an old binary + old SHA256SUMS in ~/Downloads and an old install
                old = "#!/bin/sh\necho \"stale $*\" >> %s\n" % shlex.quote(str(mark))
                (home / "Downloads" / self.NAME).write_text(old, encoding="utf-8")
                (home / "Downloads" / "SHA256SUMS").write_text(
                    "%s  %s\n" % (hashlib.sha256(old.encode()).hexdigest(), self.NAME), encoding="utf-8")
                (home / ".local" / "bin").mkdir(parents=True)
                (home / ".local" / "bin" / "aibd").write_text("old install\n", encoding="utf-8")
            exe = src / self.NAME
            if sums != "no-binary":
                exe.write_text("#!/bin/sh\necho \"$*\" >> %s\n" % shlex.quote(str(mark)), encoding="utf-8")
            good = hashlib.sha256(exe.read_bytes()).hexdigest() if exe.exists() else "0" * 64
            lines = {"ok": "%s  %s\n" % (good, self.NAME), "no-binary": "%s  %s\n" % (good, self.NAME),
                     "bad": "%s  %s\n" % ("f" * 64, self.NAME), "missing": "%s  aibd-other\n" % good}[sums]
            (src / "SHA256SUMS").write_text(lines, encoding="utf-8")
            (fbin / "curl").write_text(FAKE_CURL, encoding="utf-8")
            (fbin / "curl").chmod(0o755)
            env = dict(os.environ, HOME=str(home), SRC=str(src), PATH="%s:%s" % (fbin, os.environ.get("PATH", "")))
            here = set(os.listdir("."))
            # run where a user pastes it (~/Downloads holds the stale files); never in the repo / test cwd
            r = subprocess.run(["bash", "-c", block(f, "sh")], env=env, cwd=str(home / "Downloads"),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(set(os.listdir(".")) - here, set(), "安裝段的下載檔不准落在測試的 cwd")
            inst = home / ".local" / "bin" / "aibd"
            installed = inst.exists() and inst.read_text(encoding="utf-8") != "old install\n"
            ran = mark.read_text(encoding="utf-8").split("\n")[:-1] if mark.exists() else []
            return r.returncode, installed, ran, r.stdout + r.stderr

    def test_d2_only_a_matching_checksum_installs_and_runs(self):
        for f in ("INSTALL.md", "INSTALL.zh-TW.md"):
            with self.subTest(f=f, case="ok"):
                rc, inst, ran, log = self.run_block(f, "ok")
                self.assertEqual((rc, inst, ran), (0, True, ["--version", "selfcheck"]), log)
            for case in ("bad", "missing", "no-binary"):
                with self.subTest(f=f, case=case):
                    rc, inst, ran, log = self.run_block(f, case)
                    self.assertNotEqual(rc, 0, log)
                    self.assertFalse(inst, "%s: 不准裝\n%s" % (case, log))
                    self.assertEqual(ran, [], "%s: 不准執行\n%s" % (case, log))
            with self.subTest(f=f, case="stale old files, download fails"):
                rc, inst, ran, log = self.run_block(f, "no-binary", stale=True)
                self.assertNotEqual(rc, 0, log)
                self.assertFalse(inst, "舊檔不准被搬去裝\n" + log)
                self.assertEqual(ran, [], "舊檔不准被執行\n" + log)

    def test_d2_release_notes_block_too(self):
        rc, inst, ran, log = self.run_block("RELEASE-NOTES-0.2.0-preview.1.md", "bad")
        self.assertNotEqual(rc, 0, log)
        self.assertFalse(inst, log)
        self.assertEqual(ran, [], log)



class InstallZshPaste(unittest.TestCase):
    def test_d4_no_comments_in_sh_blocks(self):
        for f in ("INSTALL.md", "INSTALL.zh-TW.md", "RELEASE-NOTES-0.2.0-preview.1.md"):
            with self.subTest(f=f):
                for n, ln in enumerate(block(f, "sh").splitlines(), 1):
                    code = re.sub(r'"[^"]*"', '""', ln)       # 引號裡的 # 不算(例:網址片段)
                    self.assertNotIn("#", code, "%s 第 %d 行有 # 註解:互動式 zsh 貼上會壞(V 變空)" % (f, n))

    @unittest.skipUnless(shutil.which("zsh"), "needs zsh (macOS Terminal's default shell)")
    def test_d5_interactive_zsh_paste_sets_v_and_f(self):
        for f in ("INSTALL.md", "INSTALL.zh-TW.md", "RELEASE-NOTES-0.2.0-preview.1.md"):
            with self.subTest(f=f):
                lines = block(f, "sh").splitlines()
                setup = []                                   # 開頭的設定行(NAME=...),到第一個指令為止
                for ln in lines:
                    if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", ln):
                        break
                    setup.append(ln)
                self.assertTrue(any(ln.startswith("V=") for ln in setup) and any(ln.startswith("F=") for ln in setup), lines)
                paste = "unsetopt interactivecomments\nPS1=''\n" + "\n".join(setup) + '\nprint -r -- "@@V=[$V] F=[$F]"\nexit\n'
                with tempfile.TemporaryDirectory() as home:
                    r = subprocess.run(["zsh", "-f", "-i"], input=paste, capture_output=True, text=True, timeout=30,
                                       env=dict(os.environ, HOME=home, ZDOTDIR=home))
                out = r.stdout + r.stderr
                got = re.search(r"@@V=\[(.*?)\] F=\[(.*?)\]", out)
                self.assertIsNotNone(got, out)
                self.assertEqual(got.group(1), "0.2.0-preview.1", "V 變空 = 互動 zsh 把 # 當指令\n" + out)
                self.assertEqual(got.group(2), "aibd-0.2.0-preview.1-aarch64-apple-darwin", out)
                self.assertNotIn("command not found", out)

class InstallWindowsOrder(unittest.TestCase):
    def test_d3(self):
        for f in ("INSTALL.md", "INSTALL.zh-TW.md", "RELEASE-NOTES-0.2.0-preview.1.md"):
            with self.subTest(f=f):
                b = block(f, "powershell")
                lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
                self.assertEqual(lines[0], '$ErrorActionPreference = "Stop"')
                i_none = b.index("if (-not $line) { Remove-Item")
                i_cmp = b.index("if (-not $want -or $want -ne $got) { Remove-Item")
                i_unblock = b.index("Unblock-File")
                i_run = b.index("--version")
                self.assertLess(b.index("Get-FileHash"), i_cmp)
                self.assertLess(i_none, i_cmp)
                self.assertLess(i_cmp, i_unblock)
                self.assertLess(i_unblock, i_run)
                self.assertEqual(b.count("throw"), 2)


if __name__ == "__main__":
    unittest.main()
