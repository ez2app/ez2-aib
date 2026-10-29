#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""任務團 S4 前半:預算硬上限 + 規則監看 + aib mission watch(SPEC-mission-monitor-fd787-1029,§8 @f5fb8 裁示)。只用標準庫。

evaluate(st, now, probe) ⇒ [(事件類別, data)]:純函式(不碰檔、不改 st),tick 在鎖內逐筆 apply + 寫檔。
  預算:minutes / seats / quota 到 80% 寫 budget/warn、到 100% 寫 budget/halt + team/halt(reason=budget);同一維同一級只寫一次。
        席數在加人時就擋(mission.apply over_seats),這裡只在「改預算調低到人數以下」時叫停。
        額度:第一次量到記基準(budget/base);用量 = 各 (引擎, 視窗, 層) 現值 − 基準 取大(5h / 7d 都算,裁示 4);
        現值比基準小 = 視窗重設 ⇒ 那格基準換成現值、從 0 累加。量不到 ⇒ 不當 0、不叫停,開一筆 anomaly/quota_unmeasured。
  監看(不用 LLM):R1 idle(pm / worker 自己最後一筆事件 > 10 分)、R2 now_stale / now_missing(aib now > 15 分沒更新 / 沒檔)、
        R3 throttle(load1 > 12 標降速,≤ 10 才解除;只標記不叫停)、R4 qa_loop(同一任務品管連退兩次)、
        R5 blocked_long(卡關 > 15 分)。同一條規則對同一個對象只在進入 / 解除各寫一筆 anomaly ⇒ 事件量有上限。
        量不到(load、aib now 讀檔失敗)⇒ 那條這輪跳過,不猜、不洗版。
  監看只寫事件,不改任務、不改計畫;叫停只有預算那條自動,其他異常要不要停由 monitor 席自己 team halt。
watch:一屏印完就結束(要持續看外面包 watch -n 10);只讀,不寫事件。退出碼 0 正常 / 3 有待人或預算叫停 / 2 沒這團。
同帳號威脅模型:missions/ 所有 AI 都寫得到,預算叫停與監看只防誤操作與失控,不是防線;額度量測是本機檔 ⇒ 寧可高估。
  不主動 probe 用量(主動 probe 會花額度),只讀 usage_probe 已經寫好的紀錄檔。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mission as M  # noqa: E402

IDLE_S = 10 * 60
NOW_STALE_S = 15 * 60
BLOCKED_S = 15 * 60
THROTTLE_ON, THROTTLE_OFF = 12.0, 10.0
QA_LOOP = 2
LOAD_FN = os.getloadavg


class Unmeasured(Exception):
    """量不到(原因碼放在 args[0])。"""


class Probe:
    """真的讀法;測試換成假資料。每個讀法量不到就丟 Unmeasured。"""

    def __init__(self, env=None, now=None):
        self.env, self.now = env, now

    def load1(self):
        try:
            return float(LOAD_FN()[0])
        except (OSError, AttributeError, IndexError, ValueError) as err:
            raise Unmeasured("load_" + type(err).__name__)

    def seat_now(self, code):
        """⇒ 最後一次 aib now 的 epoch;None = 沒有檔(從沒跑過 / 檔不見)。"""
        import seat_now as SN
        import seats_dir_lib
        path = os.path.join(SN.now_dir(seats_dir_lib.state_root(self.env)[0]), code + ".json")
        if not os.path.exists(path):
            return None
        rec = SN.read(code, state_root=seats_dir_lib.state_root(self.env)[0])
        if not rec or type(rec.get("at")) is not int:
            raise Unmeasured("now_unreadable")
        return rec["at"]

    def usage(self):
        """⇒ {"引擎/視窗/層": used_pct};沒有一筆新鮮的 ok 紀錄 ⇒ Unmeasured。"""
        import usage_probe as UP
        now = time.time() if self.now is None else self.now
        try:
            recs = UP.load()
        except (OSError, ValueError) as err:
            raise Unmeasured("usage_" + type(err).__name__)
        out = {}
        for r in recs:
            if r.get("status") == "ok" and type(r.get("used_pct")) is int and UP._ok_is_fresh(r, now):
                out["%s/%s/%s" % (r.get("engine"), r.get("window"), r.get("tier"))] = r["used_pct"]
        if not out:
            raise Unmeasured("usage_no_fresh_record")
        return out


def _quota(st, usage):
    """⇒ (新基準 或 None(沒變), 用量 %)。現值比基準小 = 視窗重設 ⇒ 基準換現值。"""
    base = dict(st.get("quota_base") or {})
    changed = False
    for k, v in usage.items():
        if k not in base or v < base[k]:
            base[k] = v
            changed = True
    used = max([usage[k] - base[k] for k in usage] + [0])
    return (base if changed else None), used


def _level(used, cap, dim):
    if dim == "seats":                               # 席數用滿 = 正常(加人時已擋);超過才是調低預算造成的
        return "halt" if used > cap else ("warn" if used * 100 >= cap * M.WARN_PCT else None)
    return "halt" if used >= cap else ("warn" if used * 100 >= cap * M.WARN_PCT else None)


