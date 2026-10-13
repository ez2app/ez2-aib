# SPDX-License-Identifier: Apache-2.0
"""E2.2 標題接軌 title-beat + 信任狀態紅燈。

兩件事合成一條標題:

  **title-beat**:標題帶一個每次取樣都會前進的節拍。靜止的標題和當掉的更新器
  長得一模一樣 —— 沒有節拍,看板凍在三小時前你也看不出來。節拍讓「還活著」
  變成畫面上看得見的事實,而不是一個假設。

  **信任紅燈**:這個 session 現在算不算被系統認得。判定只看機器觀測得到的欄位,
  缺欄位一律紅(fail-closed),與 E2.7 血緣律的 registry_field_absent 同一條紀律。

## 紅黃綠的判準(與現況)

  red    沒有 registry 記錄 / 心跳過期 / 沒有任何綁定欄位 / 綁定申請未經人核可
  yellow 已綁定但 ACB 標記未經證實(bound_unattested)/ 人已核可但真綁未成 /
         已證實但心跳轉舊
  green  已綁定**且** ACB 自己標 formal_eligible 與 native_attestation 皆為 true

**欄位名的教訓(2026-08-08 修正)**:我原本用 `registry_verified` 判斷,那個名字
是我猜的。EA.1 綁定完成後實查 498 筆 registry 記錄,`registry_verified` **一筆都沒有**;
真正存在的是 `binding_uid` / `session_status` / `formal_eligible` /
`native_attestation` / `authority_level`。猜錯名字的代價是:綁定明明成功了,
這支程式卻會把每個已綁 session 都判成「沒有驗證欄位」——燈永遠紅,而且紅得毫無道理。

**綠燈現在仍亮不起來,但理由變了。** 77 筆記錄有 `binding_uid`,其中 68 筆
`session_status=active`;那 68 筆**自己標著** `formal_eligible=false`、
`native_attestation=false`、`authority_level=same_user_registry_unattested`,
意思是「綁了,但這份權威未經證實」。所以現況的最佳狀態是黃燈 bound_unattested。
把「綁定成功」直接畫成綠燈,等於幫 ACB 宣告它自己都不敢宣告的事。

純度:本模組不讀時鐘、不讀檔案。now_ms 與記錄都由呼叫端傳入,
所以同一份輸入必得同一條標題(測試與黃金幀才可能成立)。
"""

import sys
# ★ E2.2 純度契約(acceptance/E2.2.json 第 4 條:AST 禁 import time/datetime、禁 open()):
#   本檔原本 `import time  # 只給 cabin_since 格式化用` —— 註解說得對,契約仍然紅(qa 批二真紅,
#   2026-08-22)。契約釘的是「模組層零時鐘依賴」不是「有沒有真的讀時鐘」:time.localtime 讀 TZ,
#   同一份輸入在兩台機器會得兩條標題。現在 cabin_since 的時間轉換**只**由呼叫端注入,格式化
#   用 struct_time 欄位自己拼,不 import time。

RED, YELLOW, GREEN = "red", "yellow", "green"

# 心跳新鮮度門檻(毫秒):與 demo 看板的 15 分/1 小時分界一致
FRESH_MS = 15 * 60 * 1000
STALE_MS = 60 * 60 * 1000

BEAT_FRAMES = ["·", ":", "∴", ":"]      # 四拍循環,等寬安全(不用 emoji)
LIGHT_GLYPH = {RED: "●", YELLOW: "●", GREEN: "●"}

# ★★ 2026-08-16 the maintainer 問「紅是代表什麼」——問題本身就是答案:**分不出來**。
#   上面三級用**同一個符號**,靠 `_COLOR` 的 ANSI 區分,而
#   **tmux 視窗標題不吃 ANSI**(本檔 title_string 的 docstring 自己寫著)。
#   所以在標題上紅/黃/綠長得一模一樣,那盞燈在它唯一要顯示的地方無法顯示狀態。
#   ★ 而同日稍早把狀態碼(`seat_live`)移出標題,等於拿掉最後一個可區分的
#     資訊 —— **那一刀讓它更難讀,不是更乾淨。**
#   修法:無色情境用**形狀**區分,不靠顏色。空心=紅、半實=黃、實心=綠,
#   即使在單色終端機、截圖、複製貼上之後都還讀得出來。
#   ★ 既有的 LIGHT_GLYPH 不動:它有別的消費者(面板吃得下 ANSI),
#     動它會把一個顯示問題擴散成兩個。
MONO_GLYPH = {RED: "○", YELLOW: "◐", GREEN: "●"}

