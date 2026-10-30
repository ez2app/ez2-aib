#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""folder-add --route-key:uid 庫新建的夾掛自己的路由代號,newproj / 綁定 / aib memory write 才認得(10-16 @abb89 派)。

10-16 @0c105 實踩:folder-add 建出的「LINE 圖文選單管理」(fld_p_sub_a7a9fce5e545)沒有 routeKey,
aib newproj --folder 只認路由契約 routes[] ⇒ 新夾綁不成專案;aib memory write --route 也只能選既有路由。
做法:代號登在 Vault 自己的 route-key-index(帶 inheritsFrom = 最近一層上層路由);route_register.load_contract
併入這些條目,權限一律照抄上層那條(不另給、不放寬)。契約本身只有維護者能改,這裡不寫契約。全在 tmp 沙盒。

K1 既有的夾補掛代號:folder-id-index / route-key-index 帶 inheritsFrom、verify 綠;同代號再跑一次不變
K2 新建夾時一起掛代號
K3 擋下且什麼都沒寫:代號形狀不合 / 已被別的夾用 / 契約既有的代號 / 上層沒有路由 / 夾已掛別的代號 / 讀不到契約
K4 load_contract 併入:權限照抄上層(dataLevel / aiWritable)、帶標題;check_against_contract 過;上層是敏感 ⇒ 擋;
   跟契約撞名 / inheritsFrom 不在契約 / 沒設 EZ2_VAULT_DIR / with_vault_routes=False ⇒ 不併
K5 newproj.resolve_folder:代號、資料夾標題都對得到
K6 aib newproj --folder <代號>(子行程):掛之前回錯、掛之後建卡成功、卡上 route_key 是新代號
K7 掛了代號的夾不能 folder-retire
I1 新鍵四語都有、英文值沒有中日韓字
刀(複本,走 run_guarded):X1 load_contract 不併 ⇒ K4 紅;X2 權限不照抄上層(寫死 aiWritable True)⇒ K4 紅;
   X3 route-key-index 不收 inheritsFrom 的夾 ⇒ K1 紅
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
sys.path.insert(0, str(ROOT / "tools" / "project"))
sys.path.insert(0, str(ROOT / "src" / "persona_route"))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import route_register as RR  # noqa: E402
import uid_layout_convert as U  # noqa: E402

