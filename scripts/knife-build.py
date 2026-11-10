#!/usr/bin/env python3
"""Build-time knife: break one line on purpose, check the BUILD fails for our reason.

    python3 -I scripts/knife-build.py FILE OLD NEW --expect-error TEXT -- CMD...

Same rules as scripts/knife.py (OLD exactly once; restore by writing the original
text back so the mtime is new), but for guards that are compile-time checks
(`const` asserts): there is no failed test to name, so the knife passes (rc 0) only
if CMD fails AND its output contains TEXT (the assert's own message). A build that
fails for another reason (a typo in the knife) does not count.
"""
import argparse
import subprocess
import sys


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
    hit = a.expect_error in (p.stdout + p.stderr)
    verdict = "BITES" if p.returncode != 0 and hit else "MISSES"
    print(f"KNIFE-BUILD {a.expect_error!r}: {verdict} rc={p.returncode}")
    return 0 if verdict == "BITES" else 1


if __name__ == "__main__":
    sys.exit(main())
