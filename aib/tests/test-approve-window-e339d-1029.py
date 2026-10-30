#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""TID-2c 時間窗接上 ask()(10-29,草稿 DRAFT-TID2-window-grants-e339d-1029;@aeb40 13:43 Q2、#419 R2)。

假 aib-sehelper 跟 test-approve-grants-e339d-1029 同一支(HMAC 模擬);LaunchAgent / launchctl / osascript 一律假的。
(系統錨定驗單第 2 張起:鎖屏看系統日誌 —— 這裡用 Ctx(syslog=) 注入假查詢;真的 log show 解析在 test-approve-syslog-e339d-1030)
W1 有效單 + 系統日誌查得到 + 簽單之後沒有鎖屏事件 ⇒ find_valid 找得到
W2 系統日誌查不到 ⇒ log_failed;超過 3 秒 ⇒ log_timeout;現在鎖著 / 量不到 ⇒ screen_locked
W3 簽單之後鎖過螢幕 ⇒ locked_since(D3);查詢起點 = 最新那張的簽發時間
W4 高風險 / 未登記類別 ⇒ find_valid 一律 no_window_category;durations_for 空
W5 ask():有單 ⇒ 不彈窗、why window、記一列 grant-uses
W6 ask():沒單 ⇒ 選單;選 1h ⇒ SE 簽單(同一次指紋)、不叫 LAContext;只這次 ⇒ LAContext;取消 ⇒ 都不叫
W7 ask():簽單句子放不下(reason_refused)⇒ 退回 LAContext;其他簽單失敗 ⇒ 不放行
W8 ask():高風險類 ⇒ 找單、選單都不叫(介面一處)
W9 端到端(GateWindow + 假 helper + 假選單):選 1h ⇒ 視窗字以這次的句子開頭、單驗得過;下一次同類 ⇒ 自動同意 + 記帳
W10 舊 helper(se-pub 沒回 owner_acl)⇒ old_helper,不開窗
W11 durations_for:outbound / command / seat_input 都 ≤ 3h(#441:上限 = 日誌涵蓋範圍 7 小時,24h 拿掉)
W14 系統日誌查不到 / helper 不在 ⇒ 不出時長選單(只這次)
W15 系統日誌查不到 + 簽章正確的有效窗單 ⇒ ask 仍一次一按、不出選單
W16 正式路徑(不注入 touchid / window):不是 Mac / 固定路徑 helper 沒裝 ⇒ _default_window 回 None,
    授權單模組給得出有效單也照舊一次一按(系統錨定驗單第 3 張起:兩項都成立才開窗)
W17 審查探針 P4 + P1:EZ2_AIB_STATE_ROOT + 種假 sehelper(stamp = 真原始碼雜湊)+ 自寫 lockwatch.json ⇒ ask 仍彈窗
W18 #433 R1 M2:這版 macOS 不在實機驗過的名單 / 讀不到 build ⇒ find_valid 回 os_unverified(不查系統日誌);
    doctor_line ⇒ note os_unverified;os_build 只跑 /usr/bin/sw_vers -buildVersion、env 只給 PATH,不是 Mac 不跑
W19 第 3 張接窗:Mac + 固定路徑 helper 驗得過 ⇒ 正式路徑 ask(seat_input) 找到本人簽的單 ⇒ 不彈窗、why window、記帳;
    高風險類照舊一次一按;GateWindow 的狀態根 = pwd 查到的家目錄(pinned_env 拿掉 EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME、HOME 換掉)
W20 刀:換狀態根 —— EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME / HOME 都指到 AI 的目錄、裡面有一張驗得過的單 ⇒ 正式路徑不採用,一次一按
W21 刀:覆寫 helper —— 固定路徑那支不是 root 擁有,AI 在 <家>/.ez2-aib/bin 種假 helper + 用它簽的單 ⇒ 不開窗,一次一按
W22 刀:自寫鎖屏紀錄 —— 本人簽的單、系統日誌有簽單後的鎖屏,AI 自寫 lockwatch.json「沒鎖過」⇒ 一次一按
W16o #441 R1 M1:真的 anchored_helper.check(只換 lstat / stamp / codesign):helper 不是 root 擁有 ⇒ 正式 ask() 一次一按
W20b #441 R1 M2:只改行程 HOME(單在 <假 HOME>/.ez2-aib,pwd 家目錄不同)⇒ 一次一按
W23 H2 刀:--revoke(撤銷 = 鎖屏)之後刪 revoked.jsonl 重播 ⇒ locked_since;鎖屏失敗時刪紀錄就回來 = 已知限制(斷言照實)
W24 H2 刀:--rotate-key(換鑰 + 鎖屏)之後還原退休鑰 + grant-kid.json 重播 ⇒ locked_since
W25 系統日誌涵蓋不到簽發 → 現在(log_uncovered)⇒ 有效單也要彈指紋、不出選單
W26 #441 R2 H1 容差邊界重播:helper 在 T 簽(stamp issued = T),T+0 / +1 / +2 / +5 鎖屏,T+6 使用 ⇒ locked_since;
    沒鎖屏 ⇒ 有效;改 stamp 把起算挪到鎖屏之後 ⇒ 簽章不符、不採用;叫 helper 時自帶未來 issued_utc ⇒ 不簽
W12 --lockwatch on 要本人按一次(類別 window_grant;取消 ⇒ 不寫 plist、不叫 launchctl);--lockwatch on / off 只動給的 home + 假 launchctl;plist 是 KeepAlive 的 lockwatch;doctor_line 有單但系統日誌查不到 ⇒ note
  (第 2 張起 lockwatch.json 不再參與判斷;W2 另驗自寫一份「沒鎖過」的 lockwatch.json 也不會讓單生效)
刀(複本落刀,錨要恰好一次):KL1 lock_state 不理查詢失敗 ⇒ W2 紅;KL2 拿掉鎖屏後作廢 ⇒ W3 紅;KL3 拿掉「現在鎖著」⇒ W2 紅;
  KL10 lock_state 拿掉 build 名單 ⇒ W18 紅;KL11 os_build 不給乾淨 env ⇒ W18 紅;
  KL4 拿掉 owner_acl 檢查 ⇒ W10 紅;KL5 拿掉 find_valid 的高風險排除 ⇒ W4 紅;KL6 拿掉查不到不出選單 ⇒ W14 紅;
  KL7 find_valid 拿掉 lock_state ⇒ W15 紅;KL9 lock_state 改回讀 lockwatch.json ⇒ W2 紅;KL8 --lockwatch on 拿掉 Touch ID ⇒ W12 紅;
  KG1 ask 拿掉高風險不走窗 ⇒ W8 紅;KG2 拿掉記帳 ⇒ W5 紅;KG3 拿掉取消 ⇒ W6 紅
  KH1 / KH2 _default_window 拿掉固定路徑 helper 檢查 ⇒ W16 / W17 紅;KH3 拿掉平台檢查 ⇒ W16 紅
  KW1 pinned_env 照收呼叫端 env(狀態根跟著 EZ2_AIB_STATE_ROOT)⇒ W20 紅;KW2 helper_exe 退回 <狀態根>/bin ⇒ W21 紅;
  KW3 正式路徑 lock_state 改讀 lockwatch.json ⇒ W22 紅;KW4 pinned_env 改看行程 HOME ⇒ W20b 紅;
  KW8 find_valid 查鎖屏時起算容 5 秒(= 舊的 issued 容差)⇒ W26 紅;
  KW5 --revoke 不鎖屏 ⇒ W23 紅;KW6 --rotate-key 不鎖屏 ⇒ W24 紅;KW7 涵蓋不足當沒鎖過 ⇒ W25 紅;
  KH4 anchored_helper 拿掉擁有者檢查 ⇒ W16o 紅
"""
import hashlib
import importlib.util
import io
import json
import os
import contextlib
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

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


VERIFIED = object()                  # Env.build 的預設:用受測模組名單裡的第一個 build


class Env(T2B.Env):
    def __init__(self):
        super().__init__()
        self.locked = [False]
        self.locks, self.log_why, self.log_calls = None, None, []      # 預設:系統日誌查不到
        self.build = [VERIFIED]                                        # 預設:名單裡的 macOS build

    def _lock(self):
        """假鎖屏:記一筆;假系統日誌開著時也記一筆 loginwindow 鎖屏事件(真鎖屏也會)。"""
        self.lock_calls = getattr(self, "lock_calls", []) + [int(self.t[0])]
        if self.locks is not None:
            self.locks.append(int(self.t[0]))
        return "locked"

    def ctx(self, M=GR):
        self.clock(M)
        return M.Ctx(env=self.env, helper=self.helper, platform="darwin", now=lambda: self.t[0], lock_screen=self._lock,
                     boot=lambda: self.boot[0], machine=lambda: self.machine[0], screen_locked=lambda: self.locked[0],
                     syslog=self._syslog,
                     os_build=lambda: sorted(M.VERIFIED_BUILDS)[0] if self.build[0] is VERIFIED else self.build[0])

    def _syslog(self, since, now):
        self.log_calls.append((since, now))
        if self.log_why or self.locks is None:
            return None, self.log_why or "log_failed"
        return [x for x in self.locks if since <= x <= now], None

    def log(self, locks=(), why=None):
        """假系統日誌:locks = loginwindow 鎖屏 / 解鎖事件的秒數;why = 查詢失敗原因(log_timeout …)。"""
        self.locks, self.log_why = list(locks), why


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
        e.log()
        got, why = M.find_valid("seat_input", e.ctx(M))
        t.assertIsNone(why)
        t.assertEqual(got["nonce"], doc["nonce"])
    finally:
        e.close()


def cell_w2(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "1h", e.ctx(M))
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "log_failed"))
        now = int(e.t[0])                       # AI 不經 --lockwatch on 自寫一份「心跳新、沒鎖過」⇒ 不採信(第 2 張)
        M._write_json(M.lockwatch_path(e.env), {"v": 1, "beat_utc": M._utc(now), "last_lock_utc": M._utc(now - 3600)})
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "log_failed"))
        e.log(why="log_timeout")
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "log_timeout"))
        e.log()
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
        e.log(locks=[T0 - 600, T0 + 300])                              # 簽之前鎖過(不算)、簽完之後又鎖過一次
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "locked_since"))
        t.assertEqual(e.log_calls[-1], (T0, T0 + 600))                 # 從簽發時間查到現在
        e.t[0] = T0 + 700
        doc, _ = M.sign(["seat_input"], "3h", e.ctx(M))                # 解鎖後重簽 ⇒ 有效
        e.t[0] = T0 + 800
        t.assertEqual(M.find_valid("seat_input", e.ctx(M))[0]["nonce"], doc["nonce"])
        t.assertEqual(e.log_calls[-1], (T0 + 700, T0 + 800))           # 只查最新那張之後
    finally:
        e.close()


def cell_w4(t, M):
    e = Env()
    try:
        e.log()
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


def cell_w12_needs_touchid(t, M, e, lc, home, calls):
    """--lockwatch on 要本人按一次;取消 ⇒ 不寫 plist、不叫 launchctl。"""
    out = io.StringIO()
    rc = M.main(["--lockwatch", "on"], out=out, ctx=e.ctx(M), launchctl=lc, home=home,
                touchid=lambda r: {"ok": False, "why": "cancel"})
    t.assertEqual((rc, json.loads(out.getvalue()).get("why")), (2, "cancel"))
    t.assertFalse(os.path.exists(M.lockwatch_plist_path(home)))
    t.assertEqual(calls, [])


def cell_w12k(t, M):
    e = Env()
    home = tempfile.mkdtemp(prefix="aib-window-home-")
    calls = []
    try:
        cell_w12_needs_touchid(t, M, e, lambda *a: calls.append(a) or (0, ""), home, calls)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        e.close()


def cell_w15(t, M):
    """系統日誌查不到時,就算有一張簽章正確的有效窗單,ask 仍一次一按(@aeb40 16:36 加刀;第 2 張改看系統日誌)。"""
    e = Env()
    td = tempfile.mkdtemp(prefix="aib-window-lock-")
    try:
        doc, _ = M.sign(["seat_input"], "3h", e.ctx(M))
        t.assertTrue(M.verify(T2B._grant(M, e, doc), "seat_input", e.ctx(M))[0])     # 單本身是好的
        seen = []
        win = M.GateWindow(e.ctx(M), run=lambda *a, **k: t.fail("系統日誌查不到不該出選單"))
        res = AG.ask(_r(AG), td, touchid=lambda x: seen.append(x) or {"ok": True, "why": "ok"}, window=win)
        t.assertEqual(len(seen), 1)
        t.assertNotEqual(res["why"], "window")
    finally:
        shutil.rmtree(td, ignore_errors=True)
        e.close()


def cell_w16(t, G):
    """正式路徑(不注入 touchid / window):不是 Mac / 固定路徑 helper 沒裝 ⇒ 就算授權單模組給得出一張有效單,ask 也照舊一次一按。"""
    td = tempfile.mkdtemp(prefix="aib-window-h1-")
    try:
        for plat, check in (("linux", lambda **k: (GR.anchored_helper.EXE, None)),
                            ("darwin", lambda **k: (None, "not_installed")),
                            ("darwin", lambda **k: (None, "not_root_owned"))):
            fake = FakeWin(doc={"nonce": "n" * 32}, choice="1h")
            seen = []
            with mock.patch.object(GR, "GateWindow", lambda *a, **k: fake), \
                    mock.patch.object(G.sys, "platform", plat), \
                    mock.patch.object(GR.anchored_helper, "check", check), \
                    mock.patch.object(G.TA, "ask", lambda r: seen.append(r) or {"ok": True, "why": "ok"}):
                t.assertIsNone(G._default_window(), plat)
                res = G.ask(_r(G), td)
            t.assertEqual((len(seen), fake.calls), (1, []), plat)  # 彈了一次指紋;沒找單、沒出選單
            t.assertNotEqual(res.get("why"), "window", plat)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _fake_lstat(owner_uid):
    """假 lstat:固定路徑 helper 的擁有者 = owner_uid,其他(stamp / 每層目錄)都是 root、不可寫。"""
    import stat as st

    def lstat(p):
        if p == GR.anchored_helper.EXE:
            return types.SimpleNamespace(st_mode=st.S_IFREG | 0o755, st_uid=owner_uid)
        if p == GR.anchored_helper.STAMP:
            return types.SimpleNamespace(st_mode=st.S_IFREG | 0o644, st_uid=0)
        return types.SimpleNamespace(st_mode=st.S_IFDIR | 0o755, st_uid=0)
    return lstat


def cell_w16_owner(t, AH, G=AG):
    """#441 R1 M1:用真的 anchored_helper.check(只換 lstat / stamp / codesign);helper 不是 root 擁有 ⇒ 正式 ask() 一次一按。
    AH = 受測的 anchored_helper(刀測拿複本)。"""
    import functools
    runtime = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "CodeDirectory flags=0x10002(adhoc,runtime)\n")  # noqa: E731
    td = tempfile.mkdtemp(prefix="aib-window-owner-")
    try:
        for uid, want in ((0, (AH.EXE, None)), (501, (None, "not_root_owned"))):
            check = functools.partial(AH.check, lstat=_fake_lstat(uid), read=lambda p: "d" * 64,
                                      src_digest=lambda: "d" * 64, run=runtime)
            t.assertEqual(check(platform="darwin"), want, uid)
        fake = FakeWin(doc={"nonce": "n" * 32}, choice="1h")
        seen = []
        with mock.patch.object(GR, "GateWindow", lambda *a, **k: fake), \
                mock.patch.object(G.sys, "platform", "darwin"), \
                mock.patch.object(GR.anchored_helper, "check", check), \
                mock.patch.object(G.TA, "ask", lambda r: seen.append(r) or {"ok": True, "why": "ok"}):
            t.assertIsNone(G._default_window())
            res = G.ask(_r(G), td)
        t.assertEqual((len(seen), fake.calls), (1, []))
        t.assertNotEqual(res.get("why"), "window")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_w17(t, G):
    """審查探針 P4 + P1:EZ2_AIB_STATE_ROOT 指到自己的目錄 + 種假 bin/aib-sehelper(stamp 寫真原始碼雜湊)+ 不經
    --lockwatch on 自寫 lockwatch.json ⇒ 簽單拿不到 helper(helper_not_installed)、正式路徑 ask(seat_input) 仍彈窗、
    時長選單一次都沒叫。#432 R1 L2:改成斷言(原本這台沒裝固定路徑 helper 就 skip,KH2 落刀也不會真紅)。
    固定路徑那支的檢查換成「沒裝」、現在鎖屏換成「沒鎖」⇒ 每台機器結果一樣,不碰 Secure Enclave、不彈任何視窗。"""
    import se_vault
    td = tempfile.mkdtemp(prefix="aib-window-p4-")
    try:
        root = os.path.join(td, "state")
        env = dict(os.environ, EZ2_AIB_STATE_ROOT=root, EZ2_AIB_HOME=root, EZ2_AIB_LOCALE="zh-TW")
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir, mode=0o700)
        exe = os.path.join(bindir, "aib-sehelper")
        with open(exe, "w") as f:
            f.write(T2B.FAKE)
        os.chmod(exe, 0o700)
        with open(se_vault.SRC, "rb") as f, open(exe + ".sha256", "w") as g:
            g.write(hashlib.sha256(f.read()).hexdigest())
        seen, chose, found = [], [], []
        real_find = GR.find_valid
        with mock.patch.object(GR.anchored_helper, "check", lambda **k: (None, "not_installed")), \
                mock.patch.object(GR, "find_valid", lambda *a, **k: found.append(a) or real_find(*a, **k)), \
                mock.patch.object(GR, "screen_locked_now", lambda *a, **k: False), \
                mock.patch.object(GR, "choose_window", lambda r, ctx, run=None: chose.append(r) or "once"), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(G.TA, "ask", lambda r: seen.append(r) or {"ok": True, "why": "ok"}):
            t.assertEqual(GR.sign(["seat_input"], "3h", GR.Ctx(env=env)), (None, "helper_not_installed"))
            now = int(time.time())
            GR._write_json(GR.lockwatch_path(env), {"v": 1, "beat_utc": GR._utc(now), "last_lock_utc": GR._utc(now - 3600)})
            res = G.ask(_r(G), os.path.join(td, "lk"))
        t.assertEqual((len(seen), chose, found), (1, [], []))     # 窗關著:連找單都不找(第 2 張起 choose 也會擋)
        t.assertNotEqual(res.get("why"), "window")
    finally:
        shutil.rmtree(td, ignore_errors=True)


class Prod:
    """正式路徑(_default_window)的假系統:pwd 家目錄 = self.home、固定路徑 helper = 假 helper(HMAC 模擬)、
    系統日誌 / build / 開機 / 機器 / 現在鎖屏 / 時長選單 / LAContext 都換假的 ⇒ 不碰真 HOME、Secure Enclave、log、視窗。
    M = 受測的 approve_grants(刀測拿複本;_default_window 裡 import 的那份也換成它)。"""

    def __init__(self):
        self.td = tempfile.mkdtemp(prefix="aib-window-prod-")
        self.home = os.path.join(self.td, "home")
        os.makedirs(self.home)
        self.exe = os.path.join(self.td, "anchored", "aib-sehelper")
        os.makedirs(os.path.dirname(self.exe))
        with open(self.exe, "w") as f:
            f.write(T2B.FAKE)
        os.chmod(self.exe, 0o700)
        self.anchored = (self.exe, None)
        self.locks, self.seen, self.chose = [], [], []

    def _syslog(self, since, now, **k):
        return [x for x in self.locks if since <= x <= now], None

    def on(self, G, M, environ=None):
        st = contextlib.ExitStack()
        st.enter_context(mock.patch.dict(sys.modules, {"approve_grants": M}))
        st.enter_context(mock.patch.object(G.sys, "platform", "darwin"))
        st.enter_context(mock.patch("pwd.getpwuid", lambda uid: types.SimpleNamespace(pw_dir=self.home)))
        st.enter_context(mock.patch.object(M.anchored_helper, "check", lambda **k: self.anchored))
        st.enter_context(mock.patch.object(M, "syslog_locks", self._syslog))
        st.enter_context(mock.patch.object(M, "os_build", lambda *a, **k: sorted(M.VERIFIED_BUILDS)[0]))
        st.enter_context(mock.patch.object(M, "screen_locked_now", lambda *a, **k: False))
        st.enter_context(mock.patch.object(M, "boot_epoch", lambda *a, **k: 1_799_990_000))
        st.enter_context(mock.patch.object(M, "machine_id", lambda *a, **k: "m" * 16))
        st.enter_context(mock.patch.object(M, "choose_window", lambda r, ctx, run=None: self.chose.append(r) or "once"))
        st.enter_context(mock.patch.object(G.TA, "ask", lambda r: self.seen.append(r) or {"ok": True, "why": "ok"}))
        # 行程 HOME 一律指到暫存家目錄:程式就算退回 expanduser / 行程 env,也寫不到真 ~/.ez2-aib
        st.enter_context(mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW", "HOME": self.home}))
        for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
            os.environ.pop(k, None)
        if environ:
            st.enter_context(mock.patch.dict(os.environ, environ))
        return st

    def home_env(self):
        return dict(os.environ, HOME=self.home, EZ2_AIB_STATE_ROOT=os.path.join(self.home, ".ez2-aib"))

    def sign_home(self, M, cats=("seat_input",), dur="1h"):
        """本人在(假的)真家目錄簽一張(固定路徑 helper)。"""
        doc, why = M.sign(list(cats), dur, M.Ctx(env=self.home_env(), platform="darwin"))
        assert why is None, why
        return doc

    def close(self):
        shutil.rmtree(self.td, ignore_errors=True)


def cell_w19(t, G, M=GR):
    p = Prod()
    try:
        with p.on(G, M):
            doc = p.sign_home(M)
            w = G._default_window()
            t.assertIsNotNone(w)
            t.assertEqual(M._key_dir(w.ctx.env), os.path.join(p.home, ".ez2-aib", "secrets", "se"))
            res = G.ask(_r(G), os.path.join(p.td, "lk"))
            t.assertEqual((res.get("why"), res.get("grant"), p.seen), ("window", doc["nonce"], []))
            res = G.ask(_r(G, cat="release_sign", site="x", text="核准:簽發版"), os.path.join(p.td, "lk"))
            t.assertEqual((res.get("why"), len(p.seen)), ("ok", 1))            # 高風險照舊一次一按
        env = M.pinned_env({"EZ2_AIB_STATE_ROOT": "/x", "EZ2_AIB_HOME": "/y", "HOME": "/z", "LANG": "C"}, home="/h")
        t.assertEqual(env, {"HOME": "/h", "LANG": "C", "EZ2_AIB_STATE_ROOT": "/h/.ez2-aib"})
    finally:
        p.close()


def cell_w20(t, G, M=GR):
    """換狀態根:AI 的目錄裡有一張(用固定路徑 helper)驗得過的單;正式路徑只看 pwd 家目錄 ⇒ 沒單 ⇒ 一次一按。"""
    p = Prod()
    try:
        fake = os.path.join(p.td, "ai")
        os.makedirs(fake)
        with p.on(G, M, {"EZ2_AIB_STATE_ROOT": os.path.join(fake, "state"), "EZ2_AIB_HOME": os.path.join(fake, "state"),
                         "HOME": fake}):
            M.sign(["seat_input"], "3h", M.Ctx(env=dict(os.environ), platform="darwin"))
            t.assertTrue(M.load_all(dict(os.environ)))                        # AI 那邊確實有單
            res = G.ask(_r(G), os.path.join(p.td, "lk"))
        t.assertEqual((res.get("why"), len(p.seen)), ("ok", 1))
    finally:
        p.close()


def cell_w21(t, G, M=GR):
    """覆寫 helper:固定路徑那支不是 root 擁有;AI 在 <家>/.ez2-aib/bin 種假 helper(stamp = 真原始碼雜湊)+ 用它簽的單。"""
    import se_vault
    p = Prod()
    try:
        bindir = os.path.join(p.home, ".ez2-aib", "bin")
        os.makedirs(bindir)
        exe = os.path.join(bindir, "aib-sehelper")
        shutil.copy(p.exe, exe)
        with open(se_vault.SRC, "rb") as f, open(exe + ".sha256", "w") as g:
            g.write(hashlib.sha256(f.read()).hexdigest())
        with p.on(G, M):
            env = p.home_env()
            M.sign(["seat_input"], "3h", M.Ctx(env=env, helper=exe, platform="darwin"))
            t.assertTrue(M.load_all(env))
            p.anchored = (None, "not_root_owned")
            res = G.ask(_r(G), os.path.join(p.td, "lk"))
        t.assertEqual((res.get("why"), len(p.seen)), ("ok", 1))
    finally:
        p.close()


def cell_w22(t, G, M=GR):
    """自寫鎖屏紀錄:本人簽的單;系統日誌有簽單後的鎖屏;AI 自寫 lockwatch.json「心跳新、沒鎖過」⇒ 一次一按。"""
    p = Prod()
    try:
        with p.on(G, M):
            doc = p.sign_home(M)
            now = int(time.time())
            p.locks = [M._parse_utc(doc["issued_utc"])]                       # 簽完那一刻就鎖了
            env = p.home_env()
            M._write_json(M.lockwatch_path(env), {"v": 1, "beat_utc": M._utc(now), "last_lock_utc": M._utc(now - 86400)})
            res = G.ask(_r(G), os.path.join(p.td, "lk"))
        t.assertEqual((res.get("why"), len(p.seen)), ("ok", 1))
        t.assertNotEqual(res.get("grant"), doc["nonce"])
    finally:
        p.close()


def cell_w20b(t, G, M=GR):
    """#441 R1 M2:只改行程 HOME —— 單放在 <假 HOME>/.ez2-aib、pwd 家目錄是另一個 ⇒ 正式路徑不採用,一次一按。"""
    p = Prod()
    try:
        fake = os.path.join(p.td, "ai-home")
        os.makedirs(fake)
        with p.on(G, M, {"HOME": fake}):
            env = dict(os.environ, EZ2_AIB_STATE_ROOT=os.path.join(fake, ".ez2-aib"))
            M.sign(["seat_input"], "3h", M.Ctx(env=env, platform="darwin"))
            t.assertTrue(M.load_all(env))
            t.assertNotIn("EZ2_AIB_STATE_ROOT", os.environ)
            res = G.ask(_r(G), os.path.join(p.td, "lk"))
        t.assertEqual((res.get("why"), len(p.seen)), ("ok", 1))
    finally:
        p.close()


def cell_w23(t, M):
    """#441 R1 H2 刀:撤銷 = 鎖屏。--revoke 之後 AI 刪掉 revoked.jsonl 重播 ⇒ 中間鎖過屏 ⇒ locked_since。
    沒鎖上(鎖屏失敗)時刪紀錄就回來 = 已知限制(PR 照實寫),這裡也斷言它,免得以後誤以為擋得住。"""
    for locked in (True, False):
        e = Env()
        try:
            e.log()
            if not locked:
                e._lock = lambda: "failed"
            doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
            e.t[0] = T0 + 60
            out = io.StringIO()
            M.main(["--revoke", "all"], out=out, ctx=e.ctx(M))
            t.assertEqual(json.loads(out.getvalue())["lock"], "locked" if locked else "failed")
            e.t[0] = T0 + 120
            t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "revoked"))   # 撤銷紀錄還在:先擋在這
            os.unlink(os.path.join(M._dir(e.env), "revoked.jsonl"))          # AI 刪撤銷紀錄
            got = M.find_valid("seat_input", e.ctx(M))
            if locked:
                t.assertEqual(got, (None, "locked_since"))
            else:
                t.assertEqual(got[0]["nonce"], doc["nonce"])                 # 已知限制:沒鎖屏 ⇒ 擋不住
        finally:
            e.close()


