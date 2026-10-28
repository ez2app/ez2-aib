#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""任務團資料契約(S1,SPEC-mission-team-f5fb8-1029「三方審查定案」2 / 3 / 4 / 9)。只用標準庫。

aib mission new|list|show|roster|team|task|tick … [--json]

存在 <狀態根>/missions/<id>/(目錄 0700、檔 0600):
  events.jsonl   正本。一行一筆事件 {v, seq, eid, req, at, by, type, task?, data};seq 從 1 連號。
                 每次讀都從頭重放、每筆照同一套規則驗(非法轉移 ⇒ 整份讀不起來,不猜)⇒ 重開機接得回。
                 最後一行沒寫完(斷電)⇒ 當沒寫,下次寫入前截掉。
  charter.json / roster.json / budget.json / tasks.jsonl   重放後的快照(給畫面 / skill 讀;不當正本)。
  qa/<收據 id>.json   品管收據原件。

狀態機:
  團   draft 草稿 → ready 待開始 → running 進行中 ⇄ paused 暫停;running|paused → halted 叫停 → running(本人再按開始)
       running → verified 已驗證(全部任務 passed,機器判)→ handed 待人收下 → archived 已封存(本人收下)
       verified|handed → running(退回重做)
  任務 todo 待做 → assigned 已派 → doing 做中 ⇄ blocked 卡關;doing → qa 品管中 → passed 通過 | returned 退回 → assigned
       租期 LEASE_S(15 分):派工 / 每次回報續租;過期 ⇒ 回 todo、派工代號作廢(PM 改派發新代號,舊代號的回報拒收)。
       passed 只有品管收據翻得了(PM / 工人 / 本人都不行);改驗收(amend)⇒ 舊通過作廢回 qa 重驗。
唯一識別 + 版本:每筆指令可帶 --eid;同 eid 同內容 ⇒ 回原結果不重做,同 eid 不同內容 ⇒ eid_conflict。
  任務帶 rev(每次變動 +1,--if-rev 不合 ⇒ stale_rev)、accept_rev(驗收版本)、code(派工代號)。

本人動作(開始 / 收下)走 approve_gate 的 Touch ID;非 Mac / 叫不出 / 取消 ⇒ 拒。
同帳號威脅模型(GUIDE-same-user-ai-threat-model):missions/ 所有 AI 都寫得到;--by 是自報。角色表、租期、eid、
  收據格式只防誤操作與 AI 之間互踩,不是防線。真正的防線只有 Touch ID 那兩步;改了驗得出要等 S4 的 SE 簽授權單。
  收據裡的 rc / CI 結論也是自報 ⇒ show 的 verified_pct 只算 ci / pr_merged 兩種(S2 起由核心重跑再補)。
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
LEASE_S = 900
ID_RE = re.compile(r"^m_[0-9a-f]{12}$")
TASK_RE = re.compile(r"^t[0-9]{1,4}$")
EID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
CODE_RE = re.compile(r"^[0-9a-f]{12}$")
SEAT_RE = re.compile(r"^[0-9a-f]{5}$")
HEAD_RE = re.compile(r"^[0-9a-f]{7,40}$")
BY_RE = re.compile(r"^(seat:[0-9a-f]{5}|human)$")
ROLES = ("pm", "worker", "qa", "monitor")
TEAM = ("draft", "ready", "running", "paused", "halted", "verified", "handed", "archived")
TASK = ("todo", "assigned", "doing", "blocked", "qa", "passed", "returned", "cancelled")
LEASED = ("assigned", "doing", "blocked")
RECEIPT_KINDS = ("test", "ci", "pr_merged", "checklist")
MACHINE_VERIFIED = ("ci", "pr_merged")
TEXT_MAX, LIST_MAX = 200, 20

