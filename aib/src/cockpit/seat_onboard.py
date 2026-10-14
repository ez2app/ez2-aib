#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib seat onboard — 開席單的「讀到了」閉環(A)與補投(B)。2026-08-16。

## 這一刀在補什麼

開席鏈(`aib`:seat open 尾段)本來就會自動投開席單,但它止於 `aib dm` 的
rc0——而 `aib-dm.sh` 檔頭把自己的界線寫得很清楚:**「本收據只證明按鍵落入
目標 pane,不證明對方已讀或已執行」**。於是整條鏈的終點是「字進了輸入框」,
而開席單尾段要求的那句「已就緒」**沒有任何東西在收**。

兩個後果,都在 2026-08-16 實際發生:

  A 投遞失敗看起來像暫時性問題。`aib-dm.sh` 曾拿 socket 名當 session 名查
    (已修),第二台起(aihub-7 / aihub-8)必然 rc3;而 rc3 的字面是「已送
    未驗——CLI 可能仍在冷啟」,**看起來像等一下就好**,所以沒有人去查。
    那些席位從來沒收到過開席單,而畫面上看不出任何異狀。

  B 模板改了,在席的人不會知道。08-16 一天內開席單改了四次(⚫→🌑、
    `[X]` 裁定、鐵則第 1 條與第 5 條改寫),**每一次都是 root 席一席一席
    手動 DM 通知的**。「載入時必然知道」只解決了新席,沒解決在席。

## 判準與帳本形狀

判準(四態)在 `seat_status.onboard_reason`,與 `unassigned_reason` 同一支
——兩者都是面板與守衛共用的判斷,各留一份遲早分岔成「面板說沒事、守衛說
有事」。本模組只負責採集(掃帳本建索引)與動作(投遞/等回執/入帳)。

★★ 帳本沒有 `seat_onboarded` 這種事件種類,**也不該有**。
`schemas/event.schema.json` 的 `kind` 是封閉集合、註記寫明「恰 15 種,
≤15 上限」。硬加第 16 種等於為了一個功能改動全系統的事件字彙。改用既有
兩種,而且語意剛好對上這一刀要分的那條線:

    message.outbound   我們把開席單投出去了(dm 真 rc0 才記)
    message.inbound    我們在那一席畫面上觀察到約定的回執
    system.note        dm rc=4「稿進了對方輸入框但未送出」那一態(2026-08-22 root 裁
                       「不加第 16 種」):body.subject = {"kind":"message",
                       "id":"onboard:stuck_in_composer:<seat_id>"} 是機器標記,
                       讀端(load_index)靠它分流,不讀 summary 散文。
                       程式裡的邏輯名仍叫 message.stuck_in_composer(record() 映射),
                       帳本上不會出現這個字串——它不在 enum,出現就是 schema 拒。

    correlation_id     seat-onboard:<seat_id>      ← 索引鍵
    body.subject       {"kind": "identity", "id": <seat_id>}
    body.data_ref      {path: 模板相對路徑, sha256: 模板內容雜湊}

`data_ref` 的既有語意是「外置 payload 指標:相對路徑 + 內容 SHA-256;投影
不得依賴未入帳 hash 的檔案內容」——B 要做的正是拿現行模板 sha 去比對已入帳
的 sha,這個欄位是為此存在的,不是借用。

★ actor 一律是本工具(`kind: "system"`),**inbound 也是**。看似該把回執
記成那一席發的,但我們沒有任何東西可以驗證畫面上那行字是誰打的——記成
它發的等於幫它自報。誠實的形狀是:「本工具觀察到 X 席出現回執」,身份在
subject,觀察者在 actor。

## rc 契約

    0  已回執(投遞成功且等到回執;--missing 時=本輪處理的席全數回執)
    2  用法/環境錯(模板不存在、seats 目錄不存在、hub 不可達…)
    3  已投未回執(誠實未解:對方冷啟/沒理會/回了但我們沒認出)

★ 3 不是靜默:它同時在帳本留下一筆 outbound-without-inbound,面板據此
掛 `!`。這正是這一刀的重點——**逾時要留下痕跡,不是回一個沒人看的 rc**。
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "ledger"))

from seat_status import (  # noqa: E402
    ONBOARD_NEVER, ONBOARD_NO_ACK, ONBOARD_STALE, onboard_reason,
    record_legible,
    ONBOARD_LOADING_WINDOW_S,
)
import seat_address  # noqa: E402 -- B11 合法投遞碼只由 code_of() 產生
import open_attempt  # noqa: E402 -- O1 開席碼表:回執到了把 ready 記到那一次開席

TEMPLATE_REL = "docs/templates/SEAT-ONBOARD.md"
CORRELATION_PREFIX = "seat-onboard:"
# O5:這些家在啟動參數裡自取開席單,後面不准再往輸入框 dm。
# 還原刀把這一行改成空集合,入口格必須紅。
SELF_FETCH_PROVIDERS = frozenset(("claude", "codex", "agy", "grok"))  # O5-self-fetch-on
# 橋接席:與 sand_bridge 同後端,不會在 pane 回「已就緒」——outbound 後立即 inbound。
AIBOT_PROVIDERS = frozenset(("sand", "codexbot", "grokbot", "claudebot", "musebot"))
PROVIDERS_JSON = os.path.join(HERE, "providers.json")


def api_key_provider_ids(path=None):
    """providers.json 裡 auth=api_key 的引擎 id。選單通知不投進這些 REPL。"""
    src = path or PROVIDERS_JSON
    try:
        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return frozenset()
    rows = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return frozenset()
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("auth") != "api_key":
            continue
        pid = row.get("id")
        if isinstance(pid, str) and pid:
            out.append(pid)
    return frozenset(out)

# 回執 token。★ 必然也出現在開席單正文裡——所以「畫面上有這些字」不是回執,
# 那是我們自己剛貼上去的字。判準必須是「比投遞當下多了一次」,見 ack_reached。
# the maintainer 2026-09-18:第一次開席＝「已就緒」;之後重投／讀新版＝「已更新」。
ACK_TOKEN = "已就緒"           # 第一次(相容舊名)
ACK_TOKEN_UPDATE = "已更新"     # 第二次起
ACK_TOKENS = (ACK_TOKEN, ACK_TOKEN_UPDATE)
ACK_INSTRUCTION_FIRST = (
    "讀完回覆一句「已就緒」並待命,首任務候派。不要動任何檔。"
)
ACK_INSTRUCTION_UPDATE = (
    "這是開席單更新版。讀完回覆一句「已更新」"
    "（不要回「已就緒」——你早已就緒）。不要動任何檔。"
)

# v0.70.0 第 8 條(3):直接貼的開席單開頭(回執字一律放在「」裡,守望只認裸字,見 count_reply_tokens)。
INLINE_HEAD_PREFIX = "這是開席單"
INLINE_HEAD_FIRST = "這是開席單,讀完回「已就緒」。"
INLINE_HEAD_UPDATE = "這是開席單更新版,讀完回「已更新」(不要回「已就緒」)。"
INLINE_SEP = " / "


def genes_block(text=""):
    """the maintainer 09-30 19:5x「首要基因要到全部 AI,要有開關」:text 前面接上基因(開關關 ⇒ 原樣;已經有 ⇒ 不再加)。
    單一來源 ez2_genes.with_genes;API 席不走這裡(它們每一輪的系統說明已帶 GENE_HEAD)。讀不到模組 ⇒ 原樣。"""
    try:
        import ez2_genes
        return ez2_genes.with_genes(text)
    except Exception:  # noqa: BLE001 —— 基因讀不到不擋開席
        return text


def genes_line(line):
    """一行通知(不載入/逾時)後面接上攤平成一行的基因(同 inline_body 的「 / 」);開關關 ⇒ 原樣。"""
    block = genes_block("")
    if not block:
        return line
    return lint_safe(INLINE_SEP.join([line] + [p.strip() for p in block.splitlines() if p.strip()]))


def persona_block(window, provider, base=None):
    """基因(開著才有)+ 這一席綁的人格包 + 記憶;都沒有或不該由這裡送 ⇒ 空字串。

    09-30 19:5x:基因放最前面(鐵則優先),沒綁人格的席也拿得到;開關在 ez2_genes(aib genes on|off)。
    ★ API 席、橋接席照舊 "":API 席的系統說明已帶基因與人格;橋接席由 bridge_persona 從橋自己的送信路送。
    """
    if provider in AIBOT_PROVIDERS or provider in api_key_provider_ids():
        return ""
    return genes_block(_persona_memory_block(window, provider, base))


def _persona_memory_block(window, provider, base=None):
    """這一席綁的人格包(接在開席單/開席通知後面);沒綁、讀不到、或不該由這裡送 ⇒ 空字串。

    the maintainer 09-26「全部 AI 的開席入口,必要第一個載入對應的人格」;09-30 測試艙 b1af 實測:新開的席完全收不到人格
    (人格包只在「載入舊席位」與 aib newrole --deliver 送)。這裡補「新開」那條。
    ★ API 席不從這裡送:它們每一輪的系統說明已經帶人格(api_persona.py),再貼一次只會變成輸入框裡的稿。
    ★ 桌面 App 橋接席(AIBOT_PROVIDERS)不從這裡送:09-30 19:5x 起由 bridge_persona.py 從橋自己的送信路送一次。"""
    if provider in AIBOT_PROVIDERS or provider in api_key_provider_ids():
        return ""
    try:
        path = os.path.join(base or seats_dir(), "w%s.json" % str(window).lstrip("@"))
        with open(path, encoding="utf-8") as fh:
            uuid = str((json.load(fh) or {}).get("session_uuid") or "")
        if not uuid:
            return ""
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "identity"))
        import persona_pack
        from pathlib import Path as _P
        home = _P(os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib"))
        pack = persona_pack.render_pack(uuid, home)
    except Exception:  # noqa: BLE001 —— 人格讀不到不擋開席
        return ""
    text = pack.get("text") or "" if pack.get("status") == "ok" else ""
    try:                                    # 09-30:接著帶這一席(或它的專案)最新幾則 aib memory
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "memory"))
        import aib_memory
        mem = aib_memory.seat_brief(uuid)
    except Exception:  # noqa: BLE001
        mem = ""
    return (text + ("\n\n" + mem if mem else "")).strip()


def handoff_notice_block(window, base=None):
    """10-01 the maintainer「85% 或準備 clear 必做三步交接(aib handoff),全部 AI、不可跳過」:
    這一席是接續別席開的(席位檔 forked_from = 前身的 session_uuid,接回 / 分出新席都這樣記)而前身最後一輪
    沒有交接收據 ⇒ aib_handoff 自動補抓事實、記缺漏旗標,回一段提醒(接在開席單後面)。沒有前身 ⇒ 什麼都不寫、
    回 ""(新席第一輪的開始點就是席位檔 started_epoch)。任何錯 ⇒ ""(不擋開席;aib_handoff 自己記 log)。"""
    try:
        path = os.path.join(base or seats_dir(), "w%s.json" % str(window).lstrip("@"))
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh) or {}
        new = str(rec.get("session_uuid") or "")
        if not new:
            return ""
        import aib_handoff
        return aib_handoff.predecessor_check(str(rec.get("forked_from") or ""), new,
                                             source="onboard:%s" % (rec.get("provider") or "?"))
    except Exception:  # noqa: BLE001 —— 交接檢查壞掉不擋開席
        return ""


# 通知類投遞(載入舊席位說明、不載入/逾時那一行)的結束碼:0 = 送了(含 dm 3「已送出未確認」);
# ★ review r17:dm 4 = 字已進對方框但沒送出 —— 契約「絕不重送」⇒ 回 ONBOARD_NOTICE_STUCK(5),
#   aib 的 `--timeout-notice || --skipped-notice` 退路只在 3(一個字都沒進去)時才補送;以前 4 也壓成 3 ⇒
#   第二份(1.4KB 帶基因)疊進框裡已卡著的第一份。
ONBOARD_NOTICE_STUCK = 5


def onboard_notice_exit(rc):
    if rc in (0, 3):
        return 0
    if rc == 4:
        return ONBOARD_NOTICE_STUCK
    return 3


def inline_onboard_provider(provider):
    """走 dm 的家 ⇒ 直接貼。自取的家(SELF_FETCH_PROVIDERS)與橋接席(AIBOT_PROVIDERS)不動;
    provider 量不到(None/空)也不動 —— 不猜。"""
    return (isinstance(provider, str) and bool(provider)
            and provider not in SELF_FETCH_PROVIDERS and provider not in AIBOT_PROVIDERS)


_BOX_ASCII = {0x2500: "-", 0x2501: "-", 0x2502: "|", 0x2503: "|"}