PY = sys.executable
BIN_AIB = ROOT.parent / "bin" / "aib"
GROUP, ROUTE, SUB = "fld_p_group_5d4b60295e72", "fld_p_route_d229ff09b371", "fld_p_sub_a7a9fce5e545"
BOARD = "fld_x_group_337e040be117"
HANDOFF = "fld_c_route_79bb4878617e"
KEY = "proj.line-richmenu"
CONTRACT = {"contract": "test", "version": 1, "routes": [
    {"routeKey": "product.expert.draft", "module": "P", "folder": "11-Drafts", "dataLevel": "一般",
     "aiReadable": True, "aiWritable": True},
    {"routeKey": "core.handoff", "module": "C", "folder": "C80", "dataLevel": "內部", "aiReadable": True,
     "aiWritable": True},
    {"routeKey": "product.expert.agent_library", "module": "P", "folder": "10-Roles", "dataLevel": "一般",
     "aiReadable": True, "aiWritable": True},
    {"routeKey": "product.expert.archive", "module": "P", "folder": "19-Archive", "dataLevel": "一般",
     "aiReadable": True, "aiWritable": False},
]}


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-fldroute-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.contract = self.tmp / "contract.json"
        self.contract.write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        for k in ("EZ2_VAULT_ROOT", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT"):
            os.environ.pop(k, None)
        os.environ.update({"HOME": str(self.home), "EZ2_VAULT_DIR": str(self.v), "EZ2_AIB_LOCALE": "en",
                           "EZ2_ROUTE_CONTRACT_SPEC": str(self.contract), "EZ2_EZ2AI_REPO": str(self.tmp / "no-repo"),
                           "EZ2_AIB_STATE_ROOT": str(self.home / ".ez2-aib"), "EZ2_AIB_HOME": str(self.home / ".ez2-aib")})
        U.LOC = "en"                                  # 語系在 import 時就定了;訊息比對用英文
        self.items = [
            item(GROUP, GROUP, "", 1, "P-Ez2 AI專家", "group", "folder.group.p.5d4b6029"),
            item(ROUTE, GROUP + "/" + ROUTE, GROUP, 2, "P20-專家進行中", "route", "product.expert.draft"),
            item(SUB, GROUP + "/" + ROUTE + "/" + SUB, ROUTE, 3, "LINE 圖文選單管理", "sub"),
            item(BOARD, BOARD, "", 1, "10-看板區 Dashboards", "group", "folder.group.x.337e040b"),
            item("fld_c_group_f7d29f6a675c", "fld_c_group_f7d29f6a675c", "", 1, "C-Ez2 Core系統底層", "group"),
            item(HANDOFF, "fld_c_group_f7d29f6a675c/" + HANDOFF, "fld_c_group_f7d29f6a675c", 2,
                 "C80-系統記憶交接", "route", "core.handoff"),
        ]
        for it in self.items:
            (self.v / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        (self.v / ".ez2").mkdir(parents=True, exist_ok=True)
        (self.v / ".ez2" / "migration-state.json").write_text(json.dumps({"mode": "uid_layout"}), encoding="utf-8")
        U.write_indexes(self.v, json.loads(json.dumps(self.items)), {}, [{"uid": "3aaaa1", "path": "x"}], "t0")

    def fid(self, folder=SUB):
        return next(i for i in json.loads((self.v / ".ez2/index/folder-id-index.json").read_text("utf-8"))["items"]
                    if i["folderId"] == folder)

    def rk(self):
        return json.loads((self.v / ".ez2/index/route-key-index.json").read_text("utf-8"))["items"]

    def snapshot(self):
        return sorted((str(p.relative_to(self.tmp)), p.read_bytes() if p.is_file() else b"")
                      for p in self.tmp.rglob("*"))

    def verify_ok(self):
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        self.assertTrue(res["ok"], {k: v for k, v in res.items() if isinstance(v, list) and v})

    def refused(self, fn, *a, pattern=None, **kw):
        before = self.snapshot()
        with self.assertRaises(SystemExit) as cm:
            fn(*a, **kw)
        self.assertEqual(before, self.snapshot(), "a refused call must not write anything")
        if pattern:
            self.assertRegex(str(cm.exception.code), pattern)

    def add_key(self):
        return U.folder_add(self.v, ROUTE, "LINE 圖文選單管理", route_key=KEY)


class TestFolderAddRouteKey(Base):
    def test_K1_existing_folder_gets_key(self):
        out = self.add_key()
        self.assertEqual((out["folderId"], out["created"], out["routeKey"], out["inheritsFrom"], out["route_added"]),
                         (SUB, False, KEY, "product.expert.draft", True))
        rec = self.fid()
        self.assertEqual((rec["routeKey"], rec["inheritsFrom"], rec["kind"]), (KEY, "product.expert.draft", "sub"))
        hit = [i for i in self.rk() if i["routeKey"] == KEY]
        self.assertEqual(len(hit), 1)
        self.assertEqual((hit[0]["folderId"], hit[0]["folderPath"], hit[0]["inheritsFrom"]),
                         (SUB, rec["folderPath"], "product.expert.draft"))
        self.assertEqual({i["routeKey"] for i in self.rk()}, {"product.expert.draft", "core.handoff", KEY})
        self.verify_ok()
        before = self.snapshot()
        again = self.add_key()
        self.assertEqual((again["routeKey"], again["created"]), (KEY, False))
        self.assertEqual(before, self.snapshot(), "same key again changes nothing")

    def test_K2_new_folder_with_key(self):
        out = U.folder_add(self.v, ROUTE, "官網改版", route_key="proj.site-revamp")
        self.assertTrue(out["created"])
        rec = self.fid(out["folderId"])
        self.assertEqual((rec["routeKey"], rec["inheritsFrom"], rec["level"]), ("proj.site-revamp", "product.expert.draft", 3))
        self.assertIn("proj.site-revamp", {i["routeKey"] for i in self.rk()})
        self.verify_ok()
        sub2 = U.folder_add(self.v, SUB, "第二層", route_key="proj.deep")        # 上層是掛代號的夾 ⇒ 沿用再上一層的契約路由
        self.assertEqual(sub2["inheritsFrom"], "product.expert.draft")

    def test_K3_refusals_write_nothing(self):
        self.refused(self.add_key_with, "Bad_Key", pattern="Bad_Key")
        self.refused(self.add_key_with, "single", pattern="single")
        self.refused(self.add_key_with, "core.handoff", pattern="already used")          # 已掛在 C80
        self.refused(self.add_key_with, "product.expert.archive", pattern="route contract")   # 契約有、沒有夾掛
        self.refused(U.folder_add, self.v, BOARD, "看板子夾", route_key="proj.board", pattern="proj.board")
        self.add_key()
        self.refused(U.folder_add, self.v, ROUTE, "別的夾", route_key=KEY, pattern="LINE")
        self.refused(self.add_key_with, "proj.other", pattern=re.escape(KEY))
        os.environ["EZ2_ROUTE_CONTRACT_SPEC"] = str(self.tmp / "missing.json")
        self.refused(U.folder_add, self.v, ROUTE, "又一個", route_key="proj.more", pattern="contract")

    def add_key_with(self, key):
        return U.folder_add(self.v, ROUTE, "LINE 圖文選單管理", route_key=key)

    def test_K4_load_contract_merges_vault_routes(self):
        self.add_key()
        c = RR.load_contract()
        mine = [r for r in c["routes"] if r["routeKey"] == KEY]
        self.assertEqual(len(mine), 1)
        r = mine[0]
        self.assertEqual((r["folder"], r["dataLevel"], r["aiWritable"], r["inheritsFrom"], r["source"], r["title"]),
                         (self.fid()["folderPath"], "一般", True, "product.expert.draft", "vault", "LINE 圖文選單管理"))
        self.assertEqual(RR.check_against_contract(KEY, c)[:2], (True, "contract_ok"))
        self.assertEqual(RR.resolve_route_folder(KEY, contract=c, root=str(self.v)), self.fid()["folderPath"])
        self.assertNotIn(KEY, RR.contract_route_keys(RR.load_contract(with_vault_routes=False)))
        # 上層是敏感 ⇒ 照抄 ⇒ 擋;上層不可寫 ⇒ 照抄 ⇒ 擋
        for patch, code in (({"dataLevel": "敏感"}, "sensitive_data_level"), ({"aiWritable": False}, "route_not_ai_writable")):
            strict = json.loads(json.dumps(CONTRACT))
            strict["routes"][0].update(patch)
            self.contract.write_text(json.dumps(strict, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(RR.check_against_contract(KEY, RR.load_contract())[1], code)
        self.contract.write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        # 跟契約撞名 / inheritsFrom 不在契約 / 路徑不安全 ⇒ 不併
        rk = json.loads((self.v / ".ez2/index/route-key-index.json").read_text("utf-8"))
        rk["items"] += [{"routeKey": "core.handoff", "folderId": SUB, "folderPath": "x", "inheritsFrom": "product.expert.draft"},
                        {"routeKey": "proj.orphan", "folderId": SUB, "folderPath": "x", "inheritsFrom": "no.such"},
                        {"routeKey": "proj.escape", "folderId": SUB, "folderPath": "../out", "inheritsFrom": "product.expert.draft"}]
        (self.v / ".ez2/index/route-key-index.json").write_text(json.dumps(rk), encoding="utf-8")
        c = RR.load_contract()
        self.assertEqual([r["folder"] for r in c["routes"] if r["routeKey"] == "core.handoff"], ["C80"])
        self.assertFalse({"proj.orphan", "proj.escape"} & RR.contract_route_keys(c))
        os.environ.pop("EZ2_VAULT_DIR")
        self.assertNotIn(KEY, RR.contract_route_keys(RR.load_contract()))

    def test_K5_newproj_resolve_folder(self):
        import new_project as NP
        self.assertEqual(NP.resolve_folder(RR.load_contract(), KEY)[2] is not None, True)   # 掛之前:對不到
        self.add_key()
        c = RR.load_contract()
        path = self.fid()["folderPath"]
        for want in (KEY, "LINE 圖文選單管理", path):
            self.assertEqual(NP.resolve_folder(c, want), (KEY, path, None), want)

    def test_no_env_uses_default_vault_symlink_for_all_providers(self):
        self.add_key()
        legacy = self.home / "Desktop" / "Obsidian" / "Ez2"
        legacy.parent.mkdir(parents=True)
        legacy.symlink_to(self.v, target_is_directory=True)
        os.environ.pop("EZ2_VAULT_DIR")
        self.assertEqual(RR.check_against_contract(KEY, RR.load_contract())[:2], (True, "contract_ok"))
        # 各家的工具殼沒有 EZ2_VAULT_* 時,實際 CLI 仍能解析同一條已登錄路由。
        for provider in ("codex", "grok", "agy", "copilot", "claude"):
            with self.subTest(provider=provider):
                env = {k: os.environ[k] for k in ("HOME", "EZ2_ROUTE_CONTRACT_SPEC", "EZ2_EZ2AI_REPO",
                                                  "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME")}
                env.update({"PATH": os.environ.get("PATH", ""), "EZ2_AIB_LOCALE": "en",
                            "AIB_GENES": "0", "AIB_AUTO_BIND": "0", "EZ2_SESSION_PROVIDER": provider})
                r = subprocess.run([PY, str(BIN_AIB), "newproj", "--title", "No env", "--folder", KEY,
                                    "--dry-run"], cwd=str(self.tmp), env=env, capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL, timeout=30)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["route_key"], KEY)

    def test_no_env_uses_aib_default_vault(self):
        self.add_key()
        dest = self.home / ".ez2-aib" / "vault"
        dest.parent.mkdir(parents=True)
        self.v.rename(dest)
        os.environ.pop("EZ2_VAULT_DIR")
        self.assertIn(KEY, RR.contract_route_keys(RR.load_contract()))

    def test_explicit_vault_does_not_borrow_default_routes(self):
        self.add_key()
        legacy = self.home / "Desktop" / "Obsidian" / "Ez2"
        legacy.parent.mkdir(parents=True)
        legacy.symlink_to(self.v, target_is_directory=True)
        other = self.tmp / "other-vault"
        other.mkdir()
        os.environ["EZ2_VAULT_DIR"] = str(other)
        self.assertNotIn(KEY, RR.contract_route_keys(RR.load_contract()))
        os.environ.pop("EZ2_VAULT_DIR")
        os.environ["EZ2_VAULT_ROOT"] = str(other)
        self.assertNotIn(KEY, RR.contract_route_keys(RR.load_contract()))

    def test_K6_newproj_cli(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        env = {k: os.environ[k] for k in ("HOME", "EZ2_VAULT_DIR", "EZ2_ROUTE_CONTRACT_SPEC", "EZ2_EZ2AI_REPO",
                                          "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME")}
        env.update({"PATH": os.environ.get("PATH", ""), "EZ2_AIB_LOCALE": "en", "AIB_GENES": "0", "AIB_AUTO_BIND": "0"})

        def newproj(title):
            return subprocess.run([PY, str(BIN_AIB), "newproj", "--title", title, "--folder", KEY], cwd=str(proj),
                                  env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        r = newproj("Before")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.add_key()
        r = newproj("LINE RichMenu")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        card = json.loads((self.home / ".ez2-aib" / "projects" / out["project_uid"] / "card.json").read_text("utf-8"))
        self.assertEqual(card.get("route_key"), KEY)
        self.assertEqual(out.get("folder"), "LINE 圖文選單管理")

    def test_K7_routed_folder_not_retired(self):
        self.add_key()
        self.refused(U.folder_retire, self.v, SUB, self.tmp / "bk", pattern=re.escape(KEY))

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = U.main(["folder-add", "--vault", str(self.v), "--parent", ROUTE, "--title", "LINE 圖文選單管理",
                         "--route-key", KEY])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["routeKey"], KEY)


class TestI18n(unittest.TestCase):
    KEYS = ("help_folder_route_key", "err_route_key_bad", "err_route_key_taken", "err_route_key_in_contract",
            "err_route_no_contract", "err_route_no_parent", "err_folder_has_route")

    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text("utf-8"))
            for k in self.KEYS:
                self.assertTrue(d.get("aib.vault_convert." + k), (loc, k))
                if loc == "en":
                    self.assertIsNone(cjk.search(d["aib.vault_convert." + k]), k)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([PY, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_X1_no_merge(self):
        self._knife("src/persona_route/route_register.py", "    if with_vault_routes:\n", "    if False:\n",
                    "TestFolderAddRouteKey.test_K4_load_contract_merges_vault_routes")

    def test_X2_permission_not_inherited(self):
        self._knife("src/persona_route/route_register.py", '"aiWritable": p.get("aiWritable"),', '"aiWritable": True,',
                    "TestFolderAddRouteKey.test_K4_load_contract_merges_vault_routes")

    def test_X3_index_skips_routed_sub(self):
        self._knife("tools/vault/uid_layout_convert.py", 'or bool(f.get("inheritsFrom")))', ")",
                    "TestFolderAddRouteKey.test_K1_existing_folder_gets_key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
