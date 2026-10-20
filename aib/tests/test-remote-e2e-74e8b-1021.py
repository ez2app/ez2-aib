#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""手機指揮端到端(10-21 維護者:「自動開發到一鍵安裝可用」):手機(照 phone.js 的打法)↔ 假 ez2.page(fake_ez2page_remote)↔ aib seat remote link ↔ 席位。

全程真的 HTTP;只有公鑰清單 / 網址從參數注入(正式程式寫死、不從環境變數換 ⇒ E3 證明假伺服器簽的單子正式清單一律拒)。
E1 一輪:選席 → 解鎖單 → 打字(進到席位)→ 看對話(不帶本機路徑)→ 回答單答題 → 停止 → 上鎖 → 再打字被拒;Mac 上沒彈 Touch ID
E2 憑證不對 ⇒ link 停在 unauthorized;機器指紋不對 ⇒ 403
E4 機器指紋測試向量(跟 ez2.page 兩邊各放一份同樣的):uuid 6f1c1d1e-0000-4000-8000-000000000001 ⇒ 89e13cddf7e05b0867bcfa94ff6d636444428fcd4c4509cdc6fb368bc883aa2f
E3 不注入測試公鑰(= 正式清單，目前空)⇒ 解鎖單 grant_unknown-key,什麼都沒送進席位
"""
import importlib.util
import json
import os
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_ez2page_remote as FAKE  # noqa: E402

_spec = importlib.util.spec_from_file_location("seat_act_base_1020", HERE / "test-seat-act-d07f9-1020.py")
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
CODE, PROMPT, PERMISSION = BASE.CODE, BASE.PROMPT, BASE.PERMISSION
FP = "f" * 64
ACCT = "t_lfu0327"
TOKEN = "tok-e2e-secret"


class Phone:
    """照 phone.js:session 拿 csrf → 單子 → /cmd → /result 等回條"""

    def __init__(self, base):
        self.base, self.csrf = base + FAKE.PREFIX, None

    def req(self, method, path, body=None):
        h = {"accept": "application/json"}
        data = None
        if body is not None:
            h["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if self.csrf:
            h["x-ez2-csrf"] = self.csrf
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw, st = resp.read(), resp.status
        except urllib.error.HTTPError as e:
            raw, st = e.read(), e.code
        return st, (json.loads(raw) if raw.strip() else None)

    def login(self):
        st, d = self.req("GET", "/session")
        self.csrf = d["csrf_token"]

    def cmd(self, op, **kw):
        st, d = self.req("POST", "/cmd", dict(kw, machine=FP, op=op, seat=kw.get("seat", CODE)))
        assert st == 200, (st, d)
        until = time.time() + 15
        while time.time() < until:
            st, r = self.req("GET", "/result?id=" + d["id"])
            if st == 200 and r.get("done"):
                return r["result"]
        raise AssertionError("沒等到回條:" + op)


class E2E(BASE.Box):
    def setUp(self):
        super().setUp()
        import remote_grant
        import remote_link
        self.RG, self.RL = remote_grant, remote_link
        self.RG.set_remote(True)
        self.fake = FAKE.Fake(FP, ACCT, TOKEN, poll_s=0.3)
        self.srv = self.fake.serve()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.scr = self.tmp / "screen.txt"
        (self.bin / "tmux").write_text('#!/bin/bash\necho "$@" >> "$FAKE_TMUX_LOG"\ncase " $* " in *" capture-pane "*) cat "%s" 2>/dev/null;; esac\nexit 0\n' % self.scr)
        self.screen = PROMPT

    @property
    def screen(self):
        return self._screen

    @screen.setter
    def screen(self, v):
        self._screen = v
        self.scr.write_text(v, encoding="utf-8")

    def tearDown(self):
        self.RG.set_remote(False)
        if getattr(self, "th", None):
            self.th.join(timeout=10)
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def act(self, keys):
        SA, t, me = self.SA, self.tid(), self
        common = dict(touchid=t, touchid_available=lambda: True, platform="darwin")
        return types.SimpleNamespace(
            do_unlock=lambda c, m, env=None, grant=None: SA.do_unlock(c, m, env=env, grant=grant, keys=keys, machine=(FP, {ACCT}), **common),
            do_input=lambda c, x, env=None, window_only=False: SA.do_input(c, x, env=env, screen=me.screen, window_only=window_only, **common),
            do_stop=lambda c, hard=False, env=None, window_only=False: SA.do_stop(c, hard=hard, env=env, window_only=window_only, **common),
            do_answer=lambda c, ch, ask_id=None, free_text=None, env=None, grant=None: SA.do_answer(
                c, ch, ask_id=ask_id, free_text=free_text, env=env, screen=me.screen, grant=grant, grant_keys=keys, machine=(FP, {ACCT}), **common),
            do_unlocked=SA.do_unlocked, do_lock=SA.do_lock, remote_left=SA.remote_left)

    def start_link(self, keys, token=TOKEN):
        self.out = {}

        def run():
            self.out["res"] = self.RL.link(token, FP, base=self.base, act=self.act(keys), sleep=lambda s: time.sleep(0.05))
        self.th = threading.Thread(target=run, daemon=True)
        self.th.start()

    def test_e1_round(self):
        self.start_link(FAKE.test_keys())
        ph = Phone(self.base)
        st, ms = ph.req("GET", "/machines")
        self.assertEqual(ms["machines"][0]["fp"], FP)
        ph.login()
        seats = ph.cmd("seats")
        self.assertEqual([x["seat"] for x in seats["seats"]], [CODE])
        self.assertEqual(ph.cmd("input", text="還沒解鎖")["why"], "locked")
        st, g = ph.req("POST", "/unlock-grant", {"machine": FP, "seat": CODE, "minutes": 15, "nonce": "n" * 24})
        self.assertEqual(st, 200)
        r = ph.cmd("unlock", grant=g)
        self.assertEqual((r["ok"], r["via"], r["minutes"]), (True, "remote-grant", 15), r)
        r = ph.cmd("input", text="跑一下測試")
        self.assertEqual((r["ok"], r["via"]), (True, "unlock-window"), r)
        self.assertIn("跑一下測試", "\n".join(self.keys()), "字真的送進席位")
        t = ph.cmd("transcript", offset=0)
        self.assertTrue(t["ok"], t)
        self.assertNotIn(str(self.home), json.dumps(t, ensure_ascii=False), "不帶本機路徑")
        self.screen = PERMISSION
        p = ph.cmd("pending")
        aid = p["ask"]["id"]
        self.assertIn(":", aid, "ask id 有冒號(@9f808 值域已放寬)")
        q = self.RG.question_hash(p["ask"]["prompt"], p["ask"]["options"])          # 手機對「顯示給本人看的題目 + 選項」算
        st, ag = ph.req("POST", "/answer-grant", {"machine": FP, "seat": CODE, "ask_id": aid, "choice": "1", "q_sha256": q, "nonce": "a" * 24})
        r = ph.cmd("answer", grant=ag, choice="1", ask_id=aid)
        self.assertEqual((r["ok"], r["via"]), (True, "remote-grant"), r)
        self.assertEqual(ph.cmd("answer", grant=ag, choice="1", ask_id=aid)["why"], "grant_replayed", "同一張不能答兩次")
        self.screen = PROMPT
        self.assertTrue(ph.cmd("stop")["ok"])
        self.assertTrue(ph.cmd("lock")["ok"])
        self.assertEqual(ph.cmd("input", text="上鎖後")["why"], "locked")
        self.assertNotIn("上鎖後", "\n".join(self.keys()))
        self.assertEqual(self.asked, [], "Mac 上一次 Touch ID 都沒彈")
        self.RG.set_remote(False)
        self.th.join(timeout=10)
        self.assertEqual(self.out["res"][0]["why"], "remote-off", "關掉開關 ⇒ link 自己停")

    def test_e2_auth(self):
        self.start_link(FAKE.test_keys(), token="wrong")
        self.th.join(timeout=10)
        self.assertEqual(self.out["res"][0]["why"], "unauthorized")
        ph = Phone(self.base)
        ph.login()
        st, _d = ph.req("POST", "/unlock-grant", {"machine": "0" * 64, "seat": CODE, "minutes": 5, "nonce": "n" * 24})
        self.assertEqual(st, 403)

    def test_e3_prod_keys_reject(self):
        self.start_link(None)
        ph = Phone(self.base)
        ph.login()
        st, g = ph.req("POST", "/unlock-grant", {"machine": FP, "seat": CODE, "minutes": 10, "nonce": "n" * 24})
        r = ph.cmd("unlock", grant=g)
        self.assertEqual(r["why"], "grant_unknown-key", "正式清單空 ⇒ 假伺服器簽的單子一律拒")
        self.assertEqual(ph.cmd("input", text="嗨")["why"], "locked")
        self.assertEqual(self.keys(), [])

    def test_e4_fp_vector(self):
        self.assertEqual(self.RG.machine_fp("6f1c1d1e-0000-4000-8000-000000000001"), "89e13cddf7e05b0867bcfa94ff6d636444428fcd4c4509cdc6fb368bc883aa2f")
        self.assertEqual(self.RG.machine_fp("00000000-0000-4000-8000-000000000000"), "53fb497e86648f5ac1020d2dbe2d44d7ca32cc13d67a3d1b56d3d25e18e40f2e")


if __name__ == "__main__":
    unittest.main()
