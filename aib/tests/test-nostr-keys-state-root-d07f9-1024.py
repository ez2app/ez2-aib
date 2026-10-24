#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""席金鑰跟著狀態根走(10-24 fishmac:只設 EZ2_AIB_STATE_ROOT 跑 aib chat prepare,席位讀暫存根、金鑰寫真的家目錄 ⇒
原本 4 席的替代金鑰全被封存、轉接程式隨即重發新的,名冊換了公鑰)。

R1 根:沒有明寫 EZ2_AIB_SECRETS_DIR / AIB_NOSTR_MAP 時,金鑰夾與對照表跟 aib_secrets.secrets_dir 同一個根
   (EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > HOME/.ez2-aib);明寫的照舊優先
R2 回歸:真家目錄已有主人 + 兩席替代金鑰;另設空的暫存狀態根跑 ensure_owner + sync_seats(live_seats) ⇒
   真家目錄一個檔都沒變(內容 + 檔名);暫存根有自己的、不同的主人
   (只帶 HOME 時照舊用 env 的 HOME:測試沙盒不准讀到行程自己的家目錄;aib_secrets 那支這種情況讀行程的 HOME,不比)
刀(複本):K1 _root 改回只看 HOME ⇒ R2 紅
"""
import hashlib
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

    def test_r2_temp_root_leaves_real_home(self):
        before = tree(self.home)
        self.assertEqual(len([e for e in K.read_map(self.real)["seats"] if e["status"] == "active"]), 2)
        tmp = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.td / "st")}
        owner = K.ensure_owner(tmp)
        K.sync_seats(K.live_seats(env=tmp), tmp)
        self.assertEqual(tree(self.home), before)
        self.assertNotEqual(owner, K.read_map(self.real)["owner"]["npub"])
        self.assertTrue((self.td / "st" / "secrets" / "nostr" / "owner.key").is_file())


class Knife(unittest.TestCase):
    def _run(self, anchor, repl):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/nostr_keys.py", anchor, repl, tests=(name,)) as root:
            return run_guarded([sys.executable, str(root / "tests" / name), "Box"], cwd=str(root), max_procs=60, timeout=120)

    def test_k1_home_only(self):
        r = self._run('    root = env.get("EZ2_AIB_STATE_ROOT") or env.get("EZ2_AIB_HOME")\n', "    root = None\n")
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
