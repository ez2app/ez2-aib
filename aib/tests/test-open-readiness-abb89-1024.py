#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右鍵「另開 AI」開席前先看缺不缺東西(10-24 @abb89,右鍵 14 家實測;Andy「依照你的建議開始修到全好」)。

事故:Pro 的計費組金鑰檔在、值是空的 ⇒ 點 DeepSeek 開出馬上死掉的窗;grokbot 9 個 agent 沒一個接門鈴、
codexbot 沒連 Codex 桌面版、musebot 沒綁過 Muse 對話 ⇒ 開得起來、收不到信,要到席上才知道。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COCKPIT = REPO / "src" / "cockpit"
MOUSE = COCKPIT / "mouse"
sys.path.insert(0, str(MOUSE))

import open_readiness as R  # noqa: E402

API_KEY_PROVIDERS = ("deepseek", "nvidia", "openrouter", "typesafe", "openrouterjev")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.sec = self.tmp / "secrets"
        self.sec.mkdir(mode=0o700)
        os.chmod(self.sec, 0o700)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.tmp), "EZ2_AIB_HOME": str(self.tmp), "PATH": "/usr/bin:/bin"}

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, rel, text):
        p = self.sec / rel
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_text(text)
        os.chmod(p, 0o600)


class ApiKey(Base):
    def test_every_api_key_provider_names_its_key(self):
        rows = json.loads((COCKPIT / "providers.json").read_text(encoding="utf-8"))["providers"]
        named = {r["id"]: r.get("api_key_env") for r in rows if r.get("auth") == "api_key"}
        self.assertEqual(set(named), set(API_KEY_PROVIDERS))
        for pid, name in named.items():
            self.assertRegex(name or "", r"^[A-Z][A-Z0-9_]*_API_KEY$", pid)

    def test_empty_key_file_is_need_key(self):
        self.put("api-key-deepseek.env", "DEEPSEEK_API_KEY=\n")
        self.assertEqual(R.check("deepseek", environ=self.env), ("need_key", "DEEPSEEK_API_KEY"))

    def test_missing_key_file_is_need_key(self):
        self.assertEqual(R.check("nvidia", environ=self.env), ("need_key", "NVIDIA_API_KEY"))

    def test_value_in_file_or_env_is_ok(self):
        self.put("api-key-deepseek.env", "DEEPSEEK_API_KEY=enc.v1.whatever\n")
        self.assertEqual(R.check("deepseek", environ=self.env), ("ok", ""))
        self.assertEqual(R.check("nvidia", environ=dict(self.env, NVIDIA_API_KEY="x")), ("ok", ""))

    def test_openrouterjev_shares_openrouter_key(self):
        self.put("api-key-openrouter.env", "OPENROUTER_API_KEY=sk-or-x\n")
        self.assertEqual(R.check("openrouterjev", environ=self.env), ("ok", ""))

    def test_unreadable_vault_does_not_block(self):
        self.put("api-key-deepseek.env", "DEEPSEEK_API_KEY=\n")
        os.chmod(self.sec / "api-key-deepseek.env", 0o644)      # 權限太寬:開席那一步會講,這裡不擋
        self.assertEqual(R.check("deepseek", environ=self.env)[0], "ok")

    def test_native_is_always_ok(self):
        for pid in ("claude", "codex", "agy", "copilot", "grok", "nope"):
            self.assertEqual(R.check(pid, environ=self.env), ("ok", ""), pid)


class Bots(Base):
    def test_grokbot_without_any_doorbell_hints(self):
        self.assertEqual(R.check("grokbot", environ=self.env), ("hint", "no_doorbell"))
        import grokbot_threads as gt
        d = self.tmp / "secrets" / gt.AGENT_WAKE_DIR
        d.mkdir(parents=True)
        (d / "bc-00000000-0000-0000-0000-000000000001.env").write_text("GROKBOT_WAKE_URL=x\n")
        self.assertEqual(R.check("grokbot", environ=self.env), ("ok", ""))

    def test_codexbot_thread_from_env_or_file(self):
        self.assertEqual(R.check("codexbot", environ=self.env), ("hint", "no_codex_thread"))
        self.assertEqual(R.check("codexbot", environ=dict(self.env, AIB_CODEXBOT_THREAD_ID="t")), ("ok", ""))
        self.put("codexbot.env", "AIB_CODEXBOT_THREAD_ID=019a0000-0000-7000-8000-000000000000\n")
        self.assertEqual(R.check("codexbot", environ=self.env), ("ok", ""))

    def test_musebot_without_history_hints(self):
        self.assertEqual(R.check("musebot", environ=self.env), ("hint", "no_muse_conv"))

    def test_claudebot_no_hint(self):
        self.assertEqual(R.check("claudebot", environ=self.env), ("ok", ""))


