#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""💧 狀態列尾巴:mod 狀態段併進 CTX 那一行(10-28 Andy 工作單 S「這個 aib 太多行了,我希望縮成一行」)。

要的樣子:Opus 5.5 | @aeb40 | 💧 Ez2 CTX 10% [💧─────────] 100k/1M 未讀 dm 5 · 🌐 zh-TW

T1 組合:boards + languages 都在 = 「未讀 dm 5 · 🌐 zh-TW」;只有 languages = 「🌐 zh-TW」;只有 boards = 「未讀 dm 5」;都沒有 = 空
T2 判準不靠路徑:段過期(TTL)、檔裡的對話 id / mod 名對不上、壞 JSON ⇒ 不收;不在表上的 mod、壞對話 id ⇒ put 拒收
T3 消毒:控制字元、\\x1f、& \\、換行拿掉,一段最多 TEXT_MAX 字
T4 「畫出過」記號(#401 R1):mark_shown(畫出的 mod)之後那個 mod 的 put 回 shown=true,沒畫到的 mod 仍 false;
   沒有新東西不重寫;閒置多久都不翻回(不看時間);狀態列設定(settings.json statusLine)換掉 ⇒ false;沒記號 = false
S1 seat_now --statusline --rank-field --tail-field:第 5 欄 = 尾巴,並留記號;沒帶 --tail-field ⇒ 照舊 4 欄、不留記號(舊殼)
E1 狀態列腳本:第 5 欄接在 CTX 那一行最後面;沒有第 5 欄 ⇒ 跟現在一樣
E2 舊 seat_now 不認得 --tail-field ⇒ 先 grep 看出來、直接只帶 --rank-field(不多起一次失敗的 python;#401 R1),
   grep 說有但跑起來還是空 ⇒ 照舊退回 --rank-field;等級照畫、沒有尾巴
E3 艙內 CTX 那一行量不到不印 ⇒ 尾巴接到 📌 那一行(mod 已收掉自己那一行,不接就整段不見)
刀:拿掉腳本接尾巴那一行 ⇒ E1 紅;拿掉 tail_text 的 TTL 判斷 ⇒ T2 紅(交件時各下一次)。
全在暫存狀態根 / 暫存 HOME,不碰真 ~/.ez2-aib。
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))
import statusline_tail as ST  # noqa: E402

SCRIPT = ROOT / "deploy" / "statusline" / "claude" / "statusline-ez2-v215.sh"
BASH = os.environ.get("AIB_TEST_BASH") or "/bin/bash"
NODE = shutil.which("node")
SID = "11111111-2222-4333-8444-5555555fd787"
NOW = 1_800_000_000


