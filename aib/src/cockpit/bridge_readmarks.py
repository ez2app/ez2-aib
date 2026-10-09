#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""信箱橋的「已讀走」與「沒來收信」(10-07 各 bot 比對,root a seat)。

claudebot 有 claudebot_live(讀 transcript,知道 Claude 在做什麼);grokbot、musebot 以前什麼都沒有:
席上打完字只看到「grokbot> …」,不知道 bot 收了沒;bot 一直不來收,右欄也照樣全綠。
這裡只看一件與哪一家無關的事實:那封信還在不在 inbox(bot 收信 = 原子搬走)。
  · 信不見了 ⇒ 席上印「✓ 已讀走」與等了幾秒;
  · 最舊那封等超過 stale_after_s ⇒ 席上說一次,status.json health 標 degraded/not-collecting(右欄 ⚠沒來收信);
  · 全部收走 ⇒ 警示自己解除。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

POLL_S = 1.0
STALE_KIND = "not-collecting"


class ReadMarks:
    def __init__(self, label: str, say, set_health, stale_after_s: float, stale_why: str = ""):
        self.label = label
        self.say = say
        self.set_health = set_health          # set_health(dict | None);None = 解除(只解除自己標的)
        self.stale_after_s = float(stale_after_s)
        self.stale_why = stale_why
        self.pending: list[tuple[Path, float, str]] = []
        self.warned = False
        self._lock = threading.Lock()

    def note(self, path, text: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        with self._lock:
            self.pending.append((Path(path), now, " ".join(str(text).split())[:16]))
            n = len(self.pending)
        return "↳ 已放進信箱,等 %s 來收(待收 %d 則)" % (self.label, n)

    def seed_backlog(self, inbox) -> int:
        """10-09 a seat(D9):開席**之前**就躺在 inbox 的信也算進來,以那封檔案的 mtime 起算。

        以前 pending 只記本行程 note() 過的信 ⇒ 每重開一次就歸零:10-09 a seat 的信從 10-06 18:41 堆到
        10-09 02:3x(兩天半),health 仍是 ok,右欄沒有任何警示。回補進來的封數。"""
        n = 0
        try:
            files = sorted(Path(inbox).glob("*.json"))
        except OSError:
            return 0
        with self._lock:
            known = {str(p) for p, _t, _h in self.pending}
            for f in files:
                if not f.is_file() or str(f) in known:
                    continue
                try:
                    t = f.stat().st_mtime
                except OSError:
                    continue
                self.pending.append((f, t, "(開席前就在)"))
                n += 1
        return n

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            gone = [it for it in self.pending if not it[0].exists()]
            self.pending = [it for it in self.pending if it[0].exists()]
            oldest = min((t for _p, t, _h in self.pending), default=None)
        for _p, t, head in gone:
            self.say("✓ %s 已讀走「%s」(%d 秒)" % (self.label, head, int(now - t)))
        if self.warned and oldest is None:
            self.warned = False
            self.set_health(None)
            self.say("%s:信都收走了,警示解除" % self.label)
        elif not self.warned and oldest is not None and now - oldest >= self.stale_after_s:
            self.warned = True
            mins = int((now - oldest) // 60)
            msg = "%s 已經 %d 分鐘沒來收信%s" % (self.label, mins, ("(" + self.stale_why + ")") if self.stale_why else "")
            self.set_health({"health": "degraded", "last_error_kind": STALE_KIND, "last_error": msg})
            self.say("⚠ " + msg)


def start_thread(rm: ReadMarks, stop: dict) -> threading.Thread:
    def run():
        while not stop.get("flag"):
            try:
                rm.tick()
            except Exception:  # noqa: BLE001 —— 標記壞了不能讓橋停
                pass
            time.sleep(POLL_S)
    t = threading.Thread(target=run, name="bridge-readmarks", daemon=True)
    t.start()
    return t
