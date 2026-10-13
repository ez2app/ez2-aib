#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib-now-gate —— 每一席的 📌 狀態(aib now)由程式要求更新,不靠記性(the maintainer 10-12)。

the maintainer 10-12 截圖:a seat /clear 之後做了一整晚的事,輸入框下面一直是「📌【未回報】尚未回報主項目」——
那一行是每一席自己跑 `aib now` 才會變(src/cockpit/seat_now.py),沒有任何東西會提醒或擋。
the maintainer:「把一開席之後每一個階段任務完成,就必需程式化要求更新狀態 aib now 的程式架構出來」。

## 兩個掛點(Claude Code hook)

  session-start  SessionStart(開席 / clear / compact / resume):把這一席現在掛著的 📌 那一行印進新對話,
                 寫明「第一個動作先跑 aib now」。stdout = 放進對話的字。
  stop           Stop:本回合有用工具(= 做了事、到了一個段落)⇒ 這一席的 aib now 紀錄必須在**本回合開始之後**
                 更新過;沒有 ⇒ rc 2 擋下,stderr 寫要跑的那一行。純聊天(本回合零工具)不擋。

「階段完成」怎麼認:Stop 只在 AI 把話交回給人時觸發 —— 那正是一個段落結束、要回報的時刻;
中途的工具呼叫不會觸發 Stop。所以「本回合做了事 + 交回給人」= 一個階段,判準只認機器事實:
本回合工具名(transcript,與 route-gate-stop 同一條切法)+ 紀錄檔裡的 at(seat_now.write / clear 寫的)。

## 失效方向(照 route-gate-stop / aib-memory-gate 判例)

- fail-open:讀不到 transcript、認不出這一席、讀紀錄出例外 ⇒ 放行,留痕 <狀態根>/logs/now-gate.log。
  一個會把全艦 Stop 卡死的閘比沒有閘更糟。
- **紀錄檔不存在不是讀不到**:那就是「從沒回報過」(畫面上的【未回報】),要擋 —— 不然閘從第一天起恆放行。
- stop_hook_active ⇒ 放行(Claude Code 的防迴圈旗標:擋過一次,AI 補跑後再停就不會二次擋)。
- 逃生門:AIB_NOW_GATE_REASON="理由" ⇒ 放行並記檔。AIB_NOW_GATE=0 ⇒ 整支關(治具)。
- 不在 AIB 席裡(認不出席位碼)⇒ 什麼都不做:一般 Claude 對話不受影響。

## 試跑名單(the maintainer 10-12「依照你的建議」:先只在一席試跑一天,再全裝)

  <狀態根>/state/seat-now/gate-seats.txt   一行一個席位碼(# 開頭是註解);有 `*` = 全部。
  檔不存在 ⇒ 全部的席都要求(正式狀態)。檔在而這一席不在名單 ⇒ 不擋(開席提醒照印,只是不說會擋)。

## 已知取捨(10-12 審查)

- 每回合最多擋一次:被擋後下一次 Stop 帶 stop_hook_active ⇒ 放行。同一次 Stop 別的閘也擋了,續跑那段本閘也放行。
- 逃生門 AIB_NOW_GATE_REASON 要在開席時就在環境裡(AI 在 Bash 裡設的傳不到 hook 行程)⇒ 等於整席關閉,慎用。
- 認席靠 seat_now.self_code(TMUX_PANE ⇒ 窗號):從席裡起的 `claude -p` 會被認成父席。

## 它擋不住什麼

- 內容對不對:只看「本回合有沒有更新」,不看主項目寫得準不準、步數有沒有照實。
- Codex / Grok / Copilot / agy 席:它們沒有 Stop hook 可掛,本支只管 Claude 席。

## 介面
    python3 aib-now-gate.py stop            (stdin = hook JSON)
    python3 aib-now-gate.py session-start   (stdin = hook JSON)
    python3 aib-now-gate.py status          印這一席現在的判斷,永遠 rc 0
    python3 aib-now-gate.py install [--settings PATH] [--script PATH]    先備份,只加/換本支那兩條
    python3 aib-now-gate.py uninstall [--settings PATH]
    python3 aib-now-gate.py ensure [--settings PATH]   開 Claude 席前呼叫(provider-launch.sh):已裝好不動、沒裝或路徑不對才裝;
                                                       永遠 rc 0,不擋開席(10-12 the maintainer「如何確保我的要求是程式化必要流程」)
  治具:AIB_NOW_GATE_CODE=<席碼>(不認席,直接用這個碼)、EZ2_AIB_STATE_ROOT(紀錄與留痕 logs/now-gate.log 的狀態根)。
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COCKPIT = os.path.normpath(os.path.join(HERE, "..", "..", "cockpit"))
sys.path.insert(0, COCKPIT)

MARKER = "aib-now-gate.py"
HOOK_TIMEOUT_S = {"SessionStart": 10, "Stop": 10}
EVENT_MODE = {"SessionStart": "session-start", "Stop": "stop"}
DEFAULT_SETTINGS = os.path.expanduser("~/.claude/settings.json")


def _log_path():
    try:
        import seats_dir_lib
        root = seats_dir_lib.state_root()[0]
    except Exception:  # noqa: BLE001 —— 留痕找不到地方也不能擋人
        root = os.path.expanduser("~/.ez2-aib")
    return os.path.join(root, "logs", "now-gate.log")


def _note(msg):
    """留痕;寫不進去也不停 —— 這支的任何自我故障都不得擋人。"""
    try:
        p = _log_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg))
    except OSError:
        pass


