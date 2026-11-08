#!/usr/bin/env python3
"""Sessions smoke for a built aibd: open a program that has a grandchild, DELETE it, check the whole tree is gone.

    python3 -I scripts/aibd-sessions-smoke.py PATH/TO/aibd[.exe] [--require] [--timeout S]

a63ab PLAN-preview2-usable-today (1ced7, 11-09). Same script on macOS and Windows (release build + verify jobs).

1. Starts aibd the same way as scripts/aibd-api-smoke.py: AIBD_BIND=127.0.0.1:0, AIBD_DATA_DIR in a fresh temp dir,
   reads the start-up line {"listening", "token_file"}.
2. POST /api/v1/sessions {"argv": [python, helper, marker], "cwd": tmp}. The helper starts one grandchild
   (python sleeping), writes "<child pid> <grandchild pid>" to the marker file, then sleeps.
   - 404 / 405 on POST = this aibd has no sessions API ⇒ SKIP (rc 0), unless --require (then rc 1).
3. GET /api/v1/sessions/{id} must answer 200 while it runs.
4. DELETE /api/v1/sessions/{id} must answer 2xx and must not itself report a survivor (state stop_unconfirmed,
   stop.status tree_not_clean or stop.survivors non-empty; a9136 CONTRACT-sessions-preview2 §1 + bb7dc H2); then every pid (session pid, child, grandchild) must be gone
   within the grace window. A survivor is a FAIL: the script kills it itself (only pids it learned from its own
   session) so nothing is left behind, and says so.
5. Stops the aibd it started (by handle), removes the temp dir.

Prints one JSON line. rc: 0 pass or skipped / 1 a check failed (or absent with --require) / 2 usage.
Not checked: env whitelist, argv limits, many sessions at once, session restart, PTY.
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

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WINDOWS = os.name == "nt"

HELPER = r'''
import subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
with open(sys.argv[1] + ".tmp", "w") as f:
    f.write("%d %d" % (__import__("os").getpid(), g.pid))
__import__("os").replace(sys.argv[1] + ".tmp", sys.argv[1])
time.sleep(300)
'''


def call(method, url, token=None, body=None, timeout=10.0):
    """-> (status, parsed JSON or None). status 0 = nothing answered."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except (urllib.error.URLError, OSError):
        return 0, None
    try:
        return code, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return code, None


def alive(pid):
    if not pid or pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers kill(0): treat "exited, not reaped yet" as gone
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return bool(out.strip()) and not out.strip().startswith("Z")
    except (OSError, subprocess.TimeoutExpired):
        return True


def kill_pid(pid):
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def first_json_line(path, proc, deadline):
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
    if proc.poll() is not None:
        return "already_exited"
    if os.name == "posix":
        import signal
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=grace)
            return "sigint"
        except subprocess.TimeoutExpired:
            pass
    proc.kill()
    proc.wait(timeout=grace)
    return "killed"


