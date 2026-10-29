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
    python3 src/cockpit/grokbot_threads.py bound <agentId>             # 這一個 agent 綁好了沒(rc 0 好、1 沒)
  connect 成功後會問要不要同步到其他機器(grokbot_bind_sync.py;aib grokbot sync-bind)
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


ACCOUNT_SLOT_LOGICAL = "sand.client.slice.client-meta.account-slot"
_LOGIN_NAMES = {"github": "GitHub", "google": "Google", "apple": "Apple", "x": "X", "email": "email"}


def app_account(app_dir=None) -> str:
    """Grok Bot.app 本機登入的帳號,一行人話(例「GitHub 登入,帳號編號尾碼 EZRX(讀不到 email)」);讀不到回 ""。
    10-11 a seat(待修清單第 22 件):app 本機只記 client-meta.account-slot = "<登入方式>|user_<編號>",沒有 email ⇒
    只講登入方式與編號尾 4 碼,讓人對得上是哪個帳號;不碰任何憑證檔(sand-secrets.json 不讀)。"""
    pers = persistence_dir(app_dir)
    try:
        names = os.listdir(pers)
    except OSError:
        return ""
    for fn in names:
        if _decode(fn) != ACCOUNT_SLOT_LOGICAL:
            continue
        try:
            value = json.loads((pers / fn).read_text(encoding="utf-8")).get("value")
        except (OSError, ValueError, AttributeError):
            return ""
        if not isinstance(value, str) or "|" not in value or len(value) > 200:
            return ""
        how, _, uid = value.partition("|")
        tail = re.sub(r"[^A-Za-z0-9]", "", uid)[-4:]
        if not tail:
            return ""
        how = how.strip().lower()
        label = _LOGIN_NAMES.get(how) or (how if re.fullmatch(r"[a-z0-9_-]{1,20}", how) else "不明方式")
        return "%s 登入,帳號編號尾碼 %s(讀不到 email)" % (label, tail)
    return ""


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


ENV_PBCOPY = "AIB_GROKBOT_PBCOPY"   # 尺用:換成假的 pbcopy


