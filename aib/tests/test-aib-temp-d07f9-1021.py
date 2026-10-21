#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib temp(PROPOSAL-gaps v2 F;@ee4f8 第十件):臨時主機變更登記到期日與拆法,過期會亮。

T1 add ⇒ 回 tmp-xxxxxx、存 host / desc / undo / until / seat;YYYY-MM-DD ⇒ 當天 23:59:59 本機時區
T2 欄位檢查:空白 / 超長 / until 已過 / ISO 沒時區 / 席碼不對 ⇒ 拒,不寫檔;控制 / 零寬字清掉
T3 list 照到期排、過期的標 overdue;check 有過期 rc 1、沒有 rc 0
T4 done ⇒ 從 list 消失(--all 看得到);再 done 一次 ⇒ already_done;id 不對 ⇒ bad_id / not_found
T5 aib doctor:過期沒拆 ⇒ MANUAL(列 id / 主機 / 席);沒有 ⇒ OK;一律讀 doctor 傳進來的庫(環境變數指別處也一樣)
T6 上限:還沒拆的滿 MAX_OPEN ⇒ too_many;拆過超過 30 天的會被清掉
T7 aib temp 接上、四語都有、--bogus rc 2
刀(複本):K1 overdue 判斷永遠 False ⇒ T3 紅;K2 doctor 改回 setdefault ⇒ T5 紅
"""
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit", REPO / "src" / "bootstrap", HERE):
    sys.path.insert(0, str(_p))
import aib_temp as AT  # noqa: E402
import aib_doctor  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime(2026, 10, 21, 20, 0, 0, tzinfo=TZ)


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-temp-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW"}

    def add(self, until="2026-10-25", host="eu01", seat="abcde", now=NOW, **kw):
        return AT.add(host, kw.get("desc", "測試網址 /ez2-hg-test"), until, kw.get("undo", "刪 nginx location 再 reload"),
                      seat=seat, env=self.env, now=now)

    def stored(self):
        p = AT.store_path(self.env)
        return json.loads(p.read_text())["items"] if p.exists() else []


class Temp(Base):
    def test_t1_add(self):
        r = self.add()
        self.assertRegex(r["id"], r"^tmp-[0-9a-f]{6}$")
        self.assertEqual(r["until"], "2026-10-25T23:59:59+08:00")
        self.assertEqual((r["host"], r["seat"], r["done_at"]), ("eu01", "abcde", None))
        self.assertEqual(self.stored()[0]["undo"], "刪 nginx location 再 reload")
        r = self.add(until="2026-10-22T09:00:00+08:00")
        self.assertEqual(r["until"], "2026-10-22T09:00:00+08:00")

    def test_t2_validation(self):
        cases = [(dict(host=" "), "empty_host"), (dict(host="h" * 121), "too_long_host"),
                 (dict(until="2026-10-20"), "until_past"), (dict(until="2026-10-30T09:00:00"), "bad_until"),
                 (dict(until="next week"), "bad_until"), (dict(seat="ABCDE"), "bad_seat"),
                 (dict(undo=""), "empty_undo"), (dict(desc="字" * 201), "too_long_desc")]
        for kw, why in cases:
            with self.assertRaises(AT.TempError, msg=why) as cm:
                self.add(**kw)
            self.assertEqual(cm.exception.why, why)
        self.assertEqual(self.stored(), [])
        r = self.add(host="eu01\u200b\x1b[31m")
        self.assertEqual(r["host"], "eu01 [31m")

    def test_t3_list_check(self):
        a = self.add(until="2026-10-30")
        b = self.add(until="2026-10-22T09:00:00+08:00")
        later = NOW + datetime.timedelta(days=2)
        rows = AT.listing(self.env, now=later)
        self.assertEqual([r["id"] for r in rows], [b["id"], a["id"]])
        self.assertEqual([r["overdue"] for r in rows], [True, False])
        self.assertEqual([r["id"] for r in AT.overdue(self.env, now=later)], [b["id"]])
        self.assertEqual(AT.overdue(self.env, now=NOW), [])
        AT.done(b["id"], "拆了", by="abcde", env=self.env)
        self.assertEqual(AT.main(["check"], out=io.StringIO(), env=self.env), 0, "真時鐘下 10-30 還沒到")
        real = datetime.datetime.now().astimezone()
        c = self.add(until=(real - datetime.timedelta(hours=1)).isoformat(timespec="seconds"),
                     now=real - datetime.timedelta(days=1))
        buf = io.StringIO()
        self.assertEqual(AT.main(["check", "--json"], out=buf, env=self.env), 1)
        self.assertEqual([r["id"] for r in json.loads(buf.getvalue())["overdue"]], [c["id"]])

    def test_t4_done(self):
        a = self.add()
        r = AT.done(a["id"], "已刪 location", by="abcde", env=self.env, now=NOW)
        self.assertEqual((r["done_by"], r["done_why"]), ("abcde", "已刪 location"))
        self.assertEqual(AT.listing(self.env, now=NOW), [])
        self.assertEqual(len(AT.listing(self.env, include_done=True, now=NOW)), 1)
        for iid, why in ((a["id"], "already_done"), ("tmp-zzzzzz", "bad_id"), ("tmp-000000", "not_found")):
            with self.assertRaises(AT.TempError) as cm:
                AT.done(iid, env=self.env)
            self.assertEqual(cm.exception.why, why)

    def test_t5_doctor(self):
        r = aib_doctor.check_temp(self.root, REPO, env=self.env, now=NOW)
        self.assertEqual(r["status"], aib_doctor.OK)
        a = self.add(until="2026-10-22T09:00:00+08:00", host="eu01-sftp")
        r = aib_doctor.check_temp(self.root, REPO, env=self.env, now=NOW + datetime.timedelta(days=1))
        self.assertEqual(r["status"], aib_doctor.MANUAL)
        self.assertIn(a["id"], r["detail"])
        self.assertIn("eu01-sftp", r["detail"])
        self.assertIn("abcde", r["detail"])
        self.assertTrue(r["remedy"])
        with tempfile.TemporaryDirectory() as other:
            r = aib_doctor.check_temp(self.root, REPO, env=dict(self.env, EZ2_AIB_STATE_ROOT=other),
                                      now=NOW + datetime.timedelta(days=1))
            self.assertEqual(r["status"], aib_doctor.MANUAL, "環境變數指 A、doctor 傳 B ⇒ 讀 B")
            r = aib_doctor.check_temp(Path(other), REPO, env=self.env, now=NOW + datetime.timedelta(days=1))
            self.assertEqual(r["status"], aib_doctor.OK)

    def test_t6_limits(self):
        old = self.add()
        AT.done(old["id"], env=self.env, by="", now=NOW)
        for _ in range(AT.MAX_OPEN):
            self.add()
        with self.assertRaises(AT.TempError) as cm:
            self.add()
        self.assertEqual(cm.exception.why, "too_many")
        later = NOW + datetime.timedelta(days=31)
        for r in AT.listing(self.env, now=NOW):
            AT.done(r["id"], env=self.env, by="", now=later - datetime.timedelta(days=1))
        self.add(until="2026-12-31", now=later)
        ids = [i["id"] for i in self.stored()]
        self.assertNotIn(old["id"], ids, "拆過超過 30 天的清掉")
        self.assertEqual(len(ids), AT.MAX_OPEN + 1)

    def test_t7_wired(self):
        src = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn('exec "$T_PY3" "$HERE/aib_temp.py" "$@"', src)
        keys = [k for k in json.loads((REPO / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
                if k.startswith(("aib.temp.", "aib.doctor.check.temp."))]
        self.assertGreaterEqual(len(keys), 20)
        for lang in ("en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            self.assertEqual([k for k in keys if k not in d], [], lang)
        self.assertEqual(AT.main(["--bogus"], out=io.StringIO(), env=self.env), 2)
        buf = io.StringIO()
        self.assertEqual(AT.main(["add", "h", "d", "--until", "x", "--undo", "u", "--json"], out=buf, env=self.env), 2)
        self.assertEqual(json.loads(buf.getvalue())["why"], "bad_until")


class Knife(unittest.TestCase):
    def test_k1_never_overdue(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/aib_temp.py",
                        'overdue=bool(not i.get("done_at") and u is not None and u <= t)', "overdue=False",
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Temp.test_t3_list_check"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


class Knife2(unittest.TestCase):
    def test_k2_setdefault(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/bootstrap/aib_doctor.py", '    env["EZ2_AIB_STATE_ROOT"] = str(state_root)',
                        '    env.setdefault("EZ2_AIB_STATE_ROOT", str(state_root))', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Temp.test_t5_doctor"], cwd=str(root),
                            max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
