#!/usr/bin/env python3
"""approve_gate 類別收緊(10-29 TID-2a;@aeb40 #404 R1 L2,02:58 裁 Q1–Q4)。時間窗(TID-2)會拿類別做決定,先把三個洞補起來。

L1 類別不在 Reason 上:Reason 沒有可寫的屬性;site / category / refused 查登記簿(認物件本身,不認同樣的字)
   ⇒ 事後改 .category、自己 Reason(...) 造、同字新物件 都當沒登記        (刀 KA1 登記簿查不到也放行、KA2 改成比字 ⇒ 紅)
L2 純字串交給 ask() / se_reason() / chat_owner.prove() 直接拒(reason_refused),指紋視窗不彈  (刀 KA1 ⇒ 紅)
L3 reason() 沒有 _depth:只看直接叫它的那一層                                                     (刀 KB 加回參數 ⇒ 紅)
L4 路徑比完整真路徑:別處同尾端的檔 ⇒ 未登記;符號連結 / .. 指到真檔 ⇒ 照登記                (刀 KC1 改回 endswith、KC2 拿掉 resolve ⇒ 紅)
L5 那層要是 sys.modules 裡那支模組本身:真檔名 compile 但 globals 是別的 dict / 模組 __file__ 不是那支 ⇒ 未登記
                                                                                                    (刀 KC3 拿掉 globals 比對、KC4 拿掉 __file__ 比對 ⇒ 紅)
L6 真模組照常:真的 import aib_quote 去組 ⇒ quote 類;新登記的 auto_update.exclude / chat_phone_forward.loosen 是高風險
L7 靜態掃描:src / tools 裡 prove / ask / se_reason 交純字串(字面、f 字串、T(...)、format…)或叫 coerce() ⇒ 紅
                                                                                                    (刀 KS1 chat_owner 加回 coerce、KS2 auto_update 改回 T(...) ⇒ 紅)
L8 登記簿有上限,丟掉的那句再交給 ask ⇒ 拒;丟最舊那段在鎖裡做(多執行緒不交錯)              (刀 KR1 拿掉鎖 ⇒ 紅)
L10 #413 r1:已登記但被拒的 Reason,ask() 照樣回它真的類別 / site(紀錄不誤導)                   (刀 KR3 ⇒ 紅)
    ask() 統一擋 LA_MAX:整句超過就拒、不彈窗(不交給視窗去截)                                (刀 KR4 ⇒ 紅)
    靜態掃描:(prove or SA.prove)("x") 這種包起來的、別名 PROVE 也抓(先放進變數再交的不抓,要資料流)

說明照實寫:這些只防「核心程式裡低類別冒用 / 漏登記 / 寫錯」。行程內的程式做得到 mint_as 一樣的事(L5 那個 helper 就是示範),
同帳號 AI 也改得到 approve_gate.py;真正的防線仍是指紋 / 登入密碼本身,之後的時間窗授權單由 Secure Enclave 簽。
"""
import ast
import inspect
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

SITE, FACTS = "seat_act.input", {"seat": "abcde", "text": "x"}     # 可開窗類(seat_input)


def real_path(rel):
    p = ROOT / "src" / rel
    return p if p.is_file() else ROOT / "tools" / rel


def run_in(filename, site, facts=None, file_attr=None, globals_override=None, suffix=()):
    """在 sys.modules 放一個模組(__file__ 預設 = filename),用 filename compile 去叫 AG.reason。"""
    name = "aibtest_l2_%d" % id(object())
    mod = types.ModuleType(name)
    mod.__file__ = str(file_attr if file_attr is not None else filename)
    mod.AG = AG
    sys.modules[name] = mod
    g = mod.__dict__ if globals_override is None else globals_override(name)
    try:
        exec(compile("r = AG.reason(%r, %r, %r)" % (site, facts or {}, tuple(suffix)), str(filename), "exec"), g)
    finally:
        sys.modules.pop(name, None)
    return g["r"]


def mint_site(site=SITE, facts=None, suffix=()):
    return run_in(real_path(AG.SITES[site]["mod"]), site, facts if facts is not None else FACTS, suffix=suffix)


