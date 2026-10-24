#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室任務板 = aib 全套操作的由淺而深課程(R / SR / SSR / UR;契約 contracts/power.json onboarding)。

正本 pending/PLAN-power-curriculum-abb89-1024.md(Andy 10-24 02:2x「從 cli 教學操作、到艦長室操作,都由淺而深的給任務」)。
- 每條 = (編號, 等級, 偵測代碼, 需要什麼)。「做完」只由核心偵測真實事件,偵測到就記在 state/power-onboarding.json,
  之後一直算 done(席收掉了也不會變回未完成)。核心還偵測不到的條 detectable = false,頁面照實顯示,不准讓按鈕自己勾。
- 獎勵(claimed)由伺服器簽,這一期不做;reward 一律 null。
- SSR1 艦隊擴編令 / UR1 全動力認證 = 輸入購買序號兌換(伺服器驗);核心只留 aib power redeem 介面,回 not_yet。
- where = power(動力室任務板,免費純 CLI 30 條)| room(艦長室任務板);tiers / all 只算 power,room 另給總數。
- 等級開關:開 = 顯示並提醒;關 = 收起不提醒,但偵測照記(之後打開補勾)。
- 這份紀錄檔是同帳號寫得到的本機檔,只當「顯示誰做過」用,不是防線;領獎一律伺服器判。
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

TIERS = ("R", "SR", "SSR", "UR")

# (編號, 等級, 偵測代碼, 需要):需要 = None | "room"(艦長室外掛) | "server"(伺服器回條) | "skins"(皮膚開關)| "command"(指令還沒做;10-24 UR4 aib support 做好後暫時沒有條用它)
TASKS = (
    ("R1", "R", "power_opened", None), ("R2", "R", "born", None), ("R3", "R", "seat_registered", None),
    ("R4", "R", "persona_bound", None), ("R5", "R", "brain_bound", None), ("R6", "R", "launch_recorded", None),
    ("R7", "R", "now_written", None), ("R8", "R", "dm_sent", None), ("R9", "R", "handoff_receipt", None),
    ("R10", "R", "doctor_run", None), ("R11", "R", "device_bound", None), ("R12", "R", "pet_stage_1", "server"),
    ("SR1", "SR", "two_providers_online", None), ("SR2", "SR", "second_seat_same_role", None),
    ("SR3", "SR", "reports_to_set", None), ("SR4", "SR", "dm_done", None), ("SR5", "SR", "memory_10", None),
    ("SR6", "SR", "aib_upgraded", None), ("SR7", "SR", "test_green", None), ("SR8", "SR", "room_opened", "room"),
    ("SR9", "SR", "ai_room_opened", "room"), ("SR10", "SR", "decor_placed", "room"), ("SR11", "SR", "panel_input_sent", "room"),
    ("SR12", "SR", "launch_streak_3", None), ("SR13", "SR", "pet_stage_2", "server"),
    # 地上 SR 重點三條(Andy 10-24 03:0x「三條一起先開」;編號 @abb89 03:14):偵測等 LINE 端(@9f808),分享 / 點擊要記誰
    ("SR14", "SR", "line_affiliate_bound", "server"), ("SR15", "SR", "line_shared", "server"),
    ("SR16", "SR", "share_tokens_viewed", "server"),
    ("SSR1", "SSR", "redeem_fleet", "server"), ("SSR2", "SSR", "new_cabin", None), ("SSR3", "SSR", "newrole", None),
    ("SSR4", "SSR", "projects_3", None), ("SSR5", "SSR", "dm_done_cross_seat", None), ("SSR6", "SSR", "mount_trained", "room"),
    ("SSR7", "SSR", "travel_sent", "room"), ("SSR8", "SSR", "souvenir_listed", "room"), ("SSR9", "SSR", "bbcall_read", "room"),
    ("SSR10", "SSR", "adventure_explored", "room"), ("SSR11", "SSR", "launch_streak_7", None),
    ("SSR12", "SSR", "pet_stage_3", "server"),
    ("UR1", "UR", "redeem_full_power", "server"), ("UR2", "UR", "creation_submitted", "server"),
    ("UR3", "UR", "boss_defeated", "server"), ("UR4", "UR", "support_bundle", None), ("UR5", "UR", "launch_streak_30", None),
    ("UR6", "UR", "pet_stage_final", "server"), ("UR7", "UR", "ssr_all_done", None),
)
TASK_IDS = tuple(t[0] for t in TASKS)
# 10-24 02:5x Andy「付費的內容，我希望到艦長室之後再出現」(SPEC §14):艦長室相關 + 序號兩條只在艦長室任務板出現;
# 資料照記、偵測照接，動力室頁不列。動力室 = 免費純 CLI 的 30 條
IN_ROOM = frozenset(("SR8", "SR9", "SR10", "SR11", "SR14", "SR15", "SR16", "SSR1", "SSR6", "SSR7", "SSR8", "SSR9", "SSR10", "SSR12",
                     "UR1", "UR2", "UR3"))
