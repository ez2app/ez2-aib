#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib-hdr — 產「第一行」:時間 + 人格 + 角色 + 引擎 + 席碼 + CTX(一律機器產)。

派工 a seat 2026-08-23 12:35。起因是他手抄自己上一則的時間與 CTX,
時間累積誤差一個半小時、CTX 一直寫 49% 而實際 71%。

★★ 本工具要消滅的判準:**一個看起來精確的數字,標的可能是一件沒發生過的量測**,
   而它與「我沒查」在版面上逐字相同。

排版:the maintainer 2026-08-23 12:5x 指定,六處只換外觀、資料層一格沒動 ——
  時區 +0800→台北 / 直線分隔→單一空格 / 角色字→emoji / claude-Opus 5→CLA /
  席:xxxxx→@xxxxx / CTX n%已用(machine)→n%。

人格三態(the maintainer 12:56 裁定,推翻 root 12:53 的兩態;判定條件寫死在 read_persona 檔頭):
  沒有可量的來源 → 逐字「量不到」/ 量到了但還沒綁 → 逐字「待綁人格」/ 綁了 → 人格名。
  ★ the maintainer 原話:「先寫量不到,量到再寫待綁人格」。
  ★ 分不出來的話,「系統沒有這個概念」會被讀成「系統知道它待綁」——而後者聽起來像快好了。

三條規矩(逐條有臂,見 tests/test-hdr-line.py):
  一 每一格都現量。量不到就印「量不到」——**不得留空、不得沿用上一次的值**。
  二 CTX 一律轉成「已用」。兩引擎語意相反(claude 報已用、codex 報剩餘),
     同一個位置放兩個相反的數字,讀的人會誤判。
  三 連續兩次呼叫時間戳完全相同 → 判異常(時鐘停了,或有人在快取)。

★ 還有一格是 root 自己點出來的:**工具存在不等於工具被用**。
   所以每產一行就落一筆收據,並提供 --verify 證明某一行是工具產的而不是人打的。

用法:
  aib-hdr.py                 印一行(並落收據)
  aib-hdr.py --verify '<行>'  驗一行是不是工具產的
  aib-hdr.py --self-check    只跑自檢,不印行
