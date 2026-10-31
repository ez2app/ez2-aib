#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""一步開席接第二大腦(Andy 11-01 00:15「為什麼不能直接接已有大腦,或綁目前第一席的第二大腦」;@34396 工單)。
疊在 test-newproj-bootstrap-7c510-1025 的沙盒上(開席 / bind_one 假的、newproj 走真的),再加一個真的 uid 版面 Vault
+ 沙盒契約(folder-add --route-key 走真的)。頁面建的專案卡沒有 route_key,這裡一律用那種卡。

F1 --folder <路由代號>:bind_seat 收到那條、卡補記 route_key、回條 route_key
F2 --folder seat:<艙位碼>/<席碼>:跟那一席同一個大腦;那一席沒綁 ⇒ why=brain-unresolved、0 席
F3 --folder new:在 product.expert.draft 底下照專案標題真的建夾(proj.<uid 前 12 碼>)、綁上、卡補記;
   預演不建;重跑(帶 --seat)接同一個、不建第二個
F4 開席前就擋:對不到 ⇒ brain-unresolved;AI 不可寫 ⇒ brain-rejected;卡已記別的大腦 ⇒ folder-mismatch;都是 0 席
F5 aib newproj --list-brains:列可寫的大腦(含 Vault 自掛、筆記數)、不列不可寫的;seats 帶每席 brain_route_key、root 標 captain、
   crown 照右欄 👑(受派優先,其次開席早)、每艙一席排最前
F6 新鍵四語都有、英文值沒有中日韓字
F7(r1 H1)--folder new 的上層路由 AI 不可寫 ⇒ 開席前擋、不建夾
F8(r1 M2/M3)標題建不了夾(. 開頭 / 帶 /)⇒ 開席前擋;建夾失敗 ⇒ next 帶 --folder new;reindex 失敗只提醒、照常綁完
F9(r1 M5/M7)掃描總預算用完標 notes_capped;🤖 橋接席 / uuid 形狀不對的 root 不戴冠
(r1 M6 併在 F5:uid 版面數索引真路徑,不數契約字面的舊夾)
F9 續(r2 M5/M7/L6)空目錄也扣預算、用完後回 None+capped;跨艦隊時冠給第一艦隊;uid 查不到索引 folder 給空
F10(r2 M4)建夾握 aib vault 寫入端同一把 .ez2/.write.lock;刀 X6 拿掉 ⇒ F10 紅
刀(複本,走 run_guarded):X1 解出的路由沒記下 ⇒ F1 紅;X2 拿掉 mismatch 擋 ⇒ F4 紅;X3 不建新夾 ⇒ F3 紅;
   X4 拿掉上層路由檢查 ⇒ F7 紅;X5 續做那一行不帶 --folder ⇒ F8 紅
