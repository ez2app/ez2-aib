#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib <ai> <dgrs|safe> <角色> [艙] gameworld`:開席後進遊戲化大廳(設計稿 DESIGN-gameworld v0.3)。

兩個入口(都由 src/cockpit/aib 呼叫,開席 rc 一律不受影響):
  gameworld.py check                       前景:外掛有沒有裝 / 夠不夠新 / 有沒有 --switch;印一行人話。
                                            rc 0 = 照做;rc 3 = 這次不做大廳(沒裝 / 太舊)。
  gameworld.py open --cabin <四碼> --req <請求 ID> --home <艙家> [--notify-session <S>]
                                            背景:同鍵原子領取 → 叫外掛一次 → 驗結果 → 開視窗 / 提示。

核心只叫外掛的公開指令 `aib room open --no-browser [--switch <四碼>]`,不讀外掛權杖、不打外掛 HTTP。
外掛結果(一行 JSON):{"ok", "result": started|already|switched|not_live|timeout|error, "cabin", "url", "elapsed_ms"}。
只有 ok 且 result 是成功三種且 cabin == 目標,才算「已切到」;其他一律不這樣說。
**未知**(11-08,#369 bb7dc 修法意見 4):外掛回 post_unconfirmed:true(送過 POST 但沒讀回確認)、核心 12 秒硬逾時、
JSON 壞(外掛可能已 POST)⇒ result=unknown:不報成功、不開窗、不當普通失敗;寫 <鍵>.unknown,同鍵**不自動重試**
(之後同鍵 ⇒ unknown_kept);人再打一次指令是新請求 ID、新鍵,照常做。<鍵>.unknown 跟 .done 一樣 24 小時 / 32 筆後收。
網址進紀錄 / 提示前只留 scheme://host:port/(將來網址帶配對資料也不會被寫出去)。

去重:鍵 = (請求 ID, 目標艙),紀錄夾 <艙家>/cockpit/gameworld-req/,每鍵一個鎖檔 <鍵>.lock:
  open(O_CREAT) + flock(LOCK_EX|LOCK_NB)(Windows:LockFileEx),拿著直到叫完外掛、開完窗、寫好 <鍵>.done 才放。
  拿不到鎖 = 擁有者還活著 ⇒ dedup,不管多久(活著但慢 ≠ 已死;#486 審查 P1);擁有者死了(含 SIGKILL)作業系統自動放鎖,
  下一個人照常拿、記 gameworld.stat.reclaimed。不用逾時、pid、啟動時間判死活,「建檔到寫 pid」的空檔也不存在。
  拿到鎖後再看一次 .done(檢查到拿鎖之間前一位做完了就不做),並比對 inode(拿到已被刪的舊檔就重來)。
  做不到 exactly once:開完、寫 .done 之前崩潰 ⇒ 之後同請求重試會多開一次。卡住不退的活程序會一直佔著這個鍵,
  但外掛有 12 秒硬逾時,而且鍵含請求 ID,人再打一次指令是新鍵,不會被擋。
  鎖檔要在本機檔案系統(艙家都在本機);拿不到鎖的檔案系統一律當「別人拿著」⇒ 不開大廳,不會重複開。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import aib_ui  # noqa: E402

CODE4 = re.compile(r"^[0-9a-f]{4}$")
OK_RESULTS = ("started", "already", "switched")
PLUGIN_TIMEOUT_S = 12   # 核心這端的硬逾時(外掛自己 10 秒)
KEEP_S = 24 * 3600
KEEP_N = 32


def T(key, **params):
    import i18n_lib
    return i18n_lib.t("aib.gameworld." + key, params, locale=i18n_lib.resolve_locale())


def origin_only(url):
    """只留 http://127.0.0.1:<埠>/;不是這個形狀 ⇒ None(不開窗、不記)。"""
    m = re.match(r"^(http://127\.0\.0\.1:[0-9]{1,5})(?:[/?#]|$)", url or "")
    return (m.group(1) + "/") if m else None


# ── 能力快取:外掛版本 + 入口檔身份(版本沒變但換了執行檔也重查)──────────────
def plugin_ident(find=None):
    """回 (身份字串, 腳本路徑) 或 (None, None)=沒裝 / 沒授權。"""
    try:
        if find is None:
            sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "verify"))
            import aib_plugins
            find = aib_plugins.find_command
        script, name, _why = find("room")
    except Exception:  # noqa: BLE001
        return None, None
    if script is None:
        return None, None
    try:
        st = os.stat(str(script))
    except OSError:
        return None, None
    ver = ""
    try:
        man = json.loads((Path(script).parent / "plugin.json").read_text(encoding="utf-8"))
        ver = str(man.get("version") or "")
    except (OSError, ValueError, AttributeError):
        pass
    return "%s|%s|%d|%d|%d" % (ver, os.path.realpath(str(script)), st.st_ino, st.st_size, st.st_mtime_ns), script


