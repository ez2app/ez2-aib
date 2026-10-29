#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""思維桌核心(SPEC-meeting-table-f5fb8-1029「做法」1–2 + 02:5x 三方審查定案)。只用標準庫,不碰畫面。

aib meeting new|list|show|summary|next|card|fork|note|table|link|tick … [--json]

會議的輸出是**主張卡**,不是對話。存在 <狀態根>/meetings/<g_ + 12 位十六進位>/(目錄 0700、檔 0600):
  events.jsonl   正本(同 mission.py):{v, seq, eid, req, at, by, type, card?, data};每次讀從頭重放、每筆照同一套規則驗,
                 非法 ⇒ 整份讀不起來(不猜);最後一行沒寫完 ⇒ 當沒寫,下次寫入前截掉。
  meeting.json / cards.jsonl / summary.json   重放後的快照(給畫面讀;不當正本)。

主張卡 {id c<n>, by, kind, text, why, evidence[], target, task_ids[]}:
  kind = claim 主張 / second 附議 / object 反對 / question 問號 / evidence 證據;後四種一定指向一張 claim(target)。
  claim 的狀態一律重放算出來,不給 AI 自報:
    settled  已定 = ≥1 附議 + ≥1 證據 + 沒有未解的反對(02:5x「沒證據的主張不進已定」;@f5fb8 13:45 定門檻)
    forked   分岔 = 有未解的反對;rejected = 分岔裁成反對那邊;open = 有人碰但還不夠;untouched = 沒人碰
  證據 = claim 自己帶的 --evidence,或指向它的 evidence 卡帶的;一條 = 一個參照(檔 / 網址 / 收據 id),內容核實是品管的事。
  疊層數 = 附議的不同席數(不投票)。不能附議 / 反對自己的主張;品管(qa)只能出 question / evidence,永遠在桌邊。
分岔卡(SPEC-mission-team 已定 2 + 02:5x):反對一出現 ⇒ 開 f<n>。主持挑 2–3 席(品管不在內,不同家優先 ⇒ 標 same_brand)
  各寫一句(view);全到齊主持才能定預設(要附證據);倒數 FORK_S 30 秒沒人改 ⇒ tick 由 system 記「預設採用」;
  本人可隨時改選(pick)。離開模式(away)不等 30 秒:預設一出就算數。主持確認只在本人在線時走這 30 秒。
  高風險(花錢 / 發版 / 刪)不在這裡自動走:那種走決定卡,這支不管。
人的三個動作:📝 note 便條(來源 human)/ ⏸ pause · ▶ resume · 🔚 close / ✋ intervene on|off(打開才能以 human 出卡)。
散會(close,或 tick 到 minutes 自動散;還在倒數的分岔照預設合回)⇒ summary.json 一頁:結論(settled)/ 分歧(已解 + 未解)/ 下一步 / 需要你的。
  🚀:只把 settled 卡對成任務團 aib mission new 草稿(handoff);分岔 / 沒定的卡標「未決」,不進做中。
  任務團開好之後主持用 link 把 task_ids 寫回卡上(只有 settled 卡可以)。
保險絲:每席 claim 數 ≤ rounds(預設 3)/ 會議 minutes(預設 30)到 tick 自動散 / 每張卡 text、why ≤ chars(預設 400)。
  主持只發摘要 + 輪到誰:核心給 next(誰還沒出卡、沒人碰的、等裁的分岔),不替主持寫字。
