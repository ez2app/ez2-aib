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
去重(同鍵原子領取)
  U6 同請求同艙第二次 ⇒ dedup、外掛只叫一次;不同艙 / 新請求照叫;失敗後同請求重試會叫
  U7 並發:12 個程序同時搶同一鍵 ⇒ 只有一個拿到;主程序拿著時另一個程序拿不到
  U7b 障礙:第二人過了檢查(看不到第一人的領取)直接建同一代 ⇒ O_EXCL 擋下
  U8 崩潰殘留:20 秒前、擁有者已死 ⇒ 接手、reclaimed +1;3 秒前、擁有者活著 ⇒ 不接手
  U9 12 個程序同時接手同一個卡住的領取 ⇒ 只有一個拿到
其他
  U10 check:沒裝 / 太舊 ⇒ rc 3 + 對的那句;沒 --switch ⇒ rc 0 + 提示
  U11 四語字典鍵齊、用法有 [gameworld]
  U12 aib 的前景檢查在開窗收尾段之前、批次 exec 之前;seats_batch 在逐席呼叫前取走並 unset 請求 ID
  U13 代開(AIB_NO_ATTACH / AIB_NO_CLIENT_SWITCH)⇒ 不叫 python、清掉請求、印代開那句
刀(複本):KG1 3 個字也剝 ⇒ G3 紅;KG2 拿掉 cabin == 目標 ⇒ U3 紅;KG3 領取不用 O_EXCL ⇒ U7b 紅;
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

    def lobby(self, fake, cabin="9320", req="r1"):
        import io
        out = io.StringIO()
        r = G.open_lobby(cabin, req, str(self.home), "aib-x", out=out, env=self.env, run=fake, find=self.find)
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
        bad = Fake(result={"ok": False, "result": "error"})
        self.assertEqual(self.lobby(bad, req="C")[0], "error")
        f4 = self.ok()
        self.assertEqual(self.lobby(f4, req="C")[0], "switched", "失敗後同請求重試會叫")
        self.assertEqual(len(f4.opens()), 1)

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
        cl = list(d.glob("*.claim.*"))
        self.assertEqual(len(cl), 1)
        # 主程序拿著(活著、剛領)⇒ 另一個程序拿不到
        os.unlink(str(cl[0]))
        mine = G.claim(str(d), "S", "9320")
        self.assertIsNotNone(mine)
        code = "import sys;sys.path.insert(0,%r);import gameworld as G;print(G.claim(%r,'S','9320'))" % (str(COCKPIT), str(d))
        self.assertEqual(subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True).stdout.strip(), "None")

    def test_u7b_barrier_check_then_create(self):
        # 障礙:第二個程序在「檢查」時看不到第一個人的領取(模擬兩人同時過了檢查),直接去建同一代 ⇒ 只能靠 O_EXCL 擋下
        d = self.home / "cockpit" / "gameworld-req"
        self.assertIsNotNone(G.claim(str(d), "B", "9320"))
        code = ("import sys;sys.path.insert(0,%r);import gameworld as G\n"
                "real=G._gens;n=[0]\n"
                "def first_blind(d,k):\n  n[0]+=1\n  return [] if n[0]==1 else real(d,k)\n"
                "G._gens=first_blind\nprint(G.claim(%r,'B','9320'))\n" % (str(COCKPIT), str(d)))
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "None", "過了檢查的第二人也拿到領取\n" + r.stdout + r.stderr)

    def _dead_pid(self):
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def test_u8_crash_residue(self):
        d = self.home / "cockpit" / "gameworld-req"
        d.mkdir(parents=True, exist_ok=True)
        k = G._key("R", "9320")
        (d / (k + ".claim.1")).write_text(json.dumps({"pid": self._dead_pid(), "at": time.time() - 20}))
        self.assertIsNotNone(G.claim(str(d), "R", "9320"), "卡住的要被接手")
        self.assertEqual((self.home / "cockpit" / "gameworld.stat.reclaimed").read_text(), "1")
        k2 = G._key("R2", "9320")
        (d / (k2 + ".claim.1")).write_text(json.dumps({"pid": os.getpid(), "at": time.time() - 3}))
        self.assertIsNone(G.claim(str(d), "R2", "9320"), "活著、剛領的不接手")

    def test_u9_concurrent_reclaim(self):
        dead = self._dead_pid()

        def prep(d):
            (d / (G._key("R", "9320") + ".claim.1")).write_text(json.dumps({"pid": dead, "at": time.time() - 30}))
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
                "start_failed", "not_switched")
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k in keys:
                self.assertTrue(d.get("aib.gameworld." + k), (loc, k))
            self.assertIn("{target}", d["aib.gameworld.not_switched"])
            self.assertIn("{actual}", d["aib.gameworld.not_switched"])
            self.assertIn("{url}", d["aib.gameworld.open_failed"])
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
    ("KG3", "os.O_CREAT | os.O_EXCL | os.O_WRONLY", "os.O_CREAT | os.O_WRONLY", "src/cockpit/gameworld.py",
     "Dedup.test_u7b_barrier_check_then_create"),
    ("KG4", '(r["result"], target or "-", r["cabin"] or "unknown", how or "-", o or "-"))',
     '(r["result"], target or "-", r["cabin"] or "unknown", how or "-", r.get("url") or "-"))',
     "src/cockpit/gameworld.py", "Interface.test_u4_url_origin_only"),
    ("KG5", "st.st_ino, st.st_size, st.st_mtime_ns)", "st.st_ino, 0, 0)", "src/cockpit/gameworld.py",
     "Interface.test_u5_capability_cache"),
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