def capabilities(env=None, run=subprocess.run, find=None):
    """回 {"installed", "no_browser", "switch"};--help 結果按身份快取在 <狀態根>/captain-room/gameworld-cap.json。"""
    ident, _script = plugin_ident(find)
    if ident is None:
        return {"installed": False, "no_browser": False, "switch": False}
    cache = aib_ui.state_root(env) / "captain-room" / "gameworld-cap.json"
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("ident") == ident and isinstance(d.get("caps"), dict):
            return dict(d["caps"], installed=True)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        r = run([aib_ui._aib_bin(), "room", "open", "--help"], capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL)
        h = (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return {"installed": True, "no_browser": False, "switch": False}   # 查不到不快取,下次再查
    caps = {"no_browser": "--no-browser" in h, "switch": "--switch" in h}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps({"ident": ident, "caps": caps}), encoding="utf-8")
        os.replace(str(tmp), str(cache))
    except OSError:
        pass
    return dict(caps, installed=True)


def check(out=None, env=None, run=subprocess.run, find=None):
    out = sys.stdout if out is None else out
    caps = capabilities(env, run, find)
    if not caps["installed"]:
        out.write(T("missing") + "\n")
        return 3
    if not caps["no_browser"]:
        out.write(T("too_old") + "\n")
        return 3
    if not caps["switch"]:
        out.write(T("no_switch") + "\n")
    return 0


# ── 同鍵領取:作業系統持有的鎖 ──────────────────────────────────────────────
def _key(req, cabin):
    return hashlib.sha256(("%s\0%s" % (req, cabin)).encode("utf-8")).hexdigest()[:32]


def _try_lock(fd):
    """不等待地拿獨佔鎖:拿到 True;別人拿著(或這台機器的檔案系統不給鎖)False。
    鎖掛在開啟的檔上,程序結束(含 SIGKILL / 當機)由作業系統釋放,不靠逾時判死活。"""
    try:
        import fcntl
    except ImportError:                                   # Windows:LockFileEx(msvcrt 包的那一層)
        try:
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except (ImportError, OSError):
            return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class Claim:
    """拿著鎖的領取。fd 不給子程序繼承(Python 預設),外掛 / 視窗子程序不會把鎖帶走。"""

    def __init__(self, lock, done, fd):
        self.lock, self.done, self.fd = lock, done, fd

    def __fspath__(self):
        return str(self.lock)


def _bump(d, name):
    f = Path(d).parent / ("gameworld.stat." + name)
    try:
        n = int(f.read_text().strip() or "0")
    except (OSError, ValueError):
        n = 0
    try:
        f.write_text(str(n + 1))
    except OSError:
        pass


def _gc(d, now):
    try:
        names = sorted(os.listdir(str(d)))
    except OSError:
        return
    olds = []
    for n in names:
        if not (n.endswith(".done") or n.endswith(".lock") or n.endswith(".unknown")):
            continue
        try:
            m = os.stat(os.path.join(str(d), n)).st_mtime
        except OSError:
            continue
        olds.append((m, n))
    olds.sort()
    extra = max(0, len(olds) - KEEP_N)
    for i, (m, n) in enumerate(olds):
        if not (i < extra or now - m > KEEP_S):
            continue
        p = os.path.join(str(d), n)
        if n.endswith(".done") or n.endswith(".unknown"):
            try:
                os.unlink(p)
            except OSError:
                pass
            continue
        # 舊鎖檔:自己拿到鎖才刪(有人拿著 = 正在做,不動);拿鎖的人之後會比對 inode,刪掉的舊檔不算數
        try:
            fd = os.open(p, os.O_RDWR)
        except OSError:
            continue
        try:
            if _try_lock(fd):
                os.unlink(p)
        except OSError:
            pass
        finally:
            os.close(fd)


