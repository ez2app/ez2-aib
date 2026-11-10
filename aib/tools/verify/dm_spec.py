#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""1.x dm 佇列行為的對照表(043dd 11-09;Rust 版 dm 第 0 步,Andy「照你的建議，先做第 0 步」)。

用**現在的 Python 實作**跑過「起始狀態 × 目標狀態 × 操作者 × 理由 × 證據 × 信件旗標」每一格,
把結果(准 / 拒、拒絕代碼、改了哪些欄位、送不送回條)寫成一份排好序的 JSON。用途:
  1. Rust 版 dm 照這份對:兩邊每一格一樣 = 沒漏規則(之後 Rust 測試讀同一份檔)。
  2. 1.x 之後再改 dm_queue:這份不一樣就紅,要嘛是退步、要嘛是故意改了規則 ⇒ 重產並在 PR 裡寫為什麼。

這支是 Python,因為它讀的就是 Python 實作的行為(新工具一律 Rust 的例外:量的對象本身是 Python)。

  python3 tools/verify/dm_spec.py --write   重產 tests/fixtures/dm-spec/transitions-v1.json
  python3 tools/verify/dm_spec.py --check   產一份到暫存比對;不同 ⇒ rc 1 並印前幾格差異
全在暫存狀態根 + 暫存 HOME 跑;不送真 dm、不碰 ~/.ez2-aib、不連網(回條改成只記錄)。
沒涵蓋(要環境才判得出來):證據是 commit(要 git)、證據是別席佇列裡的回信 id;檔頭 not_covered 照列。
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

AIB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AIB / "src" / "cockpit"))

import dm_queue as Q  # noqa: E402

# 刀測複本不帶 tests/fixtures ⇒ 測試用 AIB_DM_SPEC_FIXTURE 指回真樹那份(只讀)
FIXTURE = Path(os.environ.get("AIB_DM_SPEC_FIXTURE") or AIB / "tests" / "fixtures" / "dm-spec" / "transitions-v1.json")
SCHEMA = "aib-dm-spec-transitions/v1"
SEAT = "aaaaa"
OTHER = "bbbbb"
NOW = 1792930000
T0 = NOW - 3600

ACTORS = {
    "system": "system",
    "seat:self": "seat:" + SEAT,
    "seat:other": "seat:" + OTHER,
    "sender": "sender:" + OTHER,
    "human": "human:andy",
    "human:from-seat": "human:andy",            # 同一個 actor,但呼叫端看起來是 AI 席位
    "agent:self": "agent:%s/headless-x" % SEAT,
    "agent:other": "agent:%s/headless-x" % OTHER,
    "bad-format": "andy",
    "missing": None,
}
WHYS = {
    "none": None,
    "plain": "回過了",
    "auto_delete": Q.AUTO_DELETE_WHY,
    "auto_read": Q.AUTO_READ_WHY,
    "onboard_ack": Q.ONBOARD_ACK_WHY,
    "supersede": Q.SUPERSEDE_DONE_PREFIX + "q_0123456789abcdef",
    "reopen": Q.REOPEN_WHY_PREFIX + "結錯了",
    "reopen-empty": Q.REOPEN_WHY_PREFIX,
    "superseded": "superseded",
    "done_12h": Q.AUTO_ARCHIVE_WHY,
    "ack_12h": Q.ACK_ARCHIVE_WHY,             # B11(1ced7 11-10):ack 已讀滿 12 小時由系統備存
}
EVIDENCES = ("none", "file", "pr", "text")
FLAGS = ("plain", "global", "im")
NOT_COVERED = ["evidence=commit(要 git 倉)", "evidence=回信 id(要別席佇列)", "rev 檢查只列四種(見 rev_cases)",
               "kind=ack 由 record --status read 直接落成已讀,不經轉移(見 test-dm-ack-1ced7-1110.py)"]


# 暫存目錄(_Env)的路徑:Mac 的 /var/folders/… 一定超過 40 字 ⇒ 本來就記 <str>;Linux 的 /tmp/aib-dm-spec-xxxxxxxx/…
#   常常不到 40 字 ⇒ 會照原文寫進去,而且每次隨機(#570 ubuntu 三格紅 58 格)。含暫存路徑的字串一律記 <str>,兩個平台一樣。
TMP_ROOTS = []


def _norm(v, now):
    if isinstance(v, str) and any(r and r in v for r in TMP_ROOTS):
        return "<str>"
    if v == now:
        return "<now>"
    if v == T0:
        return "<t0>"
    if v is None or isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, str):
        return v if len(v) <= 40 else "<str>"
    return "<%s>" % type(v).__name__


