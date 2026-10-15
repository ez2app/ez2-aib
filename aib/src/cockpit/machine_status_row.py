#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄最上方的機器狀態列 —— 取值、快取、折到 pane 寬。

## 這支為什麼存在(而不是在 pane-right-tabs.sh 裡直接叫 machine_status)

兩個理由,兩個都是量出來的:

1. **一輪取值 0.72 秒,而右欄迴圈是 5 秒一輪** —— 直接接等於每輪多燒 14% CPU,
   那正是 v0.53(BF)剛把面板負載由 226 壓到 5 的那件事。本檔用**快取 + 背景更新**:
   畫面永遠只讀檔(毫秒級),過期時另起一個行程去更新,**不擋畫面**。
   ⇒ 均攤成本 ≈ 0.72s / TTL,TTL=20s 時是 3.6%。

2. **`machine_status.render()` 回的是單行 126 字元,而右欄只有 44 欄寬** ——
   直接印會爛版。折行用 `render_lib.visible_width`(座艙自己那一層,CJK 算兩格),
   **不從 tools/report 借 `wrap_cells`**:那是回報工具的檔域,跨域引用會讓兩邊改動互相牽制。

## 三態(承 machine_status 的紀律,不得共用同一個字樣)

    有快取且新鮮      → 直接印
    有快取但過期      → 印舊值並標 `↻`,同時背景更新;**不印空白**
    沒有快取(第一輪)→ 印「量測中」,背景更新;下一輪就有值

★ 「還沒量到」與「量到但是 0」在畫面上必須看得出差別 —— 這是 machine_status 檔頭第 11 行
  立的規矩,本檔照著走。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, "render")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from render_lib import BOLD, DIM, GREEN, GREY, RESET, YELLOW, trunc, visible_width  # noqa: E402

# 紅色 render_lib 沒有(它的調色盤只到 YELLOW)。在這裡具名補一個,不去改它的表:
# 那張表是四區配色的正本,為了一格狀態列去動它,爆炸半徑是整個座艙。
RED = "\x1b[31m"
COLOR = {"ok": GREEN, "warn": YELLOW, "hot": RED, "critical": RED,
         "unmeasured": GREY}

DEFAULT_TTL_S = 20
MEASURING = "機器狀態 量測中…"   # 只在字典也載不到時用;正常走 aib.machine.measuring
STALE_MARK = "↻"
REFRESH_LOCK_S = 30              # 已有一次更新在跑就不要再堆


def _state_dir():
    """座艙狀態目錄。★ **沒有 fallback,這是刻意的**。

    原本寫成「讀 env,讀不到就 expanduser 一個寫死的預設狀態目錄」,
    而 `tests/test-seats-dir-resolution.py::test_no_new_resolver_skips_hub_derivation`
    當場抓到:那個 fallback 在多艙環境會**退回主艙**,拿到一個
    「格式正確的錯答案」—— 快取寫進 A 艙、面板從 B 艙讀,而兩邊都不會紅。
    面板自己走的是 `os.environ['EZ2_COCKPIT_STATE_DIR']`(缺就報錯),這裡跟它一致:
    **拿不到就回 None,由呼叫端說「量不到」,不猜一個。**

    ★ 上面那段刻意**不寫出那個預設路徑的字面**:那支臂的偵測器讀的是「剝掉註解後的碼」,
      而 docstring 是字串常數不是註解 —— 我第一版在這裡逐字寫出那個路徑,
      **等於把偵測器要找的樣式寫進它會讀到的地方**,於是它把這支檔判成一個解析器。
      「談論它」與「做它」在那把尺上逐字相同。
    """
    d = os.environ.get("EZ2_COCKPIT_STATE_DIR")
    return d if d else None


def cache_path():
    state = _state_dir()
    return os.path.join(state, "machine-status.json") if state else None


