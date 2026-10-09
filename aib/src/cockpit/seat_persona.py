# SPDX-License-Identifier: Apache-2.0
"""席位人格名的**唯一**取值與消毒來源。

為什麼要有這個檔:08-14 the maintainer 令「顯示層改用綁定的人格、拿掉角色文字」,而
顯示人格的地方有兩處——右欄(`pane-right-ai.sh`)與艦長列(`nav/captains-ctl.sh`)。
交接單第三節 (B) 的硬性條件是「人格取值與右欄同源、不得另立來源」。
兩處各寫一份等值的判斷**不算同源**:它們今天等值,明天改一邊就分岔,而分岔
的症狀是「同一席在兩個地方顯示不同名字」——那種 bug 沒有人會去比對。

同族前例:席位命名本來就已經在右欄與艦長列分家(0a5b3 交接單第三節記
「the maintainer 改一次要改兩次」)。本檔是把那兩處併回同一個來源的第一步。
"""

import json
import os
import re
import seat_address


# 控制字元進畫面即注入面。刪字元會留下 ']0;pwn' 這種誤導殘文,所以命中=
# 整欄丟棄,與「整筆拒」同哲學(原註解在 pane-right-ai.sh,隨函式一起搬來)。
CTRL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')

SEAT_SCHEMA = 'aib-seat/v1'
PERSONA_LIMIT = 24


def clean_text(text, limit):
    """外部字串進畫面的同注入面(描述/agentType/人格名)。

    非字串、含控制字元、命中 verified 語意 denylist=回空字串(丟該欄不丟整列)。
    """
    if not isinstance(text, str):
        return ''
    if CTRL_RE.search(text):
        return ''
    text = text.strip()
    if 'verified' in text.lower():
        return ''
    return text[:limit]


def load_display(path=None):
    """讀席位顯示投影(`binding_projection.py --display-out` 的產物)。

    ★ **為什麼顯示層改讀投影,而不是讓投影去寫席位檔的 `persona_card`**:
    後者會讓席位檔變成人格的第二個存放處,而正本在 `identity/agents/`
    的綁定層——兩份遲早分岔,而分岔的症狀是「畫面顯示的人格跟綁定的不同」,
    那種 bug 沒有人會去比對。**改讀端不改寫端。**

    ★ 路徑刻意**不吃 `EZ2_AIB_INSTANCE_HOME`**:投影是全艦隊一份,不是每艙
    一份。右欄只畫自己那一艙的席位,但它查的那張表是共用的。

    缺檔=回空 dict(還沒跑過投影是合法狀態,不是錯誤);壞檔也回空,
    因為顯示層寧可少顯示一行,也不要拿半信半疑的值去畫。
    """
    if path is None:
        path = os.environ.get('EZ2_AIB_SEAT_DISPLAY')
    if path is None:
        root = os.environ.get('EZ2_AIB_HOME') or \
            os.path.expanduser('~/.ez2-aib')
        path = os.path.join(root, 'identity', 'seat-display.json')
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    entries = data.get('display') if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def display_key(rec):
    """讀顯示投影要用的鍵。**這是本檔對外唯一的鍵推導處。**

    ★ EH.17 3b-i:投影的鍵已換源到 `seat_address.code_of`(全庫唯一末五碼取法),
      所以讀端也必須用同一支算。拿不到碼時退回 `seat_id`,與投影端的補位同形 ——
      兩邊用同一個規則,才不會出現「投影寫得進去、畫面查不到」那種靜默落空。
    ★ **這裡不出聲**:出聲的是產生端(投影器)。讀取端每查一次就喊一次的話,
      `kind=emit` 的計數會被**讀取次數**放大,而那個計數是翻階段 4 的閘。
    ★ 為什麼放這裡而不是各呼叫點各寫一份:`persona_of` / `folder_of` /
      右欄的 `persona_source` 是**同一份投影的三個消費者**。
      我第一版只改了第三個,於是畫面上人格整個不見了而三支測試才抓到 ——
      **同一份東西的鍵推導必須只有一處。**
    """
    if not isinstance(rec, dict):
        return None
    code = seat_address.code_of(rec)
    if code:
        return code
    seat_id = rec.get('seat_id')
    return seat_id if isinstance(seat_id, str) else None


