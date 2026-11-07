#!/usr/bin/env python3
"""Read-only legacy platform-coupling scan (AIB 2.0 Phase 0).

Runs `git grep` over a checkout and writes:
  - <out>/raw.csv      one row per (file, line, keyword)
  - <out>/summary.md   per category: files ranked by hits, with keywords

It never writes inside the scanned repo. Usage:
  python3 -I scripts/audit-legacy/scan.py --repo PATH --out DIR [--rev REV] [--names-only]

--names-only keeps file paths and counts but drops the matched source line
(use it for private repositories whose content must not be copied).
"""
import argparse
import collections
import csv
import os
import subprocess
import sys

# keyword -> category (spec section 13.1 plus a few close relatives)
KEYWORDS = {
    "tmux": "terminal-mux",
    "Terminal.app": "terminal-app",
    "osascript": "terminal-app",
    "fcntl": "file-lock",
    "flock": "file-lock",
    "os.fork": "process",
    "setsid": "process",
    "killpg": "process",
    "SIGKILL": "process",
    "start_new_session": "process",
    "preexec_fn": "process",
    "os.kill": "process",
    "pgrep": "process-discovery",
    "pkill": "process-discovery",
    "lsof": "process-discovery",
    "ps -": "process-discovery",
    "launchctl": "autostart",
    "LaunchAgent": "autostart",
    "swiftc": "native-helper",
    "LocalAuthentication": "native-helper",
    "security find-generic-password": "secrets",
    "security add-generic-password": "secrets",
    "Keychain": "secrets",
    "/tmp/": "paths",
    "Library/": "paths",
    "expanduser(\"~": "paths",
    "shell=True": "shell-string",
    "bash -c": "shell-string",
    "sh -c": "shell-string",
    "LD_PRELOAD": "loader-injection",
    "DYLD_": "loader-injection",
    "pbcopy": "clipboard",
    "pbpaste": "clipboard",
    "open -a": "terminal-app",
    "sw_vers": "os-probe",
    "sysctl": "os-probe",
    "pmset": "os-probe",
}


def git_grep(repo, kw, rev=None):
    cmd = ["git", "-C", repo, "grep", "-n", "-I", "-F", "-e", kw]
    if rev:
        cmd.append(rev)
    p = subprocess.run(
        cmd + ["--", "."],
        capture_output=True, text=True, errors="replace",
    )
    if p.returncode not in (0, 1):
        sys.exit(f"git grep failed in {repo}: {p.stderr.strip()}")
    for line in p.stdout.splitlines():
        if rev:  # "<rev>:<path>:<line>:<text>"
            line = line[len(rev) + 1:]
        path, _, rest = line.partition(":")
        lineno, _, text = rest.partition(":")
        yield path, int(lineno), text.strip()


def area(path):
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] in ("aib",) and parts[1] in ("src", "extensions"):
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--names-only", action="store_true")
    ap.add_argument("--rev", help="scan this commit (e.g. origin/main) instead of the work tree")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    rows = []
    for kw, cat in KEYWORDS.items():
        for path, ln, text in git_grep(repo, kw, a.rev):
            rows.append((cat, kw, path, ln, "" if a.names_only else text[:160]))

    with open(os.path.join(out, "raw.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "keyword", "file", "line", "text"])
        w.writerows(sorted(rows))

    by_cat = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for cat, kw, path, _ln, _t in rows:
        by_cat[cat][path][kw] += 1
    by_area = collections.defaultdict(collections.Counter)
    for cat, kw, path, _ln, _t in rows:
        by_area[area(path)][cat] += 1

    with open(os.path.join(out, "summary.md"), "w") as f:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "--short", a.rev or "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        f.write(f"# scan summary ({os.path.basename(repo)} @ {head})\n\n")
        f.write("| category | files | hits |\n|---|---:|---:|\n")
        for cat in sorted(by_cat, key=lambda c: -sum(sum(v.values()) for v in by_cat[c].values())):
            files = by_cat[cat]
            f.write(f"| {cat} | {len(files)} | {sum(sum(v.values()) for v in files.values())} |\n")
        f.write("\n## by area\n\n| area | total | categories |\n|---|---:|---|\n")
        for ar, c in sorted(by_area.items(), key=lambda x: -sum(x[1].values())):
            f.write(f"| {ar} | {sum(c.values())} | {', '.join(f'{k} {v}' for k, v in c.most_common())} |\n")
        for cat in sorted(by_cat):
            f.write(f"\n## {cat}\n\n| file | hits | keywords |\n|---|---:|---|\n")
            for path, kws in sorted(by_cat[cat].items(), key=lambda x: -sum(x[1].values())):
                f.write(f"| {path} | {sum(kws.values())} | {', '.join(f'{k}×{v}' for k, v in kws.most_common())} |\n")
    print(f"rows={len(rows)} categories={len(by_cat)} out={out}")


if __name__ == "__main__":
    main()
