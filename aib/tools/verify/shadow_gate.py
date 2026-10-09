#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shadow-resume self-check: prove the tracer and the reuse decision really work before a release relies on them.

the maintainer 09-28: every release must (1) enable shadow resume and (2) confirm it is actually working; only when it
cannot be enabled does the run fall back to a plain full run. `tools/aib-release.sh` and
`tools/governance/main-merge-witness.sh` call `check` first; there is no operator switch that skips shadow.

check runs a tiny canary in a child process with the same tracer hook the suite uses
(PYTHONPATH=tools/verify, EZ2_VERIFY_TRACE), then:
  stage trace   the trace must show the canary reading tools/verify/sitecustomize.py (present wherever the tracer is)
  stage reuse   a fingerprint row made from that trace must be judged "reuse" when nothing changed
  stage refuse  the same row with a wrong input fingerprint must be judged "run" (no always-reuse)
Output (one JSON line): {"ok": bool, "stage": "...", "reason": "...", "traced": [...], "check_s": n}
rc 0 = shadow resume works; rc 1 = it does not (the caller falls back to a plain full run and says why).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CANARY_TEST = "tests/test-shadow-gate-canary.py"   # row name only; the canary child just reads one tracer file


def check(repo, helpers=None):
    """repo = the tree whose tests will run (its tools/verify is the tracer the children load);
    helpers = the tree whose witness_shadow / shadow_tracer make the reuse decision (default: repo).
    main-merge-witness.sh traces in the candidate tree but decides with its own tree, so it passes both."""
    t0 = time.perf_counter()
    repo = Path(repo).resolve()
    helpers = Path(helpers).resolve() if helpers else repo
    out = {"ok": False, "stage": "setup", "reason": "", "traced": []}
    verify = repo / "tools" / "verify"
    hv = helpers / "tools" / "verify"
    gov = helpers / "tools" / "governance"
    if not (verify / "sitecustomize.py").is_file():
        out["reason"] = "no tracer in the tree under test (tools/verify/sitecustomize.py missing)"
        return _done(out, t0)
    for need in (hv / "shadow_tracer.py", hv / "reuse_index.py", gov / "witness_shadow.py"):
        if not need.is_file():
            out["reason"] = "missing helper %s" % need
            return _done(out, t0)
    sys.path.insert(0, str(hv))
    sys.path.insert(0, str(gov))
    try:
        import shadow_tracer as st
        import witness_shadow as ws
    except Exception as exc:                       # a broken import is exactly what this gate must catch
        out["reason"] = "import failed: %s: %s" % (type(exc).__name__, exc)
        return _done(out, t0)
    with tempfile.TemporaryDirectory(prefix="aib-shadow-gate-") as d:
        trace = Path(d) / "trace.jsonl"
        envf = Path(d) / "env.txt"
        base = dict(os.environ)
        base.pop("EZ2_VERIFY_TRACE", None)
        envf.write_text("".join("%s=%s\n" % (k, v) for k, v in base.items() if "\n" not in v), encoding="utf-8")
        child = dict(base, PYTHONPATH=str(verify) + (os.pathsep + base["PYTHONPATH"] if base.get("PYTHONPATH") else ""),
                     EZ2_VERIFY_TRACE=str(trace))
        out["stage"] = "trace"
        try:
            p = subprocess.run([sys.executable, "-c", "open('tools/verify/sitecustomize.py', encoding='utf-8').read()"], cwd=str(repo),
                               env=child, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            out["reason"] = "canary did not run: %s" % exc
            return _done(out, t0)
        if p.returncode != 0:
            out["reason"] = "canary rc=%d: %s" % (p.returncode, (p.stderr or "")[-200:])
            return _done(out, t0)
        try:
            events = st.load_trace(trace)
            items, _fp, unmeasured, _ext, why = st.inputs_from_events(events, repo=str(repo))
        except Exception as exc:
            out["reason"] = "trace unreadable: %s: %s" % (type(exc).__name__, exc)
            return _done(out, t0)
        paths = [str(i.get("path") if isinstance(i, dict) else i) for i in items]
        out["traced"] = paths[:10]
        if not any("tools/verify/sitecustomize.py" in x for x in paths):   # items look like open:./path:<sha>
            out["reason"] = "tracer did not record the canary read (unmeasured=%s why=%s)" % (unmeasured, why)
            return _done(out, t0)
        out["stage"] = "reuse"
        rowf = Path(d) / "row.json"
        seed = json.dumps({"test": CANARY_TEST, "rc": 0, "outcome": "pass", "duration_s": 0.1})
        try:
            rc = ws.main(["fingerprint", "--trace", str(trace), "--test", CANARY_TEST, "--repo", str(repo),
                          "--row", seed, "--env-file", str(envf), "--out", str(rowf)])
            row = json.loads(rowf.read_text(encoding="utf-8"))
        except Exception as exc:
            out["reason"] = "fingerprint failed: %s: %s" % (type(exc).__name__, exc)
            return _done(out, t0)
        if rc != 0 or row.get("measured") is not True:
            out["reason"] = "fingerprint row not measured (rc=%s measured=%r why=%r)" % (rc, row.get("measured"), row.get("why"))
            return _done(out, t0)
        env_now = st.load_env_file(str(envf))
        action, _prev, why = ws.decide_why(CANARY_TEST, row, str(repo), environ=env_now)
        if action != "reuse":
            out["reason"] = "unchanged canary was not reused (why=%s)" % why
            return _done(out, t0)
        out["stage"] = "refuse"
        bad = dict(row, input_fp="0" * 16, key="tampered")
        action2, _p2, why2 = ws.decide_why(CANARY_TEST, bad, str(repo), environ=env_now)
        if action2 == "reuse":
            out["reason"] = "a wrong fingerprint was still reused (always-reuse)"
            return _done(out, t0)
    out.update(ok=True, stage="done", reason="tracer recorded the read; unchanged => reuse; changed => run")
    return _done(out, t0)


def _done(out, t0):
    out["check_s"] = round(time.perf_counter() - t0, 3)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="shadow-resume self-check (release gate)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    c.add_argument("--helpers", default="")
    a = ap.parse_args(argv)
    res = check(a.repo, a.helpers or None)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