class Tail(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.settings = os.path.join(self.root, "settings.json")
        self.set_statusline("/bin/bash $HOME/.claude/statusline-ez2-v215.sh")

    def tearDown(self):
        self._tmp.cleanup()

    def set_statusline(self, cmd):
        with open(self.settings, "w", encoding="utf-8") as fh:
            json.dump({"statusLine": {"type": "command", "command": cmd}}, fh)

    def shown(self, mod):
        return ST.is_shown(SID, mod, state_root=self.root, settings_path=self.settings)

    def mark(self, mods, sid=SID):
        return ST.mark_shown(sid, mods, state_root=self.root, settings_path=self.settings)

    def put(self, mod, text, now=NOW, sid=SID):
        return ST.put(mod, sid, text, state_root=self.root, now=now, settings_path=self.settings)

    def tail(self, now=NOW, sid=SID):
        return ST.tail_text(sid, state_root=self.root, now=now)

    def test_t1_combinations(self):
        self.assertEqual(self.tail(), "")
        self.put("aib-languages", "🌐 zh-TW")
        self.assertEqual(self.tail(), "🌐 zh-TW")
        self.put("aib-boards", "未讀 dm 5")
        self.assertEqual(self.tail(), "未讀 dm 5 · 🌐 zh-TW")       # 順序固定:dm 在前、語系在後
        self.put("aib-languages", None)
        self.assertEqual(self.tail(), "未讀 dm 5")
        self.assertEqual(self.tail(sid="other-session"), "")        # 別的對話看不到

    def test_t2_freshness_and_identity(self):
        self.put("aib-boards", "dm 0", now=NOW - ST.TTL_S - 1)
        self.put("aib-languages", "🌐 en", now=NOW)
        self.assertEqual(self.tail(), "🌐 en")                       # 過期的段不收(mod 停了)
        p = pathlib.Path(ST.tail_dir(self.root)) / ("%s.aib-languages.json" % SID)
        d = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps(dict(d, session="someone-else")), encoding="utf-8")
        self.assertEqual(self.tail(), "")                            # 檔裡的對話 id 對不上 ⇒ 不收
        p.write_text("{壞", encoding="utf-8")
        self.assertEqual(self.tail(), "")
        self.assertEqual(self.put("aib-evil", "x")["reason"], "unknown_mod")
        self.assertEqual(self.put("aib-boards", "x", sid="../../etc")["reason"], "bad_session")
        self.assertFalse(os.path.exists(os.path.join(ST.tail_dir(self.root), "%s.aib-evil.json" % SID)))

    def test_t3_sanitize(self):
        self.put("aib-boards", "未讀\x1b[31m dm\x1f 5 &\\\n 二行")
        t = self.tail()
        for bad in ("\x1b", "\x1f", "&", "\\", "\n"):
            self.assertNotIn(bad, t)
        self.assertTrue(t.startswith("未讀"))
        self.put("aib-boards", "長" * 200)
        self.assertEqual(len(self.tail()), ST.TEXT_MAX)

    def test_t4_shown_mark(self):
        self.assertFalse(self.put("aib-boards", "dm 0")["shown"])   # 狀態列還沒畫過 ⇒ mod 照舊自己一行
        self.assertTrue(self.mark(["aib-boards"]))
        self.assertTrue(self.put("aib-boards", "dm 0")["shown"])
        self.assertFalse(self.put("aib-languages", "🌐 zh-TW")["shown"])   # 還沒畫到含這段的那一次 ⇒ 不收(#401 R1 Low①)
        self.assertFalse(self.mark(["aib-boards"]))                           # 沒新東西不重寫
        self.assertTrue(self.mark(["aib-languages"]))
        self.assertTrue(self.shown("aib-boards") and self.shown("aib-languages"))   # 聯集
        # #401 R1 Medium:會員沒設 refreshInterval、閒置遠超過舊的 3 分鐘 ⇒ 狀態列不重跑,記號也不翻回
        idle = NOW + 24 * 3600
        old = os.path.getmtime(ST._shown_path(SID, self.root)) - 24 * 3600
        os.utime(ST._shown_path(SID, self.root), (old, old))                  # 記號檔也老了一天(不能靠檔案時間判)
        self.assertTrue(self.put("aib-boards", "dm 1", now=idle)["shown"])
        self.assertTrue(self.put("aib-languages", "🌐 zh-TW", now=idle)["shown"])
        # 換成自己的狀態列 ⇒ 指紋對不上 ⇒ mod 自己那一行回來;換回來再畫一次才收
        self.set_statusline("/usr/local/bin/my-statusline")
        self.assertFalse(self.put("aib-boards", "dm 1")["shown"])
        self.assertTrue(self.mark(["aib-boards"]))
        self.assertTrue(self.shown("aib-boards"))
        self.assertFalse(self.shown("aib-languages"))                         # 指紋換了 ⇒ 舊聯集不帶過來
        os.unlink(self.settings)
        self.assertFalse(self.shown("aib-boards"))
        self.assertFalse(self.mark("x/../y"))
        self.assertFalse(ST.mark_shown(SID, ["not-a-mod"], state_root=self.root, settings_path=self.settings))


