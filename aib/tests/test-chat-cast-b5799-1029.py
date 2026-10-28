#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""影音牆投放 AIB 畫面(10-29,SPEC-ytwall-aib-cast-aeb40-1029;@aeb40 02:47 六點)。

C1 發單:好友 + share 不是 off;Touch ID(approve_gate chat_cast.issue,trusted_device 高風險);沒簽章器 / 讀不到開機編號 /
   鎖屏 ⇒ 不發、不彈窗;取消 ⇒ 不發;視窗字由核心組(顯示名夾零寬 ⇒ 只放 npub 碼;--pin 多一段)(K1)
C2 投放單每輪驗:簽章、到期、時鐘倒退、開機編號、鎖屏、觀看者解除好友 ⇒ 作廢 + 送 close + 記帳(K2、K6)
C3 心跳:60 秒內沒心跳 ⇒ 不推;visible:false ⇒ 不推但 👁 還在;有心跳才推(K3)
C4 白名單:夾 ctx / dm / path / host / token / 未知欄位全丟;📌 字只在 --pin 才帶;兩端都洗(K4)
C5 觀看端:寄件主人 ≠ 投放單主人 ⇒ 丟;過期 / 已關 ⇒ 丟;1 份 / 3 秒;每位主人 ≤ 4 格;close 刪畫面檔(K5)
C6 間隔升降:基準 5、負載 / 電池 15、重負載 / 低電 60、觀看端只能拉長;內容沒變只送 unchanged(60 秒一則)(K8)
C7 畫面字串不進 AI:chat_cast 只被轉接程式 / bridge CLI / caps 引用;收件不進佇列(靜態掃描)(K7)
C8 檔權限 0600 / 0700;cast.jsonl 只記中繼資料、2 MB 輪替;list --json = aib-cast/v1;CLI 非 Mac 不發單
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
sys.path.insert(0, str(HERE))
import chat_cast as CC  # noqa: E402
import chat_roster as CR  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

FRIEND = NK.npub(NK.pubkey_hex("%064x" % 0x7201))
MINE = NK.npub(NK.pubkey_hex("%064x" % 0x7202))
STRANGER = NK.npub(NK.pubkey_hex("%064x" % 0x7203))
SPOOF = NK.npub(NK.pubkey_hex("%064x" % 0x7204))
T0 = 1_800_000_000
GOOD = {"ship": "Ez2", "power": "ac", "at": T0, "seats": [
    {"seat": "b5799", "emoji": "🛠", "name": "後端", "ai": "claude", "role": "dev", "state": "busy", "n": 2, "m": 6,
     "floor": 2, "topic": "客戶 X 的報價"}]}


def fake_sign(b):
    return "sig:" + __import__("hashlib").sha256(b).hexdigest()


def fake_verify(b, sig):
    return sig == fake_sign(b)


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-cast-")
        self.addCleanup(td.cleanup)
        self.td = Path(td.name)
        self.root = self.td / "bridge"
        self.env = {"HOME": str(self.td), "EZ2_AIB_STATE_ROOT": str(self.td / "state"), "AIB_CHAT_BRIDGE_DIR": str(self.root),
                    "AIB_CHAT_HOME": str(self.td / "chat"), "EZ2_AIB_LOCALE": "en", "PATH": "/usr/bin:/bin"}
        GF.ensure_dirs(self.root)
        CR.add_friend(FRIEND, "Pro 13", self.env)
        CR.add_friend(MINE, "Mini", self.env, kind="mine")
        CR.add_friend(SPOOF, "Other", self.env)
        CR.set_share("status", FRIEND, self.env)
        CR.set_share("status", SPOOF, self.env)
        self.ep = {"boot": 111, "locked": False}
        self.proved, self.prove_why = [], None
        for name, val in (("SIGN", fake_sign), ("VERIFY", fake_verify), ("EPOCH", lambda: dict(self.ep)),
                          ("PROVE", lambda r, env: self.proved.append(str(r)) or self.prove_why)):
            p = mock.patch.object(CC, name, val)
            p.start()
            self.addCleanup(p.stop)
        CC._POWER.update(at=0)

    def issue(self, who=FRIEND, hours=1, pin=False, now=T0):
        return CC.issue(self.env, self.root, who, hours, pin, now=now)

    def q(self):
        return [rec["msg"] for _p, rec in CC.queued(self.root, now=T0)]

    def beat(self, gid, who=FRIEND, visible=True, want=None, now=T0):
        msg = {"op": "watch", "grant_id": gid, "visible": visible, "at": now}
        if want is not None:
            msg["want"] = want
        return CC.on_incoming(self.env, who, msg, now=now)

    def tick(self, now=T0, load=0.1, ncpu=8, power=("ac", False, 90), raw=GOOD):
        return CC.tick(self.env, self.root, now=now, load=load, ncpu=ncpu, power=power, build=lambda e, n, p: raw)

    def grants(self):
        return CC.read(self.env)["grants"]