def _changes(before, after, now):
    keys = sorted(set(before) | set(after))
    out = {}
    for k in keys:
        if k.startswith("_"):
            continue
        if before.get(k, "<absent>") != after.get(k, "<absent>"):
            out[k] = _norm(after[k], now) if k in after else "<removed>"
    return out


class _Env:
    def __init__(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-dm-spec-")
        d = Path(self.td.name)
        TMP_ROOTS[:] = sorted({str(d), os.path.realpath(str(d))}, key=len, reverse=True)
        (d / "home").mkdir()
        self.env = {"EZ2_AIB_STATE_ROOT": str(d / "state"), "HOME": str(d / "home"), "EZ2_AIB_LOCALE": "zh-TW"}
        self.evfile = d / "evidence.txt"
        self.evfile.write_text("evidence\n", encoding="utf-8")

    def close(self):
        self.td.cleanup()


def _make(env, status, flag):
    it = Q.record(SEAT, "對照表", "agent:offhub", status="delivered", env=env, now=T0)
    item = Q._load_item(SEAT, it["id"], env)
    item["status"] = status
    item["global"] = flag == "global" or None
    if flag == "im":
        item["im"] = {"kind": "telegram"}
    Q._write_item(SEAT, item, env)
    if status in Q.ACTIVE_STATUSES:
        Q._order_append(SEAT, item["id"], env)      # 進行中的信本來就在送信序裡(看得出轉移後有沒有被移出)
    return item["id"]


def _drop(env, iid):
    """一格做完就清掉,佇列不越長越大(每格都從同一個乾淨起點開始)。"""
    try:
        Q.item_path(SEAT, iid, env).unlink()
    except OSError:
        pass
    Q._order_remove(SEAT, iid, env)


def _evidence(kind, box):
    return {"none": None, "file": str(box.evfile), "pr": "https://github.com/ez2app/ez2-aib/pull/1",
            "text": "看過了"}[kind]


def cell(box, frm, to, actor_key, why_key, ev_key, flag):
    env = box.env
    iid = _make(env, frm, flag)
    before = dict(Q._load_item_raw(SEAT, iid, env)[0])
    receipts = []
    from_seat = actor_key == "human:from-seat"
    with mock.patch.dict(Q.HUMAN_ACTOR_GATE, {"caller_is_seat": lambda e: from_seat}), \
            mock.patch.object(Q, "global_delivery",
                              lambda item, state, *a, **k: receipts.append([state, k.get("by")]) or False):
        r = Q._transition(SEAT, iid, to, ACTORS[actor_key], env=env, now=NOW, why=WHYS[why_key],
                          evidence=_evidence(ev_key, box), no_rev_check=True)
    after = Q._load_item_raw(SEAT, iid, env)[0] or {}
    row = {"from": frm, "to": to, "actor": actor_key, "why": why_key, "evidence": ev_key, "flag": flag,
           "ok": bool(r.get("ok")), "reason": r.get("reason")}
    if r.get("ok"):
        row["set"] = _changes(before, after, NOW)
        row["in_order"] = iid in (Q._load_order(SEAT, env).get("ids") or [])
        if receipts:
            row["receipt"] = receipts
    _drop(env, iid)
    return row


def rev_cases(box):
    """rev 檢查(fail-closed):沒帶 rev / 對 / 錯 / 信不在 / 墓碑。"""
    env = box.env
    out = []
    iid = _make(env, "delivered", "plain")
    rev = Q.item_rev(Q._load_item(SEAT, iid, env))
    for name, kw in (("no_rev", {"if_rev": None, "no_rev_check": False}),
                     ("match", {"if_rev": rev, "no_rev_check": False}),
                     ("mismatch", {"if_rev": rev + 1, "no_rev_check": False}),
                     ("garbage", {"if_rev": "x", "no_rev_check": False})):
        r = Q.rev_check(SEAT, iid, kw["if_rev"], env, no_rev_check=kw["no_rev_check"])
        out.append({"case": name, "ok": r[0], "reason": r[1]})
    r = Q.rev_check(SEAT, "q_ffffffffffffffff", 1, env)
    out.append({"case": "missing", "ok": r[0], "reason": r[1]})
    tomb = _make(env, "deleted", "plain")
    r = Q.rev_check(SEAT, tomb, None, env, no_rev_check=True)
    out.append({"case": "tombstone-no-rev-check", "ok": r[0], "reason": r[1]})
    return out


def _wanted(frm, to, why_key, ev_key, flag):
    """格子太多,只在會影響判斷的維度上展開:理由在 read/done/archived/deleted 才展開;證據只在 done 展開;
    旗標只配 why=none、證據=none/file(其餘維度不跟旗標交互)。"""
    if ev_key != "none" and to != "done":
        return False
    if why_key != "none" and to not in ("read", "done", "archived", "deleted", Q.HOLD_STATUS):
        return False
    if flag != "plain" and (why_key not in ("none", "plain") or ev_key not in ("none", "file")):
        return False
    return True


def build():
    box = _Env()
    try:
        with mock.patch.dict(os.environ, box.env):
            rows = []
            for frm in Q.STATUSES:
                for to in Q.STATUSES:
                    if frm == to:
                        continue
                    for a in ACTORS:
                        for w in WHYS:
                            for ev in EVIDENCES:
                                for fl in FLAGS:
                                    if _wanted(frm, to, w, ev, fl):
                                        rows.append(cell(box, frm, to, a, w, ev, fl))
            return {"schema": SCHEMA, "statuses": list(Q.STATUSES), "active": list(Q.ACTIVE_STATUSES),
                    "transitions": sorted([list(k) + [list(v)] for k, v in Q.TRANSITIONS.items()]),
                    "reasons": sorted(Q.TRANSITION_REASON), "rev_reasons": sorted(Q.REV_REASON),
                    "actors": ACTORS, "whys": WHYS, "evidences": list(EVIDENCES), "flags": list(FLAGS),
                    "not_covered": NOT_COVERED, "rev_cases": rev_cases(box), "cells": rows}
    finally:
        box.close()


DIMS = ("from", "to", "actor", "why", "evidence", "flag")


def to_row(c):
    """一格 ⇒ [六維…, reason, 准的話 {set, in_order, receipt}];被拒的只存代碼(檔案小一半以上)。"""
    row = [c[k] for k in DIMS] + [c["reason"]]
    if c["ok"]:
        row.append({k: c[k] for k in ("set", "in_order", "receipt") if k in c})
    return row


def from_row(row):
    c = dict(zip(DIMS, row[:6]))
    c["reason"] = row[6]
    c["ok"] = row[6] == "ok"
    if len(row) > 7:
        c.update(row[7])
    return c


def dumps(spec):
    """檔頭照一般 JSON;cells 一格一行(diff 好讀、檔小)。整份仍是合法 JSON。"""
    head = {k: v for k, v in spec.items() if k != "cells"}
    head["cell_format"] = list(DIMS) + ["reason", "{set,in_order,receipt} 只在准的格"]
    text = json.dumps(head, ensure_ascii=False, sort_keys=True, indent=1)
    rows = ",\n".join(json.dumps(to_row(c), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                      for c in spec["cells"])
    return text[:-2] + ',\n "cells": [\n' + rows + "\n ]\n}\n"


def load(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    spec["cells"] = [from_row(r) for r in spec.get("cells") or []]
    return spec


def diff(old, new, limit=10):
    """兩份對照表不同的格子(以前六維為鍵)。"""
    key = lambda c: tuple(c[k] for k in DIMS)  # noqa: E731
    a = {key(c): c for c in old.get("cells") or []}
    b = {key(c): c for c in new.get("cells") or []}
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append({"cell": "/".join(k), "was": a.get(k), "now": b.get(k)})
    for top in ("transitions", "reasons", "rev_reasons", "rev_cases", "statuses", "active"):
        if old.get(top) != new.get(top):
            out.insert(0, {"cell": top, "was": old.get(top), "now": new.get(top)})
    return out[:limit], len(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--fixture", default=str(FIXTURE))
    args = ap.parse_args(argv)
    spec = build()
    path = Path(args.fixture)
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(spec), encoding="utf-8")
        print("wrote %s(%d 格)" % (path, len(spec["cells"])))
        return 0
    try:
        old = load(path)
    except (OSError, ValueError) as exc:
        print("dm-spec: 讀不到對照表 %s(%s)" % (path, exc.__class__.__name__))
        return 2
    shown, n = diff(old, spec)
    if not n:
        print("dm-spec: 一致(%d 格)" % len(spec["cells"]))
        return 0
    print("dm-spec: %d 格和對照表不同(dm 行為變了:退步就修;故意改規則就 --write 重產並在 PR 寫為什麼)" % n)
    for d in shown:
        print(json.dumps(d, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    sys.exit(main())
