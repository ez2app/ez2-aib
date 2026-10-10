#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ClaudeBot 席的即時對話體驗(the maintainer 2026-10-07 03:4x 令,選 1、2、3 + 上一份 1、2 合成一包)。

橋(sand_bridge,provider=claudebot)起一條執行緒跑 LiveTracker,每秒看一次:
  1. Claude 現在在做什麼 —— 讀綁定對話的 transcript 尾巴(Claude Code 自己寫的,不用 Claude 配合):
       思考中 / 正在跑工具 X / 等你批准(只有 AIB 代開的對話看得到:讀 tmux 畫面)/ 閒著。
     狀態列只改席位畫面的一行,不整頁重畫(打字不閃)。
  2. 你在席上打的字去哪了 —— 打完若 Claude 正忙,席上說「已排隊」;那則信被 hook 從 inbox 取走時說「✓ Claude 已讀走」。
  3. 門鈴過期也叫得醒(上一份第 2 條)—— 門鈴最長等 55 分鐘(claudebot_hook.DOORBELL_S),過了席上打字就叫不醒。
     信在 inbox 等超過 WAKE_AFTER_S、Claude 閒著、門鈴行程不在:
       AIB 代開的對話(claudebot_spawn 有對照檔)⇒ 對它的 tmux 送一句「[AIB] 信箱有新信」叫醒(prompt hook 會把信附上);
       你自己開的對話 ⇒ 叫不到,席上說一次並把 health 標成 degraded/doorbell-expired(右欄亮 ⚠門鈴過期)。

判讀全是「量得到的事實」;量不到就說量不到,不猜:找不到 transcript ⇒ 狀態列寫「讀不到對話紀錄」。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

POLL_S = 1.0
TAIL_BYTES = 256 * 1024
WAKE_AFTER_S = 8.0           # 信在 inbox 等這麼久還沒被取走,才算「門鈴沒在接」
WAKE_COOLDOWN_S = 60.0       # 同一席叫醒的間隔
STALE_S = 600.0              # transcript 這麼久沒長、又停在回合中間 ⇒ 不再說「思考中」(可能當掉),改說量不到
IDLE_SETTLE_S = 3.0          # 最後一段是文字回覆、而且這麼久沒再長 ⇒ 閒著
DOORBELL_FILE = "claude-doorbell"   # 與 claudebot_hook.DOORBELL_FILE 同值
WAKE_TEXT = "[AIB] 信箱有新信(門鈴已過期,AIB 代為叫醒)。請收信並照信裡的規則處理。"
INTERRUPTED_MARK = "[Request interrupted by user"
WORKING_MARK = "esc to interrupt"       # Claude Code 正在跑時畫面底部那一行
APPROVAL_MARKS = ("Do you want to", "Do you want to proceed", "❯ 1. Yes", "Allow this")

STATE_IDLE = "idle"
STATE_THINKING = "thinking"
STATE_TOOL = "tool"
STATE_APPROVAL = "approval"
STATE_UNKNOWN = "unknown"


def transcript_for(sid: str, projects_dir: Path | None = None) -> Path | None:
    try:
        import claudebot_threads as ct
        root = projects_dir or ct.projects_dir()
        hits = sorted(root.glob("*/%s.jsonl" % sid), key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0] if hits else None
    except (OSError, ImportError):
        return None


def _tail_entries(path: Path) -> list[dict]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()
            raw = fh.read()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") in ("user", "assistant"):
            out.append(d)
    return out


def _blocks(entry: dict) -> list:
    c = (entry.get("message") or {}).get("content")
    return c if isinstance(c, list) else ([{"type": "text", "text": c}] if isinstance(c, str) else [])


