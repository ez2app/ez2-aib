#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib support 問題回報包(support_bundle.py;契約 contracts/power.json support_receipt;10-24 Andy 准精簡版)。暫存狀態根。

S1 沒 --write ⇒ support_preview、file null、一個檔都不寫(support/ 與 run-marks 都不出現);欄位 = 契約
S2 --write ⇒ 檔 0600、夾 0700、只有六段 + schema / at;記 run-marks/support.json;回條 file 是 ~ 開頭
S3 不收的東西真的不在檔裡:席位檔的專案名 / 艙號 / 席號、dm 寄送紀錄的內文欄位、Vault 路徑下的筆記、金鑰檔內容
S4 redact:家目錄 → ~、使用者 → <user>、暫存路徑 → <tmp>、艙號 → <cabin>、席號 → <seat>、像金鑰的字串 → [redacted](算處數)
S5 failures 只留不成功的代碼 + 時間、最多 20 筆;怪代碼 / 成功的不收
S6 借 doctor 結果不記 R10(run-marks/doctor.json 不出現);aib support 從 aib 啟動器轉得到;power_tasks 讀 support.json ⇒ support_bundle
刀(複本):K1 不遮金鑰 ⇒ S4 紅;K2 預覽也寫檔 ⇒ S1 紅;K3 席位檔帶專案名 ⇒ S3 紅
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-support-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import power_tasks as PT  # noqa: E402
import support_bundle as S  # noqa: E402

C = json.loads((REPO / "contracts" / "power.json").read_text(encoding="utf-8"))["support_receipt"]
SECRET_TOKEN = "ghp_" + "Z" * 36
PROJECT = "機密專案甲"


def seed(root):
    r = Path(root)
    seats = r / "instances" / "20261014-133230-abcd" / "seats"
    seats.mkdir(parents=True)
    (seats / "w1.json").write_text(json.dumps({"role": "dev", "seat_id": "9a8b7", "project_title": PROJECT,
                                               "cwd": "/Users/x/" + PROJECT}), encoding="utf-8")
    (seats / "w2.json").write_text(json.dumps({"role": "Evil Role!", "seat_id": "1c2d3"}), encoding="utf-8")
    logs = r / "logs"
    logs.mkdir()
    rows = [{"outcome": "queued", "ts": 1, "msg": "dm 內文不該出現"},
            {"outcome": "blocked_unconfirmed", "ts": 2, "msg": "dm 內文不該出現", "seat": "9a8b7"},
            {"outcome": "Bad Code!", "ts": 3}]
    rows += [{"outcome": "relay_down", "ts": 10 + i} for i in range(30)]
    (logs / "dm-send.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    (logs / "seat-panel-actions.jsonl").write_text(
        json.dumps({"action": "input", "ok": False, "at": 100, "why": "面板原文不該出現"}, ensure_ascii=False) + "\n"
        + json.dumps({"action": "stop", "ok": True, "at": 101}) + "\n", encoding="utf-8")
    st = r / "state"
    st.mkdir()
    (st / "power-login.json").write_text(json.dumps({"claude": {"v": True, "at": 1}, "codex": {"v": "x"}}))
    sec = r / "secrets"
    sec.mkdir()
    (sec / "push.env").write_text("TOKEN=" + SECRET_TOKEN)


class Support(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        os.makedirs(TMP)
        seed(TMP)

    def test_s1_preview_writes_nothing(self):
        before = sorted(str(p) for p in Path(TMP).rglob("*"))
        r, rc = S.run()
        self.assertEqual((rc, r["ok"], r["detail"], r["file"], r["sent"]), (0, True, "support_preview", None, False))
        self.assertEqual(list(r), C["fields"])
        self.assertEqual(r["sections"], list(S.SECTIONS))
        self.assertEqual(sorted(str(p) for p in Path(TMP).rglob("*")), before)

    def test_s2_write(self):
        r, rc = S.run(write=True)
        self.assertEqual((rc, r["detail"]), (0, "support_written"))
        files = list((Path(TMP) / "support").glob("aib-support-*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((Path(TMP) / "support").stat().st_mode), 0o700)
        body = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(sorted(body), sorted(("schema", "at") + S.SECTIONS))
        self.assertTrue((Path(TMP) / "state" / "run-marks" / "support.json").is_file())
        self.assertEqual(body["cabins"], [{"seats": 2, "roles": ["?", "dev"]}])
        self.assertEqual(body["logins"], {"claude": True})

    def test_s3_excluded(self):
        S.run(write=True)
        text = next((Path(TMP) / "support").glob("aib-support-*.json")).read_text(encoding="utf-8")
        for bad in (PROJECT, "9a8b7", "1c2d3", "20261014-133230-abcd", "dm 內文", "面板原文", SECRET_TOKEN, TMP):
            self.assertNotIn(bad, text, bad)

    def test_s4_redact(self):
        home = "/Users/someone"
        got, n = S.redact({"a": [home + "/x", "by someone here", "/private/var/folders/ab/T/tmp.1/q",
                                 "cabin 20261014-133230-6ddf", "seat 0c105 @abb89", "k " + SECRET_TOKEN,
                                 "nsec1" + "q" * 40, "plain words 12345"]}, home=home, user="someone")
        self.assertEqual(got["a"], ["~/x", "by <user> here", "<tmp>", "cabin <cabin>", "seat <seat> <seat>",
                                    "k [redacted]", "[redacted]", "plain words 12345"])
        self.assertEqual(n, 2)

    def test_s5_failures(self):
        f = S.failures(TMP)
        self.assertEqual(len(f), S.FAIL_MAX)
        self.assertEqual(f[-1], {"source": "seat_panel", "code": "input", "at": 100})
        for row in f:
            self.assertEqual(sorted(row), ["at", "code", "source"])
            self.assertNotIn(row["code"], ("queued", "Bad Code!", "stop"))

    def test_s6_doctor_unmarked_dispatch_detect(self):
        S.run()
        self.assertFalse((Path(TMP) / "state" / "run-marks" / "doctor.json").exists())
        launcher = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn('if [ "${1:-}" = "support" ]; then', launcher)
        self.assertIn("support_bundle.py", launcher)
        self.assertNotIn("support_bundle", PT.detect(TMP, {}, None))
        S.run(write=True)
        self.assertIs(PT.detect(TMP, {}, None).get("support_bundle"), True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(S.main(["support", "--json"]), 0)
        self.assertEqual(json.loads(buf.getvalue())["detail"], "support_preview")
        self.assertEqual(S.main(["support", "--zap"]), 2)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/support_bundle.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_secret_mask(self):
        self._knife('        v, k = SECRET.subn("[redacted]", v)\n', "        k = 0\n", "Support.test_s4_redact")

    def test_k2_preview_writes(self):
        self._knife("    if not write:\n        return r, 0\n", "", "Support.test_s1_preview_writes_nothing")

    def test_k3_project_leak(self):
        self._knife('        out.append({"seats": len(roles), "roles": sorted(roles)})',
                    '        out.append({"seats": len(roles), "roles": sorted(roles), "p": [json.load(open(p)).get("project_title") for p in glob.glob(os.path.join(cab, "*.json"))]})',
                    "Support.test_s3_excluded")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
