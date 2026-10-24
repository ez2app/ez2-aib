#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib support 問題回報包(support_bundle.py;契約 contracts/power.json support_receipt;10-24 Andy 准精簡版)。暫存狀態根。

S1 沒 --write ⇒ support_preview、file null、一個檔都不寫(連綁了 ez2.page 也不連網寫快取、不重產角色卡清單);欄位 = 契約
S2 --write ⇒ 檔 0600、夾 0700、只有六段 + schema / at、doctor 每筆只有 id / status / detail;記 run-marks/support.json;
   回條 file 只把家目錄換 ~(本人要找得到檔)
S3 不收的東西真的不在檔裡:席位檔的專案名 / 艙號 / 席號、dm 寄送紀錄的內文欄位、金鑰檔內容;
   10-24 稽核種子:交接單檔名帶專案名、壞席位檔欄位原文、temp 過期項目的客戶主機名 IP、EZ2_AIB_INSTANCE 艙短號、
   dm 佇列夾名 ⇒ doctor 只收 DOCTOR_PURE + provider_*,這些一律不在檔裡
S4 redact:家目錄 → ~、使用者 → <user>、暫存路徑 → <tmp>、艙號 → <cabin>、席號 → <seat>、像金鑰的字串 → [redacted](算處數)
S5 failures 只留不成功的代碼 + 時間、最多 20 筆;怪代碼 / 成功的不收
S7 失敗代碼的邊角:成功的四種 / 怪代碼 / 時間不是數字 / 面板沒有 ok / 大檔只讀尾端、切半的第一行丟掉;角色超過 32 字 ⇒ ?;寫不進去 ⇒ rc 1、不記
S9 寫檔保護:暫存路徑被佔 / 是連結 ⇒ 不寫;換名失敗不留暫存;NaN 不進檔;support 夾是連結 ⇒ 不寫、不 chmod 別的夾
S10 第 2 輪稽核:外掛 manifest 版本是字典 / NaN / 控制字元、資料夾名帶換行、角色卡包 ⇒ ? 或不收;
    power-login 太深 / 鍵帶專案名、席位檔是 FIFO ⇒ 不炸不卡;PATH 裡專案夾的執行檔路徑不進包
S8 不叫整套 doctor(run_all / main / 探針 / 角色卡清單重產),只叫 DOCTOR_PURE + 各家 AI 工具
S6 借 doctor 結果不記 R10(run-marks/doctor.json 不出現);aib support 從 aib 啟動器轉得到;power_tasks 讀 support.json ⇒ support_bundle
S13 會員入口(倉根 bin/aib → aib/cli.py)打 aib support 真的跑到 support_bundle(10-25 雙審:cli.py TARGETS 原本指舊
    tools/support/collect.py,會收艙號 / 席號 / log 原文、金鑰沒遮):帶艙號 / 席號 / 假 log(dm + ghp_ 金鑰)環境跑
    ⇒ rc 0、support_preview、這些原文一個都不在;--write ⇒ 檔 0600、不帶席號檔名;舊收集器不在;狀態根種 seed() 的艙號 /
    席號 / dm 原文 / 金鑰也都不在;外掛宣告 support(免費搶 / 加購沒授權擋)⇒ 仍是核心 support_preview
S14 內建 skill ez2-aib-support 只叫 aib support / --write,不叫舊收集器、不准 AI 代寄(--sender ai);issue 範本 bug.yml 也不叫 --confirm;四語 README 同口徑
S15 10-25 突變測試存活補格:各段鍵集合寫死(versions 五鍵、外掛列四鍵:manifest 的 requires_aib / path 不收)、
    git_present / schemas_readable 失敗的 detail、doctor 例外只留型別名、power-login.json 是連結 ⇒ 不讀、
    暫存路徑先被一般檔佔 ⇒ 不寫且原檔不變、EXCLUDED 八項、每種金鑰前綴各一筆;使用者名後面接底線也遮
