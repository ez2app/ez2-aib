#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm_midturn_hint —— 回合中途就知道待審 ≥2 封(流程優化 7a;Andy 11-09 06:0x「dm 能在 >=2 封時，立刻必定由子 ai
先處理、把多封適當的合併，而不用等」;a63ab 06:29 准開 draft:單檔、開關預設關)。

## 為什麼要有
dm_pending_gate 只在回合結束(Stop)擋;開場摘要只在 UserPromptSubmit。長回合中途進來的信，席位要等回合結束才知道;
核心的背景整理在席位忙的時候延後(triage-why.jsonl:moving / waiting-pickup / headless-deferred:seat-busy)。
本支掛 Claude 的 PostToolUse:每次工具呼叫後，若「沒結的信 ≥ 2 封、這一批沒提示過、離上次提示超過節流秒數」
⇒ 用 additionalContext 注入一行，請席位 AI 當下開 1 個整理子代理。

## 算哪些信(跟 SUMMARY-workflow-043dd-1109 第二節同一套)
  - 待審頁:dm_pending_gate.pending_rows 的判準(別席寄、queued / delivered / 席位寄的 hold_human、沒過期、不是全球 / IM),
    但**不等 3 分鐘寬限**(要的就是「不用等」);
  - 進行中頁:read 過、沒結、沒設 wait 的(讀了忘了結的要繼續提示;在處理中的請設 wait)。系統讀的(read_by=system)不算。

## 去重與節流
  - 批次鍵 = 排序後「id@rev」的 sha256;同一批只提示一次(狀態檔 <佇列根>/<席>/midturn-hint.json)。
  - 換批(新信 / 新 rev / 結掉幾封又來新的)也要離上次提示 ≥ MIN_GAP_S 才再提示(a63ab:7a 要節流)。

## 開關(預設關)
  - 環境變數 AIB_DM_MIDTURN=1 ⇒ 開;=0 ⇒ 關(優先)。
  - 或 <now 目錄>/dm-midturn-seats.txt 列了這一席(或 `*`)⇒ 開。都沒有 ⇒ 關。

## 它做不到的(照實寫，不說「必定」)
  - 只在「下一次工具呼叫之後」觸發:一條長指令跑著時不會出現;純聊天回合沒有工具呼叫也不會出現。
  - 只有 Claude 有 PostToolUse additionalContext;Codex 等席要靠核心的背景整理(7b)。
  - 提示只是請 AI 開子代理，不是替它開;真正「不用等」要 7b 的事件驅動整理。

## 失效方向
讀不到佇列 / 認不出席 / 任何例外 ⇒ 不輸出(rc 0),不擋工具。整理子代理自己的工具呼叫:payload 帶 agent_id ⇒ 不提示。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COCKPIT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "cockpit")
for _p in (COCKPIT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

AT_LEAST = 2
MIN_GAP_S = 120
SHOW_MAX = 5
PREVIEW = 80
STATE_FILE = "midturn-hint.json"
SEATS_FILE = "dm-midturn-seats.txt"


def enabled(seat, env, now_dir):
    v = (env.get("AIB_DM_MIDTURN") or "").strip()
    if v == "0":
        return False
    if v == "1":
        return True
    try:
        with open(os.path.join(now_dir, SEATS_FILE), encoding="utf-8") as fh:
            names = {ln.split("#", 1)[0].strip() for ln in fh}
    except OSError:
        return False
    return seat in names or "*" in names


def open_rows(tabs, seat, now):
    """沒結的信:待審頁(不等寬限)+ 進行中頁沒設 wait 的。照進佇列時刻舊的在前。"""
    import dm_pending_gate as G
    waiting = G.pending_rows((tabs.get("waiting") or {}).get("rows"), seat, now + G.GRACE_S)  # 不等 3 分鐘寬限
    seen = {r.get("id") for r in waiting}
    inprog = []
    for r in (tabs.get("read") or {}).get("rows") or []:
        if not isinstance(r, dict) or r.get("id") in seen:
            continue
        if r.get("wait") or r.get("done_at") or r.get("read_by") == "system":
            continue
        if (r.get("sender_seat") or "") == seat:
            continue
        inprog.append(r)
    rows = waiting + inprog
    rows.sort(key=lambda r: (int(r.get("ts") or 0), r.get("id") or ""))
    return rows


def batch_key(rows):
    pairs = sorted("%s@%s" % (r.get("id"), r.get("rev")) for r in rows)
    return hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()[:16]


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def message(seat, rows):
    lines = ["[dm 中途提示 7a] @%s 有 %d 封沒結的信(待審 + 進行中沒設 wait):" % (seat, len(rows))]
    for r in rows[:SHOW_MAX]:
        lines.append("  - %s rev=%s @%s %s" % (r.get("id"), r.get("rev"), r.get("sender_seat") or "?",
                                              _one_line(r.get("preview") or r.get("msg"), PREVIEW)))
    if len(rows) > SHOW_MAX:
        lines.append("  …+%d" % (len(rows) - SHOW_MAX))
    lines.append("≥2 封 ⇒ 現在就開 1 個整理子代理(只讀、分組合併、擬稿、列可結證據;不寄、不刪、"
                 "不結授權 / High / 暫不通過 / 要裁 / 真人原話的信),你看它的結果再寄 / 結。")
    return "\n".join(lines)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, d):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)


def decide(seat, now, env, now_dir, payload=None):
    """回要注入的字(空字串 = 不提示)。只在要提示時寫狀態檔。"""
    if isinstance(payload, dict) and payload.get("agent_id"):
        return ""   # 子代理自己的工具呼叫:不回頭叫它再開子代理
    if not enabled(seat, env, now_dir):
        return ""
    import dm_queue
    p = dm_queue.tabs_payload(seat, env=env, now=int(now))
    if p.get("errors"):
        return ""
    rows = open_rows(p.get("tabs") or {}, seat, int(now))
    if len(rows) < AT_LEAST:
        return ""
    key = batch_key(rows)
    sd = dm_queue.seat_dir(seat, env)
    path = os.path.join(str(sd), STATE_FILE)
    st = _load(path)
    if st.get("key") == key:
        return ""
    if now - float(st.get("at") or 0) < MIN_GAP_S:
        return ""
    os.makedirs(str(sd), exist_ok=True)
    _save(path, {"key": key, "at": now, "n": len(rows)})
    return message(seat, rows)


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        payload = {}
    try:
        env = os.environ
        seat = (env.get("AIB_NOW_GATE_CODE") or "").strip()
        if not seat:
            import seat_now
            seat = seat_now.self_code()
        if not seat:
            return 0
        import seat_now
        text = decide(seat, time.time(), env, seat_now.now_dir(), payload)
    except Exception as e:  # noqa: BLE001 —— 失效方向：不提示、不擋工具(rc 0 時 stderr 只進記錄，不給 AI)
        print("dm_midturn_hint failopen: %s" % e.__class__.__name__, file=sys.stderr)
        return 0
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
