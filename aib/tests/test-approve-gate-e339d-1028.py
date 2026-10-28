#!/usr/bin/env python3
"""本人核准統一入口 approve_gate(10-28 @aeb40 TID-1)。

G1 類別表:規格列的高風險類都在、都不開窗;可開窗三類的上限照 D5;SITES 用到的類別都在表裡、登記的模組檔都在
G2 呼叫端不能自報類別:不是登記的那支檔去組 ⇒ 未登記;登記的那支組 ⇒ 表上的類別          (刀:拿掉模組比對 ⇒ 紅)
G3 未登記 = 高風險:沒登記的 site 是 unregistered + high_risk;純字串交給 ask() 直接拒(10-29 TID-2a)(刀:拿掉 ⇒ 紅)
G4 清字:控制字元 / 方向控制 / 零寬字拿掉;數字欄位截長;FULL 欄位不截、超過 FULL_MAX 直接拒、拒了不彈窗(刀:拿掉 clean ⇒ 紅)
G5 ask:一次一個視窗(別人拿著鎖 ⇒ busy)、取消 / 讀不懂 ⇒ 不過;ready:非 Mac / AIB_TOUCHID=0 / 叫不出 ⇒ 拒
G6 呼叫點都走統一入口:SITES 每個 site 在登記的模組裡真的用到;除了自己拿鎖的 consent,沒有人直接叫 TA.ask
G7 語系:四語都有類別名與新句子;en 下視窗字是英文
G8 用到 AG / TA 的函式,那個名字在自己的作用域裡真的有定義(不靠 NOSTR venv 也抓得到漏 import;10-29 chat_pair._confirm)
"""
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import approve_gate as AG  # noqa: E402
import touchid_approve as TA  # noqa: E402

RLO, ZWSP, BOM = chr(0x202E), chr(0x200B), chr(0xFEFF)


def real_path(rel):
    p = ROOT / "src" / rel
    return p if p.is_file() else ROOT / "tools" / rel


