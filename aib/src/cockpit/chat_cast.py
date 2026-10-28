#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影音牆投放 AIB 畫面(10-29,SPEC-ytwall-aib-cast-aeb40-1029;Andy Q1–Q4 02:3x)。只用標準庫。

投放端 = 被看的那台;觀看端 = 把它放上 2F 📺 影音牆的那台。走 Nostr 主人層級私訊(aib-type=cast),不開埠、不 iframe。
  觀看端  request {req_id}                       想看你的艦長室畫面(同一位一小時一則,多的靜默)
  投放端  open {grant_id, until} / reject {req_id}
  觀看端  watch {grant_id, visible, want}       每 30 秒心跳;visible:false = 牆不在畫面上 ⇒ 停推只留 👁
  投放端  frame {grant_id, seq, hash, interval, data} / unchanged {grant_id, hash}(內容沒變每 60 秒一則)
  兩邊    close {grant_id}                       投放端 = 收回 / 到期 / 作廢;觀看端 = 不看了
畫面資料只有第 4 節白名單(艦名、每個公開席的席碼 / emoji / 名 / AI 家 / 角色 / 狀態碼 / n/m、電源狀態、時間);
📌 主項目字預設不給,發單時另勾(--pin)才帶 topic。兩端都再洗一次白名單,其他欄位一律丟。

投放單(投放端 casts.json 的 grants):payload {v, grant_id, viewer, cat, issued_at, until, boot, show_pin} + sig。
  發單 = approve_gate「chat_cast.issue」(trusted_device 高風險,一次一按,不開時間窗)+ cast_grant_sign(SE 簽,接 TID-2)。
  每次推之前 cast_grant_verify + 到期 + 時鐘倒退 + 開機編號(session_epoch)+ 鎖屏 + 觀看者仍是好友 / 我的機器;
  有一項不過 ⇒ 投放單作廢、送 close。簽章轉接層 TID-2 合併前沒有簽章器 ⇒ 不發單(sign_unavailable),不退回弱方式。
觀看端(casts.json 的 in + cast/in/<grant_id>.json):只收寄件主人 = 投放單主人、沒過期、仍是好友、不在封鎖;
  每格 1 份 / 3 秒、每位主人同時 ≤ 4 格。畫面字串只給影音牆畫(aib chat cast frames --json),絕不進任何 AI 席。
帳 cast.jsonl(0600):只記中繼資料(誰、開始 / 結束、grant_id、間隔變化、作廢原因),不記畫面;90 天 / 2 MB 輪替。

同帳號威脅模型(GUIDE-same-user-ai-threat-model):投放端本機 AI 讀得到主人私鑰,本來就能繞過 aib 直接把資料寄給好友;
  這裡只管「經 aib 正路開的投放」。請求一小時一則、每格速率、同時 4 格只防誤操作 / 洗版。「收件時驗過寄件人」≠ 已驗證。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

V = 1
SCHEMA = "aib-cast/v1"
OPS = ("request", "reject", "open", "close", "watch", "frame", "unchanged")
GID_RE = re.compile(r"^[0-9a-f]{16}$")
SEAT_RE = re.compile(r"^[0-9a-f]{5}$")
AI_RE = re.compile(r"^[a-z0-9_-]{1,16}$")
HOURS = (1, 4, 8, 24)
BASE_S, MID_S, SLOW_S = 5, 15, 60
HEART_S = 30                 # 觀看端心跳
ALIVE_S = 60                 # 投放端:最近 60 秒內有心跳才推
UNCHANGED_S = 60
RATE_S = 3                   # 觀看端每格最快 1 份 / 3 秒
PER_OWNER_MAX = 4
REQUEST_EVERY_S = 3600
REQUEST_VALID_S = 86400      # 觀看端:送出請求後一天內的 open 才收(#411 R1 M2)
EPOCH_CACHE_S = 5            # session_epoch 兩個子行程:5 秒才重量一次(#411 R1 M3)
CLOCK_SKEW_S = 60
WIRE_MAX = 48 * 1024
SEATS_MAX = 32
STATES = ("busy", "reply", "decide", "idle")
POWERS = ("ac", "battery", "low")
QUEUE = "cast-out"
QUEUE_TTL_S = 3600
WATCH_TTL_S = 45
LOG_MAX = 2 * 1024 * 1024
LOG_KEEP_S = 90 * 86400
LOG_KEYS = ("at", "ev", "grant_id", "who", "interval", "why", "hours")

SIGN = None          # TID-2 的 SE 簽章:SIGN(bytes) ⇒ 簽章字串;None = 還沒有 ⇒ 不發單
VERIFY = None        # VERIFY(bytes, sig) ⇒ True / False
EPOCH = None         # 測試換掉 session_epoch()
PROVE = None         # 測試換掉 Touch ID(reason, env) ⇒ None 過 / 原因碼


