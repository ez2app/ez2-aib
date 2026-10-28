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
import unittest.mock
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
        # 審查 r1(P1/P2):名字釘死,改錯一家照樣綠就抓不到;各 *_cli 讀的也是這個名字
        self.assertEqual(named, {"deepseek": "DEEPSEEK_API_KEY", "nvidia": "NVIDIA_API_KEY",
                                 "openrouter": "OPENROUTER_API_KEY", "typesafe": "TYPESAFE_API_KEY",
                                 "openrouterjev": "OPENROUTER_API_KEY"})
        for pid in ("deepseek", "nvidia", "openrouter", "typesafe"):
            cli = COCKPIT / ("%s_cli.py" % pid)
            if cli.exists():
                self.assertIn(named[pid], cli.read_text(encoding="utf-8"), pid)

    def test_blank_env_value_is_still_need_key(self):
        self.assertEqual(R.check("deepseek", environ=dict(self.env, DEEPSEEK_API_KEY="  ")),
                         ("need_key", "DEEPSEEK_API_KEY"))

    def test_unknown_key_name_or_broken_table_is_ok(self):
        table = self.tmp / "p.json"
        table.write_text(json.dumps({"providers": [{"id": "zz", "auth": "api_key", "api_key_env": "ZZ_NOPE_API_KEY"}]}))
        self.assertEqual(R.check("zz", environ=self.env, providers_json=table), ("ok", ""))
        table.write_text("{not json")
        self.assertEqual(R.check("deepseek", environ=self.env, providers_json=table), ("ok", ""))
        table.write_text(json.dumps({"providers": 7}))       # 讀得懂但形狀不對 ⇒ 例外也當 ok
        self.assertEqual(R.check("deepseek", environ=self.env, providers_json=table), ("ok", ""))

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
        # 10-28:設定檔在但網址 / 金鑰不能用 ⇒ 不算綁過(以前只看檔案在不在,任一 agent 有檔就放行)
        self.assertEqual(R.check("grokbot", environ=self.env), ("hint", "no_doorbell"))
        (d / "bc-00000000-0000-0000-0000-000000000001.env").write_text(
            "GROKBOT_WAKE_URL=https://api2.cursor.sh/automations/webhook/0123abcd-0123-4567-89ab-0123456789ab\n"
            "GROKBOT_WAKE_KEY=k_valid\n")
        self.assertEqual(R.check("grokbot", environ=self.env), ("ok", ""))
        # 環境帶了這次要開的 agent ⇒ 只看它:別的 agent 綁了不算
        self.assertEqual(R.check("grokbot", environ=dict(self.env, AIB_GROKBOT_AGENT_ID="other-agent")),
                         ("hint", "no_doorbell"))
        self.assertEqual(R.check("grokbot", environ=dict(
            self.env, AIB_GROKBOT_AGENT_ID="bc-00000000-0000-0000-0000-000000000001")), ("ok", ""))

    def test_codexbot_thread_from_env_or_file(self):
        self.assertEqual(R.check("codexbot", environ=self.env), ("hint", "no_codex_thread"))
        self.assertEqual(R.check("codexbot", environ=dict(self.env, AIB_CODEXBOT_THREAD_ID="t")), ("ok", ""))
        self.put("codexbot.env", "AIB_CODEXBOT_THREAD_ID=019a0000-0000-7000-8000-000000000000\n")
        self.assertEqual(R.check("codexbot", environ=self.env), ("ok", ""))

    def test_codexbot_unreadable_env_file_does_not_hint(self):
        self.put("codexbot.env", "AIB_CODEXBOT_THREAD_ID=\n")
        os.chmod(self.sec / "codexbot.env", 0o644)
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

    def _ask(self, runner):
        return R.ask_key("deepseek", environ=self.env, runner=runner, locale="zh-TW")

    def test_ask_key_saved_only_when_key_really_there(self):
        import i18n_lib
        saved = i18n_lib.t("aib.context.menu.key_saved", {"name": "DEEPSEEK_API_KEY", "engine": "deepseek"}, locale="zh-TW")
        seen = {}

        class Ok:
            returncode = 0

        def runner(cmd, **kw):
            seen.update(kw)
            self.put("api-key-deepseek.env", "DEEPSEEK_API_KEY=enc.v1.x\n")
            return Ok()
        self.assertEqual(self._ask(runner), saved)
        self.assertIs(seen.get("stdin"), subprocess.DEVNULL)      # 不讓 secret ask 吃到呼叫端的輸入
        self.assertIs(seen.get("stdout"), subprocess.DEVNULL)     # 它印的 JSON 回條不能混進艙底部那句話

    def test_stub_only_under_temp_state_root_and_is_labelled(self):
        stub = self.tmp / "stub"
        stub.write_text("#!/bin/sh\necho '{\"ok\":false,\"fingerprint\":\"abcd1234\"}'\nexit 1\n")
        os.chmod(stub, 0o700)
        env = dict(self.env, EZ2_OPEN_READINESS_AIB=str(stub))
        self.assertEqual(R._aib_bin(env), str(stub))
        cmds = []

        class No:
            returncode = 1
        R.ask_key("deepseek", environ=env, runner=lambda cmd, **kw: cmds.append(cmd) or No())
        self.assertEqual(cmds[0][1], str(stub))
        msg = R.ask_key("deepseek", environ=env, locale="zh-TW")
        self.assertTrue(msg.startswith("[測試樁] "), msg)
        self.assertNotIn("fingerprint", msg)
        real = str(R.COCKPIT / "aib")
        self.assertEqual(R._aib_bin({"EZ2_OPEN_READINESS_AIB": str(stub)}), real)            # 沒設狀態根
        self.assertEqual(R._aib_bin({"EZ2_OPEN_READINESS_AIB": str(stub),
                                     "EZ2_AIB_STATE_ROOT": "~/.ez2-aib"}), real)            # 真的狀態根

    def test_ask_key_rc0_but_still_empty_is_not_saved(self):
        class Ok:
            returncode = 0
        self.assertNotIn("✓", self._ask(lambda cmd, **kw: Ok()) or "")
        import i18n_lib
        saved = i18n_lib.t("aib.context.menu.key_saved", {"name": "DEEPSEEK_API_KEY", "engine": "deepseek"}, locale="zh-TW")
        self.assertNotEqual(self._ask(lambda cmd, **kw: Ok()), saved)

    def test_ask_key_runner_oserror_is_not_saved(self):
        import i18n_lib
        not_saved = i18n_lib.t("aib.context.menu.key_not_saved", {"name": "DEEPSEEK_API_KEY", "engine": "deepseek"}, locale="zh-TW")

        def boom(cmd, **kw):
            raise OSError("no bash")
        self.assertEqual(self._ask(boom), not_saved)

    def test_missing_translation_never_reaches_menu(self):
        self.assertEqual(R.label_suffix("hint", "no_such_code_xyz", locale="zh-TW"), "")
        with unittest.mock.patch.object(R, "check", return_value=("hint", "no_such_code_xyz")):
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                R.main(["grokbot"])
        self.assertEqual(buf.getvalue(), "ok\n")