def mint_as(rel, site, facts=None, suffix=()):
    """假裝是 rel 那支真檔在組:在 sys.modules 放一個 __file__ = 那支真檔的模組,用它的 globals、真檔名 compile。
    (這正是 L2 只防誤用、不算防線的示範:行程內的程式做得到一樣的事。)"""
    path = real_path(rel)
    name = "aibtest_mint_" + re.sub(r"\W", "_", rel)
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    mod.AG = AG
    sys.modules[name] = mod
    try:
        exec(compile("r = AG.reason(%r, %r, suffix=%r)" % (site, facts or {}, tuple(suffix)), str(path), "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod.__dict__["r"]


def mint_site(site, facts=None, suffix=()):
    return mint_as(AG.SITES[site]["mod"], site, facts, suffix)


class Env(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gate-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                                         "EZ2_AIB_LOCALE": "en", "HOME": str(self.root)})
        p.start()
        self.addCleanup(p.stop)


class G1Table(Env):
    def test_high_risk_categories(self):
        for cat in ("release_sign", "payment", "delete_data", "trusted_device", "wp_login", "quote", "secret",
                    "cf_token", "page_bind", "phone_pair", "unregistered"):
            self.assertTrue(AG.CATEGORIES[cat]["high_risk"], cat)
            self.assertEqual(AG.CATEGORIES[cat]["max_window"], 0, cat)

    def test_window_caps_d5(self):
        self.assertEqual(AG.CATEGORIES["seat_input"]["max_window"], 24 * 3600)
        self.assertEqual(AG.CATEGORIES["outbound"]["max_window"], 3 * 3600)
        self.assertEqual(AG.CATEGORIES["command"]["max_window"], 3 * 3600)
        for cat in ("seat_input", "outbound", "command"):
            self.assertFalse(AG.CATEGORIES[cat]["high_risk"])

    def test_sites_point_at_real_modules_and_categories(self):
        for site, spec in AG.SITES.items():
            self.assertIn(spec["cat"], AG.CATEGORIES, site)
            self.assertTrue((ROOT / "src" / spec["mod"]).is_file() or (ROOT / "tools" / spec["mod"]).is_file(), site)

    def test_d1_sites_are_high_risk(self):
        for site in ("aib_approve.yes", "aib_quote.add", "secret_ask.ask", "cf_grant.grant", "page_bind.bind",
                     "page_unbind.unbind", "chat_pair.start", "chat_pair.confirm", "chat_pair.revoke",
                     "release.sign_approve", "consent.sign_script", "device_lost.revoke"):
            self.assertTrue(AG.CATEGORIES[AG.SITES[site]["cat"]]["high_risk"], site)


class G2CallerCannotChooseCategory(Env):
    def test_foreign_module_gets_unregistered(self):
        r = AG.reason("chat_human.send", {"who": "a", "text": "b"})       # 這支測試檔不是 chat_human.py
        self.assertEqual(r.category, AG.UNREGISTERED)
        self.assertTrue(r.high_risk)

    def test_low_category_from_another_module_is_unregistered(self):
        r = mint_as("cockpit/aib_quote.py", "chat_human.send", {"who": "a", "text": "b"})
        self.assertEqual(r.category, AG.UNREGISTERED, "aib_quote 冒用 chat_human 的低類別")

    def test_registered_module_gets_table_category(self):
        self.assertEqual(mint_site("chat_human.send", {"who": "a", "text": "b"}).category, "outbound")
        self.assertEqual(mint_site("aib_quote.add", {"text": "hi"}).category, "quote")
        self.assertEqual(mint_site("seat_act.input", {"seat": "abcde", "text": "x"}).category, "seat_input")

    def test_no_category_parameter(self):
        with self.assertRaises(TypeError):
            AG.reason("chat_human.send", {}, category="outbound")    # noqa


class G3UnregisteredIsHighRisk(Env):
    def test_unknown_site(self):
        r = mint_as("cockpit/chat_human.py", "nope.nothing")
        self.assertEqual(r.category, AG.UNREGISTERED)
        self.assertTrue(r.high_risk)
        self.assertIn("nope.nothing", r)

    def test_plain_string(self):
        r = AG.coerce("do something")
        self.assertEqual(r.category, AG.UNREGISTERED)
        self.assertTrue(r.high_risk)
        seen = []                        # 10-29 TID-2a:純字串直接拒、不彈窗;coerce 只給測試(詳見 test-approve-gate-l2)
        res = AG.ask("do something", self.root, touchid=lambda t: seen.append(t) or {"ok": True})
        self.assertFalse(res["ok"])
        self.assertEqual((res["category"], res["detail"]), (AG.UNREGISTERED, AG.UNMINTED))
        self.assertEqual(seen, [])
        res = AG.ask(r, self.root, touchid=lambda t: seen.append(t) or {"ok": True})
        self.assertTrue(res["ok"])
        self.assertEqual(res["category"], AG.UNREGISTERED)
        self.assertEqual(seen, ["do something"])


class G4Clean(Env):
    def test_strips_invisible_and_control(self):
        r = mint_site("chat_human.send", {"who": "Bo" + RLO + "b" + ZWSP, "text": "hi\nthere" + BOM})
        for bad in (RLO, ZWSP, BOM, "\n"):
            self.assertNotIn(bad, r)
        self.assertIn("Bob", r)

    def test_limit_truncates(self):
        r = mint_site("chat_human.send", {"who": "w" * 100, "text": "t"})
        self.assertNotIn("w" * 33, r)
        self.assertIn("w" * 31 + "…", r)

    def test_full_not_truncated(self):
        host = "very-long-host-" + "x" * 150 + ".example.com:2222"
        r = mint_site("secret_ask.ask", {"name": "K", "dest": host})
        self.assertIsNone(r.refused)
        self.assertIn(host, r)

    def test_full_too_long_refused_and_no_window(self):
        r = mint_site("aib_approve.yes", {"code": "1", "site": "s" * (AG.FULL_MAX + 1), "user": "u", "ip": "1.2.3.4"})
        self.assertTrue(r.refused and r.refused.startswith("fact_too_long"))
        self.assertIsNone(AG.se_reason(r))
        called = []
        res = AG.ask(r, self.root, touchid=lambda t: called.append(t) or {"ok": True})
        self.assertFalse(res["ok"])
        self.assertEqual(called, [])

    def test_full_fact_still_cleaned(self):
        r = mint_site("aib_approve.yes", {"code": "1", "site": "ex" + RLO + "ample", "user": "u", "ip": "1.2.3.4"})
        self.assertIn("example", r)
        self.assertNotIn(RLO, r)

    def test_fact_clean_alone_counts_cleaned_length(self):
        """只靠「事實清字」這道:隱形字灌長的 FULL 欄位,清完才算長度 ⇒ 不被誤拒(拿掉事實清字 ⇒ 紅)。"""
        r = mint_site("aib_quote.add", {"text": ZWSP * (AG.FULL_MAX + 50) + "hello"})
        self.assertIsNone(r.refused)
        self.assertIn("hello", r)

    def test_sentence_clean_alone_cleans_template(self):
        """只靠「整句再清」這道:句子模板本身(字典)夾方向字元 ⇒ 也拿掉(拿掉整句清 ⇒ 紅)。"""
        import i18n_lib
        with mock.patch.object(i18n_lib, "t", lambda key, params=None, locale=None, **k: "ok" + RLO + " {text}".replace("{text}", (params or {}).get("text", ""))):
            r = mint_site("aib_quote.add", {"text": "hi"})
        self.assertNotIn(RLO, r)
        self.assertIn("hi", r)

    def test_downstream_limit(self):
        """整句照下游 helper 上限比:unseal 200、se-sign 320;超過就拒,不交給 helper 截(@aeb40 #404 R1 Medium)。"""
        r = mint_site("aib_quote.add", {"text": "q" * 250})
        self.assertIsNone(r.refused)
        self.assertIsNotNone(AG.se_reason(r))
        self.assertIsNone(AG.se_reason(r, AG.UNSEAL_MAX))
        self.assertIsNone(AG.se_reason(mint_site("aib_quote.add", {"text": "q" * 320})), "整句(含句頭)超過 320")

    def test_suffix_whitelist(self):
        r = mint_site("cf_grant.grant", {"zone": "z", "perms": "dns-write", "ttl": "5m", "host": "h"}, suffix=["dns_write"])
        self.assertIsNone(r.refused)
        bad = mint_site("cf_grant.grant", {"zone": "z", "perms": "p", "ttl": "5m", "host": "h"}, suffix=["made_up"])
        self.assertTrue(bad.refused)


class G5AskAndReady(Env):
    def test_busy_when_lock_held(self):
        r = mint_site("chat_human.send", {"who": "a", "text": "b"})
        with TA.one_at_a_time(AG.lock_path(self.root)) as held:
            self.assertTrue(held.held)
            res = AG.ask(r, self.root, touchid=lambda t: {"ok": True})
        self.assertFalse(res["ok"])
        self.assertEqual(res["why"], "busy")

    def test_fail_closed(self):
        r = mint_site("chat_human.send", {"who": "a", "text": "b"})
        for fake in ({"ok": False, "why": "cancel"}, {"ok": "yes"}, None, "ok", {}):
            res = AG.ask(r, self.root, touchid=lambda t, f=fake: f)
            self.assertFalse(res["ok"], fake)

    def test_ready(self):
        self.assertEqual(AG.ready({}, "linux"), "unavailable")
        self.assertEqual(AG.ready({"AIB_TOUCHID": "0"}, "darwin", lambda: True), "off")
        self.assertEqual(AG.ready({}, "darwin", lambda: False), "unavailable")
        self.assertIsNone(AG.ready({}, "darwin", lambda: True))


class G5bRefusedOnEveryPath(Env):
    def test_chat_owner_non_mac_refuses(self):
        """非 Mac 走 human_attest 那條也要先看 refused,不准因欄位超長放行(@aeb40 #404 R1 L1)。"""
        import chat_owner as CO
        bad = mint_site("aib_approve.yes", {"code": "1", "site": "s" * (AG.FULL_MAX + 1), "user": "u", "ip": "i"})
        self.assertEqual(CO.prove(bad, {}, platform="linux", attest=lambda: {"human": True}), "reason_refused")
        self.assertEqual(CO.prove(bad, {}, platform="darwin", touchid=lambda t: {"ok": True},
                                  touchid_available=lambda: True, root=self.root), "reason_refused")
        ok = mint_site("chat_human.send", {"who": "a", "text": "b"})
        self.assertIsNone(CO.prove(ok, {}, platform="linux", attest=lambda: {"human": True}))


class G6EveryCallSiteUsesGate(unittest.TestCase):
    def test_sites_used_in_their_module(self):
        for site, spec in AG.SITES.items():
            path = ROOT / "src" / spec["mod"]
            path = path if path.is_file() else ROOT / "tools" / spec["mod"]
            src = path.read_text(encoding="utf-8")
            head = site.rsplit(".", 1)[0] + "."
            self.assertTrue(('"%s"' % site) in src or ('"%s"' % head) in src, "%s 沒在 %s 用到" % (site, spec["mod"]))

    def test_no_direct_ta_ask(self):
        allowed = {"touchid_approve.py", "approve_gate.py", "consent.py"}   # consent 自己拿著鎖,字仍由 AG.reason 組
        bad = []
        for base in (ROOT / "src", ROOT / "tools"):
            for p in base.rglob("*.py"):
                if p.name in allowed:
                    continue
                if re.search(r"\b(TA|touchid_approve)\.ask\b", p.read_text(encoding="utf-8", errors="replace")):
                    bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [], "直接叫 TA.ask(沒走 approve_gate)")


