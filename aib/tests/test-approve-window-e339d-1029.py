#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""TID-2c 時間窗接上 ask()(10-29,草稿 DRAFT-TID2-window-grants-e339d-1029;@aeb40 13:43 Q2、#419 R2)。

假 aib-sehelper 跟 test-approve-grants-e339d-1029 同一支(HMAC 模擬);LaunchAgent / launchctl / osascript 一律假的。
W1 有效單 + lockwatch 心跳新 + 簽在最後一次鎖屏之後 ⇒ find_valid 找得到
W2 lockwatch 沒檔 ⇒ no_lockwatch;心跳 > 90 秒 ⇒ lockwatch_stale;現在鎖著 / 量不到 ⇒ screen_locked
W3 簽單之後鎖過螢幕 ⇒ locked_since(D3)
W4 高風險 / 未登記類別 ⇒ find_valid 一律 no_window_category;durations_for 空
W5 ask():有單 ⇒ 不彈窗、why window、記一列 grant-uses
W6 ask():沒單 ⇒ 選單;選 1h ⇒ SE 簽單(同一次指紋)、不叫 LAContext;只這次 ⇒ LAContext;取消 ⇒ 都不叫
W7 ask():簽單句子放不下(reason_refused)⇒ 退回 LAContext;其他簽單失敗 ⇒ 不放行
W8 ask():高風險類 ⇒ 找單、選單都不叫(介面一處)
W9 端到端(GateWindow + 假 helper + 假選單):選 1h ⇒ 視窗字以這次的句子開頭、單驗得過;下一次同類 ⇒ 自動同意 + 記帳
W10 舊 helper(se-pub 沒回 owner_acl)⇒ old_helper,不開窗
W11 durations_for:outbound ≤ 3h、seat_input 到 24h
W14 鎖屏偵測沒在跑 ⇒ 不出時長選單(只這次)
W12 --lockwatch on / off 只動給的 home + 假 launchctl;plist 是 KeepAlive 的 lockwatch;doctor_line 有單沒 lockwatch ⇒ note
刀(複本落刀,錨要恰好一次):KL1 拿掉心跳過期 ⇒ W2 紅;KL2 拿掉鎖屏後作廢 ⇒ W3 紅;KL3 拿掉「現在鎖著」⇒ W2 紅;
  KL4 拿掉 owner_acl 檢查 ⇒ W10 紅;KL5 拿掉 find_valid 的高風險排除 ⇒ W4 紅;KL6 拿掉沒 lockwatch 不出選單 ⇒ W14 紅;
  KG1 ask 拿掉高風險不走窗 ⇒ W8 紅;KG2 拿掉記帳 ⇒ W5 紅;KG3 拿掉取消 ⇒ W6 紅
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit"
sys.path.insert(0, str(SRC))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import approve_gate as AG  # noqa: E402
import approve_grants as GR  # noqa: E402

_spec = importlib.util.spec_from_file_location("aib_grants_cells_2b", str(ROOT / "tests" / "test-approve-grants-e339d-1029.py"))
T2B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T2B)
T0 = T2B.T0


class Env(T2B.Env):
    def __init__(self):
        super().__init__()
        self.locked = [False]

    def ctx(self, M=GR):
        return M.Ctx(env=self.env, helper=self.helper, platform="darwin", now=lambda: self.t[0],
                     boot=lambda: self.boot[0], machine=lambda: self.machine[0], screen_locked=lambda: self.locked[0])

    def beat(self, M=GR, beat=None, last_lock=None):
        M._write_json(M.lockwatch_path(self.env), {"v": 1, "beat_utc": M._utc(self.t[0] if beat is None else beat),
                                                   "last_lock_utc": M._utc(T0 - 600 if last_lock is None else last_lock)})


def _r(G, cat="seat_input", site="seat_act.input", text="核准:在 @abc12 輸入「繼續」"):
    return G._mint(text, site, cat)          # 只給測試:直接記一句指定類別的話


class FakeWin:
    def __init__(self, doc=None, choice="once", sign=None):
        self.doc, self.choice, self.sign_res = doc, choice, sign or ({"nonce": "n" * 32}, None)
        self.calls = []

    def find(self, cat):
        self.calls.append(("find", cat))
        return (self.doc, None) if self.doc else (None, "no_grant")

    def use(self, doc, site, cat):
        self.calls.append(("use", site, cat))

    def choose(self, r):
        self.calls.append(("choose", r.category))
        return self.choice

    def sign(self, r, dur):
        self.calls.append(("sign", r.category, dur))
        return self.sign_res