def read_cache(path):
    """回 (資料, 幾秒前量的);讀不到回 (None, None)。

    ★ 快取存的是**格與等級的 JSON**,不是排好版的字串:寬度會變(WINCH / 拆欄),
      存字串的話換寬度就得重新取值 0.72 秒。存資料的話重排是微秒級。
    """
    try:
        age = time.time() - os.stat(path).st_mtime
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict) or not data.get("cells"):
        return None, None
    return data, age


def spawn_refresh(path):
    """背景更新一次。★ 不等它、不看它的 rc —— 這一輪的畫面不依賴它。

    用 <path>.<pid> 暫存再 rename:同時有兩個更新在跑也不會讀到寫到一半的檔。
    已經有一次在跑就不要再堆:leftover 艙很多時,每 5 秒再起一次會把負載
    自己推高,快取反而一直過期。
    """
    lock = path + ".refresh"
    if not _refresh_lock(lock):
        return
    tool = os.path.abspath(__file__)
    tmp = "%s.%d" % (path, os.getpid())
    # ★ 10-03:子行程直接是 python(--produce-to),自己寫 tmp、os.replace 換上、失敗刪 tmp、一定刪鎖。
    #   原本是 /bin/sh -c "(… --produce > tmp && mv -f tmp path || rm -f tmp); rm -f lock",
    #   語意相同,但 sh 那一層影子續跑看不進去 ⇒ 畫右欄的測試全被判「量不到」。見 atomic_refresh。
    # ★ 不用 start_new_session:活席那一格靠 **ppid 鏈**判「這個 pid 還是不是那一席」,
    #   脫鏈啟動(PPID=1)會把那條鏈打斷 ⇒ 該格由「20 席 / 3 艙」變成「量不到」。
    #   實測 09-04 19:1x:加了它,面板上那一格當場壞掉,而其餘七格全部正常
    #   —— 失效面只有一格,而那一格的字樣是「量不到」,看起來像環境問題不像我的 bug。
    #   Popen 本身就不阻塞,分離不是必要的;留在同一個 session 反而讓面板收掉時它一起收。
    _spawn([sys.executable, tool, "--produce-to", path, "--tmp", tmp, "--lock", lock], lock)


def _spawn(argv, lock):
    """背景起 python 子行程;不等它、不看 rc。起不來就當場收鎖。

    ★ Popen 刻意留在本檔(不藏進 atomic_refresh):test-pane-right-status 的
      「不得脫鏈」那把尺讀的就是本檔,Popen 搬走它就讀到空的。
    """
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        try:
            os.remove(lock)
        except OSError:
            pass


def _refresh_lock(lock):
    """搶到鎖回 True;已有一次在跑(未滿 REFRESH_LOCK_S)回 False。"""
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        try:
            age = time.time() - os.stat(lock).st_mtime
        except OSError:
            return False
        if age < REFRESH_LOCK_S:
            return False
        try:
            os.remove(lock)
        except OSError:
            return False
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
    try:
        os.write(fd, b"%d\n" % os.getpid())
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    return True


def wrap(text, width):
    """折到 width 格(CJK 算兩格)。以雙空白(格與格)為斷點,不把「降溫機制 ?」拆開。"""
    if width <= 0:
        return [text]
    out, line = [], ""
    for token in text.split("  "):
        candidate = token if not line else line + "  " + token
        if line and visible_width(candidate) > width:
            out.append(line)
            line = token
        else:
            line = candidate
    if line:
        out.append(line)
    return [trunc(x, width) for x in (out or [""])]


def _paint(text, state):
    """上色並**當場關閉**。★ 不關的話 wrap() 折到下一行時,顏色會染到整行後面 ——
    render_lib.trunc() 有補 RESET,而本檔的 wrap() 是自己寫的,不補就會漏。"""
    return "%s%s%s" % (COLOR.get(state, GREY), text, RESET)


