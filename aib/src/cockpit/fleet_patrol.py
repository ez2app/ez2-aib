#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦隊巡查器 — 把「人肉巡查」做成機器判準(the maintainer 2026-08-18 直令)。

兩件事程式化:
  1. **對話欄卡住偵測**:艦外指揮今晚實測到五種形態,靠人眼逐席看不可持續,
     且我自己就誤判過兩次(把 AI 自留提示當卡稿、把忙碌排隊當沒送出而重按)。
  2. **開新席閘**(the maintainer 直令):開新席的前提=**全艦至少半數席位 CTX > 80%**。
     不成立就把任務發回原席繼續做,不准開新席稀釋。

## 對話欄五種形態(判準寫進 classify_box)

| 形態 | 判準 | 處置 |
|---|---|---|
| empty | `❯` 後無字 | 無 |
| queued | 畫面含 `Press up to edit queued messages` | **等**(框有字≠沒送出) |
| dm_draft | 框內容以 `[role@seat >` 開頭(dm 標頭格式) | 可代按(the maintainer 08-13 常設授權) |
| human_draft | 框有字、無 dm 標頭、且**不是**該席自產提示 | **絕不代按** |
| self_note | 框有字、無 dm 標頭、但與該席近期輸出同源(AI 自己排的下一步) | 不動 |

★ **草稿年齡規則(the maintainer 2026-08-18 直令:5 分鐘)**:human_draft 的內容預設遮蔽
(只回長度),但**同一份草稿滯留超過 5 分鐘未變動**時可讀取內容判斷。理由:真人
手稿不會擱那麼久,超時多半代表「有東西該處理而沒人處理」;而遮蔽的代價是那件事
永遠沒人處理。★ 讀取一律留痕(`~/.ez2-aib/logs/draft-read.jsonl`),不是靜默放行
——放寬隱私邊界必須看得見誰在什麼時候讀了什麼。年齡由**內容指紋首見時間**算
(存 `~/.ez2-aib/state/draft-seen.json`),不是畫面時間:改一個字就重新計時。

★ human_draft 與 self_note 的分野:兩者都無 dm 標頭。判準取「這行字有沒有出現在
  該席自己最近的輸出裡」——AI 自留提示是它自己剛講過的話,人的手稿不會。
  分不清時一律歸 human_draft(寧可不代按,錯按人的稿比漏按嚴重)。

## 另外兩種非對話欄停擺(也偵測)

  login_expired  畫面含 "Login expired" → 只有真人能修,回報不處置
  awaiting_owner 席位自述在等 the maintainer 本人授權 → 回報不處置

CLI:
    fleet_patrol.py scan [--hub aihub-13] [--json]      # 全席巡查
    fleet_patrol.py seat-gate [--hub aihub-13] [--json] # 開新席閘(rc 0=可開 3=不可開)
    fleet_patrol.py bind-notices [--hub aihub-13] [--json] # 右欄綁定提醒快照
rc:0 正常/2 用法或環境錯/3 閘不成立(seat-gate)或有需人處置項(scan --strict)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import sys

# ED.1(2026-08-22):狀態根順位單源(EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。
_HERE_ED1 = os.path.dirname(os.path.abspath(__file__))
if _HERE_ED1 not in sys.path:
    sys.path.insert(0, _HERE_ED1)
import seats_dir_lib  # noqa: E402
import d17_attention_view  # noqa: E402
import seat_persona  # noqa: E402

HUB_DEFAULT = os.environ.get("EZ2_AIHUB_SOCKET", "aihub")

# 開新席閘門檻(the maintainer 2026-08-18 直令):半數席位 CTX 超過 80% 才准開新席。
# ★ 為什麼是「半數超過 80%」而不是「有一席滿」:開新席的成本是真的——
#   每艙每席都帶自己的輪詢與渲染稅(今晚實測 load 9→5 有一半來自收艙),
#   而且新席是零上下文的新生兒,交接成本比讓原席做完更高。只有當**整支艦隊
#   普遍逼近容量**時,增員才划算;否則任務發回原席繼續完成。
# EH.28:門檻單一資料源。**在模組層先定義**,因為下面的常數在 import 時就求值,
# 而本檔原本把 rotation 目錄插進 sys.path 是在函式裡(第 150 行附近)——
# ★ 順序是這一格唯一會出錯的地方,而它的失效形狀是 ImportError(會叫),
#   不是靜默預設值。那是刻意的:寧可 import 就炸,也不要靜默拿到別的數字。
def _ctx_policy():
    _rot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rotation")
    _rot = os.path.normpath(_rot)
    if _rot not in sys.path:
        sys.path.insert(0, _rot)
    import ctx_policy
    return ctx_policy


# 10-15 維護者「只保留 A 一張表」(policy 只剩各席 7 格):B 的 actions.prepare_compact(80)刪了,
#   增員門檻不改寫成新數字 ⇒ None = 開新席閘不再自動判,要不要加開由人決定(seat_gate 回不准 + 說明)。
SEAT_GATE_CTX_THRESHOLD = None
SEAT_GATE_FRACTION = 0.5

# dm 標頭:`[發送者 > 收件者]`。發送者兩形都要收——`role@五碼`(dm-ctl 正常
# 推導)與**裸 role**(席位檔缺欄或跨艙轉述時的降級形)。實測 08-18:
# `[dev5 > a seat]` 被首版正則漏掉 → 歸成 self_note → 該代按的沒按。
# 判準錨在「有 > 分隔且右側是 role@五碼」,不在左側形狀。
_DM_HEAD_RE = re.compile(r"^\[[a-z0-9_-]+(@[0-9a-f]{4,8})?\s*>\s*[a-z0-9_-]+@[0-9a-f]{4,8}")
_CTX_RE = re.compile(r"CTX (\d+)%")
_PROMPT_RE = re.compile(r"^❯\s?(.*)$")

QUEUED_MARK = "Press up to edit queued messages"
LOGIN_EXPIRED_MARK = "Login expired"


def _tmux_probe(hub, *args):
    """回 (rc, stdout)。★ 存在的理由:`_tmux` 把 rc 吃掉了,而**「伺服器不通」與
    「伺服器活著但沒有輸出」拿到的都是空字串**。空字串當狀態用沒事(降級成沒有值),
    當**分母**用會出人命:分母 0 = 「所有席都回報了」= 直接推進階段 4。
    所以需要一個看得到 rc 的入口 —— 不是另寫一份呼叫,是把同一份拆開。"""
    cmd = ["env", "-u", "TMUX", "tmux", "-L", hub] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout


def _tmux(hub, *args):
    rc, out = _tmux_probe(hub, *args)
    return out if rc == 0 else ""


def live_windows(hub):
    """回 [(window_id, name, dead)];tmux 不通=空(呼叫端 fail-closed)。"""
    out = _tmux(hub, "list-panes", "-a", "-F",
                "#{window_id}|#{window_name}|#{pane_dead}")
    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2] == "1"))
    return rows


def capture(hub, wid, lines=60):
    return _tmux(hub, "capture-pane", "-p", "-S", "-%d" % lines, "-t", wid)


def ctx_of(screen):
    """畫面上印的 CTX%(降級後備)。★ 這是**渲染後的字**,不是量測值。"""
    hits = _CTX_RE.findall(screen or "")
    return int(hits[-1]) if hits else None


# ---- CTX 真值:機器探針優先,畫面值只當降級後備(the maintainer 08-18 同時採納的邊界)----
# ★ 病根:巡查器讀畫面上的 `CTX 58%`,而輪替協定(rotation/lifecycle)讀
#   `$EZ2_CTX_DIR/<uuid>.pct`。**同一個量有兩套判準、兩個來源**,兩邊漂移的時候
#   沒有人會發現——畫面那條還特別脆:窗太窄、狀態列改版、席位剛開還沒渲染,
#   任何一種都會讓它變成 None 或舊值,而 seat_gate 把 None 當「未超過」。
# 修法不是把畫面那條刪掉(它是唯一在探針拿不到時還有值的來源),而是
#   **分級 + 標源**:machine > screen > none,並讓輸出誠實說出這個值是哪來的。
_SEATS_DIR_ENV = ("EZ2_AIB_INSTANCE_HOME", "EZ2_AIB_HOME")


def _seats_dir(hub=None):
    """席位檔目錄。順位:--hub 導出 > EZ2_AIB_INSTANCE_HOME > EZ2_AIB_INSTANCE(與
    _pulse_path 同一套解析,同一個真相源=「這次實際在掃誰」)。

    ★ 三修(艦外 08-18 15:0x 親跑抓到,交 dev1):**同一個 bug 兩個呼叫點,只修了一個**
    ——二修把 _pulse_path 改成由 --hub 導出,_seats_dir 原封不動只認 env,查無就掉到
    主艙 ~/.ez2-aib/seats(真的存在 w1~w15)。後果:`scan --hub aihub-13` 讀的是**主艙**
    席位檔的 uuid,再拿去查 .pct——今天沒出事只因為那些 uuid 的 .pct 剛好 stale 而
    安靜降級成 screen;它們要是新鮮的,巡查器會把**別艙的 CTX 印在 13 艙的窗號底下**。"""
    root = seats_dir_lib.state_root()[0]          # ED.1 順位單源
    inst = instance_of_hub(hub)
    if inst:
        return os.path.join(root, "instances", inst, "seats")
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
    if not home:
        env_inst = os.environ.get("EZ2_AIB_INSTANCE")
        cand = os.path.join(root, "instances", str(env_inst)) if env_inst else None
        home = cand if (cand and os.path.isdir(cand)) else root
    return os.path.join(home, "seats")


