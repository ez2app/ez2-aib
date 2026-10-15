# SPDX-License-Identifier: Apache-2.0
"""假轉接程式:全球 dm 寄件端測試用(開發文件 v0.5 第 22.3 節 PR-4)。

只讀寫檔案契約的資料夾(src/cockpit/dm_global_files.py),不連網、不碰 nostr-sdk。
一條背景執行緒:寫心跳 → 看 outbox → 照劇本領件、寫回條。

    with FakeBridge(root, script=lambda rec: [("relayed", {}), ("delivered", {})]) as fb:
        ...                                 # 跑寄件端
        fb.claimed                          # 領過的 msgid

script(rec) 回 [(stage, extra), …];特殊值:
    None          不領件(模擬轉接程式卡住 ⇒ 寄件端逾時會撤回成功)
    "claim-only"  領件但不寫任何回條(模擬已交出、對方沒回 ⇒ 寄件端 rc 4)
heartbeat=False ⇒ 不寫心跳(模擬轉接程式沒開)。
"""
import threading
import time

import dm_global_files as G


class FakeBridge(object):
    def __init__(self, root, script, heartbeat=True, poll=0.02, step_delay=0.0):
        self.root = root
        self.script = script
        self.heartbeat = heartbeat
        self.poll = poll
        self.step_delay = step_delay
        self.claimed = []
        self.seen = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="fake-bridge", daemon=True)

    def __enter__(self):
        G.ensure_dirs(self.root)
        if self.heartbeat:
            G.write_heartbeat(self.root)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=2)

    def _run(self):
        last_beat = time.time()
        while not self._stop.is_set():
            if self.heartbeat and time.time() - last_beat > 1:
                G.write_heartbeat(self.root)
                last_beat = time.time()
            for msgid, rec, _p in G.pending(self.root, "outbox"):
                if msgid in self.seen:
                    continue
                self.seen.append(msgid)
                steps = self.script(rec)
                if steps is None:
                    continue
                if G.claim(self.root, msgid) is None:
                    continue
                self.claimed.append(msgid)
                if steps == "claim-only":
                    continue
                for stage, extra in steps:
                    if self.step_delay:
                        time.sleep(self.step_delay)
                    r = {"v": 1, "at": int(time.time()), "stage": stage}
                    r.update(extra or {})
                    G.append_receipt(self.root, msgid, r)
            self._stop.wait(self.poll)