def _ts(entry: dict) -> float | None:
    t = entry.get("timestamp")
    if not isinstance(t, str):
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(entries: list[dict], mtime: float, now: float) -> tuple[str, str, float | None]:
    """回 (狀態, 細節, 這一輪開始的時刻)。純函式,尺直接餵。"""
    if not entries:
        return STATE_UNKNOWN, "讀不到對話紀錄", None
    turn_start = None
    for e in reversed(entries):
        if e.get("type") == "user" and not e.get("isMeta") and any(
                isinstance(b, dict) and b.get("type") == "text" for b in _blocks(e)):
            turn_start = _ts(e)
            break
    last = entries[-1]
    blocks = _blocks(last)
    if last.get("type") == "assistant":
        tools = [b.get("name") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        if tools:
            if now - mtime > STALE_S:
                return STATE_UNKNOWN, "%d 分鐘沒有動靜" % int((now - mtime) // 60), turn_start
            return STATE_TOOL, str(tools[-1] or "工具"), turn_start
        if now - mtime >= IDLE_SETTLE_S:
            return STATE_IDLE, "", turn_start
        return STATE_THINKING, "", turn_start
    # 10-07 真機:按 Esc 中斷(含拒絕批准)⇒ 最後一筆是 user「[Request interrupted by user…]」,這一輪已經結束、Claude 閒著。
    #   而且中斷不會觸發 Stop ⇒ 門鈴沒重新掛上;不判成閒著的話,叫醒那一段永遠不會跑,信就卡在信箱(實測卡了 90 秒)。
    if any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").startswith(INTERRUPTED_MARK)
           for b in blocks):
        return STATE_IDLE, "", turn_start
    # 最後一筆是 user(真人那句或工具結果)⇒ 輪到 Claude
    if now - mtime > STALE_S:
        return STATE_UNKNOWN, "%d 分鐘沒有動靜" % int((now - mtime) // 60), turn_start
    return STATE_THINKING, "", turn_start


def status_text(state: str, detail: str, turn_start: float | None, now: float) -> str:
    """狀態列的字。秒數取 5 秒一格,狀態列才不會每秒重寫。"""
    secs = ""
    if turn_start and state in (STATE_THINKING, STATE_TOOL, STATE_APPROVAL):
        el = max(0, int(now - turn_start))
        secs = " %d 秒" % (el - el % 5 if el >= 10 else el)
    if state == STATE_THINKING:
        return "⏳ Claude 思考中" + secs
    if state == STATE_TOOL:
        return "⚙ Claude 正在跑工具 %s%s" % (detail, secs)
    if state == STATE_APPROVAL:
        return "⚠ Claude 在等你批准(進對話按一下才會繼續)"
    if state == STATE_IDLE:
        return ""
    return "· Claude 狀態量不到(%s)" % detail if detail else "· Claude 狀態量不到"


def doorbell_alive(seat_dir: Path) -> bool:
    try:
        pid = int((seat_dir / DOORBELL_FILE).read_text(encoding="utf-8").split()[0])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


class LiveTracker:
    """橋端的一條執行緒。say(text) 印一行;set_status(text) 改狀態列;on_health(dict|None) 改 status.json 的健康欄。"""

    def __init__(self, root: Path, sid: str, say, set_status, on_health=None, projects_dir: Path | None = None,
                 spawn_record=None, tmux_bin: str | None = None):
        self.root, self.sid = Path(root), sid
        self.say, self.set_status, self.on_health = say, set_status, on_health or (lambda _h: None)
        self.projects_dir = projects_dir
        self.spawn_record = spawn_record          # claudebot_spawn.record_of(sid);None = 不是 AIB 代開的
        self.tmux_bin = tmux_bin
        self.pending: list[tuple[Path, float, str]] = []   # (inbox 檔, 進信箱時刻, 開頭幾個字)
        self.state, self.detail, self.turn_start = STATE_UNKNOWN, "", None
        self._last_status = None
        self._last_wake = 0.0
        self._warned_expired = False
        self._lock = threading.Lock()

    # ── 給橋呼叫 ──────────────────────────────────────
    def note_sent(self, path: Path, text: str, now: float | None = None) -> str:
        """席上打的一句已放進 inbox。回要印的那一行(已排隊 / …)。"""
        now = time.time() if now is None else now
        with self._lock:
            self.pending.append((Path(path), now, " ".join(str(text).split())[:16]))
            n = len(self.pending)
        if self.state in (STATE_THINKING, STATE_TOOL, STATE_APPROVAL):
            return "↳ 已排隊:Claude 這一輪結束後讀(待讀 %d 則)" % n
        return "claudebot> …"

    def seed_backlog(self, files) -> int:
        """10-09 a seat(第 26 件):開席或 /bind **之前**就躺在 inbox 的信也算進來,以檔案 mtime 起算。

        以前 pending 只記本行程 note_sent 過的信 ⇒ 橋一重開就歸零:10-09 18:46 a seat 重開時信箱有 2 封
        (16:19 開席包、18:40「你在嗎」)沒人收,health 仍是 ok、席上沒說門鈴過期。files 由呼叫端用
        claudebot_mailbox.waking_files 篩過(門鈴本來就不為它叫醒的 dm 不算)。回補進來的封數。"""
        n = 0
        with self._lock:
            known = {str(p) for p, _t, _h in self.pending}
            for f in files:
                f = Path(f)
                if str(f) in known:
                    continue
                try:
                    t = f.stat().st_mtime
                except OSError:
                    continue
                self.pending.append((f, t, "(開席前就在)"))
                n += 1
        return n

    # ── 一拍 ──────────────────────────────────────────
    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._observe(now)
        self._check_read()
        self._check_doorbell(now)
        text = status_text(self.state, self.detail, self.turn_start, now)
        if text != self._last_status:
            self._last_status = text
            self.set_status(text)

    def _observe(self, now: float) -> None:
        tp = transcript_for(self.sid, self.projects_dir)
        if tp is None:
            self.state, self.detail, self.turn_start = STATE_UNKNOWN, "讀不到對話紀錄", None
            return
        try:
            mtime = tp.stat().st_mtime
        except OSError:
            mtime = 0.0
        state, detail, start = classify(_tail_entries(tp), mtime, now)
        if state != STATE_IDLE and self.spawn_record:
            pane = self._pane_tail()
            if any(m in pane for m in APPROVAL_MARKS):
                state, detail = STATE_APPROVAL, ""
            elif pane and "❯" in pane and WORKING_MARK not in pane.lower():
                # 第二來源(只有代開的對話有):畫面沒有「esc to interrupt」、輸入框在 ⇒ 閒著。
                #   防紀錄判讀漏掉的收尾形狀(中斷、錯誤…)讓信卡在信箱。
                state, detail = STATE_IDLE, ""
        self.state, self.detail, self.turn_start = state, detail, start

    def _pane_tail(self) -> str:
        rec = self.spawn_record or {}
        try:
            r = subprocess.run([self.tmux_bin or "tmux", "-L", str(rec.get("tmux_server") or "claudebot"), "capture-pane",
                                "-p", "-t", str(rec.get("tmux_session") or "")], capture_output=True, text=True,
                               timeout=3, stdin=subprocess.DEVNULL, env=_clean_env())
        except (OSError, subprocess.TimeoutExpired):
            return ""
        # 窗格底部常有一長串空白行(真機 10-07 03:5x:45 行的窗只用了上半)⇒ 先去掉尾端空白行再取最後 15 行
        lines = (r.stdout or "").splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines[-15:])

    def _check_read(self) -> None:
        with self._lock:
            still, gone = [], []
            for item in self.pending:
                (still if item[0].exists() else gone).append(item)
            self.pending = still
        for _p, _t, head in gone:
            self.say("✓ Claude 已讀走:%s…" % head)
        if gone and self._warned_expired:
            self._warned_expired = False
            self.on_health(None)

    def _check_doorbell(self, now: float) -> None:
        with self._lock:
            waiting = [t for _p, t, _h in self.pending if now - t >= WAKE_AFTER_S]
        if not waiting or self.state != STATE_IDLE or doorbell_alive(self.root):
            return
        if self.spawn_record:
            if now - self._last_wake < WAKE_COOLDOWN_S:
                return
            self._last_wake = now
            if self._send_wake():
                self.say("AIB 系統> 門鈴已過期,已替你叫醒 Claude 收信")
            else:
                self.say("⚠ 門鈴已過期,代為叫醒失敗(tmux 送不出);進對話說一句它就會收信")
            return
        if not self._warned_expired:
            self._warned_expired = True
            self.say("⚠ 門鈴已過期(閒置超過 55 分鐘):這條對話不是 AIB 代開的,叫不醒;到 Claude 那邊說一句才會收信")
            self.on_health({"health": "degraded", "last_error_kind": "doorbell-expired",
                            "last_error": "門鈴已過期:Claude 那邊說一句才會再收信"})

    def _send_wake(self) -> bool:
        rec = self.spawn_record or {}
        base = [self.tmux_bin or "tmux", "-L", str(rec.get("tmux_server") or "claudebot")]
        name = str(rec.get("tmux_session") or "")
        try:
            a = subprocess.run(base + ["send-keys", "-t", name, "-l", WAKE_TEXT], capture_output=True, timeout=5,
                               stdin=subprocess.DEVNULL, env=_clean_env())
            time.sleep(0.3)
            b = subprocess.run(base + ["send-keys", "-t", name, "Enter"], capture_output=True, timeout=5,
                               stdin=subprocess.DEVNULL, env=_clean_env())
        except (OSError, subprocess.TimeoutExpired):
            return False
        return a.returncode == 0 and b.returncode == 0

    def run(self, stop: dict) -> None:
        while not stop.get("flag"):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 —— 狀態列壞了不能讓橋停
                pass
            time.sleep(POLL_S)


def _clean_env() -> dict:
    try:
        import claudebot_spawn
        return claudebot_spawn.clean_env()
    except ImportError:
        env = dict(os.environ)
        env.pop("TMUX", None)
        return env


def start_thread(tracker: LiveTracker, stop: dict) -> threading.Thread:
    t = threading.Thread(target=tracker.run, args=(stop,), name="claudebot-live", daemon=True)
    t.start()
    return t
