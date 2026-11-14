# SPDX-License-Identifier: Apache-2.0
"""只改文件的 PR 只跑文件檢查(11-08 PROPOSAL-19,a63ab 總結 3/4 表態);
只改 Rust 的 PR 也不跑 Python smoke 六格(11-09 Andy「只動 Rust 就跳過」,a63ab 派 1ced7):Rust 由 aibd.yml 測。

由 `.github/workflows/ci.yml` 的 `docs` job 呼叫:

    python3 aib/tools/release/docs_only.py decide --event pull_request --base <sha> --head <sha>
        印 docs_only=true|false 與 rust_only=true|false 兩行(給 $GITHUB_OUTPUT),原因印到 stderr
    python3 aib/tools/release/docs_only.py check --base <sha> --head <sha>
        只改文件時才跑:改到的 md 裡相對連結都在、不是空檔、換行是 LF;有刪除 / 改名時整份白名單跑連結檢查
    python3 aib/tools/release/docs_only.py md-tests
        列出 main 推送時會掃 .md 的測試(隱形字元等);只改文件時 docs job 逐支直接跑,不在這裡重寫
    python3 aib/tools/release/docs_only.py rust-ci --event pull_request --base <sha> --head <sha>
        給 aibd.yml 的 changes job:印 rust_ci=true|false。false = 沒動 Rust 路徑(RUST_CI_PATHS),
        rust 兩格換 ubuntu 只印一行;main 推送 / 算不出來 / 0 個檔 / 動到 .github/** 一律 true(真跑)
    python3 aib/tools/release/docs_only.py rust-tests
        只改 Rust 時 docs job 逐支直接跑的 Python 測試:讀 crates/ 或 Cargo.* 的(RUST_READ_TESTS)+ 上面那份

規則:
- **白名單**,不在名單內的都算程式。名單只放已核實沒有程式 / 測試讀的純文件:
  根目錄 docs/ 底下的 .md、SECURITY*.md、CODE_OF_CONDUCT*.md。
  不算文件:README*(fresh-machine 照它安裝、測試讀)、CHANGELOG*(發版檢查四語版號)、CONTRIBUTING*
  (tests/test_contributing.py 讀)、aib/docs/**(templates、tmux-hook-indexes 等有程式讀)、aib/skills/**、
  aib/src/**.md、.github/**、*.json。
- 只有 pull_request 才可能是 true;main 推送永遠 false(發版鏈不變)。
- 混合改動(只要有一個檔不在名單)⇒ false。
- **算不出來一律 false**(base 抓不到、diff 失敗、0 個檔):寧可多跑。
- 改名當成「刪舊 + 加新」,兩邊都要在名單內(diff 用 --no-renames)。
- rust_only:每個檔都是 Rust(根目錄 Cargo.toml / Cargo.lock、crates/**)或上面白名單內的文件,且至少一個 Rust 檔。
  main 推送 / 混合 / 算不出來 / 0 個檔 ⇒ false;**動到 .github/** 一律 false**(就算之後有人把 Rust 範圍改寬)。
  Rust 檔一定也會觸發 aibd.yml(守門測試對 aibd.yml 的 paths),所以 Rust 本身照樣有測。
- 守門測試(aib/tests/test-docs-only-ci-1ced7-1108.py)掃程式 / 測試裡寫到名單內檔名的地方;
  之後有人寫測試去讀 docs/ 底下的檔,會在那支被擋下,提醒把那個檔移出名單。

只用標準函式庫 + git。
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys

RC_OK, RC_BAD, RC_USAGE = 0, 1, 2

# 白名單(fnmatch,* 不跨目錄;docs/ 底下用前綴判斷,任意深度)
DOC_PREFIXES = ("docs/",)
DOC_ROOT_GLOBS = ("SECURITY*.md", "CODE_OF_CONDUCT*.md")
# docs/ 底下但有程式 / 測試讀的檔(不算純文件,改它要跑全套)。新增一條要寫誰讀。
DOC_EXCLUDE = (
    "docs/perf/timers.md",  # crates/aibd/src/timers.rs 的測試比對它跟 timers::ALL(ADR 0005,a63ab #510)
    "docs/aibd/INSTALL.md",  # aib/tests/test-aibd-release-1ced7-1108.py D1 檢查(先對雜湊才執行、寫明沒簽章)
    "docs/aibd/INSTALL.zh-TW.md",  # 同上(D1、D2 實跑 macOS 指令、D3)
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.1.md",  # 同上(D2 實跑 macOS 指令、D3 Windows 順序);發版時 aibd-release.yml 也讀
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.2.md",  # 同上(D2–D6);preview.2 發版時 aibd-release.yml 讀
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.3.md",  # 同上;preview.3 發版時 aibd-release.yml 讀(Andy 11-09 20:46 定稿)
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.4.md",  # 同上;preview.4 發版時 aibd-release.yml 讀(Andy 11-09 23:45 定稿)
    "docs/aibd/RELEASE-NOTES-0.2.0-preview.5.md",  # 同上;preview.5 發版時 aibd-release.yml 讀(待 Andy 點頭)
)
# main 推送時會掃倉裡 .md 的測試:只改文件時 docs job 照這份清單直接跑(043dd #504 審 High、a63ab 裁示:
# 不自己重寫那些檢查 —— 原本自己查 4 個隱形字元,U+202A 這類會 docs 綠、main 推送才紅)。
# 守門測試會找「讀 git ls-files 又看 .md」的測試,不在這份清單就紅。
MD_SCAN_TESTS = ("aib/tests/test-invisible-chars-d07f9-1021.py",)
# Rust 範圍(rust_only 用):根目錄這兩個檔 + crates/ 底下任意深度。xtask/、migrations/、clippy.toml、
# rust-toolchain.toml 雖然 aibd.yml 也看,但沒核實沒有 Python 讀 ⇒ 不算(寧可多跑)。
RUST_FILES = ("Cargo.toml", "Cargo.lock")
# aibd.yml 真跑 fmt / clippy / test 的路徑(11-09 Andy「Rust 兩格改成必過」,a63ab 派:aibd.yml 拿掉 paths 過濾,
# 每張 PR 都觸發、這裡判斷要不要真跑)。原本寫在 aibd.yml 的 on.*.paths;現在這份是唯一正本,
# prepr(aib/tools/verify/prepr.py)也讀這裡。只准兩種寫法:完整檔名、或「目錄/**」。
RUST_CI_PATHS = (
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    "clippy.toml",
    "crates/**",
    "xtask/**",
    "migrations/**",
    "scripts/check-core-purity.sh",
    "scripts/check-sys-deps.sh",
    "scripts/check-win-job-knives.sh",
    # aibd.yml 的刀經過 knife.py(#535 / #543);之後加刀規格夾(043dd 提案 2)時也要加進來 ——
    # 守門測試:aibd.yml 與它呼叫的 scripts/* 提到的 scripts/ 路徑都要在這份,不在就紅
    "scripts/knife.py",
    "scripts/knives-web.sh",
    "scripts/knives-pairing.sh",
    "scripts/knives-static.sh",
    "scripts/knives-errorcode.sh",
    "scripts/knife-build.py",
    "scripts/knives-signlint.sh",
    "scripts/knives-approve.sh",
    ".github/workflows/aibd.yml",
)
RUST_PREFIXES = ("crates/",)
NEVER_LIGHT_PREFIXES = (".github/",)
# 讀 crates/ 或 Cargo.* 的 Python 測試:只改 Rust 時 docs job 照這份直接跑(a63ab 11-09,比照 MD_SCAN_TESTS);
# 守門測試找「寫到 crates/ / Cargo.toml / Cargo.lock / ".rs"」的測試,不在這份就紅。
RUST_READ_TESTS = (
    "aib/tests/test-aibd-release-1ced7-1108.py",
    "aib/tests/test-dm-stalehead-gate-1ced7-1112.py",
    "aib/tests/test-docs-only-ci-1ced7-1108.py",
    "tests/test_aib_prepr.py",
)
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def is_doc(path):
    """單一路徑(倉庫根相對、正斜線)算不算純文件。"""
    p = path.strip()
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    if not p.endswith(".md"):
        return False
    if p in DOC_EXCLUDE:
        return False
    if any(p.startswith(pre) for pre in DOC_PREFIXES):
        return True
    return "/" not in p and any(fnmatch.fnmatchcase(p, g) for g in DOC_ROOT_GLOBS)


def is_rust(path):
    """單一路徑算不算 Rust(rust_only 用)。"""
    p = path.strip()
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    return p in RUST_FILES or any(p.startswith(pre) for pre in RUST_PREFIXES)


def rust_only(event, files):
    """(True/False, 原因):只改 Rust(+ 白名單文件)的 PR。規則同 docs_only,另外動到 .github/** 一律 false。"""
    if event != "pull_request":
        return False, "event %s is not a pull_request (main pushes always run everything)" % event
    if files is None:
        return False, "could not list the changed files; running everything"
    files = [f for f in files if f.strip()]
    if not files:
        return False, "no changed files found; running everything"
    gh = [f for f in files if any(f.strip().startswith(pre) for pre in NEVER_LIGHT_PREFIXES)]
    if gh:
        return False, "touches %s; running everything" % gh[0]
    other = [f for f in files if not is_rust(f) and not is_doc(f)]
    if other:
        return False, "%d of %d changed files are neither Rust nor docs (first: %s)" % (len(other), len(files), other[0])
    rust = [f for f in files if is_rust(f)]
    if not rust:
        return False, "no Rust file changed"
    return True, "all %d changed files are Rust (%d) or docs" % (len(files), len(rust))


def in_rust_ci(path):
    p = path.strip()
    for pat in RUST_CI_PATHS:
        if pat.endswith("/**"):
            if p.startswith(pat[:-2]):
                return True
        elif p == pat:
            return True
    return False


def rust_ci(event, files):
    """(True = 真跑 Rust 兩格, 原因)。只有「確定沒動到 Rust 路徑」才 False;拿不準一律 True。"""
    if event != "pull_request":
        return True, "event %s is not a pull_request (main pushes always run the Rust cells)" % event
    if files is None:
        return True, "could not list the changed files; running the Rust cells"
    files = [f for f in files if f.strip()]
    if not files:
        return True, "no changed files found; running the Rust cells"
    gh = [f for f in files if any(f.strip().startswith(pre) for pre in NEVER_LIGHT_PREFIXES)]
    if gh:
        return True, "touches %s; running the Rust cells" % gh[0]
    rust = [f for f in files if in_rust_ci(f)]
    if rust:
        return True, "%d changed file(s) on Rust paths (first: %s)" % (len(rust), rust[0])
    return False, "none of the %d changed files is on a Rust path" % len(files)


def rust_tests():
    out = []
    for t in RUST_READ_TESTS + MD_SCAN_TESTS:
        if t not in out:
            out.append(t)
    return out


def docs_only(event, files):
    """(True/False, 原因)。files = 改到的路徑清單;None = 讀不到。"""
    if event != "pull_request":
        return False, "event %s is not a pull_request (main pushes always run everything)" % event
    if files is None:
        return False, "could not list the changed files; running everything"
    files = [f for f in files if f.strip()]
    if not files:
        return False, "no changed files found; running everything"
    code = [f for f in files if not is_doc(f)]
    if code:
        return False, "%d of %d changed files are not docs (first: %s)" % (len(code), len(files), code[0])
    return True, "all %d changed files are docs" % len(files)


def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def changed_files(root, base, head):
    """base...head(merge base 起)改到的檔;讀不到回 None。"""
    if not base or not head:
        return None
    out = git(root, "diff", "--no-renames", "--name-only", "%s...%s" % (base, head))
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def check_file(root, rel):
    """一個改到的 md:回問題清單(空 = 沒問題)。"""
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return []           # 刪掉的檔不檢查
    with open(path, "rb") as fh:
        raw = fh.read()
    bad = []
    if not raw.strip():
        bad.append("%s: empty" % rel)
        return bad
    if b"\r" in raw:
        bad.append("%s: CR line endings (use LF)" % rel)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return bad + ["%s: not UTF-8 (%s)" % (rel, e)]
    bad += ["%s:%d: broken relative link %s" % (rel, n, target)
            for n, target, dest in links(root, rel, text) if not os.path.exists(dest)]
    return bad


def links(root, rel, text):
    """(行號, 原字面, 解析後的絕對路徑):md 裡的相對連結(略過 code fence、網址、頁內錨點)。"""
    path = os.path.join(root, rel)
    out = []
    in_fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in LINK.findall(line):
            t = target.strip("<>")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) or t.startswith("#"):
                continue        # http: / mailto: / 頁內錨點
            t = t.split("#", 1)[0].split("?", 1)[0]
            if not t:
                continue
            dest = os.path.normpath(os.path.join(root, t.lstrip("/")) if t.startswith("/")
                                    else os.path.join(os.path.dirname(path), t))
            out.append((n, target, dest))
    return out


def after_delete(root, deleted):
    """有刪除 / 改名時(043dd #504 審 Medium、a63ab 裁示「整份跑」):
    1. 白名單內的文件**整份**跑連結檢查(不只改到的那幾個);
    2. 其他 .md(README 等)連到這次刪掉的路徑也報(那些檔不在白名單,整份查會把本來就斷的算進來)。"""
    if not deleted:
        return []
    out = git(root, "ls-files", "-z", "--", "*.md")
    if out is None:
        return ["cannot list the tracked .md files to re-check links after a delete"]
    gone = {os.path.normpath(os.path.join(root, d)) for d in deleted}
    bad = []
    for rel in [f for f in out.split("\0") if f]:
        try:
            text = open(os.path.join(root, rel), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        whole = is_doc(rel)
        bad += ["%s:%d: %s %s" % (rel, n, "broken relative link" if whole else "links to deleted file", target)
                for n, target, dest in links(root, rel, text)
                if (whole and not os.path.exists(dest)) or (not whole and dest in gone)]
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(prog="docs_only", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("md-tests")
    sub.add_parser("rust-tests")
    rc_ = sub.add_parser("rust-ci")
    rc_.add_argument("--event", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--event", required=True)
    for p in (d, rc_, sub.add_parser("check")):
        p.add_argument("--base", default="")
        p.add_argument("--head", default="")
        p.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    if a.cmd == "md-tests":
        print("\n".join(MD_SCAN_TESTS))
        return RC_OK
    if a.cmd == "rust-tests":
        print("\n".join(rust_tests()))
        return RC_OK
    if a.cmd == "rust-ci":
        files = changed_files(a.root, a.base, a.head) if a.event == "pull_request" else []
        run, why = rust_ci(a.event, files)
        print("rust_ci=%s" % ("true" if run else "false"))
        print("rust_ci: " + why, file=sys.stderr)
        return RC_OK
    if a.cmd == "decide":
        files = changed_files(a.root, a.base, a.head) if a.event == "pull_request" else []
        ok, why = docs_only(a.event, files)
        rok, rwhy = (False, "docs-only already") if ok else rust_only(a.event, files)
        print("docs_only=%s" % ("true" if ok else "false"))
        print("rust_only=%s" % ("true" if rok else "false"))
        print("docs_only: " + why, file=sys.stderr)
        print("rust_only: " + rwhy, file=sys.stderr)
        return RC_OK
    files = changed_files(a.root, a.base, a.head)
    ok, why = docs_only("pull_request", files)
    if not ok:
        rok, rwhy = rust_only("pull_request", files)
        if not rok:
            print("::error::docs check asked for, but this change is neither docs-only nor Rust-only: %s / %s"
                  % (why, rwhy))
            return RC_BAD
    bad = []
    for rel in files:
        if is_doc(rel):
            bad += check_file(a.root, rel)
    for b in after_delete(a.root, [rel for rel in files if is_doc(rel) and not os.path.exists(os.path.join(a.root, rel))]):
        if b not in bad:
            bad.append(b)
    for b in bad:
        print("::error::" + b)
    print("docs check: %d file(s), %d problem(s)" % (len(files), len(bad)))
    return RC_BAD if bad else RC_OK


if __name__ == "__main__":
    sys.exit(main())
