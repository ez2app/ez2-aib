#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib ui power` 艦長動力室正式頁(power_page.py;SPEC-power-room §9–§15)。量測與開視窗全換假的;暫存狀態根。

P1 外來字(版本 / hint / 人格名 / 大腦名)一律跳脫:惡意字串進不了標記;只有一段會執行的 script,
   資料區塊(application/json)裡的 </script> 關不掉區塊
P2 按鈕只複製指令:yellow / red ⇒ aib power login <家>;can_update ⇒ aib power upgrade <家>;green 沒有登入鈕;沒裝 ⇒ off
P3 CSP 不准連網、頁上沒有任何外部網址;任務板四級分頁(SSR 鎖、room 條不列)、全部完成 n/m;reset_at 有時間
P4 open_power 寫到 <狀態根>/power-room/index.html、用 file:// 開;開不了 ⇒ rc 2 印檔案位置
P5 aib ui power 轉到 power_page;不改 aib 預設模式
P6 四語字典 aib.power.page.* 鍵一樣、{參數} 一樣
P7 給腳本的資料先篩格式:壞模型 / 壞人格 uid / 壞路由代號丟掉;最近出發裡已不在的人格 / 大腦變 None;最多 20 筆
P8 付費內容不出現(SPEC §14):四語整頁沒有價錢 / 試用 / 序號 / 商店 / 購買字樣
P9 常數跟資料模組對齊:16 個「為什麼」選項順序、最多 5 名、控制台的家、格式正規式
P10 open_power 併入出發清單 / 艦員檔案;那塊炸了照樣出頁;燈號交給 launch_list 判自動出發
P11 四語整頁沒有缺鍵(!!鍵!!)
刀(複本):K1 _e 不跳脫 ⇒ P1 紅;K2 資料區塊不跳脫 < > & ⇒ P1 紅
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
                                                          {"id": "SR1", "tier": "SR", "state": "todo", "reward": None, "detectable": True},
                                                          {"id": "R99", "tier": "R", "where": "room", "state": "todo",
                                                           "reward": None, "detectable": True}],
                           "tiers": {"R": {"on": True, "done": 1, "total": 3}, "SR": {"on": True, "done": 0, "total": 1},
                                     "SSR": {"on": False, "done": 0, "total": 5, "locked": True, "unlock": "launch_recorded"}},
                           "all": {"done": 1, "total": 4}},
            "catalog": {"available": False, "items": []}, "hint": None}


def full():
    st = status()
    st["launch"] = {"model_choices": {"claude": ["claude-opus-5-5", "bad model;rm -rf ~", EVIL], "codex": "junk"},
                    "personas": [{"uid": "71c97a", "name": EVIL, "fav": True}, {"uid": "bad uid;x", "name": "x"},
                                 {"uid": "p2", "name": "a\u202eb\x1bc"}],
                    "brains": [{"route_key": "p20.office", "title": "</script><script>alert(3)</script>"},
                               {"route_key": "Bad Key", "title": "x"}],
                    "recent": [{"brand": "claude", "model": "claude-opus-5-5", "persona": "71c97a", "brain": "p20.office",
                                "at": 1_900_000_000},
                               {"brand": "claude", "model": "$(boom)", "persona": "gone", "brain": "gone", "at": True},
                               {"brand": "evil", "model": None}] + [{"brand": "agy"}] * 30}
    st["crew"] = {"why_rank": ["memory", "nope", "coding"]}
    st["suggest"] = ["q_memory", "<b>", "q_coding"]
    st["version"] = "0.1.55"
    return st


def data_of(h):
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', h, re.S)
    return json.loads(m.group(1))