同帳號威脅模型(GUIDE-same-user-ai-threat-model):meetings/ 所有 AI 都寫得到;--by 是自報(含 human)。
  狀態由卡算、eid、rev 只防誤操作與 AI 之間互踩,不是防線;這裡沒有高風險動作 ⇒ 不走 Touch ID
  (🚀 之後真的開團 / 花額度,走 aib mission 的 Touch ID)。
  給畫面 / 之後簡報室的規矩(#429 R1 L2 / L4):by=human 不能當「本人按的」證明(任何 AI 都能自報 human、自報主持);
  evidence / note 的 ref 是任意字串(可能是 javascript:…),畫面只當文字顯示,不直接當連結。
事件總數上限 EVENTS_MAX(滿了只收散會);被改壞的 events.jsonl ⇒ 那場 corrupt:<seq>,list 照列、別場不受影響。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

V = 1
FORK_S = 30
ID_RE = re.compile(r"^g_[0-9a-f]{12}$")
CARD_RE = re.compile(r"^c[0-9]{1,4}$")
FORK_RE = re.compile(r"^f[0-9]{1,4}$")
TASK_RE = re.compile(r"^t[0-9]{1,4}$")
MISSION_RE = re.compile(r"^m_[0-9a-f]{12}$")
EID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
SEAT_RE = re.compile(r"^[0-9a-f]{5}$")
BY_RE = re.compile(r"^(seat:[0-9a-f]{5}|human)$")
BRAND_RE = re.compile(r"^[a-z0-9-]{1,20}$")
ROLES = ("host", "member", "qa")
KINDS = ("claim", "second", "object", "question", "evidence")
QA_KINDS = ("question", "evidence")
TABLE = ("open", "paused", "closed")
MOVES = {"pause": ("open",), "resume": ("paused",), "close": ("open", "paused")}
FLAGS = ("intervene", "away")
DEFAULTS = {"rounds": 3, "minutes": 30, "chars": 400}
LIMITS = {"rounds": (1, 20), "minutes": (1, 240), "chars": (40, 2000)}
TOPIC_MAX, REF_MAX, LIST_MAX, ATTEND_MAX, CARDS_MAX = 120, 200, 20, 12, 2000
EVENTS_MAX = 20000                   # 一場事件總數上限(table 開關 / link 沒有別的上限;#429 R1 L1);散會照收


class MeetingError(Exception):
    pass


def _txt(v, n):
    import touchid_approve as TA
    return TA.clean(v, n) if isinstance(v, str) else ""


def _long(v, n):
    """卡的內文:超過上限 ⇒ 拒(保險絲,不偷偷截);回清過的字。"""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise MeetingError("bad_text")
    if len(v) > n:
        raise MeetingError("too_long")
    return _txt(v, n)


def _refs(v):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > LIST_MAX:
        raise MeetingError("bad_evidence")
    out = [_txt(x, REF_MAX) for x in v if isinstance(x, str)]
    if len(out) != len(v) or any(not x for x in out):
        raise MeetingError("bad_evidence")
    return out


# ── 檔(同 mission.py)──

def root(env=None):
    import seats_dir_lib
    return Path(seats_dir_lib.state_root(env)[0]) / "meetings"


def gdir(gid, env=None):
    if not ID_RE.match(str(gid or "")):
        raise MeetingError("bad_id")
    return root(env) / gid


def _mkdir(p):
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p), 0o700)


@contextmanager
def _locked(d, create=False):
    if not create and not d.is_dir():
        raise MeetingError("no_meeting")
    _mkdir(d)
    fd = os.open(str(d / ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))


def _read_events(d):
    p = d / "events.jsonl"
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return [], None
    evs, pos, torn = [], 0, None
    for line in raw.splitlines(keepends=True):
        try:
            if not line.endswith(b"\n"):
                raise ValueError("torn")
            ev = json.loads(line)
            if not isinstance(ev, dict):
                raise ValueError("not_obj")
        except (ValueError, RecursionError):        # 深巢狀的 JSON 丟 RecursionError,不是 ValueError(#429 R1 M1)
            if pos + len(line) == len(raw) and not line.endswith(b"\n"):
                torn = pos
                break
            raise MeetingError("corrupt:%d" % (len(evs) + 1))
        evs.append(ev)
        pos += len(line)
    return evs, torn


def _empty(gid):
    return {"v": V, "id": gid, "status": None, "seq": 0, "topic": "", "mission": None, "attend": {}, "fuses": {},
            "cards": {}, "forks": {}, "notes": [], "eids": {}, "intervene": False, "away": False,
            "closed_by": None, "created_at": None, "updated_at": None}


def replay(gid, env=None):
    return _load(gdir(gid, env), gid)[0]


def _load(d, gid):
    evs, torn = _read_events(d)
    st = _empty(gid)
    for ev in evs:
        try:
            apply(st, ev)
        except MeetingError as err:
            raise MeetingError("corrupt:%s:%s" % (ev.get("seq"), err))
        except Exception:  # noqa: BLE001 —— 被改過的行型別不對(list 當卡號…)⇒ 這場讀不起來,不丟 traceback(#429 R1 M1)
            raise MeetingError("corrupt:%s:bad_event" % ev.get("seq"))
    return st, torn, evs


# ── 規則(指令與重放共用一套)──

def _seat(by):
    return by[5:] if isinstance(by, str) and by.startswith("seat:") else None


def _role(st, by):
    if by == "human":
        return "human"
    a = st["attend"].get(_seat(by) or "")
    return a["role"] if a else None


def _claim(st, cid):
    c = st["cards"].get(cid)
    if not c or c["kind"] != "claim":
        raise MeetingError("bad_target")
    return c


def _open_fork(st, cid):
    for f in st["forks"].values():
        if f["claim"] == cid and f["status"] == "open":
            return f
    return None


def _resolve(st, f, choice, adopted, at):
    f.update(status="resolved", choice=choice, adopted=adopted, resolved_at=at)


def _close(st, by_what, at):
    """散會:還在倒數、已有預設的分岔照預設合回桌心(記 default_on_close);沒預設的留著,散會一頁列進未決。"""
    for f in st["forks"].values():
        if f["status"] == "open" and f["default"]:
            _resolve(st, f, f["default"]["choice"], "default_on_close", at)
    st.update(status="closed", closed_by=by_what, closed_at=at)


