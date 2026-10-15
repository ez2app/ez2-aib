#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接繞過偵測器(E6.3 延伸,2026-08-15;the maintainer 直令「交接要寫成 hook」)。

## 這支在答什麼問題

**「機器認定的艦長,有沒有走過交接協定?」**

08-15 的事故:艦長交接用手工訊息完成,`rotation.py` 全程沒被呼叫——沒有
handoff 檔、沒有驗收紀錄、沒有 `pending_confirm`。而席位檔那邊照樣有人
掛著 `captain_assigned: true`。**兩邊都「正常」,中間那段沒有人在看。**

## ★ 治具取自實況(2026-08-15),而實況比「有人混進來」更值得擔心

    席位檔 w15.json:seat_id=b7227、role=root、captain_assigned=true
    交棒來源:a seat 於 CTX 95% 正當交棒(記憶索引可查)、pid 活著
    全機器 acceptance 紀錄:0 份

★★ **這份治具取自 `~/.ez2-aib/seats/`(非 instance 樹),不是 aihub-7 的席位。**
   拿 `~/.ez2-aib/instances/7/seats/` 去對照會對不上,那不是治具壞了,
   是**取樣來自另一棵樹**。標在這裡免得下一個人以為它壞了。
   (aihub-7 自己那棵樹裡宣稱艦長的是 `@4`/`seat_id=15368`/role=pm。)

**這是一個經正當交棒、活著的艦長席。** 所以偵測器要紅的原因**不是這個席位
有問題**——是**這條路徑本來就不留痕**。一次完全合規的交棒,在機器裡沒有
留下任何可查的東西,那不是意外,那是預設行為。

(第一次寫這段治具時把 w15 說成「殭屍席」是錯的,08-15 由第一艦長更正:
取樣=一份較早的殭屍清單,涵蓋=「現在的艦長是死的」,撐不起來。錯的版本
會讓人以為修好身分驗證就沒事了,而真正要修的是留痕。)

## 它擋不住什麼(先說清楚,免得被誤讀)

**這是偵測器,不是閘門。** 它不能阻止任何人用聊天訊息宣布交接,也不能
阻止 `captains-ctl.sh --assign` 寫下旗標——它只能讓「宣稱是艦長但拿不出
驗收紀錄」這件事**變成一個會紅的 rc**。

**尤其不要把它讀成「偽造交接已經擋住了」**:確認人 `confirmed_by` 至今
只驗格式與 seat 前綴,沒有簽章或 session binding(E6.3 codex 審高1,
未關)。寫得出對方 sig 的人可以自己走完兩階段,產出一份這支查得到、
看起來完全合格的驗收紀錄。**它擋的是「無紀錄的交接」,不是「偽造的交接」。**

## ★ 已知缺口:兩套席位命名接不起來(不假裝接得起來)

實查 2026-08-15 的兩邊資料:

    席位檔(captains-ctl 寫的)  role=root      seat_id=b7227   window=@15
    輪替存放(rotation.py 寫的) cl3 dev1 flt2 pm root2

**`root2` 不是 `root`,也不是 `b7227`。** 兩套命名各自成立、彼此沒有
對照表,所以「w15 這個艦長對應輪替存放裡哪一席」**機器現在答不出來**。

★★ **而且問題比「詞彙不同」更深:兩者的「範圍」不同。**

    席位身分  = per-instance(`~/.ez2-aib/instances/<N>/seats/`,08-15 實查 19 個 instance)
    輪替存放  = 全域(`~/.ez2-aib/rotation/`,沒有 instance 維度)

`window_id` 因此**不是唯一的 key**。08-15 實測同一個 `@4` 在兩棵樹裡是兩個席:

    instances/7/seats/w4.json   @4  seat_id=15368  role=pm    captain_assigned=true
    ~/.ez2-aib/seats/w4.json    @4  seat_id=b288f  role=dev2

**所以這不是「還沒建對照表」,是一個 per-instance 的 key 在結構上索引不到
一個全域的 store——建了對照表也會錯**,除非 key 帶上 instance。