# 團的轉移:動作 ⇒ (從哪些狀態, 到, 誰能做)。human 那兩步另過 Touch ID(_human_gate)。
TEAM_MOVES = {
    "submit":  (("draft",), "ready", ("pm",)),
    "start":   (("ready", "halted"), "running", ("human",)),
    "pause":   (("running",), "paused", ("pm", "monitor", "human")),
    "resume":  (("paused",), "running", ("pm", "human")),
    "halt":    (("running", "paused"), "halted", ("pm", "monitor", "human")),
    "verify":  (("running",), "verified", ("pm", "qa", "monitor", "human")),
    "handoff": (("verified",), "handed", ("pm",)),
    "accept":  (("handed",), "archived", ("human",)),
    "reopen":  (("verified", "handed"), "running", ("pm", "human")),
}
HUMAN_SITES = {"start": "mission.start", "accept": "mission.accept"}
# 任務的回報:工人自己能翻的(其餘只有 PM 派工 / 品管 / 租期到期翻)
REPORT_MOVES = {"assigned": ("doing",), "doing": ("doing", "blocked", "qa"), "blocked": ("doing", "blocked")}
PLANNING = ("draft", "ready", "running", "paused")

PROVE = None          # 測試換掉 Touch ID(reason, env) ⇒ None 過 / 原因碼


class MissionError(Exception):
    pass


def _txt(v, n=TEXT_MAX):
    import touchid_approve as TA
    return TA.clean(v, n) if isinstance(v, str) else ""


def _list(v):
    out = [_txt(x) for x in (v or []) if isinstance(x, str) and _txt(x)]
    if len(out) > LIST_MAX:
        raise MissionError("too_many")
    return out


# ── 檔 ──

def root(env=None):
    import seats_dir_lib
    return Path(seats_dir_lib.state_root(env)[0]) / "missions"


def mdir(mid, env=None):
    if not ID_RE.match(str(mid or "")):
        raise MissionError("bad_id")
    return root(env) / mid


def _mkdir(p):
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p), 0o700)


@contextmanager
def _locked(d):
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
    """⇒ (事件清單, 斷尾位元組位置或 None)。中間有壞行 ⇒ corrupt。"""
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
        except ValueError:
            if pos + len(line) == len(raw) and not line.endswith(b"\n"):
                torn = pos                     # 斷電寫一半的最後一行:當沒寫
                break
            raise MissionError("corrupt:%d" % (len(evs) + 1))
        evs.append(ev)
        pos += len(line)
    return evs, torn


def _empty(mid):
    return {"v": V, "id": mid, "status": None, "rev": 0, "seq": 0, "charter": {}, "budget": {}, "roster": {},
            "tasks": {}, "eids": {}, "receipts": {}, "created_at": None, "updated_at": None}


def replay(mid, env=None):
    """從 events.jsonl 重放出現在的樣子;每筆照 apply 的規則驗,有一筆不合 ⇒ corrupt:<seq>。"""
    return _load(mdir(mid, env), mid)[0]


def _load(d, mid):
    """⇒ (state, 斷尾位置, 事件清單)。"""
    evs, torn = _read_events(d)
    st = _empty(mid)
    for ev in evs:
        try:
            apply(st, ev)
        except MissionError as err:
            raise MissionError("corrupt:%s:%s" % (ev.get("seq"), err))
    return st, torn, evs


# ── 規則(指令與重放共用一套)──

def _role(st, by):
    if by == "human":
        return "human"
    seat = by[5:] if isinstance(by, str) and by.startswith("seat:") else None
    r = st["roster"].get(seat) if seat else None
    return r["role"] if r and not r.get("left_at") else None


def _need(st, by, roles):
    if _role(st, by) not in roles:
        raise MissionError("not_allowed")


def _task(st, tid):
    t = st["tasks"].get(tid)
    if not t:
        raise MissionError("no_task")
    return t


def _bump(t, at, status=None):
    t["rev"] += 1
    t["updated_at"] = at
    if status:
        t["status"] = status


