#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ui power` 艦長動力室極簡頁(power_page.py)。量測與開視窗全換假的;暫存狀態根。

P1 外來字(版本 / hint)一律跳脫:惡意字串進不了標記;頁上只有自己那一段 script
P2 按鈕只複製指令:yellow / red ⇒ aib power login <家>;can_update ⇒ aib power upgrade <家>;green 沒有登入鈕;沒裝 ⇒ off
P3 CSP 不准連網、頁上沒有任何外部網址;新手任務 n/m;reset_at 有時間
P4 open_power 寫到 <狀態根>/power-room/index.html、用 file:// 開;開不了 ⇒ rc 2 印檔案位置
P5 aib ui power 轉到 power_page;不改 aib 預設模式
P6 四語字典 aib.power.page.* 鍵一樣、{參數} 一樣
刀(複本):K1 _e 不跳脫 ⇒ P1 紅
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (HERE, REPO / "src" / "cockpit"):
    sys.path.insert(0, str(_p))
TMP = tempfile.mkdtemp(prefix="aib-power-page-test-")
os.environ["EZ2_AIB_STATE_ROOT"] = TMP
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import aib_ui  # noqa: E402
import power_page as PG  # noqa: E402

EVIL = '<script>alert(1)</script>"\'><img src=x onerror=alert(2)>'


def ai(key, light, **kw):
    row = {"key": key, "installed": light != "off", "local": "1.0", "latest": "1.0", "update": False,
           "can_update": False, "light": light, "reason": None, "hint": None, "reset_at": None, "login_cmd": None}
    row.update(kw)
    return row


def status():
    return {"ok": True, "v": 1, "detail": "power_status",
            "ais": [ai("claude", "green"), ai("codex", "yellow", reason="login_missing", login_cmd="codex login"),
                    ai("grok", "red", reason="quota_reached", reset_at=2_000_000_000, login_cmd="grok login"),
                    ai("copilot", "off", local=None), ai("agy", "unknown", local=EVIL),
                    ai("aib", "green", latest="0.1.45", update=True, can_update=True)],
            "health": [{"key": "tmux", "ok": False, "value": None, "hint": EVIL},
                       {"key": "disk", "ok": True, "value": 50.0, "hint": None},
                       {"key": "touchid", "ok": None, "value": None, "hint": None}],
            "auto_update": None, "binding": {"seats": 3, "ok": 1, "not_ok": []},
            "onboarding": {"birth_done": False, "tasks": [{"id": "R1", "tier": "R", "state": "done", "reward": None, "detectable": True},
                                                          {"id": "R2", "tier": "R", "state": "todo", "reward": None, "detectable": True},
                                                          {"id": "R8", "tier": "R", "state": "todo", "reward": None, "detectable": False},
                                                          {"id": "SR1", "tier": "SR", "state": "todo", "reward": None, "detectable": True}],
                           "tiers": {"R": {"on": True, "done": 1, "total": 3}, "SR": {"on": True, "done": 0, "total": 1}},
                           "all": {"done": 1, "total": 4}},
            "catalog": {"available": False, "items": []}, "hint": None}


class Page(unittest.TestCase):
    def test_p1_escape(self):
        h = PG.render(status(), now=1_900_000_000)
        self.assertNotIn("<script>alert", h)
        self.assertNotIn("<img", h)
        self.assertEqual(h.count("<script"), 1)
        self.assertIn("&lt;script&gt;", h)

    def test_p2_buttons(self):
        h = PG.render(status())
        cmds = re.findall(r'data-cmd="([^"]*)"', h)
        self.assertIn("aib power login codex", cmds)
        self.assertIn("aib power login grok", cmds)
        self.assertIn("aib power upgrade aib", cmds)
        self.assertNotIn("aib power login claude", cmds)
        self.assertNotIn("aib power login copilot", cmds)
        self.assertIn('class="mod off"', h)

    def test_p3_offline_and_tasks(self):
        h = PG.render(status())
        self.assertIn("default-src 'none'", h)
        self.assertEqual(re.findall(r'(?:src|href)\s*=\s*"?https?:', h), [])
        self.assertIn("1/4", h)
        self.assertIn("R 1/3", h)
        self.assertIn("SR 0/1", h)
        self.assertIn(PG.T("task_soon"), h)                      # 偵測不到的條照實標
        self.assertNotIn(PG.T("task_sr1"), h)                     # 極簡頁只列 R
        self.assertIn(PG._when(2_000_000_000), h)
        h = PG.render({"ais": "junk", "onboarding": None, "health": [None, 3], "binding": []})   # 怪資料不炸
        self.assertIn("0/0", h)

    def test_p4_open(self):
        seen = []
        out = io.StringIO()
        rc = PG.open_power(out=out, status=status, opener=lambda url, env: seen.append(url) or ("app", "chrome"))
        p = Path(TMP) / "power-room" / "index.html"
        self.assertEqual(rc, 0)
        self.assertTrue(p.is_file())
        self.assertEqual(seen, [p.as_uri()])
        out = io.StringIO()
        rc = PG.open_power(out=out, status=status, opener=lambda url, env: ("none", url))
        self.assertEqual(rc, 2)
        self.assertIn(str(p), out.getvalue())

    def test_p5_ui_dispatch(self):
        calls = []
        orig = PG.open_power
        PG.open_power = lambda out=None: calls.append(1) or 0
        try:
            before = aib_ui.get_mode()
            self.assertEqual(aib_ui.main(["power"], out=io.StringIO()), 0)
            self.assertEqual(aib_ui.get_mode(), before)
        finally:
            PG.open_power = orig
        self.assertEqual(calls, [1])
        self.assertEqual(aib_ui.main(["power", "x"], out=io.StringIO()), 2)

    def test_p6_i18n(self):
        got = {}
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            got[loc] = {k: sorted(re.findall(r"\{(\w+)\}", v)) for k, v in d.items() if k.startswith("aib.power.page.")}
        self.assertTrue(got["zh-TW"])
        for loc in ("en", "ja", "ko"):
            self.assertEqual(got[loc], got["zh-TW"], loc)
        src = (REPO / "src" / "cockpit" / "power_page.py").read_text(encoding="utf-8")
        for k in set(re.findall(r'T\("(\w+)"[,)]', src)):
            self.assertIn("aib.power.page." + k, got["zh-TW"], k)
        for light in PG.LIGHT_COLOR:
            self.assertIn("aib.power.page.light_" + light, got["zh-TW"])
        for n in range(1, 13):
            self.assertIn("aib.power.page.task_r%d" % n, got["zh-TW"])


class Knife(unittest.TestCase):
    def test_k1_no_escape(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_page.py", 'return html.escape("" if v is None else str(v), quote=True)',
                        'return "" if v is None else str(v)', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Page.test_p1_escape"], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