"""
import pathlib
import json
import os
import re
import subprocess
import sys
import time
import uuid

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 十三條地基逐字列著「i18n 是地基」。
# ★★ 順序是承重的(33f17 抓到):連續兩次 insert(0) 會讓後 insert 的排前面 ⇒ 主庫贏本樹,
#   而 i18n_lib 的字典根綁 __file__ ⇒ 會讀回主庫舊字典。本樹 insert(0)、fallback append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)


UNKNOWN = "量不到"           # ★ 唯一的缺值表示法。不得用空字串、"?"、"-" 或上一次的值。
FRESH_SEC = 600              # 沿用 src/claimtruth/ctx-handoff-nudge.sh 既有門檻,不另發明
AIB_HOME = os.environ.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
CTX_DIR = os.environ.get("EZ2_CTX_DIR") or os.path.join(os.path.expanduser("~"), ".ez2", "ctx")
RECEIPT_DIR = os.path.join(AIB_HOME, "state", "aib-hdr")

# ★ H1(判準 docs/criteria/CRITERIA-H1-hdr-selfcheck-multiwriter-4e623-0831.md = 68770bbe):
#   **本行程的寫者識別**,模組載入時產一次。收據檔以 address_code 命名 ⇒ 整席共用、
#   多行程共寫;自檢要問的是「**我**上一次寫的那筆」,不是「檔案最後一筆」。
#   ★ 為什麼不是 pid:pid 會被作業系統回收,另一個行程可能拿到同一個號 ⇒ 假的「同寫者」。
#     隨機 token 由構造保證唯一,不依賴 boot 時戳、不依賴任何 OS 介面。
#   ★ pid 仍照樣寫進收據 —— 那是給人反查用的,不是拿來判同一性的。
WRITER_ID = uuid.uuid4().hex
_SCREEN_USED = re.compile(r"CTX (\d+)%")            # 已用(fleet_patrol.py:86 同一條)
_SCREEN_LEFT = re.compile(r"(\d+)% context left")   # 剩餘 → 要轉
ROWMAP_FRESH_SEC = 120       # rowmap 約每秒重發;120 秒沒動 = 面板沒在跑,那上面的冠位是舊的
TZ_LABEL = os.environ.get("EZ2_TZ_LABEL", "台北")   # the maintainer 指定的字面
CABIN_RESOLVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "cockpit",
    "cabin_resolve.sh")


# ★★★ rowmap 讀取的重試預算。**這個數字有量測依據,不是猜的**(a seat 08-27 22:2x):
#   以 5ms 取樣連續監看 12 秒:1941 次取樣裡 **102 次檔案不存在(5.3%)**,
#   空窗共 3 段,長度 **203 / 205 / 224 ms**(約每 4 秒一次)。
#   ⇒ 舊版重試預算 20+40+60 = **120ms,比空窗短**,所以重試等於沒做。
#   預算取 **500ms**:蓋得住量到的最長空窗(224ms)並留一倍餘裕。
#   ★ 成本只在檔案真的不在時付(約 5% 的呼叫),平均增加延遲 ≈ 12ms。
#
# ★★★ 2026-08-31 06:1x 重量(H2,cap a seat 派;**同一個 5ms×N 秒取樣法,數字才可比**):
#   艙 4901(本席、今晚最忙):2834 次取樣 **558 次不存在(19.69%)**,
#     空窗 3 段 **920 / 945 / 925 ms**  ⇒ 舊預算 500ms **比今晚每一段都短**,重試又等於沒做。
#   同時間另兩艙:bff5 9.0% / 最長 635ms;instance 17 3.0% / 最長 110ms。
#   ⇒ 空窗長度**跨艙差 8 倍、四天內變長 4 倍**(224 → 945)。
#   預算改 **1.9s** = 今晚最長空窗 × 2(裁定給的公式)。
#   ★ 誠實標記一:成本變大了。約 20% 的呼叫會落在空窗裡,那些呼叫最多等 1.9s
#     ——不是 08-27 那個「平均 12ms」的量級。aib-hdr 每產一次表頭就呼叫一次。
#   ★ 誠實標記二:**這個數字正在追一個會動的目標**。空窗長度 ≈ rowmap 的重發週期,
#     由寫端與當下艙數決定,讀端改不了。今天 ×2 蓋得住,明天艙再多就不一定。
#   ★★ 所以這仍然是**緩解不是修好**,而且緩解的邊際效益正在下降 ——
#      真因是寫端 `pane-right-ai.sh` 的 `rm -f`(啟動 :34、EXIT trap :54),
#      那個檔自己的註解 :42 就記著「舊程序的 EXIT trap 會刪掉新程序剛發布的 rowmap」。
#      寫端若改成「只原子覆寫、不刪」,空窗這一類就整個消失,不必再追數字。
#      (src/cockpit/ 不在本件檔域,已回報請授權。)
#   ★★ 這是**讀端對寫端缺陷的緩解,不是修好**:真因在 `src/cockpit/pane-right-ai.sh`
#      啟動與 EXIT trap 的 `rm -f`(該檔自己的註解就記載了這個危害)。
#      預算再大也只是把殘量壓低,**不會變成零** —— 殘量要靠具名理由被歸因,不是被藏起來。
ROWMAP_READ_BUDGET_SEC = 1.9      # 08-31 重量:最長空窗 945ms × 2(見上)
ROWMAP_READ_TRIES = 3         # (保留給非「檔不存在」那幾類的重讀)
PPID_PROBE_TRIES = 3          # 一跳最多問幾次 `ps`(暫時性失敗才需要重試)


def _ppid_chain(pid=None, why=None):
    """本行程的祖先 pid 鏈。★ 席位歸屬只有這條可信:寫死席碼會讓每個人都印同一個碼。

    `why`:可選的 dict。鏈**沒有走到頂**時填入原因;走到頂則不填。

    ★★★ 08-27 22:1x 修(a seat,注入式實證):
      舊版一次 `ps` 失敗就讓整條鏈**靜默截短**,而截短的鏈與「鏈真的到頂了」
      **在回傳值上逐字相同** —— 於是 `resolve_seat` 找不到席位、回 `None`,
      而 `gather` 把它壓成 `mark_src="none"`。**這一型佔全部收據的 9.9%**
      (`a seat` 數 3668 筆),而產出端的 `anomaly` 欄對它**什麼都沒說**。

    ★★ 而它的路徑比「例外被吞掉」更難看:`ps` 失敗時 stdout 是**空的**,
      `int("" or 0)` = **0**,`while pid and pid > 1` 隨即結束 ——
      **它根本走不到 `except: break`,是從「成功」那條路離開的。**
      ⇒ **失效值(空輸出)被解析成一個合法的終止值(0)。**
      所以這裡必須**顯式檢查 returncode 與空輸出**,不能只包 try。
    """
    pid = pid or os.getpid()
    chain, seen = [], set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        chain.append(pid)
        nxt, err = None, None
        for attempt in range(PPID_PROBE_TRIES):
            err = None
            try:
                out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=5)
            except Exception as exc:                     # timeout / OSError
                err = type(exc).__name__
            else:
                if out.returncode != 0:
                    err = "ps_rc%d" % out.returncode
                elif not out.stdout.strip():
                    # ★ 空輸出**不是** ppid=0。舊版就是在這裡把失敗讀成「到頂了」。
                    err = "ps_empty"
                else:
                    try:
                        nxt = int(out.stdout.strip())
                    except ValueError:
                        err = "ps_unparsable"
            if err is None:
                break
            time.sleep(0.02 * (attempt + 1))            # 暫時性:退避後再問一次
        if err is not None:
            if why is not None:
                why["reason"] = "ppid_probe_failed_%s" % err
                why["truncated_at"] = pid
                why["tries"] = PPID_PROBE_TRIES
            break                                        # 截斷,但**不再是靜默的**
        pid = nxt
    return chain


def resolve_seat(home=None, chain=None, why=None):
    """靠 ppid 鏈找到自己的席位檔。找不到回 None —— **不猜、不掉回主艙**。

    ★ 主艙 <home>/seats/wN.json 與 instances/<N>/seats/wN.json **同檔名同窗號**,
      而主艙那份常是化石(pid 已死)。掉回去會得到一個**格式完全正確的錯答案**。
      所以這裡只掃 instances/*/seats/,並且要求 pid 真的在我的祖先鏈上。
    """
    home = home or AIB_HOME
    cw = {}
    chain = chain or _ppid_chain(why=cw)
    inst_root = os.path.join(home, "instances")

    def _fail(reason):
        # ★ 四種處境不得共用一個 None(a seat 交還材料逐字):
        #   無 instances / 席位檔讀不回來 / **ppid 鏈沒走完** / 真的沒有。
        #   前三是「我沒量到」,第四才是答案 —— 收據裡要有欄位分得開。
        if why is not None:
            why["reason"] = cw.get("reason") or reason
            if cw.get("truncated_at"):
                why["truncated_at"] = cw["truncated_at"]
        return None

    if not os.path.isdir(inst_root):
        return _fail("no_instances_dir")
    for inst in sorted(os.listdir(inst_root)):
        sdir = os.path.join(inst_root, inst, "seats")
        if not os.path.isdir(sdir):
            continue
        for name in sorted(os.listdir(sdir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(sdir, name), "r", encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("schema") != "aib-seat/v1":
                continue
            if rec.get("pid") in chain:
                rec["_path"] = os.path.join(sdir, name)
                rec["_instance"] = inst
                return rec
    return _fail("no_seat_matches_chain")


def read_gauge(session_uuid, now=None, ctx_dir=None):
    """機器探針:$EZ2_CTX_DIR/<uuid>.pct。回 (dict, age_sec) 或 (None, None)。

    ★ 這個檔的 pct **就是已用**——判準不是我推的:src/claimtruth/ctx-handoff-nudge.sh
      用 pct>=85 提醒交接、>=95 紅線,只有「已用」講得通。
    """
    if not session_uuid:
        return None, None
    path = os.path.join(ctx_dir or CTX_DIR, "%s.pct" % session_uuid)
    if not os.path.isfile(path) or os.path.islink(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            g = json.load(fh)
        ts = time.strptime(g["observed_at"][:19], "%Y-%m-%dT%H:%M:%S")
        import calendar
        age = (now or time.time()) - calendar.timegm(ts)
        return g, age
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def screen_socket(instance=None, socket=None):
    """畫面後備要用的 tmux socket。順位:明示參數 > 環境變數 > **由席位檔的 instance 導出**。

    ★ 2026-08-23 19:0x 修:原本只讀 `EZ2_AIHUB_SOCKET`,而一般 shell 沒設它 ——
      於是畫面後備**根本沒被嘗試**,結果與「畫面上真的沒有 CTX」逐字相同,都印「量不到」。
      root 實測撞到:畫面明明是 `CTX 77%`,工具說量不到。
      **一個靜默的前置條件,把「我沒去量」變成了「量不到」** —— 那正是這支工具要消滅的東西。
    ★ instance 是從席位檔所在目錄來的(`instances/<N>/seats/`),不是猜的。
    """
    explicit = socket or os.environ.get("EZ2_AIHUB_SOCKET")
    if explicit or not instance:
        return explicit
    try:
        result = subprocess.run(
            ["/bin/bash", CABIN_RESOLVER, "--hub", str(instance)],
            capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    resolved = result.stdout.strip()
    return resolved or None


def read_screen_ctx(window_id, socket=None):
    """畫面值(降級後備)。回 (pct_used, raw) 或 (None, None)。

    ★ fleet_patrol.py:130-136 已經裁過這件事:machine > screen > none,而且**要標源**,
      因為畫面那條特別脆(窗太窄、狀態列改版、剛開席還沒渲染都會變 None 或舊值)。
    ★ 兩引擎語意相反,所以 `N% context left` 要轉成 100-N 才能放進同一格。
    """
    sock = socket
    if not sock:
        return None, "no_socket"
    if not window_id:
        return None, "no_window"
    try:
        out = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", window_id],
                             capture_output=True, text=True, timeout=8)
        if out.returncode != 0:
            return None, "capture_rc%d" % out.returncode
        screen = out.stdout
    except Exception as exc:
        return None, "capture_error:%s" % type(exc).__name__
    left = _SCREEN_LEFT.findall(screen)
    if left:
        return 100 - int(left[-1]), "left"      # ★ 剩餘語意 → 轉成已用
    used = _SCREEN_USED.findall(screen)
    if used:
        return int(used[-1]), "used"
    return None, "screen_empty"


PENDING = "待綁人格"          # the maintainer 2026-08-23 12:56 指定的字面(第二態)


def read_persona(code, home=None):
    """人格三態(the maintainer 2026-08-23 12:56 裁定,推翻 root 12:53 的兩態)。回 (文字, 來源標籤)。

        沒有可量的來源  → 逐字「量不到」   系統對人格這件事一無所知
        量到了但還沒綁  → 逐字「待綁人格」 有來源、有這個席、就是還沒綁上
        綁了            → 人格名

    ★ 判定條件寫死在這裡,四種情況各有歸屬,**不得有第四種掉進其中一格**:
      (a) `identity/bindings.json` 讀不到/不存在 → `量不到`(`no_source`)
      (b) 讀得到,但這個五碼**不在** bindings 裡 → `待綁人格`(`unbound`)
          ★ 這一格 root 要我判,我判「待綁」,理由比「來源存在」更窄:
            **bindings.json 是一份「綁定」的登記簿,不是「席位」的登記簿** ——
            未綁的席本來就不會出現在裡面,所以「不在裡面」正是**未綁的正常表示法**,
            不是知識的缺口。反過來若整個檔讀不到,我們才是真的什麼都不知道。
      (c) 在 bindings 裡但沒有 `persona_uid` → `待綁人格`(`unbound_no_uid`),同上。
      (d) ★ 有 `persona_uid` 但 `cards.json` 查不到名字 → **`量不到`**(`binding_broken`)。
          這是第四種情況,我**不讓它掉進「待綁」**:登記簿說綁了而卡片不見了,
          那是**壞掉的綁定**不是「還沒綁」;印「待綁」會把一個缺陷說成一個正常的中間狀態。
          兩個可用字面裡,`量不到` 是唯一不會多宣稱的那個,而收據裡記著真正的原因。

    ★ 來源鏈與 `src/cockpit/seat_address.py:359` 的 `nickname_of()` 逐環相同
      (`bindings.json.bindings[<五碼>].persona_uid` → `cards.json.cards[<uid>].display_name`)。
      **不直接呼叫它的唯一理由是:它刻意把三種失敗全收斂成 `None`** —— 而那正是這裡要拆開的。
    ★ `EZ2_PERSONA` 不再當來源:全機沒有這個變數,拿它當來源等於給一格永遠空的欄位。
    ★ 名字只用於**顯示**。`seat_address.py:357` 寫明:括號內的字任何程式不得解析
      —— 拿顯示標籤做判斷就是把它升格成身份。本工具不對它做任何判斷。
    """
    root = home or AIB_HOME
    bpath = os.path.join(root, "identity", "bindings.json")
    cpath = os.path.join(root, "identity", "cards.json")
    try:
        with open(bpath, "r", encoding="utf-8") as fh:
            binds = json.load(fh).get("bindings") or {}
    except (OSError, ValueError, AttributeError):
        return UNKNOWN, "no_source"
    if not isinstance(code, str) or code == UNKNOWN:
        return UNKNOWN, "no_code"
    # ★ 09-29 a seat:bindings.json 只列「人格與專案(路由)都綁了」的席,所以上面 (b)(c) 那句
    #   「不在裡面就是未綁」對只綁人格的席不成立 —— 真機上 a seat 綁著人格、頂行整晚印「待綁人格」。
    #   顯示投影 seat-display.json 綁了人格就有(`seat_address.nickname_of` 09-27 已經先讀它)。
    #   這裡只在 (b)(c) 要回「待綁」之前問它一次;問不到、或它也說沒有,四種情況一格都不變。
    def _shown_name():
        try:
            with open(os.path.join(root, "identity", "seat-display.json"), "r", encoding="utf-8") as fh:
                shown = (json.load(fh).get("display") or {}).get(code)
        except (OSError, ValueError, AttributeError):
            return None
        name = shown.get("display_name") if isinstance(shown, dict) else None
        return name.strip() if isinstance(name, str) and name.strip() else None

    # ★ 用「鍵在不在」判,不用真假值:`{}` 是 falsy,拿 `if not entry` 會把
    #   「有列但空的」誤判成「沒有列」,兩者的收據原因就分不開了(R16 抓到的)。
    if not isinstance(binds, dict) or code not in binds:
        shown_name = _shown_name()
        if shown_name:
            return shown_name, "bound_display"
        return PENDING, "unbound"
    entry = binds.get(code)
    uid = (entry or {}).get("persona_uid") if isinstance(entry, dict) else None
    if not uid:
        shown_name = _shown_name()
        if shown_name:
            return shown_name, "bound_display"
        return PENDING, "unbound_no_uid"
    try:
        with open(cpath, "r", encoding="utf-8") as fh:
            cards = json.load(fh).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return UNKNOWN, "binding_broken"
    name = (cards.get(uid) or {}).get("display_name")
    if isinstance(name, str) and name.strip():
        return name.strip(), "bound"
    return UNKNOWN, "binding_broken"


def read_rowmap(instance, seat_id, home=None, now=None, why=None):
    """讀 `instances/<N>/cockpit/ai-rowmap.json` 裡自己那一列。回 (row, age) 或 (None, None)。

    ★★ 為什麼是讀不是算:`render_lib.py:571-577` 已經裁過同一件事 ——
      「👑 的真規則住在 pane-right-ai.sh(全域唯一、只給第一艦隊首位、未編制永不戴冠),
       那條規則不該被抄第二份;本欄讓標題**讀那條規則算出來的結果**,而不是重算一次。」
      它記載的事故就是抄第二份的後果:**右欄畫 👑、標題畫 ⚓,同一席同一瞬間兩個畫面說相反的話。**
      我用 role=="root" 自己發冠,就會是第三把尺。
    ★ 只讀本艙那份。主艙 `~/.ez2-aib/cockpit/ai-rowmap.json` 實測 mtime 停在 02:45、
      裡面是別代的席位 —— 又一個「格式完全正確的錯答案」。
    """
    def _no(reason):
        # ★★★ 08-27 22:1x(a seat):這裡原本**五條路徑共用一個 None** ——
        #   沒 instance / 檔不存在 / 讀壞或 JSON 壞 / 格式不對 / 檔讀得好但沒有我這一列。
        #   最後一條與第二條語意完全相反(一個是「我看過了,你不在」、一個是「我沒看到檔」),
        #   而它們在回傳值上逐字相同。⇒ 每一條都要有名字。
        if why is not None:
            why["reason"] = reason
        return None, None

    if not instance or not seat_id:
        return _no("no_instance_or_seat")
    path = os.path.join(home or AIB_HOME, "instances", str(instance), "cockpit", "ai-rowmap.json")
    rows = age = None
    err = None
    deadline = (now or time.time()) + ROWMAP_READ_BUDGET_SEC
    attempt = 0
    while True:
        err = None
        attempt += 1
        # ★★★ `isfile` 也要進重試圈(08-27 22:2x 實測才知道):
        #   40 次裡 3 次拿到 `rowmap_missing` —— 那個檔**短暫消失又回來**。
        #   `os.rename` 沒有這種窗口,所以寫者不是 atomic 的那一種
        #   (「已知寫者都走 atomic_json」這個假設對這個檔**不成立**)。
        #   ⇒ **一個消失幾毫秒又回來的檔,是暫時性,不是不存在。**
        #   ★ 但重試耗盡後仍要說 `rowmap_missing`,不得改口說別的 ——
        #     重試是降低發生率,不是改變它是什麼。
        if not os.path.isfile(path):
            err = "rowmap_missing"
            if time.time() >= deadline:
                break
            time.sleep(0.025)
            continue
        try:
            age = (now or time.time()) - os.path.getmtime(path)
            with open(path, "r", encoding="utf-8") as fh:
                rows = json.load(fh)
        except OSError as exc:
            err = "rowmap_oserror_%s" % type(exc).__name__
        except ValueError:
            # ★ JSON 解不開 = **正在被改寫**(非原子寫者;`a seat` 用並行改寫者
            #   在 400 次裡重現 198 次)。這是**暫時性**的,退避後再讀一次才是對的處置;
            #   舊版直接回 None,而那個 None 與「檔不存在」同形。
            err = "rowmap_partial_json"
        if err is None:
            break
        if attempt >= ROWMAP_READ_TRIES and time.time() >= deadline:
            break
        time.sleep(0.025)
    if err is not None:
        # ★ 重試耗盡後仍照實說是哪一類 —— 重試降低發生率,不改變它是什麼。
        return _no(err)
    if not isinstance(rows, list):
        return _no("rowmap_not_a_list")
    for r in rows:
        d = (r or {}).get("detail") if isinstance(r, dict) else None
        if not isinstance(d, dict):
            continue
        if d.get("seat") == seat_id and d.get("kind") == "main":
            return d, age
    # ★ 這一條**不是**探測失敗:檔讀得好好的,只是沒有我這一列。它要與上面分得開。
    if why is not None:
        why["reason"] = "row_absent_from_rowmap"
    return None, age


def _display_model(raw):
    """`.pct` 的 model → 畫面字;規則正本在 src/cockpit/model_label.py,這裡不抄第二份。

    ★ 讀不到那支模組就回 None ⇒ 抬頭原樣退回三字碼。**抬頭不得因為一個顯示用的
      增強欄而掛掉** —— 它是每一則回報的第一行,掛了整條回報就送不出去。
    """
    for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                              "src", "cockpit"),
                 os.path.join(os.path.expanduser("~"), "ez2-aib", "src", "cockpit")):
        try:
            if cand not in sys.path:
                sys.path.insert(0, cand)
            from model_label import display_model
            return display_model(raw)
        except Exception:
            continue
    return None


def _engine_letter(provider, home=None):
    """provider -> 三字縮寫。★ 讀 providers.json 那份資料,不自己抄一張表。"""
    for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                              "src", "cockpit", "providers.json"),
                 os.path.join(os.path.expanduser("~"), "ez2-aib", "src", "cockpit", "providers.json")):
        try:
            with open(cand, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        items = data.get("providers") if isinstance(data, dict) else data
        for it in (items or []):
            if isinstance(it, dict) and it.get("id") == provider and it.get("letter"):
                return it["letter"]
    return None


def _label_zone():
    """標籤是預設的「台北」(沒設 EZ2_TZ_LABEL)⇒ Asia/Taipei;自己設了別的標籤 ⇒ None(照舊用機器時間,不猜它是哪一區)。
    EZ2_TZ_NAME 可以指定時區名(IANA)。讀不到時區資料 ⇒ None。"""
    name = os.environ.get("EZ2_TZ_NAME") or ("Asia/Taipei" if "EZ2_TZ_LABEL" not in os.environ else "")
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - 沒有時區資料 ⇒ 退回機器時間
        return None


def gather(now=None, home=None, ctx_dir=None):
    """量所有格子。每一格獨立;任何一格量不到都不影響其他格,也不沿用舊值。"""
    now = now if now is not None else time.time()
    seat_why = {}
    seat = resolve_seat(home=home, why=seat_why)
    f = {}
    tz = _label_zone()
    if tz is not None:                                      # 10-14:標「台北」就照台北算,不跟機器時鐘(雲端是 UTC,標頭曾差 8 小時)
        import datetime
        dt = datetime.datetime.fromtimestamp(now, tz)
        f["time"] = dt.strftime("%Y-%m-%d %H:%M")
        f["tz_offset"] = dt.strftime("%z")
    else:
        lt = time.localtime(now)
        f["time"] = time.strftime("%Y-%m-%d %H:%M", lt)
        f["tz_offset"] = time.strftime("%z", lt)           # 留在收據裡:標籤說謊時查得出來
    f["tz_label"] = TZ_LABEL                                # the maintainer 指定字面
    # 人格三態:判定條件全在 read_persona 檔頭。
    f["role"] = (seat or {}).get("role") or UNKNOWN
    su = (seat or {}).get("session_uuid")
    f["address_code"] = (su[-5:] if su else None) or UNKNOWN  # 位址=session_uuid 尾五碼
    f["persona"], f["persona_src"] = read_persona(f["address_code"], home=home)
    f["seat_id"] = (seat or {}).get("seat_id") or UNKNOWN   # 舊碼,席位記憶目錄的鍵

    # ── 角色圖示與引擎縮寫:讀 rowmap 算好的結果,不重算(見 read_rowmap 檔頭)──
    row_why = {}
    row, rage = read_rowmap((seat or {}).get("_instance"), f["seat_id"] if f["seat_id"] != UNKNOWN else None,
                            home=home, now=now, why=row_why)
    f["rowmap_age"] = int(rage) if rage is not None else None
    if row and rage is not None and rage <= ROWMAP_FRESH_SEC:
        f["mark"] = row.get("mark") or UNKNOWN
        f["mark_src"] = "rowmap"
        f["engine_letter"] = row.get("engine") or UNKNOWN
        # ★ 兩個來源算同一個東西就要對帳,不要靜默挑一個。
        rl = str(row.get("label") or "")
        f["label_disagree"] = bool(
            rl and f["address_code"] != UNKNOWN and rl != "@" + f["address_code"])
        f["rowmap_label"] = rl or None
    else:
        f["mark"] = UNKNOWN            # ★ rowmap 舊了就說量不到,不得用一頂舊的冠
        # ★★★ 這裡**刻意保留字面 `"none"`**,理由是被一支釘子打出來的(08-27 22:2x):
        #   我原本改成 `"none_<理由>"` 想讓它可歸因,而 `SourceVocabularyIsPinned`
        #   立刻紅了 —— 因為判定面的 (b) 用**等值**比對 `PRODUCER_NO_SOURCE == "none"`,
        #   改名之後那些失效值**不再等於它**,(b) 會**靜默變成恆真**。
        #   ⇒ **不得拿「讓失效值不再被認得」去換可歸因性。**
        #   兩件事可以並存:**值保持原字面**(判定面照樣抓得到),
        #   **理由住在自己的欄位** `row_why` / `seat_why`(收據裡查得到)。
        #   ★ 那支釘子是別席的,而它擋下的正是我這一刀 —— 它在做它該做的事。
        f["mark_src"] = "stale" if row else "none"
        f["engine_letter"] = UNKNOWN
        f["label_disagree"] = False
        f["rowmap_label"] = None
    # ★★ BD(09-03 交接單記過):`/clear` 換 session 之後,**席位檔登記的 uuid
    #   那份 .pct 就不再更新**,而它不會消失 —— 它只是變舊。09-05 實測本席:
    #   登記 uuid 的檔 age=136159s(1.6 天)寫著 model=Fable 5.1、pct=85;
    #   現任 session 的檔 age=4s 寫著 model=Opus 5 (1M context)、pct=30。
    #   ⇒ 照登記 uuid 讀,抬頭會印一個**錯的、而且完全像對的**模型名。
    #   採集端(pane-right-ai.sh)早就走 resolve_live_session 這條縫,抬頭沒接上。
    #   ★ pane 取不到(不在 tmux 裡、形狀不合)時該函式原樣回 registered,
    #     所以這一段對治具與艙外呼叫是零行為改變。
    su_live, f["session_rebound"] = su, False
    # ★★ 09-05 B8 驗證抓到(test_hfix_b):席**解析不到**時 su=None,而重綁函式拿 None
    #   當登記值,任何 live 觀測都可能被誤採 ⇒ 抬頭綁到**呼叫者自己**的 session,
    #   引擎欄印出呼叫者的模型名(帶空白的兩個 token),把 @碼 擠到第 8 格。
    #   BD 修的是「我的登記 uuid 在 /clear 後變舊」,前提是這席解析得出來;
    #   解析不到就沒有「舊登記」可換,一律不重綁 —— 失效值必須留在失效值。
    try:
        if not su:
            raise LookupError("seat unresolved: no registered uuid to rebind from")
        for _c in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "src", "cockpit"),
                   os.path.join(os.path.expanduser("~"), "ez2-aib", "src", "cockpit")):
            if _c not in sys.path:
                sys.path.insert(0, _c)
        from ctx_by_seat import resolve_live_session
        su_live, f["session_rebound"], _ = resolve_live_session(
            su, ctx_root=ctx_dir,
            instance=(seat or {}).get("_instance") or "main",
            window=(seat or {}).get("window_id"))
    except Exception:
        pass                       # 讀不到那支模組 ⇒ 原樣走登記 uuid,不讓抬頭掛掉
    g, age = read_gauge(su_live, now=now, ctx_dir=ctx_dir)
    if g and age is not None and age <= FRESH_SEC and isinstance(g.get("pct"), (int, float)):
        f["ctx_used"] = int(g["pct"])
        f["ctx_src"] = "machine"
        f["ctx_age"] = int(age)
        f["engine_full"] = "%s/%s" % (g.get("engine") or UNKNOWN, g.get("model") or UNKNOWN)
        # ★ the maintainer 09-05 令:抬頭顯示目前用的模型,不只三字碼。
        #   量不到就留 None ⇒ render() 退回三字碼(退回值刻意與正常值不同形)。
        f["model"] = _display_model(g.get("model"))
        if f["engine_letter"] == UNKNOWN:
            f["engine_letter"] = _engine_letter(g.get("engine"), home=home) or UNKNOWN
    else:
        f["ctx_age"] = int(age) if age is not None else None
        f["gauge_stale"] = bool(g) and age is not None and age > FRESH_SEC
        sock = screen_socket((seat or {}).get("_instance"))
        pct, why = read_screen_ctx((seat or {}).get("window_id"), socket=sock)
        if pct is not None:
            f["ctx_used"], f["ctx_src"] = pct, "screen"
        else:
            # ★ 「量不到」一定要說得出為什麼,否則它與「我沒去量」分不開。
            f["ctx_used"], f["ctx_src"] = None, "none_%s" % (why or "unknown")
        f["engine_full"] = (seat or {}).get("provider") or UNKNOWN
        f["model"] = None          # gauge 不新鮮就沒有可信的模型值,不沿用舊的
        if f["engine_letter"] == UNKNOWN:
            f["engine_letter"] = _engine_letter((seat or {}).get("provider"), home=home) or UNKNOWN
    # A fresh turn can identify its model before token usage is available.
    if g and age is not None and age <= FRESH_SEC:
        f["model"] = _display_model(g.get("model"))
    f["row_why"] = row_why.get("reason")          # rowmap 那一格解不出來時的具名理由
    f["seat_why"] = seat_why.get("reason")        # 解不出來時是誰解不出來,留在收據裡
    f["seat_why_at"] = seat_why.get("truncated_at")
    f["ts_ms"] = int(now * 1000)
    return f


def render(f):
    """the maintainer 2026-08-23 指定的排版。六處只換外觀,資料層一格沒動。

        2026-08-23 13:22 台北 人格:待綁人格 👑 CLA a seat 71%

    ★ 壓縮可以少印細節,**但不得讓兩個可靠度不同的值長得一樣**。
      所以:機器值且新鮮 → 乾淨的 `71%`(完全是 the maintainer 那個樣子);
            畫面降級值 → `~71%`(一個字元,只有降級時才花);
            量不到 → 逐字 `量不到`。
    ★ 行尾的 `#id` 保留:沒有它,同一分鐘內兩次呼叫產的行**逐位元組相同**,
      於是「抄了上一則」會驗出 OK —— 那正是這支工具要擋的東西(見 tests R12)。
    """
    if f["ctx_used"] is None:
        ctx = UNKNOWN
    elif f["ctx_src"] == "machine":
        ctx = "%d%%" % f["ctx_used"]
    else:
        ctx = "~%d%%" % f["ctx_used"]
    # ★ 09-05:引擎欄印模型名;量不到才退三字碼。兩者刻意不同形 ——
    #   看到 `Opus 5` = 引擎與模型都量到;看到 `CLA` = 引擎知道、模型量不到。
    engine_cell = f.get("model") or f["engine_letter"]
    return "%s %s 人格:%s %s %s @%s %s" % (
        f["time"], f["tz_label"], f["persona"], f["mark"], engine_cell,
        f["address_code"], ctx)


def receipt_path(address_code):
    return os.path.join(
        RECEIPT_DIR,
        "%s.jsonl" % (address_code if address_code != UNKNOWN else "unresolved"))


def last_receipt(path, writer=None, scan_tail=400):
    """回收據檔的最後一筆;給了 `writer` 就回**最後一筆同寫者的**。

    ★ H1:只看檔案最後一筆會把「別人剛好同毫秒寫了一筆」讀成「我連寫兩次」。
      而且只比最後一筆還會**漏報**:A→B→A 的順序下,A 真正的時鐘問題會被 B 的列擋掉。
      所以要往回找**同寫者**的那一筆,不是最後一筆。
    ★ 沒有 `writer` 欄的舊列(本檔落地前的 16,000+ 筆)**不可比** —— 一律跳過。
      把它們當成同寫者會讓假紅原樣復活;當成不同寫者則只是這一格對舊列靜默,
      而這是自檢不是安全閘,靜默的代價是可接受的、假紅的代價不是。
    ★ 只讀尾端有界的列:自檢不值得每次全檔掃。
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
    except OSError:
        return None
    for raw in reversed(lines[-scan_tail:]):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if writer is None:
            return rec
        if rec.get("writer") == writer:
            return rec
    return None



def render_top_line_for_seat(seat, home=None, width=None):
    """席檔四欄 → 頂行字串(包 B 第二行)。flags 讀 A count_flags;無則 stub 0。

    ★ fail-closed 同意欄在 top_line;本函式不自行預設 is_human=True。
    """
    hdr_dir = os.path.dirname(os.path.abspath(__file__))
    if hdr_dir not in sys.path:
        sys.path.insert(0, hdr_dir)
    try:
        import top_line as TL
    except ImportError:
        return ""
    cockpit = os.path.join(hdr_dir, "..", "..", "src", "cockpit")
    if cockpit not in sys.path:
        sys.path.insert(0, cockpit)
    fields = {
        "task_uid": "",
        "task_started_at": "",
        "approval_event_uid": "",
        "report_to": "",
    }
    if isinstance(seat, dict):
        try:
            import seat_task_fields as STF
            fields = STF.read_seat_task_fields(seat)
        except Exception:
            for k in fields:
                fields[k] = str(seat.get(k) or "")
    # approval meta:席檔若帶 approval_meta 才用;缺 ⇒ is_human 預設 False ⇒ 未核准
    appr = {}
    if isinstance(seat, dict) and isinstance(seat.get("approval_meta"), dict):
        appr = dict(seat["approval_meta"])
    report_code = ""
    role = (fields.get("report_to") or "").strip()
    if role in ("第一艦長", "第一舰长", "captain", "captain_seat"):
        try:
            if cockpit not in sys.path:
                sys.path.insert(0, cockpit)
            import cooldown
            report_code = (cooldown.captain_seat() or "") or ""
            report_code = str(report_code).lstrip("@")
        except Exception:
            report_code = ""
    drift_n, orphan_n = 0, 0
    # ★ 誠實 stub:本 tip flags.py 無 count_flags(包 A 未合);有則讀。
    try:
        flags_path = os.path.join(hdr_dir, "..", "project", "flags.py")
        if os.path.isfile(flags_path):
            import importlib.util
            spec = importlib.util.spec_from_file_location("aib_flags_hdr", flags_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "count_flags"):
                ev_path = os.environ.get("AIB_DRIFT_EVENTS_JSON", "")
                events = None
                if ev_path and os.path.isfile(ev_path):
                    with open(ev_path, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if isinstance(raw, list):
                        events = raw
                    elif isinstance(raw, dict):
                        events = raw.get("events") or raw.get("items")
                counts = mod.count_flags(events, writeset=None)
                drift_n = int(counts.get("detour_unreturned", 0))
                orphan_n = int(counts.get("orphan", 0))
    except Exception:
        drift_n, orphan_n = 0, 0
    w = width if width is not None else TL.DEFAULT_WIDTH
    try:
        w = int(os.environ.get("AIB_TOP_LINE_WIDTH", w))
    except ValueError:
        pass
    return TL.render_top_line_from_fields(
        fields,
        approval_meta=appr,
        report_code=report_code,
        drift_n=drift_n,
        orphan_n=orphan_n,
        width=w,
    )



def emit(now=None, home=None, ctx_dir=None, receipt_dir=None):
    f = gather(now=now, home=home, ctx_dir=ctx_dir)
    rdir = receipt_dir or RECEIPT_DIR
    os.makedirs(rdir, exist_ok=True)
    rpath = os.path.join(
        rdir,
        "%s.jsonl" % (
            f["address_code"] if f["address_code"] != UNKNOWN else "unresolved"))
    prev = last_receipt(rpath, writer=WRITER_ID)
    anomaly = None
    # ★ 規矩三:連續兩次時間戳完全相同 = 時鐘停了或有人在快取,兩種都該出聲。
    #   ★ H1:「連續兩次」指的是**同一個寫者**的連續兩次。不同行程同毫寫入是正常併發,
    #     不得出聲 —— 假紅會訓練人忽略這個自檢,那比沒有自檢更糟。
    #   ★ 判在**毫秒**上,不判在畫面那個「分」上:同一分鐘內呼叫兩次是正常的,
    #     判在分上會製造假紅,而假紅會訓練人忽略它。
    if prev and prev.get("ts_ms") == f["ts_ms"]:
        anomaly = "identical_ts_ms:%s" % f["ts_ms"]
    hid = uuid.uuid4().hex[:5]
    line = "%s  #%s" % (render(f), hid)
    with open(rpath, "a", encoding="utf-8") as fh:
        # ★ 判斷一:排版把 (machine) 壓掉了,可查性不能跟著沒。全部來源住在收據裡,
        #   由行尾那個一次性 id 反查 —— 這就是為什麼 id 不能為了乾淨而拿掉。
        fh.write(json.dumps({"id": hid, "ts_ms": f["ts_ms"], "line": line,
                             "writer": WRITER_ID, "pid": os.getpid(),
                             "ctx_used": f["ctx_used"], "ctx_src": f["ctx_src"], "ctx_age": f["ctx_age"],
                             "ctx_semantic": "used",     # ★ 判斷二:一律「已用」,而且逐筆寫死
                             "persona": f["persona"], "persona_src": f["persona_src"],
                             "mark": f["mark"], "mark_src": f["mark_src"], "rowmap_age": f.get("rowmap_age"),
                             "seat_why": f.get("seat_why"), "seat_why_at": f.get("seat_why_at"),
                             "row_why": f.get("row_why"),
                             "engine_letter": f["engine_letter"], "tz_offset": f["tz_offset"],
                             "tz_label": f["tz_label"], "label_disagree": f.get("label_disagree"),
                             "anomaly": anomaly},
                            ensure_ascii=False) + "\n")
    # 包 B:第二行 = 頂行(主任務／同意／回報／旗標)
    seat_for_top = None
    try:
        seat_for_top = resolve_seat(home=home)
    except Exception:
        seat_for_top = None
    top = render_top_line_for_seat(seat_for_top, home=home)
    return line, f, anomaly, top


def verify(line, receipt_dir=None):
    """證明某一行是工具產的。回 (verdict, detail)。

    ★ 誠實邊界:這擋的是「手打」與「抄了舊的再改」(= root 今天那個失效模式),
      不是擋惡意偽造 —— 有人真的去跑工具再原封不動貼上,那本來就是工具產的。
    """
    m = re.search(r"#([0-9a-f]{5})\s*$", line.strip())
    if not m:
        return "NOT_TOOL_GENERATED", "行尾沒有工具產的 id"
    hid = m.group(1)
    rdir = receipt_dir or RECEIPT_DIR
    if not os.path.isdir(rdir):
        return "NOT_TOOL_GENERATED", "沒有任何收據檔"
    # ★ H1-c(判準 docs/criteria/CRITERIA-H1c-verify-id-collision-4e623-0831.md,cap 05:31 裁 b):
    #   **掃完所有同 id 的收據再判**,不得掃到第一筆就 return。
    #   id 只有 5 個十六進位字元 ⇒ 碰撞不是異常是必然:存量 16024 筆已有 116 組碰撞,
    #   下一筆碰撞機率 ≈1.5% 且隨筆數平方惡化。掃到第一筆就判的失效形狀是
    #   **一行真的是工具產的卻被指控造假**,而它是隨機的 ⇒「重跑一次就好」會變成解法,
    #   那正是訓練所有人忽略這個判準的方式。
    # ★ H1-c 更正二(cap 05:34):**掃完全部再判,而且不得安靜挑一筆**。
    #   同 id 多筆且行都對得上時,明細要說出「有幾筆、對上幾筆、在哪些席」——
    #   在 (a) 加寬 id 落地之前,那個 id 已經不足以唯一識別一筆收據,
    #   而**明細是使用者唯一看得到那件事的地方**。
    hits = []          # 所有同 id 的收據:(席名, rec)
    exact = []         # 其中行逐字相同的
    for name in sorted(os.listdir(rdir)):
        if not name.endswith(".jsonl"):
            continue
        try:
            fh = open(os.path.join(rdir, name), "r", encoding="utf-8")
        except OSError:
            continue
        with fh:
            for raw in fh:
                if not raw.strip():
                    continue
                # ★ 逐列吞 ValueError:原本 except 包住整個檔的迴圈,一列壞掉的 JSON
                #   會讓那個檔剩下的列全部不看。掃全部之後那會把一份**存在的**收據
                #   判成 NOT_TOOL_GENERATED —— 壞一列不該讓同檔其餘列消失。
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("id") != hid:
                    continue
                hits.append((name[:-6], rec))
                if rec.get("line") == line.strip():
                    exact.append((name[:-6], rec))
    if exact:
        seat, rec = exact[0]
        prov = ("CTX %s%% 語意=%s 來源=%s 齡=%ss / 人格來源=%s / 圖示來源=%s / 時區=%s(%s)" % (
            rec.get("ctx_used"), rec.get("ctx_semantic"), rec.get("ctx_src"),
            rec.get("ctx_age"), rec.get("persona_src"), rec.get("mark_src"),
            rec.get("tz_label"), rec.get("tz_offset")))
        if rec.get("label_disagree"):
            prov += " ★席碼兩來源不一致"
        if rec.get("anomaly"):
            prov += " ★自檢異常:%s" % rec["anomaly"]
        if len(hits) > 1:
            prov += (" ★此 id 有 %d 筆收據(5 hex 碰撞),其中 %d 筆逐字相同;命中席:%s"
                     % (len(hits), len(exact), "、".join(n for n, _ in exact)))
        return "OK", "收據 %s(席 %s)  %s" % (hid, seat, prov)
    if hits:
        # ★ H1 附記三(cap 05:44 裁;量測 root2 a seat 12:3x):**並列第二種可能,並指出第一個不同的位置**。
        #   比對是逐字相等 ⇒ 行內少一個空白就判偽造,而**貼版正是人搬運這種行的正常方式**
        #   (聊天、dm、markdown 表格都會把連續空白壓成一個)。實測:那一行 `(machine)` 與 `#`
        #   之間是兩個空白,壓成一個 ⇒ rc=1;而行首行尾多空白無妨(有 strip)。
        #   ⇒ 工具分不出「有人竄改」與「通道壓掉一個空白」,而它原本挑了**指控人**的那個講法。
        #     給出位置,人一眼就分得出是空白還是內容 —— 這比訓練所有人「不要用貼的」便宜。
        got = line.strip()
        best, best_i = None, -1
        for seat, rec in hits:
            other = rec.get("line") or ""
            i = 0
            while i < len(got) and i < len(other) and got[i] == other[i]:
                i += 1
            if i > best_i:
                best, best_i = (seat, other), i
        where = ""
        if best is not None:
            seat, other = best
            win = 6
            where = (";與席 %s 那筆最接近,第一個不同在第 %d 個字元(第 %d 位元組)"
                     "——收據 %r / 你給的 %r"
                     % (seat, best_i + 1, len(got[:best_i].encode("utf-8")) + 1,
                        other[best_i:best_i + win], got[best_i:best_i + win]))
        return ("TAMPERED_OR_COPIED",
                "id %s 有 %d 筆收據(席:%s),沒有一筆的行與它逐字相同"
                "(抄了再改?**或這一行經過了會壓縮空白的通道**)%s"
                % (hid, len(hits), "、".join(n for n, _ in hits), where))
    return "NOT_TOOL_GENERATED", "id %s 不在任何收據裡" % hid


def main(argv):
    # Parse the complete command shape before calling emit().  Falling through
    # on an unknown flag used to mint a plausible header and receipt, turning a
    # usage error into a write operation.
    if len(argv) > 1 and argv[1] == "--verify":
        if len(argv) != 3:
            print(T("aib.hdr.usage_verify"), file=sys.stderr)
            return 2
        verdict, detail = verify(argv[2])
        print("%s  %s" % (verdict, detail))
        return 0 if verdict == "OK" else 1
    if len(argv) > 1 and argv[1] != "--self-check":
        print(T("aib.hdr.usage"), file=sys.stderr)
        return 2
    if len(argv) > 2:
        print("用法:aib-hdr.py [--self-check|--verify '<行>']", file=sys.stderr)
        return 2
    line, f, anomaly, top = emit()
    if len(argv) > 1 and argv[1] == "--self-check":
        print(json.dumps({"anomaly": anomaly, "ctx_src": f["ctx_src"], "ctx_age": f["ctx_age"],
                          "address_code": f["address_code"], "unknown_fields":
                          [k for k in ("persona", "role", "address_code", "engine")
                           if f.get(k) == UNKNOWN],
                          "top_line": top},
                         ensure_ascii=False))
        return 4 if anomaly else 0
    if anomaly:
        print(T("aib.hdr.anomaly", {"why": anomaly}), file=sys.stderr)
    # 第一行=身分抬頭;第二行=頂行(包 B)。頂行空仍印空行以固定兩行形狀。
    print(line)
    print(top if top is not None else "")
    return 4 if anomaly else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