def _check_receipt(rec, t, tid):
    """品管收據:綁任務 + 驗收版本 + 產物版本,而且要有機器證據(rc / CI / 已合 / 逐項勾)。回清過的收據。"""
    if not isinstance(rec, dict) or rec.get("kind") not in RECEIPT_KINDS:
        raise MissionError("bad_receipt")
    if rec.get("task") != tid or rec.get("accept_rev") != t["accept_rev"]:
        raise MissionError("receipt_stale")
    if t.get("artifact") and rec.get("artifact") != t["artifact"]:
        raise MissionError("receipt_stale")
    k = rec["kind"]
    out = {"kind": k, "task": tid, "accept_rev": t["accept_rev"], "artifact": t.get("artifact"),
           "cmd": _txt(rec.get("cmd"), 400)}
    if k == "test":
        ok = (type(rec.get("rc")) is int and rec["rc"] == 0 and out["cmd"] and type(rec.get("total")) is int
              and rec["total"] > 0 and rec.get("passed") == rec["total"])
        out.update(rc=rec.get("rc"), passed=rec.get("passed"), total=rec.get("total"))
    elif k == "ci":
        ok = rec.get("conclusion") == "success" and HEAD_RE.match(str(rec.get("head") or "")) is not None
        out.update(conclusion=rec.get("conclusion"), head=rec.get("head"), url=_txt(rec.get("url"), 200))
    elif k == "pr_merged":
        ok = rec.get("merged") is True and HEAD_RE.match(str(rec.get("head") or "")) is not None
        out.update(merged=rec.get("merged"), head=rec.get("head"), url=_txt(rec.get("url"), 200))
    else:
        items = rec.get("items") if isinstance(rec.get("items"), list) else []
        clean = [{"text": _txt(i.get("text")), "ok": i.get("ok") is True} for i in items if isinstance(i, dict)]
        ok = bool(clean) and all(i["ok"] and i["text"] for i in clean) and len(clean) <= LIST_MAX
        out["items"] = clean
    if not ok:
        raise MissionError("receipt_not_green")
    out["machine_verified"] = k in MACHINE_VERIFIED
    return out


