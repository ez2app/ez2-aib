#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""像素工坊真生圖放行的本人核准(aib-plugins #320;@aeb40 10-30 定介面)。

外掛 `aib pixel approve [--hours N]` 從核心載入這支、叫 ask(hours);回 ok 才由外掛寫 real-gen-ok.json(until = 現在 + hours)。
  - 類別 pixel_real_gen:真生圖會扣外部生圖額度 ⇒ 高風險、一次一按、不開時間窗(放行多久由外掛檔的 until 表示,不用授權單)。
  - hours 只收 1–24 的整數(bool / 字串 / 浮點 ⇒ bad_hours),在 ready() 和彈窗之前就擋;彈窗那句帶 hours,本人看得到放行多久。
  - 回 {"ok", "why"};why = ok / bad_hours / off / unavailable / busy / refused(視窗取消 / 逾時 / 失敗 / 句子組不出都算 refused)。
  - 放行檔只防誤跑、不算防線(GUIDE-same-user-ai-threat-model):同帳號 AI 寫得到那個檔;防線是這一次指紋 / 登入密碼。
"""
import os

MAX_HOURS = 24


def ask(hours, env=None, platform=None, touchid=None, touchid_available=None):
    """彈 Touch ID 問本人要不要放行真生圖 hours 小時。platform / touchid / touchid_available 只給測試注入。"""
    import approve_gate as AG
    if type(hours) is not int or not 1 <= hours <= MAX_HOURS:   # bool 是 int 的子類,用 type 比
        return {"ok": False, "why": "bad_hours"}
    env = dict(os.environ if env is None else env)
    no = AG.ready(env, platform, touchid_available)
    if no:
        return {"ok": False, "why": no}
    r = AG.reason("pixel_approve.real_gen", {"hours": str(hours)}, env=env)
    res = AG.ask(r, AG.state_root_default(env), touchid)
    if res.get("ok") is True:
        return {"ok": True, "why": "ok"}
    return {"ok": False, "why": "busy" if res.get("why") == "busy" else "refused"}