class Cli(Base):
    def run_cli(self, *args):
        env = dict(self.env, AIB_LANG="zh-TW")
        r = subprocess.run([sys.executable, str(MOUSE / "open_readiness.py"), *args], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_one_line_per_provider_in_order(self):
        out = self.run_cli("claude", "__SEP__", "deepseek", "codexbot")
        self.assertEqual(len(out), 4)
        self.assertEqual(out[:2], ["ok", "ok"])
        self.assertTrue(out[2].startswith("need_key\tDEEPSEEK_API_KEY\t"), out[2])
        self.assertTrue(out[3].startswith("hint\tno_codex_thread\t"), out[3])
        self.assertTrue(out[2].split("\t")[2])               # 選單上要有字,不能是空的

    def test_ask_key_never_runs_for_ok_provider(self):
        calls = []
        self.assertEqual(R.ask_key("claude", environ=self.env, runner=lambda *a, **k: calls.append(a)), "")
        self.assertEqual(calls, [])

    def test_ask_key_reports_not_saved_when_cancelled(self):
        calls = []

        class Done:
            returncode = 1

        msg = R.ask_key("deepseek", environ=self.env, runner=lambda cmd, **k: calls.append(cmd) or Done())
        self.assertEqual(calls[0][-3:], ["secret", "ask", "DEEPSEEK_API_KEY"])
        self.assertIn("DEEPSEEK_API_KEY", msg)


class MenuWiring(Base):
    def menu(self):
        st = self.tmp / "state"
        st.mkdir(exist_ok=True)
        env = dict(self.env, HOME=str(self.tmp), EZ2_COCKPIT_STATE_DIR=str(st), EZ2_PY3=sys.executable,
                   PATH=os.environ.get("PATH", "/usr/bin:/bin"))
        r = subprocess.run(["bash", str(MOUSE / "mouse-ctl.sh"), "--new-ai-menu", "", "", "0", "0", "--dry-run"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_missing_key_row_asks_key_instead_of_opening(self):
        out = self.menu()
        self.assertIn("--new-ai-key deepseek", out)
        self.assertNotIn("--new-ai-menu deepseek", out)
        self.assertIn("--new-ai-menu claude", out)

    def test_key_present_row_opens_normally(self):
        self.put("api-key-deepseek.env", "DEEPSEEK_API_KEY=x\n")
        out = self.menu()
        self.assertIn("--new-ai-menu deepseek", out)
        self.assertNotIn("--new-ai-key deepseek", out)

    def test_new_ai_key_refuses_unknown_engine(self):
        st = self.tmp / "state"
        st.mkdir(exist_ok=True)
        env = dict(self.env, HOME=str(self.tmp), EZ2_COCKPIT_STATE_DIR=str(st), EZ2_PY3=sys.executable)
        for eng, want in (("deepseek", True), ("not-an-engine", False)):
            r = subprocess.run(["bash", str(MOUSE / "mouse-ctl.sh"), "--new-ai-key", eng, "", "--dry-run"],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual("ask-key" in r.stdout, want, eng)


class OnboardNoticeMarkedRead(Base):
    """第 5 項:開席單讀過、席回了「已就緒」⇒ 那一封標已讀(以前 read_at 一直 null,狀態列卡「未讀 dm 1」)。"""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(COCKPIT))
        import dm_queue as DQ
        self.DQ = DQ
        self.env = dict(self.env, HOME=str(self.tmp))

    def test_onboard_item_marked_read_by_system(self):
        it = self.DQ.record("6d76f", "這是開席單,讀完回「已就緒」。", "系統", status="delivered", env=self.env)
        out = self.DQ.mark_onboard_acked("6d76f", it["id"], env=self.env)
        self.assertTrue(out.get("ok"), out)
        got = self.DQ._load_item("6d76f", it["id"], self.env)
        self.assertIsNotNone(got["read_at"])
        self.assertEqual(got.get("read_why"), self.DQ.ONBOARD_ACK_WHY)

    def test_held_or_done_items_are_not_touched(self):
        held = self.DQ.record("6d76f", "x", "系統", status=self.DQ.HOLD_STATUS, env=self.env)
        self.assertFalse(self.DQ.mark_onboard_acked("6d76f", held["id"], env=self.env).get("ok"))
        self.assertIsNone(self.DQ._load_item("6d76f", held["id"], self.env)["read_at"])
        self.assertFalse(self.DQ.mark_onboard_acked("6d76f", "q_doesnotexist0000", env=self.env).get("ok"))

    def test_system_still_cannot_read_with_other_reasons(self):
        it = self.DQ.record("6d76f", "x", "系統", status="delivered", env=self.env)
        out = self.DQ._transition("6d76f", it["id"], "read", "system", env=self.env, why="anything")
        self.assertFalse(out.get("ok"))

    def test_watcher_carries_queue_id_and_marks_on_ack(self):
        import seat_onboard as SO
        src = (COCKPIT / "seat_onboard.py").read_text(encoding="utf-8")
        self.assertIn('cmd += ["--queue-seat", str(queue_seat), "--queue-id", str(queue_id)]', src)
        self.assertIn("mark_onboard_read(args.queue_seat, args.queue_id)", src)
        it = self.DQ.record("6d76f", "x", "系統", status="delivered", env=self.env)
        self.assertTrue(SO.mark_onboard_read("6d76f", it["id"], env=self.env))
        self.assertFalse(SO.mark_onboard_read("", it["id"], env=self.env))


class ProbeTrustCleanup(Base):
    """第 9 項:用量探針在暫存資料夾按了「信任」,agy / grok 的信任清單留下暫存路徑;收尾要拿掉。"""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(COCKPIT))
        import usage_probe as UP
        self.UP = UP
        self.fake_tmp = self.tmp / "T"
        self.fake_tmp.mkdir()
        self._old_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.fake_tmp)
        self.gem = self.tmp / "gem"
        (self.gem / "antigravity-cli").mkdir(parents=True)
        self.grok = self.tmp / "grok"
        self.grok.mkdir()
        self.penv = {"GEMINI_CLI_HOME": str(self.gem), "GROK_HOME": str(self.grok)}

    def tearDown(self):
        tempfile.tempdir = self._old_tempdir
        super().tearDown()

    def test_agy_drops_own_and_stale_probe_dirs_only(self):
        own = self.fake_tmp / "ez2-usage-probe.own1"
        stale = str(self.fake_tmp / "ez2-usage-probe.gone")
        alive = self.fake_tmp / "ez2-usage-probe.alive"
        alive.mkdir()
        keep = ["/Users/x/proj", str(alive), str(self.fake_tmp / "not-a-probe")]
        cfg = self.gem / "antigravity-cli" / "settings.json"
        cfg.write_text(json.dumps({"model": "m", "trustedWorkspaces": keep + [str(own), stale]}))
        os.chmod(cfg, 0o640)
        own.mkdir()                     # 探針收尾時自己的資料夾可能還在 ⇒ 照樣拿掉
        self.assertEqual(self.UP.forget_probe_trust(own, environ=self.penv), 2)
        d = json.loads(cfg.read_text())
        self.assertEqual(d["trustedWorkspaces"], keep)
        self.assertEqual(d["model"], "m")
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o640)

    def test_grok_drops_probe_blocks_and_keeps_others(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("沒有 tomllib")
        stale = str(self.fake_tmp / "ez2-agy-quota-zz")
        body = ('[folders."/Users/x/proj"]\ntrusted = true\ndecided_at = 1\n\n'
                '[folders."%s"]\ntrusted = true\ndecided_at = 2\n\n'
                '[folders."/tmp/other"]\ntrusted = false\ndecided_at = 3\n') % stale
        cfg = self.grok / "trusted_folders.toml"
        cfg.write_text(body)
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 1)
        left = tomllib.loads(cfg.read_text())["folders"]
        self.assertEqual(set(left), {"/Users/x/proj", "/tmp/other"})
        self.assertIs(left["/tmp/other"]["trusted"], False)

    def test_symlink_and_unreadable_untouched(self):
        real = self.tmp / "dot.json"
        real.write_text(json.dumps({"trustedWorkspaces": [str(self.fake_tmp / "ez2-usage-probe.x")]}))
        (self.gem / "antigravity-cli" / "settings.json").symlink_to(real)
        (self.grok / "trusted_folders.toml").write_text("[folders\n")
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertIn("ez2-usage-probe.x", real.read_text())
        self.assertEqual((self.grok / "trusted_folders.toml").read_text(), "[folders\n")

    def test_probe_exit_and_agy_quota_call_cleanup(self):
        src = (COCKPIT / "usage_probe.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("forget_probe_trust(self.cwd)"), 1)
        self.assertEqual(src.count("forget_probe_trust(cwd)"), 1)


if __name__ == "__main__":
    unittest.main()
