#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""主管角色集——**全庫唯一正本**(艦外 08-18 21:33 裁 (b)+;dev2 主刀)。

## 病(root2 抓的)

同一個「誰算主管」存在兩份字面清單:appoint.py 的 SUCCESSOR_ROLES(入口,
含 root2)與 rotation.py 的 LEAD_ROLES_DEFAULT(驗證端,**沒有** root2)。
驗證端比入口窄的後果已經付過帳:d482c(真 role=root2)過不了 G3 字面閘,
五筆 scout 權宜把 role 寫成 root 才過——**帳被清單的分岔逼著說謊**。

## 方(擇「第三常數模組」而非 rotation 匯入 appoint,理由)

appoint 在 src/cockpit(上層工具、拉著 captains-ctl/subprocess),rotation
在 src/rotation(底層帳本)。讓底層 import 上層=層次倒掛;反向讓 appoint
import rotation 又把入口綁死在帳本模組。放 src/policy(政策層本來就住這)
一個零依賴小模組,入口(appoint)、寫端閘(rotation.scout_gate)、讀端
(handover_probe (f))三處同匯——grep 全庫只剩這一份清單。

## lead 字面的處置(pm2 0o 搜出:全庫零席零引用)

**刪。** 它只出現在 appoint.SUCCESSOR_ROLES 一行,無任何席位用過此 role、
無第二處引用——默默匯入一個沒人用的角色=零樣本的綠。出處:appoint.py
初版(bd1da58,08-18)手寫清單帶入。root2 主審若裁「留」,在 LEAD_ROLES
加回一字+補負向測即可(一行事)。

## 唯一解析函式

is_eligible_lead(role, captain_assigned, extra_roles) —— 過閘=role 在
單一清單(∪ extra_roles,如 EZ2_AIB_CAPTAIN_ROLES)**或** captain_assigned
(機器可驗的艦長段位)。寫端(scout_gate)與讀端(probe (f))都必須呼叫
這一支,不得各自 in 一份集合——分岔就是這次的病。
"""

LEAD_ROLES = ("pm", "pm2", "pm3", "root", "root2")


def lead_role_set(extra_roles=()):
    """單一清單 ∪ 環境增補(如 captain_roles())。"""
    return set(LEAD_ROLES) | {r for r in (extra_roles or ()) if r}


def is_eligible_lead(role, captain_assigned=False, extra_roles=()):
    """唯一解析函式:回 (ok, via);via ∈ 'role' | 'captain_assigned' | None。
    role 判定在前(與 G3 既有語意一致:role 過了就不必查席位檔)。"""
    if role in lead_role_set(extra_roles):
        return True, "role"
    if captain_assigned:
        return True, "captain_assigned"
    return False, None
