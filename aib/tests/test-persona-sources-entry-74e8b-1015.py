#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib persona 來源|狀態|source|status` 的入口在開源版補上(維護者 10-15 23:2x「把 aib persona 來源 的入口也補上」)。

原因:src/cockpit/aib 分派到 tools/entitlement/aib-persona-sources,但開源版沒帶那支 ⇒ aib_need 擋下、
印「這一份 aib 沒有這個功能」rc 2;人格來源開關(src/entitlement/sources_cli.py)寫好了卻打不到。
這裡改成開源版自帶的 tools/entitlement/aib_persona_sources.py(用 aib 選好的 Python 跑,不另找 python3)。

全部在暫存 HOME(沒有授權檔 = 免費態),子行程一律經 aib_proc_guard.run_guarded。
E1 `aib persona 狀態`(zh-TW)⇒ rc 0;每家都列;有「所有 AI 都免費」;沒有「沒有這個功能」
E2 `aib persona 來源 codex 開` ⇒ rc 0;$HOME/.ez2-aib/persona/sources.json 的 codex 為 true
E3 英文介面 `aib persona source codex on` 再 `aib persona status` ⇒ 「codex: on」,狀態沒有中日韓字
E4 不認得的 provider ⇒ rc 2、英文說「Unknown provider: <名字>」;不是 Python traceback
刀(落在複本,不碰真樹):K1 分派改回不存在的 tools/entitlement/aib-persona-sources ⇒ E1 紅;
   K2 入口不把 src 放進 sys.path ⇒ E1 紅
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
sys.path.insert(0, str(ROOT / "tests"))
from aib_proc_guard import run_guarded  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

THIS = Path(__file__).name
ENTRY_REL = "tools/entitlement/aib_persona_sources.py"
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def aib(root, home, *args, locale="zh-TW"):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "en_US.UTF-8",
           "EZ2_AIB_LOCALE": locale}
    r = run_guarded(["bash", str(Path(root) / "src" / "cockpit" / "aib"), *args],
                    timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert r.tripped is None, "aib 子行程被看門狗砍了:%s" % r.tripped
    return r


def providers(root):
    sys.path.insert(0, str(Path(root) / "src"))
    from entitlement.providers import list_providers
    return list(list_providers())


def check_e1(t, root, home):
    r = aib(root, home, "persona", "狀態")
    t.assertEqual(r.returncode, 0, r.stdout + r.stderr)
    for p in providers(root):
        t.assertIn("  %s: " % p, r.stdout)
    t.assertIn("所有 AI 都免費", r.stdout)
    t.assertNotIn("沒有這個功能", r.stdout + r.stderr)


class Entry(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)

    def test_e1_status(self):
        check_e1(self, ROOT, self.home)

    def test_e2_turn_on_codex(self):
        r = aib(ROOT, self.home, "persona", "來源", "codex", "開")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        obj = json.loads((self.home / ".ez2-aib" / "persona" / "sources.json").read_text(encoding="utf-8"))
        self.assertIs(obj["enabled"].get("codex"), True)

    def test_e3_english(self):
        r = aib(ROOT, self.home, "persona", "source", "codex", "on", locale="en")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = aib(ROOT, self.home, "persona", "status", locale="en")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("  codex: on", r.stdout)
        self.assertFalse(CJK.search(r.stdout), r.stdout)

    def test_e4_unknown_provider(self):
        r = aib(ROOT, self.home, "persona", "source", "no-such-ai", "on", locale="en")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("Unknown provider: no-such-ai", r.stderr)

    def test_k1_dispatch_back_to_missing_target(self):
        with knife_copy(ROOT, "src/cockpit/aib", '"$AIB_SOURCE_ROOT/%s"' % ENTRY_REL,
                        '"$AIB_SOURCE_ROOT/tools/entitlement/aib-persona-sources"') as copy_root:
            with self.assertRaises(AssertionError):
                check_e1(self, copy_root, self.home)

    def test_k2_entry_without_src_on_path(self):
        with knife_copy(ROOT, ENTRY_REL, "sys.path.insert(0, str(SRC))\n", "pass\n") as copy_root:
            with self.assertRaises(AssertionError):
                check_e1(self, copy_root, self.home)


if __name__ == "__main__":
    unittest.main(verbosity=2)
