#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交接 / 整理 / 遺漏回顧寫的筆記照席位的路由落點(G29;10-17 @0c105 實測、@abb89 派,排 0.1.18)。全在 tmp 沙盒。

以前 aib memory write 沒帶 --route 就一律 core.handoff;aib memory tidy、aib handoff write、遺漏回顧都沒帶 ⇒
@0c105 綁了 proj.line-richmenu,筆記還是全落在 C80。現在:明講的 > 專案卡 route_key > 席位綁定 route_key > core.handoff;
這一席的路由寫不進去 ⇒ 退回 core.handoff 並記 route_fallback(交接不能因此整個寫不進去)。

R1 席位專案卡 route_key = proj.line-richmenu ⇒ 筆記落在那個夾、索引 route_key 對
R2 明講 --route ⇒ 照明講的(core.handoff)
R3 沒專案、沒綁定 ⇒ core.handoff
R4 只有席位綁定的 route_key(專案卡沒有)⇒ 用綁定的
R5 席位路由寫不進去(上層路由不可寫)⇒ 退回 core.handoff、回傳 route_fallback 寫明原因;明講的寫不進去照舊報錯
R6 aib handoff 的第二大腦寫入(_brain_write)與 tidy 的進度筆記(write_note)都走這條 ⇒ 落在專案夾
刀(複本,走 run_guarded):X1 不看席位路由 ⇒ R1 紅;X2 不退回 ⇒ R5 紅
"""
import importlib.util
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "memory"))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("frk_mr", str(HERE / "test-folder-route-key-977e3-1016.py"))
FRK = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FRK)
KEY = FRK.KEY
PROJ = "proj:93e995b5d52502c0"
UUID = "d1d63632-411b-416d-9cdc-10324fc0c105"


@unittest.skipUnless(shutil.which("node"), "note-format runner needs node")
class TestSeatRoute(FRK.Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("EZ2_AI_REPO", None)
        self.add_key()
        self.state = Path(os.environ["EZ2_AIB_HOME"])
        seats = self.state / "instances" / "20261014-133230-6ddf" / "seats"
        seats.mkdir(parents=True)
        (seats / "w10.json").write_text(json.dumps({"seat_id": "88ce1", "session_uuid": UUID, "project_uid": PROJ}),
                                        encoding="utf-8")
        (self.state / "identity").mkdir(parents=True, exist_ok=True)
        self.bind("")
        self.card(KEY)
        import aib_memory as AM
        self.AM = AM

    def bind(self, route):
        (self.state / "identity" / "bindings.json").write_text(
            json.dumps({"bindings": {"0c105": {"persona_uid": "fek1wt", "route_key": route}}}), encoding="utf-8")

    def card(self, route):
        d = self.state / "projects" / PROJ
        d.mkdir(parents=True, exist_ok=True)
        (d / "card.json").write_text(json.dumps({"project_uid": PROJ, "title": "LINE 圖文選單管理", "route_key": route}),
                                     encoding="utf-8")

    def write(self, **kw):
        return self.AM.write("progress", "進度", "內文", seat="6ddf/0c105", **kw)

    def folder_of(self, out):
        return Path(out["path"]).parent

    def test_R1_project_card_route(self):
        out = self.write()
        self.assertEqual(out["route_key"], KEY)
        self.assertEqual(self.folder_of(out), self.v / self.fid()["folderPath"])
        self.assertNotIn("route_fallback", out)
        rec = json.loads(self.AM.index_path().read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["route_key"], KEY)

    def test_R2_explicit_route_wins(self):
        out = self.write(route_key="core.handoff")
        self.assertEqual(out["route_key"], "core.handoff")
        self.assertEqual(self.folder_of(out).name, FRK.HANDOFF)

    def test_R3_nothing_bound(self):
        (self.state / "projects" / PROJ / "card.json").unlink()
        out = self.write()
        self.assertEqual((out["route_key"], self.folder_of(out).name), ("core.handoff", FRK.HANDOFF))

    def test_R4_binding_route_only(self):
        self.card("")
        self.bind(KEY)
        self.assertEqual(self.write()["route_key"], KEY)

    def test_R5_unwritable_seat_route_falls_back(self):
        strict = json.loads(json.dumps(FRK.CONTRACT))
        strict["routes"][0]["aiWritable"] = False                   # product.expert.draft 不可寫 ⇒ 掛在下面的 KEY 也不可寫
        self.contract.write_text(json.dumps(strict, ensure_ascii=False), encoding="utf-8")
        out = self.write()
        self.assertEqual((out["route_key"], self.folder_of(out).name), ("core.handoff", FRK.HANDOFF))
        self.assertIn(KEY, out["route_fallback"])
        with self.assertRaises(self.AM.MemoryError_):
            self.write(route_key=KEY)

    def test_R6_handoff_and_tidy_writers(self):
        import aib_handoff as AH
        res = AH._brain_write({"code": "0c105"}, "第 1 步", "交接內文", "6ddf/0c105")
        self.assertTrue(res["ok"], res)
        self.assertEqual(Path(res["path"]).parent, self.v / self.fid()["folderPath"])
        import memory_tidy as MT
        ctx = dict(self.AM.seat_context("6ddf/0c105"), model="claude-test")
        status, uid = MT.write_note(ctx, "進度內文", seat="6ddf/0c105", supersede=False)
        self.assertEqual(status, "ok")
        rec = [json.loads(x) for x in self.AM.index_path().read_text(encoding="utf-8").splitlines() if uid in x][-1]
        self.assertEqual(rec["route_key"], KEY)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/memory/aib_memory.py", needle, replacement,
                        tests=(name, "test-folder-route-key-977e3-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_ignore_seat_route(self):
        self._knife("dict.fromkeys([seat_route(ctx), DEFAULT_ROUTE])", "dict.fromkeys([DEFAULT_ROUTE])",
                    "TestSeatRoute.test_R1_project_card_route")

    def test_X2_no_fallback(self):
        self._knife("            if last:\n                raise\n", "            raise\n",
                    "TestSeatRoute.test_R5_unwritable_seat_route_falls_back")


if __name__ == "__main__":
    unittest.main(verbosity=2)
