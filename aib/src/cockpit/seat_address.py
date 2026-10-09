#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位定址:**位址與抬頭一律五碼,不用角色名**(the maintainer 08-18 17:3x 直令)。

原話:「不要用 dev4 這種名字,要用 @xxxxx 這種名字」。這條做成程式,不是慣例——
角色名是**顯示用的標籤**,會撞名、會換人、會隨編制改;位址必須是身份本身。
今天為「用名字判身份」已經修了一整天(dm 發送席取 client 視窗、任命只在對話裡、
繼任閘看 role 名),這是同一條的最後一段:**抬頭與位址也不准用名字**。

## ★ 五碼有兩組,而它們不一樣 —— 產生端只用 UUID,舊碼只供拒投診斷

    seat_id      f300c   bindings.json / appointments.jsonl / rotation 帳 / 右欄都用它
    uuid 末五碼   3479d   the maintainer 字面講的是這個

`code_of()` 是合法位址的唯一產生端。`seat_id` 仍需讀得出,但只為了在收到舊碼時
具名拒投並告知正確座位碼；它不得再成為投遞目標或出站抬頭。
"""

import json
import os
import re
import sys

# legacy 欄名集中一處,只供診斷/稽核；合法位址來源固定在 code_of()。
CODE_FIELD = "seat_id"

# 角色字面:抬頭與位址一律不得出現(閘用)
# 08-21:`design` 升為獨立角色、`dev` 家族含無編號的裸 `dev`,兩者一併入閘。
# ★ 順序有意義(見下方比對迴圈的「長的先比」):裸 `dev` 必須排在
#   dev1..dev5 之後,否則它會先命中而回報的字面是 `dev` 不是 `dev3`
#   ——訊息指錯字面,修的人會找不到。`design` 與 `dev` 無前綴關係
#   (design 裡沒有 dev 這個子字串),放哪都不影響,排在 dev 家族前面
#   只是為了讀起來像編制表。
ROLE_LITERALS = ("root2", "root", "pm2", "pm3", "pm",
                 "design",
                 "dev1", "dev2", "dev3", "dev4", "dev5", "dev",
                 "qa", "monitor")
_CODE_RE = re.compile(r"^[0-9a-z]{4,8}$")


UUID_FIELD = "session_uuid"
UNKNOWN_LABEL = "@?????"     # 一般顯示層的未知標籤；本來就過不了位址白名單
UNMEASURABLE_LABEL = "量不到"  # B11 出站層的誠實值,不得偽裝成可貼位址
LEGACY_MARK = "aib-addr: legacy-seat-id-accepted"
# ★ 艦長 02:11 裁:翻階段 4 的條件是**兩個**計數都連續為 0 ——
#   「還解得到舊碼」(kind=resolve)與「還在發舊碼」(kind=emit)是兩件事:
#   前者歸零代表沒有人再用舊碼呼叫,後者歸零代表沒有席還缺 uuid。
#   只數其中一個都會太早翻頁。所以 kind= 是**欄位**不是修辭,巡查照它分開數。
LEGACY_KIND_RESOLVE = "kind=resolve"
LEGACY_KIND_EMIT = "kind=emit"
# ★ EH.17 3c:純討論舊碼的訊息也要留紀錄,但**不得**被算進翻轉條件。
#   那一行若帶 kind=emit,討論舊碼就會讓 emit 非 0,於是階段 4 永遠到不了 ——
#   那正好是「同一個條件的兩種壞法」的第一種(到不了的翻轉條件)。
#   所以它是**第三個 kind**,巡查分開數;三個值互不相等由測試釘住。
LEGACY_KIND_TREND = "kind=trend"


def uuid_tail5(value):
    """session_uuid → 末五碼(小寫、剝連字號);算不出回 None。

    ★ EH.17 甲:這是全庫**唯一**一處末五碼取法。收進來之前散在三個地方,
      而且**不等價**:`cockpit_lib.py:54` 是 `u[-5:]`(不剝連字號),
      另兩處是 `u.replace("-","")[-5:]`。

    ★ 活體 7 席對這件事**零分辨力** —— 格式良好的 uuid 末 12 字元不含連字號,
      兩種取法同值。分歧只在畸形輸入上出現,而 `u[-5:]` 在那時會產出**含 `-` 的碼**,
      那個碼過不了下面的 `_CODE_RE`,也就是**產出一個非法位址而不報錯**。
      → 「活體上兩種寫法答案一樣」不是它們等價的證據,只是那批資料沒有鑑別力。
    """
    if not isinstance(value, str):
        return None
    flat = value.replace("-", "").strip().lower()
    if len(flat) < 5:
        return None
    tail = flat[-5:]
    return tail if _CODE_RE.match(tail) else None


def code_of(seat_rec):
    """席位檔(dict)→ 五碼字串;取不到回 None(**不猜、不用 seat_id 頂替**)。

    ★ EH.17 乙-2 換源:來源是 `session_uuid` 末五碼,不再是 `seat_id`。
      the maintainer 直令「用程式把這個事固定起來」。

    ★ B11 後是**產生端單源、診斷端識舊**:
      這一支管合法位址；`match_code()` 的 legacy 回值只讓拒投路徑認得
      寄件者給的是舊碼並指出正確座位碼,不得再解成投遞目標。
    """
    if not isinstance(seat_rec, dict):
        return None
    return uuid_tail5(seat_rec.get(UUID_FIELD))


def legacy_codes_from_seats(seats_dir):
    """席位目錄 → 舊碼(seat_id)集合。讀不到回空 tuple,**不拋**。

    ★ 從席位檔讀,不寫死:寫死的話新開的席不會被計到,而**漏計沒有紅燈**。
    """
    import glob as _glob
    import json as _json
    out = set()
    try:
        paths = sorted(_glob.glob(os.path.join(seats_dir, "*.json")))
    except (OSError, TypeError):
        return ()
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                rec = _json.load(handle)
        except (OSError, ValueError):
            continue
        val = rec.get(CODE_FIELD) if isinstance(rec, dict) else None
        if isinstance(val, str) and _CODE_RE.match(val):
            out.add(val)
    return tuple(sorted(out))


def _legacy_re(codes, at_prefix):
    """舊碼比對正則。**詞界是承重的**,不是防禦性寫法。

    ★ 七個舊碼是 base36,長度 4-8 —— 它們很容易長在別的東西裡面:
      sha 前綴(`d524f3ab9c`)、分支名(`dev1/d524f-fix`)、檔名(`96188.json`)。
      沒有詞界的話數字會往上跳,而**沒有人看得出來為什麼**,
      於是那個計數會從「翻轉條件」變成「一個沒人敢信的數字」。
    """
    if not codes:
        return None
    body = "|".join(re.escape(c) for c in codes)
    head = r"(?<![0-9a-z])@" if at_prefix else r"(?<![0-9a-z])"
    return re.compile(head + "(" + body + r")(?![0-9a-z])")


def legacy_in_addresses(text, codes):
    """正文裡**當位址用**的舊碼 → (次數, 命中清單)。這是翻轉條件那個數字。

    ★ 只認 `@` 前綴(艦長 07:19 三格反推出來的唯一規則):
        `a seat`      → 計   —— 拿舊碼當位址
        `d524f3ab9c`  → 不計 —— sha 前綴
        `舊碼 d524f`  → 不計 —— 討論它,不是用它定址
    ★ 最後一格特別重要:**掃描器會掃到討論舊碼的訊息,包括宣告這條規則的那一封。**
      與「談論偵測器的樣式會污染偵測器」同族。

    ★★ 這個數字的**母體限定,少了它整個數字會被誤讀**(a seat 07:31 判為合入條件):
      掃的是**送件端的 BODY**,所以它數的是「**本席**送出去的訊息裡有幾個舊碼位址」,
      不是「全艦還有多少人在用舊碼」。
      → 翻轉條件必須寫成 **「所有席的 addr 連續 N 次為 0」**;
        寫成「emit 為 0」會被**單席的沉默**滿足 —— 一個不送信的席天然貢獻 0。
      → 下一個人若看到自己這席 addr=0 就去翻階段 4,而舊碼一停用,
        **還在用舊碼的那些席就送不出訊息**。

    ★ 同一個條件的兩種壞法(a seat 07:31,兩種都不是在量它名字上那件事):
        **到不了的翻轉條件** —— 化石艙的 emit 永遠非 0,於是永遠不滿足;
        **被沉默滿足的翻轉條件** —— 單席不送信,於是太容易滿足。
      前者由「計數必須指定 hub、化石艙不計入」擋;後者由「所有席」這個量詞擋。
    """
    pattern = _legacy_re(codes, at_prefix=True)
    if pattern is None or not isinstance(text, str):
        return 0, []
    hits = pattern.findall(text)
    return len(hits), hits


def legacy_anywhere(text, codes):
    """全文出現的舊碼 → (次數, 命中清單)。**這是趨勢指標,不是翻轉條件。**

    ★ 兩個數字必須是兩個函式:合成一個的話,討論舊碼的訊息會把翻轉條件頂住,
      而階段 4 永遠不會到。**一個永遠到不了的條件與沒有條件是同一件事。**
    """
    pattern = _legacy_re(codes, at_prefix=False)
    if pattern is None or not isinstance(text, str):
        return 0, []
    hits = pattern.findall(text)
    return len(hits), hits


def voice_legacy_in_body(text, codes):
    """正文裡有舊碼當位址時出聲一行(kind=emit),回 (addr, anywhere)。

    ★ 為什麼要有這一支:`label_of()` 的 emit 只看得到**席位紀錄**,
      而**手寫在正文裡的舊碼一次都不經過它** —— 於是 emit 恆為 0,
      而那個 0 正是階段 4 的翻轉條件之一。**失效值等於它表示安全的那個值。**
      (a seat 找到;a seat 用自己 24 份稿量到舊碼 43 次 / 新碼 3 次。)

    ★ 出聲不改 rc:這條路徑**現在是正常路徑**,散文裡引用別席位址是日常行為。
      報錯會讓遷移期無法運作,而一個永遠會紅的閘會訓練所有人忽略紅。
    """
    addr, hits = legacy_in_addresses(text, codes)
    anywhere, anyhits = legacy_anywhere(text, codes)
    if addr:
        sys.stderr.write(
            "%s %s addr=%d anywhere=%d codes=%s"
            "  ★ 只有 addr= 是翻轉條件,anywhere= 是趨勢指標\n"
            % (LEGACY_MARK, LEGACY_KIND_EMIT, addr, anywhere,
               ",".join(sorted(set(hits)))))
    elif anywhere:
        # ★ EH.17 3c(a seat 07:31 登記、艦長 07:34 開工):
        #   `anywhere=` 原本只在 addr>0 時被印出來,於是**趨勢指標恰好在它最有
        #   資訊的那個母體上看不見** —— 純討論舊碼的訊息完全不留紀錄。
        # ★ 修法不是「多印一行」是「多一個 kind」:帶位址那一種的標記的話,
        #   討論舊碼就會把翻轉條件頂住。兩個數字要能分開數才有用。
        # ★★ 這一行的**說明文字裡不可以出現另一個 kind 的字面** ——
        #   我第一版寫「翻轉只看 kind=emit 的 addr=」,於是巡查 grep 那個字面時
        #   **會數到這些趨勢行**,而這支函式存在的理由正是不要汙染那個計數器。
        #   判準 test_the_trend_line_is_not_counted_as_emit 當場抓到。
        #   同族:**不要把偵測器的樣式寫進它會讀到的地方** —— 這次是我在
        #   一個防止計數汙染的功能裡親手汙染了那個計數。
        sys.stderr.write(
            "%s %s addr=0 anywhere=%d codes=%s"
            "  ★ 趨勢指標,不是翻轉條件;翻轉只看位址那一種的 addr=\n"
            % (LEGACY_MARK, LEGACY_KIND_TREND, anywhere,
               ",".join(sorted(set(anyhits)))))
    return addr, anywhere


def match_code(seat_rec, token):
    """這個 token 指的是這一席嗎。回 "uuid" / "legacy" / None。

    B11 之後的非對稱是刻意的:uuid 命中可供投遞；legacy 命中只供呼叫端
    **具名診斷後拒投**。保留 legacy 判斷是為了把「查無此人」改成可操作的
    「你給的是 seat_id,正確座位碼是 X」,不是保留第二條投遞軌。

    解到舊碼仍在 stderr 出聲並帶固定標記,讓遷移稽核保有歷史連續性；
    但任何把 "legacy" 當成可投遞命中的呼叫端都違反 B11。
    """
    if not isinstance(seat_rec, dict) or not isinstance(token, str):
        return None
    tok = token.strip().lstrip("@").lower()
    if not tok:
        return None
    code = code_of(seat_rec)
    if code and tok == code:
        return "uuid"
    legacy = seat_rec.get(CODE_FIELD)
    if isinstance(legacy, str) and _CODE_RE.match(legacy) and tok == legacy:
        sys.stderr.write("%s kind=resolve seat=%s uuid_code=%s\n"
                         % (LEGACY_MARK, legacy, code or "-"))
        return "legacy"
    return None


def label_of(seat_rec, fallback_wid=None, allow_legacy=True):
    """顯示/位址標籤；出站呼叫必傳 allow_legacy=False。

    ★ EH.17 甲:不再退回窗號。舊行為的失效形狀是**送到了、抬頭掛別人的名字**
      —— 窗號在多艙下不唯一,而它長得像一個合法位址,所以沒有人會發現。
      一般 UI 仍以 `@?????` 顯示未知；B11 出站層改印「量不到」並停手。

    ★ `fallback_wid` 參數留著不刪:呼叫端 6 處都還在傳它,拿掉會變成
      TypeError 風暴,而那與本階段要修的事無關。**它現在被忽略,而那是刻意的**
      —— 下一顆再清呼叫端。

    ★ B11 收掉 EH.17 的**出站抬頭**補位:缺 UUID 時不得用 seat_id 假裝座位碼。
      所以 dm/CLI 明傳 `allow_legacy=False`,得到逐字「量不到」。右欄等顯示層
      仍可明傳或沿用 True 顯示舊席,避免定址裁定越界改變面板排序與可見性。
      舊碼仍可由 `match_code()` 辨識,但只能用於具名拒投。
    """
    code = code_of(seat_rec)
    if code:
        return "@" + code
    if allow_legacy and isinstance(seat_rec, dict):
        legacy = seat_rec.get(CODE_FIELD)
        if isinstance(legacy, str) and _CODE_RE.match(legacy):
            sys.stderr.write("%s kind=emit seat=%s uuid_code=-\n"
                             % (LEGACY_MARK, legacy))
            return "@" + legacy
    if not allow_legacy:
        return UNMEASURABLE_LABEL
    return UNKNOWN_LABEL


def contains_role_literal(text):
    """閘:這段字裡有沒有角色字面。回命中的那個(沒有回 None)。

    ★ 比對前剝掉 `(暱稱)`:括號內是給人看的人格卡名字,**本來就允許是人話**。
      若某張卡叫「pm 助理」而閘擋掉它,就是把「加人類看得懂的暱稱」自己否定掉。
      **閘管的是位址那一半,不是給人看的那一半。**

    ★ 曾經還有一行「先剝 @五碼」,理由寫的是「@f3pm5 這種合法碼會被誤判」——
      **那個理由不成立,而且那行一個案例都沒救到**(dev4⚓ 二方審 B 條指出,本席實測
      更徹底):正則兩側已有 `(?<![0-9A-Za-z])` 邊界,`@f3pm5`/`@pm2ab`/`@qa123`
      剝與不剝都不命中;而 `@a-pm-b` 這種帶連字號的**非法**碼,因為不符
      `@[0-9a-z]{4,8}` 所以剝也剝不掉,剝與不剝**都**命中。
      → 帶著假理由的死碼比沒有更糟:下一個人會照那個理由去改動它。已刪。
    """
    if not isinstance(text, str):
        return None
    stripped = re.sub(r"[((][^))]*[))]", "", text)
    for role in ROLE_LITERALS:      # 長的先比(root2 先於 root、pm2 先於 pm)
        # 不分大小寫:`a seat` / `PM@x` 一樣是拿名字當位址,不能因為大寫就放行。
        if re.search(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(role),
                     stripped, re.IGNORECASE):
            return role
    return None


def assert_no_role_literal(text, where="header"):
    """不合格就拋 ValueError(呼叫端 fail-closed)。"""
    hit = contains_role_literal(text)
    if hit:
        raise ValueError("%s 不得出現角色字面 %r(位址一律 @五碼):%r" % (where, hit, text))
    return text


def _main(argv):
    """CLI:`seat_address.py label '<seats-json>' [fallback_wid]` → 印 @五碼。
    給 shell 消費端(dm-ctl.sh)用,避免 shell 內散落第二種取法。"""
    import json
    import sys
    if not argv or argv[0] != "label":
        print("usage: seat_address.py label '<json>' [fallback]", file=sys.stderr)
        return 2
    try:
        rec = json.loads(argv[1]) if len(argv) > 1 else {}
    except ValueError:
        rec = {}
    label = display_label(rec, argv[2] if len(argv) > 2 else None,
                          allow_legacy=False)
    try:
        assert_no_role_literal(label, "label")
    except ValueError as err:
        print("seat_address: %s" % err, file=sys.stderr)
        return 2
    print(label)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))


# ---- 人類看得懂的暱稱(the maintainer 08-18 17:49 批「是」)----------------------------
# 格式 `a seat(開發工程師)`:**五碼承重,括號是給人看的**。
# 純五碼機器乾淨但人一眼認不出誰是誰;兩者不必二選一——但**分工要寫死**:
#
#   ★★ 括號內的字**任何程式不得解析**。它來自人格卡 display_name:會改名、會換綁、
#      會因為卡片升版而變。程式一旦拿它做判斷,就等於把「顯示標籤」升格成身份,
#      那正是今天修了一整天的病(role 當位址、名字判身份)換一個包裝回來。
#      要比對席位請用 `code_in_label()` / `same_seat()`,它們只看五碼。
#
# 來源鏈:bindings.json.bindings[<五碼>].persona_uid → cards.json.cards[<uid>].display_name
# 取不到就不加括號(**不用 role 頂替**——那是這一刀要拔掉的東西)。
IDENTITY_DIRNAME = "identity"
_LABEL_RE = re.compile(r"@([0-9a-z]{4,8})")


def _identity_path(name, aib_home=None):
    import os
    root = aib_home or os.environ.get("EZ2_AIB_HOME") or \
        os.path.join(os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(root, IDENTITY_DIRNAME, name)


CODEXBOT_NICK = "CodexBot 桌面"


def nickname_of(code, aib_home=None):
    """五碼 → 人格卡 display_name;任一環拿不到回 None(**不猜、不用 role 頂替**)。
    先查 seat-display.json(顯示投影:綁了人格就有,不管大腦綁沒綁),再退回 bindings.json → cards.json。
    09-27 the maintainer 抓到:a seat 綁了「包租婆」沒綁專案 ⇒ bindings 投影(要 route_key)沒它 ⇒ dm 抬頭印「a seat」,
    而 aib who 印包租婆——同一件事兩個來源。抬頭是顯示,就讀顯示投影。"""
    import json
    import os
    if not isinstance(code, str) or not _CODE_RE.match(code):
        return None
    try:
        with open(_identity_path("seat-display.json", aib_home), encoding="utf-8") as fh:
            disp = (json.load(fh).get("display") or {}).get(code) or {}
        name = disp.get("display_name") if isinstance(disp, dict) else None
        if isinstance(name, str) and name.strip():
            return _well_formed(name.strip())
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(_identity_path("bindings.json", aib_home), encoding="utf-8") as fh:
            binds = json.load(fh).get("bindings") or {}
        uid = (binds.get(code) or {}).get("persona_uid")
        if not uid:
            return None
        with open(_identity_path("cards.json", aib_home), encoding="utf-8") as fh:
            cards = json.load(fh).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return None
    name = (cards.get(uid) or {}).get("display_name")
    return _well_formed(name.strip()) if isinstance(name, str) and name.strip() else None


def _well_formed(name):
    """r1 低(a seat 21:42):括號沒閉合的舊名,右欄會切(seat_persona.well_formed_name),抬頭也要切,不然同一席兩個樣子。
    seat_persona import 了本檔 ⇒ 這裡延後 import 避免循環;載不進來就原樣。"""
    try:
        import seat_persona
        return seat_persona.well_formed_name(name) or name
    except Exception:  # noqa: BLE001
        return name


def display_label(seat_rec, fallback_wid=None, aib_home=None,
                  allow_legacy=True):
    """抬頭用的完整標籤:`@五碼(暱稱)`;沒有暱稱就只有 `@五碼`。

    ★ 這是**顯示**函式。任何要做判斷的地方請改用 code_of()/code_in_label()。"""
    base = label_of(seat_rec, fallback_wid, allow_legacy=allow_legacy)
    # 出站模式下暱稱只附在真正的 UUID 座位碼後面；「量不到」不得借舊碼
    # 查出一個看似完整的身份,否則未知值又會被包裝成正常值。
    code = code_in_label(base)
    nick = nickname_of(code, aib_home) if code else None
    # B4(09-24):橋接席(provider=codexbot)的暱稱固定「CodexBot 桌面」——它綁的是桌面 ChatGPT,人格卡的暱稱
    #   會讓兩邊抬頭都叫同一個名字(`[a seat(a persona card) > a seat(a persona card)]`),模型與人都分不出誰是桌面。
    if isinstance(seat_rec, dict) and seat_rec.get("provider") == "codexbot" and code:
        nick = CODEXBOT_NICK
    if not nick:
        return base
    # 暱稱本身不得含括號(否則解析器與人眼都會被拆錯)
    nick = nick.replace("(", "[").replace(")", "]").replace("(", "[").replace(")", "]")
    return "%s(%s)" % (base, nick)


def code_in_label(label):
    """從標籤取五碼——**唯一**合法的標籤解析法:只看 @五碼,括號一律忽略。"""
    if not isinstance(label, str):
        return None
    m = _LABEL_RE.search(label)
    return m.group(1) if m else None


def same_seat(a, b):
    """兩個標籤是不是同一席。**只比五碼**:暱稱改了不影響任何判斷。

    突變樁:改成比整串字面 → tests/test-seat-address.py 立刻紅。"""
    ca, cb = code_in_label(a), code_in_label(b)
    return bool(ca) and ca == cb


# ---- A:位址半邊改**白名單**(dev4⚓ 二方審跟進 A)------------------------------
# 黑名單(ROLE_LITERALS)的形狀錯了:今天在編的十席擋得住,但 root3/pm4/integ/cap3/
# flt2/dev6 這些**歷史用過與未來新增**的角色一律放行——**每多一個角色就多一個洞,
# 而且沒有人會收到通知**。這正是「規則要附動作」的反面:一條靠人記得維護的清單。
# 白名單反過來:位址半邊只准長成這三種形狀,其餘一律拒,新角色名自動被擋。
_ADDR_CODE_RE = re.compile(r"^@[0-9a-z]{4,8}(?:[((][^))]*[))])?$")
# ★ EH.17 甲:窗號**已從白名單移除**。常數留著只為了讓「把它加回去」是一個
#   刻意動作而不是重寫一行正則;`address_ok()` 不再引用它。
#   加回白名單會讓 tests/test-eh17-address-uuid.py 的 N1 轉紅。
_ADDR_WID_RE_REMOVED_EH17 = re.compile(r"^@[0-9]{1,10}$")
# ★ 人類發送席目前是 `human:$USER`,**零憑證**:任何艙外行程只要 $USER 對,抬頭就是
#   「人類 the maintainer」,而全艦紀律建立在「the maintainer 本人說的才算授權」。今天能忍是因為艙外就是
#   他自己的機器——**那個物理保證是唯一的憑證**,遠端一接上就沒了。
#   工單已入版控(docs/reports/TICKET-remote-console-identity-20260818.md):
#   **人類身分改成可驗證憑證之前,遠端通道不得上線。** 這裡先照現況收下,並標明它是洞。
_ADDR_HUMAN_RE = re.compile(r"^human:[A-Za-z0-9._-]{1,32}$")
# ★ EG.5 第一刀:hub 外的 AI(dm-ctl.sh 的第三格 kind=agent_offhub)。
#   加這一型的**唯一理由**是:在它存在之前,艦外 AI 只能借 `human:` 這一型上路,
#   於是收件端看到的每一則艦外訊息都自稱人類。多一型不是放寬——是把原本
#   偽裝成 human 的那條流量,趕到一個叫得出名字的格子裡。
#   ★ 名字部分現在用不到:dm-ctl 只發 `agent:offhub` 這一個值(抬頭不帶送方自選名)。
#     這裡仍允許一般名字,是為了第二刀(憑證後才配得上具名)不必再動白名單;
#     而**角色字面照樣被 assert_address_whitelist 的第二層擋掉**——`agent:root-brain`
#     命中 root 即拒(test_agent_form_cannot_wear_a_role_name 釘住)。
_ADDR_AGENT_RE = re.compile(r"^agent:[a-z0-9][a-z0-9._-]{0,31}$")
# ★ 靶心修正(艦長 00:08):身份完全推導不出來時的低權格。
#   它取代了舊的 `human:$USER` 兜底——舊兜底的問題不是名字不對,是**方向不對**:
#   證明不了的時候給出的是權限最高的標籤。這一型的存在就是為了讓「不知道」
#   有地方可寫,不必借 human 的殼。
# ★ 09-05:加上可選的 `(暱稱)`,與上面 `_ADDR_CODE_RE` 的 `@五碼(暱稱)` **同一個形狀**。
#   the maintainer 令「這個要有一個 i18n 國三生白話的名字」——機器名留著(可 grep、可反查),
#   白話名放括號裡給人看。不是新概念,是把既有的暱稱形狀套到這一型上。
#   暱稱允許非 ASCII(中日韓);切壞抬頭的字元由 sender_origin.FRIENDLY_FORBIDDEN 擋在產出端。
# ★ 09-27:祖先鏈量不到席、但呼叫端遞來的完整 session_uuid 對得上**唯一一個活席**時的中間格。
#   它刻意**不是** `@五碼`:那一格的意思是「量到的席」,借它的形狀等於把自稱裝成量到,
#   而收件端結構上分不出來。括號裡的「自稱」是**結構性標記不是措辭** —— 釘在白名單裡,
#   下一個覺得「統一用 @ 比較整齊」的人會撞到這條規則,而不是靜靜把一個區分改掉
#   (未結案 27 的形狀:只靠措辭分開的兩態,守得住相同、守不住相近)。
_ADDR_SEAT_CLAIMED_RE = re.compile(r"^seat_claimed:[0-9a-f]{5}[(（]自稱[)）]$")
_ADDR_UNATTESTED_RE = re.compile(r"^unattested:[a-z0-9][a-z0-9._-]{0,31}(?:[((][^()（）>/]*[))])?$")

_ORIGIN_PLAIN_CACHE = None


def origin_plain_names():
    """i18n `aib.sender.origin.*` 四語值的封閉集合。從字典導出,不寫死。

    09-10 the maintainer 令:抬頭可只印白話名,白名單必須恰好認這些值;其他裸名仍拒。
    """
    global _ORIGIN_PLAIN_CACHE
    if _ORIGIN_PLAIN_CACHE is not None:
        return _ORIGIN_PLAIN_CACHE
    names = set()
    here = os.path.dirname(os.path.abspath(__file__))
    i18n_dir = os.path.abspath(os.path.join(here, "..", "..", "i18n"))
    prefix = "aib.sender.origin."
    # 10-07(the maintainer 同意):改走 i18n_lib.prefixed(讀法、略過規則與原本相同)⇒ 測試沿用只看這一組鍵,
    #   字典裡別的句子改了,用到這裡的測試不必整批重跑。
    if here not in sys.path:
        sys.path.insert(0, here)
    import i18n_lib
    for loc in ("zh-TW", "en", "ja", "ko"):
        data = i18n_lib.prefixed(prefix, loc, i18n_dir=i18n_dir)
        for key, value in data.items():
            if not isinstance(key, str) or not key.startswith(prefix):
                continue
            if not isinstance(value, str):
                continue
            text = " ".join(value.split())
            if not text or text.startswith("!!"):
                continue
            names.add(text)
    _ORIGIN_PLAIN_CACHE = frozenset(names)
    return _ORIGIN_PLAIN_CACHE


# 來源白話名 + 可選 Agent 修飾:「GrokBot (Ez2 第二大腦)」。
# 主名必須仍是 i18n 白話封閉集;括號內禁 >/[]()（）與控制字元。
_ORIGIN_QUALIFIED_RE = re.compile(
    r"^(.+?) [(]([^()（）>/\[\]]+)[)]$"
)
_ORIGIN_QUALIFIER_FORBIDDEN = set(">/[]()（）")


def address_ok(token):
    """位址 token 是否合法。白名單:@五碼(暱稱可選) / human:名 / agent:名 / unattested:名 /
    seat_claimed:五碼(自稱) / 來源白話名[(Agent名)]。

    ★ EH.17 甲:**窗號已移除**。它在多艙下不唯一,而它長得像一個合法位址 ——
      失效形狀是「送到了、抬頭掛別人的名字」,而那不會報錯。
    ★ 這裡管的是**抬頭裡的位址**；B11 同時在 dm-ctl 收掉窗號目標,
      兩層都只接受 UUID 座位碼,不得再以「解析層不同」恢復舊入口。
    """
    if not isinstance(token, str):
        return False
    t = token.strip()
    if (_ADDR_CODE_RE.match(t)
            or _ADDR_HUMAN_RE.match(t) or _ADDR_AGENT_RE.match(t)
            or _ADDR_UNATTESTED_RE.match(t)
            or _ADDR_SEAT_CLAIMED_RE.match(t)
            or t in origin_plain_names()):
        return True
    m = _ORIGIN_QUALIFIED_RE.match(t)
    if not m:
        return False
    base, qual = m.group(1).strip(), m.group(2).strip()
    if base not in origin_plain_names():
        return False
    if not qual or len(qual) > 40:
        return False
    if _ORIGIN_QUALIFIER_FORBIDDEN & set(qual):
        return False
    if any(ord(ch) < 32 for ch in qual):
        return False
    return True


def header_addresses(header):
    """從 `[A > B / 台北 …]` 取出兩個位址 token;取不出來回 []。

    ★ 只切位址那一半:`/ 台北 …` 之後是時間,不參與判定。"""
    if not isinstance(header, str):
        return []
    m = re.match(r"^\[([^\]]*)\]", header.strip())
    body = m.group(1) if m else header
    body = body.split(" / ")[0]
    parts = [p.strip() for p in body.split(">")]
    return [p for p in parts if p]


def assert_address_whitelist(header, where="dm 抬頭"):
    """白名單閘:抬頭的每個位址 token 都必須合法,否則拋 ValueError。

    比 assert_no_role_literal 嚴:**新角色名不必登記就自動被擋**。"""
    toks = header_addresses(header)
    if not toks:
        raise ValueError("%s 取不出位址(形狀不符 `[A > B / …]`):%r" % (where, header))
    for t in toks:
        if not address_ok(t):
            raise ValueError(
                "%s 位址 %r 不合白名單(只准 @五碼[(暱稱)] / human:名 / agent:名 / "
                "unattested:名 / seat_claimed:五碼(自稱)):%r"
                % (where, t, header))
        # ★★ 白名單**單獨不夠**,兩層要一起(dev4⚓ 建議反轉成白名單,實測發現的補充):
        #   `@dev5` 會通過碼形——`dev5` 剛好是合法的 base36 四碼。純白名單擋得住
        #   未知**形狀**,擋不住「剛好長得像碼的角色名」。所以碼形過了之後,
        #   位址本體再過一次角色字面。合法五碼不受影響:`pm123` 的 pm 後面接數字,
        #   有邊界所以不命中(由 test_a_code_that_happens_to_contain_a_role_substring 釘住)。
        hit = contains_role_literal(t)
        if hit:
            raise ValueError(
                "%s 位址 %r 是角色名冠上 @ 冒充的(命中 %r):%r" % (where, t, hit, header))
    return header
