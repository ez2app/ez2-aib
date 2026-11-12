#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pre-push knife: block a push that adds any word from a private hide-words list.

This repo is public. Some words (who runs our hosts, where they stand) must
never land in it, and the list of those words is itself private, so it lives
outside the repo and CI cannot see it. This check therefore runs only on our
own machines, from the pre-push hook (scripts/hooks/pre-push).

Files (one entry per line; blank lines and lines starting with '#' skipped):
    $EZ2_HIDE_WORDS        default ~/.ez2-aib/private/hide-words.txt
    $EZ2_HIDE_WORDS_ALLOW  default ~/.ez2-aib/private/hide-words-allow.txt
Both must be mode 600 or tighter (no group / other bits) and owned by the
user running the check; otherwise the check refuses (rc 2) and prints nothing
from them. No list file: prints one line and passes (rc 0).

Matching (E6 plan section 7, L2):
    - an entry shorter than 4 characters is ignored;
    - case-insensitive (str.casefold on both sides);
    - whole word: the characters right before and right after the hit must not
      be an ASCII letter or digit. So for the entry `vendor-x`:
          `vendor-x`, `Vendor-X`, `vendor-x.example`, `vendor-x_api`,
          `api-vendor-x`, `用vendor-x主機`        are hits;
          `vendor-xyz`, `myvendor-x`, `vendor-x2` are not.
      `_` and `-` count as boundaries (unlike regex \\b, which treats `_` as a
      word character), so an entry inside an identifier such as
      `VENDOR_X_TOKEN` is still caught; non-ASCII letters count as
      boundaries, so an entry written in CJK is caught inside a CJK sentence;
    - a hit that lies wholly inside an allow-list phrase on the same line
      (case-insensitive) does not count.

What is read: every commit the push would add to the remote (each commit
against its parent, so a word added and removed again in a later commit is
still caught, since it stays in history), the added lines of each, the file
paths each touches, and each commit message. A merge commit is read only for
lines that are in none of its parents (its parents are read on their own).
Binary files are not read.

Output never shows an entry or the line that hit, because it may be pasted
into a dm or a PR. A hit prints only `<path>:<line> 命中第 N 個詞` where N is
the entry's line number in the list file; a path that itself contains an
entry is not printed either.

    python3 -I scripts/check-hide-words.py --pre-push <remote> [<url>]  (refs on stdin, as git gives them)
    python3 -I scripts/check-hide-words.py --range <base>..<head>

rc 0 clean or skipped, 1 hit (push blocked), 2 refused / could not check.
"""
import os
import stat
import subprocess
import sys

ZERO = "0" * 40
DEFAULT_LIST = "~/.ez2-aib/private/hide-words.txt"
DEFAULT_ALLOW = "~/.ez2-aib/private/hide-words-allow.txt"
MIN_LEN = 4
HIDDEN_PATH = "<路徑含私有詞，不印>"


class Refuse(Exception):
    """A reason to stop with rc 2; the text never includes list content."""


def _is_word_char(ch):
    return ch.isascii() and ch.isalnum()


def read_private(path, kind):
    """[(line_no, entry)] or None when the file is missing. Refuses a loose file."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise Refuse("%s 不是一般檔案" % kind)
    if os.name == "nt" or st.st_mode & 0o077:
        raise Refuse("%s 權限太寬(要 600 或更嚴):%s" % (kind, oct(st.st_mode & 0o777)))
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise Refuse("%s 不是目前使用者的檔案" % kind)
    rows = []
    with open(path, encoding="utf-8", errors="strict") as fh:
        for n, raw in enumerate(fh, 1):
            s = raw.strip()
            if s and not s.startswith("#"):
                rows.append((n, s))
    return rows


def load_terms(rows):
    """[(line_no, casefolded entry)] for entries of at least MIN_LEN characters."""
    return [(n, s.casefold()) for n, s in rows if len(s) >= MIN_LEN]


def line_hits(text, terms, allow):
    """Line numbers (in the list file) of the entries that hit `text`, in list order."""
    low = text.casefold()
    spans = []
    for a in allow:
        i = low.find(a)
        while i != -1:
            spans.append((i, i + len(a)))
            i = low.find(a, i + 1)
    out = []
    for n, t in terms:
        i = low.find(t)
        while i != -1:
            e = i + len(t)
            before_ok = i == 0 or not _is_word_char(low[i - 1])
            after_ok = e == len(low) or not _is_word_char(low[e])
            if before_ok and after_ok and not any(s <= i and e <= se for s, se in spans):
                out.append(n)
                break
            i = low.find(t, i + 1)
    return out


def git(args, cwd=None):
    p = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise Refuse("git %s 失敗(rc %d)" % (args[0], p.returncode))
    return p.stdout.decode("utf-8", errors="replace")


def _diff_path(line):
    p = line[4:]
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    if p == "/dev/null":
        return None
    return p[2:] if p[:2] in ("a/", "b/") else p


