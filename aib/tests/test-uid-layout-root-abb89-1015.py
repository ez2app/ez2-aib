#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""uid 版面:查資料夾一律用「實際要寫的那個 vault」,查不到就停,不把舊名資料夾建回來(10-15 盤點 U1/U2/U6/U18)。

S1 route_register.resolve_route_folder(root=…):uid 版面 ⇒ 走那個 vault 的索引;索引沒有這條 ⇒ None(不退回契約字面);
   舊版面 ⇒ 契約字面。環境變數 EZ2_VAULT_DIR 指別處也不影響。
S2 aib memory write:沒設 EZ2_VAULT_DIR(一般終端機)、vault 在預設位置且已轉 ⇒ 落到索引的 fld_ 夾;
   索引沒有這條路由 ⇒ 報錯,而且沒有建出契約字面的舊名夾。(要 node + ez2-ai 筆記格式;環境不齊跳過)
S3 newrole --vault <uid 版面庫>,EZ2_VAULT_DIR 指別的庫 ⇒ 卡建進 --vault 那個庫的索引角色庫,不另建 10-Roles。
S4 轉換工具 verify:卡片索引掃的是這個 vault、card_path_rel 已不存在 ⇒ 列出、ok=False、給重掃指令。
治具全在 tmpdir。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _p in ("memory", "persona_route", "identity"):
    sys.path.insert(0, str(ROOT / "src" / _p))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
import route_register as RR  # noqa: E402

CONVERT = ROOT / "tools" / "vault" / "uid_layout_convert.py"
CREATE = ROOT / "src" / "identity" / "create_and_bind_persona.py"
C80 = "C-Ez2 Core系統底層/C80-系統記憶交接"
LIB = "P-Ez2 AI專家/P10-專家模板/角色庫"
CONTRACT = {"routes": [
    {"routeKey": "core.handoff", "module": "C", "folder": C80, "aiReadable": True, "aiWritable": True},
    {"routeKey": "core.inbox", "module": "C", "folder": "C-Ez2 Core系統底層/C10-共用暫存", "aiReadable": True, "aiWritable": True},
    {"routeKey": "product.expert.agent_library", "module": "P", "folder": LIB, "aiReadable": True, "aiWritable": True},
]}


