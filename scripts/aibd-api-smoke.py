#!/usr/bin/env python3
"""API smoke for a built aibd: start it, hit the three /api/v1 routes, stop only the process we started.

    python3 -I scripts/aibd-api-smoke.py PATH/TO/aibd[.exe] [--timeout S]

Same script on macOS and Windows (the release verify job, a63ab preview.2 prep, 043dd 11-08; based on the D1
scripts in pending/aib2-a63ab-1107/d1/). What it does:

1. Starts aibd with AIBD_BIND=127.0.0.1:0 (the OS picks a free port: no collision) and AIBD_DATA_DIR in a fresh
   temp dir (never the user's real data dir), stdout to a file (a pipe can be held open by a leftover child).
2. Reads aibd's start-up line {"listening": ip:port, "token_file": path}; listening must be 127.0.0.1 with a real
   port. run/aibd.json must name the pid we started (so what we stop is ours).
3. Checks, each on its own:
     health  no token -> 200          metrics / events  no token -> 401
     metrics / events  token -> 200   metrics  wrong token -> 401
     health  Host: evil.example -> refused (not 200; DNS rebinding guard)
4. Stops the process it started, by its handle (never by name or by a pid read later), waits for it, removes
   the temp dir.

Prints one JSON line with every result. rc: 0 all pass / 1 a check failed / 2 usage or the binary is missing.
Not checked: TLS (none; loopback only), Origin rules, events paging, long-running behaviour.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# never through a proxy: CI images can set http_proxy, and loopback must be asked directly
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

EXPECT = [
    ("health_noauth", "/api/v1/health", None, None, 200),
    ("metrics_noauth", "/api/v1/metrics", None, None, 401),
    ("events_noauth", "/api/v1/events", None, None, 401),
    ("metrics_auth", "/api/v1/metrics", "TOKEN", None, 200),
    ("events_auth", "/api/v1/events", "TOKEN", None, 200),
    ("metrics_wrong_token", "/api/v1/metrics", "smoke-wrong-token", None, 401),
]


def http_code(url, token=None, host=None, timeout=5.0):
    """Status code, or 0 when nothing answered (refused, timeout)."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if host:
        req.add_header("Host", host)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def first_json_line(path, proc, deadline):
    """aibd prints one JSON line when it listens; tracing goes to stderr."""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.endswith("\n"):
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(d, dict) and d.get("listening"):
                            return d
        except OSError:
            pass
        time.sleep(0.1)
    return None


def stop(proc, grace=5.0):
    """Stop the process we started (its handle, not a name). Polite first where the OS has a polite signal."""
    if proc.poll() is not None:
        return "already_exited"
    if os.name == "posix":
        import signal
        proc.send_signal(signal.SIGINT)      # aibd shuts down on ctrl-c
        try:
            proc.wait(timeout=grace)
            return "sigint"
        except subprocess.TimeoutExpired:
            pass
    proc.kill()                              # Windows: TerminateProcess on our handle
    proc.wait(timeout=grace)
    return "killed"


def tail(path, n=3):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [x.rstrip() for x in f.readlines()[-n:]]
    except OSError:
        return []


def smoke(exe, timeout):
    res = {"exe": os.path.basename(exe)}
    work = tempfile.mkdtemp(prefix="aibd-smoke-")
    out_path, err_path = os.path.join(work, "out.txt"), os.path.join(work, "err.txt")
    env = dict(os.environ, AIBD_BIND="127.0.0.1:0", AIBD_DATA_DIR=os.path.join(work, "data"))
    env.pop("AIBD_ALLOWED_ORIGINS", None)
    proc = None
    ok = False
    try:
        with open(out_path, "w") as out, open(err_path, "w") as err:
            proc = subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env)
        res["pid"] = proc.pid
        info = first_json_line(out_path, proc, time.monotonic() + timeout)
        res["started"] = bool(info)
        if not info:
            res["exit_code"] = proc.poll()
            res["err_tail"] = tail(err_path)
            return res, False
        listening = str(info.get("listening", ""))
        ip, _, port = listening.rpartition(":")
        res["listening"] = listening
        res["loopback"] = ip == "127.0.0.1" and port.isdigit() and int(port) > 0
        token = ""
        tf = info.get("token_file") or ""
        try:
            with open(tf, encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            res["token_error"] = type(e).__name__
        res["token_read"] = bool(token)
        try:
            with open(os.path.join(os.path.dirname(tf), "aibd.json"), encoding="utf-8") as f:
                res["run_info_pid_is_ours"] = json.load(f).get("pid") == proc.pid
        except (OSError, ValueError):
            res["run_info_pid_is_ours"] = False
        base = "http://" + listening
        passed = res["loopback"] and res["token_read"] and res["run_info_pid_is_ours"]
        for key, path, tok, host, want in EXPECT:
            got = http_code(base + path, token if tok == "TOKEN" else tok, host)
            res[key] = got
            passed = passed and got == want
        bad_host = http_code(base + "/api/v1/health", host="evil.example")
        res["health_bad_host"] = bad_host
        passed = passed and bad_host not in (0, 200)
        ok = passed
        return res, ok
    finally:
        if proc is not None:
            try:
                res["stopped"] = stop(proc)
            except (OSError, subprocess.TimeoutExpired) as e:
                res["stopped"] = "error:" + type(e).__name__
                ok = False
            res["exited"] = proc.poll() is not None
        if not ok:
            res.setdefault("err_tail", tail(err_path))
        shutil.rmtree(work, ignore_errors=True)
        res["cleaned"] = not os.path.exists(work)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    timeout = 20.0
    if "--timeout" in argv:
        i = argv.index("--timeout")
        try:
            timeout = float(argv[i + 1])
        except (IndexError, ValueError):
            print("aibd-api-smoke: --timeout needs seconds")
            return 2
        del argv[i:i + 2]
    if len(argv) != 1:
        print("usage: aibd-api-smoke.py PATH/TO/aibd [--timeout S]")
        return 2
    exe = os.path.abspath(argv[0])
    if not os.path.isfile(exe):
        print("aibd-api-smoke: no such file: %s" % exe)
        return 2
    res, ok = smoke(exe, timeout)
    ok = (ok and res.get("exited") is True and res.get("cleaned") is True
          and not str(res.get("stopped", "")).startswith("error"))
    res["pass"] = ok
    print(json.dumps(res, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
