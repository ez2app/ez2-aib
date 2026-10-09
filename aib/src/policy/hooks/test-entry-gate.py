#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse(Bash) gate: running a test file directly bypasses shadow resume — use `aib test` instead.

the maintainer 09-29: "running tests must (1) enable shadow resume, (2) confirm it is enabled".  `aib test` does both
(tools/verify/test_entry.py); `python3 tests/test-x.py` does neither and leaves nothing a release run can reuse.
Design: docs/DESIGN-aib-test-entry-shadow-0929.md §5 and §7 cell 4.  Installed in the repository-level
.claude/settings.json, so every Claude session opened in this repository (and its worktrees) loads it.

Blocks (exit 2, message on stderr for the model) only when a simple command in the Bash line *executes* a test file:
  python / python3 / python3.X [options] <…/tests/test-*.py>      (also after `cd …tests &&`: python3 test-x.py)
  ./tests/test-x.py  or  /abs/…/tests/test-x.py  as the program itself
  for VAR in …tests/test-…; do python3 "$VAR"; done                (the loop variable is followed)
  with env assignments (TMPDIR=… python3 …) and wrappers (timeout, env, nice, time, command, exec, nohup, caffeinate)
  in front, and across &&, ||, ;, |, newlines.
Lets through everything else, on purpose: python3 -m py_compile / -c / -m …, grep/cat/sed/head/git on test files,
`bash some.sh` (what a script does inside is not this gate's business), `aib test …`, heredoc bodies.
Known not caught: `bash -c "python3 tests/…"`, `while read t; do python3 "$t"; done < list`, `xargs python3`.

Fails open: unreadable input, a parse error or any exception ⇒ exit 0 and one line on stderr saying why.
A gate on every Bash call that blocks by mistake costs more than one that misses.
Codex / Grok seats do not load Claude hooks: for them this is a rule in the onboarding text, not a block.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON_RE = re.compile(r"^python(?:3(?:\.\d+)?)?$")
TEST_NAME_RE = re.compile(r"^test-[^/]*\.py$")
WRAPPERS = {"timeout", "env", "nice", "time", "command", "exec", "nohup", "caffeinate"}
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", ";;", "|&"}
KEYWORDS = {"do", "done", "then", "else", "elif", "fi", "if", "while", "until", "!"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)\n\s*\2\s*(?:\n|$)", re.S)


def _t(key, params=None):
    try:
        sys.path.insert(0, str(ROOT / "src" / "cockpit"))
        import i18n_lib
        return i18n_lib.t("aib.test." + key, params, locale=os.environ.get("EZ2_AIB_LOCALE", "zh-TW"))
    except Exception:
        return "%s %s" % (key, params or "")


def _strip_heredocs(cmd):
    """Heredoc bodies are data (a script being written), not commands; keep the opening line, drop the body."""
    return HEREDOC_RE.sub(lambda m: m.group(0).split("\n", 1)[0] + "\n", cmd)


def _simple_commands(cmd):
    """The Bash line ⇒ list of argv lists (quotes removed, comments dropped)."""
    lex = shlex.shlex(_strip_heredocs(cmd).replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    out, cur = [], []
    for tok in lex:
        if tok in SEPARATORS or (not cur and tok in KEYWORDS):
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _is_test_path(arg, in_tests_dir):
    p = arg.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if not TEST_NAME_RE.match(name):
        return False
    return "/tests/" in "/" + p or in_tests_dir


def _strip_front(argv):
    """Drop NAME=value assignments and wrapper programs (with their numeric/option arguments)."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if ASSIGN_RE.match(a):
            i += 1
            continue
        if a in WRAPPERS:
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or re.match(r"^\d+[smhd]?$", argv[i])
                                     or ASSIGN_RE.match(argv[i])):
                i += 1
            continue
        break
    return argv[i:]


def _python_target(argv):
    """First positional of a python invocation, or None when it runs -m / -c / stdin."""
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-m", "-c", "-"):
            return None
        if a in ("-X", "-W", "-Q"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None


def find_direct_test_run(cmd):
    """The offending test path (or loop word list) if the line executes a test file directly, else None."""
    loop_vars = {}
    in_tests_dir = False
    for argv in _simple_commands(cmd):
        if argv and argv[0] == "for" and len(argv) >= 3 and argv[2] == "in":
            words = argv[3:]
            if any("tests/test-" in w or TEST_NAME_RE.match(w.rsplit("/", 1)[-1]) for w in words):
                loop_vars[argv[1]] = " ".join(words)
            continue
        argv = _strip_front(argv)
        if not argv:
            continue
        if argv[0] == "cd":
            in_tests_dir = bool(len(argv) > 1 and argv[1].rstrip("/").endswith("tests"))
            continue
        prog = argv[0]
        if _is_test_path(prog, in_tests_dir) and (prog.startswith(("./", "/")) or "/" in prog):
            return prog
        if PYTHON_RE.match(prog.rsplit("/", 1)[-1]):
            target = _python_target(argv)
            if target is None:
                continue
            m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", target)
            if m and m.group(1) in loop_vars:
                return loop_vars[m.group(1)]
            if _is_test_path(target, in_tests_dir):
                return target
    return None


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if event.get("tool_name") != "Bash":
            return 0
        cmd = (event.get("tool_input") or {}).get("command") or ""
        hit = find_direct_test_run(cmd)
    except Exception as exc:  # fail open: a broken gate must never block work
        sys.stderr.write("test-entry-gate: %s: %s (allowed)\n" % (type(exc).__name__, exc))
        return 0
    if hit is None:
        return 0
    sys.stderr.write(_t("gate_blocked", {"path": hit}) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
