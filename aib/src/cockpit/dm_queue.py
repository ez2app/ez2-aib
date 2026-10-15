#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ez2 DM 排序暫存:忙碌先排、空閒再送;緊急優先,其餘先到先送。

畫面最新在上、月日時分秒、緊急可編輯。巡查讀全文,列表才裁一列。
進階版(EZ2_LICENSE plan=advanced)+開關打開才啟用。關開關不刪既有列。
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BOOT = _HERE.parent / "bootstrap"
for _p in (_HERE, _BOOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import atomic_json as AJ  # noqa: E402
import license_plan as LP  # noqa: E402
import seats_dir_lib  # noqa: E402
import seat_address as SA  # noqa: E402
# ★ 依賴方向(第 47b 項):本檔 → feature_policy,**單向**。
#   「開關檔住哪」與「准不准」的判斷只住 feature_policy —— 它只做這件事、檔小、
#   不依賴佇列;本檔拿它的結果來用。反過來 feature_policy **不得** import 本檔:
#   會成環,而且別的功能只想問「准不准」,不該被迫拖進排序與投遞這一整套。
#   守這條的是 tests/test-feature-policy-47.py 的 t19(AST 讀兩支的 import)。
import feature_policy as FP  # noqa: E402
# ★ the maintainer 09-30 17:2x 待審區程式歸納:本檔 → dm_triage,**單向**(dm_triage 不 import 本檔;
#   分組規則住那一支、讀寫哪個檔與何時叫醒住這一支)。
import dm_triage as TR  # noqa: E402
import dm_relay as DR  # noqa: E402

POLICY_SCHEMA = "aib-dm-queue-policy/v1"
ORDER_SCHEMA = "aib-dm-queue-order/v1"
ITEM_SCHEMA = "aib-dm-queue-item/v2"
# 第 70 項 P2(接口正本 pending/IFACE-dm-queue-v2-pm5cfee-4c24-0913.md):v1 的檔照讀不改盤,
# 缺的欄位一律 None(畫面印「量不到」),不猜 thread / kind、不補 read_at;`migrate --apply` 才落盤。
LEGACY_ITEM_SCHEMAS = ("aib-dm-queue-item/v1",)
TABS_SCHEMA = "aib-dm-queue-tabs/v1"
OWNER_SCHEMA = "aib-owner/v1"
VIEW_SCHEMA = "aib-dm-queue-view/v1"
WAITING_ORDER_SCHEMA = "aib-dm-queue-waiting-order/v1"   # 待審頁顯示順序(不是送信順序)
VISIBLE = 3
VISIBLE_CHOICES = (1, 3, 5, 10)
SEAT_RE = re.compile(r"^[0-9a-z]{4,8}$")
ITEM_RE = re.compile(r"^q_[0-9a-f]{16}$")
# 字面「esc to interrupt」不當忙碌訊號:指令回音或對話提到它,空閒席會永遠像在忙。
# 忙碌改看兩幀是否在動(秒數遞增那種)。草稿/選單仍擋。
HOLD_WAIT_S = 1.5
CLAIM_STALE_S = 90
# ★ 重送迴圈(qa a seat 09-12 13:54 抓;開發 C a seat 修):投遞端 dm-ctl.sh 的 rc 契約是
#   0 已送 / 2 一個字都沒進目標(可重送)/ 3 已送未驗(勿重貼)/ 4 進了沒送出或不可判(絕不重送)。
#   舊 drain_once 只認 0 刪、其餘一律 unclaim 退回 queued,而右欄每幀 Popen 一次 drain-idle
#   ⇒ 3 與 4 每幀重貼一次,正是契約明文禁止的事;且沒有次數上限、沒有退避、沒有出口。
#   對映表只住這裡一份(生產端訂碼、消費端照抄,不自己解讀):
#     0 → 刪;2 → unclaim 帶 attempts+1,滿 RESEND_MAX_ATTEMPTS 轉 hold;3/4/其他非 0 → hold_human。
#   hold_human 的信不再被 claim,只有人 delete 或 requeue;畫面上寄件人前綴「待人處理」。
HOLD_STATUS = "hold_human"
RESEND_MAX_ATTEMPTS = 3
HOLD_LABEL = "人審"   # the maintainer 09-30 15:3x(收件匣 A 項 4):畫面上的字改兩個字;狀態碼仍是 hold_human、文件裡仍叫待人處理
HOLD_WHY = {
    "rc3": "投遞回 3(已送未驗,契約:勿重貼)",
    "rc4": "投遞回 4(進了沒送出或框內不可判,契約:絕不重送)",
    "rc1": "投遞回 1(lint 硬擋,重送不會成功)",
    "rc_other": "投遞回 %d(不在契約碼表裡,分不出就停手)",
    "attempts": "回 2 可重送但已重送 %d 次仍沒進目標,停手",
    "pane_dead": "收件席的視窗已結束(pane_dead),不投往重用的窗號;重開那一席後 requeue",
}
# ── 第 70 項 P2:狀態、種類、轉移表(只住這裡一份;畫面與任務區讀 payload,不自己算)──
# status 的完整集合。`updated` 不是 status:是衍生旗標 is_update(supersedes 非空且還沒讀),畫面掛角標。
STATUSES = ("queued", "claimed", "delivered", "read", "done", "archived", "deleted", HOLD_STATUS)
# 還在 order.json 裡、會被排序/領走/畫在待審框的三態;離開這三態就從 order.json 拿掉(檔留著當歷史)。
ACTIVE_STATUSES = ("queued", "claimed", HOLD_STATUS)
# waiting tab 的母體(第 13 項):排隊、貼稿中、待人處理、已送達未讀 —— 待審/卡住的列。
WAITING_STATUSES = ("queued", "claimed", HOLD_STATUS, "delivered")
# 用途:只有 order/rule/pipeline(順位/規矩/管線起停)這三種廣播可被同 thread 的新版取代;
# deliver/decide/report(交件/要裁/自報)永遠追加。沒帶 = other(也永遠追加)。
KINDS = ("order", "rule", "pipeline", "deliver", "decide", "report", "other", "notice")   # notice:10-13 S7-2 知會,讀過即備存
BROADCAST_KINDS = frozenset(("order", "rule", "pipeline"))
# 寄件端量到的來源;v1 的 "ai" 讀進來當 seat。human 只在送信端三條件全量到時才會出現(dm-ctl 那一刀)。
SENDER_KINDS = ("seat", "seat_claimed", "human", "system", "unknown")
# seat_claimed:寄件席**有名字但沒有被量到**——名字是呼叫端遞來的證據(codex 的 turn metadata)
# 經本機登記簿對得上活席而來,不是從行程祖先鏈量出來的。刻意不併進 "seat":
# 消費端要放寬到它必須顯式寫出這個值,**放寬因此是一個看得見的決定**。
# 寄件身分的來源(帳本用;弱強度必須在欄位上分得出來,不能只靠措辭)。
# codex_thread_env(09-30):codex 席從 shell 工具打 `aib dm`,證據是 codex 放進工具行程環境的
# CODEX_THREAD_ID;強度與 codex_session_meta 相同(都只到 seat_claimed),分開記是為了讀帳分得出哪條路。
# bg_fork_chain(10-10 a seat,the maintainer 裁):寄件行程是 Claude Code 的背景分身(--fork-session 掛在全機共用的
# 守護程式下),祖先鏈認到分身就停,席碼取分身 --resume 的**原席**;不走 pane_pid_chain 是因為那條會爬到
# 「生守護程式的那一席」(真機:44ed3 的分身被記成 a seat)。強度同 seat(機器從命令列量出,不是自報)。
SENDER_SOURCES = ("pane_pid_chain", "ppid_chain", "codex_session_meta", "codex_thread_env", "bg_fork_chain")
THREAD_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}/[^\s/]{1,64}$")
ARCHIVE_DAYS_DEFAULT = 7          # 備存天數,艦長 09-12 17:52 裁;可用 --days 改,真人另裁才改預設
STALE_MINUTES_DEFAULT = 30        # 第 149 項:未讀超過幾分鐘算「卡住」
# actor 字面:system / seat:<收件席> / sender:<寄件席> / human:<id>。
# TRANSITIONS 的值是 actor **類別**(冒號前那截);seat 類還要等於收件席本人(_transition 裡查)。
TRANSITIONS = {
    ("queued", "claimed"): ("system",),
    ("claimed", "queued"): ("system",),
    ("claimed", "delivered"): ("system",),
    ("claimed", HOLD_STATUS): ("system",),
    ("queued", HOLD_STATUS): ("system",),
    (HOLD_STATUS, "queued"): ("human",),
    ("queued", "archived"): ("system",),          # 只有 why=superseded
    (HOLD_STATUS, "archived"): ("system",),       # 只有 why=superseded
    ("delivered", "read"): ("seat",),
    # 收件席每輪自己 list 佇列時信還是 queued(沒被 drain 過);讀完標 read ⇒ 離開 order.json、不再投(a seat 00:3x 抓:
    # 少這一列,規矩 3 的機器版會變成「讀完被拒、然後 drain 再貼一次」)。hold 的信收件席親口說看到了,hold 就解了。
    # claimed → read 刻意不開:排空器手上正在貼。
    ("queued", "read"): ("seat",),
    (HOLD_STATUS, "read"): ("seat",),
    ("delivered", "done"): ("seat",),
    ("read", "done"): ("seat",),
    ("delivered", "archived"): ("system",),
    ("read", "archived"): ("system",),
    ("done", "archived"): ("system",),
}
for _s in STATUSES:
    if _s != "deleted":
        TRANSITIONS[(_s, "deleted")] = ("human",)
del _s
# ★ the maintainer 09-30 17:1x 裁「超過 12 小時自動備存,超過 30 天自動刪除」:
#   完成(done)滿 12 小時 ⇒ 系統備存(why=done_12h);備存(archived)滿 30 天 ⇒ 系統刪除(留墓碑,why=archived_30d)。
#   系統刪除**只開這一條**:archived→deleted 且 why 必須是 AUTO_DELETE_WHY(_transition 裡查);
#   其他狀態刪除照舊只有 human。待審/進行中不因時間自動動(那是還沒做完的信)。
AUTO_ARCHIVE_DONE_HOURS = 12
AUTO_DELETE_ARCHIVED_DAYS = 30
AUTO_ARCHIVE_WHY = "done_12h"
AUTO_DELETE_WHY = "archived_30d"
RETENTION_EVERY_S = 600           # 自動保留整理最多每 10 分鐘跑一次(排空器每幀都會叫到這裡)
TRANSITIONS[("archived", "deleted")] = ("human", "system")
# ★ the maintainer 09-30 18:2x「好」(系統代標通知已讀):叫醒那一席之後,「通知」組(dm_triage.NOTICE_* 表,只放完全不用動作的字)
#   由系統代標已讀,why 必須是 AUTO_READ_WHY(_transition 裡查),信檔記 read_by=system / read_why。
#   只開 queued/delivered → read;人審(hold_human)不代標 —— 那是等真人看的。
AUTO_READ_WHY = "auto_notice"
TRANSITIONS[("queued", "read")] = ("seat", "system")
TRANSITIONS[("delivered", "read")] = ("seat", "system")
ARCHIVE_WHYS = ("days", "superseded", AUTO_ARCHIVE_WHY, "duplicate", "test_ping", "notice_read")   # 後兩個:stage-tidy 機器層(10-13)
TRANSITION_REASON = {
    "ok": "",
    "transition": "這個狀態不能這樣改",
    "actor": "這個動作不是你能做的",
    "actor_missing": "要帶 --by(收件席讀完請用 read,不要 delete)",
    "evidence": "完成要帶證據(存在的檔 / 你寄出的回信 id / 查得到的 commit),或用 --why 寫結案理由",
    "not_found": "這封信已經不在了",
}
ACTOR_FORMAT_HINT = "--by 格式不對(收到 %(given)s):收件席寫 seat:%(seat)s,真人寫 human:<名字>,系統寫 system"
TZ_NAME = "Asia/Taipei"
PRIORITIES = ("urgent", "important", "normal", "reminder")
ORIGINS = ("live", "test")
PRIORITY_RANK = {"urgent": 0, "important": 1, "normal": 2, "reminder": 3}
# 沒寫 origin 的舊列:這幾個座位碼只出現在測試治具,不是活艙席。
TEST_FIXTURE_SEATS = frozenset(("a0005", "a0007", "abcde"))
TEST_MSG_RE = re.compile(
    r"測試訊息|repress lane|qa B7|qa true prompt|指名末五|"
    r"正確座位碼正向端|改留言測試|測試送出",
    re.I)
MOVE_REASON = {
    "ok": "",
    "missing": "找不到這則，沒有移動",
    "edge": "已經在最上或最下，沒有移動",
    "cross_band": "沒有可對調的（要同一緊急程度）",
    "cross_seat": "不同席的信不對調（各席自己排）",
    "cross_kind": "排隊中與已送達的信不對調",
}
PRIORITY_LABEL = {
    "urgent": "緊急",
    "important": "重要",
    "normal": "一般",
    "reminder": "提醒",
}
# 256 色:紅/橙/淺灰/藍。渲染層原樣用,不在這裡拼畫面。
PRIORITY_SGR = {
    "urgent": "\x1b[38;5;196m",
    "important": "\x1b[38;5;208m",
    "normal": "\x1b[38;5;250m",
    "reminder": "\x1b[38;5;39m",
}

DEFAULT_POLICY = {
    "schema": POLICY_SCHEMA,
    "enabled": False,
}


def _env(env):
    return os.environ if env is None else env


def state_root(env=None):
    root, _src = seats_dir_lib.state_root(_env(env))
    return Path(root)


def queue_root(env=None):
    return state_root(env) / "state" / "dm-queue"


def policy_path(env=None):
    """本艙佇列開關檔。住哪的規則只有一份:`feature_policy.cabin_policy_path`
    (艙家 > 明寫的狀態根 > None,永不退回 ~/.ez2-aib)。"""
    return FP.cabin_policy_path("dm-queue-policy.json", env)


def owner_path(env=None):
    return state_root(env) / "identity" / "owner.json"


SEAT_REC_SCHEMA = "aib-seat/v1"


def wid_for_seat(seat, env=None):
    """席位五碼 → (窗號, provider);查不到回 (None, None),**不猜**。

    ★ 第 149 項:投遞位址是「關於現在」的事實(那一席此刻在哪個窗),
      而信上的 wid 是寄件當下抄下來的一份影本 —— 寄件路徑忘了帶就是空的,
      帶了也可能在信排隊的這段時間過期。所以投遞當下要回登記表現查。
      災情形狀:wid 空 ⇒ capture_screen 回 None ⇒ seat_is_hold 判 no-screen
      ⇒ 每一輪都 hold ⇒ 那封信**永遠不送**,而寄件人與收件人都不會收到任何錯誤。

    ★ 五碼取法走 seat_address.uuid_tail5(全庫唯一一處),不自己切 [-5:];
      `seat_id` 欄是舊碼,**不得**拿來當投遞目標(seat_address 檔頭 B11)。
    ★ 檔名與記錄內 window_id 要對得上才採信(與 activity_probe.seat_for_window 同一道)。
    """
    if not SEAT_RE.fullmatch(seat or ""):
        return None, None
    try:
        sdir = Path(seats_dir_lib.seats_dir(environ=_env(env)))
        paths = sorted(sdir.glob("*.json"))
    except (OSError, TypeError, ValueError):
        return None, None
    for path in paths:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != SEAT_REC_SCHEMA:
            continue
        wid = str(rec.get("window_id") or "")
        if not wid or path.name != ("w%s.json" % wid.lstrip("@")):
            continue
        if SA.uuid_tail5(rec.get("session_uuid")) != seat:
            continue
        prov = rec.get("provider")
        return wid, (prov.strip().lower() if isinstance(prov, str) and prov.strip() else None)
    return None, None


def drain_target(seat, item, env=None):
    """投遞這一則要用的 (窗號, provider):登記表現查優先,查不到才退回信上抄的那一份。

    ★ 回值只看 seat 與登記表,**不看是哪一則信** —— 於是「排頭那則 hold 住,
      後面可送的也不送」在結構上不可能再由位址造成(hold 是席位的性質,不是信的)。
      釘住這件的是 test_hold_target_does_not_depend_on_which_item_is_at_the_head。
    """
    wid, prov = wid_for_seat(seat, env)
    item = item if isinstance(item, dict) else {}
    if not wid:
        wid = str(item.get("wid") or "")
    if not prov:
        prov = (str(item.get("provider") or "").strip().lower() or None)
    return (wid or ""), prov


HUB_NAME_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}$")


ORIGIN_NAME_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}$")
# 跨主機收件(dm_xhost):xhost:<sshd 量到的對方 IP>。忙碌入列後重投靠它把抬頭位址寫回對方,
#   不然重投那一輪會寫成收件機自己的 IP(10-15 實測:Pro 的席被標成 Mini 的區網 IP)。
XHOST_ORIGIN_RE = re.compile(r"^xhost:[0-9A-Fa-f:.]{2,45}$")


def _origin_or_none(value):
    """寄件來源機器名(sender_origin 推的腳本名或五碼,或 xhost:<IP>);不合形回 None。"""
    v = str(value or "").strip()
    return v if ORIGIN_NAME_RE.fullmatch(v) or XHOST_ORIGIN_RE.fullmatch(v) else None


_CABIN_HUB_CACHE = {}