def evaluate(st, now, probe):
    """⇒ [(typ, data)]。純函式:同一份 st、同一組 probe ⇒ 同一串結果;不寫檔、不改 st。"""
    out = []
    status = st["status"]
    if status in (None, "archived"):                  # 封存的團不收任何事件(apply 拒 archived)
        return out
    live = status in ("running", "paused")
    # R3 降速(任何狀態都看;回差 2 免得在 12 附近一直跳)
    try:
        load = probe.load1()
    except Unmeasured:
        load = None
    if load is not None:
        if not st["throttle"] and load > THROTTLE_ON:
            out.append(("throttle", {"on": True, "load1": round(load, 2)}))
        elif st["throttle"] and load <= THROTTLE_OFF:
            out.append(("throttle", {"on": False, "load1": round(load, 2)}))
    want, keep = {}, set()                            # 這輪成立的異常;量不到、維持原樣的異常
    # 預算
    b = st["budget"]
    used = {"minutes": M.used_minutes(st, now), "seats": M.used_seats(st)}
    if b.get("quota_pct") and (status == "running" or (status == "paused" and st.get("quota_base"))):
        try:
            usage = probe.usage()
        except Unmeasured as err:
            usage = None
            want["quota_unmeasured:quota"] = {"why": str(err.args[0] if err.args else "unmeasured")}
        if usage is not None:
            base, used["quota"] = _quota(st, usage)
            if base is not None:
                out.append(("budget", {"level": "base", "dim": "quota", "base": base}))
    elif b.get("quota_pct"):
        keep.add("quota_unmeasured:quota")            # 這輪不量(沒在跑)⇒ 維持原樣;拿掉額度上限才解除
    halting = False
    if live:
        for dim, key in M.BUDGET_DIMS.items():
            cap = b.get(key)
            if not cap or dim not in used:
                continue
            lv = _level(used[dim], cap, dim)
            marks = st["budget_marks"].get(dim, [])
            if lv is None or "halt" in marks or lv in marks:
                continue
            out.append(("budget", {"level": lv, "dim": dim, "used": used[dim], "cap": cap}))
            if lv == "halt" and not halting:
                halting = True
                out.append(("team", {"move": "halt", "reason": "budget", "dim": dim}))
    running = status == "running" and not halting
    if running:
        since = int(st.get("run_since") or now)
        for seat, r in sorted(st["roster"].items()):
            if r.get("left_at"):
                continue
            if r["role"] in ("pm", "worker"):         # R1:monitor / qa 沒事等是正常的
                last = max(int(st["last_by"].get(seat) or 0), int(r.get("joined_at") or 0), since)
                if now - last > IDLE_S:
                    want["idle:" + seat] = {"last_at": last}
            try:                                      # R2
                at = probe.seat_now(seat)
            except Unmeasured:
                keep.update(k for k in st["anomalies"] if k in ("now_stale:" + seat, "now_missing:" + seat))
                continue
            if at is None:
                want["now_missing:" + seat] = {}
            elif now - at > NOW_STALE_S:
                want["now_stale:" + seat] = {"at": at}
    for tid, t in sorted(st["tasks"].items(), key=lambda kv: int(kv[0][1:])):
        if int(t.get("returns") or 0) >= QA_LOOP and t["status"] not in ("passed", "cancelled"):
            want["qa_loop:" + tid] = {"returns": int(t["returns"])}      # R4:任何時候都看
        if running and t["status"] == "blocked" and t.get("blocked_since") is not None \
                and now - int(t["blocked_since"]) > BLOCKED_S:
            want["blocked_long:" + tid] = {"since": int(t["blocked_since"])}
    for k in sorted(want):
        if k not in st["anomalies"]:
            rule, key = k.split(":", 1)
            data = {"rule": rule, "key": key, "end": False}
            data.update({x: v for x, v in want[k].items() if x != "why"})
            out.append(("anomaly", data))
    for k in sorted(st["anomalies"]):
        if k not in want and k not in keep:
            rule, key = k.split(":", 1)
            out.append(("anomaly", {"rule": rule, "key": key, "end": True}))
    return out


# ── watch ──

def T(key, **kw):
    return M.T("watch." + key, **kw)


def _ago_min(now, at):
    return max(0, int(now) - int(at)) // 60