class CastError(Exception):
    pass


# ── 轉接層(TID-2 合併後只換這三支)──

def _canon(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signer_ready():
    return SIGN is not None


def cast_grant_sign(payload):
    """投放單 payload ⇒ 簽章字串;沒有簽章器 / 簽失敗 ⇒ None(呼叫端不發單)。"""
    if SIGN is None:
        return None
    try:
        sig = SIGN(_canon(payload))
    except Exception:   # noqa: BLE001
        return None
    return sig if isinstance(sig, str) and sig else None


def cast_grant_verify(rec):
    """{payload, sig} 驗得過 ⇒ True;沒有驗章器 / 驗不過 / 格式不對 ⇒ False。"""
    if VERIFY is None or not isinstance(rec, dict) or not isinstance(rec.get("payload"), dict):
        return False
    try:
        return VERIFY(_canon(rec["payload"]), rec.get("sig")) is True
    except Exception:   # noqa: BLE001
        return False


def session_epoch(platform=None, run=subprocess.run):
    """{"boot": 開機時間秒, "locked": 鎖屏中?};非 Mac / 讀不到 ⇒ None(呼叫端:不發單、已發的作廢)。
    TID-2 有了改接它的開機編號。"""
    if EPOCH is not None:
        return EPOCH()
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return None
    try:
        out = run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"sec\s*=\s*(\d+)", out or "")
        if not m:
            return None
        raw = run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, timeout=5).stdout
        import plistlib
        users = plistlib.loads(raw).get("IOConsoleUsers") or []
    except Exception:   # noqa: BLE001
        return None
    locked = any(isinstance(u, dict) and u.get("CGSSessionScreenIsLocked") is True for u in users)
    return {"boot": int(m.group(1)), "locked": locked}


_EPOCH = {"at": None, "val": None}


def _epoch():
    """session_epoch 快取 EPOCH_CACHE_S 秒(轉接程式每 0.2 秒叫 tick;不能每次都開 sysctl + ioreg)。"""
    if EPOCH is None and _EPOCH["at"] is not None and 0 <= time.monotonic() - _EPOCH["at"] < EPOCH_CACHE_S:
        return _EPOCH["val"]
    try:
        ep = session_epoch()
    except Exception:   # noqa: BLE001
        ep = None
    ep = ep if isinstance(ep, dict) and type(ep.get("boot")) is int else None
    if EPOCH is None:
        _EPOCH.update(at=time.monotonic(), val=ep)
    return ep


# ── 帳 ──

def _home(env=None):
    import chat_roster as CR
    return CR._chat_home(env)


def path(env=None):
    return _home(env) / "casts.json"


def frames_dir(env=None):
    return _home(env) / "cast" / "in"


@contextmanager
def _locked(env=None):
    d = _home(env)
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / "casts.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _empty():
    return {"v": V, "req_in": {}, "req_out": {}, "grants": {}, "in": {}}


def read(env=None):
    try:
        rec = json.loads(path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(rec, dict) or rec.get("v") != V:
        return _empty()
    out = _empty()
    for k in out:
        if k != "v" and isinstance(rec.get(k), dict):
            out[k] = rec[k]
    return out


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))


def _save(rec, env=None):
    _write(path(env), json.dumps(rec, ensure_ascii=False, sort_keys=True))


def log(env, ev, gid=None, who=None, now=None, **kw):
    """cast.jsonl:只記 LOG_KEYS 裡的中繼資料;超過 2 MB ⇒ 丟 90 天前的,還是太大就只留後半。"""
    now = int(time.time() if now is None else now)
    row = {"at": now, "ev": ev, "grant_id": gid, "who": who}
    row.update({k: v for k, v in kw.items() if k in LOG_KEYS and isinstance(v, (int, str, type(None)))})
    p = _home(env) / "cast.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(str(p), 0o600)
    try:
        if p.stat().st_size > LOG_MAX:
            _rotate(p, now)
    except OSError:
        pass