def _is_turn_start(row):
    """這一列是不是「真人把話交給 AI」—— 本回合的起點。
    ★ 10-12 審查抓到:route-gate-stop.turn_facts 只排除 tool_result,而下面三種也是 type=user、不帶 tool_result,
      照那條切法會被當成新回合,同一回合前面跑過的 aib now 就不算 ⇒ 誤擋:
        isMeta            載入技能的全文、圖片附檔、/clear 的說明列、別的 Claude 對話送來的訊息
        isCompactSummary  自動壓縮後的摘要
        origin.kind = task-notification   背景工作完成的通知、Stop hook 擋下時的回饋(不是人講話)
      例外:排程 / /loop 喚醒(isMeta 但 turnOrigin = scheduled)是新的一段工作 ⇒ 算起點(第二輪審查:
      不算的話整晚巡查只要回報過一次就一路放行)。
      別席 aib dm 是貼進輸入框的,origin.kind = human ⇒ 算新回合(那是新的交辦)。
      只改本支的副本,不動 route-gate-stop(它的判準是「本回合有沒有問人」,另有它自己的取捨)。
    ★ 10-12 a seat 審查:isSidechain(子代理那條對話)的列不算起點、它的工具也不算本回合的。
      主對話檔裡 10-12 量到 0 筆(子代理另存 subagents/),是保險。
    欄位名 10-12 對過本機真檔:origin.kind=human(99 檔)、task-notification(65)、turnOrigin=scheduled(5)、
      isCompactSummary(14)、isMeta(69)。"""
    if row.get("type") != "user" or row.get("isCompactSummary") or row.get("isSidechain"):
        return False
    if row.get("isMeta") and row.get("turnOrigin") != "scheduled":
        return False
    origin = row.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


TAIL_CHUNK = 1 << 16