★ 連帶:下面 `--map @15=root2` 的左側**現在就是有歧義的**(哪一個 instance
  的 @15?)。本輪不改介面(the maintainer 已裁「命名收斂先不做」),但用的人要知道:
  **`--map` 只在單一 instance 的情境下有意義。**

處理方式是 fail-closed 並且**明說是哪一種紅**:對照不出來 = `unresolved`,
計入未背書。**不猜一個對照規則**——猜錯的對照會給出「已背書」的假綠,
那比紅燈難發現得多。要消掉 unresolved,請用 `--map` 顯式宣告對照
(`--map @15=root2`),或由上游把兩套命名收斂成一套(治理決定,不在本支)。

## rc 分層(沿 E6.3:0/2/3 三態不得混為一談)

    0  所有艦長宣稱都有 accepted 驗收撐著
    3  有宣稱、有紀錄,但只到 pending_confirm(接棒者提交了,交棒者還沒背書)
    2  有宣稱而拿不出驗收紀錄(含 unresolved),或來源不可讀
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from rotation import (RotationError, acceptance_states,  # noqa: E402
                      _SEAT_RE, ADVISOR_STOP_CTX, LEAD_ROLES_DEFAULT,
                      _is_eligible_lead as rotation_mod_is_eligible,
                      checkpoints_of_kind, patrol_overdue, read_patrols,
                      read_scouts, testament_status)
import lifecycle  # noqa: E402

# ---- G1-09 艦長生命週期偵測(PLAN §2.6):門檻與 lifecycle.STAGES 同源 ----
# 10-15 維護者「只保留 A 一張表」:B 的找接班(50)/ 完整總結共治(75)/ 顧問停止(96)三格刪了,
#   表上沒有對應的格 ⇒ 這三道偵測不再自動判(值 None = 不判),不改寫成新門檻。
_STAGE_FLOOR = {name: floor for floor, name in lifecycle.STAGES}
SCOUT_FLOOR = None
FULL_SUMMARY_FLOOR = None
ADVISOR_STOP_FLOOR = ADVISOR_STOP_CTX
# finding code → 等級:red 計 rc=2、warn 計 rc=3、info 不計
LIFECYCLE_CODES = {
    "manual_only": "info",              # (e) 無機器 ctx:不進自動輪替,不判死
    "no_scout_at_50": "red",            # (a)
    "no_full_summary_at_75": "red",     # (b)
    "patrol_overdue": "warn",           # (c)
    "advisor_96_no_receipt": "red",     # (d)
    "successor_not_same_fleet": "red",  # (f) 儲君不在本艦席位檔/非主管角色
    # (f2) 帳上把儲君記成**角色名**而不是五碼:人找得到、機器對不上。
    # ★ 這在舊帳裡是常態(實查 8 筆 scout 有 6 筆記角色名),而它先前會直接變成
    #   (f) 那個 red——**紅的理由是錯的**:dev4 明明在本艦,只是帳的鍵不是五碼。
    #   指錯原因的紅會先耗光信任,然後整道閘被拿掉(本檔 P1 註解同一條)。
    #   所以拆成 warn:可自動對照就照常判,但要求把帳補成五碼。
    "successor_recorded_by_role": "warn",
    "successor_role_ambiguous": "red",  # 同 role 命中多席:不挑第一個,不猜
    "ledger_unreadable": "red",         # 五本帳任一損毀=拒用(fail-closed)
}

SEAT_SCHEMA = "aib-seat/v1"


