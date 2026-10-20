#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機遠端解鎖單(10-21 維護者:「手機版、電腦版能直接指揮 aib」「有 Touch ID、或刷臉優先，沒有使用輸入設定密碼也行」)。

(GUIDE-same-user-ai-threat-model 檢查表:防線 = ez2.page 伺服器的 Ed25519 簽章 + 寫死在已簽版本的公鑰;本機總開關 / nonce 記錄是本機檔，只防網路端，照實寫)
G1 好單子 ⇒ aib seat unlock 不彈 Touch ID、開視窗(分鐘照單子);之後打字走 unlock-window
G2 總開關：預設關 ⇒ 拒;on 要 Touch ID(取消 ⇒ 仍關)、席位呼叫擋;off 不問
G3 拒：改欄位(簽章不符)/ 不認得的 kid / 別台 / 別帳號 / 別席 / 過期 / 壽命太長 / iat 在未來 / auth 不在允許清單 / nonce 格式 / 重放
G4 分鐘被本機上限夾;單子不過不寫視窗、不退回 Touch ID
G5 這台的身分從 ez2.page 綁定檔讀(machine.json + 綁到這台的頁);綁的是別台 ⇒ 拒
G6 公鑰清單釘住：正式清單目前空 ⇒ 一律 unknown-key;程式不從環境變數 / 設定檔讀公鑰
G8 ez2.page 範例單(外層 {grant, sig}、一次性測試鑰)驗得過、canonical 兩邊一致;改一個字不過
G7 入口:aib seat remote status、aib seat unlock --grant(關著 ⇒ rc 3);四語字串
刀(複本):K1 不驗簽 ⇒ G3 紅;K2 不擋重放 ⇒ G3 紅;K3 on 不驗 Touch ID ⇒ G2 紅
"""
import base64
import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
from entitlement import ed25519, signing  # noqa: E402

_spec = importlib.util.spec_from_file_location("seat_act_base_1020", HERE / "test-seat-act-d07f9-1020.py")
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
CODE, PROMPT = BASE.CODE, BASE.PROMPT

SEED = bytes(range(32))
OTHER = bytes(range(1, 33))
KID = "test-1021"
KEYS = {KID: "ed25519:" + ed25519.public_key(SEED).hex()}
MID = "6f1c1d1e-0000-4000-8000-000000000001"
ACCT = "t_lfu0327"
MACHINE = (MID, {ACCT})
EXAMPLE = '{"pubkey": {"kid": "6c0748f51a15a6c0", "alg": "Ed25519", "pub_b64": "FdR6iDS3oZQSQbeme6vsPV5wSdoxWYpcKMtw/9FJflw="}, "canonical": "{\\"account_id\\":\\"acct_example0001\\",\\"auth\\":\\"line\\",\\"exp\\":1792540920,\\"iat\\":1792540800,\\"kid\\":\\"6c0748f51a15a6c0\\",\\"kind\\":\\"seat-unlock\\",\\"machine\\":\\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\",\\"minutes\\":10,\\"nonce\\":\\"bm9uY2UtZXhhbXBsZS0wMDAx\\",\\"seat\\":\\"9f808\\",\\"v\\":1}", "envelope": {"grant": {"v": 1, "kind": "seat-unlock", "kid": "6c0748f51a15a6c0", "account_id": "acct_example0001", "machine": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "seat": "9f808", "minutes": 10, "nonce": "bm9uY2UtZXhhbXBsZS0wMDAx", "iat": 1792540800, "exp": 1792540920, "auth": "line"}, "sig": "P-pgcu_9UfpEMf38JXzV2yuNMLR7g0CEgJ5SIy-LcEsQt4kaCNOoJP0OKwhSNLunCwz9VgTlLjWaipg-E-iXAg"}}'


def make(seed=SEED, **kw):
    now = int(time.time())
    g = {"v": 1, "kind": "seat-unlock", "kid": KID, "machine": MID, "seat": CODE, "minutes": 10,
         "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("="), "iat": now, "exp": now + 120,
         "account_id": ACCT, "auth": "passkey"}
    g.update(kw)
    g["sig"] = base64.urlsafe_b64encode(ed25519.sign(seed, signing.canonical(g))).decode().rstrip("=")
    return g


class Grant(BASE.Box):
    def setUp(self):
        super().setUp()
        import remote_grant
        self.RG = remote_grant

    def on(self, **kw):
        return self.act(self.SA.do_remote, "on", **kw)

    def unlock(self, g, machine=MACHINE, keys=KEYS, **kw):
        return self.act(self.SA.do_unlock, CODE, None, grant=g, keys=keys, machine=machine, **kw)

    def window(self):
        return (self.state / "panel-unlock" / (CODE + ".json")).exists()

    def test_g1_good(self):
        self.on()
        self.asked.clear()
        res, rc = self.unlock(make(minutes=7))
        self.assertEqual((rc, res["ok"], res["via"], res["minutes"], res["unlock_left_s"], res["auth"]), (0, True, "remote-grant", 7, 420, "passkey"), res)
        self.assertEqual(self.asked, [], "有單子就不彈 Touch ID")
        res, rc = self.act(self.SA.do_input, CODE, "嗨", screen=PROMPT)
        self.assertEqual((rc, res["via"]), (0, "unlock-window"), res)
        self.assertEqual(self.asked, [])

    def test_g2_switch(self):
        res, rc = self.unlock(make())
        self.assertEqual((rc, res["why"]), (3, "grant_remote-off"), "預設關")
        res, rc = self.on(touchid=self.tid(False, "cancel"))
        self.assertEqual(rc, 3, res)
        self.assertFalse(self.RG.remote_cfg()["on"], "Touch ID 沒過 ⇒ 仍關")
        res, rc = self.on(env=dict(os.environ, CLAUDE_CODE_SESSION_ID="x"))
        self.assertEqual((rc, res["why"]), (3, "caller-is-seat"))
        self.assertFalse(self.RG.remote_cfg()["on"])
        res, rc = self.on()
        self.assertEqual((rc, res["on"]), (0, True), res)
        self.assertEqual(len(self.asked), 2)
        res, rc = self.act(self.SA.do_remote, "off")
        self.assertEqual((rc, res["on"]), (0, False))
        self.assertEqual(len(self.asked), 2, "關不問")
        res, rc = self.unlock(make())
        self.assertEqual(res["why"], "grant_remote-off")

    def test_g3_rejects(self):
        self.on()
        now = int(time.time())
        bad = make()
        bad["minutes"] = 60                                  # 簽完才改
        cases = [
            (bad, "bad-sig"),
            (make(seed=OTHER), "bad-sig"),
            (make(kid="nope"), "unknown-key"),
            (make(machine="6f1c1d1e-0000-4000-8000-000000000999"), "wrong-machine"),
            (make(account_id="t_other"), "wrong-account"),
            (make(seat="zz999"), "wrong-seat"),
            (make(iat=now - 400, exp=now - 100), "expired"),
            (make(iat=now, exp=now + 3600), "expired"),
            (make(iat=now + 600, exp=now + 700), "expired"),
            (make(auth="sms"), "auth-not-allowed"),
            (make(nonce="short"), "bad-grant"),
            (make(minutes=10.0), "bad-grant"),
            (make(minutes=0), "bad-grant"),
            (make(iat=now + 45, exp=now + 100), "expired"),
            (make(v=2), "bad-grant"),
            (make(kind="license"), "bad-grant"),
            ("not-a-dict", "bad-grant"),
        ]
        for g, why in cases:
            res, rc = self.unlock(g)
            self.assertEqual((rc, res.get("why")), (3, "grant_" + why), (g, res))
        self.assertFalse(self.window(), "全部被拒 ⇒ 沒有視窗")
        g = make()
        self.assertEqual(self.unlock(g)[1], 0)
        self.SA.do_lock(CODE)
        res, rc = self.unlock(g)
        self.assertEqual((rc, res["why"]), (3, "grant_replayed"), "同一張不能用兩次")
        self.assertEqual(self.asked[1:], [], "被拒不退回 Touch ID")

    def test_g4_cap(self):
        self.on(max_min=15)
        res, rc = self.unlock(make(minutes=60))
        self.assertEqual((rc, res["minutes"]), (0, 15))
        self.act(self.SA.do_remote, "on", allow=["passkey"])
        res, rc = self.unlock(make(auth="line"))
        self.assertEqual(res["why"], "grant_auth-not-allowed", "本機可以只收 passkey")

    def test_g5_binding(self):
        self.on()
        d = self.state / "auth" / "page"
        d.mkdir(parents=True)
        (d / "machine.json").write_text(json.dumps({"id": MID, "name": "mac"}))
        self.assertEqual(self.RG.this_machine(), (MID, set()), "還沒綁頁 ⇒ 沒有帳號")
        (d / "t_lfu0327.p1.json").write_text(json.dumps({"tenant_id": ACCT, "page_id": "p1", "machine_id": MID}))
        (d / "t_x.p2.json").write_text(json.dumps({"tenant_id": "t_x", "page_id": "p2", "machine_id": "other"}))
        self.assertEqual(self.RG.this_machine(), (MID, {ACCT}))
        res, rc = self.unlock(make(), machine=None)
        self.assertEqual(rc, 0, res)
        res, rc = self.unlock(make(account_id="t_x"), machine=None)
        self.assertEqual(res["why"], "grant_wrong-account", "綁在別台的頁不算")

    def test_g8_ez2page_example(self):
        """@9f808 給的範例單(一次性測試鑰、外層 {grant, sig}):用本機驗得過;改一個字就不過"""
        ex = json.loads(EXAMPLE)
        keys = {ex["pubkey"]["kid"]: self.RG.pub_from_b64(ex["pubkey"]["pub_b64"])}
        g = ex["envelope"]["grant"]
        self.RG.set_remote(True)
        mins, why = self.RG.verify(ex["envelope"], g["seat"], now=g["iat"] + 10, keys=keys, machine=(g["machine"], {g["account_id"]}))
        self.assertEqual((mins, why), (10, None))
        self.assertEqual(signing.canonical(g).decode(), ex["canonical"], "兩邊 canonical 一致")
        env = json.loads(EXAMPLE)["envelope"]
        env["grant"]["minutes"] = 60
        self.assertEqual(self.RG.verify(env, g["seat"], now=g["iat"] + 10, keys=keys, machine=(g["machine"], {g["account_id"]}))[1], "bad-sig")

    def test_g6_pinned(self):
        self.assertEqual(self.RG.TRUSTED_GRANT_KEYS, {}, "正式公鑰清單改了 ⇒ 要維護者點頭再改這行")
        self.on()
        res, rc = self.unlock(make(), keys=None)
        self.assertEqual(res["why"], "grant_unknown-key", "沒有正式鑰 ⇒ 一律拒")
        src = (COCKPIT / "remote_grant.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("TRUSTED_GRANT_KEYS ="), 1)
        for bad in ("TRUSTED_GRANT_KEYS.update", "TRUSTED_GRANT_KEYS[", "environ.get(\"EZ2_GRANT", "GRANT_KEYS_FILE"):
            self.assertNotIn(bad, src)

    def test_g7_cli(self):
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "remote", "status", "--json"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-300:])
        st = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual((st["on"], st["keys"]), (False, 0))
        f = self.home / "g.json"
        f.write_text(json.dumps(make()))
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "unlock", CODE, "--grant", str(f), "--json"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual((p.returncode, json.loads(p.stdout.strip().splitlines()[-1])["why"]), (3, "grant_remote-off"), p.stderr[-300:])
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertTrue(d.get("aib.seat.panel.touchid_remote_on"), loc)


class Knife(unittest.TestCase):
    def _knife(self, rel, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, rel, needle, repl, tests=(name, "test-seat-act-d07f9-1020.py")) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_no_sig(self):
        self._knife("src/cockpit/remote_grant.py", "    if not ok:\n        return None, \"bad-sig\"", "    if False:\n        return None, \"bad-sig\"", "Grant.test_g3_rejects")

    def test_k2_no_replay(self):
        self._knife("src/cockpit/remote_grant.py", "    if _seen(env, nonce, exp, t, mark=True):", "    if False:", "Grant.test_g3_rejects")

    def test_k3_on_no_touchid(self):
        self._knife("src/cockpit/seat_act.py", '        out["via"], why = prove(T("touchid_remote_on"), env, touchid, touchid_available, platform)',
                    '        out["via"], why = "touchid", None', "Grant.test_g2_switch")


if __name__ == "__main__":
    unittest.main()