刀(複本):K7 versions 多收主機名 ⇒ S15 紅;K8 使用者名邊界改回把底線當字 ⇒ S15 紅;K6 cli.py 拿掉核心 support 那段(外掛搶得到)⇒ S13 紅;K1 不遮金鑰 ⇒ S4 紅;K2 預覽也寫檔 ⇒ S1 紅;K3 席位檔帶專案名 ⇒ S3 紅;K4 整套 doctor + 拿掉允許清單 ⇒ S3 紅;K5 改回借整套 doctor ⇒ S8 紅
"""
import io
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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
HOSTIP = "eu01.client-shop.example 203.0.113.7"


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
    (r / "pending").mkdir()
    (r / "pending" / ("HANDOFF-%s-x.md" % PROJECT)).write_text("x", encoding="utf-8")
    (r / "seats").mkdir()
    (r / "seats" / "w1A2B3.json").write_text(json.dumps({"schema": PROJECT, "window_id": PROJECT}, ensure_ascii=False),
                                             encoding="utf-8")
    (r / "temp").mkdir()
    (r / "temp" / "changes.json").write_text(json.dumps([{"id": "tmp-abc123", "host": HOSTIP, "until": 1}]))
    (st / "dm-queue" / "71c97a").mkdir(parents=True)
    (r / "page").mkdir()
    (r / "page" / "t1.p1.json").write_text("{}")


class Support(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        os.makedirs(TMP)
        seed(TMP)
        self._env = dict(os.environ)
        os.environ["EZ2_AIB_INSTANCE"] = "20261015-123456-6ddf"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

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
        mark = Path(TMP) / "state" / "run-marks" / "support.json"
        self.assertEqual(stat.S_IMODE(mark.stat().st_mode), 0o600)
        self.assertEqual(json.loads(mark.read_text()), {"at": body["at"]})
        self.assertEqual(S.show_path("/Users/x", home="/Users/x"), "~")
        self.assertEqual(body["cabins"], [{"seats": 2, "roles": ["?", "dev"]}])
        self.assertEqual(body["logins"], {"claude": True})
        self.assertEqual(r["file"], S.show_path(str(files[0])))
        self.assertEqual(S.show_path("/Users/x/.ez2-aib/support/a.json", home="/Users/x"), "~/.ez2-aib/support/a.json")
        self.assertEqual(S.show_path("/Users/xy/a.json", home="/Users/x"), "/Users/xy/a.json")
        self.assertTrue(body["doctor"]["checks"])
        for c in body["doctor"]["checks"]:
            self.assertEqual(sorted(c), ["detail", "id", "status"])

    def test_s3_excluded(self):
        S.run(write=True)
        text = next((Path(TMP) / "support").glob("aib-support-*.json")).read_text(encoding="utf-8")
        for bad in (PROJECT, "9a8b7", "1c2d3", "20261014-133230-abcd", "dm 內文", "面板原文", SECRET_TOKEN, TMP,
                    "client-shop", "203.0.113.7", "6ddf", "71c97a", "w1A2B3", "tmp-abc123"):
            self.assertNotIn(bad, text, bad)
        ids = {c["id"] for c in json.loads(text)["doctor"]["checks"]}
        self.assertTrue(ids)
        for i in ids:
            self.assertTrue(i in S.DOCTOR_PURE or i.startswith("provider_"), i)

    def test_s4_redact(self):
        home = "/Users/someone"
        got, n = S.redact({"a": [home + "/x", "by someone here", "/private/var/folders/ab/T/tmp.1/q",
                                 "cabin 20261014-133230-6ddf", "seat 0c105 @abb89", "k " + SECRET_TOKEN,
                                 "nsec1" + "q" * 40, "plain words 12345"]}, home=home, user="someone")
        self.assertEqual(got["a"], ["~/x", "by <user> here", "<tmp>", "cabin <cabin>", "seat <seat> <seat>",
                                    "k [redacted]", "[redacted]", "plain words <seat>"])     # 全數字也當席號(寧可多遮)
        got, _ = S.redact(["D07F9", "seat_d07f9", "wd07f9.json", "20261024-120000-6DDF", "/USERS/SOMEONE/x",
                           "/Users/someone_old", "SomeOne", "/TMP/q", "Bearer " + "a1" * 10, "c" * 32,
                           "AIza" + "B" * 32, "sk_live_" + "C" * 12, "glpat-" + "d" * 20, "hf_" + "E" * 22],
                          home=home, user="someone")
        # /Users/someone_old:家目錄前綴不准誤換成 ~_old;使用者名接底線 10-25 起也遮(稽核 Low)⇒ /Users/<user>_old
        self.assertEqual(got, ["<seat>", "seat_<seat>", "<seat>.json", "<cabin>", "~/x", "/Users/<user>_old", "<user>",
                               "<tmp>", "[redacted]", "[redacted]", "[redacted]", "[redacted]", "[redacted]",
                               "[redacted]"])
        self.assertEqual(S.redact({home + "/k": {SECRET_TOKEN: 1}}, home=home, user="zz")[0], {"~/k": {"[redacted]": 1}})
        self.assertEqual(S.redact(["/h1/abc/x"], home="/h1/abc/", user="zz")[0], ["~/x"])
        self.assertEqual(S.redact(["xsomeone"], home="/h", user="someone")[0], ["xsomeone"])
        self.assertEqual(S.redact(["xoxb-11111-2222222222"], home="/h", user="zz")[0], ["[redacted]"])   # 金鑰先於席號
        self.assertEqual(n, 2)
        self.assertEqual(S.redact(["ab cd"], home="/h", user="ab")[0], ["ab cd"])          # 太短的名字不換
        self.assertEqual(S.redact(["someoneelse"], home="/h", user="someone")[0], ["someoneelse"])
        self.assertEqual(S.redact(["%s %s" % (SECRET_TOKEN, SECRET_TOKEN)], home="/h", user="zz")[1], 2)
        for key in ("f" * 40, "sk-" + "a" * 20, "AKIA" + "B" * 16, "eyJ" + "a" * 10 + "." + "b" * 10,
                    "-----BEGIN RSA PRIVATE KEY-----", "Q" * 48, "xoxb-" + "1" * 12, "github_pat_" + "x" * 22):
            self.assertEqual(S.redact([key], home="/h", user="zz")[0], ["[redacted]"], key)

    def test_s5_failures(self):
        f = S.failures(TMP)
        self.assertEqual(len(f), S.FAIL_MAX)
        self.assertEqual(f[-1], {"source": "seat_panel", "code": "input", "at": 100})
        for row in f:
            self.assertEqual(sorted(row), ["at", "code", "source"])
            self.assertNotIn(row["code"], ("queued", "Bad Code!", "stop"))

    def test_s7_edges(self):
        root = Path(TMP) / "edge"
        (root / "logs").mkdir(parents=True)
        rows = [{"outcome": o, "ts": i} for i, o in enumerate(("delivered", "delivered_direct", "read", "done", "queued"))]
        rows += [{"outcome": "Bad Code!", "ts": 7}, {"outcome": "blocked_unconfirmed", "ts": "x"}]
        (root / "logs" / "dm-send.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
        (root / "logs" / "seat-panel-actions.jsonl").write_text(json.dumps({"action": "input2", "at": 5}) + "\n")
        self.assertEqual([(r["code"], r["at"]) for r in S.failures(str(root))], [("blocked_unconfirmed", None)])
        big = json.dumps({"outcome": "old_one", "ts": 1}) + "\n"
        cut = json.dumps({"outcome": "half_line", "pad": "y" * S.TAIL_BYTES, "ts": 2})   # 比讀取窗口長 ⇒ 切點在它中間
        tail = json.dumps({"outcome": "new_one", "ts": 3}) + "\n"
        body = big + cut + "\n" + tail
        (root / "logs" / "dm-send.jsonl").write_text(body, encoding="utf-8")
        (root / "logs" / "seat-panel-actions.jsonl").write_text("")
        got = [r["code"] for r in S.failures(str(root))]
        self.assertNotIn("old_one", got)                                  # 只讀尾端
        self.assertIn("new_one", got)
        start = len(body.encode()) - S.TAIL_BYTES                         # 切點落在 half_line 那行中間 ⇒ 那行丟掉
        line_at = len(big.encode())
        self.assertTrue(line_at < start < line_at + len(cut.encode()), (line_at, start))
        self.assertNotIn("half_line", got)
        seats = root / "instances" / "c" / "seats"
        seats.mkdir(parents=True)
        (seats / "w1.json").write_text(json.dumps({"role": "a" * 33}))
        (seats / "w2.json").write_text(json.dumps({"role": "a" * 32}))
        self.assertEqual(S.cabins(str(root))[0]["roles"], ["?", "a" * 32])
        (root / "logs" / "dm-send.jsonl").write_text('{"outcome": "x1", "ts": Infinity}\n{"outcome": "x2", "ts": true}\n')
        self.assertEqual([r["at"] for r in S.failures(str(root))], [None, None])
        (Path(TMP) / "support").mkdir(mode=0o755)
        os.chmod(Path(TMP) / "support", 0o755)
        stamp = S.time.strftime("%Y%m%d-%H%M%S", S.time.localtime(1792780000))
        S.time.time = lambda: 1792780000.5
        try:
            a, _ = S.run(write=True)
            b, _ = S.run(write=True)
            c, _ = S.run(write=True)
        finally:
            S.time.time = time.time
        self.assertEqual(len({a["file"], b["file"], c["file"]}), 3)
        self.assertEqual(stat.S_IMODE((Path(TMP) / "support").stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in (Path(TMP) / "support").iterdir()),
                         ["aib-support-%s-2.json" % stamp, "aib-support-%s-3.json" % stamp, "aib-support-%s.json" % stamp])
        shutil.rmtree(Path(TMP) / "support")
        (Path(TMP) / "support").mkdir()
        os.symlink("/nonexistent-aib-x", str(Path(TMP) / "support" / ("aib-support-%s.json" % stamp)))
        S.time.time = lambda: 1792780000.5
        try:
            d, _ = S.run(write=True)
        finally:
            S.time.time = time.time
        self.assertTrue(os.path.islink(str(Path(TMP) / "support" / ("aib-support-%s.json" % stamp))))   # 斷掉的連結也不蓋
        self.assertTrue(d["file"].endswith("-2.json"), d["file"])
        shutil.rmtree(Path(TMP) / "support")
        (Path(TMP) / "state" / "run-marks" / "support.json").unlink()
        (Path(TMP) / "support").write_text("x")
        r, rc = S.run(write=True)
        self.assertEqual((rc, r["ok"], r["detail"]), (1, False, "support_write_failed"))
        self.assertFalse((Path(TMP) / "state" / "run-marks" / "support.json").exists())

    def test_s8_no_whole_doctor(self):
        sys.path.insert(0, str(REPO / "src" / "bootstrap"))
        import aib_doctor as D

        def boom(*a, **k):
            raise AssertionError("aib support 不准借整套 doctor(會寫檔、連網、說明帶專案名)")
        real = (D.run_all, D.main, D.check_writable, D.check_cards_projection)
        D.run_all = D.main = D.check_writable = D.check_cards_projection = boom
        try:
            got = S.doctor(TMP)
        finally:
            D.run_all, D.main, D.check_writable, D.check_cards_projection = real
        self.assertNotIn("error", got)
        self.assertEqual([c["id"] for c in got["checks"]][:4], list(S.DOCTOR_PURE))
        self.assertTrue(any(c["id"].startswith("provider_") for c in got["checks"]))
        self.assertFalse({"sand", "gemini"} & set(S._providers()))                      # retired / alias-only 不收
        here = S.HERE
        fake = Path(TMP) / "fake-here"
        fake.mkdir()
        (fake / "providers.json").write_text(json.dumps({"providers": [
            {"id": "Bad Id!", "status": "supported"}, {"id": "ok1", "status": "supported"}, {"id": "old", "status": "retired"}]}))
        S.HERE = str(fake)
        try:
            self.assertEqual(S._providers(), ["ok1"])
        finally:
            S.HERE = here

    def test_s9_write_guards(self):
        d = Path(TMP) / "w"
        d.mkdir()
        p = str(d / "f.json")
        victim = d / "victim"
        tok = S.secrets.token_hex
        S.secrets.token_hex = lambda n: "fixed"
        try:
            os.symlink(str(victim), "%s.%d.fixed.tmp" % (p, os.getpid()))
            with self.assertRaises(OSError):
                S._write_private(p, "x")                                     # 暫存路徑被預先佔住 / 是連結 ⇒ 不寫
            self.assertFalse(victim.exists())
        finally:
            S.secrets.token_hex = tok
        rep = S.os.replace

        def bad(a, b):
            raise OSError("boom")
        S.os.replace = bad
        try:
            with self.assertRaises(OSError):
                S._write_private(str(d / "g.json"), "x")
        finally:
            S.os.replace = rep
        self.assertEqual(glob.glob(str(d / "g.json") + ".*.tmp"), [])          # 失敗不留暫存檔
        real = S.collect
        S.collect = lambda root=None: ({"schema": "x", "at": 1, "versions": {}, "plugins": [], "doctor": {}, "logins": {},
                                        "cabins": [], "failures": [{"at": float("nan")}]}, 0)
        try:
            self.assertEqual(S.run(write=True)[0]["detail"], "support_write_failed")   # NaN 不准進檔
        finally:
            S.collect = real
        other = Path(TMP) / "elsewhere"
        other.mkdir(mode=0o755)
        os.chmod(other, 0o755)
        shutil.rmtree(Path(TMP) / "support", ignore_errors=True)
        os.symlink(str(other), str(Path(TMP) / "support"))
        r, rc = S.run(write=True)
        self.assertEqual((rc, r["detail"]), (1, "support_write_failed"))          # support 是連結 ⇒ 不寫、不 chmod 別的夾
        self.assertEqual(stat.S_IMODE(other.stat().st_mode), 0o755)
        self.assertEqual(list(other.iterdir()), [])

    def test_s10_round2(self):
        r = Path(TMP)
        for name, man in (("p1", '{"version": {"project": "%s"}}' % PROJECT), ("p2", '{"version": NaN}'),
                          ("p3", '{"version": "1.0\u001b[31mRED"}'), ("p4\nline2", '{"version": "1.2.3"}'),
                          ("p5", "[1]"), ("ok6", '{"version": "0.4.1", "signature": "x"}')):
            (r / "plugins" / name).mkdir(parents=True)
            (r / "plugins" / name / "aib-ext.json").write_text(man, encoding="utf-8")
        (r / "personas" / "owner-clone-persona").mkdir(parents=True)
        (r / "personas" / "owner-clone-persona" / "aib-ext.json").write_text('{"version": "1.0"}')
        (r / "state" / "power-login.json").write_text("[" * 200000 + "]" * 200000)
        os.mkfifo(str(r / "instances" / "20261014-133230-abcd" / "seats" / "f.json"))
        work = r / "work" / PROJECT / "bin"
        work.mkdir(parents=True)
        (work / "codex").write_text("#!/bin/sh\n")
        os.chmod(work / "codex", 0o755)
        os.environ["PATH"] = str(work) + os.pathsep + os.environ.get("PATH", "")
        rr, rc = S.run(write=True)
        self.assertEqual((rc, rr["detail"]), (0, "support_written"))
        text = next((r / "support").glob("aib-support-*.json")).read_text(encoding="utf-8")
        body = json.loads(text)
        got = {(x["kind"], x["name"]): x["version"] for x in body["plugins"]}
        self.assertEqual(got.get(("plugin", "ok6")), "0.4.1")
        for k in (("plugin", "p1"), ("plugin", "p2"), ("plugin", "p3")):
            self.assertEqual(got.get(k), "?", k)
        self.assertIn(("plugin", "?"), got)                                    # 帶換行的資料夾名 ⇒ ?
        self.assertNotIn("persona", [x["kind"] for x in body["plugins"]])
        self.assertEqual(body["logins"], {})
        for bad in (PROJECT, "line2", "RED", "owner-clone"):
            self.assertNotIn(bad, text, bad)
        cli = [c for c in body["doctor"]["checks"] if c["id"] == "provider_cli_codex"]
        self.assertEqual([c["detail"] for c in cli], [None])
        (r / "state" / "power-login.json").write_text(json.dumps({PROJECT + "-客戶 acme": {"v": True}, "claude": {"v": True}}))
        self.assertEqual(S.logins(TMP), {"claude": True})
        real = S.cabins
        S.cabins = lambda root: [][0]
        try:
            rr, rc = S.run()
        finally:
            S.cabins = real
        self.assertEqual((rc, rr["ok"], rr["detail"]), (1, False, "support_collect_failed"))

    def test_s11_round3(self):
        # r2 審查:寫進包之前真的有遮;manifest / 紀錄檔是 FIFO 不卡;超過上限的 JSON 不讀
        import signal

        def _stuck(_s, _f):
            raise AssertionError("aib support 卡住")
        old = signal.signal(signal.SIGALRM, _stuck)
        signal.alarm(10)
        try:
            r = Path(TMP)
            (r / "plugins" / "fifo1").mkdir(parents=True)
            os.mkfifo(str(r / "plugins" / "fifo1" / "aib-ext.json"))
            os.mkfifo(str(r / "logs" / "seat-panel-actions.jsonl.fifo"))
            os.replace(str(r / "logs" / "seat-panel-actions.jsonl.fifo"), str(r / "logs" / "seat-panel-actions.jsonl"))
            (r / "state" / "power-login.json").write_text(json.dumps({"claude": {"v": True}, "pad": "y" * (S.READ_MAX + 10)}))
            real = S.versions
            S.versions = lambda: {"x": "k " + SECRET_TOKEN}
            try:
                rr, rc = S.run(write=True)
            finally:
                S.versions = real
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        self.assertEqual((rc, rr["detail"]), (0, "support_written"))
        self.assertGreaterEqual(rr["redacted"], 1)
        text = Path(os.path.expanduser(rr["file"])).read_text(encoding="utf-8")
        self.assertNotIn(SECRET_TOKEN, text)
        body = json.loads(text)
        self.assertEqual(body["logins"], {})                                   # 超過 READ_MAX ⇒ 不讀
        self.assertIn(("plugin", "fifo1"), {(x["kind"], x["name"]) for x in body["plugins"]})
        self.assertEqual(S.redact(["eyJ" + "a" * 10 + "." + "b" * 10 + ".sig_part"], home="/h", user="zz")[0],
                         ["[redacted]"])                                       # JWT 三段整條遮

    def test_s12_round4(self):
        # r3 審查:Linux 容器取不到使用者名(OSError / KeyError)不整份失敗;路徑是資料夾不漏 fd
        def nouser():
            raise err
        real = S.getpass.getuser
        for err in (OSError("no passwd entry"), KeyError("uid")):
            S.getpass.getuser = nouser
            try:
                rr, rc = S.run(write=True)
            finally:
                S.getpass.getuser = real
            self.assertEqual((rc, rr["detail"]), (0, "support_written"), err)
        d = Path(TMP) / "adir"
        d.mkdir()
        fds = len(os.listdir("/dev/fd"))
        for _ in range(5):
            self.assertIsNone(S._read_json(str(d)))
            self.assertEqual(S._tail_rows(str(d)), [])
        self.assertEqual(len(os.listdir("/dev/fd")), fds)

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


class Harden(unittest.TestCase):
    """10-25 突變測試存活的 9 條 + 稽核 Low(使用者名底線邊界)。"""

    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        os.makedirs(TMP)
        seed(TMP)

    def test_s15_keys_and_guards(self):
        from unittest import mock
        sys.path.insert(0, str(REPO / "src" / "bootstrap"))
        import aib_ext
        import aib_doctor as D
        body = S.collect(TMP)[0]
        self.assertEqual(set(body), {"schema", "at"} | set(S.SECTIONS))
        self.assertEqual(set(body["versions"]), {"aib", "os", "macos", "machine", "python"}, "versions 多收了欄位(主機名常帶真名)")
        self.assertEqual(set(body["doctor"]) - {"error"}, {"checks", "counts"})
        for c in body["doctor"]["checks"]:
            self.assertEqual(set(c), {"id", "status", "detail"})
        for row in body["cabins"]:
            self.assertEqual(set(row), {"seats", "roles"})
        for row in body["failures"]:
            self.assertEqual(set(row), {"source", "code", "at"})
        fake = [{"kind": "plugin", "name": "ok1", "version": "1.0", "signed": True, "requires_aib": PROJECT,
                 "path": "/Users/x/" + PROJECT}]
        with mock.patch.object(aib_ext, "installed", return_value=fake):
            rows = S.plugins()
        self.assertEqual(rows, [{"kind": "plugin", "name": "ok1", "version": "1.0", "signed": True}], "外掛列只准四鍵")
        bad_git = {"id": "git_present", "status": "ok", "detail": "/Users/x/%s/bin/git" % PROJECT}
        bad_schema = {"id": "schemas_readable", "status": "fail", "detail": "broken: /Users/x/%s/a.json" % PROJECT}
        with mock.patch.object(D, "check_git", return_value=bad_git), \
                mock.patch.object(D, "check_schemas", return_value=bad_schema):
            got = {c["id"]: c["detail"] for c in S.doctor(TMP)["checks"]}
        self.assertEqual((got["git_present"], got["schemas_readable"]), (None, None), "git 路徑 / schema 失敗原文不准進包")
        with mock.patch.object(D, "check_python", side_effect=OSError("/Users/x/%s/boom" % PROJECT)):
            d = S.doctor(TMP)
        self.assertEqual(d.get("error"), "OSError")
        self.assertNotIn(PROJECT, json.dumps(d, ensure_ascii=False))
        st = Path(TMP) / "state"
        st.mkdir(exist_ok=True)
        real = Path(TMP) / "elsewhere.json"
        real.write_text(json.dumps({"claude": {"v": True}}), encoding="utf-8")
        if (st / "power-login.json").exists() or (st / "power-login.json").is_symlink():
            (st / "power-login.json").unlink()
        os.symlink(str(real), str(st / "power-login.json"))
        self.assertEqual(S.logins(TMP), {}, "power-login.json 是連結 ⇒ 不跟、不讀")
        target = Path(TMP) / "out.json"
        with mock.patch.object(S.secrets, "token_hex", return_value="feed"):
            squat = Path("%s.%d.feed.tmp" % (target, os.getpid()))
            squat.write_text("ORIGINAL", encoding="utf-8")
            with self.assertRaises(OSError):
                S._write_private(str(target), "NEW")
        self.assertEqual(squat.read_text(encoding="utf-8"), "ORIGINAL", "暫存路徑被一般檔佔 ⇒ 不准寫進去")
        self.assertFalse(target.exists())
        self.assertEqual(S.EXCLUDED, ("messages", "vault", "project_names", "persona", "secrets", "licenses",
                                      "log_text", "cabin_seat_ids"))
        keys = ["gh%s_%s" % (p, "A" * 36) for p in "pousr"] + ["sk_test_" + "a" * 24, "sk_live_" + "a" * 24]
        out, n = S.redact(keys, home="/h", user="zz")
        self.assertEqual((out, n), (["[redacted]"] * len(keys), len(keys)), "每種金鑰前綴都要遮")
        out, _n = S.redact(["andylin_fail", "x-andylin", "0.1.44-andylin", "andylinx"], home="/h", user="andylin")
        self.assertEqual(out, ["<user>_fail", "x-<user>", "0.1.44-<user>", "andylinx"], "使用者名後面接底線也要遮")


class Entry(unittest.TestCase):
    """會員實際打的 aib support(倉根 bin/aib)。"""

    def test_s13_member_entry_runs_bundle(self):
        td = Path(tempfile.mkdtemp(prefix="aib-support-entry-"))
        self.addCleanup(shutil.rmtree, str(td), True)
        (td / "home").mkdir()
        seed(td / "state")                                        # 狀態根種艙號 / 席號 / 專案名 / 帶金鑰的 dm log(#340 審查 L2)
        log = td / "fake.log"
        keys = (SECRET_TOKEN, "nsec1" + "q" * 58, "Bearer " + "a" * 32, "sk-ant-api03-" + "Z" * 40)
        log.write_text("dm from @abb89: 客戶報價 %s %s\n" % (PROJECT, " ".join(keys)), encoding="utf-8")
        # EZ2_SUPPORT_LOG / PANE_CAPTURE 是舊收集器才讀的;留著只為驗新入口不會讀它們
        env = dict(os.environ, HOME=str(td / "home"), EZ2_AIB_STATE_ROOT=str(td / "state"), AIB_TOUCHID="0",
                   EZ2_AIB_HOME=str(td / "aibhome"),
                   EZ2_AIB_INSTANCE="20261014-133230-6ddf", EZ2_AIB_SEAT_LABEL="@d07f9", EZ2_SUPPORT_LOG=str(log),
                   EZ2_SUPPORT_PANE_CAPTURE="Andy> 幫我回客戶 %s %s" % (PROJECT, " ".join(keys)))
        (td / "pkg").mkdir()
        os.symlink(str(REPO), str(td / "pkg" / "aib"))           # 跟倉根 bin/aib 一樣:from aib.cli import main(刀測複本也適用)
        aib = ["-c", "import sys; sys.path.insert(0, sys.argv.pop(1)); from aib.cli import main; sys.exit(main())",
               str(td / "pkg")]
        # 外掛宣告 support:免費的搶不走、要加購沒授權的擋不住(#340 審查 M1)
        for name, extra in (("a-hijack", {}), ("b-paid", {"requires_addon": "nope"})):
            pd = td / "aibhome" / "plugins" / name
            pd.mkdir(parents=True)
            (pd / "run.py").write_text("print('HIJACK')\n", encoding="utf-8")
            (pd / "plugin.json").write_text(json.dumps(dict({"name": name, "module": "m", "commands": {"support": "run.py"}},
                                                            **extra)), encoding="utf-8")
            r = subprocess.run([sys.executable] + aib + ["support", "--json"], capture_output=True, text=True, env=env,
                               timeout=120)
            self.assertEqual(r.returncode, 0, name + r.stdout + r.stderr)
            self.assertNotIn("HIJACK", r.stdout, name)
            self.assertEqual(json.loads(r.stdout)["detail"], "support_preview", name)
            shutil.rmtree(str(pd))
        r = subprocess.run([sys.executable] + aib + ["support", "--json"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["detail"], "support_preview", r.stdout)
        seeded = ("133230-abcd", "9a8b7", "dm 內文不該出現", "面板原文不該出現", SECRET_TOKEN)
        for bad in ("20261014-133230-6ddf", "@d07f9", "dm from", PROJECT, "Andy>", "ghp_", "nsec1", "Bearer", "sk-ant-") + seeded:
            self.assertNotIn(bad, r.stdout + r.stderr, "會員入口的 aib support 漏出:" + bad)
        self.assertFalse((td / "state" / "support").exists(), "沒 --write 不准寫檔")
        stray = [str(p.relative_to(td / "home")) for p in (td / "home").rglob("*")
                 if p.is_file() and p.suffix != ".pyc"]          # Python 位元碼快取(啟動器 / macOS 內建 3.9 各有一處)不算
        self.assertEqual(stray, [], "沒 --write 不准在家目錄寫東西")
        r = subprocess.run([sys.executable] + aib + ["support", "--write"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = list((td / "state" / "support").glob("aib-support-*.json"))
        self.assertEqual(len(files), 1, r.stdout)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertNotIn("d07f9", files[0].name)
        body = files[0].read_text(encoding="utf-8")
        for bad in ("20261014-133230-6ddf", "@d07f9", "dm from", PROJECT, "ghp_", "nsec1", "Bearer", "sk-ant-") + seeded:
            self.assertNotIn(bad, body)
        self.assertFalse((REPO / "tools" / "support" / "collect.py").exists(), "舊收集器要退掉")

    def test_s14_skill_uses_bundle(self):
        skill = (REPO / "skills" / "ez2-aib-support" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("aib support --write", skill)
        for bad in ("collect.py", "--sender ai", "--confirm", "issues/new"):
            self.assertNotIn(bad, skill, "skill 還叫舊收集器 / 准 AI 代寄:" + bad)
        bug = REPO.parent / ".github" / "ISSUE_TEMPLATE" / "bug.yml"
        if bug.exists():                                   # 刀測複本只有 aib/,沒有 .github(倉裡一定有)
            text = bug.read_text(encoding="utf-8")
            self.assertIn("aib support --write", text)
            for bad in ("--confirm", "collect.py"):
                self.assertNotIn(bad, text, "issue 範本還叫舊收集器:" + bad)
        for lang in ("", ".zh-TW", ".ja", ".ko"):                 # 四語 README 同口徑:先看、--write 存本機、要不要給由本人決定
            rd = REPO.parent / ("README%s.md" % lang)
            if rd.exists():
                text = rd.read_text(encoding="utf-8")
                self.assertIn("aib support --write", text, lang)
                for bad in ("collects a", "attach it to a GitHub issue", "附在 GitHub issue", "添付してください", "첨부해 주세요"):
                    self.assertNotIn(bad, text, lang)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case, extra=None):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/support_bundle.py", needle, repl, tests=(name,)) as root:
            if extra:                                   # 同一份複本再砍第二刀(錨一樣要恰好一次)
                f = root / "src" / "cockpit" / "support_bundle.py"
                t = f.read_text(encoding="utf-8")
                self.assertEqual(t.count(extra[0]), 1, "第二刀的錨不是恰好一次")
                f.write_text(t.replace(extra[0], extra[1]), encoding="utf-8")
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k6_cli_no_core_support(self):
        name = Path(__file__).name
        with knife_copy(REPO, "cli.py", '    if sys.argv[1] == "support":\n', '    if False:\n', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Entry.test_s13_member_entry_runs_bundle"],
                            cwd=str(root), max_procs=40, timeout=180)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k7_host_in_versions(self):
        self._knife('"machine": platform.machine(), "python": platform.python_version()}',
                    '"machine": platform.machine(), "python": platform.python_version(), "host": platform.node()}',
                    "Harden.test_s15_keys_and_guards")

    def test_k8_user_boundary_underscore(self):
        self._knife('v = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(user)',
                    'v = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(user)',
                    "Harden.test_s15_keys_and_guards")

    def test_k1_no_secret_mask(self):
        self._knife('        v, k = SECRET.subn("[redacted]", v)', "        k = 0", "Support.test_s4_redact")

    def test_k2_preview_writes(self):
        self._knife("    if not write:\n        return r, 0\n", "", "Support.test_s1_preview_writes_nothing")

    def test_k4_whole_doctor_no_allowlist(self):
        self._knife("""              and (c.get("id") in DOCTOR_PURE or str(c.get("id")).startswith("provider_"))]""",
                    "]", "Support.test_s3_excluded", extra=(
                        "        got = [D.check_python(), D.check_git(), D.check_state_root_perms(root), D.check_schemas(REPO)]\n"
                        "        got += D.provider_checks(_providers(), False)\n",
                        "        got = D.run_all(root, REPO, _providers(), False)\n"))

    def test_k5_whole_doctor(self):
        self._knife("        got = [D.check_python(), D.check_git(), D.check_state_root_perms(root), D.check_schemas(REPO)]\n"
                    "        got += D.provider_checks(_providers(), False)\n",
                    "        got = D.run_all(root, REPO, _providers(), False)\n", "Support.test_s8_no_whole_doctor")

    def test_k3_project_leak(self):
        self._knife('        out.append({"seats": len(roles), "roles": sorted(roles)})',
                    '        out.append({"seats": len(roles), "roles": sorted(roles), "p": [json.load(open(p)).get("project_title") for p in glob.glob(os.path.join(cab, "*.json"))]})',
                    "Support.test_s3_excluded")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
