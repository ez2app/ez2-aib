#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""記憶索引改用 uid 現查路徑(10-16 @977e3,@abb89 派)。全在暫存目錄:不碰真 ~/.ez2-aib、真 Vault。

10-16 真庫轉 uid 版面後發現:~/.ez2-aib/memory-index/notes.jsonl 每筆存 Vault 的**絕對路徑**,
aib_memory.note_text 直讀它 ⇒ 庫一搬家,開席帶回(brief)的內文靜默變空。cards.json 的 source.vault_root
也是絕對路徑,persona_pack / persona_fit 沒傳 vault 就用它 ⇒ 搬家後人格包說卡不見。

M1  舊格式(絕對路徑)、舊版面:庫搬家後 note_text 照樣帶回內文。
M2  舊格式、uid 版面:庫搬家後靠 Vault 的 uid-index 找回來。
M3  新寫的索引列不存絕對路徑,只存 uid + 相對路徑提示;搬家後照樣讀得到。
M4  uid 版面裡 aib 自己後寫、uid-index 還沒收的筆記:沒有提示也找得到(<uid>.md)。
M5  真的找不到 ⇒ 報錯,不靜默給空:note_text raise、brief 列出「找不到」、show 回非 0。
M6  人格卡:cards.json 記的是舊庫根,EZ2_VAULT_DIR 指新庫 ⇒ persona_fit / persona_pack 讀新庫的卡。
M7  舊版面新寫的那筆(只有 rel)被拖到別夾:用 rel 的檔名 <uid>-<標題>.md 找回來。
M8  rel 是 ../ 跑出 Vault ⇒ 不讀外面的檔,當找不到報錯。
K1  刀測:同一支 M1 在 main 的舊 aib_memory 上要紅(經 aib_proc_guard.run_guarded 跑子行程)。
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
for _p in ("memory", "identity", "cockpit", "persona_route"):
    sys.path.insert(0, str(ROOT / "src" / _p))

BODY = "這是交接正文 uid-lookup。"


def note_md(uid, body=BODY):
    return "---\nuid: %s\ntitle: 測試\n---\n%s\n" % (uid, body)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.t = Path(self._td.name)
        self.home = self.t / "aib"
        (self.home / "memory-index").mkdir(parents=True)
        self.a = self.t / "A" / "Ez2"
        self.b = self.t / "B" / "Ez2"
        self.a.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT")}
        env.update({"HOME": str(self.t / "home"), "EZ2_AIB_HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.home),
                    "EZ2_AIB_LOCALE": "zh-TW", "EZ2_VAULT_DIR": str(self.a)})
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        import aib_memory
        self.M = aib_memory

    def index(self, rows):
        self.M.index_path().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def move(self):
        """庫整個搬到 B,EZ2_VAULT_DIR 跟著指 B(搬家的唯一動作)。"""
        self.b.parent.mkdir(parents=True)
        shutil.move(str(self.a), str(self.b))
        os.environ["EZ2_VAULT_DIR"] = str(self.b)

    def uid_layout(self, vault, notes):
        (vault / ".ez2" / "index").mkdir(parents=True, exist_ok=True)
        (vault / ".ez2" / "migration-state.json").write_text(json.dumps({"mode": "uid_layout"}), encoding="utf-8")
        (vault / ".ez2" / "index" / "uid-index.json").write_text(
            json.dumps({"items": [{"uid": u, "path": p} for u, p in notes.items()]}), encoding="utf-8")


class M1LegacyOldLayout(Base):
    def test_moved_vault_still_reads_body(self):
        f = self.a / "C80-系統記憶交接" / "3abc12-交接.md"
        f.parent.mkdir(parents=True)
        f.write_text(note_md("3abc12"), encoding="utf-8")
        self.index([{"uid": "3abc12", "kind": "handoff", "title": "交接", "path": str(f), "seat_uid": "s1", "created_ms": 1}])
        self.move()
        rec = self.M.entries()[0]
        self.assertEqual(self.M.note_text(rec), BODY)
        self.assertIn(BODY, self.M.brief("s1"))


