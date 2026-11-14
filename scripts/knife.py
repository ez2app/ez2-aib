#!/usr/bin/env python3
"""Knife test: break one line on purpose, run a command, restore the file.

    python3 -I scripts/knife.py FILE OLD NEW --expect-fail TEST_NAME -- CMD...

- OLD must occur exactly once in FILE (otherwise nothing is changed, rc 2).
- The file is restored by writing the original text back, so its mtime is new
  and cargo / Python rebuild it. Never restore with cp/mv: an old mtime makes
  cargo reuse the knifed build and every later run fails for the wrong reason.
- Passes (rc 0) only if CMD fails AND the output names TEST_NAME as failed.
  "Everything went red" does not count: the knife must hit its own test.
"""
import argparse
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--expect-fail", required=True)
    argv = sys.argv[1:]
    if "--" not in argv:
        sys.exit("knife: missing `-- CMD...`")
    cut = argv.index("--")
    a = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]
    if not cmd:
        sys.exit("knife: missing command after --")
    with open(a.file, encoding="utf-8") as f:
        orig = f.read()
    n = orig.count(a.old)
    if n != 1:
        print(f"KNIFE {a.expect_fail}: pattern found {n} times in {a.file}; nothing changed")
        return 2
    try:
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(orig.replace(a.old, a.new, 1))
        p = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(orig)
    out = p.stdout + p.stderr
    failed = sorted({ln.split()[1] for ln in out.splitlines()
                     if ln.startswith("test ") and ln.rstrip().endswith("FAILED")})
    hit = any(a.expect_fail in t for t in failed)
    verdict = "BITES" if p.returncode != 0 and hit else "MISSES"
    print(f"KNIFE {a.expect_fail}: {verdict} rc={p.returncode} failed={failed}")
    return 0 if verdict == "BITES" else 1


if __name__ == "__main__":
    sys.exit(main())
