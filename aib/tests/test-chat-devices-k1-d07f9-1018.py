#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""同一位主人、多台機器 K1(開發文件第 37.7 節;@abb89 10-18 03:35 審的第 3、4 點):機器清單 + 機器名冊 + 查人合併。

核心(一定跑,只用標準庫):
D1 清單驗證:合格的過;owner 不是簽章者 / seq 不是正整數 / valid_until 超過 31 天 / 超過 16 台 / 同一台兩筆 /
   停用的還列在 devices / 主人自己列在 devices / 簽的時間在未來 ⇒ 整份拒
D2 看過的最大 seq 持久存檔:收過 seq 5 ⇒ 換一個行程讀檔也是 5;seq 4 ⇒ 不收;seq 5 再來 ⇒ 收
D3 可用的清單:過了 valid_until ⇒ expired(只認 d=aib-roster);seq 比看過的小 ⇒ stale_seq;壞的 ⇒ bad:<代碼>
D4 合併:主人名冊 + 機器名冊各自的艙都在、每艙記宣告它的機器;兩台宣告同一個艙碼(座位不同)⇒ 那個艙兩邊都不收、
   記 devices-cabin-clash;座位完全一樣的重複公告 ⇒ 收一次;沒有主人名冊時名字 / 收信站取機器名冊
D5 機器名冊:簽章者不在清單 ⇒ signer_not_listed;owner 欄不是這位主人 ⇒ owner_mismatch
連 Nostr(要 AIB_TEST_NOSTR_VENV;只連本機假中繼站,沒有就跳過):
N1 A1 照舊發名冊;主人金鑰簽清單列 A2、A2 用機器金鑰發機器名冊 ⇒ lookup <主人>/<A2 的艙>/<席> 拿到 A2 那席、device = A2;
   A1 的艙照舊查得到