class Env(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-gate-l2-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root),
                                         "EZ2_AIB_LOCALE": "en", "HOME": str(self.root)})
        p.start()
        self.addCleanup(p.stop)

    def no_window(self, r):
        seen = []
        res = AG.ask(r, self.root, touchid=lambda t: seen.append(t) or {"ok": True})
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["why"], "refused")
        self.assertEqual(seen, [], "不准彈指紋視窗")
        self.assertIsNone(AG.se_reason(r))
        return res


class L1CategoryNotOnReason(Env):
    def test_registered_baseline(self):
        r = mint_site()
        self.assertEqual(r.category, "seat_input")
        self.assertIsNone(AG.refusal(r))
        res = AG.ask(r, self.root, touchid=lambda t: {"ok": True})
        self.assertTrue(res["ok"])
        self.assertEqual((res["category"], res["site"]), ("seat_input", SITE))

    def test_cannot_rewrite_attributes(self):
        r = AG.reason("nope.nothing")                     # 未登記(高風險)
        for attr, val in (("category", "command"), ("site", SITE), ("refused", None)):
            with self.assertRaises(AttributeError, msg=attr):
                setattr(r, attr, val)
            with self.assertRaises(AttributeError, msg=attr):
                object.__setattr__(r, attr, val)
        with self.assertRaises(AttributeError):
            r.__dict__                                    # noqa: B018 —— 沒有 __dict__ 可塞
        self.assertEqual(r.category, AG.UNREGISTERED)

    def test_self_made_reason_refused(self):
        r = AG.Reason("Allow typing into seat abcde")
        self.assertEqual(r.category, AG.UNREGISTERED)
        self.assertEqual(AG.refusal(r), AG.UNMINTED)
        self.assertEqual(self.no_window(r)["detail"], AG.UNMINTED)

    def test_same_text_new_object_refused(self):
        good = mint_site()
        twin = AG.Reason(str(good))
        self.assertEqual(twin, good)                      # 字一樣
        self.assertEqual(twin.category, AG.UNREGISTERED)  # 但不是登記的那個物件
        self.no_window(twin)
        self.assertIsNone(AG.refusal(good))


class L2PlainStringRefused(Env):
    def test_ask_and_se_reason(self):
        self.assertEqual(self.no_window("Allow aib to auto-update the plugin x")["detail"], AG.UNMINTED)
        self.assertEqual(self.no_window(T_SUB("x"))["detail"], AG.UNMINTED)

    def test_chat_owner_prove(self):
        import chat_owner as CO
        for platform in ("darwin", "linux"):
            self.assertEqual(CO.prove("plain", {}, platform=platform, touchid=lambda t: {"ok": True},
                                      touchid_available=lambda: True, attest=lambda: {"human": True}, root=self.root),
                             "reason_refused", platform)

    def test_coerce_is_test_only_but_still_high_risk(self):
        r = AG.coerce("do something")
        self.assertEqual(r.category, AG.UNREGISTERED)
        self.assertTrue(r.high_risk)
        self.assertIsNone(AG.refusal(r))


class T_SUB(str):
    """別的 str 子類別(不是 Reason)也一樣拒。"""


class L3NoDepth(Env):
    def test_signature(self):
        self.assertNotIn("_depth", inspect.signature(AG.reason).parameters)
        with self.assertRaises(TypeError):
            AG.reason(SITE, FACTS, _depth=2)              # noqa

    def test_helper_outside_cannot_borrow_caller(self):
        """登記的那支叫一個外面的 helper,helper 去組 ⇒ 直接叫的是 helper ⇒ 未登記。"""
        name = "aibtest_l2_helper"
        helper = types.ModuleType(name)
        helper.__file__ = str(self.root / "helper.py")
        helper.AG = AG
        sys.modules[name] = helper
        self.addCleanup(sys.modules.pop, name, None)
        exec(compile("def mk(site, facts):\n    return AG.reason(site, facts)\n", helper.__file__, "exec"), helper.__dict__)
        mod_name = "aibtest_l2_caller"
        caller = types.ModuleType(mod_name)
        caller.__file__ = str(real_path(AG.SITES[SITE]["mod"]))
        caller.mk = helper.mk
        sys.modules[mod_name] = caller
        self.addCleanup(sys.modules.pop, mod_name, None)
        exec(compile("r = mk(%r, %r)" % (SITE, FACTS), caller.__file__, "exec"), caller.__dict__)
        r = caller.__dict__["r"]
        self.assertEqual(r.category, AG.UNREGISTERED)