class Page(unittest.TestCase):
    def test_p1_escape(self):
        h = PG.render(full(), now=1_900_000_000)
        self.assertNotIn("<script>alert", h)
        self.assertNotIn("<img", h)
        self.assertEqual(len(re.findall(r"<script>", h)), 1)                 # 只有自己那一段會執行
        self.assertEqual(h.count("</script>"), 2)                            # 資料區塊裡的 </script> 被跳脫
        self.assertIn("&lt;script&gt;", h)
        d = data_of(h)
        self.assertEqual(d["personas"][0]["name"], EVIL[:PG.TEXT_MAX])       # 跳脫只為了放進頁面,讀回來字一樣
        self.assertEqual(d["brains"][0]["title"], "</script><script>alert(3)</script>"[:PG.TEXT_MAX])

    def test_p2_buttons(self):
        h = PG.render(status())
        cmds = re.findall(r'data-cmd="([^"]*)"', h)
        self.assertIn("aib power login codex", cmds)
        self.assertIn("aib power login grok", cmds)
        self.assertIn("aib power upgrade aib", cmds)
        self.assertNotIn("aib power login claude", cmds)
        self.assertNotIn("aib power login copilot", cmds)
        self.assertIn('class="mod off"', h)
        self.assertIn("aib power tier R off", cmds)                          # 等級開關 = 複製指令,不在頁上自己勾
        self.assertNotIn("aib power tier SSR off", cmds)                     # 鎖住的級沒有開關
        for c in cmds:                                                       # 複製的指令只有 aib 開頭的(建議題除外)
            self.assertTrue(c.startswith("aib ") or c == PG.T("q_first_seat") or c in (PG.T("q_lights"), PG.T("q_handoff")), c)

    def test_p3_offline_and_tasks(self):
        h = PG.render(status())
        self.assertIn("default-src 'none'", h)
        self.assertEqual(re.findall(r'(?:src|href)\s*=\s*"?https?:', h), [])
        self.assertNotIn("http://", h.replace("http-equiv", ""))
        self.assertNotIn("https://", h)
        self.assertIn(">1/4<", h)                                            # 全部完成大禮:只算 where = power
        self.assertIn("R 1/3", h)
        self.assertIn("SR 0/1", h)
        self.assertIn("SSR 🔒 " + PG.T("tier_locked"), h)
        self.assertNotIn("SSR 0/5", h)
        self.assertIn(PG.T("task_soon"), h)                                  # 偵測不到的條照實標
        self.assertIn(PG.T("task_sr1"), h)                                   # 正式頁四級都列
        self.assertIn(PG.T("task_r1_how"), h)
        self.assertNotIn("R99", h)                                           # where = room 的不列(SPEC §14)
        self.assertIn(PG._when(2_000_000_000), h)
        h = PG.render({"ais": "junk", "onboarding": None, "health": [None, 3], "binding": [], "launch": 3, "crew": "x"})
        self.assertIn(">0/0<", h)                                            # 怪資料不炸(大禮計數,不是 HUD 的 0/0 就緒)
        self.assertIn(PG.T("hud_ready", n=0, m=0), h)
        PG.render(None)
        PG.render({"ais": [{"key": ["x"], "light": ["y"]}, {"key": "claude", "light": {}}], "health": 3, "suggest": 5,
                   "onboarding": {"tasks": 7}, "launch": {"recent": [{"brand": "claude", "persona": ["x"], "model": ["m"]}],
                                                          "personas": 3}, "crew": {"why_rank": [["x"]]}})

    def test_p4_open(self):
        seen = []
        out = io.StringIO()
        rc = PG.open_power(out=out, status=status, opener=lambda url, env: seen.append(url) or ("app", "chrome"),
                           extra=lambda light=None: {})
        p = Path(TMP) / "power-room" / "index.html"
        self.assertEqual(rc, 0)
        self.assertTrue(p.is_file())
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual(seen, [p.as_uri()])
        out = io.StringIO()
        rc = PG.open_power(out=out, status=status, opener=lambda url, env: ("none", url), extra=lambda light=None: {})
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
        import power_tasks
        for tid, _tier, _code, _req in power_tasks.TASKS:                  # 動力室 30 條:標題 + 做法四語齊
            if tid in power_tasks.IN_ROOM:
                continue
            for suffix in ("", "_how"):
                for loc in got:
                    self.assertIn("aib.power.page.task_%s%s" % (tid.lower(), suffix), got[loc], (loc, tid))
        import power_ask
        for q in list(power_ask.SUGGEST.values()) + list(power_ask.SUGGEST_BASE):   # 問 AI 建議題四語齊
            self.assertIn("aib.power.page." + q, got["zh-TW"], q)

    def test_p7_data_filtered(self):
        d = data_of(PG.render(full()))
        self.assertEqual(d["models"]["claude"], ["claude-opus-5-5"])
        self.assertEqual(d["models"]["codex"], [])
        self.assertEqual([p["uid"] for p in d["personas"]], ["71c97a", "p2"])
        self.assertEqual(d["personas"][1]["name"], "abc")                    # 方向控制 / 控制字元去掉
        self.assertEqual([b["route_key"] for b in d["brains"]], ["p20.office"])
        self.assertEqual(d["recent"][0], {"brand": "claude", "model": "claude-opus-5-5", "persona": "71c97a",
                                          "brain": "p20.office", "when": PG._when(1_900_000_000)})
        self.assertEqual(d["recent"][1], {"brand": "claude", "model": None, "persona": None, "brain": None, "when": ""})
        self.assertEqual(len(d["recent"]), 20)
        self.assertNotIn("evil", [r["brand"] for r in d["recent"]])
        self.assertEqual(d["why_rank"], ["memory", "coding"])
        h = PG.render(full())
        self.assertIn(PG.T("q_memory"), h)
        self.assertIn(PG.T("q_coding"), h)
        self.assertNotIn("!!aib.", h)                                        # 壞題號(<b>)丟掉,不拿去查字典

    def test_p8_no_paid_content(self):
        words = {"zh-TW": ("價", "試用", "序號", "商店", "購買", "付費", "NT$"), "en": ("price", "trial", "serial", "shop", "buy", "purchase", "paid"),
                 "ja": ("価格", "試用", "シリアル", "ショップ", "購入", "有料"), "ko": ("가격", "체험", "시리얼", "상점", "구매", "유료")}
        for loc, bad in words.items():                                   # 整份 aib.power.page.* 字典(30 條任務、全部建議題都在內)
            d = json.loads((REPO / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for k, v in d.items():
                if k.startswith("aib.power.page."):
                    for w in bad:
                        self.assertNotIn(w.lower(), v.lower(), (loc, k, w))
        h = PG.render(full()).lower()
        for w in words["zh-TW"]:
            self.assertNotIn(w.lower(), h, w)

    def test_p9_constants_aligned(self):
        import power_crew
        import power_launch
        self.assertEqual(PG.WHY, power_crew.WHY)
        self.assertEqual(PG.WHY_MAX, power_crew.RANK_MAX)
        self.assertEqual(PG.LAUNCH_BRANDS, power_launch.BRANDS)
        self.assertEqual(PG.MODEL_RE.pattern, power_launch.MODEL_RE.pattern)
        self.assertEqual(PG.ROUTE_RE.pattern, power_launch.ROUTE_RE.pattern)
        self.assertEqual(re.findall(r'data-why="(\w+)"', PG.render(status())), list(power_crew.WHY))

    def test_p10_extras(self):
        got = {}

        def extra(light=None):
            got["claude"], got["codex"], got["zz"] = light("claude"), light("codex"), light("zz")
            return {"crew": {"why_rank": ["art"]}, "version": "9.9.9"}
        PG.open_power(out=io.StringIO(), status=status, opener=lambda url, env: ("app", "x"), extra=extra)
        self.assertEqual(got, {"claude": "green", "codex": "yellow", "zz": "unknown"})
        h = (Path(TMP) / "power-room" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(data_of(h)["why_rank"], ["art"])
        self.assertIn(">9.9.9<", h)

        def boom(light=None):
            raise RuntimeError("x")
        self.assertEqual(PG.open_power(out=io.StringIO(), status=status, opener=lambda url, env: ("app", "x"), extra=boom), 0)
        ex = PG.extras(light=lambda b: "green")                              # 真的那份:暫存狀態根下每塊都有、不炸
        self.assertEqual(set(ex), {"launch", "crew", "suggest", "version"})

    def test_p12_grey_light_and_login(self):
        h = PG.render(status())
        cmds = re.findall(r'data-cmd="([^"]*)"', h)
        self.assertNotIn("aib power login agy", cmds)                        # 灰燈不出登入鈕
        d = data_of(h)
        self.assertEqual({a["key"]: a["can_login"] for a in d["ais"]}["codex"], True)
        self.assertEqual({a["key"]: a["can_login"] for a in d["ais"]}["agy"], False)
        import power_ask
        self.assertEqual(tuple(d["ask_brands"]), power_ask.BRANDS)

    def test_p11_no_missing_keys(self):
        import i18n_lib
        orig = PG.T
        try:
            for loc in ("zh-TW", "en", "ja", "ko"):
                PG.T = lambda key, _loc=loc, **params: i18n_lib.t("aib.power.page." + key, params, locale=_loc)
                h = PG.render(full())
                self.assertNotIn("!!aib.", h, loc)
        finally:
            PG.T = orig


class Knife(unittest.TestCase):
    def test_k1_no_escape(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_page.py", 'return html.escape("" if v is None else str(v), quote=True)',
                        'return "" if v is None else str(v)', tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Page.test_p1_escape"], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k2_json_block_unescaped(self):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/power_page.py", 'return "".join(JSON_ESC.get(c, c) for c in s)', 'return s',
                        tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "Page.test_p1_escape"], cwd=str(root),
                            max_procs=40, timeout=120)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
