#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""G4:Grok Bot.app 的對話清單,給 bot_threads.BACKENDS 與右鍵開席選單用。

來源是 app 本機的名冊切片 `…roster.last-roster`(不是對話副本:副本裡會留著名冊已經沒有的舊 agent)。
只讀名冊的 id / 名字 / 時間 / path / 旗標;**不讀 lastEntry**(那是對話內容,N43)。
名冊 schemaVersion 釘 4(與對話副本的 1 不同);變了就丟 RosterSchemaChanged,不猜(N52)。
本檔不寫 Grok Bot 目錄。

對外介面(bot_threads 接的是這三個):
  list_agents(limit=20, app_dir=None) -> [{id, name, updated, cwd, is_group}]  新的在前;updated 是 epoch 秒
  held_by(agent_id, base=None) -> list[str]   空 = 沒被占;判準是 grokbot-bindings/<agentId>.lock 的 flock
  lock_path(agent_id, base=None) -> Path       橋要占用時對它取 LOCK_EX|LOCK_NB(G5 用)
  (G5)validate_wake(url, key) -> list[str]     只驗設定形狀,不發請求;空 = 正常
  (G5)agent_wake_path(agent_id, base=None)     這個 agent 專用的 webhook 設定檔
  (G5)connect(agent_id, url, key, …)           驗形狀 + 確認 agent 在名冊 ⇒ 寫 0600 設定檔;不試打

    python3 src/cockpit/grokbot_threads.py list [--limit N] [--json] [--dir D]
    python3 src/cockpit/grokbot_threads.py held <agentId>
    python3 src/cockpit/grokbot_threads.py connect --agent <agentId>           # 跳一個視窗兩格:網址、金鑰(隱藏)(預設)
    python3 src/cockpit/grokbot_threads.py connect --agent <agentId> --stdin   # 同一套拆法,從 stdin 讀(尺用)
    python3 src/cockpit/grokbot_threads.py connected                   # 哪些 agent 已綁 webhook(只列 id)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

ENV_DIR = "AIB_GROKBOT_DIR"
DEFAULT_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Grok Bot"
PERSISTENCE_NAME = "sand-client-persistence"
ROSTER_RE = re.compile(r"\.roster\.last-roster$")
EXPECTED_ROSTER_SCHEMA = 4
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
BINDINGS_NAME = "grokbot-bindings"
HELD_NAME_UNKNOWN = "(占用者名字查不到)"
RC_OK, RC_USAGE, RC_UNREADABLE, RC_SCHEMA = 0, 2, 3, 5


class RosterSchemaChanged(RuntimeError):
    """名冊格式變了。訊息是給人看的一句話。"""


class RosterUnreadable(RuntimeError):
    """名冊找不到或讀不懂。"""


def persistence_dir(app_dir=None) -> Path:
    raw = (str(app_dir) if app_dir else "") or (os.environ.get(ENV_DIR) or "").strip()
    p = Path(raw).expanduser() if raw else DEFAULT_APP_SUPPORT
    return p if p.name == PERSISTENCE_NAME else p / PERSISTENCE_NAME