def claim_state(st, c):
    """一張 claim 現在的狀態(畫面 / 散會共用;不存,每次算)。"""
    kids = [k for k in st["cards"].values() if k.get("target") == c["id"]]
    forks = [f for f in st["forks"].values() if f["claim"] == c["id"]]
    if any(f["status"] == "resolved" and f["choice"] == "object" for f in forks):
        return "rejected"
    overruled = {o for f in forks if f["status"] == "resolved" for o in f["objections"]}
    if any(k["kind"] == "object" and k["id"] not in overruled for k in kids):
        return "forked"
    seconds = {k["by"] for k in kids if k["kind"] == "second"}
    ev = len(c["evidence"]) + sum(len(k["evidence"]) for k in kids if k["kind"] == "evidence")
    if seconds and ev:
        return "settled"
    return "open" if kids else "untouched"


def apply(st, ev):
    """套一筆事件。不合規則 ⇒ MeetingError(指令端拒收;重放端 = 檔被改過或壞了)。"""
    if not isinstance(ev, dict) or ev.get("v") != V or ev.get("seq") != st["seq"] + 1:
        raise MeetingError("bad_seq")
    typ, by, at, d = ev.get("type"), ev.get("by"), ev.get("at"), ev.get("data")
    if not isinstance(d, dict) or type(at) is not int or not EID_RE.match(str(ev.get("eid") or "")):
        raise MeetingError("bad_event")
    if ev["eid"] in st["eids"]:
        raise MeetingError("dup_eid")
    system = typ in ("fork_expire", "timeout")
    if (by == "system") != system or (not system and not BY_RE.match(str(by or ""))):
        raise MeetingError("bad_by")
    if st["status"] is None and typ != "created":
        raise MeetingError("not_created")
    if st["status"] == "closed" and typ != "link":
        raise MeetingError("closed")
    if st["status"] == "paused" and typ not in ("table", "note", "link", "timeout"):
        raise MeetingError("paused")                # 暫停中時間到照樣散會,不然 resume / close 全被擋(#429 R1 H1)
    if st["seq"] >= EVENTS_MAX and typ != "timeout" and not (typ == "table" and d.get("move") == "close"):
        raise MeetingError("too_many_events")
    role = _role(st, by)
    if typ == "created":
        if st["status"] is not None:
            raise MeetingError("exists")
        attend, host = d.get("attend"), d.get("host")
        if not isinstance(attend, dict) or not 1 <= len(attend) <= ATTEND_MAX:
            raise MeetingError("bad_attend")
        for seat, a in attend.items():
            if not SEAT_RE.match(str(seat)) or not isinstance(a, dict) or a.get("role") not in ROLES:
                raise MeetingError("bad_attend")
            if a.get("brand") is not None and not BRAND_RE.match(str(a["brand"])):
                raise MeetingError("bad_attend")
        if [s for s, a in attend.items() if a["role"] == "host"] != [host]:
            raise MeetingError("bad_host")          # 主持一定在出席名單、只有一位
        fuses = dict(DEFAULTS)
        for k, (lo, hi) in LIMITS.items():
            v = (d.get("fuses") or {}).get(k)
            if v is not None:
                if type(v) is not int or not lo <= v <= hi:
                    raise MeetingError("bad_fuse")
                fuses[k] = v
        mission = d.get("mission")
        if mission is not None and not MISSION_RE.match(str(mission)):
            raise MeetingError("bad_mission")
        topic = _txt(d.get("topic"), TOPIC_MAX)
        if not topic:
            raise MeetingError("no_topic")
        st.update(status="open", topic=topic, mission=mission, fuses=fuses, created_at=at,
                  attend={s: {"role": a["role"], "brand": a.get("brand")} for s, a in attend.items()})
    elif typ == "card":
        kind = d.get("kind")
        if kind not in KINDS:
            raise MeetingError("bad_kind")
        if role is None or (role == "human" and not st["intervene"]):
            raise MeetingError("not_allowed")       # 本人要先 ✋ 介入才算一席
        if role == "qa" and kind not in QA_KINDS:
            raise MeetingError("qa_side_only")      # 品管永遠在桌邊:只問、只補證據
        cid = ev.get("card")
        if not CARD_RE.match(str(cid or "")) or cid in st["cards"] or len(st["cards"]) >= CARDS_MAX:
            raise MeetingError("bad_card")
        n = st["fuses"]["chars"]
        card = {"id": cid, "by": by, "kind": kind, "text": _long(d.get("text"), n), "why": _long(d.get("why"), n),
                "evidence": _refs(d.get("evidence")), "target": None, "task_ids": [], "at": at}
        if not card["text"] and kind != "second":
            raise MeetingError("no_text")
        if kind == "claim":
            if d.get("target") is not None:
                raise MeetingError("bad_target")
            mine = sum(1 for c in st["cards"].values() if c["kind"] == "claim" and c["by"] == by)
            if mine >= st["fuses"]["rounds"]:
                raise MeetingError("rounds_used")
        else:
            target = _claim(st, d.get("target"))
            card["target"] = target["id"]
            if kind in ("second", "object"):
                if target["by"] == by:
                    raise MeetingError("own_claim")
                if claim_state(st, target) == "rejected":
                    raise MeetingError("bad_target")
                if any(k["target"] == target["id"] and k["by"] == by and k["kind"] in ("second", "object")
                       for k in st["cards"].values()):
                    raise MeetingError("already_reacted")   # 一席對一張主張只表態一次
            if kind == "evidence" and not card["evidence"]:
                raise MeetingError("bad_evidence")
        st["cards"][cid] = card
        if kind == "object":
            f = _open_fork(st, card["target"])
            if f:
                f["objections"].append(cid)
            else:
                fid = "f%d" % (len(st["forks"]) + 1)
                st["forks"][fid] = {"id": fid, "claim": card["target"], "objections": [cid], "status": "open",
                                    "panel": [], "views": {}, "default": None, "default_at": None,
                                    "choice": None, "adopted": None, "opened_at": at, "resolved_at": None}
    elif typ == "fork":
        f = st["forks"].get(d.get("fork"))
        if not f:
            raise MeetingError("no_fork")
        if f["status"] != "open":
            raise MeetingError("fork_resolved")
        op = d.get("op")
        if op == "panel":
            if role != "host" or f["panel"]:
                raise MeetingError("not_allowed")
            seats = d.get("seats")
            if not isinstance(seats, list) or not 2 <= len(seats) <= 3 or len(set(seats)) != len(seats):
                raise MeetingError("bad_panel")
            for s in seats:
                a = st["attend"].get(s) if isinstance(s, str) else None
                if not a or a["role"] == "qa":
                    raise MeetingError("bad_panel")    # 品管不在三人內(02:5x)
            f["panel"] = list(seats)
        elif op == "view":
            seat = _seat(by)
            if seat not in f["panel"] or seat in f["views"]:
                raise MeetingError("not_allowed")
            text = _long(d.get("text"), st["fuses"]["chars"])
            if not text:
                raise MeetingError("no_text")
            f["views"][seat] = text
        elif op == "default":
            if role != "host":
                raise MeetingError("not_allowed")
            if not f["panel"] or set(f["views"]) != set(f["panel"]):
                raise MeetingError("views_missing")    # 三方都先寫完,主持才定
            if f["default"]:
                raise MeetingError("not_allowed")
            choice = d.get("choice")
            if choice not in ("claim", "object"):
                raise MeetingError("bad_choice")
            refs = _refs(d.get("evidence"))
            if not refs:
                raise MeetingError("bad_evidence")    # 預設附證據(02:5x)
            f.update(default={"choice": choice, "why": _long(d.get("why"), st["fuses"]["chars"]), "evidence": refs},
                     default_at=at)
            if st["away"]:
                _resolve(st, f, choice, "default_away", at)   # 離開模式不等 30 秒
        elif op == "pick":
            if by != "human":
                raise MeetingError("not_allowed")
            choice = d.get("choice")
            if choice not in ("claim", "object"):
                raise MeetingError("bad_choice")
            _resolve(st, f, choice, "human", at)
        else:
            raise MeetingError("bad_op")
    elif typ == "fork_expire":
        f = st["forks"].get(d.get("fork"))
        if not f or f["status"] != "open" or not f["default"] or at - f["default_at"] < FORK_S:
            raise MeetingError("not_due")
        _resolve(st, f, f["default"]["choice"], "default", at)
    elif typ == "note":
        if by != "human":
            raise MeetingError("not_allowed")
        text, ref = _long(d.get("text"), st["fuses"]["chars"]), _refs(d.get("ref"))
        if not text and not ref:
            raise MeetingError("no_text")
        if len(st["notes"]) >= LIST_MAX * 5:
            raise MeetingError("too_many")
        st["notes"].append({"n": len(st["notes"]) + 1, "text": text, "ref": ref, "source": "human", "at": at})
    elif typ == "table":
        mv = d.get("move")
        if mv in MOVES:
            if role not in ("host", "human"):
                raise MeetingError("not_allowed")
            if st["status"] not in MOVES[mv]:
                raise MeetingError("bad_transition")
            if mv == "close":
                _close(st, "human" if by == "human" else "host", at)
            else:
                st["status"] = {"pause": "paused", "resume": "open"}[mv]
        elif mv in FLAGS:
            if by != "human" or type(d.get("on")) is not bool:
                raise MeetingError("not_allowed")
            st[mv] = d["on"]
        else:
            raise MeetingError("bad_move")
    elif typ == "timeout":
        if st["status"] not in ("open", "paused") or at - st["created_at"] < st["fuses"]["minutes"] * 60:
            raise MeetingError("not_due")
        _close(st, "timeout", at)
    elif typ == "link":
        if role not in ("host", "human"):
            raise MeetingError("not_allowed")
        c = _claim(st, ev.get("card"))
        if claim_state(st, c) != "settled":
            raise MeetingError("not_settled")         # 只有已定的卡進任務團
        tids = d.get("task_ids")
        if not isinstance(tids, list) or not tids or len(tids) > LIST_MAX or \
                any(not TASK_RE.match(str(t)) for t in tids):
            raise MeetingError("bad_task")
        c["task_ids"] = sorted(set(c["task_ids"]) | set(tids), key=lambda t: int(t[1:]))
    else:
        raise MeetingError("bad_type")
    st["seq"] = ev["seq"]
    st["eids"][ev["eid"]] = {"seq": ev["seq"], "req": ev.get("req")}
    st["updated_at"] = at


