#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位「❓未編制」判定 — 單一真相源(the maintainer 08-15 直令)。

★ 語意(the maintainer 原話已確認,寫進碼是刻意的):
  **❓未編制 =「系統無法驗證這是什麼」,不是「這是垃圾」。**
  它描述的是**我們的驗證能力**,不是那一席的價值或死活。一席可能正在密集
  工作、回訊息、pid 也對得上,但只要沒有任何機器可驗證的身份,面板就不該
  假裝認得它。修好身份之後它會**自動**變回原本的角色圖示,不需要人工改
  任何東西——本模組是純函式,沒有狀態、沒有側檔、沒有人工覆寫入口。

★ 會遇到的反對意見,先寫在這裡(08-15 實際爭論過):
  「@15 剛證明它是活的 root(回訊息、pid 一致),標成 ❓ 不是更不準?」
  不是。席位檔的 `trust` 恆為 `self_reported`(aib:264 紅線),缺 uuid 表示
  **沒有任何第二方背書**。它自己說它是 root,而我們無法驗證。這是既有
  「寧缺勿假」紅線的延伸:`[X]` 已經做到「沒訊號就不亮」,顯示層跟上。

兩個觸發條件(任一成立即 ❓;the maintainer 直令原文對應):
  a. no_seat  未綁進 aib:沒有席位檔,或席位檔不完整到無法辨識
  b. no_uuid  沒有 uuid:session_uuid 缺失或形狀不合

判定順序 no_seat → no_uuid:由外而內,先否定整份記錄再否定身份。
順序只影響回傳哪一個診斷碼,不影響是否 ❓。

★★ 曾經有第三條 (c) child_source,08-16 初審高-3 移除(the maintainer 裁)。
   **它擔心的事沒有消失,是被一個不變量涵蓋掉了**,寫在這裡免得下一個人
   重新推導出 (c) 再加回來:

   (c) 原本要防的是——「子代理是從 `<projects>/<session>/subagents/` 撈出來
   的,撈哪個目錄就決定了這些子是誰的;席位若沒有可驗證的 session_uuid,
   面板仍可能靠 seat_id 五碼去對 session 檔名(既有 fallback),那是『自報
   的五碼剛好等於某個檔名的頭或尾』,於是右欄會把**別席的子代理當成自己
   的**」。這個顧慮本身是對的,而且與 seat-detect.py:76-78「錯綁比空值更糟」
   同源。

   **但它在本實作裡不可達,而且是邏輯上多餘的:**

     ① 五碼 fallback 只在 `session_uuid` 缺或壞時才走 —— 而那時 (b) 已經
        先回傳 NO_UUID,根本走不到 (c)。
     ② `session_uuid` 合法時,`activity_probe.resolve_session` 的第一個分支
        就 `return raw`(合法值直接綁,免猜),所以面板拿去撈子代理的 uuid
        **恆等於**席位檔宣告的那一個 —— 子代理的來源由建構方式保證正確,
        沒有東西可以分岔。

   ★ 所以真正該被守住的不是 (c) 那段碼,是**讓 (c) 多餘的那個不變量**:
     「`resolve_session` 對合法 uuid 原樣回傳」。那條不變量一旦壞掉(或兩份
     刻意分開的 uuid regex 漂移),(c) 想防的病就會回來 —— 而 (c) 那段碼
     **永遠不會叫**,它只在測試裡被直接呼叫過。
     現在改由 `test-seat-status.py` 的 InvariantThatMakesCUnnecessaryTest
     釘住:**用一支會在正確時機變紅的斷言,換掉一段永遠不會執行的防禦碼。**