def _cell(by, key, fmt, short=None):
    """一格 → 已上色的短字串;量不到回灰色的 `?`(不回 0、不留空白)。

    忙碌時 top 逾時會把降溫機制標成量不到。若快取裡還有上一筆真值,畫上次的
    語意並加 ↻,不讓「?」在最需要看的時候突然出現。↻ 表示這不是這一秒的現值。
    """
    c = by.get(key) or {}
    label = short or c.get("label") or key
    state = c.get("state") or "unmeasured"
    stale = False
    if state == "unmeasured":
        last = c.get("last_good")
        if isinstance(last, dict) and last.get("state") not in (None, "unmeasured"):
            c = dict(c)
            c["state"] = last["state"]
            if "value" in last:
                c["value"] = last["value"]
            state = c["state"]
            stale = True
        else:
            return _paint("%s ?" % label, state)
    try:
        text = fmt(c)
    except Exception:
        return _paint("%s ?" % label, "unmeasured")
    painted = "%s %s" % (DIM + label + RESET, _paint(text, state))
    if stale:
        painted += STALE_MARK
    return painted


def _cooldown_semantic(cell, tr):
    """把既有三色 state 翻成白話，不在 renderer 複製數值門檻。

    `_cell` 的既有色盤按 ok、warn、hot 上色，上游負責決定實際 state；這裡若
    再看百分比切一次，兩份門檻遲早會漂移。critical 與 hot 本來同為紅色，
    故共用「忙碌」。未知 state 讓 `_cell` 的例外路徑誠實畫成灰色 `?`。
    """
    key = {
        "ok": "aib.cool.not_hot",
        "warn": "aib.cool.normal",
        "hot": "aib.cool.busy",
        "critical": "aib.cool.busy",
    }[cell["state"]]
    return tr.t(key)


def _t():
    """翻譯器。★ 字典壞掉不得讓面板整個不畫 —— 退回一個回 key 的殼,畫面會出現
    `!!aib.machine.busy!!` 這種可見的壞字,比一片空白好抓。"""
    try:
        import i18n_lib
        return i18n_lib.Translator()
    except Exception:
        class _Fallback(object):
            def t(self, key, params=None):
                return "!!%s!!" % key
        return _Fallback()


def _center(line, width):
    """置中。★ 用 visible_width 不用 len():CJK 一個字佔兩格,
    用 len() 會把中文行往左推一半,而那正是「偏左」的成因。"""
    pad = (width - visible_width(line)) // 2
    return (" " * pad + line) if pad > 0 else line