# SPEC §15(Andy 02:5x):SSR / UR 兩級鎖到「出發」之後才開;解鎖條件給代碼,頁面不自己猜。
# 「降落地球」是否只指進艦長室待 Andy 確認 ⇒ 先用出發紀錄(R6 launch_recorded,記住了就一直開)
UNLOCK = {"SSR": "launch_recorded", "UR": "launch_recorded"}
LEGACY = {"first_seat": "R3", "bind_seat": "R5", "first_handoff": "R9"}   # 10-24 第一版的三條 ⇒ 搬到新編號


def state_dir():
    return os.path.join(seats_dir_lib.state_root()[0], "state")


def _path():
    return os.path.join(state_dir(), "power-onboarding.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def birth_path():
    return os.path.join(state_dir(), "power-birth.json")


# ---------- 偵測 ----------

def _rows(root):
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "identity"))
        import bindme
        return [r for r in bindme.status(root) if r.get("problem") != "bot"]
    except Exception:  # noqa: BLE001
        return []


def streak(days, today=None):
    """連續出發天數:以今天或昨天結尾的連續日數(days = 'YYYY-MM-DD' 清單)。"""
    import datetime
    today = today or datetime.date.today()
    got = set()
    for d in days or []:
        try:
            got.add(datetime.date.fromisoformat(d))
        except (TypeError, ValueError):
            continue
    cur = today if today in got else today - datetime.timedelta(days=1)
    n = 0
    while cur in got:
        n += 1
        cur -= datetime.timedelta(days=1)
    return n


def _launch_days():
    try:
        import power_launch
        return power_launch.launch_days()
    except Exception:  # noqa: BLE001
        return []


def _aib_version():
    try:
        import cli_versions as CV
        return CV.local_version(CV._tool("aib"))
    except Exception:  # noqa: BLE001
        return None


