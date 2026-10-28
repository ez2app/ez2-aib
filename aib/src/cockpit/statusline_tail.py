#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""💧 狀態列尾巴:mod 的狀態段接在 CTX 那一行後面,不再各佔一行(10-28 Andy 工作單 S)。

Andy 10-28:「這個 aib 太多行了,我希望縮成一行」⇒ 要的樣子
  Opus 5.5 | @aeb40 | 💧 Ez2 CTX 10% [💧─────────] 100k/1M 未讀 dm 5 · 🌐 zh-TW

誰寫、誰讀:
  - mod(aib-boards、aib-languages)在跑的時候呼叫 `put`,把自己那一段(已照語系翻好)連時間寫進
    <狀態根>/state/statusline-tail/<對話 id>.<mod>.json。**沒裝的 mod 從來不寫** ⇒ 狀態列自然不帶它那段;
    mod 停了,段過期(TTL_S)就消失。判準不看本機路徑、不看 settings.json 裝了什麼。
  - Claude 狀態列(statusline-ez2-v215.sh → seat_now.py --statusline --tail-field)用 `tail_parts` 讀新鮮的段,
    照 ORDER 接起來畫,並用 `mark_shown` 記下「這個對話的 Ez2 狀態列畫出過哪幾個 mod 的段」。
  - mod 的 `put` 回 {"shown": true} = 自己那段**已經畫進尾巴過** ⇒ mod 收掉自己的 $.ui.status 那一行;false(會員用自己的
    狀態列、Ez2 狀態列還沒跑過、或還沒畫到含這段的那一次)⇒ mod 照舊自己一行,資訊不會因為併行而整個不見。
  - #401 R1(@aeb40):記號**黏住、不看時間**——對話內畫過一次就不翻回(會員沒設 refreshInterval、閒置很久,
    狀態列不重跑也不會讓 mod 那行又跳回來、跟凍住的尾巴兩個數字並存)。只有狀態列換掉才失效:記號裡存指紋,put 時再算一次
    對不上 ⇒ shown=false。
  - #401 R2(@aeb40 定範圍,同類照這個):指紋 = 使用者層 settings.json + 這個對話 cwd 的專案層
    (.claude/settings.json、.claude/settings.local.json)的 statusLine + 實際腳本檔(statusline-ez2-v215.sh)的大小與 mtime;
    外加保險期限:SHOWN_MAX_S(24 小時)沒畫過 ⇒ 翻回(畫著的對話每 SHOWN_TOUCH_S 刷一次記號時間)。

鍵用 Claude 的對話 id:狀態列輸入有 session_id,mod 有 $.session.id(),兩邊同一個值;席碼 languages 不知道。

  statusline_tail.py put --mod aib-boards --session SID --text "未讀 dm 5"   ⇒ {"ok": true, "shown": bool}
  statusline_tail.py put --mod aib-boards --session SID --clear              ⇒ 拿掉這段
  statusline_tail.py show --session SID                                      ⇒ 印接好的尾巴(除錯用)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SUBDIR = os.path.join("state", "statusline-tail")
ORDER = ("aib-boards", "aib-languages")     # 尾巴的順序:未讀 dm(+交接標記)在前、語系在後;不在表上的 mod 不收
TTL_S = 300                                  # 段多久沒更新就當 mod 不在了(boards 60 秒刷一次、languages 60 秒心跳)
TEXT_MAX = 60                                # 一段最多幾個字
SHOWN_MAX_S = 24 * 3600                      # 記號多久沒畫過就翻回(保險;#401 R2)
SHOWN_TOUCH_S = 3600                         # 畫著的對話多久刷一次記號時間(狀態列重跑很頻繁,不必每次寫)
SCRIPT_NAME = "statusline-ez2-v215.sh"
SID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
JOIN = " · "


def tail_dir(state_root: str | None = None) -> str:
    if state_root is None:
        import seats_dir_lib
        state_root = seats_dir_lib.state_root()[0]
    return os.path.join(state_root, SUBDIR)


def clean(text) -> str:
    """外部字串進狀態列:去控制字元與欄位分隔(\\x1f)、殼的取代字元(& \\)、換行,收成一行,最多 TEXT_MAX 字。"""
    if not isinstance(text, str):
        return ""
    t = "".join(ch for ch in text if ch.isprintable() and ch not in "\x1f&\\")
    return " ".join(t.split())[:TEXT_MAX]


def _write(path: str, obj: dict) -> None:
    import atomic_json
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    atomic_json.write_json(path, obj)


def _seg_path(sid: str, mod: str, state_root: str | None) -> str:
    return os.path.join(tail_dir(state_root), "%s.%s.json" % (sid, mod))


def _shown_path(sid: str, state_root: str | None) -> str:
    return os.path.join(tail_dir(state_root), "%s.shown" % sid)


