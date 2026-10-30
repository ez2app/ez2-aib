#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""任務團資料契約(S1,SPEC-mission-team-f5fb8-1029「三方審查定案」2 / 3 / 4 / 9)。只用標準庫。

aib mission new|list|show|roster|team|task|tick|budget|watch … [--json]

存在 <狀態根>/missions/<id>/(目錄 0700、檔 0600):
  events.jsonl   正本。一行一筆事件 {v, seq, eid, req, at, by, type, task?, data};seq 從 1 連號。
                 每次讀都從頭重放、每筆照同一套規則驗(非法轉移 ⇒ 整份讀不起來,不猜)⇒ 重開機接得回。
                 最後一行沒寫完(斷電)⇒ 當沒寫,下次寫入前截掉。
  charter.json / roster.json / budget.json / tasks.jsonl   重放後的快照(給畫面 / skill 讀;不當正本)。
  qa/<收據 id>.json   品管收據原件。

狀態機:
  團   draft 草稿 → ready 待開始 → running 進行中 ⇄ paused 暫停;
       ready 倒數 AUTOSTART_S(30 秒,Andy 03:1x 裁示 7):沒人按停(hold)⇒ tick 由 system 寫 autostart;
       hold = 按「改範圍 / 預算 / 只要討論」停倒數,再 submit 一次重新倒數;本人按開始(Touch ID)隨時可以;running|paused → halted 叫停 → running(本人再按開始)
       running → verified 已驗證(全部任務 passed,機器判)→ handed 待人收下 → archived 已封存(本人收下)
       verified|handed → running(退回重做)
  任務 todo 待做 → assigned 已派 → doing 做中 ⇄ blocked 卡關;doing → qa 品管中 → passed 通過 | returned 退回 → assigned
       租期 LEASE_S(15 分):派工 / 每次回報續租;過期 ⇒ 回 todo、派工代號作廢(PM 改派發新代號,舊代號的回報拒收)。
       時間一律讀系統鐘,CLI 不收時間參數(同帳號 AI 能自報時間繞租期;@aeb40 10-29 11:18);只有函式層 now= 給測試。
       passed 只有品管收據翻得了(PM / 工人 / 本人都不行);改驗收(amend)⇒ 舊通過作廢回 qa 重驗。
唯一識別 + 版本:每筆指令可帶 --eid;同 eid 同內容 ⇒ 回原結果不重做,同 eid 不同內容 ⇒ eid_conflict。
  任務帶 rev(每次變動 +1,--if-rev 不合 ⇒ stale_rev)、accept_rev(驗收版本)、code(派工代號)。

