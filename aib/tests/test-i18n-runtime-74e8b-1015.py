#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""英文介面下的執行時訊息不再是中文(10-15 多語第二批,Andy「把所有 aib 應修正的先修正」)。

全部在沙盒 HOME 跑(EZ2_AIB_LOCALE=en,env 清掉 EZ2_/AIB_/TMUX):
R1 aib doctor:還沒 init(約 35 行 FAIL 與修法)與 init 之後,都沒有中日韓字、沒有 !!缺鍵!!
R2 aib genes status(預設關)、page list(沒綁頁面)、task show(缺參數)、now --all(沒有活席)、top --seconds 1
R3 aib cli status 的表格(換掉查版本那一段,不連網):表頭、沒裝 / 找不到、✓ / ↑ 標記、更新提示
R4 persona 子指令:precheck / watchlist / receipt / report 的錯誤與提示
R5 底部面板:效能頁(perf_tab_fmt,含 [X] 落點不變)、大綱進度行(outline-progress.sh,讀得到與讀不到)
R6 降溫:四級 LABEL、plan() 三袋的字;aib top 的量法說明
Z1 zh-TW 照舊是中文(不是退回英文):doctor、perf_tab_fmt、outline-progress、cooldown LABEL 抽查與原字逐字相同
Z2 字典四語同鍵、佔位符一致(Translator.parity_report)
刀(複本,tests/knife-i18n-runtime-74e8b-1015.sh):K1 doctor 一條細節改回中文字面 ⇒ R1 紅;K2 效能頁「手動」改回字面 ⇒ R5 紅;
   K3 now --all「量不到任何還在的席」改回字面 ⇒ R2 紅;K4 en 字典 genes 的 {n} 寫兩次 ⇒ Z2 紅
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # = aib/
AIB = [sys.executable, str(ROOT.parent / "bin" / "aib")]
COCKPIT = ROOT / "src" / "cockpit"
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
MISSING = re.compile(r"!!aib\.")


