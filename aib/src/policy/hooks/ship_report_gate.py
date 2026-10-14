#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""推包「省多少」回報守門(the maintainer 10-13 19:3x,經 a seat 派工):推成之後,推的那一席回報裡一定要原樣出現
「沒有 aib 一支一支跑:約 X」與「這次實際:Y」兩行,不靠 AI 記性。

## 怎麼運作

- tools/release/ship.py 推成時(announce_savings)在 ~/.ez2-aib/ship/last-push-<倉標籤>.json 加:
  reported=false、pusher=<推的那一席席碼>、savings=[G2 四行,字面跟 REPORT-DRAFT 的 G2.1~G2.4 逐字相同]。
- 本支由 aib-now-gate.py 的 stop 先呼叫(那支已由 provider-launch.sh ensure 裝在每一席,不另加 settings 條目):
  這一席有 reported=false 且 pusher=本席、24 小時內的推包 ⇒ 本回合的回覆要逐字含 savings 前兩行(空白不計);
  有 ⇒ 改 reported=true 放行;沒有、或數字被手改 ⇒ 擋一次,印出要貼的四行。
- 只擋推的那一席;別席、不在 AIB 席、檔太舊 ⇒ 不管。

## 失效方向(同 aib-now-gate)

- fail-open:讀不到檔、認不出席、解析失敗 ⇒ 放行並留痕 <狀態根>/logs/ship-report-gate.log。
- stop_hook_active(本回合已被擋過一次)⇒ 放行,記 missed_at(不讓 AI 卡在迴圈;the maintainer 那邊另有推包工具自己送的通知與紀錄)。
- AIB_SHIP_REPORT_GATE=0 ⇒ 整支關(治具)。注意:aib-now-gate 的 AIB_NOW_GATE=0 / AIB_NOW_GATE_REASON 只關 now 那段,不關本支。
- 擋過一次還漏(missed_at)⇒ 之後的回合不再擋,交給巡查(shadow_explain_patrol)點名、第二次通知 the maintainer。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_AGE_S = 86400
_NOW_GATE = None


def ship_dir():
    return os.environ.get("AIB_SHIP_DIR") or os.path.join(os.path.expanduser("~"), ".ez2-aib", "ship")


