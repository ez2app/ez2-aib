# SPDX-License-Identifier: Apache-2.0
"""aib prepr — the pre-PR checklist (DECISION-7 A, 2026-11-08), called by hand.

    aib prepr [--repo DIR] [--base REF] [--run-tests] [--json]

Each item prints pass / fail / not_run, with the reason, the scope, the tool version and the real exit code.
"not_run" is never counted as green. Without --run-tests the test items are not run (they are long and need the
machine lease); the items only a person can check (reruns, knives, formulas, PR text, dm, watchers) are listed as
not_run with what to do. Exit code: 1 if any item failed, 2 on a usage error, else 0.

Nothing is installed: a missing tool or target is reported as not_run with the reason.
Standard library only.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

WINDOWS_TARGET = "x86_64-pc-windows-msvc"
TAIL = 600   # characters of output kept per item

# (item number in DECISION-7 A, what to do) for the items a person checks
MANUAL = (
    (3, "time / concurrency / async / random tests: rerun each new one >= 5 times and record every result"),
    (4, "knives: cargo --no-fail-fast, one knife at a time, the target assertion really red (not timeout), "
        "restore and keep the real exit code; a positive flow too"),
    (5, "locks / claims / do-once: dead, alive and well, alive but slow, unknown; pid reuse; hand-over "
        "between check and action; multi-process barrier test; fail closed"),
    (6, "metrics / threshold formulas: miss 1, extra 1, miss 1 + extra 1, merged, duplicate counterexamples; "
        "identity coverage apart from the count proxy"),
    (7, "PR text: compare with the old behaviour item by item, write only what was verified, mark UNVERIFIED"),
    (8, "dm: read RESULT stage (sort-queued is queued, not delivered); close with evidence for the right PR + head"),
    (9, "background watchers: pid, last output, real exit code, timeout class; empty output is not done"),
)


def run(cmd, cwd, env=None, timeout=3600):
    """(rc, combined output). rc None = could not start or timed out (the reason is the output)."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
    except FileNotFoundError:
        return None, "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return None, "timed out after %ds" % timeout
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def item(n, name, status, reason="", scope="", version="", rc=None, cmd=None, out=""):
    return {"item": n, "name": name, "status": status, "reason": reason, "scope": scope, "version": version,
            "rc": rc, "cmd": " ".join(cmd) if cmd else "", "tail": out[-TAIL:] if out else ""}


def changed_files(repo, base, runner=run):
    """Files changed since the merge base with `base`, plus uncommitted and untracked ones."""
    files = set()
    for cmd in (["git", "diff", "--name-only", "%s...HEAD" % base], ["git", "diff", "--name-only", "HEAD"],
                ["git", "ls-files", "--others", "--exclude-standard"]):
        rc, out = runner(cmd, repo)
        if rc != 0:
            raise RuntimeError("%s: rc %s: %s" % (" ".join(cmd), rc, out.strip()[-200:]))
        files.update(x for x in out.splitlines() if x.strip())
    return sorted(files)


def kinds(files):
    """Which checks apply: rust, python (code that `aib test` covers), docs-only."""
    rust = any(f.endswith((".rs", "Cargo.toml", "Cargo.lock")) for f in files)
    code = any(not f.endswith((".md", ".rs", "Cargo.toml", "Cargo.lock")) for f in files)
    return {"rust": rust, "python": code, "docs_only": bool(files) and not rust and not code}


def windows_split(metadata):
    """(members that build without C, {excluded member: [C packages it pulls in]}) from `cargo metadata`
    filtered for the Windows target. A package that declares `links` builds C code (libsqlite3-sys)."""
    pkgs = {p["id"]: p for p in metadata["packages"]}
    deps = {n["id"]: [d["pkg"] for d in n.get("deps", [])] for n in (metadata.get("resolve") or {}).get("nodes", [])}
    ok, excluded = [], {}
    for mid in metadata["workspace_members"]:
        seen, stack, c = set(), [mid], set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            if pid != mid and pkgs.get(pid, {}).get("links"):
                c.add(pkgs[pid]["name"])
            stack.extend(deps.get(pid, []))
        name = pkgs[mid]["name"]
        if c:
            excluded[name] = sorted(c)
        else:
            ok.append(name)
    return sorted(ok), excluded


