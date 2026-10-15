#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""影子續跑說明(the maintainer 10-13 19:4x:「如果開影子續跑,一定要列出說明」;a seat 併進推包省多少那包)。

aib test / 推前檢查(pushgate)跑完,**一定**印一段固定格式的說明,不能有不印的路徑:
  沿用幾支、挑掉幾支、實跑幾支 / 沒有 aib 一支一支跑要多久 / 這次實際多久 / 省多少 / 主要重跑原因。
--fresh(關掉影子續跑)印一行「這次沒用影子續跑」。
同時把要原樣出現在回報裡的幾行記到 <狀態根>/state/shadow-explain/<席碼>.json(reported=false;a seat 19:5x 指定位置),
由 src/policy/hooks/ship_report_gate.py 在那一席回合結束時處理(Claude:回覆沒貼就由 hook 自己顯示一次、不擋;其他家:check 提醒)。認不出席 ⇒ 只印不記。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src" / "cockpit"))
import i18n_lib  # noqa: E402 - 畫面文字走四語字典(tests/test-tools-i18n-ratchet.py)

LOC = i18n_lib.resolve_locale()   # 語系單一來源(盤查第 1 條):環境變數之外還有設定檔層


def T(key, params=None):
    return i18n_lib.t("aib.explain." + key, params, locale=LOC)


def explain_lines(dur, *, reused, skipped, ran, without_s, actual_s, causes, fresh=False,
                  lease_wait_s=0, mem_wait_s=0, unverified=0):
    """回 (全部要印的行, 回報裡必須原樣出現的行)。causes = 已翻好的原因字串清單(最多前三)。
    10-14(a seat 派):actual_s 是含排隊的牆鐘;排隊等機器 / 等記憶體另列一行,「省多少」拿扣掉等待之後真正在跑的時間比
    (506 支那批印「這次實際 3.4 小時」,其中 58 分鐘是排隊,看起來像 aib 變慢)。
    unverified = 全部格子都跳過、沒驗的支數:不算在沿用 / 實跑裡,要寫出來,不然跟開頭「沿用 / 實跑」的總數對不上。"""
    if fresh:
        ln = T("fresh", {"n": int(ran)})
        return [T("head"), ln], [ln]
    params = {"reused": int(reused), "skipped": int(skipped), "ran": int(ran), "n": int(unverified or 0)}
    counts = T("counts_unverified" if unverified else "counts", params)
    without = T("without", {"total": dur(without_s)}) if without_s and without_s > 0 else T("without_none")
    waits = [(k, float(v)) for k, v in (("wait_lease", lease_wait_s), ("wait_mem", mem_wait_s)) if v and float(v) >= 1]
    run_s = max(0.0, float(actual_s or 0) - sum(v for _k, v in waits))
    # 等待寫在「這次實際」同一行(不另起一行):右欄任務區與 tmux 提示只取第 2~5 行,多一行會把「省多少」擠掉(審查)
    actual = (T("actual_waited", {"actual": dur(actual_s), "run": dur(run_s),
                                  "what": T("sep").join(T(k, {"t": dur(v)}) for k, v in waits)})
              if waits else T("actual", {"actual": dur(actual_s)}))
    saved = max(0.0, float(without_s or 0) - run_s)
    saved_ln = T("saved", {"seconds": dur(saved)}) if saved >= 1 else T("saved_none")
    cause_ln = T("causes", {"what": T("sep").join(causes[:3])}) if causes else T("causes_none")
    return [T("head"), counts, without, actual, saved_ln, cause_ln], [counts, without, actual]


def explain_dir():
    root = os.environ.get("EZ2_AIB_STATE_ROOT") or str(Path.home() / ".ez2-aib")
    return Path(root) / "state" / "shadow-explain"


def _seat():
    try:
        import seat_now
        return seat_now.self_code()
    except Exception:  # noqa: BLE001
        return None


def load(seat=None, ship_dir=None):
    """這一席最近一次的說明紀錄(dict);沒有 / 讀不了 / 認不出席 ⇒ None。"""
    seat = seat or _seat()
    if not seat:
        return None
    try:
        d = json.loads(((Path(ship_dir) if ship_dir else explain_dir()) / ("%s.json" % seat)).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def record(kind, lines, required, seat=None, ship_dir=None, tree=None, next_actions=None):
    """記這一席最近一次的說明(同一席新的蓋舊的)。回寫的路徑或 None(認不出席 / 寫不了)。
    tree = aib test 印的 🧮 樹狀(含 📋 💡)那幾行(10-14 v2:守門要 AI 貼的就是它,取代純文字段);
    next_actions = 💡 那幾條(shadow_advice.build),下一輪開跑讀回來逐條判有沒有照做。"""
    seat = seat or _seat()
    if not seat:
        return None
    d = Path(ship_dir) if ship_dir else explain_dir()
    if next_actions is None:      # 沒給(推包那段、這一輪沒算建議)⇒ 留著上一輪的 💡,不要清掉(審查:不然下一輪無從判)
        next_actions = (load(seat, ship_dir) or {}).get("next_actions") or []
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("%s.json" % seat)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "kind": kind, "reported": False, "pusher": seat,
                                   "savings": list(lines), "required": list(required),
                                   "tree": list(tree or []), "next_actions": list(next_actions or []),
                                   "cwd": os.getcwd()}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, f)
        return f
    except OSError:
        return None


def show_in_pane(lines, runner=None):
    """10-13 the maintainer 20:0x「所有家都直接印」:在跑這個指令的那一席視窗用 tmux display-message 顯示一行摘要 20 秒
    (不靠各家 hook)。不在 tmux 裡 / AIB_SHADOW_EXPLAIN_TMUX=0 / 失敗 ⇒ 略過,回 False。"""
    pane = os.environ.get("TMUX_PANE")
    if not pane or not os.environ.get("TMUX") or os.environ.get("AIB_SHADOW_EXPLAIN_TMUX") == "0":
        return False
    import subprocess
    text = " | ".join(str(x) for x in lines[1:5])
    try:
        p = (runner or subprocess.run)(["tmux", "display-message", "-d", "20000", "-t", pane, text],
                                       capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        return getattr(p, "returncode", 1) == 0
    except Exception:  # noqa: BLE001
        return False


def emit(kind, dur, printer=print, seat=None, tree=None, required=None, next_actions=None, **kw):
    """印 + 記 + 在那一席視窗顯示。任何錯都不影響呼叫端的結束碼(說明印不出來就印一行原因)。"""
    try:
        lines, req = explain_lines(dur, **kw)
        for ln in lines:
            printer(ln)
        record(kind, lines, required if required else req, seat=seat, tree=tree, next_actions=next_actions)
        show_in_pane(lines)
        return lines
    except Exception as e:  # noqa: BLE001
        printer(T("failed", {"reason": "%s: %s" % (type(e).__name__, e)}))
        return []