# ANSI 色碼(render_lib 也有一份調色盤,但這裡只需要三個前景色,
# 為了讓本模組零依賴、可被任何腳本單獨匯入,刻意不 import render_lib)
_COLOR = {RED: "\x1b[31m", YELLOW: "\x1b[33m", GREEN: "\x1b[32m"}
_RESET = "\x1b[0m"


def trust_light(record, bind_item=None, now_ms=None):
    """回 {"level", "code", "detail"};判定只看機器事實,缺一律紅。

    record   ACB registry 記錄(dict)或 None
    bind_item E2.6 綁定佇列的卡(dict)或 None
    """
    if now_ms is None:
        raise ValueError("now_ms 必須由呼叫端傳入(本模組不讀時鐘)")
    if not isinstance(record, dict) or not record:
        return {"level": RED, "code": "no_registry_record",
                "detail": "registry 查無此 session"}

    beat = record.get("beat_epoch") or 0
    age_ms = now_ms - int(beat) * 1000 if beat else None
    if age_ms is None:
        return {"level": RED, "code": "no_heartbeat", "detail": "記錄裡沒有心跳"}
    if age_ms > STALE_MS:
        return {"level": RED, "code": "heartbeat_stale",
                "detail": "心跳已 %d 分鐘沒動" % (age_ms // 60000)}

    # ACB v2 綁定的真實欄位(2026-08-08 實查 498 筆:registry_verified 一筆都沒有,
    # 真正存在的是 binding_uid / session_status / formal_eligible /
    # native_attestation / authority_level)。先前用 registry_verified 是我猜的名字,
    # 猜錯的後果是已綁 session 被永遠判成「沒有驗證欄位」。
    verified = record.get("registry_verified")
    if verified is None:
        bound = bool(record.get("binding_uid")) and \
            record.get("session_status") == "active"
        if bound:
            # ACB 自己標 formal_eligible=false / native_attestation=false,
            # 意思是「綁了,但這份權威未經證實」。那不是綠燈,是黃燈。
            if record.get("formal_eligible") is True and \
                    record.get("native_attestation") is True:
                return {"level": GREEN, "code": "bound_attested",
                        "detail": "已綁定且經證實"}
            return {"level": YELLOW, "code": "bound_unattested",
                    "detail": "已綁定但 ACB 標記未經證實(%s)"
                              % (record.get("authority_level") or "authority_level 缺")}
        if bind_item and bind_item.get("status") == "approved_pending_ea1":
            return {"level": YELLOW, "code": "approved_pending_ea1",
                    "detail": "人已核可,真綁候 EA.1"}
        if bind_item and bind_item.get("status") == "pending":
            return {"level": RED, "code": "bind_pending",
                    "detail": "綁定申請候人核可"}
        return {"level": RED, "code": "registry_field_absent",
                "detail": "registry 沒有任何綁定欄位,視同未綁"}
    if not verified:
        return {"level": RED, "code": "not_registry_verified",
                "detail": "registry 驗證未通過"}
    if age_ms > FRESH_MS:
        return {"level": YELLOW, "code": "verified_but_aging",
                "detail": "已驗證,但心跳 %d 分鐘沒動" % (age_ms // 60000)}
    return {"level": GREEN, "code": "verified_fresh", "detail": "已驗證且心跳新鮮"}


def beat_glyph(beat_seq):
    return BEAT_FRAMES[int(beat_seq) % len(BEAT_FRAMES)]


def seat_record(seat, pid_alive=False, transcript_age_s=None, now_s=None,
                persona=None, rank_hint=None, model=None):
    """席位檔 → `trust_light` 吃的記錄形狀。取不到就回 None。

    ## 為什麼要有這支(2026-08-16,the maintainer:「unbound no_registry_record 要修好」)

    那盞燈原本只讀 **ACB 的 `sessions/by-id/<uuid>.json`**,而 08-16 實測:
    那個目錄最新一筆停在 **08-11**,而且不會再有新的 —— ACB 已判廢除。
    **所以那盞燈永遠不會綠,它問的是一個已經停止更新的資料源。**
    ★ 同日另一項實測佐證:把 `EZ2_ACB_HOME` 指到空目錄,那盞燈顯示的字
      **一模一樣** —— 它早就已經是「查不到」了,與 ACB 在不在無關。

    而 AIB 自己的席位檔有全部需要的東西,**而且證據品質更好**:

        這是誰    ACB 的 `sig`            → 席位檔 seat_id + role
        還活著嗎  ACB 的 `beat_epoch`     → **pid 實測 + transcript mtime**
        被驗證嗎  ACB 的 registry_verified→ 席位檔 trust(誠實寫死 self_reported)

    ★★ `ps -p <pid>` 比讀心跳檔可靠:心跳檔會因為**寫它的人**死掉而停止更新,
      而那跟「這個行程活著」是兩件事;pid 是直接問作業系統。

    ## 為什麼上限是黃燈,不是綠燈

    席位檔的 `trust` 永遠是 `self_reported`(`aib` 的紅線:直起路徑沒有密碼學
    身份,**不得自稱 verified**)。三項證據齊全只證明「活著、身份可讀」,
    不證明「有第二方背書」。**綠燈保留給真的有背書那天。**

    參數刻意全部由呼叫端傳入(pid 活性、transcript 年齡、時鐘)——
    本模組是純函數,不讀時鐘也不碰系統。
    """
    if not isinstance(seat, dict) or seat.get("schema") != "aib-seat/v1":
        return None
    sid = seat.get("seat_id")
    if not isinstance(sid, str) or not sid:
        return None
    # ★ 09-14(the maintainer 螢幕實見:標題 `a seat/Copilot`、右欄同一席 `a seat`——同一席兩個碼):
    #   五碼的唯一來源是 seat_address(session_uuid 尾五碼,EH.17 08-22 換源),右欄與艦長列
    #   早就換了,只有標題還在印 seat_id。這裡改讀同一支 label_of;沒有 uuid 的舊席退回
    #   seat_id(與右欄同一條補位路,同一個 LEGACY 計數器出聲),所以舊治具的輸出不變。
    #   import 放這裡不放檔頭:title/ 是子目錄,呼叫端(title-beat.sh)已把 EZ2_COCKPIT_DIR
    #   放進 sys.path;純度契約只禁時鐘與 open(),seat_address 兩者都不碰。
    try:
        import seat_address as _sa
        _label = _sa.label_of(seat)
        if isinstance(_label, str) and _label.startswith("@") and "?" not in _label:
            sid = _label[1:]
    except ImportError:
        pass
    role = seat.get("role") if isinstance(seat.get("role"), str) else "?"
    provider = seat.get("provider") if isinstance(seat.get("provider"), str) \
        else "?"
    # ★ 段位圖示與艦長列、右欄**同一套語彙**(👑 現任 / ⚓ 次位 / 🎓 顧問),
    #   不另發明一套 —— 同一個席位在三個地方要長一樣。
    #   判準也同源:`captain_assigned` 勝過 role(受派勝過資深),而
    #   `advisor` 永不計入現任(顧問是獨立段位)。
    #   ★ 這裡只認**這一席自己的**旗標,不做跨席比較,所以給的是「⚓ 或 👑」
    #     裡保守的那個判斷:受派=👑,其餘艦長級=⚓。跨席排序是艦長列的事,
    #     標題只回答「我這一席是什麼」。
    #   ★★ 2026-08-21 修(the maintainer 實見:第一艦長的標題掛 ⚓)。
    #     上面那段「保守判斷」在**艙裡只有一個艦長**時給出的是錯的值,不是
    #     保守的值——⚓ 的語意是「第二位起」,唯一的艦長戴它就是說謊。
    #     成因是這支純函數看不到別席,而 👑 的真規則(全域唯一、只給第一
    #     艦隊首位、未編制永不戴冠、captain_assigned 單獨不算數)住在
    #     pane-right-ai.sh。
    #     修法**不是把規則抄一份進來**——那正是本檔一直在防的形狀(同一條
    #     規則兩份實作,遲早分岔成兩個畫面說相反的話,而這個 bug 就是那個
    #     分岔已經發生的證據)。改成:呼叫端把右欄**算完的結果**傳進來
    #     (`rank_hint`,來源=rowmap 的 detail.mark),本函式只認段位那三枚。
    #     取不到就退回原本的自判 —— 面板還沒渲染過是合法狀態,不是錯誤。
    #   ★★★ 2026-08-21 qa(codex,獨立第二引擎)二審判退,已修:
    #     白名單收 👑⚓🎓❓🤖🔑(🤖=aibot 橋接席,2026-09-18;🔑=API-key 次位,2026-09-20);收到未列字元才退回自判。舊註:只收 👑⚓🎓 時,❓ 會退回自判,
    #     於是一個**身份驗不出來**的 root 席在標題上拿回 ⚓。
    #     那比原本的 bug 更糟:右欄刻意拒絕替未編制的席做段位主張
    #     (pane-right-ai.sh:「未編制者永不戴皇冠;👑=現任艦長,那是身份
    #     主張」),而我的退路把那個拒絕撤銷了——**兩個畫面對同一席說相反
    #     的話,而說得比較篤定的那個是證據比較少的那個。**
    #     ❓ 也是右欄算出來的結果,一併收下並原樣顯示:未編制壓過段位,
    #     與右欄同一條規則。
    if isinstance(rank_hint, str) and rank_hint in ("👑", "⚓", "🎓", "❓", "🤖", "🔑"):
        rank = rank_hint + " "
    elif seat.get("advisor") is True:
        rank = "🎓 "
    elif seat.get("captain_assigned") is True:
        rank = "👑 "
    elif role in ("root",):
        # 無右欄 hint 時的保守自判:API-key 席次位語彙是 🔑 不是 ⚓
        # (the maintainer 2026-09-20);provider 不明仍走 ⚓。
        _prov = (seat.get("provider") or "").strip().lower()
        if _prov in ("deepseek", "nvidia", "openrouter", "typesafe", "openrouterjev"):
            rank = "🔑 "
        else:
            rank = "⚓ "
    else:
        rank = ""
    # ★★ 2026-08-16 the maintainer 改判:`@五碼` 前面顯示**綁定的人格**,與右欄一致。
    #   人格由呼叫端用既有的 `seat_persona.persona_of()` 取好再傳進來——
    #   **不在這裡另做一套取值**:那支已經擋掉 `@` 與 `|` 兩種偽造字元
    #   (「假人格@他人五碼」會在畫面上冒充別席身分),重寫一份等於把那道
    #   防護漏掉。沒綁人格就退回 role,畫面不會空掉。
    #   ★★ 這裡**自己再擋一次** `@` 與 `|`,不是重複勞動:呼叫端可能繞過
    #     persona_of 直接傳字串進來,而「保護在別處、這裡假設它一定被叫到」
    #     正是本 repo 今天反覆抓到的形狀。實測繞過時會印成
    #     `👑 假人@a seat/Claude` —— 前半看起來就是一個合法身分。
    #     髒值一律退回 role,不顯示比顯示一個可冒充的字串安全。
    who = persona if (isinstance(persona, str) and persona
                      and "@" not in persona and "|" not in persona) else role
    # ★ 2026-08-21 the maintainer:sig 尾端再加一段「這一席在用哪個模型」
    #   (`a seat/Claude/Opus5`)。引擎答「哪一家」,模型答「哪一顆」——
    #   同一個 provider 底下不同模型的行為差很多,而在此之前畫面上分不出來。
    # ★ 取不到就**整段不出現**,退回兩段 `who@sid/Provider`:
    #   猜一個模型比不顯示危險得多(它會讓人以為那一席在用別的東西)。
    #   非 claude 的席今天就會落在這裡,理由見 title-beat.sh 的採集段。
    _model = model_label(model)
    rec = {
        "sig": "%s%s@%s/%s%s" % (rank, who, sid, provider.capitalize(),
                                 ("/" + _model) if _model else ""),
        # 席位活性走**兩個獨立訊號**:pid 還在,或 transcript 剛動過。
        # 兩個都取不到就不給 beat —— 讓燈誠實地說「沒有心跳」。
        "seat_trust": seat.get("trust"),
    }
    if now_s is None:
        return rec                       # 沒給時鐘就不編心跳(純函數紅線)
    if pid_alive:
        rec["beat_epoch"] = int(now_s)
    elif transcript_age_s is not None:
        rec["beat_epoch"] = int(now_s - max(0, transcript_age_s))
    return rec


def seat_light(rec, now_ms):
    """席位記錄的燈。**上限黃燈**,理由見 `seat_record`。

    ★ 這裡**不能**直接沿用 `trust_light` 的結果再加工:那支階梯的終點是
      `registry_verified` 這個 ACB 欄位,而席位檔永遠沒有它,所以它一定會
      在「registry_field_absent」那一格回紅。**第一版我寫成「非紅才覆蓋」,
      於是永遠覆蓋不到** —— 實測仍印 `registry_field_absent`。
      席位檔這條路的判準是獨立的:**有沒有活性訊號**,而不是「有沒有 ACB
      的驗證欄位」。只借用 `trust_light` 的心跳新鮮度門檻,不借它的階梯。
    """
    if not isinstance(rec, dict) or not rec.get("sig"):
        return {"level": RED, "code": "no_seat_record",
                "detail": "席位檔認不出來"}
    beat = rec.get("beat_epoch") or 0
    if not beat:
        # pid 死了、transcript 也沒動 —— 沒有任何活性訊號。
        return {"level": RED, "code": "seat_no_signal",
                "detail": "席位檔在,但 pid 不存在且 transcript 未更新"}
    age_ms = now_ms - int(beat) * 1000
    if age_ms > STALE_MS:
        return {"level": RED, "code": "seat_stale",
                "detail": "最後活動已超過門檻"}
    # 有活性訊號 + 身份可讀 = 黃。`self_reported` 拿不到綠。
    return {"level": YELLOW, "code": "seat_live",
            "detail": "席位檔可讀且行程活著;trust=%s,無第二方背書"
                      % (rec.get("seat_trust") or "?")}


def cabin_code(token, name=None):
    """艙碼:`<艙名>·<token 前八碼>`,取不到就退到看得出「取不到」的字。

    ★ 2026-08-16 the maintainer 直令:「aib 執行後最上方要有一個獨一無二的 aib 專屬
      uuid,用來方便所有 AI 彼此定位。」

    ★★ **刻意不新發一個識別碼。** 這個系統已經有三層(instance-token 32 位、
      seat_id 五碼、session_uuid),再加第四層只會讓「哪個碼指哪件事」更難
      講清楚。這裡用的是**已經存在、已經每艙唯一**的 instance-token 前八碼
      ——它本來就在每一席的環境變數裡(`EZ2_AIB_INSTANCE_TOKEN`),只是
      從來沒有上過畫面。

    ★ 三層各自回答什麼(寫在這裡,免得下一棒又發一個新碼):
        instance-token  這是**哪一艙**      ← 本函式用它
        seat_id         這是**艙裡哪一席**
        session_uuid    這一席**當下是哪個 session**(換 session 會變)

    ★ 取前八碼不是為了美觀:32 位塞進視窗標題會把後面的信任狀態擠掉,而
      八碼十六進位對「同時開著的艙數」(個位數)是遠遠夠用的。**若哪天真的
      撞碼,那是顯示問題不是身份問題**——完整 32 位一直在 instance-token 檔裡。
    """
    short = ""
    if isinstance(token, str):
        t = token.strip()
        if t:
            short = t[:8]
    if not short:
        # 誠實降級:不要編一個看起來像 token 的東西出來。
        short = "no-token"
    return "%s·%s" % (name, short) if name else short


NO_CABIN = "no-cabin"


def cabin_label(instance):
    """艙位碼四碼:艙 id 最後一個 `-` 之後、最多四碼(與 dm_address.cabin_code / dm_header.cabin_tail 同式)。

    ★ 2026-10-07 the maintainer 直令:標題改成 `AIB <IP>:[<艙位碼>]`,不再印 `<session 名>·<token 八碼>`。
      舊寫法把開艙時間印了兩次(session 名裡一次、尾段 `MM/DD HH:MM:SS` 一次),token 八碼對人沒意義,
      而 Terminal 標題列自己又會補一段 `tmux attach -t aib-…`,整條重複三次。
    ★ 用 dm 地址同一個碼:dm 寫 `IP/艙位碼/席碼`,標題寫 `IP:[艙位碼]`,人看標題就知道怎麼 dm 這一艙。
    ★ 取不到 ⇒ `no-cabin`(看得出取不到,不編碼)。舊流水號艙 `13` ⇒ `13`;主艙 `main` ⇒ `main`。
    """
    if not isinstance(instance, str):
        return NO_CABIN
    inst = instance.strip()
    if not inst:
        return NO_CABIN
    return inst.rsplit("-", 1)[-1][-4:]


def uptime_str(started_epoch, now_s):
    """已執行多久 → `3d4h` / `12h34m` / `7m` / `41s`。取不到回空字串。

    ★ 只取兩個最大單位:標題的最後一段,再長就會被終端機截掉。
    ★ 時鐘由呼叫端注入(now_s),本模組不讀時鐘。
    """
    try:
        delta = int(now_s) - int(started_epoch)
    except (TypeError, ValueError):
        return ""
    if delta < 0 or int(started_epoch) <= 0:
        return ""                        # 未來的開艙時間=資料壞了,不編數字
    d, rem = divmod(delta, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return "%dd%dh" % (d, h)
    if h:
        return "%dh%dm" % (h, m)
    if m:
        return "%dm%ds" % (m, s)
    return "%ds" % s


def title_string(sig, light, beat_seq, role=None, color=False, cabin=None,
                 since=None, uptime=None, version=None, addr=None, public=None):
    """一行標題:`AIB [<外網IP> | ]<IP>:[<艙位碼>] <beat> <燈> <sig> <role|since>`(addr=None 時艙段退回 `[<cabin>]`)。
    public:外網 IP(10-08 the maintainer 令;public_ip.py,取不到 = None ⇒ 整段不印,其他格式不變)。

    燈的顏色只在 color=True 時上色(tmux 標題不吃 ANSI,面板才吃)。

    ★ 艙碼排在最前面(緊接 AIB):視窗標題會被終端機從**尾端**截斷,
      而「這是哪一艙」是所有 AI 互相定位的第一個問題,不能被截掉。
      cabin=None 時整段不出現(相容既有呼叫端與黃金幀)。

    ★★ 2026-08-16 the maintainer 改判尾段:**不要 `[role]` 與狀態碼,改顯示這一艙是
       什麼時候啟用的**(`MM/DD HH:MM:SS`)。
       理由(我的理解,寫下來以便被糾正):`sig` 已經帶了段位圖示與角色,
       `[pm]` 是同一件事講第二次;而狀態碼對**天天看著它的人**幾乎恆定,
       占著標題最後一段卻不帶資訊。開艙時間反而是「這是哪一次開的艙」——
       跟艙碼互補:艙碼答「哪一艙」,時間答「哪一次」。
       ★ 燈本身沒有消失:`glyph` 仍在(●/◐/○ + 顏色),紅了照樣看得見;
         **消失的是那串代碼,不是狀態**。要看為什麼紅,`--print` 一次即得。
       since=None 時退回舊行為(帶 role 與 code),既有呼叫端與黃金幀不受影響。
    """
    # 有顏色時用實心+ANSI;沒顏色(tmux 標題)時用**形狀**區分,見 MONO_GLYPH。
    if color:
        glyph = _COLOR[light["level"]] + LIGHT_GLYPH[light["level"]] + _RESET
    else:
        glyph = MONO_GLYPH[light["level"]]
    # ★ 版號放在**最尾端**(2026-08-21 the maintainer 直令改判)。
    #   我原本放在 AIB 之後、艙碼之前,理由是「標題被終端機從尾端截斷,
    #   放尾巴會被沉默地截掉」。the maintainer 要求放最後,那是他的畫面他決定。
    #   ★ 動手前實測了截斷風險:當下標題 85 顯示格、終端機 112 格,
    #     餘裕 27 格——**今天不會被截**。所以那個顧慮是理論的,不是現況。
    #   ★ 它什麼時候會咬人:標題被顯示在比終端機更窄的地方(tmux 狀態列的
    #     窗清單、終端機分頁標籤),或人格名很長把中段撐開。真的發生時
    #     失效形狀是**沉默的假陰性**——更新記號被截掉,而你只會以為沒有更新。
    #     這句留在這裡,不是為了翻案,是為了那天有人來查時不必重新推一遍。
    parts = ["AIB"]
    if cabin and public:
        # 10-08 the maintainer:`AIB <外網IP> | <區網IP>:[<艙位碼>]`。外網段只在查得到時出現。
        parts.append("%s |" % public)
    if cabin:
        # 10-07 the maintainer:`<IP>:[<艙位碼>]`,跟 dm 地址 `IP/艙位碼/席碼` 用同一組碼。addr 取不到就只印 `[艙位碼]`,不印假 IP。
        parts.append(("%s:[%s]" % (addr, cabin)) if addr else ("[%s]" % cabin))
    parts += [beat_glyph(beat_seq), glyph, sig or "?"]
    if since:
        parts.append(since)
        if uptime:
            parts.append("| %s" % uptime)
        # ★ 版號在**兩條回傳路徑上都要接**。這支函式有兩個 return,
        #   只接一條的話「舊呼叫端(帶 role 不帶 since)」會整段消失,
        #   而那種漏法不會有人發現——畫面只是少一段,不會報錯。
        if version:
            parts.append(version)
        return " ".join(parts)
    if role:
        parts.append("[%s]" % role)
    parts.append(light["code"])
    if version:
        parts.append(version)
    return " ".join(parts)


#: 版號段的更新記號。單 codepoint、EAW=W 與艦隊圖示同紀律。
UPDATE_MARKS = {
    "behind": "↑",        # ↑ 遠端有東西還沒合進來
    "code_changed": "⟳",  # ⟳ 這一席啟動後碼變過(跑的是舊碼)
    "unknown": "?",            # 讀不到判準來源 —— **不是**「沒事」
}


def model_label(model_id):
    """模型 id → 標題上的短名。取不到回空字串(整段不出現,**不猜**)。

        claude-opus-5              → Opus5
        claude-fable-5             → Fable5
        claude-haiku-4-5-20251001  → Haiku4.5
        claude-opus-5[1m]          → Opus5
        gpt-5.6-sol                → Gpt5.6

    ★ 用**規則**不用對照表:對照表的失效形狀是「新模型出來那天悄悄變成
      空白或原字串」,而那不會紅——今天早上才因為逐族列舉的角色圖示付過
      同一筆學費(dev3 掉進 fallback)。規則的代價是偶爾不漂亮,那是划算的。

    ★ 砍掉的三種東西各有理由:
        `[1m]` 之類的括號變體 —— 那是同一個模型的容量變體,不是另一個模型
        結尾的 8 位日期      —— 是發版日不是版本
        版號之後的尾碼        —— 例如 `-sol`,對「這是哪個模型」不加資訊
    """
    if not isinstance(model_id, str):
        return ""
    m = model_id.strip().lower()
    if not m:
        return ""
    m = m.split("[")[0]                      # claude-opus-5[1m] → claude-opus-5
    for pref in ("us.anthropic.", "anthropic.", "claude-", "models/"):
        if m.startswith(pref):
            m = m[len(pref):]
    parts = [t for t in m.replace("_", "-").split("-") if t]
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        parts = parts[:-1]                   # 結尾的發版日期
    if not parts:
        return ""
    name = parts[0]
    if not name[:1].isalpha():
        return ""
    nums = []
    for t in parts[1:]:
        if t.replace(".", "").isdigit():
            nums.append(t)
        else:
            break                            # 版號之後的尾碼不要
    return name.capitalize() + ".".join(nums)


def short_version(version):
    """顯示用的短版號:砍掉第一個 `-` 之後的預發行後綴。取不到回空字串。

    `0.42.1-alpha.1` → `0.42.1`(the maintainer 08-21:「只要顯示 v0.42.1 這樣即可」)

    ★ 砍的是**顯示**不是判準:更新偵測比的是 git ref,與這個字串無關,
      所以砍短不會讓任何版本比較變弱。這句要留著——否則下一個人會以為
      版號被截短就等於版本比較被截短。
    ★ 抽成獨立函式是因為它有**兩個消費者**:標題的版號段、與狀態列要的
      純版號。第一版我讓後者去呼叫 version_segment() 再剝掉開頭的 v,
      結果連預設參數帶出來的「不明」記號一起吃進去,狀態列拿到 `0.42.1?`。
      **同一條規則兩個呼叫路徑,就該是同一支函式。**
    """
    if not isinstance(version, str):
        return ""
    return version.strip().partition("-")[0]


def version_segment(version, behind=None, code_changed=None):
    """版號段:`v<版號>` 加上更新記號。取不到版號回空字串(整段不出現)。

    ## 三態,不是兩態

    `behind` / `code_changed` 收 **True / False / None**:

        True  → 有這件事,掛記號
        False → 確定沒有
        None  → **判準來源讀不到**,掛 `?`

    ★ `None` 與 `False` 一定要分開。git 的 ref 可能被 pack 進 `packed-refs`
      而讓 `refs/heads/main` 這個檔不存在;那時如果把「讀不到」當成「沒有新
      版本」,這個指示器的失效值就恰好等於它表示安全的那個值——
      **壞掉的時候長得跟一切正常一模一樣**,而這種形狀本 repo 已經付過好幾
      次代價(空輸入的 sha1 與守衛失敗逐字相同、collector 對不存在的目錄回
      空 rc=0)。寧可畫一個 `?` 讓人問一句。

    ## 為什麼不在這裡讀檔

    本模組是純函數(不讀時鐘、不碰系統),採集一律在呼叫端。這也是它能被
    測試釘死三態的原因——治具只要傳值,不必造一棵 git 樹。
    """
    if not isinstance(version, str) or not version.strip():
        return ""
    version = short_version(version)
    if not version:
        return ""
    marks = ""
    if behind is None or code_changed is None:
        marks += UPDATE_MARKS["unknown"]
    if behind is True:
        marks += UPDATE_MARKS["behind"]
    if code_changed is True:
        marks += UPDATE_MARKS["code_changed"]
    return "v%s%s" % (version.strip(), marks)


def version_state(behind=None, code_changed=None):
    """更新狀態 → 一個**純字串**,給狀態列的顏色分支用。

    回 "ok" / "behind" / "stale" / "unknown"。

    ★ 為什麼是字串不是顏色:顏色屬於顯示層,而**視窗標題與狀態列是兩個
      不同的顯示層**——標題只吃純文字(終端機的 OSC 標題通道沒有樣式這
      個東西,實測 macOS Terminal 讀回來就是一串字),狀態列吃得下樣式。
      本函式只回答「現在是什麼狀態」,兩層各自決定要畫成形狀還是顏色。
      **判準一份,呈現兩種**——這正是今天標題段位那個 bug 教的事。

    ★ unknown 排在最前面判:讀不到判準來源時,**不得**因為 behind=False
      就落到 ok。失效值不可以等於表示安全的那個值。
    """
    if behind is None or code_changed is None:
        return "unknown"
    if code_changed is True:
        return "stale"
    if behind is True:
        return "behind"
    return "ok"


def cabin_since(epoch, localtime=None):
    """艙啟用時間 → `MM/DD HH:MM:SS`。取不到回空字串。

    ★ 時間轉換由呼叫端注入(`localtime`:epoch → struct_time,採集端傳 time.localtime),
      本模組不 import time、不讀時鐘也不碰時區設定 —— 與 `trust_light` 同一條紀律:
      判定是純函數,環境只在採集端讀。**沒注入就回空字串**,不偷用預設時鐘
      (空白比一個在別台機器上不同的時間誠實)。
    """
    try:
        e = int(epoch)
    except (TypeError, ValueError):
        return ""
    if e <= 0 or localtime is None:
        return ""
    try:
        t = localtime(e)
        mon, day = int(t.tm_mon), int(t.tm_mday)
        hh, mm, ss = int(t.tm_hour), int(t.tm_min), int(t.tm_sec)
    except (AttributeError, OSError, OverflowError, ValueError, TypeError):
        return ""
    return "%02d/%02d %02d:%02d:%02d" % (mon, day, hh, mm, ss)


# 九種信任狀態的代表輸入(黃金幀用;也是「這支程式能產生哪些畫面」的清單)
LIGHT_CASES = [
    ("no_registry_record", None, None),
    ("no_heartbeat", {"sig": "x@00000/Claude"}, None),
    ("heartbeat_stale", {"sig": "x@00000/Claude", "beat_offset_s": -3 * 3600}, None),
    ("bind_pending", {"sig": "x@00000/Claude", "beat_offset_s": -60},
     {"status": "pending"}),
    ("registry_field_absent", {"sig": "x@00000/Claude", "beat_offset_s": -60}, None),
    ("not_registry_verified",
     {"sig": "x@00000/Claude", "beat_offset_s": -60, "registry_verified": False}, None),
    ("approved_pending_ea1", {"sig": "x@00000/Claude", "beat_offset_s": -60},
     {"status": "approved_pending_ea1"}),
    ("bound_unattested",
     {"sig": "x@00000/Claude", "beat_offset_s": -60,
      "binding_uid": "bind_x", "session_status": "active",
      "formal_eligible": False, "native_attestation": False,
      "authority_level": "same_user_registry_unattested"}, None),
    ("bound_attested",
     {"sig": "x@00000/Claude", "beat_offset_s": -60,
      "binding_uid": "bind_x", "session_status": "active",
      "formal_eligible": True, "native_attestation": True}, None),
    ("verified_but_aging",
     {"sig": "x@00000/Claude", "beat_offset_s": -20 * 60, "registry_verified": True},
     None),
    ("verified_fresh",
     {"sig": "x@00000/Claude", "beat_offset_s": -60, "registry_verified": True}, None),
]


def state_table_lines(now_ms, cases=None):
    """把每一種信任狀態各畫一行(黃金幀凍結它,顏色與版面退化都會被抓到)。

    純函數:record 用相對秒數描述心跳,呼叫端給 now_ms 才算出絕對時間,
    所以同一份輸入永遠得到同一張表。
    """
    out = []
    for i, (expect, rec, bind) in enumerate(cases or LIGHT_CASES):
        record = None
        if rec is not None:
            record = dict(rec)
            if "beat_offset_s" in record:
                record["beat_epoch"] = now_ms // 1000 + record.pop("beat_offset_s")
        light = trust_light(record, bind, now_ms)
        assert light["code"] == expect, "案例 %s 實得 %s" % (expect, light["code"])
        out.append(title_string(record["sig"] if record else "unbound",
                                light, i, role="dev1", color=True))
    return out


def _main(argv):
    import argparse
    import json
    p = argparse.ArgumentParser(prog="title_beat.py")
    p.add_argument("--record-json", default="")
    p.add_argument("--bind-json", default="")
    p.add_argument("--sig", default="?")
    p.add_argument("--role", default=None)
    p.add_argument("--beat", type=int, default=0)
    p.add_argument("--now-ms", type=int, required=True)
    p.add_argument("--color", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    def _load(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except ValueError:
            return None

    light = trust_light(_load(a.record_json), _load(a.bind_json), a.now_ms)
    if a.json:
        print(json.dumps(light, ensure_ascii=False))
    else:
        print(title_string(a.sig, light, a.beat, a.role, a.color))
    return 0 if light["level"] == GREEN else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