class L4FullPath(Env):
    def test_same_tail_elsewhere_is_unregistered(self):
        fake = self.root / "aib" / "src" / AG.SITES[SITE]["mod"]
        fake.parent.mkdir(parents=True)
        fake.write_text("# fake\n", encoding="utf-8")
        self.assertEqual(run_in(fake, SITE, FACTS).category, AG.UNREGISTERED)

    def test_symlink_and_dotdot_to_real_file_count(self):
        real = real_path(AG.SITES[SITE]["mod"])
        link = self.root / "link.py"
        link.symlink_to(real)
        self.assertEqual(run_in(link, SITE, FACTS).category, "seat_input", "符號連結指到真檔")
        dotted = real.parent / ".." / real.parent.name / real.name
        self.assertEqual(run_in(dotted, SITE, FACTS).category, "seat_input", ".. 指到真檔")

    def test_tools_module(self):
        self.assertEqual(mint_site("release.sign_approve", {"plugin": "p", "ver": "1", "pin": "abc"}).category, "release_sign")
        self.assertEqual(AG.module_paths("release/sign_approve.py"), {(ROOT / "tools" / "release" / "sign_approve.py").resolve()})


class L5ModuleIdentity(Env):
    def test_foreign_globals_with_real_filename(self):
        """compile 用真檔名,但 globals 是另一個 dict(__name__ 指到 sys.modules 裡那支) ⇒ 未登記。"""
        real = real_path(AG.SITES[SITE]["mod"])
        r = run_in(real, SITE, FACTS, globals_override=lambda name: {"__name__": name, "AG": AG})
        self.assertEqual(r.category, AG.UNREGISTERED)

    def test_plain_dict_globals(self):
        real = real_path(AG.SITES[SITE]["mod"])
        r = run_in(real, SITE, FACTS, globals_override=lambda name: {"__name__": "aibtest_not_loaded", "AG": AG})
        self.assertEqual(r.category, AG.UNREGISTERED)

    def test_module_file_elsewhere(self):
        real = real_path(AG.SITES[SITE]["mod"])
        r = run_in(real, SITE, FACTS, file_attr=self.root / "other.py")
        self.assertEqual(r.category, AG.UNREGISTERED)


class L6RealModules(Env):
    def test_real_import_gets_category(self):
        import aib_quote
        r = aib_quote.touchid_reason("hello")
        self.assertEqual(r.category, "quote")
        self.assertIsNone(AG.refusal(r))

    def test_new_sites_high_risk(self):
        for site, facts in (("auto_update.exclude", {"name": "captain-room"}),
                            ("chat_phone_forward.loosen", {"minutes": 30, "per_hour": 4})):
            r = mint_site(site, facts)
            self.assertNotEqual(r.category, AG.UNREGISTERED, site)
            self.assertTrue(r.high_risk, site)
            self.assertIsNone(AG.refusal(r), site)
        self.assertIn("captain-room", mint_site("auto_update.exclude", {"name": "captain-room"}))
        long = "x" * (AG.FULL_MAX + 1)
        self.assertTrue(AG.refusal(mint_site("auto_update.exclude", {"name": long})), "外掛名不截,太長直接拒")


# 交給這些的第一個(或指定位置)參數必須是 reason() 組好的,不准是字串字面 / f 字串 / T(...) / format…
GATED = {"prove": 0, "_prove": 0, "PROVE": 0, "se_reason": 0, "human_gate": 0, "_proof_then_pass": 0, "sign_list_change": 2}
AG_NAMES = {"AG", "approve_gate"}
STRINGY_CALLS = {"T", "_", "t", "tr", "str", "format", "join", "replace", "strip"}


def _is_ag(node):
    if isinstance(node, ast.Name):
        return node.id in AG_NAMES
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__"
            and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "approve_gate")


def _stringy(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, (ast.JoinedStr, ast.BinOp)):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        return name in STRINGY_CALLS
    return False