class C1Issue(Base):
    def test_issue_touchid_and_open_sent(self):
        g = self.issue(hours=4)
        self.assertEqual(len(self.proved), 1)
        self.assertIn("Pro 13", self.proved[0])
        self.assertIn(FRIEND[:12], self.proved[0])
        self.assertIn(" 4 h", self.proved[0])
        rec = self.grants()[g["grant_id"]]
        self.assertEqual((rec["payload"]["viewer"], rec["payload"]["until"] - T0, rec["payload"]["boot"]), (FRIEND, 4 * 3600, 111))
        self.assertTrue(CC.cast_grant_verify(rec))
        self.assertEqual(self.q()[-1], {"op": "open", "grant_id": g["grant_id"], "until": T0 + 4 * 3600, "at": T0})

    def test_gate_failures_issue_nothing(self):
        self.prove_why = "touchid_cancel"
        with self.assertRaises(CC.CastError) as cm:
            self.issue()
        self.assertEqual(str(cm.exception), "touchid_cancel")
        self.assertEqual(self.grants(), {})
        self.assertEqual(self.q(), [])

    def test_no_signer_epoch_or_locked_no_prompt(self):
        cases = (("SIGN", None, "sign_unavailable"), ("EPOCH", lambda: None, "unavailable"),
                 ("EPOCH", lambda: {"boot": 1, "locked": True}, "locked"))
        for name, val, why in cases:
            with self.subTest(why), mock.patch.object(CC, name, val):
                with self.assertRaises(CC.CastError) as cm:
                    self.issue()
                self.assertEqual(str(cm.exception), why)
        self.assertEqual(self.proved, [])

    def test_who_must_be_shareable_friend(self):
        CR.add_friend(STRANGER, None, self.env, kind="blocks")
        for who in (STRANGER, "junk", MINE + "x"):
            with self.subTest(who), self.assertRaises(CC.CastError):
                self.issue(who)
        CR.set_share("off", FRIEND, self.env)
        with self.assertRaises(CC.CastError):
            self.issue()
        with self.assertRaises(CC.CastError):
            self.issue(hours=2)
        self.assertEqual(self.proved, [])

    def test_reason_uses_core_text(self):
        with mock.patch.object(CC, "_names", lambda env: {FRIEND: ("Pro" + chr(0x202E) + "13", "friend")}):
            self.issue(pin=True)
        self.assertNotIn(chr(0x202E), self.proved[0])
        self.assertNotIn("Pro", self.proved[0])
        self.assertIn(FRIEND[:12], self.proved[0])
        self.assertIn("current-task", self.proved[0])

    def test_gate_registered_high_risk(self):
        import approve_gate as AG
        spec = AG.SITES["chat_cast.issue"]
        self.assertEqual(spec["mod"], "cockpit/chat_cast.py")
        self.assertTrue(AG.CATEGORIES[spec["cat"]]["high_risk"])

    def test_non_mac_refuses(self):
        with mock.patch.object(CC, "PROVE", None), mock.patch.object(CC, "EPOCH", None), mock.patch("sys.platform", "linux"):
            with self.assertRaises(CC.CastError) as cm:
                self.issue()
        self.assertEqual(str(cm.exception), "unavailable")


