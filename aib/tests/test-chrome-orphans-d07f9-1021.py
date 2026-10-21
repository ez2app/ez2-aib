#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib chrome orphans(PROPOSAL-gaps v3 第 7 件 D):列沒人收的自動化 Chrome 與起它的席碼,只列不殺。

C1 只列自動化主行程:Helper / --type= 子行程、使用者自己的 Chrome(沒自動化旗標)、不是瀏覽器的都不列
C2 孤兒 = ppid 1;--all 連有父行程的一起列
C3 主人:環境 AIB_RESUME_SESSION_UUID 末五碼;沒有 ⇒ AIB_REVIVE_SESSION_UUID;都沒有 ⇒ TMUX_PANE;再沒有 ⇒ None;
   壞格式不認;其他環境變數(token)不出現在輸出
C4 ps 跑不了 ⇒ rc 1、why ps-failed(跟「沒有」分開);沒有 ⇒ rc 0
C5 人看的輸出:@席碼、孤兒字樣、提示不要寬 pkill;四語字典
C6 不殺:模組裡沒有 kill / signal
刀(複本):K1 不判 ppid ⇒ C2 紅
"""
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))
import chrome_orphans as CO  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HELPER = "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"
PS = "\n".join([
    "  101     1 02:03:04 %s --headless=new --user-data-dir=/tmp/aib-shot-1 --remote-debugging-port=9222" % CH,
    "  102   555    10:00 %s --headless --user-data-dir=/var/folders/xx/T/prof" % CH,
    "  103     1 1-00:00:00 %s --type=renderer --user-data-dir=/tmp/aib-shot-1" % HELPER,
    "  104     1    05:00 %s" % CH,                                         # 使用者自己的
    "  105     1    05:00 /usr/bin/python3 server.py --headless",              # 不是瀏覽器
    "  106     1    00:30 %s --enable-automation --user-data-dir=/tmp/p2" % CH,
    "  107     1    00:31 %s --headless --user-data-dir=/tmp/p3" % CH,
])
ENV = {
    101: "%s AIB_RESUME_SESSION_UUID=06eb486f-cf37-4822-872b-50da2cdd07f9 TMUX_PANE=%%8 X_CAPTAIN_TOKEN=sekret" % CH,
    102: "%s AIB_REVIVE_SESSION_UUID=3cc3991a-9491-4590-ab95-aa3762f74e8b" % CH,
    106: "%s TMUX_PANE=%%12 AIB_RESUME_SESSION_UUID=not-a-uuid" % CH,
    107: "%s HOME=/Users/x" % CH,
}


def fake_run(cmd):
    if cmd[:2] == ["ps", "-axo"]:
        return PS
    if cmd[:2] == ["ps", "-E"]:
        return ENV.get(int(cmd[3]), "")
    raise AssertionError(cmd)


class Scan(unittest.TestCase):
    def rows(self, all_=False):
        return {r["pid"]: r for r in CO.scan(all_=all_, run=fake_run)}

    def test_c1_c2_filter(self):
        self.assertEqual(sorted(self.rows()), [101, 106, 107], "只列孤兒自動化主行程")
        rows = self.rows(all_=True)
        self.assertEqual(sorted(rows), [101, 102, 106, 107])
        self.assertEqual((rows[102]["orphan"], rows[102]["ppid"]), (False, 555))
        self.assertEqual((rows[101]["user_data_dir"], rows[101]["age"], rows[101]["headless"]), ("/tmp/aib-shot-1", "02:03:04", True))
        self.assertFalse(rows[106]["headless"])

    def test_c3_owner(self):
        rows = self.rows(all_=True)
        self.assertEqual((rows[101]["seat"], rows[101]["pane"]), ("d07f9", "%8"))
        self.assertEqual(rows[102]["seat"], "74e8b")
        self.assertEqual((rows[106]["seat"], rows[106]["pane"]), (None, "%12"), "壞格式 uuid 不認")
        self.assertEqual((rows[107]["seat"], rows[107]["pane"]), (None, None))
        buf = io.StringIO()
        CO.main(["orphans", "--all", "--json"], out=buf, run=fake_run)
        self.assertNotIn("sekret", buf.getvalue())
        self.assertNotIn("X_CAPTAIN_TOKEN", buf.getvalue())

    def test_c4_ps_failed(self):
        def boom(cmd):
            raise OSError("no ps")
        buf = io.StringIO()
        self.assertEqual(CO.main(["orphans", "--json"], out=buf, run=boom), 1)
        self.assertEqual(json.loads(buf.getvalue())["why"], "ps-failed")
        buf = io.StringIO()
        self.assertEqual(CO.main(["orphans"], out=buf, run=lambda c: ""), 0)
        self.assertIn("沒有", buf.getvalue())

    def test_c5_text_and_i18n(self):
        buf = io.StringIO()
        self.assertEqual(CO.main(["orphans"], out=buf, run=fake_run), 0)
        text = buf.getvalue()
        self.assertIn("@d07f9", text)
        self.assertIn("%12", text)
        self.assertIn("孤兒", text)
        self.assertIn("pkill", text)
        want = None
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / (lang + ".json")).read_text(encoding="utf-8"))
            keys = {k for k in d if k.startswith("aib.chrome.")}
            self.assertGreaterEqual(len(keys), 9, lang)
            want = want or keys
            self.assertEqual(keys, want, lang)

    def test_c6_never_kills(self):
        src = (REPO / "src" / "cockpit" / "chrome_orphans.py").read_text(encoding="utf-8")
        for bad in ("os.kill", "signal.", "killpg", "pkill", '"kill"'):
            self.assertNotIn(bad, src.split('"""', 2)[2], bad)


class Knife(unittest.TestCase):
    def test_k1_no_ppid_check(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chrome_orphans.py", "        orphan = ppid == 1\n", "        orphan = True\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Scan.test_c1_c2_filter"], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