class M2LegacyUidLayout(Base):
    def test_moved_uid_vault_found_by_uid_index(self):
        rel = "fld_c_group_aaaa/fld_c_route_bbbb/3abc13.md"
        (self.a / rel).parent.mkdir(parents=True)
        (self.a / rel).write_text(note_md("3abc13"), encoding="utf-8")
        self.uid_layout(self.a, {"3abc13": rel})
        # 轉換前寫的那筆:路徑是舊版面的中文夾,早就不存在
        self.index([{"uid": "3abc13", "kind": "progress", "title": "進度",
                     "path": str(self.a / "C80-系統記憶交接" / "3abc13-進度.md"), "created_ms": 1}])
        self.move()
        self.assertEqual(self.M.note_text(self.M.entries()[0]), BODY)


class M3NewRowsHaveNoAbsolutePath(Base):
    def test_write_stores_uid_and_relative_hint(self):
        route_dir = "fld_c_group_aaaa/fld_c_route_bbbb"
        (self.a / route_dir).mkdir(parents=True)
        self.uid_layout(self.a, {})
        (self.a / ".ez2" / "index" / "route-key-index.json").write_text(json.dumps({"items": [
            {"routeKey": "core.handoff", "folderId": "fld_c_route_bbbb", "folderPath": route_dir}]}), encoding="utf-8")
        out = {"note": {"uid": "3abc14", "file_name": "3abc14-x.md"}, "markdown": note_md("3abc14")}
        route = {"routeKey": "core.handoff", "folder": "C80-系統記憶交接"}
        with mock.patch.object(self.M, "build_note", return_value=(out, route, "cat_core_handoff", [], [])), \
                mock.patch.object(self.M, "seat_context", return_value={"seat_uid": "s3"}):
            rec = self.M.write("progress", "進度", BODY)
        self.assertTrue(Path(rec["path"]).is_file(), "write 回給人看的還是完整路徑")
        row = json.loads(self.M.index_path().read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("path", row, "索引不存絕對路徑")
        self.assertEqual(row["rel"], route_dir + "/3abc14.md")
        self.move()
        self.assertEqual(self.M.note_text(self.M.entries()[0]), BODY)


class M4UidNoteNotInIndexYet(Base):
    def test_found_by_uid_filename(self):
        rel = "fld_c_group_aaaa/fld_c_route_bbbb/3abc15.md"
        (self.a / rel).parent.mkdir(parents=True)
        (self.a / rel).write_text(note_md("3abc15"), encoding="utf-8")
        self.uid_layout(self.a, {})                       # uid-index 還沒收這則
        self.index([{"uid": "3abc15", "kind": "note", "title": "n", "created_ms": 1}])
        self.move()
        self.assertEqual(self.M.note_text(self.M.entries()[0]), BODY)


class M7OldLayoutNoteDraggedElsewhere(Base):
    def test_found_by_rel_file_name(self):
        """舊版面新寫的那筆(只有 rel):在 Obsidian 裡被拖到別夾,檔名 <uid>-<標題>.md 照樣找得到。"""
        moved = self.a / "06-工作區 Work" / "3abc17-進度.md"
        moved.parent.mkdir(parents=True)
        moved.write_text(note_md("3abc17"), encoding="utf-8")
        self.index([{"uid": "3abc17", "kind": "progress", "title": "進度",
                     "rel": "C80-系統記憶交接/3abc17-進度.md", "created_ms": 1}])
        self.assertEqual(self.M.note_text(self.M.entries()[0]), BODY)


class M8RelStaysInsideVault(Base):
    def test_rel_escaping_vault_is_ignored(self):
        outside = self.t / "secret.md"
        outside.write_text(note_md("3abc18", "外面的檔"), encoding="utf-8")
        self.index([{"uid": "3abc18", "kind": "note", "title": "x", "rel": "../../secret.md", "created_ms": 1}])
        with self.assertRaises(self.M.MemoryError_):
            self.M.note_text(self.M.entries()[0])


class M5MissingIsLoud(Base):
    def setUp(self):
        super().setUp()
        self.index([{"uid": "3abc16", "kind": "handoff", "title": "不見了", "seat_uid": "s5",
                     "path": str(self.a / "C80" / "3abc16-不見了.md"), "created_ms": 1}])

    def test_note_text_raises(self):
        with self.assertRaises(self.M.MemoryError_):
            self.M.note_text(self.M.entries()[0])

    def test_brief_says_missing(self):
        out = self.M.brief("s5")
        self.assertIn("3abc16", out)
        self.assertIn("找不到", out)

    def test_show_fails(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = self.M.main(["show", "--uid", "3abc16"])
        self.assertNotEqual(rc, 0)
        self.assertIn("3abc16", err.getvalue())


class M6PersonaCardFollowsLiveVault(Base):
    def setUp(self):
        super().setUp()
        self.rel = "10-Roles/abc123/角色.md"
        (self.a / self.rel).parent.mkdir(parents=True)
        self.card = "---\nuid: abc123\nname: 測試員\n---\n# 測試員\n\n## 職責\n測試。\n"
        (self.a / self.rel).write_text(self.card, encoding="utf-8")
        (self.home / "identity").mkdir(parents=True)
        self.cards = self.home / "identity" / "cards.json"
        self.cards.write_text(json.dumps({"source": {"vault_root": str(self.a)}, "cards": {"abc123": {
            "uid": "abc123", "card_path_rel": self.rel, "display_name": "測試員",
            "persona_sha": hashlib.sha256(self.card.encode()).hexdigest()}}}, ensure_ascii=False), encoding="utf-8")
        self.move()                                       # cards.json 還記著 A

    def test_persona_fit_reads_card_in_live_vault(self):
        import persona_fit
        entry = json.loads(self.cards.read_text(encoding="utf-8"))["cards"]["abc123"]
        self.assertIn("測試員", persona_fit.card_text(self.cards, entry))

    def test_persona_pack_finds_card_in_live_vault(self):
        import persona_pack
        sid = "00000000-0000-0000-0000-000000abcde1"
        (self.home / "identity" / "bindings.json").write_text(json.dumps({"bindings": {
            persona_pack.tail5(sid): {"persona_uid": "abc123", "route_key": ""}}}), encoding="utf-8")
        pack = persona_pack.render_pack(sid, self.home)
        self.assertNotEqual(pack.get("status"), "card_missing", pack)
        self.assertIn("測試員", pack.get("text") or "")


KNIFE_CHILD = "AIB_MEMUID_KNIFE_CHILD"


@unittest.skipIf(os.environ.get(KNIFE_CHILD), "子行程裡不再開刀")
class K1KnifeOldCodeIsRed(unittest.TestCase):
    """main 上的舊 aib_memory 換進暫存副本,跑 M1 要紅(證明這支測試抓得到靜默變空)。"""

    def test_old_aib_memory_fails_m1(self):
        try:
            old = subprocess.run(["git", "-C", str(ROOT), "show", "origin/main:aib/src/memory/aib_memory.py"],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("沒有 git")
        if old.returncode != 0 or "def note_text" not in old.stdout:
            self.skipTest("拿不到 origin/main 的舊版")
        if "def resolve_note_path" in old.stdout:
            self.skipTest("main 已含本修正")
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "aib"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (copy / "src" / "memory" / "aib_memory.py").write_text(old.stdout, encoding="utf-8")
            env = dict(os.environ, **{KNIFE_CHILD: "1", "PYTHONDONTWRITEBYTECODE": "1"})
            r = run_guarded([sys.executable, str(copy / "tests" / Path(__file__).name), "M1LegacyOldLayout"],
                            cwd=str(copy), env=env, max_procs=50, timeout=120)
        self.assertIsNone(r.tripped, "看門狗動手了:%s" % r.tripped)
        self.assertNotEqual(r.returncode, 0, "舊版應該紅:\n" + (r.stdout or "") + (r.stderr or ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
