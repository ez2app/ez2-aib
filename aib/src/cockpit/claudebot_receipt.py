#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""dm 送進 claudebot 席時,收據說得出「這封會不會有人讀」(待修清單第 26 件寄件端;the maintainer 10-09 23:4x「aib dm 那件給你做」)。

claudebot 席的信要靠 Claude 那邊的 hook 收:門鈴(claudebot_hook doorbell)最長等 55 分鐘,過了就沒人收。
AIB 代開的對話,橋會自己叫醒(claudebot_live);the maintainer 在桌面 app 自己開的對話叫不醒,要有人在 app 說一句。
以前 dm-ctl 貼進席位成功就回 delivered,寄件席以為對方在處理 —— 10-09 18:40 一封「你在嗎」就這樣卡著沒人知道。

這支給 dm-ctl 問:「這封現在會被讀嗎?」只看量得到的事實,量不到就不說(印空行):
  · 橋自己標的健康欄(status.json health=degraded + last_error_kind)⇒ 照那一種說;
  · 否則:綁著對話、門鈴行程不在、Claude 閒著、對話不是 AIB 代開 ⇒ 說門鈴過期。
    Claude 正忙 ⇒ 這一輪結束時 Stop hook 會收,不說;代開的 ⇒ 橋幾秒內會叫醒,不說。
用法:python3 claudebot_receipt.py <席 session uuid> ⇒ 印一行警示或空行。永遠 rc 0。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

KIND_TEXT = {
    "doorbell-expired": "門鈴已過期:信進了它的信箱,但要有人在 Claude 那邊(桌面 app)說一句才會被讀",
    "unbound": "這一席沒綁 Claude 對話:信只進了信箱,沒有人收",
    "wake-failed": "上一次叫醒失敗:信進了信箱,不一定有人收",
    "not-collecting": "它已經一陣子沒來收信:信進了信箱,不一定有人收",
}


def _state_root(base=None) -> Path:
    return Path(base or os.environ.get("EZ2_AIB_STATE_ROOT") or os.environ.get("EZ2_AIB_HOME") or Path.home() / ".ez2-aib")


def note(session_uuid: str, base=None, projects_dir=None, now: float | None = None) -> str:
    """回要附在收據後面的一句;沒事或量不到 ⇒ ''。"""
    if not session_uuid:
        return ""
    seat_dir = _state_root(base) / "bridge" / session_uuid
    try:
        st = json.loads((seat_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(st, dict) or st.get("provider") != "claudebot":
        return ""
    if st.get("health") == "degraded" and st.get("last_error_kind") in KIND_TEXT:
        return KIND_TEXT[st["last_error_kind"]]
    sid = str(st.get("agent_id") or "")
    if not sid:
        return ""
    try:
        import claudebot_live as live
        import claudebot_spawn as spawn
    except ImportError:
        return ""
    if live.doorbell_alive(seat_dir) or spawn.record_of(sid):
        return ""
    tp = live.transcript_for(sid, projects_dir)
    if tp is None:
        return ""
    try:
        mtime = tp.stat().st_mtime
    except OSError:
        return ""
    state, _detail, _start = live.classify(live._tail_entries(tp), mtime, time.time() if now is None else now)
    return KIND_TEXT["doorbell-expired"] if state == live.STATE_IDLE else ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        print(note(argv[0] if argv else ""))
    except Exception:  # noqa: BLE001 —— 收據的附註壞了不能讓 dm 失敗
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