def _decode(filename: str):
    if not filename.endswith(".blob"):
        return None
    stem = filename[:-5]
    try:
        return base64.b32decode(stem.upper() + "=" * ((8 - len(stem) % 8) % 8)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _epoch_s(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n // 1000 if n > 10 ** 12 else n  # 名冊用毫秒;約定是秒


def _roster_rows(pers: Path) -> list[dict]:
    blobs = [p for p in sorted(pers.glob("*.blob")) if ROSTER_RE.search(_decode(p.name) or "")]
    if not pers.is_dir() or not blobs:
        raise RosterUnreadable("找不到 Grok Bot 名冊")
    rows = []
    for p in blobs:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RosterUnreadable("名冊讀不懂(%s)" % type(exc).__name__) from exc
        ver = data.get("schemaVersion") if isinstance(data, dict) else None
        if ver != EXPECTED_ROSTER_SCHEMA:
            raise RosterSchemaChanged("Grok Bot 的名冊格式變了(schemaVersion=%s),本工具不猜" % (
                "缺" if ver is None else ver))
        value = data.get("value")
        got = value.get("rows") if isinstance(value, dict) else None
        if not isinstance(got, list):
            raise RosterSchemaChanged("Grok Bot 的名冊格式變了(沒有 value.rows),本工具不猜")
        rows.extend(r for r in got if isinstance(r, dict))
    return rows


def roster_written_at(app_dir=None) -> str:
    """本機名冊最後寫入時間(給人看)。名冊是快取:app 啟動與點選時才寫,不是即時。"""
    import time
    pers = persistence_dir(app_dir)
    ts = [p.stat().st_mtime for p in pers.glob("*.blob") if ROSTER_RE.search(_decode(p.name) or "")] \
        if pers.is_dir() else []
    return time.strftime("%m-%d %H:%M", time.localtime(max(ts))) if ts else ""


def list_agents(limit: int = 20, app_dir=None) -> list[dict]:
    seen, out = set(), []
    for r in _roster_rows(persistence_dir(app_dir)):
        aid = r.get("id")
        if not isinstance(aid, str) or not AGENT_ID_RE.match(aid) or aid in seen:
            continue
        if r.get("isHiddenFromSidebar") is True:
            continue  # app 側欄自己也不顯示的,選單不列
        seen.add(aid)
        name = r.get("name") if isinstance(r.get("name"), str) else ""
        title = r.get("title") if isinstance(r.get("title"), str) else ""
        path = r.get("path") if isinstance(r.get("path"), str) else ""
        out.append({
            "id": aid,
            "name": (name or title).strip(),
            "updated": max(_epoch_s(r.get("lastActivityAt")), _epoch_s(r.get("updatedAt"))),
            "cwd": path,
            "is_group": r.get("isGroup") is True,
        })
    out.sort(key=lambda x: (-x["updated"], x["id"]))
    return out[: max(0, int(limit))]


def _state_root(base=None) -> Path:
    if base:
        return Path(base)
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def lock_path(agent_id: str, base=None) -> Path:
    if not AGENT_ID_RE.match(agent_id or ""):
        raise ValueError("agentId 形狀不對")
    return _state_root(base) / BINDINGS_NAME / (agent_id + ".lock")


def held_by(agent_id: str, base=None) -> list[str]:
    """這個 agent 現在是不是被一隻活橋占著。空 list = 沒被占。

    與 codexbot_threads.held_by 同一種判法:對橋會拿的那把鎖試一次 LOCK_EX|LOCK_NB。
    鎖檔不存在 ⇒ 沒被占,且**不建檔**(探針不留下它在量的東西)。"""
    import fcntl
    lock = lock_path(agent_id, base)
    if not lock.exists():
        return []
    with lock.open("r") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return held_by_names(agent_id, base) or [HELD_NAME_UNKNOWN]
        fcntl.flock(fh, fcntl.LOCK_UN)
    return []


def held_by_names(agent_id: str, base=None) -> list[str]:
    """占用者叫什麼:讀各席的 bridge/<session>/status.json(agent_id 相同、pid 活著)。

    只拿來報名字;占不占用的判準仍是上面那把鎖(兩個來源會不同步,鎖為準)。"""
    out = []
    for st in sorted((_state_root(base) / "bridge").glob("*/status.json")):
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            if d.get("agent_id") != agent_id or d.get("state") == "stopped":
                continue
            os.kill(int(d.get("pid")), 0)
        except (OSError, ValueError, TypeError):
            continue
        out.append("@%s" % (d.get("code") or "?"))
    return out


WAKE_HOST = "api2.cursor.sh"
WAKE_PATH_PREFIX = "/automations/webhook/"
AGENT_WAKE_DIR = "grokbot-agents"


def validate_wake(url: str, key: str) -> list[str]:
    """webhook 設定只驗形狀,**一個請求都不發**。sand_bridge --check-wake 與 connect 共用這一份。"""
    import urllib.parse
    problems = []
    if not url:
        problems.append("webhook 網址沒設")
    if not key:
        problems.append("webhook 金鑰沒設")
    if url:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https":
            problems.append("網址不是 https(是 %s)" % (u.scheme or "空"))
        if (u.hostname or "") != WAKE_HOST:
            problems.append("網域不是 %s" % WAKE_HOST)
        tail = u.path[len(WAKE_PATH_PREFIX):] if u.path.startswith(WAKE_PATH_PREFIX) else None
        if tail is None or not re.fullmatch(r"[0-9a-fA-F-]{36}", tail):
            problems.append("路徑不是 automations/webhook/<uuid>")
        if u.query or u.fragment or u.username or u.password:
            problems.append("網址多了查詢字串、錨點或帳密")
    if key and (len(key) > 512 or any(ord(c) < 33 for c in key)):
        problems.append("金鑰含空白或控制字元,或太長")
    return problems


def agent_wake_path(agent_id: str, base=None) -> Path:
    if not AGENT_ID_RE.match(agent_id or ""):
        raise ValueError("agentId 形狀不對")
    return _state_root(base) / "secrets" / AGENT_WAKE_DIR / (agent_id + ".env")


def read_agent_wake(agent_id: str, base=None):
    """回 (url, key);沒綁回 ("", "")。不印、不回傳到任何輸出面。"""
    p = agent_wake_path(agent_id, base)
    url = key = ""
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "GROKBOT_WAKE_URL":
                url = v.strip()
            elif k.strip() == "GROKBOT_WAKE_KEY":
                key = v.strip()
    return url, key


def connect(agent_id: str, url: str, key: str, base=None, app_dir=None) -> str:
    """驗形狀 + 確認 agent 在名冊 ⇒ 原子寫 0600 設定檔。回給人看的一句話;不合格丟 ValueError。"""
    url, key = (url or "").strip(), (key or "").strip()
    problems = validate_wake(url, key)
    if problems:
        raise ValueError(";".join(problems))
    names = {r["id"]: r["name"] for r in list_agents(10 ** 6, app_dir)}
    if agent_id not in names:
        raise ValueError("名冊裡沒有這個 agent(%s)。本機名冊是 app 的快取,最後寫入 %s;"
                         "剛建的 agent 要在 app 裡點選一下,app 才會把名冊寫進來,之後再跑一次"
                         % (agent_id, roster_written_at(app_dir) or "不明"))
    dest = agent_wake_path(agent_id, base)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    tmp = dest.with_name(dest.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("GROKBOT_WAKE_URL=%s\nGROKBOT_WAKE_KEY=%s\n" % (url, key))
    os.replace(tmp, dest)
    return ("已綁定:%s → 這個 agent 專用的 webhook(只驗設定,沒有試打)\n寫在:%s"
            % (names[agent_id] or agent_id, dest))


PASTE_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+/automations/webhook/[0-9a-fA-F-]{36}")


def parse_pasted(text: str):
    """一次貼上的網址 + 金鑰拆成 (url, key)(the maintainer 09-25:一個框貼完)。

    網址最後一段固定 36 字元 ⇒ 網址與金鑰中間沒有分隔(對話框把換行吃掉)也切得開。
    金鑰可帶 app「Authorization header」那格的 `Authorization: Bearer ` 前綴。拆不出來丟 ValueError。"""
    raw = (text or "").strip()
    m = PASTE_URL_RE.search(raw)
    if not m:
        raise ValueError("貼的內容裡找不到 Webhook URL(https://…/automations/webhook/<uuid>)")
    rest = (raw[:m.start()] + " " + raw[m.end():]).strip()
    rest = re.sub(r"(?i)^authorization\s*:\s*", "", rest).strip()
    rest = re.sub(r"(?i)^bearer\s+", "", rest).strip()
    if not rest:
        raise ValueError("只貼到網址,沒有金鑰")
    if len(rest.split()) != 1:
        raise ValueError("網址之外多了不只一段文字,認不出哪一段是金鑰")
    return m.group(0), rest


DIALOG_TIMEOUT_S = 600
ENV_OSASCRIPT = "AIB_GROKBOT_OSASCRIPT"   # 尺用:換成假的 osascript


class DialogAborted(RuntimeError):
    """對話框沒拿到值:取消、逾時或空白。訊息是給人看的一句話。"""


ENV_OPEN = "AIB_GROKBOT_OPEN"   # 尺用:換成假的 open


def open_routines(agent_id: str) -> bool:
    """用 app 自己的深層連結切到這個 agent 的「例行任務」頁(路徑表見設計頁 §8.1 R3)。失敗不擋 connect。"""
    import subprocess
    if not AGENT_ID_RE.match(agent_id or ""):
        return False
    url = "grokbot://app/v1/sidebar?agent=%s&tab=routines" % agent_id
    try:
        r = subprocess.run([os.environ.get(ENV_OPEN) or "open", url], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def ask_dialog(prompt: str, hidden: bool = False) -> str:
    """macOS 原生輸入框(the maintainer 09-25:connect 的必要流程)。

    貼的值直接回傳給呼叫端,不經過殼的指令列或歷史。取消 / 逾時 / 空白各自丟 DialogAborted,不當成空值往下走
    (第一版把三種都吞成空字串,connect 只能說「沒設」,分不出是哪一種)。"""
    import subprocess
    esc = prompt.replace("\\", "\\\\").replace('"', '\\"')
    script = ('with timeout of %d seconds\n'
              'text returned of (display dialog "%s" default answer ""%s with title "AIB connect" '
              'buttons {"取消", "確定"} default button "確定" cancel button "取消" giving up after %d)\n'
              'end timeout') % (DIALOG_TIMEOUT_S + 30, esc, " with hidden answer" if hidden else "",
                                DIALOG_TIMEOUT_S)
    exe = os.environ.get(ENV_OSASCRIPT) or "osascript"
    try:
        r = subprocess.run([exe, "-e", script], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=DIALOG_TIMEOUT_S + 60)
    except FileNotFoundError as exc:
        raise DialogAborted("這台機器跳不出對話框(找不到 osascript);改用 stdin 餵兩行") from exc
    except subprocess.TimeoutExpired as exc:
        raise DialogAborted("對話框逾時,沒有收到輸入") from exc
    err = r.stderr or ""
    if r.returncode != 0:
        if "-128" in err:
            raise DialogAborted("你按了取消")
        if "-1712" in err:
            raise DialogAborted("對話框逾時,沒有收到輸入")
        raise DialogAborted("對話框出錯(rc=%s)" % r.returncode)
    val = (r.stdout or "").strip()
    if not val:
        raise DialogAborted("對話框回來是空白(可能是逾時自動關掉,或沒貼上就按了確定)")
    return val


TWO_FIELD_JXA = r"""
ObjC.import('Cocoa');
function run(argv) {
  var title = argv[0], info = argv[1];
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
  // 用腳本叫出的原生視窗沒有「編輯」選單 ⇒ ⌘V 貼不上。自己補一個。
  var menubar = $.NSMenu.alloc.init, editItem = $.NSMenuItem.alloc.init;
  menubar.addItem(editItem);
  var edit = $.NSMenu.alloc.initWithTitle('Edit');
  edit.addItemWithTitleActionKeyEquivalent('Cut', 'cut:', 'x');
  edit.addItemWithTitleActionKeyEquivalent('Copy', 'copy:', 'c');
  edit.addItemWithTitleActionKeyEquivalent('Paste', 'paste:', 'v');
  edit.addItemWithTitleActionKeyEquivalent('Select All', 'selectAll:', 'a');
  editItem.submenu = edit;
  app.mainMenu = menubar;

  var alert = $.NSAlert.alloc.init;
  alert.messageText = title;
  alert.informativeText = info;
  alert.addButtonWithTitle('確定');
  alert.addButtonWithTitle('取消');
  var v = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 440, 118));
  function label(text, y) {
    var l = $.NSTextField.labelWithString(text);
    l.frame = $.NSMakeRect(0, y, 440, 18);
    v.addSubview(l);
  }
  label('POST 到(Webhook URL)', 98);
  var url = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 70, 440, 24));
  url.placeholderString = 'https://api2.cursor.sh/automations/webhook/…';
  v.addSubview(url);
  label('金鑰(輸入會隱藏;貼「標頭」那格也可以)', 42);
  var key = $.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(0, 14, 440, 24));
  v.addSubview(key);
  url.nextKeyView = key;
  alert.accessoryView = v;
  alert.window.initialFirstResponder = url;
  app.activateIgnoringOtherApps(true);
  var r = alert.runModal;
  if (r != $.NSAlertFirstButtonReturn) { throw new Error('User canceled. (-128)'); }
  return url.stringValue.js + '\n' + key.stringValue.js;
}
"""


def ask_two_fields(title: str, info: str) -> str:
    """一個視窗兩格(the maintainer 09-25:「為什麼不要二格?這比較易懂」):網址一格、金鑰一格(隱藏)。

    回「網址\n金鑰」,交給 parse_pasted 拆,與 --stdin 同一套判準。取消 / 逾時 / 空白各自丟 DialogAborted。"""
    import subprocess
    exe = os.environ.get(ENV_OSASCRIPT) or "osascript"
    try:
        r = subprocess.run([exe, "-l", "JavaScript", "-e", TWO_FIELD_JXA, title, info],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=DIALOG_TIMEOUT_S + 60)
    except FileNotFoundError as exc:
        raise DialogAborted("這台機器跳不出對話框(找不到 osascript);改用 --stdin") from exc
    except subprocess.TimeoutExpired as exc:
        raise DialogAborted("對話框逾時,沒有收到輸入") from exc
    if r.returncode != 0:
        if "-128" in (r.stderr or ""):
            raise DialogAborted("你按了取消")
        raise DialogAborted("對話框出錯(rc=%s)" % r.returncode)
    val = (r.stdout or "").strip()
    if not val:
        raise DialogAborted("兩格都是空白")
    return val


def connected_agents(base=None) -> list[str]:
    d = _state_root(base) / "secrets" / AGENT_WAKE_DIR
    return sorted(p.stem for p in d.glob("*.env") if AGENT_ID_RE.match(p.stem)) if d.is_dir() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="grokbot_threads.py")
    sub = ap.add_subparsers(dest="cmd")
    lp = sub.add_parser("list")
    lp.add_argument("--limit", type=int, default=20)
    lp.add_argument("--json", action="store_true")
    lp.add_argument("--dir", default="")
    hp = sub.add_parser("held")
    hp.add_argument("agent_id")
    cp = sub.add_parser("connect")
    cp.add_argument("--agent", required=True)
    cp.add_argument("--dir", default="")
    cp.add_argument("--stdin", action="store_true",
                    help="從 stdin 讀(與對話框同一套拆法);沒給就跳 macOS 對話框(預設、the maintainer 定的必要流程)")
    sub.add_parser("connected")
    args = ap.parse_args(argv)
    if args.cmd == "connected":
        print(json.dumps({"connected": connected_agents()}, ensure_ascii=False))
        return RC_OK
    if args.cmd == "connect":
        # 金鑰不走 argv(會出現在 ps 與殼歷史)。預設:跳 macOS 對話框讓人貼(the maintainer 09-25 定為必要流程);
        # --stdin:從 stdin 讀兩行,第一行網址、第二行金鑰(尺與自動化用)。
        if args.stdin:
            pasted = sys.stdin.read()
        else:
            try:
                names = {r["id"]: r["name"] for r in list_agents(10 ** 6, args.dir or None)}
            except (RosterSchemaChanged, RosterUnreadable):
                names = {}
            label = names.get(args.agent) or args.agent[:8]
            open_routines(args.agent)
            try:
                pasted = ask_two_fields(
                    "AIB 綁定 Grok Bot「%s」" % label,
                    "app 已切到這個 agent 的「例行任務」頁。點進觸發方式是 Webhook 的那個例行任務,"
                    "把「POST 到」和「金鑰」兩格分別複製到下面。\n\n"
                    "還沒有例行任務:先在跟它的聊天裡請它建一個 Webhook 觸發的例行任務"
                    "(app 規定例行任務要在聊天裡請 bot 建)。")
            except DialogAborted as exc:
                print("沒綁定:" + str(exc), file=sys.stderr)
                return RC_USAGE
        try:
            url, key = parse_pasted(pasted)
        except ValueError as exc:
            print("沒綁定:" + str(exc), file=sys.stderr)
            return RC_USAGE
        try:
            print(connect(args.agent, url, key, app_dir=args.dir or None))
        except (RosterSchemaChanged, RosterUnreadable) as exc:
            print(str(exc), file=sys.stderr)
            return RC_SCHEMA if isinstance(exc, RosterSchemaChanged) else RC_UNREADABLE
        except ValueError as exc:
            print("沒綁定:" + str(exc), file=sys.stderr)
            return RC_USAGE
        return RC_OK
    if args.cmd == "held":
        try:
            names = held_by(args.agent_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return RC_USAGE
        print(json.dumps({"agentId": args.agent_id, "held_by": names}, ensure_ascii=False))
        return RC_OK
    if args.cmd != "list":
        ap.print_help(sys.stderr)
        return RC_USAGE
    try:
        rows = list_agents(args.limit, args.dir or None)
    except RosterSchemaChanged as exc:
        print(str(exc), file=sys.stderr)
        return RC_SCHEMA
    except RosterUnreadable as exc:
        print(str(exc), file=sys.stderr)
        return RC_UNREADABLE
    if args.json:
        print(json.dumps({"provider": "grokbot", "rows": rows}, ensure_ascii=False))
    else:
        for r in rows:
            print("%s  %s%s" % (r["id"], r["name"] or "(沒名字)", "(群組)" if r["is_group"] else ""))
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