class C2Void(Base):
    def run_void(self, mutate, now=T0 + 10, closes=(("close", FRIEND),)):
        g = self.issue()["grant_id"]
        self.beat(g, now=now)
        mutate(g)
        out = self.tick(now=now)
        self.assertEqual([(o["kind"], o["to"]) for o in out], list(closes))   # 簽章不過 ⇒ 收件人也不可信,不送
        self.assertNotIn(g, self.grants())
        log = [json.loads(x) for x in (CC._home(self.env) / "cast.jsonl").read_text().splitlines()]
        return log[-1]

    def edit(self, g, **kw):
        rec = CC.read(self.env)
        rec["grants"][g]["payload"].update(kw)
        CC._save(rec, self.env)

    def test_tampered_until_or_viewer(self):
        self.assertEqual(self.run_void(lambda g: self.edit(g, until=T0 + 99 * 3600), closes=())["why"], "sig")

    def test_tampered_viewer(self):
        self.assertEqual(self.run_void(lambda g: self.edit(g, viewer=SPOOF), closes=())["why"], "sig")

    def test_expired(self):
        g = self.issue()["grant_id"]
        out = self.tick(now=T0 + 3600)
        self.assertEqual([o["kind"] for o in out], ["close"])
        self.assertNotIn(g, self.grants())

    def test_clock_back(self):
        g = self.issue()["grant_id"]
        self.beat(g, now=T0 - 600)
        self.assertEqual([o["kind"] for o in self.tick(now=T0 - 600)], ["close"])

    def test_reboot(self):
        self.assertEqual(self.run_void(lambda g: self.ep.update(boot=222))["why"], "reboot")

    def test_locked(self):
        self.assertEqual(self.run_void(lambda g: self.ep.update(locked=True))["why"], "locked")

    def test_unfriended(self):
        self.assertEqual(self.run_void(lambda g: CR.set_share("off", FRIEND, self.env))["why"], "unfriended")


class C3Heartbeat(Base):
    def test_no_watch_no_push(self):
        g = self.issue()["grant_id"]
        self.assertEqual(self.tick(), [])
        self.beat(g, now=T0)
        self.assertEqual([o["kind"] for o in self.tick(now=T0 + 1)], ["frame"])
        self.assertEqual(self.tick(now=T0 + 61 + 10), [])          # 心跳過了 60 秒 ⇒ 停推

    def test_hidden_no_push_but_watching(self):
        g = self.issue()["grant_id"]
        self.beat(g, visible=False)
        self.assertEqual(self.tick(now=T0 + 1), [])
        pl = CC.payload(self.env, now=T0 + 1)
        self.assertEqual([(w["grant_id"], w["live"]) for w in pl["watchers"]], [(g, True)])
        self.assertEqual(pl["watching"], 1)

    def test_watch_from_other_owner_ignored(self):
        g = self.issue()["grant_id"]
        self.assertIsNone(self.beat(g, who=SPOOF))
        self.assertEqual(self.tick(now=T0 + 1), [])


class C4Whitelist(Base):
    def test_unknown_and_private_fields_dropped(self):
        raw = json.loads(json.dumps(GOOD))
        raw.update(ctx=88, unread=3, dm="secret dm", path="/Users/x", host="10.0.0.2", token="tok", layout={"x": 1})
        raw["seats"][0].update(ctx=91, path="/Users/x/proj", dm="hi", host="h", ip="1.2.3.4", term="$ rm", unread=2)
        raw["seats"].append({"seat": "../x", "name": "bad"})
        out = CC.whitelist(raw)
        self.assertEqual(set(out), {"ship", "floor", "power", "at", "seats"})
        self.assertEqual(set(out["seats"][0]), {"seat", "emoji", "name", "ai", "role", "state", "n", "m", "floor"})
        self.assertEqual(len(out["seats"]), 1)
        flat = json.dumps(out, ensure_ascii=False)
        for bad in ("secret", "/Users", "10.0.0.2", "tok", "rm", "客戶"):
            self.assertNotIn(bad, flat)

    def test_pin_only_when_granted(self):
        self.assertNotIn("topic", CC.whitelist(GOOD)["seats"][0])
        self.assertEqual(CC.whitelist(GOOD, True)["seats"][0]["topic"], "客戶 X 的報價")
        g = self.issue()["grant_id"]
        self.beat(g)
        frame = self.tick(now=T0 + 1)[0]["msg"]["data"]
        self.assertNotIn("topic", frame["seats"][0])

    def test_strings_cleaned_and_codes_fixed(self):
        raw = {"ship": "E" + chr(0x200B) + "z2\x1b[31m", "power": "nuclear", "seats": [
            {"seat": "b5799", "name": "x" * 99, "state": "hacking", "ai": "Claude Code!", "n": 9, "m": 0}]}
        out = CC.whitelist(raw)
        s = out["seats"][0]
        self.assertNotIn(chr(0x200B), out["ship"])
        self.assertNotIn("\x1b", out["ship"])
        self.assertEqual((out["power"], s["state"], s["ai"], s["n"], s["m"], len(s["name"]) <= 24), (None, "idle", None, None, None, True))