# ── 算(畫面 / 散會共用)──

def _claims(st):
    return sorted((c for c in st["cards"].values() if c["kind"] == "claim"), key=lambda c: int(c["id"][1:]))


def _kids(st, cid, kind):
    return [k for k in st["cards"].values() if k.get("target") == cid and k["kind"] == kind]


def card_view(st, c):
    q = _kids(st, c["id"], "question")
    evs = _kids(st, c["id"], "evidence")
    answered = [x for x in q if any(e["at"] >= x["at"] and int(e["id"][1:]) > int(x["id"][1:]) for e in evs)]
    return {"id": c["id"], "by": c["by"], "text": c["text"], "why": c["why"], "state": claim_state(st, c),
            "seconds": sorted({k["by"] for k in _kids(st, c["id"], "second")}),
            "objections": [k["id"] for k in _kids(st, c["id"], "object")],
            "evidence": c["evidence"] + [r for e in evs for r in e["evidence"]],
            "questions": len(q), "questions_open": len(q) - len(answered), "task_ids": c["task_ids"]}


def _fork_view(st, f, now):
    brands = [st["attend"].get(s, {}).get("brand") for s in f["panel"]]
    known = [b for b in brands if b]
    objs = [st["cards"][o] for o in f["objections"] if o in st["cards"]]
    return dict(f, same_brand=len(known) != len(set(known)),
                # 分岔卡並排兩條路:反對卡的字一起給(只限這個分岔的;@ad79b 15:36)
                object_cards=[{"id": o["id"], "by": o["by"], "text": o["text"], "why": o["why"]} for o in objs],
                decide_in=(max(0, f["default_at"] + FORK_S - now)
                           if f["status"] == "open" and f["default_at"] and not st["away"] else None))