def added_lines(commit, parents):
    """Yield ('path', path, 0) for each touched path and ('line', path, line_no, text) for each added line."""
    merge = parents > 1
    args = ["diff-tree", "-p", "-U0", "--no-color", "--no-ext-diff", "--no-commit-id", "-r"]
    args += ["--cc"] if merge else ["--root"]
    out = git(args + [commit])
    path, n, in_header, cols = None, 0, False, 1
    for line in out.split("\n"):
        if line.startswith("diff --git ") or line.startswith("diff --cc ") or line.startswith("diff --combined "):
            in_header, path = True, None
            continue
        if in_header:
            if line.startswith("+++ "):
                path = _diff_path(line)
                if path:
                    yield ("path", path, 0, None)
                continue
            if line.startswith("rename to ") or line.startswith("copy to "):
                yield ("path", line.split(" ", 2)[2], 0, None)
                continue
            if not line.startswith("@@"):
                continue
            in_header = False
        if line.startswith("@@"):
            at = len(line) - len(line.lstrip("@"))
            cols = at - 1
            plus = [w for w in line[at:].split(" ") if w.startswith("+")]
            try:
                n = int(plus[0][1:].split(",")[0]) - 1
            except (IndexError, ValueError):
                n = 0
            continue
        if path is None or len(line) < cols:
            continue
        marks = line[:cols]
        if "-" in marks or line.startswith("\\"):
            continue
        n += 1
        if marks == "+" * cols:
            yield ("line", path, n, line[cols:])


def commits_to_push(stdin_text, remote):
    seen, out = set(), []
    for row in stdin_text.splitlines():
        parts = row.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO:
            continue  # deleting a remote ref adds nothing
        args = ["rev-list", "--parents", local_sha, "--not"]
        if remote_sha != ZERO and subprocess.run(
                ["git", "cat-file", "-e", remote_sha + "^{commit}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            args.append(remote_sha)
        args.append("--remotes=" + remote)
        for r in git(args).split("\n"):
            ids = r.split()
            if ids and ids[0] not in seen:
                seen.add(ids[0])
                out.append((ids[0], len(ids) - 1))
    return out


def commits_in_range(rng):
    base, _, head = rng.partition("..")
    if not base or not head:
        raise Refuse("--range 要寫成 <base>..<head>")
    out = []
    for r in git(["rev-list", "--parents", head, "--not", base]).split("\n"):
        ids = r.split()
        if ids:
            out.append((ids[0], len(ids) - 1))
    return out


def scan(commits, terms, allow):
    found, path_cache, reported = [], {}, set()

    def path_terms(p):
        if p not in path_cache:
            path_cache[p] = line_hits(p, terms, allow)
        return path_cache[p]

    for c, parents in commits:
        short = c[:10]
        for kind, path, n, text in added_lines(c, parents):
            hidden = bool(path_terms(path))
            if kind == "path":
                if hidden and (c, path) not in reported:
                    reported.add((c, path))
                    for k in path_terms(path):
                        found.append("%s 命中第 %d 個詞(檔名;commit %s)" % (HIDDEN_PATH, k, short))
                continue
            for k in line_hits(text, terms, allow):
                found.append("%s:%d 命中第 %d 個詞(commit %s)" % (HIDDEN_PATH if hidden else path, n, k, short))
        msg = git(["log", "-1", "--format=%B", c])
        for i, text in enumerate(msg.split("\n"), 1):
            for k in line_hits(text, terms, allow):
                found.append("commit 訊息第 %d 行 命中第 %d 個詞(commit %s)" % (i, k, short))
    return found


def main(argv, stdin):
    if len(argv) >= 2 and argv[0] == "--pre-push":
        mode, arg = "push", argv[1]
    elif len(argv) == 2 and argv[0] == "--range":
        mode, arg = "range", argv[1]
    else:
        print(__doc__.strip().split("\n\n")[-2], file=sys.stderr)
        return 2
    list_path = os.path.expanduser(os.environ.get("EZ2_HIDE_WORDS") or DEFAULT_LIST)
    allow_path = os.path.expanduser(os.environ.get("EZ2_HIDE_WORDS_ALLOW") or DEFAULT_ALLOW)
    rows = read_private(list_path, "hide-words 清單")
    if rows is None:
        print("hide-words 略過：沒有私有清單")
        return 0
    terms = load_terms(rows)
    allow = [s.casefold() for _, s in (read_private(allow_path, "hide-words 白名單") or [])]
    if not terms:
        print("hide-words 略過：清單沒有 %d 字元以上的詞" % MIN_LEN)
        return 0
    commits = commits_to_push(stdin.read(), arg) if mode == "push" else commits_in_range(arg)
    found = scan(commits, terms, allow)
    if not found:
        print("hide-words ok:%d 個 commit 沒有命中" % len(commits))
        return 0
    for f in found:
        print("✖ hide-words:" + f, file=sys.stderr)
    print("✖ 擋下推送：新增內容含私有 hide-words 共 %d 處。改掉(含歷史 commit)再推;"
          "誤報請加進白名單，不要用 --no-verify 繞。" % len(found), file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:], sys.stdin))
    except Refuse as e:
        print("✖ hide-words 拒絕執行:%s" % e, file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # never let a traceback carry list content out
        print("✖ hide-words 檢查出錯(%s),擋下推送" % type(e).__name__, file=sys.stderr)
        sys.exit(2)