def _rows_backwards(path, stats=None):
    """從檔尾往回一列一列吐(已解析的 dict);解析不了的列跳過。
    ★ 10-12 a seat 審查:原本每次 Stop 把整份 transcript 讀進記憶體,長席上百 MB、Mini 只有 16G;
      這一回合的起點通常就在檔尾不遠,往回讀到它就停。stats["bytes"] 記讀了多少(測試用)。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        rest = b""
        while pos > 0:
            n = min(TAIL_CHUNK, pos)
            pos -= n
            f.seek(pos)
            buf = f.read(n) + rest
            if stats is not None:
                stats["bytes"] = stats.get("bytes", 0) + n
            lines = buf.split(b"\n")
            rest = lines[0] if pos > 0 else b""
            for raw in reversed(lines[1:] if pos > 0 else lines):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    yield row


def _turn_facts(transcript_path, stats=None):
    """回 (本回合用過的工具名 set, 本回合起始 epoch;量不到 = 0)。從檔尾往回讀,遇到本回合起點就停。"""
    tools = set()
    since = 0
    for r in _rows_backwards(transcript_path, stats):
        if _is_turn_start(r):
            ts = r.get("timestamp") or ""
            try:
                from datetime import datetime
                since = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                since = 0
            break
        if r.get("type") != "assistant" or r.get("isSidechain"):
            continue
        content = (r.get("message") or {}).get("content") or []
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools.add(b.get("name") or "")
    return tools, since


def _proc_table():
    """{pid: (ppid, args)};ps 跑不起來 ⇒ None。一次 ps 全拿,不逐層叫(上溯十幾層也只花一次)。"""
    import subprocess
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=5,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table or None


def _is_claude(args):
    """這個程式是不是 Claude Code:argv[0] 檔名是 claude,或用 node 跑 claude-code 的 cli。"""
    words = (args or "").split()
    if not words:
        return False
    if os.path.basename(words[0]) == "claude":
        return True
    return os.path.basename(words[0]) == "node" and any("claude-code" in w or os.path.basename(w) == "claude"
                                                         for w in words[1:3])


def nested_claude(table, start):
    """從 start 往上找,路上有兩個以上 Claude ⇒ 這個 hook 屬於席裡另開的子 Claude(claude -p 之類)。
    ★ 10-12 a seat 審查:子 Claude 繼承 TMUX_PANE ⇒ 被認成父席,被擋後照做跑 aib now 會把父席的狀態蓋掉。
      不用 session_id 對席位檔:/clear 之後對話編號換了、席位檔記的還是舊的(10-12 本席實測 73ce… vs ab67…),
      那樣比 ⇒ 每一席 clear 之後閘就等於關掉。程式樹的親子關係不會因 clear 改變。
    回 True / False;table 是 None(ps 失敗)⇒ None(呼叫端照原本認席,不因量不到而放掉整席)。"""
    if not table:
        return None
    seen, pid, claudes = set(), start, 0
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        row = table.get(pid)
        if not row:
            break
        if _is_claude(row[1]):
            claudes += 1
            if claudes >= 2:
                return True
        pid = row[0]
    return False


def _nested():
    """AIB_NOW_GATE_CODE(治具直接給席碼)⇒ 不量程式樹:結果不能隨「誰在跑測試」而變。"""
    if (os.environ.get("AIB_NOW_GATE_CODE") or "").strip():
        return False
    return nested_claude(_proc_table(), os.getppid())


def self_seat():
    """這一席的席位碼;認不出 ⇒ None(不是 AIB 席,或量不到 —— 兩種都不管)。"""
    forced = (os.environ.get("AIB_NOW_GATE_CODE") or "").strip()
    if forced:
        return forced
    import seat_now
    return seat_now.self_code()


def _record(code):
    """回 (紀錄 dict 或 None, 檔案在不在)。不在 = 從沒回報(要擋);在而讀不到 = 讀檔故障(放行)。"""
    import seat_now
    path = os.path.join(seat_now.now_dir(), code + ".json")
    return seat_now.read(code), os.path.exists(path)


def _shown(code):
    import seat_now
    return seat_now.shown_line(code, color=False)


GATE_SEATS = "gate-seats.txt"


def enforced(code):
    """這一席要不要擋。名單檔讀不到 ⇒ 當成沒有名單(全部擋)—— 試跑名單只用來縮小範圍,讀檔故障不該把閘關掉。"""
    import seat_now
    path = os.path.join(seat_now.now_dir(), GATE_SEATS)
    try:
        with open(path, encoding="utf-8") as fh:
            names = {ln.split("#", 1)[0].strip() for ln in fh}
    except FileNotFoundError:
        return True
    except OSError as e:
        _note("名單 %s 讀不到(%s),當成全部擋" % (path, e))
        return True
    names.discard("")
    return "*" in names or code in names


def fix_line(code):
    return ('aib now "<這一階段的主項目>" <做完幾步>/<共幾步> [--state reply|decide]'
            "  (等別席回 = reply、等真人決定 = decide;全部做完沒事 = aib now --clear)")


def decide(payload):
    """回 (should_block, message)。純函式邏輯,臂直接餵字典。"""
    if os.environ.get("AIB_NOW_GATE") == "0":
        return False, "AIB_NOW_GATE=0,關閉"
    if payload.get("stop_hook_active"):
        return False, "stop_hook_active,放行(避免阻擋迴圈)"
    reason = (os.environ.get("AIB_NOW_GATE_REASON") or "").strip()
    if reason:
        _note("BYPASS %s" % reason)
        return False, "逃生門放行:%s" % reason
    try:
        code = self_seat()
    except Exception as e:  # noqa: BLE001 —— 認席炸掉 ⇒ 放行
        _note("FAILOPEN 認席失敗:%s" % e)
        return False, "警告:認不出這一席,放行"
    if not code:
        return False, "不在 AIB 席裡,不管"
    try:
        if _nested():
            return False, "這是席裡另開的子 Claude(不是 @%s 本人),不管" % code
    except Exception as e:  # noqa: BLE001
        _note("量程式樹失敗:%s(照原本認席)" % e)
    if not enforced(code):
        return False, "@%s 不在試跑名單(%s),不擋" % (code, GATE_SEATS)
    tp = payload.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        _note("FAILOPEN @%s 讀不到 transcript" % code)
        return False, "警告:讀不到 transcript,放行(fail-open)"
    try:
        tools, since = _turn_facts(tp)
    except (OSError, ValueError) as e:
        _note("FAILOPEN @%s 解析 transcript 失敗:%s" % (code, e))
        return False, "警告:解析 transcript 失敗,放行(fail-open)"
    if not tools:
        return False, "本回合沒有用工具(純對話),不要求更新"
    if not since:
        _note("FAILOPEN @%s 量不到本回合起點" % code)
        return False, "警告:量不到本回合起點,放行(fail-open)"
    try:
        rec, exists = _record(code)
    except Exception as e:  # noqa: BLE001
        _note("FAILOPEN @%s 讀紀錄失敗:%s" % (code, e))
        return False, "警告:讀不到狀態紀錄,放行(fail-open)"
    if exists and rec is None:
        _note("FAILOPEN @%s 紀錄檔在但讀不動" % code)
        return False, "警告:狀態紀錄檔在但讀不動,放行(fail-open)"
    at = 0
    if rec:
        try:
            at = int(rec.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
    if at >= since:
        return False, "本回合已更新 aib now"
    when = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "從沒回報過"
    # 10-13 a seat:彙整誤擋要看「哪種回合被擋」⇒ 留痕帶本回合用過的工具名(只有名字,不寫內容)
    _note("BLOCK @%s at=%s since=%s tools=%s" % (code, at, since, ",".join(sorted(t for t in tools if t))))
    msg = ("aib-now-gate:@%s 這一回合做了事,但 📌 狀態沒有更新(上次更新:%s)。\n"
           "the maintainer 10-12 規定:每一個階段完成、把話交回給人之前,先更新狀態列。請跑:\n  %s\n"
           "現在畫面上是:%s") % (code, when, fix_line(code), _shown(code))
    return True, msg


def session_start_text(payload):
    """開席 / clear / compact / resume:印進新對話的提醒。不在 AIB 席、關閉、出錯 ⇒ 空字串。"""
    if os.environ.get("AIB_NOW_GATE") == "0":
        return ""
    try:
        code = self_seat()
        if not code or _nested():
            return ""
        shown = _shown(code)
    except Exception as e:  # noqa: BLE001
        _note("FAILOPEN session-start:%s" % e)
        return ""
    src = payload.get("source") or "startup"
    try:
        hard = enforced(code)
    except Exception:  # noqa: BLE001
        hard = True
    tail = ("沒更新的話,回合結束時 aib-now-gate 會擋下(the maintainer 10-12)。" if hard
            else "(本席不在試跑名單,目前不會擋,但規則一樣。)")
    return ("[AIB 狀態列] @%s(%s)輸入框下方現在是:%s\n"
            "第一個動作先跑 `%s`,寫這一席現在的主項目;之後每做完一個階段、把話交回給人之前都要再更新一次 ——%s\n"
            ) % (code, src, shown, fix_line(code), tail)


def _read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


# ── 安裝(照 claudebot_hook.install:先備份、只動自己那幾條、壞 JSON 拒絕覆寫)──────────────
def hook_command(mode, script=None, python="python3"):
    """test -f 先驗腳本在不在:工作樹刪掉或 repo 搬家時放行,不讓每一條 Claude 對話每一句都被擋(python 找不到檔也回 2)。"""
    sc = shlex.quote(script or os.path.abspath(__file__))
    return "test -f %s || exit 0; exec %s %s %s" % (sc, python, sc, mode)


def _is_ours(h):
    return isinstance(h, dict) and MARKER in str(h.get("command") or "")


def strip_ours(cfg):
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        return cfg
    for ev in list(hooks):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not _is_ours(h)]
                if not inner:
                    continue
                g = dict(g, hooks=inner)
            kept.append(g)
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if not hooks:
        cfg.pop("hooks", None)
    return cfg


def add_ours(cfg, script=None):
    if not isinstance(cfg.get("hooks", {}), dict):
        raise ValueError("settings.json 的 hooks 不是物件")
    cfg = strip_ours(cfg)
    hooks = cfg.setdefault("hooks", {})
    for ev, mode in EVENT_MODE.items():
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": hook_command(mode, script),
                                                    "timeout": HOOK_TIMEOUT_S[ev]}]})
    return cfg


def is_installed(cfg, script):
    """settings 裡本支那兩條是不是恰好等於這次會裝的樣子(路徑、模式、時限都對)。"""
    want = add_ours({}, script)["hooks"]
    have = (cfg or {}).get("hooks")
    if not isinstance(have, dict):
        return False
    for ev, groups in want.items():
        mine = [h for g in have.get(ev) or [] if isinstance(g, dict) for h in g.get("hooks") or [] if _is_ours(h)]
        if mine != groups[0]["hooks"]:
            return False
    return True


def _stamp_of(path):
    """(mtime_ns, size);檔不在 ⇒ None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def ensure(path=DEFAULT_SETTINGS):
    """開席前的確認:回 (做了什麼, 說明)。任何問題都不丟例外 —— 開席不能因為它失敗。"""
    if os.environ.get("AIB_NOW_GATE") == "0":
        return "off", "AIB_NOW_GATE=0"
    script = canonical_script()
    if not script or not os.path.isfile(script):
        return "skip", "正本樹沒有 %s" % MARKER
    try:
        before = _stamp_of(path)
        cfg = _load(path)
    except (OSError, ValueError) as e:
        _note("ensure 讀不懂 %s:%s(不動)" % (path, e))
        return "skip", "讀不懂 %s" % path
    try:
        if is_installed(cfg, script):
            return "ok", "已裝"
        cfg = add_ours(cfg, script)
        if _stamp_of(path) != before:
            # 讀完到要寫之間別人(Claude Code、另一席同時開)改了它 ⇒ 不蓋,下次開席再裝(審查第 3 條)
            _note("ensure %s 剛被別人改過,這次不寫" % path)
            return "skip", "%s 剛被改過" % path
        backup = _save(path, cfg)
    except (OSError, ValueError) as e:
        _note("ensure 寫不進 %s:%s" % (path, e))
        return "skip", "寫不進 %s" % path
    _note("ENSURE 裝上 %s(備份 %s)" % (path, backup))
    return "installed", "已裝上 aib now 狀態檢查(%s%s)" % (path, ";原檔備份 %s" % backup if backup else "")


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)              # 壞 JSON ⇒ 例外,呼叫端拒絕覆寫
    if not isinstance(data, dict):
        raise ValueError("settings.json 頂層不是物件")
    return data


REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))


def canonical_script(repo_root=None):
    """正本樹裡的本支(git worktree list 第一列 = 主工作樹);量不到 ⇒ None。
    審查:從工作樹跑 install 會裝成工作樹路徑,工作樹一刪 test -f 讓閘永遠安靜放行,沒人知道。
    只有「確定不在 git 裡」(這棵樹根本沒有 .git:zip 下載的公開版)才回本支自己的路徑 ——
    那時沒有工作樹這回事,本支就是正本。git 跑失敗、輸出空白而樹裡有 .git(可能是連結工作樹)⇒ None,不猜
    (第二輪審查:不然 git 一出錯,工作樹路徑就會蓋掉設定裡原本正確的正本路徑)。"""
    import subprocess
    root = repo_root or REPO_ROOT
    in_git = os.path.exists(os.path.join(root, ".git"))
    try:
        r = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"], capture_output=True,
                           text=True, timeout=10, stdin=subprocess.DEVNULL)
        out = r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            return os.path.join(line[len("worktree "):], "src", "policy", "hooks", MARKER)
    if not in_git:
        return os.path.join(root, "src", "policy", "hooks", MARKER)
    return None


def _save(path, cfg):
    backup = None
    if os.path.exists(path):
        base = "%s.bak-now-gate-%s-%d" % (path, time.strftime("%Y%m%d-%H%M%S"), os.getpid())
        backup, n = base, 1
        while os.path.exists(backup):      # 同一秒同一行程裝兩次:不蓋掉第一份(那份才是原檔)
            backup, n = "%s.%d" % (base, n), n + 1
        shutil.copy2(path, backup)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp-now-gate-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return backup


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "stop"
    if mode == "stop":
        try:
            block, msg = decide(_read_payload())
        except Exception as e:  # noqa: BLE001 —— 最後一道 fail-open
            _note("FAILOPEN 未預期:%s" % e)
            return 0
        if block:
            print(msg, file=sys.stderr)
            return 2
        return 0
    if mode == "session-start":
        try:
            text = session_start_text(_read_payload())
        except Exception as e:  # noqa: BLE001
            _note("FAILOPEN session-start 未預期:%s" % e)
            return 0
        if text:
            sys.stdout.write(text)
        return 0
    if mode == "status":
        code = None
        try:
            code = self_seat()
        except Exception as e:  # noqa: BLE001
            print("認不出這一席:%s" % e)
        if not code:
            print("不在 AIB 席裡:aib-now-gate 不管這個對話")
            return 0
        print("@%s 現在:%s" % (code, _shown(code)))
        return 0
    if mode == "ensure":
        try:
            did, why = ensure(_opt(argv, "--settings", DEFAULT_SETTINGS))
        except Exception as e:  # noqa: BLE001 —— 開席前呼叫,絕不能擋
            _note("ensure 未預期:%s" % e)
            return 0
        if did == "installed":
            print("aib: %s" % why, file=sys.stderr)
        return 0
    if mode in ("install", "uninstall"):
        path = _opt(argv, "--settings", DEFAULT_SETTINGS)
        try:
            cfg = _load(path)
        except (OSError, ValueError) as e:
            print("讀不懂 %s(%s):不覆寫" % (path, e), file=sys.stderr)
            return 1
        script = _opt(argv, "--script")
        if mode == "install" and not script:
            script = canonical_script()
            if not script or not os.path.isfile(script):
                print("找不到正本樹裡的 %s(正本還沒有這支?先合併再裝,或用 --script 指定)" % MARKER, file=sys.stderr)
                return 1
        try:
            cfg = add_ours(cfg, script) if mode == "install" else strip_ours(cfg)
        except ValueError as e:
            print("讀不懂 %s(%s):不覆寫" % (path, e), file=sys.stderr)
            return 1
        backup = _save(path, cfg)
        print("%s 完成:%s%s" % ("安裝" if mode == "install" else "移除", path,
                                ("(原檔備份 %s)" % backup) if backup else ""))
        return 0
    print("用法:aib-now-gate.py stop|session-start|status|install|uninstall|ensure [--settings PATH] [--script PATH]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
