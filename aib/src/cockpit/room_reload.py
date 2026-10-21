#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib update 之後讓還在跑舊碼的艦長室自己換新碼重開(@abb89 10-21 派工 ②;伺服器端 aib-plugins #82)。

    python3 room_reload.py [--json]

做法(@74e8b q_5b96fb1020b16e1e 的叫法):
  1. 讀 <狀態根>/state/captain-room/server.json 的 url / token / room_version / pid。
  2. 沒在跑(沒檔 / pid 不在)⇒ 什麼都不做。
  3. room_version 跟磁碟上艦長室外掛 plugin.json 的 version 一樣 ⇒ 不用重開。
  4. 不一樣(或舊伺服器沒帶 room_version)⇒ POST {url}api/reload,header X-Captain-Token,body {"why":"aib update"},不帶 Origin。
     200 ⇒ 已重開;409 / 404 / 連不上 ⇒ 印一行請本人關掉艦長室再開。
規矩:核心**不 kill** 艦長室(真艙不准寬 pkill);token 只放 header,不印、不寫 log;url 只接 http://127.0.0.1 / localhost。
結束碼一律 0(更新本身成功了,重開失敗只是提示)。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
TIMEOUT_S = 5
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def T(key, **params):
    try:
        import i18n_lib
        v = i18n_lib.t("aib.room_reload." + key, params or None, locale=i18n_lib.resolve_locale())
        if v and not v.startswith("!!"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return key


def server_json_path(env=None):
    import seats_dir_lib
    return Path(seats_dir_lib.state_root(env)[0]) / "state" / "captain-room" / "server.json"


def read_server(env=None):
    try:
        d = json.loads(server_json_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)              # 0 = 只問在不在,不送訊號
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def plugins_dir(env=None):
    """跟 tools/verify/aib_plugins._home 同一個規則(EZ2_AIB_HOME 或 ~/.ez2-aib),只是吃傳進來的 env。"""
    e = os.environ if env is None else env
    return Path(e.get("EZ2_AIB_HOME") or (Path(e.get("HOME") or Path.home()) / ".ez2-aib")) / "plugins"


def disk_version(env=None):
    """裝在 plugins 底下的艦長室外掛(commands 有 room 的那個)plugin.json 的 version;找不到 ⇒ None。"""
    base = plugins_dir(env)
    try:
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return None
    for d in dirs:
        try:
            m = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and isinstance(m.get("commands"), dict) and isinstance(m["commands"].get("room"), str):
            v = m.get("version")
            return v if isinstance(v, str) and v else None
    return None


def _local_url(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme == "http" and (u.hostname or "") in LOCAL_HOSTS and not u.username


def post_reload(url, token, timeout=TIMEOUT_S):
    """回 HTTP 狀態碼;連不上 ⇒ 0。"""
    req = urllib.request.Request(url.rstrip("/") + "/api/reload", data=json.dumps({"why": "aib update"}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Captain-Token": token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def reload_room(env=None, post=None, alive=None):
    """回 {"result": not-running|same|reloaded|failed|no-plugin, ...}。"""
    srv = read_server(env)
    if not srv or not (alive or pid_alive)(srv.get("pid")):
        return {"result": "not-running"}
    disk = disk_version(env)
    if disk is None:
        return {"result": "no-plugin"}
    running = srv.get("room_version") or ""
    if running == disk:
        return {"result": "same", "version": disk}
    url, token = srv.get("url"), srv.get("token")
    if not isinstance(url, str) or not _local_url(url) or not isinstance(token, str) or not token:
        return {"result": "failed", "why": "bad-server-json", "from": running, "to": disk}
    code = (post or post_reload)(url, token)
    if code == 200:
        return {"result": "reloaded", "from": running, "to": disk}
    return {"result": "failed", "why": "http-%d" % code if code else "unreachable", "from": running, "to": disk}


def main(argv=None, out=None, env=None, **kw):
    out = out or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)
    res = reload_room(env, **kw)
    if "--json" in argv:
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
    elif res["result"] == "reloaded":
        out.write(T("reloaded", old=res["from"] or "?", new=res["to"]) + "\n")
    elif res["result"] == "failed":
        out.write(T("failed", old=res["from"] or "?", new=res["to"]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