# ★★ 這兩個預設**刻意不同**,不要「順手統一」——統一過一次了,見下。
#
# 席位是 per-instance:`~/.ez2-aib/instances/<N>/seats/`。08-15 實查有 19 個
# instance,其中 5/6/7 有 seats。
# 輪替存放是**全域**:`~/.ez2-aib/rotation/`,沒有 instance 維度
# (`find ~/.ez2-aib -maxdepth 3 -name rotation -type d` 只回一個)。
#
# 本檔第一版兩者共用 `EZ2_AIB_INSTANCE_HOME` 當 base,於是帶著 cockpit 真的
# 會設的 env 跑時,rotation 指到不存在的 `instances/7/rotation`,每一席都會
# 被判 unbacked——**方向是紅的,但紅的理由是錯的**。
#
# ★ 通則(這個 repo 反覆付過錢的一條):
#   **為了錯誤理由而紅的閘會被關掉,那跟 fail-open 的下場一樣。**
#   fail-closed 只保證方向,不保證那個紅指得出真正的原因;指錯原因的紅
#   會先耗光人的信任,然後整道閘被拿掉。
#
# 這件由 `tests/test-rotation.py` 的「兩個 base 刻意不同」釘住(**兩個一起
# 斷言**:守的是「它們不同」這件事,不是其中一個的值)。
def _aib_home():
    """instance 無關的 AIB 根;只吃 EZ2_AIB_HOME,不吃 INSTANCE_HOME。"""
    return (os.environ.get("EZ2_AIB_HOME")
            or os.path.join(os.path.expanduser("~"), ".ez2-aib"))


def default_seats_dir():
    """席位登記簿解析。順位:**HUB 導出** > EZ2_AIB_INSTANCE_HOME > 主艙。

    席位=per-instance,所以這個**要**吃 EZ2_AIB_INSTANCE_HOME;但在它之前
    要先問 HUB(EH.2,2026-08-21)。

    ★ 修前實測(本檔的缺陷長什麼樣,留著當對照組):

        AIB_DM_SOCKET=aihub-16 時
          default_seats_dir()          → ~/.ez2-aib/seats            ← 主艙,死檔
          seat_onboard.seats_dir()     → ~/.ez2-aib/instances/16/seats ← 正解

      同一台機器、同一個時刻,兩支解析器指到兩個目錄,而**兩邊都存在、
      兩邊的檔名與窗號都一樣**——拿到的是一份格式完全正確的錯答案。
      08-21 早上 seat-onboard 印錯五席 seat id 就是這顆;本檔是輪替機具,
      讀錯席位=拿死席位當繼任憑據,而它不會報錯。

    ★★ **主艙不是回退來源,是衝突。** HUB 說得出是哪一艙時就只用那一艙;
      那一艙的目錄不存在是錯誤,不是「那就用主艙的吧」。
      解析順位與 `src/cockpit/seat_onboard.py:seats_dir` 逐段對齊,不另發明第二套。
    """
    hub = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if hub and hub.startswith("aihub-"):
        inst = hub[len("aihub-"):]
        if inst:
            return os.path.join(_aib_home(), "instances", inst, "seats")
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME") or _aib_home()
    return os.path.join(home, "seats")


def default_rotation_root():
    """輪替存放=全域,所以這個**不能**吃 EZ2_AIB_INSTANCE_HOME(見上)。"""
    return os.path.join(_aib_home(), "rotation")


# ★ 艦長的定義必須與面板同源(b7227 覆審洞二)。
#
# 面板 `src/cockpit/pane-right-ai.sh` 的判準是
#     r[2] in CAPTAIN_ROLES or r[3]['captain_assigned']
# 也就是**只要 role 落在艦長角色集,就算艦長**,不必寫 captain_assigned。
#
# 本檔第一版只認 `captain_assigned is True`,於是**畫面上正戴著皇冠的那一席
# 完全不在覆蓋範圍內**——而那正是最該被守的位置。後果不是漏一席:
# **任何人只要不寫 captain_assigned、靠 role=root 當艦長,就自動繞過這道閘,
# 而且畫面上看起來完全正常。那不需要偽造,只需要什麼都不做。**
#
# 兩邊各自演化是「第二真相源」。這裡不能 import(對面是 shell 內嵌 python),
# 所以取次佳解:**讀同一個環境變數、用同一條規則,並由測試釘住對面那行
# 表達式沒有變**(形狀相同由測試看守,不由 import 綁死)。
CAPTAIN_ROLES_ENV = "EZ2_AIB_CAPTAIN_ROLES"
CAPTAIN_ROLES_DEFAULT = "root"