class C5Viewer(Base):
    def open(self, gid="a" * 16, who=FRIEND, now=T0):
        return CC.on_incoming(self.env, who, {"op": "open", "grant_id": gid, "until": now + 3600, "at": now}, now=now)

    def frame(self, gid="a" * 16, who=FRIEND, now=T0, data=GOOD, h="h1"):
        return CC.on_incoming(self.env, who, {"op": "frame", "grant_id": gid, "seq": 1, "hash": h, "interval": 5, "at": now,
                                              "data": data}, now=now)

    def test_spoofed_sender_dropped(self):
        self.assertEqual(self.open(), "opened")
        self.assertIsNone(self.frame(who=SPOOF))
        self.assertIsNone(self.frame(who=STRANGER))
        self.assertFalse((CC.frames_dir(self.env) / ("a" * 16 + ".json")).exists())
        self.assertEqual(self.frame(now=T0 + 1), "frame")
        g = CC.payload(self.env, now=T0 + 2)["grants"][0]
        self.assertEqual((g["owner"], g["state"], g["sender"]), (FRIEND, "live", "checked_on_receive"))
        self.assertEqual(g["frame"]["seats"][0]["topic"], "客戶 X 的報價")

    def test_rate_and_expiry(self):
        self.open()
        self.assertEqual(self.frame(now=T0 + 1), "frame")
        self.assertEqual(self.frame(now=T0 + 2), "rate")
        self.assertEqual(CC.on_incoming(self.env, FRIEND, {"op": "unchanged", "grant_id": "a" * 16, "hash": "h1"}, now=T0 + 5), "alive")
        self.assertEqual(self.frame(now=T0 + 3601), "expired")

    def test_per_owner_max(self):
        got = [self.open("%016x" % i) for i in range(CC.PER_OWNER_MAX + 1)]
        self.assertEqual(got[-1], "full")
        self.assertEqual(got[:-1], ["opened"] * CC.PER_OWNER_MAX)

    def test_close_removes_frame(self):
        self.open()
        self.frame(now=T0 + 1)
        self.assertEqual(CC.on_incoming(self.env, FRIEND, {"op": "close", "grant_id": "a" * 16}, now=T0 + 2), "closed")
        self.assertFalse((CC.frames_dir(self.env) / ("a" * 16 + ".json")).exists())
        self.assertEqual(self.frame(now=T0 + 9), "expired")

    def test_viewer_rewhitelists(self):
        self.open()
        evil = json.loads(json.dumps(GOOD))
        evil["dm"] = "leak"
        evil["seats"][0]["path"] = "/Users/x"
        self.frame(data=evil, now=T0 + 1)
        saved = (CC.frames_dir(self.env) / ("a" * 16 + ".json")).read_text()
        self.assertNotIn("leak", saved)
        self.assertNotIn("/Users", saved)