def _t(key: str, fallback: str, **params) -> str:
    """四語字典(i18n/<locale>.json,語系照 i18n_lib.resolve_locale)。字典壞掉或缺鍵 ⇒ 用呼叫端給的 zh-TW 原句,不讓 connect 當。"""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import i18n_lib
        text = i18n_lib.t(key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001 —— i18n_lib 故意大聲失敗;這裡只要字
        text = ""
    if not text or text.startswith("!!"):
        try:
            text = fallback.format(**params)
        except (KeyError, IndexError, ValueError):
            text = fallback
    return text


ROUTINE_NAME = "AIB 收信"   # 例行任務名稱(bot 側的識別字,不翻譯)
BOT_ASK_FALLBACK = (
    "請幫我接上 AIB 席位的門鈴(Grok Bot agent id:{agent})。\n"
    "1. 如果你還沒有觸發方式是 Webhook、名稱「AIB 收信」的例行任務,就建一個。指示:每次被觸發時,用 local-exec 執行 "
    "~/.local/bin/aib-grokbot-mail pull --agent {agent} 收信,照輸出裡的說明處理每一則,"
    "回覆一律用輸出裡給的 post / dm 指令寫回(通行碼也在輸出裡)。\n"
    "2. 建好(或本來就有)之後,直接回我「Webhook URL」和「Webhook 金鑰」兩個可以點開的欄位連結,我點開複製後貼回 AIB 的綁定視窗。\n"
    "3. 不要在聊天裡貼出金鑰本身,只給連結。")


def routine_request(agent_id: str) -> str:
    """「複製給 bot 的話」那段(10-28 Andy 准:綁定視窗的按鈕複製這段,不再跳窗前偷放剪貼簿)。
    請被綁的 bot ①沒有 Webhook 觸發的「AIB 收信」例行任務就建一個 ②回「Webhook URL」「Webhook 金鑰」兩個可點的欄位連結
    ③不在聊天裡貼金鑰本身;帶 agent id 讓它知道是哪一席。
    收信指示照 docs/GROKBOT-DESKTOP.md §9.4 真機打通那一版:門鈴只是叫醒,內容要它自己用 pull 拿、用 post 回。"""
    return _t("aib.grokconnect.bot_ask", BOT_ASK_FALLBACK, agent=agent_id)


def copy_to_clipboard(text: str) -> bool:
    """放進 macOS 剪貼簿(pbcopy);失敗回 False,不擋 connect。"""
    import subprocess
    try:
        r = subprocess.run([os.environ.get(ENV_PBCOPY) or "pbcopy"], input=text, text=True,
                           capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


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
function build(argv) {
  var title = argv[0], info = argv[1], cfg = {};
  try { cfg = JSON.parse(argv[2] || '{}') || {}; } catch (e) { cfg = {}; }
  function s(k, d) { return (typeof cfg[k] === 'string' && cfg[k]) ? cfg[k] : d; }
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
  alert.addButtonWithTitle(s('ok', '確定'));
  alert.addButtonWithTitle(s('cancel', '取消'));
  var copyText = s('copy_text', '');
  var top = copyText ? 40 : 0;
  var v = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 440, 118 + top));
  var out = {alert: alert, btn: null, target: null};
  if (copyText) {
    // 「複製給 bot 的話」:NSAlert 自己的按鈕按了會關窗 ⇒ 放在 accessoryView 裡的 NSButton,target/action 自己接,視窗不關
    ObjC.registerSubclass({
      name: 'AIBGrokCopyAskTarget',
      superclass: 'NSObject',
      methods: {
        'copyAsk:': {
          types: ['void', ['id']],
          implementation: function (sender) {
            var pb = cfg.pasteboard ? $.NSPasteboard.pasteboardWithName(cfg.pasteboard) : $.NSPasteboard.generalPasteboard;
            pb.clearContents;
            var ok = pb.setStringForType($(copyText), $.NSPasteboardTypeString);
            sender.title = ok ? s('copied', '已複製 ✓') : s('copy_failed', '複製失敗');
          }
        }
      }
    });
    out.target = $.AIBGrokCopyAskTarget.alloc.init;
    out.btn = $.NSButton.buttonWithTitleTargetAction(s('copy', '複製給 bot 的話'), out.target, 'copyAsk:');
    out.btn.frame = $.NSMakeRect(0, 118 + 6, 260, 28);
    v.addSubview(out.btn);
  }
  function label(text, y) {
    var l = $.NSTextField.labelWithString(text);
    l.frame = $.NSMakeRect(0, y, 440, 18);
    v.addSubview(l);
  }
  label(s('url_label', 'POST 到(Webhook URL)'), 98);
  var url = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 70, 440, 24));
  url.placeholderString = 'https://api2.cursor.sh/automations/webhook/…';
  v.addSubview(url);
  label(s('key_label', '金鑰(輸入會隱藏;貼「標頭」那格也可以)'), 42);
  var key = $.NSSecureTextField.alloc.initWithFrame($.NSMakeRect(0, 14, 440, 24));
  v.addSubview(key);
  url.nextKeyView = key;
  alert.accessoryView = v;
  alert.window.initialFirstResponder = url;
  out.url = url;
  out.key = key;
  return out;
}
function run(argv) {
  var b = build(argv);
  $.NSApplication.sharedApplication.activateIgnoringOtherApps(true);
  var r = b.alert.runModal;
  if (r != $.NSAlertFirstButtonReturn) { throw new Error('User canceled. (-128)'); }
  return b.url.stringValue.js + '\n' + b.key.stringValue.js;
}
"""


def dialog_texts(label: str) -> dict:
    """綁定視窗上人看得到的字(四語)。copy_text 另外塞。"""
    return {
        "title": _t("aib.grokconnect.title", "AIB 綁定 Grok Bot「{label}」", label=label),
        "info": _t("aib.grokconnect.info",
                   "app 已切到這個 agent 的「例行任務」頁。\n\n"
                   "步驟:按「複製給 bot 的話」→ 貼到跟它的聊天送出 → 點它回的「Webhook URL」和「Webhook 金鑰」"
                   "兩個連結,各自複製 → 貼回下面這兩格,按「確定」。\n\n"
                   "已經有 Webhook 例行任務的話,也可以直接從例行任務頁把「POST 到」和「金鑰」兩格複製過來。"),
        "ok": _t("aib.grokconnect.ok", "確定"),
        "cancel": _t("aib.grokconnect.cancel", "取消"),
        "url_label": _t("aib.grokconnect.url_label", "POST 到(Webhook URL)"),
        "key_label": _t("aib.grokconnect.key_label", "金鑰(輸入會隱藏;貼「標頭」那格也可以)"),
        "copy": _t("aib.grokconnect.copy", "複製給 bot 的話"),
        "copied": _t("aib.grokconnect.copied", "已複製 ✓"),
        "copy_failed": _t("aib.grokconnect.copy_failed", "複製失敗:請自己在聊天裡請 bot 回兩個連結"),
    }


def ask_two_fields(title: str, info: str, texts=None, copy_text: str = "") -> str:
    """一個視窗兩格(the maintainer 09-25:「為什麼不要二格?這比較易懂」):網址一格、金鑰一格(隱藏)。
    copy_text 非空 ⇒ 視窗上多一顆「複製給 bot 的話」按鈕(10-28 Andy 准):按了把這段放進剪貼簿、視窗不關、字改「已複製 ✓」。
    texts:按鈕與欄位的字(dialog_texts);不給 ⇒ JXA 用原本的中文字。

    回「網址\n金鑰」,交給 parse_pasted 拆,與 --stdin 同一套判準。取消 / 逾時 / 空白各自丟 DialogAborted。"""
    import subprocess
    exe = os.environ.get(ENV_OSASCRIPT) or "osascript"
    cfg = {k: v for k, v in (texts or {}).items() if k not in ("title", "info") and isinstance(v, str)}
    if copy_text:
        cfg["copy_text"] = copy_text
    try:
        r = subprocess.run([exe, "-l", "JavaScript", "-e", TWO_FIELD_JXA, title, info,
                            json.dumps(cfg, ensure_ascii=False)],
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


def agent_bound(agent_id: str, base=None) -> bool:
    """這一個 agent 真的綁好了沒:有它自己的設定檔,而且網址 / 金鑰形狀對(同 connect 的判準)。
    10-28:開席前要看「這次要開的那一個」,不是「任何一個綁過」(connected_agents 只看檔案在不在)。"""
    if not AGENT_ID_RE.match(agent_id or ""):
        return False
    url, key = read_agent_wake(agent_id, base)
    return not validate_wake(url, key)


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
    bp = sub.add_parser("bound")   # rc 0 = 這個 agent 綁好了、1 = 沒綁或設定壞了、2 = id 形狀不對
    bp.add_argument("agent_id")
    ap_acct = sub.add_parser("account")
    ap_acct.add_argument("--dir", default="")
    args = ap.parse_args(argv)
    if args.cmd == "account":
        print(app_account(args.dir or None) or "讀不到")
        return RC_OK
    if args.cmd == "bound":
        if not AGENT_ID_RE.match(args.agent_id or ""):
            print("agentId 形狀不對", file=sys.stderr)
            return RC_USAGE
        try:
            ok = agent_bound(args.agent_id)
        except Exception as exc:  # noqa: BLE001 —— 量不到 ≠ 沒綁:rc 3,開席那邊不跳視窗(Python 崩潰的 rc 1 會被當成沒綁)
            print("讀不到綁定(%s)" % type(exc).__name__, file=sys.stderr)
            return RC_UNREADABLE
        print(json.dumps({"agentId": args.agent_id, "bound": ok}, ensure_ascii=False))
        return RC_OK if ok else 1
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
            # 10-28 Andy 准:不再跳窗前偷放剪貼簿;視窗上「複製給 bot 的話」按鈕按了才放(請 bot 建例行任務 + 回兩個連結)
            open_routines(args.agent)
            texts = dialog_texts(label)
            try:
                pasted = ask_two_fields(texts["title"], texts["info"], texts, copy_text=routine_request(args.agent))
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
        # 10-28 Andy:補救 —— bot 例行任務要跑的收信入口不在 / 過時 ⇒ 建好(只印有改動或失敗的;失敗不改結束碼)
        try:
            import bot_launchers
            for ln in bot_launchers.lines(bot_launchers.ensure(only=["aib-grokbot-mail"], repair_only=True), quiet=True):
                print("aib: " + ln, file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print("aib: bot launchers: %s" % type(exc).__name__, file=sys.stderr)
        # 10-28 Andy 准(必要功能):綁好後一定問「要不要同步到其他機器」,按「同步」才傳;--stdin / 沒有交談終端 ⇒ 不傳,只印指令。
        #   同步自己出錯不改 connect 的結束碼(這台已經綁好了)。
        try:
            import grokbot_bind_sync
            grokbot_bind_sync.offer_after_connect(args.agent, interactive=(not args.stdin) and sys.stdin.isatty())
        except Exception as exc:  # noqa: BLE001
            print("aib: grokbot sync-bind: %s" % type(exc).__name__, file=sys.stderr)
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
