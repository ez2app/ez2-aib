#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""mod 裝好要向 Claude Code 登記,一行安裝 / aib update / aib init 自動裝附帶的免費、已簽章 mod
(維護者 10-15 23:3x「裝上並在下一版自動安裝」,0.1.9;@abb89 派)。

原因:`aib ext install <mod>` 只把檔案複製到 ~/.ez2-aib/mods/<名>,mod 沒有 engines ⇒ 沒有任何一家 AI 被通知,
Claude Code 根本不知道它在(aib-boards 要自己打 claude plugin marketplace add / install 才生效)。

全部用暫存 HOME + 假 claude(tests 裡寫的一支 python,狀態存在暫存檔),PATH 拿掉真 claude 所在的資料夾,
不碰真的 ~/.claude、~/.ez2-aib。來源是本倉 extensions/aib-boards 的複本(簽章是真的)。
A1 install aib-boards ⇒ marketplace add <mods/aib-boards> + plugin install aib-boards@ez2-aib-boards;狀態記下 claude_mod
A2 再 enable 一次(同一個資料夾、同一版已登記)⇒ 不再 add / install
A3 已裝的版本跟資料夾不同 ⇒ marketplace update + plugin update
A4 這台沒有 claude ⇒ 照實說跳過、rc 0、檔案照放
A5 disable ⇒ uninstall + marketplace remove、狀態清掉;remove ⇒ 同樣撤掉再刪資料夾
A6 auto:extensions/ 裡受信鑰匙簽過的延伸外掛都裝(mod 進 mods/、skill 進 skills/);沒簽章、要加購的不裝;
   有動到才印「每席 /restart」;再跑一次不印(改過說明檔的複本用測試鑰匙重簽,鑰匙寫進 <狀態根>/trusted-ext-keys)
A7 AIB_EXT_AUTO=0 ⇒ 什麼都不裝,說一句
A8 有舊的 aib-seat@ez2-aib-mods ⇒ 提示怎麼移除,不替人刪
A9 aib init(子行程、AIB_EXT_AUTO=1)⇒ 裝好並登記 aib-boards;不是終端機又沒設 AIB_EXT_AUTO ⇒ 不裝
A10 四語字典新鍵四份都有、佔位符相同
A11 aib-languages(只有 plugin.json、沒有 marketplace.json)⇒ aib 在 ext-engines/aib-languages/claude-mod 產生 marketplace
    ez2-aib-languages、mod 複製到 ./plugin,登記 aib-languages@ez2-aib-languages;remove ⇒ 撤登記、刪掉產生的那份
刀(同檔變體,不碰真樹):K1 mod 不登記 ⇒ A1 紅;K2 不看「已登記」⇒ A2 紅;K3 auto 不看簽章 ⇒ A6 紅;
   K4 auto 不看 AIB_EXT_AUTO ⇒ A7 紅;K5 不替沒有 marketplace.json 的 mod 產生 ⇒ A11 紅
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]          # = aib/
REPO = ROOT.parent
SRC = ROOT / "src" / "cockpit" / "aib_ext.py"
BOARDS = REPO / "extensions" / "aib-boards"
LANGS = REPO / "extensions" / "aib-languages"
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))
import aib_ext  # noqa: E402
from entitlement import ed25519, signing  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

