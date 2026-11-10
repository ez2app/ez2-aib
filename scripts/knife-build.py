#!/usr/bin/env python3
"""Build-time knife: break one line on purpose, check the BUILD fails for our reason.

    python3 -I scripts/knife-build.py FILE OLD NEW --expect-error TEXT -- CMD...

Same rules as scripts/knife.py (OLD exactly once; restore by writing the original
text back so the mtime is new), but for guards that are compile-time checks
(`const` asserts): there is no failed test to name, so the knife passes (rc 0) only
if CMD fails AND rustc reports a const-evaluation error (`error[E0080]`) whose panic
message is exactly TEXT. rustc also prints the source line around any error, and the
assert's message sits on that line, so a plain "TEXT is somewhere in the output" check
would count a build that broke for another reason (a typo in the knife) as a bite.
Both rustc wordings are read: `evaluation panicked: TEXT` (1.88+) and the older
`the evaluated program panicked at 'TEXT', file:line:col`.
"""
import argparse
import re
import subprocess
import sys

# One rustc diagnostic runs from its `error...:` / `warning...:` line to the next one.
_DIAG = re.compile(r"(?m)^(?=(?:error|warning)(?:\[[A-Z]\d{4}\])?:)")
_PANIC = re.compile(r"evaluation panicked: (.*)$|panicked at '(.*?)', \S+:\d+:\d+", re.M)


def const_panics(out):
    """Panic messages of the const-evaluation errors (E0080) in rustc's output."""
    found = []
    for diag in _DIAG.split(out):
        if not diag.startswith("error[E0080]"):
            continue
        for m in _PANIC.finditer(diag):
            found.append((m.group(1) if m.group(1) is not None else m.group(2)).strip())
    return found


def main():
    argv = sys.argv[1:]
    if "--" not in argv:
        sys.exit("knife-build: missing `-- CMD...`")
    cut = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--expect-error", required=True)
    a = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]
    if not cmd:
        sys.exit("knife-build: missing command after --")
    with open(a.file, encoding="utf-8") as f:
        orig = f.read()
    n = orig.count(a.old)
    if n != 1:
        print(f"KNIFE-BUILD {a.expect_error!r}: pattern found {n} times in {a.file}; nothing changed")
        return 2
    try:
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(orig.replace(a.old, a.new, 1))
        p = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(orig)
    panics = const_panics(p.stdout + p.stderr)
    verdict = "BITES" if p.returncode != 0 and a.expect_error in panics else "MISSES"
    print(f"KNIFE-BUILD {a.expect_error!r}: {verdict} rc={p.returncode} const_panics={panics}")
    return 0 if verdict == "BITES" else 1


if __name__ == "__main__":
    sys.exit(main())