def compose(cells, level=None, width=44, stale=False, legacy=False):
    """排成三行、置中、用白話。

    ★ 版面規矩(the maintainer 09-04 令:置中、走 i18n、國三生白話、字不要多):
        第一行  現在熱不熱 + 因此做了什麼(這是唯一要「讀」的一行)
        第二行  會隨熱度變的三格:忙碌 / 溫度 / 降溫機制
        第三行  不太會變的三格:記憶體 / 暫存 / AI 幾位
      ⇒ 眼睛平常只掃第一行;要細看才往下。
    ★ 技術字一律翻成白話:load1 -> 忙碌、CPU 溫度 -> 溫度、
      kernel_task -> 降溫機制的語意狀態，
      swap→暫存、席→AI。**理由不是好聽,是這一列給人看第一眼就要懂。**
    ★ 降溫機制只讀既有 state，不在渲染端複製門檻；原始百分比不上畫面。
    """
    tr = _t()
    by = {c.get("key"): c for c in cells if isinstance(c, dict)}
    lines = []
    if level or stale or legacy:
        separated = (isinstance(level, dict) and
                     level.get("load_level") in ("ok", "warn", "hot", "critical") and
                     level.get("thermal_level") in ("ok", "warn", "hot", "critical", "unknown"))
        if legacy or not separated:
            # 舊格式或等級欄不齊,沒有可以沿用的「現在熱不熱」。
            lines.append(_paint(tr.t("aib.machine.pending_update"), "unmeasured"))
        else:
            # 過期仍畫上次的溫度判斷,加 ↻,不把第一行換成「等待更新(舊資料)」——
            # 下面三格數字都還在時,那句話會讓人以為整列都壞了。
            thermal = level["thermal_level"]
            load = level["load_level"]
            thermal_key = {
                "ok": "normal", "warn": "warn", "hot": "hot",
                "critical": "critical", "unmeasured": "unknown",
            }.get(thermal, "unknown")
            # C13(10-15):窄欄時長句會被截成「CPU temperature unavail…」⇒ 放不下就換短句(四語各一)
            head_text = tr.t("aib.machine.thermal.%s" % thermal_key)
            if visible_width(head_text) > width:
                head_text = tr.t("aib.machine.thermal_short.%s" % thermal_key)
            head = _paint(head_text, thermal)
            if load in ("hot", "critical"):
                head += " %s· %s%s" % (DIM, tr.t("aib.machine.load.high"), RESET)
            if stale:
                head += STALE_MARK
            lines.append(head)
    heat = "  ".join(x for x in (
        _cell(by, "load1", lambda c: "%.1f" % c["value"], tr.t("aib.machine.busy")),
        _cell(by, "cpu_temp", lambda c: "%.0f°C" % c["value"], tr.t("aib.machine.temp")),
        _cell(by, "kernel_task", lambda c: _cooldown_semantic(c, tr),
              tr.t("aib.cool.mechanism")),
    ) if x)
    res = "  ".join(x for x in (
        _cell(by, "mem", lambda c: "%.0f%%" % c["value"], tr.t("aib.machine.mem")),
        _cell(by, "swap", lambda c: "%.1fG" % (c["value"] / 1024.0), tr.t("aib.machine.swap")),
        _cell(by, "seats", lambda c: tr.t("aib.machine.seats_value", {"n": int(c["value"])}),
              tr.t("aib.machine.seats")),
    ) if x)
    for block in (heat, res):
        if block:
            lines.append(block)
    out = []
    for line in lines:
        for w in wrap(line, width):
            out.append(_center(w, width))
    return out or [_center(tr.t("aib.machine.measuring"), width)]


def rows(width, ttl=DEFAULT_TTL_S, path=None):
    """畫面用:讀快取(毫秒)、就地排版,過期時背景更新。**取值永遠不在這一層**。"""
    path = path or cache_path()
    if not path:
        return [MEASURING]          # 座艙狀態目錄不明 ⇒ 說量不到,不猜一個艙
    data, age = read_cache(path)
    if data is None:
        spawn_refresh(path)
        return [MEASURING]
    # A future mtime is not fresh evidence: clock skew must not let a cached
    # normal/critical verdict remain current forever.  Treat missing age the
    # same way rather than silently assuming it is current.
    stale = age is None or age < 0 or age > ttl
    level = data.get("level")
    legacy = not isinstance(level, dict) or "load_level" not in level or "thermal_level" not in level
    lines = compose(data.get("cells") or [], level, width, stale=stale, legacy=legacy)
    lines.extend(composer_notice_lines(width))
    if stale or legacy:
        spawn_refresh(path)
    return lines


def _clip_visible(text, width):
    """按**可見寬度**裁,不按字元數:中日韓字一個字佔兩欄,用 len() 裁會爆版。"""
    if visible_width(text) <= width:
        return text
    out = ""
    for ch in text:
        if visible_width(out + ch) > max(1, width - 1):
            return out + "…"
        out += ch
    return out


# ⚠ 那兩行原本貼齊左右邊。左右各留兩格,跟置中的機器狀態列一樣有呼吸。
NOTICE_SIDE_PAD = 2
NOTICE_ZONE_MAX = 6   # 廣場信每位 AI 可佔兩行(the maintainer 09-30 要分行)


def _pad_notice(text, width):
    pad = NOTICE_SIDE_PAD
    inner = max(1, int(width) - 2 * pad)
    return (" " * pad) + _clip_visible(text, inner)


PLAZA_TTL_S = 180     # 廣場摘要要 git fetch 每個遠端(GitHub 那個實測 19 秒),只在背景更新


