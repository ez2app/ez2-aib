# SPDX-License-Identifier: Apache-2.0
"""aib prepr — the pre-PR checklist (DECISION-7 A, 2026-11-08), called by hand.

    aib prepr [--repo DIR] [--base REF] [--run-tests] [--json]

Each item prints pass / fail / warn / not_run, with the reason, the scope, the tool version and the real exit code.
"not_run" and "warn" are never counted as green; "warn" is not a failure either. Without --run-tests the test items are not run (they are long and need the
machine lease); the items only a person can check (reruns, knives, formulas, PR text, dm, watchers) are listed as
not_run with what to do. Exit code: 1 if any item failed, 2 on a usage error, else 0.

Nothing is installed: a missing tool or target is reported as not_run with the reason.
Standard library only.
"""
import argparse
import ast
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "cockpit"))
import proc_group  # noqa: E402  (DECISION-20 shared tool)

WINDOWS_TARGET = "x86_64-pc-windows-msvc"
TAIL = 600   # characters of output kept per item
AIB_TEST_RC_ALL_SKIPPED = 4   # test_entry.py: every selected test skipped
AIB_TEST_RC_EXCLUSIVE = 5     # test_entry.py RC_EXCLUSIVE: did not run, someone holds the machine exclusively
AIB_TEST_PER_TEST_TIMEOUT_S = 900   # `aib test --timeout`: per test
# The whole `aib test` run: only a backstop, aib test stops a hung test itself. 3600 s stopped healthy big
# batches (244 tests took about 3685 s; #494 L7).
AIB_TEST_OUTER_TIMEOUT_S = 6 * 3600

# Item 10 (DECISION-20, a63ab 11-08): a NEW subprocess call with its own timeout that does not go through
# proc_group. Its timeout kills only the direct child; the tree it started stays (the 11-07 / 11-08 orphans).
# Only lines this change adds are looked at, so the existing calls do not drown the report.
# a63ab: the first week only warns. Turning it into a failure is a separate, reviewed PR (no date switch here).
TIMEOUT_CALLS_STATUS = "warn"
SUBPROCESS_FUNCS = ("run", "call", "check_call", "check_output")
# a call written on purpose this way says why on one of its lines
TIMEOUT_EXEMPT = "proc-group: ok"
TIMEOUT_SKIP = ("aib/src/cockpit/proc_group.py",)

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
    """(rc, combined output). rc None = could not start or timed out (the reason is the output).

    Runs through the shared proc_group tool (DECISION-20): its own process group, a timeout stops the whole tree
    (TERM, grace, KILL, verified), and leftovers of a finished command are stopped too, so a `cargo test` /
    `aib test` never stays behind holding the machine."""
    r = proc_group.run_group(cmd, timeout=timeout, cwd=cwd, env=env)
    if r.timed_out:
        st = "%s %s, still running: %s" % (r.stop.status, r.stop.why, r.stop.survivors or "none listed") \
            if r.stop else "?"
        return None, "timed out after %ss (its process group: %s)\n%s" % (timeout, st, r.out)
    if r.rc is None:
        return None, "not found: %s (%s)" % (cmd[0], r.error)
    if r.stop is not None and r.stop.status != "stopped":
        # bb7dc / a63ab #509: what the command left running could not be shown stopped. Red first: a non-zero
        # exit stays a failure with its real code, the leftover reason appended at the END (the item keeps the
        # tail). Only rc 0 + unverified is lowered to not_run — never green.
        note = "leftovers unverified (%s, still running: %s)" % (r.stop.why, r.stop.survivors or "unknown")
        if r.rc != 0:
            return r.rc, "%s\n[prepr] rc %s; %s\n" % (r.out.rstrip("\n"), r.rc, note)
        return None, "finished with rc 0, but its %s\n%s" % (note, r.out)
    return r.rc, r.out


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