def cell_w24(t, M):
    """#441 R1 H2 刀:--rotate-key 之後 AI 還原退休鑰檔 + grant-kid.json 重播 ⇒ 中間鎖過屏 ⇒ locked_since。"""
    e = Env()
    try:
        e.log()
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        kd = M._key_dir(e.env)
        keep = {n: open(os.path.join(kd, n), "rb").read() for n in (M.KEY_NAME + ".sekey", "grant-kid.json")}
        e.t[0] = T0 + 60
        out = io.StringIO()
        M.main(["--rotate-key"], out=out, ctx=e.ctx(M))
        t.assertEqual(json.loads(out.getvalue())["lock"], "locked")
        for n, b in keep.items():                                           # AI 還原舊鑰 / 舊 kid
            with open(os.path.join(kd, n), "wb") as f:
                f.write(b)
        e.t[0] = T0 + 120
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "locked_since"))
    finally:
        e.close()


def cell_w25(t, M):
    """#441 R1(@aeb40):系統日誌涵蓋不到「簽發 → 現在」整段(log_uncovered)⇒ 有效單也不自動同意、不出選單,要彈指紋。"""
    e = Env()
    td = tempfile.mkdtemp(prefix="aib-window-cover-")
    try:
        doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
        e.log()
        t.assertEqual(M.find_valid("seat_input", e.ctx(M))[0]["nonce"], doc["nonce"])
        e.log(why="log_uncovered")
        seen = []
        win = M.GateWindow(e.ctx(M), run=lambda *a, **k: t.fail("涵蓋不足不該出選單"))
        res = AG.ask(_r(AG), td, touchid=lambda x: seen.append(x) or {"ok": True, "why": "ok"}, window=win)
        t.assertEqual((len(seen), res["why"]), (1, "ok"))
        t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "log_uncovered"))
    finally:
        shutil.rmtree(td, ignore_errors=True)
        e.close()