def base_dir_exists(seats_dir):
    return Path(seats_dir).is_dir()


def captain_roles():
    """與面板同一個來源與預設值(pane-right-ai.sh 的 CAPTAIN_ROLES)。"""
    return set((os.environ.get(CAPTAIN_ROLES_ENV)
                or CAPTAIN_ROLES_DEFAULT).split())


def role_family(role):
    """角色字面 → 家族名(剝掉尾端的 ASCII 數字)。

    `role_family('root2') == 'root'`;`role_family('qa') == 'qa'`;
    全是數字的怪字面(如 '2')原樣回傳,不回空字串——空字串會悄悄命中
    別的鍵,而怪字面本來就該掉進 fallback 被看見。

    ## 為什麼是複製而不是 import(2026-08-21,派工判準 c 要求說明)

    正本在 `src/cockpit/pane-right-ai.sh:313`,而那是 **.sh 裡的內嵌 python**:
    要 import 只能把那段原文切出來再 exec。兩種做法的**失效方向相反**:

        text-slice import  → 對方改排版/改函式名,切不出來 → 取不到家族制
                             → 退回純集合比對 → **靜默 fail-open**,而且長得跟今天一模一樣
        複製 + 分岔釘子     → 兩邊定義不一致時**釘子紅** → fail-closed

    這一格的失效值就是它今天的錯誤值(root2 不算艦長),所以不能選會 fail-open 的那邊。
    ★ 複製的代價是分岔,而分岔要有人看得見:判準見交付回報給 `a seat` 的釘子規格
      (行為比對:從 .sh 原文取出 role_family 跑同一組樣本,逐項相同)。

    ## 這條規則今天付過的錢

    面板 08-21 起 `is_captain_role` 吃家族(root2/root3 算艦長),本檔沒有,於是
    `role=root2` 的副艦長 `a seat` 在右欄戴冠,而**交接偵測器不認為他是艦長**——
    「機器認定的艦長有沒有走過交接協定」那道掃描掃不到他。
    ★ 本檔檔頭記著同型的前一次:08-15 艦長交接用手工訊息完成,rotation.py 全程沒被呼叫,
      兩邊都「正常」而中間那段沒有人在看。**同一支檔、同一個形狀、第二次。**
    """
    if not isinstance(role, str) or not role:
        return role
    i = len(role)
    while i > 0 and role[i - 1].isascii() and role[i - 1].isdigit():
        i -= 1
    return role[:i] or role


def is_captain_claim(rec, roles):
    """與面板同一條規則:role 在艦長角色集(**含家族**)**或** captain_assigned。

    回 (是不是, 憑什麼) —— 憑什麼要記下來,否則報告分不出「它自己宣告的」
    與「靠角色推定的」,而那兩者的治理意義不同。

    ★ 家族命中回 `role_family` 而不是 `role`:`root2` 是**靠家族推定**的艦長,
      與逐字命中 `root` 不是同一件事,治理上要分得開(消費端只把它記進 claim,
      沒有分支邏輯,所以多一個值不改變任何判定)。
    """
    if rec.get("captain_assigned") is True:
        return True, "captain_assigned"
    role = rec.get("role")
    if role in roles:
        return True, "role"
    if role_family(role) in roles:
        return True, "role_family"
    return False, None