class SeatNowField(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._env = dict(os.environ)
        os.environ["EZ2_AIB_STATE_ROOT"] = self.root
        os.environ["CLAUDE_CONFIG_DIR"] = self.root   # 指紋讀暫存的 settings.json(不存在 ⇒ "-"),不碰真 ~/.claude

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def run_now(self, *flags):
        import seat_now
        out = io.StringIO()
        rc = seat_now.main(["--statusline", *flags, "--session", SID], code="fd787", out=out, err=io.StringIO(),
                           state_root=self.root, gate_check=False)
        self.assertEqual(rc, 0)
        return out.getvalue().rstrip("\n").split("\x1f")

    def test_s1_fifth_field(self):
        ST.put("aib-boards", SID, "未讀 dm 5", state_root=self.root)
        ST.put("aib-languages", SID, "🌐 zh-TW", state_root=self.root)
        f = self.run_now("--rank-field", "--tail-field")
        self.assertEqual(len(f), 5)
        self.assertEqual((f[0], f[4]), ("fd787", "未讀 dm 5 · 🌐 zh-TW"))
        self.assertTrue(ST.is_shown(SID, "aib-boards", state_root=self.root))
        self.assertTrue(ST.is_shown(SID, "aib-languages", state_root=self.root))

    def test_s1_old_shell_no_field_no_mark(self):
        ST.put("aib-boards", SID, "未讀 dm 5", state_root=self.root)
        self.assertEqual(len(self.run_now("--rank-field")), 4)
        self.assertFalse(ST.is_shown(SID, "aib-boards", state_root=self.root))   # 舊殼不畫尾巴 ⇒ 不能叫 mod 收掉自己那一行


@unittest.skipUnless(NODE and os.path.exists("/usr/bin/python3"), "要 node 與 /usr/bin/python3")
class Script(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.home = self.d / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        shutil.copy(str(SCRIPT), str(self.claude / "statusline-ez2-v215.sh"))
        self.ctx("Opus 5.5 | 💧 Ez2 CTX 10% 100k/1M")
        self.aib = self.d / "aib"
        (self.aib / "src" / "cockpit").mkdir(parents=True)
        self.calls = self.d / "calls"

    def ctx(self, text):
        (self.claude / "statusline-ez2-ctx.js").write_text(
            "process.stdin.resume();process.stdin.on('data',()=>{});"
            "process.stdin.on('end',()=>process.stdout.write(%s));" % json.dumps(text))

    def seat_now(self, tail=None, old=False):
        """假 seat_now:印「席位碼␟📌␟圖示␟等級[␟尾巴]」;old=True ⇒ 不認得 --tail-field(像舊版 argparse 退出)。"""
        src = ("import sys\nopen(%r,'a').write(' '.join(sys.argv[1:])+'\\n')\n" % str(self.calls)
               + ("if '--tail-field' in sys.argv: sys.exit(2)\n" if old else '# ap.add_argument("--tail-field", ...)\n')
               + "f=['fd787','NOW','LOGO','5⭐']\n"
               + ("if '--tail-field' in sys.argv: f.append(%r)\n" % tail if tail is not None else "")
               + "sys.stdout.write('\\x1f'.join(f))\n")
        (self.aib / "src" / "cockpit" / "seat_now.py").write_text(src, encoding="utf-8")

    def run_sl(self, cabin=False):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin:" + os.path.dirname(NODE), "EZ2_AIB_ROOT": str(self.aib),
               "EZ2_CTX_DIR": str(self.d / "ctx")}
        if cabin:
            env["EZ2_AIB_INSTANCE"] = "cab1"
        p = subprocess.run([BASH, str(self.claude / "statusline-ez2-v215.sh")],
                           input=json.dumps({"session_id": SID}).encode(), env=env, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode("utf-8")

    def test_e1_tail_appended(self):
        self.seat_now(tail="未讀 dm 5 · 🌐 zh-TW")
        self.assertEqual(self.run_sl().split("\n")[0],
                         "LOGO  Opus 5.5 | @fd787 5⭐ | 💧 Ez2 CTX 10% 100k/1M 未讀 dm 5 · 🌐 zh-TW")
        self.seat_now(tail="")
        self.assertEqual(self.run_sl().split("\n")[0], "LOGO  Opus 5.5 | @fd787 5⭐ | 💧 Ez2 CTX 10% 100k/1M")

    def test_e2_old_seat_now_falls_back(self):
        self.seat_now(old=True)
        self.assertEqual(self.run_sl().split("\n")[0], "LOGO  Opus 5.5 | @fd787 5⭐ | 💧 Ez2 CTX 10% 100k/1M")
        calls = self.calls.read_text().splitlines()
        self.assertEqual(len(calls), 1)                      # grep 先看出是舊版 ⇒ 不多起一次失敗的 python
        self.assertNotIn("--tail-field", calls[0])
        self.assertIn("--rank-field", calls[0])

    def test_e3_cabin_unmeasured_goes_to_now_line(self):
        self.seat_now(tail="未讀 dm 5 · 🌐 zh-TW")
        self.ctx("Opus 5.5 | 💧 Ez2 CTX ?")
        self.assertEqual(self.run_sl(cabin=True), "NOW · 未讀 dm 5 · 🌐 zh-TW")


if __name__ == "__main__":
    unittest.main()
