#!/usr/bin/env python3
"""aib vault newpost --meta-json / --appendable 與 aib vault append(0.1.25;維護者 10-19 點頭,條件三條)。

M1 白名單欄位照 JSON 寫進 frontmatter;privacy_level 調高到 restricted;verify、doctor 綠
M2 核心管的鍵(uid / routeKey / aliases / created)與不認得的鍵 ⇒ 整筆拒、庫位元組不變
M3 型別不對(people 是字串、source 不是物件清單、值是 bool、record_id 是數字)⇒ 整筆拒、不默默丟
M4 privacy_level 只收英文四級(public / internal / restricted / sensitive),比 internal 低或中文 / 不認得 ⇒ 拒;
   origin / ai_memory 列舉外 ⇒ 拒
M5 字串值裡的控制字元(含換行)、方向字元、零寬字清掉;換行不能撐破 frontmatter
M5b U+2028 / U+2029 也清掉:不能藉 splitlines 斷行偽造 append_route(沒 --appendable 的那篇 append 照樣拒)
M7 單獨代理字元 / NaN / Infinity ⇒ 整筆拒、庫裡不留半個檔
M6 CLI:--meta-json 不是 JSON ⇒ 結束碼 2、不寫;合法 ⇒ 0
A1 append 到 --appendable 那篇:正文接在尾巴、updated 換成 ISO 新時間、索引路徑不變、verify / doctor 綠
A2 沒標 --appendable 的筆記 / 不存在的 uid / 空正文 ⇒ 拒、檔案不變
A3 搬到別條路由底下(doctor --fix 同步過)⇒ 拒;同一條上層路由下、兩個掛代號的子夾之間搬 ⇒ 拒;
   手改 append_route ⇒ 拒;uid-index 指錯篇(檔內 uid 不同)⇒ 拒
A4 單次正文超過 64 KB ⇒ 拒、檔案不變
A5 CLI append --body-file -(標準輸入)⇒ 0
A6 另一個行程拿著庫的寫入鎖 ⇒ append 等著(不同時改);doctor --fix 也排隊;鎖檔是符號連結 ⇒ 不跟、不清空目標
A8 Obsidian 複製出來的那篇(<uid> 1.md)經 doctor --fix 換成新 uid ⇒ 拿掉 append_route,append 拒;原篇照樣可接
A9 壞 UTF-8 的錯誤訊息講的是那個參數(不是 --meta-json)
A10 在 Obsidian 只改名(舊路徑已不在、全庫只有這篇帶這個 uid)⇒ doctor --fix 當搬家:沿用原 uid、索引路徑更新、
    append_route / record_id 保留,照樣可接
A11 複本換新 uid ⇒ record_id 拿掉(source 保留)、原篇不動;原篇也改了名、兩篇都帶同一個 uid ⇒ 兩篇都換新、都不可接
A7 CLI:--body 與 --body-file 同時給 ⇒ 結束碼 2;--body-file 超過上限 ⇒ 拒(只讀上限 + 1)
刀(複本,走 run_guarded):Y1 不認得的鍵當字串收 ⇒ M2 紅;Y2 拿掉 privacy 只能調高 ⇒ M4 紅;Y3 不清字元 ⇒ M5 紅;
   Y4 拿掉 appendable 檢查 ⇒ A2 紅;Y5 拿掉路由比對 ⇒ A3 紅;Y6 拿掉大小上限 ⇒ A4 紅;Y7 拿掉鎖 ⇒ A6 紅;
   Y8 不清 U+2028 ⇒ M5b 紅;Y9 不驗 UTF-8 ⇒ M7 紅;Y10 不比檔內 uid ⇒ A3 紅;Y11 路由只看 kind=route ⇒ A3 紅;
   Y12 doctor --fix 不上鎖 ⇒ A6 紅;Y13 換 uid 不拿掉標記 ⇒ A8 紅;Y14 改名不認搬家 ⇒ A10 紅;
   Y15 不數全庫幾篇帶這個 uid ⇒ A11 紅;Y16 複本留 record_id ⇒ A11 紅
I1 新鍵四語都有、英文值沒有中日韓字、佔位符一致
I2 privacy 等級順序跟 brain_write_plan.PRIVACY_ORDER 一樣
測試明寫 EZ2_AIB_STATE_ROOT 與暫存 EZ2_VAULT_DIR,不碰真庫。
"""
import contextlib
import fcntl
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
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "vault"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import uid_layout_convert as U  # noqa: E402
import vault_cli as VC  # noqa: E402