"""

import json
import os
import re

# 席位檔 schema 正本(與 aib:259 write_seat_record 同值;此處只讀不寫)
SEAT_SCHEMA = "aib-seat/v1"

# 完整 uuid;與 activity_probe._UUID_FULL_RE / seat-detect.UUID_RE 同形。
# 刻意各自留一份 regex:三處是不同層(採集/偵測/判定),共用會讓任一層
# 改形狀時另兩層靜默跟著變。形狀相同由測試看守,不由 import 綁死。
_UUID_FULL_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_WID_RE = re.compile(r"@[0-9]+")

NO_SEAT = "no_seat"
NO_UUID = "no_uuid"
# CHILD_SOURCE 已移除(08-16 初審高-3,理由見檔頭)。**不留成別名**:
# 留一個永遠不會被回傳的常數,下一個人會以為那條路還活著。

# 顯示層字彙:❓ 單 codepoint(the maintainer 舊拍板:複合 emoji 弄歪網格)。
UNASSIGNED_MARK = "❓"


def uuid_shape_ok(value):
    """完整 uuid 形狀;非字串/缺/壞形一律 False(不做任何正規化)。"""
    return isinstance(value, str) and bool(_UUID_FULL_RE.fullmatch(value))


def record_legible(rec, base=None):
    """席位記錄「認得出來」嗎——(可讀, 診斷字串|None)。

    認不出的四種形狀與 pane-right-ai.sh 既有的 skip 判準逐條對齊(不另發明
    第二套):非 dict / schema 不符 / window_id 形狀不合 / 檔名與內容對不上。
    base 給了才驗檔名(guard 有檔名,渲染端逐檔讀時也有)。"""
    if not isinstance(rec, dict):
        return False, "席位記錄不是物件"
    if rec.get("schema") != SEAT_SCHEMA:
        return False, "schema 不是 %s:%r" % (SEAT_SCHEMA, rec.get("schema"))
    wid = rec.get("window_id")
    if not isinstance(wid, str) or not _WID_RE.fullmatch(wid):
        return False, "window_id 形狀不合:%r" % (wid,)
    if base is not None and base != "w%s.json" % wid.lstrip("@"):
        return False, "檔名與 window_id 對不上:%s vs %s" % (base, wid)
    return True, None


# ---- 身份候偵測(the maintainer 2026-08-29 直令:開席即正式角色圖示,不戴紅 ❓)------
# 這三家 CLI 不接受指派 session id(provider-launch:97),uuid 靠事後 artifact
# 偵測回填——「還沒回填」是這些引擎開席的**正常中間態**,不是「無法驗證」。
# ★ 射程只到顯示層:守衛照舊用 unassigned_reason,安全判準一格未放寬。
PENDING_PROVIDERS = frozenset({"codex", "copilot", "agy"})


def identity_pending(rec, base=None):
    """NO_UUID 且屬候偵測引擎 ⇒ True(顯示層戴角色圖示;guard 不得引用本函式)。"""
    if unassigned_reason(rec, base) != NO_UUID:
        return False
    return isinstance(rec, dict) and rec.get("provider") in PENDING_PROVIDERS


def unassigned_reason(rec, base=None):
    """回兩碼之一或 None(None=可驗證,顯示正常角色圖示)。

    ★ 08-16 初審高-3:原本還收 `child_source_uuid` / `has_children` 兩個參數
    給第三條 (c)。**兩個參數一併移除,不留成忽略的參數**——一個收下來卻不用
    的參數,會讓呼叫端以為自己傳的東西有被檢查。理由全文見檔頭。
    """
    legible, _why = record_legible(rec, base)
    if not legible:
        return NO_SEAT
    if not uuid_shape_ok(rec.get("session_uuid")):
        return NO_UUID
    return None


# ---- 開席單回執判定(A;2026-08-16)-------------------------------------
# ★ 為什麼跟 unassigned_reason 放同一支:兩者都是「面板要顯示什麼」與
#   「守衛要擋什麼」共用的判準。既有紀律(本檔 pane-right-ai.sh:160-163
#   註記)寫得很清楚——各留一份等值判斷,遲早分岔成「面板說沒事、守衛說
#   有事」。回執判定同理,故不另開模組。
#
# ★ 這裡判的是**「它回了沒」,不是「它做了沒」**。回執只證明那一席在收到
#   開席單之後,在自己的畫面上輸出了約定的字。aib-dm.sh 檔頭那句
#   「送達≠送到工作」在這一層仍然成立,只是把界線從「按鍵落進輸入框」
#   往前推了一格到「對方對這封信有反應」。不要在文案上宣稱更多。
ONBOARD_NEVER = "onboard_never"      # 沒有任何投遞紀錄(B 的補投目標)
ONBOARD_NO_ACK = "onboard_no_ack"    # 投了,沒等到回執
ONBOARD_STALE = "onboard_stale"      # 回執有,但停在舊版模板

# 顯示層字彙:單欄 ASCII。★ 刻意不用 ✉/⚠ 這類符號——它們是 East Asian
# Width「模糊」字,同一個字在不同終端佔 1 或 2 欄,整片網格會歪(⚫ 與
# ● 那次實測的同一個坑)。這個位置只需要「有東西不對」的一格提示,
# 不需要它好看。
# ★★ 08-22 換字元(判準 a seat `CRITERIA-onboard-mark-char-0822.md`,
#   艦長 12:36 裁):原本是 `!`,而**碼撞號標記也是 `!`**
#   (`cockpit_lib.mark_colliding_labels`)—— 同一個字元承載兩個互不相關的意思,
#   而畫面上**一個解釋都沒有**(i18n 值裡含 `!` 的鍵 = 0;校準:含 `~=` 的 = 4)。
#   實害:眼驗看到 `a seat!`,而使用者能查到的唯一一條該字元說明講的是碼衝突。
# ★ 挑 `*` 的過程是機器的不是品味:已佔用集合由碼取出
#   (seat_status 的 *MARK* 常數、`mark_colliding_labels(["x","x"])` 反推、
#    i18n `*legend*` 值裡 `=` 左邊那一段)= {❓, !, ?, ~};`*` 不在其中,
#   而且黃金幀裡出現 0 次。單欄 ASCII 的理由與下面那段原註解同(EAW 模糊字會歪版)。
ONBOARD_MARK = "*"


def should_mark_onboard(reason, legend_present):
    """要不要在名字後面掛開席單標記。**純函式,兩個條件都要成立。**

    ★★ 這一支存在的理由是「它原本不存在」:那個判斷本來內聯在
      `pane-right-ai.sh` 的一個 for 迴圈裡,而**內聯在 shell 內嵌 python 裡的
      判斷,單元測試碰不到** —— 碰不到的判斷就是沒有人在守的判斷,
      而這一整批要修的東西(一個字元上了畫面而沒有人解釋它)正是那樣長出來的。

    ★ `legend_present` 是第二個條件,不是裝飾:標記與解釋它的圖例必須同時
      上畫面(與 `~` 逐字同一條不變式)。圖例沒注入就不標 ——
      **拿掉這個條件,畫面會出現一個使用者查不到解釋的字元。**
    """
    return bool(legend_present) and reason in (ONBOARD_NO_ACK, ONBOARD_STALE)


# ★★ v0.70.0 第 9 條(the maintainer 2026-09-27 09:58 裁):**開席不等「已就緒」就當可用。**
#   開席單是背景資訊;沒回只在名字後掛 `*`(另開欄位,不寫進 name),**不蓋列、不擋用**。
#   舊版在這裡有「載入 Ez2 AIB 中… {階段}」整列蓋掉席碼的輪播(09-10 起),
#   實測 09-27:copilot 月額度用完、grok 週額度用完、deepseek 讀不到開席單檔
#   —— 三席都開好了,右欄卻「開好又退回載入中」15 分鐘。那一整組(輪播鍵、
#   年齡閘 onboard_loading_applies、階段索引)已刪,不留成死碼。
# 守望窗仍是 15 分鐘:它是回執守望者等多久(seat_onboard.DETACHED_ACK_TIMEOUT_S)
#   與收艙 grace 的單源,與畫面無關了。
ONBOARD_LOADING_WINDOW_S = 15 * 60

# 橋接席(sand/codexbot/grokbot/claudebot)不會回「已就緒」
AIBOT_PROVIDERS = frozenset(("sand", "codexbot", "grokbot", "claudebot", "musebot"))

# 名字後綴(另開欄位 name_suffix,右欄渲染時接在名字後面;不寫進 name ——
#   open_right_check 對 name 做完全比對,後綴寫進 name 會讓重判把好席判成缺席碼)。
ACK_FAIL_QUOTA = "quota"


def onboard_name_suffix(reason, legend_present, ack_fail_why=None, acked_now=False,
                        quota_text=None):
    """一席名字後面要接什麼。純函式。→ 字串(可空)。

    ・開席單未回/停舊版 且 圖例在 ⇒ `*`(與 should_mark_onboard 同一條件)
    ・回執守望記到額度用完 且 現行回執沒到 ⇒ 再接 ` <額度用完>`(quota_text 由採集端給 i18n 值)
    兩者都**只是後綴**,不改 name、不蓋列。"""
    out = ""
    if should_mark_onboard(reason, legend_present):
        out += ONBOARD_MARK
    if ack_fail_why == ACK_FAIL_QUOTA and not acked_now and quota_text:
        out += " " + str(quota_text)
    return out


# O2:右欄「開席進行中 / 失敗原因 / 重試」讀 O1 JSONL,不拿 Q 的輪播冒充進度。
# 階段鍵給 i18n `aib.opening.stage.*`;失敗鍵給 `aib.opening.fail.*`。
OPENING_STAGE_BY_EVENT = {
    "pressed": "starting",
    "start": "starting",
    "cabin_ready": "window",
    "window_built": "window",
    "switch": "provider",
    "provider_first_frame": "ack",
    "can_type": "ack",
}
OPENING_FAIL_EVENTS = frozenset(("pane_dead", "watch_timeout"))
OPENING_DONE_EVENTS = frozenset(("ready",))
OPENING_FAIL_WHYS = frozenset(("timeout", "pane_dead", "capture_unavailable",
                               "watch_timeout"))
# idle_reaper 有限 grace:開席還沒結束的艙不當 detach 收掉。
OPENING_REAPER_GRACE_S = ONBOARD_LOADING_WINDOW_S


def _usable_after(rows, verdict_row):
    """判決列之後,事件鏈有沒有走到 can_type(= 這一席已經能打字)。純函式,不讀檔。

    用 identity 找位置(同一份 rows 裡的同一個 dict),不比內容:兩筆判決可以一模一樣。
    """
    seen = False
    for r in rows:
        if r is verdict_row:
            seen = True
            continue
        if seen and isinstance(r, dict) and r.get("event") == "can_type":
            return True
    return False


def opening_attempt_state(rows, ack_fail_why=None, pane_dead=False):
    """一份 attempt 的事件列 → {mode, stage?, fail?} 或 None。

    ready ⇒ None(畫普通席)。失敗事件或帳本 ack_fail ⇒ failed。
    其餘有事件 ⇒ opening。空列 / 非法輸入 ⇒ None,不猜。
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        return None
    events = [r.get("event") for r in rows if isinstance(r, dict)]
    if any(e in OPENING_DONE_EVENTS for e in events):
        return None
    # 09-14(the maintainer 18:0x「右方顯示正確的席位資訊=開席成功」):right_painted_ok 帶 waited_ms
    # =五項全對 ⇒ 開席完成,畫普通席(開席單改成選單後不會再有 ready,不能再等它)。
    # 帶 missing ⇒ 未確認:列上標「未確認 · 缺 X」,不是失敗、也不是開席中。
    # right_pane_off ⇒ 右欄關著判不了,照舊當開席中往下走。
    # ★ 取**最後一筆**:同一次開席可以被重判(判準修了、或人手動複判),最新的判決才算數。
    _ok_rows = [r for r in rows if isinstance(r, dict) and r.get("event") == "right_painted_ok"]
    if _ok_rows:
        note = str(_ok_rows[-1].get("note") or "")
        if note.startswith("waited_ms="):
            return None                                        # O2-right-ok-done
        if "missing=" in note:
            # ★ 10-10(the maintainer 直令,a seat 實踩):那一筆是**當下畫面的快照**,不是永久判決。
            #   新艙第一席常在右欄自己都還沒畫好時就被量到 ⇒ 記下「五項全缺」;
            #   但事件鏈接著走到 can_type(CLI 能打字=這一席可用,v0.70.0 第 9 條)。
            #   快照在 can_type **之前**就代表它已經過期,再畫「未確認 · 缺…」是假警報,
            #   而且永遠不會自己消失(實例:艙 931e 的 a seat 掛了 40 分鐘)。
            #   往下掉到 can_type 那一條即可——窗死仍然蓋過一切,複判(新的 right_painted_ok)
            #   因為排在 can_type 之後,照樣算數。
            if not _usable_after(rows, _ok_rows[-1]):
                miss = note.split("missing=", 1)[1].split()[0]
                return {"mode": "unconfirmed",
                        "missing": [m for m in miss.split(",") if m]}
    # ★ v0.70.0 第 9 條:CLI 已經能打字(can_type)⇒ 這一席**可用**。之後唯一算失敗的是窗死;
    #   回執逾時/讀不到畫面/額度用完都只是「開席單沒回」,名字後掛 `*`(與額度後綴),畫普通席。
    #   can_type 之前的行為一個字都不變(碼表逾時、帳本 ack_fail 照舊畫失敗)。
    if "can_type" in events:                                   # O2-usable-after-can-type
        if pane_dead is True or "pane_dead" in events or ack_fail_why == "pane_dead":
            return {"mode": "failed", "fail": "pane_dead"}
        return None
    why = None
    for e in events:
        if e in OPENING_FAIL_EVENTS:
            why = "pane_dead" if e == "pane_dead" else "timeout"
    if isinstance(ack_fail_why, str) and ack_fail_why in OPENING_FAIL_WHYS:
        why = ack_fail_why if ack_fail_why != "watch_timeout" else "timeout"
    # 09-11(@15 c0317):tmux 說這個窗的 pane 死了,就是死了——碼表到過 can_type、
    # 回執逾時,都改不了「CLI 已退出」這個事實;蓋過其他原因,提示才會是重開不是重投。
    if pane_dead is True:
        why = "pane_dead"
    if why:
        return {"mode": "failed", "fail": why}  # O2-opening-fail-on
    stage = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        ev = r.get("event")
        if ev in OPENING_STAGE_BY_EVENT:
            stage = OPENING_STAGE_BY_EVENT[ev]
    if not stage:
        return None
    return {"mode": "opening", "stage": stage}  # O2-opening-progress-on