def _log(msg):
    try:
        root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
        p = os.path.join(root, "logs", "ship-report-gate.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg))
    except OSError:
        pass


def _now_gate():
    """aib-now-gate.py(檔名有連字號,import 不到)⇒ 照路徑載入,借它的 transcript 讀法(從檔尾往回、認回合起點)。"""
    global _NOW_GATE
    if _NOW_GATE is None:
        spec = importlib.util.spec_from_file_location("aib_now_gate", os.path.join(HERE, "aib-now-gate.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _NOW_GATE = mod
    return _NOW_GATE


def explain_dir():
    """aib test / 推前檢查的影子續跑說明(tools/verify/shadow_explain.py 寫的):<狀態根>/state/shadow-explain/<席碼>.json。"""
    root = os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    return os.path.join(root, "state", "shadow-explain")


def _candidates():
    return (sorted(glob.glob(os.path.join(ship_dir(), "last-push-*.json")))
            + sorted(glob.glob(os.path.join(explain_dir(), "*.json"))))


def required_lines(d):
    """回報裡必須原樣出現的行:說明檔有 required 用它;推包檔用 savings 前兩行。"""
    req = d.get("required")
    if isinstance(req, list) and req:
        return [str(x) for x in req]
    return [str(x) for x in (d.get("savings") or [])[:2]]


def _open_records(now=None):
    """所有還沒回報、24 小時內的紀錄(不分席):[(檔路徑, 內容)]。"""
    now = time.time() if now is None else now
    out = []
    for f in _candidates():
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("reported") is not False:
            continue
        try:
            if now - float(d.get("at") or 0) > MAX_AGE_S:
                continue
        except (TypeError, ValueError):
            continue
        out.append((f, d))
    return out


def _update(path, fields, only_if_open=True):
    """重讀再合併寫回(審查:巡查 / post-tool 拿著舊的一份等 tmux / dm 幾十秒再整份寫回,會把 Stop 剛改的 reported=true 蓋回 false)。
    only_if_open:檔上現在已經 reported 不是 false ⇒ 不寫,回 False。"""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(d, dict) or (only_if_open and d.get("reported") is not False):
        return False
    d.update(fields)
    _write(path, d)
    return True


def pending(code, now=None, for_stop=False):
    """這一席還沒回報的推包與影子續跑說明:[(檔路徑, 內容)]。
    for_stop:Stop 守門用 ⇒ 已經擋過一次還漏(missed_at)的不再擋(審查:不然之後 24 小時每一回合都被擋一次,連純聊天、/clear 後都擋);
    那種交給巡查點名與通知 the maintainer。"""
    now = time.time() if now is None else now
    out = []
    for f in _candidates():
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("reported") is not False or d.get("pusher") != code:
            continue
        if for_stop and d.get("missed_at"):
            continue
        sv = d.get("savings")
        if not isinstance(sv, list) or len(sv) < 2:
            continue
        try:
            if now - float(d.get("at") or 0) > MAX_AGE_S:
                continue
        except (TypeError, ValueError):
            continue
        out.append((f, d))
    return out


def shown_lines(d):
    """要給人看、要 AI 貼的那段。aib test 的說明檔有 tree(🧮 樹狀 + 📋 上一輪建議 + 💡 下次怎麼更省,10-14 v2)
    ⇒ 就是它,取代純文字段(a seat 示意:「gate 要求貼的內容 = 這個樹狀」);推包檔沒有 tree ⇒ 照舊 savings。"""
    tree = [str(x) for x in (d.get("tree") or [])]
    return tree or [str(x) for x in (d.get("savings") or [])]


def _norm(text):
    return "".join(str(text or "").split())


def reply_text(payload):
    """本回合 AI 交回的文字:hook 有給 last_assistant_message 就用它;否則從 transcript 檔尾往回收到本回合起點。"""
    msg = payload.get("last_assistant_message")
    if isinstance(msg, str) and msg.strip():
        return msg
    tp = payload.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return None
    ng = _now_gate()
    parts = []
    for r in ng._rows_backwards(tp):
        if ng._is_turn_start(r):
            break
        if r.get("type") != "assistant" or r.get("isSidechain"):
            continue
        content = (r.get("message") or {}).get("content") or []
        if isinstance(content, list):
            parts.extend(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")
    return "\n".join(reversed(parts))


def _write(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def check(payload, code, now=None):
    """回 (should_block, message)。code = 這一席席碼,或回傳席碼的函式(先看有沒有未回報的推包,有才認席 ——
    每一席每次 Stop 都會走到這裡,沒推包時不多花一次 ps)。"""
    if os.environ.get("AIB_SHIP_REPORT_GATE") == "0":
        return False, ""
    now = time.time() if now is None else now
    if not [1 for _p, d in _open_records(now) if not d.get("missed_at")]:
        return False, ""                      # 沒有任何待回報 ⇒ 不認席(不跑 ps;審查:舊推包檔永遠在,不能拿「有檔」當條件)
    if callable(code):
        code = code()
    if not code:
        return False, ""
    todo = pending(code, now, for_stop=True)
    if not todo:
        return False, ""
    text = reply_text(payload)
    if text is None:
        _log("FAILOPEN @%s 讀不到回覆" % code)
        return False, ""
    body = _norm(text)
    missing = []
    for path, d in todo:
        need = required_lines(d)
        if need and all(_norm(ln) in body for ln in need):
            _update(path, {"reported": True, "reported_at": now, "reported_session": payload.get("session_id")})
            _log("OK @%s %s" % (code, os.path.basename(path)))
        else:
            missing.append((path, d))
    if not missing:
        return False, ""
    if payload.get("stop_hook_active"):
        for path, d in missing:
            _update(path, {"missed_at": now})
        _log("MISSED @%s %s" % (code, ",".join(os.path.basename(p) for p, _ in missing)))
        return False, ""
    lines = []
    for _p, d in missing:
        if d.get("kind") == "push":
            lines.append("推包 %s(%s 顆):" % (str(d.get("head") or "")[:9], d.get("commits")))
        else:
            lines.append("%s 的影子續跑說明:" % (d.get("kind") or "aib test"))
        lines.extend("  " + ln for ln in shown_lines(d))
    _log("BLOCK @%s %s" % (code, ",".join(os.path.basename(p) for p, _ in missing)))
    return True, ("ship-report-gate:@%s 這一回合跑了推包 / aib test,但回覆沒有原樣附上影子續跑說明的數字"
                  "(the maintainer 10-13:如果開影子續跑,一定要列出說明)。請把下面那段原樣貼進回覆(有 🧮 樹狀與 💡 的整段照貼;走七段總回報時放在 S0 省時間),數字一字不改:\n%s"
                  % (code, "\n".join(lines)))


RUN_CMD_RE = re.compile(r"(\baib\s+(test|ship)\b|src/cockpit/aib\s+test\b|test_entry\.py|pushgate\.py|tools/release/ship\.py)")
SHOW_WINDOW_S = 900


def post_tool(payload, code, now=None):
    """Claude PostToolUse(Bash):剛跑的是 aib test / 推包 / 推前檢查 ⇒ 回要直接顯示給人看的那段(systemMessage),
    沒有就回 ""。只顯示這一席 15 分鐘內、還沒顯示過的紀錄(顯示過記 shown_at,不重複洗版)。
    背景跑的(run_in_background)這時還沒寫紀錄 ⇒ 不顯示;那種靠工具收尾的 tmux 顯示與 Stop 守門。"""
    if os.environ.get("AIB_SHIP_REPORT_GATE") == "0":
        return ""
    if payload.get("tool_name") not in (None, "Bash"):
        return ""
    ti = payload.get("tool_input") or {}
    cmd = str(ti.get("command") or "")
    if not RUN_CMD_RE.search(cmd) or ti.get("run_in_background"):
        return ""                              # 背景跑:這時紀錄還沒寫,顯示的會是上一次的數字(審查)
    now = time.time() if now is None else now
    if not _open_records(now):
        return ""
    if callable(code):
        code = code()
    if not code:
        return ""
    parts = []
    for path, d in pending(code, now):
        try:
            if now - float(d.get("at") or 0) > SHOW_WINDOW_S or d.get("shown_at"):
                continue
        except (TypeError, ValueError):
            continue
        if _update(path, {"shown_at": now}):
            parts.append("\n".join(shown_lines(d)))
    return "\n".join(p for p in parts if p)


def mark_reported(code, now=None, how="appended"):
    """程式代補(API 席 REPL、Bot 橋接把那段直接附在回覆後面)之後呼叫:這一席待回報的全部改 true。回改了幾份。"""
    now = time.time() if now is None else now
    n = 0
    for path, _d in pending(code, now):
        n += int(_update(path, {"reported": True, "reported_at": now, "reported_how": how}))
    return n


def segment(code, now=None):
    """這一席待回報的那幾段(要附在回覆後面的字);沒有 ⇒ 空字串。"""
    out = []
    for _p, d in pending(code, now):
        out.extend(shown_lines(d))
    return "\n".join(out)


def main(argv=None):
    """給沒有 Claude Stop hook 的各家掛點與巡查共用(a seat × a seat 10-13):
      check --seat <席> [--reply-file <檔>|stdin]   rc 2 = 漏了(stdout 印提醒 + 要貼的那段);rc 0 = 沒事或已補
      segment --seat <席> [--mark]                    印要附在回覆後面的那段;--mark 代補後改 reported=true
    任何自己的故障 ⇒ rc 0(fail-open)。"""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="ship_report_gate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--seat", required=True)
    c.add_argument("--reply-file", default=None)
    g = sub.add_parser("segment")
    g.add_argument("--seat", required=True)
    g.add_argument("--mark", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            if a.reply_file:
                with open(a.reply_file, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            else:
                text = sys.stdin.read()
            block, msg = check({"last_assistant_message": text or " "}, a.seat)
            if block:
                print(msg)
                return 2
            return 0
        seg = segment(a.seat)
        if seg:
            print(seg)
            if a.mark:
                mark_reported(a.seat)
        return 0
    except Exception as e:  # noqa: BLE001
        _log("FAILOPEN cli %s" % e)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