def seat_session_uuid(wid, seats_dir=None):
    """窗 id -> 席位檔記的 session uuid;讀不到回 None(不猜)。"""
    if not wid or not wid.startswith("@") or not wid[1:].isdigit():
        return None
    path = os.path.join(seats_dir or _seats_dir(), "w%s.json" % wid[1:])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
        return None
    uuid = rec.get("session_uuid")
    return uuid if isinstance(uuid, str) and uuid else None


def _seat_record(wid, seats_dir=None, hub=None):
    """Read one main-seat record for patrol-owned product callpoints.

    A subagent has no seat file, so a valid ``aib-seat/v1`` record is the
    structural main-seat boundary.  Missing or malformed records are not
    guessed from the window name.
    """
    if not wid or not wid.startswith("@") or not wid[1:].isdigit():
        return None
    path = os.path.join(seats_dir or _seats_dir(hub), "w%s.json" % wid[1:])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1" \
            or str(rec.get("window_id") or "") != wid:
        return None
    return rec


def _ctx_scheduler_result(reason, rc=2, **fields):
    return {"ok": rc == 0, "reason": reason, "rc": rc, **fields}


def _ctx_scheduler_tick(wid, hub, now_ms):
    """Tick the existing durable scheduler for one scanned main seat.

    Patrol owns no CTX threshold, binding, handoff-capability or scheduler
    state.  It resolves the sealed active binding, reads the durable
    generation, and delegates one tick to ``fleet_identity.ctx_scheduler``.
    Every refusal is returned as a typed receipt on the patrol row; a missing
    callpoint therefore cannot look like a successful manual-only decision.
    """
    rec = _seat_record(wid, hub=hub)
    required = ("project_id", "fleet_id", "fleet_member_id", "session_uuid")
    if rec is None or any(
            not isinstance(rec.get(name), str) or not rec[name]
            for name in required):
        return _ctx_scheduler_result("ctx_scheduler_patrol_seat_unbound")

    state_root = seats_dir_lib.state_root()[0]
    ctx_dir = os.environ.get("EZ2_CTX_DIR") or os.path.expanduser("~/.ez2/ctx")
    policy_path = os.environ.get("EZ2_AIB_CTX_POLICY_PATH") \
        or os.environ.get("EZ2_AIB_CTX_POLICY") \
        or _ctx_policy().policy_path()
    authority_path = os.environ.get("EZ2_AIB_AUTHORITY_PATH")
    if not all(isinstance(value, str) and os.path.isabs(value)
               for value in (state_root, ctx_dir, policy_path)):
        return _ctx_scheduler_result("ctx_scheduler_patrol_config_invalid")

    try:
        src_dir = os.path.normpath(os.path.join(_HERE_ED1, ".."))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from fleet_identity import bind_apply, ctx_scheduler

        resolved = bind_apply.execute(
            Path(state_root), Path(authority_path) if authority_path else None,
            {
                "operation": "resolve",
                "project_id": rec["project_id"],
                "fleet_id": rec["fleet_id"],
                "fleet_member_id": rec["fleet_member_id"],
            },
            int(now_ms),
        )
        if not resolved.get("ok"):
            return _ctx_scheduler_result(
                resolved.get("reason", "ctx_scheduler_patrol_binding_unresolved"),
                int(resolved.get("rc", 2)),
            )
        binding = resolved.get("binding")
        if not isinstance(binding, dict) \
                or binding.get("session_uuid") != rec["session_uuid"]:
            return _ctx_scheduler_result(
                "ctx_scheduler_patrol_binding_holder_mismatch", 1)

        binding_receipt = resolved.get("receipt")
        status = ctx_scheduler.execute(
            Path(state_root), Path(authority_path) if authority_path else None,
            {
                "schema": ctx_scheduler.REQUEST_SCHEMA,
                "operation": "status",
                "binding_receipt": binding_receipt,
            },
            int(now_ms),
        )
        if not status.get("ok"):
            return status
        generation = status.get("scheduler_generation")
        if not isinstance(generation, int) or isinstance(generation, bool):
            return _ctx_scheduler_result("ctx_scheduler_patrol_status_invalid")

        raw_policy = Path(policy_path).read_bytes()
        policy = _ctx_policy().load(policy_path)
        policy_generation = policy.get("schema_version")
        if not isinstance(policy_generation, (str, int)) \
                or isinstance(policy_generation, bool):
            return _ctx_scheduler_result("ctx_scheduler_patrol_policy_invalid")

        observation = _machine_ctx(
            rec["session_uuid"], ctx_dir=ctx_dir, now_ms=int(now_ms))
        observation_sha = hashlib.sha256(json.dumps(
            observation, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        request = {
            "schema": ctx_scheduler.REQUEST_SCHEMA,
            "operation": "tick",
            "binding_receipt": binding_receipt,
            "session_uuid": rec["session_uuid"],
            "ctx_dir": ctx_dir,
            "ctx_policy_path": policy_path,
            "ctx_policy_sha256": hashlib.sha256(raw_policy).hexdigest(),
            "ctx_policy_generation": str(policy_generation),
            "expected_scheduler_generation": generation,
            # The observation fingerprint distinguishes missing/stale/fresh
            # while generation keeps a post-emission replay request identical.
            "idempotency_key": "patrol:%s:%d:%s" % (
                rec["session_uuid"], generation, observation_sha),
        }
        return ctx_scheduler.execute(
            Path(state_root), Path(authority_path) if authority_path else None,
            request, int(now_ms))
    except (OSError, TypeError, ValueError):
        return _ctx_scheduler_result("ctx_scheduler_patrol_input_unavailable")
    except Exception:
        return _ctx_scheduler_result("ctx_scheduler_patrol_internal_error")


def _machine_ctx(uuid, ctx_dir=None, now_ms=None):
    """借 rotation/lifecycle 的讀端(唯一合法的機器 CTX 來源;它自己有 stale/型別/
    路徑那一整套閘)。載不到該模組=沒有機器值,誠實降級,不自己另寫一份讀法。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        rot = os.path.join(os.path.dirname(here), "rotation")
        if rot not in sys.path:
            sys.path.insert(0, rot)
        import lifecycle
    except Exception:                                    # noqa: BLE001
        return None
    try:
        return lifecycle.read_machine_ctx(uuid, ctx_dir=ctx_dir, now_ms=now_ms)
    except Exception:                                    # noqa: BLE001
        return None


def _seat_address_code(wid, seats_dir=None, hub=None):
    """窗 id → 席位五碼;讀不到回 None(**不用角色名頂替**)。
    來源收在 seat_address.code_of 一處:the maintainer 未定 seat_id vs uuid 尾碼,
    答完只改那個函式,這裡不自己再取一次(第二個真相源今天已經治了一整天)。"""
    import json as _json
    if not wid or not wid.startswith("@") or not wid[1:].isdigit():
        return None
    path = os.path.join(seats_dir or _seats_dir(hub), "w%s.json" % wid[1:])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rec = _json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
        return None
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import seat_address
    except Exception:                                    # noqa: BLE001
        return None
    # ★ EH.17:巡查欄顯示的是「我們**實際會發出去**的那個碼」,不是 code_of()。
    #   遷移期對還沒有 uuid 的席發舊碼(seat_address.label_of 的 allow_legacy),
    #   若這裡用 code_of() 就會對那些席回 None —— 那一欄變空白,而空白讀起來像
    #   「這席沒有碼」而不是「這席還沒換源」。全艙實測有 10 份席位檔屬於這一類。
    #   `code_in_label(label_of(rec))` 讓顯示與發送用同一條規則,只有一個來源。
    return seat_address.code_in_label(seat_address.label_of(rec))


class SeatsUnavailable(RuntimeError):
    """活席名單取不到(hub 的 tmux server 不通)。

    ★ 它是**例外**不是空清單,而這一格是刻意的:`live_seats()` 的主要消費者是
      EX.26 的**分母**,而分母的失效值若等於「零席」,一次 tmux 不通就會讓
      「所有席的 addr 連續 N 次為 0」成立 —— **探測器的失效值不得等於它表示安全的那個值。**
      回空清單只可能是「這個 hub 上沒有席位檔對得上活窗」,不可能是「連不上」。
    """


def seat_rows(hub):
    """該 hub 上**每一個對得上窗的席位檔**,含死窗;`tmux_alive` 是真的觀測值。

    ★ 存在的理由:`live_seats()` 只回活的,所以它那一欄 `tmux_alive` **恆為 true**
      —— 一個只能取一個值的欄位撐不起任何斷言(EH.21b 的 `calibrated=True` 同病)。
      而「排除了哪幾席」要能印出來,那份名單只能從這裡拿。
      **一份列舉,兩個問題各自回答;不是兩份列舉。**
    """
    return _seat_rows(hub)


def live_seats(hub):
    """活席名單:席位檔 ∩ 該 hub tmux server 上**還活著**的窗。

    回 `[{"wid", "uuid", "code", "last_used_epoch", "tmux_alive"}]`,依窗號數值排序。
    hub 的 tmux server 不通 -> 丟 `SeatsUnavailable`(見該類的說明)。
    ★ 這一支回傳裡的 `tmux_alive` 恆為 true(死的在上面就濾掉了)——
      它是**不變式不是觀測值**,不要拿它當「我驗過這席活著」的證據;要觀測值用 `seat_rows()`。

    ★★ T3(`a seat` 09:5x 抓到,本席的缺陷):前一版寫 `wid not in alive`,
      而 `alive` 是 `{窗: 有沒有活 pane}` —— **全 pane 皆死的窗仍然 `in alive`**
      (值是 `False`),於是它照樣進分母。那一席永遠不可能回報 ->
      每一輪都 `unknown` -> **連續零輪數永遠是 0 -> 階段 4 永遠到不了。**
      ★ 當時的註解逐字寫著「死窗/無此窗不入分母」—— **註解與碼相反,而碼沒做到**。
        我 09:47 才對別席說過「註解擋不住任何人」,自己這一支就是實例。
      ★★ 它與我 09:45 警告 `a seat` 的那件事**逐字同型**:更嚴的條件做出一道
        打不開的閘。我警告了別人,而我自己的碼裡就有一個 ——
        差別只在他那條還沒落地,我這條已經推上去了。
    """
    return [r for r in _seat_rows(hub) if r["tmux_alive"]]


def _seat_rows(hub):
    """實作單源:兩支公開函式共用這一份列舉。

    ★ 本函式**不做化石艙排除**(艦長 09:32 裁)。理由:生命週期閘要的「活席」與
      巡查分母要的「應回報席」,在「活著但今天沒動的艙」那一格上要的答案不一樣。
      **一個函式不要同時回答兩個問題**;排除規則住在呼叫端。
    """
    rc, out = _tmux_probe(hub, "list-panes", "-a", "-F",
                          "#{window_id}|#{pane_dead}")
    if rc != 0:
        raise SeatsUnavailable("tmux -L %s 不可達(rc=%d)" % (hub, rc))
    alive = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].startswith("@"):
            # 同一窗多 pane:只要有一個沒死就算活
            alive[parts[0]] = alive.get(parts[0], False) or (parts[1] != "1")
    seats_dir = _seats_dir(hub)
    rows = []
    for name in sorted(os.listdir(seats_dir)) if os.path.isdir(seats_dir) else []:
        if not (name.startswith("w") and name.endswith(".json")):
            continue
        wid = "@" + name[1:-5]
        if not wid[1:].isdigit() or wid not in alive:
            continue                       # 這個 hub 上沒有這個窗 -> 不是這艙的席
        try:
            with open(os.path.join(seats_dir, name), "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
            continue
        if str(rec.get("window_id") or "") != wid:
            continue                       # 檔名與欄位交叉驗(既有紀律,同 dm-ctl)
        lu = rec.get("last_used_epoch")
        rows.append({
            "wid": wid,
            "uuid": rec.get("session_uuid") if isinstance(rec.get("session_uuid"), str) else None,
            "code": _seat_address_code(wid, seats_dir=seats_dir, hub=hub),
            "last_used_epoch": lu if isinstance(lu, (int, float)) else None,
            "tmux_alive": bool(alive.get(wid)),
        })
    rows.sort(key=lambda r: int(r["wid"][1:]))
    return rows


def resolve_ctx(wid, screen, seats_dir=None, ctx_dir=None, now_ms=None, hub=None):
    """回 (pct, source):source ∈ machine | screen | none。

    machine = `$EZ2_CTX_DIR/<uuid>.pct` 的非 stale 值(與輪替協定同一個真相源)。
    screen  = 畫面上印的字(探針拿不到時的後備;stale 的機器值也算拿不到)。
    none    = 兩邊都沒有 —— seat_gate 依舊把它當「未超過」(fail-closed)。
    """
    uuid = seat_session_uuid(wid, seats_dir=seats_dir or _seats_dir(hub))
    ctx = _machine_ctx(uuid, ctx_dir=ctx_dir, now_ms=now_ms) if uuid else None
    if ctx and not ctx.get("stale"):
        return ctx["pct"], "machine"
    pct = ctx_of(screen)
    return (pct, "screen") if pct is not None else (None, "none")


BIND_NOTICE_SCHEMA = "aib-fleet-bind-notices/v1"
_FIVE_CODE_RE = re.compile(r"^[0-9a-f]{5}$")


def _binding_display(path=None):
    """回可判讀的全艦投影；缺檔或壞檔是 unknown，不得當成未綁。"""
    path = path or os.environ.get("EZ2_AIB_SEAT_DISPLAY")
    if not path:
        path = os.path.join(
            seats_dir_lib.state_root()[0], "identity", "seat-display.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != "0.1.0-draft" \
            or not isinstance(doc.get("display"), dict):
        return None
    return doc["display"]


def _binding_targets(rec, display):
    """回 (人格已綁,第二大腦已綁)；None 表示投影有資料但形狀不可判。"""
    if not isinstance(rec, dict) or not isinstance(display, dict):
        return None, None
    entry = seat_persona.display_entry(rec, display)
    if entry is None:
        keys = [seat_persona.display_key(rec)]
        legacy = rec.get("seat_id") if isinstance(rec, dict) else None
        if legacy not in keys:
            keys.append(legacy)
        if any(isinstance(key, str) and key in display for key in keys):
            return None, None
        return False, False
    persona_uid = entry.get("persona_uid")
    persona_bound = True if isinstance(persona_uid, str) and persona_uid else None
    brain_status = entry.get("brain_binding_status")
    route_key = entry.get("route_key")
    if brain_status == "bound" and isinstance(route_key, str) and route_key:
        brain_bound = True
    elif brain_status == "unbound" and route_key is None:
        brain_bound = False
    else:
        brain_bound = None
    return persona_bound, brain_bound


def _bind_attention_notice(code, pct, source, persona_bound, brain_bound):
    """只把 d17 的既有決定補上席碼與第二個綁定目標，不重算門檻。"""
    if not _FIVE_CODE_RE.fullmatch(code or ""):
        return None
    if persona_bound is None or brain_bound is None:
        return None
    reason = (d17_attention_view.UNBOUND_REASON
              if not (persona_bound and brain_bound) else None)
    src_dir = os.path.normpath(os.path.join(_HERE_ED1, ".."))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    from fleet_identity.bind_attention import CTX_USED_THRESHOLD
    line = d17_attention_view.patrol_attention_line(
        pct, source, CTX_USED_THRESHOLD, scheduler_reason=reason)
    suffix = "該綁人格"
    if not isinstance(line, str) or not line.endswith(suffix):
        return None
    return "@%s %s與第二大腦" % (code, line)


def _opening_candidate_count(wid, hub):
    """重用開席器的 dry-run 結果；只有 not_unique 才回候選數。"""
    identity_dir = os.path.normpath(os.path.join(_HERE_ED1, "..", "identity"))
    if identity_dir not in sys.path:
        sys.path.insert(0, identity_dir)
    try:
        import seat_rebind
        rc, result = seat_rebind.rebind(
            seats_dir_lib.state_root()[0], _seats_dir(hub), wid, dry_run=True)
    except Exception:
        return None
    why = (result.get("why") or "").split(";")[-1].strip() \
        if isinstance(result, dict) else ""
    count = result.get("candidates") if isinstance(result, dict) else None
    if rc != 0 or result.get("action") != "suggest" \
            or seat_rebind.reason_code(why) != seat_rebind.REASON_NOT_UNIQUE \
            or type(count) is not int or count < 2:
        return None
    return count


def _bind_notice_windows(hub):
    """使用右欄同輪快照；直接呼叫 CLI 時才自行取一份帶真 rc 的快照。"""
    if "EZ2_HUB_WINDOWS" in os.environ:
        rows = []
        seen = set()
        for line in os.environ.get("EZ2_HUB_WINDOWS", "").splitlines():
            if not line:
                continue
            parts = line.split("|")
            wid = parts[0]
            if not re.fullmatch(r"@[0-9]+", wid):
                raise SeatsUnavailable("EZ2_HUB_WINDOWS 有不合法窗號")
            role = parts[1] if len(parts) > 1 else ""
            if wid not in seen:
                rows.append((wid, role))
                seen.add(wid)
        return rows

    rc, out = _tmux_probe(hub, "list-panes", "-a", "-F",
                          "#{window_id}|#{window_name}|#{pane_dead}")
    if rc != 0:
        raise SeatsUnavailable("tmux -L %s 不可達(rc=%d)" % (hub, rc))
    live = {}
    order = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 3 or re.fullmatch(r"@[0-9]+", parts[0]) is None:
            raise SeatsUnavailable("tmux 活窗快照形狀不合")
        wid, role, dead = parts[0], parts[1], parts[2]
        if dead not in ("0", "1"):
            raise SeatsUnavailable("tmux pane_dead 形狀不合")
        if wid not in live:
            live[wid] = [role, False]
            order.append(wid)
        live[wid][1] = live[wid][1] or dead == "0"
    return [(wid, live[wid][0]) for wid in order if live[wid][1]]


def bind_notices(hub):
    """產一份完整、只讀的右欄綁定提醒快照。"""
    display = _binding_display()
    if display is None:
        raise SeatsUnavailable("seat-display 投影缺失或不合法")
    observed = []
    for wid, role in _bind_notice_windows(hub):
        if role in ("watch", "lobby") or role.startswith("sys."):
            continue
        rec = _seat_record(wid, hub=hub)
        if rec is None:
            continue
        code = _seat_address_code(wid, hub=hub)
        if _FIVE_CODE_RE.fullmatch(code or "") is None:
            continue
        observed.append((wid, rec, code))

    counts = {}
    for _wid, _rec, code in observed:
        counts[code] = counts.get(code, 0) + 1
    notices = []
    qualifying = []
    seen = set()
    for wid, rec, code in observed:
        # 五碼碰撞時 @code 不能唯一指人；不畫一則看似可行動、實際無法定址的提醒。
        if counts[code] != 1:
            continue
        persona_bound, brain_bound = _binding_targets(rec, display)
        if persona_bound is None or brain_bound is None:
            raise SeatsUnavailable("@%s 的綁定投影形狀不合" % code)
        pct, source = resolve_ctx(wid, "", hub=hub)
        attention = _bind_attention_notice(
            code, pct, source, persona_bound, brain_bound)
        cand_n = None
        if not persona_bound:
            cand_n = _opening_candidate_count(wid, hub)
        if attention or cand_n is not None:
            if code not in seen:
                seen.add(code)
                qualifying.append(code)
        notices.extend(_per_seat_zbind_notices(code, attention, cand_n))
    n = len(qualifying)
    if n >= 1:
        notices.append({
            "seat": "cabin",
            "kind": "bind-unbound",
            "text": "未綁人格:%d 席" % n,
        })
    return {"schema": BIND_NOTICE_SCHEMA, "notices": notices}


def _per_seat_zbind_notices(code, attention_text, candidate_count):
    """212:逐席「開席建議 / 用量已過 10%」已停;還原刀對準這一支。"""
    return []


def box_text(screen):
    """輸入列內容(最後一個 ❯ 行的字);沒有=None。"""
    last = None
    for line in (screen or "").splitlines():
        m = _PROMPT_RE.match(line.strip())
        if m:
            last = m.group(1).strip()
    return last


DRAFT_AGE_REVEAL_S = 300   # the maintainer 08-18:滯留 5 分鐘即可讀

# ---- 無反應偵測(08-18 a seat 實案:巡查三層全部漏掉)-------------------
#
# qa 停止回應 70 分鐘,而三層巡查(艦長 26 筆、dev4 2 筆、本工具每輪)沒有一層
# 抓到:CTX 有值、框有字、pane 未死——**三項狀態全部「正常」**。艦外據此判它
# 是 human_draft,連按四次 Enter,四次都沒問「為什麼按了沒反應」。
#
# ★ 根因一句話:**所有巡查器都在看「狀態」,沒有一個在看「變化」**。
#   CTX 64% 是狀態,CTX 70 分鐘紋風不動是變化;框有字是狀態,按了 Enter 字
#   還在是變化。停止回應只有看變化才抓得到。
#
# 判準:同一席的 (CTX, 框內容指紋) 兩者**都**沒變且已逾 UNRESPONSIVE_S,
# 判 unresponsive。★ 為什麼要「都」沒變:CTX 不動但框在打字=它在思考(正常);
# 框不動但 CTX 漲=它在跑背景工作(正常)。兩者同時凍住才是沒反應。
class PulseStateUnwritable(RuntimeError):
    """pulse 狀態不可寫=偵測器失效,必須吵不能靜默(dev4⚓ P2)。"""


UNRESPONSIVE_S = 600       # 10 分鐘無任何變化
# ★ P1(dev4⚓ 二方審實測):路徑與鍵都必須跟 instance 走。原本檔在共用
#   ~/.ez2-aib/state/、鍵是裸 @窗號——aihub-9 也有 @2..@5,任一艙跑 scan 就
#   重設對方的 since。實測:共用檔時 13 艦 @6 靜止 40 分鐘回 stalled=0/False,
#   分檔則 2400s/True。**偵測器往「一切正常」靜默失效**,與 appoint.py 同根:
#   狀態路徑沒跟 instance 走。
def instance_of_hub(hub):
    """從 --hub 導出艙號:aihub-13 → "13";aihub(主艙)→ None。

    ★ 二修根因(dev4⚓ 08-18 阻擋級,艦長複驗屬實並收回自己的終審綠):
      一修讓路徑跟 **env** 走,但掃描目標跟 **--hub** 走——**兩個真相源**。
      後果:(a) 乾淨 env 照文件跑 `scan --hub aihub-13`,路徑回共用檔,P1
      原封不動;(b) 反向 `EZ2_AIB_INSTANCE=9` 掃 aihub-13,會把 13 的樣本
      寫進 9 的檔——比原本更糟(污染別艙)。
      修法:**狀態路徑由掃描目標導出**,env 只當 fallback。同一個問題只能有
      一個真相源,而那個源必須是「這次實際在掃誰」。"""
    if not hub:
        return None
    name = str(hub).strip()
    if name.startswith("aihub-"):
        tail = name[len("aihub-"):]
        return tail if tail and all(c.isalnum() or c == "-" for c in tail) else None
    return None


def _pulse_path(hub=None):
    """pulse 狀態檔。順位:--hub 導出 > EZ2_AIB_INSTANCE_HOME > EZ2_AIB_INSTANCE。"""
    root = seats_dir_lib.state_root()[0]          # ED.1 順位單源
    inst = instance_of_hub(hub)
    if inst:
        return os.path.join(root, "instances", inst, "state", "seat-pulse.json")
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
    if not home:
        env_inst = os.environ.get("EZ2_AIB_INSTANCE")
        cand = os.path.join(root, "instances", str(env_inst)) if env_inst else None
        home = cand if (cand and os.path.isdir(cand)) else root
    return os.path.join(home, "state", "seat-pulse.json")


def _load_pulse(path=None, hub=None):
    try:
        with open(path or _pulse_path(hub), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_pulse(data, path=None, hub=None):
    """回 True/False。★ P2(dev4⚓):原本吞 OSError → 狀態檔不可寫時 pulse 恆回
    (0, False)=永遠「一切正常」。**偵測器的失效方向必須是吵,不是靜默放行**。"""
    p = path or _pulse_path(hub)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def pulse_check(window, ctx_pct, box_text, now_s=None, path=None, hub=None):
    """回 (stalled_s, is_unresponsive)。同一席 CTX 與框內容都沒變才累計。

    ★ 不用畫面時間、不用 last_used_epoch:那些是**別人寫的**,席位停止回應時
      仍可能被心跳更新(自報值的老問題)。這裡用巡查自己實際觀察到的變化。"""
    import hashlib
    now = now_s if now_s is not None else int(time.time())
    sig = hashlib.sha256(("%s|%s" % (ctx_pct, box_text or "")).encode()).hexdigest()[:16]
    data = _load_pulse(path, hub)
    prev = data.get(window)
    if not prev or prev.get("sig") != sig:
        data[window] = {"sig": sig, "since": now}
        if not _save_pulse(data, path, hub):
            # 寫不進去=下一輪必然又是「首見」=永遠測不出無反應。
            # fail-loud:讓呼叫端看得到偵測器本身壞了。
            raise PulseStateUnwritable("pulse 狀態寫不進 %s——無反應偵測失效"
                                       % (path or _pulse_path(hub)))
        return 0, False
    stalled = max(0, now - int(prev.get("since", now)))
    return stalled, stalled >= UNRESPONSIVE_S
# ED.1:狀態根可注入。★ 照抄本檔既有的樣式(:134、:280),不自創第二種寫法 ——
#   同一個解析規則在同一個檔裡有兩種寫法,就是下一次分岔的起點。
# ★ 這兩個原本是**純硬編**(全檔 21 個 expanduser 命中裡,只有 3 個沒有 env 逃生門,
#   其中 2 個在這裡)。後果不是「路徑錯」,是**注入到暫存根之後這兩個檔仍然寫進真 HOME**
#   —— 測試看起來乾淨,而真狀態根被動到了。
_AIB_ROOT = seats_dir_lib.state_root()[0]      # ED.1 順位單源(STATE_ROOT > HOME > ~)
_SEEN_PATH = os.path.join(_AIB_ROOT, "state", "draft-seen.json")
_READ_LOG = os.path.join(_AIB_ROOT, "logs", "draft-read.jsonl")


def _fingerprint(window, text):
    import hashlib
    return hashlib.sha256(("%s|%s" % (window, text)).encode()).hexdigest()[:16]


def draft_age_s(window, text, now_s=None, seen_path=None):
    """同一份草稿(窗+內容指紋)首見至今的秒數;首見即登記回 0。

    ★ 用內容指紋不用畫面時間:人改一個字就是新草稿,要重新計時——否則
      「一直在編輯中」會被當成「擱置五分鐘」而被讀。"""
    path = seen_path or _SEEN_PATH
    now = now_s if now_s is not None else int(time.time())
    fp = _fingerprint(window, text)
    try:
        with open(path, encoding="utf-8") as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    first = seen.get(fp)
    if first is None:
        seen[fp] = now
        # 順手回收 24h 前的指紋,免得檔無限長
        seen = {k: v for k, v in seen.items() if now - v < 86400}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(seen, fh)
            os.replace(tmp, path)
        except OSError:
            pass
        return 0
    return max(0, now - first)


def log_draft_read(window, text, age_s, log_path=None):
    """讀取未送出草稿必留痕——放寬隱私邊界要看得見誰何時讀了什麼。"""
    path = log_path or _READ_LOG
    rec = {"at_ms": int(time.time() * 1000), "window": window,
           "age_s": age_s, "len": len(text),
           "fingerprint": _fingerprint(window, text),
           "rule": "human-0818-draft-age-5min"}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def classify_box(screen):
    """對話欄形態 → (kind, action)。判準見檔頭表。"""
    if LOGIN_EXPIRED_MARK in (screen or ""):
        return "login_expired", "needs_human"
    text = box_text(screen)
    if not text:
        return "empty", "none"
    if QUEUED_MARK in (screen or ""):
        # ★ 框有字但已排隊:早期我在這裡重按過 Enter(以為沒送出)
        return "queued", "wait"
    if _DM_HEAD_RE.match(text):
        return "dm_draft", "may_press"
    # 無 dm 標頭:分「AI 自留提示」與「真人手稿」。
    # ★ 首版判準(整句 probe 比對該席 ⏺ 輸出)實測誤判四席:AI 的自留提示是
    #   **它下一步要做的事**,不必逐字出現在它剛才的輸出裡。改用語彙判準:
    #   自留提示幾乎必含工程語彙(commit/終審/覆驗/席位代號/工單號),
    #   而真人手稿是對話式的。命中任一工程語彙=self_note,否則 human_draft。
    #   ★ 仍守「分不清歸 human_draft」:語彙表只收**人不會在對話裡順手打**的
    #   詞形(hash、L 編號、席位 @五碼、rc=、CTX%),不收「終審」這種兩邊都用的字。
    engineering = [
        r"\b[0-9a-f]{7}\b",          # commit hash
        r"\bL\d-\d\d\b",            # 工單號 L5-04/L6-01
        r"@[0-9a-f]{4,8}\b",          # 席位代號
        r"\brc=\d",                   # 真 exit code
        r"CTX \d+%",
        r"\bG1-\d\d\b",
    ]
    for pat in engineering:
        if re.search(pat, text):
            return "self_note", "none"
    return "human_draft", "never_press"


def _shared_conversation(rec, procs):
    """這席的對話另外還被誰活著持有(09-24 同碼雙活:d6a8/@4 與 98fe/@6 開著同一段)。空字串=沒有。"""
    u = (rec or {}).get("session_uuid") or ""
    if not u:
        return ""
    try:
        import seat_liveness
        h = seat_liveness.holders(u, procs=procs)
    except Exception:
        return ""
    me = (rec or {}).get("pid")
    h["registry"] = [r for r in h["registry"] if r.get("pid") != me]
    h["process"] = [p for p in h["process"] if not _pid_under(p["pid"], me, procs)]   # 自己的 CLI 不算別人
    return seat_liveness.describe(h)


def _pid_under(pid, root, procs):
    parent = {r[0]: r[1] for r in procs}
    cur, hops = pid, 0
    while cur and hops < 64:
        if cur == root:
            return True
        cur, hops = parent.get(cur, 0), hops + 1
    return False


def scan(hub):
    seats = []
    binding_display = _binding_display()
    try:
        import seat_liveness
        _procs = seat_liveness.proc_table()          # 讀一次,每席共用
    except Exception:
        _procs = []
    # ★ 讀不到要吵,不要靜默降級(三修):席位目錄不存在=每一席都拿不到 uuid,
    #   全員 ctx_source=screen——那不是「機器值剛好沒有」,是**接錯目錄**。
    #   吵在 stderr(scan 的 stdout 是 JSON/表格,不污染),並照常掃(畫面值仍是
    #   合法後備;吵過的降級才是誠實降級)。
    sd = _seats_dir(hub)
    if not os.path.isdir(sd):
        print("fleet_patrol: 席位目錄不存在:%s(--hub %s 導出)——"
              "全員將降級 screen,請確認艙號/EZ2_AIB_HOME" % (sd, hub),
              file=sys.stderr)
    for wid, name, dead in live_windows(hub):
        if name in ("watch", "lobby"):
            continue
        screen = capture(hub, wid)
        kind, action = classify_box(screen)
        raw = box_text(screen) or ""
        # ★ D9 身份邊界(艦長 08-18 實測抓到,裁決對但工具漏):human_draft 是
        #   **人還沒送出的草稿**,任何消費端(含艦長)都只該知道「那裡有東西」,
        #   不該讀到內容。只回長度;其餘形態回內容(dm 有標頭本就公開、
        #   self_note 是該席自己的話、queued 已送出)。
        age = draft_age_s(wid, raw) if kind == "human_draft" else 0
        if kind != "human_draft":
            shown = raw[:40]
        elif age >= DRAFT_AGE_REVEAL_S:
            # the maintainer 08-18 五分鐘規則:滯留過久即可讀,並留痕
            log_draft_read(wid, raw, age)
            shown = "[%dm 滯留] %s" % (age // 60, raw[:40])
        else:
            shown = "<%d 字未送出草稿 %ds>" % (len(raw), age)
        # ★ pulse_check 刻意**繼續吃畫面值**,不吃 resolve_ctx 的結果(兩者來源不同是
        #   故意的,別順手統一):pulse 的指紋是「巡查自己觀察到的東西有沒有變」,
        #   而 resolve_ctx 會在機器探針拿不到時降級成畫面值——**來源在輪次之間翻轉會讓
        #   指紋跟著變,停擺計數就永遠歸零**,一個真的卡住的席位因此永遠累積不到
        #   UNRESPONSIVE_S。那會把這道無反應偵測靜默關掉,而它正是 qa 事故的補丁。
        # ★ hub= 由二修加(dev4⚓ 阻擋級):狀態路徑必須跟**掃描目標**走,不跟 env
        #   走——兩者不同源時,照文件不帶 env 跑會讓 P1 原封不動。兩條註解都保留:
        #   前者說「值的來源不統一是刻意的」,後者說「路徑的來源必須統一」,不衝突。
        stalled, unresp = pulse_check(wid, ctx_of(screen), raw, hub=hub)
        if unresp and kind in ("human_draft", "queued", "dm_draft"):
            # ★ 無反應蓋過框態:按了沒反應時,再按只是重複同一個無效動作。
            kind, action = "unresponsive", "escalate"
            shown = "[%dm 無反應] %s" % (stalled // 60, shown)
        pct, source = resolve_ctx(wid, screen, hub=hub)
        ctx_scheduler_receipt = _ctx_scheduler_tick(
            wid, hub, int(time.time() * 1000))
        # ★ 加一欄五碼(the maintainer 08-18 17:3x 直令:報表不必再引用角色名)。
        #   **窗名 name 不動**——它是 send-keys 的定址依據,改了會炸;
        #   這裡只是讓消費端有一個不必靠名字的欄位可用。
        #   五碼來源同 seat_address.code_of(單一函式,the maintainer 定案後只改那一處)。
        code = _seat_address_code(wid, hub=hub)
        rec = _seat_record(wid, hub=hub)
        targets = _binding_targets(rec, binding_display) \
            if rec is not None and binding_display is not None else (None, None)
        attention = _bind_attention_notice(code, pct, source, *targets)
        unverified = handoff_unverified_line(code, pct, source)
        if unverified:
            attention = unverified + (("  " + attention) if attention else "")
        shared = _shared_conversation(rec, _procs)
        if shared:
            action = "needs_human" if action not in ("needs_human", "never_press") else action
            attention = ("⚠ 同一段對話另有活席:%s" % shared) + (("  " + attention) if attention else "")
        qrows = []
        if code:
            try:
                import dm_queue as _dq
                qrows = _dq.patrol_payload(code)
            except Exception:
                qrows = []
        seats.append({"window": wid, "name": name, "dead": dead,
                      "stalled_s": stalled,
                      "code": code, "addr": ("@" + code) if code else wid,
                      "ctx_pct": pct, "ctx_source": source, "box": kind,
                      "action": action, "box_text": shown,
                      "box_len": len(raw),
                      "ctx_scheduler_receipt": ctx_scheduler_receipt,
                      "attention": attention,
                      "shared_conversation": shared,
                      "handoff_unverified": bool(unverified),
                      "dm_queue": qrows})
    return seats


def _auto_distill():
    _idn = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "identity"))
    if _idn not in sys.path:
        sys.path.insert(0, _idn)
    import auto_distill_hook
    return auto_distill_hook


def handoff_unverified_line(code, pct, source):
    """唯讀(維護者 10-15 20:1x「巡檢只回報、不送指令」):機器用量 ≥ 表上「要求交接」那格、而本輪交接 verify 沒過 ⇒
    給第一艦長看的一行;Claude 席 Stop 擋滿已放行的加註。畫面值 / 量不到 / verify 讀不到 ⇒ ""(不猜)。"""
    if source != "machine" or not isinstance(pct, int) or isinstance(pct, bool) or not code:
        return ""
    try:
        floor = _ctx_policy().trigger_pct("handoff_now")
        if pct < floor:
            return ""
        ad = _auto_distill()
        if ad.handoff_done(code) is not False:
            return ""
        gave = ad.gate_gave_up(code)
    except Exception:  # noqa: BLE001
        return ""
    return "⚠ 85%% 交接未完成:用量 %d%% ≥ %d%%,本輪 aib handoff verify 沒過%s" % (
        pct, floor, "(Stop 擋滿已放行)" if gave else "")


def seat_gate(seats, purpose="capacity"):
    """開新席閘(the maintainer 直令):半數以上席位 CTX>80% 才准開。

    ★ purpose="succession"(繼任/補位席)**豁免本閘**——艦長 08-18 質疑命中:
      pm 已是三席儲君、45% 就該開 pm2 補位,但依產能閘要等半艦快爆才准開,
      **那時已來不及交接**。閘的本意是擋「稀釋產能的增員」,不是擋傳承。
      豁免仍留痕(exempt=True + reason),不是無聲放行。

    回 (allowed, detail)。**量不到 CTX 的席位算「未超過」**(fail-closed:
    不確定就不准增員,把任務發回原席)。"""
    if purpose == "succession":
        return True, {"allowed": True, "exempt": True, "purpose": purpose,
                      "verdict": "繼任/補位席豁免產能閘(傳承優先於產能)"}
    if SEAT_GATE_CTX_THRESHOLD is None:
        return False, {"allowed": False, "threshold_pct": None, "purpose": purpose,
                       "verdict": "增員門檻已刪(10-15 CTX 單表):不再自動判斷,要不要加開新席由人決定"}
    known = [s for s in seats if not s.get("dead")]
    total = len(known)
    over = [s for s in known if (s.get("ctx_pct") or 0) > SEAT_GATE_CTX_THRESHOLD]
    need = 0 if total == 0 else int(total * SEAT_GATE_FRACTION) + (
        0 if (total * SEAT_GATE_FRACTION).is_integer() else 1)
    allowed = total > 0 and len(over) >= need
    return allowed, {
        "total_seats": total,
        "over_threshold": len(over),
        "threshold_pct": SEAT_GATE_CTX_THRESHOLD,
        "required": need,
        "allowed": allowed,
        "over_seats": [s["name"] for s in over],
        # 這道閘有多少是靠畫面上的字撐起來的——降級要看得見,不能靜默生效
        "ctx_sources": {src: len([s for s in known if s.get("ctx_source") == src])
                        for src in ("machine", "screen", "none")},
        "verdict": ("可開新席" if allowed else
                    "不可開新席——任務發回原席繼續完成"),
    }


# ---------------------------------------------------------------- 刀 J(乙):CI 的紅要有收件人
# ★ 這一段解的不是「看不見」,是**沒有收件人**:下限閘的紅連喊三晚都在 log 裡,
#   而沒有人每天早上去開那份 log。閘寫得再好,紅燈沒有走到人面前就等於沒有響。
#   同族判準=刀一 F1「判準進 tests/ 才會被經過」的巡查版。
#
# ★★ 只看 schedule,不看 push(這一行是結構性的,不是偏好):
#   完整矩陣綁在 schedule 與 tag 上(ED.9),push 只跑一格 ubuntu——**push 的 run
#   裡根本沒有 a CI machine 那一格**。所以「巡查 CI」如果用 `gh run list` 掃最近的 run,
#   會掃到一堆 push run、每次都看不到自架格,然後得出「沒事」。
#   拆聚合要沿**產生那個數字的維度**拆:這裡的維度是**事件類型**,不是分支。
#
# ★★ 收據一定要帶分母(艦外 08-19 02:35 立):只說綠/紅不說「哪個平台、跑完幾套」的
#   收據不算數。本節因此把每一格的 steps 數與秒數都印出來——**steps=0 的格是
#   「從來沒開始」(帳單/派工),那不是測試紅**,把它算進紅裡會得到一個假的分母。
CI_REPO_DEFAULT = "ez2app/ez2-aib"
CI_EVENT_DEFAULT = "schedule"
CI_LOG_GROUP_RE = re.compile(r"##\[group\]tests/")
# ★ 下限閘那句訊息的**兩種寫法都要認**(a seat 08-19 23:28 裁,取代「排程兩端同時改」):
#   讀端先寬容 → 寫端哪天落地都不會有沉默期;反過來(寫端先改)中間必有一段
#   **verdict 沉默**的窗口,而窗口長度取決於有沒有人記得。
#   ★ 這個耦合是我自己造的:巡查認字面,而我同時提案要改那句字面
#     ——**同一個介面的兩端由同一個人分兩刀交付,兩端都在同一顆腦袋裡,所以看起來一致。**
#   ★ 失效方式特別安靜:不命中時 floor_found=None、verdict 保持 None,而 None 是**合法值**
#     (表示這格沒結論),所以沒有任何測試會覺得不對——它不是壞掉,是變成沉默。
#   ★ 新句多給一個 discovered(產出端自己數的檔數);這裡**只解析不使用**——
#     拿它當分母是艦長 03:26 裁的 (乙),那要動 workflows,不在本刀。
CI_FLOOR_PATTERNS = (
    re.compile(r"只找到 (?P<ran>\d+) 套,低於下限 (?P<floor>\d+)"),
    # ★ 那對括號要跳脫:第一版寫成 `圈(?:(發現 …檔))?`,而它們是 ASCII 括號,
    #   於是變成**正則的群組**而不是要比對的字元——新句因此完全不命中,
    #   而「寬容」的碼面看起來已經寬容了(同 short 三態那次:碼面看起來已經分辨了)。
    #   我是把四種輸入(舊句/新句含 discovered/新句不含/無關行)全餵一次才看到的。
    re.compile(r"只跑了 (?P<ran>\d+) 圈(?:\(發現 (?P<disc>\d+) 檔\))?,低於下限 (?P<floor>\d+)"),
)


def suites_at_sha(sha, repo_dir=None, timeout_s=20):
    """數**那顆 sha 當下**的 `tests/test-*.py` 檔數;數不到回 None。

    ★ a seat 08-19 03:24 二審發現(艦長 03:10「狀態會動」生效後抓到的第一個實作):
      第一版拿**巡查當下的本地工作樹**檔數,去比**過去某個 sha 的 CI run** 的 group 數
      ——兩個不同狀態。實測 4d284e8 有 124 檔、bfd0bc0 有 126,差兩檔。
      主要方向是假 short(便宜的一端),但反向會出人命:repo 若刪了檔使本地數變小,
      `groups >= total` 會讓**真的少跑**判成過——那是假 OK,貴的那一端。
    ★ 所以數不到時回 None,呼叫端一律翻成「這一項沒驗」,**不拿本地數字頂替**。
      (頂替一次就會安靜地過,而且過的那次剛好是狀態差最大的那次。)"""
    if not sha:
        return None
    try:
        p = subprocess.run(["git", "-C", repo_dir or ".", "ls-tree", "-r",
                            "--name-only", sha, "tests/"],
                           capture_output=True, text=True, timeout=timeout_s)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    names = [n for n in (p.stdout or "").splitlines()
             if n.startswith("tests/test-") and n.endswith(".py")]
    return len(names) or None


def _gh_json(args, timeout_s=45):
    """跑 gh 並解析 JSON。回 (data, err);**任何一步不確定都回 err,不回空資料**。

    ★ fail-closed 的方向在這裡很容易寫反:gh 不在、未登入、網路不通時若回
      「沒有紅」,巡查就會在最需要它的時候說一切正常。所以錯誤一律往上帶,
      由呼叫端翻成 unknown(rc=2),**永遠不翻成綠**。"""
    try:
        p = subprocess.run(["gh"] + list(args), capture_output=True, text=True,
                           timeout=timeout_s)
    except FileNotFoundError:
        return None, "gh 不在 PATH(巡查無法判定,不等於沒事)"
    except subprocess.TimeoutExpired:
        return None, "gh 逾時 %ds(判不出來)" % timeout_s
    if p.returncode != 0:
        return None, "gh rc=%d:%s" % (p.returncode, (p.stderr or "").strip()[:200])
    try:
        return json.loads(p.stdout or ""), None
    except ValueError:
        return None, "gh 輸出不是 JSON(前 120 字:%r)" % (p.stdout or "")[:120]


def _job_cell(job):
    """把一個 job 翻成一格。**started 用 steps 數判,不用 conclusion 判**——
    帳單擋住時 job 照樣是 conclusion=failure,但它一個 step 都沒有跑過。"""
    steps = job.get("steps") or []
    started = len(steps) > 0
    secs = None
    a, b = job.get("startedAt"), job.get("completedAt")
    if a and b:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            secs = int((time.mktime(time.strptime(b, fmt))
                        - time.mktime(time.strptime(a, fmt))))
        except (ValueError, OverflowError):
            secs = None
    failed_steps = [st.get("name") for st in steps
                    if st.get("conclusion") not in ("success", "skipped", None)]
    return {"name": job.get("name"), "conclusion": job.get("conclusion"),
            "status": job.get("status"), "steps": len(steps), "seconds": secs,
            "started": started, "failed_steps": failed_steps,
            "id": job.get("databaseId")}


def ci_run_report(run, jobs):
    """單一 run 的收據(純函式,好測)。**分母在這裡產生**:幾格真的執行過。"""
    cells = [_job_cell(j) for j in jobs]
    # ★ plan 不是測試格:它只決定矩陣。把它算進「執行過的格數」會讓一個
    #   「只有自架格真的跑過」的 run 看起來有對照組——分母灌水最容易發生在
    #   「把不同種類的東西數在同一個計數裡」。
    tests_cells = [c for c in cells if (c["name"] or "").strip() != "plan"]
    executed = [c for c in tests_cells if c["started"]]
    never = [c for c in tests_cells if not c["started"]]
    reds = [c for c in executed if c["conclusion"] not in ("success", "skipped")]
    return {
        "run_id": run.get("databaseId"), "sha": (run.get("headSha") or "")[:12],
        "created_at": run.get("createdAt"), "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "cells_total": len(tests_cells), "cells_executed": len(executed),
        "cells_never_started": len(never),
        # ★ 沒有對照組要講出來:只有一格真的跑過時,「這一格紅」無法區分
        #   「碼壞了」與「那台機器壞了」——那正是自架格當初刻意只放 sweep 的理由。
        "has_control_group": len(executed) > 1,
        "reds": [c["name"] for c in reds],
        # ★ cells 只放**測試格**;plan 另外放。第一版把 plan 留在 cells 裡,
        #   結果數 group 時 plan 被當成「跑了 0 套的格」判成缺套——
        #   同一個清單被兩種用途共用,錯的那一邊不會叫。
        "cells": tests_cells,
        "plan": next((c for c in cells if (c["name"] or "").strip() == "plan"), None),
    }


def ci_log_facts(text, total_suites=None):
    """從 job log 取兩個機器事實:**跑了幾套**、下限閘喊了什麼。

    ★ 這裡是「診斷指錯方向」的修正點:下限閘的訊息寫「測試被刪或改名」,
      但同一份 log 裡數得出 32 個 `##[group]tests/`——**它不是少發現,是跑到一半停**。
      兩者的查法完全相反(一個查 glob/檔名,一個查什麼終止了迴圈),
      所以巡查在這裡直接給出正確分類,不等 workflow 的訊息被改。"""
    groups = len(CI_LOG_GROUP_RE.findall(text or ""))
    m = next((x for x in (rx.search(text or "") for rx in CI_FLOOR_PATTERNS) if x), None)
    floor_found = int(m.group("ran")) if m else None
    floor_min = int(m.group("floor")) if m else None
    # 產出端自報的檔數(只有新句有);解析下來留痕,本刀不拿它當分母。
    floor_discovered = int(m.group("disc")) if m and m.groupdict().get("disc") else None
    verdict = None
    if floor_found is not None:
        if groups and floor_found == groups and (total_suites is None
                                                 or groups < total_suites):
            verdict = "aborted_early"      # 中止:計數等於實際跑過的圈數
        elif groups == 0:
            verdict = "discovery_short"    # 發現不足:一圈都沒跑
    # ★ short 與 verdict 是兩件事:verdict 講「這次為什麼少」(中止 vs 發現不足),
    #   short 只講「少了」——**它不看 conclusion**,因為一個綠格也可能是提早停下來的。
    # ★★ short 是**三態**:True 少了 / False 沒少 / **None 沒驗**(拿不到那顆 sha 的分母)。
    #   第一版寫成 bool,於是「沒驗」與「沒少」壓成同一個 False——資訊沒丟(分母另讀得到),
    #   但**只讀 short 的第二個消費者會把「沒驗」讀成「過」**(a seat 08-19 03:33 抓到)。
    #   這正是本刀在別處挑過的那條長在自己身上:新狀態被靜默歸進最寬鬆的那一格。
    #   ★ 讀 short 必須同時讀 total_suites_at_sha —— 但這句是輔助不是防線,
    #     防線是三態本身(文件擋不住第二個消費者,今晚已數過三個「規格寫了碼沒照做」的實例)。
    short = None if not total_suites else groups < total_suites
    return {"test_groups": groups, "floor_found": floor_found,
            "floor_min": floor_min, "floor_discovered": floor_discovered,
            "verdict": verdict, "short": short,
            "total_suites": total_suites}


def is_short(facts):
    """「這一格少跑了嗎」的**單一入口**。★ 一定要 `is True`,不能用真值判斷。

    ★★ a seat 08-19 03:35 抓到的陷阱(而我已經先推了半套):把 `short` 改成
      三態、卻讓呼叫點維持 `if facts.get("short"):`,**行為與兩態完全一樣**——
      Python 的 `if x:` 對 `None` 與 `False` 給同一個答案。
      **在函式裡拆開的東西,語言在呼叫點又把它合起來,而那個壓縮不出現在任何一行你寫的碼裡。**
      比不改更危險的是:碼面看起來已經分辨了,於是沒有人會再回來看那兩行。
    ★ 所以「沒驗」在這裡必須自己有一條路:它不進缺套名單(不是紅),
      也不得被當成「沒少跑」(不是綠)——收據另有一行說它沒驗。"""
    return bool(facts) and facts.get("short") is True


def ci_patrol(event=CI_EVENT_DEFAULT, limit=3, repo=CI_REPO_DEFAULT,
              with_log=False, total_suites=None, gh=None, repo_dir=None,
              suites_fn=None):
    """CI 巡查:最近 N 次**排程** run 的分格收據。回 {ok, unknown, runs, why}。

    rc 由 main() 給:0=最新一次全綠且有分母 / 1=有紅(要人收)/ 2=判不出來。

    ★ **`unknown=true` 不足以決定要不要重試,必須讀 `why`**(a seat 08-19 23:33 實測):
      限流(`gh rc=1:…429…`)與「查無排程 run」在 `rc` 與 `unknown` 上**同值**,
      只有 `why` 分得出來——前者該重試,後者不該。兩者都 fail-closed 所以不影響安全,
      但**只讀 `unknown` 的消費者分不出處置**。
      ★ 這與 short 被 `if x:` 壓回兩態同型,差別是這次壓縮發生在
        **消費者選擇讀哪一欄**,不是語言替他壓的。"""
    gh = gh or _gh_json
    runs, err = gh(["run", "list", "-R", repo, "-e", event, "--limit", str(limit),
                    "--json", "databaseId,conclusion,createdAt,headSha,event"])
    if err:
        return {"ok": False, "unknown": True, "why": err, "runs": []}
    out = []
    for run in runs or []:
        detail, err2 = gh(["run", "view", str(run.get("databaseId")), "-R", repo,
                           "--json", "jobs"])
        if err2:
            out.append({"run_id": run.get("databaseId"), "unknown": True, "why": err2})
            continue
        rep = ci_run_report(run, (detail or {}).get("jobs") or [])
        # ★ 分母要取**那個 run 的那顆 sha**,不是巡查當下的工作樹(見 suites_at_sha)。
        #   顯式傳進來的 total_suites 只在測試注入時使用。
        run_total = total_suites
        if run_total is None:
            run_total = (suites_fn or suites_at_sha)(run.get("headSha"), repo_dir)
        rep["total_suites_at_sha"] = run_total
        if with_log:
            rep["log"] = []
            # ★★ 常設判準(a seat 08-19 升常設,b543f3a):**「126 套全跑到」要每輪都驗**,
            #   不是修完就結案的驗收項。「迴圈提早結束」是**症狀類別不是單一 bug**——
            #   已知兩個成因、指紋不同且不互相解釋(08-11 `set -e` 中止=開了 group 中間零輸出;
            #   08-18 stdin 餓死=heartbeat 有完整輸出 Ran 6 才停)。**第三次不會通知我們。**
            #   所以這裡對**每一個執行過的格**都數 group,綠格也數:
            #   ★ 綠也可能是因為它更早就停了。
            for cell in rep["cells"]:
                if cell["started"]:
                    text, err3 = _gh_text(["api", "repos/%s/actions/jobs/%s/logs"
                                           % (repo, cell["id"])])
                    rep["log"].append({"cell": cell["name"],
                                       "conclusion": cell["conclusion"],
                                       "facts": ci_log_facts(text or "", run_total)
                                       if not err3 else None,
                                       "why": err3})
            rep["suites_shortfall"] = [e["cell"] for e in rep["log"]
                                       if is_short(e.get("facts"))]
        out.append(rep)
    latest = out[0] if out else None
    unknown = latest is None or latest.get("unknown")
    # ★ 全綠還不夠:任何執行過的格如果沒跑滿全部套數,這一輪照樣不算過
    #   (常設判準,見上)。--with-log 沒開時 suites_shortfall 不存在,
    #   此時 ok 只代表「沒有紅」,**收據會自己說它沒驗這一項**。
    ok = (bool(latest) and not unknown and not latest.get("reds")
          and not (latest.get("suites_shortfall") or []))
    return {"ok": ok, "unknown": unknown, "why": None if not unknown else "查無排程 run",
            "event": event, "repo": repo, "runs": out}


def _gh_text(args, timeout_s=90):
    """同 _gh_json 但回原文(log 用)。"""
    try:
        p = subprocess.run(["gh"] + list(args), capture_output=True, text=True,
                           timeout=timeout_s)
    except FileNotFoundError:
        return None, "gh 不在 PATH"
    except subprocess.TimeoutExpired:
        return None, "gh 逾時 %ds" % timeout_s
    if p.returncode != 0:
        return None, "gh rc=%d" % p.returncode
    return p.stdout, None


def ci_receipt_lines(report):
    """人看的收據。★ 每一行都帶分母;沒有分母的行不准出現在這裡。"""
    lines = []
    if report.get("unknown"):
        lines.append("[CI 巡查] **判不出來**:%s —— 這不是綠燈"
                     % (report.get("why") or "未知"))
        return lines
    runs = report.get("runs") or []
    reds = [r for r in runs if r.get("reds")]
    lines.append("[CI 巡查] %s 最近 %d 次:紅 %d 次"
                 % (report.get("event"), len(runs), len(reds)))
    for r in runs:
        if r.get("unknown"):
            lines.append("  run %s:判不出來(%s)" % (r.get("run_id"), r.get("why")))
            continue
        lines.append("  run %s @%s %s → %s(%d/%d 格真的執行過%s)"
                     % (r["run_id"], r["sha"], (r["created_at"] or "")[:16],
                        r["conclusion"], r["cells_executed"], r["cells_total"],
                        "" if r["has_control_group"] else ",**無對照組**"))
        for c in r["cells"]:
            if not c["started"]:
                lines.append("    %-22s 從未開始(steps=0,%ss)——不是測試紅"
                             % (c["name"], c["seconds"]))
            elif c["conclusion"] not in ("success", "skipped"):
                lines.append("    %-22s 紅(steps=%d,%ss,失敗步=%s)"
                             % (c["name"], c["steps"], c["seconds"],
                                ";".join(c["failed_steps"]) or "?"))
        if "suites_shortfall" not in r:
            lines.append("    (未開 --with-log:**沒驗「全部套數都跑到」這一項**)")
        elif not r.get("total_suites_at_sha"):
            # ★ falsy 一律當沒驗:0 也是沒驗,而 0 不是 None——只判 `is None` 的話,
            #   分母為 0 的那次會**兩邊都不說**(既不進缺套名單,也不印沒驗)。
            lines.append("    (取不到 @%s 那顆 sha 的測試檔數:**沒驗「全部套數都跑到」這一項**"
                         "——不拿本地當下的檔數頂替)" % r["sha"])
        for entry in r.get("log") or []:
            facts = entry.get("facts")
            if not facts:
                continue
            if is_short(facts) and entry.get("conclusion") == "success":
                lines.append("    ★ %s:這一格是**綠的**,但 log 裡只有 %d 個測試 group"
                             "(全樹 %s 套)——綠也可能是因為它更早就停了"
                             % (entry["cell"], facts["test_groups"],
                                facts["total_suites"]))
            if facts["verdict"] == "aborted_early":
                lines.append("    ★ %s:log 裡只有 %d 個測試 group,而下限閘說"
                             "「只找到 %d 套」——**數字一致=跑到一半中止**,"
                             "不是測試被刪或改名(閘的訊息本身指錯方向)"
                             % (entry["cell"], facts["test_groups"],
                                facts["floor_found"]))
            elif facts["verdict"] == "discovery_short":
                lines.append("    ★ %s:一個測試 group 都沒有=發現不足,查 glob/檔名"
                             % entry["cell"])
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fleet_patrol")
    ap.add_argument("command", choices=["scan", "seat-gate", "ci", "bind-notices"])
    ap.add_argument("--hub", default=HUB_DEFAULT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--purpose", choices=["capacity", "succession"],
                    default="capacity",
                    help="succession=繼任/補位席,豁免產能閘(留痕)")
    ap.add_argument("--strict", action="store_true",
                    help="scan:有需人處置項(login_expired/human_draft)回 rc3")
    ap.add_argument("--limit", type=int, default=3, help="ci:看最近幾次 run")
    ap.add_argument("--repo", default=CI_REPO_DEFAULT)
    ap.add_argument("--event", default=CI_EVENT_DEFAULT,
                    help="ci:預設只看 schedule——push 的 run 沒有自架那一格")
    ap.add_argument("--with-log", action="store_true",
                    help="ci:抓紅格的 log,分辨「中止」與「發現不足」(慢)")
    args = ap.parse_args(argv)

    if args.command == "ci":
        # ★ CI 巡查不需要 tmux:紅在 GitHub 上,不在艙裡。放在 scan() 之前,
        #   否則沒開艙的機器(例如排程/CI 自己)就永遠拿不到這份收據。
        # ★ 分母由每個 run 自己的 sha 決定(suites_at_sha),這裡只給 repo 目錄。
        report = ci_patrol(event=args.event, limit=args.limit, repo=args.repo,
                           with_log=args.with_log, repo_dir=os.getcwd())
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for line in ci_receipt_lines(report):
                print(line)
        if report.get("unknown"):
            return 2                     # 判不出來 ≠ 綠
        return 0 if report.get("ok") else 1

    if args.command == "bind-notices":
        try:
            snapshot = bind_notices(args.hub)
        except SeatsUnavailable as exc:
            print("fleet_patrol: %s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
        else:
            for notice in snapshot["notices"]:
                print(notice["text"])
        return 0

    if args.command == "scan" and not args.json:
        # G11(@abb89 10-15 22:56):行程數 / 上限與最多的前 3 名指令放最前面 ——
        #   行程表快滿時 tmux 常連不上、下面就只剩「無可巡席位」,這兩行要在那之前印出來。
        #   --json 的席位清單形狀不動(別的程式在讀);機器要讀行程數走 proc_count.py --json。
        import proc_count
        for line in proc_count.report_lines(proc_count.check()):
            print(line)
    seats = scan(args.hub)
    if not seats:
        print("fleet_patrol: 艙 %s 無可巡席位(tmux 不通或空艙)" % args.hub,
              file=sys.stderr)
        return 2

    if args.command == "seat-gate":
        allowed, detail = seat_gate(seats, purpose=args.purpose)
        if args.json:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
        elif detail.get("exempt") or detail.get("threshold_pct") is None:
            print(detail["verdict"])
        else:
            print("%s(%d/%d 席 CTX>%d%%,需 %d)" %
                  (detail["verdict"], detail["over_threshold"],
                   detail["total_seats"], detail["threshold_pct"],
                   detail["required"]))
        return 0 if allowed else 3

    if args.json:
        print(json.dumps(seats, ensure_ascii=False, indent=2))
    else:
        for s in seats:
            print("%-4s %-14s ctx=%-5s %-13s %s%s" % (
                s["window"], s["name"],
                (("%d%%" % s["ctx_pct"]) + ("~" if s.get("ctx_source") == "screen" else ""))
                if s["ctx_pct"] is not None else "?",
                s["box"], s["box_text"],
                ("  " + s["attention"]) if s.get("attention") else ""))
    if args.strict and any(s["action"] in ("needs_human", "never_press")
                           for s in seats):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
