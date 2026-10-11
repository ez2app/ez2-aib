#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""重開自動續綁(L2-04 / 總綱 §二-D「重開自動續綁」)。

    python3 src/identity/seat_rebind.py --window @8 [--seats-dir DIR] [--state-root DIR]
                                        [--cards F] [--contract SPEC] [--dry-run]
    (由 `aib` 開席尾段在 seat-detect 之後呼叫;AIB_NO_REBIND=1 可關;失敗不擋開席)

## 在解決什麼

agent_uid = session uuid 末五碼(the maintainer 08-15 定),而 uuid 每個 session 一個:同一個席位
(同 tmux 窗、同 seat_id)重開,就是**一個新的 agent**,人格綁定歸零——右欄從「專案經理」
變回「[未綁]」,人要再點一次。這支把它接回來:

  席位檔 w<N>.json(seat_id 同窗重開不變) → identity/seat-continuity/<seat_id>.json
  (L2-02 執行器每次成功綁定都寫:上一次綁的 uid / sha / brain / 舊 uuid)
  → 若這席「上一次綁了人格」且「現在的 uuid 是新的」→ 對新 agent 跑同一支執行器
    bind_seat(card=舊 uid, pin_sha=舊 sha, brain=舊 brain) → 自動重投影,右欄不歸零。

## 這不是 rebind

`role_model.request_rebind/approve_rebind` 管的是「**同一個** session 改綁給另一個身份」
(要真人 receipt)。這裡是新 session、新 agent、第一次綁,沒有任何舊綁定被改寫;
舊 agent 的記錄與帳本原樣(bind-seats.py:20-25 明寫:想跨 session 同一 agent 要改 uid
來源,不是改工具)。

## 紅線

- **只續不猜**:延續檔沒人格/席位檔沒 uuid → 什麼都不做,rc 0 說明原因。
  **沒有延續檔**(這席從沒綁過)自 BA 起改走自動綁分支:唯一命中且滿分才綁,
  其餘一律只建議(`action=suggest`),仍是零寫入。
- **有延續檔就不走自動綁**:人上一次的決定優先,機器的候選碰都不碰。
- **釘舊 sha 不釘新值**:席位關著的期間卡升了版,重開後要亮「卡已更新」,不是被續綁悄悄
  蓋成新版(bind_seat pin_sha)。
- **同 uuid 不重做**:延續檔記的 uuid 就是現在的 → 已綁過(或人剛綁),不入帳。
- **卡不在登記表/契約讀不到/執行器拒** → rc 1 + 說明,`aib` 端 `|| true` 不擋開席;
  帳本零寫入。
- 署名 `aib@<seat_id>/reopen`、kind system(是開席鏈在做,不是人點的)。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC / "identity"))

import bind_one  # noqa: E402

WINDOW_RE = re.compile(r"@?(\d{1,6})")

# BA(the maintainer 2026-09-02 23:1x 裁,推翻 08-22「預設建議、不自動綁」):
# 候選**唯一命中且滿分**時開席自動綁,來源記 auto(可拆);不唯一只建議;首綁仍要人。
# ★ 這裡刻意**不動** `persona_candidates.AUTO_BIND`:那個常數釘的是「候選器自己不綁」,
#   候選器仍是純讀端(D17 oracle §0-4 的不變式在講的就是它)。自動綁的決定與效果住在
#   **開席執行器**,也就是本檔——本檔今天就已經在開席鏈上自動呼叫 bind_one.bind_seat
#   (L2-04 續綁,actor kind=system),那條路是既有裁定開的,不是本件新開的。
AUTOBIND_SOURCE = "auto"
# 索引來源:現算,不讀磁碟(理由見 compute_index 的 docstring)。
INDEX_SOURCE_COMPUTED = "computed_now"
AUTOBIND_ACTOR_SUFFIX = "autobind"
# G2:第一艦長(冠位 👑)開席指名綁 a persona card,不走唯一命中。
DIRECTED_PERSONA_UID = "71c97a"

