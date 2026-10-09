# SPDX-License-Identifier: Apache-2.0
"""API 席的人格:每一輪都放在系統說明裡(the maintainer 09-30「確認全部種類 AI 都能真正的套用到人格」)。

09-30 測試艙 b1af 實測:deepseek / kiosapi / nvidia / teamo / teamoclaude / openrouter 綁了人格卡、抬頭也是那個名字,
但模型本身說「沒有載入具名人格」。原因:人格包只用 aib dm 寄一次,而且太長會落檔成「請讀:<路徑>」,
API 席的讀檔工具不准讀專案外的路徑 ⇒ 永遠讀不到;固定的系統說明(DM_SYSTEM / TOOL_SYSTEM)裡沒有人格。

這裡把人格包接在系統說明後面。系統說明每一輪都跟著請求送出,所以:不用讀檔、不怕對話被壓縮、換人格 30 秒內生效。
認哪一席:各家 main() 開席時呼叫 set_session(sid)(sid = 這一席的 session_uuid,綁定表的鍵是它的尾五碼)。
讀不到、沒綁、任何錯 ⇒ 原樣回傳系統說明,不擋對話。AIB_API_PERSONA=0 關。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_IDENTITY = _HERE.parent / "identity"

REFRESH_S = 30
_state = {"sid": "", "at": 0.0, "text": ""}


def set_session(sid: str) -> None:
    _state.update(sid=(sid or "").strip(), at=0.0, text="")


def persona_text(now: float | None = None) -> str:
    """這一席綁的人格包全文;沒綁或讀不到回空字串。最多每 REFRESH_S 秒重讀一次。"""
    if os.environ.get("AIB_API_PERSONA") == "0":
        return ""
    sid = _state["sid"]
    if not sid or sid == "anon":
        return ""
    now = time.time() if now is None else now
    if _state["at"] and now - _state["at"] < REFRESH_S:
        return _state["text"]
    text = ""
    try:
        if str(_IDENTITY) not in sys.path:
            sys.path.insert(0, str(_IDENTITY))
        if str(_HERE) not in sys.path:
            sys.path.append(str(_HERE))
        import persona_pack
        import seats_dir_lib                 # 狀態根走單源(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)
        pack = persona_pack.render_pack(sid, Path(seats_dir_lib.state_root()[0]))
        if pack.get("status") == "ok":
            text = pack.get("text") or ""
    except Exception:  # noqa: BLE001 —— 人格讀不到不能讓整席不能說話
        text = ""
    _state.update(at=now, text=text)
    return text


def with_persona(system_msg: dict) -> dict:
    """{"role": "system", "content": ...} ⇒ 同一則,content 後面接上人格包(沒有就原樣)。"""
    text = persona_text()
    if not text or not isinstance(system_msg, dict):
        return system_msg
    out = dict(system_msg)
    out["content"] = "%s\n\n%s" % (system_msg.get("content") or "", text)
    return out
