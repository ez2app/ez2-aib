#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""開席 → 綁人格 → 綁專案 一步到位(aib newproj --new-seat --project-uid --persona-card;@7c510 10-25,@abb89 裁掛 newproj、疊 #373)。
全在 tmp 沙盒;開席與 bind_one 用假的(記下呼叫),專案那步走真的 run_newproj。

B1 三步全過 ⇒ stage=done、席位檔有 project_uid、bind_seat 收到 card + 專案資料夾、回條 seat=<艙>/<席碼>
B2 短艙號對不到 / 艙裡沒 seats ⇒ stage=resolve、why=cabin-unresolved,0 個席被開;4 碼唯一對到 ⇒ 換成完整艙名
B3 人格卡不在登記表 / 專案 uid 不存在 ⇒ stage=resolve,開席前就擋
B4 開席逾時(窗已開)⇒ stage=open、why=open-timeout、seat_opened、next 帶 --seat
B5 續做:帶 --seat 且人格已綁同一張 ⇒ 不開席、跳過人格、只綁專案;已綁別張 ⇒ why=persona-differs 不代換
B6 頁面路徑 --confirm touchid:沒生物辨識(AIB_TOUCHID=0)⇒ why=no_biometry、0 個席被開;confirm 寫錯 ⇒ bad-confirm
B7 散熱(核心 room_heat 四級)hot ⇒ why=heat-high、0 個席被開、hint 帶同一個讀數;艙鎖在握 ⇒ why=busy,過期的鎖會被換掉
B8 回條契約:why 是代碼、hint 是人話、next 是可直接跑的那一行;四語字典都有 boot_* 鍵
B9 --deliver:綁完人格把人格包寄給新席(CBP.deliver_persona,假的記呼叫),回條帶 persona_pack;沒帶就不寄
刀(複本):K1 拿掉 no_biometry 那道 ⇒ B6 紅;K2 拿掉散熱擋 ⇒ B7 紅
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src" / "identity"))
sys.path.insert(0, str(REPO / "tools" / "project"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

PY = sys.executable
BIN_AIB = REPO.parent / "bin" / "aib"
CABIN = "20261025-000000-abcd"
CARD = "p3clmm"
ACTOR = "dev@7c510/claude"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-boot-")
        self.home = Path(self.tmp, "home")
        self.root = self.home / ".ez2-aib"
        self.vault = Path(self.tmp, "vault")
        for d in (self.home, self.vault, self.root / "ledger", self.root / "identity", Path(self.tmp, "proj")):
            d.mkdir(parents=True)
        self.seats = self.root / "instances" / CABIN / "seats"
        self.seats.mkdir(parents=True)
        (self.root / "instances" / CABIN / "instance-token").write_text("tok", encoding="utf-8")
        sys.path.insert(0, str(REPO / "src" / "memory"))
        import binding_projection as BP  # noqa: E402
        (self.root / "identity" / "cards.json").write_text(json.dumps({
            "schema_version": BP.PYRAMID_VERSION,
            "cards": {CARD: {"persona_uid": CARD, "route_key": "core.handoff", "display_name": "設計助理"}}}), encoding="utf-8")
        self._saved = dict(os.environ)
        for k in ("EZ2_VAULT_ROOT", "EZ2_ROUTE_CONTRACT", "EZ2_AIB_INSTANCE", "EZ2_AIHUB_SOCKET"):
            os.environ.pop(k, None)
        os.environ.update({"HOME": str(self.home), "EZ2_VAULT_DIR": str(self.vault), "EZ2_AIB_STATE_ROOT": str(self.root),
                           "EZ2_AIB_HOME": str(self.root), "EZ2_AIB_LOCALE": "zh-TW", "AIB_GENES": "0", "AIB_AUTO_BIND": "0",
                           "AIB_TOUCHID": "0", "AIB_NEWSEAT_TIMEOUT_S": "3"})
        self.np = _load("new_project_boot", "tools/project/new_project.py")
        self.uid = self._make_project()
        import bind_one as BO  # noqa: E402
        import create_and_bind_persona as CBP  # noqa: E402
        sys.path.insert(0, str(REPO / "src" / "cockpit"))
        import room_heat as RH  # noqa: E402
        self.BO, self.CBP, self.RH = BO, CBP, RH
        self.binds, self.opened = [], []
        self.state = {}
        self._patches = [
            mock.patch.object(BO, "bind_seat", side_effect=self._fake_bind),
            mock.patch.object(BO, "seat_status", side_effect=self._fake_status),
            mock.patch.object(CBP, "open_new_seat", side_effect=self._fake_open),
            mock.patch.object(CBP, "current_cabin", return_value=CABIN),
            mock.patch.object(RH, "measure", return_value={"level": "ok", "cells": {}}),   # 散熱共用核心 room_heat;沙盒不真量
            mock.patch.object(CBP, "deliver_persona", side_effect=self._fake_pack),
            mock.patch.object(self.np, "deliver_handoff", return_value={"ok": True, "address": "abcd/x"}),
        ]
        self.packs = []
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 夾具 ──
    def _make_project(self):
        """只建卡(不開席、不綁席),行程內跑:刀測的複本裡沒有 bin/aib,走子行程會假紅。"""
        rc, out, text = self.run_np("--title", "Demo 2F", "--folder", "core.handoff", "--cwd", os.path.join(self.tmp, "proj"))
        self.assertEqual(rc, 0, text)
        return out["project_uid"]

    def _seat_file(self, n=2, seat_id="a1b2c"):
        su = str(uuid.uuid4())
        rec = {"seat_id": seat_id, "session_uuid": su, "role": "dev", "cabin_id": CABIN, "window_id": "@%d" % n,
               "provider": "claude", "pid": os.getpid(), "schema": "seat-v1"}
        (self.seats / ("w%d.json" % n)).write_text(json.dumps(rec), encoding="utf-8")
        return seat_id, rec

    def _fake_open(self, role, engine="claude", cabin="", timeout=300, interval=3.0, sleep=None, clock=None, model=""):
        self.opened.append({"role": role, "engine": engine, "cabin": cabin})
        if self.state.get("open_timeout"):
            raise self.CBP.NewSeatError("新席的窗已開,但 3 秒內沒等到它的席碼", opened=True)
        seat_id, rec = self._seat_file()
        return seat_id, rec, cabin

    def _fake_bind(self, root, seat_id, card, actor, brain=None, **kw):
        self.binds.append({"seat": seat_id, "card": card, "brain": brain, "dry_run": kw.get("dry_run", False)})
        if not kw.get("dry_run"):
            self.state["bound"] = {"persona_card": card, "brain_route_key": brain}
        return {"reproject": {}}

    def _fake_pack(self, seat_id, seat_rec, instance, name, vault_dir, dry_run=False):
        self.packs.append({"seat": seat_id, "name": name, "cabin": instance})
        return {"ok": True, "address": "abcd/x", "stage": "delivered", "expect_reply": "人格已載入 deadbeef"}

    def _fake_status(self, root, seat_id):
        return {"agent": dict(self.state["bound"]) if self.state.get("bound") else None}

    def run_np(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.np.main(["--entry", "newproj"] + list(argv))   # aib newproj 的派發就是這樣帶的
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        return rc, json.loads(lines[-1]), buf.getvalue()

    def boot(self, *extra):
        return self.run_np("--new-seat", "--project-uid", self.uid, "--persona-card", CARD, "--actor", ACTOR, *extra)

    def seat_files(self):
        return sorted(p.name for p in self.seats.glob("w*.json"))


class Boot(Sandbox):
    def test_b1_all_three(self):
        rc, out, text = self.boot()
        self.assertEqual(rc, 0, text)
        self.assertEqual((out["ok"], out["stage"]), (True, "done"), out)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0]["cabin"], CABIN)
        real = [b for b in self.binds if not b["dry_run"]]
        self.assertEqual(len(real), 1, self.binds)
        self.assertEqual((real[0]["card"], real[0]["brain"]), (CARD, "core.handoff"))
        rec = json.loads((self.seats / "w2.json").read_text(encoding="utf-8"))
        self.assertEqual(rec.get("project_uid"), self.uid, rec)
        self.assertEqual(out["seat"], "abcd/%s" % rec["session_uuid"][-5:], "回條用右欄那種 <艙位碼>/<席碼>")
        self.assertEqual(out["persona_card"], CARD)
        self.assertNotIn("approved\": true", text)

    def test_b2_cabin(self):
        rc, out, _ = self.boot("--cabin", "ffff")
        self.assertEqual((out["stage"], out["why"]), ("resolve", "cabin-unresolved"), out)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.seat_files(), [])
        rc, out, _ = self.boot("--cabin", "abcd")
        self.assertEqual(out["stage"], "done", out)
        self.assertEqual(out["cabin"], CABIN, "4 碼唯一對到 ⇒ 換成完整艙名")
        shutil.rmtree(self.seats)
        self.state.clear()
        rc, out, _ = self.boot("--cabin", CABIN)
        self.assertEqual((out["stage"], out["why"]), ("resolve", "cabin-no-seats"), out)

    def test_b3_persona_or_project_missing(self):
        rc, out, _ = self.run_np("--new-seat", "--project-uid", self.uid, "--persona-card", "nope99", "--actor", ACTOR)
        self.assertEqual((out["stage"], out["why"]), ("resolve", "persona-not-found"), out)
        rc, out, _ = self.run_np("--new-seat", "--project-uid", "proj:0000000000000000", "--persona-card", CARD, "--actor", ACTOR)
        self.assertEqual((out["stage"], out["why"]), ("resolve", "project-not-found"), out)
        rc, out, _ = self.run_np("--new-seat", "--persona-card", CARD, "--actor", ACTOR)
        self.assertEqual(out["why"], "need-project-and-persona")
        self.assertEqual(self.opened, [], "開席前就要擋")

    def test_b4_open_timeout(self):
        self.state["open_timeout"] = True
        rc, out, _ = self.boot()
        self.assertEqual((out["stage"], out["why"], out.get("seat_opened")), ("open", "open-timeout", True), out)
        self.assertIn("--persona-card %s" % CARD, out["next"])
        self.assertEqual(self.binds, [], "沒等到席碼就不綁")

    def test_b5_resume(self):
        seat_id, rec = self._seat_file(n=5, seat_id="c3d4e")
        addr = "abcd/%s" % rec["session_uuid"][-5:]
        self.state["bound"] = {"persona_card": CARD, "brain_route_key": "core.handoff"}
        with mock.patch.object(self.CBP, "seat_is_alive", return_value=True):
            rc, out, text = self.boot("--seat", addr)
        self.assertEqual((rc, out["stage"]), (0, "done"), text)
        self.assertEqual(self.opened, [], "續做不開席")
        self.assertEqual([b for b in self.binds if not b["dry_run"]], [], "人格同一張 ⇒ 跳過")
        self.assertEqual(json.loads((self.seats / "w5.json").read_text(encoding="utf-8")).get("project_uid"), self.uid)
        self.state["bound"] = {"persona_card": "zzzzzz", "brain_route_key": "core.handoff"}
        with mock.patch.object(self.CBP, "seat_is_alive", return_value=True):
            rc, out, _ = self.boot("--seat", addr)
        self.assertEqual((out["stage"], out["why"]), ("persona", "persona-differs"), out)

    def test_b6_page_path_needs_biometry(self):
        rc, out, _ = self.boot("--confirm", "touchid")
        self.assertEqual((out["stage"], out.get("why")), ("resolve", "no_biometry"), out)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.seat_files(), [], "0 個席被開")
        self.assertIn("aib newproj --new-seat", out["hint"], "hint 要列替代路(終端機那一行)")
        rc, out, _ = self.boot("--confirm", "faceid")
        self.assertEqual(out["why"], "bad-confirm")

    def test_b7_heat_and_lock(self):
        with mock.patch.object(self.RH, "measure", return_value={"level": "hot", "cells": {"load1": {"state": "hot", "text": "load 30"}}}):
            rc, out, _ = self.boot()
        self.assertEqual((out["stage"], out.get("why")), ("open", "heat-high"), out)
        self.assertIn("load 30", out.get("hint", ""), "擋的數字要跟 aib room heat 顯示的同一個")
        self.assertEqual(self.opened, [])
        SB = _load("seat_bootstrap_t", "tools/project/seat_bootstrap.py")
        lock = SB.lock_path(self.root, CABIN)
        got, _ = SB.acquire_lock(lock, "proj:other", stale_s=400)
        self.assertTrue(got)
        rc, out, _ = self.boot()
        self.assertEqual((out["stage"], out["why"]), ("open", "busy"), out)
        self.assertEqual(self.opened, [])
        lock.write_text(json.dumps({"project_uid": "proj:old", "at": int(time.time()) - 9999}), encoding="utf-8")
        rc, out, _ = self.boot()
        self.assertEqual(out["stage"], "done", "過期的鎖要被換掉")
        self.assertFalse(lock.exists(), "開完要放鎖")

    def test_b8_receipt_contract_and_i18n(self):
        rc, out, _ = self.boot("--cabin", "ffff")
        self.assertEqual(set(["ok", "stage", "seat", "why", "hint", "next"]) - set(out), set())
        self.assertRegex(out["why"], r"^[a-z0-9_-]+$")
        self.assertTrue(out["next"].startswith("aib newproj --new-seat --project-uid "))
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("boot_no_biometry", "boot_heat_high", "boot_busy", "boot_done", "help_persona_card", "help_confirm"):
                self.assertIn("aib.newproj." + k, d, "%s 缺 %s" % (loc, k))


    def test_b9_deliver(self):
        rc, out, _ = self.boot()
        self.assertEqual(self.packs, [], "沒帶 --deliver 不寄人格包")
        self.assertIsNone(out.get("persona_pack"))
        shutil.rmtree(self.seats); self.seats.mkdir(); self.state.clear()
        rc, out, text = self.boot("--deliver")
        self.assertEqual(out["stage"], "done", text)
        self.assertEqual(len(self.packs), 1, "--deliver ⇒ 綁完寄一次人格包")
        self.assertEqual((self.packs[0]["seat"], self.packs[0]["name"], self.packs[0]["cabin"]), ("a1b2c", "設計助理", CABIN))
        self.assertEqual(out["persona_pack"]["ok"], True, out)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell, expect):
        name = Path(__file__).name
        with knife_copy(REPO, "tools/project/seat_bootstrap.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=30, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        text = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + text)
        self.assertIn("AssertionError", text, "紅要紅在斷言,不是夾具壞掉\n" + text)
        self.assertIn(expect, text, "紅的原因要是預期的那一條\n" + text)

    def test_k1_no_biometry_gate_removed(self):
        self._knife("        if not touchid_approve.available():\n", "        if False:\n", "Boot.test_b6_page_path_needs_biometry", "no_biometry")

    def test_k2_heat_gate_removed(self):
        self._knife('        if heat["block"]:\n', "        if False:\n", "Boot.test_b7_heat_and_lock", "heat-high")


if __name__ == "__main__":
    unittest.main()
