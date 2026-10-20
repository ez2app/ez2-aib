#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""空間快照帶 updated_at(第二波 #5,10-21;NOTE-member-ux A3「開了分享後 10–50 秒才看得到,頁面沒提示」)。

快照 build 加 built_at(對方組快照的時間);收件端 decode 驗過才留(正整數、不在未來一小時以上;舊版沒帶 ⇒ None)。
aib chat room / island --json 多 updated_at(built_at,沒有就退回收到的時間)、received_at、age_s、built_at;
aib chat space --json 多 last_sent(我最近一次送出成功的時間;沒送過 / 上次 too_big ⇒ None)。頁面據此顯示「更新中 / 最後更新」。

U1 build:快照頂層 built_at = now;encode → decode round trip 留著
U2 decode:built_at 壞值(字串、負數、布林、未來兩小時)⇒ None;舊版沒帶 ⇒ None
U3 room / island payload:有 built_at ⇒ updated_at 用它、received_at 用收到的、age_s 對;舊版沒帶 ⇒ updated_at = received_at
U4 space --json:沒 status 檔 ⇒ last_sent None;送成功 ⇒ {at, wire_bytes, dropped};too_big ⇒ last_sent None、last_error 有
刀(複本):K1 build 不蓋 built_at ⇒ U1 紅;K2 decode 不驗 built_at ⇒ U2 紅
"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
import room_snapshot as RS  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_room_su", str(HERE / "test-chat-room-d07f9-1017.py"))
RM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RM)
FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0x8101))
SNAP = {"v": 2, "island": {"name": "Bob 島", "size": {"w": 8, "h": 8}, "owner": {"name": "Bob"}, "buildings": []},
        "people": [{"seat": "bbbb2", "emoji": "🧭", "name": "Bob 艦長", "state": "online"}],
        "rooms": {"ship": {"name": "Bob 號"}, "cabins": [], "seats": [{"seat": "bbbb2"}], "layout": None, "layout_omitted": False}}


def _load_cb():
    spec = importlib.util.spec_from_file_location("chat_bridge_su", str(COCKPIT / "chat_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Build(RM.Box):
    def test_u1_build_built_at(self):
        snap = RS.build(self.b.env(), now=1_700_000_000, with_rooms=True, with_status=True)
        self.assertEqual(snap["built_at"], 1_700_000_000)
        back = RS.decode(RS.encode(snap))
        self.assertEqual(back["built_at"], 1_700_000_000, "round trip 留著")
        auto = RS.build(self.b.env(), with_rooms=False, with_status=False, with_state=False)
        self.assertLessEqual(abs(auto["built_at"] - int(time.time())), 5)


class Pure(unittest.TestCase):
    def test_u2_decode_cleans(self):
        for bad in ("123", -1, 0, True, int(time.time()) + 7200, 10 ** 12, None):
            got = RS.decode(RS.encode(dict(SNAP, built_at=bad)))
            self.assertIsNone(got["built_at"], repr(bad))
        self.assertIsNone(RS.decode(RS.encode(SNAP))["built_at"], "舊版沒帶")
        ok = int(time.time()) - 30
        self.assertEqual(RS.decode(RS.encode(dict(SNAP, built_at=ok)))["built_at"], ok)
        self.assertEqual(RS.clean_built_at(ok), ok)


class Payload(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-snapat-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        GF.ensure_dirs(self.root)
        CR.add_friend(FRIEND, "Bob", self.env)
        self.CB = _load_cb()

    def test_u3_payload_times(self):
        now = int(time.time())            # built_at 不信未來一小時以上,所以用現在
        GF.write_room(self.root, FRIEND, dict(SNAP, built_at=now - 40), now=now - 10)
        for fn in (self.CB.room_payload, self.CB.island_payload):
            pl = fn(FRIEND, self.env, now=now)
            self.assertEqual((pl["updated_at"], pl["received_at"], pl["age_s"], pl["built_at"]), (now - 40, now - 10, 40, now - 40), fn.__name__)
        GF.write_room(self.root, FRIEND, SNAP, now=now - 10)
        pl = self.CB.room_payload(FRIEND, self.env, now=now)
        self.assertEqual((pl["updated_at"], pl["received_at"], pl["age_s"], pl["built_at"]), (now - 10, now - 10, 10, None), "舊版沒帶 ⇒ 退回收到的時間")
        GF.write_room(self.root, FRIEND, dict(SNAP, built_at="junk"), now=now - 10)
        self.assertEqual(self.CB.island_payload(FRIEND, self.env, now=now)["updated_at"], now - 10)

    def test_u4_space_last_sent(self):
        import io
        def space_json():
            buf = io.StringIO()
            self.assertEqual(self.CB.main(["space", "--json"], env=self.env, out=buf), 0, buf.getvalue())
            return json.loads(buf.getvalue().strip().splitlines()[-1])
        pl = space_json()
        self.assertEqual((pl["last_sent"], pl["last_error"]), (None, None))
        GF.write_space_status(self.root, {"at": 1234, "reason": None, "wire_bytes": 777, "dropped": "layout"})
        pl = space_json()
        self.assertEqual((pl["last_sent"], pl["last_error"]), ({"at": 1234, "wire_bytes": 777, "dropped": "layout"}, None))
        GF.write_space_status(self.root, {"at": 1235, "reason": "too_big", "wire_bytes": 99999, "limit": 1000})
        pl = space_json()
        self.assertIsNone(pl["last_sent"])
        self.assertEqual(pl["last_error"]["reason"], "too_big")


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell, extra=()):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/room_snapshot.py", needle, replacement,
                        tests=(name, "test-chat-room-d07f9-1017.py", "test-chat-send-d07f9-1016.py") + tuple(extra)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=120, timeout=400)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_build_no_stamp(self):
        self._knife('            "built_at": int(time.time() if now is None else now)}', '            "built_at": None}', "Build.test_u1_build_built_at")

    def test_k2_decode_no_clean(self):
        self._knife('    snap["built_at"] = clean_built_at(snap.get("built_at"))\n', "", "Pure.test_u2_decode_cleans")


if __name__ == "__main__":
    unittest.main()
