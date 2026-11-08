#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""`aib <ai> <dgrs|safe> <角色> [艙] gameworld`:開席後進遊戲化大廳(設計稿 DESIGN-gameworld v0.3,043dd 11-07)。

不開真瀏覽器(AIB_NO_BROWSER=1)、不碰真外掛 / 真伺服器(假的 run 只記參數、回預先寫好的結果)、狀態根指暫存夾。

解析(aib --dry-run)
  G1 … gameworld / 9320 gameworld / new gameworld / gameworld dm / dm gameworld / 3sits new gameworld 都剝對
  G2 gameworld 寫兩次 ⇒ 第二個落到艙名位置 ⇒ rc 2、說是保留字
  G3 只有 3 個字(角色就叫 gameworld)不剝
單一介面
  U1 新外掛:started / switched / already 都只叫一次 `room open --no-browser --switch <四碼>`,從不帶 --cabin;
     假 config 前後一字不差;成功、how=skipped
  U2 舊外掛(--help 沒 --switch):只叫 `room open --no-browser`,實際艙未知,提示是 legacy 那句;不讀 room status
  U3 不誤報:ok:false(not_live / timeout / error)、外掛 rc≠0、核心硬逾時、JSON 壞、缺 cabin、cabin ≠ 目標、
     cabin 不合法、url 不是 127.0.0.1 ⇒ 不開窗、提示不是成功;cabin ≠ 目標時提示帶實際艙;不合法時「實際艙未知」
  U4 網址進輸出 / 提示前只留 origin(路徑、查詢、片段裡的東西不出現)
  U5 能力快取:同身份只查一次 --help;換執行檔(大小 / mtime 變)重查;版本變重查
