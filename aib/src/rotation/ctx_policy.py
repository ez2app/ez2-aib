# SPDX-License-Identifier: Apache-2.0
"""CTX(對話用量 %)門檻的單一資料源讀取器 —— 只有一張表。

★ 10-15 維護者 20:0x「把這二個合成一個,並要確定會由程式觸發」、20:1x 裁「只保留 A 一張表」:
  原本有兩張表 —— A(各席對話用量觸發,散在碼裡:auto_distill_hook 寫死 30/50/70/85/90、
  80/95 交給開源版沒有的 shell、85 只寫在開席單)與 B(本檔的 stages / actions:50/60/75/80/85/90/96/97,
  艦隊巡檢與輪替在讀)。現在 policy/ctx-thresholds.json **只放 A 的 7 格**(`triggers`),
  B 的獨立數字全部刪掉:
  - 一定要一個數字的讀者改讀這張表的某一格(`trigger_pct(kind)`):
    派工閘 / 開新工 → `handoff_remind`(80);交接地板與交接建議 → `handoff_now`(85)。
  - B 獨有的動作(找接班 50、存檔 60、共治 75、增員 80、顧問停止 96、最後一動作 97)沒有格子可讀 ⇒
    那幾段不再自動做(各呼叫端註明)。

★ **本模組不提供預設值,這是刻意的。** 表缺、格子壞 ⇒ raise(帶 GUARD_MARK)。
  給預設值等於把「資料檔缺鍵」翻譯成「一切正常」——
  那正是本艦記過的形狀:**失效值等於它表示安全的那個值**。

表的形狀(每格一個用量 %,做的事是這一席自己的 hook 注入的動作;見 aib/docs/CTX-THRESHOLDS.md):
  {"pct": 85, "do": ["tidy", "handoff_now"]}
動作 → 給 AI 的四語字串鍵在 `texts`(`key` = Claude 注入 / agy;`dm_key` = 非 Claude 席 aib dm 給自己;
`again_key` = 85 那格 verify 沒過時每則再提醒)。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
# repo 根 = src/rotation/../.. ;測試可用 EZ2_AIB_CTX_POLICY 指到別份。
_DEFAULT_PATH = os.path.normpath(
    os.path.join(_HERE, "..", "..", "policy", "ctx-thresholds.json"))

# ★ 守衛標記:這道守衛拋出的每一個錯誤都帶著它,判準 **import 這個常數**去斷言,
#   而不是在測試裡寫死一份訊息字面(EH.28 K4 補強)。
GUARD_MARK = "ctx-thresholds-guard"

# 動作種類(由輕到重)。順序就是嚴重度:同一則跨過好幾格時,交接類只講最重的那一句。
TRIGGER_KINDS = (
    "tidy",            # 這則回完跑 aib memory tidy(靜默精煉)
    "handoff_remind",  # 一行交接提醒(艦隊派工閘:到這格不再派新工)
    "handoff_now",     # 先做完收尾三步(tidy → summary → handoff + verify)再做別的;verify 沒過每則再提醒
    "red_line",        # 停止新工作、立刻交接
)
TEXT_FIELDS = ("key", "dm_key", "again_key")


def policy_path():
    """實際會讀的那個檔。**回的是絕對路徑,讓呼叫端印得出來。**"""
    return os.environ.get("EZ2_AIB_CTX_POLICY") or _DEFAULT_PATH


def _fail(kind, msg, *args):
    return kind("%s ctx-thresholds %s" % (GUARD_MARK, msg % args))


def load(path=None):
    """讀整份 policy 並驗表。壞檔 / 缺檔 / 表壞一律 raise —— 不 fail-open。"""
    p = path or policy_path()
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise _fail(ValueError, "不是 object:%s", p)
    rows = data.get("triggers")
    if not isinstance(rows, list) or not rows:
        raise _fail(KeyError, "缺 triggers 表:%s", p)
    texts = data.get("texts")
    if not isinstance(texts, dict):
        raise _fail(KeyError, "缺 texts 區段:%s", p)
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or "pct" not in row or "do" not in row:
            raise _fail(KeyError, "triggers[%d] 要有 pct 與 do:%s", i, p)
        pct, do = row["pct"], row["do"]
        # bool 是 int 的子類,先擋掉:True 會安靜地變成 1。
        if isinstance(pct, bool) or not isinstance(pct, int) or not 0 < pct <= 100:
            raise _fail(ValueError, "triggers[%d].pct 不是 1~100 的 int:%r", i, pct)
        if pct in seen:
            raise _fail(ValueError, "triggers 有兩格都是 %d%%(一個用量只能一格):%s", pct, p)
        seen.add(pct)
        if not isinstance(do, list) or not do or len(set(do)) != len(do):
            raise _fail(ValueError, "triggers[%d].do 要是不重複的非空清單:%r", i, do)
        for kind in do:
            if kind not in TRIGGER_KINDS:
                raise _fail(ValueError, "triggers[%d].do 有不認得的動作 %r(登記表 %s)", i, kind,
                            list(TRIGGER_KINDS))
            t = texts.get(kind)
            if not isinstance(t, dict) or not isinstance(t.get("key"), str) or not t["key"]:
                raise _fail(KeyError, "texts.%s 缺 key:%s", kind, p)
            for f in TEXT_FIELDS:
                if f in t and (not isinstance(t[f], str) or not t[f]):
                    raise _fail(ValueError, "texts.%s.%s 不是字串:%r", kind, f, t[f])
    return data


def cells(path=None):
    """→ `({"pct", "do"}, ...)`,依 pct 由低到高。"""
    data = load(path)
    return tuple(sorted(({"pct": r["pct"], "do": tuple(r["do"])} for r in data["triggers"]),
                        key=lambda c: c["pct"]))


def triggers(path=None):
    """攤平成觸發格 → `({"pct", "kind", "key", 可選 "dm_key", "again_key"}, ...)`,依 pct 由低到高、同格照嚴重度。
    auto_distill_hook 只讀這一支。"""
    data = load(path)
    out = []
    for c in sorted(data["triggers"], key=lambda r: r["pct"]):
        for kind in sorted(c["do"], key=TRIGGER_KINDS.index):
            t = data["texts"][kind]
            row = {"pct": c["pct"], "kind": kind}
            row.update({f: t[f] for f in TEXT_FIELDS if f in t})
            out.append(row)
    return tuple(out)


def trigger_pct(kind, path=None):
    """某個動作在表上的(最低)那一格;例:`trigger_pct("handoff_now")` = 85。表上沒有那種動作 ⇒ raise。
    艦隊那邊一定要一個數字的讀者(派工閘、交接地板)只准經這一支取。"""
    if kind not in TRIGGER_KINDS:
        raise _fail(KeyError, "不是登記過的動作:%r(登記表 %s)", kind, list(TRIGGER_KINDS))
    hits = [r["pct"] for r in triggers(path) if r["kind"] == kind]
    if not hits:
        raise _fail(KeyError, "triggers 表上沒有 %s 這格", kind)
    return min(hits)


def stages_descending(path=None):
    """→ `((門檻, 動作), ...)` 由高到低,`ok`(0)殿後;只列會改變艦隊對這一席做法的動作
    (交接提醒 / 要求交接 / 紅線)—— 精煉格只是這一席自己整理記憶,艦隊照常派。
    `lifecycle.STAGES` 用這一支產,不再自己寫一份。"""
    rows = []
    for k in TRIGGER_KINDS:
        if k == "tidy":
            continue
        try:
            rows.append((trigger_pct(k, path), k))
        except KeyError:
            continue                 # 表上沒有這種動作 ⇒ 沒有這一階(不補數字)
    return tuple(sorted(rows, key=lambda r: (r[0], TRIGGER_KINDS.index(r[1])), reverse=True)) + ((0, "ok"),)


def unmeasured_every(path=None):
    """量不到用量的席每幾輪精煉一次(`unmeasured_tidy_every_turns`;維護者 10-15 22:4x)。缺鍵 / 不是正整數 ⇒ raise
    (同本模組:不給預設值)。auto_distill_hook(API 席 outer_turn、bot_turn)與 api_tidy 只經這一支取。"""
    data = load(path)
    n = data.get("unmeasured_tidy_every_turns")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise _fail(KeyError, "unmeasured_tidy_every_turns 要是正整數:%r", n)
    return n