class MenuWiring(Base):
    def setUp(self):
        super().setUp()
        # 樁:--dry-run 萬一被吃掉(審查 r1 突變 M11 真的發生過),也只會叫到這支、不會在真人螢幕叫出 Touch ID
        self.stub = self.tmp / "aib-stub"
        self.stub.write_text("#!/bin/sh\necho STUB-CALLED \"$@\" >&2\nexit 1\n")
        os.chmod(self.stub, 0o700)
        self.env["EZ2_OPEN_READINESS_AIB"] = str(self.stub)

    def menu(self):
        st = self.tmp / "state"
        st.mkdir(exist_ok=True)
        env = dict(self.env, HOME=str(self.tmp), EZ2_COCKPIT_STATE_DIR=str(st), EZ2_PY3=sys.executable,
                   PATH=os.environ.get("PATH", "/usr/bin:/bin"), AIB_LANG="zh-TW")
        r = subprocess.run(["bash", str(MOUSE / "mouse-ctl.sh"), "--new-ai-menu", "", "%7", "0", "0", "--dry-run"],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_missing_key_row_shows_label_and_keeps_pane(self):
        import i18n_lib
        out = self.menu()
        row = next(l for l in out.splitlines() if "--new-ai-key deepseek" in l)
        self.assertIn(i18n_lib.t("aib.context.menu.need_key", locale="zh-TW"), out)
        self.assertIn("%7", row)

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
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("STUB-CALLED", r.stderr)            # 乾跑一定不叫 secret ask
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

    def test_human_letter_is_not_marked_even_with_its_id(self):
        # 審查 r1 建議 1:id 傳錯也不能把真人來信藏起來(只防誤操作,不是防線)
        it = self.DQ.record("6d76f", "我已就緒,今天先做 #12", "human:andy", status="delivered", env=self.env)
        out = self.DQ.mark_onboard_acked("6d76f", it["id"], env=self.env)
        self.assertEqual(out.get("why"), "not_onboard")
        self.assertIsNone(self.DQ._load_item("6d76f", it["id"], self.env)["read_at"])

    def test_global_and_im_letters_are_never_onboard(self):
        # 審查 r2 M1:外人寫的信正文帶「已就緒」也不標
        for extra in ({"global": {"reply_code": "x"}}, {"im": {"room": "r"}}, {"sender_kind": "external_im"}):
            it = self.DQ.record("6d76f", "讀完回「已就緒」", "外人", status="delivered", env=self.env)
            real = self.DQ._load_item
            with unittest.mock.patch.object(self.DQ, "_load_item",
                                            side_effect=lambda s, i, e, _x=extra: dict(real(s, i, e) or {}, **_x)):
                out = self.DQ.mark_onboard_acked("6d76f", it["id"], env=self.env)
            self.assertEqual(out.get("why"), "not_onboard", extra)
            self.assertIsNone(real("6d76f", it["id"], self.env)["read_at"])

    def test_relay_pointer_to_fifo_or_huge_file_does_not_hang(self):
        fifo = self.tmp / "RELAY-fifo"
        os.mkfifo(fifo)
        self.assertFalse(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:%s" % fifo))
        big = self.tmp / "RELAY-big"
        big.write_bytes(b"x" * (self.DQ.TIDY_RELAY_MAX_BYTES + 1) + "「已就緒」".encode("utf-8"))
        self.assertFalse(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:%s" % big))
        self.assertFalse(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:/dev/zero"))

    def test_relay_over_limit_with_marker_at_start_is_not_read(self):
        big = self.tmp / "RELAY-head"                           # 記號在開頭、總長超過上限 ⇒ 保守失敗(定案)
        big.write_bytes("「已就緒」".encode("utf-8") + b"x" * self.DQ.TIDY_RELAY_MAX_BYTES)
        self.assertFalse(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:%s" % big))

    def test_relay_fifo_with_writer_is_not_regular_file(self):
        fifo = self.tmp / "RELAY-fifo-w"
        os.mkfifo(fifo)
        fd = os.open(fifo, os.O_RDWR)                           # 有寫端、裡面真有記號 ⇒ 仍要因不是一般檔而拒
        try:
            os.write(fd, "讀完回「已就緒」".encode("utf-8"))
            self.assertFalse(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:%s" % fifo))
        finally:
            os.close(fd)

    def test_relay_read_is_bounded(self):
        relay = self.tmp / "RELAY-small"
        relay.write_text("讀完回「已就緒」", encoding="utf-8")
        sizes = []
        real_read = os.read

        def spy(fd, n):
            sizes.append(n)
            return real_read(fd, n)
        with unittest.mock.patch.object(self.DQ.os, "read", spy):
            self.assertTrue(self.DQ._is_onboard_msg("長訊已落檔(9 位元組),請讀:%s" % relay))
        self.assertEqual(sizes, [self.DQ.TIDY_RELAY_MAX_BYTES + 1])

    def test_relayed_onboard_is_recognised_from_relay_file(self):
        relay = self.tmp / "RELAY-dm-6ddf-1024-000000-1"
        body = "……讀完回覆一句「已就緒」並待命,首任務候派。"
        relay.write_text(body, encoding="utf-8")
        msg = "長訊已落檔(%d 位元組),請讀:%s" % (len(body.encode("utf-8")), relay)
        self.assertEqual(self.DQ.DR.relay_path_of(msg), str(relay))
        it = self.DQ.record("6d76f", msg, "系統", status="delivered", env=self.env)
        self.assertTrue(self.DQ.mark_onboard_acked("6d76f", it["id"], env=self.env).get("ok"))

    def test_markers_follow_ack_tokens(self):
        import seat_onboard as SO
        self.assertEqual(set(self.DQ.ONBOARD_MARKERS), {"「%s」" % t for t in SO.ACK_TOKENS})
        for text in (SO.INLINE_HEAD_FIRST, SO.INLINE_HEAD_UPDATE, SO.ACK_INSTRUCTION_FIRST, SO.ACK_INSTRUCTION_UPDATE):
            self.assertTrue(any(m in text for m in self.DQ.ONBOARD_MARKERS), text)

    def test_system_still_cannot_read_with_other_reasons(self):
        it = self.DQ.record("6d76f", "x", "系統", status="delivered", env=self.env)
        out = self.DQ._transition("6d76f", it["id"], "read", "system", env=self.env, why="anything")
        self.assertFalse(out.get("ok"))

    def test_watcher_carries_queue_id_and_marks_on_ack(self):
        import seat_onboard as SO
        src = (COCKPIT / "seat_onboard.py").read_text(encoding="utf-8")
        self.assertIn('cmd += ["--queue-seat", str(queue_seat), "--queue-id", str(queue_id)]', src)
        self.assertIn("mark_onboard_read(args.queue_seat, args.queue_id)", src)
        it = self.DQ.record("6d76f", "這是開席單,讀完回「已就緒」。", "系統", status="delivered", env=self.env)
        self.assertFalse(SO.mark_onboard_read("", it["id"], env=self.env))
        with unittest.mock.patch.object(self.DQ, "mark_onboard_acked") as m:
            self.assertFalse(SO.mark_onboard_read("6d76f", "FAILED", env=self.env))
        m.assert_not_called()
        self.assertTrue(SO.mark_onboard_read("6d76f", it["id"], env=self.env))
        self.assertFalse(SO.mark_onboard_read("6d76f", it["id"], env=self.env))      # 已讀 ⇒ False
        self.assertFalse(SO.mark_onboard_read("6d76f", "q_doesnotexist0000", env=self.env))

    def test_after_read_runs_and_stale_rev_is_refused(self):
        it = self.DQ.record("6d76f", "這是開席單,讀完回「已就緒」。", "系統", status="delivered", env=self.env)
        with unittest.mock.patch.object(self.DQ, "_after_read") as after:
            self.assertTrue(self.DQ.mark_onboard_acked("6d76f", it["id"], env=self.env).get("ok"))
        after.assert_called_once()
        it2 = self.DQ.record("6d76f", "這是開席單,讀完回「已就緒」。", "系統", status="delivered", env=self.env)
        real = self.DQ._load_item

        first = [True]

        def stale(seat, iid, env):                              # 只有預檢那一次讀到舊的(之後信被改過)
            got = real(seat, iid, env)
            if got and first[0]:
                first[0] = False
                return dict(got, rev=(got.get("rev") or 0) - 1)
            return got
        with unittest.mock.patch.object(self.DQ, "_load_item", side_effect=stale):
            self.assertFalse(self.DQ.mark_onboard_acked("6d76f", it2["id"], env=self.env).get("ok"))
        self.assertIsNone(real("6d76f", it2["id"], self.env)["read_at"])

    def _onboard(self, SO, detach, target_code="6d76f", qid="q_0123456789abcdef"):
        marks, spawns = [], []
        patches = dict(
            resolve_seat_context=lambda w: ("", ""), resolve_latest_sweep=lambda: ("", ""),
            ack_instruction_for_seat=lambda *a: SO.ACK_INSTRUCTION_FIRST,
            render=lambda *a, **k: "讀完回覆一句「已就緒」", persona_block=lambda *a: "",
            handoff_notice_block=lambda *a: "", self_fetch_ready=lambda *a, **k: False,
            capture=lambda w: "", record=lambda *a, **k: None,
            wait_ack=lambda *a, **k: True,
            spawn_ack_watcher=lambda *a, **k: spawns.append(k),
            mark_onboard_read=lambda s, i, env=None: marks.append((s, i)) or True)
        with unittest.mock.patch.multiple(SO, **patches), \
                unittest.mock.patch.object(SO.open_attempt, "mark_ready_for_window", lambda *a, **k: None):
            got = SO.onboard_one("@9", "fffff", "dev", "x---y", "sha", str(self.tmp / "ledger.jsonl"),
                                 detach_ack=detach, deliver_fn=lambda t, b, dry_run=False: (0, {"id": qid}),
                                 provider="claude", target_code=target_code, environ=self.env)
        return got, marks, spawns

    def test_sync_ack_marks_the_delivered_item(self):
        import seat_onboard as SO
        got, marks, _ = self._onboard(SO, detach=False)
        self.assertEqual(got, "acked")
        self.assertEqual(marks, [("6d76f", "q_0123456789abcdef")])

    def test_detached_watcher_gets_queue_seat_and_id(self):
        import seat_onboard as SO
        got, _, spawns = self._onboard(SO, detach=True)
        self.assertEqual(got, "pending")
        self.assertEqual((spawns[0]["queue_seat"], spawns[0]["queue_id"]), ("6d76f", "q_0123456789abcdef"))

    def test_window_is_never_used_as_queue_seat(self):
        import seat_onboard as SO
        _, marks, _ = self._onboard(SO, detach=False, target_code=None)
        self.assertEqual(marks, [])
        _, _, spawns = self._onboard(SO, detach=True, target_code=None)
        self.assertEqual(spawns[0]["queue_seat"], "")


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

    def test_probe_named_dir_outside_tmp_is_kept(self):
        outside = str(self.tmp / "ez2-usage-probe.notmp")      # 名字像、但不在暫存根底下
        cfg = self.gem / "antigravity-cli" / "settings.json"
        cfg.write_text(json.dumps({"trustedWorkspaces": [outside]}))
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertIn(outside, cfg.read_text())

    def test_symlinked_tmp_root_and_own_link_spelling(self):
        link_root = self.tmp / "Tlink"
        link_root.symlink_to(self.fake_tmp)
        tempfile.tempdir = str(link_root)                      # 暫存根是連結(/var → /private/var 那種)
        own_real = self.fake_tmp / "ez2-usage-probe.mine"
        own_real.mkdir()
        stale_real = str(self.fake_tmp / "ez2-agy-quota-old")
        cfg = self.gem / "antigravity-cli" / "settings.json"
        cfg.write_text(json.dumps({"trustedWorkspaces": [str(own_real), stale_real, "/Users/x/p"]}))
        self.assertEqual(self.UP.forget_probe_trust(link_root / "ez2-usage-probe.mine", environ=self.penv), 2)
        self.assertEqual(json.loads(cfg.read_text())["trustedWorkspaces"], ["/Users/x/p"])

    def test_listed_link_spelling_with_real_own(self):
        lnk = self.tmp / "lnk"
        lnk.symlink_to(self.fake_tmp)
        own = self.fake_tmp / "ez2-usage-probe.z"
        own.mkdir()                                             # 還在:不讓「已不存在」那條退路接住
        own_set = {str(own), os.path.realpath(str(own))}      # 照 forget_probe_trust 組法
        self.assertTrue(self.UP._is_probe_dir(str(lnk / "ez2-usage-probe.z"), own=own_set))

    def test_realpath_only_for_probe_named_entries(self):
        seen = []
        real = os.path.realpath

        def spy(p, *a, **k):
            seen.append(str(p))
            return real(p, *a, **k)
        with unittest.mock.patch.object(self.UP.os.path, "realpath", spy):
            self.assertFalse(self.UP._is_probe_dir("/Volumes/gone/proj", own={"/x/ez2-usage-probe.a"}))
        self.assertNotIn("/Volumes/gone/proj", seen)            # 斷掉的網路磁碟不 realpath

    def test_grok_symlink_untouched(self):
        real = self.tmp / "real.toml"
        body = '[folders."%s"]\ntrusted = true\n' % (self.fake_tmp / "ez2-usage-probe.q")
        real.write_text(body)
        (self.grok / "trusted_folders.toml").symlink_to(real)
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertEqual(real.read_text(), body)

    def test_grok_escaped_keys_and_value_guard(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("沒有 tomllib")
        stale = str(self.fake_tmp / "ez2-usage-probe.e")
        body = ('[folders."/Users/x/a\\"b"]\ntrusted = true\n\n\n\n'
                '[folders."%s"]\ntrusted = true\n\n\n\n'
                '[folders."/Users/x/m"]\nnote = """\n[folders."%s"]\n"""\n') % (stale, stale)
        cfg = self.grok / "trusted_folders.toml"
        cfg.write_text(body)
        # 多行字串裡那行長得像要刪的標頭 ⇒ 刪了會改到 note 的值 ⇒ 守門擋下、整檔不動
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertEqual(cfg.read_text(), body)
        body2 = ('[folders."/Users/x/a\\"b"]\ntrusted = true\n\n\n\n'
                 '[folders."%s"]\ntrusted = true\n\n\n\n[folders."/Users/x/m"]\ntrusted = false\n') % stale
        cfg.write_text(body2)
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 1)
        new = cfg.read_text()
        self.assertEqual(set(tomllib.loads(new)["folders"]), {'/Users/x/a"b', "/Users/x/m"})
        self.assertNotIn("\n\n\n", new)

    def test_file_changed_between_read_and_write_is_not_overwritten(self):
        cfg = self.gem / "antigravity-cli" / "settings.json"
        cfg.write_text("before")
        seen = cfg.stat().st_mtime_ns
        os.utime(cfg, ns=(seen, seen + 10**9))                 # 讀完之後:像有人剛按了「信任」
        with self.assertRaises(OSError):
            self.UP._rewrite(cfg, "after", seen_mtime=seen)
        self.assertEqual(cfg.read_text(), "before")
        self.assertEqual([x.name for x in cfg.parent.iterdir()], ["settings.json"])   # 暫存檔有收掉

    def test_cleanup_skips_both_files_when_changed_after_read(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            self.skipTest("沒有 tomllib")
        agy = self.gem / "antigravity-cli" / "settings.json"
        agy.write_text(json.dumps({"trustedWorkspaces": [str(self.fake_tmp / "ez2-usage-probe.r")]}))
        grok = self.grok / "trusted_folders.toml"
        grok.write_text('[folders."%s"]\ntrusted = true\n' % (self.fake_tmp / "ez2-usage-probe.r"))
        before = (agy.read_text(), grok.read_text())
        real = self.UP._rewrite

        def bumped(path, text, seen_mtime=None):                # 讀完、寫之前:像有人剛按了「信任」
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            return real(path, text, seen_mtime=seen_mtime)
        with unittest.mock.patch.object(self.UP, "_rewrite", bumped):
            self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertEqual((agy.read_text(), grok.read_text()), before)

    def test_own_dir_third_spelling_via_realpath(self):
        own = self.fake_tmp / "ez2-usage-probe.slash"
        own.mkdir()
        cfg = self.gem / "antigravity-cli" / "settings.json"
        cfg.write_text(json.dumps({"trustedWorkspaces": [str(own) + "/", "/Users/x/p"]}))
        self.assertEqual(self.UP.forget_probe_trust(own, environ=self.penv), 1)
        self.assertEqual(json.loads(cfg.read_text())["trustedWorkspaces"], ["/Users/x/p"])

    def test_grok_value_guard_when_truncated_toml_still_parses(self):
        try:
            import tomllib  # noqa: F401
        except ImportError:
            self.skipTest("沒有 tomllib")
        stale = str(self.fake_tmp / "ez2-usage-probe.v")
        body = ('[folders."%s"]\ntrusted = true\n\n'
                '[folders."/Users/x/m"]\nnote = """\n[folders."%s"]\nlost line\n[x]\n"""\n') % (stale, stale)
        cfg = self.grok / "trusted_folders.toml"
        cfg.write_text(body)
        self.assertEqual(self.UP.forget_probe_trust(None, environ=self.penv), 0)
        self.assertEqual(cfg.read_text(), body)

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