def view(st, now=None):
    now = int(time.time() if now is None else now)
    cards = [card_view(st, c) for c in _claims(st)]
    count = {k: sum(1 for c in cards if c["state"] == k) for k in ("settled", "forked", "rejected", "open", "untouched")}
    judged = count["settled"] + count["rejected"]
    return {"v": V, "id": st["id"], "status": st["status"], "seq": st["seq"], "topic": st["topic"],
            "mission": st["mission"], "attend": st["attend"], "fuses": st["fuses"], "intervene": st["intervene"],
            "away": st["away"], "claims": cards, "counts": count,
            "consensus_pct": int(100 * count["settled"] / len(cards)) if cards else 0,
            "judged_pct": int(100 * judged / len(cards)) if cards else 0,
            "forks": [_fork_view(st, f, now) for f in sorted(st["forks"].values(), key=lambda f: int(f["id"][1:]))],
            "notes": st["notes"], "created_at": st["created_at"], "updated_at": st["updated_at"],
            "closed_by": st.get("closed_by"), "closed_at": st.get("closed_at"),
            "ends_in": (max(0, st["created_at"] + st["fuses"]["minutes"] * 60 - now)
                        if st["status"] != "closed" else None)}


def next_up(st, now=None):
    """主持用:輪到誰(還沒出 claim、額度還有的出席者)、沒人碰的、等裁的分岔。只給事實,不替主持寫字。"""
    v = view(st, now)
    used = {}
    for c in st["cards"].values():
        if c["kind"] == "claim":
            used[c["by"]] = used.get(c["by"], 0) + 1
    turn = [s for s, a in sorted(st["attend"].items()) if a["role"] != "qa"
            and used.get("seat:" + s, 0) < st["fuses"]["rounds"] and used.get("seat:" + s, 0) == min(
                [used.get("seat:" + x, 0) for x, b in st["attend"].items() if b["role"] != "qa"] or [0])]
    forks = [f for f in v["forks"] if f["status"] == "open"]
    return {"turn": turn, "untouched": [c["id"] for c in v["claims"] if c["state"] == "untouched"],
            "need_panel": [f["id"] for f in forks if not f["panel"]],
            "need_views": [{"fork": f["id"], "seats": [s for s in f["panel"] if s not in f["views"]]}
                           for f in forks if f["panel"] and set(f["views"]) != set(f["panel"])],
            "need_default": [f["id"] for f in forks if f["panel"] and set(f["views"]) == set(f["panel"])
                             and not f["default"]],
            "counting_down": [{"fork": f["id"], "decide_in": f["decide_in"]} for f in forks if f["default"]]}