def display_entry(rec, display):
    """席位紀錄 → 投影裡屬於它的那一筆(取不到回 None)。

    **投影查表只有這一處**:`persona_of` / `folder_of` / 右欄的 `persona_source`
    是同一份投影的三個消費者,鍵推導分三份寫就會分岔成「投影寫得進去、
    畫面查不到」——那正是 3b-i 第一版踩過的。

    ★★ 遷移期**雙認**:先用新鍵(`seat_address.code_of`),查不到再退回
      `seat_id`。理由是量出來的,不是保守:本機
      `~/.ez2-aib/identity/seat-display.json`(mtime 08-21 18:23)32 把鍵
      全是 `seat_id` 形,活席七席的碼一把都不在裡面;而投影**只在綁定時
      重跑**(`bind_one.py:30`),不隨面板刷新重算。
      換鍵當天不雙認 = 每一席的人格行與 📁 行同時空白,直到有人重綁。

    ★★★ 而那個空白**不會有紅燈**:查不到回 None,畫出來是「沒綁人格」,
      與「這席真的沒綁」逐字相同 —— 失效值就是它表示安全的那個值。
      這正是雙認要擋的東西。

    ★ 與艦上 EH.17 既定分階段同形(解析端雙認 → 換顯示 → 停認舊碼);
      階段 4 拿掉的就是下面那個 `fallback` 分支。

    ★ 這裡**不出聲**(與本檔既有紀律一致):出聲的是產生端(投影器)與
      標籤層(`label_of`)。讀取端每查一次就喊一次的話,`kind=emit` 的計數
      會被**讀取次數**放大,而那個計數是翻階段 4 的閘。

    ★ 殘留風險(具名,不假裝沒有):投影已換新鍵、而某席**自己的碼不在檔裡**
      時,若它的 `seat_id` 恰好等於**另一席的碼**,退回那一步會取到別人那筆。
      五碼撞號在本庫是已知會發生的事(右欄的 `label_collision` 就是為它存在)。
      退回只在「自己的碼查不到」時發生,所以新鍵投影下的已綁席不受影響;
      階段 4 拿掉退回之後這個風險歸零。
    """
    if not isinstance(display, dict):
        return None
    key = display_key(rec)
    if isinstance(key, str):
        entry = display.get(key)
        if isinstance(entry, dict):
            return entry
    if isinstance(rec, dict):
        legacy = rec.get('seat_id')
        if isinstance(legacy, str) and legacy != key:
            entry = display.get(legacy)
            if isinstance(entry, dict):
                return entry
    return None


def _bound_field(rec, display, field):
    """從投影取某一欄。取不到回 None(讓呼叫端決定要不要退回席位檔)。"""
    entry = display_entry(rec, display)
    if entry is None:
        return None
    value = entry.get(field)
    return value if isinstance(value, str) else None


def folder_of(rec, display):
    """右欄第三行的 📁:綁定所指路由在契約裡的資料夾。

    ★ **不截斷**——截到幾欄是那個面板的事(三行制設計正本量到 18 欄),
    換一個寬度就不同。這裡回全文,截斷留給畫的人。
    ★ 與人格名走**同一套消毒**(控制字元、`verified` 語意、`@`、`|`),
    因為它同樣是外部字串進畫面,而且同樣會經過 `|` 分隔的列格式。
    """
    value = _bound_field(rec, display, 'folder')
    if value is None:
        return ''
    folder = clean_text(value, 512)
    if '@' in folder or '|' in folder:
        return ''
    return folder


_PROJECT_UID_RE = re.compile(r'[A-Za-z0-9:._@-]{4,128}')