# Paths that make the Rust checks apply are read from the repo's own `.github/workflows/aibd.yml`
# (`on.pull_request.paths`, the filter the PR CI applies), never copied by hand: when aibd.yml gains a path,
# prepr follows it. Any `.rs` file and any `Cargo.toml` count as Rust as well.
AIBD_WORKFLOW = ".github/workflows/aibd.yml"
# Only when a repo has no aibd.yml (or it cannot be parsed): the bare minimum, and the report says so.
FALLBACK_RUST_PATHS = ("Cargo.toml", "Cargo.lock", "crates/**")


def workflow_paths(text, event="pull_request"):
    """The `paths:` list under `on.<event>` of a workflow, in order (the `!` negations kept). [] if there is none.
    A small line reader on purpose (no YAML dependency): it reads the block form GitHub workflows use here."""
    out, ev_indent, in_paths, paths_indent = [], None, False, None
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        ind = len(ln) - len(ln.lstrip())
        st = ln.strip()
        if ev_indent is None:
            if re.match(r"^%s:\s*(#.*)?$" % re.escape(event), st):
                ev_indent = ind
            continue
        if ind <= ev_indent:
            break
        if in_paths:
            m = re.match(r"""^-\s*["']?([^"'#]+?)["']?\s*(#.*)?$""", st)
            if m and ind >= paths_indent:
                out.append(m.group(1).strip())
                continue
            in_paths = False
        if re.match(r"^paths:\s*(#.*)?$", st):
            in_paths, paths_indent = True, ind
    return out


# GitHub path-filter characters prepr does not reproduce: `?` (zero or one of the previous character), `+`,
# `[...]`, `\` escapes. A workflow using any of them makes every file count as Rust (#494 L9: a reader that is
# wrong must err towards running the Rust checks, never towards skipping them).
GLOB_UNSUPPORTED = set("?+[]\\")


def glob_re(pattern):
    """GitHub's path filter glob for `*` and `**`: `**` any characters including `/`, `*` stops at `/`, and a
    leading `**/` also matches at the top level (`**/README.md` matches `README.md`)."""
    out, i = "", 0
    if pattern.startswith("**/"):
        out, i = "(?:.*/)?", 3
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^%s$" % out)


def rust_paths(repo):
    """(patterns, where they came from) for `repo`."""
    try:
        with open(os.path.join(repo, AIBD_WORKFLOW), encoding="utf-8") as f:
            paths = workflow_paths(f.read())
    except OSError:
        paths = []
    odd = [p for p in paths if GLOB_UNSUPPORTED & set(p)]
    if odd:
        return ("**",), "every file (%s has patterns prepr cannot read: %s)" % (AIBD_WORKFLOW, ", ".join(odd))
    if paths:
        return tuple(paths), AIBD_WORKFLOW
    return FALLBACK_RUST_PATHS, "built-in fallback (no readable %s)" % AIBD_WORKFLOW


def is_rust(f, patterns=FALLBACK_RUST_PATHS):
    if f.endswith(".rs") or f == "Cargo.toml" or f.endswith("/Cargo.toml"):
        return True
    hit = False
    for pat in patterns:  # in order, like GitHub: a later `!pattern` takes a match back
        neg = pat.startswith("!")
        if glob_re(pat[1:] if neg else pat).match(f):
            hit = not neg
    return hit


def kinds(files, patterns=FALLBACK_RUST_PATHS):
    """Which checks apply: rust, python (code that `aib test` covers), docs-only."""
    rust = any(is_rust(f, patterns) for f in files)
    code = any(not is_rust(f, patterns) and not f.endswith(".md") for f in files)
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
    rc, o = runner(cmd, repo, env, timeout=AIB_TEST_OUTER_TIMEOUT_S)
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