def _funcs(f):
    """被叫的那個東西;(prove or SA.prove)(...) 這種 BoolOp 拆開每一個(#413 r1 Low-2)。"""
    if isinstance(f, ast.BoolOp):
        return [g for v in f.values for g in _funcs(v)]
    return [f]


def scan(src, filename):
    """只抓「直接交字面」那種;先放進變數再交的要資料流,不抓(誤判多)。執行時照樣會拒。"""
    bad = []
    for node in ast.walk(ast.parse(src, filename)):
        if not isinstance(node, ast.Call):
            continue
        for f in _funcs(node.func):
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name == "coerce" and isinstance(f, ast.Attribute) and _is_ag(f.value):
                bad.append("%s:%d coerce()" % (filename, node.lineno))
                break
            idx = 0 if (name == "ask" and isinstance(f, ast.Attribute) and _is_ag(f.value)) else GATED.get(name)
            if idx is not None and len(node.args) > idx and _stringy(node.args[idx]):
                bad.append("%s:%d %s(純字串)" % (filename, node.lineno, name))
                break
    return bad


class L7StaticScan(unittest.TestCase):
    def test_no_plain_string_or_coerce(self):
        bad = []
        for base in (ROOT / "src", ROOT / "tools"):
            for p in base.rglob("*.py"):
                if p.name == "approve_gate.py":
                    continue
                src = p.read_text(encoding="utf-8", errors="replace")
                if not re.search(r"\b(prove|_prove|ask|se_reason|human_gate|_proof_then_pass|sign_list_change|coerce)\(", src):
                    continue
                try:
                    bad += scan(src, str(p.relative_to(ROOT)))
                except SyntaxError:
                    continue
        self.assertEqual(bad, [], "交純字串給本人核准 / 正式路徑叫 coerce")

    def test_scanner_catches(self):
        src = ("def a(env):\n    return prove(T('k', name=1), env)\n"
               "def b(env):\n    return AG.ask('hi', env)\n"
               "def c(env):\n    return __import__('approve_gate').se_reason(f'x{env}')\n"
               "def d(r):\n    return AG.coerce(r, 120)\n"
               "def e(env, out):\n    return OC.sign_list_change(env, out, 'x' + 'y', None)\n"
               "def f(env, prove=None):\n    return (prove or SA.prove)(T('k'), env)\n"
               "def g(env):\n    return PROVE('x', env)\n"
               "def ok(env, prove=None):\n    r = AG.reason('s', {})\n    A.ask('claude', 'q')\n"
               "    (prove or SA.prove)(r, env)\n    return prove(r, env)\n")
        got = [b.split(" ", 1)[1] for b in scan(src, "x.py")]
        self.assertEqual(got, ["prove(純字串)", "ask(純字串)", "se_reason(純字串)", "coerce()", "sign_list_change(純字串)",
                               "prove(純字串)", "PROVE(純字串)"])


class L8RegistryCap(Env):
    def test_evicted_refused(self):
        with mock.patch.object(AG, "_MINTED_MAX", 3), mock.patch.dict(AG._MINTED, clear=True):
            first = mint_site()
            for _ in range(3):
                mint_site()
            self.assertLessEqual(len(AG._MINTED), 3)
            self.assertEqual(AG.refusal(first), AG.UNMINTED)
            self.no_window(first)


class _LockCheckingDict(dict):
    def __len__(self):
        if not AG._MINT_LOCK.locked():
            raise AssertionError("丟最舊那段沒在鎖裡")
        return dict.__len__(self)


