#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib left|center|right|footer on|off` —— 座艙四格的顯示開關(設定層)。

the maintainer 2026-09-05 原話:「aib 有 左/中/右/下 四個視窗,一樣可以用 aib left on/off、
aib right on/off、aib center on/off、aib footer on/off 來開關,並且可以記憶,
下次啟用時是一致的。」

## 名詞對碼(SPEC §一,現查 cockpit-full.sh,不另取名)

    the maintainer 說的     tmux 的 @aib_role     跑什麼
    left          left                  pane-left-tabs.sh
    center        hub                   AI 對話所在
    right         right                 pane-right-tabs.sh(AI 清單)
    footer        bottom                pane-bottom-tabs.sh

★ CLI 字面照 **the maintainer 的用字**(`center` / `footer`),`@aib_role` 照**碼裡既有的**
  (`hub` / `bottom`),兩者用 `ROLE_OF` 對映。統一成一邊都會壞:統一成 role
  名 the maintainer 打不出他自己講的那個指令;統一成 the maintainer 的字則要動 tmux 那一層的既有標記。
  ⇒ **兩個名字各自對一個讀者,合併會同時弄壞兩邊。**

## 這一支**只管設定**,不建也不殺任何 pane

啟動器與 `--print-plan` 讀它(第 3、4 件);本檔零 tmux 呼叫。
分開的理由是可測性:設定的正確性可以在沒有 tmux 的地方逐格證,
而「建不建得出來」要另一組完全不同的證據。

## ★★ 預設值:只開 center+right —— 這是一個**會被使用者看到的行為變更**

the maintainer 更正一(艦長 09-05 裁):缺設定檔 = 只開 center+right,`left`/`footer` 關。

**這與「缺檔 = 今天的座艙」不再相等**,而且方向是減的:
今天所有**從未設定過**的使用者(也就是全部)下一次開艙會**少掉左欄與下欄**。
派工單原文第 2 點寫的是「缺檔 = 四格全開 = 今天的座艙」,更正一把它改了但沒有
寫出這個後果。★ 我照裁示實作,並把後果寫在這裡與交件收據裡 ——
**沒寫下來的話,它會以「使用者的座艙壞了」的形狀回報上來。**
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pane_view  # noqa: E402  設定的讀寫機制:與 ai_view 共用同一份
from i18n_lib import Translator  # noqa: E402

SCHEMA = "aib-panes/v1"
FILENAME = "panes.json"
#: ★ 覆寫變數**一個檔一個**:`EZ2_AIB_AI_VIEW` 是一條完整檔路徑,指不到 panes.json。
#: ★ 新增覆寫變數要**同時**進 `cabin_resolve.sh:aib_cabin_env_names()` ——
#:   L3 就是漏了那一步,`test-cabin-single-source-names` 在合入樹上紅了一次
#:   (09-05 `52d3fbda` 補的)。本檔連同那一行一起交。
OVERRIDE_ENV = "EZ2_AIB_PANES"

#: 缺檔時的值。見檔頭:**不等於今天的座艙**,是 the maintainer 更正一指定的新預設。
DEFAULTS = {"left": False, "center": True, "right": True, "footer": False}
#: CLI 字面 → tmux `@aib_role`。呼叫端要 role 名時問這裡,不要在別處再抄一份。
ROLE_OF = {"left": "left", "center": "hub", "right": "right", "footer": "bottom"}
KEYS = tuple(DEFAULTS)
ON, OFF = "on", "off"


def config_path(env=None):
    return pane_view.config_path(FILENAME, OVERRIDE_ENV, env)


def load(path=None, env=None):
    """回 `(values, warning)`;`warning` 由呼叫端決定音量,不由這裡決定有沒有。"""
    path = config_path(env) if path is None else path
    return pane_view.load(path, DEFAULTS, "panes", "預設版面(中欄+右欄)")


def save(values, path=None, env=None):
    path = config_path(env) if path is None else path
    return pane_view.save(values, path, SCHEMA, DEFAULTS)


def visible_roles(values):
    """回**照建格順序**的 `@aib_role` 清單(給啟動器與 --print-plan 用)。

    ★ 順序不是排版:四格是一棵有向的樹(SPEC §四),錨要先在才切得出下一格。
      所以這裡固定回 `left → bottom → hub → right` 的既有建格序,
      呼叫端再依實際在場的格決定錨(那是第 3 件的事,不在本檔)。
    """
    order = ("left", "footer", "center", "right")
    return [ROLE_OF[k] for k in order if values.get(k, DEFAULTS[k])]