def cell_w26(t, M):
    """#441 R2 H1:容差邊界完整重播。簽 → 鎖屏(簽後 0–5 秒)→ 使用 ⇒ locked_since(起算 = helper 簽的這一刻,沒有容差)。"""
    for lock_at in (0, 1, 2, 5, None):
        e = Env()
        try:
            e.log()
            doc, _ = M.sign(["seat_input"], "1h", e.ctx(M))
            g = _grant_of(M, e, doc)
            t.assertEqual(g["stamp"]["issued"], T0)                         # helper 的時鐘,不是呼叫端給的
            if lock_at is not None:
                e.locks.append(T0 + lock_at)
            e.t[0] = T0 + 6
            got = M.find_valid("seat_input", e.ctx(M))
            if lock_at is None:
                t.assertEqual(got[0]["nonce"], doc["nonce"])
                continue
            t.assertEqual(got, (None, "locked_since"), lock_at)
            if lock_at == 2:
                g["stamp"] = dict(g["stamp"], issued=T0 + 4, expires=T0 + 3604)   # AI 把起算挪到鎖屏之後
                M._write_json(os.path.join(M._dir(e.env), doc["nonce"] + ".json"), g)
                t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "bad_sig"))
                d = dict(g["doc"], nonce="e" * 32, issued_utc=M._utc(T0 + 10))     # 重簽一張自帶未來起算的 ⇒ helper 不簽
                r = T2B._txt(d["cats"], d["dur"])
                _, why = e.ctx(M).helper_call(["se-sign", "--name", M.KEY_NAME, "--dir", M._key_dir(e.env), "--reason", r],
                                              M.canonical(d))
                t.assertEqual(why, "bad_time")
        finally:
            e.close()