def _rotate(p, now):
    keep = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if int(json.loads(line).get("at") or 0) >= now - LOG_KEEP_S:
                keep.append(line)
        except (ValueError, AttributeError):
            continue
    while keep and sum(len(x) + 1 for x in keep) > LOG_MAX // 2:
        keep = keep[len(keep) // 2:]
    _write(p, "".join(x + "\n" for x in keep))


# ── 誰 ──

def _friend(owner, env):
    """好友或我的機器、沒封鎖 ⇒ 主人 npub;否則 None。"""
    import chat_roster as CR
    o = CR.friend_owner(owner or "")
    if not o or CR.is_blocked(o, env) or not (CR.is_friend(o, env) or CR.is_mine(o, env)):
        return None
    return o


def _shareable(o, env):
    import chat_roster as CR
    return bool(o) and CR.share_level(o, env) != "off"


def npub_short(o):
    o = str(o or "")
    return "%s…%s" % (o[:12], o[-6:]) if len(o) > 20 else o


def _names(env):
    import chat_roster as CR
    out = {}
    for kind in ("friends", "mine"):
        for f in CR.read_friends(env, kind):
            out[f["owner"]] = (f.get("name"), "device" if kind == "mine" else "friend")
    return out


def _who_label(o, env):
    """Touch ID 視窗那格:顯示名(清過)+ npub 前 12 碼 …後 6 碼;顯示名夾零寬 / 方向控制 ⇒ 只放 npub 碼。"""
    import touchid_approve as TA
    name = (_names(env).get(o) or (None, None))[0]
    clean = TA.clean(name, 24) if isinstance(name, str) else ""
    if not clean or clean != name[:len(clean)] or any(0x200B <= ord(c) <= 0x206F or c == "\ufeff" for c in name):
        return npub_short(o)
    return "%s(%s)" % (clean, npub_short(o))


# ── 白名單 ──

def _t(v, n):
    import touchid_approve as TA
    return TA.clean(v, n) if isinstance(v, str) else None


def _int(v, lo, hi):
    return v if type(v) is int and lo <= v <= hi else None


def whitelist(raw, show_pin=False):
    """畫面資料只留第 4 節欄位;其他(ctx / 未讀 / dm / 路徑 / host / token …)一律不在輸出裡。兩端都跑這一支。"""
    raw = raw if isinstance(raw, dict) else {}
    seats = []
    for s in (raw.get("seats") if isinstance(raw.get("seats"), list) else [])[:SEATS_MAX]:
        if not isinstance(s, dict) or not SEAT_RE.match(str(s.get("seat") or "")):
            continue
        row = {"seat": s["seat"], "emoji": _t(s.get("emoji"), 8), "name": _t(s.get("name"), 24),
               "ai": s.get("ai") if AI_RE.match(str(s.get("ai") or "")) else None, "role": _t(s.get("role"), 24),
               "state": s.get("state") if s.get("state") in STATES else "idle",
               "n": _int(s.get("n"), 0, 999), "m": _int(s.get("m"), 1, 999), "floor": _int(s.get("floor"), 1, 99)}
        if row["n"] is None or row["m"] is None:
            row["n"] = row["m"] = None
        if show_pin and _t(s.get("topic"), 60):
            row["topic"] = _t(s.get("topic"), 60)
        seats.append(row)
    return {"ship": _t(raw.get("ship"), 40), "floor": _int(raw.get("floor"), 1, 99),
            "power": raw.get("power") if raw.get("power") in POWERS else None,
            "at": _int(raw.get("at"), 0, 1 << 40), "seats": seats}


def _hash(data):
    return hashlib.sha256(_canon(data)).hexdigest()[:32]


def build_frame(env=None, now=None, power=None):
    """投放端組這一份(還沒過白名單):公開席 + 席狀態;power = 'ac' | 'battery' | 'low' | None。"""
    import chat_roster as CR
    import nostr_keys as NK
    import room_snapshot as RS
    import seat_now as SN
    env = os.environ if env is None else env
    now = int(time.time() if now is None else now)
    pub = set((CR.read_profile(env) or {}).get("public") or [])
    active = {e["seat5"] for e in NK.sending_seats(env)}
    home = env.get("EZ2_AIB_STATE_ROOT") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".ez2-aib")
    room = RS._read_json(RS.room_path(env))
    room = room if isinstance(room, dict) else {}
    extra = room.get("seats") if isinstance(room.get("seats"), dict) else {}
    seats = []
    for s in NK.live_seats(env=env):
        if s["seat5"] not in pub or s["seat5"] not in active:
            continue
        rec = SN.read(s["seat5"], state_root=home) or {}
        e = extra.get(s["seat5"]) if isinstance(extra.get(s["seat5"]), dict) else {}
        seats.append({"seat": s["seat5"], "emoji": RS._emoji_of(home, s.get("persona_uid")), "name": s.get("name"),
                      "ai": s.get("provider"), "role": s.get("role"),
                      "state": rec.get("state") if rec.get("state") in ("busy", "reply", "decide") else "idle",
                      "n": rec.get("steps_done"), "m": rec.get("steps_total"), "floor": e.get("floor"),
                      "topic": rec.get("item")})
    ship = room.get("ship") if isinstance(room.get("ship"), dict) else {}
    return {"ship": ship.get("name"), "power": power, "at": now, "seats": seats}