def watch_view(st, now, probe):
    """watch --json 的欄位(固定)。只讀;額度用量用當下量到的值算,不寫基準。"""
    v = M.view(st, now)
    live = [t for t in st["tasks"].values() if t["status"] != "cancelled"]
    count = lambda *ss: sum(1 for t in live if t["status"] in ss)       # noqa: E731
    b = st["budget"]
    budget = {}
    for dim, key in M.BUDGET_DIMS.items():
        cap = b.get(key)
        row = {"cap": cap, "used": None, "pct": None, "unmeasured": None}
        if dim == "minutes":
            row["used"] = M.used_minutes(st, now)
        elif dim == "seats":
            row["used"] = M.used_seats(st)
        elif cap:
            try:
                row["used"] = _quota(st, probe.usage())[1] if st.get("quota_base") or st["status"] == "running" else 0
            except Unmeasured as err:
                row["unmeasured"] = str(err.args[0] if err.args else "unmeasured")
            row["shared_account"] = True            # 同帳號其他席也算進來
        if cap and row["used"] is not None:
            row["pct"] = int(100 * row["used"] / cap)
        budget[dim] = row
    try:
        load = round(probe.load1(), 2)
    except Unmeasured:
        load = None
    an = [dict(a, id=k) for k, a in sorted(st["anomalies"].items())]
    waiting = []
    if v["autostart_in"] is not None:
        waiting.append({"kind": "autostart", "in": v["autostart_in"]})
    elif st["status"] == "ready" and st.get("hold"):
        waiting.append({"kind": "hold"})
    if st["status"] == "handed":
        waiting.append({"kind": "handed"})
    if st["status"] == "halted":
        waiting.append({"kind": "halted", "reason": st.get("halt_reason")})
    waiting += [{"kind": "qa_loop", "task": a["key"]} for a in an if a["rule"] == "qa_loop"]
    return {"v": M.V, "id": st["id"], "title": st["charter"].get("title"), "status": st["status"],
            "run_min": M.used_minutes(st, now),
            "progress": {"passed": v["passed"], "total": v["total"], "verified_pct": v["verified_pct"],
                         "machine": sum(1 for t in live if t["status"] == "passed"
                                        and (st["receipts"].get(t.get("receipt")) or {}).get("machine_verified")),
                         "doing": count("assigned", "doing"), "blocked": count("blocked"), "qa": count("qa")},
            "budget": budget, "load1": load, "throttle": bool(st["throttle"]),
            "stuck": [a for a in an if a["rule"] in ("blocked_long", "qa_loop")],
            "anomalies": [a for a in an if a["rule"] not in ("blocked_long", "qa_loop")],
            "waiting": waiting}


def _cap_txt(row, dim):
    if row.get("unmeasured"):
        return T("unmeasured", why=row["unmeasured"])
    if not row["cap"]:
        return T("no_cap", used=row["used"])
    if dim == "quota":
        return T("quota", used=row["used"], cap=row["cap"])
    return "%d/%d(%d%%)" % (row["used"], row["cap"], row["pct"])


def _an_txt(a, now):
    d = a.get("data") or {}
    if a["rule"] == "idle":
        return T("an_idle", seat=a["key"], m=_ago_min(now, d.get("last_at", a["since"])))
    if a["rule"] == "now_stale":
        return T("an_now_stale", seat=a["key"], m=_ago_min(now, d.get("at", a["since"])))
    if a["rule"] == "now_missing":
        return T("an_now_missing", seat=a["key"])
    if a["rule"] == "blocked_long":
        return T("an_blocked", task=a["key"], m=_ago_min(now, d.get("since", a["since"])))
    if a["rule"] == "qa_loop":
        return T("an_qa_loop", task=a["key"], n=d.get("returns", QA_LOOP))
    return T("an_quota_unmeasured")


def _wait_txt(w):
    if w["kind"] == "autostart":
        return T("w_autostart", s=w["in"])
    if w["kind"] == "halted":
        return T("w_halted_budget") if w.get("reason") == "budget" else T("w_halted", by=w.get("reason") or "-")
    if w["kind"] == "qa_loop":
        return T("w_qa_loop", task=w["task"])
    return T("w_" + w["kind"])


def render(w, now):
    p, b = w["progress"], w["budget"]
    sep = " · "
    lines = [T("head", id=w["id"], title=w["title"] or "", st=M.T("st." + w["status"]), m=w["run_min"]),
             T("progress", passed=p["passed"], total=p["total"], machine=p["machine"], pct=p["verified_pct"],
               doing=p["doing"], blocked=p["blocked"], qa=p["qa"]),
             T("budget", minutes=_cap_txt(b["minutes"], "minutes"), seats=_cap_txt(b["seats"], "seats"),
               quota=_cap_txt(b["quota"], "quota")),
             T("load_unmeasured") if w["load1"] is None
             else T("load_throttle" if w["throttle"] else "load", load=w["load1"]),
             T("stuck", items=sep.join(_an_txt(a, now) for a in w["stuck"]) or T("nothing")),
             T("anomalies", items=sep.join(_an_txt(a, now) for a in w["anomalies"]) or T("nothing")),
             T("waiting", items=sep.join(_wait_txt(x) for x in w["waiting"]) or T("nothing"))]
    return "\n".join(lines)


def watch_cmd(mid, as_json, env=None, out=None, now=None, probe=None):
    out = out or sys.stdout
    now = int(time.time() if now is None else now)
    try:
        st = M.replay(mid, env)
        if st["status"] is None:
            raise M.MissionError("no_mission")
    except M.MissionError as err:
        out.write((json.dumps({"ok": False, "error": str(err)}, ensure_ascii=False) if as_json
                   else M.T("err", code=str(err))) + "\n")
        return 2
    w = watch_view(st, now, probe or Probe(env, now))
    out.write((json.dumps(dict(w, ok=True), ensure_ascii=False) if as_json else render(w, now)) + "\n")
    return 3 if w["waiting"] else 0