def summary(st, now=None):
    """散會一頁(純由卡算,不叫模型)。🚀 handoff = aib mission new 的參數草稿,只收已定的卡。"""
    v = view(st, now)
    settled = [c for c in v["claims"] if c["state"] == "settled"]
    undecided = [c for c in v["claims"] if c["state"] in ("forked", "open", "untouched")]
    forks = v["forks"]
    return {"v": V, "id": st["id"], "topic": st["topic"], "status": st["status"], "closed_by": st.get("closed_by"),
            "conclusions": [{"card": c["id"], "text": c["text"], "layers": len(c["seconds"]),
                             "evidence": c["evidence"], "task_ids": c["task_ids"]} for c in settled],
            "divergence": {
                "resolved": [{"fork": f["id"], "claim": f["claim"], "choice": f["choice"], "adopted": f["adopted"],
                              "views": f["views"], "default": f["default"]} for f in forks if f["status"] == "resolved"],
                "candidates": [{"card": c["id"], "text": c["text"]} for c in v["claims"] if c["state"] == "rejected"],
                "undecided": [{"card": c["id"], "text": c["text"], "state": c["state"]} for c in undecided]},
            "next": [{"card": c["id"], "text": c["text"], "linked": bool(c["task_ids"])} for c in settled],
            "needs_you": [{"fork": f["id"], "claim": f["claim"], "decide_in": f["decide_in"]}
                          for f in forks if f["status"] == "open"]
                         + [{"card": c["id"], "questions_open": c["questions_open"]}
                            for c in v["claims"] if c["questions_open"]],
            "notes": st["notes"],
            "handoff": {"title": st["topic"][:80], "mission": st["mission"],
                        "tasks": [{"title": c["text"][:80], "accept": c["evidence"][:LIST_MAX], "card": c["id"]}
                                  for c in settled if not c["task_ids"]]} if settled else None,
            "consensus_pct": v["consensus_pct"]}


# ── 寫 ──

