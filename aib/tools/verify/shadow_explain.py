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

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    return i18n_lib.t("aib.explain." + key, params, locale=LOC)


def explain_lines(dur, *, reused, skipped, ran, without_s, actual_s, causes, fresh=False):
    """回 (全部要印的行, 回報裡必須原樣出現的行)。causes = 已翻好的原因字串清單(最多前三)。"""
    if fresh:
        ln = T("fresh", {"n": int(ran)})
        return [T("head"), ln], [ln]
    counts = T("counts", {"reused": int(reused), "skipped": int(skipped), "ran": int(ran)})
    without = T("without", {"total": dur(without_s)}) if without_s and without_s > 0 else T("without_none")
    actual = T("actual", {"actual": dur(actual_s)})
    saved = max(0.0, float(without_s or 0) - float(actual_s or 0))
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


def record(kind, lines, required, seat=None, ship_dir=None):
    """記這一席最近一次的說明(同一席新的蓋舊的)。回寫的路徑或 None(認不出席 / 寫不了)。"""
    seat = seat or _seat()
    if not seat:
        return None
    d = Path(ship_dir) if ship_dir else explain_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("%s.json" % seat)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "kind": kind, "reported": False, "pusher": seat,
                                   "savings": list(lines), "required": list(required),
                                   "tree": os.getcwd()}, ensure_ascii=False) + "\n",
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


def emit(kind, dur, printer=print, seat=None, **kw):
    """印 + 記 + 在那一席視窗顯示。任何錯都不影響呼叫端的結束碼(說明印不出來就印一行原因)。"""
    try:
        lines, required = explain_lines(dur, **kw)
        for ln in lines:
            printer(ln)
        record(kind, lines, required, seat=seat)
        show_in_pane(lines)
        return lines
    except Exception as e:  # noqa: BLE001
        printer(T("failed", {"reason": "%s: %s" % (type(e).__name__, e)}))
        return []