def captain_claims(seats_dir):
    """席位檔裡宣稱艦長的那些窗 -> (claims, errors, scanned)。

    只認 schema 對得上的檔;壞檔不靜默跳過而是收進 errors——「讀不到所以
    沒看到」與「看過了確實沒有」是兩件事,混在一起就會拿假綠交差。

    ★ `scanned` 是 b7227 覆審洞一的修正:第一版只回 claims,於是
    「掃到 0 個席位檔」與「掃了 15 個檔沒有一個是艦長」**輸出逐字相同、
    都是綠**。上面那句 docstring 是對的,而下一個函式違反了它。
    這與本輪修的 P1 是同一個病的鏡像:P1 是「方向紅、理由錯」,
    這條是**方向綠、理由錯**——而綠的那半沒有人會去查。
    指錯目錄在這個系統裡是已經發生過的事(席位分 per-instance 與頂層兩棵樹),
    不是理論風險。"""
    claims, errors = [], []
    base = Path(seats_dir)
    if not base.is_dir():
        return claims, ["席位目錄不存在:%s" % seats_dir], 0
    roles = captain_roles()
    scanned = 0
    for path in sorted(base.glob("w*.json")):
        scanned += 1
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            errors.append("%s 不可讀/壞 JSON:%s" % (path.name, err))
            continue
        if not isinstance(rec, dict) or rec.get("schema") != SEAT_SCHEMA:
            errors.append("%s schema 不合(得到 %r)"
                          % (path.name, rec.get("schema")
                             if isinstance(rec, dict) else type(rec).__name__))
            continue
        claimed, via = is_captain_claim(rec, roles)
        if not claimed:
            continue
        claims.append({
            "file": path.name,
            "window_id": rec.get("window_id"),
            "role": rec.get("role"),
            "seat_id": rec.get("seat_id"),
            "advisor": rec.get("advisor") is True,
            "session_uuid": rec.get("session_uuid"),
            # 憑什麼算艦長:自己宣告的,還是靠角色推定的
            "claim_via": via,
        })
    return claims, errors, scanned