本人動作(開始 / 收下)走 approve_gate 的 Touch ID(都是高風險,一次一按);非 Mac / 叫不出 / 取消 ⇒ 拒。
  開始也可以不按:待開始倒數 30 秒沒人按停就自動開始(Andy 03:1x 裁示 7);收下一定要按。
  契約(#414 R1 M1):started_by = "human" | "autostart"(charter.json / show 都有)。自動開始的 running ≠ 本人授權:
  S1 的自動開始只改狀態(不開席、不起行程、不花額度);S4 要開席 / 花額度一定要看 started_by,autostart 只能用免簽額度(grant §7)。
  本人按暫停 ⇒ 只有本人能恢復(paused_by)。暫停 / 恢復的 --by human 是自報(沒過 Touch ID):只防誤操作,不是防線。
同帳號威脅模型(GUIDE-same-user-ai-threat-model):missions/ 所有 AI 都寫得到;--by 是自報。角色表、租期、eid、
  收據格式只防誤操作與 AI 之間互踩,不是防線。真正的防線只有 Touch ID 那兩步;改了驗得出要等 S4 的 SE 簽授權單。
  收據裡的 rc / CI 結論也是自報 ⇒ show 的 verified_pct 只算 ci / pr_merged 兩種(S2 起由核心重跑再補)。

S4 前半(SPEC-mission-monitor-fd787-1029,§8 @f5fb8 裁示):預算硬上限 + 規則監看(不用 LLM)+ aib mission watch。
  預算三維:minutes(團在 running 的時間,從事件算,暫停 / 叫停不算)、seats_max(加人時就擋 over_seats)、
  quota_pct(額度:第一次量到記基準,用量 = 各 (引擎, 視窗) 現值 − 基準 取大;5h / 7d 都算;同帳號別席也算進來 ⇒ 寧可高估)。
  tick 時 80% 寫 budget/warn、100% 寫 budget/halt + team/halt(reason=budget),同一維同一級只寫一次;
  預算叫停後要本人先改預算(budget_set)再按開始。提高預算 = 花額度 ⇒ 只有本人 + Touch ID(site mission.budget,
  高風險一次一按、不進時間窗);調低 PM 也能寫。量不到額度不當 0,寫 anomaly/quota_unmeasured;
  設了額度上限時派工 / 開始量不到 ⇒ 拒 quota_unmeasured(#427 r1 H1);派工 / 進 running 前在團鎖內再驗一次預算。
  照實寫(#427 r2 L5):預算上限只防失控與誤操作,不防同帳號 AI。AI 直接往 events.jsonl 補一筆
  `budget_set by human proved:true` 就能解除叫停、不用 Touch ID;EZ2_USAGE_DIR / 用量紀錄檔也改得到。
  Touch ID 只擋走正常 CLI 的那條路;要真的擋得住,得等 SE 簽的授權單(事件帶簽章、重放時驗)。
  監看規則在 mission_monitor.evaluate(純函式);只寫 budget / anomaly / throttle 事件(都是 system 寫),不改任務、不改計畫。
  誰定期叫 tick:monitor 席每 60 秒叫一次,核心不加常駐。

G0 分組(SPEC-mission-team §A–§G / §J,@22220 REVIEW-mission-groups;10-29 14:11 @aeb40 定範圍):
  mode single → grouped 只走 group/split(pm / 本人):要有契約 Task v1(kind=contract,producer / consumers 都在要拆的組裡)、
  每組一位組長(roster role=lead,不能是 PM ⇒ 總指揮不兼組長)、團裡要有品管;編不足 ⇒ understaffed。
  拆完全團有效席不能超過授權單合計(還沒接 S4 授權單前 = 一張硬頂 GRANT_SEATS 6)⇒ out_of_grant:seats;
  各組預扣分鐘合計 ≤ 團預算 ×(100 − 預備金 15%)、預扣席 ≥ 該組人數 ⇒ 否則 over_group_budget;進行中有人在做還拆 ⇒ 要帶差異單(diff)。
  組員只能進一組;組滿(預扣席)再加人 ⇒ over_group_budget。合組(group/merge)不自動,PM / 本人寫。
  組長(lead):只能在自己組開單(不能開契約)、只派自己組的工給自己組的人;寫別組任務的 task_add / dispatch / report ⇒ cross_group。
  組長每 3 筆該組事件或卡關要寫 group/summary(要 why);未滿 3 筆又不是卡關 ⇒ too_soon(R9 逾期由 G1 監看判)。
  組長離隊 ⇒ 該組已派 / 做中 / 卡關回待做,PM 用 group/lead 改派新組長(不自動把工人升組長)。
  契約 Task:只有 PM 開、group 空、持有人 = PM(派工只准派給持有人,不派給工人)、帶 contract{producer, consumers, path, rev};
  消費組的實作 Task deps 必含該契約;contract_rev(只 PM)⇒ 契約與依賴它的已通過任務作廢回 qa(同 amend)。
  整合收據 kind=integration:只用在 kind=integration 的任務;checks 要含契約本身 + 每一組至少一件已通過的產物、每條帶可重跑指令;
  品管席若在任一被檢查任務的 workers[] ⇒ maker_is_checker;綁組的品管不能寫整合。PM / 組長本來就不能寫品管事件。
  照實寫:組 / 角色 / 契約跟其他名冊欄位一樣都是 events.jsonl 裡的自報資料,只防 AI 之間互踩與誤操作,不是防線。
  ci / pr_merged 收據的 head 要對得上任務回報的 artifact(前綴相符);回報沒帶 artifact ⇒ 只能用 test / checklist 收據。
  verified「品管全過」= 每件任務都有通過的收據;machine_all = 每件的收據都是機器證據(只有 checklist / test 的團也收得了)。
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
AUTOSTART_S = 30
ID_RE = re.compile(r"^m_[0-9a-f]{12}$")
TASK_RE = re.compile(r"^t[0-9]{1,4}$")
EID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
CODE_RE = re.compile(r"^[0-9a-f]{12}$")
SEAT_RE = re.compile(r"^[0-9a-f]{5}$")
HEAD_RE = re.compile(r"^[0-9a-f]{7,40}$")
BY_RE = re.compile(r"^(seat:[0-9a-f]{5}|human)$")
ROLES = ("pm", "worker", "qa", "monitor", "lead")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
GROUPS_MAX = 4
GRANT_SEATS = 6          # 一張授權單的席數硬頂(S4 授權單接上前,分組用這個當授權單合計)
RESERVE_PCT = 15         # 總指揮整合預備金(艦長裁 G-3)
SUMMARY_EVERY = 3        # 組長每 3 筆該組事件或卡關要摘要(定案 10)
TASK_KINDS = (None, "contract", "integration")
TEAM = ("draft", "ready", "running", "paused", "halted", "verified", "handed", "archived")
TASK = ("todo", "assigned", "doing", "blocked", "qa", "passed", "returned", "cancelled")
LEASED = ("assigned", "doing", "blocked")
RECEIPT_KINDS = ("test", "ci", "pr_merged", "checklist", "integration")
MACHINE_VERIFIED = ("ci", "pr_merged")
TEXT_MAX, LIST_MAX = 200, 20
# S4 前半:預算三維(事件裡的 dim ⇒ budget 欄位)、門檻、監看規則、system 才能寫的事件
BUDGET_DIMS = {"minutes": "minutes", "seats": "seats_max", "quota": "quota_pct"}
BUDGET_LEVELS = ("warn", "halt", "base")
WARN_PCT = 80
RULES = ("idle", "now_stale", "now_missing", "qa_loop", "blocked_long", "quota_unmeasured")
SYSTEM_TYPES = ("expire", "budget", "anomaly", "throttle")
BUDGET_SITE = "mission.budget"

# 團的轉移:動作 ⇒ (從哪些狀態, 到, 誰能做)。human 那兩步另過 Touch ID(_human_gate)。
TEAM_MOVES = {
    "submit":  (("draft", "ready"), "ready", ("pm",)),
    "hold":    (("ready",), "ready", ("pm", "human")),
    "autostart": (("ready",), "running", ("system",)),
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
PROBE = None          # 測試換掉監看量測(mission_monitor.Probe 的替身);寫入路徑驗額度用
BUDGET_MAX = {"minutes": 100000, "seats_max": 500, "quota_pct": 100}   # 上限的上限(#427 Codex r1 M2:10**400 也收)


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
def _locked(d, create=False):
    if not create and not d.is_dir():
        raise MissionError("no_mission")          # 不存在的 id 不留空目錄(#414 R1 L4)
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
            "tasks": {}, "eids": {}, "receipts": {}, "created_at": None, "updated_at": None,
            "budget_marks": {}, "budget_block": {}, "anomalies": {}, "throttle": False, "run_s": 0, "run_since": None,
            "last_by": {}, "quota_base": None, "halt_reason": None,
            "mode": "single", "groups": {}, "reserve_pct": RESERVE_PCT}


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
    if status == "blocked" and t["status"] != "blocked":
        t["blocked_since"] = at                  # R5 blocked_long 從進卡關那一刻算(卡關中再回報不重算)
    elif status and status != "blocked":
        t["blocked_since"] = None
    if status:
        t["status"] = status


def _budget(raw):
    """重放用(事件裡的預算):照舊寬鬆解讀 ⇒ 正整數留下、其他丟掉;quota_pct > 100 拒。
    不套 BUDGET_MAX(#427 r2 High:main 寫過的 minutes 100001 / seats_max 501 是合法舊事件,升級後要讀得回來)。
    新輸入一律先過 _budget_input(嚴格),所以新寫的事件不會再有被丟掉的欄位。"""
    if not isinstance(raw, dict):
        return {}
    out = {k: raw[k] for k in BUDGET_DIMS.values() if type(raw.get(k)) is int and raw[k] > 0}
    if out.get("quota_pct", 0) > 100:
        raise MissionError("bad_budget")
    return out


def _budget_input(raw, allow_zero=False):
    """新輸入(建團 / 改預算):不認得的欄位、負數、非整數、超過 BUDGET_MAX ⇒ bad_budget,不靜默丟掉
    (#427 Codex r1 M2:丟掉 = 變成不限)。0:建團不收(r2 L3);改預算 allow_zero ⇒ 0 = 拿掉上限。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or any(k not in BUDGET_MAX for k in raw):
        raise MissionError("bad_budget")
    for k, v in raw.items():
        if type(v) is not int or not (0 if allow_zero else 1) <= v <= BUDGET_MAX[k]:
            raise MissionError("bad_budget")
    return dict(raw)


def _raises(old, new):
    """新上限比舊的鬆(調高,或拿掉上限 = 不限)⇒ 花額度。舊的沒設 = 不限,新設一個上限只會更省。"""
    return any(k in old and (k not in new or new[k] > old[k]) for k in BUDGET_DIMS.values())


def used_minutes(st, now):
    """團在 running 的分鐘數(從事件算;暫停 / 叫停 / 待開始不算)。"""
    s = int(st.get("run_s") or 0)
    if st.get("run_since") is not None:
        s += max(0, int(now) - int(st["run_since"]))
    return s // 60


def used_seats(st):
    return sum(1 for r in st["roster"].values() if not r.get("left_at"))


def _uint(v):
    return type(v) is int and v >= 0


def _seat_of(by):
    return by[5:] if isinstance(by, str) and by.startswith("seat:") else None


def _group_of(st, seat):
    """這席現在在哪一組(只有 grouped 模式算;不在名冊 / 離隊 / 沒分組 ⇒ None)"""
    r = st["roster"].get(seat) if seat else None
    return r.get("group_id") if r and not r.get("left_at") and st["mode"] == "grouped" else None


def _live_group(st, gid):
    g = st["groups"].get(gid) if isinstance(gid, str) else None
    if st["mode"] != "grouped" or not g or g.get("merged_at"):
        raise MissionError("bad_group")
    return g


def _group_staff(st, gid):
    return [s for s, r in st["roster"].items() if not r.get("left_at") and r.get("group_id") == gid]


def _contracts(st):
    return [t for t in st["tasks"].values() if t.get("kind") == "contract" and t["status"] != "cancelled"]


def _cross(st, by, t):
    """組長 / 組員碰別組的任務 ⇒ cross_group(PM / 本人 / 沒分組的人不在這裡擋)"""
    mine = _group_of(st, _seat_of(by))
    if st["mode"] == "grouped" and _role(st, by) in ("lead", "worker") and mine != t.get("group"):
        raise MissionError("cross_group")


def _check_integration(st, rec, by):
    """整合收據(艦長裁 G-1):checks 要含契約 + 每一組至少一件已通過的產物,每條有可重跑指令;做過其中任一件 ⇒ maker_is_checker"""
    cid = rec.get("contract")
    c = st["tasks"].get(cid) if isinstance(cid, str) else None
    if not c or c.get("kind") != "contract" or c["status"] != "passed":
        raise MissionError("receipt_stale")
    checks = rec.get("checks") if isinstance(rec.get("checks"), list) else []
    if not checks or len(checks) > LIST_MAX:
        raise MissionError("bad_receipt")
    seen, clean = set(), []
    for ck in checks:
        x = st["tasks"].get(ck.get("task")) if isinstance(ck, dict) and isinstance(ck.get("task"), str) else None
        cmd = _txt(ck.get("cmd"), 400) if isinstance(ck, dict) else ""
        if not x or ck.get("ok") is not True or not cmd or x["status"] != "passed":
            raise MissionError("receipt_not_green")
        if _seat_of(by) in x["workers"]:
            raise MissionError("maker_is_checker")
        seen.add(x["id"])
        clean.append({"task": x["id"], "cmd": cmd, "ok": True})
    groups = {c["contract"]["producer"], *c["contract"]["consumers"]}
    covered = {st["tasks"][i].get("group") for i in seen}
    if cid not in seen or not groups <= covered:
        raise MissionError("receipt_not_green")
    return {"contract": cid, "checks": clean}


def _check_receipt(rec, t, tid):
    """品管收據:綁任務 + 驗收版本 + 產物版本,而且要有機器證據(rc / CI / 已合 / 逐項勾)。回清過的收據。"""
    if not isinstance(rec, dict) or rec.get("kind") not in RECEIPT_KINDS:
        raise MissionError("bad_receipt")
    if rec.get("task") != tid or rec.get("accept_rev") != t["accept_rev"]:
        raise MissionError("receipt_stale")
    if t.get("artifact") and rec.get("artifact") != t["artifact"]:
        raise MissionError("receipt_stale")
    k = rec["kind"]
    if k in MACHINE_VERIFIED:
        head, art = str(rec.get("head") or ""), str(t.get("artifact") or "")
        if not art or not HEAD_RE.match(head) or not (head.startswith(art) or art.startswith(head)):
            raise MissionError("receipt_stale")      # CI / 已合要綁到這件任務的產物頭碼(#414 R1 M2)
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
    elif k == "integration":
        ok = True                                   # 內容由 _check_integration 驗(要看整個團的狀態)
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
    system = typ in SYSTEM_TYPES or (typ == "team" and (d.get("move") == "autostart"
                                                       or (d.get("move") == "halt" and d.get("reason") == "budget")))
    if (by == "system") != system or (not system and not BY_RE.match(str(by or ""))):
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
                  budget=_budget(d.get("budget")))
        if not st["charter"]["title"]:
            raise MissionError("no_title")
        st["roster"][pm] = {"role": "pm", "joined_at": at, "left_at": None}
    elif typ == "team":
        mv = TEAM_MOVES.get(d.get("move"))
        if not mv:
            raise MissionError("bad_move")
        frm, to, who = mv
        if who != ("system",) and by != "system":     # system 只寫得了 autostart 與預算叫停(上面 bad_by 擋)
            _need(st, by, who)
        if st["status"] not in frm:
            raise MissionError("bad_transition")
        if d["move"] == "submit":
            if st["status"] == "ready" and not st.get("hold"):
                raise MissionError("bad_transition")
            st.update(ready_at=at, hold=False)
        if d["move"] == "hold":
            if st.get("hold"):
                raise MissionError("bad_transition")
            st["hold"] = True
        if d["move"] == "autostart" and (st.get("hold") or at - int(st.get("ready_at") or at) < AUTOSTART_S):
            raise MissionError("not_due")
        if d["move"] == "verify":
            live = [t for t in st["tasks"].values() if t["status"] != "cancelled"]
            if not live or any(t["status"] != "passed" for t in live):
                raise MissionError("not_all_passed")
        if d["move"] == "start" and d.get("proved") is not True:
            raise MissionError("not_proved")
        if d["move"] == "start" and st["status"] == "halted" and _over_budget(st, at):
            raise MissionError("over_budget")       # 預算叫停後沒先改預算 ⇒ 開了下一次 tick 又停(S4 §1.2)
        if d["move"] == "halt":
            st["halt_reason"] = "budget" if by == "system" else ("human" if by == "human" else _role(st, by))
        if d["move"] == "accept" and d.get("proved") is not True:
            raise MissionError("not_proved")
        if d["move"] == "resume" and st.get("paused_by") == "human" and by != "human":
            raise MissionError("not_allowed")       # 本人按的暫停只有本人能恢復(#414 R1 L9)
        if d["move"] == "pause":
            st["paused_by"] = "human" if by == "human" else _role(st, by)
        if d["move"] in ("autostart", "start"):
            st.update(started_by="autostart" if d["move"] == "autostart" else "human", started_at=at)   # #414 R1 M1
        if to == "running" and st["status"] in ("paused", "halted"):
            for t in st["tasks"].values():        # 暫停期間不算租期:回來時重給一整段
                if t["status"] in LEASED:
                    t["lease_until"] = at + LEASE_S
        if st["status"] == "running" and to != "running":     # 預算分鐘:只算 running 的時間
            st["run_s"] = int(st.get("run_s") or 0) + max(0, at - int(st["run_since"]))
            st["run_since"] = None
        elif to == "running" and st["status"] != "running":
            st["run_since"] = at
        if to == "running":
            st["halt_reason"] = None
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
            if st["budget"].get("seats_max") and used_seats(st) >= st["budget"]["seats_max"]:
                raise MissionError("over_seats")    # 席數是硬上限:加人時就擋,不等事後叫停
            gid = d.get("group")
            if gid is not None:                     # 分組後加進某組:只收工人 / 品管(組長走 group/lead);PM / 監看不屬於任何組
                g = _live_group(st, gid)
                if role not in ("worker", "qa"):
                    raise MissionError("bad_group")
                if len(_group_staff(st, gid)) >= g["quota"]["seats"]:
                    raise MissionError("over_group_budget")
            if st["mode"] == "grouped" and used_seats(st) >= GRANT_SEATS * st.get("grants", 1):
                raise MissionError("out_of_grant:seats")
            st["roster"][seat] = {"role": role, "joined_at": at, "left_at": None}
            if gid is not None:
                st["roster"][seat]["group_id"] = gid
        elif d.get("op") == "remove":
            cur = st["roster"].get(seat)
            if not cur or cur.get("left_at"):
                raise MissionError("not_in")
            cur["left_at"] = at
            led = [g for g in st["groups"].values() if g.get("lead") == seat and not g.get("merged_at")]
            for g in led:                         # 組長離隊:該組已派 / 做中 / 卡關都回待做,等 PM 用 group/lead 改派(不自動升工人)
                g["lead"] = None
            for t in st["tasks"].values():        # 離隊 ⇒ 手上的任務回待做(同租期過期)
                if t["status"] in LEASED and (t.get("assignee") == seat or any(t.get("group") == g["id"] for g in led)):
                    t.update(assignee=None, code=None, lease_until=None)
                    _bump(t, at, "todo")
        else:
            raise MissionError("bad_op")
    elif typ == "task_add":
        _need(st, by, ("pm", "lead"))
        kind, gid = d.get("kind"), d.get("group")
        if kind not in TASK_KINDS:
            raise MissionError("bad_kind")
        if _role(st, by) == "lead":               # 組長只在自己組開一般單(不能開契約 / 整合)
            if st["mode"] != "grouped" or gid != _group_of(st, _seat_of(by)) or gid is None:
                raise MissionError("cross_group")
            if kind is not None:
                raise MissionError("not_allowed")
        if gid is not None:
            _live_group(st, gid)
            if kind is not None:
                raise MissionError("bad_group")       # 契約 / 整合不屬於任何一組
        if st["status"] not in PLANNING:
            raise MissionError("bad_transition")
        if not TASK_RE.match(str(tid or "")) or tid in st["tasks"]:
            raise MissionError("bad_task")
        deps = d.get("deps") or []
        if not isinstance(deps, list) or any(not isinstance(x, str) or x not in st["tasks"] for x in deps):
            raise MissionError("bad_dep")             # 打錯的依賴不能當沒依賴(#414 R1 L3)
        parent = d.get("parent")
        if parent is not None and parent not in st["tasks"]:
            raise MissionError("bad_parent")
        weight = d.get("weight", 1)
        if type(weight) is not int or not 1 <= weight <= 100:
            raise MissionError("bad_weight")
        title = _txt(d.get("title"), 80)
        if not title:
            raise MissionError("no_title")
        extra = {}
        if kind == "contract":
            producer, consumers, path = d.get("producer"), d.get("consumers"), _txt(d.get("path"), 200)
            if (not GROUP_RE.match(str(producer or "")) or not isinstance(consumers, list) or not 1 <= len(consumers) < GROUPS_MAX
                    or any(not GROUP_RE.match(str(c or "")) for c in consumers) or len(set(consumers)) != len(consumers)
                    or producer in consumers or not path):
                raise MissionError("bad_contract")
            extra = {"kind": kind, "holder": _seat_of(by),
                     "contract": {"producer": producer, "consumers": list(consumers), "path": path, "rev": 1}}
        elif kind == "integration":
            extra = {"kind": kind}
        if gid is not None:
            need = [c["id"] for c in _contracts(st) if gid in c["contract"]["consumers"]]
            if any(c not in deps for c in need):
                raise MissionError("contract_dep_missing")   # 消費組的實作 Task 一定要等契約(定案 9)
            extra["group"] = gid
        st["tasks"][tid] = {"id": tid, "title": title, "accept": _list(d.get("accept")), "deps": deps, "parent": parent, "weight": weight,
                            "est": None, "status": "todo",
                            "rev": 1, "accept_rev": 1, "assignee": None, "code": None, "lease_until": None,
                            "artifact": None, "receipt": None, "workers": [], "codes": [], "created_at": at, "updated_at": at,
                            **extra}
    elif typ in ("dispatch", "report", "qa", "cancel", "amend", "expire"):
        t = _task(st, tid)
        if type(d.get("if_rev")) is int and d["if_rev"] != t["rev"]:
            raise MissionError("stale_rev")
        if typ == "dispatch":
            _need(st, by, ("pm", "lead"))
            if _role(st, by) == "lead" and (st["mode"] != "grouped" or t.get("group") is None):
                raise MissionError("cross_group")   # 沒分組時組長不派工;沒組的任務(契約 / 整合)只有 PM 派
            _cross(st, by, t)                     # 組長只派自己組的任務
            if st["status"] != "running":
                raise MissionError("team_not_running")
            if t["status"] not in ("todo", "returned"):
                raise MissionError("bad_transition")
            to = d.get("to")
            r = st["roster"].get(to)
            if t.get("kind") == "contract":         # 契約只派給持有人(總指揮),不派給工人
                if to != t.get("holder") or _role(st, "seat:%s" % to) != "pm":
                    raise MissionError("contract_holder")
            elif not r or r.get("left_at") or r["role"] not in ("worker", "lead"):
                raise MissionError("not_worker")
            elif st["mode"] == "grouped" and t.get("group") and r.get("group_id") != t["group"]:
                raise MissionError("cross_group")   # 有組的任務只派給同組的人
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
            _cross(st, by, t)
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
            mine = _group_of(st, _seat_of(by))
            if mine is not None and t.get("group") != mine:
                raise MissionError("cross_group")       # 綁組的品管只驗自己組(整合要全團品管)
            if d.get("verdict") == "pass":
                rec = _check_receipt(d.get("receipt"), t, tid)
                if (rec["kind"] == "integration") != (t.get("kind") == "integration"):
                    raise MissionError("bad_receipt")   # 整合收據只給整合任務,整合任務只收整合收據
                if rec["kind"] == "integration":
                    rec.update(_check_integration(st, d["receipt"], by))
                rid = d.get("receipt_id")
                if not EID_RE.match(str(rid or "")):
                    raise MissionError("bad_receipt")
                rec.update(id=rid, by=by, at=at)
                st["receipts"][rid] = rec
                t.update(receipt=rid, returns=0)
                _bump(t, at, "passed")
            elif d.get("verdict") == "return":
                t.update(assignee=None, code=None, why=_txt(d.get("why")), returns=int(t.get("returns") or 0) + 1)
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
            t["returns"] = 0                        # 改驗收 ⇒ 品管連退重新計(R4)
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
    elif typ == "group":
        _apply_group(st, by, at, d)
    elif typ == "contract_rev":
        _need(st, by, ("pm",))
        t = _task(st, tid)
        if t.get("kind") != "contract" or t["status"] == "cancelled" or st["status"] not in PLANNING:
            raise MissionError("bad_transition")
        if type(d.get("if_rev")) is int and d["if_rev"] != t["rev"]:
            raise MissionError("stale_rev")
        path = _txt(d.get("path"), 200) if d.get("path") is not None else t["contract"]["path"]
        if not path:
            raise MissionError("bad_contract")
        t["contract"] = dict(t["contract"], path=path, rev=t["contract"]["rev"] + 1)
        for x in [t] + [x for x in st["tasks"].values() if tid in x["deps"]]:
            x["accept_rev"] += 1                    # 舊 rev 的通過作廢、回品管重驗(沿用 amend)
            if x["status"] == "passed":
                x["receipt"] = None
                _bump(x, at, "qa")
            else:
                _bump(x, at)
    elif typ == "budget":
        _apply_budget(st, d)
    elif typ == "budget_set":
        _need(st, by, ("pm", "human"))
        new = _budget(d.get("budget"))
        if new == st["budget"]:
            raise MissionError("no_change")           # 沒改內容不收:不然一筆空改就能清掉叫停記號
        if _raises(st["budget"], new):
            if by != "human":
                raise MissionError("not_allowed")   # 提高上限 = 花額度 ⇒ 只有本人
            if d.get("proved") is not True:
                raise MissionError("not_proved")
        old, st["budget"] = st["budget"], new
        reset = d.get("reset")
        if reset is None:                           # 舊事件(5cf99d94 以前):照舊版語意,改預算 = 全部記號與阻擋清掉(r3 High:舊序列要重放得回來)
            st["budget_marks"], st["budget_block"] = {}, {}
        elif reset != "dims":
            raise MissionError("bad_event")
        else:                                       # 新事件:只重計真的改了的那一維(r1 H3)
            for dim, key in BUDGET_DIMS.items():
                if old.get(key) != new.get(key):
                    st["budget_marks"].pop(dim, None)   # 「已通知」:改了就重新通知
                    if key in old and (key not in new or new[key] > old[key]):
                        st["budget_block"].pop(dim, None)   # 「仍要擋」:只有放寬那一維才解除(r2 Low:兩件事分開存)
    elif typ == "anomaly":
        rule, key, end = d.get("rule"), d.get("key"), d.get("end")
        if rule not in RULES or not isinstance(key, str) or not 0 < len(key) <= 40 or type(end) is not bool:
            raise MissionError("bad_event")
        k = rule + ":" + key
        if end != (k in st["anomalies"]):            # 只在進入 / 解除各寫一筆;重複的進入或沒開過的解除 = 檔被改過
            raise MissionError("bad_event")
        if end:
            st["anomalies"].pop(k)
        else:
            st["anomalies"][k] = {"rule": rule, "key": key, "since": at,
                                  "data": {x: d[x] for x in ("last_at", "at", "returns", "since") if _uint(d.get(x))}}
    elif typ == "throttle":
        on, load = d.get("on"), d.get("load1")
        if type(on) is not bool or on == bool(st["throttle"]) or not isinstance(load, (int, float)) or isinstance(load, bool):
            raise MissionError("bad_event")
        st["throttle"] = on
        st["load1"] = load
    else:
        raise MissionError("bad_type")
    if isinstance(by, str) and by.startswith("seat:"):
        st["last_by"][by[5:]] = at                   # R1 idle:這席最後一次自己寫事件
    g = st["groups"].get((st["tasks"].get(tid) or {}).get("group")) if tid else None
    if g and not g.get("merged_at") and st["mode"] == "grouped":
        g["since_summary"] += 1                      # 組長摘要節奏:該組任務每一筆事件都算
    st["seq"] = ev["seq"]
    st["eids"][ev["eid"]] = {"req": ev.get("req"), "seq": ev["seq"], "task": tid}
    st["rev"] += 1
    st["updated_at"] = at
    return st


def _apply_group(st, by, at, d):
    op = d.get("op")
    if op == "split":
        _need(st, by, ("pm", "human"))
        if st["mode"] == "grouped" or st["status"] not in PLANNING:
            raise MissionError("bad_transition")
        rows = d.get("groups")
        if not isinstance(rows, list) or not 2 <= len(rows) <= GROUPS_MAX or any(not isinstance(r, dict) for r in rows):
            raise MissionError("bad_group")
        ids = [r.get("id") for r in rows]
        if any(not GROUP_RE.match(str(i or "")) for i in ids) or len(set(ids)) != len(ids):
            raise MissionError("bad_group")
        c = st["tasks"].get(d.get("contract")) if isinstance(d.get("contract"), str) else None
        if (not c or c.get("kind") != "contract" or c["status"] == "cancelled"
                or not {c["contract"]["producer"], *c["contract"]["consumers"]} <= set(ids)):
            raise MissionError("no_contract")
        active = {s: r for s, r in st["roster"].items() if not r.get("left_at")}
        leads = [r.get("lead") for r in rows]
        if (len(set(leads)) != len(leads) or any((active.get(x) or {}).get("role") != "lead" for x in leads)
                or not any(r["role"] == "qa" for r in active.values())):
            raise MissionError("understaffed")      # 每組一位組長(PM 不兼)+ 團裡有品管;不夠就維持單組
        taken, plan = set(leads), {}
        for r in rows:
            members = r.get("members") or []
            if not isinstance(members, list) or any((active.get(x) or {}).get("role") != "worker" or x in taken for x in members):
                raise MissionError("bad_group")
            taken.update(members)
            q = r.get("quota") if isinstance(r.get("quota"), dict) else {}
            seats, minutes = q.get("seats", 1 + len(members)), q.get("minutes")
            if type(seats) is not int or seats < 1 + len(members) or seats > GRANT_SEATS * 2 \
                    or (minutes is not None and (type(minutes) is not int or minutes < 1)):
                raise MissionError("over_group_budget")
            plan[r["id"]] = {"id": r["id"], "name": _txt(r.get("name"), 40) or r["id"], "lead": r["lead"], "members": list(members),
                             "quota": {"seats": seats, **({"minutes": minutes} if minutes else {})}}
        reserve = d.get("reserve_pct", RESERVE_PCT)
        if type(reserve) is not int or not 0 <= reserve <= 50:
            raise MissionError("bad_group")
        cap = st["budget"].get("minutes")
        if cap and sum(g["quota"].get("minutes", 0) for g in plan.values()) > cap * (100 - reserve) // 100:
            raise MissionError("over_group_budget")  # 預扣合計不能吃掉預備金
        if len(active) > GRANT_SEATS * st.get("grants", 1):
            raise MissionError("out_of_grant:seats")
        if st["budget"].get("seats_max") and sum(g["quota"]["seats"] for g in plan.values()) \
                + sum(1 for s in active if s not in taken) > st["budget"]["seats_max"]:
            raise MissionError("over_seats")
        busy = any(t["status"] in LEASED for t in st["tasks"].values())
        if busy and not _txt(d.get("diff")):
            raise MissionError("no_diff")         # 進行中再拆 = 改範圍:要總指揮的差異單
        for gid, g in plan.items():
            st["groups"][gid] = dict(g, since_summary=0, last_summary=None, created_at=at, merged_at=None)
            for x in [g["lead"]] + g["members"]:
                st["roster"][x]["group_id"] = gid
        st.update(mode="grouped", reserve_pct=reserve)
    elif op == "merge":
        _need(st, by, ("pm", "human"))
        if st["mode"] != "grouped":
            raise MissionError("bad_transition")
        for g in st["groups"].values():
            g.setdefault("merged_at", None)
            if not g["merged_at"]:
                g["merged_at"] = at
        for r in st["roster"].values():
            r.pop("group_id", None)
        st["mode"] = "single"
    elif op == "lead":                              # 改派組長(組長離隊後;不自動升工人)
        _need(st, by, ("pm", "human"))
        g = _live_group(st, d.get("group"))
        seat = d.get("seat")
        r = st["roster"].get(seat) if isinstance(seat, str) else None
        if not r or r.get("left_at") or r["role"] != "lead" or any(x.get("lead") == seat for x in st["groups"].values()
                                                                   if not x.get("merged_at")):
            raise MissionError("understaffed")
        if r.get("group_id") not in (None, g["id"]):
            raise MissionError("cross_group")
        g["lead"] = seat
        r["group_id"] = g["id"]
    elif op == "summary":
        g = _live_group(st, d.get("group"))
        _need(st, by, ("lead",))
        if g.get("lead") != _seat_of(by):
            raise MissionError("cross_group")       # 只有該組組長寫該組摘要
        why = _txt(d.get("why"))
        if not why:
            raise MissionError("no_why")
        stuck = d.get("stuck") is True and any(t.get("group") == g["id"] and t["status"] == "blocked" for t in st["tasks"].values())
        if g["since_summary"] < SUMMARY_EVERY and not stuck:
            raise MissionError("too_soon")
        g.update(since_summary=0, last_summary={"at": at, "why": why, "stuck": stuck})
    else:
        raise MissionError("bad_op")


def _apply_budget(st, d):
    level, dim = d.get("level"), d.get("dim")
    if level not in BUDGET_LEVELS or dim not in BUDGET_DIMS:
        raise MissionError("bad_event")
    if level == "base":                              # 額度基準(第一次量到 / 視窗重設 / 新的 (引擎, 視窗))
        base = d.get("base")
        if dim != "quota" or not isinstance(base, dict) or not base or len(base) > 40 \
                or any(not isinstance(k, str) or type(v) is not int or not 0 <= v <= 100 for k, v in base.items()):
            raise MissionError("bad_event")
        st["quota_base"] = dict(base)
        return
    if not _uint(d.get("used")) or not _uint(d.get("cap")) or not d["cap"]:
        raise MissionError("bad_event")
    marks = st["budget_marks"].setdefault(dim, [])
    if level in marks or "halt" in marks:            # 同一維同一級只寫一次
        raise MissionError("bad_event")
    marks.append(level)
    if level == "halt":
        st["budget_block"][dim] = True


def _over_budget(st, at):
    """叫停後還在上限以上(分鐘 / 席數當場算;額度看有沒有還沒被改預算清掉的 halt 記號)。"""
    b = st["budget"]
    if b.get("minutes") and used_minutes(st, at) >= b["minutes"]:
        return True
    if b.get("seats_max") and used_seats(st) > b["seats_max"]:
        return True
    return any(st["budget_block"].values())


# ── 寫 ──

def _req_digest(typ, by, task, data, with_reset=False):
    """同 eid 重送比的是「要求」(不含系統產生的代號 / 收據 id)。
    reset 是事件語意版本(budget_set 新版才帶),不是要求的內容 ⇒ 不算進來,舊版成功的指令原樣重送照回原結果(#427 r4 Medium)。
    with_reset:59fb3761 那版的算法(含 reset),比對時兩種都認(#427 r5)。"""
    drop = ("code", "receipt_id", "proved") if with_reset else ("code", "receipt_id", "proved", "reset")
    keep = {k: v for k, v in data.items() if k not in drop}
    raw = json.dumps([typ, by, task, keep], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _public_task(t):
    """快照 / show 都不帶派工代號(#414 R1 L8;同帳號下不算防線,只防經 API 外流)。"""
    return {k: v for k, v in t.items() if k not in ("code", "codes")}


def _machine_all(st):
    live = [t for t in st["tasks"].values() if t["status"] != "cancelled"]
    return bool(live) and all(t["status"] == "passed" and (st["receipts"].get(t["receipt"]) or {}).get("machine_verified")
                              for t in live)


def _snapshots(d, st):
    """快照(給畫面 / skill 讀,不當正本)。charter.json 帶 rev、最後寫 ⇒ 它的 rev 對得上 = 四個檔都寫完了;
    中途斷掉 ⇒ 下一次 tick 看 _snap_stale 補寫(#427 r4 Low)。"""
    _write(d / "roster.json", json.dumps({"v": V, "roster": st["roster"]}, ensure_ascii=False, sort_keys=True))
    _write(d / "budget.json", json.dumps({"v": V, "budget": st["budget"], "marks": st["budget_marks"], "block": st["budget_block"],
                                          "quota_base": st["quota_base"], "run_s": st["run_s"],
                                          "run_since": st["run_since"], "throttle": st["throttle"],
                                          "anomalies": st["anomalies"]}, ensure_ascii=False, sort_keys=True))
    _write(d / "tasks.jsonl", "".join(json.dumps(_public_task(t), ensure_ascii=False, sort_keys=True) + "\n"
                                      for t in sorted(st["tasks"].values(), key=lambda t: int(t["id"][1:]))))
    _write(d / "charter.json", json.dumps({"v": V, "id": st["id"], "status": st["status"], "rev": st["rev"],
                                           "ready_at": st.get("ready_at"), "hold": bool(st.get("hold")),
                                           "started_by": st.get("started_by"), "started_at": st.get("started_at"),
                                           "paused_by": st.get("paused_by"), "machine_all": _machine_all(st),
                                           "mode": st["mode"], "groups": st["groups"], "reserve_pct": st["reserve_pct"],
                                           "charter": st["charter"], "created_at": st["created_at"],
                                           "updated_at": st["updated_at"]}, ensure_ascii=False, sort_keys=True))


def _snap_stale(d, st):
    try:
        return json.loads((d / "charter.json").read_text(encoding="utf-8")).get("rev") != st["rev"]
    except (OSError, ValueError, AttributeError):
        return True


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


def _system(d, st, typ, data, now, torn):
    ev = {"v": V, "seq": st["seq"] + 1, "eid": "x_" + secrets.token_hex(8), "req": None, "at": now,
          "by": "system", "type": typ, "data": data}
    apply(st, ev)
    _append(d, ev, torn)
    return ev


def _budget_gate(d, st, now, probe, torn):
    """派工 / 進 running 之前,在團鎖內驗預算(#427 Codex r1 H1:不能只靠 tick)。
    先把該寫的 system 事件寫掉(額度基準、超限記號;running 的團超限 ⇒ team/halt),回 (寫了幾筆, 拒絕碼 或 None)。
    over_budget = 超限;quota_unmeasured = 設了額度上限但量不到 ⇒ 不放行(fail-close)。"""
    import mission_monitor as MM
    evs, why = MM.precheck(st, now, probe)
    for typ, data in evs:
        _system(d, st, typ, data, now, torn)
        torn = None
    return len(evs), why


def _expire_due(d, st, evs_torn, now, probe=None):
    """倒數滿了沒人按停 ⇒ autostart(預算驗過才開);租期過了的任務寫 expire。都是 system 寫。回寫了幾筆。"""
    n = 0
    if st["status"] == "ready" and not st.get("hold") and now - int(st.get("ready_at") or now) >= AUTOSTART_S:
        n, why = _budget_gate(d, st, now, probe, evs_torn)
        evs_torn = None if n else evs_torn
        if why:
            return n                                # 超限 / 額度量不到 ⇒ 不自動開始,等本人改預算或量得到
        ev = {"v": V, "seq": st["seq"] + 1, "eid": "x_" + secrets.token_hex(8), "req": None, "at": now,
              "by": "system", "type": "team", "data": {"move": "autostart"}}
        apply(st, ev)
        _append(d, ev, evs_torn)
        evs_torn = None
        n += 1
        st["_autostarted"] = True
    if st["status"] != "running":
        return n
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


def _probe(probe, env, now):
    import mission_monitor as MM
    return probe or PROBE or MM.Probe(env, now)


def _to_running(st, typ, data):
    mv = TEAM_MOVES.get((data or {}).get("move")) if typ == "team" else None
    return bool(mv) and mv[1] == "running" and st["status"] in mv[0]


def commit(mid, typ, by, data, env=None, task=None, eid=None, now=None, probe=None):
    """寫一筆。回 {"ok", "duplicate", "seq", "state"}。先把過期的租期處理掉(過期的回報才拒得掉)。"""
    now = int(time.time() if now is None else now)
    d = mdir(mid, env)
    if eid is not None and not EID_RE.match(str(eid)):
        raise MissionError("bad_eid")
    req = _req_digest(typ, by, task, data)
    probe = _probe(probe, env, now)
    with _locked(d, create=typ == "created"):
        st, torn, evs = _load(d, mid)
        if typ == "created" and evs:
            raise MissionError("exists")
        if eid and eid in st["eids"]:
            if st["eids"][eid]["req"] not in (req, _req_digest(typ, by, task, data, with_reset=True)):
                raise MissionError("eid_conflict")
            orig = evs[st["eids"][eid]["seq"] - 1]
            return {"ok": True, "duplicate": True, "seq": orig["seq"], "state": st, "task": orig.get("task"), "event": orig}
        if typ == "task_add" and task is None:     # 編號在鎖內配(兩席同時開單不撞號)
            task = "t%d" % (len(st["tasks"]) + 1)
        n = _expire_due(d, st, torn, now, probe)
        torn = None if n else torn
        if typ == "dispatch" or _to_running(st, typ, data):
            k, why = _budget_gate(d, st, now, probe, torn)
            n, torn = n + k, None if k else torn
            if why:
                if n:
                    _snapshots(d, st)
                raise MissionError(why)
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


def tick(mid, env=None, now=None, probe=None):
    """aib mission tick:倒數滿了自動開始;租期過期的任務放回待做;再跑監看規則(預算 / 異常 / 降速,S4)。
    回 [{"task","from"}](過期的任務);監看寫了哪些事件放在 st["_monitor"]。monitor 席每 60 秒叫一次。"""
    import mission_monitor as MM
    now = int(time.time() if now is None else now)
    d = mdir(mid, env)
    probe = _probe(probe, env, now)
    with _locked(d):
        st, torn, evs = _load(d, mid)
        if not evs:
            raise MissionError("no_mission")
        n = _expire_due(d, st, torn, now, probe)
        torn = None if n else torn
        wrote = []
        for typ, data in MM.evaluate(st, now, probe):
            ev = {"v": V, "seq": st["seq"] + 1, "eid": "x_" + secrets.token_hex(8), "req": None, "at": now,
                  "by": "system", "type": typ, "data": data}
            apply(st, ev)
            _append(d, ev, torn)
            torn = None
            wrote.append(ev)
        if n or wrote or _snap_stale(d, st):
            _snapshots(d, st)
        st["_monitor"] = wrote
        return st.get("_expired", [])


def new(by, title, goal="", accept=(), pm=None, budget=None, env=None, eid=None, now=None):
    pm = pm or (by[5:] if str(by).startswith("seat:") else None)
    if not SEAT_RE.match(str(pm or "")):
        raise MissionError("bad_seat")
    _budget_input(budget)                     # 預算不合先拒,不留空的團目錄(#427 r2 L3)
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
            "tasks": [dict(_public_task(t), code_set=bool(t.get("code")),
                           lease_left=max(0, t["lease_until"] - now) if t.get("lease_until") else None) for t in tasks],
            "receipts": st["receipts"], "hold": bool(st.get("hold")),
            "started_by": st.get("started_by"), "started_at": st.get("started_at"), "paused_by": st.get("paused_by"),
            "machine_all": _machine_all(st), "cancelled": len(tasks) - len(live),
            "mode": st["mode"], "groups": st["groups"], "reserve_pct": st["reserve_pct"],
            "autostart_in": (max(0, int(st["ready_at"]) + AUTOSTART_S - now)
                             if st["status"] == "ready" and not st.get("hold") and st.get("ready_at") else None),
            "passed": sum(1 for t in live if t["status"] == "passed"), "total": len(live),
            "verified_pct": int(100 * len(mv) / len(live)) if live else 0}


# ── 本人那兩步 ──

def _human_gate(move, mid, st, env):
    """開始 / 收下:approve_gate 組字 + Touch ID(高風險類不開時間窗)。回 None = 過;否則原因碼。"""
    live = [t for t in st["tasks"].values() if t["status"] != "cancelled"]
    return _gate(HUMAN_SITES[move], {"title": st["charter"].get("title") or mid, "id": mid, "n": str(len(live)),
                                     "k": str(len(st["tasks"]) - len(live))}, env)     # 另取消 k 件也給本人看(#414 R1 L7)


def _gate(site, facts, env):
    import approve_gate as AG
    r = AG.reason(site, facts, env=env)
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
        if eid and eid in st["eids"]:              # 重送 ⇒ 不再彈 Touch ID,直接回原結果 / eid_conflict(#414 R1 L5)
            return commit(mid, "team", by, dict(data, proved=True), env=env, eid=eid, now=now)
        why = _human_gate(move, mid, st, env)
        if why:
            raise MissionError(why)
        data["proved"] = True
    return commit(mid, "team", by, data, env=env, eid=eid, now=now)


def _budget_text(old, new):
    keys = [k for k in BUDGET_DIMS.values() if old.get(k) != new.get(k)]
    return ", ".join("%s %s→%s" % (k, old.get(k, "-"), new.get(k, "-")) for k in keys) or "-"


def budget_set(mid, by, changes, env=None, eid=None, now=None):
    """改預算。changes:{欄位: 正整數 | 0(拿掉上限)}。提高(含拿掉上限)= 花額度 ⇒ 只有本人 + Touch ID
    (site mission.budget,高風險一次一按、不進時間窗);調低 PM 也能寫、不用按。"""
    st = replay(mid, env)
    if st["status"] is None:
        raise MissionError("no_mission")
    new = dict(st["budget"])
    for k, v in _budget_input(changes, allow_zero=True).items():   # 只驗這次改的欄位;舊事件留下的值照讀(r2 High)
        if v:
            new[k] = v
        else:
            new.pop(k, None)
    new = _budget(new)
    data = {"budget": new, "reset": "dims"}         # 帶旗標 = 新語意(只重計改了的維度);沒帶的舊事件照舊版解(r3 High)
    if eid and eid in st["eids"]:                    # 重送 ⇒ 不再彈 Touch ID,回原結果 / eid_conflict
        return commit(mid, "budget_set", by, dict(data, proved=True), env=env, eid=eid, now=now)
    if _raises(st["budget"], new):
        if by != "human":
            raise MissionError("not_allowed")
        why = _gate(BUDGET_SITE, {"title": st["charter"].get("title") or mid, "id": mid,
                                  "what": _budget_text(st["budget"], new)}, env)
        if why:
            raise MissionError(why)
        data["proved"] = True
    return commit(mid, "budget_set", by, data, env=env, eid=eid, now=now)


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


def why(mid, tid, env=None):
    """aib mission why:一件任務的事件鏈(開單 → 派工 → 回報 → 品管 → 收據),給戰情室血緣用。不帶派工代號。"""
    if not TASK_RE.match(str(tid or "")):
        raise MissionError("bad_task")
    st, _torn, evs = _load(mdir(mid, env), mid)
    if st["status"] is None:
        raise MissionError("no_mission")
    _task(st, tid)
    chain = []
    for ev in evs:
        if ev.get("task") != tid:
            continue
        data = {k: v for k, v in ev["data"].items() if k not in ("code", "receipt")}
        rec = st["receipts"].get(data.get("receipt_id"))
        if rec:
            data["receipt"] = {k: rec.get(k) for k in ("kind", "head", "artifact", "accept_rev")}
        chain.append({"seq": ev["seq"], "at": ev["at"], "by": ev["by"], "type": ev["type"], "data": data})
    return {"task": tid, "status": st["tasks"][tid]["status"], "events": chain}


# ── CLI ──

def T(key, **kw):
    import i18n_lib
    try:
        return i18n_lib.t("aib.mission." + key, kw)
    except Exception:   # noqa: BLE001
        return key


def _parser():
    p = argparse.ArgumentParser(prog="aib mission", add_help=True, description=T("help.clock"))
    sub = p.add_subparsers(dest="cmd")
    for name in ("new", "list", "show", "roster", "team", "task", "tick", "why", "budget", "watch", "group"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--by", default=None, help=T("help.by"))
        s.add_argument("--eid", default=None, help=T("help.eid"))
        if name == "new":
            s.add_argument("--title", required=True)
            s.add_argument("--goal", default="")
            s.add_argument("--accept", action="append", default=[])
            s.add_argument("--pm", default=None, help=T("help.seat"))
        if name in ("new", "budget"):
            s.add_argument("--minutes", type=int, default=None)
            s.add_argument("--seats-max", type=int, default=None)
            s.add_argument("--quota-pct", type=int, default=None)
        if name in ("show", "roster", "team", "task", "tick", "why", "budget", "watch", "group"):
            s.add_argument("id")
        if name == "why":
            s.add_argument("task")
        if name == "roster":
            s.add_argument("op", choices=("add", "remove"))
            s.add_argument("seat", help=T("help.seat"))
            s.add_argument("--role", choices=ROLES, default=None)
            s.add_argument("--group", default=None, help=T("help.group"))
        if name == "team":
            s.add_argument("move", choices=sorted(m for m in TEAM_MOVES if m != "autostart"))
        if name == "task":
            s.add_argument("op", choices=("add", "dispatch", "report", "qa", "cancel", "amend", "contract-rev"))
            s.add_argument("task", nargs="?")
            s.add_argument("--title", default="")
            s.add_argument("--accept", action="append", default=None)
            s.add_argument("--dep", action="append", default=[])
            s.add_argument("--parent", default=None)
            s.add_argument("--weight", type=int, default=None)
            s.add_argument("--est", type=int, default=None)
            s.add_argument("--to", default=None, help=T("help.seat"))
            s.add_argument("--code", default=None, help=T("help.code"))
            s.add_argument("--state", choices=("doing", "blocked", "qa"), default=None)
            s.add_argument("--artifact", default=None)
            s.add_argument("--note", default="")
            s.add_argument("--pass", dest="verdict", action="store_const", const="pass")
            s.add_argument("--return", dest="verdict", action="store_const", const="return")
            s.add_argument("--receipt", default=None)
            s.add_argument("--why", default="")
            s.add_argument("--if-rev", type=int, default=None)
            s.add_argument("--group", default=None, help=T("help.group"))
            s.add_argument("--kind", choices=("contract", "integration"), default=None)
            s.add_argument("--producer", default=None, help=T("help.group"))
            s.add_argument("--consumer", action="append", default=[], help=T("help.group"))
            s.add_argument("--path", default=None, help=T("help.contract_path"))
        if name == "group":
            s.add_argument("op", choices=("split", "merge", "lead", "summary"))
            s.add_argument("--plan", default=None, help=T("help.group_plan"))
            s.add_argument("--group", default=None, help=T("help.group"))
            s.add_argument("--seat", default=None, help=T("help.seat"))
            s.add_argument("--why", default="")
            s.add_argument("--stuck", action="store_true")
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
        if a.cmd == "why":
            res = why(a.id, a.task, env)
            text = "%s  %s\n" % (res["task"], T("task." + res["status"]))
            text += "\n".join("  #%d  %s  %s  %s" % (e["seq"], time.strftime("%m-%d %H:%M", time.localtime(e["at"])),
                                                    e["by"], e["type"]) for e in res["events"])
            return _emit(out, a, dict(res, ok=True), text.rstrip())
        if a.cmd == "tick":
            got = tick(a.id, env)
            st = replay(a.id, env)
            return _emit(out, a, {"ok": True, "expired": got, "status": st["status"],
                                  "anomalies": sorted(st["anomalies"]), "throttle": st["throttle"]}, T("ok"))
        if a.cmd == "watch":
            import mission_monitor as MM
            return MM.watch_cmd(a.id, a.json, env=env, out=out)
        by = _by(a)
        if a.cmd == "budget":
            res = budget_set(a.id, by, {k: v for k, v in (("minutes", a.minutes), ("seats_max", a.seats_max),
                                                          ("quota_pct", a.quota_pct)) if v is not None},
                             env=env, eid=a.eid)
            return _emit(out, a, {"ok": True, "duplicate": res["duplicate"], "seq": res["seq"],
                                  "budget": res["state"]["budget"]}, T("ok"))
        if a.cmd == "new":
            budget = {k: v for k, v in (("minutes", a.minutes), ("seats_max", a.seats_max), ("quota_pct", a.quota_pct)) if v is not None}
            res = new(by, a.title, a.goal, a.accept, a.pm, budget, env=env, eid=a.eid)
            return _emit(out, a, {"ok": True, "id": res["id"], "duplicate": res["duplicate"]}, res["id"])
        if a.cmd == "roster":
            data = {"op": a.op, "seat": a.seat, "role": a.role}
            if a.group:
                data["group"] = a.group
            res = commit(a.id, "roster", by, data, env=env, eid=a.eid)
        elif a.cmd == "group":
            res = commit(a.id, "group", by, _group_data(a), env=env, eid=a.eid)
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
        if a.group:
            data["group"] = a.group
        if a.kind:
            data["kind"] = a.kind
        if a.kind == "contract":
            data.update(producer=a.producer, consumers=a.consumer, path=a.path)
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
        st = replay(a.id, env)                 # 角色先擋,再看收據(S2 回饋 2;commit 鎖內會再驗一次)
        if st["status"] is None:
            raise MissionError("no_mission")
        _need(st, by, ("qa",))
        if a.verdict == "pass" and not a.receipt:
            raise MissionError("bad_receipt")
        return qa(a.id, a.task, by, a.verdict, load_receipt(a.receipt) if a.verdict == "pass" else None, a.why,
                  env=env, eid=a.eid, if_rev=a.if_rev)
    elif a.op == "amend":
        data["accept"] = a.accept or []
    elif a.op == "contract-rev":
        if a.path is not None:
            data["path"] = a.path
    return commit(a.id, {"dispatch": "dispatch", "report": "report", "cancel": "cancel", "amend": "amend",
                         "contract-rev": "contract_rev"}[a.op],
                  by, data, env=env, task=a.task, eid=a.eid)


def _group_data(a):
    """group split 的分組計畫走 --plan <JSON 檔>:{groups:[{id,name,lead,members,quota{seats,minutes}}], contract, reserve_pct?, diff?}"""
    if a.op == "split":
        try:
            plan = json.loads(Path(a.plan).read_text(encoding="utf-8")) if a.plan else None
        except (OSError, ValueError):
            plan = None
        if not isinstance(plan, dict) or set(plan) - {"groups", "contract", "reserve_pct", "diff"}:
            raise MissionError("bad_group")
        return dict(plan, op="split")
    if a.op == "lead":
        return {"op": "lead", "group": a.group, "seat": a.seat}
    if a.op == "summary":
        return {"op": "summary", "group": a.group, "why": a.why, "stuck": a.stuck}
    return {"op": "merge"}


def _emit(out, a, body, text):
    out.write((json.dumps(body, ensure_ascii=False) if a.json else text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
