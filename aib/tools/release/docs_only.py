# SPDX-License-Identifier: Apache-2.0
"""只改文件的 PR 只跑文件檢查(11-08 PROPOSAL-19,a63ab 總結 3/4 表態)。

由 `.github/workflows/ci.yml` 的 `docs` job 呼叫:

    python3 aib/tools/release/docs_only.py decide --event pull_request --base <sha> --head <sha>
        印 docs_only=true|false(給 $GITHUB_OUTPUT),原因印到 stderr
    python3 aib/tools/release/docs_only.py check --base <sha> --head <sha>
        只改文件時才跑:改到的 md 裡相對連結都在、不是空檔、換行是 LF;有刪除 / 改名時整份白名單跑連結檢查
    python3 aib/tools/release/docs_only.py md-tests
        列出 main 推送時會掃 .md 的測試(隱形字元等);只改文件時 docs job 逐支直接跑,不在這裡重寫

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
# main 推送時會掃倉裡 .md 的測試:只改文件時 docs job 照這份清單直接跑(043dd #504 審 High、a63ab 裁示:
# 不自己重寫那些檢查 —— 原本自己查 4 個隱形字元,U+202A 這類會 docs 綠、main 推送才紅)。
# 守門測試會找「讀 git ls-files 又看 .md」的測試,不在這份清單就紅。
MD_SCAN_TESTS = ("aib/tests/test-invisible-chars-d07f9-1021.py",)
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def is_doc(path):
    """單一路徑(倉庫根相對、正斜線)算不算純文件。"""
    p = path.strip()
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    if not p.endswith(".md"):
        return False
    if any(p.startswith(pre) for pre in DOC_PREFIXES):
        return True
    return "/" not in p and any(fnmatch.fnmatchcase(p, g) for g in DOC_ROOT_GLOBS)


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
    d = sub.add_parser("decide")
    d.add_argument("--event", required=True)
    for p in (d, sub.add_parser("check")):
        p.add_argument("--base", default="")
        p.add_argument("--head", default="")
        p.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    if a.cmd == "md-tests":
        print("\n".join(MD_SCAN_TESTS))
        return RC_OK
    if a.cmd == "decide":
        files = changed_files(a.root, a.base, a.head) if a.event == "pull_request" else []
        ok, why = docs_only(a.event, files)
        print("docs_only=%s" % ("true" if ok else "false"))
        print("docs_only: " + why, file=sys.stderr)
        return RC_OK
    files = changed_files(a.root, a.base, a.head)
    ok, why = docs_only("pull_request", files)
    if not ok:
        print("::error::docs check asked for, but this change is not docs-only: " + why)
        return RC_BAD
    bad = []
    for rel in files:
        bad += check_file(a.root, rel)
    for b in after_delete(a.root, [rel for rel in files if not os.path.exists(os.path.join(a.root, rel))]):
        if b not in bad:
            bad.append(b)
    for b in bad:
        print("::error::" + b)
    print("docs check: %d file(s), %d problem(s)" % (len(files), len(bad)))
    return RC_BAD if bad else RC_OK


if __name__ == "__main__":
    sys.exit(main())
