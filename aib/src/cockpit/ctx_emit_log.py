# SPDX-License-Identifier: Apache-2.0
"""EX.26 寫端 · 舊碼出聲的落點(append-only jsonl,一席一檔)。

契約定版:艦長 09:36(`a seat` 逐欄回 + 艦長裁)。**一筆一事件,讀端加總** ——
不是週期性快照;快照的話讀端加總會重複計數,而**那一格猜錯兩邊都不會叫**
(數字只會變大,而變大不觸發任何閘)。

落點 `~/.ez2-aib/instances/<N>/ctx-emit/<session_uuid>.jsonl`

每一行:

    {"schema": "ctx-emit/v1", "at_ms": int, "kind": "emit|resolve|trend|heartbeat",
     "site": str, "seat": "<完整 session_uuid>", "code": "<五碼,顯示用>",
     "addr": int, "anywhere": int, "codes": [...], "hub": str, "instance": int}

★ `seat` 是**完整 session_uuid** 不是五碼(`a seat` 09:35 的理由,逐字值得留著):
  五碼是衍生值,而它的來源這兩天正在換 —— 換之前與換之後寫的行,同一席會出現
  兩個 seat 值,而 append-only 檔會把兩段都留著。讀端把它們當兩席就低估,
  強行合併就要在讀端重建一份對照表 = 第二真相源。**兩邊用同一個鍵,對帳才
  不需要信任任何轉換**(分母那側 `live_seats(hub)` 回的鍵就是 uuid)。

★ `schema` **每一行都帶**,不是檔頭:append-only 檔沒有檔頭,v1 與 v2 混在
  同一個檔裡時,讀端看到的東西與純 v1 逐字相同 —— 沒有任何一個位置會叫。

★ **身份由呼叫端交進來,本模組不去猜。** 理由見 `record()` 的 docstring。
"""

import json
import os
import sys
import time

_MSG_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "messaging"))
if _MSG_DIR not in sys.path:
    sys.path.insert(0, _MSG_DIR)
import ctx_emit_schema as _schema  # noqa: E402  (EX.26 文法單源)

# ★ 這四個名字保留給既有呼叫端,但**值取自共用文法**,本檔不再自己寫一份。
SCHEMA = _schema.SCHEMA
SCHEMA_UNATTRIBUTED = _schema.SCHEMA_UNATTR
UNATTRIBUTED_FILE = _schema.UNATTR_FILE
KINDS = _schema.KINDS


def emit_dir(instance=None, home=None):
    """→ `<home>/instances/<N>/ctx-emit`。`instance` 拿不到就 raise,不猜。

    ★ 不給預設艙號:猜錯的話這一席的紀錄會落到**別艙的目錄**,
      而讀端在那一艙會看到一個不該存在的席、在本艙看到一席沒回報 ——
      **兩個方向的錯互相掩護**,而其中一個方向正是「推進階段 4」。
    """
    inst = instance if instance is not None else os.environ.get("EZ2_AIB_INSTANCE")
    if inst in (None, ""):
        raise ValueError("ctx-emit:拿不到 instance(EZ2_AIB_INSTANCE 未設),不猜艙號")
    root = home or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    return os.path.join(root, "instances", str(inst), "ctx-emit")


def build_row(kind, site, seat_uuid, addr=0, anywhere=0, codes=None,
              code=None, hub=None, instance=None, at_ms=None):
    """組一行並就地驗形。**壞形狀在寫端就 raise,不寫出去。**

    ★ 為什麼寫端也驗:讀端的規則是「壞行進 malformed、該輪 total 落 unknown」,
      也就是**一行寫壞會讓整輪失去結論**。寫端多驗一次的成本是零,
      而它擋掉的是「一個 bug 讓整個艦隊的翻轉條件停在 unknown」。
    ★ `heartbeat` 的 addr/anywhere 必須是 0(艦長 09:36):
      自相矛盾的行不放行 —— 心跳的意思就是「我量過了,而且沒有命中」。
    """
    # ★ 只保留「還沒組成 row 就必須擋」的那兩格:seat_uuid 要拿去當檔名,
    #   kind 要決定走哪一條路。其餘一律交給共用 validate ——
    #   **文法只有一份,寫端不再自己寫一套。**
    if not isinstance(seat_uuid, str) or not seat_uuid.strip():
        raise ValueError("ctx-emit:seat 必須是完整 session_uuid,拿到 %r" % (seat_uuid,))
    row = {
        "schema": SCHEMA,
        "at_ms": int(at_ms if at_ms is not None else time.time() * 1000),
        "kind": kind,
        "site": str(site),
        "seat": seat_uuid,
        "addr": addr,
        "anywhere": anywhere,
    }
    if code:
        row["code"] = code
    if codes:
        row["codes"] = sorted(set(str(c) for c in codes))
    h = hub if hub is not None else os.environ.get("EZ2_AIHUB_SOCKET")
    if h:
        row["hub"] = h
    inst = instance if instance is not None else os.environ.get("EZ2_AIB_INSTANCE")
    if inst not in (None, ""):
        row["instance"] = int(inst)
    # ★ 這一行是「兩端同一份文法」的落點:寫端寫得出來的行,讀端一定收得下。
    #   抽取前兩端已分岔兩處(不帶 hub/instance 的行、四值外的 site),
    #   寫端放行而讀端拒收 —— 那種行會讓讀端整輪落 unknown,而寫的人不會知道。
    _reason = _schema.validate(row)
    if _reason:
        raise ValueError("ctx-emit:%s" % _reason)
    return row


