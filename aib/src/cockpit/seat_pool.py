#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""R6 席位池 · **查詢面**(規格 `SPEC-R6-seat-pool-87dec-0905.md` sha16 dcf6a628)。

R2 回答「該派給哪家、哪個角色」;本模組回答**下一句**:那一家現在**有沒有位子**。

★★ 最容易做錯的一格,是先量才知道的(規格 §一):
  現機 `instances/*/seats/*.json` 有 **114** 個(散在 34 個艙),而活艙只有 **22** 窗。
  拿席位檔數當佔用數 ⇒ 池**永遠是滿的**,而且是歷史累積造成的滿;那時 `pool_full`
  恆亮不帶資訊,派工永遠停在手動,而畫面上看起來像機制有在跑。
  ⇒ **判準:一個位子被佔用 = 活艙裡一個活著的席窗。**
    席位檔只用來回答「那一窗是哪一家」,不用來數。

★ 三態不是兩態:閒 / 忙 / **量不到**。fail-closed 的方向是**忙**——
  「量不到算閒」會把一個正在工作的席當成空位派新工進去,而那在池的讀數上
  與「真的有空位」逐字相同。

★ 本模組**只查不動**:收席仍歸 `idle_reaper`(四條紅線在那邊,本檔一個字都不改)。
  查詢與動作分開,否則「問一下有沒有位子」會變成一個副作用是殺行程的呼叫。

★ 本模組**不換家**:那一家滿了就說滿了。換家是 R5 升級階梯的事;
  兩個器混在一起會讓「池滿」與「升級」在讀數上分不開(規格 §二)。

★ 配額數字**候 the maintainer**(規格 §七 / SPEC-L11 8738776a §四 三候選)。
  讀不到上限 ⇒ `quota_unset`,**不派工也不套預設值**:套預設等於替他決定,
  而那個決定不會有任何一格紅。
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import seats_dir_lib

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))
ROUTING_TABLE = os.path.join(REPO, "src", "routing", "table.json")

#: 席的三態。`UNKNOWN` 一律當忙(見檔頭)。
BUSY, IDLE, UNKNOWN = "busy", "idle", "unknown"

#: 查詢結果的 reason。成功是 `ok`。
R_OK = "ok"
R_POOL_FULL = "pool_full"
R_QUOTA_UNSET = "quota_unset"
R_UNKNOWN_PROVIDER = "unknown_provider"
R_NO_CABINS = "no_live_cabins"

#: 只有治具會設這兩個;生產一律現查(見規格 §一)。
ENV_CABINS = "EZ2_SEAT_POOL_CABINS"      # 換掉「活艙從哪來」
ENV_QUOTA = "EZ2_SEAT_POOL_QUOTA"        # 換掉配額表(JSON:{"claude":2,...})

#: 採集端每輪落的那一格 occupied 快照。R2 路由讀檔,不必自己去問每一個艙。
SNAPSHOT_NAME = "seat-pool-occupancy.json"
ENV_STATE_DIR = "EZ2_COCKPIT_STATE_DIR"
ENV_REFRESH_S = "EZ2_SEAT_POOL_REFRESH_S"
#: 節流:採集端每 5 秒一幀,盤點要對每個艙/每個窗各問一次 tmux。
#: 每幀都盤等於把 render loop 的成本乘上艦隊規模,而佔用數不會 5 秒變一次。
DEFAULT_REFRESH_S = 30