class G8NamesBoundInScope(unittest.TestCase):
    """函式裡用 AG / TA,卻只在別的函式 import ⇒ 執行到才 NameError(chat 測試沒 NOSTR venv 全格跳過,全套抓不到)。
    用 symtable 看真正的作用域:區域 / 閉包(外層函式 import 的)都算有;隱含全域又沒有模組層定義 ⇒ 漏了。"""

    NAMES = ("AG", "TA")

    def _missing(self, src, filename):
        import symtable
        top = symtable.symtable(src, filename, "exec")
        bound = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported()}
        bad = []

        def walk(tab):
            for child in tab.get_children():
                if child.get_type() == "function":
                    for name in self.NAMES:
                        try:
                            sym = child.lookup(name)
                        except KeyError:
                            continue
                        if sym.is_referenced() and sym.is_global() and not sym.is_local() and name not in bound:
                            bad.append("%s:%s() %s" % (filename, child.get_name(), name))
                walk(child)
        walk(top)
        return bad

    def test_every_use_is_bound(self):
        bad = []
        for base in (ROOT / "src", ROOT / "tools"):
            for p in base.rglob("*.py"):
                src = p.read_text(encoding="utf-8", errors="replace")
                if not re.search(r"\b(AG|TA)\.", src):
                    continue
                try:
                    bad += self._missing(src, str(p.relative_to(ROOT)))
                except SyntaxError:
                    continue
        self.assertEqual(bad, [], "用到卻沒 import(執行到才 NameError)")

    def test_detects_missing(self):
        src = "def a():\n    import approve_gate as AG\n    return AG\n\ndef b():\n    return AG.reason('x')\n\n" \
              "def c():\n    import approve_gate as AG\n    def inner():\n        return AG\n    return inner\n"
        self.assertEqual(self._missing(src, "x.py"), ["x.py:b() AG"], "只有 b 漏;c 的閉包算有")