def apply(st, ev):
    """套一筆事件。不合規則 ⇒ MissionError(指令端拒收;重放端 = 檔被改過或壞了)。"""
    if not isinstance(ev, dict) or ev.get("v") != V or ev.get("seq") != st["seq"] + 1:
        raise MissionError("bad_seq")
    typ, by, at, d = ev.get("type"), ev.get("by"), ev.get("at"), ev.get("data")
    if not isinstance(d, dict) or type(at) is not int or not EID_RE.match(str(ev.get("eid") or "")):
        raise MissionError("bad_event")
    if ev["eid"] in st["eids"]:
        raise MissionError("dup_eid")
    if typ != "expire" and not BY_RE.match(str(by or "")):
        raise MissionError("bad_by")
    if typ == "expire" and by != "system":
        raise MissionError("bad_by")
    if st["status"] is None and typ != "created":
        raise MissionError("not_created")
    if st["status"] == "archived":
        raise MissionError("archived")
    tid = ev.get("task")
    if typ == "created":
        if st["status"] is not None:
            raise MissionError("exists")
        pm = d.get("pm")
        if not SEAT_RE.match(str(pm or "")):
            raise MissionError("bad_seat")
        st.update(status="draft", created_at=at, charter={"title": _txt(d.get("title"), 80), "goal": _txt(d.get("goal")),
                                                          "accept": _list(d.get("accept"))},
                  budget={k: d["budget"][k] for k in ("minutes", "seats_max") if isinstance(d.get("budget"), dict)
                          and type(d["budget"].get(k)) is int and d["budget"][k] > 0})
        if not st["charter"]["title"]:
            raise MissionError("no_title")
        st["roster"][pm] = {"role": "pm", "joined_at": at, "left_at": None}
    elif typ == "team":
        mv = TEAM_MOVES.get(d.get("move"))
        if not mv:
            raise MissionError("bad_move")
        frm, to, who = mv
        _need(st, by, who)
        if st["status"] not in frm:
            raise MissionError("bad_transition")
        if d["move"] == "verify":
            live = [t for t in st["tasks"].values() if t["status"] != "cancelled"]
            if not live or any(t["status"] != "passed" for t in live):
                raise MissionError("not_all_passed")
        if d["move"] == "start" and d.get("proved") is not True:
            raise MissionError("not_proved")
        if d["move"] == "accept" and d.get("proved") is not True:
            raise MissionError("not_proved")
        if to == "running" and st["status"] in ("paused", "halted"):
            for t in st["tasks"].values():        # 暫停期間不算租期:回來時重給一整段
                if t["status"] in LEASED:
                    t["lease_until"] = at + LEASE_S
        st["status"] = to
    elif typ == "roster":
        _need(st, by, ("pm", "human"))
        seat, role = d.get("seat"), d.get("role")
        if not SEAT_RE.match(str(seat or "")):
            raise MissionError("bad_seat")
        if d.get("op") == "add":
            if role not in ROLES:
                raise MissionError("bad_role")
            cur = st["roster"].get(seat)
            if cur and not cur.get("left_at"):
                raise MissionError("already_in")
            st["roster"][seat] = {"role": role, "joined_at": at, "left_at": None}
        elif d.get("op") == "remove":
            cur = st["roster"].get(seat)
            if not cur or cur.get("left_at"):
                raise MissionError("not_in")
            cur["left_at"] = at
            for t in st["tasks"].values():        # 離隊 ⇒ 手上的任務回待做(同租期過期)
                if t.get("assignee") == seat and t["status"] in LEASED:
                    t.update(assignee=None, code=None, lease_until=None)
                    _bump(t, at, "todo")
        else:
            raise MissionError("bad_op")
    elif typ == "task_add":
        _need(st, by, ("pm",))
        if st["status"] not in PLANNING:
            raise MissionError("bad_transition")
        if not TASK_RE.match(str(tid or "")) or tid in st["tasks"]:
            raise MissionError("bad_task")
        deps = [x for x in (d.get("deps") or []) if x in st["tasks"]]
        parent = d.get("parent")
        if parent is not None and parent not in st["tasks"]:
            raise MissionError("bad_parent")
        weight = d.get("weight", 1)
        if type(weight) is not int or not 1 <= weight <= 100:
            raise MissionError("bad_weight")
        title = _txt(d.get("title"), 80)
        if not title:
            raise MissionError("no_title")
        st["tasks"][tid] = {"id": tid, "title": title, "accept": _list(d.get("accept")), "deps": deps, "parent": parent, "weight": weight,
                            "est": None, "status": "todo",
                            "rev": 1, "accept_rev": 1, "assignee": None, "code": None, "lease_until": None,
                            "artifact": None, "receipt": None, "workers": [], "codes": [], "created_at": at, "updated_at": at}
    elif typ in ("dispatch", "report", "qa", "cancel", "amend", "expire"):
        t = _task(st, tid)
        if type(d.get("if_rev")) is int and d["if_rev"] != t["rev"]:
            raise MissionError("stale_rev")
        if typ == "dispatch":
            _need(st, by, ("pm",))
            if st["status"] != "running":
                raise MissionError("team_not_running")
            if t["status"] not in ("todo", "returned"):
                raise MissionError("bad_transition")
            to = d.get("to")
            r = st["roster"].get(to)
            if not r or r.get("left_at") or r["role"] != "worker":
                raise MissionError("not_worker")
            if not CODE_RE.match(str(d.get("code") or "")) or d["code"] in t["codes"]:
                raise MissionError("bad_code")          # 代號一次性:用過的(含已作廢的)不能再發
            undone = [x for x in t["deps"] if st["tasks"][x]["status"] not in ("passed", "cancelled")]
            if undone:
                raise MissionError("deps_open")
            t.update(assignee=to, code=d["code"], lease_until=at + LEASE_S)
            t["codes"].append(d["code"])
            if to not in t["workers"]:
                t["workers"].append(to)
            _bump(t, at, "assigned")
        elif typ == "report":
            if st["status"] != "running":
                raise MissionError("team_not_running")
            if d.get("code") != t.get("code"):
                raise MissionError("stale_code")       # 舊代號(過期 / 改派前)的結果一律拒收
            if by != "seat:%s" % t.get("assignee"):
                raise MissionError("not_assignee")
            if t["status"] not in REPORT_MOVES or (t.get("lease_until") or 0) < at:
                raise MissionError("lease_expired" if t["status"] in LEASED else "bad_transition")
            nxt = d.get("state")
            if nxt not in REPORT_MOVES[t["status"]]:
                raise MissionError("bad_transition")
            if nxt == "qa":
                art = d.get("artifact")
                if art is not None and not HEAD_RE.match(str(art)):
                    raise MissionError("bad_artifact")
                t.update(artifact=art, lease_until=None)
            else:
                t["lease_until"] = at + LEASE_S
            est = d.get("est")
            if est is not None and (type(est) is not int or not 0 <= est <= 100):
                raise MissionError("bad_est")
            if est is not None:
                t["est"] = est                   # 工人自己估的 %,戰情室標「估計」;已驗證只看收據
            t["note"] = _txt(d.get("note"))
            _bump(t, at, nxt)
        elif typ == "qa":
            _need(st, by, ("qa",))
            if st["status"] != "running":
                raise MissionError("team_not_running")
            if t["status"] != "qa":
                raise MissionError("bad_transition")
            if by[5:] in t["workers"]:
                raise MissionError("maker_is_checker")
            if d.get("verdict") == "pass":
                rec = _check_receipt(d.get("receipt"), t, tid)
                rid = d.get("receipt_id")
                if not EID_RE.match(str(rid or "")):
                    raise MissionError("bad_receipt")
                rec.update(id=rid, by=by, at=at)
                st["receipts"][rid] = rec
                t["receipt"] = rid
                _bump(t, at, "passed")
            elif d.get("verdict") == "return":
                t.update(assignee=None, code=None, why=_txt(d.get("why")))
                _bump(t, at, "returned")
            else:
                raise MissionError("bad_verdict")
        elif typ == "cancel":
            _need(st, by, ("pm", "human"))
            if t["status"] in ("passed", "cancelled"):
                raise MissionError("bad_transition")
            t.update(assignee=None, code=None, lease_until=None)
            _bump(t, at, "cancelled")
        elif typ == "amend":
            _need(st, by, ("pm", "human"))
            if st["status"] not in PLANNING or t["status"] == "cancelled":
                raise MissionError("bad_transition")
            t["accept"] = _list(d.get("accept"))
            t["accept_rev"] += 1
            if t["status"] == "passed":             # 驗收改了:舊「通過」作廢,回品管重驗
                t["receipt"] = None
                _bump(t, at, "qa")
            else:
                _bump(t, at)
        else:   # expire
            if st["status"] != "running" or t["status"] not in LEASED or (t.get("lease_until") or 0) >= at:
                raise MissionError("not_expired")
            t["expired_from"] = t.get("assignee")
            t.update(assignee=None, code=None, lease_until=None)
            _bump(t, at, "todo")
    else:
        raise MissionError("bad_type")
    st["seq"] = ev["seq"]
    st["eids"][ev["eid"]] = {"req": ev.get("req"), "seq": ev["seq"], "task": tid}
    st["rev"] += 1
    st["updated_at"] = at
    return st