def record(kind, site, seat_uuid, **kw):
    """寫一行。回實際寫進去的那個 dict(呼叫端可以印,也可以斷言)。

    ★ **身份由呼叫端交進來(`seat_uuid` 必填),本模組不自己去查「我是誰」。**
      理由是量出來的:四個出聲點都在 `seat_address` 這種**函式庫**裡,
      而函式庫沒有「自己」—— 同一支 `label_of()` 在 dm 路徑上是某一席在講話,
      在右欄面板上是面板在畫別人。**讓函式庫去猜呼叫者的身份,猜錯的形狀是
      「某一席的紀錄掛到另一席頭上」,而那不會報錯。**
      唯一知道身份的是 dm 路徑(`dm-ctl.sh:144` 由 `TMUX_PANE` 取 `window_id`),
      所以由它取好、傳進來。

    ★ 寫失敗**不吞**:出聲一行到 stderr 再往上拋。這條路徑是遷移期的量測,
      靜默失敗的形狀正好是「這一席沒有命中」= 翻轉條件要的那個值。
    """
    home = kw.pop("home", None)          # 落點參數,不是紀錄欄位
    row = build_row(kind, site, seat_uuid, **kw)
    d = emit_dir(kw.get("instance"), home)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.jsonl" % seat_uuid)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
    return row


def record_unattributed(kind, site, **kw):
    """身份取不到時走這裡。**另一個檔、另一個 schema、沒有 seat 欄。**

    ★ 為什麼不是在 `seat` 欄放 `null`(`a seat` 09:40 提、艦長 09:40 採):
      **可空欄位正是兩種狀態被壓成一種的手法** —— 讀端看到 `"seat": null`
      還要再判斷「是取不到身份」還是「這行寫壞了」,而那個判斷會有人做錯。
      兩種東西,兩個 schema,不共用欄位;讀端「seat 必須是完整 uuid」那條
      因此**一個字都不用改**,而改判準的每一次都是一個新的洞。

    ★ 為什麼不是丟掉:丟掉的失效形狀是「數字變小」,而變小正是翻轉條件要的方向。
      逐格看(`a seat` 的證明):`addr>0` 的 unattributed 進總和 → 更難翻頁;
      `addr=0` 的沒有影響;任何 unattributed **都不得**減少 `missing_seats`
      —— 不知道是誰,就不能替任何人簽到。
      **加進來只會讓它更難翻頁,漏掉才會讓它更容易翻頁。**

    ★ 讀端硬條件(`a seat` 09:40,判準歸 `a seat`):
      **`unattributed > 0` 的那一輪結果是 `unknown`,不是把它當 0。**
      只加總「有歸屬」的那些,丟掉的偏差就從後門原封回來了。
    """
    if kind == "heartbeat":
        # ★ 未歸屬的心跳在定義上沒有意義:心跳的用途是**替某一席簽到**,
        #   而未歸屬的筆**不得減少 missing_seats**(不知道是誰,就不能替任何人簽到)。
        #   允許它的話,檔案會長出一堆零資訊的行,而讀端還要為它們寫一條例外。
        raise ValueError("ctx-emit:未歸屬的筆不得是 heartbeat(它替不了任何人簽到)")
    home = kw.pop("home", None)
    row = build_row(kind, site, "unattributed", **kw)
    del row["seat"]                       # ★ 沒有 seat 欄,不是 seat=null
    row["schema"] = SCHEMA_UNATTRIBUTED
    d = emit_dir(kw.get("instance"), home)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, UNATTRIBUTED_FILE), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def heartbeat(seat_uuid, **kw):
    """本輪我量過了,而且零命中。

    ★ 這一筆是分母的一半。少了它,**一席安靜與一席當機在帳上逐字相同**,
      而「0」正是翻轉條件要的那個值。(`a seat` 09:34 強烈建議、艦長 09:36 定版。)
    """
    kw.pop("addr", None)
    kw.pop("anywhere", None)
    return record("heartbeat", "heartbeat", seat_uuid, addr=0, anywhere=0, **kw)
