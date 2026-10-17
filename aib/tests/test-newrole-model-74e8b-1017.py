#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newrole --new-seat --model(10-17 the maintainer:艦長室「開新席」要能選角色與模型)。
全在 tmp 沙盒:HOME / 狀態根 / vault 在 tmp;開席腳本換成假的(記下參數、寫一個新席位檔),不碰 tmux、不碰真艙。
M1 open_new_seat 帶 model ⇒ 開席指令多 --model <model>;沒帶 ⇒ 不加(那家預設)。
M2 模型名稱不合(- 開頭、空白、太長)⇒ NewSeatError,開席指令沒跑。
M3 CLI:--model 沒配 --new-seat ⇒ rc 2、什麼都沒寫;--new-seat 配壞 model ⇒ rc 2、沒開席;好的 model 原樣傳給 open_new_seat。
刀:K1 開席指令不帶 --model ⇒ M1 紅;K2 不驗模型名稱 ⇒ M2 紅。
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent
SRC = ROOT / "src" / "identity" / "create_and_bind_persona.py"
PROMPT = "你是測試角色。\n\n## 職責\n1. 測試\n\n## 知識域\n- 測試\n\n## 紅線\n- 不寫真庫\n"
_N = [0]


def load(needle=None, replacement=None):
    text = SRC.read_text(encoding="utf-8")
    if needle is not None:
        assert text.count(needle) == 1, "刀的錨要恰好一處:%r" % needle[:60]
        text = text.replace(needle, replacement)
    _N[0] += 1
    mod = types.ModuleType("cabp_model_%d" % _N[0])
    mod.__file__ = str(SRC)
    sys.modules[mod.__name__] = mod
    exec(compile(text, str(SRC), "exec"), mod.__dict__)
    return mod


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-model-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._saved = dict(os.environ)
        for k in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT_SPEC", "TMUX", "TMUX_PANE",
                  "EZ2_OWNER_SIG", "EZ2_AIB_INSTANCE", "EZ2_AIB_INSTANCE_HOME", "AIB_NEWSEAT_LAUNCHER"):
            os.environ.pop(k, None)
        os.environ.update({"HOME": self.home, "EZ2_AIB_HOME": os.path.join(self.home, ".ez2-aib"),
                           "EZ2_AIB_STATE_ROOT": os.path.join(self.home, ".ez2-aib"), "EZ2_AIB_LOCALE": "zh-TW",
                           "EZ2_EZ2AI_REPO": os.path.join(self.tmp, "no-ez2-ai"),
                           "AIB_GENES": "0", "AIB_AUTO_BIND": "0"})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_launcher(self, seats):
        """假的開席腳本:參數寫進 argv.txt,在 seats/ 寫一個帶新 session_uuid 的席位檔"""
        p = os.path.join(self.tmp, "fake-aib.sh")
        self.argv_file = os.path.join(self.tmp, "argv.txt")
        with open(p, "w") as f:
            f.write('#!/bin/bash\nprintf "%%s\\n" "$@" > %s\nprintf \'{"session_uuid":"u-new","seat_id":"abc12"}\' > %s/w9.json\n'
                    % (self.argv_file, seats))
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        os.environ["AIB_NEWSEAT_LAUNCHER"] = p

    def open_seat(self, mod, model):
        home = pathlib.Path(self.tmp) / "aibhome"
        seats = home / "instances" / "6ddf" / "seats"
        seats.mkdir(parents=True)
        self.fake_launcher(seats)
        with mock.patch.object(mod, "AIB_HOME", home), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return mod.open_new_seat("qa", engine="claude", cabin="6ddf", timeout=2, interval=0.01, model=model)

    def launched(self):
        if not os.path.exists(self.argv_file):
            return None
        with open(self.argv_file) as f:
            return f.read().split("\n")[:-1]


def check_m1(t, mod):
    sid, rec, cab = t.open_seat(mod, "claude-opus-5-5")
    t.assertEqual((sid, cab), ("abc12", "6ddf"))
    t.assertEqual(t.launched(), ["claude", "dgrs", "qa", "--again", "--model", "claude-opus-5-5"])


def check_m2(t, mod):
    for bad in ("-x", "a b", "x" * 81, "opus;rm"):
        with t.assertRaises(mod.NewSeatError, msg=bad):
            t.open_seat(mod, bad)
        t.assertIsNone(t.launched(), "壞模型不能開席:%r" % bad)
        shutil.rmtree(pathlib.Path(t.tmp) / "aibhome")


class OpenNewSeat(Sandbox):
    def test_m1_model_passed(self):
        check_m1(self, load())

    def test_m1_no_model_no_flag(self):
        self.open_seat(load(), "")
        self.assertEqual(self.launched(), ["claude", "dgrs", "qa", "--again"])

    def test_m1_model_forms(self):
        mod = load()
        for ok in ("opus[1m]", "gpt-5.5-codex", "gemini-3-pro", "openrouter/meta:free"):
            self.assertTrue(mod.new_seat_model_ok(ok), ok)

    def test_m2_bad_model_refused(self):
        check_m2(self, load())

    def test_k1_flag_dropped(self):
        mod = load('+ (["--model", model] if model else [])', "")
        with self.assertRaises(AssertionError):
            check_m1(self, mod)

    def test_k2_model_unchecked(self):
        mod = load('    return not model or bool(MODEL_RE.match(model))\n', '    return True\n')
        with self.assertRaises(AssertionError):
            check_m2(self, mod)


class Cli(Sandbox):
    def run_main(self, extra, opened=None):
        mod = load()
        argv = ["aib newrole", "--name", "測試角色", "--code", "tst", "--prompt", PROMPT, "--brain", "none",
                "--actor", "dev@abcde/claude"] + list(extra)
        err = io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(mod, "current_cabin", return_value="6ddf"))
            st.enter_context(mock.patch.object(mod, "new_seat_precheck", return_value=None))
            st.enter_context(mock.patch.object(mod, "open_new_seat", side_effect=opened or AssertionError("不該開席")))
            st.enter_context(mock.patch.object(sys, "argv", argv))
            st.enter_context(contextlib.redirect_stderr(err)); st.enter_context(contextlib.redirect_stdout(err))
            try:
                rc = mod.main()
            except SystemExit as exc:
                rc = exc.code
        return rc, err.getvalue()

    def test_m3_model_needs_new_seat(self):
        rc, out = self.run_main(["--seat", "6ddf/abcde", "--model", "opus"])
        self.assertEqual(rc, 2, out)
        self.assertIn("--model 只配 --new-seat", out)

    def test_m3_bad_model_before_anything(self):
        rc, out = self.run_main(["--new-seat", "--model=a b;x"])
        self.assertEqual(rc, 2, out)
        self.assertIn("模型名稱不對", out)

    def test_m3_model_reaches_open_new_seat(self):
        seen = {}

        def opened(role, **kw):
            seen.update(kw, role=role)
            raise RuntimeError("stop-here")   # 只看參數,後面綁席不跑
        with self.assertRaises(RuntimeError):
            self.run_main(["--new-seat", "--role", "qa", "--model", "opus[1m]"], opened=opened)
        self.assertEqual((seen.get("role"), seen.get("model")), ("qa", "opus[1m]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