def _env(home, locale="en", **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
    env.update({"HOME": str(home), "EZ2_AIB_LOCALE": locale, "LANG": "en_US.UTF-8", "AIB_GENES": "0",
                "XDG_CACHE_HOME": str(Path(home).parent / "cache"), "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


def _bad_lines(text):
    return [ln for ln in text.splitlines() if CJK.search(ln) or MISSING.search(ln)]


class _Sandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = Path(tempfile.mkdtemp(prefix="aib-i18n-run-"))
        cls.fresh = cls.td / "fresh"          # 沒跑過 aib init 的家
        cls.home = cls.td / "home"            # 跑過 aib init 的家
        cls.fresh.mkdir()
        cls.home.mkdir()
        p = subprocess.run(AIB + ["init"], cwd=cls.td, env=_env(cls.home), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def aib(self, args, home=None, locale="en", **extra):
        p = subprocess.run(AIB + args, cwd=self.td, env=_env(home or self.home, locale, **extra), capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=180)
        return p.returncode, p.stdout + p.stderr

    def py(self, code, locale="en", stdin=None, args=()):
        p = subprocess.run([sys.executable, "-c", code, *args], cwd=str(COCKPIT), env=_env(self.home, locale),
                           input=stdin, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def assertEnglish(self, label, out):
        self.assertTrue(out.strip(), "%s printed nothing" % label)
        bad = _bad_lines(out)
        self.assertEqual(bad, [], "%s still has CJK / missing keys:\n%s" % (label, "\n".join(bad[:8])))


class Commands(_Sandbox):
    def test_r1_doctor_before_and_after_init(self):
        for label, home in (("doctor (no init)", self.fresh), ("doctor (after init)", self.home)):
            rc, out = self.aib(["doctor"], home=home)
            self.assertIn("state_root_exists", out, label)
            self.assertEnglish(label, out)

    def test_r2_small_commands(self):
        for args in (["genes", "status"], ["page", "list"], ["task", "show"], ["now", "--all"], ["top", "--seconds", "1"]):
            rc, out = self.aib(args)
            self.assertEnglish("aib " + " ".join(args), out)

    def test_r4_persona_runtime_messages(self):
        for args in (["persona", "precheck", "--persona", "x"], ["persona", "watchlist"], ["persona", "receipt"],
                     ["persona", "report", "--persona-code", "x"]):
            rc, out = self.aib(args)
            self.assertEnglish("aib " + " ".join(args), out)


class Tables(_Sandbox):
    CLI_STATUS = r'''
import io, sys, cli_versions as C
C.check = lambda *a, **k: [
    {"key": "claude", "local": None, "latest": "2.1.300", "installed_at": None, "update": False, "can_update": False},
    {"key": "copilot", "local": "1.0.97", "latest": "1.0.98", "installed_at": 1792000000, "update": True, "can_update": True},
    {"key": "agy", "local": "1.3.5", "latest": None, "installed_at": None, "update": False, "can_update": False},
    {"key": "grok", "local": None, "latest": None, "installed_at": None, "update": False, "can_update": False}]
C.cache_path = lambda: "/nonexistent/aib-cli-cache.json"
buf = io.StringIO(); C.status(out=buf); print(buf.getvalue())
'''

    def test_r3_cli_status_table(self):
        rc, out = self.py(self.CLI_STATUS)
        self.assertEqual(rc, 0, out)
        self.assertIn("copilot", out)
        self.assertEnglish("cli status table", out)
        rows = [ln for ln in out.splitlines() if ln.startswith(("claude", "copilot", "agy", "grok"))]
        cols = {ln.index(ln.split()[1], len(ln.split()[0])) for ln in rows}
        self.assertEqual(len(cols), 1, "second column not aligned:\n" + "\n".join(rows))

    TARGETS = json.dumps({"targets": [
        {"id": "b", "kind": "cabin", "name": "old-cabin", "idle_s": 7200, "auto": True},
        {"id": "c", "kind": "cabin", "name": "attached", "idle_s": 30, "human_attached": "True"},
        {"id": "d", "kind": "proc", "name": "?", "verdict": "unknown", "idle_s": 5},
        {"id": "e", "kind": "proc", "name": "m", "idle_s": 50}]})

    def perf(self, locale):
        st = self.td / ("perf-" + locale)
        st.mkdir(exist_ok=True)
        p = subprocess.run([sys.executable, str(COCKPIT / "perf_tab_fmt.py"), "--spans"], input=self.TARGETS,
                           env=_env(self.home, locale, EZ2_COCKPIT_STATE_DIR=str(st)), capture_output=True, text=True,
                           timeout=60)
        spans = json.loads((st / "perf-spans.json").read_text(encoding="utf-8"))["spans"]
        return p.stdout + p.stderr, spans

    def outline(self, locale, path):
        p = subprocess.run(["bash", str(COCKPIT / "outline-progress.sh")],
                           env=_env(self.home, locale, EZ2_OUTLINE=str(path)), capture_output=True, text=True, timeout=60)
        return p.stdout + p.stderr

    def test_r5_bottom_panel_perf_and_outline(self):
        out, spans = self.perf("en")
        self.assertIn("[X]", out)
        self.assertEnglish("perf tab", out)
        for sp in spans:   # [X] 落點仍是畫面上那三格
            line = out.splitlines()[sp["line"]]
            self.assertEqual(line.rindex("[X]"), sp["x_span"][0], line)
        self.assertEnglish("outline (missing)", self.outline("en", self.td / "nope" / "O.md"))

    def test_r6_cooldown_and_top_words(self):
        code = r'''
import cooldown as C, machine_top as M
print([C.LABEL[lv] for lv in ("ok", "warn", "hot", "critical")])
for lv in ("ok", "warn", "hot", "critical"):
    print(C.plan(lv, ["cpu 95%"], {"killed": [], "skipped": []}))
'''
        rc, out = self.py(code)
        self.assertEqual(rc, 0, out)
        self.assertEnglish("cooldown words", out)


class ZhTW(_Sandbox):
    def test_z1_zh_tw_unchanged_spot_checks(self):
        rc, out = self.aib(["doctor"], home=self.fresh, locale="zh-TW")
        self.assertIn("跑 aib init 建立狀態根", out)
        out, _ = self.perf("zh-TW")
        self.assertIn("使用中", out)
        self.assertIn("量不到", out)
        self.assertIn("手動", out)
        self.assertRegex(out, r"可安全全關 1 項\(b\) \[X\]")
        self.assertIn("大綱:讀取失敗", self.outline("zh-TW", self.td / "nope" / "O.md"))
        rc, out = self.py('import cooldown as C; print(C.LABEL["ok"], C.LABEL["critical"])', locale="zh-TW")
        self.assertEqual(out.strip(), "正常 危險")

    TARGETS = Tables.TARGETS
    perf = Tables.perf
    outline = Tables.outline


class Dict(unittest.TestCase):
    def test_z2_four_locales_same_keys_and_placeholders(self):
        sys.path.insert(0, str(COCKPIT))
        import i18n_lib
        rep = i18n_lib.Translator(locale="en").parity_report()
        self.assertTrue(rep.get("ok"), rep.get("errors"))


if __name__ == "__main__":
    unittest.main()
