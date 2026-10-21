#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""BB Call 轉手機(PROPOSAL-gaps 第 3 件;Andy「做,預設關」;@ee4f8 @9f808 條件)。網路全換假的,不需要 nostr venv。

P1 預設關:有該轉的信也不送(off)
P2 開了:給本人的信躺超過 minutes 才轉;沒到 ⇒ none_due;一般信 / 陌生全球信不算
P3 送出的那一行只有數量:不含內文、寄件人名字、席碼
P4 同一封只轉一次;新來的信才會再轉
P5 每小時上限 per_hour:到了 ⇒ cap,不記成已轉(下個小時再送)
P6 沒配對(主人簽的清單裡沒有手機)⇒ no_phone、不記;全部手機送失敗 ⇒ send_failed、不記;一支成功就算
P7 CLI on / off / status、範圍外 rc 2;設定檔 600;檔案亂寫(on="yes"、minutes=0)⇒ 當預設
P8 接上:chat_nostr 主迴圈有 tick、只用 _signed_phones 當收件人、用機器金鑰;四語都有
刀(複本):K1 不檢查 on ⇒ P1 紅;K2 拿掉每小時上限 ⇒ P5 紅
"""
import asyncio
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
for _p in (COCKPIT, HERE):
    sys.path.insert(0, str(_p))
import chat_phone_forward as PF  # noqa: E402
import dm_queue as Q  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SEAT = "abcde"
SECRET = "合約草稿第三版內容"
NAME = "王小明"
T0 = 1_800_000_000.0
PHONE = "npub1phone"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-pfwd-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.td / "state"), "HOME": str(self.td), "EZ2_AIB_LOCALE": "zh-TW",
                    "AIB_CHAT_HOME": str(self.td / "chat"), "AIB_DM_ADDR": "100.114.152.121", "PATH": "/usr/bin:/bin"}
        self.sent = []

    def mail(self, at=T0, uid="70001"):
        return Q.enqueue(SEAT, SECRET, "Telegram:" + NAME, sender_kind="external_im", sender_origin="telegram:" + uid,
                         env=self.env, now=at, status=Q.HOLD_STATUS, hold_why=Q.IM_ROOM_WHY,
                         im_info={"net": "telegram", "id": uid, "shown": NAME, "relation": "friend"})

    def on(self, **kw):
        res, why = PF.configure(self.env, on=True, **kw)
        self.assertIsNone(why)

    def tick(self, now, phones=(PHONE,), ok=True):
        async def phones_fn():
            return set(phones)

        async def send_fn(npub, text):
            self.sent.append((npub, text))
            return ok(npub) if callable(ok) else ok
        return asyncio.run(PF.tick(self.env, now, phones_fn, send_fn))


class Forward(Base):
    def test_p1_default_off(self):
        self.mail()
        self.assertFalse(PF.load(self.env)["on"])
        self.assertEqual(self.tick(T0 + 3600), "off")
        self.assertEqual(self.sent, [])

    def test_p2_only_after_minutes(self):
        self.on(minutes=15)
        Q.enqueue(SEAT, "一般信", "@x", env=self.env, now=T0)
        self.assertEqual(self.tick(T0 + 3600), "none_due", "一般信不算")
        self.mail(at=T0)
        self.assertEqual(self.tick(T0 + 14 * 60), "none_due")
        self.assertEqual(self.tick(T0 + 15 * 60), "sent")
        self.assertEqual(len(self.sent), 1)

    def test_p3_line_has_count_only(self):
        self.on(minutes=1)
        self.mail(at=T0)
        self.mail(at=T0, uid="70002")
        self.tick(T0 + 120)
        text = self.sent[0][1]
        self.assertEqual(text, "艦長室有 2 封新信")
        for leak in (SECRET, NAME, SEAT, "70001"):
            self.assertNotIn(leak, text)

    def test_p4_once_per_letter(self):
        self.on(minutes=1, per_hour=12)
        self.mail(at=T0)
        self.assertEqual(self.tick(T0 + 120), "sent")
        self.assertEqual(self.tick(T0 + 240), "none_due")
        self.mail(at=T0 + 200, uid="70002")
        self.assertEqual(self.tick(T0 + 400), "sent")
        self.assertEqual([t for _, t in self.sent], ["艦長室有 1 封新信", "艦長室有 2 封新信"])

    def test_p5_hourly_cap(self):
        self.on(minutes=1, per_hour=1)
        self.mail(at=T0)
        self.assertEqual(self.tick(T0 + 120), "sent")
        self.mail(at=T0 + 130, uid="70002")
        self.assertEqual(self.tick(T0 + 600), "cap")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.tick(T0 + 120 + 3601), "sent", "過了一小時再送,那封還沒算轉過")

    def test_p6_phones_and_failures(self):
        self.on(minutes=1)
        self.mail(at=T0)
        self.assertEqual(self.tick(T0 + 120, phones=()), "no_phone")
        self.assertEqual(self.tick(T0 + 130, ok=False), "send_failed")
        self.assertEqual(PF.load(self.env)["sent"], {}, "沒送到不記")
        self.assertEqual(self.tick(T0 + 140, phones=("npub1a", "npub1b"), ok=lambda n: n == "npub1b"), "sent")
        self.assertEqual(len(PF.load(self.env)["sent"]), 1)

    def test_p7_cli_and_file(self):
        a = SimpleNamespace(action="on", minutes=30, per_hour=2, json=True)
        buf = io.StringIO()
        self.assertEqual(PF.cmd(a, self.env, buf), 0)
        self.assertEqual(json.loads(buf.getvalue()), {"ok": True, "on": True, "minutes": 30, "per_hour": 2})
        self.assertEqual(stat.S_IMODE(PF.path(self.env).stat().st_mode), 0o600)
        for kw, why in ((dict(minutes=0), "bad_minutes"), (dict(per_hour=99), "bad_per_hour")):
            buf = io.StringIO()
            self.assertEqual(PF.cmd(SimpleNamespace(action="status", json=True, **dict(dict(minutes=None, per_hour=None), **kw)),
                                    self.env, buf), 2)
            self.assertEqual(json.loads(buf.getvalue())["why"], why)
        buf = io.StringIO()
        self.assertEqual(PF.cmd(SimpleNamespace(action="off", minutes=None, per_hour=None, json=False), self.env, buf), 0)
        self.assertIn("關", buf.getvalue())
        PF.path(self.env).write_text(json.dumps({"on": "yes", "minutes": 0, "per_hour": True}))
        rec = PF.load(self.env)
        self.assertEqual((rec["on"], rec["minutes"], rec["per_hour"]), (False, 15, 3))

    def test_p8_wired(self):
        src = (COCKPIT / "chat_nostr.py").read_text(encoding="utf-8")
        self.assertIn("await PF.tick(None, time.time(), receiver._signed_phones, receiver._notify_phone)", src)
        i = src.index("async def _notify_phone(")
        body = src[i:src.index("async def send_receipt(", i)]
        self.assertIn('_keys("device")', body)
        self.assertNotIn('_keys("owner")', body)
        self.assertNotIn("ez2.page", body)
        cb = (COCKPIT / "chat_bridge.py").read_text(encoding="utf-8")
        self.assertIn('sub.add_parser("phone-forward"', cb)
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            for k in ("aib.chat.bbcall_phone", "aib.chat.help.phone_forward", "aib.chat.phone_forward.status_on",
                      "aib.chat.phone_forward.status_off", "aib.chat.phone_forward.bad_minutes",
                      "aib.chat.phone_forward.bad_per_hour"):
                self.assertIn(k, d, (lang, k))
            self.assertIn("{n}", d["aib.chat.bbcall_phone"])


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_phone_forward.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_ignore_switch(self):
        self._knife('    if not rec["on"]:\n        return None, [], "off"\n', "", "Forward.test_p1_default_off")

    def test_k2_no_cap(self):
        self._knife('    if sum(1 for t in rec["log"] if now - t < 3600) >= rec["per_hour"]:\n        return None, ids, "cap"\n',
                    "", "Forward.test_p5_hourly_cap")


if __name__ == "__main__":
    unittest.main()