def claim(d, req, cabin, now=time.time, pause=None):
    """回 Claim(拿到了,做完要 finish)或 None(已成功過 / 別人正拿著)。

    每鍵一個鎖檔 <鍵>.lock:open(O_CREAT) + flock(LOCK_EX|LOCK_NB),一路拿到 finish。
    - 拿不到鎖 = 擁有者還活著(作業系統保證),不管它多慢都不接手;它死了鎖自動放掉,下一個人照常拿。
    - 拿到鎖後再看一次 .done:檢查 → 拿鎖之間前一位做完了,就不再做(#486 審查 P1 的空檔)。
    - 拿到鎖後比對 inode:拿到的是已被刪掉 / 換掉的舊檔就重來,不會兩人各拿一個檔。
    - 鎖檔裡留著上一位的 pid:有內容 = 上一位沒走到 finish(崩潰)⇒ gameworld.stat.reclaimed +1。
    pause(stage):測試用障礙,stage = "checked"(看過 .done、還沒開鎖檔)/ "opened"(開了鎖檔、還沒拿鎖)/ "held"(拿到鎖、還沒叫外掛)。"""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    k = _key(req, cabin)
    done, lk = d / (k + ".done"), d / (k + ".lock")
    _gc(d, now())
    for _ in range(5):
        if _settled(done):
            return None
        if pause:
            pause("checked")
        try:
            fd = os.open(str(lk), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return None
        if pause:
            pause("opened")
        if not _try_lock(fd):
            os.close(fd)
            return None                                   # 有人拿著:活著就不接手,不管多久
        try:
            same = os.fstat(fd).st_ino == os.stat(str(lk)).st_ino
        except OSError:
            same = False
        if not same:
            os.close(fd)
            continue                                      # 拿到的是舊檔(已被收尾刪掉):重來
        if _settled(done):
            os.close(fd)
            return None                                   # 空檔裡前一位做完了(或留下未知)
        try:
            prev = os.read(fd, 256)
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "at": now()}).encode("utf-8"))
        except OSError:
            prev = b""
        if prev.strip():
            _bump(d, "reclaimed")
        if pause:
            pause("held")
        return Claim(lk, done, fd)
    return None


def _unknown(done):
    return Path(str(done)[:-len(".done")] + ".unknown")


def _settled(done):
    """這個鍵已經有結論:成功(.done)或未知(.unknown)⇒ 都不再自動做。"""
    return done.exists() or _unknown(done).exists()


def finish(cl, ok, unknown=False):
    """成功:先寫 .done 再放鎖(放鎖前寫好,後來的人拿到鎖一定看得到);失敗:不寫 .done,同請求可重試。
    unknown:寫 <鍵>.unknown(不寫 .done)⇒ 同鍵不再自動重試,也不算成功。
    鎖檔在放鎖前刪掉(Windows 刪不掉就留著,_gc 之後收);刪掉後還拿著舊 fd 的人會在 inode 比對時重來。"""
    if cl is None or cl.fd is None:
        return                                            # 已收過(例外路徑再叫一次也安全)
    try:
        if ok:
            os.close(os.open(str(cl.done), os.O_CREAT | os.O_WRONLY, 0o600))
        elif unknown:
            os.close(os.open(str(_unknown(cl.done)), os.O_CREAT | os.O_WRONLY, 0o600))
        try:
            os.unlink(str(cl.lock))
        except OSError:
            pass
    finally:
        fd, cl.fd = cl.fd, None
        os.close(fd)


# ── 叫外掛、驗結果 ────────────────────────────────────────────────────────
def call_plugin(cabin, switch, run=subprocess.run):
    """回 dict:{ok, result, cabin(驗過的實際艙或 None), url(完整,只拿來開窗), why}。"""
    argv = [aib_ui._aib_bin(), "room", "open", "--no-browser"] + (["--switch", cabin] if switch else [])
    try:
        r = run(argv, capture_output=True, text=True, timeout=PLUGIN_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        # 外掛可能已經送了 POST(副作用收不回)⇒ 切艙模式下是「未知」,不是普通逾時
        return {"ok": False, "result": "unknown" if switch else "timeout", "cabin": None, "url": None, "why": "core-timeout"}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "result": "error", "cabin": None, "url": None, "why": "spawn"}
    try:
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        if not isinstance(d, dict):
            raise ValueError
    except (ValueError, IndexError):
        return {"ok": False, "result": "unknown" if switch else "error", "cabin": None, "url": None, "why": "bad-json"}
    got = d.get("cabin")
    got = got if isinstance(got, str) and CODE4.match(got) else None
    url = d.get("url") if isinstance(d.get("url"), str) else None
    res = d.get("result") if isinstance(d.get("result"), str) else ""
    if not switch:
        # 舊外掛:沒有結果碼、沒有實際艙(room status 只有 config,不算)⇒ 實際艙未知
        ok = d.get("ok") is True and r.returncode == 0 and origin_only(url) is not None
        return {"ok": ok, "result": "legacy" if ok else "error", "cabin": None, "url": url,
                "why": "" if ok else "legacy-failed"}
    if r.returncode != 0 and d.get("ok") is True:
        got = None                                        # 結束碼說失敗、JSON 說成功:自相矛盾,它說的艙不採信
    if (r.returncode != 0 or d.get("ok") is not True or res not in OK_RESULTS) and d.get("post_unconfirmed") is True:
        return {"ok": False, "result": "unknown", "cabin": got, "url": url, "why": "post-unconfirmed"}
    if r.returncode != 0 or d.get("ok") is not True or res not in OK_RESULTS:
        return {"ok": False, "result": res if res in ("not_live", "timeout", "error") else "error",
                "cabin": got, "url": url, "why": "plugin"}
    if got != cabin:
        return {"ok": False, "result": "mismatch", "cabin": got, "url": url, "why": "cabin-mismatch"}
    if origin_only(url) is None:
        return {"ok": False, "result": "error", "cabin": got, "url": None, "why": "bad-url"}
    return {"ok": True, "result": res, "cabin": got, "url": url, "why": ""}