def interval(load1=None, ncpu=None, on_battery=False, pct=None, want=None):
    """Andy Q3:基準 5 秒;load1 > 核心 ×0.75 或用電池 ⇒ 15;load1 > 核心 ×1.5 或電量 < 20% ⇒ 60。
    觀看端要的間隔只能拉長(< 5 當 5)。全部從參數來(CI 有 Linux)。"""
    iv = BASE_S
    n = ncpu if type(ncpu) is int and ncpu > 0 else 1
    ld = load1 if isinstance(load1, (int, float)) else 0
    if ld > n * 0.75 or on_battery:
        iv = MID_S
    if ld > n * 1.5 or (isinstance(pct, (int, float)) and pct < 20):
        iv = SLOW_S
    w = want if type(want) is int else BASE_S
    return max(iv, min(SLOW_S, max(BASE_S, w)))


# ── 待送 ──

def _enqueue(root, owner, msg, now, name=None, ttl=QUEUE_TTL_S):
    q = Path(root) / QUEUE
    q.mkdir(parents=True, exist_ok=True)
    os.chmod(str(q), 0o700)
    name = name or secrets.token_hex(8)
    _write(q / (name + ".json"), json.dumps({"to": owner, "at": now, "ttl": ttl, "msg": msg}, ensure_ascii=False))