def rust_items(repo, run_tests, runner=run):
    out = []
    rc, ver = runner(["cargo", "--version"], repo)
    if rc != 0:
        reason = "cargo not available (%s)" % ver.strip()
        for name in ("rust fmt", "rust clippy (host)", "rust clippy (%s)" % WINDOWS_TARGET, "cargo test"):
            out.append(item(1, name, "not_run", reason))
        return out
    ver = ver.strip()

    def one(name, cmd, scope):
        rc, o = runner(cmd, repo)
        st = "pass" if rc == 0 else "fail" if rc is not None else "not_run"
        out.append(item(1, name, st, "" if rc is not None else o, scope, ver, rc, cmd, o))

    one("rust fmt", ["cargo", "fmt", "--all", "--check"], "workspace")
    one("rust clippy (host)", ["cargo", "clippy", "--workspace", "--all-targets", "--locked", "--", "-D", "warnings"],
        "workspace")
    rc, tl = runner(["rustup", "target", "list", "--installed"], repo)
    name = "rust clippy (%s)" % WINDOWS_TARGET
    if rc != 0 or WINDOWS_TARGET not in tl.split():
        out.append(item(1, name, "not_run", "target %s not installed (not installed for you)" % WINDOWS_TARGET,
                        version=ver))
    else:
        rc, meta = runner(["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform",
                           WINDOWS_TARGET], repo)
        try:
            ok, excluded = windows_split(json.loads(meta)) if rc == 0 else ([], {})
        except ValueError:
            rc = None
        if rc != 0 or not ok:
            out.append(item(1, name, "not_run", "cargo metadata rc %s / no member without C" % rc, version=ver,
                            out=meta if rc != 0 else ""))
        else:
            scope = "only " + ", ".join(ok)
            if excluded:
                scope += "; excluded (C build, see CI Windows): " + ", ".join(
                    "%s (%s)" % (k, "+".join(v)) for k, v in sorted(excluded.items()))
            cmd = ["cargo", "clippy"]
            for m in ok:
                cmd += ["-p", m]
            one(name, cmd + ["--target", WINDOWS_TARGET, "--all-targets", "--locked", "--", "-D", "warnings"], scope)
    if run_tests:
        one("cargo test", ["cargo", "test", "--workspace", "--locked", "--no-fail-fast"], "workspace")
    else:
        out.append(item(1, "cargo test", "not_run", "needs --run-tests (take the lease first)", "workspace", ver))
    return out


def python_item(repo, base, run_tests, aib, runner=run):
    name = "aib test --changed"
    if not run_tests:
        return item(2, name, "not_run", "needs --run-tests (take the lease first)")
    cmd = aib + ["test", "--repo", repo, "--changed", "--base", base]
    env = dict(os.environ, EZ2_AIB_LOCALE="en")
    rc, o = runner(cmd, repo, env)
    if rc is None:
        return item(2, name, "not_run", o, cmd=cmd)
    if rc == 0 and "Nothing was run" in o:
        return item(2, name, "not_run", "the changes select no tests (not green)", rc=rc, cmd=cmd, out=o)
    return item(2, name, "pass" if rc == 0 else "fail", rc=rc, cmd=cmd, out=o)


def collect(repo, base, run_tests, aib, runner=run):
    try:
        files = changed_files(repo, base, runner)
    except RuntimeError as e:
        return None, [item(0, "changed files", "fail", str(e))]
    k = kinds(files)
    items = []
    if k["rust"]:
        items += rust_items(repo, run_tests, runner)
    else:
        items.append(item(1, "rust", "not_run", "not applicable: no Rust change"))
    if k["python"]:
        items.append(python_item(repo, base, run_tests, aib, runner))
    elif k["docs_only"]:
        items.append(item(2, "docs", "not_run", "not applicable: docs only; run the doc's own check"))
    else:
        items.append(item(2, "aib test --changed", "not_run", "not applicable: no change outside Rust"))
    for n, what in MANUAL:
        items.append(item(n, "manual", "not_run", "by hand: " + what))
    return files, items


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib prepr", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--run-tests", action="store_true", help="also run cargo test / aib test (take the lease first)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    aib = [sys.executable, os.path.join(os.path.dirname(here), "bin", "aib")]
    if not os.path.exists(aib[1]):
        aib = [shutil.which("aib") or "aib"]
    files, items = collect(repo, a.base, a.run_tests, aib)
    failed = any(i["status"] == "fail" for i in items)
    if a.json:
        print(json.dumps({"repo": repo, "base": a.base, "files": files, "items": items, "failed": failed},
                         ensure_ascii=False, indent=1))
    else:
        print("aib prepr  repo=%s  base=%s  changed files=%s" % (repo, a.base, "?" if files is None else len(files)))
        for i in items:
            line = "[%s] %d %-34s %s" % (i["status"].upper().ljust(7), i["item"], i["name"], i["reason"])
            if i["rc"] is not None:
                line += "  rc=%s" % i["rc"]
            print(line.rstrip())
            for key in ("scope", "version", "cmd"):
                if i[key]:
                    print("            %s: %s" % (key, i[key]))
            if i["status"] == "fail" and i["tail"]:
                print("            " + i["tail"].strip().replace("\n", "\n            "))
        print("not_run is not green. %s" % ("FAILED" if failed else "no failures"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