def added_lines(repo, base, path, runner=run):
    """Line numbers of the working copy of `path` that this change adds: one diff from the merge base with `base`
    to the working tree (committed + uncommitted, numbered as the file is now). "all" = untracked (every line is
    new); None = could not tell (the caller says so)."""
    rc, _ = runner(["git", "ls-files", "--error-unmatch", "--", path], repo)
    if rc is None:
        return None
    if rc != 0:
        return "all"
    rc, mb = runner(["git", "merge-base", base, "HEAD"], repo)
    if rc != 0 or not mb.strip():
        return None
    rc, out = runner(["git", "diff", "-U0", "--no-color", "--no-ext-diff", mb.strip(), "--", path], repo)
    if rc != 0:
        return None
    got = set()
    for m in _HUNK.finditer(out):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        got.update(range(start, start + count))
    return got


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def timeout_calls(text):
    """[(first line, last line, label)] for subprocess calls with timeout= and Popen.communicate / wait
    (timeout=...). Raises SyntaxError on code that does not parse."""
    found = []
    popen = "Popen(" in text   # .wait(timeout=) is a subprocess wait only where Popen is used (Event.wait is not)
    for node in ast.walk(ast.parse(text)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and any(k.arg == "timeout" for k in node.keywords)):
            continue
        f, base = node.func, node.func.value
        if isinstance(base, ast.Name) and base.id == "subprocess" and f.attr in SUBPROCESS_FUNCS:
            label = "subprocess.%s(timeout=)" % f.attr
        elif f.attr == "communicate" or (f.attr == "wait" and popen):
            label = ".%s(timeout=)" % f.attr
        else:
            continue
        found.append((node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno, label))
    return sorted(found)


def timeout_item(repo, base, files, runner=run):
    name = "timeout without proc_group"
    py = [f for f in files if f.endswith(".py") and f not in TIMEOUT_SKIP]
    if not py:
        return item(10, name, "not_run", "not applicable: no Python change")
    hits, unknown = [], []
    for f in py:
        path = os.path.join(repo, f)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            continue   # deleted by this change
        except (OSError, UnicodeDecodeError) as e:
            unknown.append("%s (%s)" % (f, e.__class__.__name__))
            continue
        try:
            calls = timeout_calls(text)
        except SyntaxError:
            unknown.append("%s (does not parse)" % f)
            continue
        if not calls:
            continue
        new = added_lines(repo, base, f, runner)
        if new is None:
            unknown.append("%s (git diff failed)" % f)
            continue
        src = text.splitlines()
        for first, last, label in calls:
            span = range(first, last + 1)
            if new != "all" and not any(n in new for n in span):
                continue
            if any(TIMEOUT_EXEMPT in src[n - 1] for n in span if n - 1 < len(src)):
                continue
            hits.append("%s:%d %s" % (f, first, label))
    if not hits and not unknown:
        return item(10, name, "pass", "no new timeout call outside proc_group in %d Python file(s)" % len(py))
    reason = []
    if hits:
        reason.append("%d new call(s) with their own timeout: a timeout there kills only the direct child, "
                      "not what it started. Use proc_group.run_group, or mark the call `# %s <why>`: %s"
                      % (len(hits), TIMEOUT_EXEMPT, "; ".join(hits)))
    if unknown:
        reason.append("could not check: %s" % "; ".join(unknown))
    status = TIMEOUT_CALLS_STATUS if hits else "not_run"
    return item(10, name, status, " | ".join(reason))


def collect(repo, base, run_tests, aib, runner=run):
    try:
        files = changed_files(repo, base, runner)
    except RuntimeError as e:
        return None, [item(0, "changed files", "fail", str(e))]
    patterns, source = rust_paths(repo)
    k = kinds(files, patterns)
    items = []
    if k["rust"]:
        items += rust_items(repo, run_tests, runner)
    else:
        items.append(item(1, "rust", "not_run", "not applicable: no Rust change (Rust paths from %s)" % source))
    if k["python"]:
        items.append(python_item(repo, base, run_tests, aib, runner))
    elif k["docs_only"]:
        items.append(item(2, "docs", "not_run", "not applicable: docs only; run the doc's own check"))
    else:
        items.append(item(2, "aib test --changed", "not_run", "not applicable: no change outside Rust"))
    items.append(timeout_item(repo, base, files, runner))
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
        warned = sum(i["status"] == "warn" for i in items)
        print("not_run and warn are not green. %s%s" % ("FAILED" if failed else "no failures",
                                                       ", %d warning(s)" % warned if warned else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