def queued(root, now=None):
    """cast-out 待送 [(path, rec)];壞檔 / 過期刪掉。"""
    now = time.time() if now is None else now
    out = []
    for p in sorted((Path(root) / QUEUE).glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if (isinstance(rec, dict) and isinstance(rec.get("msg"), dict) and rec["msg"].get("op") in OPS
                    and now - int(rec.get("at") or 0) < int(rec.get("ttl") or QUEUE_TTL_S)):
                out.append((p, rec))
                continue
        except (OSError, ValueError, TypeError):
            pass
        p.unlink(missing_ok=True)
    return out


# ── 觀看端:請求 / 心跳 / 不看了 ──

def request(env, root, owner, now=None):
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o:
        raise CastError("not_friend")
    rid = secrets.token_hex(8)
    with _locked(env):
        rec = read(env)
        rec["req_out"][o] = {"at": now, "req_id": rid, "status": "sent"}
        _save(rec, env)
    _enqueue(root, o, {"op": "request", "req_id": rid, "at": now}, now)
    return {"owner": o, "req_id": rid}


def watch(env, root, gid, visible=True, want=None, now=None):
    now = int(time.time() if now is None else now)
    g = read(env)["in"].get(gid) if GID_RE.match(str(gid or "")) else None
    if not g or g.get("status") == "closed" or int(g.get("until") or 0) <= now:
        raise CastError("no_grant")
    msg = {"op": "watch", "grant_id": gid, "visible": visible is True, "at": now}
    if type(want) is int:
        msg["want"] = want
    _enqueue(root, g["owner"], msg, now, name="w-" + gid, ttl=WATCH_TTL_S)   # 每格只留最新一則心跳
    return {"grant_id": gid, "visible": msg["visible"]}


def _drop_frame(env, gid):
    (frames_dir(env) / (gid + ".json")).unlink(missing_ok=True)


# ── 投放端:發單 / 拒絕 / 收回 ──

def _human_gate(env, who, hours, until, pin, prove=None):
    import approve_gate as AG
    r = AG.reason("chat_cast.issue", {"who": who, "hours": str(hours), "until": time.strftime("%m-%d %H:%M", time.localtime(until))},
                  suffix=("pin",) if pin else (), env=env)
    if PROVE:
        return PROVE(r, env)
    if prove:
        return prove(r)
    off = AG.ready(env)
    if off:
        return "touchid_" + off
    res = AG.ask(r, AG.state_root_default(env))
    return None if res["ok"] else "touchid_" + res["why"].split(":")[0]


def issue(env, root, owner, hours, pin=False, now=None, prove=None):
    """發投放單(只給觀看者那一位)。Touch ID 一次一按;簽不出 / 開機編號讀不到 / 鎖屏 ⇒ 不發。
    prove = 轉接程式 CLI 的 human_gate(席位呼叫直接拒 + 彈窗);沒給 ⇒ 直接走 approve_gate。"""
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o or not _shareable(o, env):
        raise CastError("not_friend")
    if hours not in HOURS:
        raise CastError("bad_hours")
    if not signer_ready():
        raise CastError("sign_unavailable")      # 先講,不白按指紋
    ep = _epoch()
    if ep is None:
        raise CastError("unavailable")
    if ep.get("locked"):
        raise CastError("locked")
    until = now + hours * 3600
    why = _human_gate(env, _who_label(o, env), hours, until, pin is True, prove)
    if why:
        raise CastError(why)
    gid = secrets.token_hex(8)
    payload = {"v": V, "grant_id": gid, "viewer": o, "cat": "screen_cast", "issued_at": now, "until": until,
               "boot": ep["boot"], "show_pin": pin is True}
    sig = cast_grant_sign(payload)
    if not sig:
        raise CastError("sign_failed")
    with _locked(env):
        rec = read(env)
        rec["grants"][gid] = {"payload": payload, "sig": sig, "watch": None, "live": False,
                              "last": {"hash": None, "sent_at": 0, "beat_at": 0, "interval": None, "seq": 0}}
        cur = rec["req_in"].get(o)
        if isinstance(cur, dict) and cur.get("status") == "pending":
            cur.update(status="accepted", answered_at=now)
        _save(rec, env)
    _enqueue(root, o, {"op": "open", "grant_id": gid, "until": until, "at": now}, now)
    log(env, "issue", gid, o, now, hours=hours)
    return {"grant_id": gid, "owner": o, "until": until, "pin": pin is True}


def req_owner(env, req_id):
    for o, r in read(env)["req_in"].items():
        if isinstance(r, dict) and r.get("req_id") == req_id:
            return o
    return None


def reject(env, root, req_id, now=None):
    now = int(time.time() if now is None else now)
    with _locked(env):
        rec = read(env)
        hit = [o for o, r in rec["req_in"].items() if isinstance(r, dict) and r.get("req_id") == req_id]
        if not hit:
            raise CastError("no_request")
        rec["req_in"][hit[0]].update(status="rejected", answered_at=now)
        _save(rec, env)
    _enqueue(root, hit[0], {"op": "reject", "req_id": req_id, "at": now}, now)
    return {"owner": hit[0]}


def close(env, root, gid, now=None, why="closed"):
    """收回(投放端)或不看了(觀看端)。收權不用證明。"""
    now = int(time.time() if now is None else now)
    with _locked(env):
        rec = read(env)
        g = rec["grants"].pop(gid, None)
        mine = rec["in"].get(gid)
        if g:
            _ended(rec, (g.get("payload") or {}).get("viewer"))
            _save(rec, env)
        elif mine:
            mine.update(status="closed", closed_at=now)
            _save(rec, env)
        else:
            raise CastError("no_grant")
    if g:
        viewer = g["payload"].get("viewer")
        _enqueue(root, viewer, {"op": "close", "grant_id": gid, "at": now}, now)
        log(env, "end", gid, viewer, now, why=why)
        return {"grant_id": gid, "side": "caster"}
    _drop_frame(env, gid)
    _enqueue(root, mine["owner"], {"op": "close", "grant_id": gid, "at": now}, now)
    return {"grant_id": gid, "side": "viewer"}


def _ended(rec, viewer):
    """投放單結束(收回 / 作廢 / 到期)⇒ 這位好友的請求限流歸零,馬上能再請求(#411 R2 M:鎖屏作廢後不用等一小時)。
    sent_at 留著:舊請求重放照樣擋。"""
    cur = rec["req_in"].get(viewer) if viewer else None
    if isinstance(cur, dict):
        cur["at"] = 0


def _void_why(g, now, ep, env):
    """投放單還能不能推:None = 能;否則作廢原因。"""
    p = g.get("payload") if isinstance(g, dict) else None
    if not cast_grant_verify(g):
        return "sig"
    if type(p.get("until")) is not int or type(p.get("issued_at")) is not int or p["until"] <= now:
        return "expired"
    if now < p["issued_at"] - CLOCK_SKEW_S:
        return "clock"
    if p["until"] - p["issued_at"] > max(HOURS) * 3600:
        return "too_long"
    if ep is None:
        return "unavailable"
    if ep.get("boot") != p.get("boot"):
        return "reboot"
    if ep.get("locked"):
        return "locked"
    if not _friend(p.get("viewer"), env) or not _shareable(p.get("viewer"), env):
        return "unfriended"
    return None


_POWER = {"at": 0, "val": (None, False, None)}


def _power(root, now):
    """(power 代碼, 用電池?, 電量 %);60 秒才重讀一次 pmset。"""
    if now - _POWER["at"] < 60:
        return _POWER["val"]
    try:
        import chat_power as CP
        info = CP.power_info(root)
    except Exception:   # noqa: BLE001
        info = {}
    pct = info.get("pct") if isinstance(info.get("pct"), (int, float)) else None
    batt = info.get("ac") is False
    code = "low" if pct is not None and pct < 20 else ("battery" if batt else ("ac" if info.get("ac") else None))
    _POWER.update(at=now, val=(code, batt, pct))
    return _POWER["val"]


def tick(env, root, now=None, load=None, ncpu=None, power=None, build=None):
    """投放端每輪:驗單 → 作廢的送 close;有心跳(60 秒內)且看得見才推;間隔照負載 / 電源;內容沒變只送 unchanged。
    回 [{"to", "msg", "grant_id", "kind", "hash", "interval"}];送成功後呼叫 sent()。load / ncpu / power 給測試注入。"""
    now = int(time.time() if now is None else now)
    out, ep = [], None
    with _locked(env):
        rec = read(env)
        if not rec["grants"]:
            return out
        ep = _epoch()
        changed = False
        frame_raw = None
        for gid in sorted(rec["grants"]):
            g = rec["grants"][gid]
            why = _void_why(g, now, ep, env)
            viewer = (g.get("payload") or {}).get("viewer")
            if why:
                rec["grants"].pop(gid)
                changed = True
                if why != "sig":
                    _ended(rec, viewer)
                if viewer and why != "sig":           # 簽章不過 ⇒ 連觀看者欄都不可信,不送;對方那格到期自己收
                    _enqueue(root, viewer, {"op": "close", "grant_id": gid, "at": now}, now)   # 進佇列:送失敗下一輪補(#411 R1 L1)
                log(env, "void", gid, viewer, now, why=why)
                continue
            w = g.get("watch") if isinstance(g.get("watch"), dict) else None
            if not w or now - int(w.get("at") or 0) > ALIVE_S:
                if g.get("live"):
                    g["live"] = False
                    changed = True
                    log(env, "watch-end", gid, viewer, now)
                continue
            if w.get("visible") is not True:
                continue                                  # 牆不在畫面上:只留 👁,不推
            if load is None:
                try:
                    load = os.getloadavg()[0]
                except OSError:
                    load = 0
            pw = power if power is not None else _power(root, now)
            iv = interval(load, ncpu if ncpu is not None else os.cpu_count(), pw[1], pw[2], w.get("want"))
            last = g["last"]
            if iv != last.get("interval"):
                log(env, "interval", gid, viewer, now, interval=iv)
                last["interval"] = iv
                changed = True
            if now - max(int(last.get("sent_at") or 0), int(last.get("tried_at") or 0)) < iv:
                continue                                  # 送失敗也至少等滿一個間隔(#411 R1 M3:不 5Hz 狂送)
            if frame_raw is None:
                frame_raw = (build or build_frame)(env, now, pw[0])
            data = whitelist(frame_raw, g["payload"].get("show_pin") is True)
            h = _hash(dict(data, at=0))
            if h == last.get("hash"):
                if now - int(last.get("beat_at") or 0) >= UNCHANGED_S:
                    out.append({"to": viewer, "msg": {"op": "unchanged", "grant_id": gid, "hash": h, "at": now},
                                "grant_id": gid, "kind": "unchanged", "hash": h, "interval": iv})
                    last["tried_at"] = now
                    changed = True
                continue
            msg = {"op": "frame", "grant_id": gid, "seq": int(last.get("seq") or 0) + 1, "hash": h, "interval": iv,
                   "at": now, "data": data}
            if len(_canon(msg)) > WIRE_MAX:
                continue
            out.append({"to": viewer, "msg": msg, "grant_id": gid, "kind": "frame", "hash": h, "interval": iv})
            last["tried_at"] = now
            changed = True
        if changed:
            _save(rec, env)
    return out


def sent(env, item, now=None):
    """tick 那一則送到中繼站之後記下(送不出去 ⇒ 不記,下一輪再推)。"""
    now = int(time.time() if now is None else now)
    if item.get("kind") not in ("frame", "unchanged"):
        return
    with _locked(env):
        rec = read(env)
        g = rec["grants"].get(item["grant_id"])
        if not g:
            return
        last = g["last"]
        last["beat_at"] = now
        if item["kind"] == "frame":
            last.update(hash=item["hash"], sent_at=now, seq=item["msg"]["seq"])
        _save(rec, env)


# ── 收 ──

def parse(content):
    if not isinstance(content, str) or len(content) > WIRE_MAX:
        return None
    try:
        m = json.loads(content)
    except ValueError:
        return None
    if not isinstance(m, dict) or m.get("op") not in OPS:
        return None
    if m["op"] in ("request", "reject"):
        return m if GID_RE.match(str(m.get("req_id") or "")) else None
    return m if GID_RE.match(str(m.get("grant_id") or "")) else None


def on_incoming(env, owner, msg, now=None):
    """轉接程式已驗簽 + 名冊 + 寄件席;這裡再驗好友 / 投放單主人。回結果碼(進 audit)或 None(丟)。"""
    now = int(time.time() if now is None else now)
    o = _friend(owner, env)
    if not o or not isinstance(msg, dict):
        return None
    op, gid = msg.get("op"), msg.get("grant_id")
    sent_at = msg.get("at") if type(msg.get("at")) is int else None
    if sent_at is None or sent_at > now + CLOCK_SKEW_S:
        return "bad"                                  # 寄出時間一定要有(#411 R1 M1:重放比的是這個)
    if op in ("watch", "frame", "unchanged") and now - sent_at > ALIVE_S + CLOCK_SKEW_S:
        return "old"                                  # 重啟補收的舊心跳 / 舊畫面:不當現在
    with _locked(env):
        rec = read(env)
        g = rec["grants"].get(gid) if gid else None
        mine = rec["in"].get(gid) if gid else None
        if op == "request":
            cur = rec["req_in"].get(o)
            if isinstance(cur, dict) and sent_at <= int(cur.get("sent_at") or 0):
                return "old"                              # 不比上一則新(重放)⇒ 不把已接受 / 已拒變回待審
            if isinstance(cur, dict) and now - int(cur.get("at") or 0) < REQUEST_EVERY_S:
                return "quiet"                            # 限流看最後一次請求,不管狀態(#411 R1 L2)
            rec["req_in"][o] = {"at": now, "sent_at": sent_at, "req_id": msg["req_id"], "status": "pending"}
            _save(rec, env)
            return "pending"
        if op == "reject":
            cur = rec["req_out"].get(o)
            if isinstance(cur, dict) and cur.get("req_id") == msg["req_id"]:
                cur.update(status="rejected", answered_at=now)
                _save(rec, env)
            return "rejected"
        if op in ("watch", "close") and g and g["payload"].get("viewer") == o:     # 我是投放端
            if op == "close":
                g["watch"] = None
                if g.get("live"):
                    log(env, "watch-end", gid, o, now)
                g["live"] = False
                _save(rec, env)
                return "stopped"
            prev = g.get("watch") if isinstance(g.get("watch"), dict) else {}
            if sent_at <= int(prev.get("sent_at") or 0):
                return "old"
            g["watch"] = {"at": now, "sent_at": sent_at, "visible": msg.get("visible") is True,
                          "want": msg.get("want") if type(msg.get("want")) is int else None}
            if not g.get("live"):
                g["live"] = True
                log(env, "watch-start", gid, o, now)
            _save(rec, env)
            return "watch"
        if op == "open":
            until = msg.get("until")
            if type(until) is not int or until <= now or until - now > max(HOURS) * 3600 + CLOCK_SKEW_S:
                return "bad"
            if mine:
                return "dup"
            asked = rec["req_out"].get(o)
            fresh = (isinstance(asked, dict) and asked.get("status") == "sent" and now - int(asked.get("at") or 0) <= REQUEST_VALID_S
                     and sent_at >= int(asked.get("at") or 0) - CLOCK_SKEW_S)
            prev = rec["in"].get(asked.get("grant_id")) if isinstance(asked, dict) and asked.get("status") == "opened" else None
            ended = None
            if isinstance(prev, dict) and prev.get("owner") == o:
                ended = prev.get("closed_at") if prev.get("status") == "closed" else (
                    prev.get("until") if int(prev.get("until") or 0) <= now else None)
            reopen = (type(ended) is int and now - ended <= REQUEST_VALID_S and sent_at >= ended - CLOCK_SKEW_S)
            if not (fresh or reopen):
                return "unrequested"                      # 沒請求過就不收(#411 R1 M2);請求開過、那格已關 ⇒ 認重開一次(#411 R2 M)
            act = [k for k, v in rec["in"].items() if v.get("owner") == o and v.get("status") != "closed"
                   and int(v.get("until") or 0) > now]
            if len(act) >= PER_OWNER_MAX:
                return "full"
            rec["in"][gid] = {"owner": o, "until": until, "status": "waiting", "at": now, "rx_at": 0, "hash": None,
                              "interval": None}
            if isinstance(asked, dict):
                asked.update(status="opened", answered_at=now, grant_id=gid)
            _save(rec, env)
            return "opened"
        if not mine or mine.get("owner") != o:          # 冒名:寄件主人 ≠ 投放單主人 ⇒ 丟
            return None
        if op == "close":
            mine.update(status="closed", closed_at=now)
            _save(rec, env)
            _drop_frame(env, gid)
            return "closed"
        if mine.get("status") == "closed" or int(mine.get("until") or 0) <= now:
            return "expired"
        if now - int(mine.get("rx_at") or 0) < RATE_S:
            return "rate"
        if sent_at < int(mine.get("sent_at") or 0):
            return "old"
        if op == "unchanged":
            if msg.get("hash") == mine.get("hash"):
                mine.update(rx_at=now, alive_at=now, sent_at=sent_at)
                _save(rec, env)
                return "alive"
            return "stale"
        if op != "frame":
            return None
        if type(msg.get("seq")) is not int or msg["seq"] <= int(mine.get("seq") or 0):
            return "old"                                  # seq 只准往上(舊畫面不蓋新畫面)
        data = whitelist(msg.get("data"), True)          # 投放端決定給不給 topic;這裡只再洗一次
        iv = msg.get("interval") if type(msg.get("interval")) is int and BASE_S <= msg["interval"] <= SLOW_S else None
        d = frames_dir(env)
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(str(d), 0o700)
        os.chmod(str(d.parent), 0o700)
        _write(d / (gid + ".json"), json.dumps({"grant_id": gid, "owner": o, "at": now, "hash": msg.get("hash"),
                                                "interval": iv, "data": data}, ensure_ascii=False, sort_keys=True))
        mine.update(status="live", rx_at=now, alive_at=now, hash=msg.get("hash") if isinstance(msg.get("hash"), str) else None,
                    interval=iv, seq=msg["seq"], sent_at=sent_at)
        _save(rec, env)
        return "frame"


# ── 給畫面 ──

def _frame_of(env, gid):
    try:
        rec = json.loads((frames_dir(env) / (gid + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return whitelist(rec.get("data"), True) if isinstance(rec, dict) else None


def payload(env=None, now=None):
    """aib chat cast list --json(aib-cast/v1,@ad79b CONTRACT-ytwall-cast-ui-ad79b-1029)。過期的格順手收掉。"""
    import chat_roster as CR
    now = int(time.time() if now is None else now)
    names = _names(env)
    with _locked(env):
        rec = read(env)
        gone = [k for k, v in rec["in"].items() if v.get("status") != "closed" and int(v.get("until") or 0) <= now]
        for k in gone:
            rec["in"][k].update(status="closed", closed_at=now)
            _drop_frame(env, k)
        if gone:
            _save(rec, env)
    targets = [{"key": o, "kind": kind, "name": n, "npub_short": npub_short(o)} for o, (n, kind) in names.items()
               if not CR.is_blocked(o, env) and _shareable(o, env)]
    incoming = [{"req_id": r["req_id"], "from": o, "kind": (names.get(o) or (None, "friend"))[1],
                 "name": (names.get(o) or (None,))[0], "npub_short": npub_short(o), "at": r.get("at")}
                for o, r in rec["req_in"].items() if isinstance(r, dict) and r.get("status") == "pending" and _friend(o, env)]
    watchers = []
    for gid, g in sorted(rec["grants"].items()):
        p = g.get("payload") or {}
        if not _friend(p.get("viewer"), env):         # 封鎖 / 解除好友的不顯示(下一輪 tick 會作廢)(#411 R1 L3)
            continue
        w = g.get("watch") if isinstance(g.get("watch"), dict) else None
        watchers.append({"grant_id": gid, "owner": p.get("viewer"), "name": (names.get(p.get("viewer")) or (None,))[0],
                         "npub_short": npub_short(p.get("viewer")), "until": p.get("until"), "pin": p.get("show_pin") is True,
                         "live": bool(w) and now - int(w.get("at") or 0) <= ALIVE_S})
    grants = []
    for gid, g in sorted(rec["in"].items(), key=lambda kv: -int(kv[1].get("at") or 0)):
        if not _friend(g.get("owner"), env):
            continue
        st = g.get("status")
        iv = g.get("interval") or BASE_S
        if st == "live" and now - int(g.get("alive_at") or 0) > max(2 * iv, UNCHANGED_S) + 10:
            st = "paused"
        grants.append({"grant_id": gid, "owner": g.get("owner"), "name": (names.get(g.get("owner")) or (None,))[0],
                       "npub_short": npub_short(g.get("owner")), "until": g.get("until"),
                       "state": st if st in ("waiting", "live", "paused", "closed") else "waiting",
                       "interval": g.get("interval"), "frame": _frame_of(env, gid) if st != "closed" else None,
                       "sender": "checked_on_receive"})
    return {"ok": True, "schema": SCHEMA, "at": now, "targets": targets, "incoming": incoming, "watchers": watchers,
            "grants": grants, "watching": sum(1 for w in watchers if w["live"]), "sign_ready": signer_ready()}