def notify(session, msg, env=None, run=subprocess.run):
    if not session:
        return
    e = dict(os.environ if env is None else env)
    e.pop("TMUX", None)
    try:
        run([e.get("AIB_TMUX_BIN") or "tmux", "display-message", "-d", "8000", "-t", session, msg],
            env=e, capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def open_lobby(cabin, req, home, session="", out=None, env=None, run=subprocess.run, popen=subprocess.Popen,
               find=None, pause=None):
    """背景收尾段呼叫。回結果碼字串;開席 rc 不看它。"""
    out = sys.stdout if out is None else out
    e = os.environ if env is None else env
    caps = capabilities(env, run, find)
    if not caps["installed"] or not caps["no_browser"]:
        out.write("AIB_GAMEWORLD result=unavailable\n")
        return "unavailable"
    target = cabin if CODE4.match(cabin or "") else ""
    switch = bool(caps["switch"] and target)
    cl = claim(Path(home) / "cockpit" / "gameworld-req", req, target or "-", pause=pause)
    if cl is None:
        if _unknown(Path(home) / "cockpit" / "gameworld-req" / (_key(req, target or "-") + ".done")).exists():
            out.write("AIB_GAMEWORLD result=unknown_kept cabin=%s\n" % (target or "-"))
            return "unknown_kept"                         # 同鍵上次結果未知:不自動重試(要再試 = 人重打指令 = 新鍵)
        out.write("AIB_GAMEWORLD result=dedup cabin=%s\n" % (target or "-"))
        return "dedup"
    try:
        return _with_claim(cl, target, switch, session, out, e, env, run, popen)
    except BaseException:
        finish(cl, False)
        raise


def _with_claim(cl, target, switch, session, out, e, env, run, popen):
    """拿著鎖做外部副作用(叫外掛、開窗);鎖由作業系統持有到 finish,中間不用再驗。"""
    r = call_plugin(target, switch, run)
    o = origin_only(r.get("url"))
    actual = r["cabin"] or T("unknown_cabin")
    if r["result"] == "unknown":                          # 可能已切、沒確認:不開窗、不報成功、同鍵不自動重試
        finish(cl, False, unknown=True)
        out.write("AIB_GAMEWORLD result=unknown cabin=%s actual=%s how=- url=- why=%s\n"
                  % (target or "-", r["cabin"] or "unknown", r["why"]))
        notify(session, T("switch_unknown", target=target or "-"), env, run)
        return "unknown"
    how = ""
    if r["ok"] and o:
        if e.get("AIB_NO_BROWSER") == "1":
            how = "skipped"
        else:
            how, _ = aib_ui.open_app_window(r["url"], env, popen=popen, run=run)
    opened = how in ("app", "browser", "skipped")
    finish(cl, opened)
    out.write("AIB_GAMEWORLD result=%s cabin=%s actual=%s how=%s url=%s\n"
              % (r["result"], target or "-", r["cabin"] or "unknown", how or "-", o or "-"))
    if r["ok"] and opened:
        if r["result"] == "legacy" or not switch:
            notify(session, T("legacy_opened"), env, run)
        return r["result"]
    if r["ok"] and not opened:
        notify(session, T("open_failed", url=o or "-"), env, run)
        return "open_failed"
    if r["result"] in ("legacy", "error") and r["why"] in ("spawn", "bad-json", "legacy-failed", "bad-url"):
        notify(session, T("start_failed"), env, run)
    else:
        notify(session, T("not_switched", target=target or "-", actual=actual), env, run)
    return r["result"]


def main(argv=None, out=None):
    import argparse
    p = argparse.ArgumentParser(prog="gameworld.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    o = sub.add_parser("open")
    o.add_argument("--cabin", default="")
    o.add_argument("--req", required=True)
    o.add_argument("--home", required=True)
    o.add_argument("--notify-session", default="")
    a = p.parse_args(argv)
    if a.cmd == "check":
        return check(out)
    open_lobby(a.cabin, a.req, a.home, a.notify_session, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
