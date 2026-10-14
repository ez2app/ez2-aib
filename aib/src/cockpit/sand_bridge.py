#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""sand-bridge — Cursor 橋接席：grok-CLI 風格輸入 + 信箱來回。

輸入對齊 grok TUI：`❯ ` 提示符。
人送出 → inbox；助手（Cursor 或本地 CLI）寫 outbox → 印在窗裡。
本地 CLI（可選）：EZ2_BRIDGE_LOCAL_REPLY=1 時試 grok -p / codex exec。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path
from human_attest import human_display_name
import aib_dm_tool as _aib_dm
import dm_header as _dm_header          # 艙位碼單一來源(local_cabin;10-06 the maintainer 裁 ③)

SCHEMA = "aib-sand-bridge/v1"
MSG_SCHEMA = "aib-sand-bridge-msg/v1"


def _dock_enabled() -> bool:
    return os.environ.get("EZ2_AIB_DOCK_INPUT", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


class DockedUI:
    """可捲歷史在上、❯ 永遠釘在終端最後一行。

    非 TTY 或 EZ2_AIB_DOCK_INPUT=0 時退回一般 print（不搶游標）。
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.enabled = _dock_enabled()
        try:
            self.enabled = self.enabled and sys.stdout.isatty()
        except (AttributeError, OSError):
            self.enabled = False
        self._lock = threading.Lock()
        self.status = ""   # 10-07:狀態列(輸入框上面那一行;claudebot_live 寫)
        # 10-09 the maintainer:對話卡(每則訊息一張有框、依品牌上色的卡)。非 TTY 或 EZ2_AIB_BRIDGE_CARDS=0 ⇒ 舊形狀 `標籤> 文字`
        import bridge_cards as _bc
        self.cards = _bc.cards_enabled(self.enabled)
        self.color = _bc.color_enabled()

    def set_status(self, text: str) -> None:
        """只改輸入框上面那一行,不整頁重畫(打字中不閃、readline 的游標不動)。非 TTY 時狀態有字才印一行。"""
        with self._lock:
            self.status = str(text or "")
            if not self.enabled:
                if self.status:
                    print("· " + self.status, flush=True)
                return
            row = max(1, self._term_rows() - 1)
            sys.stdout.write("\0337\033[%d;1H\033[2K%s\0338" % (row, self.status))
            sys.stdout.flush()

    def append(self, text: str) -> None:
        for raw in str(text or "").splitlines() or [""]:
            self.lines.append(raw.rstrip("\n"))
        # 防無限長大（pane 截圖／記憶）
        if len(self.lines) > 800:
            self.lines = self.lines[-600:]

    def _term_rows(self) -> int:
        try:
            return max(4, int(shutil.get_terminal_size(fallback=(80, 24)).lines))
        except Exception:
            return 24

    def redraw(self, draft: str = "") -> None:
        """清屏重畫：上方歷史（尾端可視），底行固定 ❯ + draft。"""
        if not self.enabled:
            return
        rows = self._term_rows()
        body_h = max(1, rows - 1 - (1 if self.status else 0))
        view = self.lines[-body_h:] if self.lines else []
        # 不足則上方留白，讓提示符貼底
        pad = body_h - len(view)
        out = ["\033[H\033[2J\033[?25h"]
        for _ in range(max(0, pad)):
            out.append("\n")
        for line in view:
            out.append(line + "\n")
        if self.status:
            out.append(self.status + "\n")
        out.append("❯ " + draft)
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def message(self, kind: str, label: str, text: str, *, seat: str = "", when: str = "", redraw: bool = True) -> None:
        """一則「訊息」(真人 / app / bot 回覆 / dm):開卡就畫卡,否則照舊 `標籤> 文字`。通知類(忙碌標記、收據)不走這裡。"""
        import bridge_cards as _bc
        if getattr(self, "cards", False):
            self.say(_bc.card(kind, label, text, cols=_bc.term_cols(), when=when, color=getattr(self, "color", True), seat=seat), redraw=redraw)
        else:
            self.say(_bc.legacy(label, text), redraw=redraw)

    def _style_notice(self, text: str) -> str:
        """通知類的一行(忙碌標記 `xxx> …`、`AIB 系統> …`、`xxx: …` 橋的提示、`✓ …`、`⚠ …`)在 TTY 上畫成跟卡片同一套的
        一行小籤:品牌圖示 + 顏色。只有畫面變,信箱/紀錄/非 TTY 的輸出都是原字(claudebot_live 與 composer 的測試認字面)。"""
        if not getattr(self, "cards", False) or not text or text.startswith("\x1b"):
            return text
        try:
            import bridge_cards as _bc
        except Exception:  # noqa: BLE001
            return text
        color = getattr(self, "color", False)
        def sgr(col):
            return ("\x1b[38;5;%dm" % col) if (color and col is not None) else ""
        R = "\x1b[0m" if color else ""
        DIM = "\x1b[2m" if color else ""
        t = text.lstrip()
        if t.startswith("✓ "):
            return "%s  %s%s" % (sgr(78), text.strip(), R)
        if t.startswith("⚠ "):
            return "%s  %s%s" % (sgr(220), text.strip(), R)
        if t.startswith("AIB 系統> "):
            _, icon, col = _bc.BRANDS.get("system", ("", "⚙", 245))
            return "%s  %s AIB 系統 · %s%s" % (sgr(col), icon, t[len("AIB 系統> "):], R)
        for pid in ("claudebot", "grokbot", "musebot", "codexbot"):
            name, icon, col = _bc.BRANDS.get(pid, (pid, "", None))
            if t == pid + "> …":
                return "%s  %s %s 正在想…%s" % (sgr(col), icon, name, R)
            if t.startswith(pid + ": "):
                return "%s%s  %s %s · %s%s" % (DIM, sgr(col), icon, pid, t[len(pid) + 2:], R)
        return text

    def say(self, text: str = "", *, redraw: bool = True) -> None:
        text = self._style_notice(text)
        with self._lock:
            if text is not None and text != "":
                self.append(text)
            if self.enabled:
                if redraw:
                    self.redraw()
            else:
                if text:
                    print(text, flush=True)


def state_root() -> Path:
    for key in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        v = os.environ.get(key)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".ez2-aib"


def bridge_dir(session_uuid: str) -> Path:
    return state_root() / "bridge" / session_uuid


def ensure_dirs(root: Path) -> None:
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "outbox").mkdir(parents=True, exist_ok=True)


HEARTBEAT_WRITE_S = 2.0   # status.json 內容沒變時多久寫一次(心跳);bot_ping 認 10 秒內為新鮮
READY_RECHECK_S = 30.0   # P2(10-11):還沒就緒時多久再查一次(只讀設定檔)

# ── 10-09 the maintainer「每推一包就要手動重開 bot 席」:橋自己盯程式檔(與右欄 idle_reaper 同一套指紋),碼變了、席閒夠久就 exec 自己。
#    同一條 argv、同一個 pid(flock 在 exec 時釋放、新程式再拿);EZ2_AIB_BRIDGE_REEXEC=0 關掉。留痕同 pane-reexec.log。
REEXEC_CHECK_S = 30.0
REEXEC_IDLE_S = 600.0          # 席(tmux 窗)至少閒這麼久才重跑:正在打字 / 剛收信不動
_reexec = {"fp": "", "next": 0.0}


def _own_pane() -> str:
    # tmux 自己設的,不是艙單源要洗的名字;另包一層,免得跟 AIB_TMUX_BIN 同函式被算進艙解析讀取集
    return os.environ.get("TMUX_PANE") or ""


def pane_idle_seconds() -> float | None:
    """本席 tmux 窗多久沒動(秒);量不到回 None(⇒ 不重跑,失效側選舊碼繼續跑)。"""
    pane = _own_pane()
    tmux = os.environ.get("AIB_TMUX_BIN", "tmux")
    if not pane:
        return None
    try:
        r = subprocess.run([tmux, "display-message", "-p", "-t", pane, "#{window_activity}"],
                           capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        act = r.stdout.strip()
        return time.time() - int(act) if r.returncode == 0 and act.isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def should_reexec(now: float, fp: str, idle_s, enabled: bool = True) -> bool:
    """純判斷(測試用):指紋第一次看到只記住;之後變了、而且席閒 ≥ REEXEC_IDLE_S 才 True。"""
    if not enabled or not fp:
        return False
    if not _reexec["fp"]:
        _reexec["fp"] = fp
        return False
    if fp == _reexec["fp"]:
        return False
    return idle_s is not None and idle_s >= REEXEC_IDLE_S


def maybe_reexec(now: float | None = None) -> bool:
    """heartbeat 每 REEXEC_CHECK_S 秒問一次;真的重跑時本函式不回來。"""
    now = time.monotonic() if now is None else now
    if now < _reexec["next"]:
        return False
    _reexec["next"] = now + REEXEC_CHECK_S
    enabled = (os.environ.get("EZ2_AIB_BRIDGE_REEXEC") or "1").strip().lower() not in ("0", "false", "no", "off")
    try:
        import idle_reaper as _ir
        fp = _ir.reexec_fingerprint()
    except Exception:  # noqa: BLE001
        return False
    if not should_reexec(now, fp, pane_idle_seconds(), enabled):
        return False
    me = os.path.abspath(__file__)
    if not os.path.isfile(me) or not os.access(sys.executable, os.X_OK):
        # review r3:python 本身不在了(brew 升級刪掉舊 Cellar)⇒ exec 必失敗;不先跟收尾小幫手說 done(說了它就走了,之後席死沒人收尾)
        return False
    try:
        _ir.reexec_log(me, _reexec["fp"], fp)
    except Exception:  # noqa: BLE001
        pass
    _before = _reexec.get("before")
    if callable(_before):
        # review r2(10-11 a seat):收尾小幫手的管子在 exec 時會被關掉 ⇒ 它以為席死了,去把 app 名稱 / Claude 標題改回未綁定、
        #   還搶走那條的鎖(新程式起來搶不到就死)。exec 前先跟它說「我自己收好了」,新程式起來會再生一隻。
        try:
            _before()
        except Exception:  # noqa: BLE001
            pass
    print("sand-bridge: 程式已更新、席閒著,重跑自己載新碼(綁定不變)", file=sys.stderr, flush=True)
    sys.stdout.flush()
    try:
        os.execv(sys.executable, [sys.executable, me] + sys.argv[1:])
    except OSError as exc:
        # review r3:exec 還是失敗 ⇒ 認了這份指紋,不要每 REEXEC_CHECK_S 秒再試一次、再印一次;席照舊碼跑下去
        _reexec["fp"] = fp
        print(f"sand-bridge: 重跑自己失敗({type(exc).__name__}),照舊碼繼續跑;關席時 app 名稱可能不會改回未綁定",
              file=sys.stderr, flush=True)
        return False
    return False


def write_status(root: Path, payload: dict) -> None:
    path = root / "status.json"
    payload = dict(payload)
    payload["schema"] = SCHEMA
    payload["updated_epoch"] = int(time.time())
    # 10-08 推前檢查實踩(test-claudebot-bind-later 時紅時綠,JSONDecodeError: Extra data):心跳執行緒與主執行緒
    #   同時寫同一個固定名 status.json.tmp ⇒ 兩份內容交錯後被 replace 成正式檔,右欄/ping 讀到壞 JSON。
    #   每次寫用獨一無二的暫存檔(同目錄,replace 仍是原子的)。
    fd, tmp_name = tempfile.mkstemp(prefix="status.json.", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def short_code(session_uuid: str) -> str:
    """顯示用短碼:只走 seat_address.uuid_tail5(B15 單源),不得自切 uuid。"""
    from seat_address import uuid_tail5
    return uuid_tail5(session_uuid) or (session_uuid or "")


def _seat_tail(session_uuid: str = "") -> str:
    """卡片標題列用的席碼(本席 session uuid 末五碼;橋自己的 --session-id,不讀環境:claudebot 席的 AIB_RESUME_SESSION_UUID 是空的)。
    量不到回空字串,卡片就不帶。"""
    try:
        from seat_address import uuid_tail5
        return uuid_tail5(session_uuid) if session_uuid else ""
    except Exception:  # noqa: BLE001
        return ""


def outbox_card_args(body, provider_id: str, human: str):
    """outbox 一則 ⇒ 卡片的 (kind, label)。heartbeat 即時畫與開席時重放「之前的」都用這一份判法。"""
    if isinstance(body, dict) and body.get("role") == "user" and body.get("source") == "system":
        return "system", "AIB 系統"
    if isinstance(body, dict) and body.get("role") == "user":
        return "human", "%s(%s)" % (human, "app" if body.get("source") == "app" else "bot 轉述")
    if isinstance(body, dict) and str(body.get("to") or "") not in ("", "human", "cursor"):
        return provider_id, "%s → %s" % (title_for(provider_id), body.get("to"))
    return provider_id, title_for(provider_id)


REPLAY_N = 5   # 開席時把 outbox 最近幾則重放成卡(the maintainer 10-09「舊的不會套用嗎」):只重放,不動檔、不算新信
REPLAY_FOLDERS = ("inbox", "claimed", "delivered", "outbox")   # 10-09 the maintainer「musebot 沒完成」:它的信在 claimed(收到的)與 delivered(寄出的),outbox 永遠空


def _bridge_distill(root, session_uuid, provider_id, body=None):
    """10-13 a seat(the maintainer「Bot 也要能自動更新」「量不到 % 每 20 輪」):GrokBot / MuseBot 每回一則算一輪,
    到點放一則 aib-system 信進本席 inbox(跟改名、人格包同一條路),bot 下次收信時替這一席整理記憶。
    ClaudeBot 在 claudebot_hook 的 Stop 做(那邊量得到用量 %),這裡不算。任何錯 ⇒ 略過。"""
    if provider_id not in ("grokbot", "musebot"):
        return None
    if isinstance(body, dict) and (str(body.get("role") or "") == "user"
                                   or str(body.get("to") or "human") not in ("human", "cursor", "")):
        return None                                    # 只算 bot 回真人的(轉貼的真人話、寄給別席的 dm 不算)
    try:
        ident = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "identity")
        if ident not in sys.path:
            sys.path.append(ident)
        import auto_distill_hook as adh
        cabin = (os.environ.get("AIB_BRIDGE_CABIN") or "").strip() or _dm_header.local_cabin()   # 開席器洗掉 EZ2_AIB_INSTANCE
        text = adh.bot_turn(short_code(session_uuid), cabin)
        if text:
            return enqueue(root, "inbox", text, session_uuid, provider_id, "aib-system")
    except Exception:  # noqa: BLE001
        return None
    return None


def _bridge_ship(session_uuid, provider_id, body=None):
    """10-13 a seat 第 2 層影子續跑說明:GrokBot / MuseBot 回真人的那一則畫完 ⇒ 這一席跑過 aib test / 推包還沒回報
    就回那段,由橋補畫一張系統卡(程式代補,不靠模型抄)並記成已回報。條件跟 _bridge_distill 同。錯 ⇒ ""。"""
    if provider_id not in ("grokbot", "musebot"):
        return ""
    if isinstance(body, dict) and (str(body.get("role") or "") == "user"
                                   or str(body.get("to") or "human") not in ("human", "cursor", "")):
        return ""
    try:
        ident = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "identity")
        if ident not in sys.path:
            sys.path.append(ident)
        import auto_distill_hook as adh
        return adh.ship_append(short_code(session_uuid))
    except Exception:  # noqa: BLE001
        return ""


def inbox_card_args(body, provider_id: str, human: str):
    """inbox / claimed 一則(寄給本席 bot 的)⇒ 卡片 (kind, label):真人打的 ⇒ 真人卡;aib-system ⇒ 系統卡;別席 dm ⇒ dm 卡(標寄件人)。"""
    frm = str((body or {}).get("from") or "") if isinstance(body, dict) else ""
    if frm == "human":
        return "human", human
    if frm in ("aib-system", "system"):
        return "system", "AIB 系統"
    return "dm", (frm or "dm")


def replay_recent(ui, root: Path, provider_id: str, session_uuid: str) -> int:
    """開席重放(10-09 第二版):四個資料夾合起來依檔名毫秒排序,取最近 REPLAY_N 則;只讀檔,不動任何狀態。
    inbox/claimed = 寄給 bot 的(真人 / 系統 / 別席 dm);outbox/delivered = bot 說的(回真人 / 寄給別席)。"""
    if not getattr(ui, "cards", False):
        return 0
    cands = []
    for folder in REPLAY_FOLDERS:
        try:
            for pth in (root / folder).glob("*.json"):
                try:
                    ms = int(pth.name.split("-", 1)[0])
                except ValueError:
                    continue
                cands.append((ms, folder, pth))
        except OSError:
            continue
    picked = []
    for ms, folder, pth in sorted(cands, key=lambda x: (x[0], x[2].name), reverse=True):
        if len(picked) >= REPLAY_N:
            break
        try:
            b = json.loads(pth.read_text(encoding="utf-8"))
            t = OUTBOX_CTRL_RE.sub("", str(b.get("text") or b.get("body") or ""))
        except Exception:
            continue
        if not t.strip():
            continue
        when = time.strftime("%H:%M", time.localtime(ms / 1000)) if ms > 0 else ""
        picked.append((b, t, when, folder))
    human = human_display_name()
    if picked:
        ui.say("— 重開前的最近 %d 則(重放,不是新信)—" % len(picked), redraw=False)
    else:
        ui.say("— 重開前沒有舊訊息可重放(inbox/claimed/delivered/outbox 都空)—", redraw=False)
    for b, t, when, folder in reversed(picked):
        if folder in ("inbox", "claimed"):
            k, l = inbox_card_args(b, provider_id, human)
        else:
            k, l = outbox_card_args(b, provider_id, human)
        import bridge_cards as _bc
        ui.message(k, l, _bc._clip(t), seat=_seat_tail(session_uuid) if k in (provider_id, "system") else "", when=when, redraw=False)
    return len(picked)


def replay_recent_outbox(ui, root: Path, provider_id: str, session_uuid: str, names) -> int:
    """開席時把 outbox 最近 REPLAY_N 則重放成卡;只在開卡時;回重放了幾則。只讀檔,不動任何狀態。"""
    if not getattr(ui, "cards", False):
        return 0
    picked = []                                   # 從最新往回挑,壞檔跳過不占名額,湊滿 REPLAY_N 則
    for name in sorted(names, reverse=True):
        if len(picked) >= REPLAY_N:
            break
        try:
            b = json.loads((root / "outbox" / name).read_text(encoding="utf-8"))
            t = OUTBOX_CTRL_RE.sub("", str(b.get("text") or b.get("body") or ""))
            ms = int(name.split("-", 1)[0]); when = time.strftime("%H:%M", time.localtime(ms / 1000)) if ms > 0 else ""
        except Exception:
            continue
        picked.append((b, t, when))
    if picked:
        ui.say("— 重開前的最近 %d 則(重放,不是新信)—" % len(picked), redraw=False)
    else:   # 10-09 the maintainer 看 musebot 截圖「不對」:其實是 outbox 空(0 封)沒東西可放;印一行說清楚,不然像沒套上
        ui.say("— 重開前沒有舊訊息可重放(outbox 空)—", redraw=False)
    for b, t, when in reversed(picked):           # 舊的先印,最新的最靠近提示符
        k, l = outbox_card_args(b, provider_id, human_display_name())
        ui.message(k, l, t, seat=_seat_tail(session_uuid) if k in (provider_id, "system") else "", when=when, redraw=False)
    return len(picked)


def title_for(provider_id: str) -> str:
    if provider_id in ("grokbot", "claudebot", "musebot"):
        return provider_id
    return "sand"


_ENQ_SEQ = __import__("itertools").count()


def enqueue(root: Path, folder: str, text: str, session_uuid: str, provider_id: str, who: str) -> Path:
    ts = time.time()
    # ★ review r3 m7:舊檔名只有「毫秒-寄件人」⇒ 同一毫秒寫兩則(開席人格包執行緒 + 人打的第一句、或兩個執行緒)
    #   後寫的**蓋掉**先寫的。加上 pid + 行程內流水號 + 隨機尾碼;毫秒仍在最前面(按檔名排序 = 按時間),
    #   結尾仍是 -<寄件人>.json(grokbot_mailbox.classify 認 -aib-system.json 結尾)。
    import secrets as _secrets
    name = f"{int(ts * 1000):013d}-{os.getpid()}.{next(_ENQ_SEQ):06d}.{_secrets.token_hex(2)}-{who}.json"
    path = root / folder / name
    payload = {
        "schema": MSG_SCHEMA,
        "from": who,
        "to": "cursor" if who == "human" else "human",
        "provider": provider_id,
        "code": short_code(session_uuid),
        "session_uuid": session_uuid,
        "text": text,
        "created_epoch": int(ts),
    }
    # ★ review r4 m4:先寫同目錄的暫存檔再 os.replace 成正式檔名(原子),同 grokbot_mailbox.post / bot_closer ——
    #   收信端掃 *.json,直接寫正式檔名會讓它讀到寫一半的檔(JSON 解不開就被當壞信跳過或丟掉)。
    #   暫存檔名結尾 .tmp,不會被 *.json 掃到。
    tmp = path.with_name(name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


# 10-08:本機一次性回覆的權限模式可以換(以前寫死 bypassPermissions)。預設照舊 —— -p 一次性跑法沒有人能回答「要不要允許」,
#   改成會問的模式,它碰到要權限的動作就停住;要收緊的人自己設 AIB_GROKBOT_PERMISSION_MODE(例 default / plan)。
GROK_PERMISSION_ENV = "AIB_GROKBOT_PERMISSION_MODE"
GROK_PERMISSION_MODES = ("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions")


def local_reply_permission_mode(environ=None) -> str:
    env = os.environ if environ is None else environ
    mode = str(env.get(GROK_PERMISSION_ENV) or "").strip()
    return mode if mode in GROK_PERMISSION_MODES else "bypassPermissions"


def try_local_reply(text: str, provider_id: str, cwd: str) -> str | None:
    """Optional local one-shot. Off by default unless EZ2_BRIDGE_LOCAL_REPLY=1."""
    if os.environ.get("EZ2_BRIDGE_LOCAL_REPLY", "0") != "1":
        return None
    try:
        if provider_id in ("grokbot", "sand", "grok"):
            bin_path = subprocess.check_output(["bash", "-lc", "command -v grok"], text=True).strip()
            if not bin_path:
                return None
            r = subprocess.run(
                [bin_path, "-p", text, "--permission-mode", local_reply_permission_mode()],
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = (r.stdout or "").strip() or (r.stderr or "").strip()
            return out or None
    except Exception as exc:
        return f"（本地回覆失敗：{exc}）"
    return None


WAKE_TIMEOUT_S = 5.0
# 別席 dm 貼進本席也會走到 wake。每一則被叫醒的 dm 都會在 bot 那條對話裡插一輪、花一輪雲端費用。
# the maintainer 09-25 15:1x:「希望其他席可以與 grokbot 協同開發」⇒ 預設 seats:**已驗證身分的 AIB 席**寄的 dm 才叫醒;
#   0/off = 都不叫醒;all/1 = 連未驗證(unattested:…)的也叫醒。
ENV_WAKE_ON_DM = "AIB_GROKBOT_WAKE_ON_DM"
WAKE_DM_OFF, WAKE_DM_SEATS, WAKE_DM_ALL = "off", "seats", "all"
WAKE_LOG_NAME = "grokbot-wake.jsonl"


def wake_on_dm_mode(env=None) -> str:
    env = os.environ if env is None else env
    v = (env.get(ENV_WAKE_ON_DM) or WAKE_DM_SEATS).strip().lower()
    if v in {"0", "false", "no", "off"}:
        return WAKE_DM_OFF
    if v in {"1", "true", "yes", "on", "all"}:
        return WAKE_DM_ALL
    return WAKE_DM_SEATS


def dm_sender_is_seat(text: str) -> bool:
    """dm 抬頭的寄件人是不是**已驗證身分的 AIB 席**(`@五碼` 或 `@五碼(暱稱)`)。
    dm-ctl 只有在認得出寄件席時才寫 `@碼`;認不出就寫 `unattested:…` ⇒ 看抬頭第一個位址就夠。"""
    t = (text or "").strip()
    if not is_dm_line(t):
        return False
    from dm_header import parse   # 包 I:抬頭只有 dm_header 一份解析(新式與舊式同形)
    got = parse(t)
    return bool(got) and got["sender_kind"] == "seat"


def wake_for_dm(text: str, env=None) -> bool:
    mode = wake_on_dm_mode(env)
    if mode == WAKE_DM_ALL:
        return True
    if mode == WAKE_DM_OFF:
        return False
    return dm_sender_is_seat(text)


def is_dm_line(text: str) -> bool:
    """這一行是不是 AIB dm 貼進來的(抬頭 `[A > B / 台北 …]`)。

    判準不自寫:先走 dm_header.parse(新式 `IP:… >> …`、舊式 `[A > B / 台北 …]` 同一份);
    舊式再過 seat_address 的抬頭白名單閘(新式的格式本身就是閘)。"""
    t = (text or "").strip()
    from dm_header import parse
    got = parse(t)
    if got is None:
        return False
    if got["form"] == "v2":
        return True
    if not t.startswith("[") or " / " not in t.split("]", 1)[0]:
        return False
    head = t.split("]", 1)[0] + "]"
    try:
        from seat_address import assert_address_whitelist, header_addresses
        if len(header_addresses(head)) != 2:
            return False
        assert_address_whitelist(head)
    except Exception:
        return False
    return True


import re as _re
OUTBOX_CTRL_RE = _re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")
# 門鈴合併:上一次門鈴後 bot 還沒來收信 ⇒ 這一則會被那一輪一起收走,不再按(每按一次=雲端一輪費用)。
# 過了這麼久還沒來收,就當那一輪沒起來,再按一次。
RING_COALESCE_S = 90.0


def should_ring(root: Path, last_ring_ts, now: float, window: float | None = None) -> bool:
    if window is None:
        try:
            window = float(os.environ.get("AIB_GROKBOT_RING_COALESCE_S") or RING_COALESCE_S)
        except ValueError:
            window = RING_COALESCE_S
    if last_ring_ts is None or now - last_ring_ts >= window:
        return True
    # bot 最後一次來收信的時間:grokbot_mailbox.pull 每次拿走信都寫 inbox/last-claim。
    #   ★ 不能看被收走那些檔的 mtime:rename 不改檔案 mtime,讀到的是信「建立」的時間,永遠早於門鈴
    #     ⇒ 09-25 真機:bot 已收走改名請求,下一句卻被判「還沒來收」而不按門鈴。
    #   記號不在就退回 claimed/ 目錄的 mtime(往目錄搬進檔案會更新目錄 mtime)。
    last_claim = 0.0
    try:
        last_claim = float((root / "inbox" / "last-claim").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        try:
            last_claim = (root / "inbox" / "claimed").stat().st_mtime
        except OSError:
            last_claim = 0.0
    return last_claim >= last_ring_ts   # bot 在上次門鈴之後來收過了 ⇒ 新訊息要新門鈴


ENV_AGENT = "AIB_GROKBOT_AGENT_ID"   # G5:這一席綁哪個 Grok Bot agent(選單開席時帶)
# ClaudeBot(docs/CLAUDEBOT-DESKTOP.md):這一席綁哪一條本機 Claude session。沒有 webhook ——
#   Claude 那邊的 hook(claudebot_hook.py)直接讀寫本席信箱,門鈴也在 hook 那邊。
ENV_CLAUDE_SESSION = "AIB_CLAUDEBOT_SESSION_ID"
# MuseBot(docs/MUSEBOT-DESKTOP.md):這一席綁哪一個 Muse 對話(可空)。排程喚醒制——
#   沒有 webhook,訊息由 Muse 側經檔案信箱拉/回寫。AIB_ 前綴自動過 env 艙門(identity-env.sh)。
ENV_MUSEBOT_CONVERSATION = "AIB_MUSEBOT_CONVERSATION_ID"


OWN_CLOSER_WAIT_S = 50.0   # 開席時選的那條被「自己這一席剛關的收尾小幫手」握著:最多等這麼久(它最多 45 秒)
LAUNCH_ENV = "AIB_BRIDGE_LAUNCH_BIND"   # "<session>=<開席命令帶的綁定 id>";自我重跑時認開席那條(10-11 review r1)


def bound_env(provider_id: str) -> str:
    if provider_id == "claudebot":
        return ENV_CLAUDE_SESSION
    if provider_id == "musebot":
        return ENV_MUSEBOT_CONVERSATION
    return ENV_AGENT


def bound_agent(provider_id: str = "grokbot") -> str:
    """這一席綁的對話 id(grokbot=agentId、claudebot=Claude session uuid、musebot=Muse 對話 id);沒綁或形狀不對回空字串。"""
    if provider_id == "claudebot":
        import claudebot_threads
        v = (os.environ.get(ENV_CLAUDE_SESSION) or "").strip()
        return v if claudebot_threads.valid_session_id(v) else ""
    if provider_id == "musebot":
        # 10-05 a seat 複核修:原本「只驗非空」,而這個值會變成鎖檔的檔名 ⇒ 要驗形狀(musebot_threads)。
        import musebot_threads
        v = (os.environ.get(ENV_MUSEBOT_CONVERSATION) or "").strip()
        return v if musebot_threads.valid_conversation_id(v) else ""
    import grokbot_threads
    v = (os.environ.get(ENV_AGENT) or "").strip()
    return v if v and grokbot_threads.AGENT_ID_RE.match(v) else ""


def load_wake_config(provider_id: str = "grokbot") -> dict:
    """讀 wake 設定;回 {url, key, source}。不印、不回傳到任何輸出面。"""
    pid = (provider_id or "grokbot").strip().lower()
    agent = bound_agent() if pid == "grokbot" else ""
    if agent:
        # G5:綁了 agent 就只用它專用的那組。共用的 grokbot.env 看不出屬於哪個 agent,
        # 退回去可能叫醒錯的 agent ⇒ 不退回。
        import grokbot_threads
        url, key = grokbot_threads.read_agent_wake(agent, state_root())
        hint = f"agent {agent[:8]} 專用的 webhook"
        return {"url": url, "key": key, "source": "agent" if url else "",
                "url_name": hint + "網址", "key_name": hint + "金鑰", "agent": agent}
    prefix = pid.upper()  # grokbot → GROKBOT
    url_name = f"{prefix}_WAKE_URL"
    key_name = f"{prefix}_WAKE_KEY"
    url = os.environ.get(url_name, "").strip() or os.environ.get("BRIDGE_WAKE_URL", "").strip()
    key = os.environ.get(key_name, "").strip() or os.environ.get("BRIDGE_WAKE_KEY", "").strip()
    source = "env" if url else ""
    for env_path in (
        state_root() / "secrets" / f"{pid}.env",
        state_root() / "secrets" / "bridge.env",  # 舊名兼容
        state_root() / "bridge-wake.env",
    ):
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in (url_name, "BRIDGE_WAKE_URL") and not url:
                url, source = v, env_path.name
            if k in (key_name, "BRIDGE_WAKE_KEY") and not key:
                key = v
    return {"url": url, "key": key, "source": source, "url_name": url_name, "key_name": key_name, "agent": ""}


def check_wake_config(provider_id: str = "grokbot") -> list[str]:
    """只驗設定,**不發任何請求**。形狀判準與 connect 共用 grokbot_threads.validate_wake。"""
    import bot_caps
    if bot_caps.caps(provider_id).wake != "webhook" and provider_id in bot_caps.CAPS:
        # 排程喚醒(musebot)、hook(claudebot):沒有 webhook/門鈴要驗,永遠無問題。
        return []
    import grokbot_threads
    cfg = load_wake_config(provider_id)
    problems = grokbot_threads.validate_wake(cfg["url"], cfg["key"])
    # 保留原本「哪個鍵沒設」的說法(席上與 --check-wake 都印這一句)
    return [(f"{cfg['url_name']} 沒設" if m == "webhook 網址沒設" else
             f"{cfg['key_name']} 沒設" if m == "webhook 金鑰沒設" else m) for m in problems]


def _wake_log(result: dict, session_uuid: str, trigger: str) -> None:
    """落 AIB 自己的日誌。只有結果欄;不落訊息內容、不落網址、不落金鑰。"""
    try:
        d = state_root() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "code": short_code(session_uuid),
            "trigger": trigger,
            "ok": result.get("ok"),
            "status": result.get("status"),
            "reason": result.get("reason"),
            "ms": result.get("ms"),
        }
        with (d / WAKE_LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 日誌寫不進去不擋席;席上那一行仍會印


def wake_cursor_chat(session_uuid: str, seat: str, text: str, provider_id: str = "grokbot") -> dict:
    """POST provider-specific webhook so the matching bot wakes.

    回 {ok, status, reason, ms}。失敗一律要有 reason —— 原本整段 except: pass,
    401/404/逾時在 AIB 這側零徵兆(G1 §3 第一件)。"""
    # 金鑰櫃：~/.ez2-aib/secrets/<provider>.env
    #   grokbot → GROKBOT_WAKE_URL / GROKBOT_WAKE_KEY
    #   （舊 Cursor codexbot 已刪；Desktop provider=codexbot 走 codexbot_bridge，不經此 webhook）
    import urllib.error
    import urllib.request
    pid = (provider_id or "grokbot").strip().lower()
    cfg = load_wake_config(pid)
    if not cfg["url"]:
        return {"ok": False, "status": None, "reason": "沒設", "ms": 0}
    try:
        timeout = float(os.environ.get("AIB_GROKBOT_WAKE_TIMEOUT_S") or WAKE_TIMEOUT_S)
    except ValueError:
        timeout = WAKE_TIMEOUT_S
    body = json.dumps({
        "source": "aib-sand-bridge",
        "provider": pid,
        "session_uuid": session_uuid,
        "code": seat,
        "text": text[:2000],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(cfg["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if cfg["key"]:
        req.add_header("Authorization", "Bearer " + cfg["key"])
        req.add_header("X-Webhook-Key", cfg["key"])
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
        return {"ok": 200 <= int(code) < 300, "status": int(code),
                "reason": "HTTP %s" % code, "ms": ms()}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "reason": "HTTP %s" % exc.code, "ms": ms()}
    except (TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(exc):
            return {"ok": False, "status": None, "reason": "逾時(%.0f 秒)" % timeout, "ms": ms()}
        return {"ok": False, "status": None,
                "reason": "連不上(%s)" % type(reason if isinstance(reason, BaseException) else exc).__name__,
                "ms": ms()}


def wake_line(result: dict, provider_id: str) -> str | None:
    """席上要印的那一行實話;成功回 None(回覆本身會出現,不再多印)。"""
    if result.get("ok"):
        return None
    who = title_for(provider_id)
    if result.get("reason") == "沒設":
        agent = bound_agent() if provider_id == "grokbot" else ""
        if agent:
            return (f"{who}: 沒叫醒——agent {agent[:8]} 還沒綁 webhook"
                    f"(grokbot_threads.py connect --agent {agent})")
        return f"{who}: 沒叫醒——wake 沒設定(~/.ez2-aib/secrets/{provider_id}.env),回覆要等 bot 自己來收"
    return f"{who}: 沒叫醒——{result.get('reason')}(耗時 {result.get('ms')}ms)"



# ── G3:grokbot 對外 dm 從席發出(對齊 CodexBot B1)────────────────────────────
# Grok Bot 的 local-exec 起 grokbot.sh 時沒有 tmux 祖先鏈 ⇒ dm-ctl 只能推成 unattested:grokbot。
# 修法同 CodexBot:席上的橋開本機 socket 收單,在席 pane 的子行程裡跑 run_aib_dm ⇒ 抬頭是 @席碼。
# socket 協定與伺服端直接用 codexbot_dm_socket(不改它);路徑放 grokbot 自己的目錄。
# ★ 10-06 a seat:**跑這支橋的 bot 只有這三家**,而且 socket 與人格包兩處一律用這一份,不各寫一次。
#   起因(實踩):信箱 CLI 這邊 10-06 已經讓 musebot 走 `musebot-bindings/<session>.dm.sock`,
#   但橋這邊開 socket 的名單只有 (grokbot, claudebot) ⇒ musebot 的 socket **從來沒被開過**,
#   信箱每次都落到「直送 + 印一行抬頭會是未認證」的退路。功能不崩、測試不紅、看起來像設計如此,
#   musebot 的抬頭卻永遠修不好 —— 名單各寫一次的壞法就長這樣。
#   codexbot **刻意不在這裡**:它走自己的 codexbot_bridge.py(provider-launch.sh 第 94 行分流),
#   socket 由那支自己開(codexbot_bridge.py 的 DmSocketServer),也沒有 tools/aib-codexbot-mail 入口。
#   守衛:tests/test-bot-provider-sites-987f7.py 的 SandBridgeBotList —— 有 tools/aib-<家>-mail 入口的
#   每一家都必須在這份名單裡,少一家即紅並指名。
SAND_BOT_PROVIDERS = ("grokbot", "claudebot", "musebot")
GROKBOT_BINDINGS = "grokbot-bindings"
CLAUDEBOT_BINDINGS = "claudebot-bindings"   # 與 claudebot_threads.BINDINGS_NAME 同值
MUSEBOT_BINDINGS = "musebot-bindings"       # 與 musebot_threads.BINDINGS_NAME 同值;seat_retire 收 dm.sock 用 "<prov>-bindings" 找,目錄名要對得上
ENV_DM_DRY = "AIB_GROKBOT_DM_DRY"      # =1:橋不真的送,回「會送給誰」(尺用)
ENV_PICK_SEAT = "AIB_GROKBOT_SEAT"     # 多個活席時指定五碼
ROUTE_OK, ROUTE_SENT_FAILED, ROUTE_AMBIGUOUS, ROUTE_NO_SEAT = 0, 1, 2, 3


def dm_socket_path(session_uuid: str, provider_id: str = "grokbot") -> Path:
    d = {"claudebot": CLAUDEBOT_BINDINGS, "musebot": MUSEBOT_BINDINGS}.get(provider_id, GROKBOT_BINDINGS)
    return state_root() / d / f"{session_uuid}.dm.sock"


def dm_runner(target: str, message: str) -> str:
    if (os.environ.get(ENV_DM_DRY) or "") == "1":
        return f"DM 指令 rc=0:DRY 會送給 {target.strip()}({len(message.strip())} 字)"
    return _aib_dm.run_aib_dm(target, message)


def live_grokbot_seats() -> list[dict]:
    """活的 grokbot 席:socket 檔在、旁邊的 pid 檔指向活行程。不連 socket(連了會在席上吵)。"""
    out = []
    d = state_root() / GROKBOT_BINDINGS
    for sock in sorted(d.glob("*.dm.sock")):
        session = sock.name[: -len(".dm.sock")]
        pid_file = d / f"{session}.pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            continue
        out.append({"session": session, "code": short_code(session), "sock": sock, "pid": pid})
    return out


def route_dm(target: str, message: str) -> tuple[int, str]:
    """grokbot.sh 呼叫:交給唯一的活 grokbot 席送。回 (rc, 人話)。"""
    import codexbot_dm_socket as _dms
    seats = live_grokbot_seats()
    pick = (os.environ.get(ENV_PICK_SEAT) or "").strip().lstrip("@")
    if pick:
        seats = [x for x in seats if x["code"] == pick]
        if not seats:
            return ROUTE_NO_SEAT, f"指定的 grokbot 席 @{pick} 沒有活著"
    if not seats:
        return ROUTE_NO_SEAT, "沒有活的 grokbot 席"
    if len(seats) > 1:
        codes = " ".join("@" + x["code"] for x in seats)
        return ROUTE_AMBIGUOUS, (f"有 {len(seats)} 個活的 grokbot 席({codes}),不猜;"
                                 f"設 {ENV_PICK_SEAT}=<五碼> 指定")
    seat = seats[0]
    try:
        reply = _dms.send_via_bridge(seat["sock"], target, message)
    except (OSError, ValueError) as exc:
        return ROUTE_NO_SEAT, f"連不上 @{seat['code']} 的 socket({type(exc).__name__})"
    outcome = reply.get("outcome") or ""
    return (ROUTE_OK if reply.get("ok") else ROUTE_SENT_FAILED), f"經 @{seat['code']}:{outcome}"


def start_dm_socket(session_uuid: str, notice, provider_id: str = "grokbot"):
    import codexbot_dm_socket as _dms
    path = dm_socket_path(session_uuid, provider_id)
    server = _dms.DmSocketServer(path, dm_runner, notice=notice)
    server.start()
    (path.parent / f"{session_uuid}.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    return server


def stop_dm_socket(server, session_uuid: str, provider_id: str = "grokbot") -> None:
    if server is None:
        return
    server.stop()
    try:
        (dm_socket_path(session_uuid, provider_id).parent / f"{session_uuid}.pid").unlink()
    except FileNotFoundError:
        pass



def try_bridge_onboard_ack(session_uuid: str, provider_id: str) -> None:
    """橋接 ready 後:若開席單仍 NO_ACK,補記 inbound(失敗不擋橋)。"""
    try:
        import json as _json
        from pathlib import Path as _Path
        cockpit = _Path(__file__).resolve().parent
        import sys as _sys
        if str(cockpit) not in _sys.path:
            _sys.path.insert(0, str(cockpit))
        import seat_onboard as so  # noqa: WPS433
        # HUB 導出 seats(多艙不得退回主艙殘檔)。提到 EZ2_AIHUB_SOCKET /
        # AIB_DM_SOCKET + instances 是給 seats-dir 守衛看的實碼,不是註解。
        import seats_dir_lib
        _ = (os.environ.get("EZ2_AIHUB_SOCKET"), os.environ.get("AIB_DM_SOCKET"), "instances")
        seats = _Path(seats_dir_lib.seats_dir())
        if not seats.is_dir():
            return
        home = str(seats.parent)
        window = seat_id = None
        for p in seats.glob("w*.json"):
            try:
                rec = _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("session_uuid") != session_uuid:
                continue
            window = rec.get("window_id")
            seat_id = rec.get("seat_id")
            break
        if not window or not seat_id:
            return
        sha = so.current_template_sha()
        led = so.default_ledger_path()
        idx, _bad = so.load_index(led)
        entry = idx.get(seat_id) or {}
        reason = so.onboard_reason(entry, sha)
        # 已有 inbound 或從未投過都不動;只補 NO_ACK
        if reason != so.ONBOARD_NO_ACK:
            return
        so.record(led, "message.inbound", seat_id, window, sha)
        try:
            import open_attempt as oa
            oa.mark_ready_for_window(home, window)
        except Exception:
            pass
    except Exception:
        pass



def _pack_send(root: Path, session_uuid: str, provider_id: str, wake, last_ring: dict, text: str) -> None:
    """人格包那一則:放進本席 inbox(寄件人 aib-system);grokbot 再按門鈴(claudebot 的門鈴在 hook 那邊)。"""
    enqueue(root, "inbox", text, session_uuid, provider_id, "aib-system")
    import bot_caps
    if bot_caps.caps(provider_id).wake == "webhook":
        last_ring["ts"] = time.time()
        wake("[AIB 系統] 人格包", "system")


def start_pack_delivery(root: Path, session_uuid: str, provider_id: str, say, wake, last_ring: dict,
                        threads: list, stop: dict) -> threading.Thread:
    """起背景執行緒:bridge_persona.deliver_on_open 把「基因 + 人格包」放進本席 inbox(grokbot 再按門鈴)。
    模組缺了或 import 就壞 ⇒ 說一聲、回 None,橋照常起(review r1 m7)。"""
    try:
        import bridge_persona
    except Exception as exc:  # noqa: BLE001 —— 人格包送不出去不能讓橋停
        say(f"{provider_id}: 人格包模組載不進來({type(exc).__name__}),這一席先不送")
        return None

    send = lambda text: _pack_send(root, session_uuid, provider_id, wake, last_ring, text)  # noqa: E731

    def run() -> None:
        try:
            got = bridge_persona.deliver_on_open(root, session_uuid, send, stop=lambda: stop["flag"])
        except Exception as exc:  # noqa: BLE001 —— 人格包送不出去不能讓橋停
            say(f"{provider_id}: 人格包沒送出去({type(exc).__name__})")
            return
        if got.startswith("sent:"):
            # review r9 m4:基因關著時只送了人格包,別說有基因
            what = {"sent:persona+genes": "基因與人格包", "sent:persona": "人格包",
                    "sent:topup": "基因(人格包之前送過,基因開關後來才打開)"}.get(got, "基因(這一席還沒綁人格)")
            say(f"{provider_id}: 已把{what}放進信箱,bot 下次收信時讀")
        if got not in ("sent:genes", "already"):
            return
        # 只送了基因(或重開時記號還是 genes):同一條背景執行緒再等人格綁上,讀得到就補送一次人格包;橋收掉就停
        try:
            up = bridge_persona.upgrade_after_genes(root, session_uuid, send, stop=lambda: stop["flag"])
        except Exception as exc:  # noqa: BLE001 —— 補送不出去不能讓橋停
            say(f"{provider_id}: 人格包補送失敗({type(exc).__name__})")
            return
        if up == "sent:persona":
            say(f"{provider_id}: 這一席剛綁上人格,已把人格包補放進信箱,bot 下次收信時讀")

    t = threading.Thread(target=run, name="bridge-persona", daemon=True)
    t.start()
    threads.append(t)
    return t


def _speaker_label(text: str) -> str:
    """貼進席的別席私訊:看 dm 抬頭認寄件席(已驗證才給席碼),不看內文自稱。"""
    try:
        from grokbot_mailbox import dm_sender
        who = dm_sender(text)
    except Exception:  # noqa: BLE001
        who = ""
    return ("別席 %s" % who) if who else "別席(未驗證)"


_OK_HEALTH = {"health": "ok", "last_error_kind": None, "last_error": None}
_UNBOUND_HEALTH = {"health": "degraded", "last_error_kind": "unbound",
                   "last_error": "沒綁對話:信只進信箱,沒有人收;在席上打 /bind 綁一條(不用重開)"}


class _EitherStop:
    """兩個停止旗標任一舉起就停(橋整個收掉,或這一條綁定被 /unbind)。只給 claudebot_live.run 用(它只呼叫 get)。"""

    def __init__(self, *stops: dict) -> None:
        self.stops = stops

    def get(self, key, default=None):
        return any(s.get(key) for s in self.stops)

    def __getitem__(self, key):
        return self.get(key)


def sync_seat_claude_session(session_uuid: str, session_id: str, base=None) -> bool:
    """10-09 a seat(待修清單第 11 件,a seat 報告):席上 /bind、/unbind 以前只改橋的 status.json,
    席位檔的 claude_session_id 一直留著開席那一條(w17 記 bc1d9ade,實綁 eccd6bf3)。讀它的人(右欄退路、別席查這一席綁誰)
    會看到舊的。這裡把席位檔那一欄對上;寫法用 atomic_json.update_json(席位檔多寫者,艦長列心跳每 5 秒寫一次,要持鎖)。
    找不到這一席的席位檔(測試沙箱、席位檔已退役)⇒ 回 False,不擋 /bind。"""
    import glob as _glob
    root = Path(base) if base else state_root()
    for f in sorted(_glob.glob(str(root / "instances" / "*" / "seats" / "w*.json"))):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("session_uuid") != session_uuid or rec.get("provider") != "claudebot":
            continue
        try:
            import atomic_json

            def _mutate(r):
                if not isinstance(r, dict) or r.get("session_uuid") != session_uuid:
                    return r            # 鎖內再判一次:這一格已換成別席就不動
                r["claude_session_id"] = session_id or ""
                r["claude_session_source"] = "bridge-bind" if session_id else "bridge-unbind"
                return r
            atomic_json.update_json(f, _mutate)
            return True
        except Exception:  # noqa: BLE001 —— 同步失敗不擋綁定;右欄以 status.json 為準
            return False
    return False


BIND_OVERRIDE_NAME = "bind-override.json"


def save_bind_override(root: Path, launch_id: str, chosen: str) -> None:
    """10-09 a seat:席上 /bind、/unbind 的選擇要撐過橋重啟。真機:a seat 席上換綁到 the maintainer 桌面 app 的對話 eccd6bf3,
    橋 14:25 重啟後照開席命令的 AIB_CLAUDEBOT_SESSION_ID 綁回 bc1d9ade,沒有任何人知道;之後的 dm 全進了舊那條。
    記「開席時帶的那條(launch)」與「席上選的(chosen,空 = 解綁)」。"""
    try:
        (root / BIND_OVERRIDE_NAME).write_text(json.dumps({"launch": launch_id or "", "chosen": chosen or "",
                                                           "at": int(time.time())}, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    except OSError:
        pass


def load_bind_override(root: Path, launch_id: str):
    """回 (有沒有可沿用的選擇, 選的那條)。開席帶的那條跟記錄時不同 = 有人明確用新的一條重開 ⇒ 照新的,並刪掉舊紀錄。"""
    f = root / BIND_OVERRIDE_NAME
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    if not isinstance(d, dict) or (d.get("launch") or "") != (launch_id or ""):
        try:
            f.unlink()
        except OSError:
            pass
        return False, ""
    return True, str(d.get("chosen") or "")


def _spawn_claude_closer(agent: str, session_uuid: str):
    """claudebot 收尾小幫手(bot_closer close_claudebot);起不來回 None,不擋開席。AIB_CLAUDEBOT_RENAME=0 關掉(同 grokbot/codexbot)。"""
    if (os.environ.get("AIB_CLAUDEBOT_RENAME") or "1") == "0":
        return None
    try:
        import bot_closer
        return bot_closer.spawn("claudebot", {"session_id_claude": agent, "session_id": session_uuid}, state_root())
    except Exception:  # noqa: BLE001
        return None


def claim_bound_agent(agent: str, provider_id: str):
    """綁定前兩道關:claudebot 不准綁 AIB 自己某一席的對話;同一個 agent / Claude session 只准一隻 AIB 席(鎖)。
    回 (鎖檔 handle, "") 或 (None, 錯誤句, rc)。開席時與席上 /bind 共用(10-07 a seat 抽出來,以前只在開席那段)。"""
    is_claude = provider_id == "claudebot"
    if is_claude:
        # 10-07 root a seat:綁到 AIB 自己某一席的 Claude session ⇒ hook 會把信塞進那一席、把它的回覆當 bot 回覆送出(兩席串線)。
        #   選單已經不列這種對話(claudebot_threads.aib_seat_sessions);這裡擋命令列直接帶 uuid 與 /bind 的路。
        import claudebot_threads as _ct
        if _ct.is_aib_seat_session(agent, state_root()):
            return None, (f"Claude 對話 {agent} 是 AIB 自己某一席的對話,不能再綁成 claudebot(會兩席串線);"
                          f"請在 Claude app 或終端機另開一條本機對話再綁"), 2
    import fcntl
    import importlib
    import bot_caps
    cap = bot_caps.caps(provider_id)
    _threads = importlib.import_module(cap.threads_module or "grokbot_threads")
    lp = _threads.lock_path(agent, state_root())
    lp.parent.mkdir(parents=True, exist_ok=True)
    handle = lp.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        who = _threads.held_by_names(agent, state_root()) or ["另一隻 AIB 席"]
        return None, f"{cap.bind_what} {agent[:8]} 已經被 {'、'.join(who)} 占著,不開第二隻", 3
    return handle, "", 0


def write_mail_token(root: Path) -> None:
    # the maintainer 09-25 #2:回信通行碼。每次開席(或席上 /bind)一組;grokbot_mailbox pull 附在輸出裡,post 必須帶上。
    #   0600、先寫 .tmp 再改名。只用 0-9a-f:urlsafe 有 1/64 以 - 開頭,`--token -x…` 會被 argparse 當選項。
    import secrets as _secrets
    tmp = root / "mail-token.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_secrets.token_hex(16) + "\n")
    os.replace(tmp, root / "mail-token")


# 10-07 the maintainer 令「開席在本 AIB 裡、然後再綁」:沒綁的 claudebot 席上打 /bind 就地綁,不必關席重開。
#   /bind        列出能綁的本機 Claude 對話(與右鍵選單同一份清單:不列 AIB 席、不列 claude -p、被占的標出來)
#   /bind <n>    綁清單第 n 條;/bind <完整 uuid> 綁那一條;/bind new AIB 代開一條新的並綁上(同一鍵開席)
#   已綁的席不准換綁(換綁要先解掉舊的 hook 綁定與人格包狀態,範圍另排);要換就關席重開。
BIND_LIST_LIMIT = 9


def bind_candidates(limit: int = BIND_LIST_LIMIT) -> list[dict]:
    import claudebot_threads as _ct
    rows = _ct.list_sessions(limit=limit, base=state_root())
    for r in rows:
        try:
            r["held"] = "、".join(_ct.held_by_names(r["id"], state_root()))
        except Exception:  # noqa: BLE001 —— 量不到誰占著 ≠ 沒人占;綁的時候鎖會再擋一次
            r["held"] = ""
        r["perm"] = _ct.permission_mode(r["id"])
    return rows


def permission_note(mode: str) -> str:
    """綁定時要讓人看到的權限一句話;沒什麼好說的回空字串。"""
    import claudebot_threads as _ct
    risk = _ct.permission_risk(mode)
    if risk == "danger":
        return (f"⚠ 這條對話是「{_ct.permission_label(mode)}」({mode}):別席的 dm 叫醒它時,它會不問你就改檔、跑指令。"
                f"建議在那條對話按 Shift+Tab 切回「動手前先問」再綁")
    if risk == "caution":
        return f"留意:這條對話是「{_ct.permission_label(mode)}」({mode}),有些動作不會先問你"
    if risk == "unknown":
        return "留意:量不到這條對話的權限模式(對話檔還沒有紀錄),綁上後別席的 dm 能讓它做什麼不確定"
    return ""


def render_bind_list(rows: list[dict]) -> list[str]:
    import claudebot_threads as _ct
    if not rows:
        return ["claudebot: 沒有能綁的本機 Claude 對話;打 /bind new 讓 AIB 代開一條並綁上"]
    out = ["claudebot: 能綁的本機 Claude 對話(打 /bind <編號>;/bind new = AIB 代開一條新的):"]
    for i, r in enumerate(rows, 1):
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated") or 0))
        name = OUTBOX_CTRL_RE.sub("", str(r.get("name") or ""))[:50]
        held = f"  [正被 {r['held']} 使用,不能選]" if r.get("held") else ""
        mode = r.get("perm") or ""
        mark = "⚠" if _ct.permission_risk(mode) == "danger" else ""
        out.append(f"  {i}. {name}  ({when} · {r['id'][:8]} · {mark}{_ct.permission_label(mode)}){held}")
    if any(_ct.permission_risk(r.get("perm") or "") == "danger" for r in rows):
        out.append("  標 ⚠ 的對話不問就動手;真的要綁,在編號後加 !(例:/bind 2!)")
    return out


def resolve_bind_arg(arg: str, rows: list[dict]) -> tuple[str, str]:
    """/bind 後面那段 ⇒ (session uuid, 錯誤句)。"new" 由呼叫端處理。"""
    import claudebot_threads as _ct
    arg = arg.strip().lower()
    if arg.isdigit():
        n = int(arg)
        if not rows:
            return "", "先打 /bind 看清單,再打 /bind <編號>"
        if not 1 <= n <= len(rows):
            return "", f"編號 {n} 不在清單裡(1–{len(rows)})"
        if rows[n - 1].get("held"):
            return "", f"第 {n} 條正被 {rows[n - 1]['held']} 使用,不能綁"
        return rows[n - 1]["id"], ""
    if _ct.valid_session_id(arg):
        return arg, ""
    return "", "看不懂「%s」:/bind 列清單、/bind <編號>、/bind <完整 uuid>、/bind new" % arg[:40]


# 10-11 a seat(Bot 開發 P1,the maintainer「最少步驟串成 bot」):GrokBot、MuseBot 也能席上 /bind、/unbind,不用關席重開。
#   ClaudeBot 那一條(上面幾支 + 主迴圈 claudebot 分支)原封不動;另兩家走下面這幾支,差別只問 bot_caps。
#   已綁的席不准直接 /bind 別條(同 ClaudeBot):先 /unbind 再 /bind,兩行、席不關。
def valid_bind_id(provider_id: str, value: str) -> bool:
    """席上 /bind、橋重啟沿用時,這個 id 形狀對不對(形狀錯的值會變成鎖檔名,不能放過)。"""
    v = (value or "").strip()
    if provider_id == "claudebot":
        import claudebot_threads
        return claudebot_threads.valid_session_id(v)
    if provider_id == "musebot":
        import musebot_threads
        return musebot_threads.valid_conversation_id(v)
    import grokbot_threads
    return bool(v and grokbot_threads.AGENT_ID_RE.match(v))


def other_bind_candidates(provider_id: str, limit: int = BIND_LIST_LIMIT) -> list[dict]:
    """GrokBot = app 本機名冊的 agent(新的在前);MuseBot = 綁過的 Muse 對話(Muse 沒有本機名冊,只列得出綁過的)。
    每列 {id, name, updated, held, wake};held 以鎖為準(同右鍵選單);wake 只有 GrokBot 有意義(這個 agent 接了門鈴沒)。"""
    out = []
    if provider_id == "grokbot":
        import grokbot_threads as _gt
        for r in _gt.list_agents(limit):
            url, _key = _gt.read_agent_wake(r["id"], state_root())
            out.append({"id": r["id"], "name": r.get("name") or "", "updated": r.get("updated") or 0,
                        "held": "、".join(_gt.held_by(r["id"], state_root())), "wake": bool(url)})
    elif provider_id == "musebot":
        import musebot_threads as _mt
        for conv, mt in _mt.history(state_root())[: max(0, int(limit))]:
            out.append({"id": conv, "name": "", "updated": int(mt),
                        "held": "、".join(_mt.held_by(conv, state_root())), "wake": True})
    return out


def render_other_bind_list(provider_id: str, bind_what: str, rows: list[dict]) -> list[str]:
    if not rows:
        if provider_id == "grokbot":
            return ["grokbot: Grok Bot 名冊裡沒有 agent;剛建的要先在 Grok Bot app 點它一下才會出現,再打 /bind"]
        return [f"{provider_id}: 還沒綁過任何{bind_what};第一次請打 /bind <{bind_what} id>"]
    out = [f"{provider_id}: 能綁的{bind_what}(打 /bind <編號>,或 /bind <完整 id>):"]
    for i, r in enumerate(rows, 1):
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated") or 0))
        name = OUTBOX_CTRL_RE.sub("", str(r.get("name") or ""))[:50]
        held = f"  [正被 {r['held']} 使用,不能選]" if r.get("held") else ""
        bell = "" if r.get("wake") else "  [沒接門鈴]"
        out.append(f"  {i}. {(name + '  ') if name else ''}({when} · {r['id'][:8]}){bell}{held}")
    if provider_id == "musebot":
        out.append("  不在清單裡的(第一次綁):/bind <Muse 對話 id>")
    return out


def resolve_other_bind_arg(provider_id: str, arg: str, rows: list[dict]) -> tuple[str, str]:
    """/bind 後面那段 ⇒ (id, 錯誤句)。不轉小寫:Grok agent id 與 Muse 對話 id 大小寫照原樣。"""
    arg = arg.strip()
    if arg.isdigit():
        n = int(arg)
        if not rows:
            return "", "先打 /bind 看清單,再打 /bind <編號>"
        if not 1 <= n <= len(rows):
            return "", f"編號 {n} 不在清單裡(1–{len(rows)})"
        if rows[n - 1].get("held"):
            return "", f"第 {n} 條正被 {rows[n - 1]['held']} 使用,不能綁"
        return rows[n - 1]["id"], ""
    if valid_bind_id(provider_id, arg):
        return arg, ""
    return "", "看不懂「%s」:/bind 列清單、/bind <編號>、/bind <完整 id>" % arg[:40]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sand-bridge")
    ap.add_argument("--session-id", default="")
    ap.add_argument("--role", default="root")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--mode", default="dgrs")
    ap.add_argument("--model", default="sand-bridge")
    ap.add_argument("--effort", default="")
    ap.add_argument("--provider-id", default="sand")
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv[:1] == ["--route-dm"]:
        rest = raw_argv[1:]
        if len(rest) >= 3 and rest[1] == "--file":
            target, message = rest[0], Path(rest[2]).read_text(encoding="utf-8")
        elif len(rest) >= 2:
            target, message = rest[0], " ".join(rest[1:])
        else:
            print("用法:sand_bridge.py --route-dm <目標> <訊息...> | <目標> --file F", file=sys.stderr)
            return ROUTE_AMBIGUOUS
        rc, text = route_dm(target, message)
        print(text, file=sys.stdout if rc in (ROUTE_OK, ROUTE_SENT_FAILED) else sys.stderr)
        return rc
    if "--check-wake" in raw_argv:
        cap = argparse.ArgumentParser(prog="sand-bridge --check-wake")
        cap.add_argument("--check-wake", action="store_true")
        cap.add_argument("--provider-id", default="grokbot")
        cargs, _ = cap.parse_known_args(argv)
        problems = check_wake_config(cargs.provider_id)
        if problems:
            for msg in problems:
                print("✖ " + msg)
            print("(只驗設定,沒有試打)")
            return 1
        print("wake 設定正常(只驗設定,沒有試打)")
        return 0
    args, _unknown = ap.parse_known_args(argv)

    session_uuid = args.session_id.strip().lower()
    if len(session_uuid) < 8:
        print("sand-bridge: invalid --session-id", file=sys.stderr)
        return 2

    agent_lock = None
    import bot_caps
    cap = bot_caps.caps(args.provider_id)   # 10-07 a seat:各家差異只問這張表(bot_caps.py 檔頭說為什麼)
    is_claude = args.provider_id == "claudebot"
    # 10-05 a seat 複核修:musebot 原本不在這兩行的名單裡 ⇒ bound_agent/bound_env 的 musebot 分支
    #   在真流程走不到(綁定從不被讀、也沒有「同一對話只准一隻席」的鎖),而測試直接呼叫那兩個函式所以是綠的。
    binds = bool(cap.bind_env) and cap.bridge == "mailbox"
    agent = bound_agent(args.provider_id) if binds else ""
    if binds and (os.environ.get(bound_env(args.provider_id)) or "").strip() \
            and not agent:
        print(f"sand-bridge: {bound_env(args.provider_id)} 形狀不對,不開席", file=sys.stderr)
        return 2
    launch_agent = agent      # 開席命令帶的那條;席上 /bind、/unbind 的選擇以它為鍵記下,橋重啟時沿用(10-09)
    # 10-11 a seat(review r1):grokbot/musebot 席上換綁會改寫 bound_env 那個環境變數(門鈴要跟),而閒時自我重跑(maybe_reexec)
    #   用 execv 帶著改過的環境 ⇒ 重跑後讀到的「開席那條」變成選的那條,bind-override 的 launch 對不上被刪,之後艙重開就默默綁回舊的。
    #   ⇒ 開席那條另記在 LAUNCH_ENV(值帶席的 session,別的席繼承到也不認),重跑時以它為準。
    _launch_mark = (os.environ.get(LAUNCH_ENV) or "").split("=", 1)
    if len(_launch_mark) == 2 and _launch_mark[0] == session_uuid \
            and (not _launch_mark[1] or valid_bind_id(args.provider_id, _launch_mark[1])):
        launch_agent = _launch_mark[1]
    os.environ[LAUNCH_ENV] = f"{session_uuid}={launch_agent}"
    if cap.bind_in_seat:
        _has, _chosen = load_bind_override(bridge_dir(session_uuid), launch_agent)
        if _has and _chosen and _chosen != launch_agent and valid_bind_id(args.provider_id, _chosen):
            # review r1:選的那條現在被別席占著 ⇒ 這次先接開席那條(用了它橋搶不到鎖、席當場死);紀錄留著,下次空了再沿用
            try:
                import importlib
                _held = importlib.import_module(cap.threads_module).held_by(_chosen, state_root())
            except Exception:  # noqa: BLE001 —— 量不到 ≠ 被占;真被占的話下面搶鎖還會擋
                _held = []
            _own = "@" + short_code(session_uuid)
            if _held and all(h == _own for h in _held):
                # review r2:只有自己這一席(剛被關掉的那隻的收尾小幫手,改名時握著鎖,最多 bot_closer.GROK_CLOSE_WAIT_S)⇒ 等它放手,
                #   不等的話下面搶鎖必失敗、席當場死
                _deadline = time.time() + OWN_CLOSER_WAIT_S
                while _held and time.time() < _deadline:
                    time.sleep(0.5)
                    try:
                        _held = importlib.import_module(cap.threads_module).held_by(_chosen, state_root())
                    except Exception:  # noqa: BLE001
                        _held = []
            if _held:
                print(f"sand-bridge: 席上 /bind 選的 {cap.bind_what} {_chosen[:8]} 正被 {'、'.join(_held)} 使用,"
                      f"這次先接開席命令那條 {launch_agent[:8] or '(無)'}", file=sys.stderr)
                _has = False
                agent = launch_agent       # review r2:自我重跑時環境變數已是選的那條 ⇒ 要明確退回開席那條
        if _has:
            if _chosen and valid_bind_id(args.provider_id, _chosen):
                agent = _chosen
                print(f"sand-bridge: 沿用席上 /bind 選的 {cap.bind_what} {_chosen}(開席命令帶的是 {launch_agent or '(無)'})", file=sys.stderr)
            elif not _chosen:
                agent = ""
                print("sand-bridge: 沿用席上 /unbind:這一席維持不綁", file=sys.stderr)
        if not is_claude:
            # 10-11 a seat:門鈴(load_wake_config → bound_agent)讀的是環境變數 ⇒ 沿用的選擇也寫回去,門鈴才叫對 agent
            os.environ[bound_env(args.provider_id)] = agent
    if agent:
        agent_lock, err, rc = claim_bound_agent(agent, args.provider_id)
        if agent_lock is None:
            print(f"sand-bridge: {err}", file=sys.stderr)
            return rc

    root = bridge_dir(session_uuid)
    ensure_dirs(root)
    if agent or cap.mail_without_bind:
        # musebot:沒綁對話時 agent 為空,但信箱照常運作 ⇒ 也發通行碼(musebot_mailbox 認)。
        write_mail_token(root)
    closer = None
    if agent and cap.rename_in_app and (os.environ.get("AIB_GROKBOT_RENAME") or "1") != "0":
        # 收尾小幫手在開席第一步就生(拿到 agent 鎖、建好席目錄之後):以前放在綁定改名之後,
        #   席在頭一秒內死掉時還沒有小幫手 ⇒ app 名稱不會改回未綁定(09-25 批次尺抓到的空窗)。
        import bot_closer
        closer = bot_closer.spawn("grokbot", {"agent_id": agent, "session_id": session_uuid}, state_root())
    if agent and is_claude:
        # 10-09 a seat(the maintainer 令):claudebot 關席也把對話標題改回「( 未綁定AIB席位 )」—— 同 grokbot,交給收尾小幫手
        #   (席在自己快死時收尾不可靠)。小幫手只動標題與 status,搶不到那條對話的鎖(新席已綁)就不動。
        closer = _spawn_claude_closer(agent, session_uuid)
    stop = {"flag": False}

    def _closer_done_before_exec() -> None:
        if closer is not None:     # 讀當下那一隻(席上 /bind、/unbind 會換)
            closer.done()
    _reexec["before"] = _closer_done_before_exec
    seen_out = set(p.name for p in (root / "outbox").glob("*.json"))

    def _stop(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    # [X] 關窗 / 艙收掉時,pane 收到的是 SIGHUP;以前直接死 ⇒ 關席收尾(把 app 名稱改回未綁定)做不到
    signal.signal(signal.SIGHUP, _stop)

    _live_health = {"override": None}   # 10-07:claudebot_live 量到門鈴過期等 ⇒ 換掉 health 那三欄;None = 照舊

    def set_health_for(kind: str, h) -> None:
        # 10-07 a seat:門鈴失敗、沒來收信兩種警示各自標、各自解除(解除只拿掉自己標的那一種,不蓋掉別人的)
        cur = _live_health.get("override")
        if h is None:
            if isinstance(cur, dict) and cur.get("last_error_kind") == kind:
                _live_health["override"] = None
        else:
            _live_health["override"] = h

    _started = {"v": None}

    def status_payload(state: str) -> dict:
        # started_epoch 只在首次寫入,心跳不得刷新——activity_probe 靠它當
        # 「尚無 inbox/outbox 訊息」時的開席活動跡證。
        started = _started.get("v")   # 10-07 a seat:讀一次就記住(以前每 0.1 秒讀一次 status.json)
        if started is None:
            try:
                prev = json.loads((root / "status.json").read_text(encoding="utf-8"))
                started = prev.get("started_epoch")
            except (OSError, ValueError, TypeError):
                started = None
            if not isinstance(started, (int, float)) or started <= 0:
                started = int(time.time())
            _started["v"] = started
        return {
            "provider": args.provider_id,
            "session_uuid": session_uuid,
            "code": short_code(session_uuid),
            "role": args.role,
            "mode": args.mode,
            "model": args.model,
            "cwd": args.cwd,
            # ★ 10-06(the maintainer 裁 ③):艙位碼由橋自己寫,單一來源。以前信箱 CLI 要補艙位碼只能掃
            #   instances/*/seats/w*.json 找同一個 session_uuid,恰好一席才敢用;搬艙或殘留就查到兩個、不補。
            #   橋起來時環境裡就有 EZ2_AIB_INSTANCE,寫下來最準。讀端仍保留掃登記簿當退路(舊橋沒這一欄)。
            # 10-07:開席器洗掉了 EZ2_AIB_INSTANCE,改由它預先算好的 AIB_BRIDGE_CABIN 帶進來;沒有才退回自己算(手動起橋/治具)
            "cabin": (os.environ.get("AIB_BRIDGE_CABIN") or "").strip() or _dm_header.local_cabin(),
            "pid": os.getpid(),
            "state": state,
            "started_epoch": int(started),
            "inbox": len(list((root / "inbox").glob("*.json"))),
            "outbox": len(list((root / "outbox").glob("*.json"))),
            "input": "grok-style",
            "prompt": "❯",
            "agent_id": agent or None,
            # 10-07 root a seat:沒綁對話的 claudebot/grokbot 席「活著但收不到也送不出」(a seat 01:53 開,右欄整列綠)。
            #   寫進 a seat 包 B 定的兩欄,右欄(pane-right-ai.sh _bridge_health_text)就會在名字後接「⚠沒綁對話」。
            #   musebot 不算:它沒綁 Muse 對話時信箱照常運作(排程拉信),是設計上的正常狀態。
            **(_UNBOUND_HEALTH if (cap.unbound_is_fault and not agent)
               else (_live_health.get("override") or _OK_HEALTH)),
        }

    ui = DockedUI()
    code = short_code(session_uuid)
    banner = [
        f"Ez2 AIB · {title_for(args.provider_id)} bridge  @{code}",
        f"session {session_uuid}",
        "輸入對齊 grok CLI（❯）。Enter 送出。/dm <@席> <訊> · /status 重繪 · /quit 結束" + (" · /bind 綁對話 · /unbind 解除" if cap.bind_in_seat else "")
        + (" · /connect 接門鈴" if args.provider_id == "grokbot" else ""),
        _aib_dm.dm_boot_banner(),
        cap.reply_from or "回覆來自：Cursor 助手寫 outbox（預設）；或 EZ2_BRIDGE_LOCAL_REPLY=1 本地 CLI。",
        "",
        "AIB-READY",
    ]
    for line in banner:
        ui.append(line)
    # 10-09:重放放在橫幅**後面**(原本在橫幅前,被橫幅捲掉,the maintainer 以為沒套上);離提示符最近,打開席第一眼就看到
    replay_recent(ui, root, args.provider_id, session_uuid)
    write_status(root, status_payload("ready"))
    try_bridge_onboard_ack(session_uuid, args.provider_id)
    if cap.wake == "schedule":
        ui.say("喚醒:排程制(cron/hook 拉信箱),無需 webhook 設定", redraw=False)
    if args.provider_id == "grokbot":
        # ★ 用 say 不用 append:append 只進畫面緩衝,沒有終端機時(非 TTY)整行不會印出(09-25 尺抓到)
        problems = check_wake_config(args.provider_id)
        if problems:
            ui.say("wake 設定:" + ";".join(problems) + "(只驗設定,沒有試打)", redraw=False)
        else:
            ui.say("wake 設定:正常(只驗設定,沒有試打)", redraw=False)
        try:   # 第 22 件(10-11 a seat):開席講清楚 Grok Bot app 用哪個帳號;讀不到照實說,不擋開席
            import grokbot_threads as _gt22
            _acct = _gt22.app_account()
        except Exception:  # noqa: BLE001 —— 顯示帳號是附加的
            _acct = ""
        ui.say("grokbot: 這台 Grok Bot app 登入的帳號:%s" % (_acct or "讀不到"), redraw=False)
        _mode = wake_on_dm_mode()
        ui.say({WAKE_DM_OFF: f"別席 dm 進來不叫醒 bot({ENV_WAKE_ON_DM}=seats 讓 AIB 席的 dm 叫醒)",
                WAKE_DM_SEATS: "別席 dm:已驗證的 AIB 席寄的會叫醒 bot(協作);未驗證的只進信箱",
                WAKE_DM_ALL: "別席 dm:全部都會叫醒 bot(含未驗證的)"}[_mode], redraw=False)
    if is_claude:
        if agent:
            ui.say(f"claudebot: 綁定 Claude 對話 {agent}(本機 session 要裝 hook:python3 src/cockpit/claudebot_hook.py install;"
                   f"雲端 session 走中繼:aib claudebot relay up)", redraw=False)
            try:
                import claudebot_threads as _ct5
                import claudebot_spawn as _cbs5   # AIB 代開的對話模式是我們給的(預設 manual),剛開時對話檔還沒紀錄,不印「量不到」
                _note = "" if _cbs5.record_of(agent) else permission_note(_ct5.permission_mode(agent))
                if _note:
                    ui.say("claudebot: " + _note, redraw=False)
            except Exception:  # noqa: BLE001 —— 量權限失敗不能讓橋停
                pass
            try:
                import claudebot_spawn as _cbs
                if _cbs.record_of(agent):
                    # 10-07:這條對話是 AIB 代開的(claudebot_spawn),告訴人怎麼進去打字
                    ui.say(f"claudebot: 這條對話由 AIB 代開;要進去打字:{_cbs.attach_command(agent)}(離開按 Ctrl-b d)", redraw=False)
            except Exception:  # noqa: BLE001 —— 提示印不出來不能讓橋停
                pass
        else:
            ui.say(f"claudebot: 沒綁 Claude 對話({ENV_CLAUDE_SESSION} 沒帶),訊息只會進信箱,沒有人收;"
                   f"打 /bind 列出能綁的對話、/bind new 讓 AIB 代開一條,不用關席重開", redraw=False)
        # the maintainer 09-27 15:2x「席上印兩個環境變數」:雲端 Claude 環境要填的中繼網址與通行碼(通行碼是鑰匙,只在自己的終端機上)。
        #   量 tailscale 最多幾秒;任何錯都不擋開席。
        #   r4 D1/D2:通行碼只印前 4 碼(畫面會被存檔、被別席讀);中繼沒在跑就只給指令,不量 tailscale、不生成鑰匙檔。
        try:
            import claudebot_relay as _relay
            _port = int(os.environ.get("AIB_CLAUDEBOT_RELAY_PORT") or _relay.DEFAULT_PORT)
            if _relay.health(_port, 1.0):
                ui.say("雲端環境變數(中繼在跑;通行碼全文:aib claudebot relay token):", redraw=False)
                for _ln in _relay.cloud_env_lines(_port, generate=False):
                    ui.say("  " + _ln, redraw=False)
            else:
                ui.say("雲端環境變數:中繼沒在跑;aib claudebot relay up 會起中繼並印出要填的兩行", redraw=False)
        except Exception as _exc:  # noqa: BLE001
            ui.say("雲端環境變數:量不到(%s)" % type(_exc).__name__, redraw=False)
    if agent and cap.rename_in_app and (os.environ.get("AIB_GROKBOT_RENAME") or "1") != "0" and closer is None:
        ui.say("grokbot: 收尾小幫手起不來;關席時 app 名稱不會改回未綁定")
    # 10-11 a seat(Bot 開發 P2):就緒檢查(bot_ready.py,只讀設定、不寫信、不花額度)。開席、席上 /bind 各查一次;
    #   查不過 ⇒ health 標那一種原因,右欄名字後面出現「⚠沒接門鈴」「⚠沒裝hook」;還沒好時心跳每 READY_RECHECK_S 秒再查,
    #   人在別處修好(例如跑了 aib bot connect)⇒ 自己拿掉 ⚠ 並在席上說一聲,不用重開。
    _ready = {"bad": False, "at": 0.0}

    def check_ready(say_it: bool = True) -> None:
        _ready["at"] = time.time()
        try:
            import bot_ready as _br
            rows = _br.check(args.provider_id, agent)
        except Exception:  # noqa: BLE001 —— 就緒檢查是附加的,壞了不能讓橋停
            return
        bad = _br.first_problem(rows)
        flipped = _ready["bad"] != bool(bad)
        _ready["bad"] = bool(bad)
        _mine = (_br.KIND_NO_AGENT, _br.KIND_NO_DOORBELL, _br.KIND_NO_HOOK, _br.KIND_NO_MAIL_TOOL)
        for _k in _mine:
            set_health_for(_k, None)
        _cur = _live_health.get("override")
        if bad and (not isinstance(_cur, dict) or _cur.get("last_error_kind") in _mine):
            # 優先權最低:門鈴失敗、沒來收信、門鈴過期是「真的發生了」,比「設定看起來不對」更該先讓人看到,不蓋掉
            set_health_for(bad["kind"], {"health": "degraded", "last_error_kind": bad["kind"], "last_error": bad["fix"]})
        line = _br.summary(args.provider_id, rows)
        if line and (say_it or flipped):
            ui.say(line)

    check_ready()
    ui.say(redraw=True)

    _hb = {"last": None, "at": 0.0}

    _last_shown = {"key": None, "at": 0.0}   # 10-09:同一段被 post 兩次(兩支 Stop hook 同時跑,相隔 50 毫秒)畫面只留一張

    def heartbeat():
        while not stop["flag"]:
            try:
                # 10-07 a seat(各 bot 比對):以前每 0.1 秒整檔重寫一次(開 5 隻橋 = 每秒 50 次寫入)。
                #   內容變了(綁上、警示、信箱數)立刻寫;沒變就每 HEARTBEAT_WRITE_S 秒寫一次當心跳。看 outbox 仍每 0.1 秒。
                _p = status_payload("alive")
                _now = time.time()
                if _p != _hb["last"] or _now - _hb["at"] >= HEARTBEAT_WRITE_S:
                    write_status(root, _p)
                    _hb["last"], _hb["at"] = _p, _now
            except Exception:
                pass
            try:
                maybe_reexec()
            except Exception:
                pass
            if _ready["bad"] and time.time() - _ready["at"] >= READY_RECHECK_S:
                check_ready(say_it=False)   # P2:還沒就緒時定時再查,修好了自己拿掉 ⚠
            try:
                for p in sorted((root / "outbox").glob("*.json")):
                    if p.name in seen_out:
                        continue
                    seen_out.add(p.name)
                    body = {}
                    try:
                        body = json.loads(p.read_text(encoding="utf-8"))
                        text = body.get("text") or body.get("body") or ""
                    except FileNotFoundError:
                        continue   # 10-06:musebot 排水器剛把它搬走(送出中/已送),不是壞檔
                    except Exception:
                        text = p.name
                    # 對面寫的字會印在人的終端機上 ⇒ 濾控制字元(ESC 等),只留換行與 tab
                    text = OUTBOX_CTRL_RE.sub("", str(text))
                    _key = ((body.get("role") if isinstance(body, dict) else ""), text)
                    if _key == _last_shown["key"] and time.time() - _last_shown["at"] < 5.0:
                        continue   # 重複的那一份不畫(檔還在,信箱/紀錄不動;只是畫面不重複)
                    _last_shown["key"], _last_shown["at"] = _key, time.time()
                    _bridge_distill(root, session_uuid, args.provider_id, body)
                    # 即時重畫；input 內的編輯緩衝仍由 readline 保留。
                    _kind, _label = outbox_card_args(body, args.provider_id, human_display_name())
                    ui.message(_kind, _label, text, seat=_seat_tail(session_uuid) if _kind == args.provider_id or _kind == "system" else "")
                    _ship = _bridge_ship(session_uuid, args.provider_id, body)
                    if _ship:
                        _sk, _sl = outbox_card_args({"role": "user", "source": "system"}, args.provider_id, "")
                        ui.message(_sk, _sl, _ship, seat=_seat_tail(session_uuid))
            except Exception:
                pass
            time.sleep(0.1)

    thr = threading.Thread(target=heartbeat, name="sand-bridge-hb", daemon=True)
    thr.start()

    mirror = None
    if args.provider_id == "grokbot":
        import grokbot_mirror
        # 10-11 a seat:鏡射物件留在手上,席上 /bind、/unbind 換它盯的 agent
        mirror = grokbot_mirror.Mirror(human_label=human_display_name(), pin_agent=agent or None)
        grokbot_mirror.start_thread(ui.say, stop, human_label=human_display_name(), pin_agent=agent or None, mirror=mirror)

    dm_server = None
    if args.provider_id in SAND_BOT_PROVIDERS:
        try:
            dm_server = start_dm_socket(session_uuid, ui.say, args.provider_id)
        except OSError as exc:
            ui.say(f"{args.provider_id}: dm socket 開不起來({type(exc).__name__}: {str(exc)[:200]});bot 發的 dm 會直送、抬頭 unattested")

    live_stop = {"flag": False}   # 10-07 a seat:這一條綁定自己的停止旗標(/unbind 只停它,不停橋)

    def start_live(sid: str):
        # 10-07:ClaudeBot 即時體驗 —— 狀態列、已排隊/已讀、門鈴過期叫醒(claudebot_live.py)。開席與 /bind 共用。
        live_stop["flag"] = False
        try:
            import claudebot_live as _live
            import claudebot_spawn as _cbs2
            tracker = _live.LiveTracker(root, sid, ui.say, ui.set_status,
                                        on_health=lambda h: _live_health.__setitem__("override", h),
                                        spawn_record=_cbs2.record_of(sid))
            # 10-09 a seat(第 26 件):開席/換綁前就堆著的信也要等,橋重開不得把「門鈴過期」歸零
            try:
                import claudebot_mailbox as _cbm2
                tracker.seed_backlog(_cbm2.waking_files(root))
            except Exception:  # noqa: BLE001 —— 回補失敗只是少一次警示,不能讓狀態列起不來
                pass
            _live.start_thread(tracker, _EitherStop(stop, live_stop))
            return tracker
        except Exception as exc:  # noqa: BLE001 —— 狀態列起不來不能讓橋停,但要說
            ui.say(f"claudebot: 即時狀態起不來({type(exc).__name__}),只是看不到 Claude 在做什麼,收發照常", redraw=False)
            return None

    live_tracker = start_live(agent) if (cap.live_status and agent) else None
    read_marks = None
    if cap.read_marks_stale_s > 0:
        # 10-07 a seat:grokbot/musebot 的「✓ 已讀走」與「⚠沒來收信」(bridge_readmarks;claudebot 由 claudebot_live 管)
        try:
            import bridge_readmarks as _rm
            read_marks = _rm.ReadMarks(title_for(args.provider_id), ui.say,
                                       lambda h: set_health_for(_rm.STALE_KIND, h), cap.read_marks_stale_s,
                                       {"webhook": "門鈴可能沒叫醒它", "schedule": "排程可能停了"}.get(cap.wake, ""))
            # 10-09 a seat(D9):開席前就堆著的信也要算,重開不得把「沒來收信」歸零。
            read_marks.seed_backlog(root / "inbox")
            _rm.start_thread(read_marks, stop)
        except Exception as exc:  # noqa: BLE001
            read_marks = None
            ui.say(f"{title_for(args.provider_id)}: 已讀標記起不來({type(exc).__name__}),收發照常", redraw=False)

    if cap.outbox_drainer:
        # 10-06(the maintainer 令 root a seat 全權):Muse 對這台 Mac 只有檔案讀寫、沒有 shell ⇒ 它寄 dm 的唯一方式是
        #   往自己的 outbox 寫一個 to=艙位碼/席碼 的檔。這裡在**橋自己的行程**裡起排水執行緒,用與 dm socket 同一條
        #   dm_runner 送 ⇒ dm-ctl 從 pane 祖先鏈認出 @本席碼(強態)。不走 cron、不自報 session(那只會得到「(自稱)」)。
        try:
            import musebot_outbox_drainer as _drain
            _drain.start_thread(root, dm_runner, ui.say, stop)
            ui.say("musebot: outbox 排水器已起(to=艙位碼/席碼 的信由本席送出,抬頭 @本席碼)", redraw=False)
        except Exception as exc:  # noqa: BLE001 —— 排水器起不來不能讓橋停,但要說
            ui.say(f"musebot: outbox 排水器起不來({type(exc).__name__}:{str(exc)[:80]});to=別席 的信會留在 outbox 不送")

    wake_threads: list[threading.Thread] = []
    last_ring = {"ts": None}
    last_wake_line = {"text": None}

    def wake_and_report(text: str, trigger: str) -> None:
        result = wake_cursor_chat(session_uuid, short_code(session_uuid), text, args.provider_id)
        _wake_log(result, session_uuid, trigger)
        line = wake_line(result, args.provider_id)
        # 10-07 a seat(各 bot 比對):門鈴失敗以前只印在席上,右欄照樣全綠(a seat 一直「沒設」)⇒ 標進 health
        if result.get("ok") is False:
            set_health_for("wake-failed", {"health": "degraded", "last_error_kind": "wake-failed",
                                           "last_error": line or "門鈴失敗(%s)" % (result.get("reason") or result.get("status"))})
        elif result.get("ok"):
            set_health_for("wake-failed", None)
        # 同一句實話連續出現只印一次(沒設定時每則都印會洗版);狀態一變就再印。
        if line != last_wake_line["text"]:
            last_wake_line["text"] = line
            if line:
                ui.say(line)

    rename_on = cap.rename_in_app and (os.environ.get("AIB_GROKBOT_RENAME") or "1") != "0"

    def ask_app_rename(release: bool = False):
        """the maintainer 09-25:綁定後把席碼寫回 app 裡 agent 的名稱(例 `AIB ( @xxxxx )`)。
        沒有 token 改不了名 ⇒ 請 bot 自己用 update_state(target: profile) 改(09-25 真機問過它有這個工具)。
        走的是同一個信箱 + 門鈴:放一則系統訊息進 inbox,bot 收信時照做。
        release=True(10-11 a seat,席上 /unbind):請它改回「( 未綁定AIB席位 )」,門鈴同步響(呼叫端還綁著舊 agent),
        回那封信的路徑讓呼叫端等它被收走;沒寄回 None。"""
        import bot_seat_name
        import grokbot_threads
        try:
            current = {r["id"]: r["name"] for r in grokbot_threads.list_agents(10 ** 6)}.get(agent)
        except (grokbot_threads.RosterSchemaChanged, grokbot_threads.RosterUnreadable) as exc:
            ui.say(f"grokbot: 名冊讀不到({exc}),沒請 bot 改名")
            return None
        if current is None:
            return None
        code = short_code(session_uuid)
        wanted = bot_seat_name.with_unbound(current) if release else bot_seat_name.with_seat_tag(current, code)
        if current == wanted:
            ui.say(f"grokbot: app 裡的名稱已經是「{wanted}」")
            return None
        lead = f"這一席 @{code} 要解除綁定你了" if release else f"這一席 @{code} 已綁定你"
        path = enqueue(root, "inbox",
                       f"[AIB 系統] {lead}。請用 update_state(target: profile) "
                       f"把你在 Grok Bot app 裡的名稱改成「{wanted}」。改好不用回覆。",
                       session_uuid, args.provider_id, "aib-system")
        last_ring["ts"] = time.time()
        if release:
            wake_and_report("[AIB 系統] 解除綁定改名", "system")
        else:
            rt = threading.Thread(target=wake_and_report, args=("[AIB 系統] 改名", "system"), daemon=True)
            rt.start()
            wake_threads.append(rt)
        ui.say(f"grokbot: 已請 bot 把 app 裡的名稱改成「{wanted}」(bot 下次收信時改)")
        return path

    if agent and rename_on:
        ask_app_rename()

    if (agent or cap.mail_without_bind) and args.provider_id in SAND_BOT_PROVIDERS:
        # the maintainer 09-30 19:5x:橋接席開席也要收到人格包(與基因);沒有系統說明可塞 ⇒ 當第一則 aib-system 信放進 inbox。
        # 10-06:musebot 沒綁 Muse 對話也開(排程喚醒制,信箱照常運作、通行碼照發)⇒ 人格包也照送,不然這一席永遠收不到人格。
        #   續綁在橋起來之後才跑 ⇒ 背景等綁定(bridge_persona.WAIT_S);每一席只送一次(bridge 目錄裡的記號檔)。
        start_pack_delivery(root, session_uuid, args.provider_id, ui.say, wake_and_report, last_ring,
                            wake_threads, stop)

    bind_rows: list[dict] = []   # /bind 上一次列出的清單(/bind <編號> 照它挑)
    while not stop["flag"]:
        try:
            if ui.enabled:
                ui.redraw()
            line = input("" if ui.enabled else "❯ ")
            if ui.enabled:
                # input 吃掉底行後重畫一次，避免殘字
                ui.redraw()
        except (EOFError, OSError):
            break   # EOF 或終端機已經不在(關窗後 pty 讀不到)⇒ 走收尾
        except KeyboardInterrupt:
            ui.say("")
            break
        text = line.strip()
        if not text:
            continue
        slash = _aib_dm.parse_dm_slash(text)
        if slash:
            target, body = slash
            outcome = _aib_dm.run_aib_dm(target, body)
            ui.say(outcome)
            continue
        if text in ("/quit", "/exit", ":q"):
            break
        if args.provider_id == "grokbot" and text == "/connect":
            # 10-11 a seat(P4):席上接門鈴,不用另開終端機打 aib bot connect。對話框最多等 10 分鐘 ⇒ 放背景,席上照常打字;
            #   接好就重查就緒(P2),右欄「⚠沒接門鈴」當場拿掉。
            if not agent:
                ui.say("grokbot: 先打 /bind 綁一個 agent,再 /connect 接門鈴")
                continue

            def _do_connect(aid: str = agent) -> None:
                try:
                    r = subprocess.run([sys.executable, str(Path(__file__).resolve().with_name("grokbot_threads.py")),
                                        "connect", "--agent", aid],
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    ui.say(f"grokbot: 接門鈴沒跑完({type(exc).__name__})")
                    return
                if r.returncode == 0:
                    ui.say("grokbot: 門鈴接好了(只驗設定,沒有試打)")
                    check_ready()
                else:
                    last = (r.stderr or "").strip().splitlines()
                    ui.say("grokbot: 門鈴沒接上:" + (last[-1][:200] if last else "rc=%s" % r.returncode))

            threading.Thread(target=_do_connect, name="grokbot-connect", daemon=True).start()
            ui.say("grokbot: 已跳視窗讓你貼「POST 到」與「金鑰」(請 bot 建例行任務那句已放剪貼簿);席上照常可以打字")
            continue
        # 10-11 a seat(P1):GrokBot、MuseBot 席上 /unbind、/bind。ClaudeBot 走下面原本那兩段(不動)。
        if cap.bind_in_seat and not is_claude and text == "/unbind":
            pid = args.provider_id
            if not agent:
                ui.say(f"{pid}: 這一席本來就沒綁{cap.bind_what};打 /bind 綁一條")
                continue
            old = agent
            if rename_on:
                # 席還綁著舊 agent:先請它把 app 名稱改回未綁定,等它收走那封信再放手 ——
                #   bot 收信靠 status.json 的 agent_id 找席,放手之後它就找不到這一席、那封信永遠沒人收。
                asked = ask_app_rename(release=True)
                if asked is not None:
                    try:
                        wait_s = max(0.0, float(os.environ.get("AIB_GROKBOT_CLOSE_WAIT_S") or 45.0))
                    except ValueError:
                        wait_s = 45.0
                    ui.say(f"grokbot: 等 bot 收走改名信(最多 {int(wait_s)} 秒;同關席收尾)…")
                    claimed = asked.parent / "claimed" / asked.name
                    deadline = time.time() + wait_s
                    while time.time() < deadline and not claimed.exists():
                        time.sleep(0.2)
                    if not claimed.exists():
                        ui.say("grokbot: bot 沒來收改名信;app 裡的名稱可能還帶 @席碼,要在 app 手動改回")
            if closer is not None:
                closer.done()              # 改回未綁定這件事席上已做(或已請過),關席小幫手不用再做一次
                closer = None
            agent = ""                     # status_payload 是閉包:下一次心跳 agent_id=None
            os.environ[bound_env(pid)] = ""   # 門鈴讀環境變數 ⇒ 之後不再叫醒舊 agent
            if mirror is not None:
                mirror.pin_agent = None    # 跟開席沒綁時一樣:看 app 目前選中的那條
                mirror.last_full = None
            if agent_lock is not None:
                try:
                    agent_lock.close()     # 放掉鎖,舊的那條可以給別席綁
                except OSError:
                    pass
                agent_lock = None
            write_mail_token(root)         # 換通行碼:舊的那條手上那組不能再回信到這一席
            save_bind_override(root, launch_agent, "")
            check_ready(say_it=False)      # P2:解綁 ⇒ 拿掉就緒警示(沒綁另有「⚠沒綁對話」)
            try:
                import bridge_persona as _bp
                _mark = root / _bp.SENT_MARK
                if _mark.exists():
                    _mark.replace(root / (_bp.SENT_MARK + ".prev-" + old[:8]))
            except Exception:  # noqa: BLE001
                pass
            write_status(root, status_payload("alive"))
            _left = len(list((root / "inbox").glob("*.json")))
            ui.say(f"{pid}: 已解除綁定{cap.bind_what} {old[:8]}(席沒關);打 /bind 綁新的"
                   + (f"。信箱還有 {_left} 則沒被收,綁上新的後會給新的那條" if _left else ""))
            continue
        if cap.bind_in_seat and not is_claude and (text == "/bind" or text.startswith("/bind ")):
            pid = args.provider_id
            arg = text[len("/bind"):].strip()
            if agent:
                ui.say(f"{pid}: 這一席已綁{cap.bind_what} {agent[:8]};要換,先打 /unbind 再 /bind")
                continue
            if not arg:
                try:
                    bind_rows = other_bind_candidates(pid)
                except Exception as exc:  # noqa: BLE001
                    ui.say(f"{pid}: 清單讀不到({type(exc).__name__}: {str(exc)[:120]});可以直接打 /bind <完整 id>")
                    continue
                for ln in render_other_bind_list(pid, cap.bind_what, bind_rows):
                    ui.say(ln)
                continue
            chosen, err = resolve_other_bind_arg(pid, arg, bind_rows)
            if not chosen:
                ui.say(f"{pid}: {err}")
                continue
            new_lock, err, _rc = claim_bound_agent(chosen, pid)
            if new_lock is None:
                ui.say(f"{pid}: 綁不上:{err}")
                continue
            agent_lock = new_lock
            write_mail_token(root)
            agent = chosen                 # 下一次心跳起 status.json agent_id 就是它(bot 收信靠它找席)
            os.environ[bound_env(pid)] = chosen   # 門鈴(load_wake_config)叫這個 agent
            write_status(root, status_payload("alive"))
            save_bind_override(root, launch_agent, agent)   # 橋重啟後沿用這次選的
            if mirror is not None:
                mirror.pin_agent = agent
                mirror.last_full = None
            if rename_on:
                if closer is None:
                    import bot_closer
                    closer = bot_closer.spawn("grokbot", {"agent_id": agent, "session_id": session_uuid}, state_root())
                    if closer is None:
                        ui.say("grokbot: 收尾小幫手起不來;關席時 app 名稱不會改回未綁定")
                ask_app_rename()
            if pid == "grokbot":
                import grokbot_threads as _gt6
                if not _gt6.read_agent_wake(agent, state_root())[0]:
                    ui.say(f"grokbot: 這個 agent 還沒接門鈴(webhook),bot 收不到叫醒;"
                           f"接法:打 /connect(或 aib bot connect grokbot --agent {agent}),接好就生效,不用重開")
            start_pack_delivery(root, session_uuid, pid, ui.say, wake_and_report,
                                last_ring, wake_threads, stop)
            ui.say(f"{pid}: 已綁{cap.bind_what} {agent}(席不用重開)")
            check_ready()                  # P2:綁上就查一次就緒
            continue
        if cap.bind_in_seat and text == "/unbind":
            # 10-07 a seat(the maintainer 令「換綁」):解掉現在這條,席不關。之後 /bind 綁新的。
            if not agent:
                ui.say("claudebot: 這一席本來就沒綁對話;打 /bind 綁一條")
                continue
            old = agent
            agent = ""                 # status_payload 是閉包:下一次心跳 agent_id=None ⇒ hook 找不到這一席、右欄 ⚠沒綁對話
            live_stop["flag"] = True   # 停掉舊對話的狀態列與門鈴過期叫醒
            live_tracker = None
            _live_health["override"] = None
            ui.set_status("")
            if agent_lock is not None:
                try:
                    agent_lock.close()    # 放掉「一條對話只准一隻席」的鎖,舊對話可以給別席綁
                except OSError:
                    pass
                agent_lock = None
            write_mail_token(root)     # 換通行碼:舊對話手上那組不能再回信到這一席
            # 10-09 a seat:舊對話的標題改回「( 未綁定AIB席位 )」(席還活著,當場做);舊對話的收尾小幫手不用再動
            if (os.environ.get("AIB_CLAUDEBOT_RENAME") or "1") != "0":
                try:
                    import claudebot_hook as _cbh
                    _cbh.release_title(old, root)
                except Exception:  # noqa: BLE001
                    pass
            if closer is not None:
                closer.done()
                closer = None
            sync_seat_claude_session(session_uuid, "")   # 10-09:席位檔那一欄也清掉(待修清單第 11 件)
            save_bind_override(root, launch_agent, "")    # 10-09:橋重啟後維持不綁
            check_ready(say_it=False)      # P2:解綁 ⇒ 拿掉就緒警示
            try:
                import bridge_persona as _bp
                _mark = root / _bp.SENT_MARK
                if _mark.exists():     # 人格包「送過了」是對舊對話說的;留底改名,下一次 /bind 會對新對話再送一次
                    _mark.replace(root / (_bp.SENT_MARK + ".prev-" + old[:8]))
            except Exception:  # noqa: BLE001
                pass
            write_status(root, status_payload("alive"))
            _left = len(list((root / "inbox").glob("*.json")))
            ui.say(f"claudebot: 已解除綁定 Claude 對話 {old[:8]}(席沒關);打 /bind 綁新的"
                   + (f"。信箱還有 {_left} 則沒被收,綁上新對話後會給新的那條" if _left else ""))
            continue
        if cap.bind_in_seat and (text == "/bind" or text.startswith("/bind ")):
            arg = text[len("/bind"):].strip()
            if agent:
                ui.say(f"claudebot: 這一席已綁 Claude 對話 {agent[:8]};要換,先打 /unbind 再 /bind")
                continue
            if not arg:
                try:
                    bind_rows = bind_candidates()
                except Exception as exc:  # noqa: BLE001
                    ui.say(f"claudebot: 清單讀不到({type(exc).__name__});可以直接打 /bind <完整 uuid> 或 /bind new")
                    continue
                for ln in render_bind_list(bind_rows):
                    ui.say(ln)
                continue
            spawned = False
            if arg.lower() == "new":
                try:
                    import claudebot_spawn as _cbs3
                    ui.say("claudebot: AIB 正在代開一條本機 claude 對話(最多等十幾秒)…")
                    want, err = _cbs3.start(args.cwd)
                except Exception as exc:  # noqa: BLE001
                    want, err = "", type(exc).__name__
                if not want:
                    ui.say(f"claudebot: 代開失敗:{err}")
                    continue
                spawned = True
            else:
                confirmed = arg.endswith("!")
                want, err = resolve_bind_arg(arg.rstrip("!").strip(), bind_rows)
                if not want:
                    ui.say(f"claudebot: {err}")
                    continue
                # 10-07 a seat:不問就動手的對話,沒加 ! 不綁(別席 dm 叫醒它就能直接改檔跑指令)
                import claudebot_threads as _ct4
                _mode = _ct4.permission_mode(want)
                if _ct4.permission_risk(_mode) == "danger" and not confirmed:
                    ui.say("claudebot: 沒綁。" + permission_note(_mode) + f";確定要綁就打 /bind {arg.rstrip('!').strip()}!")
                    continue
                _note = permission_note(_mode)
                if _note:
                    ui.say("claudebot: " + _note)
            new_lock, err, _rc = claim_bound_agent(want, args.provider_id)
            if new_lock is None:
                ui.say(f"claudebot: 綁不上:{err}")
                continue
            agent_lock = new_lock
            write_mail_token(root)
            agent = want              # status_payload 是閉包,下一次心跳起 agent_id 就是它(hook 靠這一欄找席)、⚠沒綁對話 消失
            write_status(root, status_payload("alive"))
            live_tracker = start_live(agent)
            start_pack_delivery(root, session_uuid, args.provider_id, ui.say, wake_and_report,
                                last_ring, wake_threads, stop)   # 與開席同一條(那一行是人格包測試 Z1 的刀錨,這裡刻意換行)
            sync_seat_claude_session(session_uuid, agent)   # 10-09:席位檔那一欄對上新綁的(待修清單第 11 件)
            save_bind_override(root, launch_agent, agent)   # 10-09:橋重啟後沿用這次選的
            if closer is None:
                closer = _spawn_claude_closer(agent, session_uuid)   # 10-09:席上綁的也要有關席收尾
            ui.say(f"claudebot: 已綁 Claude 對話 {agent}(席不用重開)")
            check_ready()                  # P2:綁上就查一次就緒
            if spawned:
                _cbs3.spawn_kicker(agent)   # 背景等綁好再替你送第一句(門鈴才會掛上),與一鍵開席同一條
                ui.say(f"claudebot: 要進去打字:{_cbs3.attach_command(agent)}(離開按 Ctrl-b d)")
            continue
        if text in ("/status", "/redraw"):
            ui.say(
                f"inbox={status_payload('alive')['inbox']} "
                f"outbox={status_payload('alive')['outbox']} bridge={root}"
            )
            continue

        from_dm = is_dm_line(text)
        # 10-07:貼進來的別席私訊標成別席(看抬頭,不看內文自稱),不再標成你本人
        if from_dm:
            ui.message("dm", _speaker_label(text), text)
        else:
            ui.message("human", human_display_name(), text)
        _inbox_path = enqueue(root, "inbox", text, session_uuid, args.provider_id, "human")
        if cap.wake == "hook":
            # 沒有 webhook:門鈴在 Claude 那邊(claudebot_hook.py doorbell 盯著本席 inbox)
            import claudebot_mailbox as _cbm
            _origin = (_cbm.ORIGIN_SEAT if dm_sender_is_seat(text) else _cbm.ORIGIN_DM) if from_dm else _cbm.ORIGIN_HUMAN
            if agent and not _cbm.worth_waking(_origin):
                # 10-07 a seat:hook 的門鈴本來就不為這種 dm 叫醒 Claude;以前仍交給 claudebot_live 等「已讀」,
                #   等不到就當門鈴過期、自己送字叫醒 —— 等於讓未驗證的 dm 叫醒了 Claude,還把席標成 ⚠門鈴過期。
                ui.say("claudebot: 這則 dm 不叫醒 Claude(寄件人未驗證或 dm 叫醒關著);Claude 下一輪會一起收到")
            elif agent:
                # 10-07:Claude 正忙 ⇒「已排隊」;被取走時 claudebot_live 會印「✓ 已讀走」
                ui.say(live_tracker.note_sent(_inbox_path, text) if live_tracker else "claudebot> …")
            else:
                ui.say("claudebot: 這一席沒綁 Claude 對話,這則只進了信箱")
        elif cap.wake == "schedule":
            # 10-07 a seat:musebot 以前掉進下面 grokbot 的 webhook 那條(印「沒叫醒——wake 沒設定」)。排程制沒有門鈴可按。
            ui.say(read_marks.note(_inbox_path, text) + "(排程制,不按門鈴)" if read_marks else
                   f"{title_for(args.provider_id)}> 已放進信箱;Muse 下次排程來拉信時會收到(不按門鈴)")
        elif from_dm and not wake_for_dm(text):
            _wake_log({"ok": None, "status": None,
                       "reason": "dm 不叫醒(%s)" % ("開關關著" if wake_on_dm_mode() == WAKE_DM_OFF
                                                 else "寄件人未驗證"), "ms": 0},
                      session_uuid, "dm")
        elif args.provider_id == "grokbot" and not should_ring(root, last_ring["ts"], time.time()):
            _wake_log({"ok": None, "status": None, "reason": "合併:上一次門鈴那一輪還沒來收信", "ms": 0},
                      session_uuid, "dm" if from_dm else "human")
            if read_marks:
                ui.say(read_marks.note(_inbox_path, text))
        else:
            last_ring["ts"] = time.time()
            # 先推醒(不等 HTTP 完成顯示);結果由 wake_and_report 印一行實話
            t = threading.Thread(target=wake_and_report,
                                 args=(text, "dm" if from_dm else "human"), daemon=True)
            t.start()
            wake_threads.append(t)
            ui.say(read_marks.note(_inbox_path, text) if read_marks else f"{title_for(args.provider_id)}> …")
        local = try_local_reply(text, args.provider_id, args.cwd)
        if local:
            enqueue(root, "outbox", local, session_uuid, args.provider_id, "assistant")
            ui.message(args.provider_id, title_for(args.provider_id), local, seat=_seat_tail(session_uuid))

    stop["flag"] = True
    # 關席把 app 名稱改回「( 未綁定AIB席位 )」一律交給收尾小幫手(bot_closer.py):
    #   席在自己快死時做收尾不可靠(09-25 真機:tmux 裡 SIGHUP ⇒ 收尾沒跑)。本行程一結束,管子斷,小幫手接手。
    for t in wake_threads:
        t.join(timeout=WAKE_TIMEOUT_S + 1.0)
    stop_dm_socket(dm_server, session_uuid, args.provider_id)
    write_status(root, status_payload("stopped"))
    ui.say("sand-bridge: stopped")
    return 0



if __name__ == "__main__":
    sys.exit(main())
