#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談艙位名冊與收信中繼站清單(開發文件第 5.4、5.6、5.8 節;22.1 PR-7 = 第 1 期 1.3)。

核心(一定跑,只用標準庫):
R1 aib chat on 沒給顯示名稱 / 國別 ⇒ rc 2、不開;名稱含 >> ⇒ rc 2;合格 ⇒ rc 0
R2 aib chat name / country:合格就改、不合格不動;國碼小寫轉大寫、三碼不收
R3 組名冊:預設一席都不列(Andy 23:5x 裁);publish 才列(npub、人格名、角色);不在活席的不列;hide 拿掉;
   雜湊不含 updated_at、改名就變
R4 驗名冊:自己組的那份驗得過;owner 不是簽章者 ⇒ 拒;http 中繼站 ⇒ 拒;同艙同席兩筆 ⇒ 拒;壞 npub ⇒ 拒;
   不合的名字退回 None(不整份拒);find_seat;沒有共同信封版本 ⇒ None
R5 中繼站:預設 primal、nos.lol;AIB_CHAT_RELAYS 只收 wss:// 與本機 ws://127.0.0.1
R6 沒有 nostr-sdk:轉接程式組好名冊、狀態 no_relays、不寫心跳;主人資料被刪 ⇒ no_profile
連 Nostr(要 AIB_TEST_NOSTR_VENV = 裝了 nostr-sdk==0.45.1 的 venv;只連本機假中繼站,沒有就跳過):
N1 當班發名冊 + 10050 ⇒ 狀態 published、published.json 雜湊 = outgoing;lookup <主人 npub>/<艙>/<席> 拿到席 npub 與收信站
N2 lookup:沒公開的席 ⇒ no_seat;沒發過名冊的主人 ⇒ no_roster
N3 冒名:有人用主人的 pubkey 塞一則簽章不對的名冊(id 對、簽章錯)⇒ 當沒看到(假站把真名冊擠掉 ⇒ no_roster),絕不採用偽造內容
N4 off ⇒ 發空名冊、撤下 10050 ⇒ lookup 變 no_seat
L1 洩漏:整個沙盒(金鑰檔與 wrap.key 以外)、假中繼站收到的每一則事件,都沒有 nsec1 與私鑰 hex
刀(複本):K1 parse 不比對 owner 與簽章者 ⇒ R4 紅。
  「抓名冊不驗簽」這把刀砍不出紅(10-16 實測):nostr-sdk 0.45.1 收事件時自己就驗簽、簽章錯的直接丟,
  chat_nostr._fetch_latest 那一行 e.verify() 是第二道(換 SDK 或換抓法時還在)。N3 驗的是整條路不採用偽造內容。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
BRIDGE = COCKPIT / "chat_bridge.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""


def _nostr_ok():
    py = Path(NOSTR_VENV) / "bin" / "python"
    if not NOSTR_VENV or not py.exists():
        return False
    return subprocess.run([str(py), "-c", "import nostr_sdk"], capture_output=True).returncode == 0


HAVE_NOSTR = _nostr_ok()