def _cabin_hub(name):
    """艙 id → socket 名,向單源 cabin_resolve.sh --hub 要(test-cabin-resolution R8:不在這裡拼)。
    10-02(a seat):原本兩處自己拼前綴加艙 id。一艙查一次進快取;解析器不收/失敗 ⇒ None(不猜),
    逾時不進快取。"""
    if name in _CABIN_HUB_CACHE:
        return _CABIN_HUB_CACHE[name]
    resolver = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cabin_resolve.sh")
    try:
        p = subprocess.run(["bash", resolver, "--hub", name], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    hub = p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None
    _CABIN_HUB_CACHE[name] = hub
    return hub


def _hub_or_empty(value):
    """tmux socket 名只收安全字元(它會變成 `tmux -L <名>` 的引數);不合形回空字串,不猜。"""
    v = str(value or "").strip()
    return v if HUB_NAME_RE.fullmatch(v) else ""


def _seat_records_all_cabins(seat, env=None):
    """這台機器上登記這個席碼的所有席位檔 → [(窗號, provider, socket, 是本艙?)],本艙在前。

    ★ the maintainer 09-30 15:3x(收件匣 A 項 1):wid_for_seat 只看「排空器自己這一艙」的 seats 目錄,
      而佇列根是整台機器共用的 ⇒ 別艙席的信在本艙查不到登記、退回信上抄的窗號,再拿**本艙**的
      tmux 去問那個窗號 —— 問到的是本艙同號的另一個窗(或根本沒有)。這裡把主艙 seats +
      instances/*/seats 全掃,socket 取席位檔自己寫的那一欄(沒寫的:本艙用排空器的 hub,
      instances/<艙>/seats 用 aihub-<艙>,與 seats_dir_lib 的對映同一條;主艙沒寫就不收,不猜)。
    ★ 採信條件與 wid_for_seat 同一道:schema 對、檔名與記錄內 window_id 對得上、uuid_tail5 等於席碼。
    """
    if not SEAT_RE.fullmatch(seat or ""):
        return []
    e = _env(env)
    cur_hub = _hub_or_empty(e.get("EZ2_AIHUB_SOCKET"))
    try:
        cur_dir = os.path.realpath(seats_dir_lib.seats_dir(environ=e))
        root = str(state_root(env))
    except (OSError, TypeError, ValueError):
        return []
    dirs = [(cur_dir, None)]
    try:
        dirs.append((os.path.realpath(os.path.join(root, "seats")), None))
        inst_root = os.path.join(root, "instances")
        if os.path.isdir(inst_root):
            for name in sorted(os.listdir(inst_root)):
                dirs.append((os.path.realpath(os.path.join(inst_root, name, "seats")), _cabin_hub(name)))
    except OSError:
        pass
    out, seen = [], set()
    for d, inst_hub in dirs:
        if d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        is_cur = (d == cur_dir)
        for path in sorted(Path(d).glob("w*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("schema") != SEAT_REC_SCHEMA:
                continue
            wid = str(rec.get("window_id") or "")
            if not wid or path.name != ("w%s.json" % wid.lstrip("@")):
                continue
            if SA.uuid_tail5(rec.get("session_uuid")) != seat:
                continue
            hub = _hub_or_empty(rec.get("socket")) or (cur_hub if is_cur else _hub_or_empty(inst_hub))
            if not hub:
                continue
            prov = rec.get("provider")
            prov = prov.strip().lower() if isinstance(prov, str) and prov.strip() else None
            out.append((wid, prov, hub, is_cur or hub == cur_hub))
    out.sort(key=lambda r: not r[3])          # 穩定排序:本艙在前,其餘保持掃描順序
    return out


SEAT_UNRESOLVED_WHY = "seat-unresolved"   # 登記表有這一席、但沒有一艙答得出它的窗 ⇒ 這一輪跳過,不轉待人處理


def drain_hold(seat, item, env=None):
    """排空器的判忙(生產路徑):回 (busy, why)。登記表現查那一席**現在**在哪一艙哪個窗,在那一艙問。

    ★ the maintainer 09-30 15:3x(收件匣 A 項 1)的災情形狀:舊 CLI 的 _hold 拿信上抄的窗號、配排空器**自己這一艙**
      的 socket 去問 ⇒ 別艙席的信 (a) 本艙沒有那個窗 ⇒ no-screen ⇒ 永遠判忙、永遠不送;(b) 本艙那個窗號
      正好是別人的死窗 ⇒ pane_dead ⇒ **把別艙活席的信轉成待人處理**,人看到還以為對方關窗了。
    ★ 選窗順序(每個候選都在它自己那一艙問 #{pane_dead}):
      1. 有一個候選答「活著」⇒ 用它;
      2. 本艙候選問不到(None)⇒ 用它(= 09-30 前的行為:本艙的判準照舊由 seat_is_hold 決定);
      3. 只有候選答「死了」⇒ 用它 —— 死窗是在**那一席自己那一艙**量到的,才算獨立證明;
      4. 登記表有這一席、但每個別艙候選都問不到 ⇒ (True, seat-unresolved):這一輪跳過,
         **不**轉待人處理、**不**判死窗(問不到不等於死,第 54 項 a 的「不猜」);
      5. 登記表完全沒有這一席 ⇒ 退回信上抄的窗號,艙用信上抄的 hub(沒抄 = 排空器這一艙,舊行為)。
    """
    item = item if isinstance(item, dict) else {}
    recs = _seat_records_all_cabins(seat, env)
    if recs:
        live = cur_unknown = dead = None
        for wid, prov, hub, is_cur in recs:
            st = pane_is_dead(wid, hub, env=env)
            if st is False and live is None:
                live = (wid, prov, hub)
            elif st is None and is_cur and cur_unknown is None:
                cur_unknown = (wid, prov, hub)
            elif st is True and dead is None:
                dead = (wid, prov, hub)
        pick = live or cur_unknown or dead
        if pick is None:
            return True, SEAT_UNRESOLVED_WHY
        wid, prov, hub = pick
        prov = prov or (str(item.get("provider") or "").strip().lower() or None)
        return seat_is_hold(wid, hub, provider=prov, env=env)
    wid = str(item.get("wid") or "")
    hub = _hub_or_empty(item.get("hub")) or _hub_or_empty(_env(env).get("EZ2_AIHUB_SOCKET"))
    prov = str(item.get("provider") or "").strip().lower() or None
    return seat_is_hold(wid, hub, provider=prov, env=env)


SEND_LOG_SCHEMA = "aib-dm-send/v1"


def send_log_path(env=None):
    """寄件端紀錄:`<狀態根>/logs/dm-send.jsonl`(追加式)。"""
    return state_root(env) / "logs" / "dm-send.jsonl"


def msg_sha16(msg):
    """內容指紋前 16 —— 留指紋不留全文:日後要回答「送出的是不是同一則」,
    而全文可能含只該給收件人看的東西。"""
    body = msg if isinstance(msg, str) else ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def log_send(seat, msg, sender_label, outcome, queue_id=None, env=None, now=None):
    """寄件端落一行。**永不讓送信失敗**。

    ★ 09-14 05:5x 的現場:⚓ a seat 發給 👔 a seat 的一則**完全消失**——收件人沒收到、
      佇列裡沒有殘影,而 `~/.ez2-aib/logs/` 底下沒有任何寄件端紀錄 ⇒ **「它有沒有被送出」
      這個問題沒有正本**,兩端都答不出來。後果是兩個人各做了同一包,一份整包丟棄。
    ★ 失效方向要選對:**留不下痕跡是可惜,送不出去是災情**。所以這裡吞掉所有寫入例外,
      不得讓紀錄的問題變成信的問題(守它的格:test_a_write_failure_never_swallows_the_message)。
    """
    ts = int(time.time() if now is None else now)
    row = {
        "schema": SEND_LOG_SCHEMA,
        "ts": ts,
        "when": taipei_stamp(ts),
        "seat": seat,
        "sender_label": sender_label or "未知",
        "outcome": outcome,          # queued / delivered_direct / refused
        "queue_id": queue_id,
        "msg_sha16": msg_sha16(msg),
        "msg_bytes": len((msg or "").encode("utf-8")),
    }
    try:
        path = send_log_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError):
        pass                          # 具名吞掉:見上面「失效方向」那一段
    return row


def dmctl_timeout_s(msg):
    """排空器給 dm-ctl 一次投遞的秒數:基本 60 秒,1KB 以上每 KB 加 8 秒,最多 240 秒(見 DMCTL_TIMEOUT_* 註)。"""
    kb = len(str(msg or "").encode("utf-8")) / 1024.0
    extra = max(0.0, kb - 1.0) * DMCTL_TIMEOUT_PER_KB_S
    return int(min(DMCTL_TIMEOUT_MAX_S, DMCTL_TIMEOUT_BASE_S + extra))


def seat_dir(seat, env=None):
    return queue_root(env) / seat


SEAT_DELIVER_LOCK = "deliver.lock"
# ★ review r16(佇列)發現一:7d1f619b8 起長稿要等落地、按 Enter 後等框清,等待隨稿長放大 ⇒ 投遞逾時也隨稿長放大
#   (以前一律 60 秒:16KB 以上、對方吃得慢的稿會被砍成 rc 4 轉人審,而底下的引擎可能照樣按了 Enter ⇒ 人一重排就兩份)。
DMCTL_TIMEOUT_BASE_S = 60
DMCTL_TIMEOUT_PER_KB_S = 8
DMCTL_TIMEOUT_MAX_S = 240
SEAT_DELIVER_WAIT_S = DMCTL_TIMEOUT_MAX_S + 15.0   # 叫醒等排空器那一封貼完;要比 dm-ctl 最長一次還長
# ★ review r2 m4:triage.json 的讀-改-寫(一拍的重算/記叫過,與鎖外投遞之後的寫回/回滾)共用這把每席小鎖。
#   審查建議拿 drain.lock;沒用它是因為 drain_once 拿著 drain.lock 逐席投遞(每席最多 60 秒),寫回要陪著等整輪。
#   triage.json 的寫者只有 _triage_seat(本來就在 drain.lock 裡)、_triage_deliver、triage_run --apply,三處都拿這把。
SEAT_TRIAGE_LOCK = "triage.lock"
SEAT_TRIAGE_WAIT_S = 30.0


def _seat_deliver_lock(seat, env=None, wait_s=0.0, name=SEAT_DELIVER_LOCK):
    """★ review r2 m2:每席一把投遞鎖(<佇列根>/<席>/deliver.lock)。叫醒已經搬到 drain.lock 外面投,
    沒有這把鎖時,叫醒那句與排空器同一拍貼給同一窗的排隊信會交錯貼進同一個輸入框。
    兩條投遞路(排空器逐席、叫醒)都先拿它。wait_s=0 ⇒ 拿不到立刻回 None;>0 ⇒ 最多等這麼久;
    None ⇒ 等到拿到為止(review r4 m6:只給「持有者都很短」而且等不到就會記錯帳的路用)。
    回已上鎖的 fd(呼叫端用 _seat_deliver_unlock 放),拿不到回 None。"""
    try:
        d = seat_dir(seat, env)
        d.mkdir(parents=True, exist_ok=True)
        lk = os.open(str(d / name), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    if wait_s is None:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
            return lk
        except OSError:
            os.close(lk)
            return None
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while True:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lk
        except OSError:
            if time.monotonic() >= deadline:
                os.close(lk)
                return None
            time.sleep(0.2)


def _seat_deliver_unlock(lk):
    if lk is None:
        return
    try:
        fcntl.flock(lk, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(lk)


def order_path(seat, env=None):
    return seat_dir(seat, env) / "order.json"


def view_path(seat, env=None):
    return seat_dir(seat, env) / "view.json"


def waiting_order_path(seat, env=None):
    """待審頁上「已不在排隊」的信(已送達/待人處理)的顯示順序。與 order.json 分開:
    order.json 是送信佇列,排空器照它領信貼稿,已送達的 id 放進去會被重貼。"""
    return seat_dir(seat, env) / "waiting-order.json"


def _load_waiting_order(seat, env=None):
    try:
        data = json.loads(waiting_order_path(seat, env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("schema") != WAITING_ORDER_SCHEMA:
        return []
    return [i for i in (data.get("ids") or []) if isinstance(i, str) and ITEM_RE.fullmatch(i)]


def _write_waiting_order(seat, ids, env=None):
    path = waiting_order_path(seat, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    AJ.write_json(str(path), {"schema": WAITING_ORDER_SCHEMA, "seat": seat, "ids": list(ids)})
    _bump_source_rev(seat, env)


def item_path(seat, item_id, env=None):
    return seat_dir(seat, env) / ("%s.json" % item_id)


def load_policy(env=None):
    path = policy_path(env)
    if path is None:
        # 「沒艙家」與「檔不在」分兩態(第 47b 項;理由在 feature_policy 檔頭 b 條)。
        return dict(DEFAULT_POLICY), FP.R_NO_HOME
    if not path.is_file():
        return dict(DEFAULT_POLICY), "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY), "unreadable"
    if not isinstance(data, dict) or data.get("schema") != POLICY_SCHEMA:
        return dict(DEFAULT_POLICY), "unreadable"
    out = dict(DEFAULT_POLICY)
    if isinstance(data.get("enabled"), bool):
        out["enabled"] = data["enabled"]
    else:
        return dict(DEFAULT_POLICY), "unreadable"
    return out, "ok"


def write_policy(enabled, env=None):
    path = policy_path(env)
    if path is None:
        raise OSError("dm_queue: 沒有艙家,寫不了開關")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    AJ.write_json(str(path), {
        "schema": POLICY_SCHEMA,
        "enabled": bool(enabled),
    })


def owner_display_name(env=None):
    path = owner_path(env)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != OWNER_SCHEMA:
        return None
    name = data.get("display_name")
    if not isinstance(name, str):
        return None
    name = " ".join(name.split())
    return name[:32] if name else None


def sender_fields(label, is_ai=False, env=None):
    text = (label or "").strip()
    if text.startswith("@") and SEAT_RE.fullmatch(text[1:]):
        return {"kind": "ai", "label": text}
    if is_ai:
        return {"kind": "ai", "label": text or "未知"}
    human = owner_display_name(env)
    if human:
        return {"kind": "human", "label": human}
    return {"kind": "unknown", "label": "未知"}


def admit(env=None):
    """(ok, reason)。缺授權或開關關 = 不啟用,既有立刻貼稿不變。

    判斷只有一份:`feature_policy.judge`(第 47b 項)。本檔只負責讀自己那份開關檔。
    """
    policy, how = load_policy(env)
    return FP.judge(how, lambda: LP.license_plan(env), policy.get("enabled"))


def _empty_order(seat):
    return {"schema": ORDER_SCHEMA, "seat": seat, "ids": []}


def _load_order(seat, env=None):
    path = order_path(seat, env)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_order(seat)
    if not isinstance(data, dict) or data.get("schema") != ORDER_SCHEMA:
        return _empty_order(seat)
    ids = [i for i in (data.get("ids") or []) if isinstance(i, str) and ITEM_RE.fullmatch(i)]
    return {"schema": ORDER_SCHEMA, "seat": seat, "ids": ids}


def _write_order(seat, ids, env=None):
    path = order_path(seat, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    AJ.write_json(str(path), {"schema": ORDER_SCHEMA, "seat": seat, "ids": list(ids)})
    _bump_source_rev(seat, env)


# v2 新增欄位與「量不到」的預設值;v1 檔讀進來照這張表補 None,不猜。
V2_FIELDS = {
    "thread": None, "kind": "other", "sender_seat": None, "human_id": None,
    "sender_source": None,
    "sent_at": None, "delivered_at": None, "read_at": None, "done_at": None, "done_evidence": None,
    "archived_at": None, "archived_why": None, "deleted_at": None, "deleted_by": None, "deleted_why": None,
    "supersedes": None, "superseded_by": None, "superseded_at": None, "expires": None,
    "expired": None, "expired_at": None, "expired_why": None,
    "attempts": 0, "hold_why": None, "hold_rc": None, "held_at": None,
}


def normalize_sender_kind(value):
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    if v == "ai":
        return "seat"
    return v if v in SENDER_KINDS else "unknown"


def normalize_sender_source(value):
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in SENDER_SOURCES else None


def normalize_kind(value):
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in KINDS else "other"


def normalize_thread(value):
    if isinstance(value, str) and THREAD_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _upgrade_item(data):
    """v1 → v2 只在記憶體:缺欄補 None、ai→seat、sent_at 沒有就用 ts。不改磁碟(migrate --apply 才改)。"""
    out = dict(data)
    legacy = out.get("schema") in LEGACY_ITEM_SCHEMAS
    for key, default in V2_FIELDS.items():
        if key not in out:
            out[key] = default
    out["sender_kind"] = normalize_sender_kind(out.get("sender_kind"))
    out["sender_source"] = normalize_sender_source(out.get("sender_source"))
    out["kind"] = normalize_kind(out.get("kind"))
    if out.get("sent_at") is None:
        try:
            out["sent_at"] = int(out.get("ts") or 0) or None
        except (TypeError, ValueError):
            out["sent_at"] = None
    if not isinstance(out.get("status"), str) or out["status"] not in STATUSES:
        out["status"] = "queued"
    out["_legacy"] = legacy
    return out


def _load_item_raw(seat, item_id, env=None):
    """讀一則;回 (item|None, reason)。reason ∈ ok / missing / json / schema / id。
    壞檔要能被 tabs_payload 點名(病 3:壞與空在版面上不得同形),所以理由要分得開。"""
    path = item_path(seat, item_id, env)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, "missing"
    except ValueError:
        return None, "json"
    if not isinstance(data, dict) or (
            data.get("schema") != ITEM_SCHEMA and data.get("schema") not in LEGACY_ITEM_SCHEMAS):
        return None, "schema"
    if data.get("id") != item_id:
        return None, "id"
    return _upgrade_item(data), "ok"


def _load_item(seat, item_id, env=None, include_deleted=False):
    """活的那一則(None=不存在**或已是墓碑**)。墓碑對所有既有呼叫端都等於「已經不在了」(34 尺 A5b、36b 的判準);
    要看墓碑走 include_deleted=True 或 _all_items / tabs_payload。"""
    item, _why = _load_item_raw(seat, item_id, env)
    if item is not None and item.get("status") == "deleted" and not include_deleted:
        return None
    return item


def _strip_private(item):
    return {k: v for k, v in item.items() if not k.startswith("_")}


def source_rev_path(seat, env=None):
    return seat_dir(seat, env) / "rev"


def source_rev(seat, env=None):
    """每席單調遞增的來源修訂號(§4b 觀測接點):任何 item / order 變動都 +1。畫面每幀記下它讀的是哪一號,
    鬼影事件就從「幀裡的 (id,rev) 在那一號來源找不到」算出來;沒有這個數,零鬼影只是一句話。"""
    try:
        return int(source_rev_path(seat, env).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump_source_rev(seat, env=None):
    d = seat_dir(seat, env)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / "rev.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        n = source_rev(seat, env) + 1
        path = source_rev_path(seat, env)
        tmp = path.with_name(".rev.tmp.%d.%s" % (os.getpid(), secrets.token_hex(3)))
        tmp.write_text("%d\n" % n, encoding="utf-8")
        os.replace(str(tmp), str(path))
        return n
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


AUDIT_FILE = "audit.jsonl"


def _audit(seat, env, **fields):
    """狀態轉移留痕(准與拒都寫):ts/id/from/to/actor/ok/reason/rev。一行一筆,壞不了主檔。"""
    try:
        d = seat_dir(seat, env)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ts": int(fields.pop("ts", None) or time.time())}
        # --by 是字面守衛不是身份守衛(任何殼都能打 --by human:<name>);多記呼叫端三樣,擋不住但事後查得到是哪個 pane。
        e = _env(env)
        rec["caller"] = {"tmux_pane": e.get("TMUX_PANE") or None,
                         "instance": e.get("EZ2_AIB_INSTANCE") or None,
                         "ppid": os.getppid()}
        rec.update(fields)
        with open(str(d / AUDIT_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def audit_rows(seat, env=None, tail=None):
    path = seat_dir(seat, env) / AUDIT_FILE
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                rows.append({"raw": line, "reason": "json"})
    except OSError:
        return []
    if tail:
        rows = rows[-int(tail):]
    return rows


def parse_actor(actor):
    """'system' / 'seat:<碼>' / 'sender:<碼>' / 'human:<id>' → (類別, id);認不得 → (None, None)。"""
    if not isinstance(actor, str) or not actor.strip():
        return None, None
    text = actor.strip()
    if text == "system":
        return "system", None
    if ":" not in text:
        return None, None
    cls, ident = text.split(":", 1)
    cls, ident = cls.strip(), ident.strip()
    if cls in ("seat", "sender", "human") and ident:
        return cls, ident
    return None, None


def _write_item_plain(path, item):
    """同目錄已被 order 鎖住時用:不可再走 atomic_json(目錄鎖不可重入)。"""
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
    tmp = path.with_name(".%s.tmp.%d.%s" % (path.name, os.getpid(), secrets.token_hex(4)))
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def _write_item(seat, item, env=None, locked=False):
    item_id = item["id"]
    path = item_path(seat, item_id, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    payload = _strip_private(item)
    if payload.get("schema") in LEGACY_ITEM_SCHEMAS:
        # 寫回去就升成 v2:欄位已由 _upgrade_item 補齊,不再是 v1 的形狀。
        payload["schema"] = ITEM_SCHEMA
    if locked:
        _write_item_plain(path, payload)
    else:
        AJ.write_json(str(path), payload)
    _bump_source_rev(seat, env)


def _new_id():
    return "q_" + secrets.token_hex(8)


def taipei_stamp(ts=None):
    """月日時分秒: MM-DD HH:MM:SS(台北)。**格式化的是 ts 那一刻,不是現在。**

    ★ 09-15 03:2x the maintainer 抓到:舊版呼叫 `date +格式` 沒帶 epoch ⇒ 不論傳入哪一刻都印「現在」,
      只有 date 失敗時的退路才用 ts。於是「本則發出於 HH:MM,現在 HH:MM,已過 N 分」兩個時刻恆同,
      而既有尺的期望值也是用本函式算的(同一個錯寫兩次,恆綠)。改用 zoneinfo,不再起子行程。
    """
    epoch = int(time.time() if ts is None else ts)
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        return _dt.datetime.fromtimestamp(epoch, ZoneInfo(TZ_NAME)).strftime("%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 — 時區資料缺:退回本機時區,仍是 ts 那一刻
        return time.strftime("%m-%d %H:%M:%S", time.localtime(epoch))


def normalize_priority(value, default="normal"):
    if isinstance(value, str) and value.strip().lower() in PRIORITY_RANK:
        return value.strip().lower()
    return default


def normalize_origin(value, env=None, default="live"):
    if isinstance(value, str) and value.strip().lower() in ORIGINS:
        return value.strip().lower()
    raw = (_env(env).get("AIB_DM_QUEUE_ORIGIN") or "").strip().lower()
    if raw in ORIGINS:
        return raw
    return default if default in ORIGINS else "live"


def looks_like_test(item, seat=None):
    """舊列沒 origin 時的分類。認得出才當測試,認不出當真信。"""
    rec = item if isinstance(item, dict) else {}
    code = seat or rec.get("seat") or ""
    if code in TEST_FIXTURE_SEATS:
        return True
    sender = str(rec.get("sender_label") or rec.get("sender") or "")
    if sender.startswith("unattested:"):
        return True
    return bool(TEST_MSG_RE.search(str(rec.get("msg") or "")))


def item_origin(item, seat=None):
    rec = item if isinstance(item, dict) else {}
    raw = rec.get("origin")
    if isinstance(raw, str) and raw.strip().lower() in ORIGINS:
        return raw.strip().lower()
    return "test" if looks_like_test(rec, seat) else "live"


def _new_item(seat, msg, sender_label, sender_kind="unknown", session_uuid="", wid="",
              provider="", priority="normal", env=None, now=None, origin=None,
              thread=None, kind=None, sender_seat=None, human_id=None, expires=None, status="queued",
              sender_source=None, hub=None, sender_hub=None, sender_origin=None, closes=None):
    if not SEAT_RE.fullmatch(seat or ""):
        raise ValueError("seat")
    body = msg if isinstance(msg, str) else ""
    reg_wid, reg_prov = (None, None)
    if not (wid or "") or not (provider or "").strip():
        reg_wid, reg_prov = wid_for_seat(seat, env)
    ts = int(time.time() if now is None else now)
    kind_v = normalize_kind(kind)
    sk = normalize_sender_kind(sender_kind)
    hid = (human_id or "").strip() or None
    if sk == "human" and not hid:
        # 接口 §1:human_id 量不到就不得標 human(失效方向落在「不是真人」那邊,the maintainer 09-12 14:3x 裁)。
        sk = "unknown"
    if sk != "human":
        hid = None
    seat_claim = (sender_seat or "").strip().lower() or None
    if sk == "seat_claimed" and not seat_claim:
        # 形狀照上面那條 human:**標記了弱身分卻沒有身分值**,那個標記就沒有意義。
        # 失效方向一樣落在低權格(unknown),不落在看起來正常的那一格。
        sk = "unknown"
    try:
        exp = int(expires) if expires not in (None, "") else None
    except (TypeError, ValueError):
        exp = None
    item = {
        "schema": ITEM_SCHEMA,
        "id": _new_id(),
        "ts": ts,
        "when": taipei_stamp(ts),
        "priority": normalize_priority(priority),
        "origin": normalize_origin(origin, env=env),
        "status": status if status in STATUSES else "queued",
        "claimed_at": None,
        "claim_id": None,
        "seat": seat,
        "session_uuid": session_uuid or "",
        # 第 149 項:寄件路徑沒帶位址就現查登記表補上(空 wid 的信會在排空器那邊
        # 被判 no-screen 而永遠送不出去;投遞端也會再查一次,這裡補是為了讓巡查
        # 與右欄看得到真的窗號,不是為了取代那一次現查)。
        "wid": (wid or "") or (reg_wid or ""),
        "provider": ((provider or "").strip().lower() or (reg_prov or "")),
        # ★ the maintainer 09-30 15:3x(收件匣 A 項 1):佇列根是整台機器共用、任何一艙的右欄都在排空所有席的信,
        #   而窗號只在它自己那一艙有意義。抄下寄件當下的艙(tmux socket 名),排空器在登記表查不到那一席時
        #   才退回「這一艙的這個窗號」—— 沒有它,別艙的排空器會拿自己艙的同號窗去判忙/判死窗。
        #   舊信沒有這欄 = 退回排空器自己的艙(09-30 前的行為,不更壞)。
        "hub": _hub_or_empty(hub if hub else _env(env).get("EZ2_AIHUB_SOCKET")),
        "sender_label": sender_label or "未知",
        "sender_kind": sk,
        "msg": body,
        # 第 36 項:內容修訂號。內容(msg/priority)每改一次 +1;確認回呼帶著它,執行前重讀比對,
        # 不符 ⇒ 不執行(§89.7.1 接口一規則 2)。舊檔沒這欄一律當 0。
        "rev": 1,
    }
    item.update(V2_FIELDS)
    item.update({
        "thread": normalize_thread(thread),
        "kind": kind_v,
        "sender_seat": seat_claim,
        "human_id": hid,
        "sender_source": normalize_sender_source(sender_source),
        "sent_at": ts,
        "expires": exp,
        # ★ the maintainer 09-30 15:3x(收件匣 A 項 2):排隊投遞時抬頭要寫**原寄件人**,不是排空器自己。
        #   sender_label 已經是寄件當下的抬頭 token;抬頭還要寄件人那一艙(艙位碼)與來源機器名
        #   (dm_header 用它把白話名歸成「系統」)。兩欄都只收安全字元,不合形留 None/空,不猜。
        "sender_hub": _hub_or_empty(sender_hub),
        "sender_origin": _origin_or_none(sender_origin),
    })
    if item["sender_seat"] is None:
        # 沒明給寄件席就從 label(@五碼)推;推不到留 None,取代比對就永遠不成立(不合併)。
        lab = (sender_label or "").strip()
        if lab.startswith("@") and SEAT_RE.fullmatch(lab[1:]):
            item["sender_seat"] = lab[1:]
    if isinstance(closes, str) and ITEM_RE.fullmatch(closes.strip()):
        item["closes"] = closes.strip()   # S7-3:送達後要結掉的原信(只在有帶時才有這欄,舊列形狀不變)
    return item


def enqueue(seat, msg, sender_label, sender_kind="unknown",
            session_uuid="", wid="", provider="", priority="normal",
            env=None, now=None, origin=None,
            thread=None, kind=None, sender_seat=None, human_id=None, expires=None,
            sender_source=None, hub=None, sender_hub=None, sender_origin=None, no_relay=False, closes=None):
    item = _new_item(seat, msg, sender_label, sender_kind=sender_kind, session_uuid=session_uuid,
                     wid=wid, provider=provider, priority=priority, env=env, now=now, origin=origin,
                     thread=thread, kind=kind, sender_seat=sender_seat, human_id=human_id, expires=expires,
                     sender_source=sender_source, hub=hub, sender_hub=sender_hub, sender_origin=sender_origin,
                     closes=closes)
    if no_relay:
        # review r13 M1:寄件端明說「整行貼、不落檔」(AIB_DM_RELAY_BYTES=0:給讀不到落檔的家的人格包/交接/開席單)
        #   ⇒ 排進佇列也記著;排空時 _dmctl_env 看到這個鍵才把門檻設 0,不然排空用預設 600 又換成指標
        item["no_relay"] = True
    item_id = item["id"]
    d = seat_dir(seat, env)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    _write_item(seat, item, env)
    _order_append(seat, item_id, env)
    log_send(seat, msg, sender_label, "queued", queue_id=item_id, env=env, now=now)
    return item


def _appended_order(rec, seat, item_id):
    """order.json 的 mutate:尾端加一個 id(壞檔/舊 schema 就從空的開始)。模組層函式,到達性掃描器看得到呼叫者。"""
    if rec.get("schema") != ORDER_SCHEMA:
        rec = _empty_order(seat)
    ids = [i for i in (rec.get("ids") or []) if isinstance(i, str)]
    ids.append(item_id)
    rec["schema"] = ORDER_SCHEMA
    rec["seat"] = seat
    rec["ids"] = ids
    return rec


def _order_append(seat, item_id, env=None):
    AJ.update_json(str(order_path(seat, env)), lambda rec: _appended_order(rec, seat, item_id),
                   default=_empty_order(seat))


def _waiting_empty(seat, env=None):
    """待審頁畫面上一則都沒有(tabs_payload waiting,含已送達/待人處理)。點選檔只在這時才刪。"""
    return not tabs_payload(seat, env=env)["tabs"]["waiting"]["rows"]


def _order_remove(seat, item_id, env=None):
    order = _load_order(seat, env)
    ids = [i for i in order["ids"] if i != item_id]
    if ids != order["ids"]:
        _write_order(seat, ids, env)
    # 送信佇列空了 ≠ 待審頁空了:已送達的信還在待審頁上、可以被點選。原本 `if not ids` 就刪
    # ⇒ 送信佇列一直是空的席(d2678),任何一次移出(含清測試信 _unlink_item,不留 audit)都把
    # 剛點的選取洗掉,按鈕閃一下就不見(09-15 01:0x 實測:設焦點後 1 秒內 view.json 消失)。
    if not ids and _waiting_empty(seat, env):
        _delete_view(seat, env)


def _transition(seat, item_id, to, actor, env=None, now=None, why=None, evidence=None,
                if_rev=None, no_rev_check=None, expect_status=None):
    """狀態機的唯一入口(接口 §2)。回 {ok, reason, rev, item, message}。
    不在 TRANSITIONS 上的 (from,to,actor類別) 一律拒;seat 類還要是收件席本人;done 要存在的憑據路徑;
    准與拒都寫一行 audit.jsonl。離開 ACTIVE_STATUSES 就從 order.json 拿掉(檔留著當歷史)。
    ★ 還原刀錨:test-dm-queue-v2-70.py 把 TRANSITIONS 刪一列,對應合法轉移那格必紅。"""
    now = int(time.time() if now is None else now)
    cls, ident = parse_actor(actor)
    if no_rev_check is None:
        no_rev_check = True
    ok, reason, cur = rev_check(seat, item_id, if_rev, env, no_rev_check=no_rev_check)
    if not ok:
        _audit(seat, env, ts=now, id=item_id, to=to, actor=actor, ok=False, reason=reason)
        return {"ok": False, "reason": reason, "rev": cur, "item": None, "message": REV_REASON[reason]}
    item = _load_item(seat, item_id, env)
    if not item:
        _audit(seat, env, ts=now, id=item_id, to=to, actor=actor, ok=False, reason="not_found")
        return {"ok": False, "reason": "not_found", "rev": None, "item": None,
                "message": TRANSITION_REASON["not_found"]}
    frm = item.get("status")

    def refuse(rs):
        _audit(seat, env, ts=now, id=item_id, **{"from": frm}, to=to, actor=actor, ok=False,
               reason=rs, rev=item_rev(item))
        return {"ok": False, "reason": rs, "rev": item_rev(item), "item": None,
                "message": TRANSITION_REASON.get(rs, rs), "from": frm, "to": to, "actor": actor}

    if cls is None:
        out = refuse("actor_missing")
        if isinstance(actor, str) and actor.strip():
            # 10-05 a seat:有帶 --by 但格式不對時,原訊息說「要帶 --by」像是沒帶 ⇒ 指錯方向。reason 不變(消費端認它)。
            out["message"] = ACTOR_FORMAT_HINT % {"given": actor.strip(), "seat": seat}
        return out
    if expect_status is not None and frm != expect_status:
        return refuse("transition")   # 審查:rev 只在改內容時加,狀態被別人改過(剛讀 / 剛結)rev 看不出來 ⇒ 呼叫端帶它期望的狀態
    allowed = TRANSITIONS.get((frm, to))
    if not allowed:
        return refuse("transition")
    if cls not in allowed:
        return refuse("actor")
    if cls == "seat" and ident != seat:
        return refuse("actor")
    if to == "deleted" and cls == "system" and why != AUTO_DELETE_WHY:
        return refuse("actor")        # 系統只准刪「備存滿 30 天」那一種(the maintainer 09-30 17:1x)
    if to == "read" and cls == "system" and why != AUTO_READ_WHY:
        return refuse("actor")        # 系統只准代標「通知」組已讀(the maintainer 09-30 18:2x)
    done_kind = None
    if to == "done":
        # 10-13 S7-1(the maintainer 准;kimi / grok / gpt 三家都說「只收存在的檔」是進行中只進不出的主因):
        #   證據收 檔案 / 本席寄出的回信 id / 查得到的 commit;都不是但有 why ⇒ 憑理由結案(另記 done_kind=reason)。
        done_kind = evidence_kind(seat, evidence, env)
        if done_kind is None:
            if isinstance(why, str) and why.strip():
                done_kind = "reason"
            else:
                return refuse("evidence")
    if to == "archived" and why not in ARCHIVE_WHYS:
        return refuse("transition")
    if to == "archived" and frm in ACTIVE_STATUSES and why != "superseded":
        return refuse("transition")   # 從沒送達的信只有被取代才會進備存,不會因為天數

    item["status"] = to
    if to == "delivered":
        item["delivered_at"] = now
        item["claimed_at"] = None
        item["claim_id"] = None
    elif to == "read":
        item["read_at"] = now
        if cls == "system":
            item["read_by"] = "system"
            item["read_why"] = why
    elif to == "done":
        item["done_at"] = now
        item["done_evidence"] = str(evidence) if done_kind != "reason" else None
        item["done_kind"] = done_kind
        if done_kind == "reason":
            item["done_why"] = why.strip()[:500]
        if item.get("read_at") is None:
            item["read_at"] = now
    elif to == "archived":
        item["archived_at"] = now
        item["archived_why"] = why
        item["claimed_at"] = None
        item["claim_id"] = None
    elif to == "deleted":
        item["deleted_at"] = now
        item["deleted_by"] = ident
        item["deleted_why"] = (why or "").strip() or None
        item["claimed_at"] = None
        item["claim_id"] = None
    elif to == HOLD_STATUS:
        item["claimed_at"] = None
        item["claim_id"] = None
        item["hold_why"] = str(why) if why is not None else item.get("hold_why")
        item["held_at"] = now
    elif to == "queued" and frm == HOLD_STATUS:
        item["attempts"] = 0
        item["hold_why"] = None
        item["hold_rc"] = None
        item["held_at"] = None
        item["expired"] = None      # 第 13 項(審 2/3):requeue 可逆,清過期標記
        item["expired_at"] = None
        item["expired_why"] = None
    _write_item(seat, item, env)
    if to not in ACTIVE_STATUSES:
        _order_remove(seat, item_id, env)
    _audit(seat, env, ts=now, id=item_id, **{"from": frm}, to=to, actor=actor, ok=True,
           reason="ok", rev=item_rev(item), why=why)
    return {"ok": True, "reason": "ok", "rev": item_rev(item), "item": item, "message": ""}


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
MEASURED_SOURCES = ("pane_pid_chain", "bg_fork_chain")


def evidence_kind(seat, evidence, env=None):
    """完成證據的種類:file(存在的檔)/ dm(這一席寄出、某一席佇列裡查得到的信)/ commit(本倉查得到)/ None。"""
    ev = evidence.strip() if isinstance(evidence, str) else ""
    if not ev:
        return None
    if ITEM_RE.fullmatch(ev):
        for other in iter_queue_seats(env):
            it = _load_item(other, ev, env)
            if it and it.get("sender_seat") == seat and it.get("sender_source") in MEASURED_SOURCES:
                return "dm"           # 審查:只認量到的寄件身分(label 推出來的 @席碼不算)
        return None
    if _SHA_RE.fullmatch(ev):
        try:
            r = subprocess.run(["git", "-C", str(_HERE.parents[1]), "cat-file", "-e", ev + "^{commit}"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return "commit"
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        if Path(ev).expanduser().is_file():
            return "file"
    except (OSError, ValueError):
        pass
    return None


def _after_read(seat, item_id, env=None, now=None):
    """S7-2:寄件人標成知會(kind=notice)的信,讀過就備存(kimi:不必等 12 小時)。回備存結果或 None。"""
    it = _load_item(seat, item_id, env)
    if not it or it.get("kind") != "notice" or it.get("status") != "read":
        return None
    return _transition(seat, item_id, "archived", "system", env=env, now=now, why="notice_read")


def mark_read(seat, item_id, actor, env=None, now=None, if_rev=None, no_rev_check=None):
    out = _transition(seat, item_id, "read", actor, env=env, now=now, if_rev=if_rev,
                      no_rev_check=no_rev_check)
    if out.get("ok"):
        _after_read(seat, item_id, env=env, now=now)
    return out


def mark_done(seat, item_id, actor, evidence, env=None, now=None, if_rev=None, no_rev_check=None, why=None):
    return _transition(seat, item_id, "done", actor, env=env, now=now, evidence=evidence, why=why,
                       if_rev=if_rev, no_rev_check=no_rev_check)


def read_done(seat, item_id, actor, evidence, env=None, now=None, if_rev=None, no_rev_check=None, why=None):
    """S7-1 `read --done`:讀和結案一步做完(排隊 / 已送達 ⇒ read ⇒ done;已讀的直接 done)。"""
    it = _load_item(seat, item_id, env)
    if it and it.get("status") in ("queued", "delivered", HOLD_STATUS):
        r = _transition(seat, item_id, "read", actor, env=env, now=now, if_rev=if_rev, no_rev_check=no_rev_check)
        if not r.get("ok"):
            return r
        if_rev, no_rev_check = None, True
    return mark_done(seat, item_id, actor, evidence, env=env, now=now, if_rev=if_rev,
                     no_rev_check=no_rev_check, why=why)


def group_of(seat, item_id, env=None, now=None):
    """S7-1 `done --group`:這封所在的合併組(與 stage-tidy 同一個 merge_groups;只看待審與進行中)。回 id 清單(舊→新)。"""
    t = tabs_payload(seat, env=env, now=now)
    # 審查:跟 stage-tidy 交給 AI 的那一組同一個母體(待審頁、未到期);不含進行中(那些 AI 這次沒看到),
    #   排隊中的算進組但不會被結(還沒送到眼前)
    rows = [r for r in t["tabs"]["waiting"]["rows"]
            if r.get("status") in ("queued", "claimed", "delivered") and not r.get("expired_now")]
    for g in TR.merge_groups(rows, relay_text=_relay_text):
        ids = [str(r["id"]) for r in g]
        if item_id in ids:
            return ids
    return [item_id]


def done_group(seat, item_id, actor, evidence, env=None, now=None, if_rev=None, no_rev_check=None, why=None):
    """代表那封照 if_rev 驗;組內其他封同一份證據 / 理由一起結(每封各寫稽核)。回 {ok, done:[...], failed:[...]}。"""
    members = group_of(seat, item_id, env=env, now=now)    # 先算組:代表結掉之後它就不在待審 / 進行中,再算會找不到組
    first = read_done(seat, item_id, actor, evidence, env=env, now=now, if_rev=if_rev,
                      no_rev_check=no_rev_check, why=why)
    if not first.get("ok"):
        return dict(first, done=[], failed=[item_id])
    done, failed = [item_id], []
    for other in members:
        if other == item_id:
            continue
        cur = (_load_item(seat, other, env) or {}).get("status")
        if cur != "delivered":
            failed.append(other)            # 排隊中(還沒送到)/ 已經被改過的不代結
            continue
        r = read_done(seat, other, actor, evidence, env=env, now=now, no_rev_check=True, why=why)
        (done if r.get("ok") else failed).append(other)
    return {"ok": True, "reason": "ok", "done": done, "failed": failed, "rev": first.get("rev")}


def reclass(seat, item_id, kind, actor, env=None, now=None, if_rev=None):
    """S7-2:收件席把寄件人的分類改掉(知會 → 工作 / 要裁…);只有收件席本人、只改已送達 / 已讀的;
    改了 rev+1(審查:不加的話 triage / stage-tidy 先前拿的 if_rev 擋不住,升成要裁的信還會被代標讀)。"""
    now = int(time.time() if now is None else now)
    cls, ident = parse_actor(actor)
    if cls != "seat" or ident != seat or kind not in KINDS:
        return {"ok": False, "reason": "actor" if kind in KINDS else "kind"}
    ok, reason, _cur = rev_check(seat, item_id, if_rev, env, no_rev_check=if_rev is None)
    if not ok:
        return {"ok": False, "reason": reason}
    it = _load_item(seat, item_id, env)
    if not it:
        return {"ok": False, "reason": "not_found"}
    if it.get("status") not in ("delivered", "read"):
        return {"ok": False, "reason": "transition"}
    old = it.get("kind")
    it["kind"] = kind
    it["rev"] = item_rev(it) + 1
    _write_item(seat, it, env)
    _audit(seat, env, ts=now, id=item_id, to="reclass", actor=actor, ok=True, reason="ok", old=old, kind=kind)
    return {"ok": True, "reason": "ok", "old": old, "kind": kind}


def _close_original(item, env=None, now=None):
    """S7-3 `aib dm --close <原信 id>`:回信**送達**(delivered)之後才把寄件席自己佇列裡的原信結掉,證據 = 回信 id。
    只信量到的寄件身分(pane_pid_chain / bg_fork_chain);原信要是已送達或已讀。回 _transition 結果或 None。"""
    orig = item.get("closes")
    who = item.get("sender_seat")
    if not orig or not who or item.get("status") != "delivered":
        return None
    if item.get("sender_source") not in MEASURED_SOURCES:
        return None
    try:
        it = _load_item(who, orig, env)
        if not it or it.get("status") not in ("delivered", "read"):
            return None
        if it.get("sender_seat") and it.get("sender_seat") != item.get("seat"):
            return None       # 審查:回信要回給原信的寄件人,才算結了那一封
        return mark_done(who, orig, "seat:%s" % who, item["id"], env=env, now=now, no_rev_check=True)
    except Exception:  # noqa: BLE001 —— 審查:結原信失敗不得讓投遞 / record 變失敗(信已送到)
        return None


def archive_item(seat, item_id, actor="system", why="days", env=None, now=None):
    return _transition(seat, item_id, "archived", actor, env=env, now=now, why=why)


def archive_sweep(env=None, now=None, days=None, dry_run=False, seat=None):
    """系統照天數推備存:delivered/read/done 且最後動作(done_at > read_at > delivered_at)早於 now−days。
    queued / hold 不會因天數備存(那是還沒送到的信,備存等於弄丟)。"""
    now = int(time.time() if now is None else now)
    days = ARCHIVE_DAYS_DEFAULT if days is None else int(days)
    cutoff = now - days * 86400
    out = []
    seats = [seat] if seat else all_seats(env)
    for s in seats:
        for item in history_items(s, env=env):
            if item.get("status") not in ("delivered", "read", "done"):
                continue
            last = item.get("done_at") or item.get("read_at") or item.get("delivered_at")
            try:
                last = int(last)
            except (TypeError, ValueError):
                continue     # 時戳量不到就不動它(不猜)
            if last >= cutoff:
                continue
            row = {"seat": s, "id": item["id"], "status": item.get("status"), "last": last}
            if not dry_run:
                res = archive_item(s, item["id"], "system", "days", env=env, now=now)
                row["ok"] = bool(res.get("ok"))
            out.append(row)
    return {"days": days, "cutoff": cutoff, "dry_run": bool(dry_run), "n": len(out), "rows": out}


def retention_sweep(env=None, now=None, dry_run=False, seat=None):
    """the maintainer 09-30 17:1x:完成滿 12 小時 ⇒ 備存;備存滿 30 天 ⇒ 刪除(墓碑)。時戳量不到就不動(不猜)。"""
    now = int(time.time() if now is None else now)
    done_cut = now - AUTO_ARCHIVE_DONE_HOURS * 3600
    arch_cut = now - AUTO_DELETE_ARCHIVED_DAYS * 86400
    archived, deleted = [], []
    seats = [seat] if seat else all_seats(env)
    for s in seats:
        for item in history_items(s, env=env):
            st = item.get("status")
            if st == "done":
                try:
                    at = int(item.get("done_at"))
                except (TypeError, ValueError):
                    continue
                if at >= done_cut:
                    continue
                row = {"seat": s, "id": item["id"], "done_at": at}
                if not dry_run:
                    row["ok"] = bool(archive_item(s, item["id"], "system", AUTO_ARCHIVE_WHY, env=env, now=now).get("ok"))
                archived.append(row)
            elif st == "archived":
                try:
                    at = int(item.get("archived_at"))
                except (TypeError, ValueError):
                    continue
                if at >= arch_cut:
                    continue
                row = {"seat": s, "id": item["id"], "archived_at": at}
                if not dry_run:
                    row["ok"] = bool(_transition(s, item["id"], "deleted", "system", env=env, now=now,
                                                 why=AUTO_DELETE_WHY).get("ok"))
                deleted.append(row)
    return {"dry_run": bool(dry_run), "done_hours": AUTO_ARCHIVE_DONE_HOURS,
            "archived_days": AUTO_DELETE_ARCHIVED_DAYS, "archived": archived, "deleted": deleted}


def retention_tick(env=None, now=None):
    """節流版(排空器每幀呼叫):距上次跑不到 RETENTION_EVERY_S 就不跑。回 None=這次沒跑。"""
    now = int(time.time() if now is None else now)
    root = queue_root(env)
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = root / "retention.stamp"
        last = int((stamp.read_text(encoding="utf-8").strip() or "0")) if stamp.is_file() else 0
    except (OSError, ValueError):
        last = 0
    if now - last < RETENTION_EVERY_S:
        return None
    try:
        (root / "retention.stamp").write_text("%d\n" % now, encoding="utf-8")
    except OSError:
        pass
    return retention_sweep(env=env, now=now)


# ── the maintainer 09-30 17:2x:待審區超過三封 ⇒ 程式先歸納(重複/通知/更NN/待處理),再叫醒該席一次 ──
# 「待審區只要有超過三封,該 AI 就應該把它們全部讀取,並將重複的、純通知的歸納;同一作者有多封時,
#   在對應的分類裡寫上 更01、更02」。分組規則在 dm_triage(決定性、不叫 LLM);這裡管三件事:
#   (1) 分組結果寫在 <佇列根>/<席>/triage.json(畫面與 hook 讀它);
#   (2) 排空器每幀 drain-idle 先呼叫 triage_tick(節流),未讀 > 3 且有新的一批 ⇒ 席閒著才貼一句叫醒;
#   (3) 叫醒那句**不進佇列**(進了會讓封數 +1、下一批又叫一次,自己叫醒自己),直接走排空器同一支投遞器。
# ★ 絕不刪信、絕不標完成、絕不改狀態 —— 那是收件席讀過之後自己用 done --evidence 做的事。
TRIAGE_EVERY_S = 60               # 排空器每幀都會叫到;每 60 秒最多掃一次所有席


def triage_path(seat, env=None):
    return seat_dir(seat, env) / TR.TRIAGE_FILE


class TriageLockBusy(RuntimeError):
    """triage --apply 等不到每席 triage 小鎖(review r4 m7):不寫檔,呼叫端回錯誤。"""


def triage_run(seat, env=None, now=None, apply=False):
    """現算一次分組;apply=True 才寫檔(保留上一份的叫醒紀錄)。回 (rec, 待審列)。
    apply=True 而等不到 triage 小鎖 ⇒ 丟 TriageLockBusy、不寫(review r4 m7:沒鎖就寫 = 與叫醒的寫回互相蓋掉)。"""
    now = int(time.time() if now is None else now)
    rows = tabs_payload(seat, env=env, now=now)["tabs"]["waiting"]["rows"]
    path = triage_path(seat, env)
    if not apply:
        return TR.build_record(seat, rows, prev=TR.load(path), now=now), rows
    tl = _seat_deliver_lock(seat, env, wait_s=SEAT_TRIAGE_WAIT_S, name=SEAT_TRIAGE_LOCK)   # review r2 m4
    if tl is None:
        raise TriageLockBusy("triage.lock busy for %s" % seat)   # r4-m7-no-lock-no-write
    try:
        rec = TR.build_record(seat, rows, prev=TR.load(path), now=now)
        TR.write(path, rec)
    finally:
        _seat_deliver_unlock(tl)
    return rec, rows


def triage_counts(seat, env=None, now=None):
    """給 inbox_brief_hook:分組檔裡「現在還在待審」的封數;沒有分組檔回 None。"""
    rec = TR.load(triage_path(seat, env))
    if rec is None:
        return None
    rows = tabs_payload(seat, env=env, now=now)["tabs"]["waiting"]["rows"]
    return TR.counts_for(rec, [r["id"] for r in TR.eligible_rows(rows)])


def _triage_seat(seat, env, now, hold_fn, deliver_fn=None):
    """一席的一拍(在 drain.lock 裡跑):該重算就重算、決定叫不叫。回一列結果(沒事回 None)。
    要叫 ⇒ 先把 woke_* 寫進 triage.json(同一批不叫第二次、冷卻,靠的就是這幾欄),再回帶 "_plan" 的一列;
    **投遞不在這裡做**(review r1 m2):dm-ctl 一次最多 60 秒,在鎖裡投會讓排空器整段卡住。
    由 triage_tick 放掉鎖之後呼叫 _triage_deliver。deliver_fn 參數保留相容,不用。"""
    path = triage_path(seat, env)
    prev = TR.load(path)
    # 10-13 與 a seat 定案(17:12):那一席開了 auto_tidy ⇒ 這一拍先做 stage-tidy 機器層,
    #   叫醒條件改成「有新信 + 閒下來」(不要求超過三封),叫醒那句換成完整處理指示(TR.tidy_wake_text)。
    tidy_on = tidy_enabled(seat, env)[0]
    if tidy_on:
        # 審查:那一席所在的艙沒開信件排隊 ⇒ 不替它清(以前只在叫醒成功後才代標,不該因為新功能就動別艙的信)
        hubs = [r[2] for r in _seat_records_all_cabins(seat, env) if r[2]]
        if hubs and all(_cabin_queue_enabled(h, env) is False for h in hubs):
            tidy_on = False
    tidy_res = stage_tidy(seat, env=env, now=now) if tidy_on else None
    cleaned = tidy_res["cleaned"] if tidy_res else 0
    rows = tabs_payload(seat, env=env, now=now)["tabs"]["waiting"]["rows"]
    unread = TR.unread_ids(rows)
    if prev is None and len(unread) <= (0 if tidy_on else TR.TRIAGE_MIN_UNREAD):
        return None                        # 從沒超過三封(auto_tidy:一封都沒有):不建檔、不叫
    cur = TR.batch_key(r["id"] for r in TR.eligible_rows(rows))
    # ★ review r12 M1:分類器換過(指紋不同)也重算 —— 不然舊版分類器認定的「通知」會被照舊代標已讀
    if prev is None or prev.get("batch") != cur or prev.get("classifier") != TR.CLASSIFIER_FP:
        rec = TR.build_record(seat, rows, prev=prev, now=now)
        TR.write(path, rec)
    else:
        # ★ review r2 m3:批次鍵(待審集合)沒變 ≠ 未讀集合沒變 —— 一封 delivered → hold 仍在待審、卻不再是未讀。
        #   分組/標籤沿用上一份,未讀 id 每拍從現在的列重算(否則 ≤3 封未讀也會被叫)。
        rec = dict(prev)
        if [str(i) for i in (prev.get("unread_ids") or [])] != unread:
            rec["unread_ids"] = unread
            TR.write(path, rec)
    if not (TR.should_wake_tidy(rec, now=now, cooldown_s=TIDY_WAKE_COOLDOWN_S) if tidy_on else TR.should_wake(rec, now=now)):
        return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": "no-new-batch-or-cooldown",
                "cleaned": cleaned}
    gate_ok, gate_why = _wake_gate(seat, rec, env, now, idle_cap=not tidy_on)
    if not gate_ok:
        return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": gate_why, "cleaned": cleaned}
    if tidy_on:
        quiet, rec = _screen_quiet(seat, rec, env, now)
        TR.write(path, rec)
        if quiet is False:
            return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": "screen-moving", "cleaned": cleaned}
    # 判忙與排空器同一個判準(drain_hold:登記表現查那一席在哪一艙哪個窗);忙就下一拍再試,不記 woke
    #   探針用**信檔本身**(帶 provider / hub;待審列沒有這兩欄),登記表查得到那一席時 drain_hold 會以登記表為準
    probe = next((r for r in rows if r.get("wid")), None) or (rows[0] if rows else {})
    probe = _load_item(seat, probe["id"], env) or probe if probe.get("id") else probe
    res = hold_fn(seat, probe) if hold_fn is not None else drain_hold(seat, probe, env=env)
    busy, why = (bool(res[0]), str(res[1] or "")) if isinstance(res, tuple) else (bool(res), "")
    if busy:
        return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": why or "busy"}
    # 通知組裡**已送達**(delivered)的 ⇒ 叫醒成功後由系統代標已讀,提醒裡照樣列出來。
    #   ★ review r1 M4:排隊中(queued)的從沒貼到那一席眼前,代標已讀 = 那一席永遠不知道有這封;人審不代標。
    by_id = {str(r.get("id")): r for r in rows}
    #   ★ review r12 M1:代標前用**現在的**分類器對信的內文再判一次(分組檔可能是別的版本寫的)
    notes = [i for i in ((rec.get("groups") or {}).get("notice") or [])
             if (by_id.get(i) or {}).get("status") == "delivered" and TR.is_notice((by_id.get(i) or {}).get("msg"))]
    # review r2 m5:引擎以登記表(那一席現在的席位檔)為準;信上蓋的 provider 是寄信當下抄的,查不到登記才退回它
    reg_prov = next((r[1] for r in _seat_records_all_cabins(seat, env) if r[1]), None)
    mode = _triage_mode(reg_prov or probe.get("provider"))
    if tidy_on:
        notes = []                         # 通知已在機器層代標過,不再列
        st = tidy_res                      # 審查:不再跑第二次(相似度合併很花時間,而且在排空鎖裡)
        text = TR.tidy_wake_text(seat, st["need_ai"], st["todo"], str(_HERE / "dm_queue.py"), mode=mode)
    else:
        text = TR.wake_text(seat, rec.get("counts") or {}, len(rec.get("ids") or []), str(_HERE / "dm_queue.py"),
                            mode=mode, digest=TR.human_lines(rec, rows) if mode == "digest" else None,
                            notes=[(by_id.get(i) or {}).get("msg") or "" for i in notes])
    text = _one_line_for_line_reader(text, reg_prov or probe.get("provider"))
    # id 故意不合 ITEM_RE:投遞器看到不是佇列 id 就不帶 AIB_DM_QUEUED_ITEM_ID,dm-ctl 也就不回佇列找它
    # no_relay:叫醒那句整段貼進去,不讓 dm-ctl 換成「去讀某檔」的指標(見 _dmctl_env)
    wake = {"id": "triage_%s" % rec.get("batch"), "seat": seat, "msg": text, "sent_at": now, "ts": now,
            "no_relay": True}
    # 先記「叫過了」再投(review r1 m2):放掉鎖之後別的一拍看到的已經是叫過的狀態
    prev_woke = {k: rec[k] for k in _WOKE_ROLLBACK_KEYS if k in rec}
    day_log = [int(t) for t in (rec.get("wake_log") or []) if isinstance(t, (int, float)) and now - int(t) < 86400]
    rec = dict(rec)
    rec.update({"woke_batch": rec.get("batch"), "woke_ids": list(rec.get("unread_ids") or []),
                "woke_at": now, "woke_rc": None, "woke_mode": mode, "auto_read": [],
                "wake_log": day_log + [now]})
    if not TR.write(path, rec):
        # review r1 m3:記不下來就不叫 —— 否則下一拍看不到叫過,每分鐘都叫一次
        return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": "triage-write-failed"}
    # ★ review r7 m5:記下每封通知在分組當下的修訂號;代標時帶 if_rev ⇒ 投遞那段時間內容被改過(已不一定是通知)
    #   就拒、不蓋掉。狀態被那一席自己改掉(read / done)由 TRANSITIONS 擋(done/read → read 沒開)。
    note_revs = {i: (by_id.get(i) or {}).get("rev") for i in notes}
    return {"seat": seat, "batch": rec.get("batch"), "woke": False, "why": "planned", "mode": mode,
            "_plan": {"wake": wake, "notes": notes, "note_revs": note_revs, "path": str(path),
                      "prev_woke": prev_woke, "at": now, "probe": probe}}


# 叫醒紀錄欄;rc=2 回滾時照上一份原樣放回(上一份沒有的欄就拿掉)
_WOKE_KEYS = ("woke_batch", "woke_ids", "woke_at", "woke_rc", "woke_mode", "auto_read", "wake_log")
# ★ review r2 m1:wake_log(每日上限的帳)**永不回滾** —— 每一次投遞嘗試都花過一次 dm-ctl,照算
_WOKE_ROLLBACK_KEYS = tuple(k for k in _WOKE_KEYS if k != "wake_log")
# ★ review r2 m1:rc=2 最多回滾這麼多次,之後記成叫過。dm-ctl 的 2 以前也用在
#   「回讀不符、勿自動重送」,沒有上限就會每分鐘重貼一次、一直貼下去。
#   (10-15 B1:那一格已改回 4 = 勿重送;上限照留,防的是下一個把 2 用錯的出口。)
# ★ review r3 m3:次數**每席**累計(woke_attempts 跟著 build_record 跨批次抄),只在叫醒成功(rc=0)後歸零 ——
#   以前換一批(來一封新信)就從 1 重算,一直來信就一直重貼。
WAKE_RC2_MAX = 3


def _triage_busy_now(seat, plan, env, hold_fn):
    """★ review r3 M1:拿到每席投遞鎖之後、投之前再判一次忙(與 _triage_seat 同一個判準:注入的 hold_fn,
    否則 drain_hold)。一拍判閒 → 等投遞鎖的那段時間排空器剛好貼了一封給同一席 ⇒ 那一席現在正在動,
    叫醒那句不能再貼進去。回 (busy, why);判準自己壞掉 ⇒ 當忙(寧可下一拍再叫,不往不確定的框裡貼)。"""
    probe = plan.get("probe") or {}
    try:
        res = hold_fn(seat, probe) if hold_fn is not None else drain_hold(seat, probe, env=env)
    except Exception as exc:  # noqa: BLE001
        return True, "hold-error:%s" % exc.__class__.__name__
    busy, why = (bool(res[0]), str(res[1] or "")) if isinstance(res, tuple) else (bool(res), "")
    return busy, why


def _triage_unplan(seat, plan, path, batch, now, why):
    """一個字都沒送、而且**沒有花掉一次投遞嘗試**(review r3 M1:投遞當下判忙;m3:等不到投遞鎖)⇒ 把「叫過了」原樣退回:
    woke_* 回上一份、這一拍記進 wake_log 的那一筆拿掉(不算進每日上限),woke_attempts 不動(不算進 rc=2 上限)。
    呼叫端拿著 triage 小鎖。"""
    cur = TR.load(path) or {}
    if cur.get("woke_at") == now and cur.get("woke_batch") == batch:
        back = {k: v for k, v in cur.items() if k not in _WOKE_ROLLBACK_KEYS}
        back.update(plan.get("prev_woke") or {})
        log = list(back.get("wake_log") or [])
        if now in log:
            log.remove(now)
        back["wake_log"] = log
        TR.write(path, back)
    return {"seat": seat, "batch": batch, "woke": False, "why": why}


def _still_notice(seat, item_id, env):
    """review r12 M1:代標前最後一道 —— 重讀信檔,仍是「已送達」而且現在的分類器仍判純通知才代標。"""
    item = _load_item(seat, item_id, env) or {}
    return item.get("status") == "delivered" and TR.is_notice(item.get("msg"))


def _triage_deliver(seat, plan, env, deliver_fn=None, hold_fn=None):
    """鎖外投遞一次叫醒,並把結果寫回 triage.json。回那一席的結果列。"""
    wake, notes, now = plan["wake"], plan["notes"], plan["at"]
    path = Path(plan["path"])
    batch = wake["id"][len("triage_"):]
    # review r2 m2:與排空器逐席投遞共用一把每席投遞鎖;等不到(排空器那一封卡住)⇒ 一個字都沒送
    #   ★ review r3 m3:等不到鎖不是一次投遞嘗試 ⇒ 與「投遞當下判忙」同一條退回路:不算 rc=2 上限、不花每日額度
    lk = _seat_deliver_lock(seat, env, wait_s=SEAT_DELIVER_WAIT_S)
    busy_why = None
    if lk is None:
        busy_why = "deliver-lock-timeout"
        rc = None
    else:
        try:
            busy, why = _triage_busy_now(seat, plan, env, hold_fn)   # review r3 M1:拿到鎖之後再判一次忙
            if busy:
                busy_why = why or "busy"
                rc = None
            else:
                rc = int((deliver_fn or _deliver_via_dmctl)(wake) or 0)
        finally:
            _seat_deliver_unlock(lk)
    if busy_why is not None:
        # ★ review r4 m6:一個字都沒送,這裡的回滾**必須**做到 —— 等不到就回的話,帳上留著「叫過了」而其實沒貼,
        #   那一批就再也不會叫。triage 小鎖的持有者(一拍的重算、另一次回滾/寫回)都只做讀-改-寫,很短 ⇒ 等到拿到為止。
        tl = _seat_deliver_lock(seat, env, wait_s=None, name=SEAT_TRIAGE_LOCK)   # r4-m6-rollback-waits
        if tl is None:       # 只剩「鎖檔開不了」(佇列目錄壞掉)⇒ triage.json 本來也寫不進去
            return {"seat": seat, "batch": batch, "woke": False, "why": "triage-lock-unavailable", "rc": None}
        try:
            return _triage_unplan(seat, plan, path, batch, now, busy_why if lk is None
                                  else "busy-at-deliver:%s" % busy_why)
        finally:
            _seat_deliver_unlock(tl)
    # 代標只在叫醒**確定貼進去**(rc=0)時做;rc=3/4 可能沒貼進去,通知留給那一席自己讀
    #   ★ review r7 m5:帶分組當下看到的修訂號(if_rev);對不上 ⇒ 這一封不代標(寧可留給那一席自己讀)
    note_revs = plan.get("note_revs") or {}
    auto_read = [i for i in notes
                 if rc == 0 and _still_notice(seat, i, env)
                 and _transition(seat, i, "read", "system", env=env, now=now, why=AUTO_READ_WHY,
                                 if_rev=note_revs.get(i), no_rev_check=False).get("ok")]
    for i in auto_read:
        _after_read(seat, i, env=env, now=now)    # 審查:kind=notice 讀過即備存,這條代標路也要一致
    # review r2 m4:寫回/回滾在 triage 小鎖裡做(讀-改-寫);等不到 ⇒ 不寫回,保持「已叫過」(寧可少叫不重貼)
    tl = _seat_deliver_lock(seat, env, wait_s=SEAT_TRIAGE_WAIT_S, name=SEAT_TRIAGE_LOCK)
    if tl is None:
        return {"seat": seat, "batch": batch, "woke": rc != 2, "why": "triage-lock-timeout", "rc": rc,
                "auto_read": auto_read}
    try:
        return _triage_writeback(seat, plan, path, batch, rc, now, auto_read)
    finally:
        _seat_deliver_unlock(tl)


def _triage_writeback(seat, plan, path, batch, rc, now, auto_read):
    """_triage_deliver 的寫回(呼叫端拿著 triage 小鎖)。"""
    cur = TR.load(path) or {}
    mine = cur.get("woke_at") == now and cur.get("woke_batch") == batch
    if rc == 2:
        # 一個字都沒進目標(dm-ctl 契約)⇒ 回滾「叫過了」那幾欄,下一拍再試;3/4 可能已經貼進去了,照契約勿重貼 ⇒ 記成叫過
        #   ★ review r2 m1:回滾到第 WAKE_RC2_MAX 次就不再回滾(記成叫過);wake_log 不回滾
        #   ★ review r3 m3:次數每席累計、不看批次(換批不歸零),叫醒成功才歸零
        n = int(cur.get("woke_attempts") or 0) + 1
        if mine and n < WAKE_RC2_MAX:
            back = {k: v for k, v in cur.items() if k not in _WOKE_ROLLBACK_KEYS}
            back.update(plan.get("prev_woke") or {})
            back.update({"woke_attempts": n, "woke_attempts_batch": batch})
            TR.write(path, back)
            return {"seat": seat, "batch": batch, "woke": False, "why": "rc2", "rc": rc}
        if mine:
            cur = dict(cur)
            cur.update({"woke_rc": rc, "auto_read": [], "woke_attempts": n, "woke_attempts_batch": batch})
            TR.write(path, cur)
        return {"seat": seat, "batch": batch, "woke": False, "why": "rc2-max" if mine else "rc2", "rc": rc}
    if mine:
        cur = dict(cur)
        cur.update({"woke_rc": rc, "auto_read": auto_read})
        # review r3 m3:叫醒成功才歸零。★ review r4 m9:「成功」照 dm-ctl 四態契約算 —— 3=已送未驗、4=進了沒送出/
        #   框內不可判,兩者都「可能已經進了目標、勿重貼」,本檔也已把它們記成叫過(不重試)。woke_attempts 只量
        #   「連續幾次一個字都沒進(rc=2)」,3/4 打斷了這個連續 ⇒ 一樣歸零;不歸零的話,之後某一批偶發一次 rc=2
        #   就直接撞上限、不再重試,而那一席其實一直收得到。其他 rc(不在契約裡)不動。
        if rc in (0, 3, 4):                          # r4-m9-reset-on-delivered
            cur["woke_attempts"] = 0
        TR.write(path, cur)
    return {"seat": seat, "batch": batch, "woke": True, "rc": rc,
            "mode": (cur or {}).get("woke_mode"), "auto_read": auto_read}


WAKE_DAILY_MAX = 12              # the maintainer 09-30 18:4x(預設照建議):每席一天最多叫醒 12 次,超過只在待審區顯示
WAKE_IDLE_MAX_S = 3600          # 閒置超過 1 小時的席不叫(等它下次自己開口時由 hook 提醒)


def _seat_activity_age(seat, wid, hub, env=None, now=None):
    """那一席**真的**多久沒動靜(秒);量不到 ⇒ None。
    ★ review r1 M3:舊版用 tmux #{window_activity},而 activity_probe.py 檔頭寫明它會被 TUI 自己重繪刷新
      (席位檔 12-17 小時舊而 window_activity 只有 0.2 小時)⇒ 「閒置超過 1 小時不叫」這道閘永遠不會關。
      改用本倉唯一的真活動訊號 activity_probe(claude transcript / codex rollout / agy db / 橋接訊息的 mtime),
      與右欄 [X]、殺窗前重驗同一條鏈(seat_for_window → activity_age)。
    席位檔找法與 _seat_records_all_cabins 同一道:那一艙的 seats 目錄裡 w<窗>.json、schema 對、uuid 尾五碼 = 席碼。"""
    if not wid or not SEAT_RE.fullmatch(seat or ""):
        return None
    try:
        import activity_probe as _AP
        e = _env(env)
        root = str(state_root(env))
        h = _hub_or_empty(hub)
        dirs = []
        if h.startswith("aihub-"):
            dirs.append(os.path.join(root, "instances", h[len("aihub-"):], "seats"))
        dirs += [seats_dir_lib.seats_dir(environ=e), os.path.join(root, "seats")]
        for d in dirs:
            path = os.path.join(str(d), "w%s.json" % str(wid).lstrip("@"))
            try:
                rec = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or SA.uuid_tail5(rec.get("session_uuid")) != seat:
                continue
            prov, sess = _AP.seat_for_window(str(wid), str(d))
            # ★ review r2 M1:seat_for_window → live_session_for 用的是**本行程**的艙 env
            #   (EZ2_AIB_INSTANCE / EZ2_AIHUB_SOCKET);從 upkeep(launchd)或別艙右欄跑時認不出那一席
            #   /clear 後的現用 session,退回登記那支(clear 後停寫)⇒ 1 小時後永遠 idle-over-1h、永遠不叫。
            #   這裡用**那一席自己的艙**(hub → 艙 id)與窗號明給,再取 {登記, 現用} 裡最新的那一支。
            cands = [sess]
            reg, _note = _AP.resolve_session(rec)
            cands.append(reg)
            inst = _hub_instance(h)
            if prov == "claude" and reg and inst:
                import ctx_by_seat as _CBS
                live, _rebound, _n = _CBS.resolve_live_session(reg, instance=inst, window=str(wid))
                cands.append(live)
            ages = [a for a in (_AP.activity_age(prov, c, now=now) for c in dict.fromkeys(x for x in cands if x))
                    if a is not None]
            return min(ages) if ages else None
    except Exception:  # noqa: BLE001 —— 探針壞掉 = 量不到,不擋叫醒
        return None
    return None


def _hub_instance(hub):
    """tmux socket 名 → 艙鍵(與 ctx_by_seat.instance_key 的 socket 對映同一條):aihub ⇒ main、aihub-<艙> ⇒ <艙>。"""
    h = _hub_or_empty(hub)
    if h == "aihub":
        return "main"
    if h.startswith("aihub-") and len(h) > len("aihub-"):
        return h[len("aihub-"):]
    return None


def _cabin_queue_enabled(hub, env=None):
    """那一艙(aihub-<艙>)有沒有打開信件排隊;認不出艙或讀不到 ⇒ None。"""
    hub = _hub_or_empty(hub)
    if not hub.startswith("aihub-"):
        return None
    home = os.path.join(str(state_root(env)), "instances", hub[len("aihub-"):])
    if not os.path.isdir(home):
        return None
    e = dict(_env(env))
    e["EZ2_AIB_INSTANCE_HOME"] = home
    try:
        pol, how = load_policy(e)
    except Exception:  # noqa: BLE001
        return None
    return bool(pol.get("enabled")) if how == "ok" else False


def _wake_gate(seat, rec, env, now, idle_cap=True):
    """叫醒前的三道閘(the maintainer 09-30 18:4x,預設照 a seat 建議):
    1. 那一席所在的艙要**有開信件排隊** —— 佇列是整台機器共用的,別艙沒開就別替它叫(以前開一艙就全機都叫);
    2. 那一席真活動(activity_probe,不是 tmux 重繪時戳)閒置超過 1 小時不叫(閒很久的席每叫一次就花一次額度;
       它下次自己開口時 hook 會提醒);量不到 ⇒ 放行;
    3. 每席一天最多 WAKE_DAILY_MAX 次。
    登記表查不到這一席 ⇒ 1、2 不判(舊行為;治具也走這條),3 照判。回 (ok, why)。"""
    log = [t for t in (rec.get("wake_log") or []) if isinstance(t, (int, float)) and now - int(t) < 86400]
    if len(log) >= WAKE_DAILY_MAX:
        return False, "daily-max"
    recs = _seat_records_all_cabins(seat, env)
    if not recs:
        return True, ""
    wid, _prov, hub, _cur = recs[0]
    for r in recs:
        if pane_is_dead(r[0], r[2], env=env) is False:
            wid, _prov, hub, _cur = r
            break
    if _cabin_queue_enabled(hub, env) is False:
        return False, "cabin-queue-off"
    # 量不到(未知引擎 / 找不到席位檔 / 探針壞)⇒ 放行,但照樣算進每日上限(叫醒成功才記 wake_log)
    idle = _seat_activity_age(seat, wid, hub, env, now=now)
    # auto_tidy(idle_cap=False):the maintainer 要「閒下來就處理」,閒很久也算閒;花費由每日上限擋
    if idle_cap and idle is not None and idle > WAKE_IDLE_MAX_S:
        return False, "idle-over-1h"
    return True, ""


TIDY_WAKE_COOLDOWN_S = 120       # auto_tidy 叫醒冷卻(有新信才叫,不必等舊的 10 分鐘)
TIDY_QUIET_S = 120               # the maintainer 10-13 17:0x:畫面 2 分鐘沒變才算閒
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TICK_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:ms|s|m|h|秒|分鐘?|小時)\b|\d+:\d+(?::\d+)?|\d+%|\d+\s*分前")
QUIET_TAIL_LINES = 8


def _quiet_text(screen):
    """判「畫面沒變」用的內容(審查:整窗雜湊會被狀態列每 15 秒刷新的分鐘數、時鐘、用量 % 弄成永遠在變 ⇒ 永遠不叫):
    去色碼、拿掉最後 QUIET_TAIL_LINES 行(輸入框下方的狀態列區)、再拿掉計時 / 時鐘 / 百分比(同 a seat pane_idle_state 的作法)。"""
    lines = _ANSI_RE.sub("", screen or "").rstrip("\n").split("\n")
    body = "\n".join(lines[:-QUIET_TAIL_LINES] if len(lines) > QUIET_TAIL_LINES else lines)
    return _TICK_RE.sub("", body)


def _screen_quiet(seat, rec, env, now, screen_fn=None):
    """畫面連續 TIDY_QUIET_S 秒沒變 ⇒ True;還在變 ⇒ False;量不到(登記表查不到 / 擷取失敗)⇒ None(交給判忙那一道)。
    畫面雜湊與「從何時起沒變」記在分組檔(screen_sha / screen_since),每拍比一次。回 (結果, 新的 rec)。"""
    recs = _seat_records_all_cabins(seat, env)
    if not recs:
        return None, rec
    wid, _prov, hub, _cur = recs[0]
    for r in recs:                      # 審查:跟 _wake_gate 同一個挑法(第一個活窗),不拿死掉 / 舊的那一筆
        if pane_is_dead(r[0], r[2], env=env) is False:
            wid, _prov, hub, _cur = r
            break
    grab = screen_fn or (lambda w, h: capture_screen(w, h, env=env))
    screen = grab(wid, hub)
    if screen is None:
        return None, rec
    sha = hashlib.sha1(_quiet_text(screen).encode("utf-8")).hexdigest()[:16]
    rec = dict(rec)
    if rec.get("screen_sha") != sha:
        rec["screen_sha"], rec["screen_since"] = sha, now
        return False, rec
    try:
        return now - int(rec.get("screen_since") or now) >= TIDY_QUIET_S, rec
    except (TypeError, ValueError):
        return None, rec


def _one_line_for_line_reader(text, provider):
    """★ 10-02 第三輪凍結審 MAJOR:叫醒提醒(摘要)是多行、整段直接貼(no_relay);API 席(deepseek/nvidia/
    openrouter…)用 input() 一行一行讀,tmux 把每個換行當 Enter ⇒ 一封拆成十幾輪、各花一次 API,收據卻說已送達。
    ★ 10-02 P5:凡是 _triage_mode==digest 的家(含 grokbot/claudebot/codexbot 橋接席)也攤成一行;
    inline_onboard_provider 為真的家(含 copilot/gemini)照舊攤。判不出 provider 或例外 ⇒ 原字不動。"""
    try:
        import seat_onboard as _SO
        if _triage_mode(provider) != "digest" and not _SO.inline_onboard_provider(provider):
            return text
        sep = _SO.INLINE_SEP
    except Exception:  # noqa: BLE001 —— 查不到就原字(不擋叫醒)
        return text
    return sep.join(p.strip() for p in str(text).splitlines() if p.strip())


def _triage_mode(provider):
    """叫醒提醒分兩種(the maintainer 09-30 18:1x):API 席(auth=api_key)與桌面橋接席(menu_group=bot)⇒ digest(摘要直接附上);
    其餘(Claude/Codex/Grok/agy/Copilot 原生席,kimi 等未登記先比照)⇒ agent(跑 triage、派子 AI)。"""
    try:
        import panel_layout as _PL
        rec = _PL.provider_record(str(provider or "")) or {}
    except Exception:  # noqa: BLE001 —— 查不到就當原生席(不擋叫醒)
        rec = {}
    if str(rec.get("auth") or "").lower() == "api_key" or str(rec.get("menu_group") or "").lower() == "bot":
        return "digest"
    return "agent"


def triage_tick(env=None, now=None, hold_fn=None, deliver_fn=None, force=False):
    """節流版(排空器 drain-idle 每幀呼叫,形狀同 retention_tick):距上次不到 TRIAGE_EVERY_S 就不跑。
    回 None = 這次沒跑;否則回每席結果列。hold_fn(seat, row) / deliver_fn(item) 給尺注入。"""
    now = int(time.time() if now is None else now)
    ok, _reason = admit(env)
    if not ok:
        return None
    root = queue_root(env)
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = root / "triage.stamp"
        last = int((stamp.read_text(encoding="utf-8").strip() or "0")) if stamp.is_file() else 0
    except (OSError, ValueError):
        last = 0
    if not force and now - last < TRIAGE_EVERY_S:
        return None
    # 與 drain_once 共用 drain.lock:兩個右欄同一拍都跑到這裡,只有一個能叫醒(否則同一批會貼兩次)
    try:
        fd = os.open(str(root / "drain.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 與排空器同一把 drain.lock(行尾註解:別讓 v2-70 的 flock 刀錨數到兩次)
        except OSError:
            return None
        try:
            (root / "triage.stamp").write_text("%d\n" % now, encoding="utf-8")
        except OSError:
            pass
        out = []
        for seat in all_seats(env):
            tl = _seat_deliver_lock(seat, env, wait_s=SEAT_TRIAGE_WAIT_S, name=SEAT_TRIAGE_LOCK)
            if tl is None:
                out.append({"seat": seat, "woke": False, "why": "triage-lock-timeout"})
                continue
            try:
                res = _triage_seat(seat, env, now, hold_fn)
            except Exception as exc:  # noqa: BLE001 —— 一席壞掉不得擋其他席、更不得擋投遞
                res = {"seat": seat, "woke": False, "why": "error:%s" % exc.__class__.__name__}
            finally:
                _seat_deliver_unlock(tl)
            if res is not None:
                out.append(res)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
    # 鎖已放掉才投(review r1 m2):每一席的叫醒最多等 dm-ctl 60 秒,不讓排空器陪著等
    for i, res in enumerate(out):
        plan = res.pop("_plan", None)
        if plan is None:
            continue
        try:
            out[i] = _triage_deliver(res["seat"], plan, env, deliver_fn, hold_fn)
        except Exception as exc:  # noqa: BLE001
            out[i] = {"seat": res["seat"], "woke": False, "why": "error:%s" % exc.__class__.__name__}
    return out


# ── 待審區自動處理 stage-tidy(a seat × a seat 10-13,設計稿 DESIGN-dm-waiting-unify-1013 §S6)──
#   機器層只動四種已送達的信:重複(留最新)/ 被取代的舊版 / 純回執 / 超過 1 小時的驗證信;其餘交 AI(need_ai)。
#   絕不碰:要裁(decide)、真人寄的、人審、交件(deliver)、自報(report)、還沒送到的(queued/claimed)。
#   開關:每席 <佇列>/<席>/tidy.json 優先,其次全艦 <佇列>/tidy-policy.json,都沒有 = 關(備選版;原版預設開)。
#   全艦檔刻意不併進 policy.json:write_policy 會整份重寫、load_policy 對形狀很嚴,併進去會互相蓋掉。
TIDY_SCHEMA = "aib-dm-tidy/v1"
TIDY_DEFAULT = False   # 10-13 a seat 備選:預設關(真席沒試前不改所有席的叫醒行為);要開:aib dm-queue tidy-switch on --fleet
TIDY_NEED_AI_MAX = 10
TIDY_SUMMARY_CHARS = 80
TIDY_TEST_PING_AGE_S = 3600
TIDY_NEVER_KINDS = ("decide", "deliver", "report")
# 驗證信只認兩種寫死的形狀(e2e 代號、新機開席驗收);ping 這類口語不猜
TIDY_TEST_PING_RES = (
    re.compile(r"^\s*\[e2e e2e-\d{6}-\d{3,5}\]"),
    re.compile(r"^\s*\S{0,12}請只回覆 AIB-SEAT-READY\s*$"),   # 審查:整封就是那句才算,引用到這句的真信不收
)


def tidy_seat_path(seat, env=None):
    return seat_dir(seat, env) / "tidy.json"


def tidy_fleet_path(env=None):
    return queue_root(env) / "tidy-policy.json"


def _read_tidy_flag(path):
    """回 True / False / None(檔不在、壞檔、形狀不對 = None,交給下一層)。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != TIDY_SCHEMA or not isinstance(data.get("auto_tidy"), bool):
        return None
    return data["auto_tidy"]


def tidy_enabled(seat, env=None):
    """(開不開, 來源)。來源 = seat / fleet / default。"""
    v = _read_tidy_flag(tidy_seat_path(seat, env))
    if v is not None:
        return v, "seat"
    v = _read_tidy_flag(tidy_fleet_path(env))
    if v is not None:
        return v, "fleet"
    return TIDY_DEFAULT, "default"


def tidy_switch(value, seat=None, env=None):
    """value = on / off / default。seat=None ⇒ 全艦檔;default ⇒ 拿掉那一層的檔。回寫完之後的 tidy_enabled。"""
    path = tidy_fleet_path(env) if seat is None else tidy_seat_path(seat, env)
    if value == "default":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        AJ.write_json(str(path), {"schema": TIDY_SCHEMA, "auto_tidy": value == "on"})
    if seat is not None:
        return tidy_enabled(seat, env)
    flag = _read_tidy_flag(path)
    return (TIDY_DEFAULT, "default") if flag is None else (flag, "fleet")


def _tidy_untouchable(row):
    return (row.get("status") != "delivered" or row.get("kind") in TIDY_NEVER_KINDS
            or row.get("sender_kind") == "human")


def _is_test_ping(row, now):
    try:
        old = int(now) - int(row.get("sent_at") or row.get("ts") or now) >= TIDY_TEST_PING_AGE_S
    except (TypeError, ValueError):
        return False
    return old and any(r.search(str(row.get("msg") or "")) for r in TIDY_TEST_PING_RES)


def tidy_plan(rows, now):
    """純函式:待審列 ⇒ [(id, rev, why)]。why = duplicate / superseded / notice / test_ping。"""
    plan, seen = [], set()
    cls = TR.classify(rows)
    by_id = {str(r.get("id")): r for r in rows}

    def add(rid, why):
        r = by_id.get(rid)
        if r is None or rid in seen or _tidy_untouchable(r):
            return
        seen.add(rid)
        plan.append((rid, r.get("rev"), why))

    for grp in cls["groups"]["duplicate"]:
        for rid in grp["ids"]:
            if rid != grp["rep"]:
                add(rid, "duplicate")
    for r in rows:
        if r.get("superseded_by"):
            add(str(r.get("id")), "superseded")
    for rid in cls["groups"]["notice"]:
        if TR.is_notice((by_id.get(rid) or {}).get("msg")):
            add(rid, "notice")
    for r in rows:
        if r.get("kind") == "notice":
            add(str(r.get("id")), "notice")   # S7-2:寄件人標知會的,機器層代標讀(讀過即備存)
    for r in rows:
        if _is_test_ping(r, now):
            add(str(r.get("id")), "test_ping")
    return plan


def _tidy_summary(msg):
    text = " ".join(str(msg or "").split())
    return text[:TIDY_SUMMARY_CHARS]


TIDY_MARK_WOKE_WAIT_S = 2.0     # --mark-woke 等 triage 小鎖的上限:呼叫端是 hook,不能陪 triage_tick 等 30 秒


def _tidy_mark_woke(seat, env, now):
    """a seat 10-13 17:20:用量提醒已把待審清單交給那一席 ⇒ 記成「叫過了」,triage_tick 的冷卻與「新信」都從這裡算。
    只寫 woke_at / woke_ids(= 現在的未讀),不動 wake_log(每日上限只算 triage 自己的叫醒)。回 (記了沒, 原因)。"""
    tl = _seat_deliver_lock(seat, env, wait_s=TIDY_MARK_WOKE_WAIT_S, name=SEAT_TRIAGE_LOCK)
    if tl is None:
        return False, "triage-lock-busy"
    try:
        path = triage_path(seat, env)
        rows = tabs_payload(seat, env=env, now=now)["tabs"]["waiting"]["rows"]
        rec = TR.build_record(seat, rows, prev=TR.load(path), now=now)
        rec.update({"woke_at": now, "woke_ids": list(rec.get("unread_ids") or []), "woke_by": "stage-tidy"})
        return (True, "ok") if TR.write(path, rec) else (False, "triage-write-failed")
    finally:
        _seat_deliver_unlock(tl)


TIDY_RELAY_MAX_BYTES = 200_000


def _relay_text(msg):
    """merge_groups 用:不是長訊指標 ⇒ False;是指標 ⇒ 落檔內容(讀不到 / 太大 ⇒ None,不參與相近合併)。"""
    path = DR.relay_path_of(msg)
    if not path:
        return False
    try:
        p = Path(path)
        if p.stat().st_size > TIDY_RELAY_MAX_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def stage_tidy(seat, env=None, now=None, dry=False, mark_woke=False):
    """`aib dm-queue stage-tidy`:先做機器層(遵守開關),回該交 AI 的信。介面與 a seat 10-13 17:11 定案。
    mark_woke(17:20):need_ai 不空、不是 dry ⇒ 記進 triage.json 的叫醒時間(_tidy_mark_woke)。"""
    now = int(time.time() if now is None else now)
    on, src = tidy_enabled(seat, env)
    q_ok, q_why = admit(env)
    t = tabs_payload(seat, env=env, now=now)
    rows = t["tabs"]["waiting"]["rows"]
    out = {"enabled": bool(on and q_ok), "switch": src, "queue": q_why if not q_ok else "ok", "dry": bool(dry),
           "cleaned": 0, "cleaned_ids": [], "need_ai": [], "hold": t["badges"]["hold"],
           "expired": t["badges"]["expired"], "todo": 0, "source_rev": t["source_rev"]}
    cleaned = set()
    if out["enabled"]:
        for rid, rev, why in tidy_plan(rows, now):
            if dry:
                ok = True
            elif why == "notice":
                ok = _transition(seat, rid, "read", "system", env=env, now=now, why=AUTO_READ_WHY,
                                 if_rev=rev, no_rev_check=False, expect_status="delivered")["ok"]
                if ok:
                    _after_read(seat, rid, env=env, now=now)
            else:
                ok = _transition(seat, rid, "archived", "system", env=env, now=now, why=why,
                                 if_rev=rev, no_rev_check=False, expect_status="delivered")["ok"]
            if ok:
                cleaned.add(rid)
                out["cleaned_ids"].append({"id": rid, "why": why})
        out["cleaned"] = len(cleaned)
        if cleaned and not dry:
            out["source_rev"] = source_rev(seat, env)
    todo = [r for r in rows if r.get("id") not in cleaned
            and r.get("status") in ("queued", "claimed", "delivered") and not r.get("expired_now")]
    out["todo"] = len(todo)
    # the maintainer 10-13 17:3x「一樣的內容、或相近的一樣要先合併,然後再處理」:同寄件人同 thread 或正文相近 ⇒ 一組,
    #   need_ai 一列 = 一組(代表 = 最新那封,merged = 組內其他封);信本身不動(相近 ≠ 一樣,舊的可能有細節)。
    groups = TR.merge_groups(todo, relay_text=_relay_text)
    out["groups"] = len(groups)
    for g in groups[:TIDY_NEED_AI_MAX]:
        r = g[-1]
        try:
            age = max(0, now - int(g[0].get("sent_at") or g[0].get("ts") or now))
        except (TypeError, ValueError):
            age = None
        out["need_ai"].append({"id": r.get("id"), "rev": r.get("rev"), "from": r.get("sender"),
                               "sender_kind": r.get("sender_kind"), "kind": r.get("kind"),
                               "summary": _tidy_summary(r.get("msg")),
                               "relay_path": DR.relay_path_of(r.get("msg")), "age_s": age,
                               "merged": [{"id": m.get("id"), "rev": m.get("rev"), "when": m.get("when")}
                                          for m in g[:-1]]})
    if mark_woke:
        if dry or not out["need_ai"]:
            out["marked_woke"], out["mark_why"] = False, ("dry" if dry else "nothing-for-ai")
        else:
            out["marked_woke"], out["mark_why"] = _tidy_mark_woke(seat, env, now)
    return out


def _replaceable(item):
    """只有廣播型(order/rule/pipeline)且帶 thread、帶寄件席的信才參與「取代」;新舊兩邊都用這一個判準。
    ★ 還原刀錨:test-dm-queue-v2-70.py 把 BROADCAST_KINDS 那截拿掉 ⇒ 交件/要裁/自報也會被合併,對應格必紅。"""
    return item.get("kind") in BROADCAST_KINDS and bool(item.get("thread")) and bool(item.get("sender_seat"))


def _supersede_target(seat, item, env=None):
    """同收件席、同寄件席、同 thread、廣播型、還活著(queued/hold/delivered/read)的最新一則。"""
    if not _replaceable(item):
        return None
    best = None
    for old in _all_items(seat, env=env):
        if old["id"] == item["id"]:
            continue
        if not _replaceable(old):
            continue
        if old.get("thread") != item["thread"] or old.get("sender_seat") != item["sender_seat"]:
            continue
        if old.get("status") not in ("queued", HOLD_STATUS, "delivered", "read"):
            continue
        if old.get("superseded_by"):
            continue
        if best is None or int(old.get("ts") or 0) > int(best.get("ts") or 0):
            best = old
    return best


def upsert(seat, msg, sender_label, env=None, now=None, hold=None, hub="", **kw):
    """接口 §3 upsert:廣播型(order/rule/pipeline)且有 thread ⇒ 取代同 thread 的舊版;否則 = intercept。
    舊版標 superseded_by / superseded_at 並保留正文;舊版若還沒送到(queued/hold)→ archived(why=superseded),
    永遠不會再投出去;已送到(delivered/read)的舊版狀態不動,只掛「已被取代」。"""
    now = int(time.time() if now is None else now)
    out = intercept(seat, msg, sender_label, env=env, now=now, hold=hold, hub=hub, **kw)
    out["superseded"] = None
    if out.get("take") != "queue":
        return out
    item = _load_item(seat, out["id"], env)
    old = _supersede_target(seat, item, env=env) if item else None
    if not old:
        return out
    item["supersedes"] = old["id"]
    _write_item(seat, item, env)
    old["superseded_by"] = item["id"]
    old["superseded_at"] = now
    _write_item(seat, old, env)
    if old.get("status") in ACTIVE_STATUSES:
        _transition(seat, old["id"], "archived", "system", env=env, now=now, why="superseded")
    out["superseded"] = old["id"]
    _audit(seat, env, ts=now, id=item["id"], to="supersedes", actor="system", ok=True,
           reason="ok", old=old["id"], thread=item.get("thread"))
    return out


def record(seat, msg, sender_label, status="delivered", rc=None, env=None, now=None, attempts=None, **kw):
    """直送(沒經佇列、對方閒著直接貼進去)之後把這一則也記進歷史(接口 §3 record):
    status=delivered(rc0)或 hold_human(rc3/4;第 159 項起 rc2 + attempts=1 = 自動重送一次仍沒進目標)。
    不進 order.json,永遠不會被排空器再投。"""
    now = int(time.time() if now is None else now)
    if status not in ("delivered", HOLD_STATUS):
        raise ValueError("status")
    item = _new_item(seat, msg, sender_label, env=env, now=now, status=status, **kw)
    if str(_env(env).get("AIB_DM_RELAY_BYTES") or "") == "0":
        item["no_relay"] = True        # review r13 M1:人審後重排也要整行貼(同 enqueue)
    if status == "delivered":
        item["delivered_at"] = now
    else:
        item["held_at"] = now
        item["hold_rc"] = None if rc is None else int(rc)
        if rc == 2 and attempts:
            # 第 159 項:dm-ctl 自動重送一次仍回 2 ⇒ 用 attempts 那句(與排空器的 attempts 語意同一張表),不用「不在碼表裡」
            item["attempts"] = int(attempts)
            item["hold_why"] = HOLD_WHY["attempts"] % int(attempts)
        else:
            item["hold_why"] = (HOLD_WHY["rc3"] if rc == 3 else HOLD_WHY["rc4"] if rc == 4
                                else HOLD_WHY["rc_other"] % int(rc or 0))
    _write_item(seat, item, env)
    if status == "delivered":
        _close_original(item, env=env, now=now)   # S7-3:直送那條送達才結原信
    if status == HOLD_STATUS:
        # hold 的信人要看得到、要能 requeue ⇒ 留在 order.json。
        _order_append(seat, item["id"], env)
    # 直送的廣播也走取代鏈(a seat 02:0x 抓:不掛的話同 thread 兩版都留、沒 superseded_by,歷史裡看不出哪版作廢)。
    old = _supersede_target(seat, item, env=env)
    if old:
        item["supersedes"] = old["id"]
        _write_item(seat, item, env)
        old["superseded_by"] = item["id"]
        old["superseded_at"] = now
        _write_item(seat, old, env)
        if old.get("status") in ACTIVE_STATUSES:
            _transition(seat, old["id"], "archived", "system", env=env, now=now, why="superseded")
    _audit(seat, env, ts=now, id=item["id"], **{"from": None}, to=status, actor="system", ok=True,
           reason="record", rc=rc, old=(old["id"] if old else None))
    return item


def _all_items(seat, env=None, with_errors=False):
    """掃席目錄裡所有 q_*.json(含已離開 order.json 的歷史);壞檔另列。"""
    d = seat_dir(seat, env)
    items, errors = [], []
    if not d.is_dir():
        return (items, errors) if with_errors else items
    for p in sorted(d.iterdir()):
        name = p.name
        if not name.endswith(".json") or not ITEM_RE.fullmatch(name[:-5]):
            continue
        item, why = _load_item_raw(seat, name[:-5], env)
        if item is None:
            errors.append({"id": name[:-5], "reason": why})
        else:
            items.append(item)
    return (items, errors) if with_errors else items


def history_items(seat, env=None):
    """已離開待審的信(delivered/read/done/archived/deleted),舊在前。"""
    rows = [i for i in _all_items(seat, env=env) if i.get("status") not in ACTIVE_STATUSES]
    rows.sort(key=lambda i: (int(i.get("ts") or 0), i.get("id") or ""))
    return rows


TAB_OF_STATUS = {
    "queued": "waiting", "claimed": "waiting", HOLD_STATUS: "waiting", "delivered": "waiting",
    "read": "read", "done": "done", "archived": "archived", "deleted": "deleted",
}
TABS = ("waiting", "read", "done", "archived", "deleted")


def _row(item, seat, now, index=None):
    pri = normalize_priority(item.get("priority"))
    sent = item.get("sent_at")
    try:
        age = max(0, (int(now) - int(sent)) // 60) if sent is not None else None
    except (TypeError, ValueError):
        age = None
    row = {
        "id": item["id"],
        "priority": pri,
        "priority_label": PRIORITY_LABEL[pri],
        "priority_sgr": PRIORITY_SGR[pri],
        "when": item.get("when") or taipei_stamp(item.get("ts")),
        "ts": int(item.get("ts") or 0),
        "sender": item.get("sender_label") or "未知",
        "sender_kind": normalize_sender_kind(item.get("sender_kind")),
        "sender_source": normalize_sender_source(item.get("sender_source")),
        "origin": item_origin(item, seat),
        "wid": item.get("wid") or "",
        "session_uuid": item.get("session_uuid") or "",
        "msg": item.get("msg") or "",
        "rev": item_rev(item),
        "status": item.get("status") or "queued",
        "attempts": int(item.get("attempts") or 0),
        # hold_why 是**給人看的中文句**(只有 zh-TW);要四語的消費端請用 hold_rc 當鍵去查自己的字典,
        # 不要翻譯這一句(開發 B a seat 09-12 15:20 問任務區那行,答:印四語就用 hold_rc)。
        "hold_why": item.get("hold_why") or "",
        "hold_rc": item.get("hold_rc"),
        # the maintainer 09-30 15:3x(收件匣 A 項 4):停住的時刻。沒帶這欄時,畫面退回建立時刻的尾五字(= 分:秒)
        #   當成「待人處理的時刻」印出來,是錯的;點選人審信時的說明行讀它印 HH:MM 停住。
        "held_at": item.get("held_at"),
        # 第 70 項 P2 多帶的鍵(接口 §4);None = 量不到(v1 舊信)。
        "thread": item.get("thread"),
        "kind": normalize_kind(item.get("kind")),
        "sender_seat": item.get("sender_seat"),
        "human_id": item.get("human_id"),
        "sent_at": sent,
        "delivered_at": item.get("delivered_at"),
        "read_at": item.get("read_at"),
        "done_at": item.get("done_at"),
        "done_evidence": item.get("done_evidence"),
        "archived_at": item.get("archived_at"),
        "archived_why": item.get("archived_why"),
        "supersedes": item.get("supersedes"),
        "superseded_by": item.get("superseded_by"),
        "superseded_at": item.get("superseded_at"),
        "expires": item.get("expires"),
        "is_update": bool(item.get("supersedes")) and item.get("status") in ("queued", "delivered"),
        # 第 13 項:expired 是標記(不刪、不改 status),不是 status;與 expires(第 40 項時效)分開。
        "expired_now": (item.get("expired") or _expired(item, now)) and item.get("status") in ("queued", "claimed", HOLD_STATUS, "delivered"),
        "age_min": age,
        "legacy": bool(item.get("_legacy")),
    }
    if index is not None:
        row["index"] = index
    return row


def _waiting_key(row, idx, vidx=None):
    """待審 tab 的排序鍵(接口 §4):真人 → 要裁 → 更新 → 緊急程度 → order.json 序 → 時刻。畫面不得再排一次。
    不在 order.json 的(已送達/待人處理)排在同組排隊信之後,彼此照 waiting-order.json(vidx);沒記過的照時刻。"""
    rid = row.get("id")
    if rid in idx:
        rank = idx[rid]
    else:
        rank = 10 ** 9 + ((vidx or {}).get(rid, 10 ** 6))
    return (
        # 09-15 the maintainer「分開」:待審頁上段=還沒送出(排隊/貼稿中/待人處理),下段=已送達、對方未讀
        1 if row.get("status") == "delivered" else 0,
        0 if row.get("sender_kind") == "human" else 1,
        0 if row.get("kind") == "decide" else 1,
        0 if row.get("is_update") else 1,
        PRIORITY_RANK.get(row.get("priority"), 2),
        rank,
        int(row.get("ts") or 0),
        row.get("id") or "",
    )


def tabs_payload(seat, env=None, now=None):
    """五 tab 的投影(接口 §4)。排序在這裡排好,畫面不得再排;壞檔進 errors 不進 rows(病 3)。"""
    now = int(time.time() if now is None else now)
    items, errors = _all_items(seat, env=env, with_errors=True)
    tabs = {t: {"n": 0, "oldest_when": None, "oldest_ts": None, "rows": []} for t in TABS}
    for item in items:
        tab = TAB_OF_STATUS.get(item.get("status"), "waiting")
        row = _row(item, seat, now)
        if tab == "deleted":
            row = {k: row[k] for k in ("id", "when", "sender", "ts", "rev", "status")}
            row.update({"deleted_at": item.get("deleted_at"), "deleted_by": item.get("deleted_by"),
                        "deleted_why": item.get("deleted_why"), "msg": (item.get("msg") or "")[:40]})
        tabs[tab]["rows"].append(row)
    idx = {i: n for n, i in enumerate(_load_order(seat, env)["ids"])}
    vidx = {i: n for n, i in enumerate(_load_waiting_order(seat, env))}
    tabs["waiting"]["rows"].sort(key=lambda r: _waiting_key(r, idx, vidx))
    for t in ("read", "done", "archived", "deleted"):
        tabs[t]["rows"].sort(key=lambda r: (-int(r.get("ts") or 0), r.get("id") or ""))
    for t, rec in tabs.items():
        rec["n"] = len(rec["rows"])
        if rec["rows"]:
            oldest = min(rec["rows"], key=lambda r: int(r.get("ts") or 0))
            rec["oldest_ts"] = int(oldest.get("ts") or 0)
            rec["oldest_when"] = (oldest.get("when") or "")[6:11] or None
    waiting = tabs["waiting"]["rows"]
    # ★ the maintainer 09-30 17:2x:待審列帶上程式歸納的標籤([重複×N] / [重複] / [通知] / [更NN] / [更NN·最新])。
    #   讀檔住這裡(資料層),渲染器只照 row["triage_tag"] 畫 —— render_lib 不准做 I/O(test-golden-frames)。
    #   沒有分組檔、或這一封不在分組檔裡 ⇒ 不加這個鍵(沿用舊列形狀,其他尺比對整列的不受影響)。
    try:
        _tri = TR.load(triage_path(seat, env))
    except Exception:  # noqa: BLE001 —— 分組檔是輔助資訊,讀壞了不得讓整個待審框變錯誤列
        _tri = None
    if _tri:
        for _r in waiting:
            _tag = TR.tag_for(_tri, _r.get("id"))
            if _tag:
                _r["triage_tag"] = _tag
    badges = {
        "unread": sum(1 for r in waiting if r["status"] in ("queued", "delivered", "claimed") and not r["expired_now"]),
        "expired": sum(1 for r in waiting if r["expired_now"]),
        "update": sum(1 for r in waiting if r["is_update"]),
        "hold": sum(1 for r in waiting if r["status"] == HOLD_STATUS),
        "decide": sum(1 for r in waiting if r["kind"] == "decide"),
        "human": sum(1 for r in waiting if r["sender_kind"] == "human"),
    }
    return {"schema": TABS_SCHEMA, "seat": seat, "now": now, "source_rev": source_rev(seat, env),
            "tabs": tabs, "badges": badges, "errors": errors}


def brief_payload(seat, env=None, now=None):
    """`list --brief`:給 P5 hook 與右欄的一行數字。"""
    t = tabs_payload(seat, env=env, now=now)
    w = t["tabs"]["waiting"]
    out = dict(t["badges"])
    # ★ 「未讀 N 則(最早 HH:MM)」的「最早」要取**同一個母體**(未讀=queued/delivered/claimed 且未到期),
    #   不是整個待審頁(含待人處理/已到期)。09-15 修 taipei_stamp 恆印現在之前,兩者都印當下所以看不出來。
    unread_rows = [r for r in (w.get("rows") or [])
                   if r.get("status") in ("queued", "delivered", "claimed") and not r.get("expired_now")]
    first = min(unread_rows, key=lambda r: int(r.get("ts") or 0)) if unread_rows else None
    out.update({"seat": seat,
                "oldest_when": ((first.get("when") or "")[6:11] or None) if first else None,
                "oldest_ts": (int(first.get("ts") or 0) if first else None),
                "errors": len(t["errors"]), "source_rev": t["source_rev"]})
    return out


def stale_payload(env=None, now=None, minutes=None, seat=None):
    """第 149 項:每席未讀(queued/delivered 且沒 read_at)與其中發出超過 N 分鐘的則數。
    這個數字存在的理由:今天 11 則裁定卡三小時沒有任何一端知道。"""
    now = int(time.time() if now is None else now)
    minutes = STALE_MINUTES_DEFAULT if minutes is None else int(minutes)
    cutoff = now - minutes * 60
    out = []
    for s in ([seat] if seat else all_seats(env)):
        cand = [i for i in _all_items(s, env=env)
                if i.get("status") in ("queued", "delivered", "claimed") and i.get("read_at") is None]
        # 過期(expires 已到 或 第 13 項的 expired 標記)的不算未讀——否則永遠亮著;另給 expired 一格(a seat 00:3x)。
        expired = [i for i in cand if (i.get("expired") or _expired(i, now))]
        unread = [i for i in cand if not (i.get("expired") or _expired(i, now))]
        stale = [i for i in unread if i.get("sent_at") is not None and int(i["sent_at"]) < cutoff]
        oldest = min(unread, key=lambda i: int(i.get("sent_at") or i.get("ts") or 0)) if unread else None
        out.append({
            "seat": s, "unread": len(unread), "stale": len(stale), "expired": len(expired), "minutes": minutes,
            "oldest_ts": (int(oldest.get("sent_at") or oldest.get("ts") or 0) if oldest else None),
            "oldest_when": (oldest.get("when") if oldest else None),
            "ids": [i["id"] for i in stale],
        })
    return out


def migrate(env=None, apply=False, seat=None):
    """v1 → v2 落盤。dry-run 只印筆數與缺欄;--apply 才寫(值不變,只補 None 與 schema)。"""
    out = {"apply": bool(apply), "seats": [], "n_legacy": 0, "n_v2": 0, "errors": []}
    for s in ([seat] if seat else all_seats(env)):
        items, errors = _all_items(s, env=env, with_errors=True)
        rec = {"seat": s, "legacy": [], "v2": 0}
        for item in items:
            if item.get("_legacy"):
                raw = json.loads(item_path(s, item["id"], env).read_text(encoding="utf-8"))
                missing = [k for k in V2_FIELDS if k not in raw]
                rec["legacy"].append({"id": item["id"], "missing": missing})
                if apply:
                    _write_item(s, item, env)
            else:
                rec["v2"] += 1
        out["seats"].append(rec)
        out["n_legacy"] += len(rec["legacy"])
        out["n_v2"] += rec["v2"]
        out["errors"].extend({"seat": s, **e} for e in errors)
    return out


MAYBE_STALE_MINUTES = 30      # 第 40 項:發出超過這麼久才送到,標「可能過時」(只標記,不攔截;攔截語意要第二方注入審)
EXPIRED_HOLD_WHY = "已到期(expires %s),未投;人 requeue 或 delete"

# ── 第 13 項:私訊過期標記(只標記、不刪、不改 status、不碰 TRANSITIONS 與 order 移除)──
# expired 是旗標(expired/expired_at/expired_why),不是 status;畫面端與右欄看 expired_now 決定「不再算卡住」。
# 可逆(審 2/3):requeue 清 expired 三欄;席又活過來(查到窗且 pane 活)時 expire_sweep 清 seat_closed 標記。
HOLD_HUMAN_EXPIRE_S = 24 * 3600          # hold_human 超過 24 小時自動標 expired
EXPIRED_WHY_SEAT_CLOSED = "seat_closed"  # 收件席已關(查到窗且 pane_dead=1 連續多輪或超時才坐實;查無窗一律不標)
EXPIRED_WHY_HOLD_24H = "hold_24h"        # 待人處理超過 24 小時
SEAT_LIVENESS_SCHEMA = "aib-dm-queue-seat-liveness/v1"
SEAT_CLOSED_DEAD_ROUNDS = 2              # 連續 N 輪 pane_dead=1 才認「席已關」(respawn 一幀死後恢復不算)
SEAT_CLOSED_DEAD_MINUTES = 5             # 或首次看到死窗起超過 T 分鐘才認「席已關」


GHOST_HEARTBEAT_S = 60      # §89.15.8 契約第 3 條:心跳間隔上限,開窗前固定
GHOST_SCHEMA = "aib-inbox-ghost-report/v1"


def ghost_report(frames_path, env=None, now=None, window_start=None):
    """§4b / §89.15.8 的對帳工具:把畫面端每幀留痕(state/inbox-frames.jsonl)對回來源,算出
    ghost_events(畫面有列而來源沒有對應 id/rev、或幀宣稱的 source_rev 比來源還新)與
    unknown_samples(frame_seq 跳號、心跳超過 60 秒、幀壞掉、來源讀不到)。同一鍵連續同原因算一次。
    印的是**原始序號與逐筆事件**,不是一句「零次」——讀者從這裡重算,不讀作者的摘要。
    量不到的地方填 None 並寫進 unknown,不填 0。"""
    now = int(time.time() if now is None else now)
    out = {"schema": GHOST_SCHEMA, "frames_path": str(frames_path), "now": now,
           "frames": 0, "seats": {}, "ghost_events": [], "unknown_samples": [],
           "first_ts": None, "last_ts": None, "elapsed_seconds": None, "heartbeat_max_s": GHOST_HEARTBEAT_S}
    try:
        lines = Path(frames_path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        out["unknown_samples"].append({"kind": "frames_unreadable", "why": exc.__class__.__name__})
        return out
    last_seq, last_ts = {}, {}
    last_ghost_key = {}
    cache = {}
    for n, line in enumerate(lines, start=1):
        try:
            fr = json.loads(line)
            seat = fr["seat"]; seq = int(fr["frame_seq"]); ts = int(fr["ts"])
        except (ValueError, KeyError, TypeError):
            out["unknown_samples"].append({"kind": "frame_corrupt", "line": n})
            continue
        if window_start is not None and ts < int(window_start):
            continue
        out["frames"] += 1
        out["first_ts"] = ts if out["first_ts"] is None else min(out["first_ts"], ts)
        out["last_ts"] = ts if out["last_ts"] is None else max(out["last_ts"], ts)
        st = out["seats"].setdefault(seat, {"frames": 0, "min_seq": seq, "max_seq": seq, "gaps": 0, "late": 0})
        st["frames"] += 1; st["min_seq"] = min(st["min_seq"], seq); st["max_seq"] = max(st["max_seq"], seq)
        if seat in last_seq:
            if seq != last_seq[seat] + 1:
                st["gaps"] += 1
                out["unknown_samples"].append({"kind": "seq_gap", "seat": seat, "from": last_seq[seat], "to": seq, "line": n})
            if ts - last_ts[seat] > GHOST_HEARTBEAT_S:
                st["late"] += 1
                out["unknown_samples"].append({"kind": "heartbeat", "seat": seat, "gap_s": ts - last_ts[seat], "line": n})
        last_seq[seat], last_ts[seat] = seq, ts
        if seat not in cache:
            items, errors = _all_items(seat, env=env, with_errors=True)
            cache[seat] = ({i["id"]: i for i in items}, errors, source_rev(seat, env))
        by_id, errors, cur_rev = cache[seat]
        if errors:
            out["unknown_samples"].append({"kind": "source_unreadable", "seat": seat, "n": len(errors), "line": n})
        frev = fr.get("source_rev")
        if isinstance(frev, int) and frev > cur_rev:
            out["ghost_events"].append({"kind": "future_source_rev", "seat": seat, "frame_seq": seq, "frame_rev": frev,
                                        "source_rev": cur_rev, "line": n})
        for row in (fr.get("rows") or []):
            try:
                rid, rrev = row[0], int(row[1])
            except (TypeError, ValueError, IndexError):
                out["unknown_samples"].append({"kind": "row_corrupt", "seat": seat, "line": n}); continue
            item = by_id.get(rid)
            why = None
            if item is None:
                why = "id_not_in_source"
            elif rrev > item_rev(item):
                why = "rev_ahead_of_source"
            if why:
                key = (seat, rid, why)
                if last_ghost_key.get(seat) == key:
                    continue    # 同一鍵連續同原因算一次
                last_ghost_key[seat] = key
                out["ghost_events"].append({"kind": why, "seat": seat, "id": rid, "frame_rev": rrev,
                                            "frame_seq": seq, "line": n})
            else:
                if last_ghost_key.get(seat, (None,))[1:2] == (rid,):
                    last_ghost_key[seat] = None
    if out["first_ts"] is not None:
        out["elapsed_seconds"] = out["last_ts"] - out["first_ts"]
    out["ghost_count"] = len(out["ghost_events"]); out["unknown_count"] = len(out["unknown_samples"])
    out["population_nonempty"] = bool(out["frames"]) and any(bool(v) for v in cache.values() for v in [v[0]])
    return out


def delivery_text(item, now=None):
    """排空器貼進對方框的正文(接口 §5、第 40 項):前面加一行時效,原 msg 不改。
    有時刻:`本則發出於 HH:MM:SS,現在 HH:MM:SS,已過 N 分 S 秒`(未滿 1 分印 `已過 S 秒`;the maintainer 09-15 要精準到秒);≥ MAYBE_STALE_MINUTES 再加 `;可能過時,動手前先確認還算數`;
    有 supersedes 再加 `(取代 HH:MM 那則)`;時刻量不到:`本則發出時刻量不到(舊版佇列),現在 HH:MM`——不印空、不猜。"""
    now = int(time.time() if now is None else now)
    sent = item.get("sent_at") or item.get("ts")
    body = item.get("msg") or ""
    try:
        sent = int(sent)
    except (TypeError, ValueError):
        sent = None
    if sent is None or sent <= 0:
        head = "本則發出時刻量不到(舊版佇列),現在 %s" % taipei_stamp(now)[6:]
    else:
        secs = max(0, now - sent)
        age = secs // 60
        elapsed = ("%d 分 %d 秒" % (age, secs % 60)) if age else ("%d 秒" % secs)
        head = "本則發出於 %s,現在 %s,已過 %s" % (taipei_stamp(sent)[6:], taipei_stamp(now)[6:], elapsed)
        if age >= MAYBE_STALE_MINUTES:
            head += ";可能過時,動手前先確認還算數"
    sup = item.get("supersedes")
    if sup:
        old_when = (item.get("_supersedes_when") or "")[6:11]
        head += "(取代 %s 那則)" % (old_when or sup)
    return head + "\n" + body


def hold_expired(seat, env=None, now=None):
    """第 40 項:到期的信不投,而且要讓人看得到——轉 hold_human(why=已到期),留在待審框待人 requeue/delete。
    只碰 queued 的;claimed(排空器手上)與已 hold 的不動。回轉掉的 id 清單。"""
    now = int(time.time() if now is None else now)
    out = []
    for item in list_items(seat, env=env):
        if item.get("status") != "queued" or not _expired(item, now):
            continue
        res = _transition(seat, item["id"], HOLD_STATUS, "system", env=env, now=now,
                          why=EXPIRED_HOLD_WHY % taipei_stamp(int(item["expires"]))[6:11])
        if res.get("ok"):
            out.append(item["id"])
    return out


def _expired(item, now):
    exp = item.get("expires")
    if exp is None:
        return False
    try:
        return int(exp) <= int(now)
    except (TypeError, ValueError):
        return False


def _hold_expired(item, now):
    """hold_human 且 held_at 超過 24 小時(第 13 項)。held_at 量不到不猜(不標)。"""
    held = item.get("held_at")
    try:
        held = int(held)
    except (TypeError, ValueError):
        return False
    return (int(now) - held) > HOLD_HUMAN_EXPIRE_S


def _liveness_path(seat, env=None):
    return seat_dir(seat, env) / "liveness.json"


def _load_liveness(seat, env=None):
    try:
        data = json.loads(_liveness_path(seat, env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("schema") != SEAT_LIVENESS_SCHEMA:
        return {"dead_since": None, "dead_rounds": 0}

    def _int_or_none(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return {"dead_since": _int_or_none(data.get("dead_since")),
            "dead_rounds": _int_or_none(data.get("dead_rounds")) or 0}


def _write_liveness(seat, dead_since, dead_rounds, env=None):
    path = _liveness_path(seat, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    AJ.write_json(str(path), {
        "schema": SEAT_LIVENESS_SCHEMA,
        "seat": seat,
        "dead_since": dead_since,
        "dead_rounds": dead_rounds,
    })


def _seat_window_state(seat, env=None):
    """第 13 項(審 2/3):只信「查到窗」的那一幀。回 'alive' / 'dead' / 'unknown'。
    查無窗一律 'unknown'(新席 uuid 未落檔、席位檔不符待裁的活席都在此,不得標);
    問不到(pane_is_dead None)也 'unknown',不猜。"""
    wid, _prov = wid_for_seat(seat, env)
    hub = (_env(env).get("EZ2_AIHUB_SOCKET") or "").strip()
    if not wid or not hub:
        return "unknown"
    dead = pane_is_dead(wid, hub, env=env)
    if dead is True:
        return "dead"
    if dead is False:
        return "alive"
    return "unknown"


def _seat_liveness(seat, env=None, now=None):
    """第 13 項(審 2/3):一輪一次的席位死活判定。回 'alive' / 'dead' / 'unknown' / 'closed'。
    - 查到窗且 pane 活 ⇒ 'alive'(expire_sweep 據此清 seat_closed 標記),並清 dead 累計。
    - 查到窗且 pane_dead=1 ⇒ 累進;連續 N 輪或超過 T 分鐘 ⇒ 'closed',否則 'dead'(還沒坐實)。
    - 查無窗 / 問不到 ⇒ 'unknown',不標、也不清。"""
    now = int(time.time() if now is None else now)
    state = _seat_window_state(seat, env)
    liv = _load_liveness(seat, env)
    if state in ("alive", "unknown"):
        if liv["dead_since"] is not None or liv["dead_rounds"]:
            _write_liveness(seat, None, 0, env)
        return state
    dead_since = liv["dead_since"] if liv["dead_since"] is not None else now
    dead_rounds = liv["dead_rounds"] + 1
    if dead_since != liv["dead_since"] or dead_rounds != liv["dead_rounds"]:
        _write_liveness(seat, dead_since, dead_rounds, env)
    if dead_rounds >= SEAT_CLOSED_DEAD_ROUNDS or (now - dead_since) >= SEAT_CLOSED_DEAD_MINUTES * 60:
        return "closed"
    return "dead"


def _seat_closed(seat, env=None, now=None):
    """收件席已關(第 13 項):只信「查到窗 + pane_dead=1 連續多輪或超時」;查無窗一律不標。"""
    return _seat_liveness(seat, env, now) == "closed"


def _expire_reason(item, closed, now):
    """這一則該不該標 expired、為什麼。已標的不重標;非 waiting 態不標。"""
    if item.get("expired"):
        return None
    if item.get("status") not in WAITING_STATUSES:
        return None
    if closed:
        return EXPIRED_WHY_SEAT_CLOSED
    if item.get("status") == HOLD_STATUS and _hold_expired(item, now):
        return EXPIRED_WHY_HOLD_24H
    return None


def expire_sweep(seat=None, env=None, now=None, dry_run=False):
    """第 13 項(審 2/3、3/3):把「收件席已關」或「hold_human 超過 24 小時」的列標成 expired
    (只標記、不刪、不改 status、不碰 TRANSITIONS 與 order 移除);席又活過來時清掉 seat_closed
    標記(可逆)。只掃 waiting、有 waiting 才問一次 tmux。排空器 next_to_drain 每輪挑信前呼叫;
    畫面端 tabs_payload 唯讀,只讀 expired_now(右欄「私訊卡住」09-30 已移除)。"""
    now = int(time.time() if now is None else now)
    out = []
    for s in ([seat] if seat else all_seats(env)):
        waiting = [i for i in _all_items(s, env=env) if i.get("status") in WAITING_STATUSES]
        if not waiting:
            continue
        state = _seat_liveness(s, env, now)
        for item in waiting:
            why = _expire_reason(item, state == "closed", now)
            if why is not None:
                row = {"seat": s, "id": item["id"], "why": why}
                if not dry_run:
                    item["expired"] = True
                    item["expired_at"] = now
                    item["expired_why"] = why
                    _write_item(s, item, env)
                    row["ok"] = True
                out.append(row)
            elif state == "alive" and item.get("expired") and item.get("expired_why") == EXPIRED_WHY_SEAT_CLOSED:
                row = {"seat": s, "id": item["id"], "why": "seat_reopened", "cleared": True}
                if not dry_run:
                    item["expired"] = None
                    item["expired_at"] = None
                    item["expired_why"] = None
                    _write_item(s, item, env)
                    row["ok"] = True
                out.append(row)
    return {"n": len(out), "dry_run": bool(dry_run), "now": now, "rows": out}


def list_items(seat, env=None, newest_first=False):
    """全文。巡查必須走這條,不得裁內文。"""
    order = _load_order(seat, env)
    out = []
    for item_id in order["ids"]:
        item = _load_item(seat, item_id, env)
        if item and item.get("status") != "claimed":
            out.append(item)
    if newest_first:
        out = list(reversed(out))
    return out


def patrol_payload(seat, env=None):
    """巡查用:每封帶完整 msg,一列都不裁。"""
    # 艦長 09-12 14:12 裁(qa 抓):本 payload 是任務區「等誰」的來源;hold 與「等你閒下來就送」
    # 混成一個數字而要人做的事相反 ⇒ 帶 status / hold_why / attempts 讓消費端分得開。
    # 第 70 項 P2 起每列多帶 v2 鍵與 source_rev(list 形狀不變,所以 source_rev 掛在每列上)。
    now = int(time.time())
    srev = source_rev(seat, env)
    rows = []
    for item in list_items(seat, env=env, newest_first=True):
        row = _row(item, seat, now)
        row["source_rev"] = srev
        rows.append(row)
    return rows


def by_seat_for_render(env=None):
    root = queue_root(env)
    out = {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and SEAT_RE.fullmatch(d.name):
            out[d.name] = patrol_payload(d.name, env=env)
    return out


def _ranker_from_ids(ids):
    index_by_id = {i: n for n, i in enumerate(ids)}

    def rank(item):
        pr = PRIORITY_RANK.get(normalize_priority(item.get("priority")), 2)
        iid = item.get("id") or ""
        return (pr, index_by_id.get(iid, 10 ** 9), iid)

    return rank


def _ranker(seat, env=None):
    return _ranker_from_ids(_load_order(seat, env)["ids"])


def list_for_box(seat, env=None):
    """緊急→重要→一般→提醒;同組先到先送(order.json 順位)。"""
    items = list_items(seat, env=env, newest_first=False)
    items.sort(key=_ranker(seat, env))
    return items


def box_payload(seat, env=None):
    now = int(time.time())
    srev = source_rev(seat, env)
    rows = []
    for i, item in enumerate(list_for_box(seat, env=env), start=1):
        rows.append(_row(item, seat, now, index=i))
        rows[-1]["source_rev"] = srev
        if rows[-1]["status"] == HOLD_STATUS:
            # 畫面只讀 sender/msg;不動渲染器,把「人審」(HOLD_LABEL)掛在寄件人前面讓人看得到。
            rows[-1]["sender"] = HOLD_LABEL + "·" + rows[-1]["sender"]
    return rows


def global_payload(env=None):
    """全艙各席暫存合成一份來看。不另寫佇列檔。

    排序與單席同一套:緊急→重要→一般→提醒,同組先到先送(ts)。
    每則帶 seat,給畫面標「這則是給誰的」。
    """
    rows = []
    for seat in all_seats(env):
        for rec in box_payload(seat, env=env):
            item = dict(rec)
            item["seat"] = seat
            rows.append(item)
    rows.sort(key=lambda r: (
        PRIORITY_RANK.get(normalize_priority(r.get("priority")), 2),
        int(r.get("ts") or 0),
        r.get("seat") or "",
        r.get("id") or "",
    ))
    for i, rec in enumerate(rows, start=1):
        rec["index"] = i
    return rows


def next_to_drain(seat, env=None, now=None):
    """下一則要投的:不投 hold;到期的先轉 hold(第 40 項,hold_expired);第 13 項:expire_sweep
    把「收件席已關 / hold 超 24h」的標 expired 並跳過,所以挑出來的一定沒到期、也沒過期。"""
    now = int(time.time() if now is None else now)
    hold_expired(seat, env=env, now=now)
    expire_sweep(seat, env=env, now=now)
    items = [i for i in list_for_box(seat, env=env)
             if i.get("status") != HOLD_STATUS and not _expired(i, now) and not i.get("expired")]
    return items[0] if items else None


def move_result(seat, item_id, direction, env=None):
    """同優先才換位。沒動時帶 reason/message,給畫面講出來。"""
    if direction not in ("up", "down"):
        raise ValueError("direction")
    order = _load_order(seat, env)
    ids = list(order["ids"])
    if item_id not in ids:
        return {"ok": False, "reason": "missing",
                "message": MOVE_REASON["missing"]}
    i = ids.index(item_id)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(ids):
        return {"ok": False, "reason": "edge",
                "message": MOVE_REASON["edge"]}
    a = _load_item(seat, item_id, env)
    b = _load_item(seat, ids[j], env)
    if not a or not b:
        return {"ok": False, "reason": "missing",
                "message": MOVE_REASON["missing"]}
    if normalize_priority(a.get("priority")) != normalize_priority(b.get("priority")):
        return {"ok": False, "reason": "cross_band",
                "message": MOVE_REASON["cross_band"]}
    ids[i], ids[j] = ids[j], ids[i]
    _write_order(seat, ids, env)
    return {"ok": True, "reason": "ok", "message": ""}


def waiting_move_result(seat, item_id, direction, env=None):
    """待審 tab 上按上/下(09-15 the maintainer:「他不是在待審嗎?」):對調的是畫面上待審那一頁的鄰居。
    兩則都在排隊 ⇒ 交給 move_result(送信順序照舊跟著變);兩則都已不在排隊(已送達/待人處理)⇒
    只改待審頁顯示順序(waiting-order.json),不碰 order.json;一則排隊一則不在 ⇒ 不對調並說出來;
    真人/要裁/更新/緊急程度不同 ⇒ 照舊 cross_band。"""
    if direction not in ("up", "down"):
        raise ValueError("direction")
    rows = tabs_payload(seat, env=env)["tabs"]["waiting"]["rows"]
    ids = [r["id"] for r in rows]
    if item_id not in ids:
        return {"ok": False, "reason": "missing", "message": MOVE_REASON["missing"]}
    i = ids.index(item_id)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(ids):
        return {"ok": False, "reason": "edge", "message": MOVE_REASON["edge"]}
    idx = {x: n for n, x in enumerate(_load_order(seat, env)["ids"])}
    a, b = rows[i], rows[j]
    if _waiting_key(a, idx)[:5] != _waiting_key(b, idx)[:5]:   # 分段/真人/要裁/更新/緊急程度
        return {"ok": False, "reason": "cross_band", "message": MOVE_REASON["cross_band"]}
    a_q, b_q = a["id"] in idx, b["id"] in idx
    if a_q and b_q:
        return move_result(seat, item_id, direction, env=env)
    if a_q != b_q:
        return {"ok": False, "reason": "cross_kind", "message": MOVE_REASON["cross_kind"]}
    seq = [r["id"] for r in rows if r["id"] not in idx]
    p, q = seq.index(a["id"]), seq.index(b["id"])
    seq[p], seq[q] = seq[q], seq[p]
    _write_waiting_order(seat, seq, env)
    return {"ok": True, "reason": "ok", "message": ""}


def global_move_result(seat, item_id, direction, env=None):
    """全域框裡按上/下(第 37 項 10e):鄰居是別席的信 ⇒ 不對調並說出來;同席才交給 move_result
    (它再判同一緊急程度、最上/最下)。跨席對調沒有意義:各席的順序各自排。"""
    if direction not in ("up", "down"):
        raise ValueError("direction")
    rows = global_payload(env=env)
    idx = next((i for i, r in enumerate(rows)
                if r.get("seat") == seat and r.get("id") == item_id), None)
    if idx is None:
        return {"ok": False, "reason": "missing", "message": MOVE_REASON["missing"]}
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(rows):
        return {"ok": False, "reason": "edge", "message": MOVE_REASON["edge"]}
    if rows[j].get("seat") != seat:
        return {"ok": False, "reason": "cross_seat", "message": MOVE_REASON["cross_seat"],
                "other_seat": rows[j].get("seat") or ""}
    return move_result(seat, item_id, direction, env=env)


def move(seat, item_id, direction, env=None):
    """同優先才換位。跨緊急/重要/一般/提醒不寫檔。"""
    return bool(move_result(seat, item_id, direction, env=env).get("ok"))


def visible_pref(env=None):
    """待審框一次顯幾列。跟畫面上 [≡N] 同一份檔,沒檔就 3。"""
    e = _env(env)
    state = (e.get("EZ2_COCKPIT_STATE_DIR") or "").strip()
    if state:
        path = Path(state) / "pending-visible"
        try:
            n = int(path.read_text(encoding="utf-8").strip())
        except (OSError, TypeError, ValueError):
            n = VISIBLE
        else:
            if n in VISIBLE_CHOICES:
                return n
    return VISIBLE


def _vis(env, visible):
    if visible is not None:
        return max(1, int(visible))
    return max(1, int(visible_pref(env)))


def _empty_view(seat):
    return {
        "schema": VIEW_SCHEMA, "seat": seat, "focus_id": None,
        "anchor_id": None, "known_ids": None,
    }


def _load_view(seat, env=None):
    path = view_path(seat, env)
    if not path.is_file():
        return _empty_view(seat)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_view(seat)
    if not isinstance(data, dict) or data.get("schema") != VIEW_SCHEMA:
        return _empty_view(seat)
    out = _empty_view(seat)
    for key in ("focus_id", "anchor_id"):
        val = data.get(key)
        if isinstance(val, str) and ITEM_RE.fullmatch(val):
            out[key] = val
    raw = data.get("known_ids")
    if isinstance(raw, list):
        out["known_ids"] = [i for i in raw if isinstance(i, str) and ITEM_RE.fullmatch(i)]
    return out


def _write_view(seat, focus_id, anchor_id, env=None, known_ids=None):
    path = view_path(seat, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    if known_ids is None:
        known_ids = _load_view(seat, env).get("known_ids")
    payload = {
        "schema": VIEW_SCHEMA,
        "seat": seat,
        "focus_id": focus_id,
        "anchor_id": anchor_id,
    }
    if isinstance(known_ids, list):
        payload["known_ids"] = [
            i for i in known_ids if isinstance(i, str) and ITEM_RE.fullmatch(i)]
    AJ.write_json(str(path), payload)


def _delete_view(seat, env=None):
    path = view_path(seat, env)
    try:
        path.unlink()
    except OSError:
        pass


def _view_rows(seat, env=None, tab=None):
    """點選/捲動的母體要跟畫面那一頁同一份(09-14 22:2x:畫面畫「已讀」而點選只認待審 ⇒ 點了沒反應)。
    明示 tab(含 waiting)= tabs_payload 那一頁的列 —— 待審頁畫的是 tabs_payload 的 waiting,
    它含 delivered/hold,list_for_box 只有排隊中的(09-15 00:2x:點 delivered 那列沒反應)。
    沒給/不認得的 tab = list_for_box(舊呼叫端與既有臂的語意不動)。"""
    if tab not in TABS:
        return list_for_box(seat, env=env)
    return list((tabs_payload(seat, env=env)["tabs"].get(tab) or {}).get("rows") or [])


def _is_waiting_tab(tab):
    return tab not in TABS or tab == "waiting"


def resolve_view(seat, env=None, visible=None, tab=None):
    """回 {rows, focus, offset, focus_id}。空佇列刪 view.json(只在待審 tab;別頁空不得洗掉待審的點選)。

    沒點過的席 focus_id 是空的,focus=-1:畫面上不出現動作按鈕。
    點到那一列才寫入 focus_id。不得預設成第一則。
    visible 跟畫面上 [≡N] 同一檔;沒傳就讀那份,再沒有才 3。
    """
    vis = _vis(env, visible)
    rows = _view_rows(seat, env=env, tab=tab)
    n = len(rows)
    if n == 0:
        if _is_waiting_tab(tab) and _waiting_empty(seat, env):   # 同 _order_remove:待審頁真的空才刪
            _delete_view(seat, env)
        return {"rows": [], "focus": -1, "offset": 0, "focus_id": None, "new_count": 0}
    ids = [r["id"] for r in rows]
    view = _load_view(seat, env)
    focus_id = view.get("focus_id")
    if focus_id in ids:
        focus = ids.index(focus_id)
    else:
        focus = -1
        focus_id = None
    offset = 0
    anchor_id = view.get("anchor_id")
    if anchor_id in ids:
        offset = ids.index(anchor_id)
    if focus >= 0:
        if anchor_id in ids:
            anchor = ids.index(anchor_id)
            if anchor <= focus < anchor + vis:
                offset = anchor
        if not (offset <= focus < offset + vis):
            offset = max(0, min(focus, max(0, n - vis)))
            if focus >= offset + vis:
                offset = focus - vis + 1
            if focus < offset:
                offset = focus
    offset = max(0, min(offset, max(0, n - vis)))
    return {
        "rows": rows,
        "focus": focus,
        "offset": offset,
        "focus_id": focus_id,
        "new_count": (_new_hidden_count(ids, offset, vis, view.get("known_ids"))
                      if _is_waiting_tab(tab) else 0),
    }


def view_focus_set(seat, item_id, env=None, visible=None, tab=None):
    vis = _vis(env, visible)
    rows = _view_rows(seat, env=env, tab=tab)
    ids = [r["id"] for r in rows]
    if item_id not in ids:
        return resolve_view(seat, env=env, visible=vis, tab=tab)
    n = len(ids)
    i = ids.index(item_id)
    view = _load_view(seat, env)
    offset = 0
    if view.get("anchor_id") in ids:
        offset = ids.index(view["anchor_id"])
    offset = max(0, min(offset, max(0, n - vis)))
    if not (offset <= i < offset + vis):
        if i < offset:
            offset = i
        else:
            offset = max(0, min(i - vis + 1, max(0, n - vis)))
    _write_view(seat, item_id, ids[offset], env)
    return resolve_view(seat, env=env, visible=vis, tab=tab)


def view_focus_clear(seat, env=None, visible=None, tab=None):
    vis = _vis(env, visible)
    view = _load_view(seat, env)
    _write_view(seat, None, view.get("anchor_id"), env)
    return resolve_view(seat, env=env, visible=vis, tab=tab)


def view_focus_move(seat, direction, env=None, visible=None, tab=None):
    if direction not in ("up", "down"):
        raise ValueError("direction")
    vis = _vis(env, visible)
    state = resolve_view(seat, env=env, visible=vis, tab=tab)
    rows = state["rows"]
    if not rows:
        return state
    focus = state["focus"]
    n = len(rows)
    if focus < 0:
        nxt = state["offset"] if direction == "down" else min(
            state["offset"] + vis - 1, n - 1)
        return view_focus_set(seat, rows[nxt]["id"], env=env, visible=vis, tab=tab)
    if direction == "down":
        nxt = (focus + 1) % n
    else:
        nxt = (focus - 1) % n
    return view_focus_set(seat, rows[nxt]["id"], env=env, visible=vis, tab=tab)


def view_scroll(seat, direction, env=None, visible=None, tab=None):
    """只改畫面窗口(抬頭 1/N),不改點選的那一列。"""
    if direction not in ("up", "down"):
        raise ValueError("direction")
    vis = _vis(env, visible)
    rows = _view_rows(seat, env=env, tab=tab)
    n = len(rows)
    if n == 0:
        if _is_waiting_tab(tab) and _waiting_empty(seat, env):   # 同 _order_remove:待審頁真的空才刪
            _delete_view(seat, env)
        return {"rows": [], "focus": -1, "offset": 0, "focus_id": None, "new_count": 0}
    ids = [r["id"] for r in rows]
    view = _load_view(seat, env)
    focus_id = view.get("focus_id")
    if focus_id not in ids:
        focus_id = None
    offset = 0
    if view.get("anchor_id") in ids:
        offset = ids.index(view["anchor_id"])
    offset = max(0, min(offset, max(0, n - vis)))
    if n > vis:
        max_off = n - vis
        if direction == "down":
            offset = 0 if offset >= max_off else offset + 1
        else:
            offset = max_off if offset <= 0 else offset - 1
    _write_view(seat, focus_id, ids[offset], env)
    return resolve_view(seat, env=env, visible=vis, tab=tab)


def view_step(seat, direction, env=None, visible=None, tab=None):
    """有點選就換列(窗口跟著走);沒點選就只捲窗口。"""
    vis = _vis(env, visible)
    state = resolve_view(seat, env=env, visible=vis, tab=tab)
    if state["focus"] >= 0:
        return view_focus_move(seat, direction, env=env, visible=vis, tab=tab)
    return view_scroll(seat, direction, env=env, visible=vis, tab=tab)


def _new_hidden_count(ids, offset, vis, known):
    """已知清單之外、又不在目前窗口裡的則數。未初始化=0(第一次打開不喊新到)。"""
    if not isinstance(known, list):
        return 0
    vis_set = set(ids[offset:offset + vis])
    known_set = set(known)
    return sum(1 for i in ids if i not in known_set and i not in vis_set)


def view_observe(seat, env=None, visible=None, tab=None):
    """畫完一幀:把畫面上的則記成已知,並釘住目前窗口,免得新則把畫面搶走。
    非待審 tab 不寫:known_ids/anchor 是待審頁的「新到」記憶,別頁的列寫進去會把待審的新到算亂。"""
    vis = _vis(env, visible)
    state = resolve_view(seat, env=env, visible=vis, tab=tab)
    rows = state["rows"]
    if not rows or not _is_waiting_tab(tab):
        return state
    ids = [r["id"] for r in rows]
    view = _load_view(seat, env)
    known = view.get("known_ids")
    if not isinstance(known, list):
        known = list(ids)
    else:
        known = [i for i in known if i in ids]
    offset = state["offset"]
    visible_ids = ids[offset:offset + vis]
    for iid in visible_ids:
        if iid not in known:
            known.append(iid)
    anchor = visible_ids[0] if visible_ids else view.get("anchor_id")
    _write_view(seat, state["focus_id"], anchor, env, known_ids=known)
    return resolve_view(seat, env=env, visible=vis)


def view_jump_new(seat, env=None, visible=None, tab=None):
    """點 [+N新]:跳到第一則還沒看過的。沒有則不動。"""
    vis = _vis(env, visible)
    state = resolve_view(seat, env=env, visible=vis, tab=tab)
    rows = state["rows"]
    if not rows:
        return state
    ids = [r["id"] for r in rows]
    view = _load_view(seat, env)
    known = view.get("known_ids")
    if not isinstance(known, list):
        known = list(ids)
    known_set = set(known)
    for iid in ids:
        if iid not in known_set:
            return view_focus_set(seat, iid, env=env, visible=vis, tab=tab)
    return state


REV_MISMATCH_MSG = "這封信在你確認前被改過(或已被處理)"


def item_rev(item):
    """修訂號;舊檔沒這欄當 0。"""
    try:
        return int((item or {}).get("rev") or 0)
    except (TypeError, ValueError):
        return 0


def rev_check(seat, item_id, if_rev, env=None, no_rev_check=False):
    """確認回呼的前置(第 36/37 項):重讀那一則比對修訂號。
    回 (ok, reason, current_rev):reason ∈ ok / no_rev / not_found / rev_mismatch。
    ★ if_rev=None **一律拒**(reason=no_rev,fail-closed;艦長 07:06 裁)——沒有「舊路」;
      要繞過只准顯式 no_rev_check=True(CLI --no-rev-check,且留痕 rev_bypass=explicit-no-rev-check)。
      系統自己的路(送達後刪、清測試信)才用它。"""
    if if_rev is None:
        if no_rev_check:
            if _load_item_raw(seat, item_id, env)[0] is not None and _load_item(seat, item_id, env) is None:
                return False, "not_found", None    # 墓碑:檔在、活的信不在(第 70 項 P2)
            return True, "ok", None
        item = _load_item(seat, item_id, env)
        return False, "no_rev", (item_rev(item) if item else None)
    item = _load_item(seat, item_id, env)
    if not item:
        return False, "not_found", None    # 含墓碑:檔還在,但對 delete/edit/claim 而言它已經不在了
    cur = item_rev(item)
    try:
        want = int(if_rev)
    except (TypeError, ValueError):
        return False, "rev_mismatch", cur
    if cur != want:
        return False, "rev_mismatch", cur
    return True, "ok", cur


REV_REASON = {
    "ok": "",
    "no_rev": "這一幀沒有修訂號,重畫後再按",
    "rev_mismatch": REV_MISMATCH_MSG,
    "not_found": "這封信已經不在了",
}
CLI_NO_REV_HINT = "%(cmd)s 要帶 --if-rev:這封現在是 --if-rev %(rev)s(list --tab waiting 每列都有 rev)"
BYPASS_MARK = "rev_bypass=explicit-no-rev-check"
BYPASS_LOG = "rev-bypass.log"


def note_rev_bypass(env, cmd, seat, item_id):
    """顯式 --no-rev-check 才走到這裡:留痕一行(queue_root/rev-bypass.log),字面固定,
    與「沒帶 rev 被拒」(rev_bypass=none reason=no_rev)不得同形。"""
    try:
        root = queue_root(env)
        root.mkdir(parents=True, exist_ok=True)
        with open(str(root / BYPASS_LOG), "a", encoding="utf-8") as fh:
            fh.write("%s %s %s %s %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), cmd, seat, item_id, BYPASS_MARK))
        return True
    except OSError:
        return False


def delete_result(seat, item_id, env=None, if_rev=None, no_rev_check=None, by=None, why=None):
    """刪一則,回三態 {ok, reason, rev, message}(照 move_result 先例;36-2)。
    reason:ok / no_rev / rev_mismatch / not_found。呼叫端分得出「已刪」與「被改」。
    ★ no_rev_check=None ⇒ 當 True(程式庫寬鬆預設,與 edit 同理:36b 尺與既有格不帶 if_rev 呼叫 Q.delete);
      fail-closed 的邊界在 CLI(`delete` 要 --if-rev 或顯式 --no-rev-check 留痕)與殼——真正會拿到過期一幀的地方。
      要封閉的呼叫端明傳 no_rev_check=False。
    ★ 第 70 項 P2:刪除是**墓碑**不是 unlink——status=deleted、deleted_at/by/why 三欄、從 order.json 拿掉、
      檔留著(刪除 tab 看得到誰幾點為什麼刪)。只有 human 類 actor 能刪;程式庫沒給 by 當 human:unspecified
      (寬鬆預設同上一段的理由),CLI 沒 --by 一律拒(actor_missing,rc 7)。真要把檔拿掉只有 purge_test(測試信)。"""
    if no_rev_check is None:
        no_rev_check = True
    ok, reason, cur = rev_check(seat, item_id, if_rev, env, no_rev_check=no_rev_check)
    if not ok:
        return {"ok": False, "reason": reason, "rev": cur,
                "message": REV_REASON[reason]}  # rev_mismatch_or_missing
    # by=None 是程式庫寬鬆預設(human:unspecified);CLI 傳 ""(沒帶 --by)⇒ 到 _transition 被 actor_missing 拒。
    actor = by if by is not None else "human:unspecified"
    res = _transition(seat, item_id, "deleted", actor, env=env, why=why, no_rev_check=True)
    if not res.get("ok"):
        return {"ok": False, "reason": res["reason"], "rev": res.get("rev"),
                "message": res.get("message", "")}  # rev_mismatch_or_missing
    return {"ok": True, "reason": "ok", "rev": res.get("rev"), "message": ""}


def delete(seat, item_id, env=None, if_rev=None, no_rev_check=None, by=None, why=None):
    """刪一則(布林版,給舊呼叫端與 34 尺 A5b)。if_rev 不符 ⇒ 不刪、回 False;沒 if_rev 走程式庫寬鬆預設(見 delete_result)。"""
    return bool(delete_result(seat, item_id, env=env, if_rev=if_rev, no_rev_check=no_rev_check,
                              by=by, why=why)["ok"])


def _unlink_item(seat, item_id, env=None):
    """真的把檔拿掉(不留墓碑)。只給 purge_test 清測試信用;真信一律走 delete(墓碑)。"""
    _order_remove(seat, item_id, env)
    try:
        item_path(seat, item_id, env).unlink()
    except OSError:
        return False
    _bump_source_rev(seat, env)
    return True


def iter_queue_seats(env=None):
    root = queue_root(env)
    if not root.is_dir():
        return
    for d in sorted(root.iterdir()):
        if d.is_dir() and SEAT_RE.fullmatch(d.name):
            yield d.name


def list_test_items(env=None):
    """只列測試信。真信不進這份清單。"""
    rows = []
    for seat in iter_queue_seats(env):
        for item in list_items(seat, env=env):
            if item_origin(item, seat) == "test":
                rows.append({
                    "seat": seat,
                    "id": item.get("id"),
                    "when": item.get("when") or "",
                    "sender": item.get("sender_label") or "",
                    "msg": item.get("msg") or "",
                })
    return rows


def purge_test(env=None, dry_run=False):
    """只刪測試信。真信不動。"""
    deleted = []
    for row in list_test_items(env=env):
        seat, item_id = row["seat"], row["id"]
        if not dry_run:
            _unlink_item(seat, item_id, env=env)   # 系統路:只刪測試信(真的拿掉,不留墓碑)
        deleted.append({"seat": seat, "id": item_id})
    return deleted


def edit_result(seat, item_id, msg=None, priority=None, env=None, if_rev=None, no_rev_check=None):
    """改內容,回三態 {ok, reason, rev, item}。內容真的變了才 rev+1(34 尺 A5)。
    ★ no_rev_check=None ⇒ 當 True(程式庫呼叫的寬鬆預設):34 尺 A5 與既有格不帶 if_rev 呼叫 Q.edit;
      fail-closed 的邊界放在 CLI `edit`(要 --if-rev 或顯式 --no-rev-check)與殼(priority/body 回呼帶 rev)——
      真正會拿到過期一幀的呼叫端都在那兩層;pending_edit(36b)存稿已帶 if_rev。"""
    if no_rev_check is None:
        no_rev_check = True
    ok, reason, cur = rev_check(seat, item_id, if_rev, env, no_rev_check=no_rev_check)
    if not ok:
        return {"ok": False, "reason": reason, "rev": cur, "item": None,
                "message": REV_REASON[reason]}
    item = _load_item(seat, item_id, env)
    if not item:
        return {"ok": False, "reason": "not_found", "rev": None, "item": None,
                "message": REV_REASON["not_found"]}
    changed = False
    if msg is not None:
        new_msg = msg if isinstance(msg, str) else ""
        if new_msg != (item.get("msg") or ""):
            item["msg"] = new_msg
            changed = True
    if priority is not None:
        new_pri = normalize_priority(priority)
        if new_pri != normalize_priority(item.get("priority")):
            item["priority"] = new_pri
            changed = True
    if changed:
        item["rev"] = item_rev(item) + 1
    _write_item(seat, item, env)
    return {"ok": True, "reason": "ok", "rev": item_rev(item), "item": item, "message": ""}


def edit(seat, item_id, msg=None, priority=None, env=None, if_rev=None, no_rev_check=None):
    """改內容(舊形狀:回 item 或 None)。if_rev 不符 ⇒ None。"""
    return edit_result(seat, item_id, msg=msg, priority=priority, env=env, if_rev=if_rev,
                       no_rev_check=no_rev_check)["item"]


def _stale_claim(item, now):
    if item.get("status") != "claimed":
        return False
    claimed = item.get("claimed_at")
    try:
        claimed = int(claimed)
    except (TypeError, ValueError):
        return True
    return (now - claimed) > CLAIM_STALE_S


def claim_next(seat, env=None, now=None):
    """領一封。兩人同時領不到同一封。"""
    now = int(time.time() if now is None else now)
    d = seat_dir(seat, env)
    if not d.is_dir():
        return None
    claimed = []

    def mutate(rec):
        if rec.get("schema") != ORDER_SCHEMA:
            rec = _empty_order(seat)
        ids = [i for i in (rec.get("ids") or []) if isinstance(i, str)]
        items = []
        for item_id in ids:
            item = _load_item(seat, item_id, env)
            if not item:
                continue
            if item.get("status") == "claimed" and not _stale_claim(item, now):
                continue
            if item.get("status") == HOLD_STATUS:
                continue          # 待人處理的信不領:只有人 delete 或 requeue
            items.append(item)
        if not items:
            rec["ids"] = ids
            rec["seat"] = seat
            rec["schema"] = ORDER_SCHEMA
            return rec
        items.sort(key=_ranker_from_ids(ids))
        pick = items[0]
        pick["status"] = "claimed"
        pick["claimed_at"] = now
        pick["claim_id"] = secrets.token_hex(8)
        _write_item(seat, pick, env, locked=True)
        claimed.append(pick)
        rec["ids"] = ids
        rec["seat"] = seat
        rec["schema"] = ORDER_SCHEMA
        return rec

    AJ.update_json(str(order_path(seat, env)), mutate, default=_empty_order(seat))
    return claimed[0] if claimed else None


def claim_result(seat, item_id, env=None, now=None, if_rev=None, no_rev_check=None):
    """領指定一封,回三態 {ok, reason, rev, item}:ok / no_rev / rev_mismatch / not_found / busy(已被領走或不在序裡)。
    no_rev_check=None ⇒ 當 True(程式庫寬鬆預設;CLI/殼明傳 False 才封閉)。"""
    if no_rev_check is None:
        no_rev_check = True
    ok, reason, cur = rev_check(seat, item_id, if_rev, env, no_rev_check=no_rev_check)
    if not ok:
        return {"ok": False, "reason": reason, "rev": cur, "item": None,
                "message": REV_REASON[reason]}
    item = claim_id(seat, item_id, env=env, now=now, no_rev_check=True)   # 上面已驗過
    if not item:
        return {"ok": False, "reason": "busy", "rev": cur, "item": None,
                "message": "這封信已被領走或不在佇列裡"}
    return {"ok": True, "reason": "ok", "rev": item_rev(item), "item": item, "message": ""}


def _claimable_by_id(seat, item_id, env=None):
    """第 40 項尺抓到的洞:待審框「立刻送出」走 claim_id,而 hold 的信只有 claim_next 在擋 ⇒ 按一下就把
    「勿重貼」的信再貼一次。hold 只有人 requeue 或 delete(P1 契約),這條路也一樣。"""
    item = _load_item(seat, item_id, env)
    return bool(item) and item.get("status") != HOLD_STATUS


def claim_id(seat, item_id, env=None, now=None, if_rev=None, no_rev_check=None):
    """領指定一封(立刻送出)。已被別人領走則失敗。if_rev 不符 ⇒ 不領;沒 if_rev 走程式庫寬鬆預設(CLI/殼才封閉)。"""
    now = int(time.time() if now is None else now)
    claimed = []
    if no_rev_check is None:
        no_rev_check = True
    ok, _reason, _cur = rev_check(seat, item_id, if_rev, env, no_rev_check=no_rev_check)
    if not ok:
        return None
    if not _claimable_by_id(seat, item_id, env):
        return None

    def mutate(rec):
        if rec.get("schema") != ORDER_SCHEMA:
            rec = _empty_order(seat)
        ids = [i for i in (rec.get("ids") or []) if isinstance(i, str)]
        if item_id not in ids:
            rec["ids"] = ids
            rec["seat"] = seat
            rec["schema"] = ORDER_SCHEMA
            return rec
        item = _load_item(seat, item_id, env)
        if not item:
            rec["ids"] = ids
            rec["seat"] = seat
            rec["schema"] = ORDER_SCHEMA
            return rec
        if item.get("status") == "claimed" and not _stale_claim(item, now):
            rec["ids"] = ids
            rec["seat"] = seat
            rec["schema"] = ORDER_SCHEMA
            return rec
        item["status"] = "claimed"
        item["claimed_at"] = now
        item["claim_id"] = secrets.token_hex(8)
        _write_item(seat, item, env, locked=True)
        claimed.append(item)
        rec["ids"] = ids
        rec["seat"] = seat
        rec["schema"] = ORDER_SCHEMA
        return rec

    AJ.update_json(str(order_path(seat, env)), mutate, default=_empty_order(seat))
    return claimed[0] if claimed else None


def unclaim(seat, item_id, env=None, attempts=None):
    item = _load_item(seat, item_id, env)
    if not item:
        return None
    item["status"] = "queued"
    item["claimed_at"] = None
    item["claim_id"] = None
    if attempts is not None:
        item["attempts"] = int(attempts)
    _write_item(seat, item, env)
    return item


def hold_item(seat, item_id, why, rc=None, env=None, now=None):
    """轉待人處理:不再被 claim / drain;只有人 delete 或 requeue。"""
    item = _load_item(seat, item_id, env)
    if not item:
        return None
    item["hold_rc"] = None if rc is None else int(rc)
    _write_item(seat, item, env)
    res = _transition(seat, item_id, HOLD_STATUS, "system", env=env, now=now, why=str(why))
    return res["item"] if res.get("ok") else None


def requeue(seat, item_id, env=None, actor="human:unspecified"):
    """人把待人處理的信放回佇列:attempts 歸零、hold 欄清掉。不是 hold 的信原樣回。
    只有 human 類 actor 能做(接口 §2);CLI 要 --by human:<id>。
    第 13 項(審 2/3):requeue 同時清掉 expired/expired_at/expired_why(可逆)。"""
    item = _load_item(seat, item_id, env)
    if not item:
        return None
    if item.get("status") != HOLD_STATUS:
        return item
    res = _transition(seat, item_id, "queued", actor, env=env)
    return res["item"] if res.get("ok") else None


def hold_from_screen(screen, provider=None):
    """True=還在做事或框不可貼。草稿/選單一律擋。"""
    try:
        import composer_state as CS
        state, _text = CS.composer_state(screen, provider=provider)
    except Exception:
        return True, "unknown"
    if state in (CS.DRAFT, CS.MODAL):
        return True, state
    if state in (CS.EMPTY, CS.SUGGESTION, CS.NOT_APPLICABLE):
        return False, state or "empty"
    return True, state or "unknown"


def capture_screen(wid, hub, env=None):
    if not wid or not hub:
        return None
    # ★ 09-11 17:54(b59b6dc4)起這裡寫成 `_env()`,而 _env 要一個參數 ⇒ drain-idle 一跑就 TypeError,
    #   右欄的佇列排空從那天起全壞、暫存的信永遠不會自動送進閒置席;沒有任何格走到這一行(既有 drain 格
    #   注入 hold_fn 繞過了它)。修法照同檔其他呼叫:_env(env)。臂:test_entry_drain_idle_cli_delivers_to_idle_stub_seat。
    tmux_bin = (_env(env).get("AIB_DM_TMUX") or "tmux").strip() or "tmux"
    try:
        proc = __import__("subprocess").run(
            [tmux_bin, "-L", hub, "capture-pane", "-e", "-p", "-t", wid],
            capture_output=True, text=True, timeout=8)
    except (OSError, ValueError, __import__("subprocess").TimeoutExpired):   # review r1 m1:卡住的艙 = 量不到
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


PANE_DEAD_WHY = "pane_dead"   # 第 54 項 a:窗死了不是忙;drain 把它轉待人處理並具名,不每幀重判


def pane_is_dead(wid, hub, env=None):
    """tmux 說這個 pane 死了(#{pane_dead}=1)⇒ True;問不到 ⇒ None(量不到,不猜);活著 ⇒ False。
    第 54 項 a(顧問 09-12 實測):死窗 capture 回 unknown ⇒ 判忙 ⇒ 那席的信永遠不出、也沒人知道為什麼。"""
    if not wid or not hub:
        return None
    tmux_bin = str(_env(env).get("AIB_DM_TMUX") or "tmux").strip() or "tmux"   # 寫法刻意與 capture_screen 那行不同:那行是別支尺的刀錨(恰好一次)
    try:
        proc = __import__("subprocess").run(
            [tmux_bin, "-L", hub, "display-message", "-p", "-t", wid, "#{pane_dead}"],
            capture_output=True, text=True, timeout=8)
    except (OSError, ValueError, __import__("subprocess").TimeoutExpired):   # 一艙 tmux 卡住 = 量不到,不炸排空器
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return True if out == "1" else (False if out == "0" else None)


def seat_is_hold(wid, hub, provider=None, screen=None, second=None, env=None):
    """先看這一幀;若像空閒,再隔一小段抓第二幀。兩幀**可見文字**不同=還在動(純顏色/游標差不算,第 54 項 b)。
    窗死(第 54 項 a)⇒ (True, "pane_dead"):是忙的形狀但理由具名,drain 據此轉待人處理而不是每幀再問。"""
    if screen is None and pane_is_dead(wid, hub, env=env) is True:
        return True, PANE_DEAD_WHY
    if screen is None:
        screen = capture_screen(wid, hub, env=env)
    if screen is None:
        return True, "no-screen"
    busy, why = hold_from_screen(screen, provider=provider)
    if busy:
        return busy, why
    if second is None:
        wait = HOLD_WAIT_S
        raw = (_env(env).get("AIB_DM_HOLD_WAIT_S") or "").strip()
        if raw:
            try:
                wait = max(0.0, float(raw))
            except ValueError:
                wait = HOLD_WAIT_S
        if wait > 0:
            time.sleep(wait)
        second = capture_screen(wid, hub, env=env)
    if second is None:
        return True, "no-screen"
    try:
        import composer_state as CS
        moved = CS.frames_differ(screen, second)
    except Exception:  # noqa: BLE001 —— 判準模組壞了退回逐位元組比對(保守:寧可判忙)
        moved = second != screen
    if moved:
        return True, "moving"
    return hold_from_screen(second, provider=provider)


def intercept(seat, msg, sender_label, sender_kind="unknown",
              session_uuid="", wid="", provider="", hub="",
              priority="normal", hold=None, env=None, now=None,
              origin=None, thread=None, kind=None, sender_seat=None, human_id=None, expires=None,
              sender_source=None, sender_hub=None, sender_origin=None, closes=None):
    """admit 且忙碌 → 入列;否則交給既有貼稿。"""
    ok, reason = admit(env)
    if not ok:
        # 沒開佇列 ⇒ 直接貼。這也是「送出過」,一樣要留痕(只記入列的那一半,
        # 就會把實際發生過的那種災情排除在母體外)。
        log_send(seat, msg, sender_label, "delivered_direct", env=env, now=now)
        return {"take": "deliver", "reason": reason}
    if hold is None:
        # 第 149 項:呼叫端沒帶位址(`dm-ctl.sh:1072` 只檢查 T_CODE)就現查登記表。
        # 不補的話:空閒的席被判 no-screen ⇒ 當成忙 ⇒ 入列,而那一則入了列又因為
        # 位址是空的而永遠排不出去 —— 同一個錯誤造出兩段災情。
        wid, provider = drain_target(
            seat, {"wid": wid, "provider": provider}, env)
        hub = hub or (_env(env).get("EZ2_AIHUB_SOCKET") or "")
        busy, why = seat_is_hold(wid, hub, provider=provider, env=env)
    else:
        busy, why = bool(hold), "forced"
    if not busy:
        log_send(seat, msg, sender_label, "delivered_direct", env=env, now=now)
        return {"take": "deliver", "reason": why}
    item = enqueue(
        seat, msg, sender_label, sender_kind=sender_kind,
        session_uuid=session_uuid, wid=wid, provider=provider,
        priority=priority, env=env, now=now, origin=origin,
        thread=thread, kind=kind, sender_seat=sender_seat, human_id=human_id, expires=expires,
        sender_source=sender_source, hub=hub, sender_hub=sender_hub, sender_origin=sender_origin,
        closes=closes,
        no_relay=(str(_env(env).get("AIB_DM_RELAY_BYTES") or "") == "0"))
    return {
        "take": "queue",
        "reason": why,
        "id": item["id"],
        "priority": item.get("priority"),
        "receipt": {
            "rc_contract": 0,
            "stage": "sort-queued",
            "detail": "已排入暫存,尚未送到對方輸入框",
            "verified": "none",
            "before_placeholders": None,
            "queue": "aib-sort",
            "queue_id": item["id"],
            "priority": item.get("priority"),
        },
    }


def _settle_delivery(seat, claimed, rc, env, sent, now=None):
    """投遞 rc 對映表(單源;dm-ctl.sh 檔頭契約的消費端照抄)。

    0 → 刪(送達);2 → 可重送:attempts+1 退回 queued,滿 RESEND_MAX_ATTEMPTS 轉 hold;
    3 / 4 / 其他非 0 → hold_human(契約說勿重貼;不在碼表裡的也停手,分不出就不猜)。
    ★ 還原刀錨:test-dm-sort-queue.py 的刀把下面 `if rc == 2:` 那一段換回「非 0 一律 unclaim」,
      入口格(假投遞器回 3 跑三幀)必紅。
    ★ 第二條重送路(開發 A a seat 14:11 提,未修):排空行程在投遞中死掉,claim 過 CLAIM_STALE_S(90 s)
      會被 _stale_claim 判過期重領重送,attempts 不會加。機率低(要行程正好死在 dm-ctl 跑到一半),
      留給下一個人知道;要修的話是 claim_next 重領過期 claim 時 attempts+1。
    """
    item_id = claimed["id"]
    if rc == 0:
        # 第 70 項 P2:送達不刪、改標 delivered(離開 order.json、檔留著當歷史;收件席 read/done 才再動)。
        res = _transition(seat, item_id, "delivered", "system", env=env, now=now)
        sent.append(res["item"] if res.get("ok") else claimed)
        if res.get("ok") and res.get("item"):
            _close_original(res["item"], env=env, now=now)   # S7-3:排隊那條送達才結原信
        return
    if rc == 2:
        attempts = int(claimed.get("attempts") or 0) + 1
        if attempts >= RESEND_MAX_ATTEMPTS:
            hold_item(seat, item_id, HOLD_WHY["attempts"] % attempts, rc=rc, env=env, now=now)
        else:
            unclaim(seat, item_id, env, attempts=attempts)
        return
    why = (HOLD_WHY["rc3"] if rc == 3 else HOLD_WHY["rc4"] if rc == 4
           else HOLD_WHY["rc1"] if rc == 1 else HOLD_WHY["rc_other"] % rc)
    hold_item(seat, item_id, why, rc=rc, env=env, now=now)


def drain_once(env=None, hold_fn=None, deliver_fn=None):
    """每席最多領一封。緊急→重要→一般→提醒,同組先到先送。草稿不送。"""
    ok, _reason = admit(env)
    if not ok:
        return []
    root = queue_root(env)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "drain.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN):
            return []
        return []
    sent = []
    hub = _env(env).get("EZ2_AIHUB_SOCKET") or ""
    try:
        for seat in all_seats(env):
            try:
                _drain_seat_once(seat, env, hub, hold_fn, deliver_fn, sent)
            except Exception:  # noqa: BLE001 —— review r1 m1:一席壞掉(tmux 卡住、壞信檔)不得擋其他席
                continue
        return sent
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _drain_busy(seat, peek, env, hub, hold_fn):
    """排空器判這一席忙不忙(一個判準,領信前後各問一次都走這裡)。回 (busy, why)。"""
    _why = ""
    if hold_fn is not None:
        _res = hold_fn(peek)
        if isinstance(_res, tuple):          # (busy, why):CLI 的 _hold 回整組,drain 才分得出死窗
            busy, _why = bool(_res[0]), str(_res[1] or "")
        else:
            busy = bool(_res)
    else:
        # 第 149 項:位址現查(見 drain_target);env 要傳下去,
        # 否則注了 env 的尺量到的是真機的 AIB_DM_TMUX/等待秒數。
        t_wid, t_prov = drain_target(seat, peek, env)
        busy, _why = seat_is_hold(t_wid, hub, provider=t_prov, env=env)
    return busy, _why


def _drain_seat_once(seat, env, hub, hold_fn, deliver_fn, sent):
    """drain_once 的一席(拆出來只為了讓迴圈能逐席接住例外)。"""
    peek = next_to_drain(seat, env)
    if not peek:
        return
    busy, _why = _drain_busy(seat, peek, env, hub, hold_fn)
    if busy and _why == PANE_DEAD_WHY:
        # 第 54 項 a:死窗不是忙。這一則轉待人處理、理由具名(視窗已結束,重開後 requeue),
        # 不再每幀重問;同席後面的信下一輪自然也會撞到同一個判準。信仍可追(hold 不刪)。
        # (第 13 項:席關的標記已在 next_to_drain 的 expire_sweep 先做;這裡只在問不到
        #  窗、_seat_closed 無法確定的罕見情況下退回第 54 項的具名 hold,不致每幀重判。)
        hold_item(seat, peek["id"], HOLD_WHY["pane_dead"], env=env)
        return
    if busy:
        return
    # review r2 m2:叫醒正在貼給這一席 ⇒ 這一拍不領(信照舊排隊,下一拍再送),免得兩段貼進同一個輸入框
    lk = _seat_deliver_lock(seat, env)
    if lk is None:
        return
    try:
        # ★ review r4 m8:判閒到拿到投遞鎖之間,叫醒那句可能剛貼完(它放掉鎖我們才拿到)⇒ 那一席現在正在動。
        #   拿到鎖之後用同一個判準再問一次;忙(含死窗,下一拍前面那道會轉待人處理)⇒ 這一拍不領,信照舊排隊。
        if _drain_busy(seat, peek, env, hub, hold_fn)[0]:   # r4-m8-recheck-after-lock
            return
        # 領 peek 那一則(不是 claim_next 的第一則):next_to_drain 已跳過到期的信,claim_next 沒有這個判準,
        # 兩者的第一則可能不同 ⇒ 用 claim_id 釘住剛才看的那一則。
        claimed = claim_id(seat, peek["id"], env=env)
        if not claimed:
            return
        rc = 0
        if deliver_fn is not None:
            rc = int(deliver_fn(claimed) or 0)
        _settle_delivery(seat, claimed, rc, env, sent)
    finally:
        _seat_deliver_unlock(lk)


def all_seats(env=None):
    root = queue_root(env)
    if not root.is_dir():
        return []
    return [d.name for d in sorted(root.iterdir())
            if d.is_dir() and SEAT_RE.fullmatch(d.name)]


def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def _read_msg(args):
    if getattr(args, "msg_file", None):
        return Path(args.msg_file).read_text(encoding="utf-8")
    if getattr(args, "msg", None) is not None:
        return args.msg
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


# 投遞器環境裡一律洗掉的「寄件身分」變數(review r1 m5):從 codex 殼手動跑 upkeep / drain-idle 時,
#   dm-ctl 會拿 CODEX_THREAD_ID / DM_CODEX_SESSION_UUID 認寄件席 ⇒ 叫醒與系統投遞被標成那一席寄的。
DELIVER_IDENTITY_DROP = ("CODEX_THREAD_ID", "DM_CODEX_SESSION_UUID")


def sort_deliver_proof(seat, item_id, env=None):
    """10-15 B4:dm-ctl 的排空器模式(AIB_DM_SORT_DELIVER=1)要有證據才算數 —— 那個模式跳過真人判定、
    冒用改寫、留痕、record 與忙碌檢查,原本**一個環境變數**就打得開。
    證據 = 投遞者手上**正在貼**的那一則:
      · 佇列信(q_ 開頭):那一則在 seat 的佇列裡、狀態是 claimed(排空器 claim_id / 真人「立刻送出」都先 claim);
      · 歸納叫醒(triage_<批>):那一席 triage.json 記著「這一批叫過了」(woke_batch)而結果還沒寫回(woke_rc 是 None)。
    能力邊界同 dm-ctl「排隊投遞保留原寄件人」那段:本機行程刻意先入列再自己 claim 仍可通過;擋的是「設一個環境變數」。"""
    seat, item_id = str(seat or ""), str(item_id or "")
    if not SEAT_RE.fullmatch(seat):
        return False
    if ITEM_RE.fullmatch(item_id):
        return (_load_item(seat, item_id, env) or {}).get("status") == "claimed"
    if item_id.startswith("triage_") and len(item_id) > len("triage_"):
        rec = TR.load(triage_path(seat, env)) or {}
        return (str(rec.get("woke_batch") or "") == item_id[len("triage_"):]
                and "woke_rc" in rec and rec.get("woke_rc") is None)
    return False


def _dmctl_env(item, env=None):
    """投遞器交給 dm-ctl 的環境(抽出來給尺直接驗;行為與原本內聯那段相同,多兩件):
    · 寄件身分變數(DELIVER_IDENTITY_DROP)洗掉;
    · item["no_relay"] 為真(歸納叫醒那句)⇒ AIB_DM_RELAY_BYTES=0:dm-ctl 預設 >600 位元組就改貼「去讀某檔」
      的指標,digest 模式的叫醒(API/橋接席,本來就是要把分組直接貼給它)幾乎一定超過 ⇒ 永遠只收到指標(review r1 M2)。
      一般排隊信**不帶**這個鍵,照舊走長訊落檔。"""
    env2 = dict(os.environ if env is None else env)   # 背景 upkeep:用那一艙的環境投遞
    env2["AIB_DM_SORT_DELIVER"] = "1"
    # ★ the maintainer 09-30 15:3x(收件匣 A 項 2):不帶這個,dm-ctl 從排空器自己的行程鏈推寄件人
    #   ⇒ 排隊送出的信抬頭一律變成「系統@aib」,收件席不知道是誰寄的、也回不了信。
    #   只遞**這一則的 id**,不遞寄件人字串:dm-ctl 自己回佇列讀這一則(要是 claimed、正文要對得上),
    #   抬頭才改用信上記的原寄件人。環境裡殘留的同名變數先清掉,不讓外層的值混進來。
    for _k in [k for k in env2 if k.startswith("AIB_DM_QUEUED_")] + ["AIB_DM_SORT_WAKE_ID"]:
        env2.pop(_k, None)
    for _k in DELIVER_IDENTITY_DROP:
        env2.pop(_k, None)
    # review r2 m6:這一則住在哪一席的佇列(dm-ctl 轉給後繼席之後收件席碼就不是它了);
    #   10-15 B4:也是 dm-ctl 驗排空器模式證據(sort_deliver_proof)的那一席,叫醒那句也要帶
    if SEAT_RE.fullmatch(str(item.get("seat") or "")):
        env2["AIB_DM_QUEUED_SEAT"] = item["seat"]
    if ITEM_RE.fullmatch(str(item.get("id") or "")):
        env2["AIB_DM_QUEUED_ITEM_ID"] = item["id"]
    elif str(item.get("id") or "").startswith("triage_"):
        # 叫醒那句不是佇列信(不帶 QUEUED_ITEM_ID ⇒ 抬頭不回佇列找原寄件人),但 dm-ctl 要拿它驗證據
        env2["AIB_DM_SORT_WAKE_ID"] = item["id"]
    if item.get("no_relay"):
        env2["AIB_DM_RELAY_BYTES"] = "0"
    return env2


def _deliver_via_dmctl(item, env=None):
    """drain-idle 的投遞器:把時效行+原文寫成暫存檔交給 dm-ctl.sh(AIB_DM_SORT_DELIVER=1 走原路不再排第二次)。
    回 dm-ctl 的 rc;起不來或逾時回 4(不可判 ⇒ 絕不重送)。
    env:背景 upkeep 逐艙呼叫時傳**那一艙**的環境(艙家、aihub-<艙>);不傳 = 右欄那條路,照舊用本行程環境。"""
    ctl = str(_HERE / "dm-ctl.sh")
    msg = delivery_text(item)   # 時效行在前、原文在後(接口 §5);歷史裡存的仍是原文
    # 10-15 B9:原本固定 /tmp/aib-dmq-drain-<id>.txt —— 名字猜得到、write_text 會跟著符號連結蓋掉別的檔。
    #   改 mkstemp(O_EXCL、0600、名字隨機);開不了 ⇒ 一個字都沒送,回 2(可重送,佇列自己有 attempts 上限)。
    import tempfile
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="aib-dmq-drain-", suffix=".txt")
    except OSError:
        return 2
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(msg)
        env2 = _dmctl_env(item, env)
        proc = subprocess.run(
            ["bash", ctl, item.get("seat") or "", "--file", str(tmp)],
            capture_output=True, text=True, timeout=dmctl_timeout_s(msg), env=env2)
        return proc.returncode
    except (OSError, subprocess.TimeoutExpired):
        return 4
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ── 背景 upkeep(the maintainer 09-30):沒有人開著駕駛艙時,保留整理 / 歸納叫醒 / 投遞照樣跑 ──────────────
# 以前這三件只從右欄每幀的 drain-idle 起 ⇒ 沒有艙掛著畫面,全部停擺(信不送、完成的不備存、不叫醒)。
# `dm_queue.py upkeep` 由 launchd 每 3 分鐘起一次(tools/dm-upkeep/),逐艙跑同樣三件:
#   · 艙 = <狀態根>/instances/<艙>;只跑「那一艙 admit 准」(開關開 + 授權)且「tmux 伺服器 aihub-<艙> 活著」的艙;
#   · 每艙用自己的環境跑:EZ2_AIB_INSTANCE_HOME=<艙家>、EZ2_AIB_INSTANCE=<艙>、EZ2_AIHUB_SOCKET=aihub-<艙>;
#     外層殼帶進來的 tmux/駕駛艙變數(UPKEEP_ENV_DROP)先洗掉,免得從某一席的殼手動跑時冒用那一席;
#   · 與右欄那條路並跑是安全的:三件都用佇列根下同一份節流戳(retention.stamp / triage.stamp)
#     與同一把 drain.lock(triage_tick、drain_once 拿不到鎖就這一拍不做),upkeep 沒有另開第二套。
UPKEEP_CABIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UPKEEP_ENV_DROP = ("TMUX", "TMUX_PANE", "EZ2_TMUX_SESSION", "AIB_DM_SOCKET", "EZ2_COCKPIT_STATE_DIR",
                   "CODEX_THREAD_ID", "DM_CODEX_SESSION_UUID")   # review r1 m5:codex 殼手動跑不冒用那一席


def upkeep_cabins(env=None):
    """[(艙名, 那一艙的環境)],照艙名排序。只列目錄、不判開關(開關由 upkeep 用 admit 判)。"""
    base = _env(env)
    inst_root = state_root(env) / "instances"
    try:
        names = sorted(os.listdir(str(inst_root)))
    except OSError:
        return []
    out = []
    for name in names:
        home = inst_root / name
        if not UPKEEP_CABIN_RE.fullmatch(name) or not home.is_dir():
            continue
        e = {k: v for k, v in base.items() if k not in UPKEEP_ENV_DROP}
        e["EZ2_AIB_INSTANCE_HOME"] = str(home)
        e["EZ2_AIB_INSTANCE"] = name
        hub = _cabin_hub(name)
        if not hub:
            continue                  # 解析器不收這個艙 ⇒ 不替它猜 socket(猜錯會連到別人的 server)
        e["EZ2_AIHUB_SOCKET"] = hub
        out.append((name, e))
    return out


def cabin_server_alive(hub, env=None):
    """tmux 伺服器(-L hub)活著 ⇒ True;回「沒有伺服器」⇒ False;tmux 起不來/逾時 ⇒ None(量不到,不猜)。"""
    tb = str(_env(env).get("AIB_DM_TMUX") or "").strip() or "tmux"
    try:
        proc = subprocess.run([tb, "-L", hub, "list-sessions"], capture_output=True, text=True,
                              timeout=8, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0


def upkeep(env=None, now=None, alive_fn=None, hold_fn=None, deliver_fn=None, dry_run=False):
    """逐艙跑 retention_tick + triage_tick + drain_once。回每艙一列(ran / why / 三件結果)。
    alive_fn(艙名, 艙環境) / hold_fn(item) / deliver_fn(item) 給尺注入;不給 = 生產路徑。
    dry_run:只判哪些艙會跑,不碰任何信、不寫任何節流戳。"""
    rows = []
    for name, cenv in upkeep_cabins(env):
        row = {"cabin": name, "hub": cenv["EZ2_AIHUB_SOCKET"], "ran": False}
        rows.append(row)
        ok, why = admit(cenv)
        if not ok:
            row["why"] = "queue-off:%s" % why
            continue
        alive = alive_fn(name, cenv) if alive_fn is not None else cabin_server_alive(cenv["EZ2_AIHUB_SOCKET"], cenv)
        if alive is not True:
            row["why"] = "tmux-down" if alive is False else "tmux-unknown"
            continue
        if dry_run:
            row["why"] = "dry-run"
            continue

        def _dhold(item, _e=cenv):
            # 判忙與右欄同一個:drain_hold 回登記表現查那一席在哪一艙哪個窗,在那一艙問
            return hold_fn(item) if hold_fn is not None else drain_hold(item.get("seat") or "", item, env=_e)

        def _ddeliver(item, _e=cenv):
            return deliver_fn(item) if deliver_fn is not None else _deliver_via_dmctl(item, env=_e)

        row["ran"] = True
        try:
            ret = retention_tick(env=cenv, now=now)
            row["retention"] = None if ret is None else {"archived": len(ret.get("archived") or []),
                                                         "deleted": len(ret.get("deleted") or [])}
        except Exception as exc:  # noqa: BLE001 —— 保留整理失敗不得擋投遞(同 drain-idle)
            row["retention"] = "error:%s" % exc.__class__.__name__
        try:
            tri = triage_tick(env=cenv, now=now, deliver_fn=lambda item: _ddeliver(item),
                              hold_fn=(lambda seat, r: _dhold(r)) if hold_fn is not None else None)
            row["triage"] = None if tri is None else {"seats": len(tri), "woke": sum(1 for r in tri if r.get("woke"))}
        except Exception as exc:  # noqa: BLE001 —— 歸納/叫醒失敗不得擋投遞
            row["triage"] = "error:%s" % exc.__class__.__name__
        try:
            sent = drain_once(env=cenv, hold_fn=lambda item: _dhold(item), deliver_fn=lambda item: _ddeliver(item))  # upkeep 投遞
        except Exception as exc:  # noqa: BLE001 —— 一艙壞掉不得擋下一艙
            sent = []
            row["drain_error"] = exc.__class__.__name__
        row["sent"] = [{"id": i.get("id"), "seat": i.get("seat")} for i in sent]
    return rows


_TRANSITION_RC = {"rev_mismatch": 5, "no_rev": 6, "actor": 7, "actor_missing": 7,
                  "transition": 8, "evidence": 9, "not_found": 4}


def _v2_kw(args):
    return {"thread": getattr(args, "thread", None), "kind": getattr(args, "kind", None),
            "sender_seat": getattr(args, "sender_seat", None), "human_id": getattr(args, "human_id", None),
            "sender_source": getattr(args, "sender_source", None),
            "sender_hub": getattr(args, "sender_hub", None),
            "sender_origin": getattr(args, "sender_origin", None),
            "expires": getattr(args, "expires", None),
            "closes": getattr(args, "closes", None)}


def main(argv=None, env=None):
    """env 給尺注入用(不動 os.environ;測試改 os.environ 再 pop 會成為「洗不滿」的艙變數清除站)。"""
    p = argparse.ArgumentParser(prog="dm_queue.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("admit")

    g = sub.add_parser("enable")
    g.add_argument("--on", action="store_true")
    g.add_argument("--off", action="store_true")

    def _v2_args(sp):
        # 第 70 項 P2 送信端欄位(接口 §1);P3 的 dm-ctl 帶進來,沒帶就是 None / other。
        sp.add_argument("--thread", default=None)
        sp.add_argument("--kind", default=None, choices=KINDS)
        sp.add_argument("--sender-seat", default=None)
        sp.add_argument("--sender-source", default=None, choices=SENDER_SOURCES)
        sp.add_argument("--human-id", default=None)
        sp.add_argument("--expires", type=int, default=None)
        # the maintainer 09-30 15:3x(收件匣 A 項 2):寄件人那一艙與來源機器名,排隊投遞時抬頭照它寫原寄件人
        sp.add_argument("--sender-hub", default=None)
        sp.add_argument("--sender-origin", default=None)
        sp.add_argument("--closes", default=None)    # S7-3:這封回信送達後要結掉的原信 id(寄件席自己佇列裡)

    def _sender_args(sp):
        sp.add_argument("--seat", required=True)
        sp.add_argument("--sender-label", default="")
        sp.add_argument("--sender-kind", default="unknown")
        sp.add_argument("--session-uuid", default="")
        sp.add_argument("--wid", default="")
        sp.add_argument("--provider", default="")
        sp.add_argument("--priority", default="normal", choices=PRIORITIES)
        sp.add_argument("--origin", choices=ORIGINS)
        sp.add_argument("--msg-file")
        sp.add_argument("--msg")
        _v2_args(sp)

    q = sub.add_parser("enqueue")
    _sender_args(q)

    i = sub.add_parser("intercept")
    _sender_args(i)
    i.add_argument("--hub", default="")
    i.add_argument("--hold", choices=("yes", "no"))

    up = sub.add_parser("upsert")      # 廣播型同 thread 取代舊版;其餘 = intercept
    _sender_args(up)
    up.add_argument("--hub", default="")
    up.add_argument("--hold", choices=("yes", "no"))

    rc_ = sub.add_parser("record")     # 直送之後把這則記進歷史(P3 的 dm-ctl 在 rc0/3/4 後呼叫)
    _sender_args(rc_)
    rc_.add_argument("--status", required=True, choices=("delivered", HOLD_STATUS))
    rc_.add_argument("--rc", type=int, default=None)
    rc_.add_argument("--attempts", type=int, default=None)   # 第 159 項:自動重送過幾次(dm-ctl 上限 1)

    for name in ("read", "done"):
        sp = sub.add_parser(name)
        sp.add_argument("--seat", required=True)
        sp.add_argument("--id", required=True)
        sp.add_argument("--by", required=True)       # seat:<收件席>
        sp.add_argument("--if-rev", type=int, default=None)
        sp.add_argument("--no-rev-check", action="store_true")
        sp.add_argument("--evidence", default=None)
        sp.add_argument("--why", default=None)       # S7-1:沒有證據時的結案理由(記成 done_kind=reason)
        if name == "read":
            sp.add_argument("--done", action="store_true")    # S7-1:讀和結案一步
        else:
            sp.add_argument("--group", action="store_true")   # S7-1:同一合併組一起結
    rcl = sub.add_parser("reclass")
    rcl.add_argument("--seat", required=True)
    rcl.add_argument("--id", required=True)
    rcl.add_argument("--by", required=True)
    rcl.add_argument("--kind", required=True, choices=KINDS)

    ar = sub.add_parser("archive")
    ar.add_argument("--seat", default=None)
    ar.add_argument("--id", default=None)
    ar.add_argument("--by", default="system")
    ar.add_argument("--sweep", action="store_true")
    ar.add_argument("--retention", action="store_true", help="完成滿 12 小時備存、備存滿 30 天刪除(the maintainer 09-30)")
    ar.add_argument("--days", type=int, default=None)
    ar.add_argument("--dry-run", action="store_true")

    st = sub.add_parser("stale")
    st.add_argument("--seat", default=None)
    st.add_argument("--minutes", type=int, default=None)

    ex = sub.add_parser("expire")   # 第 13 項:標記收件席已關 / hold 超 24h 的列為 expired(不刪)
    ex.add_argument("--seat", default=None)
    ex.add_argument("--dry-run", action="store_true")

    tb = sub.add_parser("tabs")
    tb.add_argument("--seat", required=True)

    mg = sub.add_parser("migrate")
    mg.add_argument("--seat", default=None)
    mg.add_argument("--dry-run", action="store_true")
    mg.add_argument("--apply", action="store_true")

    au = sub.add_parser("audit")
    au.add_argument("--seat", required=True)
    au.add_argument("--tail", type=int, default=None)

    gh = sub.add_parser("ghosts")      # §4b:畫面留痕對回來源,算鬼影與缺測
    gh.add_argument("--frames", required=True)
    gh.add_argument("--since", type=int, default=None)

    ls = sub.add_parser("list")
    ls.add_argument("--seat", required=True)
    ls.add_argument("--newest-first", action="store_true")
    ls.add_argument("--box", action="store_true")
    ls.add_argument("--origin", choices=("live", "test", "all"), default="all")
    ls.add_argument("--tab", choices=TABS + ("all",), default=None)   # 歷史 tab;不給 = 既有 order.json 那份
    ls.add_argument("--brief", action="store_true")

    vw = sub.add_parser("view")
    vw.add_argument("--seat", required=True)
    vw.add_argument("--focus-id", default="")
    vw.add_argument("--focus-set", default="")
    vw.add_argument("--focus-clear", action="store_true")
    vw.add_argument("--focus-move", choices=("up", "down"))
    vw.add_argument("--scroll", choices=("up", "down"))
    vw.add_argument("--jump-new", action="store_true")
    vw.add_argument("--tab", choices=TABS, default=None)   # 沒帶 = 舊語意(list_for_box)

    pt = sub.add_parser("patrol")
    pt.add_argument("--seat", required=True)

    sh = sub.add_parser("show")
    sh.add_argument("--seat", required=True)
    sh.add_argument("--id", required=True)
    # the maintainer 09-30 15:3x(收件匣 A 項 2):真人按「立刻送出」也要貼時效行(本則發出於…已過 N 分),與排空器同一支 delivery_text
    sh.add_argument("--with-age", action="store_true")

    mv = sub.add_parser("move")
    mv.add_argument("--seat", required=True)
    mv.add_argument("--id", required=True)
    mv.add_argument("--dir", required=True, choices=("up", "down"))

    mv.add_argument("--global", dest="global_box", action="store_true")
    mv.add_argument("--waiting", action="store_true")   # 待審 tab:對調畫面上的鄰居(含已送達)

    dl = sub.add_parser("delete")
    dl.add_argument("--seat", required=True)
    dl.add_argument("--id", required=True)

    dl.add_argument("--if-rev", type=int, default=None)

    dl.add_argument("--no-rev-check", action="store_true")
    dl.add_argument("--by", default=None)          # human:<id>;沒帶一律拒(收件席請用 read)
    dl.add_argument("--why", default=None)

    ed = sub.add_parser("edit")
    ed.add_argument("--seat", required=True)
    ed.add_argument("--id", required=True)
    ed.add_argument("--msg-file")
    ed.add_argument("--msg")
    ed.add_argument("--priority", choices=PRIORITIES)

    ed.add_argument("--if-rev", type=int, default=None)

    ed.add_argument("--no-rev-check", action="store_true")

    cl = sub.add_parser("claim-next")
    cl.add_argument("--seat", required=True)

    ci = sub.add_parser("claim-id")
    ci.add_argument("--seat", required=True)
    ci.add_argument("--id", required=True)

    ci.add_argument("--if-rev", type=int, default=None)

    ci.add_argument("--no-rev-check", action="store_true")

    spf = sub.add_parser("sort-proof")   # 10-15 B4:dm-ctl 排空器模式的證據(rc 0=有、1=沒有)
    spf.add_argument("--seat", required=True)
    spf.add_argument("--id", required=True)

    rq = sub.add_parser("requeue")   # 人把待人處理(hold_human)的信放回佇列
    rq.add_argument("--seat", required=True); rq.add_argument("--id", required=True)
    rq.add_argument("--by", default=None)          # human:<id>;沒帶一律拒
    stl = sub.add_parser("settle")   # 真人「立刻送出」送完之後結清:照投遞 rc 對映(0→delivered、2→退回/hold、其他→hold),與排空器同一條
    stl.add_argument("--seat", required=True)
    stl.add_argument("--id", required=True)
    stl.add_argument("--rc", type=int, required=True)
    uc = sub.add_parser("unclaim")
    uc.add_argument("--seat", required=True)
    uc.add_argument("--id", required=True)

    hd = sub.add_parser("hold")
    hd.add_argument("--provider", default="")
    hd.add_argument("--wid", default="")
    hd.add_argument("--hub", default="")

    sub.add_parser("drain-idle")
    # 背景 upkeep(launchd 每 3 分鐘;tools/dm-upkeep/):逐艙跑 drain-idle 那三件,不需要有人開著駕駛艙
    upk = sub.add_parser("upkeep")
    upk.add_argument("--dry-run", action="store_true")

    # the maintainer 09-30 17:2x:待審區程式歸納(重複/通知/更NN/待處理)。不帶 --apply 只印不寫;絕不改任何信的狀態。
    tri = sub.add_parser("triage")
    tri.add_argument("--seat", required=True)
    tri.add_argument("--json", action="store_true")
    tri.add_argument("--apply", action="store_true")

    stid = sub.add_parser("stage-tidy")
    stid.add_argument("--seat", required=True)
    stid.add_argument("--json", action="store_true")
    stid.add_argument("--dry", action="store_true")
    stid.add_argument("--mark-woke", action="store_true")
    tsw = sub.add_parser("tidy-switch")
    tsw.add_argument("value", choices=("on", "off", "default"))
    tsw_who = tsw.add_mutually_exclusive_group(required=True)
    tsw_who.add_argument("--seat")
    tsw_who.add_argument("--fleet", action="store_true")

    ptst = sub.add_parser("purge-test")
    ptst.add_argument("--dry-run", action="store_true")
    sub.add_parser("list-test")

    args = p.parse_args(argv)
    env = os.environ if env is None else env

    if args.cmd == "admit":
        ok, reason = admit(env)
        _print_json({"ok": ok, "reason": reason, "plan": LP.license_plan(env)})
        return 0 if ok else 3

    if args.cmd == "enable":
        if args.on == args.off:
            print("dm_queue: 請給 --on 或 --off", file=sys.stderr)
            return 2
        ok, reason = admit(env) if args.on else (True, "ok")
        if args.on and LP.license_plan(env) != LP.ADVANCED:
            _print_json({"ok": False, "reason": "not_advanced"})
            return 3
        write_policy(bool(args.on), env=env)
        _print_json({"ok": True, "enabled": bool(args.on)})
        return 0

    if args.cmd == "enqueue":
        item = enqueue(
            args.seat, _read_msg(args), args.sender_label,
            sender_kind=args.sender_kind, session_uuid=args.session_uuid,
            wid=args.wid, provider=args.provider, priority=args.priority,
            origin=args.origin, env=env, **_v2_kw(args))
        _print_json(item)
        return 0

    if args.cmd in ("intercept", "upsert"):
        hold = None
        if args.hold == "yes":
            hold = True
        elif args.hold == "no":
            hold = False
        kw = dict(sender_kind=args.sender_kind, session_uuid=args.session_uuid,
                  wid=args.wid, provider=args.provider, hub=args.hub or env.get("EZ2_AIHUB_SOCKET") or "",
                  priority=args.priority, hold=hold, origin=args.origin, env=env, **_v2_kw(args))
        if args.cmd == "upsert":
            out = upsert(args.seat, _read_msg(args), args.sender_label, **kw)
        else:
            out = intercept(args.seat, _read_msg(args), args.sender_label, **kw)
        _print_json(out)
        return 0

    if args.cmd == "record":
        item = record(
            args.seat, _read_msg(args), args.sender_label, status=args.status, rc=args.rc,
            attempts=args.attempts,
            sender_kind=args.sender_kind, session_uuid=args.session_uuid,
            wid=args.wid, provider=args.provider, priority=args.priority,
            origin=args.origin, env=env, **_v2_kw(args))
        _print_json(_strip_private(item))
        return 0

    if args.cmd in ("read", "done"):
        if args.if_rev is None and not args.no_rev_check:
            # 10-05 a seat:CLI 沒有「重畫」可按;直接說要帶什麼、這封現在幾號。
            _ok, _rs, cur = rev_check(args.seat, args.id, None, env)
            _print_json({"ok": False, "reason": "no_rev", "rev": cur, "message": CLI_NO_REV_HINT % {
                "cmd": args.cmd, "rev": cur if cur is not None else "<list --tab waiting 那一列的 rev>"}})
            return 6
        if args.no_rev_check and args.if_rev is None:
            note_rev_bypass(env, args.cmd, args.seat, args.id)
        closing = args.cmd == "done" or getattr(args, "done", False)
        if closing and not (args.evidence or args.why):
            _print_json({"ok": False, "reason": "evidence", "message": TRANSITION_REASON["evidence"]})
            return _TRANSITION_RC["evidence"]
        if args.cmd == "read" and not args.done:
            out = mark_read(args.seat, args.id, args.by, env=env, if_rev=args.if_rev,
                            no_rev_check=bool(args.no_rev_check))
        elif args.cmd == "read":
            out = read_done(args.seat, args.id, args.by, args.evidence, env=env, if_rev=args.if_rev,
                            no_rev_check=bool(args.no_rev_check), why=args.why)
        elif args.group:
            out = done_group(args.seat, args.id, args.by, args.evidence, env=env, if_rev=args.if_rev,
                             no_rev_check=bool(args.no_rev_check), why=args.why)
        else:
            out = mark_done(args.seat, args.id, args.by, args.evidence, env=env, if_rev=args.if_rev,
                            no_rev_check=bool(args.no_rev_check), why=args.why)
        _print_json({k: v for k, v in out.items() if k != "item"})
        return 0 if out["ok"] else _TRANSITION_RC.get(out["reason"], 4)

    if args.cmd == "reclass":
        out = reclass(args.seat, args.id, args.kind, args.by, env=env)
        _print_json(out)
        return 0 if out["ok"] else _TRANSITION_RC.get(out["reason"], 2)

    if args.cmd == "archive":
        if getattr(args, "retention", False):
            _print_json(retention_sweep(env=env, dry_run=bool(args.dry_run), seat=args.seat))
            return 0
        if args.sweep:
            _print_json(archive_sweep(env=env, days=args.days, dry_run=bool(args.dry_run), seat=args.seat))
            return 0
        if not args.seat or not args.id:
            _print_json({"ok": False, "reason": "usage", "message": "archive 要 --seat --id,或 --sweep"})
            return 2
        out = archive_item(args.seat, args.id, args.by, "days", env=env)
        _print_json({k: v for k, v in out.items() if k != "item"})
        return 0 if out["ok"] else _TRANSITION_RC.get(out["reason"], 4)

    if args.cmd == "stale":
        _print_json(stale_payload(env=env, minutes=args.minutes, seat=args.seat))
        return 0

    if args.cmd == "expire":
        _print_json(expire_sweep(env=env, seat=args.seat, dry_run=bool(args.dry_run)))
        return 0

    if args.cmd == "tabs":
        _print_json(tabs_payload(args.seat, env=env))
        return 0

    if args.cmd == "migrate":
        if args.apply == args.dry_run:
            _print_json({"ok": False, "reason": "usage", "message": "migrate 要 --dry-run 或 --apply(二選一)"})
            return 2
        _print_json(migrate(env=env, apply=bool(args.apply), seat=args.seat))
        return 0

    if args.cmd == "audit":
        _print_json(audit_rows(args.seat, env=env, tail=args.tail))
        return 0

    if args.cmd == "ghosts":
        rep = ghost_report(args.frames, env=env, window_start=args.since)
        _print_json(rep)
        return 0 if (rep["ghost_count"] == 0 and rep["unknown_count"] == 0 and rep["frames"]) else 1

    if args.cmd == "list":
        if args.brief:
            _print_json(brief_payload(args.seat, env=env))
            return 0
        if args.tab:
            t = tabs_payload(args.seat, env=env)
            rows = ([r for tab in TABS for r in t["tabs"][tab]["rows"]] if args.tab == "all"
                    else t["tabs"][args.tab]["rows"])
            _print_json(rows)
            return 0
        if args.box:
            rows = box_payload(args.seat, env=env)
            if args.origin in ORIGINS:
                rows = [r for r in rows if item_origin(r, args.seat) == args.origin]
            _print_json(rows)
        else:
            rows = list_items(args.seat, env=env, newest_first=args.newest_first)
            if args.origin in ORIGINS:
                rows = [r for r in rows if item_origin(r, args.seat) == args.origin]
            _print_json(rows)
        return 0

    if args.cmd == "view":
        vis = visible_pref(env)
        tab = getattr(args, "tab", None)
        if args.focus_set or args.focus_id:
            state = view_focus_set(
                args.seat, args.focus_set or args.focus_id, env=env, visible=vis, tab=tab)
        elif getattr(args, "focus_clear", False):
            state = view_focus_clear(args.seat, env=env, visible=vis, tab=tab)
        elif getattr(args, "jump_new", False):
            state = view_jump_new(args.seat, env=env, visible=vis, tab=tab)
        elif args.scroll:
            state = view_step(args.seat, args.scroll, env=env, visible=vis, tab=tab)
        elif args.focus_move:
            state = view_focus_move(
                args.seat, args.focus_move, env=env, visible=vis, tab=tab)
        else:
            state = resolve_view(args.seat, env=env, visible=vis, tab=tab)
        _print_json({
            "focus": state["focus"],
            "offset": state["offset"],
            "focus_id": state["focus_id"],
            "n": len(state["rows"]),
            "new_count": state.get("new_count") or 0,
        })
        return 0

    if args.cmd == "patrol":
        _print_json(patrol_payload(args.seat, env=env))
        return 0

    if args.cmd == "show":
        item = _load_item(args.seat, args.id, env)
        if not item:
            return 2
        if getattr(args, "with_age", False) and item.get("msg"):
            sys.stdout.write(delivery_text(item))
            if not delivery_text(item).endswith("\n"):
                sys.stdout.write("\n")
            return 0
        sys.stdout.write(item.get("msg") or "")
        if item.get("msg") and not str(item.get("msg")).endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if args.cmd == "move":
        fn = global_move_result if args.global_box else (waiting_move_result if args.waiting else move_result)
        out = fn(args.seat, args.id, args.dir, env=env)
        _print_json(out)
        # 跨優先或到盡頭是沒動,不是故障。非 0 會讓 tmux 把指令打在畫面上,
        # 還要按 Esc 才能回來。
        return 0

    if args.cmd == "list-test":
        _print_json(list_test_items(env=env))
        return 0

    if args.cmd == "purge-test":
        deleted = purge_test(env=env, dry_run=bool(args.dry_run))
        _print_json({"ok": True, "dry_run": bool(args.dry_run),
                     "n": len(deleted), "deleted": deleted})
        return 0

    if args.cmd == "delete":
        # 第 70 項 P2:刪除只有人(--by human:<id>);收件 AI 席讀完請 `read`,不要 delete(規矩 3 的機器版)。
        # 先驗 rev 再驗 actor(rev 過期的 rc 5 / 沒帶 rev 的 rc 6 字面不變;都過了才輪到 actor 的 rc 7)。
        if args.no_rev_check and args.if_rev is None:
            note_rev_bypass(env, "delete", args.seat, args.id)
        out = delete_result(args.seat, args.id, env=env, if_rev=args.if_rev,
                            no_rev_check=bool(args.no_rev_check), by=args.by or "", why=args.why)
        _print_json(out)
        if out["ok"]:
            return 0
        return _TRANSITION_RC.get(out["reason"], 4)

    if args.cmd == "edit":
        # 第 37 項:CLI edit 一律要 --if-rev;顯式 --no-rev-check 才繞過且留痕;沒帶 ⇒ rc 6 no_rev
        if args.if_rev is None and not args.no_rev_check:
            _print_json({"ok": False, "reason": "no_rev", "message": REV_REASON["no_rev"]})
            return 6
        if args.no_rev_check and args.if_rev is None:
            note_rev_bypass(env, "edit", args.seat, args.id)
        msg = None
        if args.msg is not None or args.msg_file:
            msg = _read_msg(args)
        item = edit(args.seat, args.id, msg=msg, priority=args.priority, env=env, if_rev=args.if_rev, no_rev_check=bool(args.no_rev_check) or None)
        if not item:
            return 2
        _print_json(item)
        return 0

    if args.cmd == "claim-next":
        item = claim_next(args.seat, env=env)
        _print_json(item or {"id": None})
        return 0 if item else 3

    if args.cmd == "sort-proof":
        return 0 if sort_deliver_proof(args.seat, args.id, env=env) else 1

    if args.cmd == "claim-id":
        if args.no_rev_check and args.if_rev is None:
            note_rev_bypass(env, "claim-id", args.seat, args.id)
        out = claim_result(args.seat, args.id, env=env, if_rev=args.if_rev,
                           no_rev_check=bool(args.no_rev_check))
        if out["ok"]:
            _print_json(out["item"])
            return 0
        _print_json({"id": None, "reason": out["reason"], "rev": out["rev"],
                     "message": out["message"]})
        return {"rev_mismatch": 5, "not_found": 4, "no_rev": 6}.get(out["reason"], 3)

    if args.cmd == "settle":
        # ★ 09-15 03:5x:mouse-ctl 立刻送出原本送達後 `delete --no-rev-check` 沒帶 --by ⇒ 刪除只准 human ⇒ 被拒,
        #   信永遠停在 claimed(the maintainer 03:42 按兩則實證)。送達不刪、改標 delivered 是第 70 項 P2 的定義,與排空器同一支。
        item = _load_item(args.seat, args.id, env)
        if not item:
            print(json.dumps({"ok": False, "reason": "missing"}, ensure_ascii=False)); return 4
        if item.get("status") != "claimed":
            print(json.dumps({"ok": False, "reason": "not_claimed", "status": item.get("status")}, ensure_ascii=False)); return 5
        sent = []
        _settle_delivery(args.seat, item, int(args.rc), env, sent)
        after = _load_item(args.seat, args.id, env) or {}
        print(json.dumps({"ok": True, "status": after.get("status")}, ensure_ascii=False))
        return 0
    if args.cmd == "unclaim":
        item = unclaim(args.seat, args.id, env=env)
        _print_json(item or {"ok": False})
    if args.cmd == "requeue":
        cls, _ident = parse_actor(args.by)
        if cls != "human":
            _print_json({"ok": False, "reason": "actor_missing" if cls is None else "actor",
                         "message": TRANSITION_REASON["actor_missing" if cls is None else "actor"]})
            return 7
        item = requeue(args.seat, args.id, env=env, actor=args.by)
        _print_json(_strip_private(item) if item else {"ok": False})
        return 0 if item else 1

    if args.cmd == "hold":
        screen = sys.stdin.read() if not sys.stdin.isatty() else capture_screen(
            args.wid, args.hub or env.get("EZ2_AIHUB_SOCKET") or "")
        busy, why = hold_from_screen(screen or "", provider=args.provider or None)
        _print_json({"hold": busy, "reason": why})
        return 0

    if args.cmd == "stage-tidy":
        if not SEAT_RE.fullmatch(args.seat or ""):
            _print_json({"ok": False, "reason": "seat", "seat": args.seat})
            return 2
        _print_json(stage_tidy(args.seat, env=env, dry=args.dry, mark_woke=args.mark_woke))
        return 0

    if args.cmd == "tidy-switch":
        if args.seat is not None and not SEAT_RE.fullmatch(args.seat):
            _print_json({"ok": False, "reason": "seat", "seat": args.seat})
            return 2
        on, src = tidy_switch(args.value, seat=args.seat, env=env)
        _print_json({"ok": True, "seat": args.seat, "fleet": bool(args.fleet), "auto_tidy": on, "switch": src})
        return 0

    if args.cmd == "triage":
        if not SEAT_RE.fullmatch(args.seat or ""):
            _print_json({"ok": False, "reason": "seat", "seat": args.seat})
            return 2
        try:
            rec, rows = triage_run(args.seat, env=env, apply=args.apply)
        except TriageLockBusy:
            # review r4 m7:拿不到 triage 小鎖 ⇒ 沒寫;具名錯、rc=3(稍後再跑)
            _print_json({"ok": False, "reason": "triage-lock-busy", "seat": args.seat, "applied": False})
            return 3
        if args.json:
            # live_counts:上一份分組檔裡「現在還在待審」的封數(與 hook 那一行同一個算法;沒有分組檔 = None)
            _print_json(dict(rec, applied=bool(args.apply), live_counts=triage_counts(args.seat, env=env)))
        else:
            print("\n".join(TR.human_lines(rec, rows)))
        return 0

    if args.cmd == "upkeep":
        rows = upkeep(env=env, dry_run=args.dry_run)
        _print_json({"ts": int(time.time()), "when": taipei_stamp(), "dry_run": bool(args.dry_run),
                     "cabins": len(rows), "ran": sum(1 for r in rows if r.get("ran")),
                     "sent": sum(len(r.get("sent") or []) for r in rows),
                     "rows": [r for r in rows if r.get("ran") or not str(r.get("why") or "").startswith("queue-off")]})
        return 0

    if args.cmd == "drain-idle":
        def _hold(item):
            # 回 (busy, why) 整組:第 54 項 a 要 drain 分得出「死窗」與「忙」(前者轉待人處理具名)。
            # ★ the maintainer 09-30 15:3x(收件匣 A 項 1):不再拿信上抄的窗號配本艙 socket 問 —— 佇列是整台機器共用的,
            #   別艙席的窗號在本艙沒有意義。改走 drain_hold:登記表現查那一席現在在哪一艙哪個窗,在那一艙問。
            return drain_hold(item.get("seat") or "", item, env=env)

        try:
            retention_tick(env=env)     # the maintainer 09-30 17:1x:完成 12 小時備存、備存 30 天刪除(節流 10 分鐘)
        except Exception:  # noqa: BLE001 —— 保留整理失敗不得擋投遞
            pass
        try:
            triage_tick(env=env)        # the maintainer 09-30 17:2x:未讀 > 3 ⇒ 程式歸納 + 席閒著才叫醒一次(節流 60 秒)
        except Exception:  # noqa: BLE001 —— 歸納/叫醒失敗不得擋投遞
            pass
        # ★ hold_fn 寫成呼叫(lambda 裡叫 _hold)而不是傳函式物件:到達性閘只認「呼叫」,傳物件它看不出
        #   main 走得到 _hold,於是量 main 的入口格被判成沒量到 _hold(09-30 triage 那包撞到的,行為不變)。
        sent = drain_once(env=env, hold_fn=lambda item: _hold(item), deliver_fn=lambda item: _deliver_via_dmctl(item))
        _print_json([{"id": i.get("id"), "seat": i.get("seat")} for i in sent])
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