def load_opening_attempts(home):
    """艙家目錄 → {window_id: 該窗最新一份 attempt 的事件列}。讀不到=空 dict。"""
    out = {}
    if not home:
        return out
    try:
        import open_attempt as oa
        d = oa.attempts_dir(home)
    except Exception:
        return out
    if not os.path.isdir(d):
        return out
    best = {}  # window -> (latest_wall, rows)
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".jsonl"):
            continue
        try:
            rows = oa.read_events(os.path.join(d, fn))
        except (OSError, ValueError):
            continue
        wins = {}
        for r in rows:
            w = r.get("window")
            if not w:
                continue
            wall = int(r.get("wall_ms") or 0)
            prev = wins.get(w)
            if prev is None or wall > prev:
                wins[w] = wall
        for w, wall in wins.items():
            prev = best.get(w)
            if prev is None or wall > prev[0]:
                best[w] = (wall, rows)
    for w, (_wall, rows) in best.items():
        out[w] = rows
    return out


def load_ack_fails(ledger_path):
    """帳本 → {seat_id: timeout|pane_dead|capture_unavailable}。壞行跳過。"""
    out = {}
    if not ledger_path or not os.path.exists(ledger_path):
        return out
    try:
        import seat_onboard as so
    except Exception:
        return out
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            lines = fh
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                why = so.is_ack_fail_event(ev)
                if not why:
                    continue
                cid = (ev.get("causation") or {}).get("correlation_id")
                seat = so.seat_of_correlation(cid) if cid else None
                if seat:
                    out[seat] = why
    except OSError:
        return out
    return out


