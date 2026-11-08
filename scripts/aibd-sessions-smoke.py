#!/usr/bin/env python3
"""Sessions smoke for a built aibd: open a program that has a grandchild, DELETE it, check the whole tree is gone.

    python3 -I scripts/aibd-sessions-smoke.py PATH/TO/aibd[.exe] [--require] [--timeout S]

a63ab PLAN-preview2-usable-today (1ced7, 11-09). Same script on macOS and Windows (release build + verify jobs).

1. Starts aibd the same way as scripts/aibd-api-smoke.py: AIBD_BIND=127.0.0.1:0, AIBD_DATA_DIR in a fresh temp dir,
   reads the start-up line {"listening", "token_file"}.
2. POST /api/v1/sessions {"argv": [python, helper, marker, stopfile], "cwd": tmp} must answer 201 with an id and a pid.
   The helper starts one grandchild, writes "<child pid> <grandchild pid>" to the marker file, then both wait.
   - 404 / 405 on POST = this aibd has no sessions API ⇒ SKIP (rc 0), unless --require (then rc 1).
3. GET /api/v1/sessions/{id} must answer 200 with state "running".
4. DELETE /api/v1/sessions/{id} must answer 2xx with a stop result the contract allows as "done"
   (a9136 CONTRACT-sessions-preview2 + a63ab 11-09 ruling on #539 M2 = B):
   - Windows: state "killed" and stop.status "clean";
   - macOS / POSIX: that, or state "stop_unconfirmed" with stop.status "unverified" and reason "observed_clean";
   - always: stop.survivors empty, and every pid (session pid, child, grandchild) gone from outside within 10 s.
   Anything else is a FAIL (tree_not_clean, a survivor, another unverified reason, "killed" without "clean").
5. Clean-up never sends a signal to a pid (bb7dc #537 High 1: a bare pid may have been reused). The helper and the
   grandchild poll a stop file and exit by themselves once it exists (and after 600 s at the latest); the script
   creates it, waits for them, and reports any that are still there as `leftovers_running`.
6. "Gone" needs proof (bb7dc #537 High 2): alive() answers alive / gone / unknown; unknown is never counted as gone.
7. Stops the aibd it started (by its handle), removes the temp dir.

Prints one JSON line. rc: 0 pass or skipped / 1 a check failed (or absent with --require) / 2 usage.
Not checked: env whitelist, argv limits, many sessions at once, session restart, PTY.
UNVERIFIED: the Windows branches of alive() and of the rule in 4 have not run on Windows yet.
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
import os, subprocess, sys, time
marker, stop = sys.argv[1], sys.argv[2]
WAIT = "import os,sys,time\nt=time.time()\nwhile not os.path.exists(sys.argv[1]) and time.time()-t<600: time.sleep(0.2)"
g = subprocess.Popen([sys.executable, "-c", WAIT, stop])
with open(marker + ".tmp", "w") as f:
    f.write("%d %d" % (os.getpid(), g.pid))
os.replace(marker + ".tmp", marker)
t = time.time()
while not os.path.exists(stop) and time.time() - t < 600:
    time.sleep(0.2)
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


ALIVE, GONE, UNKNOWN = "alive", "gone", "unknown"


def _alive_windows(pid):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE          # pointer width: a c_int default truncates the handle on x64
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    h = k.OpenProcess(0x1000, False, int(pid))       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        err = ctypes.get_last_error()
        if err == 87:                                # ERROR_INVALID_PARAMETER: no such process
            return GONE
        if err == 5:                                 # ERROR_ACCESS_DENIED: it exists
            return ALIVE
        return UNKNOWN
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return UNKNOWN
        return ALIVE if code.value == 259 else GONE  # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def alive(pid):
    """alive / gone / unknown. Only clear evidence is "gone"; a failed check is "unknown", never "gone"."""
    if not isinstance(pid, int) or pid <= 0:
        return UNKNOWN
    if WINDOWS:
        try:
            return _alive_windows(pid)
        except (OSError, AttributeError, ValueError):
            return UNKNOWN
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return GONE
    except PermissionError:
        return ALIVE                                 # exists, owned by someone else
    except OSError:
        return UNKNOWN
    # kill(0) succeeded: it exists, unless it is a zombie (exited, not reaped yet) — only ps can say so
    try:
        p = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ALIVE
    st = p.stdout.strip()
    if p.returncode == 0 and st.startswith("Z"):
        return GONE
    return ALIVE                                     # ps failed / empty / not a zombie: kill(0) said it exists


def done_ok(state, status, reason, survivors):
    """Does the DELETE answer say "done" the way the contract allows on this OS? (see 4. above)"""
    if survivors:
        return False
    if state == "killed" and status == "clean":
        return True
    return (not WINDOWS) and state == "stop_unconfirmed" and status == "unverified" and reason == "observed_clean"


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
    stopfile = os.path.join(work, "stop")
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
                          {"argv": [os.path.abspath(sys.executable), helper, marker, stopfile], "cwd": work})
        res["post"] = code
        if code in (404, 405):
            res["sessions"] = "absent"
            res["skipped"] = not require
            return res, not require
        res["sessions"] = "present"
        sid = (body or {}).get("id")
        spid = (body or {}).get("pid")
        res["session_id"], res["session_pid"] = sid, spid
        if code != 201 or not isinstance(sid, str) or not sid or not isinstance(spid, int):
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
        res["alive_before"] = {str(p): alive(p) for p in pids}
        res["grandchild_pid"] = grand
        code, body = call("GET", base + "/api/v1/sessions/%s" % sid, token)
        res["get_running"] = code
        res["state_running"] = (body or {}).get("state") or (body or {}).get("status")
        code, body = call("DELETE", base + "/api/v1/sessions/%s" % sid, token, timeout=30.0)
        res["delete"] = code
        # contract §1 + #539 M2 = B: DELETE answers after the kill with the Session and its stop result
        res["delete_state"] = (body or {}).get("state")
        stop_info = (body or {}).get("stop") if isinstance((body or {}).get("stop"), dict) else {}
        res["stop_status"] = stop_info.get("status")
        res["stop_reason"] = stop_info.get("reason")
        res["stop_survivors"] = stop_info.get("survivors")
        res["done_ok"] = done_ok(res["delete_state"], res["stop_status"], res["stop_reason"],
                                 res["stop_survivors"] if isinstance(res["stop_survivors"], list) else ["?"])
        gone = wait_for(lambda: all(alive(p) == GONE for p in pids), 10.0)
        after = {p: alive(p) for p in pids}
        res["survivors"] = [p for p, a in after.items() if a == ALIVE]
        res["unknown_after"] = [p for p, a in after.items() if a == UNKNOWN]
        code, body = call("GET", base + "/api/v1/sessions/%s" % sid, token)
        res["get_after"] = code
        res["state_after"] = (body or {}).get("state") or (body or {}).get("status")
        ok = (res["token_read"] and res["marker"] and grand > 0 and grand != child
              and all(a == ALIVE for a in res["alive_before"].values())
              and res["get_running"] == 200 and res["state_running"] == "running"
              and 200 <= res["delete"] < 300 and res["done_ok"] and gone)
        return res, ok
    finally:
        # never signal a bare pid (it may be someone else's by now): the helper and grandchild exit on the stop file
        try:
            open(stopfile, "w").close()
        except OSError:
            pass
        if pids:
            wait_for(lambda: all(alive(p) == GONE for p in pids), 5.0)
            left = [p for p in pids if alive(p) != GONE]
            if left:
                res["leftovers_running"] = left
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