def _plaza_cache_path():
    state = _state_dir()
    return os.path.join(state, "plaza-summary.json") if state else None


def spawn_plaza_refresh(path, cabin):
    """背景跑 tools/collab/plaza_cabin_summary.py,寫到 path(暫存再 rename,同 spawn_refresh)。"""
    lock = path + ".refresh"
    if not _refresh_lock(lock):
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tool = os.path.join(root, "tools", "collab", "plaza_cabin_summary.py")
    if not os.path.isfile(tool):
        try:
            os.remove(lock)
        except OSError:
            pass
        return
    tmp = "%s.%d" % (path, os.getpid())
    # 10-03:同 spawn_refresh,不經 /bin/sh;寫檔、換上、收鎖由 plaza_cabin_summary --produce-to 自己做。
    _spawn([sys.executable, tool, "--cabin", cabin, "--repo", root,
            "--produce-to", path, "--tmp", tmp, "--lock", lock], lock)


def plaza_lines(now=None):
    """廣場信摘要(the maintainer 09-30:放在右欄上方原本「私訊卡住/未綁人格」那兩行的位置)。

    只讀快取;快取舊了或沒有就背景更新。還沒有快取時不佔行(第一次更新約 20 秒),
    快取舊了照樣顯示舊的字,不留白。
    """
    cabin = (os.environ.get("EZ2_AIB_INSTANCE") or "").strip()
    path = _plaza_cache_path()
    if not cabin or not path:
        return []
    now = time.time() if now is None else now
    try:
        age = now - os.stat(path).st_mtime
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        got = [str(x) for x in (data.get("lines") or []) if x]
    except (OSError, ValueError, AttributeError):
        age, got = None, []
    if age is None or age < 0 or age > PLAZA_TTL_S:
        spawn_plaza_refresh(path, cabin)
    return got


def leftover_line():
    """右欄第 3 頁「離艙」的一行摘要;沒有、或讀不到都回空字串(不佔行)。"""
    try:
        import leftover_seats
        data, _stale = leftover_seats.read_cached()
        return leftover_seats.summary_line(data)
    except Exception:  # noqa: BLE001 —— 畫面層永不因這一行掛掉
        return ""


