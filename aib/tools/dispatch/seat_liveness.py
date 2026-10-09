#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""派工前量目標席收不收得到派工(大綱 §108.3 #337;a seat 09-26 23:35 派,主線 a seat)。

起因:09-26 晚班 Grok 三席整晚零產出,派工單照樣發出去、照樣排進審查時程。
本檔只回答一件**量得到**的事:這一席現在收不收得到「經 dm 佇列送去的派工」。
它**不**判斷那一席死了沒有 —— 巡查 09-14 的教訓:read 0 次分不出「不在了」與「不在佇列協定裡」,
對活人宣告死亡比不報還糟。兩種情況派工都送不到,所以拒派的理由照實寫量到什麼,不寫推論。

三個訊號(the maintainer 經 a seat 定):
  1. 佇列:稽核紀錄裡這一席自己把信標成 read 的紀錄(to=="read" 且 ok)。
     從沒讀過 ⇒ 拒;有未讀(queued/hold_human/delivered)比窗口還老 ⇒ 拒;
     讀過、沒有過老的未讀 ⇒ 可(閒著沒信不算不活)。稽核讀不到 ⇒ 拒(量不到不是 0)。
  2. agent 已綁(只對 bot 席):bridge/<uuid>/status.json 的 agent_id(grokbot)或
     desktop_thread_id(codexbot)非空。狀態檔讀不到 ⇒ 拒。
  3. 喚醒(只對 grokbot):**先看 bot 橋自己量到的**——狀態根 logs/grokbot-wake.jsonl(sand_bridge._wake_log)
     這一席最後一筆有結論的門鈴:成功 ⇒ 可;「沒設」⇒ 拒;其他失敗(逾時、401…)⇒ 拒並附原因。
     略過政策性跳過(「合併:…」「dm 不叫醒…」)。完全沒有紀錄才退回看設定來源(與 sand_bridge 同一組:
     GROKBOT_WAKE_URL / BRIDGE_WAKE_URL 環境變數,或 secrets/grokbot.env、secrets/bridge.env、bridge-wake.env)。
     ★ 09-27 00:1x 實測:第一版只看設定來源 ⇒ 設定在 bridge 行程自己的環境裡的 a seat(門鈴剛回 HTTP 200)
       被判「喚醒設定不在」——呼叫者的環境不是橋的環境。

找席位檔掃**所有艙**:派工的人坐在某一艙,目標席常在別艙(Grok 席在 d1dd)。
母體交給 seat_authority.seat_files,並拿掉艙證據讓它走「掃全艙」那條,不另寫一份。

人話一律走 i18n(aib.parallel.live.*);本檔只產機器可讀的 (鍵, 參數)。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "src" / "cockpit", ROOT / "src" / "identity"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dm_queue  # noqa: E402
import seats_dir_lib  # noqa: E402
import seat_authority  # noqa: E402

CODE_RE = re.compile(r"^[0-9a-z]{5}$")
OUTSTANDING = ("queued", "hold_human", "delivered")   # 與巡查 aib_patrol_tick.OUTSTANDING 同一組
BOT_AGENT_FIELD = {"grokbot": "agent_id", "codexbot": "desktop_thread_id"}
WAKE_PROVIDERS = ("grokbot",)
CABIN_EVIDENCE = ("AIB_DM_SOCKET", "EZ2_AIHUB_SOCKET", "EZ2_AIB_INSTANCE_HOME")
DEFAULT_WINDOW_MIN = 60


def _env(env):
    return dict(os.environ if env is None else env)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def find_seats(code, env=None):
    """所有艙裡 session_uuid 尾五碼等於 code 的現役席位檔 [(路徑, 記錄)]。"""
    e = _env(env)
    for k in CABIN_EVIDENCE:
        e.pop(k, None)
    home = Path(seats_dir_lib.state_root(e)[0])
    hits = []
    for p in seat_authority.seat_files(home, e):
        rec = _load(p)
        if rec and seat_authority.tail5(str(rec.get("session_uuid") or "")) == code:
            hits.append((p, rec))
    return hits