def project_title_of(rec):
    """席位綁的專案卡標題(席位檔 project_uid + project_title;R16:專案 = 第二大腦)。

    09-29 a seat:`aib newproj` 與選單「新開專案」把專案寫在席位檔這兩欄,而右欄 🧠 那一行
    只會畫路由資料夾 ⇒ 專案綁好了畫面仍是「未綁」。這裡把標題交給畫的人。
    ★ 兩欄要一起在:只有標題沒有 uid 不算綁了(標題是給人看的,身份是 uid)。
    ★ 與人格名、資料夾走同一套消毒,並同樣拒 `@` 與 `|`(冒充別席、多切一欄)。
    取不到、髒值 ⇒ 空字串(不顯示不是錯誤)。"""
    if not isinstance(rec, dict):
        return ''
    uid, title = rec.get('project_uid'), rec.get('project_title')
    if not isinstance(uid, str) or not _PROJECT_UID_RE.fullmatch(uid):
        return ''
    if not isinstance(title, str):
        return ''
    text = clean_text(title, 120)
    if not text or '@' in text or '|' in text:
        return ''
    return text


def persona_of(rec, display=None):
    """從席位檔紀錄取人格顯示名;取不到/髒值一律回空字串(不顯示不是錯誤)。

    額外拒兩個字元:
    - '@':否則「假人格@他人五碼」會在畫面上冒充別席身分——艦長列的格式
      正是 `人格@五碼`,不擋等於開放偽造。
    - '|':兩個消費端都用 '|' 當列內欄位分隔(艦長列的 CAPTAIN_ROWS、
      右欄的列格式),人格名裡帶一個就會**多切出一欄**並讓後面全部錯位。
      這是資料進到分隔符格式前該擋的,不是各消費端各自 escape。
    """
    if not isinstance(rec, dict):
        return ''
    value = _bound_field(rec, display, 'display_name')
    if value is None:
        value = rec.get('persona_card')
    if value is None:
        return ''
    persona = clean_text(value, PERSONA_LIMIT)
    if '@' in persona or '|' in persona:
        return ''
    return well_formed_name(persona)


_OPEN_PARENS = '(\uff08'
_CLOSE_PARENS = ')\uff09'


def well_formed_name(name):
    """09-14 判準④「人格名正確:不是描述文、括號閉合」。

    投影(seat-display.json)是別處算好的,舊投影裡還躺著被硬切成
    「a persona card (從LINE,FB對話提取風格」的名字(card_registry 09-14 前的切法);
    畫面這一端**不能等投影重算**才正確 —— 括號沒閉合就把括號起的尾段整段丟掉,
    只留名字。閉合的括號(「小明 (代理)」)原樣保留:那是作者要的。
    """
    if not isinstance(name, str):
        return ''
    opens = sum(name.count(ch) for ch in _OPEN_PARENS)
    closes = sum(name.count(ch) for ch in _CLOSE_PARENS)
    if opens == closes:
        return name
    opens_at = [i for i in (name.find(ch) for ch in _OPEN_PARENS) if i >= 0]
    if not opens_at:
        return name          # 只多了右括號:不猜要切哪裡,原樣
    return name[:min(opens_at)].rstrip() or name


def persona_from_file(path, window_id, display=None):
    """給 shell 呼叫端:讀席位檔並做與右欄相同的 schema/window_id 交叉驗。

    交叉驗不符=當缺席(回空),不是錯誤——server 重啟 ID 重用時舊席位檔會
    錯掛新窗,顯示別席的人格比不顯示更糟。
    """
    try:
        with open(path, encoding='utf-8') as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return ''
    if not isinstance(rec, dict) or rec.get('schema') != SEAT_SCHEMA:
        return ''
    if window_id is not None and rec.get('window_id') != window_id:
        return ''
    return persona_of(rec, display)


if __name__ == '__main__':
    import sys
    args = sys.argv[1:]
    if not args:
        print('usage: seat_persona.py SEAT_FILE [WINDOW_ID]', file=sys.stderr)
        raise SystemExit(2)
    print(persona_from_file(args[0], args[1] if len(args) > 1 else None))