# 開席選單選了什麼(aib 背景段傳 AIB_OPEN_MENU_CHOICE;the maintainer 09-26 R4「只有第一艦長自動綁定該用戶的主人格,
# 其他的不載入就不載入」)。量到的洞(a seat 09-26 01:3x 沙盒):選不載入/舊席沒綁/帶人格失敗時,
# 延續檔把同一窗上一位的卡綁回新席,而且沒有提示。
#   None/"none"      選單沒跳或舊呼叫法 ⇒ 行為不變(AI 代開、右欄未確認時照舊續綁)
#   "fork"           載入舊席位 ⇒ 綁定歸 seat_load.carry_binding;這裡不續綁、不拿上一位的卡頂替
#   不載入四種       非艦長席不續綁、不自動綁
#   "1"/"2"          有載入開席單 ⇒ 照舊(R3 的 30 秒詢問另件接)
MENU_CHOICES = ("1", "2", "3", "timeout", "fork", "blank", "noviewer", "none")
MENU_SKIP_LOAD = frozenset(("3", "timeout", "noviewer", "blank"))
DIRECTED_SOURCE = "directed"
DIRECTED_ACTOR_SUFFIX = "directed"
CROWN_ICON = "\U0001F451"  # 👑 captains-ctl.sh:452 寫進 @aib_captain_icon

# 不綁的理由**碼**(白話跟在冒號後面)。為什麼要有碼:判準若綁在白話字面上,
# 改一個字就逃過去;而「四道條件各回一句不同的話」這件事本身要有格在讀,
# 否則其中任何一道都可以恆真而不被發現——恆真的守衛與沒有守衛在版面上逐字相同。
REASON_BAD_DOC = "bad_document"
REASON_REFUSED = "refused"
REASON_ZERO_CANDIDATES = "zero_candidates"
REASON_NOT_UNIQUE = "not_unique"
REASON_NEEDS_HUMAN = "needs_human"
REASON_NOT_FULL_SCORE = "not_full_score"
REASON_NOT_INDEX_HIT = "not_index_hit"
REASON_INDEX_UNUSABLE = "index_unusable"
REASON_BIND = "unique_full_score_index_hit"
REASON_DIRECTED_CARD_MISSING = "directed_card_missing"


def reason_code(why):
    """`why` → 理由碼(冒號前那一段);認不得回 None,不猜。"""
    if not isinstance(why, str) or ":" not in why:
        return None
    return why.split(":", 1)[0]


