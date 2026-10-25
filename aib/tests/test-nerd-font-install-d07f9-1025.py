#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""安裝 / 更新 aib 時一併補好 AI 符號字型(10-25 @ae6a1 fishmac 實跑建議;Andy「預設在安裝或更新 aib 時一併優化」)。

N1 沒有字型 ⇒ 下載(假的)、驗 sha256、NF / NFM 各四個字重放進 ~/Library/Fonts、記狀態;再跑 ⇒ already、不再下載
N2 sha 不符 / 下載失敗 / 壓縮檔少一個字重 ⇒ 一個檔都不放(不留半套);下載總時限、搬檔中途失敗也不留(#361 r1 M2 / L4)
N3 舊版字型(沒有 U+EC82)⇒ 不算有,照裝新版;非 Mac ⇒ 不下載
N4 改終端機:畫不出 ⇒ 先備份偏好檔、記原字型、改「預設」設定檔(不寫死 Basic);--mono ⇒ NFM;畫得出 ⇒ 不改;
   備份失敗 ⇒ 不改;restore ⇒ 改回原字型、清狀態;之後被手動改過 ⇒ 不動(--force 才改);改完徽章快取作廢;
   osascript 成功才記狀態;備份只留最近幾份(#361 r1 L5 / L6)
N5 auto:沒終端機 / 沒 --ask ⇒ 只印一行步驟、不改;只有 y / m 才改,Enter / 打錯字 ⇒ 不改並記住;EOF ⇒ 不改不記;AIB_FONT_AUTO=0 ⇒ 什麼都不做
N6 四語字典鍵齊、佔位符一致;install.sh 有掛(非 Mac / AIB_FONT_AUTO=0 略過、失敗不擋);aib 派 fonts
刀(複本):K1 不驗 sha ⇒ N2 紅;K2 沒終端機也問 ⇒ N5 紅;K3 預設是改 ⇒ N5c 紅;K4 失敗不清 ⇒ N2b 紅
"""
import hashlib
import io
import json
import os
import re
import struct
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "bootstrap", REPO / "src" / "cockpit" / "render", HERE):
    sys.path.insert(0, str(_p))
import nerd_font as N  # noqa: E402
import engine_badge_mode as M  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


def ttf(full=True):
    """最小 TTF:只有 cmap(format 12)。full ⇒ 涵蓋 U+0020–U+FFFFF;否則只有 ASCII(=舊版、沒有 U+EC82)。"""
    end = 0xFFFFF if full else 0x7E
    sub = struct.pack(">HHIII", 12, 0, 16 + 12, 0, 1) + struct.pack(">III", 0x20, end, 1)
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 10, 12) + sub
    return struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0) + b"cmap" + struct.pack(">III", 0, 28, len(cmap)) + cmap


WANT = ["%s-%s.ttf" % (N.FAMILIES[f], s) for f in ("nf", "nfm") for s in N.STYLES]


def archive(names=WANT, full=True):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for n in list(names) + ["README.md"]:
            data = ttf(full) if n.endswith(".ttf") else b"readme"
            ti = tarfile.TarInfo(n)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def term(covered, profile="Pro", font="SFMono-Regular"):
    def run(argv):
        n = len(argv) - 5
        return 0, json.dumps([{"key": "Default Window Settings", "profile": profile, "font": font,
                               "covered": [covered] * n}])
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-nf-")
        self.home = Path(self.td.name)
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.home / "st"),
                    "EZ2_AIB_OUTER_TERM": "Apple_Terminal", "EZ2_AIB_LOCALE": "zh-TW"}
        self.data = archive()
        self.fetches = []

    def tearDown(self):
        self.td.cleanup()

    def fetch(self, data=None):
        def f():
            self.fetches.append(1)
            return self.data if data is None else data
        return f

    def install(self, data=None, sha=None, **kw):
        d = self.data if data is None else data
        return N.install(self.env, fetch=self.fetch(d), sha256=sha or hashlib.sha256(d).hexdigest(),
                         platform=kw.get("platform", "darwin"))

    def fonts(self):
        p = self.home / "Library" / "Fonts"
        return sorted(x.name for x in p.iterdir()) if p.is_dir() else []

    def put_font(self, full=True):
        p = self.home / "Library" / "Fonts"
        p.mkdir(parents=True, exist_ok=True)
        (p / WANT[0]).write_bytes(ttf(full))


class Install(Base):
    def test_n1_install_then_already(self):
        self.assertEqual(self.install(), ("installed", None))
        self.assertEqual(self.fonts(), sorted(WANT))
        self.assertEqual(N.read_state(self.env)["installed"]["version"], "v3.5.1")
        self.assertEqual(self.install(), ("already", None))
        self.assertEqual(len(self.fetches), 1, "已經有了還在下載")
        self.assertTrue(N.status(self.env, platform="darwin", terminal=False)["font_ok"])

    def test_n2_nothing_half_installed(self):
        try:
            self.assertEqual(self.install(sha="0" * 64)[0], "sha_mismatch")
            self.assertEqual(self.fonts(), [])

            def boom():
                raise OSError("offline")
            self.assertEqual(N.install(self.env, fetch=boom, platform="darwin")[0], "download_failed")
            self.assertEqual(self.fonts(), [])
            short = archive(WANT[:-1])
            self.assertEqual(self.install(data=short), ("bad_archive", WANT[-1]))
            self.assertEqual(self.fonts(), [], "少一個字重還是放了一半")
            self.assertEqual(self.install(data=b"not xz")[0], "bad_archive")
            self.assertEqual(self.fonts(), [])
            self.assertNotIn("installed", N.read_state(self.env))
        except (KeyError, TypeError, AttributeError) as exc:
            self.fail("N2 例外:%r" % exc)

    def test_n2b_timeout_and_partial_write(self):
        """#361 r1 M2:下載有總時限;L4:搬檔中途失敗不留半套也不留暫存檔"""
        try:
            class Slow:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, n):
                    return b"x" * 10
            t = [0.0]

            def clock():
                t[0] += 50
                return t[0]
            with self.assertRaises(TimeoutError):
                N._fetch(opener=lambda url, timeout: Slow(), total=120, clock=clock)

            def slow():
                raise TimeoutError("total")
            self.assertEqual(N.install(self.env, fetch=slow, platform="darwin"), ("timeout", str(N.NF_TOTAL_S)))
            self.assertEqual(self.fonts(), [])
            real, n = N.os.replace, [0]

            def flaky(a, b):
                n[0] += 1
                if n[0] == 3:
                    raise OSError("disk")
                return real(a, b)
            N.os.replace = flaky
            try:
                self.assertEqual(self.install()[0], "write_failed")
            finally:
                N.os.replace = real
            self.assertEqual(self.fonts(), [], "搬到一半失敗還留檔:%s" % self.fonts())
        except (KeyError, TypeError, AttributeError) as exc:
            self.fail("N2b 例外:%r" % exc)

    def test_n3_old_font_and_platform(self):
        self.put_font(full=False)
        self.assertFalse(N.status(self.env, platform="darwin", terminal=False)["font_ok"])
        self.assertEqual(self.install()[0], "installed", "舊版字型(沒有 U+EC82)不能算有")
        self.fetches.clear()
        self.assertEqual(self.install(platform="linux")[0], "skipped_platform")
        self.assertEqual(self.fetches, [])


class Terminal(Base):
    def setUp(self):
        super().setUp()
        self.put_font()
        self.calls = []
        prefs = self.home / "Library" / "Preferences"
        prefs.mkdir(parents=True)
        self.plist = prefs / "com.apple.Terminal.plist"
        self.plist.write_bytes(b"plist")

    def setter(self, rc=0):
        def s(argv):
            self.calls.append(list(argv))
            return rc
        return s

    def test_n4_change_backup_restore(self):
        cache = Path(M._cache_path(self.env))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("{}")
        code, detail = N.set_terminal_font(self.env, runner=term(False), setter=self.setter(), platform="darwin")
        self.assertEqual((code, detail), ("changed", "Pro"))
        self.assertEqual(self.calls, [["Pro", "JetBrainsMonoNF-Regular"]], "要改「預設」設定檔,不能寫死 Basic")
        baks = list(self.plist.parent.glob("com.apple.Terminal.plist.bak-font-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), b"plist")
        st = N.read_state(self.env)
        self.assertEqual(st["terminal_original"], {"Pro": "SFMono-Regular"})
        self.assertFalse(cache.exists(), "改完徽章快取要作廢")
        # 再改一次(NFM):原字型不能被蓋成 NF
        N.set_terminal_font(self.env, mono=True, runner=term(False, font="JetBrainsMonoNF-Regular"),
                            setter=self.setter(), platform="darwin")
        self.assertEqual(self.calls[-1], ["Pro", "JetBrainsMonoNFM-Regular"])
        self.assertEqual(N.read_state(self.env)["terminal_original"], {"Pro": "SFMono-Regular"})
        # 使用者後來自己改成 Menlo ⇒ 不動、提示;--force 才改(#361 r1 L5)
        n = len(self.calls)
        self.assertEqual(N.restore_terminal_font(self.env, setter=self.setter(), getter=lambda p: "Menlo"),
                         ("user_changed", "Pro"))
        self.assertEqual(len(self.calls), n, "使用者改過的字型被蓋回")
        self.assertEqual(N.restore_terminal_font(self.env, setter=self.setter(), getter=lambda p: "JetBrainsMonoNFM-Regular"),
                         ("restored", "Pro"))
        self.assertEqual(self.calls[-1], ["Pro", "SFMono-Regular"])
        self.assertNotIn("terminal_original", N.read_state(self.env))
        self.assertEqual(N.restore_terminal_font(self.env, setter=self.setter(), getter=lambda p: "x")[0], "nothing")

    def test_n4c_state_only_after_success_and_backup_cap(self):
        """#361 r1 L6:osascript 失敗不記「改過」、不吃掉拒絕;L5:備份只留最近 BACKUP_KEEP 份"""
        try:
            N.write_state(self.env, {"terminal_declined": 1})
            self.assertEqual(N.set_terminal_font(self.env, runner=term(False), setter=self.setter(rc=1),
                                                 platform="darwin"), ("failed", "osascript:1"))
            st = N.read_state(self.env)
            self.assertNotIn("terminal_changed", st)
            self.assertNotIn("terminal_original", st)
            self.assertEqual(st.get("terminal_declined"), 1, "失敗還吃掉之前的拒絕")
            for i in range(5):
                (self.plist.parent / ("com.apple.Terminal.plist.bak-font-2026010%d-000000" % i)).write_bytes(b"o")
            N.set_terminal_font(self.env, runner=term(False), setter=self.setter(), platform="darwin")
            baks = sorted(self.plist.parent.glob("com.apple.Terminal.plist.bak-font-*"))
            self.assertEqual(len(baks), N.BACKUP_KEEP)
            self.assertEqual(baks[-1].read_bytes(), b"plist", "最新那份要留")
        except (KeyError, TypeError) as exc:
            self.fail("N4c 例外:%r" % exc)

    def test_n4b_not_needed_and_backup_fail(self):
        self.assertEqual(N.set_terminal_font(self.env, runner=term(True), setter=self.setter(), platform="darwin")[0],
                         "not_needed")
        env = dict(self.env, EZ2_AIB_OUTER_TERM="iTerm.app")
        self.assertEqual(N.set_terminal_font(env, runner=term(False), setter=self.setter(), platform="darwin")[0],
                         "not_apple_terminal")
        os.chmod(str(self.plist.parent), 0o500)
        try:
            code, detail = N.set_terminal_font(self.env, runner=term(False), setter=self.setter(), platform="darwin")
        finally:
            os.chmod(str(self.plist.parent), 0o700)
        self.assertEqual(code, "failed")
        self.assertTrue(detail.startswith("backup:"))
        self.assertEqual(self.calls, [], "備份不成還是改了")


class Auto(Terminal):
    def auto(self, answer="", ask=True, tty=True, env=None, eof=False):
        out = io.StringIO()
        rc = N.auto(env or self.env, ask=ask, inp=io.StringIO("" if eof else answer + "\n"), out=out, fetch=self.fetch(),
                    runner=term(False), setter=self.setter(), platform="darwin", isatty=tty)
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_n5_ask_decline_remember(self):
        try:
            o = self.auto(tty=False)
            self.assertIn("aib fonts terminal", o)
            self.assertEqual(self.calls, [], "沒終端機不能改")
            o = self.auto(ask=False)
            self.assertEqual(self.calls, [], "沒 --ask 不能改")
            o = self.auto("n")
            self.assertIn("不改", o)
            self.assertTrue(N.read_state(self.env).get("terminal_declined"))
            o = self.auto("y")                     # 拒絕過 ⇒ 不再問,只印一行
            self.assertNotIn("[y=", o)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.fetches, [], "字型已經有了還下載")
        except (KeyError, TypeError) as exc:
            self.fail("N5 例外:%r" % exc)

    def test_n5c_only_yes_changes(self):
        """#361 r1 L3:只認肯定字;Enter / 打錯字 ⇒ 不改(並記住回答過)"""
        for ans in ("", "yse", "ok"):
            N.write_state(self.env, {})
            self.auto(ans)
            self.assertEqual(self.calls, [], "回 %r 也改了" % ans)
            self.assertTrue(N.read_state(self.env).get("terminal_declined"), ans)

    def test_n5d_no_terminal_prints_nothing(self):
        """#361 r2 Nit:install.sh 接著要問 ⇒ 第一次 --no-terminal 不印提示、不改"""
        out = io.StringIO()
        N.auto(self.env, ask=False, inp=io.StringIO(""), out=out, fetch=self.fetch(), runner=term(False),
               setter=self.setter(), platform="darwin", isatty=True, do_terminal=False)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.calls, [])

    def test_n5b_enter_changes_and_off_switch(self):
        o = self.auto("", eof=True)              # 沒人回答(EOF)⇒ 不改、不記,下次再問
        self.assertEqual(self.calls, [])
        self.assertFalse(N.read_state(self.env).get("terminal_declined"))
        o = self.auto("y")
        self.assertIn("[y=", o)
        self.assertEqual(self.calls, [["Pro", "JetBrainsMonoNF-Regular"]])
        self.assertIn("JetBrainsMonoNF-Regular", o)
        self.calls.clear()
        self.auto("m", env=dict(self.env, AIB_FONT_AUTO="0"))
        self.assertEqual(self.calls, [])
        # 沒字型 + 下載失敗 ⇒ 印一行、不問、不改
        for x in (self.home / "Library" / "Fonts").iterdir():
            x.unlink()
        out = io.StringIO()
        N.auto(self.env, ask=True, inp=io.StringIO("\n"), out=out, fetch=lambda: (_ for _ in ()).throw(OSError()),
               runner=term(False), setter=self.setter(), platform="darwin", isatty=True)
        self.assertIn("download_failed", out.getvalue())
        self.assertEqual(self.calls, [])

    test_n4_change_backup_restore = None
    test_n4b_not_needed_and_backup_fail = None
    test_n4c_state_only_after_success_and_backup_cap = None


class Wiring(unittest.TestCase):
    def test_n6_i18n_and_hooks(self):
        keys = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            mine = {k: v for k, v in d.items() if k.startswith("aib.fonts.")}
            if keys is None:
                keys = mine
            self.assertEqual(set(mine), set(keys), lang)
            for k, v in mine.items():
                self.assertEqual(set(re.findall(r"\{\w+\}", v)), set(re.findall(r"\{\w+\}", keys[k])), (lang, k))
        src = (REPO / "src" / "bootstrap" / "nerd_font.py").read_text(encoding="utf-8")
        for k in set(re.findall(r'_T\("([\w.]+)"', src)) - {"code."}:
            self.assertIn("aib.fonts." + k, keys, k)
        for c in ("already", "installed", "skipped_platform", "download_failed", "sha_mismatch", "bad_archive",
                  "write_failed", "font_missing", "not_apple_terminal", "not_needed", "changed", "failed",
                  "restored", "nothing"):
            self.assertIn("aib.fonts.code." + c, keys, c)
        inst = (REPO.parent / "install.sh").read_text(encoding="utf-8")
        self.assertRegex(inst, r'if \[ "\$LINUX" != 1 \] && \[ "\$\{AIB_FONT_AUTO:-1\}" != 0 \]; then\n'
                               r'.*if \[ -t 1 \].*\n'
                               r'.*run_limited 300 "\$abin" fonts auto --no-terminal </dev/null\) \|\| true\n'
                               r'.*fonts auto --ask --no-install </dev/tty\) \|\| true\n'
                               r'.*else \(cd "\$DIR" && run_limited 300 "\$abin" fonts auto </dev/null\) \|\| true; fi')
        aib = (REPO / "src" / "cockpit" / "aib").read_text(encoding="utf-8")
        self.assertIn('exec "$T_PY3" "$AIB_SOURCE_ROOT/src/bootstrap/nerd_font.py" "$@"', aib)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/bootstrap/nerd_font.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, "-B", str(root / "tests" / name), cell], cwd=str(root), max_procs=40,
                            timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k1_no_sha_check(self):
        self._knife("    if hashlib.sha256(data).hexdigest() != sha256:\n", "    if False:\n",
                    "Install.test_n2_nothing_half_installed")

    def test_k2_asks_without_tty(self):
        self._knife("not (ask and tty)", "not ask", "Auto.test_n5_ask_decline_remember")

    def test_k3_default_is_change(self):
        self._knife("    if ans not in YES:", '    if ans in ("n", "no"):', "Auto.test_n5c_only_yes_changes")

    def test_k4_partial_left_behind(self):
        self._knife("            for x in tmps + moved:\n", "            for x in []:\n", "Install.test_n2b_timeout_and_partial_write")


if __name__ == "__main__":
    unittest.main()