def wait_for(pred, seconds, step=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def smoke(exe, require, timeout):
    res = {"exe": os.path.basename(exe)}
    work = tempfile.mkdtemp(prefix="aibd-sessions-smoke-")
    out_path, err_path = os.path.join(work, "out.txt"), os.path.join(work, "err.txt")
    env = dict(os.environ, AIBD_BIND="127.0.0.1:0", AIBD_DATA_DIR=os.path.join(work, "data"))
    env.pop("AIBD_ALLOWED_ORIGINS", None)
    proc, ok, pids = None, False, []
    try:
        with open(out_path, "w") as out, open(err_path, "w") as err:
            proc = subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env)
        info = first_json_line(out_path, proc, time.monotonic() + timeout)
        if not info:
            res["started"] = False
            return res, False
        res["started"] = True
        base = "http://" + str(info.get("listening"))
        try:
            with open(info.get("token_file") or "", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            token = ""
        res["token_read"] = bool(token)
        helper = os.path.join(work, "helper.py")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(HELPER)
        marker = os.path.join(work, "pids.txt")
        code, body = call("POST", base + "/api/v1/sessions", token,
                          {"argv": [os.path.abspath(sys.executable), helper, marker], "cwd": work})
        res["post"] = code
        if code in (404, 405):
            res["sessions"] = "absent"
            res["skipped"] = not require
            return res, not require
        res["sessions"] = "present"
        sid = (body or {}).get("id")
        spid = (body or {}).get("pid")
        res["session_id"], res["session_pid"] = sid, spid
        if code not in (200, 201) or not sid:
            return res, False
        res["marker"] = wait_for(lambda: os.path.exists(marker), timeout)
        if res["marker"]:
            with open(marker, encoding="utf-8") as f:
                child, grand = (int(x) for x in f.read().split())
        else:
            child = grand = 0
        pids = []                            # session pid may be the child itself (no wrapper): dedupe
        for p in (spid, child, grand):
            if isinstance(p, int) and p > 0 and p not in pids:
                pids.append(p)
        res["grandchild_pid"] = grand
        res["alive_before"] = {str(p): alive(p) for p in pids}
        code, body = call("GET", base + "/api/v1/sessions/%s" % sid, token)
        res["get_running"] = code
        res["state_running"] = (body or {}).get("state") or (body or {}).get("status")
        code, body = call("DELETE", base + "/api/v1/sessions/%s" % sid, token, timeout=30.0)
        res["delete"] = code
        # contract (a9136 CONTRACT-sessions-preview2 §1, bb7dc H2): DELETE answers after the kill with the
        # Session; state stop_unconfirmed or stop.status tree_not_clean = aibd itself says something survived
        res["delete_state"] = (body or {}).get("state")
        stop_info = (body or {}).get("stop") if isinstance((body or {}).get("stop"), dict) else {}
        res["stop_status"] = stop_info.get("status")
        res["stop_survivors"] = stop_info.get("survivors")
        aibd_says_left = res["delete_state"] == "stop_unconfirmed" or res["stop_status"] == "tree_not_clean" \
            or bool(res["stop_survivors"])
        gone = wait_for(lambda: not any(alive(p) for p in pids), 10.0)
        res["survivors"] = [p for p in pids if alive(p)]
        code, body = call("GET", base + "/api/v1/sessions/%s" % sid, token)
        res["get_after"] = code
        res["state_after"] = (body or {}).get("state") or (body or {}).get("status")
        ok = (res["token_read"] and res["marker"] and grand > 0 and grand != child and all(res["alive_before"].values()) and res["get_running"] == 200
              and 200 <= res["delete"] < 300 and gone
              and not aibd_says_left)
        return res, ok
    finally:
        left = [p for p in pids if alive(p)]
        for p in left:                       # only pids learned from our own session
            kill_pid(p)
        if left:
            res["killed_leftovers"] = left
        if proc is not None:
            try:
                res["stopped"] = stop(proc)
            except (OSError, subprocess.TimeoutExpired) as e:
                res["stopped"] = "error:" + type(e).__name__
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    require = "--require" in argv
    argv = [a for a in argv if a != "--require"]
    timeout = 20.0
    if "--timeout" in argv:
        i = argv.index("--timeout")
        try:
            timeout = float(argv[i + 1])
        except (IndexError, ValueError):
            print("aibd-sessions-smoke: --timeout needs seconds")
            return 2
        del argv[i:i + 2]
    if len(argv) != 1:
        print("usage: aibd-sessions-smoke.py PATH/TO/aibd [--require] [--timeout S]")
        return 2
    exe = os.path.abspath(argv[0])
    if not os.path.isfile(exe):
        print("aibd-sessions-smoke: no such file: %s" % exe)
        return 2
    res, ok = smoke(exe, require, timeout)
    res["pass"] = bool(ok)
    print(("SKIP sessions API absent " if res.get("skipped") else "") + json.dumps(res, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