def _grant_of(M, e, doc):
    with open(os.path.join(M._dir(e.env), doc["nonce"] + ".json"), encoding="utf-8") as f:
        return json.load(f)


def cell_w10(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "1h", e.ctx(M))
        e.log()
        flag = os.path.join(M._key_dir(e.ctx(M).env), "fake-old-helper")   # 假 helper 看 --dir 裡的旗標檔
        open(flag, "w").close()                         # (helper 子行程只拿乾淨 env,不吃行程環境;#432 R1 H1)
        try:
            t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "old_helper"))
            t.assertEqual(M.current_kid(e.ctx(M)), (None, "old_helper"))
        finally:
            os.unlink(flag)
    finally:
        e.close()


def cell_w18(t, M):
    e = Env()
    try:
        M.sign(["seat_input"], "1h", e.ctx(M))
        e.log()
        t.assertIsNotNone(M.find_valid("seat_input", e.ctx(M))[0])
        for b in ("99Z999", None, ""):                                  # 新版 macOS / 讀不到
            e.build[0] = b
            n = len(e.log_calls)
            t.assertEqual(M.find_valid("seat_input", e.ctx(M)), (None, "os_unverified"), b)
            t.assertEqual(len(e.log_calls), n, b)                      # 不查系統日誌
            t.assertEqual(M.doctor_line(e.ctx(M)), ("note", "os_unverified"), b)
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw))
            return subprocess.CompletedProcess(argv, run.rc, run.out, "")
        run.rc, run.out = 0, "24G830\n"
        t.assertEqual(M.os_build(run, "darwin"), "24G830")
        t.assertEqual(seen[-1][0], ["/usr/bin/sw_vers", "-buildVersion"])
        t.assertEqual(seen[-1][1].get("env"), {"PATH": "/usr/bin:/bin"})
        t.assertEqual(seen[-1][1].get("stdin"), subprocess.DEVNULL)
        for rc, out in ((1, "24G830\n"), (0, ""), (0, "24G830 ; rm -rf\n"), (0, "x" * 40)):
            run.rc, run.out = rc, out
            t.assertIsNone(M.os_build(run, "darwin"), (rc, out))
        n = len(seen)
        t.assertIsNone(M.os_build(run, "linux"))
        t.assertEqual(len(seen), n)                                    # 不是 Mac 不跑
        c = M.Ctx(env=e.env, platform="darwin", run=lambda a, **k: subprocess.CompletedProcess(a, 0, "99Z999\n", ""))
        t.assertEqual(c.os_build(), "99Z999")                          # Ctx 預設走 os_build(帶 Ctx 的 run / platform)
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
    def test_w12k(self): cell_w12k(self, GR)
    def test_w15(self): cell_w15(self, GR)
    def test_w16(self): cell_w16(self, AG)
    def test_w17(self): cell_w17(self, AG)
    def test_w19(self): cell_w19(self, AG)
    def test_w20(self): cell_w20(self, AG)
    def test_w21(self): cell_w21(self, AG)
    def test_w22(self): cell_w22(self, AG)
    def test_w16_owner(self): cell_w16_owner(self, GR.anchored_helper)
    def test_w20b(self): cell_w20b(self, AG)
    def test_w23(self): cell_w23(self, GR)
    def test_w24(self): cell_w24(self, GR)
    def test_w25(self): cell_w25(self, GR)
    def test_w26(self): cell_w26(self, GR)
    def test_w18(self): cell_w18(self, GR)

    def test_w18_doctor(self):
        sys.path.insert(0, str(ROOT / "src" / "bootstrap"))
        import aib_doctor
        td = tempfile.mkdtemp(prefix="aib-window-doctor-")
        try:
            with mock.patch.object(GR, "doctor_line", return_value=("note", "os_unverified")):
                r = aib_doctor.check_grants(td, ROOT, env={"EZ2_AIB_LOCALE": "zh-TW"})
            self.assertEqual(r["status"], aib_doctor.NOTE)
            self.assertIn("macOS", r["detail"])
            self.assertTrue(r["remedy"])
            for loc in ("zh-TW", "en", "ja", "ko"):
                with open(ROOT / "i18n" / ("%s.json" % loc), encoding="utf-8") as f:
                    d = json.load(f)
                for k in ("os_unverified", "os_remedy"):
                    self.assertIn("macOS", d["aib.doctor.check.grants." + k], (loc, k))
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_w9_end_to_end(self):
        e = Env()
        td = tempfile.mkdtemp(prefix="aib-window-lock-")
        try:
            e.log()
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

    def test_w14_no_menu_without_syslog(self):
        e = Env()
        try:
            ran = []
            win = GR.GateWindow(e.ctx(), run=lambda *a, **k: ran.append(a))
            self.assertEqual(win.choose(_r(AG)), "once")               # 系統日誌查不到 ⇒ 不出選單
            e.log(why="log_timeout")
            self.assertEqual(win.choose(_r(AG)), "once")               # 超過 3 秒也一樣
            e.log()
            ctx = e.ctx()
            ctx.helper = None
            ctx._anchored = lambda platform=None: (None, "not_installed")
            self.assertEqual(GR.GateWindow(ctx, run=lambda *a, **k: ran.append(a)).choose(_r(AG)), "once")  # helper 不在
            self.assertEqual(ran, [])
        finally:
            e.close()

    def test_w11_durations(self):
        self.assertEqual(GR.durations_for("outbound"), ["15m", "30m", "1h", "3h"])
        self.assertEqual(GR.durations_for("command"), ["15m", "30m", "1h", "3h"])
        self.assertEqual(GR.durations_for("seat_input"), ["15m", "30m", "1h", "3h"])   # #441:24h 超過日誌涵蓋

    def test_w12_lockwatch_install_and_doctor(self):
        e = Env()
        home = tempfile.mkdtemp(prefix="aib-window-home-")
        calls = []
        try:
            lc = lambda *a: calls.append(a) or (0, "")                  # noqa: E731
            cell_w12_needs_touchid(self, GR, e, lc, home, calls)
            asked = []
            out = io.StringIO()
            self.assertEqual(GR.main(["--lockwatch", "on"], out=out, ctx=e.ctx(), launchctl=lc, home=home,
                                     touchid=lambda r: asked.append(r) or {"ok": True, "why": "ok"}), 0)
            self.assertEqual([(a.category, a.site) for a in asked], [("window_grant", "approve_grants.lockwatch_on")])
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
            self.assertEqual(GR.doctor_line(e.ctx()), ("note", "log_failed"))
            e.log()
            self.assertEqual(GR.doctor_line(e.ctx()), ("ok", "syslog_ok"))
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
    ("KL1", '    events, why = ctx.syslog(int(since), int(ctx.now()))\n    if why:\n        return None, why\n',
     '    events, why = ctx.syslog(int(since), int(ctx.now()))\n    events = events or []\n', cell_w2),
    ("KL2", '    if issued > last_lock:\n        return doc, None\n', '    return doc, None\n', cell_w3),
    ("KL3", '    if ctx.screen_locked():\n        return None, "screen_locked"\n', '', cell_w2),
    ("KL4", '    if isinstance(res, dict) and res.get("ok") is True and res.get("owner_acl") != "probed":\n',
     '    if False:\n', cell_w10),
    ("KL6", '        if self.ctx.helper_exe()[1] is not None or lock_state(self.ctx, int(self.ctx.now()) - 60)[1] is not None:\n'
     '            return "once"\n', '', cell_w14),
    ("KL7", '    last_lock, why = lock_state(ctx, issued)\n    if why:\n        return None, why\n', '    last_lock = 0\n', cell_w15),
    ("KL9", '    events, why = ctx.syslog(int(since), int(ctx.now()))\n',
     '    try:\n        with open(lockwatch_path(ctx.env), encoding="utf-8") as fh:\n'
     '            events, why = [_parse_utc(json.load(fh)["last_lock_utc"])], None\n'
     '    except (OSError, ValueError, KeyError):\n        events, why = None, "no_lockwatch"\n', cell_w2),
    ("KL10", '    if ctx.os_build() not in VERIFIED_BUILDS:\n        return None, "os_unverified"\n', '', cell_w18),
    ("KL11", '                env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL)\n    except (OSError, subprocess.SubprocessError):\n        return None\n    b =',
     '                stdin=subprocess.DEVNULL)\n    except (OSError, subprocess.SubprocessError):\n        return None\n    b =', cell_w18),
    ("KW1", '    env["HOME"] = h\n    env["EZ2_AIB_STATE_ROOT"] = os.path.join(h, ".ez2-aib")\n', '', lambda t, M: cell_w20(t, AG, M)),
    ("KW2", '        return (exe, None) if exe else (None, "helper_" + str(why))\n',
     '        return (exe, None) if exe else (os.path.join(se_vault._state_root(self.env), "bin", "aib-sehelper"), None)\n',
     lambda t, M: cell_w21(t, AG, M)),
    ("KW4", '    h = home or pwd.getpwuid(os.getuid()).pw_dir\n', '    h = home or os.environ["HOME"]\n',
     lambda t, M: cell_w20b(t, AG, M)),
    ("KW5", '        lk = ctx.lock_screen()\n        return emit({"ok": True, "revoked": n,', '        lk = "locked"\n        return emit({"ok": True, "revoked": n,',
     cell_w23),
    ("KW6", '        lk = ctx.lock_screen() if kid else None\n', '        lk = "locked"\n', cell_w24),
    ("KW7", '    events, why = ctx.syslog(int(since), int(ctx.now()))\n    if why:\n        return None, why\n',
     '    events, why = ctx.syslog(int(since), int(ctx.now()))\n    if why == "log_uncovered":\n        events, why = [], None\n'
     '    if why:\n        return None, why\n', cell_w25),
    ("KW8", '    last_lock, why = lock_state(ctx, issued)\n', '    last_lock, why = lock_state(ctx, issued + 5)\n', cell_w26),
    ("KW3", '    events, why = ctx.syslog(int(since), int(ctx.now()))\n',
     '    try:\n        with open(lockwatch_path(ctx.env), encoding="utf-8") as fh:\n'
     '            events, why = [_parse_utc(json.load(fh)["last_lock_utc"])], None\n'
     '    except (OSError, ValueError, KeyError):\n        events, why = None, "no_lockwatch"\n', lambda t, M: cell_w22(t, AG, M)),
    ("KL8", '            ok, why = approve_lockwatch_on(ctx, touchid)\n', '            ok, why = True, None\n', cell_w12k),
    ("KL5", '    if not spec or spec["high_risk"] or spec["max_window"] <= 0:\n        return None, "no_window_category"\n'
     '    kid, why = current_kid(ctx)\n', '    kid, why = current_kid(ctx)\n', cell_w4),
]
_H1_ANCHOR = '    if ctx.helper_exe()[1] is not None:\n        return None\n'
_H1_OPEN = ''
KNIVES_AG = [
    ("KG1", '    win = window if window_open(r) else None\n', '    win = window\n', cell_w8),
    ("KG2", '                    _safe(win.use, doc, r.site, r.category)\n', '', cell_w5),
    ("KG3", '            if choice == "cancel":\n                res = {"ok": False, "why": "cancel"}\n            elif',
     '            if False:\n                pass\n            elif', cell_w6),
    ("KH1", _H1_ANCHOR, _H1_OPEN, cell_w16),
]
KNIVES_AG.append(("KH2", _H1_ANCHOR, _H1_OPEN, cell_w17))    # #432 R1 L2:不再只在 Mac、不再 skip
KNIVES_AG.append(("KH3", '    if (platform or sys.platform) != "darwin":\n        return None\n    import approve_grants\n',
                  '    import approve_grants\n', cell_w16))


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


KNIVES_AH = [
    ("KH4", '    if st.st_uid != 0:\n        return "not_root_owned"\n', '', cell_w16_owner),
]


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

    def test_knives_anchored(self):
        self._run(KNIVES_AH, SRC / "anchored_helper.py", lambda M: None)

    def test_knives_gate(self):
        self._run(KNIVES_AG, SRC / "approve_gate.py", lambda M: None)


if __name__ == "__main__":
    unittest.main()
