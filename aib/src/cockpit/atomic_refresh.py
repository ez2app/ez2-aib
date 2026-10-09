# SPDX-License-Identifier: Apache-2.0
"""背景快取更新的共用一段:暫存檔寫完再 os.replace 蓋上去,失敗不碰舊快取,鎖一定收。

★ 10-03 為什麼有這支:右欄三個背景更新(機器狀態列、離艙盤點、廣場摘要)原本都是
  `Popen(["/bin/sh", "-c", "(python3 X > tmp && mv -f tmp path || rm -f tmp); rm -f lock"])`。
  行為對,但 /bin/sh 那一層在影子續跑的追蹤器裡看不進去(sh 沒有被追蹤),
  於是凡是畫右欄的測試都被判成「量不到」。改成子行程本身就是 python、自己寫自己換:
  語意逐條對照原本那行 sh ——
    · 產出寫到呼叫端算好的 tmp(`<path>.<面板 pid>`,命名不變);
    · 產出成功(rc 0)才把 tmp 換上 path(原子,同 mv -f 同一檔案系統);
    · 產出失敗或換不上去 ⇒ 刪 tmp,舊快取原封不動;
    · 不論成敗,最後一定刪鎖。
  啟動端仍是 Popen 不等它、同一個 session(不加 start_new_session,理由見 machine_status_row)。
"""
import os
import subprocess
import sys


def _rm(p):
    if not p:
        return
    try:
        os.remove(p)
    except OSError:
        pass


def produce_to(path, tmp, write, lock=None, after=None):
    """write(fh) 回 0 表示成功。回這一次的 rc(0 成功,1 失敗)。

    after():換上去之後才做的事(例如踢右欄);它失敗不影響已經換好的快取。
    """
    try:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                rc = write(fh)
            if rc not in (0, None):
                raise RuntimeError("produce rc=%r" % (rc,))
            os.replace(tmp, path)
        except BaseException:          # noqa: BLE001 任何失敗都等同 sh 那條 `|| rm -f tmp`
            _rm(tmp)
            return 1
        if after is not None:
            try:
                after()
            except Exception:          # noqa: BLE001
                pass
        return 0
    finally:
        _rm(lock)


def spawn(argv, lock):
    """背景起 argv(python 子行程,自己負責寫檔與收鎖)。不等它、不看 rc。

    ★ 不用 start_new_session:見 machine_status_row.spawn_refresh 的註解(ppid 鏈)。
    起不來(OSError)就當場把鎖收掉,不讓下一輪被一把沒人拿的鎖擋 REFRESH_LOCK_S 秒。
    """
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        _rm(lock)
        return False
    return True


def python_argv(script, *args):
    return [sys.executable, script] + [str(a) for a in args]
