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
import signal
import subprocess
import sys
import time

WINDOWS_TARGET = "x86_64-pc-windows-msvc"
TAIL = 600   # characters of output kept per item
AIB_TEST_RC_ALL_SKIPPED = 4   # test_entry.py: every selected test skipped
AIB_TEST_RC_EXCLUSIVE = 5     # test_entry.py RC_EXCLUSIVE: did not run, someone holds the machine exclusively
AIB_TEST_PER_TEST_TIMEOUT_S = 900   # `aib test --timeout`: per test

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


def stop_group(p, grace=5.0):
    """Stop everything `p` started: its own process group only (it was started in a new session), TERM, then KILL
    after `grace`. Never signals anything outside that group. Windows: no process groups here, kill the child."""
    if not hasattr(os, "killpg"):
        p.kill()
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            # macOS answers EPERM, not ESRCH, once only zombies are left in the group: nothing left to stop.
            return
        end = time.monotonic() + wait
        while time.monotonic() < end:
            p.poll()
            try:
                os.killpg(p.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def run(cmd, cwd, env=None, timeout=3600):
    """(rc, combined output). rc None = could not start or timed out (the reason is the output).

    The command runs in a new session, so on a timeout its whole tree is stopped (grandchildren too), not just the
    direct child: a timed-out `aib test` / `cargo test` must not stay behind holding the machine."""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, start_new_session=True)
    except FileNotFoundError:
        return None, "not found: %s" % cmd[0]
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(p)
        try:
            out, _ = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = ""
        return None, "timed out after %ds (its process group was stopped)\n%s" % (timeout, out or "")
    return p.returncode, out or ""


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


# Paths that make the Rust checks apply: the same list as `.github/workflows/aibd.yml` `paths`
# (a test keeps the two in step), plus any `.rs` file.
RUST_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "clippy.toml", "scripts/check-core-purity.sh",
              ".github/workflows/aibd.yml")
RUST_DIRS = ("crates/", "xtask/", "migrations/")


def is_rust(f):
    return f.endswith(".rs") or f in RUST_FILES or f.startswith(RUST_DIRS) or f.endswith(("/Cargo.toml",))


def kinds(files):
    """Which checks apply: rust, python (code that `aib test` covers), docs-only."""
    rust = any(is_rust(f) for f in files)
    code = any(not is_rust(f) and not f.endswith(".md") for f in files)
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
    # aib test stops a hung test itself (per test); run()'s own timeout + group stop is the backstop
    cmd = aib + ["test", "--repo", repo, "--changed", "--base", base, "--timeout", str(AIB_TEST_PER_TEST_TIMEOUT_S)]
    env = dict(os.environ, EZ2_AIB_LOCALE="en")
    rc, o = runner(cmd, repo, env)
    if rc is None:
        return item(2, name, "not_run", o, cmd=cmd)
    if rc == 0 and "Nothing was run" in o:
        return item(2, name, "not_run", "the changes select no tests (not green)", rc=rc, cmd=cmd, out=o)
    # aib test's own "did not run" codes (test_entry.py): not a failure of the change, not green either
    if rc == AIB_TEST_RC_EXCLUSIVE:
        return item(2, name, "not_run", "machine held exclusively by someone else (aib test rc 5); run again later",
                    rc=rc, cmd=cmd, out=o)
    if rc == AIB_TEST_RC_ALL_SKIPPED:
        return item(2, name, "not_run", "every selected test was skipped (aib test rc 4): nothing verified",
                    rc=rc, cmd=cmd, out=o)
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
            if i["tail"] and (i["status"] == "fail" or (i["status"] == "not_run" and i["rc"] is not None)):
                print("            " + i["tail"].strip().replace("\n", "\n            "))
        print("not_run is not green. %s" % ("FAILED" if failed else "no failures"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
