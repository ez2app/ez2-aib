#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""席金鑰跟著狀態根走(10-24 第二台 Mac:只設 EZ2_AIB_STATE_ROOT 跑 aib chat prepare,席位讀暫存根、金鑰寫真的家目錄 ⇒
原本 4 席的替代金鑰全被封存、轉接程式隨即重發新的,名冊換了公鑰)。

R1 根:沒有明寫 EZ2_AIB_SECRETS_DIR / AIB_NOSTR_MAP 時,金鑰夾與對照表跟 aib_secrets.secrets_dir 同一個根
   (EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > HOME/.ez2-aib);明寫的照舊優先
R2 回歸:真家目錄已有主人 + 兩席替代金鑰;另設暫存狀態根(先放空對照表 = 刻意要新身分)跑 ensure_owner + sync_seats ⇒
   真家目錄一個檔都沒變(內容 + 檔名);暫存根有自己的、不同的主人;暫存根讀不到家目錄的席位檔
   (只帶 HOME 時照舊用 env 的 HOME:測試沙盒不准讀到行程自己的家目錄;aib_secrets 那支這種情況讀行程的 HOME,不比)
R3 升版不准靜默換身分(@abb89 #295 r1 必修 2):家目錄有舊身分、設 EZ2_AIB_HOME / STATE_ROOT 而新根空 ⇒
   ensure_owner / sync_seats 都丟 LegacyKeysElsewhere、家目錄不變、新根沒產鑰;sync_keys 回錯誤種類;
   明設 EZ2_AIB_SECRETS_DIR ⇒ 照產(刻意要新身分)
R3b(@abb89 #295 r2 突變):舊根沒有 owner.key 的三種也擋 ——(a) owner.sealed + 對照表(K2 加密主人)
   (b) 只有對照表(set_remote_owner,join 進來的機器)(c) 只有 owner.sealed;只設 AIB_NOSTR_MAP ⇒ 放行
R4 封存走暫存根(必修 3):暫存根先發兩席,再 sync_seats([]) ⇒ 家目錄不變、<暫存根>/secrets/nostr/retired/ 有檔
R5 開機自起(必修 1):boot_plist 帶 EZ2_AIB_HOME / EZ2_AIB_SECRETS_DIR / AIB_NOSTR_MAP(都是路徑)
刀(複本):K1 _root 改回只看 HOME ⇒ R2 紅;K2 拿掉舊身分保護 ⇒ R3 紅
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import aib_secrets  # noqa: E402
import nostr_keys as K  # noqa: E402

_spec = importlib.util.spec_from_file_location("chat_bridge_keyroot", str(COCKPIT / "chat_bridge.py"))
CB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CB)


def tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class Box(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-nkroot-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.home = self.td / "home"
        self.home.mkdir()
        self.real = {"HOME": str(self.home)}
        K.ensure_owner(self.real)
        K.sync_seats([{"seat5": "aaaa1", "cabin": "c1", "persona_uid": "s-aaaa1"},
                      {"seat5": "bbbb2", "cabin": "c1", "persona_uid": "s-bbbb2"}], self.real)

    def test_r1_root(self):
        st = str(self.td / "st")
        self.assertEqual(K.secrets_dir({"HOME": str(self.home)}), self.home / ".ez2-aib" / "secrets")
        for env in ({"HOME": str(self.home), "EZ2_AIB_HOME": str(self.td / "h2")},
                    {"HOME": str(self.home), "EZ2_AIB_HOME": str(self.td / "h2"), "EZ2_AIB_STATE_ROOT": st}):
            self.assertEqual(K.secrets_dir(env), aib_secrets.secrets_dir(env))
        env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": st}
        self.assertEqual(K.map_path(env), Path(st) / "identity" / "nostr-map.json")
        self.assertEqual(K.keys_dir(env), Path(st) / "secrets" / "nostr")
        env.update(EZ2_AIB_SECRETS_DIR=str(self.td / "x"), AIB_NOSTR_MAP=str(self.td / "m.json"))
        self.assertEqual((K.secrets_dir(env), K.map_path(env)), (self.td / "x", self.td / "m.json"))

    def seat(self, root, code, uid):
        d = Path(root) / "instances" / "20261024-120000-c1" / "seats"
        d.mkdir(parents=True, exist_ok=True)
        uuid = "00000000-0000-0000-0000-0000000" + code
        (d / ("w%s.json" % code)).write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": uuid, "role": "dev"}))
        disp = Path(root) / "identity" / "seat-display.json"
        disp.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(disp.read_text()) if disp.exists() else {"display": {}}
        cur["display"][code] = {"persona_uid": uid}
        disp.write_text(json.dumps(cur))

    def fresh_root(self):
        st = self.td / "st"
        (st / "identity").mkdir(parents=True)
        (st / "identity" / "nostr-map.json").write_text(json.dumps({"v": K.MAP_VERSION, "owner": None, "seats": []}))
        return {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(st)}

    def test_r2_temp_root_leaves_real_home(self):
        self.seat(self.home / ".ez2-aib", "aaaa1", "s-aaaa1")
        before = tree(self.home)
        self.assertEqual(len([e for e in K.read_map(self.real)["seats"] if e["status"] == "active"]), 2)
        self.assertEqual([s["seat5"] for s in K.live_seats(env=self.real)], ["aaaa1"])
        tmp = self.fresh_root()
        self.assertEqual(K.live_seats(env=tmp), [])                          # 暫存根讀不到家目錄的席位檔
        owner = K.ensure_owner(tmp)
        K.sync_seats(K.live_seats(env=tmp), tmp)
        self.assertEqual(tree(self.home), before)
        self.assertNotEqual(owner, K.read_map(self.real)["owner"]["npub"])
        self.assertTrue((self.td / "st" / "secrets" / "nostr" / "owner.key").is_file())

    def test_r3_legacy_keys_elsewhere(self):
        before = tree(self.home)
        for var in ("EZ2_AIB_HOME", "EZ2_AIB_STATE_ROOT"):
            env = {"HOME": str(self.home), var: str(self.td / ("new-" + var))}
            with self.assertRaises(K.LegacyKeysElsewhere):
                K.ensure_owner(env)
            with self.assertRaises(K.LegacyKeysElsewhere):
                K.sync_seats([{"seat5": "aaaa1", "cabin": "c1", "persona_uid": "s-aaaa1"}], env)
            self.assertEqual(CB.sync_keys(env), ([], "LegacyKeysElsewhere"))
            self.assertFalse((self.td / ("new-" + var) / "secrets").exists(), var)
        self.assertEqual(tree(self.home), before)
        same = {"HOME": str(self.home), "EZ2_AIB_HOME": str(self.home / ".ez2-aib")}           # 新根 = 舊根 ⇒ 不擋
        self.assertEqual(K.ensure_owner(same), K.read_map(self.real)["owner"]["npub"])
        mine = {"HOME": str(self.home), "EZ2_AIB_HOME": str(self.td / "n2"), "EZ2_AIB_SECRETS_DIR": str(self.td / "n2" / "secrets")}
        self.assertTrue(K.ensure_owner(mine).startswith("npub1"))                               # 明設金鑰夾 = 刻意要新身分
        self.assertEqual(tree(self.home), before)

    def test_r3b_legacy_without_owner_key(self):
        def legacy(name, sealed, mapped):
            home = self.td / name
            kd = home / ".ez2-aib" / "secrets" / "nostr"
            kd.mkdir(parents=True)
            if sealed:
                (kd / "owner.sealed").write_text("sealed\n")
            if mapped:
                K.set_remote_owner(K.npub(K.pubkey_hex("%064x" % 0x9101)), {"HOME": str(home)})
            return home
        for name, sealed, mapped in (("a", True, True), ("b", False, True), ("c", True, False)):
            home = legacy(name, sealed, mapped)
            before = tree(home)
            env = {"HOME": str(home), "EZ2_AIB_HOME": str(self.td / ("new-" + name))}
            with self.assertRaises(K.LegacyKeysElsewhere, msg=name):
                K.ensure_owner(env)
            with self.assertRaises(K.LegacyKeysElsewhere, msg=name):
                K.sync_seats([{"seat5": "aaaa1", "cabin": "c1", "persona_uid": "s-aaaa1"}], env)
            self.assertEqual(tree(home), before, name)
            self.assertFalse((self.td / ("new-" + name) / "secrets").exists(), name)
        home = self.td / "a"
        ok = {"HOME": str(home), "EZ2_AIB_HOME": str(self.td / "n3"), "AIB_NOSTR_MAP": str(self.td / "n3" / "m.json")}
        self.assertTrue(K.ensure_owner(ok).startswith("npub1"))                 # 只設對照表路徑 = 刻意要新身分

    def test_r4_retire_in_temp_root(self):
        before = tree(self.home)
        tmp = self.fresh_root()
        K.ensure_owner(tmp)
        K.sync_seats([{"seat5": "cccc3", "cabin": "c1", "persona_uid": "s-cccc3"},
                      {"seat5": "dddd4", "cabin": "c1", "persona_uid": "s-dddd4"}], tmp)
        acts = K.sync_seats([], tmp)
        self.assertEqual(sorted(a[2] for a in acts if a[0] == "retired"), ["cccc3", "dddd4"])
        self.assertEqual(len(list((self.td / "st" / "secrets" / "nostr" / "retired").glob("*.key"))), 2)
        self.assertEqual(tree(self.home), before)

    def test_r5_boot_env(self):
        env = {"HOME": str(self.home), "PATH": "/usr/bin", "EZ2_AIB_HOME": "/x/h", "EZ2_AIB_SECRETS_DIR": "/x/s",
               "AIB_NOSTR_MAP": "/x/m.json", "SOME_TOKEN": "nope"}
        ev = CB.boot_plist(env, python="/py", script="/s/chat_bridge.py", log="/l")["EnvironmentVariables"]
        self.assertEqual((ev.get("EZ2_AIB_HOME"), ev.get("EZ2_AIB_SECRETS_DIR"), ev.get("AIB_NOSTR_MAP")),
                         ("/x/h", "/x/s", "/x/m.json"))
        self.assertNotIn("SOME_TOKEN", ev)


class Knife(unittest.TestCase):
    def _run(self, anchor, repl):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", anchor, repl, tests=(name,)) as root:
            return run_guarded([sys.executable, str(root / "tests" / name), "Box"], cwd=str(root), max_procs=60, timeout=120)

    def test_k1_home_only(self):
        r = self._run('    root = env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME")\n', "    root = None\n")
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_k2_no_legacy_guard(self):
        r = self._run("        raise LegacyKeysElsewhere(\"legacy_keys_elsewhere\")\n", "        return\n")
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
