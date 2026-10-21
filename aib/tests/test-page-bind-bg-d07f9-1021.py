#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""一鍵綁定核心(0.1.37):aib page bind --start / --poll / --confirm / --cancel --json。
契約 CONTRACT-bind-start-poll-74e8b-1021 + @abb89 四條(confirm_digits、poll 不彈、confirm 才彈、cancelled)。

S1 start:帶 machine;回 code / confirm_digits("07" 前導 0 留)/ qr_url = confirm_link / expires_at / machine_name;
   狀態檔 0600、只有 ticket 等,沒有配對碼、沒有確認數字;沒 confirm_link ⇒ confirm_url + code;網址帶數字 ⇒ 不用它;liff 只收 liff.line.me
S2 start 拒:沒兩位數 ⇒ no-confirm-digits、不落檔;已綁這台 ⇒ already-bound;舊伺服器 seat_label_invalid ⇒ 不帶 machine 重要;
   再 start ⇒ 換新 ticket
S3 poll:none;pending ⇒ waiting;bound ⇒ confirm_local + slug、**不彈指紋、不寫正式憑證**;410 ⇒ expired 並清;本機過期 ⇒ expired;
   declined;別台的憑證 ⇒ wrong-machine 不存
S4 confirm:waiting ⇒ not-ready;席位呼叫 ⇒ rc 2、不彈、還留著;指紋拒 ⇒ declined、清掉、沒憑證;指紋過 ⇒ bound、憑證 0600、days_left;
   視窗那句帶 slug;按太晚 ⇒ expired
