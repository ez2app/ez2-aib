#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Telegram 互串第一步(開發文件第 39 節;Andy 10-19「接 Telegram」)。只用標準庫。

- 機器人金鑰:`aib secret ask TELEGRAM_BOT_TOKEN`(遮字框 + Touch ID)。只有這支讀,不進 argv / log / 錯誤訊息。
- 收:Bot API getUpdates 長輪詢(不開對外 webhook)。私聊文字 ⇒ 排進艦長席佇列,**建立時就待審**
  (hold_human,原因 im_room):只在艦長室看、不進 AI 框(第二步的 Telegram 外框做好前不准放行)。
  一律 sender_kind = external_im(資料,不是 human);封鎖的丟掉;限量每人每分鐘 20、全部 60。
- 寄:只寄給傳過訊息、沒封鎖的人;要本人證明(Mac Touch ID)。Telegram 沒有已讀回條 ⇒ 只回 sent / failed。
- Telegram 機器人私訊不是端對端加密(Telegram 看得到內容);機密走 Nostr。
- 本機狀態 telegram.json(開關、輪詢位置、聯絡人)只記帳,不是防線:同帳號 AI 讀得到金鑰、改得到這個檔
  (第 39.5 節照實寫)。
- 測試走假伺服器:AIB_TELEGRAM_API 只收 http://127.0.0.1 / localhost(不讓設定把金鑰送到別的主機)。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "bootstrap")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dm_global_files as GF  # noqa: E402
from atomic_json import write_json  # noqa: E402

API_DEFAULT = "https://api.telegram.org"
LOCAL_API_RE = re.compile(r"^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$")
STATE_FILE = "telegram.json"
SECRET_NAME = "TELEGRAM_BOT_TOKEN"
TOKEN_RE = re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,64}$")
POLL_TIMEOUT_S = 25
TEXT_MAX = 4000
RATE_USER_PER_MIN = 20
RATE_ALL_PER_MIN = 60
SEAT_RE = re.compile(r"^[0-9a-z]{5}$")
UID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
RELATIONS = ("friend", "blocked", "stranger")


class TgError(Exception):
    """Telegram 呼叫失敗;訊息只帶方法名與代碼,**永不帶金鑰或網址**。"""

    def __init__(self, method, code):
        self.method, self.code = method, code
        super().__init__("%s:%s" % (method, code))


def api_base(env):
    v = str((env or {}).get("AIB_TELEGRAM_API") or "").rstrip("/")
    return v if LOCAL_API_RE.fullmatch(v) else API_DEFAULT


class Api(object):
    def __init__(self, token, base=API_DEFAULT):
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise TgError("init", "bad_token")
        self._token, self._base = token, base

    def call(self, method, payload=None, timeout=10):
        url = "%s/bot%s/%s" % (self._base, self._token, method)
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 —— 只會是 api.telegram.org 或本機假伺服器
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise TgError(method, "http_%d" % err.code) from None
        except (urllib.error.URLError, OSError, ValueError) as err:
            raise TgError(method, type(err).__name__) from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise TgError(method, "not_ok")
        return body.get("result")


def load_token(env=None):
    import aib_secrets as S
    return S.secret(SECRET_NAME, env=env)


# ── 本機狀態(只記帳)──
def state_path(root):
    return Path(root) / STATE_FILE


def load_state(root):
    try:
        st = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = None
    if not isinstance(st, dict):
        st = {}
    st.setdefault("v", 1)
    st.setdefault("on", False)
    st.setdefault("seat", None)
    st.setdefault("offset", 0)
    if not isinstance(st.get("contacts"), dict):
        st["contacts"] = {}
    return st


def save_state(root, st):
    Path(root).mkdir(parents=True, exist_ok=True)
    write_json(str(state_path(root)), st)
    try:
        os.chmod(str(state_path(root)), 0o600)
    except OSError:
        pass


def _clean(text, n):
    return "".join(ch for ch in str(text or "") if ch.isprintable())[:n].strip()


def shown_name(user):
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if isinstance(x, str) and x)
    name = _clean(name, 48) or (("@" + _clean(user.get("username"), 32)) if user.get("username") else "")
    return name or "Telegram 使用者"