def _req_digest(typ, by, card, data):
    raw = json.dumps([typ, by, card, data], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _snapshots(d, st, now):
    v = view(st, now)
    _write(d / "meeting.json", json.dumps({k: v[k] for k in v if k != "claims"}, ensure_ascii=False, sort_keys=True))
    _write(d / "cards.jsonl", "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in v["claims"]))
    _write(d / "summary.json", json.dumps(summary(st, now), ensure_ascii=False, sort_keys=True))


def _append(d, ev, torn):
    p = d / "events.jsonl"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if torn is not None:
            os.ftruncate(fd, torn)
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, (json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(str(p), 0o600)


def _due(d, st, torn, now):
    """倒數滿了的分岔 ⇒ 預設採用;會議時間到 ⇒ 散會。都是 system 寫。回寫了幾筆。"""
    n = 0
    evs = []
    if st["status"] == "open" and not st["away"]:
        for f in sorted(st["forks"].values(), key=lambda f: int(f["id"][1:])):
            if f["status"] == "open" and f["default"] and now - f["default_at"] >= FORK_S:
                evs.append(("fork_expire", {"fork": f["id"]}))
    if st["status"] in ("open", "paused") and now - st["created_at"] >= st["fuses"]["minutes"] * 60:
        evs.append(("timeout", {}))
    for typ, data in evs:
        ev = {"v": V, "seq": st["seq"] + 1, "eid": "x_" + secrets.token_hex(8), "req": None, "at": now,
              "by": "system", "type": typ, "data": data}
        apply(st, ev)
        _append(d, ev, torn if n == 0 else None)
        n += 1
    return n


def commit(gid, typ, by, data, env=None, card=None, eid=None, now=None):
    now = int(time.time() if now is None else now)
    d = gdir(gid, env)
    if eid is not None and not EID_RE.match(str(eid)):
        raise MeetingError("bad_eid")
    req = _req_digest(typ, by, card, data)
    with _locked(d, create=typ == "created"):
        st, torn, evs = _load(d, gid)
        if typ == "created" and evs:
            raise MeetingError("exists")
        if eid and eid in st["eids"]:
            if st["eids"][eid]["req"] != req:
                raise MeetingError("eid_conflict")
            orig = evs[st["eids"][eid]["seq"] - 1]
            return {"ok": True, "duplicate": True, "seq": orig["seq"], "state": st, "card": orig.get("card")}
        n = _due(d, st, torn, now) if evs else 0
        torn = None if n else torn
        if typ == "card" and card is None:          # 編號在鎖內配(兩席同時出卡不撞號)
            card = "c%d" % (len(st["cards"]) + 1)
        ev = {"v": V, "seq": st["seq"] + 1, "eid": eid or ("e_" + secrets.token_hex(8)), "req": req, "at": now,
              "by": by, "type": typ, "card": card, "data": data}
        if card is None:
            ev.pop("card")
        try:
            apply(st, ev)
        except MeetingError:
            if n:
                _snapshots(d, st, now)
            raise
        _append(d, ev, torn)
        _snapshots(d, st, now)
        return {"ok": True, "duplicate": False, "seq": ev["seq"], "state": st, "card": card}


def tick(gid, env=None, now=None):
    now = int(time.time() if now is None else now)
    d = gdir(gid, env)
    with _locked(d):
        st, torn, evs = _load(d, gid)
        if not evs:
            raise MeetingError("no_meeting")
        n = _due(d, st, torn, now)
        if n:
            _snapshots(d, st, now)
        return {"wrote": n, "state": st}


def new(by, topic, attend, host, mission=None, fuses=None, env=None, eid=None, now=None):
    data = {"topic": topic, "attend": attend, "host": host, "mission": mission, "fuses": fuses or {}}
    _mkdir(root(env))
    with _locked(root(env), create=True):     # 帶 eid 的去重與開場在同一把鎖內(兩個同 eid 同時 new 只開一場;#429 R1 L3)
        if eid:                               # 同 eid 重送 ⇒ 找回原本那場
            for gid in list_ids(env):
                try:
                    st = replay(gid, env)
                except MeetingError:
                    continue
                if eid in st["eids"]:
                    if st["eids"][eid]["req"] != _req_digest("created", by, None, data):
                        raise MeetingError("eid_conflict")
                    return {"ok": True, "duplicate": True, "id": gid, "state": st}
        gid = "g_" + secrets.token_hex(6)
        res = commit(gid, "created", by, data, env=env, eid=eid, now=now)
    res["id"] = gid
    return res


def list_ids(env=None):
    r = root(env)
    return sorted(p.name for p in r.iterdir() if p.is_dir() and ID_RE.match(p.name)) if r.is_dir() else []


# ── CLI ──

def T(key, **kw):
    import i18n_lib
    try:
        return i18n_lib.t("aib.meeting." + key, kw)
    except Exception:   # noqa: BLE001
        return key


def _attend_arg(vals, host):
    """--attend <席>[:role[:brand]] … ⇒ {席: {role, brand}};--host 那席自動是 host。"""
    out = {}
    for v in vals:
        parts = str(v).split(":")
        if len(parts) > 3 or parts[0] in out:
            raise MeetingError("bad_attend")
        out[parts[0]] = {"role": parts[1] if len(parts) > 1 and parts[1] else "member",
                         "brand": parts[2] if len(parts) > 2 and parts[2] else None}
    if host:
        cur = out.setdefault(host, {"role": "host", "brand": None})
        cur["role"] = "host"
    return out


def _parser():
    p = argparse.ArgumentParser(prog="aib meeting", add_help=True)
    sub = p.add_subparsers(dest="cmd")
    for name in ("new", "list", "show", "summary", "next", "card", "fork", "note", "table", "link", "tick"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--by", default=None)
        s.add_argument("--eid", default=None)
        if name == "new":
            s.add_argument("--topic", required=True)
            s.add_argument("--host", required=True)
            s.add_argument("--attend", action="append", default=[])
            s.add_argument("--mission", default=None)
            for k in DEFAULTS:
                s.add_argument("--" + k, type=int, default=None)
        if name not in ("new", "list"):
            s.add_argument("id")
        if name == "card":
            s.add_argument("--kind", choices=KINDS, required=True)
            s.add_argument("--text", default="")
            s.add_argument("--why", default="")
            s.add_argument("--evidence", action="append", default=[])
            s.add_argument("--target", default=None)
        if name == "fork":
            s.add_argument("op", choices=("panel", "view", "default", "pick"))
            s.add_argument("fork")
            s.add_argument("--seat", action="append", default=[])
            s.add_argument("--text", default="")
            s.add_argument("--choice", choices=("claim", "object"), default=None)
            s.add_argument("--why", default="")
            s.add_argument("--evidence", action="append", default=[])
        if name == "note":
            s.add_argument("--text", default="")
            s.add_argument("--ref", action="append", default=[])
        if name == "table":
            s.add_argument("move", choices=("pause", "resume", "close", "intervene-on", "intervene-off",
                                            "away-on", "away-off"))
        if name == "link":
            s.add_argument("card")
            s.add_argument("--task", action="append", default=[])
    return p


def _by(a):
    if not BY_RE.match(str(a.by or "")):
        raise MeetingError("bad_by")
    return a.by


def _text_view(v):
    lines = ["%s  %s  %s  %d%%" % (v["id"], T("st." + v["status"]), v["topic"], v["consensus_pct"])]
    for c in v["claims"]:
        lines.append("  %s  %s  ×%d  %s" % (c["id"], T("card." + c["state"]), len(c["seconds"]), c["text"][:60]))
    for f in v["forks"]:
        if f["status"] == "open":
            lines.append("  %s  %s  %s" % (f["id"], T("fork.open"), f["claim"]))
    return "\n".join(lines)


def _text_summary(s):
    out = [T("sum.title", topic=s["topic"])]
    out.append(T("sum.conclusions", n=len(s["conclusions"])))
    out += ["  · %s" % c["text"][:80] for c in s["conclusions"]]
    out.append(T("sum.undecided", n=len(s["divergence"]["undecided"])))
    out += ["  · %s" % c["text"][:80] for c in s["divergence"]["undecided"]]
    out.append(T("sum.needs_you", n=len(s["needs_you"])))
    return "\n".join(out)


def run(argv, env=None, out=None):
    out = out or sys.stdout
    try:
        a = _parser().parse_args(argv)
    except SystemExit as ex:                  # --json 時用法錯也出 JSON(#429 R1 L5);--help 照舊
        if ex.code and "--json" in argv:
            out.write(json.dumps({"ok": False, "error": "usage"}) + "\n")
            return 2
        raise
    try:
        if a.cmd is None:
            raise MeetingError("usage")
        if a.cmd == "list":
            rows = []
            for gid in list_ids(env):
                try:
                    st = replay(gid, env)
                    rows.append({"id": gid, "status": st["status"], "topic": st["topic"], "updated_at": st["updated_at"]})
                except MeetingError as err:
                    rows.append({"id": gid, "status": None, "error": str(err)})
            return _emit(out, a, {"ok": True, "meetings": rows},
                         "\n".join("%s  %s  %s" % (r["id"], T("st." + (r["status"] or "unreadable")),
                                                   r.get("topic") or r.get("error")) for r in rows) or T("none"))
        if a.cmd in ("show", "summary", "next"):
            st = replay(a.id, env)
            if st["status"] is None:
                raise MeetingError("no_meeting")
            if a.cmd == "show":
                v = view(st)
                return _emit(out, a, dict(v, ok=True), _text_view(v))
            if a.cmd == "summary":
                s = summary(st)
                return _emit(out, a, dict(s, ok=True), _text_summary(s))
            n = next_up(st)
            return _emit(out, a, dict(n, ok=True), T("next", turn=", ".join("@" + s for s in n["turn"]) or "-",
                                                     untouched=len(n["untouched"]),
                                                     forks=len(n["need_panel"]) + len(n["need_views"])
                                                     + len(n["need_default"])))
        if a.cmd == "tick":
            r = tick(a.id, env)
            return _emit(out, a, {"ok": True, "wrote": r["wrote"], "status": r["state"]["status"]}, T("ok"))
        by = _by(a)
        if a.cmd == "new":
            fuses = {k: getattr(a, k) for k in DEFAULTS if getattr(a, k) is not None}
            res = new(by, a.topic, _attend_arg(a.attend, a.host), a.host, a.mission, fuses, env=env, eid=a.eid)
            return _emit(out, a, {"ok": True, "id": res["id"], "duplicate": res["duplicate"]}, res["id"])
        if a.cmd == "card":
            data = {"kind": a.kind, "text": a.text, "why": a.why, "evidence": a.evidence}
            if a.target:
                data["target"] = a.target
            res = commit(a.id, "card", by, data, env=env, eid=a.eid)
        elif a.cmd == "fork":
            data = {"op": a.op, "fork": a.fork}
            if a.op == "panel":
                data["seats"] = a.seat
            elif a.op == "view":
                data["text"] = a.text
            elif a.op == "default":
                data.update(choice=a.choice, why=a.why, evidence=a.evidence)
            else:
                data["choice"] = a.choice
            res = commit(a.id, "fork", by, data, env=env, eid=a.eid)
        elif a.cmd == "note":
            res = commit(a.id, "note", by, {"text": a.text, "ref": a.ref}, env=env, eid=a.eid)
        elif a.cmd == "table":
            mv, _, flag = a.move.partition("-")
            data = {"move": mv} if not flag else {"move": mv, "on": flag == "on"}
            res = commit(a.id, "table", by, data, env=env, eid=a.eid)
        else:
            res = commit(a.id, "link", by, {"task_ids": a.task}, env=env, card=a.card, eid=a.eid)
        st = res["state"]
        body = {"ok": True, "duplicate": res["duplicate"], "seq": res["seq"], "status": st["status"]}
        if a.cmd == "card":
            c = st["cards"].get(res.get("card")) or {}
            body.update(card=res.get("card"), kind=c.get("kind"))
            tgt = st["cards"].get(c.get("target") or c.get("id") or "")
            if tgt and tgt["kind"] == "claim":
                body["claim_state"] = claim_state(st, tgt)
        return _emit(out, a, body, T("ok"))
    except MeetingError as err:
        code = str(err)
        if a.json:
            out.write(json.dumps({"ok": False, "error": code}, ensure_ascii=False) + "\n")
        else:
            out.write(T("err", code=code) + "\n")
        return 2


def _emit(out, a, body, text):
    out.write((json.dumps(body, ensure_ascii=False) if a.json else text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
