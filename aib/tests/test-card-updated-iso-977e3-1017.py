#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""角色卡的 updated 跟全庫同一個格式(UTC ISO、毫秒、Z)(10-17 @977e3)。治具全在 tmpdir,不碰真 vault / ~/.ez2-aib。

10-17 全庫 updated 統一成 ISO 後實量兩個連帶問題:
  · card_registry 只認「2026-10-14 00:00:00」⇒ 改成 ISO 的 56 張卡 card_updated 讀成 None
  · aib newrole(create_and_bind_persona)新卡還寫本機「2026-10-17 16:40:42」⇒ aib vault doctor 一直抓到新卡

C1 card_registry 收 ISO(含毫秒 / 不含)與舊的本機格式;「2026-10-14 (融合)」、帶 +08:00、亂字 ⇒ None
C2 aib newrole 新卡:created / updated 都是 ISO Z、在建卡前後的時間之間;card_registry 讀得到 card_updated
C3 機器骨架卡(persona_create_plan.skeleton_markdown)的 updated 是 ISO Z,值 = created_ms
C4 這三種寫法 aib vault doctor 都不再當成要修的(跟 doctor 用同一條 ISO 判準)
刀(複本,走 run_guarded):X1 registry 拿掉 ISO 分支 ⇒ C1 紅;X2 newrole 改回本機格式 ⇒ C2 紅
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "memory"))
sys.path.insert(0, str(ROOT / "src" / "identity"))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import card_registry as CR  # noqa: E402

CREATE = ROOT / "src" / "identity" / "create_and_bind_persona.py"
PROMPT = "你是「測試員」。\n\n## 職責\n測試。\n\n## 紅線\n不亂寫。\n\n## 知識域\n測試。\n"
ISO_Z = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


def fm_value(text, key):
    m = re.search(r"(?m)^%s:[ \t]*(.*?)[ \t]*$" % key, text.split("\n---\n", 1)[0])
    return m.group(1) if m else None


class TestRegistry(unittest.TestCase):
    def test_C1_shapes(self):
        ok = ("2026-10-13T16:00:00.000Z", "2026-10-13T16:00:00Z", "2026-10-14 00:00:48", "2026-10-14 00:00", "2026-10-14")
        bad = ("2026-10-14 (融合)", "2026-10-14T00:00:00+08:00", "2026-10-14T00:00", "yesterday", "")
        for v in ok:
            self.assertEqual(CR._shape_or_none(v, CR._UPDATED_RE), v, v)
        for v in bad:
            self.assertIsNone(CR._shape_or_none(v, CR._UPDATED_RE), v)


class TestWriters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)
        (self.t / "home").mkdir()
        (self.t / "Vault").mkdir()
        (self.t / "p.md").write_text(PROMPT, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_C2_newrole_writes_iso(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.t / "home"), "LANG": "en_US.UTF-8"}
        before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(seconds=1)
        p = subprocess.run([sys.executable, str(CREATE), "--name", "格式測試員", "--code", "isotest",
                            "--prompt-file", str(self.t / "p.md"), "--vault", str(self.t / "Vault")],
                           capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
        after = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        out = json.loads(p.stdout[p.stdout.index("{"):])
        text = Path(out["card_file"]).read_text(encoding="utf-8")
        for key in ("created", "updated"):
            v = fm_value(text, key)
            self.assertRegex(v or "", ISO_Z, key)
            self.assertTrue(before <= dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc) <= after, v)
        cards = json.loads((self.t / "home" / ".ez2-aib" / "identity" / "cards.json").read_text(encoding="utf-8"))
        self.assertEqual(cards.get("cards", cards)[out["uid"]]["card_updated"], fm_value(text, "updated"))
        self.c4(text)

    def test_C3_skeleton_writes_iso(self):
        import persona_create_plan as PCP
        ms = 1792226442123                         # 2026-10-17T08:40:42.123Z
        text = PCP.skeleton_markdown("abc123", "dev", "977e3", ms)
        self.assertEqual(fm_value(text, "updated"), "2026-10-17T08:40:42.123Z")
        self.c4(text)

    def c4(self, text):
        import vault_cli as VC
        self.assertRegex(fm_value(text, "updated"), VC.ISO_Z_RE)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_registry_without_iso(self):
        self._knife("src/memory/card_registry.py", r"|T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z)?", ")?",
                    "TestRegistry.test_C1_shapes")

    def test_X2_newrole_local_format(self):
        self._knife("src/identity/create_and_bind_persona.py",
                    'now_str = _now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (_now.microsecond // 1000)',
                    'now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")',
                    "TestWriters.test_C2_newrole_writes_iso")


if __name__ == "__main__":
    unittest.main(verbosity=2)