def audit(root, direction, peer, result):
    """一則一行:時間、方向、對象(telegram:<id>)、結果。**不記內容。**"""
    d = Path(root) / "audit"
    try:
        d.mkdir(mode=0o700, exist_ok=True)
        line = json.dumps({"v": 1, "at": int(time.time()), "dir": "tg-" + direction, "peer": peer,
                           "result": result}, ensure_ascii=False) + "\n"
        fd = os.open(str(d / (time.strftime("%Y%m%d") + ".jsonl")), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _rate_ok(st, uid, now):
    win = [t for t in (st.get("rate_all") or []) if now - t < 60]
    mine = [t for t in ((st["contacts"].get(uid) or {}).get("rate") or []) if now - t < 60]
    st["rate_all"] = win
    if len(win) >= RATE_ALL_PER_MIN or len(mine) >= RATE_USER_PER_MIN:
        return False
    st["rate_all"] = win + [now]
    st["contacts"].setdefault(uid, {})["rate"] = mine + [now]
    return True


# ── 收 ──
def handle_update(root, st, upd, env=None, now=None):
    """一筆 update ⇒ 結果碼(queued_room / skip:* / blocked / rate / enqueue:*)。只收私聊文字。"""
    import dm_queue as DQ
    now = int(time.time() if now is None else now)
    msg = upd.get("message") if isinstance(upd, dict) else None
    if not isinstance(msg, dict):
        return "skip:not_message"
    chat, user = msg.get("chat") or {}, msg.get("from") or {}
    if chat.get("type") != "private" or not isinstance(user, dict) or user.get("is_bot"):
        return "skip:not_private"
    uid = str(user.get("id") or "")
    if not UID_RE.fullmatch(uid) or str(chat.get("id")) != uid:
        return "skip:bad_id"
    peer = "telegram:" + uid
    text = msg.get("text")
    if not isinstance(text, str) or not text.strip():
        audit(root, "in", peer, "skip:not_text")
        return "skip:not_text"
    c = st["contacts"].setdefault(uid, {})
    rel = c.get("relation") if c.get("relation") in RELATIONS else "stranger"
    if rel == "blocked":
        audit(root, "in", peer, "blocked")
        return "blocked"
    if not _rate_ok(st, uid, now):
        audit(root, "in", peer, "rate")
        return "rate"
    c.update({"relation": rel, "shown": shown_name(user), "seen_at": now})
    seat = st.get("seat")
    if not (isinstance(seat, str) and SEAT_RE.fullmatch(seat)):
        audit(root, "in", peer, "no_seat")
        return "skip:no_seat"
    try:
        item = DQ.enqueue(seat, text[:TEXT_MAX], "Telegram:" + c["shown"], sender_kind="external_im",
                          sender_origin=peer, env=env, now=now, status=DQ.HOLD_STATUS, hold_why=DQ.IM_ROOM_WHY,
                          im_info={"net": "telegram", "id": uid, "shown": c["shown"], "relation": rel})
    except (ValueError, OSError) as err:
        audit(root, "in", peer, "enqueue:" + type(err).__name__)
        return "enqueue:" + type(err).__name__
    audit(root, "in", peer, "queued_room")
    return "queued_room:" + item["id"]


def poll_once(root, api, env=None, timeout=POLL_TIMEOUT_S, now=None):
    """拉一輪 getUpdates;每筆照 handle_update;offset 往前推並存檔。回結果清單。"""
    st = load_state(root)
    if not st.get("on"):
        return []
    ups = api.call("getUpdates", {"offset": int(st.get("offset") or 0), "timeout": int(timeout),
                                  "allowed_updates": ["message"]}, timeout=timeout + 10) or []
    out = []
    for upd in ups if isinstance(ups, list) else []:
        try:
            uid = int(upd.get("update_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        out.append(handle_update(root, st, upd, env=env, now=now))
        st["offset"] = max(int(st.get("offset") or 0), uid + 1)
    st2 = load_state(root)                       # 輪詢等很久;這段期間被 off / allow / block 改過的照新的
    st2.update({"offset": st["offset"], "rate_all": st.get("rate_all") or []})
    for uid, c in st["contacts"].items():
        cur = st2["contacts"].setdefault(uid, {})
        for k in ("shown", "seen_at", "rate"):
            if k in c:
                cur[k] = c[k]
        cur.setdefault("relation", c.get("relation") or "stranger")
    save_state(root, st2)
    return out


# ── 寄 ──
def send(root, uid, text, api, now=None):
    """寄一則給傳過訊息、沒封鎖的人。回 {ok, stage, detail}。呼叫端先做本人證明。"""
    uid = str(uid or "")
    st = load_state(root)
    c = st["contacts"].get(uid)
    peer = "telegram:" + uid
    if not UID_RE.fullmatch(uid) or not isinstance(c, dict):
        return {"ok": False, "stage": "failed", "detail": "unknown_contact"}
    if c.get("relation") == "blocked":
        return {"ok": False, "stage": "failed", "detail": "blocked"}
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "stage": "failed", "detail": "empty"}
    try:
        res = api.call("sendMessage", {"chat_id": int(uid), "text": text[:TEXT_MAX]})
    except TgError as err:
        audit(root, "out", peer, "failed:" + str(err.code))
        return {"ok": False, "stage": "failed", "detail": str(err.code)}
    audit(root, "out", peer, "sent")
    return {"ok": True, "stage": "sent", "detail": "", "message_id": (res or {}).get("message_id")}


def send_as_human(root, uid, text, api, env, prove=None):
    """艦長室 / 指令代表主人寄:先本人證明(Mac Touch ID;同 chat_owner.prove),過了才寄。"""
    import chat_owner as CO
    shown = (load_state(root)["contacts"].get(str(uid)) or {}).get("shown") or "Telegram"
    why = (prove or CO.prove)("寄 Telegram 給 %s:%s" % (_clean(shown, 24), _clean(text, 40)), env)
    if why:
        return {"ok": False, "stage": "failed", "detail": why}
    return send(root, uid, text, api)


def set_relation(root, uid, relation):
    uid = str(uid or "")
    if not UID_RE.fullmatch(uid) or relation not in RELATIONS:
        return False
    st = load_state(root)
    st["contacts"].setdefault(uid, {})["relation"] = relation
    save_state(root, st)
    return True


def set_on(root, on, seat=None):
    st = load_state(root)
    if on:
        if not (isinstance(seat, str) and SEAT_RE.fullmatch(seat)):
            return False
        st["seat"] = seat
    st["on"] = bool(on)
    save_state(root, st)
    return True


async def serve_loop(root, stop, env=None):
    """轉接程式裡的一條迴圈:開著才拉;金鑰拿不到 / 呼叫失敗 ⇒ 等一下再試(不洗版)。"""
    import asyncio
    api, wait = None, 5
    while not stop.is_set():
        if not load_state(root).get("on"):
            api = None
            await _sleep(stop, 10)
            continue
        try:
            if api is None:
                api = Api(load_token(env), api_base(env or os.environ))
            await asyncio.to_thread(poll_once, root, api, env, POLL_TIMEOUT_S)
            wait = 5
        except Exception as err:   # noqa: BLE001 —— 錯誤只記代碼,不帶金鑰
            sys.stderr.write("telegram:%s\n" % (err.code if isinstance(err, TgError) else type(err).__name__))
            api = None if not isinstance(err, TgError) else api
            await _sleep(stop, wait)
            wait = min(wait * 2, 300)


async def _sleep(stop, secs):
    import asyncio
    try:
        await asyncio.wait_for(stop.wait(), secs)
    except asyncio.TimeoutError:
        pass


def main(argv=None, env=None, out=None):
    """aib chat telegram on --seat <席> | off | status | check | serve | allow <id> | block <id> | send <id>(stdin 一行內文;Touch ID)
    check = getMe 驗金鑰(只印機器人帳號名);serve = 單獨跑收信迴圈(轉接程式還沒換版時用;Ctrl-C 停)。"""
    import argparse
    env = os.environ if env is None else env
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib chat telegram")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_on = sub.add_parser("on")
    p_on.add_argument("--seat", required=True)
    sub.add_parser("off")
    sub.add_parser("status")
    sub.add_parser("check")
    sub.add_parser("serve")
    for name in ("allow", "block", "send"):
        sub.add_parser(name).add_argument("uid")
    a = ap.parse_args(argv)
    root = GF.default_root(env)
    if a.cmd == "status":
        st = load_state(root)
        rows = [{"id": k, "shown": v.get("shown"), "relation": v.get("relation") or "stranger", "seen_at": v.get("seen_at")}
                for k, v in sorted(st["contacts"].items())]
        out.write(json.dumps({"on": st["on"], "seat": st["seat"], "contacts": rows}, ensure_ascii=False) + "\n")
        return 0
    if a.cmd == "check":
        try:
            me = Api(load_token(env), api_base(env)).call("getMe") or {}
        except Exception as err:   # noqa: BLE001 —— 只印代碼
            out.write(json.dumps({"ok": False, "why": err.code if isinstance(err, TgError) else type(err).__name__}) + "\n")
            return 1
        out.write(json.dumps({"ok": True, "bot": "@" + _clean(me.get("username"), 64)}, ensure_ascii=False) + "\n")
        return 0
    if a.cmd == "serve":
        import asyncio
        import signal

        async def run():
            stop = asyncio.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stop.set)
            await serve_loop(root, stop, env)
        asyncio.run(run())
        return 0
    if a.cmd in ("on", "send"):
        import chat_owner as CO
        why = None if a.cmd == "send" else CO.prove("開啟 Telegram 轉接(收進艦長席 @%s)" % _clean(a.seat, 5), env)
        if why:
            out.write(json.dumps({"ok": False, "why": why}) + "\n")
            return 3
    if a.cmd == "on":
        ok = set_on(root, True, a.seat)
    elif a.cmd == "off":
        ok = set_on(root, False)
    elif a.cmd in ("allow", "block"):
        ok = set_relation(root, a.uid, "friend" if a.cmd == "allow" else "blocked")
    else:
        text = sys.stdin.readline().rstrip("\n")
        res = send_as_human(root, a.uid, text, Api(load_token(env), api_base(env)), env)
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return 0 if res["ok"] else 1
    out.write(json.dumps({"ok": bool(ok)}) + "\n")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
