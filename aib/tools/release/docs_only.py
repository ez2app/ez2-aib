# SPDX-License-Identifier: Apache-2.0
"""只改文件的 PR 只跑文件檢查(11-08 PROPOSAL-19,a63ab 總結 3/4 表態)。

由 `.github/workflows/ci.yml` 的 `docs` job 呼叫:

    python3 aib/tools/release/docs_only.py decide --event pull_request --base <sha> --head <sha>
        印 docs_only=true|false(給 $GITHUB_OUTPUT),原因印到 stderr
    python3 aib/tools/release/docs_only.py check --base <sha> --head <sha>
        只改文件時才跑:改到的 md 裡相對連結都在、沒有隱形字元、不是空檔、換行是 LF;有問題 rc 1

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
INVISIBLE = tuple(chr(c) for c in (0x200B, 0x202E, 0x2060, 0xFEFF))
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
    for n, line in enumerate(text.splitlines(), 1):
        for ch in INVISIBLE:
            if ch in line:
                bad.append("%s:%d: invisible character U+%04X" % (rel, n, ord(ch)))
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
            if not os.path.exists(dest):
                bad.append("%s:%d: broken relative link %s" % (rel, n, target))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(prog="docs_only", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--event", required=True)
    for p in (d, sub.add_parser("check")):
        p.add_argument("--base", default="")
        p.add_argument("--head", default="")
        p.add_argument("--root", default=".")
    a = ap.parse_args(argv)
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
    for b in bad:
        print("::error::" + b)
    print("docs check: %d file(s), %d problem(s)" % (len(files), len(bad)))
    return RC_BAD if bad else RC_OK


if __name__ == "__main__":
    sys.exit(main())
# probe 3 (1ced7, temporary)