def _ask(G, r, win, td):
    seen = []
    res = G.ask(r, td, touchid=lambda x: seen.append(str(x)) or {"ok": True, "why": "ok"}, window=win)
    return res, seen


# ---- 各格(收 M = approve_grants、G = approve_gate;刀測拿複本跑同一格) ----

def cell_w1(t, M):
    e = Env()
    try:
        doc, why = M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertIsNone(why)
        e.beat(M)
        got, why = M.find_valid("seat_input", e.ctx(M))
        t.assertIsNone(why)
        t.assertEqual(got["nonce"], doc["nonce"])
    finally:
        e.close()


def cell_w2(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "no_lockwatch"))
        e.beat(M, beat=T0 - 91)
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "lockwatch_stale"))
        e.beat(M)
        e.locked[0] = True
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "screen_locked"))
        e.locked[0] = False
        t.assertIsNotNone(M.find_valid("seat_input", e.ctx(M))[0])
        t.assertTrue(M.screen_locked_now(platform="linux"))            # 量不到 = 當鎖著
    finally:
        e.close()


def cell_w3(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "3h", e.ctx(M))
        e.t[0] = T0 + 600
        e.beat(M, last_lock=T0 + 300)                                  # 簽完之後鎖過一次
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "locked_since"))
        doc, _ = M.sign(["seat_input"], "3h", e.ctx(M))                # 解鎖後重簽 ⇒ 有效
        t.assertEqual(M.find_valid("seat_input", e.ctx(M))[0]["nonce"], doc["nonce"])
    finally:
        e.close()


def cell_w4(t, M):
    e = Env()
    try:
        e.beat(M)
        for cat in ("release_sign", "trusted_device", "window_grant", "unregistered", "nope"):
            t.assertEqual(M.find_valid(cat, e.ctx(M)), (None, "no_window_category"), cat)
            t.assertEqual(M.durations_for(cat), [], cat)
    finally:
        e.close()


def cell_w5(t, G):
    td = tempfile.mkdtemp(prefix="aib-window-lock-")
    try:
        win = FakeWin(doc={"nonce": "a" * 32})
        res, seen = _ask(G, _r(G), win, td)
        t.assertEqual((res["ok"], res["why"], res.get("grant")), (True, "window", "a" * 32))
        t.assertEqual(seen, [])
        t.assertIn(("use", "seat_act.input", "seat_input"), win.calls)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_w6(t, G):
    td = tempfile.mkdtemp(prefix="aib-window-lock-")
    try:
        win = FakeWin(choice="1h")
        res, seen = _ask(G, _r(G), win, td)
        t.assertEqual((res["ok"], res["why"]), (True, "ok"))
        t.assertEqual(seen, [])
        t.assertIn(("sign", "seat_input", "1h"), win.calls)
        win = FakeWin(choice="once")
        res, seen = _ask(G, _r(G), win, td)
        t.assertTrue(res["ok"])
        t.assertEqual(len(seen), 1)
        t.assertNotIn("sign", [c[0] for c in win.calls])
        win = FakeWin(choice="cancel")
        res, seen = _ask(G, _r(G), win, td)
        t.assertEqual((res["ok"], res["why"]), (False, "cancel"))
        t.assertEqual(seen, [])
        t.assertNotIn("sign", [c[0] for c in win.calls])
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_w7(t, G):
    td = tempfile.mkdtemp(prefix="aib-window-lock-")
    try:
        res, seen = _ask(G, _r(G), FakeWin(choice="3h", sign=(None, "reason_refused:too_long")), td)
        t.assertTrue(res["ok"])
        t.assertEqual(len(seen), 1)                                    # 退回只這次
        res, seen = _ask(G, _r(G), FakeWin(choice="3h", sign=(None, "cancel")), td)
        t.assertEqual((res["ok"], res["why"]), (False, "cancel"))
        t.assertEqual(seen, [])
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_w8(t, G):
    td = tempfile.mkdtemp(prefix="aib-window-lock-")
    try:
        for cat, site in (("release_sign", "release.sign_approve"), ("trusted_device", "chat_cast.issue"),
                          ("unregistered", "x.y")):
            win = FakeWin(doc={"nonce": "b" * 32}, choice="24h")
            res, seen = _ask(G, _r(G, cat, site), win, td)
            t.assertEqual(win.calls, [], cat)
            t.assertEqual(len(seen), 1, cat)
            t.assertNotEqual(res["why"], "window", cat)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_w14(t, M):
    e = Env()
    try:
        ran = []
        win = M.GateWindow(e.ctx(M), run=lambda *a, **k: ran.append(a) or type("P", (), {"returncode": 0,
                                                                                         "stdout": '"1 小時"'})())
        t.assertEqual(win.choose(_r(AG)), "once")
        t.assertEqual(ran, [])
    finally:
        e.close()