def _vkey(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return None


TAIL_BYTES = 256 * 1024


def _tail_has(path, needle, limit=TAIL_BYTES):
    """檔尾 limit 位元組裡有沒有 needle(大檔不整份讀)。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            return needle.encode("utf-8") in f.read(limit)
    except OSError:
        return False


def _lines_at_least(path, n, limit=4 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            return f.read(limit).count(b"\n") >= n
    except OSError:
        return False


def _local_events(root, rows):
    """本機真事件(10-24 03:3x 接上):都是 aib 自己寫的紀錄檔;同帳號寫得到,只當「顯示做過沒」,領獎由伺服器判。"""
    st = os.path.join(root, "state")
    roles = [r.get("role") for r in rows if isinstance(r.get("role"), str) and r.get("role")]
    return {
        "now_written": bool(glob.glob(os.path.join(st, "seat-now", "*.json"))),
        "dm_sent": os.path.isfile(os.path.join(st, "aib-dm-sent-v3.tsv"))
        and os.path.getsize(os.path.join(st, "aib-dm-sent-v3.tsv")) > 0,
        "dm_done": any(_tail_has(p, '"to": "done"') for p in glob.glob(os.path.join(st, "dm-queue", "*", "audit.jsonl"))),
        "memory_10": _lines_at_least(os.path.join(root, "memory-index", "notes.jsonl"), 10),
        "test_green": any(_tail_has(p, '"rc": 0,') for p in glob.glob(os.path.join(root, "verify-shadow", "verify-*.rows.jsonl"))),
        "second_seat_same_role": len(roles) != len(set(roles)),
        "projects_3": sum(1 for n in _listdir(os.path.join(root, "projects")) if n.startswith("proj:")) >= 3,
        "doctor_run": os.path.isfile(os.path.join(st, "run-marks", "doctor.json")),
        "newrole": os.path.isfile(os.path.join(st, "run-marks", "newrole.json")),
        "support_bundle": os.path.isfile(os.path.join(st, "run-marks", "support.json")),
        "new_cabin": sum(1 for n in _listdir(os.path.join(root, "instances"))
                         if os.path.isdir(os.path.join(root, "instances", n, "seats"))) >= 2,
    }


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def detect(root, memo, today=None):
    """{偵測代碼: True};只回偵測得到的(偵測不到 / 沒有偵測器的不在裡面)。memo 是紀錄檔(放 first_version 用)。"""
    rows = _rows(root)
    days = _launch_days()
    st = streak(days, today)
    got = {
        "power_opened": os.path.isfile(os.path.join(root, "power-room", "index.html")),
        "born": bool(_load(birth_path()).get("at")),
        "seat_registered": bool(rows),
        "persona_bound": any(r.get("persona_uid") for r in rows),
        "brain_bound": any(r.get("problem") == "ok" and r.get("brain_bound") for r in rows),
        "launch_recorded": bool(days),
        "handoff_receipt": any("-AUTO-" not in os.path.basename(p)
                               for p in glob.glob(os.path.join(root, "pending", "HANDOFF-*.md"))),
        "two_providers_online": len({r.get("provider") for r in rows if r.get("provider")}) >= 2,
        "launch_streak_3": st >= 3, "launch_streak_7": st >= 7, "launch_streak_30": st >= 30,
    }
    got.update(_local_events(root, rows))
    ver = _aib_version()
    first = memo.get("first_version")
    if ver and not first:
        memo["first_version"] = ver
    elif ver and first and _vkey(ver) and _vkey(first) and _vkey(ver) > _vkey(first):
        got["aib_upgraded"] = True
    return {k: v for k, v in got.items() if v is True}


DETECTABLE = frozenset(("power_opened", "born", "seat_registered", "persona_bound", "brain_bound", "launch_recorded",
                        "handoff_receipt", "two_providers_online", "aib_upgraded", "launch_streak_3", "launch_streak_7",
                        "launch_streak_30", "ssr_all_done", "now_written", "dm_sent", "dm_done", "memory_10",
                        "test_green", "second_seat_same_role", "projects_3", "doctor_run",
                        "new_cabin", "newrole",
                        "support_bundle"))


def onboarding_state(*, detector=None, now=None, today=None):
    """{birth_done, tasks:[{id, tier, where, state, since, locked, reward, detect, detectable, requires}],
    tiers:{級: {on, done, total, locked, unlock}}, all:{done, total}, room:{done, total}, room_tiers:{級: {done, total, locked, unlock}}};
    tiers / all 只算 where = power。"""
    root = seats_dir_lib.state_root()[0]
    now = time.time() if now is None else now
    memo = _load(_path())
    seen = memo.get("done") if isinstance(memo.get("done"), dict) else {}
    seen = {LEGACY.get(k, k): v for k, v in seen.items() if LEGACY.get(k, k) in TASK_IDS and isinstance(v, (int, float))}
    first_before = memo.get("first_version")
    try:
        got = (detector or detect)(root, memo, today)
    except Exception:  # noqa: BLE001 - 偵測炸了不拖垮整頁
        got = {}
    changed = memo.get("first_version") != first_before or set(seen) != set(memo.get("done") or {})
    by_code = {code: tid for tid, _tier, code, _req in TASKS}
    for code, ok in (got or {}).items():
        tid = by_code.get(code)
        if ok is True and tid and tid not in seen:
            seen[tid] = int(now)
            changed = True
    ssr = [t[0] for t in TASKS if t[1] == "SSR"]
    if all(t in seen for t in ssr) and "UR7" not in seen:
        seen["UR7"] = int(now)
        changed = True
    if changed:
        memo["done"] = seen
        _save(_path(), memo)
    tiers_on = memo.get("tiers") if isinstance(memo.get("tiers"), dict) else {}
    locked = _locked(seen)
    tasks = [{"id": tid, "tier": tier, "where": "room" if tid in IN_ROOM else "power",
              "state": "done" if tid in seen else "todo", "since": int(seen[tid]) if tid in seen else None,
              "locked": locked[tier], "reward": None, "detect": code,
              "detectable": code in DETECTABLE, "requires": req} for tid, tier, code, req in TASKS]

    def count(rows):
        return {"done": sum(x["state"] == "done" for x in rows), "total": len(rows)}

    def by_tier(rows, with_on):
        out = {}
        for t in TIERS:
            mine = [x for x in rows if x["tier"] == t]
            if not mine:
                continue
            row = {"on": tiers_on.get(t) is not False and not locked[t]} if with_on else {}
            row.update(count(mine), locked=locked[t], unlock=UNLOCK.get(t) if locked[t] else None)
            out[t] = row
        return out
    power = [x for x in tasks if x["where"] == "power"]
    room = [x for x in tasks if x["where"] == "room"]
    return {"birth_done": bool(_load(birth_path()).get("at")), "tasks": tasks, "tiers": by_tier(power, True),
            "all": count(power), "room": count(room), "room_tiers": by_tier(room, False)}


def _locked(seen):
    """{級: 鎖著沒};解鎖條件 = UNLOCK 的偵測代碼對應的任務做過(記住了就一直開)。"""
    by_code = {code: tid for tid, _tier, code, _req in TASKS}
    return {t: bool(UNLOCK.get(t)) and by_code.get(UNLOCK[t]) not in seen for t in TIERS}


def set_tier(tier, on):
    """回 (回條, rc)。關 = 收起不提醒,偵測照記。"""
    if tier not in TIERS:
        return {"ok": False, "detail": "power_tier_unknown", "tier": None, "on": None}, 2
    memo = _load(_path())
    done = memo.get("done") if isinstance(memo.get("done"), dict) else {}
    if _locked({LEGACY.get(k, k) for k in done})[tier]:
        return {"ok": False, "detail": "power_tier_locked", "tier": tier, "on": None}, 2
    tiers = memo.get("tiers") if isinstance(memo.get("tiers"), dict) else {}
    tiers[tier] = bool(on)
    memo["tiers"] = tiers
    _save(_path(), memo)
    return {"ok": True, "detail": "power_tier_set", "tier": tier, "on": bool(on)}, 0


def redeem(read=None):
    """aib power redeem:序號兌換的介面(SSR1 / UR1)。兌換伺服器還沒有 ⇒ 一律 not_yet。
    序號從 stdin 讀(不放在指令列,ps 看得到);讀了就丟,不落檔、不回顯、不在本機驗。"""
    try:
        (read or (lambda: sys.stdin.readline()))()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": False, "detail": "power_redeem_not_yet", "item": None, "hint": None}, 2


# 序號兌換成功時 item 的固定代碼(頁面查 i18n,不顯示核心 / 伺服器原文);伺服器上線前不會回
REDEEM_ITEMS = ("ssr1_fleet", "ur1_full_power")


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib power")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tier")
    t.add_argument("tier")
    t.add_argument("onoff", choices=("on", "off"))
    t.add_argument("--json", action="store_true")
    r = sub.add_parser("redeem")
    r.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 2
    res, rc = set_tier(a.tier, a.onoff == "on") if a.cmd == "tier" else redeem()
    print(json.dumps(res, ensure_ascii=False) if a.json else json.dumps(res, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
