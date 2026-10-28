#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""💧 狀態列尾巴:mod 的狀態段接在 CTX 那一行後面,不再各佔一行(10-28 Andy 工作單 S)。

Andy 10-28:「這個 aib 太多行了,我希望縮成一行」⇒ 要的樣子
  Opus 5.5 | @aeb40 | 💧 Ez2 CTX 10% [💧─────────] 100k/1M 未讀 dm 5 · 🌐 zh-TW

誰寫、誰讀:
  - mod(aib-boards、aib-languages)在跑的時候呼叫 `put`,把自己那一段(已照語系翻好)連時間寫進
    <狀態根>/state/statusline-tail/<對話 id>.<mod>.json。**沒裝的 mod 從來不寫** ⇒ 狀態列自然不帶它那段;
    mod 停了,段過期(TTL_S)就消失。判準不看本機路徑、不看 settings.json 裝了什麼。
  - Claude 狀態列(statusline-ez2-v215.sh → seat_now.py --statusline --tail-field)用 `tail_text` 讀新鮮的段,
    照 ORDER 接起來,並用 `mark_shown` 留「這個對話的 Ez2 狀態列有在畫」記號(最多 SHOWN_EVERY_S 寫一次)。
  - mod 的 `put` 回 {"shown": true} = 記號還新 ⇒ mod 收掉自己的 $.ui.status 那一行;false(會員用自己的狀態列、
    或 Ez2 狀態列還沒跑過)⇒ mod 照舊自己一行,資訊不會因為併行而整個不見。

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
SHOWN_EVERY_S = 30                           # 「有在畫」記號最多多久寫一次(狀態列每次重跑都會問)
SHOWN_TTL_S = 180                            # 記號多新才算「Ez2 狀態列有在畫」
TEXT_MAX = 60                                # 一段最多幾個字
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


def is_shown(sid: str, *, state_root: str | None = None, now: float | None = None) -> bool:
    if not SID_RE.fullmatch(sid or ""):
        return False
    try:
        age = (now if now is not None else time.time()) - os.path.getmtime(_shown_path(sid, state_root))
    except OSError:
        return False
    return age < SHOWN_TTL_S


def put(mod: str, sid: str, text: str | None, *, state_root: str | None = None, now: float | None = None) -> dict:
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
                "shown": is_shown(sid, state_root=state_root, now=now)}
    return {"ok": True, "shown": is_shown(sid, state_root=state_root, now=now)}


def tail_text(sid: str, *, state_root: str | None = None, now: float | None = None) -> str:
    """接好的尾巴(照 ORDER、只收 TTL_S 內的段);沒有就空字串。讀壞、過期、不是這個對話的一律跳過。"""
    if not SID_RE.fullmatch(sid or ""):
        return ""
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
            parts.append(text)
    return JOIN.join(parts)


def mark_shown(sid: str, *, state_root: str | None = None, now: float | None = None) -> bool:
    """「這個對話的 Ez2 狀態列有在畫」:SHOWN_EVERY_S 內寫過就不再寫(狀態列重跑很頻繁)。寫不進去就算了。"""
    if not SID_RE.fullmatch(sid or ""):
        return False
    at = int(now if now is not None else time.time())
    path = _shown_path(sid, state_root)
    try:
        if at - os.path.getmtime(path) < SHOWN_EVERY_S:
            return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % at)
        os.utime(path, (at, at))
    except OSError:
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="statusline_tail.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("put")
    p.add_argument("--mod", required=True)
    p.add_argument("--session", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--clear", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--session", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "put":
        r = put(a.mod, a.session, None if a.clear else a.text)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    print(tail_text(a.session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