def _tmux(socket, *args, timeout=10):
    """對某個 socket 問一句;問不到回 None(**不是空字串**)。

    ★ 回 None 與回 "" 要分開:前者是「問不到」(⇒ 量不到 ⇒ 算忙),
      後者是「問到了,沒有窗」(⇒ 真的是 0)。合成一種的話,
      tmux 掛掉會被讀成「這艙沒人」,而那正好是最該保守的時候。
    """
    try:
        proc = subprocess.run(["tmux", "-L", socket, *args],
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def live_cabins():
    """活著的 `aihub-*` socket 清單。治具可用 ENV_CABINS 換掉(逗號分隔)。"""
    injected = os.environ.get(ENV_CABINS)
    if injected is not None:
        return [c for c in injected.split(",") if c]
    root = os.environ.get("TMUX_TMPDIR") or "/tmp"
    sockdir = os.path.join(root, "tmux-%d" % os.getuid())
    try:
        names = sorted(os.listdir(sockdir))
    except Exception:
        return []
    return [n for n in names if n.startswith("aihub-")]


def _instances_root():
    """席位檔的根 = `<狀態根>/instances`。**狀態根向單源要,不自己 expanduser**。

    ★ 09-05 接線時被 `tests/test-seats-dir-resolution.py` 打回來的(它掃 src/
      裡每一支自己解席位目錄的碼)。原本寫死 `~/.ez2-aib`:在設了
      `EZ2_AIB_HOME` / `EZ2_AIB_STATE_ROOT` 的殼裡會讀到**另一個根**的席位檔,
      而那份檔格式完全正確、`provider` 欄也在 —— 佔用數照樣算得出來,
      只是算的是別人的艦隊。**格式正確的錯答案不會有任何一格紅。**
    ★ 只向單源要「根」,不呼叫 `seats_dir()`:那支答的是「**本席所屬**的
      seats 目錄」,而本模組要盤的是整個艦隊(規格 §一 的母體是活艙的活席窗)。
      兩者混用會讓佔用數縮成一艙,而那個數也長得很正常。
    """
    root, _source = seats_dir_lib.state_root()
    return os.path.join(root, "instances")


def _seat_records():
    """席位檔 → {窗號: provider}。**只用來回答是哪一家,不用來數**(檔頭)。"""
    out = {}
    base = _instances_root()
    for cabin in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        seats = os.path.join(base, cabin, "seats")
        if not os.path.isdir(seats):
            continue
        for name in sorted(os.listdir(seats)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(seats, name), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except Exception:
                continue
            win = rec.get("window_id") or rec.get("window")
            if win and rec.get("provider"):
                out[(cabin, str(win))] = rec.get("provider")
    return out


def seat_state(socket, window):
    """一席的三態。**讀不到一律 UNKNOWN,呼叫端把它當忙。**

    這裡刻意不呼叫 dm:dm 在席 Working 時回 rc=4(暫態),
    而「暫態」與「這席不存在」在 rc 上分不開 —— 池不該用一個分不開的訊號當判準。
    現用的訊號是 tmux 自己的:窗還在不在、pane 是不是死的。
    """
    out = _tmux(socket, "list-panes", "-t", window, "-F", "#{pane_dead}")
    if out is None:
        return UNKNOWN
    flags = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not flags:
        return UNKNOWN               # 問到了但沒有 pane ⇒ 說不出話,不當閒
    if any(f == "0" for f in flags):
        return BUSY                  # 有活著的 pane ⇒ 佔著(保守)
    return IDLE


def occupancy(provider=None):
    """現在有幾個位子被佔著。**母體是活艙的活席窗,不是席位檔數**(規格 §一)。

    回 `{"total": n, "by_provider": {...}, "unknown": k, "cabins": [...]}`。
    `unknown` 是「量不到而被算成忙」的數量 —— 單獨回報,因為
    「4 席都在忙」與「4 席都量不到」處置不同,而佔用數上兩者相同。
    """
    seats = _seat_records()
    by_provider, total, unknown = {}, 0, 0
    cabins = live_cabins()
    unreachable = []
    for socket in cabins:
        listing = _tmux(socket, "list-windows", "-F", "#{window_id}")
        if listing is None:
            # ★ 問不到的艙不能猜它有幾席,但**必須報出來**:
            #   不報的話,佔用數是一個「下界」而讀的人以為是「實數」——
            #   而「這艙沒人」與「這艙問不到」在總數上逐字相同。
            #   現查:socket 檔有 7 個而只有 3 個答得出來(其餘是死掉的 socket 殘留)。
            unreachable.append(socket)
            continue
        cabin = socket[len("aihub-"):] if socket.startswith("aihub-") else socket
        for win in [w.strip() for w in listing.splitlines() if w.strip()]:
            state = seat_state(socket, win)
            if state == IDLE:
                continue             # 閒席不佔位子(它正是可重用的那一種)
            total += 1
            if state == UNKNOWN:
                unknown += 1
            prov = seats.get((cabin, win))
            if prov:
                by_provider[prov] = by_provider.get(prov, 0) + 1
    result = {"total": total, "by_provider": by_provider,
              "unknown": unknown, "cabins": cabins,
              # 問不到的艙:total 因此是**下界**不是實數。呼叫端要看得見這件事。
              "unreachable_cabins": unreachable,
              "total_is_lower_bound": bool(unreachable)}
    if provider:
        result["provider_occupied"] = by_provider.get(provider, 0)
    return result


def quota(path=None):
    """每家上限。**沒有就是沒有** —— 回 None,呼叫端據此回 `quota_unset`。

    ★ 家的名單向 R2 的 `table.json.companies` 要(規格 §二):
      兩邊各抄一份 provider 清單就是缺陷七判例。本表只寫數字。
    """
    injected = os.environ.get(ENV_QUOTA)
    if injected is None and path is None:
        return None
    try:
        if injected is not None:
            table = json.loads(injected)
        else:
            with open(path, encoding="utf-8") as fh:
                table = json.load(fh)
    except Exception:
        return None
    if not isinstance(table, dict) or not table:
        return None
    known = known_providers()
    if known and set(table) != known:
        return None                  # 多一家少一家都拒收(與 R2 §1.1 同形)
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0
           for v in table.values()):
        return None
    return table


def known_providers(table_path=None):
    """合法的 provider 名單 = R2 `table.json` 的 `companies` 鍵集合。"""
    try:
        with open(table_path or ROUTING_TABLE, encoding="utf-8") as fh:
            return set(json.load(fh).get("companies") or {})
    except Exception:
        return set()


def has_capacity(provider, *, quota_path=None):
    """(可不可以派, 詳情)。**不換家**:這一家滿了就說滿了(規格 §二)。"""
    known = known_providers()
    if known and provider not in known:
        return False, {"reason": R_UNKNOWN_PROVIDER, "provider": provider,
                       "known": sorted(known)}
    limits = quota(quota_path)
    if limits is None:
        # ★ 不套預設值:套了等於替 the maintainer 決定,而那個決定不會有任何一格紅
        return False, {"reason": R_QUOTA_UNSET, "provider": provider}
    limit = limits.get(provider)
    if limit is None:
        return False, {"reason": R_QUOTA_UNSET, "provider": provider}
    occ = occupancy(provider)
    if not occ["cabins"]:
        # 一個活艙都沒有 ⇒ 說不出話,不是「空得很」
        return False, {"reason": R_NO_CABINS, "provider": provider}
    used = occ["by_provider"].get(provider, 0)
    detail = {"provider": provider, "occupied": used, "limit": limit,
              "unknown_counted_as_busy": occ["unknown"], "cabins": occ["cabins"],
              "unreachable_cabins": occ["unreachable_cabins"],
              "occupied_is_lower_bound": occ["total_is_lower_bound"]}
    if used >= limit:
        detail["reason"] = R_POOL_FULL
        return False, detail
    detail["reason"] = R_OK
    return True, detail


# ── 採集端的落檔面(R6 接線,2026-09-05)───────────────────────────────────
# ★ 為什麼佔用數要由**採集端**落檔,而不是由派工端現算:
#   現算要對每一個艙的每一個窗問 tmux,而派工端不在艙裡跑;採集端本來就每輪
#   在跑、本來就在讀艙。**但落下來的東西因此一定是「當時的」** —— 所以快照
#   帶 `at`,讓讀的人自己判齡。沒有 `at` 的話,「30 秒前的滿」與「現在的滿」
#   在檔案內容上逐字相同。
# ★ 這一段仍然只查不動:它寫的是 cockpit state 目錄的一個檔,
#   不碰任何席、不殺任何行程(規格 §四)。


def snapshot_path(state_dir=None):
    """快照落在哪。拿不到 state 目錄就回 None(**不猜一個路徑**)。"""
    base = state_dir or os.environ.get(ENV_STATE_DIR)
    if not base:
        return None
    return os.path.join(base, SNAPSHOT_NAME)


def _refresh_interval():
    raw = os.environ.get(ENV_REFRESH_S)
    if raw is None:
        return DEFAULT_REFRESH_S
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_S
    return val if val >= 0 else DEFAULT_REFRESH_S


def refresh_snapshot(state_dir=None, *, now=None, min_interval_s=None):
    """採集端每輪呼叫:唯讀盤點一次,落一格 occupied。回 `(path, record|None)`。

    `record is None` = **這一輪沒盤**(節流跳過),不是「盤到 0」。
    ★ 兩者要分開:合成一種的話,節流生效與艦隊真的空掉在回傳值上逐字相同。

    落下的欄位就是 R2 路由要讀的契約:

      at                      盤點當下的 epoch(讀的人自己判齡;沒有它就分不出
                              「30 秒前的滿」與「現在的滿」)
      occupied                總佔用數(活艙的活席窗;**不是席位檔數**,見檔頭)
      by_provider             逐家佔用數(家名由席位檔的 provider 欄回答)
      unknown_counted_as_busy 量不到而被算成忙的數量(單獨報:處置不同)
      cabins                  這次問過的 socket
      unreachable_cabins      問不到的 socket(具名,不只給數量)
      occupied_is_lower_bound 只要有一個艙問不到就是 true ⇒ occupied 是下界

    ★ 這裡**不做**「滿不滿」的判斷:那要配額,而配額候 the maintainer(規格 §七)。
      落的是量到的數,不是結論;把結論落進檔案等於把 `quota_unset` 這條
      紅線搬到讀的人看不見的地方。
    """
    path = snapshot_path(state_dir)
    if not path:
        return None, None
    now = time.time() if now is None else now
    interval = _refresh_interval() if min_interval_s is None else min_interval_s
    if interval > 0:
        try:
            if now - os.path.getmtime(path) < interval:
                return path, None      # 還新,這一輪不盤(不是盤到 0)
        except OSError:
            pass                       # 沒有檔/讀不到 mtime ⇒ 照盤
    occ = occupancy()
    record = {
        "at": int(now),
        "occupied": occ["total"],
        "by_provider": occ["by_provider"],
        "unknown_counted_as_busy": occ["unknown"],
        "cabins": occ["cabins"],
        "unreachable_cabins": occ["unreachable_cabins"],
        "occupied_is_lower_bound": occ["total_is_lower_bound"],
    }
    tmp = "%s.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)          # 原子換上:讀的人不會讀到寫到一半的檔
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return path, None
    return path, record