def queue_facts(code, env=None, now=None, window_min=DEFAULT_WINDOW_MIN):
    now = time.time() if now is None else now
    audit_path = dm_queue.seat_dir(code, env) / dm_queue.AUDIT_FILE
    if not audit_path.is_file():
        return {"audit": "missing", "reads": None}
    rows = dm_queue.audit_rows(code, env)
    reads = [r for r in rows if r.get("to") == "read" and r.get("ok") is True]
    last = max((r.get("ts") or 0 for r in reads), default=None)
    ages = []
    for item in dm_queue.list_items(code, env):
        if item.get("status") in OUTSTANDING and not str(item.get("sender_label") or "").startswith("unattested"):
            try:
                ages.append((now - float(item.get("ts"))) / 60)
            except (TypeError, ValueError):
                continue
    stale = [a for a in ages if a > window_min]
    return {"audit": "ok", "reads": len(reads),
            "last_read_min": None if last is None else round((now - last) / 60, 1),
            "stale": len(stale), "oldest_stale_min": round(max(stale)) if stale else None}


def wake_configured(env=None):
    e = _env(env)
    if (e.get("GROKBOT_WAKE_URL") or "").strip() or (e.get("BRIDGE_WAKE_URL") or "").strip():
        return True
    root = Path(seats_dir_lib.state_root(e)[0])
    for p in (root / "secrets" / "grokbot.env", root / "secrets" / "bridge.env", root / "bridge-wake.env"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            k, _, v = line.strip().partition("=")
            if k.strip() in ("GROKBOT_WAKE_URL", "BRIDGE_WAKE_URL") and v.strip().strip("'\""):
                return True
    return False


WAKE_LOG = ("logs", "grokbot-wake.jsonl")
WAKE_SKIP_PREFIXES = ("合併", "dm 不叫醒")


def wake_from_log(code, env=None):
    """(結論, 原因):結論 ∈ ok / missing / failed / None(沒有有結論的紀錄)。"""
    root = Path(seats_dir_lib.state_root(_env(env))[0])
    try:
        lines = (root / WAKE_LOG[0] / WAKE_LOG[1]).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, ""
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("code") != code:
            continue
        reason = str(row.get("reason") or "")
        if row.get("ok") is None or reason.startswith(WAKE_SKIP_PREFIXES):
            continue
        if row.get("ok") is True:
            return "ok", reason
        return ("missing" if reason == "沒設" else "failed"), reason
    return None, ""


def probe(code, env=None, now=None, window_min=DEFAULT_WINDOW_MIN):
    """回 {"seat", "ok", "reasons": [(鍵尾, 參數)], "facts": {...}}。ok=False 就是拒派。"""
    out = {"seat": code, "ok": False, "reasons": [], "facts": {}}
    bad = out["reasons"].append
    if not isinstance(code, str) or not CODE_RE.match(code):
        bad(("not_code", {}))
        return out
    seats = find_seats(code, env)
    if not seats:
        bad(("no_seat", {}))
        return out
    if len(seats) > 1:
        bad(("ambiguous", {"n": len(seats)}))
        return out
    _path, rec = seats[0]
    provider = str(rec.get("provider") or "")
    out["facts"]["provider"] = provider

    q = queue_facts(code, env, now, window_min)
    out["facts"]["queue"] = q
    if q["audit"] != "ok":
        bad(("audit_missing", {}))
    elif q["reads"] == 0:
        bad(("never_read", {}))
    elif q["stale"]:
        bad(("backlog", {"n": q["stale"], "count": q["oldest_stale_min"]}))

    field = BOT_AGENT_FIELD.get(provider)
    if field:
        root = Path(seats_dir_lib.state_root(_env(env))[0])
        status = _load(root / "bridge" / str(rec.get("session_uuid") or "") / "status.json")
        out["facts"]["agent"] = None if status is None else bool(status.get(field))
        if status is None:
            bad(("status_missing", {}))
        elif not status.get(field):
            bad(("agent_unbound", {}))
    if provider in WAKE_PROVIDERS:
        verdict, why = wake_from_log(code, env)
        if verdict is None:
            verdict = "ok" if wake_configured(env) else "missing"
            why = "config"
        out["facts"]["wake"] = {"verdict": verdict, "reason": why}
        if verdict == "missing":
            bad(("wake_missing", {}))
        elif verdict == "failed":
            bad(("wake_failed", {"reason": why}))

    out["ok"] = not out["reasons"]
    if out["ok"]:
        out["reasons"].append(("read_ok", {"n": q["reads"]}))
    return out