S5 cancel ⇒ cancelled、清掉;confirm_local 時取消 ⇒ 帶 manage_url(憑證已發出,要去後台解除)
S6 確認數字與配對碼從頭到尾不落地:整個狀態根掃不到 confirm_digits 鍵、掃不到配對碼
S7 remote status 帶 slug / bind_expires_at / days_left;沒綁 ⇒ None 而且不建 machine.json
S8 CLI:--start 印一行 JSON;--start 跟 --serial 互斥;--confirm 在 AIB_TOUCHID=0 ⇒ declined(不彈真指紋)
S9 四語字典
刀(複本):K1 confirm 不叫 prove ⇒ S4 紅;K2 start 把數字寫進狀態檔 ⇒ S6 紅
"""
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit" / "page"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
import bind_bg as BG  # noqa: E402
import store  # noqa: E402
from api import ApiError, TransportError  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
TOKEN = "ezp_" + "B" * 43
CODE = "EZ2-K7QM3XPA"


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


class FakeApi:
    def __init__(self, digits="07", link="https://ez2.page/manage/aib?code=" + CODE, liff=None, mid="same",
                 reject_machine=False, poll_status="pending", poll_error=None, start_error=None):
        self.digits, self.link, self.liff, self.mid = digits, link, liff, mid
        self.reject, self.poll_status, self.poll_error, self.start_error = reject_machine, poll_status, poll_error, start_error
        self.starts, self.polls, self.n = [], [], 0
        self.base = NOW                     # CLI 那格走真時鐘 ⇒ 換成現在

    def bind_start(self, seat_label, machine=None):
        self.starts.append(machine)
        if self.start_error:
            raise self.start_error
        if machine and self.reject:
            raise ApiError(400, "page_api_seat_label_invalid")
        self.n += 1
        rec = {"pair_code": CODE, "bind_ticket": "ticket-%d-" % self.n + "t" * 20, "confirm_url": "https://ez2.page/manage/aib",
               "expires_at": (self.base + timedelta(minutes=10)).isoformat()}
        if self.digits is not None:
            rec["confirm_digits"] = self.digits
        if self.link:
            rec["confirm_link"] = self.link
        if self.liff:
            rec["liff_url"] = self.liff
        return rec

    def bind_poll(self, ticket):
        self.polls.append(ticket)
        if self.poll_error:
            raise self.poll_error
        if self.poll_status != "bound":
            return {"status": self.poll_status}
        rec = {"status": "bound", "assistant_token": TOKEN, "token_id": "tk9", "tenant_id": "t_lfu0327", "page_id": "p1",
               "slug": "lfu0327", "scope": ["draft:read"], "expires_at": (NOW + timedelta(days=30)).isoformat()}
        if self.mid == "same":
            rec["machine_id"] = self.machine_id
        elif self.mid:
            rec["machine_id"] = self.mid
        return rec


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-bindbg-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"HOME": str(self.td), "EZ2_AIB_HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"),
                    "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        self.clock = Clock()
        self.asked = []

    def api(self, **kw):
        a = FakeApi(**kw)
        a.machine_id = store.machine_identity(self.env)["id"]
        return a

    def start(self, api, label="Mac Pro 艦長"):
        return BG.start(api, label, env=self.env, clock=self.clock)

    def poll(self, api):
        return BG.poll(api, env=self.env, clock=self.clock)

    def prove_ok(self, reason, env):
        self.asked.append(reason)
        return "touchid", None

    def prove_no(self, reason, env):
        self.asked.append(reason)
        return "touchid", "touchid_cancel"

    def confirm(self, prove=None, seat=False):
        return BG.confirm(env=self.env, clock=self.clock, prove=prove or self.prove_ok, caller_is_seat=lambda e: seat)

    def pending_raw(self):
        p = Path(BG.pending_path(self.env))
        return p.read_text(encoding="utf-8") if p.exists() else None

    def tokens(self):
        return store.list_tokens(self.env)


class Start(Base):
    def test_s1_start(self):
        api = self.api()
        res, rc = self.start(api)
        self.assertEqual(rc, 0, res)
        self.assertEqual((res["state"], res["code"], res["confirm_digits"]), ("waiting", CODE, "07"))
        self.assertEqual(res["qr_url"], "https://ez2.page/manage/aib?code=" + CODE)
        self.assertEqual(res["machine_name"], store.machine_identity(self.env)["name"])
        self.assertNotIn("liff_url", res)
        self.assertIsNotNone(api.starts[0], "帶 machine")
        p = Path(BG.pending_path(self.env))
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        raw = p.read_text(encoding="utf-8")
        d = json.loads(raw)
        self.assertEqual(d["state"], "waiting")
        self.assertNotIn("confirm_digits", raw)
        self.assertNotIn(CODE, raw, "配對碼不落檔")
        self.assertNotIn("ticket", json.dumps(res), "ticket 不回給呼叫端")
        # 舊伺服器沒 confirm_link ⇒ confirm_url + code
        BG.cancel(self.env)
        res, _ = self.start(self.api(link=None))
        self.assertEqual(res["qr_url"], "https://ez2.page/manage/aib?code=" + CODE)
        # 網址裡帶了數字 ⇒ 不用
        BG.cancel(self.env)
        res, _ = self.start(self.api(link="https://ez2.page/manage/aib?code=%s&d=07" % CODE))
        self.assertNotIn("07", res["qr_url"].split("code=")[1].replace(CODE, ""))
        # 不是 https / 主機不對 ⇒ 不用
        for bad in ("http://ez2.page/manage/aib?code=x", "https://evil.example/manage/aib", "https://ez2.page.evil.com/x"):
            BG.cancel(self.env)
            res, _ = self.start(self.api(link=bad))
            self.assertTrue(res["qr_url"].startswith("https://ez2.page/manage/aib"), bad)
        BG.cancel(self.env)
        res, _ = self.start(self.api(liff="https://liff.line.me/123-abc?room=1&code=" + CODE))
        self.assertEqual(res["liff_url"], "https://liff.line.me/123-abc?room=1&code=" + CODE)
        for bad in ("https://evil.example/liff", "https://liff.line.me/1?room=1&d=07"):
            BG.cancel(self.env)
            res, _ = self.start(self.api(liff=bad))
            self.assertNotIn("liff_url", res, bad)

    def test_s2_start_refusals(self):
        res, rc = self.start(self.api(digits=None))
        self.assertEqual((rc, res["ok"], res["why"]), (1, False, "no-confirm-digits"))
        self.assertIsNone(self.pending_raw(), "拒絕就不落檔")
        for bad in ("7", "077", 7, "ab"):
            res, rc = self.start(self.api(digits=bad))
            self.assertEqual(res.get("why"), "no-confirm-digits", bad)
        api = self.api(reject_machine=True)
        res, rc = self.start(api)
        self.assertEqual(rc, 0, res)
        self.assertEqual(len(api.starts), 2)
        self.assertIsNone(api.starts[1], "舊伺服器 ⇒ 不帶 machine 再要一次")
        api = self.api(start_error=ApiError(400, "page_api_body_invalid", details=["machine"]))
        res, rc = self.start(api)
        self.assertEqual((rc, len(api.starts)), (1, 1), "其他 400 不退回")
        res, rc = self.start(self.api(start_error=TransportError("x")))
        self.assertEqual(res["why"], "page-api-unavailable")
        res, rc = self.start(self.api(start_error=ApiError(429, "page_api_token_limit_reached")))
        self.assertEqual(res["why"], "token-limit")
        api = self.api()
        self.start(api)
        t1 = json.loads(self.pending_raw())["ticket"]
        self.start(api)
        self.assertNotEqual(json.loads(self.pending_raw())["ticket"], t1, "再 start ⇒ 換新一張")
        # 已綁這台 ⇒ already-bound
        api = self.api(poll_status="bound")
        self.start(api)
        self.poll(api)
        self.confirm()
        res, rc = self.start(self.api())
        self.assertEqual((rc, res["why"]), (1, "already-bound"))


class Poll(Base):
    def test_s3_poll(self):
        self.assertEqual(self.poll(self.api())[0]["state"], "none")
        api = self.api()
        self.start(api)
        res, rc = self.poll(api)
        self.assertEqual((rc, res["state"]), (0, "waiting"))
        api.poll_status = "bound"
        res, rc = self.poll(api)
        self.assertEqual((rc, res["state"], res["slug"]), (0, "confirm_local", "lfu0327"))
        self.assertEqual(self.tokens(), [], "poll 不寫正式憑證")
        self.assertEqual(self.asked, [], "poll 不彈指紋")
        n = len(api.polls)
        self.assertEqual(self.poll(api)[0]["state"], "confirm_local")
        self.assertEqual(len(api.polls), n, "confirm_local 之後不再問伺服器")
        # 410(過期 / 確認數字錯 3 次)
        BG.cancel(self.env)
        api = self.api(poll_error=ApiError(410, "page_api_bind_expired"))
        self.start(api)
        res, _ = self.poll(api)
        self.assertEqual((res["state"], res["why"]), ("expired", "page_api_bind_expired"))
        self.assertIsNone(self.pending_raw())
        # 本機到期
        api = self.api()
        self.start(api)
        self.clock.t = NOW + timedelta(minutes=11)
        self.assertEqual(self.poll(api)[0]["state"], "expired")
        self.assertIsNone(self.pending_raw())
        self.clock.t = NOW
        # declined
        api = self.api(poll_status="declined")
        self.start(api)
        self.assertEqual(self.poll(api)[0]["state"], "declined")
        self.assertIsNone(self.pending_raw())
        # 別台
        api = self.api(poll_status="bound", mid="00000000-0000-4000-8000-000000000000")
        self.start(api)
        res, rc = self.poll(api)
        self.assertEqual((rc, res["why"]), (1, "wrong-machine"))
        self.assertIsNone(self.pending_raw())
        self.assertEqual(self.tokens(), [])
        # 暫時連不上 ⇒ 還留著
        api = self.api(poll_error=TransportError("x"))
        self.start(api)
        res, rc = self.poll(api)
        self.assertEqual((rc, res["why"], res["state"]), (1, "page-api-unavailable", "waiting"))
        self.assertIsNotNone(self.pending_raw())


class Confirm(Base):
    def ready(self):
        api = self.api(poll_status="bound")
        self.start(api)
        self.assertEqual(self.poll(api)[0]["state"], "confirm_local")
        return api

    def test_s4_confirm(self):
        self.assertEqual(self.confirm()[0]["state"], "none")
        self.start(self.api())
        res, rc = self.confirm()
        self.assertEqual((rc, res["why"]), (1, "not-ready"))
        BG.cancel(self.env)
        self.ready()
        res, rc = self.confirm(seat=True)
        self.assertEqual((rc, res["why"]), (2, "seat_caller"))
        self.assertEqual(self.asked, [], "席位呼叫:不彈")
        self.assertIsNotNone(self.pending_raw(), "席位呼叫不作廢")
        res, rc = self.confirm(prove=self.prove_no)
        self.assertEqual((rc, res["state"], res["why"]), (0, "declined", "touchid_cancel"))
        self.assertIsNone(self.pending_raw())
        self.assertEqual(self.tokens(), [], "拒 ⇒ 沒憑證")
        self.asked.clear()
        self.ready()
        res, rc = self.confirm()
        self.assertEqual((rc, res["state"], res["slug"], res["days_left"]), (0, "bound", "lfu0327", 30))
        self.assertEqual(len(self.asked), 1)
        self.assertIn("lfu0327", self.asked[0])
        self.assertIn("綁到帳號", self.asked[0])
        toks = self.tokens()
        self.assertEqual((len(toks), toks[0]["assistant_token"]), (1, TOKEN))
        path, _ = store.token_path("t_lfu0327", "p1", self.env)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertIsNone(self.pending_raw())

    def test_s4b_confirm_late(self):
        self.ready()
        self.clock.t = NOW + timedelta(seconds=BG.CONFIRM_WINDOW_S + 1)
        res, rc = self.confirm()
        self.assertEqual((res["state"], res["why"]), ("expired", "confirm-timeout"))
        self.assertEqual((self.asked, self.tokens()), ([], []))

    def test_s5_cancel(self):
        self.start(self.api())
        self.assertEqual(BG.cancel(self.env), ({"ok": True, "state": "cancelled"}, 0))
        self.assertIsNone(self.pending_raw())
        self.assertEqual(BG.cancel(self.env)[0]["state"], "cancelled")
        self.ready()                                   # 伺服器已發出憑證才取消 ⇒ 叫人去後台解除
        self.assertEqual(BG.cancel(self.env), ({"ok": True, "state": "cancelled", "manage_url": BG.MANAGE_URL}, 0))
        self.assertIsNone(self.pending_raw())
        self.assertEqual(self.tokens(), [])

    def test_s6_digits_never_on_disk(self):
        def scan(step):
            for p in Path(self.env["EZ2_AIB_STATE_ROOT"]).rglob("*"):
                if p.is_file():
                    raw = p.read_text(encoding="utf-8", errors="replace")
                    self.assertNotIn("confirm_digits", raw, (step, str(p)))
                    self.assertNotIn(CODE, raw, (step, str(p)))
        api = self.api(poll_status="bound")
        res, _ = self.start(api)
        self.assertEqual(res["confirm_digits"], "07")
        scan("start")
        self.poll(api)
        scan("poll")
        self.confirm()
        scan("confirm")


class Status(Base):
    def test_s7_remote_status(self):
        import seat_act as SA
        d = SA._bind_info(self.env)
        self.assertEqual(d, {"slug": None, "bind_expires_at": None, "days_left": None})
        self.assertFalse((Path(store.token_dir(self.env)[0]) / "machine.json").exists(), "status 不建 machine.json")
        api = self.api(poll_status="bound")
        self.start(api)
        self.poll(api)
        self.confirm()
        d = SA._bind_info(self.env)
        self.assertEqual(d["slug"], "lfu0327")
        self.assertEqual(d["bind_expires_at"], (NOW + timedelta(days=30)).isoformat())
        self.assertIsInstance(d["days_left"], int)
        self.assertEqual(BG.days_left((NOW + timedelta(days=6, hours=1)).isoformat(), self.clock), 7)
        self.assertEqual(BG.days_left((NOW - timedelta(hours=1)).isoformat(), self.clock), 0)
        self.assertIsNone(BG.days_left("nope", self.clock))


class Cli(Base):
    def test_s8_cli(self):
        import cli as C
        api = self.api()
        api.base = datetime.now(timezone.utc)
        orig = C._api_for
        C._api_for = lambda record, args, environ: api
        self.addCleanup(setattr, C, "_api_for", orig)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = C.main(["bind", "--start", "--json", "--seat-label", "Mac"], environ=self.env)
        lines = buf.getvalue().strip().split("\n")
        self.assertEqual((rc, len(lines)), (0, 1), buf.getvalue())
        self.assertEqual(json.loads(lines[0])["confirm_digits"], "07")
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
                C.main(["bind", "--start", "--serial", "EZP-AAAA-BBBB-CCCC"], environ=self.env)
        api.poll_status = "bound"
        with redirect_stdout(io.StringIO()):
            C.main(["bind", "--poll", "--json"], environ=self.env)
        buf = io.StringIO()
        env = dict(self.env, AIB_TOUCHID="0")
        with redirect_stdout(buf):
            rc = C.main(["bind", "--confirm", "--json"], environ=env)
        res = json.loads(buf.getvalue())
        self.assertEqual((res["state"], res.get("why"), res)[:2], ("declined", "touchid_off"), "AIB_TOUCHID=0 ⇒ 拒,不彈真指紋")
        self.assertEqual(self.tokens(), [])

    def test_s9_i18n(self):
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.page.help.bind_") or k == "aib.page.touchid_bind"}
            self.assertGreaterEqual(len(keys), 6, lang)
            self.assertIn("{slug}", d["aib.page.touchid_bind"], lang)
            want = want or keys
            self.assertEqual(keys, want, lang)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/page/bind_bg.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_confirm_no_prove(self):
        self._knife("    _via, why = (prove or SA.prove)(reason, e)\n", "    _via, why = 'touchid', None\n", "Confirm.test_s4_confirm")

    def test_k2_digits_on_disk(self):
        self._knife('"seat_label": seat_label, "started_at"', '"confirm_digits": digits, "seat_label": seat_label, "started_at"',
                    "Confirm.test_s6_digits_never_on_disk")


if __name__ == "__main__":
    unittest.main()