def make_uid_vault(vault, routes):
    """最小 uid 版面:migration-state=uid_layout + route-key-index(只列 routes 給的那幾條)+ 那些資料夾。"""
    (vault / ".ez2" / "index").mkdir(parents=True, exist_ok=True)
    (vault / ".ez2" / "migration-state.json").write_text(json.dumps({"mode": "uid_layout"}), encoding="utf-8")
    items = []
    for rk, fp in routes.items():
        (vault / fp).mkdir(parents=True, exist_ok=True)
        items.append({"routeKey": rk, "folderId": fp.split("/")[-1], "folderPath": fp})
    (vault / ".ez2" / "index" / "route-key-index.json").write_text(json.dumps({"items": items}), encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.t = Path(self._td.name)
        self.vault = self.t / "vault"
        self.vault.mkdir()
        self.other = self.t / "other-vault"
        self.other.mkdir()


class Resolve(Base):
    def test_S1_root_decides_and_uid_layout_never_falls_back_to_literal(self):
        make_uid_vault(self.vault, {"core.handoff": "fld_c_route_aaaaaaaaaaaa"})
        with mock.patch.dict(os.environ, {"EZ2_VAULT_DIR": str(self.other)}):
            got = RR.resolve_route_folder("core.handoff", contract=CONTRACT, root=str(self.vault))
            self.assertEqual(got, "fld_c_route_aaaaaaaaaaaa", "要用呼叫端給的 vault,不是環境變數那個")
            self.assertIsNone(RR.resolve_route_folder("core.inbox", contract=CONTRACT, root=str(self.vault)),
                              "uid 版面索引沒有這條 ⇒ None;退回中文字面會被 mkdir 建回來")
            self.assertEqual(RR.resolve_route_folder("core.inbox", contract=CONTRACT, root=str(self.other)),
                             "C-Ez2 Core系統底層/C10-共用暫存", "舊版面照舊用契約字面")
            self.assertEqual(RR.resolve_route_folder("core.handoff", contract=CONTRACT, use_index=False,
                                                     root=str(self.vault)), C80, "use_index=False 仍是給人看的字面")


def _note_format_ready():
    node = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
    repo = next((p for p in (Path(os.environ.get("EZ2_AI_REPO") or "/nonexistent"), Path.home() / "ez2-ai")
                 if p.is_dir()), None)
    contract = repo / RR.DEFAULT_CONTRACT_REL if repo else None
    return (node, repo, contract) if (node and contract and contract.is_file()) else None


NOTE_ENV = _note_format_ready()


@unittest.skipUnless(NOTE_ENV, "要 node 與 ez2-ai 筆記格式(同 test-vault-uid-layout-1005 R2)")
class MemoryWrite(Base):
    def setUp(self):
        super().setUp()
        node, repo, contract = NOTE_ENV
        self.contract = json.loads(Path(contract).read_text(encoding="utf-8"))
        handoff = next(r["folder"] for r in self.contract["routes"] if r.get("routeKey") == "core.handoff")
        self.handoff_literal = handoff
        home = self.t / "home"
        (home / "Desktop" / "Obsidian").mkdir(parents=True)
        os.symlink(self.vault, home / "Desktop" / "Obsidian" / "Ez2")
        env = {k: v for k, v in os.environ.items() if k not in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT")}
        env.update({"HOME": str(home), "EZ2_ROUTE_REGISTRY": str(contract), "EZ2_AI_REPO": str(repo),
                    "EZ2_AIB_HOME": str(self.t / "aib"), "EZ2_AIB_LOCALE": "zh-TW",
                    "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")})
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        import aib_memory
        self.M = aib_memory

    def test_S2_plain_terminal_writes_into_index_folder_and_refuses_unknown_route(self):
        make_uid_vault(self.vault, {"core.handoff": "fld_c_route_bbbbbbbbbbbb"})
        rec = self.M.write("progress", "落點", "正文。", tags=["aib/test"], seat="", dry_run=True)
        self.assertEqual(Path(rec["path"]).parent.name, "fld_c_route_bbbbbbbbbbbb", rec["path"])
        idx = self.vault / ".ez2" / "index" / "route-key-index.json"
        idx.write_text(json.dumps({"items": []}), encoding="utf-8")       # 索引沒有 core.handoff
        with self.assertRaises(self.M.MemoryError_):
            self.M.write("progress", "不該寫", "正文。", tags=["aib/test"], seat="")
        self.assertFalse((self.vault / self.handoff_literal).exists(), "舊名資料夾被建回來了")


class NewRole(Base):
    def test_S3_newrole_uses_the_vault_it_was_given(self):
        make_uid_vault(self.vault, {"product.expert.agent_library": "fld_p_route_cccccccccccc"})
        (self.t / "p.md").write_text("你是「測試員」。\n\n## 職責\n測試。\n\n## 紅線\n不亂寫。\n\n## 知識域\n測試。\n",
                                     encoding="utf-8")
        (self.t / "c.json").write_text(json.dumps(CONTRACT, ensure_ascii=False), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.t / "home"), "LANG": "en_US.UTF-8",
               "EZ2_VAULT_DIR": str(self.other), "EZ2_ROUTE_REGISTRY": str(self.t / "c.json")}
        p = subprocess.run([sys.executable, str(CREATE), "--name", "庫內測試員", "--code", "t1",
                            "--prompt-file", str(self.t / "p.md"), "--vault", str(self.vault)],
                           capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        out = json.loads(p.stdout[p.stdout.index("{"):])
        self.assertEqual(Path(out["card_file"]).resolve().parent.parent,
                         (self.vault / "fld_p_route_cccccccccccc").resolve())
        self.assertFalse((self.vault / LIB).exists(), "不該在轉好的庫裡另建中文角色庫")
        self.assertFalse((self.vault / "10-Roles").exists())


class VerifyCards(Base):
    def test_S4_verify_flags_stale_card_paths(self):
        make_uid_vault(self.vault, {"product.expert.agent_library": "fld_p_route_dddddddddddd"})
        (self.vault / "fld_p_route_dddddddddddd" / "abc123").mkdir()
        (self.vault / "fld_p_route_dddddddddddd" / "abc123" / "角色.md").write_text('---\nuid: "abc123"\n---\n', encoding="utf-8")
        cards = self.t / "cards.json"
        doc = {"source": {"vault_root": str(self.vault)}, "cards": {
            "abc123": {"card_path_rel": "fld_p_route_dddddddddddd/abc123/角色.md"},
            "old999": {"card_path_rel": LIB + "/舊卡/角色.md"}}}
        cards.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        import uid_layout_convert as UC
        res = UC.check_cards(self.vault, cards)
        self.assertTrue(res["cards_checked"])
        self.assertEqual(res["cards_path_missing"], ["old999"])
        self.assertIn("card_registry.py scan --vault", res["cards_hint"])
        doc["source"]["vault_root"] = str(self.other)                    # 別的庫的索引:不判
        cards.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self.assertFalse(UC.check_cards(self.vault, cards)["cards_checked"])


if __name__ == "__main__":
    unittest.main()
