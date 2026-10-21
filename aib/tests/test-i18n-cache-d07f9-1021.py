#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""i18n_lib 字典快取(10-21 @d07f9:聊天全套 56 分鐘,大半花在每個 T() 重讀兩份字典;aib chat --help 1.1 秒)。
只用暫存的字典目錄;EZ2_AIB_STATE_ROOT 指暫存,不碰真環境。

C1 同一份字典連叫 t() 50 次只解析一次(Translator 自己載的才快取)
C2 字典改了(原地改同長度的值 / 換一份新檔)⇒ 下一次 t() 就是新值
C3 快取暖過之後字典換成 symlink ⇒ 照樣拒絕(lstat 檢查每次做)
C4 i18nload 追蹤每建一次 Translator 照樣記(測試沿用要靠它)
C5 給呼叫端整份的 load_dict(非 _keyed)每次新的一份;改它不影響 t()
刀(複本):K1 快取鍵拿掉 mtime / 大小 ⇒ C2 紅;K2 拿掉 lstat 一般檔檢查 ⇒ C3 紅
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit", HERE):
    sys.path.insert(0, str(_p))
import i18n_lib as I  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-i18ncache-")
        self.addCleanup(td.cleanup)
        self.dir = Path(td.name)
        env = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.dir / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.write("en", {"x.hello": "hello {who}", "x.only_en": "en only"})
        self.write("zh-TW", {"x.hello": "你好 {who}"})
        I._KEYED_CACHE.clear()

    def write(self, loc, data, replace=True):
        p = self.dir / ("%s.json" % loc)
        text = json.dumps(data, ensure_ascii=False)
        if replace:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, p)
        else:
            with open(p, "r+", encoding="utf-8") as fh:
                fh.write(text)
        return p

    def t(self, key, loc="zh-TW", **params):
        return I.t(key, params or None, locale=loc, i18n_dir=str(self.dir))


class Cache(Base):
    def test_c1_parse_once(self):
        n = {"decode": 0}
        orig = I._reject_duplicate_keys

        def counting(pairs):
            n["decode"] += 1
            return orig(pairs)
        I._reject_duplicate_keys = counting
        self.addCleanup(setattr, I, "_reject_duplicate_keys", orig)
        for _ in range(50):
            self.assertEqual(self.t("x.hello", who="A"), "你好 A")
            self.assertEqual(self.t("x.only_en"), "en only")
        self.assertEqual(n["decode"], 2, "zh-TW + en 各解析一次")

    def test_c2_change_seen(self):
        self.assertEqual(self.t("x.hello", who="A"), "你好 A")
        self.write("zh-TW", {"x.hello": "您好 {who}"})
        self.assertEqual(self.t("x.hello", who="A"), "您好 A", "換新檔")
        p = self.dir / "zh-TW.json"
        before = p.stat()
        self.write("zh-TW", {"x.hello": "哈囉 {who}"}, replace=False)   # 同長度、同 inode 原地改
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns + 1000))
        self.assertEqual(p.stat().st_ino, before.st_ino)
        self.assertEqual(self.t("x.hello", who="A"), "哈囉 A", "原地改")

    def test_c3_symlink_rejected_after_warm(self):
        self.assertEqual(self.t("x.hello", who="A"), "你好 A")
        p = self.dir / "zh-TW.json"
        real = self.dir / "real-zh.json"
        os.replace(p, real)
        os.symlink(real, p)
        with self.assertRaises(I.I18nError):
            I.Translator(locale="zh-TW", i18n_dir=str(self.dir))

    def test_c4_trace_each_time(self):
        seen = []
        orig = I._trace_emit
        I._trace_emit = lambda obj: seen.append(obj)
        self.addCleanup(setattr, I, "_trace_emit", orig)
        for _ in range(3):
            self.t("x.hello", who="A")
        loads = [o for o in seen if o.get("ch") == "i18nload"]
        self.assertEqual(len(loads), 6, "每建一次 Translator 兩份字典都記")

    def test_c5_whole_dict_fresh(self):
        self.t("x.hello", who="A")
        d = I.load_dict("zh-TW", str(self.dir))
        d["x.hello"] = "被改"
        self.assertIsNot(d, I.load_dict("zh-TW", str(self.dir)))
        self.assertEqual(self.t("x.hello", who="A"), "你好 A")

    def test_c6_speed(self):
        t0 = time.time()
        for _ in range(200):
            I.t("aib.page.help.unbind")
        self.assertLess(time.time() - t0, 2.0, "真字典 200 次 t() 不該要好幾秒")


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, case):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/i18n_lib.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), case], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_key_without_mtime(self):
        self._knife("st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)", "st.st_dev, st.st_ino)",
                    "Cache.test_c2_change_seen")

    def test_k2_no_lstat_check(self):
        self._knife("    if not stat.S_ISREG(st.st_mode):\n        raise I18nError(", "    if False:\n        raise I18nError(",
                    "Cache.test_c3_symlink_rejected_after_warm")


if __name__ == "__main__":
    unittest.main()