"""
import importlib.util
import json
import os
import re
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "vault"))
sys.path.insert(0, str(REPO / "src" / "persona_route"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("boot_base_1101", str(HERE / "test-newproj-bootstrap-7c510-1025.py"))
BB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BB)

CONTRACT = {"contract": "test", "version": 1, "routes": [
    {"routeKey": "product.expert.draft", "module": "P", "folder": "11-Drafts", "dataLevel": "一般", "aiReadable": True, "aiWritable": True},
    {"routeKey": "core.handoff", "module": "C", "folder": "02-Handoff", "dataLevel": "一般", "aiReadable": True, "aiWritable": True},
    {"routeKey": "core.refine", "module": "C", "folder": "01-Refine", "dataLevel": "一般", "aiReadable": True, "aiWritable": True},
    {"routeKey": "core.blueprint", "module": "C", "folder": "03-Blueprint", "dataLevel": "一般", "aiReadable": True, "aiWritable": False},
]}
sys.path.insert(0, str(REPO / "src" / "cockpit"))
from seat_status import SEAT_SCHEMA  # noqa: E402  右欄同一個 schema 字串(戴冠要驗得過)
GROUP, DRAFT = "fld_p_group_5d4b60295e72", "fld_p_route_d229ff09b371"
CGROUP, REFINE = "fld_c_group_f7d29f6a675c", "fld_c_route_1f2e3d4c5b6a"
NEW_KEYS = ("help_list_brains", "brain_new_bad_uid", "brain_new_no_vault", "brain_new_no_parent", "brain_seat_unbound",
            "brain_new_dry", "brain_new_made", "brain_new_failed", "brain_new_bad_title", "brain_new_reindex_warn")


def _item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


class FolderBase(BB.Sandbox):
    def setUp(self):
        super().setUp()
        import uid_layout_convert as U  # noqa: E402
        self.U = U
        contract = Path(self.tmp, "contract.json")
        contract.write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        os.environ.pop("EZ2_ROUTE_REGISTRY", None)    # 席位環境帶的真契約路徑:不清會被 reconcile 選走
        os.environ.update({"EZ2_ROUTE_CONTRACT_SPEC": str(contract), "EZ2_EZ2AI_REPO": str(Path(self.tmp, "no-repo"))})
        items = [_item(GROUP, GROUP, "", 1, "P-Ez2 AI專家", "group", "folder.group.p.5d4b6029"),
                 _item(DRAFT, GROUP + "/" + DRAFT, GROUP, 2, "P20-專家進行中", "route", "product.expert.draft"),
                 _item(CGROUP, CGROUP, "", 1, "C-Ez2 Core系統底層", "group"),
                 _item(REFINE, CGROUP + "/" + REFINE, CGROUP, 2, "C20-共用知識", "route", "core.refine")]
        for it in items:
            (self.vault / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        (self.vault / ".ez2").mkdir(parents=True, exist_ok=True)
        (self.vault / ".ez2" / "migration-state.json").write_text(json.dumps({"mode": "uid_layout"}), encoding="utf-8")
        U.write_indexes(self.vault, json.loads(json.dumps(items)), {}, [{"uid": "3aaaa1", "path": "x"}], "t0")
        # uid 版面:core.refine 的真路徑在索引裡(3 篇);契約字面 01-Refine 是轉換前的舊夾(5 篇),不該被算到
        for n in range(3):
            (self.vault / CGROUP / REFINE / ("n%d.md" % n)).write_text("x", encoding="utf-8")
        (self.vault / "01-Refine").mkdir()
        for n in range(5):
            (self.vault / "01-Refine" / ("old%d.md" % n)).write_text("x", encoding="utf-8")
        self.state["by_seat"] = {}
        # 頁面建的卡:沒有 route_key(沒有席位 ⇒ newproj 只建卡、不記資料夾)
        rc, out, text = self.run_np("--title", "官網改版", "--cwd", os.path.join(self.tmp, "proj"))
        self.assertEqual(rc, 0, text)
        self.page_uid = out["project_uid"]
        self.assertFalse(self.card(self.page_uid).get("route_key"), "夾具:頁面卡沒有 route_key")

    # 每一席各自的綁定(基底那支只有一份,分不出「別席綁了什麼」)
    def _fake_bind(self, root, seat_id, card, actor, brain=None, **kw):
        self.binds.append({"seat": seat_id, "card": card, "brain": brain, "dry_run": kw.get("dry_run", False)})
        if not kw.get("dry_run"):
            cur = self.state["by_seat"].setdefault(seat_id, {})
            if card:
                cur["persona_card"] = card
            if brain:
                cur["brain_route_key"] = brain
        return {"reproject": {}}

    def _fake_status(self, root, seat_id):
        got = self.state.get("by_seat", {}).get(seat_id)
        return {"agent": dict(got) if got else None}

    def card(self, uid):
        return self.np.load_card(self.root, uid) or {}

    def page_boot(self, *extra):
        return self.run_np("--new-seat", "--project-uid", self.page_uid, "--persona-card", BB.CARD, "--actor", BB.ACTOR, *extra)

    def real_binds(self):
        return [b for b in self.binds if not b["dry_run"] and b["card"]]

    def other_seat(self, brain=None, role="root"):
        su = str(uuid.uuid4())
        rec = {"seat_id": "e9f8a", "session_uuid": su, "role": role, "cabin_id": BB.CABIN, "window_id": "@1",
               "provider": "claude", "pid": os.getpid(), "schema": SEAT_SCHEMA}
        (self.seats / "w1.json").write_text(json.dumps(rec), encoding="utf-8")
        if brain:
            self.state["by_seat"]["e9f8a"] = {"persona_card": "other1", "brain_route_key": brain}
        return "abcd/%s" % su[-5:]


class Folder(FolderBase):
    def test_f1_existing_route(self):
        rc, out, text = self.page_boot("--folder", "core.refine")
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertEqual([b["brain"] for b in self.real_binds()], ["core.refine"], self.binds)
        self.assertEqual(self.card(self.page_uid).get("route_key"), "core.refine", "卡要補記 route_key")
        self.assertEqual(out["route_key"], "core.refine", out)

    def test_f2_same_brain_as_seat(self):
        addr = self.other_seat()
        with mock.patch.object(self.CBP, "seat_is_alive", return_value=True):
            rc, out, _ = self.page_boot("--folder", "seat:" + addr)
        self.assertEqual((out["stage"], out["why"]), ("resolve", "brain-unresolved"), out)
        self.assertEqual(self.opened, [], "那一席沒綁 ⇒ 開席前就擋")
        self.state["by_seat"]["e9f8a"] = {"persona_card": "other1", "brain_route_key": "core.refine"}
        with mock.patch.object(self.CBP, "seat_is_alive", return_value=True):
            rc, out, text = self.page_boot("--folder", "seat:" + addr)
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertEqual([b["brain"] for b in self.real_binds()], ["core.refine"])
        self.assertEqual(self.card(self.page_uid).get("route_key"), "core.refine")

    def test_f3_new_brain(self):
        key = "proj." + self.page_uid.split(":", 1)[1][:12]
        rc, out, text = self.page_boot("--folder", "new", "--dry-run")
        self.assertEqual((rc, out.get("route_key"), out.get("brain_new")), (0, key, True), text)
        rk = lambda: [i for i in json.loads((self.vault / ".ez2/index/route-key-index.json").read_text("utf-8"))["items"]
                      if i.get("routeKey") == key]
        self.assertEqual(rk(), [], "預演不建夾")
        rc, out, text = self.page_boot("--folder", "new")
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        hit = rk()
        self.assertEqual(len(hit), 1, "要真的建夾並掛代號")
        self.assertEqual(hit[0]["inheritsFrom"], "product.expert.draft")
        self.assertTrue((self.vault / hit[0]["folderPath"]).is_dir())
        labels = json.loads((self.vault / ".ez2/display/labels.json").read_text("utf-8"))["folders"]
        self.assertEqual(labels[hit[0]["folderId"]]["title"], "官網改版", "夾名照專案標題")
        self.assertEqual([b["brain"] for b in self.real_binds()], [key])
        self.assertEqual(self.card(self.page_uid).get("route_key"), key)
        # 重跑(續做)接同一個,不建第二個
        rec = json.loads((self.seats / "w2.json").read_text(encoding="utf-8"))
        with mock.patch.object(self.CBP, "seat_is_alive", return_value=True):
            rc, out, text = self.page_boot("--folder", "new", "--seat", "abcd/%s" % rec["session_uuid"][-5:])
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertEqual(len(rk()), 1)
        n = [i for i in json.loads((self.vault / ".ez2/index/folder-id-index.json").read_text("utf-8"))["items"]
             if i.get("parentFolderId") == DRAFT]
        self.assertEqual(len(n), 1, "Drafts 底下只有一個新夾")

    def test_f4_refused_before_open(self):
        for value, why in (("no-such-brain", "brain-unresolved"), ("core.blueprint", "brain-rejected")):
            rc, out, _ = self.page_boot("--folder", value)
            self.assertEqual((out["stage"], out["why"]), ("resolve", why), out)
        # 卡已經記了 core.handoff(基底夾具那張)⇒ 換別的不代做
        rc, out, _ = self.run_np("--new-seat", "--project-uid", self.uid, "--persona-card", BB.CARD, "--actor", BB.ACTOR,
                                 "--folder", "core.refine")
        self.assertEqual((out["stage"], out["why"]), ("resolve", "folder-mismatch"), out)
        self.assertEqual(self.opened, [], "都在開席前擋")
        self.assertEqual(self.seat_files(), [])

    def test_f5_list_brains(self):
        self.other_seat(brain="core.refine")
        self.U.folder_add(self.vault, DRAFT, "LINE 圖文選單", False, "proj.line-menu")
        self.U.reindex(self.vault)
        with mock.patch.dict(os.environ, {"EZ2_AIB_INSTANCE": ""}):
            rc, out, text = self.run_np("--list-brains")
        self.assertEqual((rc, out["ok"]), (0, True), text)
        by = {b["route_key"]: b for b in out["brains"]}
        self.assertIn("core.refine", by)
        self.assertNotIn("core.blueprint", by, "AI 不可寫的不列")
        self.assertEqual(by["core.refine"]["notes"], 3, "uid 版面要數索引裡的真路徑,不是契約字面的舊夾")
        self.assertEqual(by["core.refine"]["folder"], CGROUP + "/" + REFINE)
        self.assertTrue(by["core.refine"]["updated_epoch"])
        self.assertEqual((by["proj.line-menu"]["source"], by["proj.line-menu"]["title"]), ("vault", "LINE 圖文選單"))
        seat = [s for s in out["seats"] if s["seat_id"] == "e9f8a"]
        self.assertEqual(len(seat), 1, out["seats"])
        self.assertEqual((seat[0]["brain_route_key"], seat[0]["captain"], seat[0]["crown"]), ("core.refine", True, True))
        self.assertTrue(seat[0]["seat"].startswith("abcd/"))
        self.assertEqual(out["seats"][0]["seat_id"], "e9f8a", "戴冠的排最前")
        # 第二個 root 開得比較晚:也是 captain,但不戴冠;受派(captain_assigned)的贏過開席早的
        late = {"seat_id": "f0f0f", "session_uuid": str(uuid.uuid4()), "role": "root", "cabin_id": BB.CABIN,
                "window_id": "@8", "started_epoch": 2000000000, "schema": SEAT_SCHEMA,
                "provider": "claude"}
        (self.seats / "w8.json").write_text(json.dumps(late), encoding="utf-8")
        old = json.loads((self.seats / "w1.json").read_text(encoding="utf-8"))
        old["started_epoch"] = 1000000000
        (self.seats / "w1.json").write_text(json.dumps(old), encoding="utf-8")
        rc, out, _ = self.run_np("--list-brains", "--cabin", BB.CABIN)
        crowns = [(s["seat_id"], s["captain"]) for s in out["seats"] if s["crown"]]
        self.assertEqual(crowns, [("e9f8a", True)], out["seats"])
        self.assertTrue([s for s in out["seats"] if s["seat_id"] == "f0f0f"][0]["captain"])
        late["captain_assigned"] = True
        (self.seats / "w8.json").write_text(json.dumps(late), encoding="utf-8")
        rc, out, _ = self.run_np("--list-brains", "--cabin", BB.CABIN)
        self.assertEqual([s["seat_id"] for s in out["seats"] if s["crown"]], ["f0f0f"], "受派優先(同右欄 👑)")
        rc, out, _ = self.run_np("--list-brains", "--cabin", "nope")
        self.assertEqual(out["seats"], [], "只列那一艙")

    def test_f7_new_parent_not_writable(self):
        bad = json.loads(json.dumps(CONTRACT))
        bad["routes"][0]["aiWritable"] = False
        path = Path(self.tmp, "contract-ro.json")
        path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        os.environ["EZ2_ROUTE_CONTRACT_SPEC"] = str(path)
        before = (self.vault / ".ez2/index/folder-id-index.json").read_bytes()
        rc, out, _ = self.page_boot("--folder", "new")
        self.assertEqual((out["stage"], out["why"]), ("resolve", "brain-unresolved"), out)
        self.assertIn("route_not_ai_writable", out["hint"])
        self.assertEqual(self.opened, [], "上層不准寫 ⇒ 開席前擋")
        self.assertEqual((self.vault / ".ez2/index/folder-id-index.json").read_bytes(), before, "不建夾")

    def test_f8_new_bad_title_and_failures(self):
        for bad in (".內部專案", "官網/API"):
            rc, out, _ = self.run_np("--title", bad, "--cwd", os.path.join(self.tmp, "proj"))
            uid = out["project_uid"]
            rc, out, _ = self.run_np("--new-seat", "--project-uid", uid, "--persona-card", BB.CARD, "--actor", BB.ACTOR, "--folder", "new")
            self.assertEqual((out["stage"], out["why"]), ("resolve", "brain-unresolved"), out)
        self.assertEqual(self.opened, [], "標題建不了夾 ⇒ 開席前擋")
        import brain_pick  # noqa: E402
        # 建夾失敗:停在 persona、續做那一行帶 --folder new
        with mock.patch.object(brain_pick, "create_new", side_effect=SystemExit("disk full")):
            rc, out, _ = self.page_boot("--folder", "new")
        self.assertEqual((out["stage"], out["why"]), ("persona", "brain-create-failed"), out)
        self.assertIn("--folder new", out["next"])
        self.assertIn("--seat abcd/", out["next"])
        # reindex 失敗:夾和代號都在 ⇒ 照常綁完,只提醒
        import contextlib  # noqa: E402
        import io  # noqa: E402
        err = io.StringIO()
        with mock.patch.object(self.U, "reindex", side_effect=SystemExit("no index")), contextlib.redirect_stderr(err):
            rc, out, text = self.run_np(*out["next"].split()[2:])
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertIn("reindex", err.getvalue() + text, "提醒要說 reindex 沒跑成")
        key = "proj." + self.page_uid.split(":", 1)[1][:12]
        self.assertEqual(self.card(self.page_uid).get("route_key"), key)

    def test_f9_scan_budget_and_crown_rules(self):
        import brain_pick  # noqa: E402
        with mock.patch.object(brain_pick, "NOTE_SCAN_MAX", 2):
            rc, out, _ = self.run_np("--list-brains")
        by = {b["route_key"]: b for b in out["brains"]}
        self.assertTrue(any(b.get("notes_capped") for b in out["brains"]), by)
        # 開得早的 root 是 🤖 橋接引擎 / uuid 形狀不對 ⇒ 不戴冠,冠給驗得過的那一席
        bot = {"seat_id": "b0b0b", "session_uuid": str(uuid.uuid4()), "role": "root", "cabin_id": BB.CABIN,
               "window_id": "@3", "provider": "codexbot", "started_epoch": 1, "schema": SEAT_SCHEMA}
        odd = {"seat_id": "c0c0c", "session_uuid": "not-a-uuid", "role": "root", "cabin_id": BB.CABIN,
               "window_id": "@4", "provider": "claude", "started_epoch": 2, "schema": SEAT_SCHEMA}
        (self.seats / "w3.json").write_text(json.dumps(bot), encoding="utf-8")
        (self.seats / "w4.json").write_text(json.dumps(odd), encoding="utf-8")
        self.other_seat()
        rc, out, _ = self.run_np("--list-brains", "--cabin", BB.CABIN)
        self.assertEqual([s["seat_id"] for s in out["seats"] if s["crown"]], ["e9f8a"], out["seats"])
        # r2 M7:第二艦隊的 root 開得更早,冠仍給第一艦隊(右欄組序:純數字艦隊數值序)
        early = {"seat_id": "d0d0d", "session_uuid": str(uuid.uuid4()), "role": "root", "cabin_id": BB.CABIN, "window_id": "@6",
                 "provider": "claude", "fleet_id": "2", "started_epoch": 5, "schema": SEAT_SCHEMA}
        (self.seats / "w6.json").write_text(json.dumps(early), encoding="utf-8")
        rec1 = json.loads((self.seats / "w1.json").read_text(encoding="utf-8"))
        rec1.update({"fleet_id": "1", "started_epoch": 50})
        (self.seats / "w1.json").write_text(json.dumps(rec1), encoding="utf-8")
        rc, out, _ = self.run_np("--list-brains", "--cabin", BB.CABIN)
        self.assertEqual([s["seat_id"] for s in out["seats"] if s["crown"]], ["e9f8a"], "第一艦隊優先(同右欄 _grp_key)")
        # r2 M5:一堆空目錄也受預算管;用完之後的路由回 notes=None、capped,不是 0
        for i in range(40):
            (self.vault / CGROUP / REFINE / ("empty%02d" % i)).mkdir()
        with mock.patch.object(brain_pick, "NOTE_SCAN_MAX", 20):
            rc, out, _ = self.run_np("--list-brains")
        by = {b["route_key"]: b for b in out["brains"]}
        self.assertTrue(by["core.refine"]["notes_capped"], by["core.refine"])
        with mock.patch.object(brain_pick, "NOTE_SCAN_MAX", 1):     # 第一條就用完 ⇒ 後面的 core.refine 不開
            rc, out, _ = self.run_np("--list-brains")
        by = {b["route_key"]: b for b in out["brains"]}
        self.assertEqual((by["core.refine"]["notes"], by["core.refine"]["notes_capped"]), (None, True),
                         "預算用完後的路由照實回 None + capped,不是 0")
        # r2 L6:uid 版面查不到索引的契約路由不給舊字面路徑
        self.assertEqual(by["core.handoff"]["folder"], "", by["core.handoff"])

    def test_f10_create_uses_vault_write_lock(self):
        import vault_cli as VC  # noqa: E402
        calls = []
        real = VC._locked

        def spy(v):
            calls.append(str(v))
            return real(v)
        with mock.patch.object(VC, "_locked", side_effect=spy):
            rc, out, text = self.page_boot("--folder", "new")
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertEqual(len(calls), 1, "建夾要握 aib vault 寫入端同一把鎖")

    def test_f6_i18n(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in NEW_KEYS:
                self.assertTrue(d.get("aib.newproj." + k), "%s 缺 %s" % (lang, k))
                if lang == "en":
                    self.assertIsNone(cjk.search(d["aib.newproj." + k]), k)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell, expect):
        self._knife_mod("tools/project/seat_bootstrap.py", needle, repl, cell, expect)

    def _knife_mod(self, rel, needle, repl, cell, expect):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl,
                        tests=(name, "test-newproj-bootstrap-7c510-1025.py")) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=30, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        text = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + text)
        self.assertIn("AssertionError", text, "紅要紅在斷言,不是夾具壞掉\n" + text)
        self.assertIn(expect, text, "紅的原因要是預期的那一條\n" + text)

    def test_x1_picked_route_dropped(self):
        # 只拿掉 project() 帶的 --folder 砍不出紅:newproj 會退回「這一席現在綁的大腦」補卡(persona 那步已綁),兩條互為備援
        self._knife("        self.brain_route, self.brain_plan = route, plan\n", "        self.brain_route, self.brain_plan = \"\", plan\n",
                    "Folder.test_f1_existing_route", "core.refine")

    def test_x2_mismatch_gate_removed(self):
        self._knife("        if card_route and card_route != route:\n", "        if False:\n", "Folder.test_f4_refused_before_open", "folder-mismatch")

    def test_x4_new_parent_check_removed(self):
        self._knife_mod("tools/project/brain_pick.py", "        if not ok:\n            return None, None, T(\"aib.newproj.folder_rejected\", {\"key\": NEW_PARENT_ROUTE,",
                        "        if False:\n            return None, None, T(\"aib.newproj.folder_rejected\", {\"key\": NEW_PARENT_ROUTE,",
                        "Folder.test_f7_new_parent_not_writable", "Folder.test_f7_new_parent_not_writable")

    def test_x5_next_cmd_drops_folder(self):
        self._knife("        if want:                                   # #469 r1 M3", "        if False:                                   # #469 r1 M3",
                    "Folder.test_f8_new_bad_title_and_failures", "--folder new")

    def test_x6_vault_lock_dropped(self):
        self._knife_mod("tools/project/brain_pick.py", "    with VC._locked(vault):", "    if True:",
                        "Folder.test_f10_create_uses_vault_write_lock", "同一把鎖")

    def test_x3_new_folder_not_created(self):
        self._knife("            brain_pick.create_new(self.brain_plan)\n", "            pass\n", "Folder.test_f3_new_brain", "Folder.test_f3_new_brain")


if __name__ == "__main__":
    unittest.main()