def cell_w10(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "1h", e.ctx(M))
        e.beat(M)
        os.environ["FAKE_OLD_HELPER"] = "1"             # helper 子行程吃的是行程環境
        try:
            t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "old_helper"))
            t.assertEqual(M.current_kid(e.ctx(M)), (None, "old_helper"))
        finally:
            os.environ.pop("FAKE_OLD_HELPER", None)
    finally:
        e.close()


class Cells(unittest.TestCase):
    def test_w1(self): cell_w1(self, GR)
    def test_w2(self): cell_w2(self, GR)
    def test_w3(self): cell_w3(self, GR)
    def test_w4(self): cell_w4(self, GR)
    def test_w5(self): cell_w5(self, AG)
    def test_w6(self): cell_w6(self, AG)
    def test_w7(self): cell_w7(self, AG)
    def test_w8(self): cell_w8(self, AG)
    def test_w10(self): cell_w10(self, GR)

    def test_w9_end_to_end(self):
        e = Env()
        td = tempfile.mkdtemp(prefix="aib-window-lock-")
        try:
            e.beat()
            picked = []

            def fake_run(cmd, **kw):                                    # 假 osascript 選單:選「1 小時」
                picked.append(kw.get("input", ""))
                return type("P", (), {"returncode": 0, "stdout": json.dumps("1 小時") + "\n"})()
            win = GR.GateWindow(e.ctx(), run=fake_run)
            r = _r(AG)
            res = AG.ask(r, td, touchid=lambda x: self.fail("不該叫 LAContext"), window=win)
            self.assertEqual((res["ok"], res["why"]), (True, "ok"))
            self.assertIn(json.dumps("只這次")[1:-1], picked[0])           # JXA 裡的字一律 json 跳脫
            self.assertIn(json.dumps("本機所有 AI")[1:-1], picked[0])
            reason = e.reasons()[-1]
            self.assertTrue(reason.startswith(str(r)), reason)          # 同一次指紋:句首就是這次要核准的事
            self.assertIn("1 小時", reason)
            res2 = AG.ask(_r(AG, text="核准:在 @abc12 輸入「下一步」"), td,
                          touchid=lambda x: self.fail("窗內不該再按"), window=win)
            self.assertEqual((res2["ok"], res2["why"], res2["grant"]), (True, "window", res["grant"]))
            with open(os.path.join(GR._dir(e.env), "grant-uses.jsonl"), encoding="utf-8") as f:
                uses = [json.loads(x) for x in f]
            self.assertEqual([(u["site"], u["cat"]) for u in uses], [("seat_act.input", "seat_input")])
            res3 = AG.ask(_r(AG, "outbound", "chat_human.send", "寄給 @x"), td,
                          touchid=lambda x: {"ok": False, "why": "cancel"},
                          window=GR.GateWindow(e.ctx(), run=lambda *a, **k: type("P", (), {"returncode": 0,
                                                                                        "stdout": "null\n"})()))
            self.assertEqual((res3["ok"], res3["why"]), (False, "cancel"))   # 別類沒有窗;選單按取消
        finally:
            shutil.rmtree(td, ignore_errors=True)
            e.close()

    def test_w14_cell(self): cell_w14(self, GR)

    def test_w14_no_menu_without_lockwatch(self):
        e = Env()
        try:
            ran = []
            win = GR.GateWindow(e.ctx(), run=lambda *a, **k: ran.append(a))
            self.assertEqual(win.choose(_r(AG)), "once")               # 沒 lockwatch ⇒ 不出選單
            e.beat(beat=T0 - 200)
            self.assertEqual(win.choose(_r(AG)), "once")               # 心跳過期也一樣
            self.assertEqual(ran, [])
        finally:
            e.close()

    def test_w11_durations(self):
        self.assertEqual(GR.durations_for("outbound"), ["15m", "30m", "1h", "3h"])
        self.assertEqual(GR.durations_for("command"), ["15m", "30m", "1h", "3h"])
        self.assertEqual(GR.durations_for("seat_input"), ["15m", "30m", "1h", "3h", "24h"])

    def test_w12_lockwatch_install_and_doctor(self):
        e = Env()
        home = tempfile.mkdtemp(prefix="aib-window-home-")
        calls = []
        try:
            lc = lambda *a: calls.append(a) or (0, "")                  # noqa: E731
            out = io.StringIO()
            self.assertEqual(GR.main(["--lockwatch", "on"], out=out, ctx=e.ctx(), launchctl=lc, home=home), 0)
            path = GR.lockwatch_plist_path(home)
            import plistlib
            with open(path, "rb") as f:
                pl = plistlib.load(f)
            self.assertEqual(pl["Label"], GR.LOCKWATCH_LABEL)
            self.assertTrue(pl["KeepAlive"])
            self.assertEqual(pl["ProgramArguments"][1:4], ["lockwatch", "--name", GR.KEY_NAME])
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            self.assertEqual([c[0] for c in calls], ["bootout", "bootstrap"])
            self.assertEqual(GR.doctor_line(e.ctx()), ("ok", "no_grants"))
            GR.sign(["seat_input"], "1h", e.ctx())
            self.assertEqual(GR.doctor_line(e.ctx()), ("note", "no_lockwatch"))
            e.beat()
            self.assertEqual(GR.doctor_line(e.ctx()), ("ok", "lockwatch_ok"))
            out = io.StringIO()
            GR.main(["--lockwatch", "off"], out=out, ctx=e.ctx(), launchctl=lc, home=home)
            self.assertTrue(json.loads(out.getvalue())["removed"])
            self.assertFalse(os.path.exists(path))
        finally:
            shutil.rmtree(home, ignore_errors=True)
            e.close()

    def test_w13_no_auto_install(self):
        """簽單 / 彈窗路徑都不會順手裝 LaunchAgent(只有 --lockwatch on)。"""
        src = (SRC / "approve_grants.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("install_lockwatch(ctx, lc, hm)"), 1)
        self.assertEqual(src.count("install_lockwatch("), 2)             # 定義 + CLI 那一處
        for name in ("approve_gate.py", "aib_approve.py"):
            self.assertNotIn("install_lockwatch", (SRC / name).read_text(encoding="utf-8"))


# ---- 刀 ----
KNIVES_GR = [
    ("KL1", '    if not 0 <= now - beat <= BEAT_MAX_S:\n', '    if False:\n', cell_w2),
    ("KL2", '        if issued is None or issued <= last_lock:\n', '        if issued is None:\n', cell_w3),
    ("KL3", '    if ctx.screen_locked():\n        return None, "screen_locked"\n', '', cell_w2),
    ("KL4", '    if isinstance(res, dict) and res.get("ok") is True and res.get("owner_acl") != "probed":\n',
     '    if False:\n', cell_w10),
    ("KL6", '        if lock_state(self.ctx)[1] is not None:\n            return "once"\n', '', cell_w14),
    ("KL5", '    if not spec or spec["high_risk"] or spec["max_window"] <= 0:\n        return None, "no_window_category"\n'
     '    last_lock, why', '    last_lock, why', cell_w4),
]
KNIVES_AG = [
    ("KG1", '    win = window if window_open(r) else None\n', '    win = window\n', cell_w8),
    ("KG2", '                    _safe(win.use, doc, r.site, r.category)\n', '', cell_w5),
    ("KG3", '            if choice == "cancel":\n                res = {"ok": False, "why": "cancel"}\n            elif',
     '            if False:\n                pass\n            elif', cell_w6),
]


def _load_copy(name, src_file, anchor, repl):
    src = src_file.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, "%s 錨不是恰好一次" % name
    td = tempfile.mkdtemp(prefix="aib-window-knife-")
    p = os.path.join(td, src_file.name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(src.replace(anchor, repl))
    spec = importlib.util.spec_from_file_location("aib_window_knife_" + name, p)
    M = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = M
    spec.loader.exec_module(M)
    return M, td


class Knives(unittest.TestCase):
    def _run(self, knives, src_file, patch):
        for name, anchor, repl, cell in knives:
            with self.subTest(knife=name):
                M, td = _load_copy(name, src_file, anchor, repl)
                try:
                    patch(M)
                    with self.assertRaises(AssertionError, msg="%s 落刀後格子仍綠" % name):
                        cell(self, M)
                finally:
                    sys.modules.pop("aib_window_knife_" + name, None)
                    shutil.rmtree(td, ignore_errors=True)

    def test_knives_grants(self):
        def patch(M):
            M.window_reason = GR.window_reason          # 視窗字要從登記的那支組(複本不是登記檔)
        self._run(KNIVES_GR, SRC / "approve_grants.py", patch)

    def test_knives_gate(self):
        self._run(KNIVES_AG, SRC / "approve_gate.py", lambda M: None)


if __name__ == "__main__":
    unittest.main()