class Box(object):
    def __init__(self, relays=None):
        self.td = Path(tempfile.mkdtemp(prefix="aib-roster-"))
        self.home = self.td / "home"
        self.home.mkdir()
        self.chat = self.home / ".ez2-aib" / "chat"
        self.root = self.td / "bridge"
        self.relays = relays
        self.procs = []

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
        env.update({"HOME": str(self.home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "AIB_CHAT_TICK": "0.2",
                    "EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1", "AIB_CHAT_NET_TIMEOUT": "3"})
        if self.relays:
            env["AIB_CHAT_RELAYS"] = ",".join(self.relays)
        env.update(extra)
        return env

    def cli(self, *args, **extra):
        return subprocess.run([sys.executable, str(BRIDGE)] + list(args), capture_output=True, text=True,
                              env=self.env(**extra), timeout=120)

    def seat(self, inst, win, uuid, uid, name, role="dev"):
        d = self.home / ".ez2-aib" / "instances" / inst / "seats"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % win)).write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": uuid, "role": role}))
        disp = self.home / ".ez2-aib" / "identity" / "seat-display.json"
        disp.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(disp.read_text()) if disp.exists() else {"display": {}}
        cur["display"][uuid.replace("-", "")[-5:]] = {"persona_uid": uid, "display_name": name}
        disp.write_text(json.dumps(cur))
        return uuid.replace("-", "")[-5:]

    def link_venv(self):
        self.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(NOSTR_VENV, str(self.chat / "venv"))

    def start_run(self):
        p = subprocess.Popen([sys.executable, str(BRIDGE), "run"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=self.env(), start_new_session=True)
        self.procs.append(p)
        return p

    def status(self):
        try:
            return json.loads((self.root / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait_for(self, cond, secs=30):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.td, ignore_errors=True)


class Base(unittest.TestCase):
    relays = None

    def setUp(self):
        self.b = Box(self.relays)
        self.addCleanup(self.b.close)
        self.penv = self.b.env()

    def on(self):
        r = self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "tw")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Profile(Base):
    def test_r1_on_needs_profile(self):
        r = self.b.cli("on", "--no-venv")
        self.assertEqual(r.returncode, 2)
        self.assertIn("顯示名稱與國別", r.stdout)
        self.assertFalse((self.b.chat / "enabled.json").exists())
        r = self.b.cli("on", "--no-venv", "--name", "Andy>>x", "--country", "TW")
        self.assertEqual(r.returncode, 2)
        self.assertIn("顯示名稱不收", r.stdout)
        self.on()
        prof = json.loads((self.b.chat / "profile.json").read_text())
        self.assertEqual((prof["display_name"], prof["country"], prof["public"]), ("Andy", "TW", []))
        self.assertEqual(oct((self.b.chat / "profile.json").stat().st_mode & 0o777), "0o600")

    def test_r2_name_country(self):
        self.on()
        self.assertEqual(self.b.cli("name", "Andy Lin").returncode, 0)
        self.assertEqual(self.b.cli("name", "IP:1.2.3.4/6ddf/x").returncode, 2)
        self.assertEqual(self.b.cli("country", "jp").returncode, 0)
        self.assertEqual(self.b.cli("country", "JPN").returncode, 2)
        prof = json.loads((self.b.chat / "profile.json").read_text())
        self.assertEqual((prof["display_name"], prof["country"]), ("Andy Lin", "JP"))


class Build(Base):
    def test_r3_build(self):
        a = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        c = self.b.seat("20261015-000000-6ddf", "w3", "00000000-0000-0000-0000-00000000ccc3", "cccc33", "Helper")
        self.on()
        content, dm = CR.build(self.penv)
        self.assertEqual(content["cabins"], [], "預設一席都不列")
        self.assertEqual(dm, [])
        self.assertEqual(self.b.cli("publish", a).returncode, 0)
        self.assertEqual(self.b.cli("publish", "zzzzz").returncode, 0)          # 不是活席:記著,但名冊不列
        content, dm = CR.build(self.penv)
        seats = content["cabins"][0]["seats"]
        self.assertEqual(content["cabins"][0]["cabin"], "6ddf")
        self.assertEqual([(s["seat"], s["name"], s["role"]) for s in seats], [(a, "AIB工程師", "dev")])
        self.assertTrue(seats[0]["npub"].startswith("npub1"))
        self.assertEqual([d["seat5"] for d in dm], [a])
        self.assertEqual(content["owner"], NK.read_map(self.penv)["owner"]["npub"])
        self.assertNotIn(c, json.dumps(content))
        h1 = CR.content_hash(content)
        self.assertEqual(CR.content_hash(dict(content, updated_at=content["updated_at"] + 99)), h1)
        self.b.cli("name", "Andy2")
        self.assertNotEqual(CR.content_hash(CR.build(self.penv)[0]), h1)
        self.assertEqual(self.b.cli("hide", a).returncode, 0)
        self.assertEqual(CR.build(self.penv)[0]["cabins"], [])

    def test_r4_parse(self):
        a = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        self.on()
        self.b.cli("publish", a)
        content, _ = CR.build(self.penv)
        owner = content["owner"]
        rec = CR.parse(json.dumps(content), owner)
        self.assertEqual(CR.find_seat(rec, "6ddf", a)["seat"], a)
        self.assertIsNone(CR.find_seat(rec, "6ddf", "zzzzz"))
        self.assertEqual(CR.common_envelope(rec), 1)
        other = NK.npub(NK.pubkey_hex("%064x" % 5))
        with self.assertRaises(CR.RosterError):
            CR.parse(json.dumps(content), other)                           # 名冊不能替別的金鑰作主
        bad = json.loads(json.dumps(content))
        bad["dm_relays"] = ["http://evil.example"]
        with self.assertRaises(CR.RosterError):
            CR.parse(bad, owner)
        dup = json.loads(json.dumps(content))
        dup["cabins"][0]["seats"].append(dict(dup["cabins"][0]["seats"][0]))
        with self.assertRaises(CR.RosterError):
            CR.parse(dup, owner)
        badnpub = json.loads(json.dumps(content))
        badnpub["cabins"][0]["seats"][0]["npub"] = "npub1xyz"
        with self.assertRaises(CR.RosterError):
            CR.parse(badnpub, owner)
        longname = json.loads(json.dumps(content))
        longname["display_name"] = "x" * 40
        longname["cabins"][0]["seats"][0]["name"] = "a>>b"
        rec = CR.parse(longname, owner)
        self.assertIsNone(rec["display_name"])
        self.assertIsNone(rec["cabins"][0]["seats"][0]["name"])
        old = json.loads(json.dumps(content))
        old["envelope"] = [9]
        self.assertIsNone(CR.common_envelope(CR.parse(old, owner)))

    def test_r5_relays(self):
        self.assertEqual(CR.relays({}), ["wss://relay.primal.net", "wss://nos.lol"])
        self.assertEqual(CR.relays({"AIB_CHAT_RELAYS": "wss://a.example, http://b.example, ws://127.0.0.1:7000, ws://10.0.0.1:1"}),
                         ["wss://a.example", "ws://127.0.0.1:7000"])
        self.assertEqual(CR.relays({"AIB_CHAT_RELAYS": "http://x"}), list(CR.DEFAULT_RELAYS))

    def test_r6_bridge_without_nostr(self):
        a = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        self.on()
        self.b.cli("publish", a)
        self.b.start_run()
        self.assertTrue(self.b.wait_for(lambda: self.b.status().get("state") == "no_relays"), self.b.status())
        out = json.loads((self.b.root / "roster" / "outgoing.json").read_text())
        self.assertEqual(out["content"]["cabins"][0]["seats"][0]["seat"], a)
        self.assertFalse((self.b.root / "heartbeat.json").exists())
        (self.b.chat / "profile.json").unlink()
        self.assertTrue(self.b.wait_for(lambda: self.b.status().get("state") == "no_profile"), self.b.status())


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        self.b = Box([self.relay.url])
        self.addCleanup(self.b.close)
        self.b.link_venv()
        self.seat = self.b.seat("20261015-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000abb8", "aaaa11", "AIB工程師")
        self.hidden = self.b.seat("20261015-000000-6ddf", "w3", "00000000-0000-0000-0000-00000000ccc3", "cccc33", "Helper")
        r = self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.b.cli("publish", self.seat)
        self.owner = NK.read_map(self.b.env())["owner"]["npub"]
        self.b.start_run()
        ok = self.b.wait_for(lambda: self.b.status().get("state") == "published", 40)
        self.assertTrue(ok, self.b.status())

    def lookup(self, addr):
        r = self.b.cli("lookup", addr)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_n1_publish_and_lookup(self):
        pub = json.loads((self.b.root / "roster" / "published.json").read_text())
        out = json.loads((self.b.root / "roster" / "outgoing.json").read_text())
        self.assertEqual(pub["hash"], out["hash"])
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual(rc, 0, rec)
        self.assertEqual(rec["seat"]["npub"], out["dm_seats"][0]["npub"])
        self.assertEqual(rec["dm_relays"], [self.relay.url])
        self.assertEqual((rec["display_name"], rec["country"]), ("Andy", "TW"))

    def test_n2_not_found(self):
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.hidden))
        self.assertEqual((rc, rec["why"]), (2, "no_seat"))
        stranger = NK.npub(NK.pubkey_hex("%064x" % 7))
        rc, rec = self.lookup("%s/6ddf/%s" % (stranger, self.seat))
        self.assertEqual((rc, rec["why"]), (2, "no_roster"))

    def test_n3_forged_roster_ignored(self):
        from aib_fake_relay import RelayClient, make_event
        owner_hex = NK.npub_to_hex(self.owner)
        forged_content = {"v": 1, "owner": self.owner, "display_name": "Evil", "country": "TW", "envelope": [1],
                          "dm_relays": [self.relay.url], "cabins": [{"cabin": "6ddf", "seats": [
                              {"seat": self.seat, "npub": NK.npub(NK.pubkey_hex("%064x" % 9)), "accepts": ["dm"]}]}]}
        ev = make_event(owner_hex, CR.KIND_ROSTER, json.dumps(forged_content), tags=[["d", CR.ROSTER_D]],
                        created_at=int(time.time()) + 5)
        ev["sig"] = "00" * 64                                          # id 對、簽章錯
        fake_seat = make_event(NK.pubkey_hex("%064x" % 9), CR.KIND_DM_RELAYS, "", tags=[["relay", self.relay.url]])
        c = RelayClient(self.relay.url)
        for e in (ev, fake_seat):                                      # 偽造要完整(名冊 + 那席的 10050),刀拿掉驗簽才會整條走通
            c.send(["EVENT", e])
            c.recv()
        # 假中繼站不驗簽、名冊又是可替換事件(同主人同 d 只留最新)⇒ 偽造那則把真的擠掉了(真的中繼站會驗簽、收不進去)。
        #   這裡要驗的是用戶端:簽章錯的那則一律當沒看到 ⇒ 找不到名冊(rc 2),絕不能採用「Evil」那份。
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual((rc, rec.get("why")), (2, "no_roster"), rec)
        self.assertNotIn("Evil", json.dumps(rec), "簽章錯的名冊不准被採用")

    def test_n4_off_unpublishes(self):
        r = self.b.cli("off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已發空名冊", r.stdout)
        rc, rec = self.lookup("%s/6ddf/%s" % (self.owner, self.seat))
        self.assertEqual((rc, rec["why"]), (2, "no_seat"))

    def test_l1_no_secret_leak(self):
        env = self.b.env()
        m = NK.read_map(env)
        stems = ["owner"] + [e["key"] for e in m["seats"] if e["status"] == "active"]
        secrets = [NK.load_secret(s, env) for s in stems]
        nsecs = [NK.bech32_encode("nsec", bytes.fromhex(s)) for s in secrets]
        blobs = []
        for p in self.b.td.rglob("*"):
            if p.is_file() and not p.name.endswith(".key") and p.name != "wrap.key" and "venv" not in p.parts:
                blobs.append((str(p), p.read_bytes().decode("utf-8", "replace").lower()))
        from aib_fake_relay import RelayClient
        c = RelayClient(self.relay.url)
        c.send(["REQ", "all", {}])
        evs, _ = c.recv_until_eose("all")
        blobs.append(("relay", json.dumps(evs).lower()))
        self.assertTrue(evs)
        for where, text in blobs:
            self.assertNotIn("nsec1", text, where)
            for s, ns in zip(secrets, nsecs):
                self.assertNotIn(s, text, where)
                self.assertNotIn(ns, text, where)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, replacement, cell, env=None):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300,
                            env=dict(os.environ, **(env or {})))
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    def test_k1_parse_trusts_owner_field(self):
        self._knife("src/cockpit/chat_roster.py", 'if rec.get("owner") != signer_npub or not _NPUB_RE.match(signer_npub or ""):',
                    'if not _NPUB_RE.match(signer_npub or ""):', "Build.test_r4_parse")


if __name__ == "__main__":
    unittest.main()
