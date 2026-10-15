#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席未送接手包 ⇒ delivered=false／「未送」;有卡則 delivered=true。"""
from __future__ import annotations

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

REPO = Path(os.environ.get("AIB_SEAT_B_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "identity"))
import open_ack as OA  # noqa: E402
import seat_load  # noqa: E402

OLD = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaa11111"
NEW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbb22222"


def _sandbox_env(tmp: Path, project_uid: str = "") -> dict:
    """seat_load.py apply 的沙盒:家目錄、AIB 家、Claude/Codex 設定都指暫存。"""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "EZ2_AIB_HOME": str(tmp / "aib"),
           "CLAUDE_CONFIG_DIR": str(tmp / "claude"), "EZ2_CODEX_SESSIONS_ROOT": str(tmp / "codex-sessions"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    if project_uid:
        env["AIB_PROJECT_UID"] = project_uid
    (tmp / "aib").mkdir(parents=True, exist_ok=True)
    return env


class HandoffDeliverOpen(unittest.TestCase):
    def test_R_open_without_pack_undelivered(self):
        """open/apply 無專案卡 ⇒ delivered=false + 「未送」。"""
        with tempfile.TemporaryDirectory() as td:
            pre = Path(td) / "preamble.txt"
            pre.write_text("你是 @abcde\ndm 位址 @abcde\n其餘\n", encoding="utf-8")
            env_bak = os.environ.pop("AIB_PROJECT_UID", None)
            os.environ.pop("AIB_PROJECT_CARD_JSON", None)
            try:
                out = seat_load.insert_handoff_pack(
                    str(pre), "00000000-0000-0000-0000-00000000abcd", "abcde", td
                )
            finally:
                if env_bak is not None:
                    os.environ["AIB_PROJECT_UID"] = env_bak
            self.assertFalse(out["delivered"])
            self.assertEqual(out["open_ack"].get("signal"), "未送")
            self.assertFalse(out["open_ack"].get("delivered"))
            # preamble 未被塞接手包 header
            body = pre.read_text(encoding="utf-8")
            self.assertNotIn("[AIB 接手包 v1]", body)
            sig = OA.right_col_red_signal(out["open_ack"])
            self.assertTrue(sig["red"])
            self.assertEqual(sig["signal"], "未送")

    def test_G_open_with_project_delivers(self):
        with tempfile.TemporaryDirectory() as td:
            pre = Path(td) / "preamble.txt"
            pre.write_text("你是 @abcde\ndm 位址 @abcde\n", encoding="utf-8")
            os.environ["AIB_PROJECT_UID"] = "proj:demo-0926"
            try:
                out = seat_load.insert_handoff_pack(
                    str(pre), "00000000-0000-0000-0000-00000000abcd", "abcde", td
                )
            finally:
                os.environ.pop("AIB_PROJECT_UID", None)
            self.assertTrue(out["delivered"], out)
            self.assertEqual(out["status"], "ok")
            body = pre.read_text(encoding="utf-8")
            self.assertIn("[AIB 接手包 v1]", body)
            self.assertIn("proj:demo-0926", body)
            ack_path = Path(td) / "state" / "open-ack" / "abcde.json"
            self.assertTrue(ack_path.is_file())
            disk = json.loads(ack_path.read_text(encoding="utf-8"))
            self.assertTrue(disk["delivered"])

    def test_R_missing_ack_red_via_apply_ack_lines(self):
        st = OA.mark_pack_delivered(None, project_uid="proj:x", asked_at="t")
        st2 = OA.apply_ack_lines(st, ["已載入人格 p:1"])  # 缺接手回執
        self.assertTrue(st2["red"])
        self.assertIn("handoff", st2["judge"]["missing"])


class EntryAndKnife(unittest.TestCase):
    """到達性:open_ack/handoff_pack/project_card 的生產入口是 seat_load.py
    (aib 的 aib_load_old_seat 叫 `seat_load.py apply`)。入口格在沙盒裡真跑它;還原刀在副本落一刀,入口格必紅。
    ★ 刀的每一步寫在格裡(到達性閘看不見 helper);子跑用 AIB_SEAT_B_ROOT 指副本、只點名入口格,不會遞迴。"""

    def test_E_seat_load_py_apply_without_project_writes_undelivered(self):
        """真跑 seat_load.py apply(不給專案)⇒ 接手包未送、open-ack 狀態檔 signal=未送。"""
        tmp = Path(tempfile.mkdtemp(prefix="hdo-entry-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env = _sandbox_env(tmp)
        r = subprocess.run([sys.executable, str(REPO / "src" / "cockpit" / "seat_load.py"), "apply",
                            "--provider", "codex", "--from", OLD, "--to", NEW, "--window", "@3",
                            "--aib-home", str(tmp / "aib")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout)
        self.assertFalse(out["handoff_pack"]["delivered"], out["handoff_pack"])
        self.assertEqual(out["handoff_pack"]["status"], "no_project")
        disk = json.loads((tmp / "aib" / "state" / "open-ack" / (NEW[-5:] + ".json")).read_text(encoding="utf-8"))
        self.assertFalse(disk["delivered"])
        self.assertEqual(disk["signal"], "未送")

    def test_E_seat_load_py_apply_with_project_delivers(self):
        """真跑 seat_load.py apply(AIB_PROJECT_UID)⇒ 接手包寫進開場說明、open-ack delivered=true。"""
        tmp = Path(tempfile.mkdtemp(prefix="hdo-entry-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env = _sandbox_env(tmp, project_uid="proj:demo-0926")
        r = subprocess.run([sys.executable, str(REPO / "src" / "cockpit" / "seat_load.py"), "apply",
                            "--provider", "codex", "--from", OLD, "--to", NEW, "--window", "@3",
                            "--aib-home", str(tmp / "aib")],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout)
        self.assertTrue(out["handoff_pack"]["delivered"], out["handoff_pack"])
        self.assertEqual(out["handoff_pack"]["project_uid"], "proj:demo-0926")
        body = Path(out["deliver_file"]).read_text(encoding="utf-8")
        self.assertIn("[AIB 接手包 v1]", body)
        self.assertIn("proj:demo-0926", body)
        disk = json.loads((tmp / "aib" / "state" / "open-ack" / (NEW[-5:] + ".json")).read_text(encoding="utf-8"))
        self.assertTrue(disk["delivered"])
        self.assertEqual(disk["project_uid"], "proj:demo-0926")

    def test_E_seat_load_main_apply_with_project(self):
        """同一路徑走 seat_load.main(apply 子命令)—— seat_load 自己那層(apply/main)的入口。"""
        tmp = Path(tempfile.mkdtemp(prefix="hdo-entry-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env = _sandbox_env(tmp, project_uid="proj:main-0926")
        buf = io.StringIO()
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(env)
            with contextlib.redirect_stdout(buf):
                rc = seat_load.main(["apply", "--provider", "codex", "--from", OLD, "--to", NEW,
                                     "--window", "@3", "--aib-home", str(tmp / "aib")])
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["handoff_pack"]["delivered"], out["handoff_pack"])
        self.assertEqual(out["handoff_pack"]["project_uid"], "proj:main-0926")

    def test_K_knife_open_ack_missing_signal_reds_entry(self):
        """open_ack.py 副本:mark_pack_missing 不再標「未送」⇒ 缺專案那條入口格必紅。"""
        if os.environ.get("AIB_HDO_KNIFE_CHILD") == "1":
            self.skipTest("子跑不再落刀")
        root = Path(tempfile.mkdtemp(prefix="hdo-knife-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        layer = root / "src" / "identity" / "open_ack.py"
        text = layer.read_text(encoding="utf-8")
        anchor = '    out["delivered"] = False\n    out["signal"] = DELIVERY_NOT_SENT\n    return out\n'
        self.assertEqual(text.count(anchor), 1, "錨要恰好出現一次,否則刀沒落")
        layer.write_text(text.replace(anchor, '    out["delivered"] = False\n    out["signal"] = ""\n    return out\n'),
                         encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(anchor), 0, "刀沒落")
        env = dict(os.environ, AIB_SEAT_B_ROOT=str(root), AIB_HDO_KNIFE_CHILD="1", PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "EntryAndKnife.test_E_seat_load_py_apply_without_project_writes_undelivered",
                            "HandoffDeliverOpen.test_R_open_without_pack_undelivered"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=300)
        red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", r.stderr, re.M))
        for cell in ("test_E_seat_load_py_apply_without_project_writes_undelivered",
                     "test_R_open_without_pack_undelivered"):
            self.assertIn(cell, red, r.stderr[-800:])

    def test_K_knife_seat_load_delivered_false_reds_entry(self):
        """seat_load.py 副本:有專案時回 delivered=False ⇒ 三條「有專案」入口格必紅。"""
        if os.environ.get("AIB_HDO_KNIFE_CHILD") == "1":
            self.skipTest("子跑不再落刀")
        root = Path(tempfile.mkdtemp(prefix="hdo-knife-"))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        layer = root / "src" / "cockpit" / "seat_load.py"
        text = layer.read_text(encoding="utf-8")
        anchor = '        "status": "ok",\n        "delivered": True,\n'
        self.assertEqual(text.count(anchor), 1, "錨要恰好出現一次,否則刀沒落")
        layer.write_text(text.replace(anchor, '        "status": "ok",\n        "delivered": False,\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(anchor), 0, "刀沒落")
        env = dict(os.environ, AIB_SEAT_B_ROOT=str(root), AIB_HDO_KNIFE_CHILD="1", PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                            "EntryAndKnife.test_E_seat_load_py_apply_with_project_delivers",
                            "EntryAndKnife.test_E_seat_load_main_apply_with_project",
                            "HandoffDeliverOpen.test_G_open_with_project_delivers"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=300)
        red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", r.stderr, re.M))
        for cell in ("test_E_seat_load_py_apply_with_project_delivers",
                     "test_E_seat_load_main_apply_with_project",
                     "test_G_open_with_project_delivers"):
            self.assertIn(cell, red, r.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
