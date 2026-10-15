# SPDX-License-Identifier: Apache-2.0
"""子行程看門狗:測試起的子行程失控(無限再入、fork 炸彈)時只死它自己那一組,不拖垮整台機器。

10-15 18:4x 事故(@abb89 事故通報 19:02):刀測讓 dm-ctl.sh 無限再入,兩個沙盒各 fork 上千支,
整台 Pro 行程表滿(2650/2666)——別席 fork 失敗、維護者右欄 AI 欄掛、整合分支全套後半 rc 127。

為什麼不用 `ulimit -u`(RLIMIT_NPROC):macOS 上它算的是**整個使用者**的行程總數,
平常就有三百多支,設 200 會讓子行程一支都開不出來;設高了又擋不住。⇒ 改看「這一組」:
子行程開在自己的 session(start_new_session),看門狗每 0.2 秒數同一個 process group 的行程,
超過 max_procs 或逾時 ⇒ killpg(SIGKILL) 整組。

    r = run_guarded([sys.executable, test_file, cell], cwd=root, max_procs=150, timeout=300)
    r.returncode / r.stdout / r.stderr / r.tripped(看門狗動手了沒、為什麼)
"""
import os
import signal
import subprocess
import time


class GuardResult(object):
    def __init__(self, returncode, stdout, stderr, tripped, peak, pgid):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.pgid = pgid            # 子行程那一組的 process group id(跑完應該已經是 0 支)
        self.tripped = tripped      # None = 沒動手;"procs" = 行程數超標;"timeout" = 逾時
        self.peak = peak            # 這一組最多同時幾支


def group_size(pgid):
    """這個 process group 現在有幾支(一次 ps;數不到回 0)。"""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pgid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    want = str(pgid)
    return sum(1 for x in out.split() if x == want)


def _kill_group(p, pgid, rounds=50, gap=0.05):
    """killpg 到那一組一支不剩為止。
    10-15 22:11 CI(macOS 3.9)G1 量到 31.4 秒:killpg 那一刻正在 fork 的子行程會漏殺一支 `sleep 30`,
    它抱著 stdout 管線,communicate() 就陪它睡滿 30 秒(本機舊版 30 輪重現 1 次 30.4 秒)
    ⇒ 砍一次不夠,要砍到清空(最多 rounds 輪)。
    macOS:組裡只剩沒收屍的組長時 killpg 回 EPERM ⇒ 這輪當沒砍到,p.poll() 收掉組長再數。"""
    for _ in range(rounds):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        p.poll()
        time.sleep(gap)
        if not group_size(pgid):
            return


def run_guarded(cmd, max_procs=150, timeout=300, poll=0.2, **kw):
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("text", True)
    p = subprocess.Popen(cmd, start_new_session=True, **kw)
    pgid, tripped, peak, t0 = p.pid, None, 0, time.time()
    while p.poll() is None:
        n = group_size(pgid)
        peak = max(peak, n)
        if n > max_procs:
            tripped = "procs"
        elif time.time() - t0 > timeout:
            tripped = "timeout"
        if tripped:
            _kill_group(p, pgid)
            break
        time.sleep(poll)
    out, err = p.communicate()
    # 組長先結束、子孫還活著(孤兒)也要清:同一組剩下的一律收掉
    if group_size(pgid):
        _kill_group(p, pgid)
    return GuardResult(p.returncode, out, err, tripped, peak, pgid)
