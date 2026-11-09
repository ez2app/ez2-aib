#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI 的 Python 全套分份並行(S4,043dd 11-09;計畫 PLAN-release-tooling 9.2 第 1 項,a63ab 派)。

取代 ci.yml smoke 格裡那段「for f in aib/tests/test-*.py 逐支跑」+「unittest discover -s tests」:
同一台 runner 開 N 份同時跑,慢的先排;碰程序表 / 固定 port / 共用檔的測試留到最後一支一支跑(串行組)。
**普通實跑**:不讀沿用池、不追蹤、不寫池 —— 每支照 CI 原本的跑法(自己一個行程組、每支最多 600 秒、逾時整組殺),
只是每支多給一個全新 HOME + TMPDIR(跟 `aib test` 同一套 _own_home),並行時才不會互相踩到 ~/.ez2-aib。

結果要「逐名相等」才算過:每個根(aib/tests 的 test-*.py、倉根 tests/ 的 test*.py)跑到的名字集合
必須跟磁碟上列出來的一模一樣、每支剛好一次;漏、重複、多出來、中斷都算紅,不是只比總數。

  python3 aib/tools/verify/ci_shard.py --repo . [--jobs N] [--results out.json] [--durations-out d.json]
rc:0 全綠且集合相等 / 1 有紅、漏、重複、多出、或被中斷 / 2 用法錯。

串行組 = 檔名或內容符合 SERIAL_RE(程序表 / 固定 port 這類,規則算、不手抄)+ 檔頭 40 行有 `# aib: ci-serial`;
檔頭有 `# aib: ci-parallel-ok` ⇒ 規則誤判的那支可以退出串行組(要在 PR 寫為什麼)。
排序只用 aib/tools/verify/ci-durations.json 的秒數(沒有的當 DEFAULT_S),只影響先後,不影響紅綠。
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PER_TEST_TIMEOUT_S = 600
DEFAULT_S = 10.0
JOBS_MAX = 4
SERIAL_MARK = re.compile(r"^#\s*aib:\s*ci-serial\b", re.M)
PARALLEL_OK_MARK = re.compile(r"^#\s*aib:\s*ci-parallel-ok\b", re.M)
# 碰全機狀態的特徵:程序表(pgrep / ps / lsof / launchctl / tmux kill-server)、固定 port。寧可多列(只是慢一點),不可漏。
SERIAL_RE = re.compile(r"pgrep|pkill|killall|lsof|\[\"ps\"|'ps'|\bps -|launchctl|kill-server"
                       r"|(?:127\.0\.0\.1|localhost):[0-9]{4,5}")


