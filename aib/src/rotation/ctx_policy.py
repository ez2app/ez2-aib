# SPDX-License-Identifier: Apache-2.0
"""EH.28 · CTX 門檻的單一資料源讀取器。

★ 為什麼是一個模組而不是「各處自己 json.load」:各處自己讀的話,
  「少一個鍵」在每一處的失效形狀都不同(有的 KeyError、有的拿到 None、
  有的靜默用預設值),而**靜默用預設值的那幾處不會有紅燈**。
  一個入口才有辦法讓「少一鍵」變成同一種、而且會叫的失效。

★ **本模組不提供預設值,這是刻意的。** 取不到鍵就 raise。
  給預設值等於把「資料檔缺鍵」翻譯成「一切正常」——
  那正是本艦記過的形狀:**失效值等於它表示安全的那個值**。

★ `stages` 與 `actions` 分成兩個命名空間(艦長 09:16 裁):
  `stages.should_converge` 與 `actions.captain_compact` 今天都是 85,
  但它們是兩件事。**值相等不是合併的理由** —— 合併之後,
  改其中一個的意圖會同時改到另一個,而那個牽動沒有紅燈。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
# repo 根 = src/rotation/../.. ;測試可用 EZ2_AIB_CTX_POLICY 指到別份。
_DEFAULT_PATH = os.path.normpath(
    os.path.join(_HERE, "..", "..", "policy", "ctx-thresholds.json"))

# ★ 鍵登記表:**這一份就是「應該有哪些鍵」的正本**。
#   判準(a seat)要比對的兩個集合,一邊是這裡、一邊是 JSON 檔,
#   兩邊都由碼產,不手抄。少一鍵時兩集合不等 → 紅。
# ★ 守衛標記(EH.28 K4 補強,艦長 09:53 裁、a seat 11:1x 催):
#   這道守衛拋出的每一個錯誤都帶著它,判準 **import 這個常數**去斷言,
#   而不是在測試裡寫死一份訊息字面。
# ★ 為什麼:K4 原本只釘「rc 非 0 且訊息點名鍵」,而拔掉守衛之後它仍然全綠 ——
#   因為另一條路徑(`stages_descending()` 直接取鍵)也會拋 KeyError,
#   而 KeyError 的訊息本身就帶鍵名。**它量到的是「有東西壞了」,
#   不是「守衛擋住了」。** 帶標記之後兩者才分得開。
# ★ 為什麼是常數不是字面:兩個檔各寫一份字面,今天一致,而分岔那天沒有位置會叫。
#   今晚同一個形狀已經出現三次(kind 常數 vs 寫死字面 / 閘門白名單目錄名 /
#   佔位符文法四份)—— 那不是三筆債,是同一個形狀出現三次(a seat 語)。
GUARD_MARK = "ctx-thresholds-guard"

STAGE_KEYS = (
    "ok",
    "scout_successor",
    "should_checkpoint",
    "co_rule",
    "should_converge",
    "must_handoff",
    "advisor_stop",
    "claude_final",
)
ACTION_KEYS = (
    "prepare_compact",
    "captain_compact",
    "final_clear_advice_floor",
)


def policy_path():
    """實際會讀的那個檔。**回的是絕對路徑,讓呼叫端印得出來。**"""
    return os.environ.get("EZ2_AIB_CTX_POLICY") or _DEFAULT_PATH


def load(path=None):
    """讀整份 policy。壞檔/缺檔一律 raise —— 不 fail-open。

    ★ 這裡與 `seat_persona.load_display()` 的「缺檔回空 dict」**刻意相反**:
      那一份缺了只是畫面少一行,這一份缺了會讓所有門檻靜默變成 0,
      而 0 表示「每一席都過線」。**同一種寫法在不同語意下是相反的正確。**
    """
    p = path or policy_path()
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("%s ctx-thresholds 不是 object:%s" % (GUARD_MARK, p))
    for ns, keys in (("stages", STAGE_KEYS), ("actions", ACTION_KEYS)):
        sect = data.get(ns)
        if not isinstance(sect, dict):
            raise KeyError("%s ctx-thresholds 缺 %s 區段:%s" % (GUARD_MARK, ns, p))
        missing = [k for k in keys if k not in sect]
        if missing:
            raise KeyError("%s ctx-thresholds %s 缺鍵 %s:%s" % (GUARD_MARK, ns, missing, p))
        for k in keys:
            v = sect[k]
            # bool 是 int 的子類,先擋掉:True 會安靜地變成 1。
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("%s ctx-thresholds %s.%s 不是 int:%r" % (GUARD_MARK, ns, k, v))
    return data


def stage(name, path=None):
    """取一個階段門檻。名字不在登記表裡就 raise(打錯字不會靜默回 None)。"""
    if name not in STAGE_KEYS:
        raise KeyError("%s 不是登記過的 stage 鍵:%r(登記表 %s)" % (GUARD_MARK, name, list(STAGE_KEYS)))
    return load(path)["stages"][name]


def action(name, path=None):
    """取一個動作門檻。"""
    if name not in ACTION_KEYS:
        raise KeyError("%s 不是登記過的 action 鍵:%r(登記表 %s)" % (GUARD_MARK, name, list(ACTION_KEYS)))
    return load(path)["actions"][name]


def stages_descending(path=None):
    """→ `((門檻, 名字), ...)`,由高到低,`ok` 殿後。

    `lifecycle.STAGES` 直接用這一支產,**不再自己寫一份 tuple** ——
    自己寫一份的話,加一階要記得改兩個地方,而漏掉的那次不會有紅燈。
    """
    st = load(path)["stages"]
    # claude_final(97)不是 lifecycle 的階段:它是 clear 前的最後一動作,
    # 由 lifecycle 另外的常數承擔。這裡只回 STAGES 該有的那七階。
    names = [k for k in STAGE_KEYS if k != "claude_final"]
    return tuple(sorted(((st[n], n) for n in names), reverse=True))