def instance_opening_in_progress(home, attempts=None):
    """這艙還有未結束的開席 ⇒ idle_reaper 不得當 detach 收掉。"""
    attempts = load_opening_attempts(home) if attempts is None else attempts
    if not isinstance(attempts, dict):
        return False
    for rows in attempts.values():
        st = opening_attempt_state(rows)
        if st and st.get("mode") == "opening":
            return True
    return False


def onboard_reason(entry, template_sha):
    """回三碼之一或 None(None=已回執且停在現行模板)。

    entry = 該席的投遞索引 {"outbound": {sha...}, "inbound": {sha...}},
    由 seat_onboard.scan_onboard_events 從帳本建;沒有這一席就給 None/{}。
    template_sha = **現行**模板檔的 sha256。

    ★ 為什麼 ONBOARD_NEVER 不該上畫面(呼叫端負責):從未投過的席位,
      面板亮警示等於**對使用者宣告一件他無從處置的事**——那是 B 的補投
      目標,不是那一席的狀態。而且反過來會踩到「做過頭」:任何還沒跑過
      新投遞路徑的既有安裝,全部席位會同時亮起來。判準在這裡完整回報
      四態,要不要畫由呼叫端決定,兩件事不混在一個函式裡。
    """
    if not isinstance(entry, dict):
        entry = {}
    out = entry.get("outbound") or set()
    ack = entry.get("inbound") or set()
    if not out and not ack:
        return ONBOARD_NEVER
    if template_sha and template_sha in ack:
        return None
    # ★★ B18.4-b(裁定 RULING-B184b,cap `a seat` 08-29 23:0x):
    #   **現行模板投過而沒回執 ⇒ 一律 NO_ACK,不看往事。**
    #   原本這裡直接落下面的 `if ack: STALE`,於是**只要曾經回過任何一版,
    #   那一席就永久免疫**,再也不會出現在 no_ack —— 不論之後有幾次未回執的投遞。
    #   不是「兩席」,是「回過一次就終身豁免」。
    #   ★ 實例:0c6a3 / 5c59e 各 outbound{舊,現行} inbound{舊},被判 stale 而在缺口清單裡隱形,
    #     而那正是全機**唯二**兩次現行模板投遞 —— 缺口偵測剛好漏掉它唯一的樣本。
    if template_sha and template_sha in out:
        return ONBOARD_NO_ACK
    # ★ 走到這裡表示現行模板**還沒投給他**。於是兩態的意思各自收斂:
    #     STALE   = 回過舊版、現行未投  → 那正是 `--missing` 該送的人
    #     NO_ACK  = 現行投了沒回        → 那正是 `--retry-no-ack` 存在的理由
    #   ⇒ `seat_onboard.py` 的 `want = {NEVER, STALE}` 一個字都不用改,
    #     而它在本次之後**比之前更正確**(裁定 §二 的五類輸入表)。
    if ack:
        return ONBOARD_STALE             # 回過舊版,而現行模板還沒投給他
    return ONBOARD_NO_ACK