def _load_test_entry():
    """沿用 aib test 的 _own_home(每支全新 HOME + TMPDIR、跑完收殘留)。只拿這一個,不碰它的池 / 追蹤。"""
    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("test_entry", HERE / "test_entry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect(repo):
    """[(名字, 根, 指令)];名字 = 相對倉根的路徑。aib/tests 照 ci.yml 原本的 glob,倉根 tests/ 照 unittest discover 的預設樣式。"""
    repo = Path(repo)
    out = []
    for p in sorted((repo / "aib" / "tests").glob("test-*.py")):
        out.append((p.relative_to(repo).as_posix(), "aib/tests", [sys.executable, str(p)]))
    for p in sorted((repo / "tests").glob("test*.py")):
        out.append((p.relative_to(repo).as_posix(), "tests",
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", p.name, "-v"]))
    return out


def expected(repo):
    """磁碟上每個根該有的名字(跟 collect 分開算 ⇒ collect 漏了 / 多了驗得出來)。"""
    repo = Path(repo)
    return {"aib/tests": sorted(p.relative_to(repo).as_posix() for p in (repo / "aib" / "tests").glob("test-*.py")),
            "tests": sorted(p.relative_to(repo).as_posix() for p in (repo / "tests").glob("test*.py"))}


def _head(path, n=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(next(fh, "") for _ in range(n))
    except OSError:
        return ""


def is_serial(path):
    head = _head(path)
    if PARALLEL_OK_MARK.search(head):
        return False
    if SERIAL_MARK.search(head):
        return True
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True               # 讀不到 ⇒ 保守:串行
    return bool(SERIAL_RE.search(text))


def load_durations(repo):
    try:
        d = json.loads((Path(repo) / "aib" / "tools" / "verify" / "ci-durations.json").read_text(encoding="utf-8"))
        return {k: float(v) for k, v in d.items() if isinstance(v, (int, float))}
    except (OSError, ValueError, AttributeError):
        return {}


def plan(repo, tests, durations):
    """(並行組, 串行組);兩組都慢的先(秒數一樣照名字),同一份輸入每次排出一樣的順序。"""
    key = lambda t: (-durations.get(t[0], DEFAULT_S), t[0])  # noqa: E731
    par = sorted((t for t in tests if not is_serial(Path(repo) / t[0])), key=key)
    ser = sorted((t for t in tests if is_serial(Path(repo) / t[0])), key=key)
    return par, ser


class Runner:
    def __init__(self, repo, te, timeout_s=PER_TEST_TIMEOUT_S, out=sys.stdout):
        self.repo = Path(repo)
        self.te = te
        self.timeout_s = timeout_s
        self.out = out
        self.lock = threading.Lock()
        self.results = []
        self.running = {}         # name -> Popen
        self.stopping = False
        self.t_start = time.monotonic()
        self.serial = []
        gc = Path(os.path.expanduser("~/.gitconfig"))
        self.gitconfig = str(gc) if gc.is_file() else None   # ci.yml 先設了全域 git 身分;換 HOME 之後沿用那份

    def run_one(self, test):
        name, root, cmd = test
        with tempfile.NamedTemporaryFile("w+", prefix="aib-ci-shard-", suffix=".log", delete=False) as log:
            log_path = log.name
        t0 = time.monotonic()
        rc, timed_out = None, False
        with self.te._own_home(self.repo, dict(os.environ), name) as env:
            if self.gitconfig:
                # 1ced7 #571 r2 N1:不設 GIT_CONFIG_GLOBAL —— 那是指到治具外的 GIT_* 路徑,影子追蹤器會判
                # untracked:git-env(test-shadow-reuse-holes g2 在 ubuntu 三格紅)。複製進這支自己的 HOME。
                try:
                    shutil.copyfile(self.gitconfig, os.path.join(env["HOME"], ".gitconfig"))
                except OSError:
                    pass
            with open(log_path, "w", encoding="utf-8") as fh:
                with self.lock:
                    if self.stopping:
                        stopped = True
                        p = None
                    else:
                        stopped = False
                        p = subprocess.Popen(cmd, cwd=str(self.repo), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, start_new_session=True)
                        self.running[name] = p
                if stopped:            # 收到中斷後才輪到的:不跑、不記(驗證會列 missing),記錄檔也收掉
                    try:
                        os.unlink(log_path)
                    except OSError:
                        pass
                    return None
                try:
                    rc = p.wait(timeout=self.timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _killpg(p)
                    rc = 124
                finally:
                    with self.lock:
                        self.running.pop(name, None)
        el = round(time.monotonic() - t0, 2)
        row = {"name": name, "root": root, "rc": rc, "elapsed_s": el, "timed_out": timed_out,
               "start_s": round(t0 - self.t_start, 2), "serial": test in self.serial}
        self._emit(row, log_path)
        with self.lock:
            self.results.append(row)
        return row

    def _emit(self, row, log_path):
        """跑完才整段印(並行時各支的輸出不交錯);格式照舊:::group:: 一支一段,紅的加 ::error::。"""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        finally:
            try:
                os.unlink(log_path)
            except OSError:
                pass
        with self.lock:
            self.out.write("::group::%s (%.1f s)\n%s" % (row["name"], row["elapsed_s"], text))
            if text and not text.endswith("\n"):
                self.out.write("\n")
            self.out.write("::endgroup::\n")
            if row["timed_out"]:
                self.out.write("::error::%s still running after %d s (killed with its children)\n"
                               % (row["name"], self.timeout_s))
            elif row["rc"] != 0:
                self.out.write("::error::%s failed or hung\n" % row["name"])
            self.out.flush()

    def run_parallel(self, tests, jobs):
        queue = list(tests)
        qlock = threading.Lock()

        def worker():
            while True:
                with qlock:
                    if self.stopping or not queue:
                        return
                    t = queue.pop(0)
                self.run_one(t)

        ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, jobs))]
        for th in ths:
            th.start()
        for th in ths:
            while th.is_alive():
                th.join(0.5)

    def run_serial(self, tests):
        self.serial.extend(tests)
        for t in tests:
            if self.stopping:
                return
            self.run_one(t)

    def stop(self):
        with self.lock:
            self.stopping = True
            procs = list(self.running.values())
        for p in procs:
            _killpg(p)


def _killpg(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def verify(exp, results, interrupted=False):
    """逐名相等:每個根跑到的名字集合 = 磁碟上的,每支剛好一次;再看 rc。回 (ok, 問題清單)。"""
    problems = []
    seen = {}
    for r in results:
        seen.setdefault(r["name"], []).append(r)
    for name, rows in sorted(seen.items()):
        if len(rows) > 1:
            problems.append("duplicate %s(跑了 %d 次)" % (name, len(rows)))
    for root, names in sorted(exp.items()):
        if not names:      # 1ced7 #571 r1 必修:空集合 = 空集合也會「逐名相等」;一個根 0 支 = 收集錯了(或 --repo 指錯)
            problems.append("empty %s(這個根一支測試都沒有)" % root)
        got = sorted({r["name"] for r in results if r.get("root") == root})
        miss = sorted(set(names) - set(got))
        extra = sorted(set(got) - set(names))
        if miss:
            problems.append("missing %s:%s" % (root, ", ".join(miss[:10]) + (" …" if len(miss) > 10 else "")))
        if extra:
            problems.append("unexpected %s:%s" % (root, ", ".join(extra[:10])))
    stray = sorted({r["name"] for r in results if r.get("root") not in exp})
    if stray:
        problems.append("unexpected root:%s" % ", ".join(stray[:10]))
    for r in sorted(results, key=lambda x: x["name"]):
        if type(r.get("rc")) is not int or r["rc"] != 0:
            problems.append("red %s(rc %s)" % (r["name"], r.get("rc")))
    if interrupted:
        problems.append("interrupted(被中斷,沒跑完)")
    return (not problems), problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="CI Python 全套分份並行(普通實跑,不讀池、不追蹤)")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--jobs", type=int, default=None, help="同時幾份;預設 min(4, 核心數)")
    ap.add_argument("--results", default=None, help="每支結果 + 驗證寫成 JSON")
    ap.add_argument("--durations-out", default=None, help="這次每支秒數(更新 ci-durations.json 用)")
    ap.add_argument("--timeout", type=int, default=PER_TEST_TIMEOUT_S)
    ap.add_argument("--list", action="store_true", help="只印排程(並行 / 串行),不跑")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    jobs = a.jobs if a.jobs else max(1, min(JOBS_MAX, os.cpu_count() or 1))
    tests = collect(repo)
    exp = expected(repo)
    par, ser = plan(repo, tests, load_durations(repo))
    if a.list:
        for t in par:
            print("parallel", t[0])
        for t in ser:
            print("serial", t[0])
        return 0
    print("ci-shard: %d 支(並行 %d、串行 %d),%d 份同時跑;每支最多 %d 秒" % (len(tests), len(par), len(ser), jobs, a.timeout))
    sys.stdout.flush()
    te = _load_test_entry()
    run = Runner(repo, te, timeout_s=a.timeout)
    interrupted = []

    def on_signal(signum, _frame):
        interrupted.append(signum)
        run.stop()

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, on_signal)
    t0 = time.monotonic()
    run.run_parallel(par, jobs)
    run.run_serial(ser)
    wall = round(time.monotonic() - t0, 1)
    ok, problems = verify(exp, run.results, interrupted=bool(interrupted))
    total = round(sum(r["elapsed_s"] for r in run.results), 1)
    print("ci-shard: 牆鐘 %.1f 秒;各支相加 %.1f 秒;%s" % (wall, total, "全綠、集合逐名相等" if ok else "沒過"))
    for root, names in sorted(exp.items()):
        got = len({r["name"] for r in run.results if r.get("root") == root})
        print("ci-shard: %s 跑到 %d / 應有 %d" % (root, got, len(names)))
    for p in problems:
        print("::error::ci-shard %s" % p)
    if a.results:
        Path(a.results).write_text(json.dumps({"ok": ok, "problems": problems, "wall_s": wall, "jobs": jobs,
                                               "results": sorted(run.results, key=lambda r: r["name"])},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    if a.durations_out:
        Path(a.durations_out).write_text(json.dumps({r["name"]: r["elapsed_s"] for r in run.results if r["rc"] == 0},
                                                    ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