class L10R1Followups(Env):
    def test_evict_under_lock(self):
        with mock.patch.object(AG, "_MINTED", _LockCheckingDict()), mock.patch.object(AG, "_MINTED_MAX", 2):
            for _ in range(5):
                mint_site()
            self.assertLessEqual(dict.__len__(AG._MINTED), 2)

    def test_threads_no_exception(self):
        import threading
        errs = []

        def work():
            try:
                for _ in range(300):
                    AG.coerce("x")
            except Exception as exc:  # noqa: BLE001
                errs.append(repr(exc))
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            with mock.patch.object(AG, "_MINTED_MAX", 8), mock.patch.dict(AG._MINTED, clear=True):
                ts = [threading.Thread(target=work) for _ in range(8)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(60)
        finally:
            sys.setswitchinterval(old)
        self.assertEqual(errs, [])

    def test_refused_keeps_real_category(self):
        bad = mint_site("auto_update.exclude", {"name": "n" * (AG.FULL_MAX + 1)})
        res = self.no_window(bad)
        self.assertEqual((res["category"], res["site"]), ("auto_update", "auto_update.exclude"))
        self.assertTrue(res["detail"].startswith("fact_too_long"))

    def test_ask_refuses_over_la_max(self):
        r = mint_site("auto_update.exclude", {"name": "n" * (AG.LA_MAX + 1)})    # 欄位沒超 FULL_MAX,整句超過視窗
        self.assertIsNone(AG.refusal(r))
        seen = []
        res = AG.ask(r, self.root, touchid=lambda t: seen.append(t) or {"ok": True})
        self.assertEqual((res["ok"], res.get("detail"), seen), (False, "too_long", []))
        self.assertEqual(res["category"], "auto_update")
        ok = mint_site("auto_update.exclude", {"name": "captain-room"})
        self.assertTrue(AG.ask(ok, self.root, touchid=lambda t: {"ok": True})["ok"])


class L9Knife(unittest.TestCase):
    """每把刀落在複本,跑上面指定那格 ⇒ 要紅。"""
    GATE = "src/cockpit/approve_gate.py"
    KNIVES = (
        ("KA1", GATE, "    if e is None:\n        return UNMINTED", "    if e is None:\n        return None",
         "L1CategoryNotOnReason.test_self_made_reason_refused"),
        ("KA1b", GATE, "    if e is None:\n        return UNMINTED", "    if e is None:\n        return None",
         "L2PlainStringRefused.test_ask_and_se_reason"),
        ("KA2", GATE, "    return e if e is not None and e[0] is r else None",
         "    return next((v for v in _MINTED.values() if v[0] == r), None)",
         "L1CategoryNotOnReason.test_same_text_new_object_refused"),
        ("KB", GATE, "def reason(site, facts=None, suffix=(), env=None):", "def reason(site, facts=None, suffix=(), env=None, _depth=1):",
         "L3NoDepth.test_signature"),
        ("KC1", GATE, '    if here is None or here not in module_paths(spec["mod"]):',
         '    if here is None or not here.as_posix().endswith("/" + spec["mod"]):',
         "L4FullPath.test_same_tail_elsewhere_is_unregistered"),
        ("KC2", GATE, "        return Path(path).resolve() if path else None", "        return Path(path) if path else None",
         "L4FullPath.test_symlink_and_dotdot_to_real_file_count"),
        ("KC3", GATE, '    if mod is None or getattr(mod, "__dict__", None) is not g:', "    if mod is None:",
         "L5ModuleIdentity.test_foreign_globals_with_real_filename"),
        ("KC4", GATE, '    if _resolve(getattr(mod, "__file__", None)) != here:', "    if False:",
         "L5ModuleIdentity.test_module_file_elsewhere"),
        ("KS1", "src/cockpit/chat_owner.py", "    if AG.refusal(reason):", "    reason = AG.coerce(reason, 120)\n    if AG.refusal(reason):",
         "L7StaticScan.test_no_plain_string_or_coerce"),
        ("KR1", GATE, "    with _MINT_LOCK:\n        while len(_MINTED) >= _MINTED_MAX:", "    if True:\n        while len(_MINTED) >= _MINTED_MAX:",
         "L10R1Followups.test_evict_under_lock"),
        ("KR3", GATE, '"category": e[2] if e else UNREGISTERED, "site": e[1] if e else None}', '"category": UNREGISTERED, "site": None}',
         "L10R1Followups.test_refused_keeps_real_category"),
        ("KR4", GATE, '    no = refusal(r) or (None if len(str(r)) <= LA_MAX else "too_long")', "    no = refusal(r)",
         "L10R1Followups.test_ask_refuses_over_la_max"),
        ("KM1", GATE, '{"cabin": FULL, "role": FULL, "persona": 36, "project": 36}',
         '{"cabin": FULL, "role": FULL, "persona": 80, "project": 80}',
         "L11FitsLaMax.test_every_ask_site_fits_in_four_locales"),
        ("KS2", "src/cockpit/auto_update.py", "        _via, why = prove(shown, env, platform=platform)",
         '        _via, why = prove(T("exclude.touchid_reason", name=name), env, platform=platform)',
         "L7StaticScan.test_no_plain_string_or_coerce"),
    )

    def test_knives(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from aib_knife_copy import knife_copy
        from aib_proc_guard import run_guarded
        this = Path(__file__).name
        for kid, rel, needle, repl, cell in self.KNIVES:
            with self.subTest(kid):
                with knife_copy(ROOT, rel, needle, repl, tests=(this,)) as copy_root:
                    r = run_guarded([sys.executable, "-B", str(copy_root / "tests" / this), cell],
                                    cwd=str(copy_root), timeout=300)
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒紅:%s" % (kid, out[-800:]))
                self.assertRegex(out, r"(FAIL|ERROR): test_", kid)


# 走 ask()(LAContext 視窗,整句 ≤ LA_MAX)的登記處:不截欄位填「實際最長值」,有上限的欄位填滿(#420 r1 M1)。
# chat_owner_cli.list_change 不列:diff 就是要本人看的內容,太長 ⇒ 拒是設計(不截)。
REAL_MAX = {
    "seat": "96e0/e339d", "min": "1440", "cabin": "20261028-153924-96e0", "role": "x" * 12,
    "goals": "99", "red_lines": "99", "rl": "99", "add": "99", "rm": "99", "code": "0123abcd",
    "who": "W" * 32, "hours": "24", "until": "10-29 23:59",
    "plugin": "captain-room-extra", "ver": "0.1.100", "pin": "0123456789ab",
    "dest": "eu01.example.com:/home/ez2/.config/aib/secrets",
}
ASK_SITES = (
    ("seat_act.input", ("tail",)), ("seat_act.unlock", ()), ("seat_act.answer", ()), ("seat_act.stop", ()),
    ("seat_act.stop_hard", ()), ("seat_act.remote_on", ()), ("chat_human.send", ()), ("secret_ask.ask", ()),
    ("chat_cast.issue", ("pin",)), ("release.sign_approve", ()), ("newproj.approve", ()),
    ("newproj.approve_amend", ()), ("newproj.boot_confirm", ()),
    ("chat_owner_cli.protect", ()), ("chat_owner_cli.repair", ()), ("chat_owner_cli.off_owner", ()),
    ("approve_grants.lockwatch_on", ()), ("secret_share.copy", ()), ("secret_share.paste", ()),
)


class L11FitsLaMax(Env):
    def test_every_ask_site_fits_in_four_locales(self):
        for site, suffix in ASK_SITES:
            facts = {}
            for k, lim in AG.SITES[site]["facts"].items():
                facts[k] = REAL_MAX.get(k, "W" * 24) if lim == AG.FULL else "W" * (lim + 20)
            for loc in ("zh-TW", "en", "ja", "ko"):
                with self.subTest(site=site, loc=loc), mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": loc}):
                    r = mint_site(site, facts, suffix=suffix)
                    self.assertIsNone(AG.refusal(r), site)
                    self.assertLessEqual(len(str(r)), AG.LA_MAX, "%s %s %d 字" % (site, loc, len(str(r))))

    def test_too_long_hint(self):
        self.assertIsNone(AG.too_long_hint({"ok": False, "why": "refused", "detail": "unminted"}))
        self.assertIsNone(AG.too_long_hint({"ok": True}))
        for loc in ("zh-TW", "en", "ja", "ko"):
            h = AG.too_long_hint({"ok": False, "why": "refused", "detail": "too_long"}, env={"EZ2_AIB_LOCALE": loc})
            self.assertTrue(h and "aib.approve_gate" not in h, (loc, h))

    def test_secret_ask_too_long_keeps_code_adds_hint(self):
        import secret_ask
        seen, out = [], {}
        why = secret_ask.prove_human("CF_TOKEN", "h:/" + "p" * 300, dict(os.environ), "darwin",
                                     touchid=lambda r: seen.append(r) or {"ok": True}, touchid_available=lambda: True, out=out)
        self.assertEqual((why, seen), ("touchid_refused", []))        # 代碼不換、沒彈窗
        self.assertTrue(out.get("hint"))


if __name__ == "__main__":
    unittest.main()