class G7Locales(unittest.TestCase):
    def test_four_locales(self):
        import json
        keys = ["aib.approve_gate.cat." + c for c in AG.CATEGORIES] + ["aib.approve_gate.unregistered",
                                                                      "aib.chat.telegram.touchid_send", "aib.chat.telegram.touchid_on",
                                                                      "aib.release.sign_approve_reason"]
        for spec in AG.SITES.values():
            keys += [spec["key"]] if spec["key"] else []
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertEqual([k for k in keys if k not in d], [], loc)

    def test_telegram_no_hardcoded_chinese(self):
        src = (COCKPIT / "chat_telegram.py").read_text(encoding="utf-8")
        self.assertNotIn("寄 Telegram 給 %s", src)
        self.assertNotIn("開啟 Telegram 轉接(", src)

    def test_en_text(self):
        with mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "en"}):
            r = mint_site("chat_telegram.send", {"who": "Amy", "text": "hello"})
        self.assertIn("Telegram", r)
        self.assertIn("Amy", r)
        self.assertEqual(AG.category_name("outbound", "en"), "Outgoing messages / sharing")


class G8Knife(unittest.TestCase):
    def test_k_drop_confirm_import(self):
        """刀:把 chat_pair._confirm 補的 import 拿掉 ⇒ G8 紅在斷言上。"""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        this = Path(__file__).name
        with knife_copy(ROOT, "src/cockpit/chat_pair.py", "    import approve_gate as AG      # R1", "    pass      # R1",
                        tests=(this,)) as copy_root:
            r = run_guarded([sys.executable, str(copy_root / "tests" / this), "G8NamesBoundInScope.test_every_use_is_bound"],
                            cwd=str(copy_root), timeout=300)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("FAIL:", out, out)
        self.assertIn("_confirm() AG", out)


if __name__ == "__main__":
    unittest.main()