去重(同鍵領取:flock 持有到做完,#486 r3)
  U6 同請求同艙第二次 ⇒ dedup、外掛只叫一次;不同艙 / 新請求照叫;失敗後同請求重試會叫
  U7 並發:12 個程序同時搶同一鍵 ⇒ 只有一個拿到;主程序拿著時另一個程序拿不到
  U7b 障礙(空檔:看過 .done → 開鎖檔之間):B 停在那裡、A 整個做完放鎖 ⇒ B 繼續 ⇒ dedup、只有 A 叫外掛
  U8 崩潰:A 拿著鎖被 SIGKILL ⇒ 下一個人馬上拿到(不等逾時)、reclaimed +1
  U8b 活著的擁有者(停在拿鎖後、叫外掛前,多久都一樣)、B 嘗試 ⇒ B dedup、只有 A 叫外掛
  U8c 活著的擁有者停在「外掛呼叫中」、B 嘗試 ⇒ B dedup;A 開窗收尾後只有 A 叫過
  U8d 障礙(inode):B 開了鎖檔還沒拿鎖時,A 做完把鎖檔刪掉、C 建了新鎖檔拿著 ⇒ B 拿到舊檔的鎖也不能做
  U9 12 個程序同時搶一個崩潰留下的鎖檔 ⇒ 只有一個拿到
  U14 未知(11-08):外掛 post_unconfirmed / 核心 12 秒硬逾時 / JSON 壞 ⇒ result=unknown、不開窗、提示是「沒收到確認」、
      寫 .unknown 不寫 .done;同鍵再來 ⇒ unknown_kept、外掛不再叫;新請求照叫;舊外掛(沒 --switch)逾時仍是普通 timeout
  U15 寫標記失敗(write-ahead,#491 bb7dc 初看):叫外掛前先寫 .unknown,寫不進去 ⇒ 不叫外掛、error/state-unwritable、之後可重試;
      成功但 .done 寫不進去 / 確定失敗但標記刪不掉 / 叫外掛時出例外 ⇒ 標記留著 ⇒ 同鍵 unknown_kept、外掛不再叫
  U16 收尾(#491 bb7dc 初看 2):_gc 不刪持鎖中的 .unknown(就算超過 24 小時)、.unknown 不計入 32 筆上限;
      外掛說成功但艙不對 / 網址不對 ⇒ 證明不了沒副作用 ⇒ 標記留著;舊外掛(沒 write-ahead)失敗不會被補成未知
其他
  U10 check:沒裝 / 太舊 ⇒ rc 3 + 對的那句;沒 --switch ⇒ rc 0 + 提示
  U11 四語字典鍵齊、用法有 [gameworld]
  U12 aib 的前景檢查在開窗收尾段之前、批次 exec 之前;seats_batch 在逐席呼叫前取走並 unset 請求 ID
  U13 代開(AIB_NO_ATTACH / AIB_NO_CLIENT_SWITCH)⇒ 不叫 python、清掉請求、印代開那句
刀(複本):KG1 3 個字也剝 ⇒ G3 紅;KG2 拿掉 cabin == 目標 ⇒ U3 紅;KG3 拿鎖後不再看 .done ⇒ U7b 紅;
  KG8 鎖換成永遠拿得到 ⇒ U8b 紅;KG9 不比 inode ⇒ U8d 紅;KG10 鎖換成永遠拿得到 ⇒ U8c 紅;
  KG11 不看 post_unconfirmed ⇒ U14 紅;KG12 未知當普通失敗(不寫 .unknown)⇒ U14 紅;KG13 領取不看 .unknown ⇒ U14 紅;
  KG14 核心逾時當普通逾時 ⇒ U14 紅;KG18 _gc 不看鎖 / KG19 .unknown 計入筆數上限 / KG20 mismatch 也清標記 / KG21 舊外掛也補未知 ⇒ U16 紅;
  KG15 標記寫不進去照叫外掛 / KG16 例外時清標記 / KG17 .done 寫失敗也清標記 ⇒ U15 紅;
  KG4 輸出完整網址 ⇒ U4 紅;KG5 快取身份不看 mtime / 大小 ⇒ U5 紅;KG6 批次不 unset ⇒ U12 紅;KG7 代開也檢查 ⇒ U13 紅
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
AIB = COCKPIT / "aib"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import gameworld as G  # noqa: E402

HELP_NEW = "usage: aib room open [--no-browser] [--switch CODE4]"
HELP_OLD = "usage: aib room open [--cabin X] [--no-browser]"
HELP_ANCIENT = "usage: aib room open [--cabin X]"


def wait_until(pred, timeout):
    t = time.time()
    while time.time() - t < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


P = "%d-" % int(time.time())   # 請求 ID = <產生時間>-<亂數>;測試的短 ID 前面都接這個(open_lobby 拒絕格式不對 / 太舊的)


def K(req, cabin="9320"):
    return G._key(P + req, cabin)


class Fake:
    """假的 subprocess.run:記每次 argv;room open --help 回 help,room open 回 result(dict / 字串 / 例外)。"""

    def __init__(self, help_text=HELP_NEW, result=None, rc=0):
        self.help, self.result, self.rc, self.calls = help_text, result, rc, []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[1:] == ["room", "open", "--help"]:
            return subprocess.CompletedProcess(argv, 0, self.help, "")
        if argv[1:3] == ["room", "open"]:
            if isinstance(self.result, BaseException):
                raise self.result
            out = self.result if isinstance(self.result, str) else json.dumps(self.result)
            return subprocess.CompletedProcess(argv, self.rc, "noise\n" + out + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def opens(self):
        return [c for c in self.calls if c[1:3] == ["room", "open"] and "--help" not in c]

    def notes(self):
        return [c[-1] for c in self.calls if "display-message" in c]


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-gw-")
        self.d = Path(self.td.name)
        self.addCleanup(self.td.cleanup)
        self.plug = self.d / "plugin"
        self.plug.mkdir()
        self.script = self.plug / "captain_room.py"
        self.script.write_text("# fake\n")
        (self.plug / "plugin.json").write_text(json.dumps({"version": "0.1.80"}))
        self.home = self.d / "cabin"
        (self.home / "cockpit").mkdir(parents=True)
        self.cfg = self.d / "room-config.json"
        self.cfg.write_text('{"cabin": "auto"}\n')
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.d / "root"), "AIB_NO_BROWSER": "1", "AIB_TMUX_BIN": "tmux"}
        self._loc = os.environ.get("EZ2_AIB_LOCALE")
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        self.addCleanup(self._restore)

    def _restore(self):
        if self._loc is None:
            os.environ.pop("EZ2_AIB_LOCALE", None)
        else:
            os.environ["EZ2_AIB_LOCALE"] = self._loc

    def find(self, cmd):
        return self.script, "captain-room", "ok"

    def lobby_raw(self, fake, req, cabin="9320"):
        import io
        return G.open_lobby(cabin, req, str(self.home), "aib-x", out=io.StringIO(), env=self.env, run=fake, find=self.find)

    def lobby(self, fake, cabin="9320", req="r1"):
        import io
        out = io.StringIO()
        r = G.open_lobby(cabin, P + req, str(self.home), "aib-x", out=out, env=self.env, run=fake, find=self.find)
        return r, out.getvalue()


class Args(unittest.TestCase):
    def dry(self, *args):
        with tempfile.TemporaryDirectory(prefix="aib-gw-home-") as home:
            e = {"PATH": os.environ.get("PATH", ""), "HOME": home, "LANG": "en_US.UTF-8", "EZ2_AIB_LOCALE": "zh-TW"}
            return subprocess.run(["bash", str(AIB), *args, "--dry-run"], capture_output=True, text=True, timeout=120,
                                  env=e, stdin=subprocess.DEVNULL)

    def test_g1_strip(self):
        for args, rest, dm in ((("claude", "safe", "dev", "gameworld"), "claude safe dev", "auto"),
                               (("claude", "safe", "dev", "new", "gameworld"), "claude safe dev new", "auto"),
                               (("claude", "safe", "dev", "gameworld", "dm"), "claude safe dev", "on"),
                               (("claude", "safe", "dev", "dm", "gameworld"), "claude safe dev", "on"),
                               (("claude", "safe", "dev", "3sits", "new", "gameworld"), "claude safe dev 3sits new", "auto")):
            with self.subTest(args):
                p = self.dry(*args)
                self.assertIn("aib: (dry-run) gameworld=on dm=%s args=%s\n" % (dm, rest), p.stderr, p.stderr[-800:])
                self.assertEqual(p.returncode, 0, p.stderr[-800:])
        p = self.dry("claude", "safe", "dev", "9320", "gameworld")
        self.assertIn("gameworld=on dm=auto args=claude safe dev 9320\n", p.stderr, p.stderr[-800:])

    def test_g2_twice_is_reserved(self):
        p = self.dry("claude", "safe", "dev", "gameworld", "gameworld")
        self.assertEqual(p.returncode, 2, p.stderr[-800:])
        self.assertIn("保留字", p.stderr)

    def test_g3_three_words_not_stripped(self):
        p = self.dry("claude", "safe", "gameworld")
        self.assertNotIn("gameworld=on", p.stderr, p.stderr[-800:])


class Interface(Base):
    def test_u1_single_interface(self):
        before = self.cfg.read_bytes()
        for res in ("started", "switched", "already"):
            with self.subTest(res):
                f = Fake(result={"ok": True, "result": res, "cabin": "9320", "url": "http://127.0.0.1:8771/", "elapsed_ms": 5})
                r, out = self.lobby(f, req="u1-" + res)
                self.assertEqual(r, res, out)
                self.assertEqual(len(f.opens()), 1, f.calls)
                self.assertEqual(f.opens()[0][1:], ["room", "open", "--no-browser", "--switch", "9320"])
                self.assertFalse(any("--cabin" in c for c in f.calls), f.calls)
                self.assertFalse(any("status" in c for c in f.calls), "不叫 room status")
                self.assertIn("how=skipped", out)
                self.assertEqual(f.notes(), [], "成功不跳提示")
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_u2_legacy_plugin(self):
        f = Fake(help_text=HELP_OLD, result={"ok": True, "url": "http://127.0.0.1:8771/", "pid": 1, "browser": False})
        r, out = self.lobby(f)
        self.assertEqual(r, "legacy", out)
        self.assertEqual(f.opens()[0][1:], ["room", "open", "--no-browser"])
        self.assertFalse(any("--cabin" in c or "status" in c for c in f.calls), f.calls)
        self.assertIn("actual=unknown", out)
        self.assertEqual(len(f.notes()), 1)
        self.assertIn("實際艙未知", f.notes()[0])

    def test_u3_no_false_success(self):
        ok_url = "http://127.0.0.1:8771/"
        cases = [
            ("not_live", {"ok": False, "result": "not_live", "cabin": "1111", "url": ok_url}, 1, "1111"),
            ("timeout", {"ok": False, "result": "timeout", "cabin": None, "url": ok_url}, 1, None),
            ("error", {"ok": False, "result": "error", "url": ok_url}, 1, None),
            ("rc", {"ok": True, "result": "switched", "cabin": "9320", "url": ok_url}, 3, None),
            ("core-timeout", subprocess.TimeoutExpired(["aib"], 12), 0, None),
            ("bad-json", "{not json", 0, None),
            ("no-cabin", {"ok": True, "result": "switched", "url": ok_url}, 0, None),
            ("mismatch", {"ok": True, "result": "switched", "cabin": "abcd", "url": ok_url}, 0, "abcd"),
            ("bad-cabin", {"ok": True, "result": "switched", "cabin": "../x", "url": ok_url}, 0, None),
            ("bad-url", {"ok": True, "result": "switched", "cabin": "9320", "url": "http://evil.example/"}, 0, None),
        ]
        for i, (name, res, rc, actual) in enumerate(cases):
            with self.subTest(name):
                f = Fake(result=res, rc=rc)
                r, out = self.lobby(f, req="u3-%d" % i)
                self.assertNotIn(r, G.OK_RESULTS, out)
                self.assertNotIn("how=skipped", out, "失敗不開窗")
                self.assertIn("how=-", out)
                self.assertEqual(len(f.notes()), 1, f.calls)
                n = f.notes()[0]
                if actual:
                    self.assertIn(actual, n)
                elif "目前是" in n:
                    self.assertIn("實際艙未知", n)
                self.assertNotIn("已切到", n)

    def test_u4_url_origin_only(self):
        u = "http://127.0.0.1:8771/lobby/x?pair=SECRET1#pair=SECRET2"
        f = Fake(result={"ok": True, "result": "switched", "cabin": "9320", "url": u})
        r, out = self.lobby(f)
        self.assertEqual(r, "switched")
        self.assertIn("url=http://127.0.0.1:8771/\n", out)
        self.assertNotIn("SECRET", out)
        f = Fake(result={"ok": True, "result": "not_live", "cabin": "1111", "url": u})
        f.result["ok"] = False
        r, out = self.lobby(f, req="u4b")
        self.assertNotIn("SECRET", out + "".join(f.notes()))

    def test_u5_capability_cache(self):
        f = Fake()
        for _ in range(3):
            self.assertTrue(G.capabilities(self.env, f, self.find)["switch"])
        self.assertEqual(sum(1 for c in f.calls if "--help" in c), 1, "同身份只查一次")
        time.sleep(0.01)
        self.script.write_text("# fake replaced, same version\n")
        os.utime(str(self.script), ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
        f2 = Fake(help_text=HELP_OLD)
        self.assertFalse(G.capabilities(self.env, f2, self.find)["switch"], "換執行檔要重查")
        self.assertEqual(sum(1 for c in f2.calls if "--help" in c), 1)
        (self.plug / "plugin.json").write_text(json.dumps({"version": "0.1.81"}))
        f3 = Fake()
        self.assertTrue(G.capabilities(self.env, f3, self.find)["switch"], "版本變要重查")


class Dedup(Base):
    def ok(self, cabin="9320"):
        return Fake(result={"ok": True, "result": "switched", "cabin": cabin, "url": "http://127.0.0.1:8771/"})

    def test_u6_keys(self):
        f = self.ok()
        self.assertEqual(self.lobby(f, req="A")[0], "switched")
        self.assertEqual(self.lobby(f, req="A")[0], "dedup")
        self.assertEqual(len(f.opens()), 1)
        f2 = self.ok("abcd")
        self.assertEqual(self.lobby(f2, cabin="abcd", req="A")[0], "switched", "不同艙照叫")
        f3 = self.ok()
        self.assertEqual(self.lobby(f3, req="B")[0], "switched", "新請求照叫")
        bad = Fake(result={"ok": False, "result": "error", "sent": False})
        self.assertEqual(self.lobby(bad, req="C")[0], "error")
        f4 = self.ok()
        self.assertEqual(self.lobby(f4, req="C")[0], "switched", "失敗後同請求重試會叫")
        self.assertEqual(len(f4.opens()), 1)

    def test_u14_unknown_kept(self):
        ok_url = "http://127.0.0.1:8771/"
        cases = [
            ("post-unconfirmed", {"ok": False, "result": "timeout", "cabin": "1111", "url": ok_url, "post_unconfirmed": True}, 1),
            ("core-timeout", subprocess.TimeoutExpired(["aib"], 12), 0),
            ("bad-json", "{not json", 0),
        ]
        d = self.home / "cockpit" / "gameworld-req"
        for i, (name, res, rc) in enumerate(cases):
            with self.subTest(name):
                req = "u14-%d" % i
                f = Fake(result=res, rc=rc)
                r, out = self.lobby(f, req=req)
                self.assertEqual(r, "unknown", out)
                self.assertIn("how=-", out, "未知不開窗")
                self.assertIn("why=%s" % name, out)
                n = f.notes()
                self.assertEqual(len(n), 1, f.calls)
                self.assertIn("沒收到確認", n[0])
                self.assertNotIn("已切到", n[0])
                k = K(req)
                self.assertTrue((d / (k + ".unknown")).exists())
                self.assertFalse((d / (k + ".done")).exists(), "未知不是成功")
                again = self.ok()
                self.assertEqual(self.lobby(again, req=req)[0], "unknown_kept", "同鍵不自動重試")
                self.assertEqual(again.opens(), [])
                fresh = self.ok()
                self.assertEqual(self.lobby(fresh, req=req + "-new")[0], "switched", "人重打 = 新請求照做")
        # 舊外掛(沒 --switch,不會 POST):核心逾時仍是普通逾時
        (self.plug / "plugin.json").write_text(json.dumps({"version": "0.1.79"}))   # 換版本 ⇒ 能力快取重查
        old = Fake(help_text=HELP_OLD, result=subprocess.TimeoutExpired(["aib"], 12))
        self.assertEqual(self.lobby(old, req="u14-old")[0], "timeout")

    def test_u15_marker_write_fails(self):
        from unittest import mock
        d = self.home / "cockpit" / "gameworld-req"
        real_touch, real_unlink = G._touch, os.unlink

        def touch_fails(suffix):
            def t(path):
                if str(path).endswith(suffix):
                    raise OSError(28, "No space left on device")
                return real_touch(path)
            return t
        # a) write-ahead 寫不進去 ⇒ 不叫外掛;沒有標記;之後同鍵可重試
        f = self.ok()
        with mock.patch.object(G, "_touch", touch_fails(".unknown")):
            r, out = self.lobby(f, req="u15a")
        self.assertEqual((r, f.opens()), ("error", []), out)
        self.assertIn("why=state-unwritable", out)
        self.assertEqual(len(f.notes()), 1, f.calls)
        self.assertIn("沒辦法記錄這次切換", f.notes()[0], "專用提示,不借 start_failed")
        self.assertIn("所以這次沒有切換", f.notes()[0])
        self.assertFalse((d / (K("u15a") + ".unknown")).exists())
        f2 = self.ok()
        self.assertEqual(self.lobby(f2, req="u15a")[0], "switched", "什麼都沒發生 ⇒ 可重試")
        # b) 成功但 .done 寫不進去 ⇒ 標記留著 ⇒ 同鍵不重開
        f = self.ok()
        with mock.patch.object(G, "_touch", touch_fails(".done")):
            self.assertEqual(self.lobby(f, req="u15b")[0], "switched")
        self.assertTrue((d / (K("u15b") + ".unknown")).exists())
        again = self.ok()
        self.assertEqual((self.lobby(again, req="u15b")[0], again.opens()), ("unknown_kept", []))
        # c) 確定失敗但標記刪不掉 ⇒ 留著 = 未知(安全的方向)
        bad = Fake(result={"ok": False, "result": "not_live", "cabin": "1111", "url": "http://127.0.0.1:8771/", "sent": False}, rc=1)

        def unlink_fails(path, *a, **k):
            if str(path).endswith(".unknown"):
                raise OSError(13, "Permission denied")
            return real_unlink(path, *a, **k)
        with mock.patch.object(G.os, "unlink", unlink_fails):
            self.assertEqual(self.lobby(bad, req="u15c")[0], "not_live")
        again = self.ok()
        self.assertEqual((self.lobby(again, req="u15c")[0], again.opens()), ("unknown_kept", []))
        # d) 叫外掛時出例外(程序被打斷)⇒ 標記留著
        boom = Fake(result=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.lobby(boom, req="u15d")
        self.assertTrue((d / (K("u15d") + ".unknown")).exists())
        again = self.ok()
        self.assertEqual((self.lobby(again, req="u15d")[0], again.opens()), ("unknown_kept", []))
        # e) 外掛明說沒送(sent:false)且刪得掉 ⇒ 標記清掉,同鍵可重試
        bad = Fake(result={"ok": False, "result": "not_live", "cabin": "1111", "url": "http://127.0.0.1:8771/", "sent": False}, rc=1)
        self.assertEqual(self.lobby(bad, req="u15e")[0], "not_live")
        self.assertFalse((d / (K("u15e") + ".unknown")).exists())
        again = self.ok()
        self.assertEqual(self.lobby(again, req="u15e")[0], "switched")

    def test_u16_gc_and_clear_rules(self):
        d = self.home / "cockpit" / "gameworld-req"
        old = time.time() - G.KEEP_S - 100

        def fill(n):
            for i in range(n):
                (d / ("zz%02d.done" % i)).write_text("")
        # a) 擁有者拿著鎖、已 write-ahead,標記超過 24 小時 + 超過筆數 ⇒ _gc 不動;放鎖後才按時間收
        cl = G.claim(d, "own", "9320")
        self.assertIsNotNone(cl)
        self.assertTrue(G.arm(cl))
        mark = d / (G._key("own", "9320") + ".unknown")
        os.utime(str(mark), (old, old))
        fill(40)
        G._gc(d, time.time())
        self.assertTrue(mark.exists(), "持鎖中的標記不得被收")
        G.finish(cl, False, unknown=True)
        self.assertTrue(mark.exists())
        G._gc(d, time.time())
        self.assertFalse(mark.exists(), "沒人拿著、超過 24 小時 ⇒ 照時間收")
        # b) 年輕的 .unknown 不因筆數上限被收
        young = d / (G._key("young", "9320") + ".unknown")
        young.write_text("")
        fill(40)
        G._gc(d, time.time())
        self.assertTrue(young.exists(), ".unknown 不計入 32 筆上限")
        # c) 外掛說成功但艙不對 / 網址不對 ⇒ 標記留著 ⇒ 同鍵不自動重試
        ok_url = "http://127.0.0.1:8771/"
        for i, res in enumerate(({"ok": True, "result": "switched", "cabin": "abcd", "url": ok_url},
                                 {"ok": True, "result": "switched", "cabin": "9320", "url": "http://evil.example/"})):
            req = "u16c-%d" % i
            self.assertNotIn(self.lobby(Fake(result=res), req=req)[0], G.OK_RESULTS)
            self.assertTrue((d / (K(req) + ".unknown")).exists(), res)
            again = self.ok()
            self.assertEqual((self.lobby(again, req=req)[0], again.opens()), ("unknown_kept", []))
        # d) 舊外掛(沒 --switch、沒 write-ahead)失敗 ⇒ 不補成未知,同鍵可重試
        (self.plug / "plugin.json").write_text(json.dumps({"version": "0.1.78"}))
        bad = Fake(help_text=HELP_OLD, result={"ok": False}, rc=1)
        self.lobby(bad, req="u16d")
        self.assertFalse((d / (K("u16d") + ".unknown")).exists())
        good = Fake(help_text=HELP_OLD, result={"ok": True, "url": ok_url})
        self.assertEqual(self.lobby(good, req="u16d")[0], "legacy", "舊外掛失敗後同鍵照常重試")

    def test_u17_plugin_failure_needs_sent_false(self):
        """#491 bb7dc 讀碼 2:外掛籠統失敗(冷啟伺服器沒在時限內起來 = 背景仍會用目標艙起來)不能清標記。
        只有外掛明說 sent:false 才算「確定沒執行」;沒帶、帶 true、帶別的值一律留標記 ⇒ 同鍵不自動重試。"""
        d = self.home / "cockpit" / "gameworld-req"
        for i, extra in enumerate(({}, {"sent": True}, {"sent": "false"}, {"sent": 0})):
            req = "u17-%d" % i
            res = dict({"ok": False, "result": "error", "cabin": None, "url": None}, **extra)
            self.assertEqual(self.lobby(Fake(result=res, rc=1), req=req)[0], "error")
            self.assertTrue((d / (K(req) + ".unknown")).exists(), extra)
            again = self.ok()
            self.assertEqual((self.lobby(again, req=req)[0], again.opens()), ("unknown_kept", []), extra)
        ok = Fake(result={"ok": False, "result": "error", "cabin": None, "url": None, "sent": False}, rc=1)
        self.assertEqual(self.lobby(ok, req="u17-f")[0], "error")
        self.assertFalse((d / (K("u17-f") + ".unknown")).exists())
        again = self.ok()
        self.assertEqual(self.lobby(again, req="u17-f")[0], "switched", "明說沒送 ⇒ 同鍵可重試")

    def test_u18_stale_request_never_calls_plugin(self):
        """#491 bb7dc 讀碼 1:標記 24 小時後被收;請求 ID 超過 12 小時就拒絕 ⇒ 收掉之後同鍵也不會被自動再做。"""
        d = self.home / "cockpit" / "gameworld-req"
        t0 = int(time.time())
        req = "%d-u18" % (t0 - 3600)
        unk = Fake(result={"ok": False, "result": "timeout", "post_unconfirmed": True}, rc=1)
        out = __import__("io").StringIO()
        r = G.open_lobby("9320", req, str(self.home), "", out=out, env=self.env, run=unk, find=self.find)
        self.assertEqual(r, "unknown")
        mark = d / (G._key(req, "9320") + ".unknown")
        old = time.time() - G.KEEP_S - 100
        os.utime(str(mark), (old, old))
        G._gc(d, time.time())
        self.assertFalse(mark.exists(), "前提:標記照時間被收")
        later = t0 + G.KEEP_S                             # 標記被收的那一刻,同一個 ID 再來
        again = self.ok()
        r = G.open_lobby("9320", req, str(self.home), "", out=out, env=self.env, run=again, find=self.find,
                         now=lambda: later)
        self.assertEqual((r, again.opens()), ("stale", []), "GC 之後同鍵不准再叫外掛")
        for bad in ("", "r1", "abc-def", "%d-" % t0, "%d-x y" % t0, "%d-ok" % (t0 + 3600)):
            f = self.ok()
            self.assertEqual((self.lobby_raw(f, bad), f.opens()), ("stale", []), bad)
        f = self.ok()
        self.assertEqual(self.lobby_raw(f, "%d-%s" % (t0 - G.REQ_MAX_AGE_S + 60, "edge")), "switched", "還在期限內照做")

    def _race(self, n, prep=None):
        d = self.home / "cockpit" / "gameworld-req"
        d.mkdir(parents=True, exist_ok=True)
        if prep:
            prep(d)
        go, end = self.d / "go", self.d / "end"
        # 贏家要活到大家都搶完才退:不然晚到的程序看到擁有者已死,照設計接手,算成第二個贏家(那是崩潰接手,不是並發)
        code = ("import os,sys,time\nsys.path.insert(0,%r)\nimport gameworld as G\n"
                "while not os.path.exists(%r): time.sleep(0.001)\n"
                "print('WIN' if G.claim(%r,'R','9320') else 'LOSE', flush=True)\n"
                "t=time.time()\nwhile not os.path.exists(%r) and time.time()-t<60: time.sleep(0.01)\n"
                % (str(COCKPIT), str(go), str(d), str(end)))
        ps = [subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, text=True) for _ in range(n)]
        time.sleep(0.8)
        go.write_text("1")
        outs = [p.stdout.readline().strip() for p in ps]
        end.write_text("1")
        for p in ps:
            p.communicate(timeout=60)
        go.unlink()
        end.unlink()
        return outs, d

    def test_u7_concurrent_same_key(self):
        outs, d = self._race(12)
        self.assertEqual(outs.count("WIN"), 1, outs)
        # 主程序拿著 ⇒ 另一個程序拿不到
        mine = G.claim(str(d), "S", "9320")
        self.assertIsNotNone(mine)
        code = "import sys;sys.path.insert(0,%r);import gameworld as G;print(G.claim(%r,'S','9320'))" % (str(COCKPIT), str(d))
        try:
            self.assertEqual(subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                                            timeout=60).stdout.strip(), "None")
        finally:
            G.finish(mine, False)

    def _child(self, who, req, stage=None, sig=None, block_plugin=False):
        """真程序跑 open_lobby;stage 指定的障礙點寫 <sig>.held 再等 <sig>.go。block_plugin:停在外掛呼叫裡。"""
        calls = self.d / "calls"
        held = str(self.d / (sig + ".held")) if sig else ""
        go = str(self.d / (sig + ".go")) if sig else ""
        code = (
            "import sys,os,json,time,subprocess\nsys.path.insert(0,%(c)r)\nimport gameworld as G\n"
            "def wait():\n"
            "    open(%(held)r,'w').write(str(os.getpid()))\n"
            "    t=time.time()\n"
            "    while not os.path.exists(%(go)r) and time.time()-t<60: time.sleep(0.01)\n"
            "def run(argv, **kw):\n"
            "    if argv[1:] == ['room','open','--help']:\n"
            "        return subprocess.CompletedProcess(argv, 0, '--no-browser --switch', '')\n"
            "    if argv[1:3] == ['room','open']:\n"
            "        open(%(calls)r,'a').write(%(who)r + '\\n')\n"
            "        if %(block)r: wait()\n"
            "        return subprocess.CompletedProcess(argv, 0, json.dumps({'ok':True,'result':'switched','cabin':'9320',"
            "'url':'http://127.0.0.1:8771/'}), '')\n"
            "    return subprocess.CompletedProcess(argv, 0, '', '')\n"
            "def pause(st):\n"
            "    if st == %(stage)r: wait()\n"
            "print(G.open_lobby('9320', %(req)r, %(home)r, '', env={'EZ2_AIB_STATE_ROOT': %(root)r, 'AIB_NO_BROWSER': '1'},"
            " run=run, find=lambda c: (%(script)r,'captain-room','ok'), pause=pause), flush=True)\n"
        ) % {"c": str(COCKPIT), "calls": str(calls), "who": who, "held": held, "go": go, "stage": stage or "",
             "block": bool(block_plugin), "req": P + req, "home": str(self.home), "root": str(self.d / "root"),
             "script": str(self.script)}
        return subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _calls(self):
        f = self.d / "calls"
        return f.read_text().split() if f.exists() else []

    def _last(self, p):
        out, err = p.communicate(timeout=60)
        return (out.strip().splitlines() or ["<none>"])[-1], out + err

    def test_u7b_gap_between_check_and_lock(self):
        # 空檔:B 看過 .done(沒有)、還沒開鎖檔 → A 整個做完(寫 .done、刪鎖檔、放鎖)→ B 繼續 ⇒ B 要 dedup
        b = self._child("B", "GAP", stage="checked", sig="b")
        self.assertTrue(wait_until(lambda: (self.d / "b.held").exists(), 30), "B 沒走到障礙點")
        a = self._child("A", "GAP")
        ra, oa = self._last(a)
        (self.d / "b.go").write_text("1")
        rb, ob = self._last(b)
        self.assertEqual(ra, "switched", oa)
        self.assertEqual(rb, "dedup", ob)
        self.assertEqual(self._calls(), ["A"], "只有 A 叫外掛")

    def test_u8_sigkill_releases(self):
        import signal
        a = self._child("A", "KILL", stage="held", sig="a")
        self.assertTrue(wait_until(lambda: (self.d / "a.held").exists(), 30), "A 沒拿到鎖")
        d = self.home / "cockpit" / "gameworld-req"
        self.assertIsNone(G.claim(str(d), P + "KILL", "9320"), "A 活著時拿不到")
        os.kill(a.pid, signal.SIGKILL)
        a.communicate(timeout=60)
        t0 = time.time()
        cl = G.claim(str(d), P + "KILL", "9320")
        self.assertIsNotNone(cl, "A 死了鎖要馬上放掉")
        self.assertLess(time.time() - t0, 2, "不靠逾時")
        G.finish(cl, False)
        self.assertEqual((self.home / "cockpit" / "gameworld.stat.reclaimed").read_text(), "1")

    def test_u8b_live_owner_never_taken(self):
        # 擁有者活著、停在拿鎖後叫外掛前(停多久都一樣;20 秒 > 舊版 15 秒門檻)、B 嘗試 ⇒ 只有 A 叫外掛
        a = self._child("A", "LIVE", stage="held", sig="a")
        self.assertTrue(wait_until(lambda: (self.d / "a.held").exists(), 30), "A 沒拿到鎖")
        b = self._child("B", "LIVE")
        rb, ob = self._last(b)
        (self.d / "a.go").write_text("1")
        ra, oa = self._last(a)
        self.assertEqual(rb, "dedup", ob)
        self.assertEqual(ra, "switched", oa)
        self.assertEqual(self._calls(), ["A"], "只有一方叫外掛")

    def test_u8c_owner_inside_plugin_call(self):
        # 檢查到副作用之間:A 停在外掛呼叫裡(已經在做副作用)、B 嘗試 ⇒ B dedup;A 收尾後只有 A 叫過
        a = self._child("A", "MID", sig="a", block_plugin=True)
        self.assertTrue(wait_until(lambda: (self.d / "a.held").exists(), 30), "A 沒走到外掛呼叫")
        b = self._child("B", "MID")
        rb, ob = self._last(b)
        (self.d / "a.go").write_text("1")
        ra, oa = self._last(a)
        self.assertEqual(rb, "dedup", ob)
        self.assertEqual(ra, "switched", oa)
        self.assertEqual(self._calls(), ["A"])

    def test_u8d_stale_inode(self):
        # B 開了鎖檔還沒拿鎖;A 失敗收尾(刪鎖檔、不寫 .done);C 建新鎖檔拿著 ⇒ B 拿到舊檔的鎖也不能做
        d = self.home / "cockpit" / "gameworld-req"
        a = G.claim(str(d), P + "INO", "9320")
        self.assertIsNotNone(a)
        b = self._child("B", "INO", stage="opened", sig="b")
        self.assertTrue(wait_until(lambda: (self.d / "b.held").exists(), 30), "B 沒走到障礙點")
        G.finish(a, False)
        c = G.claim(str(d), P + "INO", "9320")
        self.assertIsNotNone(c, "C 要拿到新鎖檔")
        try:
            (self.d / "b.go").write_text("1")
            rb, ob = self._last(b)
            self.assertEqual(rb, "dedup", ob)
            self.assertEqual(self._calls(), [], "B 不能叫外掛")
        finally:
            G.finish(c, False)

    def test_u9_concurrent_reclaim(self):
        def prep(d):                                    # 崩潰留下的鎖檔(有內容、沒人拿著)
            (d / (G._key("R", "9320") + ".lock")).write_text(json.dumps({"pid": 999999, "at": time.time() - 30}))
        outs, d = self._race(12, prep)
        self.assertEqual(outs.count("WIN"), 1, outs)


class Misc(Base):
    def test_u10_check(self):
        import io
        for help_text, rc, word in ((HELP_ANCIENT, 3, "太舊"), (HELP_OLD, 0, "實際艙未知"), (HELP_NEW, 0, None)):
            with self.subTest(help_text):
                out = io.StringIO()
                root = self.d / ("r-" + str(rc) + str(word))
                self.assertEqual(G.check(out, {"EZ2_AIB_STATE_ROOT": str(root)}, Fake(help_text=help_text), self.find), rc)
                if word:
                    self.assertIn(word, out.getvalue())
                else:
                    self.assertEqual(out.getvalue(), "")
        out = io.StringIO()
        self.assertEqual(G.check(out, self.env, Fake(), lambda c: (None, None, "no-plugin")), 3)
        self.assertIn("艦長室外掛", out.getvalue())

    def test_u11_dict(self):
        keys = ("missing", "too_old", "no_switch", "no_attach", "unknown_cabin", "legacy_opened", "open_failed",
                "start_failed", "not_switched", "switch_unknown", "state_unwritable")
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertTrue(d.get("aib.gameworld." + k), (loc, k))
            self.assertIn("{target}", d["aib.gameworld.not_switched"])
            self.assertIn("{actual}", d["aib.gameworld.not_switched"])
            self.assertIn("{url}", d["aib.gameworld.open_failed"])
            self.assertIn("{target}", d["aib.gameworld.switch_unknown"])
            self.assertIn("[gameworld]", d["aib.help.usage"], loc)

    def test_u12_wiring(self):
        src = AIB.read_text(encoding="utf-8")
        self.assertLess(src.index("\naib_gameworld_check\n_pf_pid=\"\""), src.index("aib_post_frame_work >>\"$_pf_log\""))
        i = src.index('if [ -n "$BATCH_COUNT" ]; then\n  # gameworld')
        self.assertLess(i, src.index('exec bash "$HERE/seats_batch.sh" "${BATCH_COUNT}sits"'))
        self.assertIn('"$HERE/gameworld.py" open --cabin "${EZ2_AIB_INSTANCE##*-}"', src)
        b = (COCKPIT / "seats_batch.sh").read_text(encoding="utf-8")
        self.assertIn("\nunset AIB_SITS_GAMEWORLD_REQ\n", b, "逐席呼叫的 aib 不能繼承請求 ID")
        self.assertLess(b.index("\nunset AIB_SITS_GAMEWORLD_REQ\n"), b.index('"$AIB" "$prov" "$mode" "$role"'))
        self.assertLess(b.index('"$HERE/seats_report.py" --table'), b.index('"$HERE/gameworld.py" open'))

    def test_u13_no_attach(self):
        src = AIB.read_text(encoding="utf-8")
        m = re.search(r"\naib_gameworld_check\(\) \{\n.*?\n\}\n", src, re.S)
        self.assertIsNotNone(m)
        stub = self.d / "py"
        stub.write_text("#!/bin/sh\necho CALLED >> %s/called\nexit 0\n" % self.d)
        stub.chmod(0o755)
        for var in ("AIB_NO_ATTACH", "AIB_NO_CLIENT_SWITCH"):
            with self.subTest(var):
                prog = ("t() { echo \"T:$1\"; }\nDRY=0\nT_PY3=%s\nHERE=/nonexistent\nGAMEWORLD_REQ=abc\n%s\n"
                        "aib_gameworld_check\necho \"req=[$GAMEWORLD_REQ]\"\n" % (stub, m.group(0)))
                p = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, env={var: "1", "PATH": "/usr/bin:/bin"})
                self.assertIn("req=[]", p.stdout, p.stdout + p.stderr)
                self.assertIn("T:aib.gameworld.no_attach", p.stderr)
                self.assertFalse((self.d / "called").exists(), "代開不該叫 python")


KNIVES = [
    ("KG1", 'while [ "${#POS[@]}" -ge 4 ]; do\n    case "${POS[${#POS[@]}-1]}" in',
     'while [ "${#POS[@]}" -ge 3 ]; do\n    case "${POS[${#POS[@]}-1]}" in', "src/cockpit/aib", "Args.test_g3_three_words_not_stripped"),
    ("KG2", "    if got != cabin:\n", "    if False:\n", "src/cockpit/gameworld.py", "Interface.test_u3_no_false_success"),
    ("KG3", "        if _settled(done):\n            os.close(fd)\n            why.append(\"done\" if done.exists() else \"unknown\")\n            return None                                   # 空檔裡前一位做完了(或留下未知)\n",
     "", "src/cockpit/gameworld.py", "Dedup.test_u7b_gap_between_check_and_lock"),
    ("KG4", '(r["result"], target or "-", r["cabin"] or "unknown", how or "-", o or "-"))',
     '(r["result"], target or "-", r["cabin"] or "unknown", how or "-", r.get("url") or "-"))',
     "src/cockpit/gameworld.py", "Interface.test_u4_url_origin_only"),
    ("KG5", "st.st_ino, st.st_size, st.st_mtime_ns)", "st.st_ino, 0, 0)", "src/cockpit/gameworld.py",
     "Interface.test_u5_capability_cache"),
    ("KG8", "        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n", "        pass\n",
     "src/cockpit/gameworld.py", "Dedup.test_u8b_live_owner_never_taken"),
    ("KG9", "            same = os.fstat(fd).st_ino == os.stat(str(lk)).st_ino\n", "            same = True\n",
     "src/cockpit/gameworld.py", "Dedup.test_u8d_stale_inode"),
    ("KG10", "        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n", "        pass\n",
     "src/cockpit/gameworld.py", "Dedup.test_u8c_owner_inside_plugin_call"),
    ('KG11', '    if (r.returncode != 0 or d.get("ok") is not True or res not in OK_RESULTS) and d.get("post_unconfirmed") is True:\n',
     '    if False:\n', "src/cockpit/gameworld.py", "Dedup.test_u14_unknown_kept"),
    ('KG12', '        finish(cl, False, unknown=True)\n',
     '        finish(cl, False)\n', "src/cockpit/gameworld.py", "Dedup.test_u14_unknown_kept"),
    ('KG13', '    return done.exists() or _unknown(done).exists()\n',
     '    return done.exists()\n', "src/cockpit/gameworld.py", "Dedup.test_u14_unknown_kept"),
    ('KG14', '"result": "unknown" if switch else "timeout", "cabin": None, "url": None, "why": "core-timeout"}',
     '"result": "timeout", "cabin": None, "url": None, "why": "core-timeout"}', "src/cockpit/gameworld.py", "Dedup.test_u14_unknown_kept"),
    ('KG15', '    if switch and not arm(cl):',
     '    if switch and not (arm(cl) or True):', "src/cockpit/gameworld.py", "Dedup.test_u15_marker_write_fails"),
    ('KG16', '        finish(cl, False, unknown=cl.armed)',
     '        finish(cl, False)', "src/cockpit/gameworld.py", "Dedup.test_u15_marker_write_fails"),
    ('KG17', '            try:\n                _touch(cl.done)\n                clear = cl.armed\n',
     '            clear = cl.armed\n            try:\n                _touch(cl.done)\n', "src/cockpit/gameworld.py", "Dedup.test_u15_marker_write_fails"),
    ('KG18', '        if fd is not None and not _try_lock(fd):\n            return',
     '        if False:\n            return', "src/cockpit/gameworld.py", "Dedup.test_u16_gc_and_clear_rules"),
    ('KG19', '        if n.endswith(".unknown"):\n            _gc_unknown(d, n, now)                        # 未知標記另外收:不計入筆數上限、持鎖中不動\n            continue\n        if not (n.endswith(".done") or n.endswith(".lock")):',
     '        if not (n.endswith(".done") or n.endswith(".lock") or n.endswith(".unknown")):', "src/cockpit/gameworld.py", "Dedup.test_u16_gc_and_clear_rules"),
    ('KG20', 'CLEAR_WHY = ("spawn",)',
     'CLEAR_WHY = ("spawn", "cabin-mismatch", "bad-url")', "src/cockpit/gameworld.py", "Dedup.test_u16_gc_and_clear_rules"),
    ('KG22', 'CLEAR_WHY = ("spawn",)',
     'CLEAR_WHY = ("spawn", "plugin")', "src/cockpit/gameworld.py", "Dedup.test_u17_plugin_failure_needs_sent_false"),
    ('KG23', '(r["why"] == "plugin" and r.get("sent") is False)',
     '(r["why"] == "plugin" and not r.get("sent"))', "src/cockpit/gameworld.py", "Dedup.test_u17_plugin_failure_needs_sent_false"),
    ('KG24', '    if not req_fresh(req, now()):\n',
     '    if False:\n', "src/cockpit/gameworld.py", "Dedup.test_u18_stale_request_never_calls_plugin"),
    ('KG25', '    return -REQ_SKEW_S <= age <= REQ_MAX_AGE_S\n',
     '    return -REQ_SKEW_S <= age\n', "src/cockpit/gameworld.py", "Dedup.test_u18_stale_request_never_calls_plugin"),
    ('KG21', 'unknown=cl.armed and not r["ok"]',
     'unknown=not r["ok"]', "src/cockpit/gameworld.py", "Dedup.test_u16_gc_and_clear_rules"),
    ("KG6", "\nunset AIB_SITS_GAMEWORLD_REQ\n", "\n", "src/cockpit/seats_batch.sh", "Misc.test_u12_wiring"),
    ("KG7", '  if [ "${AIB_NO_ATTACH:-0}" = "1" ] || [ "${AIB_NO_CLIENT_SWITCH:-0}" = "1" ]; then\n    printf \'aib: %s\\n\' "$(t aib.gameworld.no_attach)"',
     '  if false; then\n    printf \'aib: %s\\n\' "$(t aib.gameworld.no_attach)"', "src/cockpit/aib", "Misc.test_u13_no_attach"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, needle, repl, rel, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(REPO, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=80, timeout=300)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out[-1500:]))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out[-1500:]))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out[-1500:]))


if __name__ == "__main__":
    unittest.main()