N2 停用 A2(seq 2)⇒ A2 的艙查不到;再把 seq 1 那份重簽一次(時間較新)塞回中繼站 ⇒ 仍查不到(看過 seq 2,不收舊的)
N3 沒列在清單的金鑰自稱 owner = 這位主人發機器名冊 ⇒ 查不到它的艙
N4 艙碼撞名:A2 宣告跟 A1 一樣的艙碼(座位不同)⇒ 那個艙兩台都查不到、audit 記 devices-cabin-clash
刀(複本):K1 機器名冊不對清單驗簽章者 ⇒ D5 紅;K2 不認 seq(一律收)⇒ N2 紅。
  K1 在網路那條路砍不出紅(10-18 實測):chat_nostr._owner_roster 只向中繼站要清單上那幾台的事件、收到後又比一次
  簽章者在不在清單,parse_device_roster 是第三道;N3 驗的是整條路不合併沒列在清單的金鑰。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_devices as DV  # noqa: E402
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("roster_t", str(HERE / "test-chat-roster-d07f9-1015.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)

NOW = 1_800_000_000


def npub_of(sk):
    return NK.npub(NK.pubkey_hex(sk))


SK = ["%064x" % (0x1000 + i) for i in range(40)]
OWNER, A2, A3 = npub_of(SK[0]), npub_of(SK[1]), npub_of(SK[2])


def dev(n, name=None):
    d = {"npub": n, "added_at": NOW - 100}
    if name:
        d["name"] = name
    return d


def roster(owner, cabins, relays=("wss://relay.example",), name="Andy"):
    return {"v": 1, "owner": owner, "display_name": name, "country": "TW", "envelope": [1], "dm_relays": list(relays),
            "cabins": [{"cabin": c, "seats": [{"seat": s, "npub": n, "accepts": ["dm"]} for s, n in seats]}
                       for c, seats in cabins], "updated_at": NOW}


class Core(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-devices-")
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_d1_parse_list(self):
        good = DV.build_list(OWNER, [dev(A2, "Mac mini")], seq=3, at=NOW)
        rec = DV.parse_list(json.dumps(good), OWNER, NOW)
        self.assertEqual((rec["seq"], rec["expired"]), (3, False))
        bad = [
            ("owner_mismatch", good, A2),
            ("bad_seq", dict(good, seq=0), OWNER),
            ("bad_seq", dict(good, seq=True), OWNER),
            ("bad_valid_until", dict(good, valid_until=NOW + 40 * 86400), OWNER),
            ("too_many", dict(good, devices=[dev(npub_of(SK[3 + i])) for i in range(17)]), OWNER),
            ("duplicate_device", dict(good, devices=[dev(A2), dev(A2)]), OWNER),
            ("revoked_still_listed", dict(good, revoked=[{"npub": A2, "at": NOW}]), OWNER),
            ("owner_in_devices", dict(good, devices=[dev(OWNER)]), OWNER),
            ("from_future", DV.build_list(OWNER, [dev(A2)], at=NOW + 3600), OWNER),
        ]
        for code, content, signer in bad:
            with self.assertRaises(DV.DevicesError, msg=code) as cm:
                DV.parse_list(json.dumps(content), signer, NOW)
            self.assertEqual(str(cm.exception), code)

    def test_d2_seq_persisted(self):
        self.assertTrue(DV.accept_seq(self.root, OWNER, 5))
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import chat_devices as DV; "
                            "print(DV.seen_seq(%r, %r))" % (str(COCKPIT), str(self.root), OWNER)],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "5", r.stderr)
        self.assertFalse(DV.accept_seq(self.root, OWNER, 4))
        self.assertTrue(DV.accept_seq(self.root, OWNER, 5))
        self.assertEqual(DV.seen_seq(self.root, OWNER), 5)

    def test_d3_usable_list(self):
        old = DV.build_list(OWNER, [dev(A2)], seq=1, at=NOW - 40 * 86400, valid_s=30 * 86400)
        self.assertEqual(DV.usable_list(self.root, OWNER, json.dumps(old), OWNER, NOW)[1], "expired")
        new = DV.build_list(OWNER, [dev(A2)], seq=2, at=NOW)
        rec, why = DV.usable_list(self.root, OWNER, json.dumps(new), OWNER, NOW)
        self.assertEqual((why, rec["seq"]), ("ok", 2))
        replay = DV.build_list(OWNER, [dev(A2), dev(A3)], seq=1, at=NOW)
        self.assertEqual(DV.usable_list(self.root, OWNER, json.dumps(replay), OWNER, NOW), (None, "stale_seq"))
        self.assertEqual(DV.usable_list(self.root, OWNER, "{}", OWNER, NOW), (None, "bad:bad_version"))
        self.assertEqual(DV.usable_list(self.root, OWNER, None, None, NOW), (None, "none"))

    def test_d4_merge(self):
        s1, s2, s3 = npub_of(SK[10]), npub_of(SK[11]), npub_of(SK[12])
        base = roster(OWNER, [("6ddf", [("aaaa1", s1)])])
        d2 = roster(OWNER, [("2282", [("bbbb2", s2)])], relays=("wss://two.example",))
        merged, warns = DV.merge(OWNER, base, [(A2, d2)])
        self.assertEqual(warns, [])
        seat, cab = DV.find(merged, "2282", "bbbb2")
        self.assertEqual((seat["npub"], cab["device"], cab["dm_relays"]), (s2, A2, ["wss://two.example"]))
        self.assertEqual(DV.find(merged, "6ddf", "aaaa1")[1]["device"], OWNER)
        self.assertEqual(merged["devices"], [A2])
        clash = roster(OWNER, [("6ddf", [("aaaa1", s3)]), ("2282", [("bbbb2", s2)])])
        merged, warns = DV.merge(OWNER, base, [(A2, clash)])
        self.assertEqual(DV.find(merged, "6ddf", "aaaa1"), (None, None))
        self.assertEqual(warns, [{"kind": "devices-cabin-clash", "cabin": "6ddf", "devices": sorted([OWNER, A2])}])
        self.assertIsNotNone(DV.find(merged, "2282", "bbbb2")[0])
        same = roster(OWNER, [("6ddf", [("aaaa1", s1)])])
        merged, warns = DV.merge(OWNER, base, [(A2, same)])
        self.assertEqual((warns, len(merged["cabins"])), ([], 1))
        merged, _w = DV.merge(OWNER, None, [(A2, d2)])
        self.assertEqual((merged["display_name"], merged["dm_relays"]), ("Andy", ["wss://two.example"]))

    def test_d5_device_roster(self):
        lst = DV.parse_list(json.dumps(DV.build_list(OWNER, [dev(A2)], at=NOW)), OWNER, NOW)
        content = json.dumps(roster(OWNER, [("2282", [("bbbb2", npub_of(SK[11]))])]))
        self.assertEqual(DV.parse_device_roster(content, A2, OWNER, lst)["owner"], OWNER)
        with self.assertRaises(DV.DevicesError) as cm:
            DV.parse_device_roster(content, A3, OWNER, lst)
        self.assertEqual(str(cm.exception), "signer_not_listed")
        with self.assertRaises(CR.RosterError) as cm:
            DV.parse_device_roster(json.dumps(roster(A3, [("2282", [("bbbb2", npub_of(SK[11]))])])), A2, OWNER, lst)
        self.assertEqual(str(cm.exception), "owner_mismatch")


_SIGN = r"""
import sys, json, nostr_sdk as N
out = []
for x in json.load(sys.stdin):
    b = N.EventBuilder(N.Kind(x["kind"]), x["content"]).tags([N.Tag.parse(t) for t in x.get("tags", [])])
    if x.get("at"):
        b = b.custom_created_at(N.Timestamp.from_secs(x["at"]))
    out.append(json.loads(b.finalize(N.Keys.parse(x["sk"])).as_json()))
print(json.dumps(out))
"""


@unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        self.b = RT.Box([self.relay.url])
        self.addCleanup(self.b.close)
        self.b.link_venv()
        self.seat = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        r = self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.b.cli("publish", self.seat)
        env = self.b.env()
        self.owner = NK.read_map(env)["owner"]["npub"]
        self.owner_sk = NK.load_secret("owner", env)
        self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: self.b.status().get("state") == "published", 40), self.b.status())
        self.dev_sk, self.dev_seat_sk = SK[20], SK[21]
        self.dev = npub_of(self.dev_sk)

    def push(self, specs):
        py = Path(RT.NOSTR_VENV) / "bin" / "python"
        r = subprocess.run([str(py), "-c", _SIGN], input=json.dumps(specs), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        from aib_fake_relay import RelayClient
        with RelayClient(self.relay.url) as c:
            for ev in json.loads(r.stdout):
                c.send(["EVENT", ev])
                c.recv()

    def sign_list(self, devices, seq, at=None):
        content = DV.build_list(self.owner, devices, seq=seq, at=at or int(time.time()) - 5)
        return {"sk": self.owner_sk, "kind": CR.KIND_ROSTER, "content": json.dumps(content), "tags": [["d", DV.DEVICES_D]],
                "at": at or int(time.time()) - 5}

    def device_roster(self, cabin="a2c2", seat="dddd2", signer_sk=None, at=None):
        sk = signer_sk or self.dev_sk
        content = roster(self.owner, [(cabin, [(seat, npub_of(self.dev_seat_sk))])], relays=(self.relay.url,))
        return [{"sk": sk, "kind": CR.KIND_ROSTER, "content": json.dumps(content), "tags": [["d", DV.roster_d(npub_of(sk))]],
                 "at": at or int(time.time()) - 5},
                {"sk": self.dev_seat_sk, "kind": CR.KIND_DM_RELAYS, "content": "", "tags": [["relay", self.relay.url]]}]

    def lookup(self, addr):
        r = self.b.cli("lookup", addr)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_n1_merge_device_roster(self):
        self.push([self.sign_list([dev(self.dev, "A2")], 1)] + self.device_roster())
        rc, rec = self.lookup("%s/a2c2/dddd2" % self.owner)
        self.assertEqual(rc, 0, rec)
        self.assertEqual((rec["seat"]["npub"], rec["device"], rec["dm_relays"]),
                         (npub_of(self.dev_seat_sk), self.dev, [self.relay.url]))
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual((rc, rec.get("device")), (0, self.owner), rec)

    def test_n2_revoke_and_replay(self):
        old = self.sign_list([dev(self.dev)], 1, at=int(time.time()) - 30)
        self.push([old] + self.device_roster())
        self.assertEqual(self.lookup("%s/a2c2/dddd2" % self.owner)[0], 0)
        self.push([dict(self.sign_list([], 2, at=int(time.time()) - 20),
                        content=json.dumps(DV.build_list(self.owner, [], revoked=[{"npub": self.dev, "at": int(time.time())}],
                                                         seq=2, at=int(time.time()) - 20)))])
        rc, rec = self.lookup("%s/a2c2/dddd2" % self.owner)
        self.assertEqual((rc, rec.get("why")), (2, "no_seat"), rec)
        self.push([self.sign_list([dev(self.dev)], 1, at=int(time.time()) - 2)])      # 舊清單重簽、時間較新 ⇒ 中繼站留它
        rc, rec = self.lookup("%s/a2c2/dddd2" % self.owner)
        self.assertEqual((rc, rec.get("why")), (2, "no_seat"), "看過 seq 2,seq 1 的舊清單不准再收:%s" % rec)

    def test_n3_unlisted_signer(self):
        self.push([self.sign_list([dev(self.dev)], 1)] + self.device_roster(cabin="e3e3", signer_sk=SK[30]))
        rc, rec = self.lookup("%s/e3e3/dddd2" % self.owner)
        self.assertEqual((rc, rec.get("why")), (2, "no_seat"), "沒列在清單的金鑰自稱同主人,不准合併:%s" % rec)

    def test_n4_cabin_clash(self):
        self.push([self.sign_list([dev(self.dev)], 1)] + self.device_roster(cabin="6ddf", seat=self.seat))
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual((rc, rec.get("why")), (2, "no_seat"), rec)
        lines = "".join(p.read_text() for p in (self.b.root / "audit").glob("*.jsonl"))
        self.assertIn("devices-cabin-clash:6ddf", lines)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name, "test-chat-roster-d07f9-1015.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300,
                            env=dict(os.environ))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_signer_not_checked(self):
        self._knife("src/cockpit/chat_devices.py", '''    if signer_npub not in {d["npub"] for d in machines(dev_list)}:
        raise DevicesError("signer_not_listed")''', "    pass", "Core.test_d5_device_roster")

    @unittest.skipUnless(RT.HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_seq_ignored(self):
        self._knife("src/cockpit/chat_devices.py", '''        if seq < best:
            return False''', "        pass", "Network.test_n2_revoke_and_replay")


if __name__ == "__main__":
    unittest.main()