INBOX = "fld_x_group_bfecfae260f8"
WORK = "fld_x_group_08504630b093"
ROUTE = "fld_c_route_79bb4878617e"
CORE = "fld_c_group_f7d29f6a675c"
META = {"record_id": "rec:3f9a1c2e7b604d18", "record_type": "meeting", "record_at": "2026-10-19T01:50:00", "level": "內部",
        "people": ["@aaaa1", "Andy"], "projects": ["proj:0000000000000001"], "tasks": [],
        "source": [{"kind": "dm", "id": "q_1", "t": 1000}], "origin": "external", "ai_memory": "skip", "privacy_level": "restricted"}


def item(fid, path, parent, level, title, kind, rk=""):
    return {"folderId": fid, "routeKey": rk, "folderPath": path, "parentFolderId": parent, "level": level,
            "title": title, "legacyPaths": [title], "status": "active", "kind": kind}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aib-vaultmeta-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.v = self.tmp / "vault"
        self.state = self.tmp / "state-root"
        self.state.mkdir()
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_VAULT_DIR": str(self.v)})
        env.start()
        self.addCleanup(env.stop)
        items = [item(INBOX, INBOX, "", 1, "00-暫存區 Inbox", "group", "folder.group.x.bfecfae2"),
                 item(WORK, WORK, "", 1, "06-工作區 Work", "group", "folder.group.x.08504630"),
                 item(CORE, CORE, "", 1, "C-Ez2 Core系統底層", "group"),
                 item(ROUTE, CORE + "/" + ROUTE, CORE, 2, "C80-系統記憶交接", "route", "core.handoff")]
        for it in items:
            (self.v / it["folderPath"]).mkdir(parents=True, exist_ok=True)
        (self.v / WORK / "3aaaa1.md").write_text("---\nuid: 3aaaa1\naliases: [3aaaa1, 看板]\ntitle: \"看板\"\n---\n舊筆記\n", encoding="utf-8")
        U.write_indexes(self.v, items, {"3aaaa1": {"title": "看板"}}, [{"uid": "3aaaa1", "path": WORK + "/3aaaa1.md"}], "t0")
        self.ok_verify()

    def ok_verify(self):
        res = U.verify(self.v, cards_path=str(self.tmp / "no-cards.json"))
        self.assertTrue(res["ok"], {k: v for k, v in res.items() if isinstance(v, list) and v})

    def snapshot(self):
        return sorted((str(p.relative_to(self.v)), p.read_bytes() if p.is_file() else b"") for p in self.v.rglob("*")
                      if ".write.lock" not in p.name)

    def fm(self, rel):
        fm, _ = U.split_frontmatter((self.v / rel).read_text(encoding="utf-8"))
        return U.fm_lines_to_map(fm)

    def index(self, name):
        sub = "display" if name == "labels.json" else "index"
        return json.loads((self.v / ".ez2" / sub / name).read_text(encoding="utf-8"))

    def doctor_ok(self):
        res = VC.doctor(self.v, False, None)
        self.assertTrue(res["ok"], {k: v for k, v in res["problems"].items() if v})