def expected_roles(env=None, path=None):
    """回 `(roles, warning)` —— **設定說**座艙應該有哪幾格(`@aib_role`,建格順序)。

    這是第 4 件(自癒判準改讀設定)要吃的輸入,先做成函式**不接線**
    (艦長 09-05 裁:讀設定那段先寫在這裡)。

    ★★ 它回答的是「**應該**有哪些格」,不是「**現在**有哪些格」——
       兩者在建格中途必然不一致。判「座艙壞了沒有」要拿它跟**實際**的
       `list-panes` 比;而 `cockpit-resize.sh` 用的是**實際**那一邊
       (它只能動真的存在的格),兩支問的是不同的問題,不要互相代用。

    ★ 壞檔的告警照樣往上傳,由呼叫端決定音量 —— 一個「我讀不到你的設定」
      的狀態如果靜默退預設,自癒判準就會拿**預設**去判一個使用者其實設過的座艙。
    """
    values, warning = load(path, env)
    return visible_roles(values), warning


def _mark(tr, flag):
    return tr.t("aib.panes.shown" if flag else "aib.panes.hidden")


def main(argv, env=None, out=None, err=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    tr = Translator(env.get("EZ2_AIB_LOCALE"))
    path = config_path(env)

    if argv and argv[0] == "--expected-roles":
        # 給 shell 用的機器介面(`cockpit-full.sh` 之後會叫它),一行一個 role。
        # ★ 不經 `aib`:`aib` 的 case 只認四個視窗名與 `panes`,`--expected-roles`
        #   會落到啟動器自己的位置參數解析。這是**刻意**的 —— 它不是使用者指令。
        # ★ 不另外提供 `--expected-count`:數量由行數導出,兩個出口會分岔。
        if len(argv) > 1:
            err.write(tr.t("aib.panes.too_many_args",
                           {"extra": " ".join(argv[1:])}) + "\n")
            return 2
        roles, warning = expected_roles(env, path)
        if warning:
            err.write(warning + "\n")
        for role in roles:
            out.write(role + "\n")
        return 0

    if argv and argv[0] == "panes" and len(argv) > 1:
        # ★ `aib panes left off` 看起來完全像是在設定,而它其實只是**查詢**。
        #   實測踩到:不擋的話它會印四行現值然後 rc=0,使用者以為設好了而檔沒動 ——
        #   「做了但沒生效」與「查了一下」在畫面上逐字相同。
        #   設定要走 `aib left off`(四個視窗名各自是入口)。
        err.write(tr.t("aib.panes.too_many_args",
                       {"extra": " ".join(argv[1:])}) + "\n")
        err.write(tr.t("aib.panes.usage") + "\n")
        return 2

    if not argv or argv[0] == "panes":
        values, warning = load(path, env)
        if warning:
            err.write(warning + "\n")
        for key in KEYS:
            out.write(tr.t("aib.panes.status_%s" % key,
                           {"value": _mark(tr, values[key])}) + "\n")
        out.write(tr.t("aib.panes.status_path", {"path": path}) + "\n")
        return 0

    key = argv[0]
    # ★ 可達性註記(不要刪):經 `aib <字>` 進來時,不在五個字面內的第一個字
    #   **到不了這裡** —— `aib` 的 case 沒攔到就會落到它自己的位置參數解析,
    #   由啟動器印它的用法並回 rc=2。所以下面這條是**直接呼叫 panes.py**
    #   (臂與人手)時的出口,不是 `aib` 使用者會走到的路。
    #   兩條路都回 rc=2,但訊息不同 —— 報告裡不得把它們寫成同一件事。
    if key not in KEYS:
        err.write(tr.t("aib.panes.bad_key",
                       {"key": key, "keys": " ".join(KEYS)}) + "\n")
        err.write(tr.t("aib.panes.usage") + "\n")
        return 2
    if len(argv) < 2:
        err.write(tr.t("aib.panes.usage") + "\n")
        return 2
    if len(argv) > 2:
        # 多餘引數不靜默吃掉:`aib left off right off` 看起來像一次設兩個。
        err.write(tr.t("aib.panes.too_many_args",
                       {"extra": " ".join(argv[2:])}) + "\n")
        err.write(tr.t("aib.panes.usage") + "\n")
        return 2
    word = argv[1]
    if word not in (ON, OFF):
        err.write(tr.t("aib.panes.bad_value",
                       {"value": word, "values": "%s/%s" % (ON, OFF)}) + "\n")
        err.write(tr.t("aib.panes.usage") + "\n")
        return 2

    values, warning = load(path, env)
    if warning:
        err.write(warning + "\n")
    wanted = dict(values)
    wanted[key] = (word == ON)
    # ★★ 至少留一格(派工單第二節 5)。**擋在這裡,不在 shell**:
    #    與壞值走同一條出口,rc 與「一個位元組都不寫」的保證只有一份。
    #    理由不是美觀 —— 一個空的 tmux 視窗跟壞掉的座艙在畫面上逐字相同,
    #    而使用者不會知道那是自己關掉的。
    if not any(wanted.values()):
        err.write(tr.t("aib.panes.last_one", {"key": key}) + "\n")
        return 2

    save(wanted, path, env)
    out.write(tr.t("aib.panes.set_ok",
                   {"key": key, "value": _mark(tr, wanted[key]),
                    "path": path}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