# ── 寫 ──

def _req_digest(typ, by, task, data):
    """同 eid 重送比的是「要求」(不含系統產生的代號 / 收據 id)。"""
    keep = {k: v for k, v in data.items() if k not in ("code", "receipt_id", "proved")}
    raw = json.dumps([typ, by, task, keep], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _snapshots(d, st):
    _write(d / "charter.json", json.dumps({"v": V, "id": st["id"], "status": st["status"], "rev": st["rev"],
                                           "charter": st["charter"], "created_at": st["created_at"],
                                           "updated_at": st["updated_at"]}, ensure_ascii=False, sort_keys=True))
    _write(d / "roster.json", json.dumps({"v": V, "roster": st["roster"]}, ensure_ascii=False, sort_keys=True))
    _write(d / "budget.json", json.dumps({"v": V, "budget": st["budget"]}, ensure_ascii=False, sort_keys=True))
    _write(d / "tasks.jsonl", "".join(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n"
                                      for t in sorted(st["tasks"].values(), key=lambda t: int(t["id"][1:]))))


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


def _expire_due(d, st, evs_torn, now):
    """租期過了的任務寫 expire(system)。回寫了幾筆。"""
    n = 0
    if st["status"] != "running":
        return 0
    for t in sorted(st["tasks"].values(), key=lambda t: int(t["id"][1:])):
        if t["status"] in LEASED and (t.get("lease_until") or 0) < now:
            ev = {"v": V, "seq": st["seq"] + 1, "eid": "x_" + secrets.token_hex(8), "req": None, "at": now,
                  "by": "system", "type": "expire", "task": t["id"], "data": {}}
            prev = t.get("assignee")
            apply(st, ev)
            _append(d, ev, evs_torn if n == 0 else None)
            n += 1
            st.setdefault("_expired", []).append({"task": t["id"], "from": prev})
    return n


def commit(mid, typ, by, data, env=None, task=None, eid=None, now=None):
    """寫一筆。回 {"ok", "duplicate", "seq", "state"}。先把過期的租期處理掉(過期的回報才拒得掉)。"""
    now = int(time.time() if now is None else now)
    d = mdir(mid, env)
    if eid is not None and not EID_RE.match(str(eid)):
        raise MissionError("bad_eid")
    req = _req_digest(typ, by, task, data)
    with _locked(d):
        st, torn, evs = _load(d, mid)
        if typ == "created" and evs:
            raise MissionError("exists")
        if eid and eid in st["eids"]:
            if st["eids"][eid]["req"] != req:
                raise MissionError("eid_conflict")
            orig = evs[st["eids"][eid]["seq"] - 1]
            return {"ok": True, "duplicate": True, "seq": orig["seq"], "state": st, "task": orig.get("task"), "event": orig}
        if typ == "task_add" and task is None:     # 編號在鎖內配(兩席同時開單不撞號)
            task = "t%d" % (len(st["tasks"]) + 1)
        n = _expire_due(d, st, torn, now)
        torn = None if n else torn
        ev = {"v": V, "seq": st["seq"] + 1, "eid": eid or ("e_" + secrets.token_hex(8)), "req": req, "at": now,
              "by": by, "type": typ, "task": task, "data": data}
        if task is None:
            ev.pop("task")
        try:
            apply(st, ev)
        except MissionError:
            if n:
                _snapshots(d, st)
            raise
        _append(d, ev, torn)
        _snapshots(d, st)
        return {"ok": True, "duplicate": False, "seq": ev["seq"], "state": st, "task": task, "event": ev}


def tick(mid, env=None, now=None):
    """aib mission tick:把租期過期的任務放回待做。回 [{"task","from"}]。"""
    now = int(time.time() if now is None else now)
    d = mdir(mid, env)
    with _locked(d):
        st, torn, evs = _load(d, mid)
        if not evs:
            raise MissionError("no_mission")
        if _expire_due(d, st, torn, now):
            _snapshots(d, st)
        return st.get("_expired", [])


def new(by, title, goal="", accept=(), pm=None, budget=None, env=None, eid=None, now=None):
    pm = pm or (by[5:] if str(by).startswith("seat:") else None)
    if not SEAT_RE.match(str(pm or "")):
        raise MissionError("bad_seat")
    if eid:                                   # 同 eid 重送 ⇒ 找回原本那團,不開第二團
        for mid in list_ids(env):
            try:
                st = replay(mid, env)
            except MissionError:
                continue
            if eid in st["eids"]:
                if st["eids"][eid]["req"] != _req_digest("created", by, None, _new_data(title, goal, accept, pm, budget)):
                    raise MissionError("eid_conflict")
                return {"ok": True, "duplicate": True, "id": mid, "state": st}
    _mkdir(root(env))
    mid = "m_" + secrets.token_hex(6)
    res = commit(mid, "created", by, _new_data(title, goal, accept, pm, budget), env=env, eid=eid, now=now)
    res["id"] = mid
    return res


def _new_data(title, goal, accept, pm, budget):
    return {"title": title, "goal": goal, "accept": list(accept or []), "pm": pm, "budget": budget or {}}


def list_ids(env=None):
    r = root(env)
    return sorted(p.name for p in r.iterdir() if p.is_dir() and ID_RE.match(p.name)) if r.is_dir() else []


def view(st, now=None):
    """show --json:去掉內部欄位;已驗證 % 只算機器證據(ci / pr_merged),自報的 test 不算。"""
    now = int(time.time() if now is None else now)
    tasks = sorted(st["tasks"].values(), key=lambda t: int(t["id"][1:]))
    live = [t for t in tasks if t["status"] != "cancelled"]
    mv = [t for t in live if t["status"] == "passed" and (st["receipts"].get(t["receipt"]) or {}).get("machine_verified")]
    return {"v": V, "id": st["id"], "status": st["status"], "rev": st["rev"], "seq": st["seq"], "charter": st["charter"],
            "budget": st["budget"], "roster": st["roster"], "created_at": st["created_at"], "updated_at": st["updated_at"],
            "tasks": [dict({k: v for k, v in t.items() if k not in ("code", "codes")}, code_set=bool(t.get("code")),
                           lease_left=max(0, t["lease_until"] - now) if t.get("lease_until") else None) for t in tasks],
            "receipts": st["receipts"],
            "passed": sum(1 for t in live if t["status"] == "passed"), "total": len(live),
            "verified_pct": int(100 * len(mv) / len(live)) if live else 0}


# ── 本人那兩步 ──

def _human_gate(move, mid, st, env):
    """開始 / 收下:approve_gate 組字 + Touch ID(高風險類不開時間窗)。回 None = 過;否則原因碼。"""
    import approve_gate as AG
    site = HUMAN_SITES[move]
    r = AG.reason(site, {"title": st["charter"].get("title") or mid, "id": mid,
                         "n": str(len([t for t in st["tasks"].values() if t["status"] != "cancelled"]))}, env=env)
    if PROVE:
        return PROVE(r, env)
    off = AG.ready(env)
    if off:
        return "touchid_" + off
    res = AG.ask(r, AG.state_root_default(env))
    return None if res["ok"] else "touchid_" + res["why"].split(":")[0]


def team(mid, move, by, env=None, eid=None, now=None):
    data = {"move": move}
    if move in HUMAN_SITES:
        if by != "human":
            raise MissionError("not_allowed")
        st = replay(mid, env)
        why = _human_gate(move, mid, st, env)
        if why:
            raise MissionError(why)
        data["proved"] = True
    return commit(mid, "team", by, data, env=env, eid=eid, now=now)


def load_receipt(path):
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MissionError("bad_receipt")
    return rec


def qa(mid, tid, by, verdict, receipt=None, why="", env=None, eid=None, now=None, if_rev=None):
    data = {"verdict": verdict, "why": why}
    if verdict == "pass":
        data.update(receipt=receipt, receipt_id="r_" + secrets.token_hex(8))
    if if_rev is not None:
        data["if_rev"] = if_rev
    res = commit(mid, "qa", by, data, env=env, task=tid, eid=eid, now=now)
    t = res["state"]["tasks"][tid]
    if not res["duplicate"] and t.get("receipt"):
        d = mdir(mid, env) / "qa"
        _mkdir(d)
        _write(d / (t["receipt"] + ".json"), json.dumps(res["state"]["receipts"][t["receipt"]], ensure_ascii=False,
                                                        sort_keys=True))
    return res


# ── CLI ──

def T(key, **kw):
    import i18n_lib
    try:
        return i18n_lib.t("aib.mission." + key, kw)
    except Exception:   # noqa: BLE001
        return key


def _parser():
    p = argparse.ArgumentParser(prog="aib mission", add_help=True)
    sub = p.add_subparsers(dest="cmd")
    for name in ("new", "list", "show", "roster", "team", "task", "tick"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--by", default=None)
        s.add_argument("--eid", default=None)
        if name == "new":
            s.add_argument("--title", required=True)
            s.add_argument("--goal", default="")
            s.add_argument("--accept", action="append", default=[])
            s.add_argument("--pm", default=None)
            s.add_argument("--minutes", type=int, default=None)
            s.add_argument("--seats-max", type=int, default=None)
        if name in ("show", "roster", "team", "task", "tick"):
            s.add_argument("id")
        if name == "roster":
            s.add_argument("op", choices=("add", "remove"))
            s.add_argument("seat")
            s.add_argument("--role", choices=ROLES, default=None)
        if name == "team":
            s.add_argument("move", choices=sorted(TEAM_MOVES))
        if name == "task":
            s.add_argument("op", choices=("add", "dispatch", "report", "qa", "cancel", "amend"))
            s.add_argument("task", nargs="?")
            s.add_argument("--title", default="")
            s.add_argument("--accept", action="append", default=None)
            s.add_argument("--dep", action="append", default=[])
            s.add_argument("--parent", default=None)
            s.add_argument("--weight", type=int, default=None)
            s.add_argument("--est", type=int, default=None)
            s.add_argument("--to", default=None)
            s.add_argument("--code", default=None)
            s.add_argument("--state", choices=("doing", "blocked", "qa"), default=None)
            s.add_argument("--artifact", default=None)
            s.add_argument("--note", default="")
            s.add_argument("--pass", dest="verdict", action="store_const", const="pass")
            s.add_argument("--return", dest="verdict", action="store_const", const="return")
            s.add_argument("--receipt", default=None)
            s.add_argument("--why", default="")
            s.add_argument("--if-rev", type=int, default=None)
    return p


def _by(a):
    if not BY_RE.match(str(a.by or "")):
        raise MissionError("bad_by")
    return a.by


def run(argv, env=None, out=None):
    out = out or sys.stdout
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "list":
            rows = []
            for mid in list_ids(env):
                try:
                    st = replay(mid, env)
                    rows.append({"id": mid, "status": st["status"], "title": st["charter"].get("title"),
                                 "updated_at": st["updated_at"]})
                except MissionError as err:
                    rows.append({"id": mid, "status": None, "error": str(err)})
            return _emit(out, a, {"ok": True, "missions": rows},
                         "\n".join("%s  %s  %s" % (r["id"], T("st." + (r["status"] or "unreadable")), r.get("title") or r.get("error"))
                                   for r in rows) or T("none"))
        if a.cmd == "show":
            v = view(replay(a.id, env))
            if v["status"] is None:
                raise MissionError("no_mission")
            text = "%s  %s  %s  %d/%d\n" % (v["id"], T("st." + v["status"]), v["charter"]["title"], v["passed"], v["total"])
            text += "\n".join("  %s  %s  %s  @%s" % (t["id"], T("task." + t["status"]), t["title"], t.get("assignee") or "-")
                              for t in v["tasks"])
            return _emit(out, a, dict(v, ok=True), text.rstrip())
        if a.cmd == "tick":
            got = tick(a.id, env)
            return _emit(out, a, {"ok": True, "expired": got}, T("ok"))
        by = _by(a)
        if a.cmd == "new":
            budget = {k: v for k, v in (("minutes", a.minutes), ("seats_max", a.seats_max)) if v}
            res = new(by, a.title, a.goal, a.accept, a.pm, budget, env=env, eid=a.eid)
            return _emit(out, a, {"ok": True, "id": res["id"], "duplicate": res["duplicate"]}, res["id"])
        if a.cmd == "roster":
            res = commit(a.id, "roster", by, {"op": a.op, "seat": a.seat, "role": a.role}, env=env, eid=a.eid)
        elif a.cmd == "team":
            res = team(a.id, a.move, by, env=env, eid=a.eid)
        else:
            res = _task_cmd(a, by, env)
        st = res["state"]
        body = {"ok": True, "duplicate": res["duplicate"], "seq": res["seq"], "status": st["status"]}
        if a.cmd == "task":
            tid = res.get("task") or a.task
            t = st["tasks"].get(tid) or {}
            body.update(task=tid, task_status=t.get("status"), rev=t.get("rev"))
            if a.op == "dispatch" and res["event"]["data"].get("code") == t.get("code"):
                body["code"] = t.get("code")    # 派工代號交給工人回報用;重送同 eid 也拿得回(還有效才給)
        return _emit(out, a, body, T("ok"))
    except MissionError as err:
        code = str(err)
        if a.json:
            out.write(json.dumps({"ok": False, "error": code}, ensure_ascii=False) + "\n")
        else:
            out.write(T("err", code=code) + "\n")
        return 2


def _task_cmd(a, by, env):
    if a.op == "add":                          # 重送同 eid ⇒ 回原本那張(commit 從原事件帶回編號)
        data = {"title": a.title, "accept": a.accept or [], "deps": a.dep}
        if a.parent:
            data["parent"] = a.parent
        if a.weight is not None:
            data["weight"] = a.weight
        return commit(a.id, "task_add", by, data, env=env, eid=a.eid)
    if not TASK_RE.match(str(a.task or "")):
        raise MissionError("bad_task")
    data = {} if a.if_rev is None else {"if_rev": a.if_rev}
    if a.op == "dispatch":
        data.update(to=a.to, code=secrets.token_hex(6))
    elif a.op == "report":
        data.update(code=a.code, state=a.state, artifact=a.artifact, note=a.note)
        if a.est is not None:
            data["est"] = a.est
    elif a.op == "qa":
        if a.verdict == "pass" and not a.receipt:
            raise MissionError("bad_receipt")
        return qa(a.id, a.task, by, a.verdict, load_receipt(a.receipt) if a.verdict == "pass" else None, a.why,
                  env=env, eid=a.eid, if_rev=a.if_rev)
    elif a.op == "amend":
        data["accept"] = a.accept or []
    return commit(a.id, {"dispatch": "dispatch", "report": "report", "cancel": "cancel", "amend": "amend"}[a.op],
                  by, data, env=env, task=a.task, eid=a.eid)


def _emit(out, a, body, text):
    out.write((json.dumps(body, ensure_ascii=False) if a.json else text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
