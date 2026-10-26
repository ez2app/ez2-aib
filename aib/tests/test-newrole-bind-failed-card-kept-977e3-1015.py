#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib newrole:卡已寫進 vault、綁席失敗時,要講明卡在哪、重跑同一行只會重綁(10-15 @977e3 實機撞到)。

全在 tmp 沙盒:HOME / 狀態根 / vault 都在 tmp;席位解析與 bind_one 換成假的(bind_one 一律失敗),
不碰真 ~/.ez2-aib、不碰 tmux。
B1 新卡:錯誤訊息講「已經建好在 <路徑>」「重跑同一行只會重綁」,卡真的在那個路徑。
B2 同名卡再跑(沿用既有卡):講「本來就在 <路徑>,沒動」,不說建好。
B3 帶 --overwrite 覆寫既有卡:講「已照新人設覆寫」,重跑要拿掉 --overwrite(審查抓到)。
B4 帶 --new-seat:新席已開,重跑不要再下 --new-seat、改用 --seat 綁那一席(審查抓到;開席換成假的)。
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent
SRC = ROOT / "src" / "identity" / "create_and_bind_persona.py"
PROMPT = "你是測試角色。\n\n## 職責\n1. 測試\n\n## 知識域\n- 測試\n\n## 紅線\n- 不寫真庫\n"


class BindFailedCardKept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-b1-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._saved = dict(os.environ)
        for k in ("EZ2_VAULT_DIR", "EZ2_VAULT_ROOT", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT_SPEC", "TMUX", "TMUX_PANE",
                  "EZ2_OWNER_SIG", "EZ2_AIB_INSTANCE", "EZ2_AIB_INSTANCE_HOME"):
            os.environ.pop(k, None)
        os.environ.update({"HOME": self.home, "EZ2_AIB_HOME": os.path.join(self.home, ".ez2-aib"),
                           "EZ2_AIB_STATE_ROOT": os.path.join(self.home, ".ez2-aib"), "EZ2_AIB_LOCALE": "zh-TW",
                           "EZ2_EZ2AI_REPO": os.path.join(self.tmp, "no-ez2-ai"),
                           "AIB_GENES": "0", "AIB_AUTO_BIND": "0"})
        spec = importlib.util.spec_from_file_location("cabp_b1", str(SRC))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra=(), new_seat=False):
        real = self.mod.run_cmd

        def fake_run(cmd, cwd=None, extra_env=None):
            if any(str(c).endswith("bind_one.py") for c in cmd):
                self.bound_vault = os.environ.get("EZ2_VAULT_DIR")
                return 2, "", "bind-one: 46678:agent_uid 977e3 已存在但 role='bot',席位檔是 'dev'——這不是重跑,是衝突"
            return real(cmd, cwd=cwd, extra_env=extra_env)

        argv = ["aib newrole", "--name", "測試角色", "--code", "tst", "--prompt", PROMPT, "--brain", "none",
                "--actor", "dev@abcde/claude"] + (["--new-seat"] if new_seat else ["--seat", "6ddf/abcde"]) + list(extra)
        err = io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(self.mod, "resolve_seat_arg", return_value=("abcde", {"seat_id": "abcde"}, "", None)))
            st.enter_context(mock.patch.object(self.mod, "run_cmd", side_effect=fake_run))
            if new_seat:   # 開席換成假的:開出 seat 9f9f9,不碰 tmux
                st.enter_context(mock.patch.object(self.mod, "current_cabin", return_value="6ddf"))
                st.enter_context(mock.patch.object(self.mod, "new_seat_precheck", return_value=None))
                st.enter_context(mock.patch.object(self.mod, "open_new_seat", return_value=("9f9f9", {"seat_id": "9f9f9"}, "")))
            st.enter_context(mock.patch.object(sys, "argv", argv))
            st.enter_context(contextlib.redirect_stderr(err)); st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try:
                rc = self.mod.main()
            except SystemExit as exc:
                rc = exc.code
        return rc, err.getvalue()

    def _card_path(self, text):
        line = next(ln for ln in text.splitlines() if "uid" in ln and "角色.md" in ln and ("建好在" in ln or "本來就在" in ln or "覆寫在" in ln))
        return pathlib.Path(line.split("在 ", 1)[1].split(",", 1)[0].strip())

    def test_B1_new_card_says_where_and_rerun_only_rebinds(self):
        rc, text = self._run()
        self.assertNotEqual(rc, 0)
        self.assertIn("bind_one.py 失敗", text)
        self.assertIn("已經建好在", text)
        self.assertIn("重跑同一行即可:只會重綁,不會再建一張卡", text)
        self.assertTrue(self._card_path(text).is_file(), text)
        self.assertTrue(str(self._card_path(text).resolve()).startswith(str(pathlib.Path(self.tmp).resolve())),
                        "卡只准落在沙盒")

    def test_explicit_vault_is_carried_to_binding(self):
        selected = pathlib.Path(self.tmp, "selected-vault")
        (selected / "10-Roles").mkdir(parents=True)
        # 模擬工具殼帶著另一個庫;--vault 指定的庫必須傳到綁定子行程。
        os.environ["EZ2_VAULT_DIR"] = str(pathlib.Path(self.tmp, "outer-vault"))
        rc, text = self._run(extra=("--vault", str(selected)))
        self.assertNotEqual(rc, 0)  # 測試的 binder 故意拒絕,仍需先走到 binder。
        self.assertEqual(self.bound_vault, str(selected.resolve()))
        self.assertTrue(str(self._card_path(text)).startswith(str(selected)))

    def test_B2_existing_card_says_untouched(self):
        self._run()
        rc, text = self._run()
        self.assertNotEqual(rc, 0)
        self.assertIn("本來就在", text)
        self.assertNotIn("已經建好在", text)
        self.assertTrue(self._card_path(text).is_file(), text)

        self.assertIn("重跑同一行即可", text)

    def test_B3_overwrite_says_overwritten_and_drop_flag(self):
        self._run()
        rc, text = self._run(extra=("--overwrite",))
        self.assertNotEqual(rc, 0)
        self.assertIn("已照新人設覆寫在", text)
        self.assertIn("拿掉 --overwrite", text)
        self.assertNotIn("重跑同一行即可", text)
        self.assertNotIn("已經建好在", text)

    def test_B4_new_seat_says_do_not_open_again(self):
        rc, text = self._run(new_seat=True)
        self.assertNotEqual(rc, 0)
        self.assertIn("已經建好在", text)
        self.assertIn("新席 9f9f9 已經開了", text)
        self.assertIn("不要再下 --new-seat", text)
        self.assertNotIn("重跑同一行即可", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