class C6Interval(Base):
    def test_levels(self):
        self.assertEqual(CC.interval(1, 8, False, 90), 5)
        self.assertEqual(CC.interval(7, 8, False, 90), 15)
        self.assertEqual(CC.interval(1, 8, True, 90), 15)
        self.assertEqual(CC.interval(13, 8, False, 90), 60)
        self.assertEqual(CC.interval(1, 8, True, 10), 60)
        self.assertEqual(CC.interval(1, 8, False, 90, want=1), 5)      # 觀看端要 1 秒 ⇒ 還是 5
        self.assertEqual(CC.interval(1, 8, False, 90, want=30), 30)
        self.assertEqual(CC.interval(13, 8, False, 90, want=10), 60)   # 只能拉長

    def test_tick_respects_interval_and_unchanged(self):
        g = self.issue()["grant_id"]
        self.beat(g, now=T0)
        first = self.tick(now=T0 + 1, load=13)
        self.assertEqual((first[0]["kind"], first[0]["interval"]), ("frame", 60))
        CC.sent(self.env, first[0], now=T0 + 1)
        self.beat(g, now=T0 + 30)
        self.assertEqual(self.tick(now=T0 + 31, load=13), [])          # 60 秒還沒到
        self.beat(g, now=T0 + 60)
        same = self.tick(now=T0 + 62, load=13)
        self.assertEqual([o["kind"] for o in same], ["unchanged"])
        self.assertNotIn("data", same[0]["msg"])

    def test_viewer_want_one_second(self):
        g = self.issue()["grant_id"]
        self.beat(g, want=1, now=T0)
        CC.sent(self.env, self.tick(now=T0 + 1)[0], now=T0 + 1)
        changed = dict(GOOD, ship="Ez3")
        self.assertEqual(self.tick(now=T0 + 3, raw=changed), [])
        self.assertEqual([o["kind"] for o in self.tick(now=T0 + 6, raw=changed)], ["frame"])


class C7NoAI(unittest.TestCase):
    ALLOWED = {"chat_cast.py", "chat_nostr.py", "chat_bridge.py", "caps.py", "approve_gate.py", "dm_global_files.py"}

    def test_only_wall_paths_import_cast(self):
        bad = []
        for p in (REPO / "src").rglob("*.py"):
            src = p.read_text(encoding="utf-8", errors="replace")
            if p.name not in self.ALLOWED and re.search(r"\bchat_cast\b|cast/in|casts\.json", src):
                bad.append(str(p.relative_to(REPO)))
        self.assertEqual(bad, [], "畫面資料只准進影音牆(外掛只走 aib chat cast list / frames --json)")

    def test_on_cast_never_queues(self):
        src = (COCKPIT / "chat_nostr.py").read_text(encoding="utf-8")
        body = src.split("async def on_cast", 1)[1].split("\n    async def ", 1)[0]
        for bad in ("led.received", "pump_delivery", "dm_queue", "enqueue", "inject", "send_keys"):
            self.assertNotIn(bad, body, bad)


class C8Files(Base):
    def test_modes_and_log_metadata_only(self):
        g = self.issue(pin=True)["grant_id"]
        self.beat(g)
        self.tick(now=T0 + 1)
        CC.on_incoming(self.env, FRIEND, {"op": "open", "grant_id": "b" * 16, "until": T0 + 3600}, now=T0)
        CC.on_incoming(self.env, FRIEND, {"op": "frame", "grant_id": "b" * 16, "hash": "x", "data": GOOD}, now=T0 + 1)
        home = CC._home(self.env)
        for p in (CC.path(self.env), home / "cast.jsonl", CC.frames_dir(self.env) / ("b" * 16 + ".json")):
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600", p)
        self.assertEqual(oct(CC.frames_dir(self.env).stat().st_mode & 0o777), "0o700")
        log = (home / "cast.jsonl").read_text()
        self.assertNotIn("客戶", log)
        self.assertNotIn("b5799", log)
        for row in map(json.loads, log.splitlines()):
            self.assertTrue(set(row) <= set(CC.LOG_KEYS), row)

    def test_log_rotation(self):
        with mock.patch.object(CC, "LOG_MAX", 4000):
            CC.log(self.env, "issue", "a" * 16, FRIEND, now=T0 - 100 * 86400)
            for i in range(80):
                CC.log(self.env, "interval", "a" * 16, FRIEND, now=T0 + i, interval=5)
            p = CC._home(self.env) / "cast.jsonl"
            self.assertLessEqual(p.stat().st_size, 4000)
            self.assertNotIn(str(T0 - 100 * 86400), p.read_text())

    def test_payload_schema(self):
        CC.on_incoming(self.env, FRIEND, {"op": "request", "req_id": "c" * 16}, now=T0)
        self.assertEqual(CC.on_incoming(self.env, FRIEND, {"op": "request", "req_id": "d" * 16}, now=T0 + 5), "quiet")
        pl = CC.payload(self.env, now=T0)
        self.assertEqual(pl["schema"], "aib-cast/v1")
        kinds = {t["key"]: t["kind"] for t in pl["targets"]}
        self.assertEqual((kinds.get(FRIEND), kinds.get(SPOOF), kinds.get(MINE, "device")), ("friend", "friend", "device"))
        self.assertNotIn(STRANGER, kinds)
        self.assertEqual([(r["req_id"], r["from"], r["name"]) for r in pl["incoming"]], [("c" * 16, FRIEND, "Pro 13")])
        self.assertEqual(CC.req_owner(self.env, "c" * 16), FRIEND)

    def test_parse(self):
        for bad in ("", "{", "[]", json.dumps({"op": "frame"}), json.dumps({"op": "hack", "grant_id": "a" * 16}),
                    json.dumps({"op": "frame", "grant_id": "../x"}), "x" * (CC.WIRE_MAX + 1)):
            self.assertIsNone(CC.parse(bad), bad[:30])
        self.assertEqual(CC.parse(json.dumps({"op": "close", "grant_id": "a" * 16}))["op"], "close")

    def test_close_by_caster(self):
        g = self.issue()["grant_id"]
        CC.close(self.env, self.root, g, now=T0 + 5)
        self.assertNotIn(g, self.grants())
        self.assertIn({"op": "close", "grant_id": g, "at": T0 + 5}, self.q())


