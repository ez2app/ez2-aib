#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vault 自掛路由的分類沿用上層(10-17 @abb89 派;#99 的下一步)。全在 tmp 沙盒,不碰真 Vault。

10-17 @0c105 實踩:#99 掛好 proj.line-richmenu、newproj 綁好,但 aib memory write --route proj.line-richmenu
⇒ rc 2「category_uid_is_fabricated」:分類登記表(ez2-ai category registry)沒有這條,fallbackCategoryUid 造字被閘一擋。
做法:分類跟權限同一個規則 —— 沿用 inheritsFrom 那條契約路由的分類;筆記的 routeKey 還是自己的。不寫登記表。

C1 probe_route_writability:ok、category_uid = 上層的(cat_product_expert_draft)、沒用造字
C2 aib memory write 真寫:檔案落在新夾、frontmatter routeKey 是自己的、category_uid 是上層的;索引記同樣兩個值
C3 上層路由在分類登記表也查無 ⇒ 照樣 category_uid_is_fabricated(不放寬)
C4 build_plan 走同一條規則(category_uid = 上層的)
C5 folder-add 對已掛同代號的夾重跑:輸出有 route_added=false
V1 沒有 ez2-ai 的機器用隨附副本:開源匯出在第一行加的 SPDX 標頭拿掉後指紋對得上釘、materialize 成功
   (10-17 實量:以前每台沒有 ez2-ai 的機器 aib memory write 都是 note_format_runner_unavailable)
刀(複本,走 run_guarded):X1 runner 不沿用上層 ⇒ C1/C2 紅;X2 探針不傳 inheritsFrom ⇒ C1 紅;
   X3 隨附副本不拿 SPDX 標頭 ⇒ V1 紅
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
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("frk", str(HERE / "test-folder-route-key-977e3-1016.py"))
FRK = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FRK)
U, KEY, SUB = FRK.U, FRK.KEY, FRK.SUB
HAVE_NODE = shutil.which("node") is not None
PARENT_CAT = "cat_product_expert_draft"


@unittest.skipUnless(HAVE_NODE, "沒有 node(note-format runner 要 node)")
class TestCategory(FRK.Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("EZ2_AI_REPO", None)               # HOME 是 tmp ⇒ 沒有 ~/ez2-ai ⇒ 走隨附副本(新用戶的情況)
        self.add_key()
        import brain_write_plan as B
        import aib_memory as AM
        self.B, self.AM = B, AM

    def test_C1_probe_inherits_category(self):
        p = self.B.probe_route_writability(KEY)
        self.assertTrue(p["ok"], p)
        self.assertEqual((p["checked"]["category_uid"], p["checked"]["fallback_used"]), (PARENT_CAT, False))

    def test_C2_memory_write_lands_in_new_folder(self):
        out = self.AM.write("note", "LINE 選單換版", "換版完成", route_key=KEY, project="proj:93e995b5d52502c0")
        path = Path(out["path"])
        self.assertEqual(path.parent, self.v / self.fid()["folderPath"])
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^routeKey: \"?%s\"?$" % KEY.replace(".", r"\."))
        self.assertRegex(text, r"(?m)^category_uid: \"?%s\"?$" % PARENT_CAT)
        self.assertEqual((out["route_key"], out["category_uid"]), (KEY, PARENT_CAT))
        rec = json.loads(self.AM.index_path().read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["route_key"], rec["category_uid"], rec["rel"]),
                         (KEY, PARENT_CAT, os.path.relpath(str(path), str(self.v))))
        U.LOC = "en"
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        self.assertTrue(res["ok"], {k: v for k, v in res.items() if isinstance(v, list) and v})

    def test_C3_unregistered_parent_still_blocked(self):
        out = U.folder_add(self.v, FRK.HANDOFF, "交接子夾", route_key="proj.handoff-sub")   # core.handoff 有登記
        self.assertEqual(out["inheritsFrom"], "core.handoff")
        self.assertTrue(self.B.probe_route_writability("proj.handoff-sub")["ok"])
        lib = U.folder_add(self.v, "", "角色庫外層")                                       # 沒登記分類的上層
        items = json.loads((self.v / ".ez2/index/folder-id-index.json").read_text("utf-8"))["items"]
        rec = next(i for i in items if i["folderId"] == lib["folderId"])
        rec.update({"kind": "route", "routeKey": "product.expert.agent_library"})
        U.write_indexes(self.v, items, None, None, "t1")
        U.folder_add(self.v, lib["folderId"], "角色子夾", route_key="proj.roles-sub")
        p = self.B.probe_route_writability("proj.roles-sub")
        self.assertFalse(p["ok"])
        self.assertEqual(p["codes"], ["category_uid_is_fabricated"])

    def test_C4_build_plan_same_rule(self):
        route = self.B.route_of(self.B.load_contract(None), KEY)
        modules, _s, _r = self.B._resolve_modules_dir(None, None, self.B.DEFAULT_REV, self.B.PINS_DEFAULT)
        src = Path(self.B.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count('"inheritsFrom": route.get("inheritsFrom")'), 1, "build_plan 的 runner 呼叫要帶 inheritsFrom")
        out = self.B.call_runner(modules, {
            "route": {"routeKey": KEY, "folder": route["folder"], "dataLevel": route["dataLevel"],
                      "aiReadable": True, "aiWritable": True, "inheritsFrom": route["inheritsFrom"]},
            "note": {"title": "t", "body": "b", "privacy_level": "internal", "source_kind": "ai_chat",
                     "review_status": "approved", "created_at_ms": 1791000000000,
                     "extra_frontmatter": {"created_by_ai": True}},
            "used_uids": []})
        self.assertEqual(self.B.gate_note_format(out, KEY), (True, "note_format_ok"))
        self.assertEqual(out["category"]["category_uid"], PARENT_CAT)

    def test_V1_vendor_copy_matches_pins(self):
        self.assertFalse(Path(os.path.expanduser("~/ez2-ai")).exists())
        dest, shas, _rev = self.B.materialize()
        pins = self.B.load_pins()
        self.assertEqual(shas, {rel: pins[rel] for rel in self.B.MODULE_FILES})
        for rel in self.B.MODULE_FILES:
            self.assertNotIn(b"SPDX-License-Identifier", (dest / rel).read_bytes()[:200], rel)

    def test_C5_rerun_reports_route_added_false(self):
        again = self.add_key()
        self.assertEqual((again["routeKey"], again["route_added"]), (KEY, False))


@unittest.skipUnless(HAVE_NODE, "沒有 node")
class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement, tests=(name, "test-folder-route-key-977e3-1016.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_runner_no_inherit(self):
        for cell in ("TestCategory.test_C1_probe_inherits_category", "TestCategory.test_C2_memory_write_lands_in_new_folder"):
            self._knife("src/memory/note_format_runner.js",
                        "const categoryRoute = route.inheritsFrom ?", "const categoryRoute = false ?", cell)

    def test_X2_probe_drops_inherits(self):
        self._knife("src/memory/brain_write_plan.py", '"inheritsFrom": (route or {}).get("inheritsFrom")},',
                    '"inheritsFrom": None},', "TestCategory.test_C1_probe_inherits_category")

    def test_X3_vendor_header_kept(self):
        self._knife("src/memory/brain_write_plan.py", 'return _SPDX_LINE.sub(b"", path.read_bytes(), count=1)',
                    "return path.read_bytes()", "TestCategory.test_V1_vendor_copy_matches_pins")


if __name__ == "__main__":
    unittest.main(verbosity=2)