PH = re.compile(r"\{[a-z_]+\}")
SEED = bytes(range(32))     # 測試鑰匙(只寫進暫存狀態根的 trusted-ext-keys)
NEW_KEYS = ("mod_already", "mod_not_claude", "legacy_plugin", "auto_off", "auto_unsigned", "restart_hint")
_N = [0]

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
S = Path(os.environ["FAKE_CLAUDE_STATE"])
st = json.loads(S.read_text()) if S.exists() else {"mkts": {}, "plugins": {}}
with open(str(S) + ".log", "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
def save():
    S.write_text(json.dumps(st))
def ver(path, plugin):
    try:
        mk = json.loads((Path(path) / ".claude-plugin" / "marketplace.json").read_text())
        src = [x["source"] for x in mk["plugins"] if x["name"] == plugin][0]
        return json.loads((Path(path) / src / ".claude-plugin" / "plugin.json").read_text())["version"]
    except Exception:
        return "0"
if a[:3] == ["plugin", "marketplace", "list"]:
    print(json.dumps([{"name": n, "source": "directory", "path": p, "installLocation": p} for n, p in st["mkts"].items()]))
elif a[:3] == ["plugin", "marketplace", "add"]:
    name = json.loads((Path(a[3]) / ".claude-plugin" / "marketplace.json").read_text())["name"]
    if name in st["mkts"]:
        print("marketplace already exists", file=sys.stderr); sys.exit(1)
    st["mkts"][name] = a[3]; save()
elif a[:3] == ["plugin", "marketplace", "remove"]:
    st["mkts"].pop(a[3], None); save()
elif a[:3] == ["plugin", "marketplace", "update"]:
    pass
elif a[:2] == ["plugin", "list"]:
    print(json.dumps([{"id": i, "version": v, "enabled": True} for i, v in st["plugins"].items()]))
elif a[:2] in (["plugin", "install"], ["plugin", "update"]):
    p, m = a[2].split("@", 1)
    if m not in st["mkts"]:
        print("no such marketplace", file=sys.stderr); sys.exit(1)
    st["plugins"][a[2]] = ver(st["mkts"][m], p); save()
elif a[:2] == ["plugin", "uninstall"]:
    st["plugins"].pop(a[2], None); save()
else:
    print("fake claude: unknown %r" % a, file=sys.stderr); sys.exit(2)
'''


def variant(needle, replacement):
    """aib_ext.py 換一段,以新模組名載入(__file__ 指回原檔)。錨要恰好一處。"""
    text = SRC.read_text(encoding="utf-8")
    assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
    _N[0] += 1
    mod = types.ModuleType("aib_ext_knife_%d" % _N[0])
    mod.__file__ = str(SRC)
    exec(compile(text.replace(needle, replacement), str(SRC), "exec"), mod.__dict__)
    return mod


def resign(m):
    m.pop("signature", None)
    m["signature"] = "ed25519:" + __import__("base64").b64encode(ed25519.sign(SEED, signing.canonical(m))).decode()
    return m


def clean_path(extra):
    """PATH 拿掉有真 claude 的資料夾,前面放假 claude 的資料夾(extra 可為 None = 這台沒有 claude)。"""
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not os.path.exists(os.path.join(d, "claude"))]
    return os.pathsep.join(([str(extra)] if extra else []) + keep)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ext-auto-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.state = self.tmp / "claude-state.json"
        self.env = {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.home / ".ez2-aib"),
                    "EZ2_AIB_LOCALE": "zh-TW", "FAKE_CLAUDE_STATE": str(self.state), "PATH": clean_path(self.bin)}
        self.envp = mock.patch.dict(os.environ, self.env)
        self.envp.start()
        os.environ.pop("AIB_EXT_AUTO", None)
        self.src = self.tmp / "src"
        self.src.mkdir()
        shutil.copytree(BOARDS, self.src / "aib-boards")
        self.mods = self.home / ".ez2-aib" / "mods"
        (self.home / ".ez2-aib").mkdir(parents=True, exist_ok=True)
        (self.home / ".ez2-aib" / "trusted-ext-keys").write_text("ed25519:%s\n" % ed25519.public_key(SEED).hex())

    def tearDown(self):
        self.envp.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def which(self, name):
        return shutil.which(name, path=os.environ["PATH"])

    def calls(self):
        log = Path(str(self.state) + ".log")
        return log.read_text().splitlines() if log.exists() else []

    def claude_state(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {"mkts": {}, "plugins": {}}

    def seed(self, st):
        self.state.write_text(json.dumps(st))

    def run_mod(self, mod, fn, *a, **kw):
        out = io.StringIO()
        with mock.patch.object(mod, "_which", self.which):
            rc = getattr(mod, fn)(*a, out=out, **kw)
        return rc, out.getvalue()


def check_a1(t, mod):
    rc, out = t.run_mod(mod, "install", str(t.src / "aib-boards"))
    t.assertEqual(rc, 0, out)
    dst = str(t.mods / "aib-boards")
    t.assertIn("plugin marketplace add " + dst, t.calls())
    t.assertIn("plugin install aib-boards@ez2-aib-boards", t.calls())
    t.assertEqual(t.claude_state()["mkts"].get("ez2-aib-boards"), dst)
    t.assertIn("aib-boards@ez2-aib-boards", t.claude_state()["plugins"])
    st = json.loads((t.home / ".ez2-aib" / "ext-state" / "aib-boards.json").read_text())
    t.assertEqual(st["claude_mod"], {"mkt": "ez2-aib-boards", "plugins": ["aib-boards"]})


def check_a2(t, mod):
    check_a1(t, aib_ext)
    n = len(t.calls())
    rc, out = t.run_mod(mod, "enable", "aib-boards")
    t.assertEqual(rc, 0, out)
    later = t.calls()[n:]
    t.assertFalse([c for c in later if c.startswith(("plugin marketplace add", "plugin install", "plugin update"))], later)
    t.assertIn("已登記", out)


def check_a6(t, mod):
    # 沒簽章的 mod、要加購的 mod、skill:都不該被 auto 裝
    for nm, edit in (("aib-unsigned", lambda m: (m.pop("signature", None), m)[1]),
                     ("aib-paid", lambda m: resign(dict(m, requires_addon="aib-paid"))),
                     ("aib-skilly", lambda m: resign(dict(m, kind="skill")))):
        d = t.src / nm
        shutil.copytree(BOARDS, d)
        m = json.loads((d / "aib-ext.json").read_text(encoding="utf-8"))
        m["name"] = nm
        m = edit(m) or m
        (d / "aib-ext.json").write_text(json.dumps(m), encoding="utf-8")
    rc, out = t.run_mod(mod, "auto_install", src=str(t.src))
    t.assertEqual(rc, 0, out)
    t.assertEqual(sorted(p.name for p in t.mods.iterdir()), ["aib-boards"])
    t.assertEqual(sorted(p.name for p in (t.home / ".ez2-aib" / "skills").iterdir()), ["aib-skilly"])
    t.assertIn("aib-unsigned", out)            # 沒簽章要說為什麼沒裝
    t.assertEqual(out.count("/restart"), 1, out)
    rc, out2 = t.run_mod(mod, "auto_install", src=str(t.src))
    t.assertEqual(rc, 0, out2)
    t.assertNotIn("/restart", out2)


def check_a11(t, mod):
    shutil.copytree(LANGS, t.src / "aib-languages")
    rc, out = t.run_mod(mod, "install", str(t.src / "aib-languages"))
    t.assertEqual(rc, 0, out)
    gen = t.home / ".ez2-aib" / "ext-engines" / "aib-languages" / "claude-mod"
    t.assertEqual(t.claude_state()["mkts"].get("ez2-aib-languages"), str(gen), out)
    t.assertEqual(t.claude_state()["plugins"].get("aib-languages@ez2-aib-languages"), "0.1.0")
    t.assertTrue((gen / "plugin" / ".claude-plugin" / "plugin.json").is_file())
    n = len(t.calls())
    t.run_mod(mod, "enable", "aib-languages")                     # 再一次:已登記同一份 ⇒ 不再 add / install
    t.assertFalse([c for c in t.calls()[n:] if c.startswith(("plugin marketplace add", "plugin install", "plugin update"))])
    rc, out = t.run_mod(mod, "remove", "aib-languages")
    t.assertEqual(rc, 0, out)
    t.assertEqual(t.claude_state(), {"mkts": {}, "plugins": {}})
    t.assertFalse(gen.exists())


def check_a7(t, mod):
    rc, out = t.run_mod(mod, "auto_install", src=str(t.src), env=dict(os.environ, AIB_EXT_AUTO="0"))
    t.assertEqual(rc, 0)
    t.assertFalse(t.mods.exists() and any(t.mods.iterdir()))
    t.assertEqual(t.calls(), [])
    t.assertIn("AIB_EXT_AUTO=0", out)


class Register(Base):
    def test_a1_install_registers_with_claude(self):
        check_a1(self, aib_ext)

    def test_a2_already_registered_is_left_alone(self):
        check_a2(self, aib_ext)

    def test_a3_version_change_updates(self):
        check_a1(self, aib_ext)
        st = self.claude_state()
        st["plugins"]["aib-boards@ez2-aib-boards"] = "0.0.1"
        self.seed(st)
        n = len(self.calls())
        rc, out = self.run_mod(aib_ext, "enable", "aib-boards")
        self.assertEqual(rc, 0, out)
        self.assertIn("plugin update aib-boards@ez2-aib-boards", self.calls()[n:])
        self.assertEqual(self.claude_state()["plugins"]["aib-boards@ez2-aib-boards"], "0.2.0")

    def test_a4_no_claude_skips(self):
        os.environ["PATH"] = clean_path(None)
        rc, out = self.run_mod(aib_ext, "install", str(self.src / "aib-boards"))
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.mods / "aib-boards" / "aib-ext.json").is_file())
        self.assertIn("claude", out)
        self.assertEqual(self.calls(), [])

    def test_a5_disable_and_remove_undo(self):
        check_a1(self, aib_ext)
        rc, out = self.run_mod(aib_ext, "disable", "aib-boards")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.claude_state(), {"mkts": {}, "plugins": {}})
        self.assertIn("plugin uninstall aib-boards@ez2-aib-boards", self.calls())
        self.assertTrue((self.mods / "aib-boards").is_dir())       # disable 不刪 mod 資料夾
        self.assertFalse((self.home / ".ez2-aib" / "ext-state" / "aib-boards.json").exists())
        rc, out = self.run_mod(aib_ext, "enable", "aib-boards")
        self.assertIn("aib-boards@ez2-aib-boards", self.claude_state()["plugins"])
        rc, out = self.run_mod(aib_ext, "remove", "aib-boards")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.claude_state(), {"mkts": {}, "plugins": {}})
        self.assertFalse((self.mods / "aib-boards").exists())

    def test_a6_auto_only_free_signed_mods(self):
        check_a6(self, aib_ext)

    def test_a7_auto_off(self):
        check_a7(self, aib_ext)

    def test_a11_plugin_json_only_mod_gets_generated_marketplace(self):
        check_a11(self, aib_ext)

    def test_a8_legacy_hint_only(self):
        self.seed({"mkts": {"ez2-aib-mods": "/somewhere/old"}, "plugins": {"aib-seat@ez2-aib-mods": "0.10.0"}})
        rc, out = self.run_mod(aib_ext, "install", str(self.src / "aib-boards"))
        self.assertEqual(rc, 0, out)
        self.assertIn("aib-seat@ez2-aib-mods", out)
        self.assertIn("claude plugin uninstall aib-seat@ez2-aib-mods", out)
        self.assertIn("aib-seat@ez2-aib-mods", self.claude_state()["plugins"])   # 只提示,不替人刪
        self.assertNotIn("plugin uninstall aib-seat@ez2-aib-mods", self.calls())


class Init(Base):
    def run_init(self, auto):
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "LANG": "en_US.UTF-8",
               "EZ2_AIB_LOCALE": "zh-TW", "FAKE_CLAUDE_STATE": str(self.state)}
        if auto is not None:
            env["AIB_EXT_AUTO"] = auto
        r = run_guarded([sys.executable, str(ROOT / "src" / "bootstrap" / "aib_init.py")], timeout=180, env=env,
                        stdin=subprocess.DEVNULL)
        self.assertIsNone(r.tripped)
        return r

    def test_a9_init_auto_installs_boards(self):
        r = self.run_init("1")
        self.assertIn("aib-boards", r.stdout, r.stdout + r.stderr)
        self.assertTrue((self.mods / "aib-boards" / "aib-ext.json").is_file(), r.stdout + r.stderr)
        self.assertIn("aib-boards@ez2-aib-boards", self.claude_state()["plugins"])
        self.assertIn("aib-languages@ez2-aib-languages", self.claude_state()["plugins"])
        self.assertTrue((self.home / ".ez2-aib" / "skills" / "ez2-page-oneshot" / "SKILL.md").is_file())
        self.assertTrue((self.home / ".ez2-aib" / "skills" / "aib-cabin-regroup" / "SKILL.md").is_file())
        self.assertIn("/restart", r.stdout)

    def test_a9b_init_from_script_without_flag_does_not(self):
        r = self.run_init(None)
        self.assertFalse((self.mods / "aib-boards").exists(), r.stdout)
        self.assertEqual(self.calls(), [])


class I18n(unittest.TestCase):
    def test_a10_four_locales(self):
        d = {loc: json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8")) for loc in ("en", "zh-TW", "ja", "ko")}
        for k in NEW_KEYS:
            key = "aib.ext." + k
            for loc in d:
                self.assertIn(key, d[loc], (loc, key))
                self.assertEqual(sorted(PH.findall(d[loc][key])), sorted(PH.findall(d["zh-TW"][key])), (loc, key))


class Knives(Base):
    def test_k1_mod_not_registered(self):
        mod = variant("        return claude_register(name, pkg, out, changes)\n", "        return 0\n")
        with self.assertRaises(AssertionError):
            check_a1(self, mod)

    def test_k2_ignore_already_registered(self):
        mod = variant('    same = bool(cur) and os.path.realpath(str(cur.get("path") or cur.get("installLocation") or "")) == here\n',
                      "    same = False\n")
        with self.assertRaises(AssertionError):
            check_a2(self, mod)

    def test_k3_auto_ignores_signature(self):
        mod = variant("        if not signed_ok(m):\n            out.write(T(\"auto_unsigned\"",
                      "        if False:\n            out.write(T(\"auto_unsigned\"")
        with self.assertRaises(AssertionError):
            check_a6(self, mod)

    def test_k5_no_generated_marketplace(self):
        mod = variant("        gen = _generated_marketplace(name, pkg)\n", "        gen = None\n")
        with self.assertRaises(AssertionError):
            check_a11(self, mod)

    def test_k4_auto_ignores_off_switch(self):
        mod = variant('    if env.get("AIB_EXT_AUTO", "") == "0":\n', "    if False:\n")
        with self.assertRaises(AssertionError):
            check_a7(self, mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