def lint_safe(text):
    """review r12 M1:整行直接貼的內容要過 dm 出站檢查(src/messaging/r30_lint.py:圈號/全形英數/框線/區塊字元)。
    以前長內容落檔,檢查只看到那一行路徑;改成直接貼之後,人格包的「── 角色卡」這類小標讓整則被擋(rc=3 → 什麼都沒到)。
    ⇒ 先用 r30_lint.autofix 換掉有安全替代的字,剩下的框線換成 ASCII(橫線 -、直線 |、其他 +)、區塊字元 #、
    其他還擋的字換成 *。只用在直接貼的那一份;落檔給席自己讀的原文不動。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "src", "messaging"))
        import r30_lint
    except Exception:  # noqa: BLE001 —— 檢查模組載不進來:原文送,dm 那一關照樣會把關
        return text
    fixed, left = r30_lint.autofix(text)
    if not left:
        return fixed
    bad = {f["char"]: f["code"] for f in r30_lint.lint(fixed)}
    out = []
    for ch in fixed:
        code = bad.get(ch)
        if code == "box_drawing":
            out.append(_BOX_ASCII.get(ord(ch), "+"))
        elif code == "block_element":
            out.append("#")
        elif code:
            out.append("*")
        else:
            out.append(ch)
    return "".join(out)


def inline_body(body, update=False):
    """多行開席單 → 一行:開頭固定一句,各行去頭尾空白、丟空行,以「 / 」接起來。"""
    parts = [ln.strip() for ln in str(body or "").splitlines()]
    head = INLINE_HEAD_UPDATE if update else INLINE_HEAD_FIRST
    return lint_safe(INLINE_SEP.join([head] + [p for p in parts if p]))


# 投遞後等回執:預設 90 秒 / 每 3 秒查一次。冷啟的 CLI 要幾秒到十幾秒才
# 開始處理貼進去的內容(開席鏈既有註記:seat detect 要 --retry 5),而一份
# 55 行的開席單讀完再回一句,90 秒是寬鬆但不至於卡住開席的值。
DEFAULT_TIMEOUT_S = 90
DEFAULT_INTERVAL_S = 3
# ★ O4(大綱 §85.3,2026-09-10):脫離的回執守望者**不用** 90 秒。
#   90 秒是為 claude 定的(今天實測 claude 讀完 77 秒),codex 149 秒、agy 更慢
#   ⇒ 守望在 90 秒放棄,帳上永遠只有 outbound,右欄把「載入中」畫到 15 分鐘窗到期。
#   守望窗與右欄載入窗**同一個數字、同一個來源**(seat_status.ONBOARD_LOADING_WINDOW_S),
#   兩邊分岔的失效形狀就是今天這個:守望先放棄,畫面還在等。
#   同步路徑(--missing 等人工補投)仍用 DEFAULT_TIMEOUT_S:那條路有人在終端等,15 分鐘會卡人。
DETACHED_ACK_TIMEOUT_S = int(ONBOARD_LOADING_WINDOW_S)
# 連續幾次 capture 取不到之後**做存在性探針**(list-panes):窗真的不在才是 pane 死,list 也失敗是 capture_unavailable;
# 不再空等到窗到期。取不到 ≠ 畫面是空的(capture 回 None 與 "" 分得開);一次取不到可能是 tmux 忙,要連續。
PANE_DEAD_POLLS = 5

SEAT_SCHEMA = "aib-seat/v1"
_WID_RE = re.compile(r"@[0-9]{1,10}")


class OnboardError(Exception):
    """rc2 級:用法或環境錯,一個字都還沒投出去。"""


# ---- 純函式(單元測試對象;不碰 tmux/帳本/檔案系統)----------------------

def template_sha256(text):
    """模板內容雜湊。★ 對**整份檔案的位元組**取,不是對渲染後的正文——
    渲染會代入 seat_id/window,每一席都不同,那樣 B 永遠比不出「同一版」。"""
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


PROFILES = ("full", "rules")
_SECTION_MARK_RE = re.compile(r"^<!-- aib:section ([a-z]+) -->[ \t]*\n?", re.M)


_TR = {}


def T(key, **params):
    """CLI 給人看的字走 aib.onboardcli.* 四語鍵(語系盤查第 15 條,10-14);每次照 i18n_lib.resolve_locale() 取,讀不到印鍵名。
    開席單正文(模板)另有自己的語系路,不在這裡。"""
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc)
        if tr is None:
            tr = _TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001
        return key


def now_rule_line():
    """鐵則九(aib now)的一行版,給選「不載入」/逾時的通知用(那些席讀不到鐵則段)。"""
    try:
        sys.path.insert(0, HERE)
        from i18n_lib import Translator
        text = Translator().t("aib.onboard.now_rule", {})
    except Exception:
        text = "!!aib.onboard.now_rule!!"
    if text.startswith("!!"):
        text = ('[鐵則九 · 回報主項目] 每一席開工、換主項目、換狀態、每做完一步、每次回報時都要跑:'
                'aib now "主項目" 做完幾步/總共幾步(進度 % 由步數算);'
                '等別席加 --state reply,等 the maintainer 決定加 --state decide;做完或沒事做 aib now --clear(【我很閒】)。')
    return text


def apply_profile(template_text, profile):
    """09-14(the maintainer 選單三選一「全部 / 只鐵則 / 不載入」):模板以
    `<!-- aib:section NAME -->` 行分段;`rules` 只留 rules 段,`full` 全留。
    標記行本身兩種 profile 都不投給席位(它是給機器切的,不是給席讀的)。
    首段 `---` 之前的自述照舊由 render() 丟掉,這裡不碰它。
    ★ 模板沒有任何標記 ⇒ `rules` 拿不到段 ⇒ 回空正文(fail-closed:寧可投不出去,
      也不把整份當成鐵則投出去然後宣稱只投了鐵則)。"""
    if profile not in PROFILES:
        raise OnboardError("profile 不合法:%r(合法:%s)" % (profile, "|".join(PROFILES)))
    head, sep, body = template_text.partition("---")
    marks = list(_SECTION_MARK_RE.finditer(body))
    if profile == "full":
        return head + sep + _SECTION_MARK_RE.sub("", body)
    if not marks:
        return head + sep + "\n"
    keep = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        if m.group(1) == "rules":
            keep.append(body[m.end():end])
    return head + sep + "\n" + "".join(keep)


def render(template_text, role_label, seat_id, window,
           seat_file=None, cabin_label=None,
           latest_sweep=None, sweep_note=None, ack_instruction=None):
    """模板 → 該席的開席單正文。

    ★ `latest_sweep` / `sweep_note` 是 2026-09-07 補的,修的是與下面那個
      `seat_file` **同一個形狀**的第二格:讀序寫「docs/reports/ 最新 SWEEP」,
      但**誰算、怎麼算沒有定義**,於是每一席自己發明一個取法 —— 而不同取法
      給出的三種答案在版面上長得一樣。算法要住在碼裡(見
      `resolve_latest_sweep`),不是住在模板裡讓每一席各跑一次。
      算不出來時寫**「量不到」**,同樣不退回任何一條寫死的路徑。

    ★ `seat_file` / `cabin_label` 是 2026-09-07 補的,修的是缺陷三十二:
      模板把席位檔路徑**寫死**成主艙 `~/.ez2-aib/seats/wN.json`,而艙內席的
      正本在 `instances/<艙>/seats/wN.json`。兩份同檔名同窗號,所以拿錯那份
      不會報錯 —— 它回一份**格式完全正確的錯身份**(別人的 seat_id、別人的
      provider、有時連 session_uuid 欄位都沒有)。08-22 就登記過並判「不修」,
      到 09-07 至少五席踩過,其中兩席是同一天開的。
      ★ 這一格不能由模板知道:模板是靜態文字,而答案取決於投遞當下的艙。
      不給值時寫**「量不到」**,不退回那個寫死的路徑 —— 一個猜出來的路徑
      與一個查出來的路徑在版面上逐字相同,而前者正是這個缺陷的本體。

    ★ 這段原本是 `aib` 裡的一段 inline heredoc python。搬過來是為了讓
      **開席時投的**與**補投時投的**是同一份渲染——兩處各寫一份,今天等值,
      改一邊就分岔(與 aib-dm.sh STATE_DIR 兩處同源那個缺陷同型)。

    首段 `---` 之前是模板的自述(佔位符說明),不投給席位。
    ★ 09-14:分段標記行 `<!-- aib:section NAME -->` 是給 apply_profile 切的,**任何**渲染路徑都不投給席位
      (test-seat-onboard-law 釘「鐵則必須是投遞內文的第一行」,直接餵原始模板也要成立)。"""
    body = _SECTION_MARK_RE.sub("", template_text.split("---", 1)[-1]).strip()
    wid = str(window)
    instr = ack_instruction if ack_instruction is not None else ACK_INSTRUCTION_FIRST
    return (body.replace("{role_label}", str(role_label))
                .replace("{seat_id}", str(seat_id))
                .replace("{window}", wid)
                .replace("{window_num}", wid.lstrip("@"))
                .replace("{seat_file}", seat_file or "(量不到,自己跑 aib seat detect)")
                .replace("{cabin_label}", cabin_label or "(艙別量不到)")
                .replace("{latest_sweep}",
                         latest_sweep or ("(%s)" % (sweep_note or "量不到")))
                .replace("{ack_instruction}", str(instr)))


def self_fetch_dir(environ=None):
    """開席單落檔目錄:艙內 `cockpit/onboard-self-fetch/`。沒有艙家目錄就 None。"""
    env = environ if environ is not None else os.environ
    home = (env.get("EZ2_AIB_INSTANCE_HOME") or "").strip()
    if not home:
        return None
    return os.path.join(home, "cockpit", "onboard-self-fetch")


def self_fetch_body_path(window, environ=None):
    d = self_fetch_dir(environ)
    if not d:
        return None
    wid = str(window or "").lstrip("@")
    if not wid.isdigit():
        return None
    return os.path.join(d, "w%s.txt" % wid)


def write_self_fetch_drop(window, body, sha, provider, environ=None):
    """把渲染後的開席單落到艙內檔。回路徑;寫不進去回 None。"""
    if provider not in SELF_FETCH_PROVIDERS:
        return None
    path = self_fetch_body_path(window, environ)
    if not path or not isinstance(body, str) or not body.strip():
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
        stamp = path + ".stamp.json"
        with open(stamp + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"sha": sha, "provider": provider, "window": str(window)},
                      fh, ensure_ascii=False)
        os.replace(stamp + ".tmp", stamp)
    except OSError:
        return None
    return path


def self_fetch_ready(window, sha, provider, environ=None):
    """啟動器已經把同一版開席單塞進 CLI 參數 ⇒ 後面不准再 dm。"""
    if provider not in SELF_FETCH_PROVIDERS:
        return False
    path = self_fetch_body_path(window, environ)
    if not path:
        return False
    stamp = path + ".stamp.json"
    try:
        with open(stamp, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(rec, dict):
        return False
    if rec.get("sha") != sha or rec.get("provider") != provider:
        return False
    return os.path.isfile(path)


def provider_of_window(base, window):
    path = os.path.join(base, "w%s.json" % str(window).lstrip("@"))
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    p = rec.get("provider") if isinstance(rec, dict) else None
    return p if isinstance(p, str) and p else None


def resolve_seat_context(window, environ=None):
    """(席位檔絕對路徑, 艙別標籤) —— **投遞當下現查**,查不到回 (None, None)。

    缺陷三十二的修法住在這裡。要點只有一個:**這個答案模板不可能知道**,
    因為它取決於這一次投遞是在哪一個艙裡發生的。所以它必須是現查的參數,
    不能是模板裡的字面。

    ★ 查不到就回 None,讓上層寫「量不到」。**不得退回 `~/.ez2-aib/seats/wN.json`** ——
      那個 fallback 正是本缺陷:它在主艙時碰巧對,在艙內時給出一份
      **格式完全正確的錯身份**,而兩者在版面上逐字相同。
      失效值不可以長成安全值。
    """
    environ = os.environ if environ is None else environ
    try:
        import seats_dir_lib
        d = seats_dir_lib.seats_dir_with_source(environ=environ)[0]
    except Exception:
        return (None, None)
    if not d:
        return (None, None)
    path = os.path.join(d, "w%s.json" % str(window).lstrip("@"))
    hub = environ.get("AIB_DM_SOCKET") or environ.get("EZ2_AIHUB_SOCKET") or ""
    inst = hub[len("aihub-"):] if hub.startswith("aihub-") else ""
    return (path, ("艙 %s" % inst) if inst else "主 hub")


# ---- 開工讀序的「最新 SWEEP」:算法住在這裡,只有一份 --------------------

REPORTS_REL = "docs/reports"

# 「這份文件是什麼」寫在檔名的**前導全大寫段**裡,後面的小寫段是主題。
# 這不是我發明的規則,是 docs/reports/ 現行檔名的形狀:
#   CABIN-SAFETY-DECOY-SWEEP-0828.md   kind=CABIN-SAFETY-DECOY-SWEEP  ← 它就是一份掃描
#   DISPATCH-acb-sweep-0821.md         kind=DISPATCH                  ← 派工單,主題是掃描
#   REVIEW-VERDICT-laneA-acb-sweep.md  kind=REVIEW-VERDICT            ← 覆審裁定,不是掃描
_CAPS_SEG = re.compile(r"^[A-Z][A-Z0-9]*$")


def _kind_prefix(stem):
    """檔名的前導全大寫段(文件種類)。遇到第一個非全大寫段就停。"""
    out = []
    for seg in stem.split("-"):
        if _CAPS_SEG.match(seg):
            out.append(seg)
        else:
            break
    return "-".join(out)


def sweep_candidates(reports_dir):
    """(命中, 排除但看起來很像) —— **兩個都回**。

    ★ 只回命中集合的函式沒辦法被檢查:圈母體最容易錯的一格是邊界,而邊界
      看不見。這裡把「名字裡有 sweep 但被我排除掉」的那幾筆一起交出來,
      讓臂(與人)直接看得到我圈在哪一邊。

    2026-09-07 在本樹實量:命中 3 筆、排除 4 筆(`DISPATCH-acb-sweep-0821.md`、
    `LANE-E-acb-live-code-sweep.md`、`REVIEW-VERDICT-laneA-acb-sweep.md`、
    以及目錄 `data-cabin-sweep-0828`)—— 四筆全部**不是**掃描報告本身。
    """
    hits, near = [], []
    try:
        names = sorted(os.listdir(reports_dir))
    except OSError:
        return ([], [])
    for name in names:
        if "sweep" not in name.lower():
            continue
        if not name.endswith(".md") or not os.path.isfile(os.path.join(reports_dir, name)):
            near.append(name)
        elif "SWEEP" in _kind_prefix(name[:-len(".md")]):
            hits.append(name)
        else:
            near.append(name)
    return (hits, near)


def _git_out(root, args, run=None):
    """git 的 stdout;問不到(沒有 git、不是 repo、非零 rc、逾時)一律 None。"""
    run = subprocess.run if run is None else run
    try:
        proc = run(["git", "-C", root] + list(args), stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=10)
    except Exception:                     # noqa: BLE001 問不到就是問不到,不猜
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_latest_sweep(root=None, run=None):
    """(讀序要放的那條相對路徑 or None, 說明) —— **投遞當下現算**。

    這一格修的是缺陷三十二的**同族第二格**。讀序原本寫「docs/reports/ 最新
    SWEEP」,而**誰算、怎麼算沒有定義**:2026-09-07 root 6bd0e 07:2x 與
    pm 57b15 13:0x 各自發明一個取法,各自量到同一格 —— 兩個人量到同一個
    答案不代表那條規則有定義,只代表我們碰巧發明了同一個取法。
    ★ 三種取法給出三個不同答案,而三個答案在版面上長得一樣。

    「最新」的定義:**把這份檔案加進來的那一顆的 committer 時間**。
    ------------------------------------------------------------------
    為什麼不是 mtime:mtime 是檔案系統的事實,不是內容的事實。**本樹實量**
    (2026-09-07):`git worktree add` 出來的新樹裡,三份候選的 mtime 是
    1788758103 / 1788758104 / 1788758104 —— 全是簽出那一秒,而它們的先後
    **與正本樹相反**(正本樹按 mtime 得 `CABIN-SAFETY-DECOY-SWEEP-0828`,
    新樹按 mtime 得 `SWEEP-20260814-root-4agents`)。同一份碼、同一批檔案、
    兩棵樹兩個答案,而兩個答案都是一條指得到真檔的合法路徑。

    為什麼不是檔名裡的日期:三份候選的日期格式就有兩種(`20260814` 與
    `0828`),而少了年份的那種在跨年時會把 `0102` 排在 `1228` 前面 ——
    那是一個看起來完全正常的錯答案。還有候選根本沒有日期。

    為什麼是**加入顆**而不是最後一顆:`docs/reports/*.md` 依交接單第八節
    是「寫完不再改」,所以加入顆的時間=它被寫出來的時間;用最後一顆的話,
    一次錯字修正或一次整批搬檔就會讓一份舊報告跳到最新。

    ★ 這個定義在什麼情況下會回一個看起來正常的錯答案(三個,逐個具名):
      1. **檔名沒把 SWEEP 放進前導全大寫段的掃描報告**(例如全小寫命名)
         —— 它會被靜靜排除,而回出來的那條路徑仍然完全正常。
         這就是 `sweep_candidates` 要把排除集合一起交出來的理由。
      2. **經改名/搬檔進來的報告**:git 把那一顆記成 A,於是它的「加入時間」
         跳到搬檔那天。
      3. 本函式**只看 docs/reports/ 這一層**,不遞迴。

    ★ 算不到一律回 (None, 理由),**不得退回任何一條寫死的路徑**。理由裡
      不放檔名 —— 開席令那一行要短,而且「量不到」的版面不可以長得像一條
      路徑(那正是缺陷三十二的形狀:失效值長成安全值)。
    """
    root = ROOT if root is None else root
    hits, _near = sweep_candidates(os.path.join(root, *REPORTS_REL.split("/")))
    if not hits:
        return (None, "量不到:docs/reports/ 底下沒有 SWEEP 報告")
    if _git_out(root, ["rev-parse", "--git-dir"], run) is None:
        return (None, "量不到:這棵樹問不到 git 史")
    stamped = []
    for name in hits:
        out = _git_out(root, ["log", "--diff-filter=A", "-1", "--format=%ct",
                              "--", REPORTS_REL + "/" + name], run)
        if not out:
            # 還沒進顆的候選沒有時間可比。★ 這時候「回一份已進顆的舊報告」
            # 會是一個完全正常的答案,而它可能不是最新的那一份。
            return (None, "量不到:有候選還沒進顆,誰最新無從比較")
        try:
            stamped.append((int(out.split()[0]), name))
        except ValueError:
            return (None, "量不到:加入顆的時間讀不出來")
    stamped.sort()
    if len(stamped) > 1 and stamped[-1][0] == stamped[-2][0]:
        return (None, "量不到:最新的兩份同一顆進來,分不出誰新")
    return (REPORTS_REL + "/" + stamped[-1][1], "")


def count_token(text, token=ACK_TOKEN):
    """畫面上出現幾次。壞輸入=0(採集層不得因為讀不到而炸掉開席鏈)。"""
    if not isinstance(text, str) or not token:
        return 0
    return text.count(token)


def count_ack_tokens(text):
    """已就緒＋已更新合計次數(守望用;兩種回執都算到)。"""
    if not isinstance(text, str):
        return 0
    return sum(text.count(t) for t in ACK_TOKENS)


def prior_onboard_acked(seat_id, ledger_path):
    """這席帳上是否曾有過開席單 inbound(任一版)。有⇒之後用「已更新」。"""
    if not seat_id or not ledger_path:
        return False
    try:
        idx, _bad = load_index(ledger_path)
    except Exception:
        return False
    entry = idx.get(seat_id) or {}
    return bool(entry.get("inbound"))


def ack_instruction_for_seat(seat_id, ledger_path):
    """第一次＝已就緒說明;之後＝已更新說明。"""
    if prior_onboard_acked(seat_id, ledger_path):
        return ACK_INSTRUCTION_UPDATE
    return ACK_INSTRUCTION_FIRST


def ack_reached(baseline_count, current_count):
    """回執判準:**比投遞當下嚴格變多**。

    ★ 這是本模組最容易寫錯的一行,而寫錯的方向是**假綠**:開席單正文自己
      就含「已就緒」三個字(尾段要求對方回這句),所以任何「畫面上有沒有這
      個 token」的天真比對,在 Enter 送出的那一刻就已經成立了——測試會全綠,
      而真實情況是沒有任何一席回過話。

    ★ baseline 必須在**Enter 之後**量,而且用帶完整 scrollback 的擷取
      (`capture-pane -S -`)。只擷可見區時,開席單會被後續輸出捲出畫面,
      token 數會**下降**,於是真的回執了也判不出來(這次是假紅)。
      兩個方向都踩過才寫得出這條註記,不要「簡化」掉。
    """
    try:
        return int(current_count) > int(baseline_count)
    except (TypeError, ValueError):
        return False


# ---- v0.70.0 第 8 條:只看「投遞之後新出現的行」-------------------------------
# ★ 舊判準是「整個畫面的 token 次數比投遞當下多」。貼成一整行之後它有兩個假綠入口:
#   (a) 斷行把「已就緒」切成兩半,重排後又接回 ⇒ 次數憑空 +1;
#   (b) 有些 CLI 送出後才把使用者訊息重畫一次(歷史區)⇒ 正文自己的「已就緒」又數一次。
#   修法兩件:擷取帶 -J(見 capture),以及**投遞當下那份畫面的每一行都扣掉**(多重集合差),
#   剩下的行裡、而且**不在「」引號裡**的 token 才算回執 —— 開席單正文提到回執字一律寫成
#   「已就緒」「已更新」(ACK_INSTRUCTION_* 與 INLINE_HEAD_*),真的回執是裸字。

# 行首只剝**空白、框線、區塊、常見狀態符號**;中文、引號、>、字母都不剝(第二審 r2:剝掉中文會讓
#   「我沒有 you have exceeded…」變成行首)。
_LINE_LEAD_RE = re.compile("^[\\s\u2500-\u259F\u2022\u00b7\u25cf\u23fa\u2717\u2718\u26a0\ufe0f!*-]*")


def _line_key(ln):
    """比對用的行鍵:去掉行首的空白/框線/狀態符號(CLI 重畫歷史常換前綴:●→⏺、縮排變)與行尾空白。
    第二審 r2:舊回執換前綴重畫,不得被當成新行。"""
    return _LINE_LEAD_RE.sub("", ln).rstrip()


def new_lines(baseline_text, current_text):
    """current 比 baseline 多出來的行(以 _line_key 做多重集合差)。壞輸入 ⇒ []。
    ★ 已知邊界:對方這一次的回覆與畫面上某一行舊回覆**逐字相同**、而舊的那一行又被清掉,
      會被抵銷 ⇒ 漏回執;後果是名字後面的 `*` 不消(不擋用)。反方向(假回執)才是要擋的。"""
    if not isinstance(current_text, str):
        return []
    base = {}
    if isinstance(baseline_text, str):
        for ln in baseline_text.split("\n"):
            k = _line_key(ln)
            base[k] = base.get(k, 0) + 1
    out = []
    for ln in current_text.split("\n"):
        k = _line_key(ln)
        if base.get(k, 0) > 0:
            base[k] -= 1
            continue
        out.append(ln.rstrip())
    return out


def _inside_quote(line, i, end):
    """token 落在「…」裡(同一行,之前有沒關上的「),或緊接著」(開引號被斷行切到上一行)。"""
    depth = 0
    for ch in line[:i]:
        if ch == "「":
            depth += 1
        elif ch == "」" and depth > 0:
            depth -= 1
    if depth > 0:
        return True
    return end < len(line) and line[end] == "」"


def count_reply_tokens(lines, tokens=ACK_TOKENS):
    """行裡**不在「」裡**的回執字次數(第二審 r1:只看緊鄰引號會把「請讀完再回已就緒即可」算成回執)。
    tokens 預設兩種都認;開席路徑傳**這一次該回的那一個**(更新版只認「已更新」,舊的「已就緒」被重畫不算)。"""
    n = 0
    for ln in lines or ():
        if not isinstance(ln, str):
            continue
        for tok in tokens:
            start = 0
            while True:
                i = ln.find(tok, start)
                if i < 0:
                    break
                start = i + len(tok)
                if _inside_quote(ln, i, start):
                    continue
                n += 1
    return n


# 額度用完:**完整句**(小寫比對),而且要是**那一行的開頭**(去掉前面的框線/符號/空白)。
#   CLI 自己印的錯誤訊息都長這樣(copilot「 ✗ You have exceeded…」、grok「  ┃  You hit your weekly limit.」);
#   對方回覆裡提到、引用、否定它(「我沒有 exceeded your monthly quota」)不在行首,不算。
#   不收單字 quota/limit,也不收中文「額度用完」—— 第二審 r1:「目前沒有額度用完的問題」會被當成額度用完。
# 來源:09-27 09:4x 真機畫面(copilot @39 / grok @40)+ provider-launch.sh:503 既有字樣。
QUOTA_PHRASES = (
    "you have exceeded your monthly quota",     # copilot 09-27 @39
    "you hit your weekly limit",                # grok 09-27 @40
    "you've hit your usage limit",
    "you have hit your usage limit",
    "you've reached your usage limit",
    "usage limit reached",
)


def quota_seen(lines):
    """新出現的行裡,有沒有**以**額度用完完整句開頭的行。"""
    for ln in lines or ():
        if not isinstance(ln, str):
            continue
        low = _LINE_LEAD_RE.sub("", ln).lower()
        if any(low.startswith(p) for p in QUOTA_PHRASES):
            return True
    return False


def correlation_of(seat_id):
    return CORRELATION_PREFIX + str(seat_id)


def seat_of_correlation(cid):
    """correlation_id → seat_id;不是本刀的 lane 回 None。"""
    if isinstance(cid, str) and cid.startswith(CORRELATION_PREFIX):
        return cid[len(CORRELATION_PREFIX):] or None
    return None


def index_events(lines):
    """帳本行 → {seat_id: {"outbound": {sha…}, "inbound": {sha…}}}。

    壞行跳過如實計數(原理 10:讀取是回饋器),不 raise——面板會呼叫這條
    路徑,一行壞資料不得讓右欄整支死掉。"""
    index, bad = {}, 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(ev, dict):
            bad += 1
            continue
        kind = ev.get("kind")
        stuck = is_stuck_event(ev)
        if kind not in ("message.outbound", "message.inbound") and not stuck:
            continue
        cid = (ev.get("causation") or {}).get("correlation_id")
        seat = seat_of_correlation(cid)
        if not seat:
            continue
        ref = ((ev.get("body") or {}).get("data_ref") or {})
        sha = ref.get("sha256")
        if not isinstance(sha, str):
            continue
        # ★ stuck 自己一槽,**不折進 outbound**:折了就等於宣稱「送出去了」,
        #   而 rc=4 的實況是「稿停在對方的輸入框裡,沒有送出」。
        #   兩個必須分辨的狀態不共用一個值——本批的整個由來就是這句話。
        #   (三態判準 seat_status.onboard_reason 是別席的檔,本槽先只在
        #    本檔的 --missing 挑選處消費;要不要進面板四態由那邊的擁有者決定。)
        slot = index.setdefault(seat, {"outbound": set(), "inbound": set(),
                                       "stuck": set()})
        slot.setdefault("stuck", set())
        if stuck:
            slot["stuck"].add(sha)
        elif kind == "message.outbound":
            slot["outbound"].add(sha)
        else:
            slot["inbound"].add(sha)
    return index, bad


# ---- 帳本(採集/入帳)---------------------------------------------------

def default_ledger_path():
    """ED.1 (b):EZ2_AIB_LEDGER 優先(相容),否則 <狀態根>/ledger/aib-ledger.jsonl;狀態根走
    seats_dir_lib.state_root()(本目錄已在 sys.path),與 bind_queue/decision_inbox/route_register 同一條帳本鏈。"""
    from seats_dir_lib import state_root
    return os.environ.get("EZ2_AIB_LEDGER") or \
        os.path.join(state_root()[0], "ledger", "aib-ledger.jsonl")


def load_index(ledger_path):
    """帳本不存在=空索引(尚無事件,不是錯誤)。"""
    if not os.path.exists(ledger_path):
        return {}, 0
    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            return index_events(fh)
    except OSError:
        return {}, 0


def current_template_sha(path=None):
    """現行模板的 sha;讀不到=None。

    ★ 面板要用它,所以**讀不到不得拋例外**——右欄每幀都會走這條路徑,
      一個缺檔不該讓整支面板死掉(壞資料不得殺面板,rc0 no-op 紅線)。
      None 的下游語意是「比不出版本」→ 判準退回不標記,而不是全席亮紅。
    ★ 路徑由本模組推導,呼叫端不各自拼一次:兩處各拼一份的路徑,今天
      等值,搬動目錄時就分岔(aib-dm.sh STATE_DIR 那個缺陷同型)。"""
    try:
        with open(path or os.path.join(ROOT, TEMPLATE_REL), "rb") as fh:
            return template_sha256(fh.read())
    except OSError:
        return None


def _actor():
    host = os.uname().nodename.split(".")[0][:64] or "local"
    return {
        "ai_id": "system:%s:seat-onboard" % host,
        "sig": "aib-onboard@0000/System",
        "kind": "system",
    }


STUCK_LOGICAL_KIND = "message.stuck_in_composer"      # 程式內的邏輯名(不入帳)
# O4:守望結束而沒等到回執,**留痕**(不是 inbound、不是 outbound;讀端 scan 會略過,
#   右欄的「開席進行中」狀態(O2)之後拿它畫具名失敗)。三個原因分開記,處置不同:
#   逾時 = 對方可能還在讀或卡住,人要去看那一席;pane 死 = 那一席已經不在,補投沒有對象;
#   capture_unavailable = tmux/hub 讀不到,什麼都不能斷言,先看 hub。
ACK_TIMEOUT_LOGICAL_KIND = "message.ack_timeout"
ACK_PANE_DEAD_LOGICAL_KIND = "message.ack_pane_dead"
ACK_CAPTURE_UNAVAILABLE_LOGICAL_KIND = "message.ack_capture_unavailable"
# v0.70.0 第 8 條(2):投遞後對方畫面**新出現**額度用完的句子 ⇒ 停守望、記一筆(右欄名字後綴「額度用完」)。
ACK_QUOTA_LOGICAL_KIND = "message.ack_quota"
ACK_FAIL_SUBJECT_PREFIX = "onboard:ack_fail:"
# ★ 覆審(codex a65fa 18:4x)阻擋 1:capture 回 None 不是 pane 死的證據——tmux 起不來、hub 暫斷、
#   任何 nonzero 都是 None。所以三個原因分開記,而且 pane_dead 只在「list-panes 成功且找不到那個窗」才准寫;
#   list 也失敗一律 capture_unavailable,不猜 pane 死。
ACK_FAIL_REASONS = {ACK_TIMEOUT_LOGICAL_KIND: "timeout",
                    ACK_PANE_DEAD_LOGICAL_KIND: "pane_dead",
                    ACK_CAPTURE_UNAVAILABLE_LOGICAL_KIND: "capture_unavailable",
                    ACK_QUOTA_LOGICAL_KIND: "quota"}
ACK_FAIL_KINDS = tuple(ACK_FAIL_REASONS)
ACK_WATCH_ERROR_LOG = os.path.join(
    os.environ.get("EZ2_AIB_STATE_ROOT")
    or os.path.join(os.path.expanduser("~"), ".ez2-aib", "state"),
    "onboard-ack-watch-errors.log")
ACK_WATCH_ERROR_LOG_MAX = 256 * 1024
STUCK_SUBJECT_PREFIX = "onboard:stuck_in_composer:"     # 入帳標記:system.note + subject.id 前綴
STUCK_FRAME_DIR = os.path.join(
    os.environ.get("EZ2_AIB_STATE_ROOT")
    or os.path.join(os.path.expanduser("~"), ".ez2-aib", "state"),
    "onboard-stuck")


def capture_sgr(window):
    """帶 SGR 的擷取(`-e`)。★ 與 capture() 刻意分成兩支:卡稿判讀的**唯一依據**
    是顏色(`composer_state` docstring:建議整段包在 ESC[2m ⇒ 該 NO;真稿不帶 dim),
    而 `capture()` 是 `-p` 不帶 `-e`,顏色會被剝掉。用它留證據等於留下一份判不出來的。"""
    return _tmux("capture-pane", "-e", "-p", "-t", str(window))


def snapshot_stuck_frame(window, seat_id, frame, frame_sgr=None):
    """卡稿當下把畫面留成**持久**證據,回落檔路徑;留不成回 None(但要出聲)。

    ★ 09-07 root744f4:dm 自己會把畫面留在 `$TMPDIR/aib-dm-stuck-draft-*.txt`,
      但那條路徑**沒有任何人接出來**——開席鏈只印一行「投遞失敗」,而臨時目錄
      重開機就清掉。於是這個 1/6 的偶發永遠停在「有人記得當時看到什麼」。
    ★ 實測:新席的輸入框正常是空的(3/3);出事那次框裡是一個孤零零的 `s`。
      要知道它從哪來,得**留下含不可見字元的原樣**——所以這裡同時寫純文字與
      十六進位;只存純文字的話,下一個人看到的還是「一個 s」。
    ★ fail-open:留不成不擋既有的入帳(那才是投遞語意的正本),但**必須出聲** ——
      靜默失敗會讓「沒留到」與「沒發生過」同形。
    """
    try:
        os.makedirs(STUCK_FRAME_DIR, mode=0o700, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        safe_win = str(window).replace("@", "at")
        path = os.path.join(
            STUCK_FRAME_DIR, "%s-%s-%s.txt" % (stamp, safe_win, seat_id))
        text = frame if isinstance(frame, str) else ""
        composer = [ln for ln in text.splitlines() if "\u276f" in ln]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# onboard stuck frame\n")
            fh.write("# window=%s seat=%s at=%s\n" % (window, seat_id, stamp))
            fh.write("# ★ 輸入框那幾行的十六進位在最後 —— 那是唯一能認出殘留字元的地方\n\n")
            fh.write(text)
            fh.write("\n\n# ---- composer lines (repr) ----\n")
            for ln in composer or ["(找不到 \u276f 那一行)"]:
                fh.write("%r\n" % (ln,))
            fh.write("\n# ---- composer lines (hex) ----\n")
            for ln in composer:
                fh.write("%s\n" % (ln.encode("utf-8").hex(),))
            # ★★ 帶 SGR 的那一份才判得出「dim 的建議」與「真稿」——
            #   `composer_state` 就是靠 ESC[2m 分這兩者。只留純文字的話,
            #   下一個人看到的是一模一樣的兩種東西。
            fh.write("\n# ---- composer lines WITH SGR (repr) ----\n")
            sgr_text = frame_sgr if isinstance(frame_sgr, str) else ""
            sgr_lines = [ln for ln in sgr_text.splitlines() if "\u276f" in ln]
            for ln in sgr_lines or ["(沒有帶 SGR 的擷取 —— 判不出 dim,見上方註解)"]:
                fh.write("%r\n" % (ln,))
        return path
    except Exception as err:              # noqa: BLE001 見 docstring:留不成要出聲
        print("seat-onboard: " + T("aib.onboardcli.stuck_frame_fail", err=err), file=sys.stderr)
        return None


def ledger_kind_for(kind):
    """邏輯 kind → 帳本 kind。stuck 與 O4 的三個 ack 失敗註記映成既有 system.note;其餘原樣。"""
    return "system.note" if kind == STUCK_LOGICAL_KIND or kind in ACK_FAIL_KINDS else kind


def ack_fail_subject(seat_id, kind):
    """O4:subject.id = onboard:ack_fail:<timeout|pane_dead|capture_unavailable>:<seat>,讀端靠前綴分流。"""
    why = ACK_FAIL_REASONS[kind]
    return {"kind": "message", "id": ACK_FAIL_SUBJECT_PREFIX + why + ":" + str(seat_id)}


def is_ack_fail_event(ev):
    """讀端分流:system.note 且 subject 帶 O4 標記 → 回白名單內的原因字;否則 None。純函數。
    ★ 只認白名單(覆審):onboard:ack_fail:<任意字>:<seat> 若都放行,O2 接線後會把髒值畫上右欄。"""
    if not isinstance(ev, dict) or ev.get("kind") != "system.note":
        return None
    subj = (ev.get("body") or {}).get("subject") or {}
    sid = subj.get("id")
    if subj.get("kind") != "message" or not isinstance(sid, str) \
            or not sid.startswith(ACK_FAIL_SUBJECT_PREFIX):
        return None
    why = sid[len(ACK_FAIL_SUBJECT_PREFIX):].split(":", 1)[0]
    return why if why in ACK_FAIL_REASONS.values() else None


def watch_error_note(text):
    """守望者的錯誤要落到**持久且有界**的檔:脫離的 Popen 三個 fd 都是 DEVNULL,print 沒有人看得到。
    保留 current + 一代 .1;超過上限再輪替時**覆蓋舊的 .1**(不是無限保留)。
    若連這個 fallback 也寫不進去,就沒有剩餘的可觀測通道了——回 False,但目前呼叫端沒有消費回值。"""
    try:
        os.makedirs(os.path.dirname(ACK_WATCH_ERROR_LOG), exist_ok=True)
        try:
            if os.path.getsize(ACK_WATCH_ERROR_LOG) > ACK_WATCH_ERROR_LOG_MAX:
                os.replace(ACK_WATCH_ERROR_LOG, ACK_WATCH_ERROR_LOG + ".1")
        except OSError:
            pass
        with open(ACK_WATCH_ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s pid=%d %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), os.getpid(), text))
        return True
    except OSError:
        return False


def stuck_subject(seat_id):
    return {"kind": "message", "id": STUCK_SUBJECT_PREFIX + str(seat_id)}


def is_stuck_event(ev):
    """讀端分流:system.note 且 subject 帶 stuck 標記。純函數。"""
    if not isinstance(ev, dict) or ev.get("kind") != "system.note":
        return False
    subj = (ev.get("body") or {}).get("subject") or {}
    return subj.get("kind") == "message" and \
        isinstance(subj.get("id"), str) and subj["id"].startswith(STUCK_SUBJECT_PREFIX)


def record(ledger_path, kind, seat_id, window, sha, now_ms=None, timeout_s=None):
    """入帳一筆。帳本目錄不存在就建(與其他消費者同慣例)。
    kind 可為邏輯名 message.stuck_in_composer——入帳前經 ledger_kind_for() 映成 system.note。"""
    from ledger import Ledger

    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    subject = {"kind": "identity", "id": str(seat_id)}
    if kind in ACK_FAIL_KINDS:
        subject = ack_fail_subject(seat_id, kind)
        # ★ 覆審阻擋 2:秒數要寫**實際**傳給守望的那個,不是常數(3 秒的 canary 曾被寫成 900)。
        secs = ("%d" % timeout_s) if timeout_s is not None else "量不到"
        if kind == ACK_PANE_DEAD_LOGICAL_KIND:
            zh = "開席單回執守望結束:%s(%s)的窗已不在 hub 裡,無回執" % (seat_id, window)
            en = "onboarding ack watch ended: window for %s (%s) is gone from the hub, no ack" % (seat_id, window)
        elif kind == ACK_CAPTURE_UNAVAILABLE_LOGICAL_KIND:
            zh = "開席單回執守望中止:連續讀不到 %s(%s)的畫面(tmux/hub 不可用),不判 pane 死" % (seat_id, window)
            en = "onboarding ack watch aborted: screen of %s (%s) unreadable (tmux/hub unavailable); not claiming pane dead" % (seat_id, window)
        else:
            zh = "開席單回執守望逾時(%s 秒):%s(%s)無回執,請看那一席" % (secs, seat_id, window)
            en = "onboarding ack watch timed out (%ss): no ack from %s (%s)" % (secs, seat_id, window)
    elif kind == STUCK_LOGICAL_KIND:
        zh = "開席單已進入 %s(%s)的輸入框但未送出,**絕不重投**" % (seat_id, window)
        en = ("onboarding staged in %s (%s) composer but not submitted; "
              "never retry" % (seat_id, window))
        subject = stuck_subject(seat_id)
    else:
        sent = kind == "message.outbound"
        zh = ("開席單已投遞 %s(%s),候回執" if sent
              else "開席單回執已觀察到 %s(%s)") % (seat_id, window)
        en = ("onboarding delivered to %s (%s), awaiting ack" if sent
              else "onboarding ack observed for %s (%s)") % (seat_id, window)
    led = Ledger(ledger_path, node_id="seat-onboard")
    return led.append({
        "kind": ledger_kind_for(kind),
        "actor": _actor(),
        "body": {
            "summary": {"zh-TW": zh, "en": en},
            "subject": subject,
            "data_ref": {
                "path": TEMPLATE_REL,
                "sha256": sha,
                "media_type": "text/markdown",
            },
        },
        "causation": {"causation_id": None,
                      "correlation_id": correlation_of(seat_id)},
    }, wall_ms=now_ms)


# ---- 席位(讀 seats 目錄)-----------------------------------------------

def seats_dir():
    """席位登記簿解析。順位:**HUB 導出** > EZ2_AIB_INSTANCE_HOME > 主艙。

    ## 2026-08-21:這支檔今天讓五席全部印錯 seat id

    實測(instance 16,開五席):

        seat-onboard 印  @3 f0f13  @4 b288f  @5 6bcbf  @6 78f43  @7 a769d
        席位檔實際是      d9ca1     62efe     d524f     72392     c1393

    **五席全中,一個不漏。** 成因:殘檔與活檔用**同一個檔名、同一個窗號**,
    只差一層目錄——

        ~/.ez2-aib/seats/w4.json                = b288f(死席,08-14 的殘留)
        ~/.ez2-aib/instances/16/seats/w4.json   = 62efe(活席)

    本函式只認 env;`aib` 呼叫它時沒把 HUB 導出的目錄傳下去,於是退回主艙,
    **拿到一份格式完全正確的錯答案**——不報錯、不變紅、沒有任何訊號。

    ★ 兩種發作方式差很多,而危險的是第二種:
        立刻失敗會叫 —— 拿那個 id 去 dm 得到「查無目標:7fb27(死窗不投)」
        一整天成功不會叫 —— 那一席就用死檔的 id 署名,從沒失敗過
      我這艙的 qa 席整天署名 `a seat`,而它的真 id 是 `72392`。

    ★★ **主艙不是回退來源,是衝突。**(dev1 提、root2 轉述的第三條判準)
      HUB 說得出是哪一艙時,就**只**用那一艙;那一艙的目錄不存在是錯誤,
      不是「那就用主艙的吧」。今天的形狀是**回退成功了,而成功不會叫**。

    ★ 前科:`docs/RETRO-88b48-20260818.md:213` 三天前就寫著
      「舊席位檔殘留,不是說話的人」,同一個 `b288f`。
      **寫下來三天而沒有守衛,今天照樣發生。** 判準見
      `tests/test-seats-dir-resolution.py`。
    """
    # ED.1(2026-08-22):解析本體收進 seats_dir_lib(狀態根 EZ2_AIB_STATE_ROOT >
    # EZ2_AIB_HOME > ~/.ez2-aib;HUB 導出的 instances/<N>/seats 也掛在根之下)。
    # 本函式名保留——pane-left-fleet.sh / title-beat.sh / pane-left-persona.sh 都
    # import 這個名字;它們說的「單一真相源」現在真的只有一份。
    import seats_dir_lib
    return seats_dir_lib.seats_dir()


def load_seats(base):
    """→ [(window_id, seat_id, role)];認不出的席位檔跳過並如實回報。

    認得出來的判準走 `seat_status.record_legible`(與面板/守衛同源),
    不在這裡另發明第四套 skip 條件。"""
    out = []
    for path in sorted(glob.glob(os.path.join(base, "w*.json"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            print("seat-onboard: " + T("aib.onboardcli.bad_seat_file", name=name), file=sys.stderr)
            continue
        legible, why = record_legible(rec, name)
        if not legible:
            print("seat-onboard: " + T("aib.onboardcli.seat_file_unknown", name=name, why=why),
                  file=sys.stderr)
            continue
        seat_id = rec.get("seat_id")
        if not isinstance(seat_id, str) or not seat_id:
            print("seat-onboard: " + T("aib.onboardcli.no_seat_id", name=name), file=sys.stderr)
            continue
        role = rec.get("role") if isinstance(rec.get("role"), str) else "?"
        out.append((rec["window_id"], seat_id, role))
    return out


def target_code_for_window(base, window):
    """席位窗 → UUID 座位碼；取不到回 None 並具名出聲。

    B11 收掉 dm 的窗號入口後,開席投遞仍必須先用窗找到席位檔,再把
    `session_uuid` 尾五碼交給 dm。末五碼算法只准走 seat_address.code_of()。
    """
    path = os.path.join(base, "w%s.json" % str(window).lstrip("@"))
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as err:
        print("seat-onboard: " + T("aib.onboardcli.onboard_code_unknown", path=path, why=err),
              file=sys.stderr)
        return None
    legible, why = record_legible(rec, os.path.basename(path))
    if not legible or rec.get("window_id") != window:
        print("seat-onboard: " + T("aib.onboardcli.onboard_code_unknown", path=path, why=why),
              file=sys.stderr)
        return None
    code = seat_address.code_of(rec)
    if not code:
        print("seat-onboard: " + T("aib.onboardcli.onboard_code_bad_uuid", path=path),
              file=sys.stderr)
    return code


# ---- tmux / 投遞(impure 殼)--------------------------------------------

BOUND_TMUX = ""   # --await-ack 子行程由 --tmux-bin 綁定;不走環境變數(艙導向名字有單源,不新增)


def _tmux_bin():
    """回執守望用的 tmux 在**起跑那一刻綁成絕對路徑**,脫離之後不再走 PATH。

    09-11 b21 真紅:守望是脫離行程(start_new_session),呼叫端(測試治具)收掉
    假 tmux 之後它還在輪詢 capture-pane,`tmux` 這個名字就解析到 PATH 上下一支——
    真的那支,對一個不存在的艙 socket 下指令。綁到的那支不見了就是不見了,不換一支。
    """
    if BOUND_TMUX:
        return BOUND_TMUX
    named = os.environ.get("AIB_DM_TMUX") or "tmux"
    found = shutil.which(named) if not os.path.isabs(named) else named
    return found or named


def _tmux(*args):
    hub = os.environ.get("EZ2_AIHUB_SOCKET") or "aihub"
    cmd = [_tmux_bin(), "-L", hub] + list(args)
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def capture(window):
    """帶完整 scrollback 的擷取(`-S -`)。見 ack_reached 為什麼不能只擷
    可見區。取不到=None(與空字串不同:空字串是「畫面是空的」)。"""
    # -J:tmux 自動斷行的那幾行接回一行。v0.70.0 起開席單對走 dm 的家是**一整行**貼進去,
    #   不接回的話「已就緒」會被斷行切成兩半,視窗改寬再重排就「多出一次」= 假回執。
    return _tmux("capture-pane", "-p", "-J", "-S", "-", "-t", str(window))  # O8-capture-join


DM_CTL = os.path.join(HERE, "dm-ctl.sh")   # review r4 M1:deliver 的唯一出口(測試可換成假 dm-ctl 讀回 env 與正文)


def deliver(target_code, body, dry_run=False, no_relay=False):
    """交 dm-ctl(它有 R30 lint、血緣閘、卡稿/BIND 守衛、貼後回讀)。

    ★ 不繞過 dm-ctl 自己 send-keys:那正是 2026-08-10 四條 lane 全滅的
      形狀(v1 九行裸 send-keys),aib-dm.sh 檔頭明令不要再複製。

    → dm-ctl 的 rc(2026-08-22 起四態,見 dm-ctl.sh 檔頭):
      0=送達已驗(含 stage=queued 已入對方佇列)
      2=一個字都沒進目標,**可安全重送**
      3=已送未驗(**勿重貼**)
      4=進了沒送出/框內狀態不可判,**絕不重送**

    ★★ 未修缺陷(2026-08-22 a seat 記;本檔非本席擁有,只登記不擅改):
      下面 :447 的呼叫端寫的是 `if rc != 0: return "undelivered"`,而
      "undelivered" 的語意(見 send_and_ack docstring)是「帳上留白,下一輪
      --missing 會再抓到」= **自動重投**。rc=4 的意思正好相反:稿已經在對方
      輸入框裡,重投會疊成兩份。**四態被壓回兩態,而壓扁的方向恰好是會出事
      的那一邊。**
      修法不是只改這一行:--missing 是看帳本有沒有 outbound 紀錄來挑人的,
      所以 rc=4 還需要在帳上留下一筆「稿滯留在框」才不會被重新挑中——
      那牽涉帳本語意,屬本檔擁有者的裁量,已回報 a seat / a seat。
    """
    cmd = ["bash", DM_CTL, str(target_code)]
    if dry_run:
        cmd.append("--dry-run")
    env = None
    if isinstance(body, str) and body.startswith(INLINE_HEAD_PREFIX):
        # 直接貼的開席單:關掉長訊落檔(dm-ctl 的 AIB_DM_RELAY_BYTES=0 = 關閉),內容整行進對方框
        env = dict(os.environ, AIB_DM_RELAY_BYTES="0")     # O8-inline-no-relay
    elif no_relay:
        # review r4 M1:「不載入/逾時」通知接上基因後約 1.4KB,過 dm-ctl 的 600 位元組門檻就被換成
        #   「長訊已落檔,請讀:…」—— CLI 席拿到的是指標不是通知,copilot/gemini 讀不到落檔(它們的家
        #   本來就是直接貼)⇒ 基因永遠沒到。呼叫端明說 no_relay ⇒ 這一則整行貼。
        env = dict(os.environ, AIB_DM_RELAY_BYTES="0")     # r4-M1-notice-no-relay
    try:
        proc = subprocess.run(cmd, input=body, text=True,
                              capture_output=True, env=env)
    except OSError as err:
        raise OnboardError("dm-ctl 無法執行:%s" % err)
    parsed = parse_result_text(proc.stdout)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode, parsed


def pane_exists(window):
    """存在性探針:list-panes 成功 → True/False;list 也失敗 → None(hub/tmux 不可用,不猜)。"""
    out = _tmux("list-panes", "-a", "-F", "#{window_id}")
    if out is None:
        return None
    return str(window) in {ln.strip() for ln in out.splitlines()}


def wait_ack_reason(window, baseline, timeout_s, interval_s, sleep=time.sleep,
                    dead_polls=PANE_DEAD_POLLS, clock=time.monotonic, exists=pane_exists,
                    ack_tokens=ACK_TOKENS):
    """投遞後輪詢回執。→ "acked" | "quota" | "timeout" | "pane_dead" | "capture_unavailable"。

    baseline 是**字串**(投遞當下 capture 的畫面)⇒ 只看之後新出現的行(正式路徑);
    是**整數** ⇒ 舊判準(次數變多),只留給 --baseline N 的舊呼叫端。

    ★ 都回字串不 raise:呼叫端要拿它去入帳與決定 rc,「沒等到」是正常結果不是例外。
    ★ O4:連續 dead_polls 次 capture 回 None(不是 "")之後**再問一次存在性**:
      窗真的不在 hub 裡 → pane_dead;list 也問不到 → capture_unavailable(不宣稱 pane 死)。
      沒有這道提早收線,守望窗改成 15 分鐘之後,一個關掉的席會讓守望者空等 15 分鐘。
      interval=3 時五次 miss 約 12 秒(首輪即抓、睡四次),不是 15 秒。dead_polls<=0 = 關掉(治具用)。
    ★ clock 預設 monotonic(覆審 3):系統時鐘被校正時 time.time 會讓 15 分鐘窗提早或延長。"""
    deadline = clock() + max(0, timeout_s)
    misses = 0
    while True:
        text = capture(window)
        if text is None:
            misses += 1
            if dead_polls > 0 and misses >= dead_polls:
                alive = exists(window)
                return "pane_dead" if alive is False else "capture_unavailable"
        else:
            misses = 0
            if isinstance(baseline, str):
                # v0.70.0:baseline 是投遞當下的畫面 ⇒ 只看之後新出現的行(O8-new-lines-only)
                fresh = new_lines(baseline, text)
                if count_reply_tokens(fresh, ack_tokens) > 0:
                    return "acked"
                if quota_seen(fresh):
                    return "quota"
            elif ack_reached(baseline, count_ack_tokens(text)):
                # 相容:整數 baseline(舊呼叫端/--baseline N)照舊判次數
                return "acked"
        if clock() >= deadline:
            return "timeout"
        sleep(max(0.1, interval_s))


def wait_ack(window, baseline, timeout_s, interval_s, sleep=time.sleep, ack_tokens=ACK_TOKENS):
    """相容殼:同步路徑與既有測試用的布林版。→ True 只在真的看到回執。"""
    return wait_ack_reason(window, baseline, timeout_s, interval_s, sleep=sleep,
                           ack_tokens=ack_tokens) == "acked"


def baseline_dir():
    return os.path.join(os.environ.get("EZ2_AIB_STATE_ROOT")
                        or os.path.join(os.path.expanduser("~"), ".ez2-aib", "state"),
                        "onboard-baseline")


BASELINE_MAX_AGE_S = 24 * 3600


def prune_baseline_files(d, now=None, max_age_s=BASELINE_MAX_AGE_S):
    """第二審 r6:守望者沒起來、或被殺掉,baseline 檔就沒人刪 ⇒ 每次落新檔前清掉超過一天的舊檔。
    守望窗是 15 分鐘,一天前的檔一定沒有守望者在讀。→ 刪掉的檔數;任何錯誤都吞掉(不擋開席)。"""
    now = time.time() if now is None else now
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for fn in names:
        if not fn.endswith(".txt"):
            continue
        path = os.path.join(d, fn)
        try:
            if now - os.path.getmtime(path) > max_age_s:
                os.remove(path)
                n += 1
        except OSError:
            continue
    return n


def write_baseline_file(window, seat_id, text):
    """投遞當下的畫面落檔 → 路徑;寫不進去 ⇒ None(守望者退回整數判準,不擋開席)。"""
    try:
        d = baseline_dir()
        os.makedirs(d, exist_ok=True)
        prune_baseline_files(d)
        path = os.path.join(d, "%s-%s-%d-%d.txt" % (
            re.sub(r"[^0-9A-Za-z]", "", str(window)) or "w",
            re.sub(r"[^0-9A-Za-z]", "", str(seat_id)) or "s", os.getpid(), int(time.time() * 1000)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    except OSError as err:
        watch_error_note("baseline 落檔失敗 window=%s: %s" % (window, err))
        return None


def spawn_ack_watcher(window, seat_id, sha, baseline, ledger_path,
                      timeout_s, interval_s, ack_token=None):
    """把「等回執」丟到脫離的子行程,自己立刻回來。

    ★★ 這不是效能優化,是修一個真缺陷。第一版讓 `aib seat open` 同步等
      回執,於是**開席被卡住最多 90 秒**——test-eye-ui-launch 的
      RealTmuxIsolationTest 立刻紅(outer client 沒切過去),因為開席鏈
      後面還有事要做。開席的成敗與「對方讀完沒」是兩件事,不可以讓後者
      擋前者:那一席還沒開起來的時候,它當然不會回話。

    ★ 用 Popen 重新叫自己而不是 fork():子行程要跑 tmux subprocess,
      fork 出來的半個 python 狀態(已開的檔、緩衝區)是無謂的風險面;
      重新起一支的成本在這裡完全不重要。start_new_session=True 讓它不吃
      父行程的訊號(開席窗關掉不該殺掉還在等回執的那支)。"""
    baseline_file = None
    if isinstance(baseline, str):
        # 畫面太大不走 argv(ARG_MAX:09-11 三份證詞 0 byte 那一次),落檔給守望者讀,讀完刪。
        baseline_file = write_baseline_file(window, seat_id, baseline)
        # --baseline 一律帶投遞當下的**次數**(第二審 r1):落檔失敗、或守望者讀檔失敗,都退回舊判準;
        #   帶 0 的話正文自己的「已就緒」會當場判成回執。
        baseline = count_ack_tokens(baseline)
    cmd = [sys.executable, os.path.abspath(__file__), "--await-ack",
           "--window", str(window), "--seat-id", str(seat_id),
           "--sha", str(sha), "--baseline", str(baseline),
           "--ledger", str(ledger_path),
           "--timeout", str(timeout_s), "--interval", str(interval_s)]
    try:
        cmd += ["--tmux-bin", _tmux_bin()]   # 起跑那一刻綁定成絕對路徑,脫離後不走 PATH
        if baseline_file:
            cmd += ["--baseline-file", baseline_file]
        if ack_token in ACK_TOKENS:
            cmd += ["--ack-token", ack_token]
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        return True
    except OSError as err:
        if baseline_file:
            try:
                os.remove(baseline_file)          # 守望者沒起來 ⇒ 沒人會讀,當場刪(第二審 r6)
            except OSError:
                pass
        print("seat-onboard: " + T("aib.onboardcli.watcher_fail", err=err),
              file=sys.stderr)
        return False


# ---- 一席的完整流程 ------------------------------------------------------

# dm-ctl 的 rc=4:稿進了對方輸入框但沒送出(或框內狀態不可判)。
# ★ 這個數字只寫一處,不散落在 if 裡:契約改了要能一眼找到唯一的消費點。
DM_RC_STUCK = 4
# 第 217 項:sort-queued 的 rc=0 不是「已在對方框裡」。印終態前短輪詢 delivered_at。
# 不改 rc 契約(acked/pending 仍回 0)。
QUEUE_POLL_S = 30
QUEUE_POLL_INTERVAL_S = 1.0
RESULT_LINE_RE = re.compile(
    r"^RESULT stage=(\S+) id=(\S+) relay=(\S+) thread=(\S+)(?:\s+\S+)*$")


def parse_result_text(text):
    """從 dm-ctl stdout 取最後一行 RESULT。沒有就各欄 None,不猜。"""
    out = {"stage": None, "id": None, "relay": None, "thread": None}
    if not text:
        return out
    last = None
    for ln in str(text).splitlines():
        m = RESULT_LINE_RE.match(ln.strip())
        if m:
            last = m
    if last is None:
        return out
    out["stage"] = last.group(1)
    out["id"] = None if last.group(2) == "-" else last.group(2)
    out["relay"] = None if last.group(3) == "-" else last.group(3)
    out["thread"] = None if last.group(4) == "-" else last.group(4)
    return out


def unpack_deliver_rc(value):
    """deliver 現回 (rc, RESULT 欄);舊替身仍回 int。兩種都收。"""
    if isinstance(value, tuple):
        rc = value[0] if value else 1
        extra = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
        try:
            return int(rc), extra
        except (TypeError, ValueError):
            return 1, extra
    try:
        return int(value), {}
    except (TypeError, ValueError):
        return 1, {}


def queue_item_snapshot(seat, item_id, env=None):
    """讀佇列那一則的送達欄。查不到回 None,不補。收件碼=session_uuid 尾五碼。"""
    try:
        import dm_queue as DQ
    except ImportError:
        return None
    item = DQ._load_item(seat, item_id, env=env)
    if not item:
        return None
    return {
        "id": item.get("id"),
        "status": item.get("status"),
        "delivered_at": item.get("delivered_at"),
        "when": item.get("when"),
        "hold_why": item.get("hold_why"),
    }


def _live_queue_lookup(seat, queue_id, env=None):
    return queue_item_snapshot(seat, queue_id, env=env)


queue_lookup = _live_queue_lookup
queue_sleep = time.sleep
queue_clock = time.monotonic


HOLD_STATUS_NAME = "hold_human"


def queue_is_delivered(snap):
    if not isinstance(snap, dict):
        return False
    if snap.get("delivered_at") not in (None, ""):
        return True
    return snap.get("status") in ("delivered", "read", "done")


def queue_is_stopped(snap):
    return isinstance(snap, dict) and snap.get("status") == HOLD_STATUS_NAME


def wait_queue_delivered(seat, queue_id, timeout_s=QUEUE_POLL_S,
                         interval_s=QUEUE_POLL_INTERVAL_S,
                         sleep=None, clock=None, lookup=None, env=None):
    """短輪詢 delivered_at。→ (snapshot_or_None, waited_s)。不改 rc。"""
    sleep = queue_sleep if sleep is None else sleep
    clock = queue_clock if clock is None else clock
    lookup = queue_lookup if lookup is None else lookup
    started = clock()
    deadline = started + max(0.0, float(timeout_s))
    last = None
    while True:
        last = lookup(seat, queue_id, env)
        if queue_is_delivered(last) or queue_is_stopped(last):
            return last, clock() - started
        now = clock()
        if now >= deadline:
            return last, now - started
        remain = deadline - now
        sleep(min(float(interval_s), remain))


def enrich_result_with_queue_delivery(row, timeout_s=QUEUE_POLL_S,
                                      interval_s=QUEUE_POLL_INTERVAL_S):
    """印出前:sort-queued 才問佇列。拿掉這一呼叫 ⇒ 送達格必紅。"""
    if row.get("state") != "pending":
        return row
    if row.get("dm_stage") != "sort-queued":
        return row
    qid = row.get("queue_id")
    seat = row.get("target_code")
    if not qid or not seat:
        return row
    snap, waited = wait_queue_delivered(
        seat, qid, timeout_s=timeout_s, interval_s=interval_s)
    row["queue_wait_s"] = waited
    if snap:
        row["delivered_at"] = snap.get("delivered_at")
        row["delivered_when"] = snap.get("when")
        row["queue_status"] = snap.get("status")
        row["hold_why"] = snap.get("hold_why")
    return row


def format_onboard_final_line(row):
    """開席結束給真人看的一行。三種終態必須分得出。"""
    wid = row.get("window") or "?"
    sid = row.get("seat_id") or "?"
    state = row.get("state")
    head = "seat-onboard: %s %s " % (wid, sid)
    if state == "acked":
        return head + "已回執"
    if state == "stuck_in_composer":
        return head + "卡在輸入框(不要重送)"
    if state == "undelivered":
        return head + "未送達"
    if state == "no_ack":
        return head + "未回執"
    if state == "pending":
        snap = {"delivered_at": row.get("delivered_at"),
                "status": row.get("queue_status")}
        if queue_is_delivered(snap):
            when = row.get("delivered_when") or "時刻量不到"
            return head + "已送到輸入框(%s),正在等回執" % when
        if row.get("dm_stage") == "sort-queued" and row.get("queue_id"):
            waited = row.get("queue_wait_s")
            try:
                waited_s = int(waited)
            except (TypeError, ValueError):
                waited_s = int(QUEUE_POLL_S)
            qid = row["queue_id"]
            code = row.get("target_code") or "?"
            return (head +
                    "還在暫存未送出(等了 %d 秒仍未送;queue_id=%s;"
                    "查法:python3 src/cockpit/dm_queue.py show --seat %s --id %s)"
                    % (waited_s, qid, code, qid))
        return head + "已送到輸入框,正在等回執"
    return head + "-> %s" % (state,)


def onboard_one(window, seat_id, role, template_text, sha, ledger_path,
                timeout_s=DEFAULT_TIMEOUT_S, interval_s=DEFAULT_INTERVAL_S,
                dry_run=False, sleep=time.sleep, detach_ack=False,
                target_code=None, deliver_fn=None, provider=None,
                environ=None, report=None):
    """→ "acked" | "pending" | "no_ack" | "undelivered" | "stuck_in_composer"。

    ★ 投遞失敗(dm rc≠0)**不入帳 outbound**。記了會讓 B 以為投過而跳過
      它——正是 aihub-7/8 那個病:一次沒送到,之後再也沒有人補。沒送到就
      是沒送到,帳上留白,下一輪 --missing 會再抓到。

    detach_ack=True(開席路徑用):投遞與 outbound 入帳照樣同步做完——那
    一段快且它的 rc 有意義——只有「等回執」丟給脫離的子行程。回 "pending"。
    """
    if provider == "codexbot":
        if report is not None:
            report["dm_stage"] = "desktop-onboard-not-integrated"
        print("seat-onboard: codexbot desktop acknowledgement integration pending; no automatic DM or fake ack", file=sys.stderr)
        return "undelivered"
    seat_file, cabin_label = resolve_seat_context(window)
    latest_sweep, sweep_note = resolve_latest_sweep()
    ack_instruction = ack_instruction_for_seat(seat_id, ledger_path)
    # 這一次該回哪個字:更新版只認「已更新」,第一次只認「已就緒」(畫面上舊的回執被重畫不算這一次的)
    expected_ack = ACK_TOKEN_UPDATE if ack_instruction == ACK_INSTRUCTION_UPDATE else ACK_TOKEN
    body = render(template_text, role, seat_id, window, seat_file, cabin_label,
                  latest_sweep, sweep_note,
                  ack_instruction=ack_instruction)
    deliver_fn = deliver_fn or deliver
    env = environ if environ is not None else os.environ
    if provider is None:
        provider = provider_of_window(seats_dir(), window)
    _persona = persona_block(window, provider)
    if _persona:
        body = body + "\n\n" + _persona          # 09-30:新開的席也第一個載入人格
    if expected_ack != ACK_TOKEN_UPDATE and provider not in AIBOT_PROVIDERS:
        # 10-01:前身沒交接 ⇒ 提醒接在最後(第一行留給鐵則,test-seat-onboard-law);更新版不重複提醒
        _handoff = handoff_notice_block(window)
        if _handoff:
            body = body + "\n\n" + _handoff
    if inline_onboard_provider(provider):
        # v0.70.0 第 8 條(3)(the maintainer 09-27 09:58):走 dm 的家,開席單**直接貼內容**(一行、不落 RELAY 檔)。
        #   deepseek 09-27 實測讀不到 ~/.ez2-aib/pending 的長訊檔;不放寬讀檔,一條路對所有走 dm 的家。
        body = inline_body(body, update=(expected_ack == ACK_TOKEN_UPDATE))
    # target_code=None 保留給既有 Python 測試替身；正式 CLI 路徑一律明傳。
    # 空字串是「查過但量不到」,不得退回窗號形成第二入口。
    dm_result = {}
    sent_to = target_code if target_code is not None else window
    if self_fetch_ready(window, sha, provider, environ=env):
        rc = 0
    elif target_code == "":
        if report is not None:
            report["target_code"] = ""
        return "undelivered"
    else:
        rc, dm_result = unpack_deliver_rc(
            deliver_fn(sent_to, body, dry_run=dry_run))
    if report is not None:
        report["target_code"] = sent_to
        if dm_result.get("stage"):
            report["dm_stage"] = dm_result["stage"]
        qid = dm_result.get("id")
        if qid:
            report["queue_id"] = qid
    if rc == DM_RC_STUCK:
        # ★★ 2026-08-22:dm 契約新增 rc=4(進了沒送出/框內狀態不可判,絕不重送)。
        #   這一行原本是 `if rc != 0: return "undelivered"`,而 "undelivered" 的
        #   語意就寫在上面的 docstring:「帳上留白,下一輪 --missing 會再抓到」
        #   = **自動重投**。rc=4 重投就是在對方框裡疊第二份。
        #   ★ 四態壓回兩態,而壓扁的方向恰好是會出事的那一邊。
        #   帳上必須留一筆:留白的意思是「從未投遞」,而這次不是。
        if not dry_run:
            frame_path = snapshot_stuck_frame(
                window, seat_id, capture(window) or "", capture_sgr(window) or "")
            try:
                record(ledger_path, STUCK_LOGICAL_KIND, seat_id, window, sha)
            except Exception as err:          # noqa: BLE001 帳本寫失敗要自成一態出聲,不壓回 undelivered
                print("seat-onboard: " + T("aib.onboardcli.ledger_write_failed", window=window, err=err), file=sys.stderr)
            # ★ 讓它現形:stuck 與 undelivered 的處置**相反**,而開席鏈那一行
            #   只說「可稍後 --missing 補投」—— 對 stuck 而言那是錯的指示
            #   (rc=4 絕不重送;補投會在對方框裡疊第二份)。
            print("seat-onboard: " + T("aib.onboardcli.stuck_in_composer", window=window,
                                          path=frame_path or T("aib.onboardcli.frame_save_failed")),
                  file=sys.stderr)
        return "stuck_in_composer"
    if rc != 0:
        return "undelivered"
    if dry_run:
        return "acked"          # 乾跑不碰帳本也不等回執
    # v0.70.0:baseline 是投遞當下的**整份畫面**(字串),守望只看之後新出現的行(見 new_lines)
    baseline = capture(window) or ""
    record(ledger_path, "message.outbound", seat_id, window, sha)
    # 橋接席(sand/codexbot/grokbot)不會在 pane 回「已就緒」——outbound 後立即
    # inbound(the maintainer 2026-09-18 first-success)。CLI 席仍走 detach / wait。
    if provider in AIBOT_PROVIDERS:
        record(ledger_path, "message.inbound", seat_id, window, sha)
        try:
            open_attempt.mark_ready_for_window(
                env.get("EZ2_AIB_INSTANCE_HOME"), window)
        except Exception as err:  # noqa: BLE001
            watch_error_note("ready 記不進開席碼表 window=%s: %s" % (window, err))
        return "acked"
    if detach_ack:
        # O4:脫離守望用載入窗,不用呼叫端傳來的同步 timeout。
        spawn_ack_watcher(window, seat_id, sha, baseline, ledger_path,
                          DETACHED_ACK_TIMEOUT_S, interval_s, ack_token=expected_ack)
        return "pending"
    if wait_ack(window, baseline, timeout_s, interval_s, sleep=sleep, ack_tokens=(expected_ack,)):
        record(ledger_path, "message.inbound", seat_id, window, sha)
        try:
            open_attempt.mark_ready_for_window(
                env.get("EZ2_AIB_INSTANCE_HOME"), window)
        except Exception as err:  # noqa: BLE001
            watch_error_note("ready 記不進開席碼表 window=%s: %s" % (window, err))
        return "acked"
    return "no_ack"


# ---- CLI -----------------------------------------------------------------

def _template_path(arg):
    return arg or os.path.join(ROOT, TEMPLATE_REL)


def list_missing(state_root=None, ledger_path=None, template_sha=None):
    """**唯讀**:列出開席單缺口。零 dm、零帳本寫入。

    ★ B21/B18.4 裁定三(cap `a seat` 08-29 18:0x):`--missing` 這種
      **名字像查詢、動作是投遞**的子命令要拆兩半。實害是有人以為它是「列出缺的」
      而跑了它,結果投了兩封。艦規那一句:
      **跑任何子命令前先 grep 它會不會寫(帳本/dm);名字不算證據。**

    ★★ 本支**不自己算挑人邏輯**,一律委派 `onboard_gap.ack_gap()`。
      第三份挑人邏輯就是第三真相源,而那正是 B18.4 整件事在關的病。

    ★ 兩個寫法上的硬約束(契約 §四之六,dev2 `a seat` 增補;cap 22:22 准),
      **不要「修一致」回屋內慣例**:
      1. `import onboard_gap` + **呼叫點屬性存取**。`from onboard_gap import ack_gap`
         會在 import 當下把函式物件綁死,替身之後改不動它 ——
         **屬性存取是「單源」這個性質唯一能被行為測到的形。**
      2. 具名函式(不是內聯在 `main()`):讓臂**不必經子行程** ——
         **子行程裡的東西替身進不去。**
    """
    import onboard_gap                      # 走 sys.modules,不用 importlib 另載一份
    return onboard_gap.ack_gap(state_root=state_root, ledger_path=ledger_path,
                               template_sha=template_sha)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="aib seat onboard",
        description="開席單投遞+回執(A)與補投(B)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--window", help="單席:hub 窗 id(如 @4)")
    g.add_argument("--missing", action="store_true",
                   help="(相容別名,等同 --deliver-missing)補投:帳上無回執或停在舊版模板的席位")
    # ★ 裁定三:查詢與投遞拆成兩個名字,動詞明寫在名字上。
    #   `--missing` 保留為別名而不是刪掉:`src/cockpit/aib` 的提示字串與多份 docs
    #   還在教人打它,直接拿掉會讓那些路徑變成 rc=64 用法錯。
    g.add_argument("--list-missing", action="store_true",
                   help="**唯讀**列出缺口:零 dm、零帳本寫入")
    g.add_argument("--deliver-missing", action="store_true",
                   help="補投(明示動詞;與 --missing 相同)")
    p.add_argument("--role", default="", help="--window 時的角色顯示名")
    p.add_argument("--template")
    p.add_argument("--ledger")
    p.add_argument("--seats")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    p.add_argument("--retry-no-ack", action="store_true",
                   help="--missing 時連「投過但沒回執」的席也重投")
    p.add_argument("--detach-ack", action="store_true",
                   help="投遞後不等回執,交脫離的守望者(開席路徑用)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="機器可讀結果行")
    # 內部模式:由 spawn_ack_watcher 叫起,只做「等回執+入帳 inbound」。
    # 不列在對外契約裡,但也不隱藏——藏起來的內部旗標會讓下一個人以為
    # 帳上那筆 inbound 是憑空出現的。
    p.add_argument("--tmux-bin", default="",
                   help="--await-ack 子行程用:起跑時綁定的 tmux 絕對路徑")
    p.add_argument("--await-ack", action="store_true",
                   help="(內部)只等回執並入帳,不投遞")
    p.add_argument("--seat-id", default="")
    p.add_argument("--sha", default="")
    p.add_argument("--baseline", type=int, default=0)
    p.add_argument("--ack-token", default="",
                   help="這一次該回的回執字(已就緒/已更新);不給=兩種都認")
    p.add_argument("--baseline-file", default="",
                   help="投遞當下的畫面檔(v0.70.0);給了就只看之後新出現的行,讀完刪")
    p.add_argument("--prepare-self-fetch", action="store_true",
                   help="O5:只把開席單落到檔(給啟動參數),不 dm")
    p.add_argument("--skipped-notice", action="store_true",
                   help="09-14 選單選了「不載入」:只投一行「你的選擇是【不載入】…重新載入怎麼做」,不投開席單、不入帳")
    p.add_argument("--notice-file", default="", metavar="PATH",
                   help="投這個檔的內容(載入舊席位的開場說明;seat_load.py 產)。同 --skipped-notice 的投遞與家別規則")
    p.add_argument("--timeout-notice", default="", metavar="SECONDS",
                   help="09-20 選單逾時:投一行「N 秒未選,已自動不載入」(不投開席單)")
    p.add_argument("--profile", default="full", choices=PROFILES,
                   help="full=全部(預設)/ rules=只投鐵則段(09-14 選單)")
    p.add_argument("--provider", default="",
                   help="--prepare-self-fetch 時的家(claude/…)")
    args = p.parse_args(argv)

    # ★ 唯讀那半在**任何**副作用之前就走完:不解席位、不投遞、不入帳。
    #   放這麼前面是刻意的 —— 「唯讀」要靠**路徑上沒有寫入動作**成立,
    #   不是靠後面每一個寫入點各自記得判旗標(那種寫法會在新增第 N 個寫入點時破)。
    if getattr(args, "prepare_self_fetch", False):
        if not args.window or not _WID_RE.fullmatch(args.window):
            print("seat-onboard: " + T("aib.onboardcli.need_window_self_fetch"),
                  file=sys.stderr)
            return 2
        provider = (args.provider or "").strip() or "claude"
        if provider not in SELF_FETCH_PROVIDERS:
            return 2
        tmpl = _template_path(args.template)
        try:
            with open(tmpl, "rb") as fh:
                raw = fh.read()
        except OSError as err:
            print("seat-onboard: " + T("aib.onboardcli.template_unreadable", err=err),
                  file=sys.stderr)
            return 2
        sha = template_sha256(raw)
        text = apply_profile(raw.decode("utf-8", "replace"), args.profile)
        seat_file, cabin_label = resolve_seat_context(args.window)
        # 自取路徑也要最新 sweep,但**不得**寫成與 onboard_one 同一行字面:
        # test-seat-onboard-reading-order 的還原刀以那一行為錨、要求全檔恰好一次
        # (09-11 驗證真紅:錨變兩處,刀就不知道該落哪一行)。
        sweep_pair = resolve_latest_sweep()
        latest_sweep, sweep_note = sweep_pair
        seat_id, role = args.seat_id or "?", args.role or "?"
        base = args.seats or seats_dir()
        for wid, sid, r in load_seats(base):
            if wid == args.window:
                seat_id, role = sid, args.role or r
                break
        body = render(text, role, seat_id, args.window, seat_file, cabin_label,
                      latest_sweep, sweep_note)
        _persona = persona_block(args.window, provider, args.seats or None)
        if _persona:
            body = body + "\n\n" + _persona      # 09-30:自取的家也帶人格
        _handoff = handoff_notice_block(args.window, args.seats or None)
        if _handoff:
            body = body + "\n\n" + _handoff      # 10-01:前身沒交接 ⇒ 自取的家一樣看得到
        path = write_self_fetch_drop(args.window, body, sha, provider)
        if not path:
            return 2
        print(path)
        return 0

    if getattr(args, "skipped_notice", False) or getattr(args, "timeout_notice", "") or getattr(args, "notice_file", ""):
        # the maintainer 19:2x/19:3x:選「不載入」⇒ 對話框送出「你的選擇是:【不載入】…重新載入怎麼做…不想載入可直接對話」;
        # 逾時 ⇒ 送出「N 秒未選,已自動不載入」。走同一條 deliver(dm-ctl),不入開席帳本
        # (帳本語意是「投了開席單」,這兩行不是開席單)。
        if not args.window or not _WID_RE.fullmatch(args.window):
            print("seat-onboard: " + T("aib.onboardcli.need_window_notice"), file=sys.stderr)
            return 2
        base = args.seats or seats_dir()
        code = target_code_for_window(base, args.window)
        if not code:
            return 3
        # ★ the maintainer 2026-09-24:「把系統通知濾掉,不要轉進對話」。橋接席(codexbot/grokbot/sand)
        #   的 pane 就是桌面對話的入口 —— 貼進去的每一行都會被轉給模型、留在對話裡。
        #   選單通知是給 CLI 席看的操作說明,對橋接席既無選單可選也無開席單可載,
        #   投進去只是雜訊(09-24 實例:模型認真回 dm 指出兩則通知互相矛盾)。
        #   不投、明說、rc=0(不是失敗:對這種席「不投」就是正確結果)。
        # ★ 09-25 C21:API-key REPL(providers.json auth=api_key)同一病——貼進提示符
        #   被草稿守衛當成人打的稿,擋住後來的 dm;按 Enter 則通知進模型。不投。
        _prov = provider_of_window(base, args.window)
        _api_key = api_key_provider_ids()
        if _prov in AIBOT_PROVIDERS or _prov in _api_key:
            _kind = T("aib.onboardcli.kind_bridge") if _prov in AIBOT_PROVIDERS else T("aib.onboardcli.kind_api_key")
            print("seat-onboard: " + T("aib.onboardcli.notice_not_for_bridge", window=args.window, kind=_kind, provider=_prov))
            return 0
        if getattr(args, "notice_file", ""):
            try:
                with open(args.notice_file, encoding="utf-8") as fh:
                    note = fh.read().strip()
            except OSError as err:
                print("seat-onboard: " + T("aib.onboardcli.notice_file_unreadable", err=err), file=sys.stderr)
                return 2
            # review r4 M1:這一份是「載入舊席位」的多行開場說明(人格包/交接/最近幾輪,常數 KB),
            #   整段多行直接貼會被 CLI 當成多次 Enter ⇒ 能自己讀檔的家(SELF_FETCH)照舊落檔給席去讀。
            # ★ review r11 M1:走 dm 的家(copilot/gemini 等,inline_onboard_provider)讀不到落檔 —— 換成指標
            #   = 人格包與基因(基因開著時 seat_load 插在最前面,光基因就約 1.2KB)一樣都沒進對話。
            #   ⇒ 跟開席單同一個做法:攤成一行(各行去頭尾空白、丟空行,以「 / 」接起來),整行貼、不落檔。
            relay_off = inline_onboard_provider(_prov)
            if relay_off:
                note = lint_safe(INLINE_SEP.join(p for p in (ln.strip() for ln in note.splitlines()) if p))
            rc, _dm = unpack_deliver_rc(deliver(code, note, dry_run=args.dry_run, no_relay=relay_off))
            print("seat-onboard: %s notice-file -> rc=%s" % (args.window, rc))
            return onboard_notice_exit(rc)
        key = "aib.onboard.timeout_notice" if args.timeout_notice else "aib.onboard.skipped_notice"
        params = {"window": args.window, "seconds": str(args.timeout_notice)}
        try:
            sys.path.insert(0, HERE)
            from i18n_lib import Translator
            line = Translator().t(key, params)
        except Exception:
            line = "!!%s!!" % key
        if line.startswith("!!"):
            line = (("%s 秒未選,已自動不載入。若想載入全部或只鐵則,可執行:aib seat onboard --window %s --profile full|rules;若不想載入,可直接對話!" % (args.timeout_notice, args.window)) if args.timeout_notice else
                    ("你的選擇是:【不載入】。若想重新載入或載入全部,可重新執行:aib seat onboard --window %s "
                     "--profile full(全部)/ --profile rules(只鐵則);若不想載入,可直接對話!" % args.window))
        # ★ 09-26:deliver 回 (rc, 結果);以前整個 tuple 拿去比 (0, 3) 恆不成立 ⇒ 投進去了也回 3,
        #   aib 每次都印「提示投不進」(活艙 143 次選單 83 次假警報)。同檔其他呼叫處都經 unpack_deliver_rc。
        # 09-30 19:5x:選「不載入」或逾時的席也要拿到基因(開關開著才接;攤成一行,API 席/橋接席上面已擋掉)
        line = genes_line(line)
        # 10-10 the maintainer「加進開席規則,所有席都要跑 aib now」:選「不載入」或逾時的席讀不到鐵則段,
        #   鐵則九(回報主項目)照樣接在這一行後面
        line = lint_safe(INLINE_SEP.join([line, now_rule_line()]))
        # 10-01:選「不載入」也跳不過交接檢查 —— 前身沒交接的提醒照樣接在這一行後面(攤成一行、過出站檢查)
        _handoff = handoff_notice_block(args.window, base)
        if _handoff:
            line = lint_safe(INLINE_SEP.join([line, " ".join(_handoff.split())]))
        rc, _dm = unpack_deliver_rc(deliver(code, line, dry_run=args.dry_run, no_relay=True))
        print("seat-onboard: %s %s -> rc=%s" % (args.window, "timeout-notice" if args.timeout_notice else "skipped-notice", rc))
        return onboard_notice_exit(rc)

    if getattr(args, "list_missing", False):
        import onboard_gap
        try:
            gap = list_missing(ledger_path=args.ledger or None)
        except onboard_gap.Undetermined as err:
            print("seat-onboard --list-missing: " + T("aib.onboardcli.list_missing_unknown", err=err), file=sys.stderr)
            return 3
        rows = gap["no_ack"]
        if args.json:
            print(json.dumps(gap, ensure_ascii=False))
        else:
            print(T("aib.onboardcli.gap_head", n=len(rows), bad=gap.get("bad_lines", 0)))
            for r in rows:
                print("  %-10s %-5s %s" % (r.get("cabin") or "?",
                                           r.get("window") or "?", r["seat_id"]))
            if not rows:
                print("  " + T("aib.onboardcli.none"))
        return 1 if rows else 0

    if getattr(args, "tmux_bin", ""):
        global BOUND_TMUX
        BOUND_TMUX = args.tmux_bin
    if args.await_ack:
        if not (args.window and args.seat_id and args.sha):
            print("seat-onboard: " + T("aib.onboardcli.need_await"),
                  file=sys.stderr)
            return 2
        led = args.ledger or default_ledger_path()
        baseline = args.baseline
        if getattr(args, "baseline_file", ""):
            try:
                with open(args.baseline_file, encoding="utf-8") as fh:
                    baseline = fh.read()              # 字串 ⇒ 只看新出現的行
            except OSError as err:
                watch_error_note("baseline 檔讀不到 %s: %s(退回次數判準)" % (args.baseline_file, err))
            try:
                os.remove(args.baseline_file)
            except OSError:
                pass
        toks = (args.ack_token,) if getattr(args, "ack_token", "") in ACK_TOKENS else ACK_TOKENS
        why = wait_ack_reason(args.window, baseline, args.timeout, args.interval, ack_tokens=toks)
        if why == "acked":
            record(led, "message.inbound", args.seat_id, args.window,
                   args.sha)
            # O1:三格碼表的最後一個事件(席位讀完開席單)。碼表不擋任何事:失敗只落錯誤檔。
            try:
                open_attempt.mark_ready_for_window(os.environ.get("EZ2_AIB_INSTANCE_HOME"), args.window)
            except Exception as err:  # noqa: BLE001
                watch_error_note("ready 記不進開席碼表 window=%s: %s" % (args.window, err))
            return 0
        # O4:沒等到也要留痕。原本這裡靜靜回 3 而守望者的 stdout/stderr 是 DEVNULL,
        #   於是「守望放棄了」與「還在等」在帳上與畫面上逐字相同。
        kind = {"pane_dead": ACK_PANE_DEAD_LOGICAL_KIND,
                "capture_unavailable": ACK_CAPTURE_UNAVAILABLE_LOGICAL_KIND,
                "quota": ACK_QUOTA_LOGICAL_KIND}.get(
            why, ACK_TIMEOUT_LOGICAL_KIND)
        try:
            record(led, kind, args.seat_id, args.window, args.sha, timeout_s=args.timeout)
        except Exception as err:  # noqa: BLE001 留痕失敗不改 rc 語意,但要落到看得到的地方
            # 脫離守望的 stderr 是 DEVNULL(spawn_ack_watcher),print 沒人看得到 ⇒ 寫錯誤檔(有界)。
            msg = "回執守望結束(%s)但帳本留痕失敗 seat=%s window=%s: %s" % (
                why, args.seat_id, args.window, err)
            print("seat-onboard: " + msg, file=sys.stderr)
            watch_error_note(msg)
        return 3

    tmpl = _template_path(args.template)
    try:
        with open(tmpl, "rb") as fh:
            raw = fh.read()
    except OSError as err:
        print("seat-onboard: " + T("aib.onboardcli.template_unreadable", err=err),
              file=sys.stderr)
        return 2
    sha = template_sha256(raw)
    text = apply_profile(raw.decode("utf-8", "replace"), args.profile)
    ledger_path = args.ledger or default_ledger_path()
    base = args.seats or seats_dir()

    results = []
    try:
        if args.window:
            if not _WID_RE.fullmatch(args.window):
                print("seat-onboard: " + T("aib.onboardcli.bad_window", value=repr(args.window)),
                      file=sys.stderr)
                return 2
            seat_id, role = "?", args.role or "?"
            for wid, sid, r in load_seats(base):
                if wid == args.window:
                    seat_id, role = sid, args.role or r
                    break
            target_code = target_code_for_window(base, args.window) or ""
            report = {}
            state = onboard_one(args.window, seat_id, role, text, sha,
                                ledger_path, args.timeout, args.interval,
                                dry_run=args.dry_run,
                                detach_ack=args.detach_ack,
                                target_code=target_code,
                                provider=provider_of_window(base, args.window),
                                report=report)
            row = {"window": args.window, "seat_id": seat_id, "state": state}
            row.update(report)
            results.append(row)
        else:
            if not os.path.isdir(base):
                print("seat-onboard: " + T("aib.onboardcli.no_seats_dir", path=base),
                      file=sys.stderr)
                return 2
            index, bad = load_index(ledger_path)
            if bad:
                print("seat-onboard: " + T("aib.onboardcli.bad_ledger_lines", n=bad),
                      file=sys.stderr)
            want = {ONBOARD_NEVER, ONBOARD_STALE}
            if args.retry_no_ack:
                want.add(ONBOARD_NO_ACK)
            for wid, sid, role in load_seats(base):
                # ★ 稿還停在那一席的輸入框裡 → 這一輪跳過。重投=疊兩份,
                #   而 --missing 的整個語意是「沒送到的補送」。
                if sha in ((index.get(sid) or {}).get("stuck") or set()):
                    results.append({"window": wid, "seat_id": sid,
                                    "reason": "onboard_stuck",
                                    "state": "stuck_in_composer"})
                    continue
                reason = onboard_reason(index.get(sid), sha)
                if reason not in want:
                    continue
                target_code = target_code_for_window(base, wid) or ""
                report = {}
                state = onboard_one(wid, sid, role, text, sha, ledger_path,
                                    args.timeout, args.interval,
                                    dry_run=args.dry_run,
                                    detach_ack=args.detach_ack,
                                    target_code=target_code,
                                    report=report)
                row = {"window": wid, "seat_id": sid,
                       "reason": reason, "state": state}
                row.update(report)
                results.append(row)
    except OnboardError as err:
        print("seat-onboard: %s" % err, file=sys.stderr)
        return 2

    for r in results:
        enrich_result_with_queue_delivery(r)
    if args.json:
        print(json.dumps({"template_sha256": sha, "results": results},
                         ensure_ascii=False))
    for r in results:
        print(format_onboard_final_line(r))
    if not results:
        print("seat-onboard: " + T("aib.onboardcli.nothing_pending"))
        return 0
    # rc 0 = 每一席都已回執,或已投遞且回執交給守望者(detach 模式的正常
    # 結局)。rc 3 只留給**真的出事**:投不進去,或同步等待逾時。
    # ★ 若把 pending 也算 3,開席每次都會印「未取得回執」——那句話會在
    #   一切正常時天天出現,兩天之後沒有人再看它。誠實的閘不等於吵的閘。
    return 0 if all(r["state"] in ("acked", "pending") for r in results) else 3


if __name__ == "__main__":
    sys.exit(main())