def fleet_seats(seats_dir):
    """本艦(seats_dir)全部合法席位檔的精簡表:[{seat_id, role, window_id}],給 (f) 對照。"""
    out = []
    base = Path(seats_dir)
    if not base.is_dir():
        return out
    for path in sorted(base.glob("w*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("schema") == SEAT_SCHEMA:
            out.append({"seat_id": rec.get("seat_id"), "role": rec.get("role"),
                        "window_id": rec.get("window_id"),
                        # G3 同一條:機器可驗的艦長段位也算合格儲君
                        "captain_assigned": rec.get("captain_assigned") is True})
    return out


def resolve_successor(succ, fleet, mapping):
    """scout 帳上的 successor_seat → (席位紀錄, 對法)。對法 ∈ seat_id | map | role | None。

    ★ 判準:**認五碼不認角色名**(艦外 08-18 裁)。但舊帳裡 successor_seat 常常記的是
      角色名(實查 8 筆有 6 筆),所以這裡**對得到就對**、另外報一條 warn 要求補帳,
      而不是丟一個「不在本艦」的紅——那個紅的理由是錯的。
    ★ role 命中多席=歧義,回 ("ambiguous_role"):不挑第一個。拿 role 當對照鍵會讓
      「某一席是儲君」擴散到所有同 role 的窗(本檔 :322 已經為這條付過錢)。
    """
    for rec in fleet:
        if succ and succ == rec.get("seat_id"):
            return rec, "seat_id"
    for rec in fleet:
        keys = [rec.get("seat_id"), rec.get("window_id")]
        if any(k and mapping.get(k) == succ for k in keys):
            return rec, "map"
    by_role = [rec for rec in fleet if succ and succ == rec.get("role")]
    if len(by_role) > 1:
        return None, "ambiguous_role"
    if by_role:
        return by_role[0], "role"
    return None, None


def lifecycle_findings(claim, seat, rotation_root, fleet, mapping,
                       now_ms=None, ctx_dir=None):
    """對一個已對照到輪替席位的艦長宣稱,做 G1-09 (a)-(f)。回 dict:
    {"rotation_mode", "ctx_pct", "stale_reason", "codes": [{"code","level","why"}]}。
    只吃機器 CTX(lifecycle.rotation_mode);拿不到=manual_only,只報 (e) 與 (f)。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    codes = []

    def add(code, why):
        codes.append({"code": code, "level": LIFECYCLE_CODES[code], "why": why})

    mode, ctx = lifecycle.rotation_mode(claim.get("session_uuid"),
                                        ctx_dir=ctx_dir, now_ms=now_ms)
    pct = ctx["pct"] if (ctx and mode == "auto") else None
    out = {"rotation_mode": mode, "ctx_pct": pct,
           "stale_reason": (ctx or {}).get("stale_reason"), "codes": codes}
    try:
        scouts = read_scouts(rotation_root, seat)
        patrols = read_patrols(rotation_root, seat)
        full = checkpoints_of_kind(rotation_root, seat, "full_summary")
        tstat = testament_status(rotation_root, seat)
    except RotationError as err:
        add("ledger_unreadable", str(err))
        return out
    # (f) 儲君必須是本艦席位且為主管角色——不管有沒有機器 ctx 都驗(帳在就驗)
    if scouts:
        last = scouts[-1]
        succ = last["successor_seat"]
        lead_roles = set(captain_roles()) | set(LEAD_ROLES_DEFAULT)
        match, how = resolve_successor(succ, fleet, mapping)
        if how == "ambiguous_role":
            add("successor_role_ambiguous",
                "scout 儲君 %r 以角色名命中本艦多席——不挑第一個,請把帳補成五碼" % succ)
        elif match is None:
            add("successor_not_same_fleet",
                "scout 最後一筆儲君 %r 對不到本艦任何席位檔(seat_id/--map)" % succ)
        else:
            if how == "role":
                add("successor_recorded_by_role",
                    "scout 儲君 %r 是**角色名**不是五碼(對到 %s/%s)——人找得到、機器對不上,"
                    "請把帳補成五碼" % (succ, match.get("seat_id"), match.get("window_id")))
            # ★ 資格判準與 rotation.scout_gate 同一條:主管角色 **或** 機器可驗的艦長段位。
            #   只認 role 白名單會讓「制度上最該當繼任者的第二艦長」在這裡變成紅,
            #   而那正是 G3 修掉的東西——閘放行了、偵測器照樣紅,等於沒修。
            ok_lead, _via = rotation_mod_is_eligible(match.get("role"),
                                                     match.get("captain_assigned"),
                                                     extra_roles=lead_roles)
            if not ok_lead:
                add("successor_not_same_fleet",
                    "儲君 %r 角色 %r 不在主管角色集 %s 且席位檔無 captain_assigned"
                    % (succ, match.get("role"), sorted(lead_roles)))
    if mode != "auto":
        # (e) fail-closed 是「不自動轉」,不是把人工席位判死:info 不計紅
        add("manual_only", "無機器 CTX(uuid=%r,reason=%s)—— 只能手動輪替"
            % (claim.get("session_uuid"), (ctx or {}).get("stale_reason")))
        return out
    if SCOUT_FLOOR is not None and pct >= SCOUT_FLOOR and not scouts:
        add("no_scout_at_50", "ctx %d%% ≥ %d 而無 scout.jsonl 紀錄(未立儲)"
            % (pct, SCOUT_FLOOR))
    if FULL_SUMMARY_FLOOR is not None and pct >= FULL_SUMMARY_FLOOR and not full:
        add("no_full_summary_at_75", "ctx %d%% ≥ %d 而無 kind=full_summary checkpoint"
            % (pct, FULL_SUMMARY_FLOOR))
    if patrol_overdue(patrols, now_ms, pct):
        add("patrol_overdue", "巡查逾期(上次 %s;規則:+10%% ctx 或 6h 先到者)"
            % (patrols[-1]["at_ms"] if patrols else "從未"))
    if claim.get("advisor") and ADVISOR_STOP_FLOOR is not None and pct >= ADVISOR_STOP_FLOOR \
            and not tstat["compact_ready"]:
        add("advisor_96_no_receipt",
            "顧問 ctx %d%% ≥ %d 而遺詔未投遞(testament=%s receipt=%s)—— 不得 compact"
            % (pct, ADVISOR_STOP_FLOOR, tstat["testament"], tstat["receipt"]))
    return out


# ★ role 不得作為對照鍵(08-15 pm4 覆審抓到的假綠):第一版把 role 放進查表
# 迴圈,於是 `--map root=root2` 讓**每一個** role=root 的窗都變成 backed
# ——本檔上方明文寫著那樣「等於讓任何一次 root 交接替所有 root 窗背書」,
# **而碼允許它**。文件說不可以、碼允許,是同一份交付裡最貴的一種不一致:
# 它讀起來像已經擋住了。修法=把 role 從查表迴圈拿掉(見 resolve_seat)。
#
# ★★ 一個我第一次修錯的地方,留著當例子:我原本想「在解析期用字元形狀擋掉
# role」,但 **`root`/`dev1`/`flt2` 這些 role 名剛好符合 seat_id 的字元集**
# ([0-9a-z]{4,8}),形狀根本分不出來。**分不出來的判準就不要假裝在分。**
# 改成一個真的測得出來的訊號:**對照鍵沒有對到任何席位就報錯**——那個
# 條件對 `--map root=root2` 一定成立,而且不必猜 key 是什麼種類。
def unused_map_keys(claims, mapping):
    """回傳沒有對到任何席位的對照鍵(排序)。

    靜默不匹配比報錯更糟:使用者會以為對照生效了,然後把結果當成已經對照過
    的結論來讀。"""
    known = set()
    for claim in claims:
        for key in (claim.get("window_id"), claim.get("seat_id")):
            if key:
                known.add(key)
    return sorted(k for k in mapping if k not in known)


def resolve_seat(claim, mapping):
    """把一個艦長宣稱對到輪替存放的席位名;對不出來回 None(**不猜**)。

    只查 window_id 與 seat_id,不查 role(見上)。沒有隱含的「同名即對照」
    ——形狀合法不代表存放裡有那一席,猜錯的對照會給出假綠。"""
    for key in (claim.get("window_id"), claim.get("seat_id")):
        if key and key in mapping:
            return mapping[key]
    return None


def probe(seats_dir, rotation_root, mapping, now_ms=None, ctx_dir=None):
    claims, errors, scanned = captain_claims(seats_dir)
    fleet = fleet_seats(seats_dir)
    findings = []
    # ★ 掃到 0 個席位檔 = 紅(b7227 覆審洞一)。沿用本 repo 既有規則:
    # `.github/workflows/test.yml` 紅線第 2 條「測試檔數為零也紅——測試被
    # 刪光/改名而 CI 還亮綠,是最惡性的假綠」。同一條規則搬到這裡:
    # **一個什麼都沒掃到的偵測器,它的綠沒有任何內容。**
    # (目錄不存在的情形上面已收成 errors,scanned=0 走這裡,兩種零分得開。)
    if base_dir_exists(seats_dir) and scanned == 0:
        errors.append(
            "席位目錄存在但一個席位檔都沒有:%s —— 掃到零筆的綠沒有內容,"
            "而且『指錯目錄』在本系統是已發生過的事(席位分 per-instance "
            "與頂層兩棵樹)" % seats_dir)
    for key in unused_map_keys(claims, mapping):
        # 進 source_errors 即計入 rc=2:一個沒對到東西的對照,說明使用者對
        # 這批資料的認知與實況不符——那本身就是要被看見的事。
        errors.append(
            "--map 的鍵 %r 沒有對到任何席位的 window_id 或 seat_id"
            "(role 不是合法對照鍵;拿它對照會替所有同 role 的窗背書)" % key)
    for claim in claims:
        seat = resolve_seat(claim, mapping)
        if seat is None:
            findings.append({**claim, "rotation_seat": None,
                             "verdict": "unresolved",
                             "why": "席位檔命名與輪替存放命名無對照(見檔頭"
                                    "已知缺口);用 --map 顯式宣告"})
            continue
        try:
            states = acceptance_states(rotation_root, seat)
        except RotationError as err:
            findings.append({**claim, "rotation_seat": seat,
                             "verdict": "unreadable", "why": str(err)})
            continue
        accepted = [e for e in states if e["status"] == "accepted"]
        if accepted:
            verdict, why = "backed", "有 %d 筆 accepted 驗收" % len(accepted)
        elif states:
            verdict, why = ("pending",
                            "只有 %d 筆 pending_confirm——接棒者提交了,"
                            "交棒者尚未背書" % len(states))
        else:
            verdict, why = "unbacked", "查無任何驗收紀錄"
        # G1-09:生命週期偵測(只對已對照到輪替席位者;unresolved 上面已 continue)
        life = lifecycle_findings(claim, seat, rotation_root, fleet, mapping,
                                  now_ms=now_ms, ctx_dir=ctx_dir)
        findings.append({**claim, "rotation_seat": seat,
                         "verdict": verdict, "why": why, "lifecycle": life})
    return {"seats_dir": seats_dir, "rotation_root": rotation_root,
            # seats_scanned 與 claim_count 必須都在:只有後者時,
            # 「掃到 0 個檔」與「掃了 15 個檔沒有一個是艦長」輸出逐字相同。
            "seats_scanned": scanned,
            "captain_roles": sorted(captain_roles()),
            "claim_count": len(claims), "findings": findings,
            "source_errors": errors}


def _lifecycle_levels(report):
    return [c["level"] for f in report["findings"]
            for c in (f.get("lifecycle") or {}).get("codes", [])]


def verdict_rc(report):
    bad = [f for f in report["findings"]
           if f["verdict"] in ("unbacked", "unresolved", "unreadable")]
    levels = _lifecycle_levels(report)
    if report["source_errors"] or bad or "red" in levels:
        return 2
    if any(f["verdict"] == "pending" for f in report["findings"]) \
            or "warn" in levels:
        return 3
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="handover-probe",
        description="偵測「宣稱是艦長但拿不出交接驗收」的席位(E6.3 延伸)")
    parser.add_argument("--seats-dir", default=None)
    parser.add_argument("--rotation-root", default=None)
    parser.add_argument("--map", action="append", default=[],
                        metavar="KEY=SEAT",
                        help="顯式對照,如 --map @15=root2(可重複)")
    parser.add_argument("--now-ms", type=int, default=None,
                        help="時刻注入(測試用;預設牆鐘)")
    parser.add_argument("--ctx-dir", default=None,
                        help="機器 CTX 目錄(預設 $EZ2_CTX_DIR / ~/.ez2/ctx)")
    args = parser.parse_args(argv)

    mapping = {}
    for item in args.map:
        if "=" not in item:
            print("handover-probe: --map 要 KEY=SEAT 格式:%r" % item,
                  file=sys.stderr)
            return 2
        key, seat = item.split("=", 1)
        if not _SEAT_RE.fullmatch(seat):
            print("handover-probe: 壞輪替席位名:%r" % seat, file=sys.stderr)
            return 2
        mapping[key] = seat

    report = probe(args.seats_dir or default_seats_dir(),
                   args.rotation_root or default_rotation_root(), mapping,
                   now_ms=args.now_ms, ctx_dir=args.ctx_dir)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                indent=2) + "\n")
    rc = verdict_rc(report)
    if rc != 0:
        for item in report["source_errors"]:
            print("handover-probe: 來源問題:%s" % item, file=sys.stderr)
        for f in report["findings"]:
            if f["verdict"] != "backed":
                print("handover-probe: %s %s(%s)=%s:%s"
                      % (f["file"], f.get("window_id"), f.get("role"),
                         f["verdict"], f["why"]), file=sys.stderr)
            for c in (f.get("lifecycle") or {}).get("codes", []):
                if c["level"] != "info":
                    print("handover-probe: %s %s(%s) lifecycle %s [%s]:%s"
                          % (f["file"], f.get("window_id"), f.get("role"),
                             c["code"], c["level"], c["why"]), file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