@mock.patch.object(VC, "licensed", return_value=(True, "addon"))
class TestMeta(Base):
    def test_M1_meta_written(self, _lic):
        a = VC.newpost(self.v, "會議", folder=WORK, body="正文", meta=META, now_ms=1792123456000)
        kv = self.fm(a["path"])
        self.assertEqual(kv["privacy_level"], "restricted")
        for k in ("record_id", "record_type", "record_at", "level", "people", "projects", "tasks", "source", "origin", "ai_memory"):
            self.assertEqual(json.loads(kv[k]), META[k], k)
        self.assertEqual(kv["uid"], a["uid"])
        self.assertEqual(kv["routeKey"], "core.inbox")
        self.assertTrue((self.v / a["path"]).read_text(encoding="utf-8").endswith("正文\n"))
        self.ok_verify()
        self.doctor_ok()

    def test_M2_reserved_and_unknown_keys(self, _lic):
        before = self.snapshot()
        for bad in ({"uid": "3zzzzz"}, {"routeKey": "core.handoff"}, {"aliases": ["x"]}, {"created": "2020-01-01T00:00:00.000Z"},
                    {"foo": "bar"}, dict(META, updated="x")):
            with self.assertRaises(VC.VaultError, msg=bad):
                VC.newpost(self.v, "x", meta=bad)
        self.assertEqual(self.snapshot(), before)

    def test_M3_type_errors(self, _lic):
        before = self.snapshot()
        for bad in ({"people": "@a"}, {"source": ["dm"]}, {"source": [{"ok": True}]}, {"record_id": 3}, {"tasks": [1]},
                    {"source": [{"kind": ["x"]}]}, ["record_id"], "x"):
            with self.assertRaises(VC.VaultError, msg=bad):
                VC.newpost(self.v, "x", meta=bad)
        self.assertEqual(self.snapshot(), before)

    def test_M4_privacy_and_enums(self, _lic):
        before = self.snapshot()
        for bad in ({"privacy_level": "public"}, {"privacy_level": "一般"}, {"privacy_level": "confidential"},
                    {"privacy_level": "機密"}, {"privacy_level": "內部"}, {"origin": "elsewhere"}, {"ai_memory": "maybe"}):
            with self.assertRaises(VC.VaultError, msg=bad):
                VC.newpost(self.v, "x", meta=bad)
        self.assertEqual(self.snapshot(), before)
        for ok in ("restricted", "sensitive"):
            a = VC.newpost(self.v, "x", meta={"privacy_level": ok})
            self.assertEqual(self.fm(a["path"])["privacy_level"], ok)
        b = VC.newpost(self.v, "y", meta={"privacy_level": "internal"})
        self.assertEqual(self.fm(b["path"])["privacy_level"], "internal")

    def test_M5_clean_invisible(self, _lic):
        dirty = "會‮議​\nuid: 3evil1\x07"
        a = VC.newpost(self.v, "x", meta={"record_type": dirty, "people": ["A⁦ndy﻿"], "source": [{"kind": "d‍m\r\n"}]})
        kv = self.fm(a["path"])
        self.assertEqual(json.loads(kv["record_type"]), "會議uid: 3evil1")
        self.assertEqual(json.loads(kv["people"]), ["Andy"])
        self.assertEqual(json.loads(kv["source"]), [{"kind": "dm"}])
        self.assertEqual(kv["uid"], a["uid"])
        self.ok_verify()

    def test_M5b_line_separator_cannot_forge(self, _lic):
        for sep in ("\u2028", "\u2029", "\x85"):
            a = VC.newpost(self.v, "x", folder=WORK, meta={"record_id": "x%sappend_route: core.inbox" % sep})
            with self.assertRaises(VC.VaultError, msg=repr(sep)):
                VC.append(self.v, a["uid"], "偽造")
            self.assertNotIn("append_route", self.fm(a["path"]))
        self.ok_verify()

    def test_M7_bad_utf8_and_nan(self, _lic):
        before = self.snapshot()
        for bad in ({"record_id": "\ud800"}, {"people": ["\udfff"]}, {"source": [{"t": float("nan")}]}, {"source": [{"t": float("inf")}]}):
            with self.assertRaises(VC.VaultError, msg=repr(bad)):
                VC.newpost(self.v, "x", folder=WORK, meta=bad)
        with self.assertRaises(VC.VaultError):
            VC.newpost(self.v, "x\ud800", folder=WORK)
        self.assertEqual(self.snapshot(), before)
        a = VC.newpost(self.v, "可接", folder=WORK, appendable=True)
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, a["uid"], "壞\ud800")
        self.assertEqual(self.snapshot(), before)
        self.ok_verify()

    def test_M6_cli(self, _lic):
        before = self.snapshot()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = VC.main(["newpost", "--vault", str(self.v), "--title", "x", "--meta-json", "{不是", "--json"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.snapshot(), before)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = VC.main(["newpost", "--vault", str(self.v), "--title=-會議", "--meta-json", json.dumps({"record_type": "meeting"}),
                          "--appendable", "--json"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        kv = self.fm(out["path"])
        self.assertEqual(json.loads(kv["record_type"]), "meeting")
        self.assertEqual(kv["append_route"], "core.inbox")


@mock.patch.object(VC, "licensed", return_value=(True, "addon"))
class TestAppend(Base):
    def test_A1_append(self, _lic):
        a = VC.newpost(self.v, "任務索引", folder=WORK, body="## 任務", appendable=True, now_ms=1792123456000)
        u0 = self.fm(a["path"])["updated"]
        r = VC.append(self.v, a["uid"], "- [ ] 第一件\n", now_ms=1792123999000)
        self.assertEqual(r["path"], a["path"])
        VC.append(self.v, a["uid"], "- [ ] 第二件", now_ms=1792124999000)
        text = (self.v / a["path"]).read_text(encoding="utf-8")
        self.assertTrue(text.endswith("## 任務\n\n- [ ] 第一件\n\n- [ ] 第二件\n"), text)
        u1 = self.fm(a["path"])["updated"]
        self.assertNotEqual(u0, u1)
        self.assertEqual(u1, U.iso(1792124999000))
        self.assertEqual({i["uid"]: i["path"] for i in self.index("uid-index.json")["items"]}[a["uid"]], a["path"])
        self.ok_verify()
        self.doctor_ok()

    def test_A2_refuse_not_appendable(self, _lic):
        plain = VC.newpost(self.v, "一般筆記", folder=WORK)
        ok = VC.newpost(self.v, "可接", folder=WORK, appendable=True)
        before = self.snapshot()
        with self.assertRaises(VC.VaultError) as cm:
            VC.append(self.v, plain["uid"], "x")
        self.assertEqual(str(cm.exception), VC.T("err_append_not_appendable", {"uid": plain["uid"]}))
        for uid, body in (("3aaaa1", "x"), ("3zzzzz", "x"), (ok["uid"], "  \n")):
            with self.assertRaises(VC.VaultError, msg=uid):
                VC.append(self.v, uid, body)
        self.assertEqual(self.snapshot(), before)

    def test_A3_refuse_other_route(self, _lic):
        a = VC.newpost(self.v, "交接", folder=ROUTE, appendable=True)
        b = VC.newpost(self.v, "收件", folder=WORK, appendable=True)
        VC.append(self.v, b["uid"], "還在原路由")
        # 搬到別條路由(core.inbox ⇒ core.handoff)並讓 doctor --fix 同步索引
        (self.v / b["path"]).rename(self.v / CORE / ROUTE / Path(b["path"]).name)
        self.assertTrue(VC.doctor(self.v, True, self.tmp / "bk")["ok"])
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, b["uid"], "搬家後")
        # 手改 append_route
        p = self.v / a["path"]
        p.write_text(p.read_text(encoding="utf-8").replace("append_route: core.handoff", "append_route: core.inbox"), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, a["uid"], "改過標記")
        self.assertEqual(self.snapshot(), before)

    def test_A3b_sub_routes_and_wrong_index(self, _lic):
        items = json.loads((self.v / ".ez2" / "index" / "folder-id-index.json").read_text(encoding="utf-8"))["items"]
        sa, sb = "fld_c_sub_aaaaaaaaaaaa", "fld_c_sub_bbbbbbbbbbbb"
        for fid, title, rk in ((sa, "席位 A", "seat.aaaa"), (sb, "席位 B", "seat.bbbb")):   # 跟 folder-add --route-key 建出來的同形
            it = item(fid, CORE + "/" + ROUTE + "/" + fid, ROUTE, 3, title, "sub", rk)
            it["inheritsFrom"] = "core.handoff"
            items.append(it)
            (self.v / it["folderPath"]).mkdir()
        U.write_indexes(self.v, items, None, None, "t1")
        self.ok_verify()
        a = VC.newpost(self.v, "A 的", folder=sa, appendable=True)
        self.assertEqual(self.fm(a["path"])["append_route"], "seat.aaaa")
        VC.append(self.v, a["uid"], "在 A")
        dst = self.v / CORE / ROUTE / sb / Path(a["path"]).name
        (self.v / a["path"]).rename(dst)
        self.assertTrue(VC.doctor(self.v, True, self.tmp / "bk")["ok"])
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, a["uid"], "搬到 B 之後")
        self.assertEqual(self.snapshot(), before)
        # uid-index 指錯篇:x 的條目指到 y 的檔
        x = VC.newpost(self.v, "x", folder=WORK, appendable=True)
        y = VC.newpost(self.v, "y", folder=WORK, appendable=True)
        idx = self.v / ".ez2" / "index" / "uid-index.json"
        d = json.loads(idx.read_text(encoding="utf-8"))
        for it in d["items"]:
            if it["uid"] == x["uid"]:
                it["path"] = y["path"]
        idx.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        ytext = (self.v / y["path"]).read_bytes()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, x["uid"], "寫錯篇")
        self.assertEqual((self.v / y["path"]).read_bytes(), ytext)

    def test_A4_size_limit(self, _lic):
        a = VC.newpost(self.v, "大", folder=WORK, appendable=True)
        before = self.snapshot()
        with self.assertRaises(VC.VaultError):
            VC.append(self.v, a["uid"], "字" * (VC.APPEND_MAX // 3 + 1))
        self.assertEqual(self.snapshot(), before)
        VC.append(self.v, a["uid"], "x" * VC.APPEND_MAX)

    def test_A5_cli_body_file_stdin(self, _lic):
        a = VC.newpost(self.v, "日誌", folder=WORK, appendable=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(sys, "stdin", io.StringIO("--- 從標準輸入\n")):
            rc = VC.main(["append", "--vault", str(self.v), "--uid", a["uid"], "--body-file", "-", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["uid"], a["uid"])
        self.assertIn("--- 從標準輸入", (self.v / a["path"]).read_text(encoding="utf-8"))
        self.ok_verify()

    def test_A6_lock(self, _lic):
        a = VC.newpost(self.v, "鎖", folder=WORK, appendable=True)
        code = ("import sys; sys.path.insert(0, %r); import vault_cli as VC; from pathlib import Path; "
                "VC.append(Path(%r), %r, 'x')" % (str(ROOT / "tools" / "vault"), str(self.v), a["uid"]))
        with open(self.v / ".ez2" / ".write.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=8, stdin=subprocess.DEVNULL)
        self.assertNotIn("\nx\n", (self.v / a["path"]).read_text(encoding="utf-8"))
        (self.v / WORK / "手建資料夾").mkdir()                  # 讓 doctor --fix 真的有東西要修(沒問題就不拿鎖)
        code2 = ("import sys; sys.path.insert(0, %r); import vault_cli as VC; from pathlib import Path; "
                 "VC.doctor(Path(%r), True, Path(%r))" % (str(ROOT / "tools" / "vault"), str(self.v), str(self.tmp / "bk2")))
        with open(self.v / ".ez2" / ".write.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run([sys.executable, "-c", code2], capture_output=True, timeout=8, stdin=subprocess.DEVNULL)
        # 鎖檔是符號連結 ⇒ 不跟(開不起來就錯),目標不被清空
        target = self.tmp / "precious.txt"
        target.write_text("別清空", encoding="utf-8")
        lock = self.v / ".ez2" / ".write.lock"
        lock.unlink()
        lock.symlink_to(target)
        with self.assertRaises(OSError):
            VC.append(self.v, a["uid"], "y")
        self.assertEqual(target.read_text(encoding="utf-8"), "別清空")

    def test_A8_obsidian_copy_not_appendable(self, _lic):
        a = VC.newpost(self.v, "原篇", folder=WORK, appendable=True, meta={"record_id": "rec:1"})
        src = self.v / a["path"]
        shutil.copy(src, src.with_name(a["uid"] + " 1.md"))
        self.assertTrue(VC.doctor(self.v, True, self.tmp / "bk")["ok"])
        new = [p for p in (self.v / WORK).glob("*.md") if p.stem not in (a["uid"], "3aaaa1")]
        self.assertEqual(len(new), 1)
        self.assertNotIn("append_route", self.fm(str(new[0].relative_to(self.v))))
        with self.assertRaises(VC.VaultError) as cm:
            VC.append(self.v, new[0].stem, "接到複本")
        self.assertEqual(str(cm.exception), VC.T("err_append_not_appendable", {"uid": new[0].stem}))
        VC.append(self.v, a["uid"], "原篇照樣可接")
        self.ok_verify()

    def test_A10_rename_keeps_uid(self, _lic):
        a = VC.newpost(self.v, "原篇", folder=WORK, appendable=True, meta={"record_id": "rec:1"})
        src = self.v / a["path"]
        os.replace(src, src.with_name("會議紀錄.md"))
        res = VC.doctor(self.v, True, self.tmp / "bk")
        self.assertTrue(res["ok"], res.get("left"))
        self.assertEqual(sorted(p.stem for p in (self.v / WORK).glob("*.md")), sorted([a["uid"], "3aaaa1"]))
        kv = self.fm(a["path"])
        self.assertEqual(kv["uid"], a["uid"])
        self.assertIn("append_route", kv)
        self.assertEqual(json.loads(kv["record_id"]), "rec:1")
        self.assertEqual({i["uid"]: i["path"] for i in self.index("uid-index.json")["items"]}[a["uid"]], a["path"])
        VC.append(self.v, a["uid"], "改名後照樣可接")
        self.ok_verify()

    def test_A11_copy_drops_record_id(self, _lic):
        meta = {"record_id": "rec:1", "source": [{"kind": "dm", "id": "q_1", "t": 1}]}
        a = VC.newpost(self.v, "原篇", folder=WORK, appendable=True, meta=meta)
        src = self.v / a["path"]
        shutil.copy(src, src.with_name(a["uid"] + " 1.md"))
        self.assertTrue(VC.doctor(self.v, True, self.tmp / "bk")["ok"])
        new = [p for p in (self.v / WORK).glob("*.md") if p.stem not in (a["uid"], "3aaaa1")]
        self.assertEqual(len(new), 1)
        kv = self.fm(str(new[0].relative_to(self.v)))
        self.assertNotIn("record_id", kv)
        self.assertEqual(json.loads(kv["source"]), meta["source"])
        self.assertEqual(json.loads(self.fm(a["path"])["record_id"]), "rec:1")
        # 原篇也改了名 ⇒ 兩篇都帶同一個 uid,不是搬家 ⇒ 兩篇都換新、都不可接
        b = VC.newpost(self.v, "另一篇", folder=WORK, appendable=True, meta=meta)
        src = self.v / b["path"]
        shutil.copy(src, src.with_name("複本.md"))
        os.replace(src, src.with_name("改名.md"))
        self.assertTrue(VC.doctor(self.v, True, self.tmp / "bk2")["ok"])
        self.assertFalse((self.v / b["path"]).exists())
        fresh = [p for p in (self.v / WORK).glob("*.md") if p.stem not in (a["uid"], new[0].stem, "3aaaa1")]
        self.assertEqual(len(fresh), 2)
        for p in fresh:
            kv = self.fm(str(p.relative_to(self.v)))
            self.assertNotIn("record_id", kv)
            self.assertNotIn("append_route", kv)
        self.ok_verify()

    def test_A9_utf8_message_names_the_arg(self, _lic):
        a = VC.newpost(self.v, "x", folder=WORK, appendable=True)
        with self.assertRaises(VC.VaultError) as cm:
            VC.append(self.v, a["uid"], "壞\udcff")
        self.assertEqual(str(cm.exception), VC.T("err_text_not_utf8", {"key": "--body"}))
        bad = self.tmp / "bad.txt"
        bad.write_bytes(b"ok\xff\xfe")
        before = self.snapshot()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = VC.main(["append", "--vault", str(self.v), "--uid", a["uid"], "--body-file", str(bad)])
        self.assertEqual(rc, 2)
        self.assertEqual(err.getvalue().strip(), VC.T("err_text_not_utf8", {"key": "--body-file"}))
        self.assertEqual(self.snapshot(), before)

    def test_A7_cli_body_args(self, _lic):
        a = VC.newpost(self.v, "日誌", folder=WORK, appendable=True)
        big = self.tmp / "big.md"
        big.write_bytes(b"x" * (VC.APPEND_MAX + 10))
        before = self.snapshot()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                VC.main(["append", "--vault", str(self.v), "--uid", a["uid"], "--body", "x", "--body-file", str(big)])
            self.assertEqual(cm.exception.code, 2)
            rc = VC.main(["append", "--vault", str(self.v), "--uid", a["uid"], "--body-file", str(big), "--json"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.snapshot(), before)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(ROOT, "tools/vault/vault_cli.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_Y1_unknown_keys_accepted(self):
        self._knife("want = META_TYPES.get(k)\n", "want = META_TYPES.get(k, str)\n", "TestMeta.test_M2_reserved_and_unknown_keys")

    def test_Y2_privacy_lower_accepted(self):
        self._knife("if PRIVACY_RANK[v] < PRIVACY_RANK[PRIVACY_DEFAULT]:", "if False:", "TestMeta.test_M4_privacy_and_enums")

    def test_Y3_no_cleaning(self):
        self._knife('    return INVISIBLE_RE.sub("", s)', "    return s", "TestMeta.test_M5_clean_invisible")

    def test_Y4_any_note_appendable(self):
        self._knife("        if not mark:\n", "        if False:\n", "TestAppend.test_A2_refuse_not_appendable")

    def test_Y5_route_not_checked(self):
        self._knife("        if mark != here:", "        if False:", "TestAppend.test_A3_refuse_other_route")

    def test_Y6_no_size_limit(self):
        self._knife("    if size > APPEND_MAX:", "    if False:", "TestAppend.test_A4_size_limit")

    def test_Y7_no_lock(self):
        self._knife("        fcntl.flock(fd, fcntl.LOCK_EX)\n", "", "TestAppend.test_A6_lock")

    def test_Y8_no_line_separator_cleaning(self):
        self._knife("\\u200b-\\u200f\\u2028-\\u202e", "\\u200b-\\u200f\\u202a-\\u202e", "TestMeta.test_M5b_line_separator_cannot_forge")

    def test_Y9_no_utf8_check(self):
        self._knife('        data = text.encode("utf-8")\n', '        data = text.encode("utf-8", "surrogatepass")\n', "TestMeta.test_M7_bad_utf8_and_nan")

    def test_Y10_no_uid_check(self):
        self._knife("!= uid:          # 索引指錯篇", "!= uid and False:          # 索引指錯篇", "TestAppend.test_A3b_sub_routes_and_wrong_index")

    def test_Y11_route_kind_only(self):
        self._knife("        if U.is_routed(cur):", '        if cur.get("kind") == "route" and cur.get("routeKey"):', "TestAppend.test_A3b_sub_routes_and_wrong_index")

    def test_Y12_doctor_fix_unlocked(self):
        self._knife("    with _locked(Path(vault).resolve()):                  # 拿到鎖之後重掃一次再修\n",
                    "    if True:\n", "TestAppend.test_A6_lock")

    def test_Y13_copy_keeps_mark(self):
        self._knife("        if old_fm is not None and uid != have:", "        if False:", "TestAppend.test_A8_obsidian_copy_not_appendable")

    def test_Y14_rename_not_move(self):
        self._knife("(have not in used or _moved_alone(vault, have, uidx, carriers))", "have not in used",
                    "TestAppend.test_A10_rename_keeps_uid")

    def test_Y15_carriers_not_counted(self):
        self._knife("    return carriers.get(uid, 0) == 1 and all(", "    return all(", "TestAppend.test_A11_copy_drops_record_id")

    def test_Y16_copy_keeps_record_id(self):
        self._knife('(APPEND_KEY + ":", "record_id:")', '(APPEND_KEY + ":",)', "TestAppend.test_A11_copy_drops_record_id")


class TestI18n(unittest.TestCase):
    def test_I1_four_locales(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
        zh = json.loads((ROOT / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
        new = ("help_meta_json", "help_appendable", "help_append", "help_append_uid", "help_body_file", "err_meta_bad_json",
               "err_meta_not_object", "err_meta_key", "err_meta_want_string", "err_meta_want_strings", "err_meta_want_objects",
               "err_text_not_utf8", "err_meta_enum", "err_meta_privacy_lower", "err_append_empty",
               "err_append_too_big", "err_append_not_found", "err_append_not_appendable", "err_append_route_moved", "append_done")
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in ("aib.vault_cli." + x for x in new):
                self.assertTrue(d.get(k), (loc, k))
                self.assertEqual(sorted(re.findall(r"\{\w+\}", zh[k])), sorted(re.findall(r"\{\w+\}", d[k])), (loc, k))
                if loc == "en":
                    self.assertIsNone(cjk.search(d[k]), (k, d[k]))


class TestPrivacyOrder(unittest.TestCase):
    def test_I2_same_as_brain_write_plan(self):
        sys.path.insert(0, str(ROOT / "src" / "memory"))
        import brain_write_plan
        self.assertEqual(VC.PRIVACY_RANK, brain_write_plan.PRIVACY_ORDER)


if __name__ == "__main__":
    unittest.main()
