# SPDX-License-Identifier: Apache-2.0
"""API seats: run a test through quick verify (docs/DESIGN-aib-verify-0926.md, upgrade 1, the maintainer 09-28).

When an AI seat asks aib_local to run tests/test-x.py, this module first checks whether the last *real* run of
that test in this repository was green and everything it read is unchanged (same fingerprints as the release
witness: tools/verify/shadow_tracer.reuse_row). If so the seat gets the previous green result back at once —
no machine lease, no queue, no second run. Otherwise the test really runs, under the read tracer, and the new
row is kept for next time.

Never reused: a red, timed-out, unmeasured or external row; a test whose own file or any file it read changed;
a changed environment. The seat can always ask for a fresh run ("fresh": true); a fresh run is still traced and recorded.
No off switch (the maintainer 09-29, docs/DESIGN-aib-test-entry-shadow-0929.md §6): AIB_SEAT_TEST_REUSE=0 used to turn reuse and
recording off; it is now ignored, so every real run leaves a row the release run can reuse.
Repositories without tools/verify/shadow_tracer.py (fixtures, old trees, customer projects without the adapter)
keep the old behaviour: plain run, nothing recorded.

Store (one store for the whole shadow-resume system, 09-28): the rows go into the shadow directory that
tools/verify/shadow_tracer and reuse_index already use (~/.ez2-aib/verify-shadow, or $EZ2_VERIFY_SHADOW_DIR;
$AIB_SEAT_TEST_STORE overrides the folder), as seat-<repo id>.rows.jsonl, stamped with the repository identity.
The reuse decision is reuse_index.decide: rows measured by any working tree of the same repository (same git
common dir) count — cross-tree reuse — and each one is re-fingerprinted against *this* tree before it is reused.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GREEN_TAIL_CHARS = 800      # a green result needs its summary, not 4,000 characters of log (fewer tokens per turn)
FAIL_TAIL_CHARS = 4000


def enabled(environ=None) -> bool:
    """Kept for callers; always True since 09-29 (AIB_SEAT_TEST_REUSE=0 no longer switches anything off)."""
    return True


_LOADED = {}


def _verify(root: Path):
    """shadow_tracer and reuse_index of *this* repository, loaded per folder: shadow_tracer points the child at
    its own folder (HERE), so a copy imported for another repository would install the wrong hook path; and
    reuse_index is made to use this folder's shadow_tracer, not whichever one was imported first."""
    d = (Path(root) / "tools" / "verify").resolve()
    if not (d / "shadow_tracer.py").is_file() or not (d / "reuse_index.py").is_file():
        return None
    if d in _LOADED:
        return _LOADED[d]
    import importlib.util
    added = str(d) not in sys.path
    if added:
        sys.path.insert(0, str(d))
    saved = sys.modules.get("shadow_tracer")
    try:
        mods = []
        for name in ("shadow_tracer", "reuse_index"):
            spec = importlib.util.spec_from_file_location("_seat_%s_%x" % (name, abs(hash(str(d)))), d / (name + ".py"))
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            if name == "reuse_index":
                sys.modules["shadow_tracer"] = mods[0]      # its `import shadow_tracer` gets this folder's copy
            spec.loader.exec_module(mod)
            mods.append(mod)
    except Exception:
        return None
    finally:
        if saved is not None:
            sys.modules["shadow_tracer"] = saved
        else:
            sys.modules.pop("shadow_tracer", None)
        if added:
            sys.path.remove(str(d))
    _LOADED[d] = tuple(mods)
    return _LOADED[d]


def store_dir(st) -> Path:
    override = os.environ.get("AIB_SEAT_TEST_STORE")
    return Path(override) if override else st.shadow_dir_default()


def _rows_file(root, st, ri) -> Path:
    return store_dir(st) / ("seat-%s.rows.jsonl" % (ri.repo_identity(root) or "unknown"))


def _append(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def try_reuse(root, test_rel, environ=None):
    """The previous green row when reuse_index says it may be reused on this tree, else None."""
    if not enabled(environ):
        return None
    mods = _verify(root)
    if mods is None:
        return None
    st, ri = mods
    try:
        idx = ri.build_index([str(store_dir(st))], repo=str(root))
        verdict, _reason, detail = idx.decide(str(Path(root).resolve()), test_rel, changed=[])
    except Exception:  # a broken index means "run it", never "skip it"
        return None
    if verdict != "reuse":
        return None
    row = dict(idx.row_for(test_rel, detail) or {})   # 被沿用的那一列,不一定是最新一列
    row["cross_tree"] = bool(row.get("repo")) and str(row.get("repo")) != str(Path(root).resolve())
    return row


def run(root, test_rel, timeout=600, environ=None):
    """Run the test (traced when possible). Returns (rc, output) and keeps the row for next time."""
    root = Path(root)
    mods = _verify(root) if enabled(environ) else None
    env = dict(os.environ if environ is None else environ)
    trace = None
    if mods is not None:
        st, _ri = mods
        fd, trace = tempfile.mkstemp(prefix="aib-seat-trace-", suffix=".jsonl")
        os.close(fd)
        env = st.child_env(root, Path(trace), env)
    t0 = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, str(root / test_rel)], cwd=str(root), env=env, text=True,
                              capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    finally:
        duration = round(time.monotonic() - t0, 2)
    output = (proc.stdout + proc.stderr).strip()
    if mods is not None:
        try:
            _record(root, test_rel, proc.returncode, output, duration, Path(trace), mods, environ)
        except Exception:
            pass                     # not measured ⇒ next time it simply runs again
        finally:
            try:
                os.unlink(trace)
            except OSError:
                pass
    return proc.returncode, output


def _record(root, test_rel, rc, output, duration, trace, mods, environ):
    st, ri = mods
    events = st.load_trace(trace)
    items, input_fp, unmeasured, external, why = st.inputs_from_events(events, repo=str(root))
    env_fp, _ = st.env_fingerprint(st.child_env(root, Path("/tmp/.ez2-verify-skip-probe"), environ), repo=root)
    row = {
        "test": test_rel, "rc": rc, "measured": True if not unmeasured else "unmeasured", "external": bool(external),
        "why": why, "inputs": items, "input_fp": input_fp, "env_fp": env_fp,
        "key": st.make_key(test_rel, input_fp, env_fp), "tail": output[-GREEN_TAIL_CHARS:],
        "duration_s": duration, "time": time.strftime("%Y-%m-%d %H:%M", time.localtime()), "source": "api-seat", "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _append(_rows_file(root, st, ri), ri.stamp_row(row, str(root)))


def compact(rc, output):
    """Green: the last few hundred characters are enough. Red: keep the long tail (the model needs the failure)."""
    if not output:
        return "沒有輸出"          # "no output"
    return output[-(GREEN_TAIL_CHARS if rc == 0 else FAIL_TAIL_CHARS):]
