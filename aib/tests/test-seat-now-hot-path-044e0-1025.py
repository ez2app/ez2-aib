#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""seat_now 狀態列熱路徑(10-25 @044e0):Claude 狀態列每次刷新都跑一次 seat_now --statusline。
N1  有 TMUX_PANE:整支只問 tmux 一次(窗 id / socket / 寬一起問),席位碼照席位檔、寬照 tmux 給的
N2  seat_who.instance_of_socket 不再 import fleet_patrol(那一包要 20 毫秒,只為一個純函式)
N3  instance_of_hub 實作只有一份:fleet_patrol 的是 seats_dir_lib 的同一個物件,結果照舊
N4  i18n:主語系有這個鍵 ⇒ 不載 en(en 壞了也照常翻);缺鍵 ⇒ 才載 en 補上(en 壞 ⇒ 那時才 raise);主語系壞 ⇒ 一建就 raise
刀(複本 + run_guarded):K1 狀態列不帶一次問到的值 ⇒ N1 紅(tmux 3 次);K2 seat_who 改回 import fleet_patrol ⇒ N2 紅;
K3 en 改回一建就載 ⇒ N4 紅。
刀一律斷言「FAIL: <目標>」且沒有 ERROR。全在暫存 HOME / 狀態根,tmux 是 PATH 上的假檔。
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

ROOT = HERE.parent
COCKPIT = ROOT / "src" / "cockpit"
SID = "11111111-2222-4333-8444-5555555044e0"   # 末五碼 = 席位碼


class HotPath(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.state = self.d / "state"
        seats = self.state / "instances" / "cab1" / "seats"
        seats.mkdir(parents=True)
        (seats / "w5.json").write_text(json.dumps(
            {"schema": "aib-seat/v1", "window_id": "@5", "session_uuid": SID, "provider": "claude"}))
        b = self.d / "bin"
        b.mkdir()
        self.log = self.d / "tmux.calls"
        (b / "tmux").write_text(
            "#!/bin/sh\necho \"$*\" >> %s\n"
            "case \"$*\" in *pane_width*) printf '@5\\t/tmp/x/aihub-cab1\\t104\\n' ;;"
            " *socket_path*) printf '@5\\t/tmp/x/aihub-cab1\\n' ;; *) echo ;; esac\n" % self.log)
        (b / "tmux").chmod(0o755)
        self.env = {"HOME": str(self.d), "PATH": "%s:/usr/bin:/bin" % b, "TMUX_PANE": "%9",
                    "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_HOME": str(self.state),
                    "AIB_LOCALE": "zh-TW"}

    def run_sl(self):
        p = subprocess.run([sys.executable, "-B", str(COCKPIT / "seat_now.py"), "--statusline"],
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_n1_one_tmux_call(self):
        out = self.run_sl()
        self.assertTrue(out.startswith("044e0\x1f"), out)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("-t %9", calls[0])
        self.assertTrue((self.state / "state" / "seat-now" / "044e0.beat").exists(), "心跳沒寫")

    def test_n2_seat_who_skips_fleet_patrol(self):
        code = ("import sys; sys.path.insert(0, %r); import seat_who; "
                "print(seat_who.instance_of_socket('/tmp/x/aihub-cab1'), 'fleet_patrol' in sys.modules)" % str(COCKPIT))
        p = subprocess.run([sys.executable, "-B", "-c", code], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stdout.split(), ["cab1", "False"], p.stderr)

    def test_n3_single_impl(self):
        code = ("import sys; sys.path.insert(0, %r); import fleet_patrol as f, seats_dir_lib as s; "
                "print(f.instance_of_hub is s.instance_of_hub, f.instance_of_hub('aihub-13'), "
                "f.instance_of_hub('aihub'), f.instance_of_hub('aihub-a/b'))" % str(COCKPIT))
        p = subprocess.run([sys.executable, "-B", "-c", code], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stdout.split(), ["True", "13", "None", "None"], p.stderr)

    def test_n4_i18n_fallback_lazy(self):
        sys.path.insert(0, str(COCKPIT))
        import i18n_lib
        i18n_lib._KEYED_CACHE.clear()
        d = self.d / "i18n"
        d.mkdir()
        (d / "zh-TW.json").write_text(json.dumps({"aib.x.here": "這裡 {n}"}), encoding="utf-8")
        (d / "en.json").write_text("{ broken", encoding="utf-8")
        try:
            tr = i18n_lib.Translator(locale="zh-TW", i18n_dir=str(d))
        except i18n_lib.I18nError as e:
            self.fail("主語系有鍵,建 Translator 卻去載 en:%s" % e)
        self.assertEqual(tr.t("aib.x.here", {"n": 3}), "這裡 3")
        with self.assertRaises(i18n_lib.I18nError):
            tr.t("aib.x.missing")
        (d / "en.json").write_text(json.dumps({"aib.x.here": "here {n}", "aib.x.missing": "en only"}), encoding="utf-8")
        tr = i18n_lib.Translator(locale="zh-TW", i18n_dir=str(d))
        self.assertEqual(tr.t("aib.x.missing"), "en only")
        (d / "zh-TW.json").write_text("{ broken", encoding="utf-8")
        with self.assertRaises(i18n_lib.I18nError):
            i18n_lib.Translator(locale="zh-TW", i18n_dir=str(d))


KNIVES = [
    ("K1", "src/cockpit/seat_now.py", "        facts = None if code else _pane_facts()\n", "        facts = None\n",
     "HotPath.test_n1_one_tmux_call"),
    ("K2", "src/cockpit/seat_who.py", "        import seats_dir_lib\n    except ImportError:\n        return None\n"
     "    return seats_dir_lib.instance_of_hub(", "        import fleet_patrol\n    except ImportError:\n"
     "        return None\n    return fleet_patrol.instance_of_hub(", "HotPath.test_n2_seat_who_skips_fleet_patrol"),
    ("K3", "src/cockpit/i18n_lib.py", "        self._f_data = self._p if self.locale == FALLBACK_LOCALE else None\n",
     "        self._f_data = self._p if self.locale == FALLBACK_LOCALE else load_dict(FALLBACK_LOCALE, i18n_dir, _keyed=True)\n",
     "HotPath.test_n4_i18n_fallback_lazy"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = pathlib.Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
