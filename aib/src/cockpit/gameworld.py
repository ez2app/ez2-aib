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
網址進紀錄 / 提示前只留 scheme://host:port/(將來網址帶配對資料也不會被寫出去)。

去重:鍵 = (請求 ID, 目標艙),紀錄夾 <艙家>/cockpit/gameworld-req/,每鍵一個檔:
  沒有檔 → O_CREAT|O_EXCL 建 <鍵>.claim.1(建成功的那個才准叫外掛 / 開窗)→ 成功 rename 成 <鍵>.done;失敗刪領取檔。
  最高一代卡住(領取超過 STALE_S 秒或擁有者 pid 已不在)⇒ O_EXCL 建下一代(同一代只有一個人建成),
  記 gameworld.stat.reclaimed。做不到 exactly once:開完、rename 成 .done 之前崩潰 ⇒ STALE_S 秒後同請求重試會多開一次。
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
STALE_S = 15            # 外掛逾時 10 秒 + 開窗餘裕
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


# ── 同鍵原子領取 ─────────────────────────────────────────────────────────
def _key(req, cabin):
    return hashlib.sha256(("%s\0%s" % (req, cabin)).encode("utf-8")).hexdigest()[:32]


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


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
        if not (n.endswith(".done") or ".stale." in n):
            continue
        try:
            m = os.stat(os.path.join(str(d), n)).st_mtime
        except OSError:
            continue
        olds.append((m, n))
    olds.sort()
    extra = max(0, len(olds) - KEEP_N)
    for i, (m, n) in enumerate(olds):
        if i < extra or now - m > KEEP_S:
            try:
                os.unlink(os.path.join(str(d), n))
            except OSError:
                pass


def _gens(d, k):
    out = []
    try:
        names = os.listdir(str(d))
    except OSError:
        return out
    for n in names:
        if n.startswith(k + ".claim."):
            try:
                out.append(int(n.rsplit(".", 1)[1]))
            except ValueError:
                pass
    return sorted(out)


def claim(d, req, cabin, now=time.time, alive=_pid_alive, pause=None):
    """回領取檔路徑(拿到了)或 None(已成功過 / 別人正在做)。pause:測試用障礙,在建檔成功後呼叫。

    世代號:領取檔 <鍵>.claim.<N>,誰持有看最高的 N。第一次建 N=1;最高那個卡住(超過 STALE_S 秒,或擁有者 pid 已不在)
    ⇒ 建 N+1。每一代都是 O_CREAT|O_EXCL,同一代只會有一個人建成;接手不搬別人的檔,所以不會誤拿走別人剛建的新領取。"""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    k = _key(req, cabin)
    done = d / (k + ".done")
    _gc(d, now())
    for _ in range(5):
        if done.exists():
            return None
        gens = _gens(d, k)
        top = gens[-1] if gens else 0
        if top:
            cur = d / ("%s.claim.%d" % (k, top))
            try:
                st = os.stat(str(cur))
                info = json.loads(cur.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                continue                                  # 正在變動:重看一次
            pid = info.get("pid") if isinstance(info, dict) else None
            at = info.get("at") if isinstance(info, dict) else None
            age = now() - float(at if isinstance(at, (int, float)) else st.st_mtime)
            # 剛建、內容還沒寫進去(沒有 pid)只看年紀,不當成擁有者已死
            if age < STALE_S and (pid is None or alive(pid)):
                return None
        nxt = d / ("%s.claim.%d" % (k, top + 1))
        try:
            fd = os.open(str(nxt), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue                                      # 這一代被別人建了:重看一次
        except OSError:
            return None
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"pid": os.getpid(), "at": now()}))
        if top:
            _bump(d, "reclaimed")
            for g in gens:
                try:
                    os.unlink(str(d / ("%s.claim.%d" % (k, g))))
                except OSError:
                    pass
        if pause:
            pause()
        return nxt
    return None


def finish(cl, ok):
    if cl is None:
        return
    cl = Path(cl)
    try:
        if ok:
            os.rename(str(cl), str(cl.with_name(cl.name.split(".claim.")[0] + ".done")))
        else:
            os.unlink(str(cl))
    except OSError:
        pass


# ── 叫外掛、驗結果 ────────────────────────────────────────────────────────
def call_plugin(cabin, switch, run=subprocess.run):
    """回 dict:{ok, result, cabin(驗過的實際艙或 None), url(完整,只拿來開窗), why}。"""
    argv = [aib_ui._aib_bin(), "room", "open", "--no-browser"] + (["--switch", cabin] if switch else [])
    try:
        r = run(argv, capture_output=True, text=True, timeout=PLUGIN_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "result": "timeout", "cabin": None, "url": None, "why": "core-timeout"}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "result": "error", "cabin": None, "url": None, "why": "spawn"}
    try:
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        if not isinstance(d, dict):
            raise ValueError
    except (ValueError, IndexError):
        return {"ok": False, "result": "error", "cabin": None, "url": None, "why": "bad-json"}
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
        out.write("AIB_GAMEWORLD result=dedup cabin=%s\n" % (target or "-"))
        return "dedup"
    r = call_plugin(target, switch, run)
    o = origin_only(r.get("url"))
    actual = r["cabin"] or T("unknown_cabin")
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