def format_suggest_line(out):
    """action=suggest 時給人看的一行。成功續綁/自動綁不得呼叫它來吵。

    開席器把 JSON stdout 丟了,這一行走 stderr,人才看得到「不唯一、請人挑」。
    """
    if not isinstance(out, dict) or out.get("action") != "suggest":
        return None
    tail = (out.get("why") or "").split(";")[-1].strip() or (out.get("why") or "")
    names = []
    for cand in ((out.get("suggestion") or {}).get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        uid = cand.get("persona_uid") or ""
        disp = cand.get("display_name") or uid or "?"
        names.append("%s(%s)" % (disp, uid) if uid else str(disp))
    if names:
        return "開席未自動綁人格:%s。請挑: %s" % (tail, " / ".join(names))
    if tail:
        return "開席未自動綁人格:%s" % tail
    return "開席未自動綁人格:請人挑"


def autobind_decision(doc, full_score):
    """(候選文件, 滿分值) → (要綁的那個候選 or None, 逐字理由)。**純函數:零 I/O、零寫入。**

    四道條件全部成立才回候選;任何一道不成立都回 None 並說出是哪一道
    ——「不綁」有四種理由,把它們折成同一句話,日後就分不出機器看到了什麼。
    """
    if not isinstance(doc, dict):
        return None, "%s:候選文件形狀不合,不綁" % REASON_BAD_DOC
    if doc.get("refused"):
        return None, "%s:候選被拒(%s),不綁" % (REASON_REFUSED, doc.get("refused"))
    cands = doc.get("candidates") or []
    if not cands:
        return None, "%s:零候選,不動(首綁要人)" % REASON_ZERO_CANDIDATES
    if len(cands) != 1:
        return None, "%s:候選 %d 個(不唯一),只建議不綁" % (REASON_NOT_UNIQUE, len(cands))
    cand = cands[0]
    if cand.get("needs_human") is not False:
        return None, "%s:唯一候選仍要人裁(needs_human=%r),不綁" % (REASON_NEEDS_HUMAN, cand.get("needs_human"))
    if cand.get("score") != full_score:
        return None, "%s:唯一候選不是滿分(score=%r,滿分=%r),不綁" % (REASON_NOT_FULL_SCORE, cand.get("score"), full_score)
    if "index_hit" not in (cand.get("reasons") or []):
        return None, "%s:唯一滿分但理由不是 index_hit(%r),不綁" % (REASON_NOT_INDEX_HIT, cand.get("reasons"))
    return cand, "%s:唯一命中且滿分,卡=%s" % (REASON_BIND, cand.get("persona_uid"))


def compute_index(state_root, pc, cards_path=None):
    """開席當下**現算**索引。回 (索引文件, 不可用的理由 or None)。**零寫入。**

    ## 為什麼不讀磁碟上那份 `persona-index.json`

    因為在真實狀態下**它不存在,而且結構上生不出來**。`persona_index.build` 末段是
    `if write and status == "complete"`,而 `_status_for` 要求 `errors` / `conflicts` /
    `unkeyed` 三者**皆空**才算 complete。2026-09-09 實測本機真實狀態根:

        seats=138 indexed=7 conflicts=1 unbound=13 unkeyed=45  → status=incomplete → 不落檔

    那 45 筆 unkeyed 全是 `instances/*/seats` 底下沒有 `cwd` 的歷史席(理由碼
    `seat_has_no_cwd`),沒有任何流程會去清它們 ⇒ **沒有常態輸入能讓它 complete**。
    ★ 於是 BA 的自動綁自上線起**一次都沒有開過火**,而它的外表是 `action=suggest`
      —— 與「機器看過了,認為這一席該讓人決定」**逐字相同**。這是本檔改法的全部理由。

    ## 現算換掉了哪一道守衛,以及取而代之的是什麼

    舊的 `index_is_fresh`(已刪)守的是「磁碟那份與 cards/bindings 不同步」。
    現算之後**那個狀態沒有實例**:輸入就是此刻的 cards / bindings / 席位檔。
    ★ 取而代之的不變式是**更強的一條**:磁碟上那份索引(不管多舊、說什麼)
      **不得影響這個決定**。它砍得到(種一份說謊的索引檔,決定不能變),
      而舊的那道守不住這件事——舊碼正是**只**看磁碟那份。

    ## 仍然 fail-closed 的那一格

    `status == "unknown"`(硬錯誤:席位檔讀不動之類)⇒ 母體本身不可信,
    此時「唯一命中」可能只是因為**沒看到**另一筆 ⇒ 一律不自動綁。
    判不出來時回的值,不得等於「可以放心綁」的那個值。

    成本實測(138 支席位檔,本機):冷 26.9ms、暖 6.6–7.3ms;開席鏈本來就是秒級。
    """
    pi = pc.pi
    doc, _, _ = pi.build(str(state_root), cards_path=cards_path, write=False)
    status = doc.get("status")
    if status not in ("complete", "incomplete"):
        return doc, "%s:索引現算回 status=%r(母體不可信),不自動綁" % (
            REASON_INDEX_UNUSABLE, status)
    return doc, None


def suggest_now(state_root, pc, project, role, seat_id, cards_path=None):
    """現算索引 → 候選文件。回 (候選文件, 不可用理由 or None)。**零寫入。**

    ★ 走 `pc.suggest()` 這支**純函數**而不是 `pc.run()`:run 會自己去讀磁碟索引,
      那正是這次要繞開的東西。純函數吃的是我們剛算出來的文件,兩者的候選規則同一份。
    """
    root = Path(state_root)
    cards_p = Path(cards_path) if cards_path else root.joinpath(*pc.pi.CARDS_REL)
    index_doc, bad = compute_index(state_root, pc, cards_path=cards_path)
    if bad:
        return None, bad
    cards = pc.pi.load_cards(cards_p)
    codes = pc.load_card_codes(cards_p)
    return pc.suggest(index_doc, cards, codes, project, role, seat=seat_id), None


def directed_first_captain_bind(state_root, seat_id, rec, cards_path=None,
                                contract_spec=None, dry_run=False, now_ms=None,
                                ledger_path=None):
    """冠位席首綁:指名 71c97a,不走候選。卡不在登記表 → 具名拒絕,不退回候選。"""
    out = {"source": DIRECTED_SOURCE, "persona_uid": DIRECTED_PERSONA_UID}
    cards_p = Path(cards_path) if cards_path else bind_one.default_cards_path(state_root)
    try:
        cards = bind_one.load_card_registry(str(cards_p))
        bind_one.resolve_card_ref(cards, DIRECTED_PERSONA_UID)
    except (bind_one.BindError, bind_one.ProjectionError, OSError, ValueError) as err:
        out.update(action="refused",
                   why="%s:指名卡 %s 不在 cards.json(%s);不退回候選"
                   % (REASON_DIRECTED_CARD_MISSING, DIRECTED_PERSONA_UID, err))
        return 1, out
    actor = {"ai_id": "aib@%s/%s" % (seat_id, DIRECTED_ACTOR_SUFFIX),
             "sig": "aib@%s/%s" % (seat_id, DIRECTED_ACTOR_SUFFIX),
             "kind": "system"}
    try:
        res = bind_one.bind_seat(
            state_root, seat_id, DIRECTED_PERSONA_UID, actor,
            rebind_persona=False, cards_path=cards_path,
            contract_spec=contract_spec, dry_run=dry_run,
            do_reproject=True, ledger_path=ledger_path, now_ms=now_ms,
            bind_source=DIRECTED_SOURCE)
    except bind_one.BindError as err:
        out.update(action="refused", why=str(err))
        return 1, out
    out.update(action="directed" if not dry_run else "directed_plan",
               why="冠位指名綁 %s" % DIRECTED_PERSONA_UID, result=res)
    return 0, out


def autobind_first_time(state_root, seat_id, rec, cards_path=None, contract_spec=None,
                        dry_run=False, now_ms=None, ledger_path=None):
    """這席**從沒綁過**時,依候選器決定要不要自動綁。回 (rc, dict)。

    ★ 候選器是延遲載入(函式內 import):它會拉進索引與投影兩層,那兩層壞掉不該讓
      既有的重開續綁路徑一起死——本函式的任何失敗都收斂成 action=none、rc 0、不擋開席。
    """
    out = {}
    project = rec.get("cwd")
    role = rec.get("role")
    if not project or not role:
        out.update(action="none", why="席位檔缺 cwd 或 role,無候選(首綁要人)")
        return 0, out
    try:
        import persona_candidates as pc          # noqa: PLC0415 —— 理由見 docstring
    except Exception as err:                     # noqa: BLE001 —— 不擋開席
        out.update(action="none", why="候選器載不進來(%s);不猜" % err)
        return 0, out
    out["index_source"] = INDEX_SOURCE_COMPUTED
    try:
        doc, bad = suggest_now(state_root, pc, project, role, seat_id,
                               cards_path=cards_path)
    except Exception as err:                     # noqa: BLE001 —— 算不出來都算「沒有候選」
        out.update(action="none", why="候選現算不出來(%s);不猜" % err)
        return 0, out
    if bad:
        # 母體不可信(status=unknown):此時的「唯一」可能只是因為沒看到另一筆。
        out.update(action="suggest", why=bad)
        return 0, out
    cand, why = autobind_decision(doc, pc.SCORE_INDEX_HIT)
    out["candidates"] = len(doc.get("candidates") or [])
    out["suggestion"] = doc
    if cand is None:
        out.update(action="suggest", why=why)
        return 0, out
    actor = {"ai_id": "aib@%s/%s" % (seat_id, AUTOBIND_ACTOR_SUFFIX),
             "sig": "aib@%s/%s" % (seat_id, AUTOBIND_ACTOR_SUFFIX), "kind": "system"}
    try:
        res = bind_one.bind_seat(
            state_root, seat_id, cand["persona_uid"], actor,
            brain=cand.get("route_key"), rebind_persona=False,
            cards_path=cards_path, contract_spec=contract_spec, dry_run=dry_run,
            do_reproject=True, ledger_path=ledger_path, now_ms=now_ms,
            bind_source=AUTOBIND_SOURCE)
    except bind_one.BindError as err:
        out.update(action="refused", why=str(err))
        return 1, out
    out.update(action="autobound" if not dry_run else "autobind_plan",
               why=why, source=AUTOBIND_SOURCE, result=res)
    return 0, out


def seats_dir_for(explicit, state_root):
    """席位登記簿解析。順位:顯式 > **HUB 導出** > EZ2_AIB_INSTANCE_HOME > 狀態根。

    ★ 為什麼要先看 HUB(EH.2):多艙環境下主艙與艙內**同檔名同窗號**,
      `~/.ez2-aib/seats/w5.json` 與 `~/.ez2-aib/instances/16/seats/w5.json`
      是兩個不同的席。只看 env 會退回主艙,拿到一份**格式完全正確的錯答案**
      ——不報錯、不變紅。08-21 實測:seat-onboard 五席 id 全錯,而其中一席
      整天用死檔的 id 署名,從沒失敗過。
    ★ **主艙不是回退來源,是衝突**:HUB 說得出是哪一艙時就只用那一艙。
    ★ 正本判準與參考實作:`src/cockpit/seat_onboard.py:seats_dir`
      與 `tests/test-seats-dir-resolution.py`。
      **這段推導在全庫有多份副本(本次之前 14 支未改)**——正解是一支共用
      helper,而它該住在席位解析家族(`a seat` 的檔)裡;在那之前逐支補,
      並在此具名出處,免得日後各自漂移。
    """
    if explicit:
        return explicit
    hub = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if hub and hub.startswith("aihub-"):
        inst = hub[len("aihub-"):]
        if inst:
            return os.path.join(os.path.expanduser("~"), ".ez2-aib",
                                "instances", inst, "seats")
    return os.path.join(
        os.environ.get("EZ2_AIB_INSTANCE_HOME") or str(state_root), "seats")


def seat_file_for_window(seats_dir, window_id):
    m = WINDOW_RE.fullmatch(str(window_id or ""))
    if not m:
        return None
    return Path(seats_dir) / ("w%s.json" % m.group(1))


def rebind(state_root, seats_dir, window_id, cards_path=None, contract_spec=None, dry_run=False,
           now_ms=None, ledger_path=None, directed_first_captain=False, menu_choice=None):
    """回 (rc, dict)。rc 0=續綁完成或誠實無事可做;1=想續但被拒(不擋開席)。"""
    out = {"window": str(window_id)}
    if menu_choice is not None:
        out["menu_choice"] = menu_choice
    seat_path = seat_file_for_window(seats_dir, window_id)
    if seat_path is None or not seat_path.is_file():
        out.update(action="none", why="席位檔不存在:%s" % seat_path)
        return 0, out
    try:
        with open(seat_path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as err:
        out.update(action="none", why="席位檔讀不出來:%s" % err)
        return 0, out
    seat_id = rec.get("seat_id")
    uuid = rec.get("session_uuid")
    out.update(seat_id=seat_id, session_uuid=uuid)
    if not seat_id or not bind_one.SEAT_ID_RE.fullmatch(str(seat_id)):
        out.update(action="none", why="席位檔沒有合法 seat_id")
        return 0, out
    if not uuid or not bind_one.UUID_RE.match(str(uuid)):
        out.update(action="none", why="席位檔沒有 session_uuid(seat-detect 尚未回填);不猜")
        return 0, out
    cont = bind_one.read_continuity(state_root, seat_id)
    if not directed_first_captain and menu_choice in MENU_SKIP_LOAD:
        out.update(action="none", why="開席選了不載入(%s):非艦長席不續綁、不自動綁(the maintainer 09-26 R4)" % menu_choice)
        return 0, out
    if not directed_first_captain and menu_choice == "fork":
        carried = cont is not None and cont.get("session_uuid") == uuid and bool(cont.get("persona_uid"))
        if carried:
            out.update(action="none", why="載入舊席位已帶人格 %s,不續綁" % cont.get("persona_uid"))
        else:
            out.update(action="none", unbound_after_fork=True,
                       why="載入舊席位沒有帶到人格(舊席沒綁,或帶的時候失敗/被擋);這一席維持未綁,"
                           "不拿同一窗上一位的卡頂替")
        return 0, out
    # ★ 10-11 the maintainer 直令「改冠位規則,只給真正的艦長席」:👑 是排序的結果(受派優先、其次最早開),
    #   新開的艙裡最早開的那一席**自動**戴冠。只戴冠就指名綁 a persona card,會讓每個新艙的第一席都變成
    #   the maintainer 的代言人格,歸隊到別艙後冠沒了、人格還在(10-11 實量:6 席工程師頂著代言卡)。
    #   真正的艦長 = 經任命入口寫進席位檔的受派艦長(`aib appoint captain`,`captain_assigned: true`,
    #   appoint.py:任命=寫席位檔的動作)。只戴冠沒受派 ⇒ 不指名綁,交給候選器,並把原因寫進 why。
    appointed = rec.get("captain_assigned") is True
    crowned_not_appointed = bool(directed_first_captain and not appointed)
    if cont is None:
        # G2:冠位席首綁指名 71c97a,連候選器都不叫。卡不在不得退回唯一命中。
        if directed_first_captain and appointed:
            rc_ab, ab = directed_first_captain_bind(
                state_root, seat_id, rec, cards_path=cards_path,
                contract_spec=contract_spec, dry_run=dry_run, now_ms=now_ms,
                ledger_path=ledger_path)
        else:
            # BA:這席從沒綁過 → 交給候選器決定(唯一命中且滿分才綁)。
            # ★ 守衛在這裡不在下面:**有延續檔的席一律走續綁**,自動綁碰都不碰它
            #   ——否則人上一次的決定會被機器的候選蓋掉,而畫面上兩者長得一樣。
            rc_ab, ab = autobind_first_time(
                state_root, seat_id, rec, cards_path=cards_path,
                contract_spec=contract_spec, dry_run=dry_run, now_ms=now_ms,
                ledger_path=ledger_path)
        out.update(ab)
        if crowned_not_appointed:
            ab["why"] = "戴冠但不是受派艦長(只是最早開),不指名綁首席人格、交給候選器;%s" % ab.get("why", "")
        # ★ 「沒有延續檔」這句不能被自動綁的理由蓋掉:既有消費者
        #   (`tests/test-seat-rebind-on-reopen.py` 的 NothingToDo)讀的就是它,
        #   而兩件事**同時為真**——把後來的理由接在後面,不是換掉前面那句。
        out["why"] = "沒有延續檔(這席從沒綁過);%s" % ab.get("why", "")
        return rc_ab, out
    if not cont.get("persona_uid"):
        out.update(action="none", why="延續檔記的最後狀態是未綁人格(人解綁過),不續")
        return 0, out
    if cont.get("session_uuid") == uuid:
        out.update(action="none", why="延續檔的 uuid 就是現在的 → 已綁,無事可做")
        return 0, out
    actor = {"ai_id": "aib@%s/reopen" % seat_id, "sig": "aib@%s/reopen" % seat_id, "kind": "system"}
    try:
        res = bind_one.bind_seat(
            state_root, seat_id, cont["persona_uid"], actor,
            brain=cont.get("brain_route_key"), rebind_persona=False,
            cards_path=cards_path, contract_spec=contract_spec, dry_run=dry_run,
            do_reproject=True, ledger_path=ledger_path, now_ms=now_ms,
            pin_sha=cont.get("persona_sha") if cont.get("persona_sha") else None)
    except bind_one.BindError as err:
        out.update(action="refused", why=str(err), previous=cont)
        return 1, out
    out.update(action="rebound" if not dry_run else "plan", result=res, previous=cont)
    return 0, out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seat-rebind")
    ap.add_argument("--window", required=True, help="tmux window id(@8 或 8)")
    ap.add_argument("--seats-dir", help="席位檔目錄(預設 $EZ2_AIB_INSTANCE_HOME/seats)")
    ap.add_argument("--state-root", help="身份層狀態根(預設 EZ2_AIB_HOME > ~/.ez2-aib)")
    ap.add_argument("--cards")
    ap.add_argument("--contract", help="預設 EZ2_ROUTE_CONTRACT_SPEC > EZ2_ROUTE_REGISTRY")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--directed-first-captain", action="store_true",
                    help="冠位席:指名綁 71c97a,不走唯一命中")
    ap.add_argument("--menu-choice", choices=MENU_CHOICES, default=None,
                    help="開席選單選了什麼(aib 傳 AIB_OPEN_MENU_CHOICE);不傳=舊行為")
    args = ap.parse_args(argv)
    state_root = bind_one.state_root_of(args.state_root)
    seats_dir = seats_dir_for(args.seats_dir, state_root)
    contract = args.contract or os.environ.get("EZ2_ROUTE_CONTRACT_SPEC") or os.environ.get("EZ2_ROUTE_REGISTRY")
    rc, out = rebind(state_root, seats_dir, args.window, cards_path=args.cards,
                     contract_spec=contract, dry_run=args.dry_run,
                     directed_first_captain=args.directed_first_captain,
                     menu_choice=None if args.menu_choice == "none" else args.menu_choice)
    line = format_suggest_line(out)
    if line:
        out["suggest_line"] = line
        sys.stderr.write(line + "\n")
    elif out.get("action") == "refused" and REASON_DIRECTED_CARD_MISSING in (out.get("why") or ""):
        sys.stderr.write((out.get("why") or "directed_card_missing") + "\n")
    elif out.get("unbound_after_fork"):
        sys.stderr.write("aib: " + out["why"] + "\n")   # 人眼要看得到「這席沒人格」,不是安靜地空著
    sys.stdout.write(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