def composer_notice_lines(width):
    """212:卡稿 → 廣場信。最多 6 行;都沒有整塊空白不佔高。

    ★ the maintainer 09-30:「私訊卡住」「未綁人格」這兩行不再畫在右欄,原位置改放廣場信摘要(plaza_lines)。
    ★ 09-30 the maintainer 令右欄不再顯示「私訊卡住」;計算隨之移除(git 史可找回,最後一版在 v0.69.5)。

    ★ 為什麼不畫在席位列裡:rowmap 與畫面同源,插進席位列會讓每一列的點擊座標
      位移(點 A 跳 B)。本區塊的 offset 由 `pane-right-tabs.sh` 用**實際行數**現算
      (`_mach_n`,同時加到 EZ2_H_OFFSET 與 EZ2_ROW_OFFSET),所以這裡加行是零位移。
    ★ 本區塊是**艙**範圍的;檔名帶艙尾碼,別艙的不會被撈進來。
    ★ 卡稿檔讀不出來時**少一行**,不是印一行錯的。私訊佇列讀失敗印「量不到:原因」,不印 0。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import composer_notice as cnotice
        import glob as _glob
        pattern = os.path.join(cnotice.notice_dir(),
                               "NOTICE-composer-blocked-*-%s.txt" % cnotice.cabin_suffix())
        l8b = []
        for path in sorted(_glob.glob(pattern)):
            data = cnotice.load(path)
            if not data:
                continue
            reason = data.get("reason")
            if reason in ("bind-attention", "bind-candidates",
                          "foreign-draft", "window-alias", "browse-mode", "history-mode"):
                # 輸入框裡那封沒送出的稿仍擋得住信，只是不佔 AI 清單上方。
                continue
            text = cnotice.render(data.get("blocked_seat") or "")
            if not text:
                continue
            if reason == "bind-unbound":
                continue   # the maintainer 09-30:未綁人格不畫在右欄
            l8b.append(text)
        body = []
        for text in l8b:
            body.append(_pad_notice(text, width))
        for text in plaza_lines():
            body.append(_pad_notice(text, width))
        # 09-30 the maintainer:廣場下面空一行,放「離艙未收 N 席 → 看第 3 頁」,下面再空一行;沒有就整段不佔。
        #   只讀 leftover_seats 的快取(舊了它自己背景更新),這一層不掃艙。
        left = leftover_line()
        if left:
            if body:
                body.append("")
            body.append(_pad_notice(left, width))
            body.append(" ")      # 一個空格:外面用 $(...) 接,結尾的純空行會被吃掉
        body = body[:NOTICE_ZONE_MAX]
        if not body:
            return []  # 212:三樣都沒有整塊空白,不佔高
        return [""] + body
    except Exception:           # noqa: BLE001 —— 畫面層永不因回饋器而掛掉
        return []


def attach_last_good(cells, prev_cells):
    """量不到的格帶上一次真值,給畫面畫 ↻。沒有上一筆就維持量不到。

    上一輪若已經是量不到但身上帶著 last_good,這一輪要接著帶,不能只看 state。
    否則忙碌兩輪之後降溫機制會變回光禿的問號。
    """
    prev_by = {c.get("key"): c for c in prev_cells if isinstance(c, dict)}
    out = []
    for c in cells:
        if not isinstance(c, dict) or c.get("state") != "unmeasured":
            out.append(c)
            continue
        old = prev_by.get(c.get("key")) or {}
        if old.get("state") not in (None, "unmeasured"):
            c = dict(c)
            c["last_good"] = {"state": old["state"], "value": old.get("value")}
            out.append(c)
            continue
        nested = old.get("last_good")
        if isinstance(nested, dict) and nested.get("state") not in (None, "unmeasured"):
            c = dict(c)
            c["last_good"] = {"state": nested["state"], "value": nested.get("value")}
            out.append(c)
            continue
        out.append(c)
    return out


def produce(out=None):
    """背景更新:取值 + 算降溫等級 + 落 JSON。★ 這一支才是花 0.72 秒的那一段。"""
    out = sys.stdout if out is None else out
    import machine_status as ms
    import cooldown
    prev = None
    path = cache_path()
    if path:
        prev, _age = read_cache(path)
    cells, took = ms.sample()
    if prev:
        cells = attach_last_good(cells, prev.get("cells") or [])
    level = cooldown.apply(cells)
    json.dump({"cells": cells, "level": level, "took_s": took,
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
              out, ensure_ascii=False)
    out.write("\n")
    return 0


def _flag(argv, name):
    """從 argv 取 `name 值`(取走);沒有回 None。"""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            val = argv[i + 1]
            del argv[i:i + 2]
            return val
    return None


def produce_to(path, tmp=None, lock=None):
    """--produce-to:背景更新的子行程本體(取代原本那行 /bin/sh)。見 atomic_refresh.produce_to。"""
    import atomic_refresh
    tmp = tmp or "%s.%d" % (path, os.getpid())
    return atomic_refresh.produce_to(path, tmp, produce, lock=lock)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    width = int(os.environ.get("EZ2_PANE_W") or 44)
    ttl = DEFAULT_TTL_S
    if "--produce-to" in argv:
        tmp, lock = _flag(argv, "--tmp"), _flag(argv, "--lock")
        dest = _flag(argv, "--produce-to")
        if not dest:
            return 2
        return produce_to(dest, tmp=tmp, lock=lock)
    while argv:
        a = argv.pop(0)
        if a == "--width" and argv:
            width = int(argv.pop(0))
        elif a == "--ttl" and argv:
            ttl = int(argv.pop(0))
        elif a == "--produce":
            return produce()
        elif a == "--rows-only":
            pass
    for line in rows(width, ttl):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