class C8Cli(Base):
    def _cb(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("chat_bridge_cast", str(COCKPIT / "chat_bridge.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cli_list_and_bridge_off(self):
        cb = self._cb()
        out = io.StringIO()
        rc = cb.main(["cast", "list", "--json"], env=self.env, out=out)
        self.assertEqual((rc, json.loads(out.getvalue())["schema"]), (0, "aib-cast/v1"))
        out = io.StringIO()
        rc = cb.main(["cast", "request", FRIEND, "--json"], env=self.env, out=out)
        self.assertEqual((rc, json.loads(out.getvalue())["why"]), (2, "bridge_off"))


KNIVES = [
    ("K1", '''    why = _human_gate(env, _who_label(o, env), hours, until, pin is True, prove)''', '''    why = None''',
     "C1Issue.test_gate_failures_issue_nothing"),
    ("K2", '''    if not cast_grant_verify(g):
        return "sig"''', "", "C2Void.test_tampered_until_or_viewer"),
    ("K3", '''            if not w or now - int(w.get("at") or 0) > ALIVE_S:''', '''            if not w:''',
     "C3Heartbeat.test_no_watch_no_push"),
    ("K4", '''    return {"ship": _t(raw.get("ship"), 40),''', '''    return {**raw, "ship": _t(raw.get("ship"), 40),''',
     "C4Whitelist.test_unknown_and_private_fields_dropped"),
    ("K5", '''        if not mine or mine.get("owner") != o:          # 冒名:寄件主人 ≠ 投放單主人 ⇒ 丟''',
     '''        if not mine:''', "C5Viewer.test_spoofed_sender_dropped"),
    ("K6", '''    if ep.get("boot") != p.get("boot"):
        return "reboot"''', "", "C2Void.test_reboot"),
    ("K8", '''    return max(iv, min(SLOW_S, max(BASE_S, w)))''', '''    return w if type(want) is int else iv''',
     "C6Interval.test_levels"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(REPO, "src/cockpit/chat_cast.py", needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))

    def test_k7_static_scan(self):
        """K7:有別支(例:seat_act)去讀 cast/in ⇒ C7 紅。刀落在複本的 seat_act.py。"""
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_act.py", "\nimport os\n", "\nimport os\nimport chat_cast  # noqa\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), "C7NoAI.test_only_wall_paths_import_cast"],
                            cwd=str(root), max_procs=40, timeout=120)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("FAIL: test_only_wall_paths_import_cast", out)


if __name__ == "__main__":
    unittest.main()
