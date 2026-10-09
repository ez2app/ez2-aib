# SPDX-License-Identifier: Apache-2.0
"""EX.26 · `ctx-emit` 一行的文法(**寫端與讀端共用的唯一一份**)。

★ 為什麼抽出來(`a seat` 09:47 提、艦長 09:51 裁「`a seat` 做,只搬不改」):
  這份文法原本存在兩個地方 —— 寫端 `ctx_emit_log.build_row()` 的就地驗形、
  讀端 `tools/probes/ctx-emit-rollup.py` 的 `validate()`。
  **兩端各有一份文法就是債**,而分岔的那天沒有任何位置會叫:
  同一行在一端被拒、在另一端被收,兩邊都覺得自己對。

★★ 而「只搬不改」在字面上做不到,因為**兩端今天就已經分岔了**(`a seat` 抽之前量的):

      寫端不帶 hub/instance   → 寫端放行,讀端判「缺必填欄:hub」
      site 給四值以外的字串    → 寫端放行,讀端判「site 不在五值內」
      完整正常行              → 兩端一致

  抽成單源必須選一份。選的是**讀端那份**,理由是它才符合 09:36 定版的契約
  (`hub`/`instance` 是「巡查跑在誰的殼裡不該改變答案」那一條要求寫進紀錄的;
  `site` 是「四個產生端失效方式不同,不得壓成一個總數」那一條要求必填的)。
  ★ 後果要講明白:**寫端因此變嚴,它不再寫得出那兩種行**。
    那不是回歸 —— 那兩種行本來就會被讀端判成壞行、讓整輪落 unknown,
    差別只在**現在擋在寫出去之前,而不是擋在讀回來之後**。

★ 先例:EX.20 的稱呼位置文法抽成 `src/messaging/vocative_lint.py`,
  `check_labels.py` 與 `dm-ctl.sh` 兩個消費者共用一份(艦長當時裁「文法單源」)。
★ 反例(本鏈上還活著的):佔位符文法散在四個檔,而 `aib-dm.sh` 的 BEFORE 與 AFTER
  **在同一支檔案裡**就已經是兩份 —— 一份看得見裸的那一型,一份看不見。
  **那四份也曾經逐格一致過。**
"""

SCHEMA = "ctx-emit/v1"
SCHEMA_UNATTR = "ctx-emit-unattributed/v1"
UNATTR_FILE = "unattributed.jsonl"
KINDS = ("emit", "resolve", "trend", "heartbeat")
SITES = ("label_of", "match_code", "voice_body", "binding_key", "heartbeat")
REQUIRED = ("schema", "at_ms", "kind", "seat", "site", "addr", "anywhere",
            "hub", "instance")


def validate(obj, unattributed=False):
    """回 None(好行)或一句話(壞行的理由)。★ 只回一個理由,但**先檢查最結構性的**。"""
    want = SCHEMA_UNATTR if unattributed else SCHEMA
    required = tuple(k for k in REQUIRED if k != "seat") if unattributed else REQUIRED
    if not isinstance(obj, dict):
        return "不是物件"
    if obj.get("schema") != want:
        return "schema 不是 %s(實得 %r)" % (want, obj.get("schema"))
    if unattributed and "seat" in obj:
        # ★ 反向也要擋:unattributed 檔裡出現 seat 欄,表示上游把兩種狀態又合回去了。
        return "unattributed 的行不得有 seat 欄(兩種東西兩個 schema,不共用欄位)"
    missing = [k for k in required if k not in obj]
    if missing:
        return "缺必填欄:%s" % ",".join(missing)
    # ★ bool 是 int 的子類:True 會通過 isinstance(x, int)。明寫排除,
    #   否則 `"addr": true` 會被當成 addr=1 而且沒有任何位置會叫。
    for k in ("at_ms", "addr", "anywhere", "instance"):
        v = obj[k]
        if isinstance(v, bool) or not isinstance(v, int):
            return "%s 必須是 int(實得 %r);字串型的數字當壞行,不轉型" % (k, v)
    if obj["addr"] < 0 or obj["anywhere"] < 0:
        return "計數不得為負"
    if obj["kind"] not in KINDS:
        return "kind 不在四值內:%r" % (obj["kind"],)
    if obj["site"] not in SITES:
        return "site 不在五值內:%r" % (obj["site"],)
    if not unattributed and (not isinstance(obj["seat"], str) or len(obj["seat"]) < 8):
        return "seat 必須是完整 session_uuid(五碼是顯示標籤,不是身份)"
    if obj["kind"] == "heartbeat" and (obj["addr"] or obj["anywhere"]):
        return "heartbeat 的計數必須為 0,否則這一行自相矛盾"
    if unattributed and obj["kind"] == "heartbeat":
        # ★ 寫端 `a seat` 80dfb10 同一格 raise,讀端跟上 —— **兩端不得各有一份文法**。
        #   理由是定義上的:heartbeat 的用途是替某一席簽到,而未歸屬的筆
        #   **不得減少 missing_seats**(不知道是誰,就不能替任何人簽到)。
        #   允許它只會長出零資訊的行,並逼讀端為它寫一條例外。
        #   未歸屬的 trend(addr=0、anywhere>0)照樣合法 —— 那一種有資訊。
        return "未歸屬的筆不得是 heartbeat(心跳是替某一席簽到,而未歸屬不能替任何人簽到)"
    return None