def _config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def _statusline_of(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d.get("statusLine") if isinstance(d, dict) else None


def statusline_fingerprint(cwd: str | None = None, *, config_dir: str | None = None) -> str:
    """狀態列設定 + 腳本檔的指紋(#401 R2 範圍):使用者層、cwd 專案層兩份的 statusLine,加腳本檔大小 / mtime。
    任何一樣換掉 ⇒ 指紋變 ⇒ 記號失效。讀不到的那份記成 null(移除也算換)。"""
    base = config_dir or _config_dir()
    cwd = cwd or os.getcwd()
    layers = [_statusline_of(os.path.join(base, "settings.json"))]
    for name in ("settings.json", "settings.local.json"):
        layers.append(_statusline_of(os.path.join(cwd, ".claude", name)))
    try:
        st = os.stat(os.path.join(base, SCRIPT_NAME))
        script = [st.st_size, st.st_mtime_ns]
    except OSError:
        script = None
    import hashlib
    blob = json.dumps({"layers": layers, "script": script}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _read_shown(sid: str, state_root: str | None) -> dict:
    try:
        with open(_shown_path(sid, state_root), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    ok = isinstance(d, dict) and isinstance(d.get("mods"), list) and isinstance(d.get("cfg"), str) \
        and isinstance(d.get("at"), (int, float))
    return d if ok else {}


def is_shown(sid: str, mod: str, *, state_root: str | None = None, cwd: str | None = None,
             config_dir: str | None = None, now: float | None = None) -> bool:
    """這個對話的 Ez2 狀態列畫出過 mod 那段、狀態列(設定 + 腳本)沒換過、而且 SHOWN_MAX_S 內畫過(#401 R1 / R2)。"""
    if not SID_RE.fullmatch(sid or "") or mod not in ORDER:
        return False
    d = _read_shown(sid, state_root)
    if not d or mod not in d["mods"]:
        return False
    if (now if now is not None else time.time()) - d["at"] > SHOWN_MAX_S:
        return False
    return d["cfg"] == statusline_fingerprint(cwd, config_dir=config_dir)


def put(mod: str, sid: str, text: str | None, *, state_root: str | None = None, now: float | None = None,
        cwd: str | None = None, config_dir: str | None = None) -> dict:
    """mod 寫自己那一段(text=None ⇒ 拿掉)。回 {"ok", "shown"};寫不進去不拋例外(狀態列的事不能讓 mod 掛掉)。"""
    if mod not in ORDER:
        return {"ok": False, "reason": "unknown_mod", "shown": False}
    if not SID_RE.fullmatch(sid or ""):
        return {"ok": False, "reason": "bad_session", "shown": False}
    path = _seg_path(sid, mod, state_root)
    try:
        if text is None or not clean(text):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        else:
            _write(path, {"mod": mod, "session": sid, "text": clean(text),
                          "at": int(now if now is not None else time.time())})
    except (OSError, ValueError) as e:
        return {"ok": False, "reason": "write_failed:%s" % type(e).__name__,
                "shown": is_shown(sid, mod, state_root=state_root, cwd=cwd, config_dir=config_dir, now=now)}
    return {"ok": True, "shown": is_shown(sid, mod, state_root=state_root, cwd=cwd, config_dir=config_dir, now=now)}


def tail_parts(sid: str, *, state_root: str | None = None, now: float | None = None) -> list:
    """[(mod, 段)](照 ORDER、只收 TTL_S 內的段);讀壞、過期、不是這個對話的一律跳過。"""
    if not SID_RE.fullmatch(sid or ""):
        return []
    t_now = now if now is not None else time.time()
    parts = []
    for mod in ORDER:
        try:
            with open(_seg_path(sid, mod, state_root), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("session") != sid or d.get("mod") != mod:
            continue
        at = d.get("at")
        if not isinstance(at, (int, float)) or t_now - at > TTL_S or at - t_now > 60:
            continue
        text = clean(d.get("text"))
        if text:
            parts.append((mod, text))
    return parts


def tail_text(sid: str, *, state_root: str | None = None, now: float | None = None) -> str:
    """接好的尾巴;沒有就空字串。"""
    return JOIN.join(t for _, t in tail_parts(sid, state_root=state_root, now=now))


def mark_shown(sid: str, mods, *, state_root: str | None = None, cwd: str | None = None,
               config_dir: str | None = None, now: float | None = None) -> bool:
    """記下「這次畫出了 mods 的段」(跟之前畫過的取聯集;指紋變了就重來)。沒有新東西、記號時間也還新就不寫。"""
    mods = [m for m in mods if m in ORDER]
    if not SID_RE.fullmatch(sid or "") or not mods:
        return False
    at = int(now if now is not None else time.time())
    cfg = statusline_fingerprint(cwd, config_dir=config_dir)
    d = _read_shown(sid, state_root)
    have = [m for m in d.get("mods", []) if m in ORDER] if d.get("cfg") == cfg else []
    if set(mods) <= set(have) and at - d.get("at", 0) < SHOWN_TOUCH_S:
        return False
    try:
        _write(_shown_path(sid, state_root),
               {"cfg": cfg, "mods": [m for m in ORDER if m in set(have) | set(mods)], "at": at})
    except (OSError, ValueError):
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="statusline_tail.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("put")
    p.add_argument("--mod", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--cwd", default=None)   # 這個對話的 cwd(專案層設定在這裡;#401 R2);沒給 ⇒ 行程自己的 cwd
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--clear", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--session", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "put":
        r = put(a.mod, a.session, None if a.clear else a.text, cwd=a.cwd)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    print(tail_text(a.session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
