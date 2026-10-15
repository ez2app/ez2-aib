#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CTX 用量一張表在每一個品牌的 AI 席都由程式觸發,而且安裝 / 升級時就裝好(維護者 10-15 22:1x「最好在這一版全補上」)。

  E  ensure-all:只裝這台有裝的 CLI(Claude / Codex / Grok / Copilot / agy 各一格;沒裝的略過並說明;
     AIB_AUTO_DISTILL=0 全不裝;重跑不重複裝;別人的 hook 不動、先備份;Codex 印 /hooks 信任提示)
  R  /clear 之後換了對話編號:由 by-seat 對照反查席碼 ⇒ 照樣觸發、席碼是登記的那一個;by-seat 過期 ⇒ 不觸發;
     Stop(review-gate)同樣認得
  B  橋接 bot(ClaudeBot / CodexBot / GrokBot / MuseBot / Sand):bot_turn 跑同一張表(跨 85 ⇒ 要求交接,同格只一次);
     量不到用量 ⇒ 只照輪數精煉、不假造交接
  O  外層代觸發(API 金鑰席 REPL:deepseek / nvidia / openrouter / typesafe):每一輪回完呼叫 stop 那條路
  U  量不到用量的席(API 金鑰席、量不到的 bot):每 N 輪精煉一次(N 在 policy 表上,同一席同一對話一份計數)
  N  入口:aib init(實跑,暫存 HOME)、aib update(實跑,假安裝程式)、install.sh(靜態)都呼叫 ensure-all
  K  刀:各自退回一塊 ⇒ 對應格紅

全程暫存 HOME / 狀態根 / gauge;不碰真的 ~/.claude、~/.codex、~/.grok、~/.copilot、~/.gemini、~/.ez2-aib。
子行程經 aib_proc_guard.run_guarded。
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
THIS = Path(__file__).name
sys.path.insert(0, str(HERE))
from aib_tmp_root import aib_tmp_root  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402

for _d in ("src/cockpit", "src/identity", "src/rotation", "src/memory"):
    sys.path.insert(0, str(ROOT / _d))

_BASE = tempfile.mkdtemp(prefix="ctx-brands-", dir=aib_tmp_root())
for _k in ("EZ2_AIB_CTX_POLICY", "GROK_SESSION_ID", "AIB_AUTO_DISTILL_DM", "AIB_AUTO_DISTILL", "EZ2_AIB_HOME",
           "CODEX_HOME", "TMUX_PANE", "TMUX"):
    os.environ.pop(_k, None)
os.environ["EZ2_AIB_STATE_ROOT"] = os.path.join(_BASE, "state")
os.environ["EZ2_CTX_DIR"] = os.path.join(_BASE, "ctx")
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_BASE, "claude-config")   # 任何誤裝都落在暫存
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _BASE, True)

import auto_distill_hook as A  # noqa: E402

HOOK = ROOT / "src" / "identity" / "auto_distill_hook.py"
SID = "00000000-0000-4000-8000-0000000abcde"
CODE = "abcde"
OLD = "604e5b2e-0000-4000-8000-000000eee0bb"     # 開席那次的(席位檔登記的)
NEW = "fa193ba2-0000-4000-8000-000000010f31"     # /clear 之後的
SEAT = "ee0bb"
INST = "20261014-133230-6ddf"
HUMAN = "幫我看一下這個問題好嗎"
MARK = {"tidy": "aib memory tidy collect", "handoff_remind": "交接提醒", "handoff_now": "收尾三步", "red_line": "紅線"}
BRANDS = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "grok": ".grok/hooks/aib-auto-distill.json",
          "copilot": ".copilot/hooks/aib-auto-distill.json", "agy": ".gemini/config/hooks.json"}


def _fake_human_filter():
    m = types.ModuleType("human_filter")
    m.is_human = lambda seat, row, skip_ledger=True: (not str(row.get("text") or "").startswith("[dm"), {})
    return m


def _utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(unittest.TestCase):
    MOCK_SEAT = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cell-", dir=_BASE))
        self.state = self.tmp / "state"
        self.ctx = self.tmp / "ctx"
        self.ctx.mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_CTX_DIR": str(self.ctx)})
        p.start()
        self.addCleanup(p.stop)
        mocks = [("tidy_receipt", lambda code: {}), ("inbox_block", lambda *a, **k: ""),
                 ("_live_verify", lambda code: False)]
        if self.MOCK_SEAT:
            mocks.append(("is_aib_seat", lambda sid: True))
        for name, val in mocks:
            q = mock.patch.object(A, name, val, create=True)
            q.start()
            self.addCleanup(q.stop)
        q = mock.patch.dict(sys.modules, {"human_filter": _fake_human_filter()})
        q.start()
        self.addCleanup(q.stop)

    def gauge(self, pct, sid=SID, at=None):
        (self.ctx / ("%s.pct" % sid)).write_text(json.dumps({"pct": pct, "observed_at": _utc(at or time.time())}),
                                                 encoding="utf-8")


# ---------------------------------------------------------------- E ensure-all
class EnsureAll(Base):
    def run_all(self, bins, home=None, extra=None):
        home = home or (self.tmp / "home")
        fake = self.tmp / "fakebin"
        fake.mkdir(exist_ok=True)
        for b in bins:
            f = fake / b
            f.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            f.chmod(0o755)
        home.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "AIB_AUTO_DISTILL")}
        env.update(HOME=str(home), PATH="%s:/usr/bin:/bin" % fake, EZ2_AIB_LOCALE="zh-TW",
                   PYTHONPYCACHEPREFIX=str(self.tmp / "pyc"), GIT_CONFIG_NOSYSTEM="1")
        env.update(extra or {})
        r = run_guarded([sys.executable, str(HOOK), "ensure-all"], stdin=subprocess.DEVNULL, timeout=120, env=env)
        self.assertIsNone(r.tripped)
        self.assertEqual(r.returncode, 0, r.stderr)
        return home, r.stderr

    def written(self, home):
        return sorted(b for b, rel in BRANDS.items() if (home / rel).exists())

    def test_E1_claude(self):
        home, err = self.run_all(["claude"])
        self.assertEqual(self.written(home), ["claude"], err)
        cfg = json.loads((home / BRANDS["claude"]).read_text(encoding="utf-8"))
        ups = [h["command"] for g in cfg["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        stops = [h["command"] for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
        self.assertTrue(any(c.endswith(" prompt") and A.MARKER in c for c in ups), ups)
        self.assertTrue(any(c.endswith(" review-gate") and A.MARKER in c for c in stops), "85 要能擋:Stop review-gate")

    def test_E2_codex(self):
        home, err = self.run_all(["codex"])
        self.assertEqual(self.written(home), ["codex"], err)
        cfg = json.loads((home / BRANDS["codex"]).read_text(encoding="utf-8"))
        self.assertTrue(any(A.MARKER in h["command"] and " stop;" in h["command"]
                            for g in cfg["hooks"]["Stop"] for h in g["hooks"]))
        self.assertIn("/hooks", err, "Codex 裝好要印一行 /hooks 信任提示")

    def test_E2b_codex_alias_and_codex_home(self):
        ch = self.tmp / "codex-home"
        home, err = self.run_all(["ez2-codex"], extra={"CODEX_HOME": str(ch)})
        self.assertTrue((ch / "hooks.json").is_file(), "CODEX_HOME 有設就裝在那裡:%s" % err)
        self.assertFalse((home / BRANDS["codex"]).exists())

    def test_E3_grok(self):
        home, err = self.run_all(["grok"])
        self.assertEqual(self.written(home), ["grok"], err)
        cfg = json.loads((home / BRANDS["grok"]).read_text(encoding="utf-8"))
        self.assertIn(" stop", cfg["hooks"]["Stop"][0]["hooks"][0]["command"])

    def test_E4_copilot(self):
        home, err = self.run_all(["copilot"])
        self.assertEqual(self.written(home), ["copilot"], err)
        cfg = json.loads((home / BRANDS["copilot"]).read_text(encoding="utf-8"))
        self.assertIn(" stop", cfg["hooks"]["agentStop"][0]["bash"])

    def test_E5_agy(self):
        home, err = self.run_all(["agy"])
        self.assertEqual(self.written(home), ["agy"], err)
        cfg = json.loads((home / BRANDS["agy"]).read_text(encoding="utf-8"))
        self.assertIn("agy-pre", cfg["aib-auto-distill"]["PreInvocation"][0]["command"])

    def test_E6_absent_skipped_and_said(self):
        home, err = self.run_all([])
        self.assertEqual(self.written(home), [])
        self.assertGreaterEqual(err.count("這台沒裝"), 5, err)

    def test_E7_off_installs_nothing(self):
        home, err = self.run_all(list(BRANDS), extra={"AIB_AUTO_DISTILL": "0"})
        self.assertEqual(self.written(home), [], err)

    def test_E8_rerun_no_duplicate_and_backup_others(self):
        home = self.tmp / "home"
        (home / ".codex").mkdir(parents=True)
        other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo someone-else"}]}]}}
        (home / BRANDS["codex"]).write_text(json.dumps(other), encoding="utf-8")
        self.run_all(list(BRANDS), home=home)
        first = {b: (home / rel).read_text(encoding="utf-8") for b, rel in BRANDS.items()}
        _h, err2 = self.run_all(list(BRANDS), home=home)
        second = {b: (home / rel).read_text(encoding="utf-8") for b, rel in BRANDS.items()}
        self.assertEqual(first, second, "重跑不改")
        cfg = json.loads(second["codex"])
        cmds = [h["command"] for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn("echo someone-else", cmds, "別人的 hook 不動")
        self.assertEqual(sum(1 for c in cmds if A.MARKER in c), 1, "不重複裝")
        self.assertEqual(len(list((home / ".codex").glob("hooks.json.bak-auto-distill-*"))), 1, "改之前備份一次")
        claude = json.loads(second["claude"])["hooks"]
        self.assertEqual(sum(1 for g in claude["UserPromptSubmit"] for h in g["hooks"] if A.MARKER in h["command"]), 1)


# ---------------------------------------------------------------- R /clear 之後
class ClearRebind(Base):
    MOCK_SEAT = False

    def setUp(self):
        super().setUp()
        d = self.state / "instances" / INST / "seats"
        d.mkdir(parents=True)
        (d / "w8.json").write_text(json.dumps({"cabin_id": INST, "provider": "claude", "session_uuid": OLD,
                                               "window_id": "@8", "socket": "aihub-" + INST}), encoding="utf-8")

    def by_seat(self, sid=NEW, at=None, seat=SEAT, instance=INST, window="@8"):
        d = self.ctx / "by-seat"
        d.mkdir(exist_ok=True)
        (d / ("%s.json" % seat)).write_text(json.dumps({
            "schema": "ez2-ctx-by-seat-v1", "seat": seat, "instance": instance, "window": window, "session_id": sid,
            "observed_at": _utc(at or time.time()), "pid": 4242}), encoding="utf-8")

    def st_file(self, code):
        return self.state / "state" / "auto-distill" / ("%s.json" % code)

    def test_R1_new_sid_via_by_seat_fires_with_right_code(self):
        self.by_seat()
        self.gauge(86, sid=NEW)
        out = A.on_prompt({"session_id": NEW, "prompt": HUMAN})
        self.assertIn(MARK["handoff_now"], out)
        self.assertTrue(self.st_file(SEAT).is_file(), "席碼要用登記的那一個")
        self.assertFalse(self.st_file("10f31").exists(), "不准用新編號的尾碼當席碼")
        self.assertEqual(json.loads(self.st_file(SEAT).read_text(encoding="utf-8"))["session"], NEW)

    def test_R2_stale_by_seat_no_fire(self):
        self.by_seat(at=time.time() - A.BY_SEAT_FRESH_S - 60)
        self.gauge(86, sid=NEW)
        self.assertEqual(A.on_prompt({"session_id": NEW, "prompt": HUMAN}), "")
        self.assertFalse(self.st_file(SEAT).exists())

    def test_R3_by_seat_other_instance_no_fire(self):
        self.by_seat(instance="20200101-000000-dead")
        self.gauge(86, sid=NEW)
        self.assertEqual(A.on_prompt({"session_id": NEW, "prompt": HUMAN}), "")

    def test_R4_registered_sid_still_works(self):
        self.gauge(86, sid=OLD)
        self.assertIn(MARK["handoff_now"], A.on_prompt({"session_id": OLD, "prompt": HUMAN}))
        self.assertTrue(self.st_file(SEAT).is_file())

    def test_R5_review_gate_after_clear_blocks(self):
        self.by_seat()
        self.gauge(86, sid=NEW)
        A.on_prompt({"session_id": NEW, "prompt": HUMAN})
        out = A.on_ctx_gate({"session_id": NEW})
        self.assertTrue(out and json.loads(out)["decision"] == "block", out)


# ---------------------------------------------------------------- B 橋接 bot
class Bridge(Base):
    CABIN = "20200101-000000-zz9q"

    def test_B1_bot_85_handoff_once(self):
        self.gauge(86)
        text = A.bot_turn(CODE, self.CABIN, gauge_sid=SID)
        self.assertIn(MARK["handoff_now"], text)
        self.assertIn("zz9q/%s" % CODE, text, "bot 不在那一席的窗格裡 ⇒ 指令要帶 --seat")
        self.assertEqual(A.bot_turn(CODE, self.CABIN, gauge_sid=SID), "", "同格只一次")

    def test_B2_bot_80_remind(self):
        self.gauge(81)
        self.assertIn(MARK["handoff_remind"], A.bot_turn(CODE, self.CABIN, gauge_sid=SID))

    def test_B3_bot_no_gauge_turns_only_never_handoff(self):
        outs = [A.bot_turn(CODE, self.CABIN) for _ in range(A.unmeasured_every())]
        self.assertEqual(sum(1 for o in outs if o), 1)
        self.assertIn(MARK["tidy"], outs[-1])
        self.assertFalse(any(MARK["handoff_now"] in o for o in outs), "量不到用量不准假造交接")

    def test_B4_codexbot_passes_desktop_session_gauge(self):
        import codexbot_bridge as cb
        seen = {}
        with mock.patch.object(A, "bot_turn", lambda code, cabin, gauge_sid=None, now=None: seen.update(g=gauge_sid) or ""), \
                mock.patch.dict(sys.modules, {"codex_ctx_probe": types.SimpleNamespace(refresh=lambda u: None)}), \
                mock.patch.dict(os.environ, {"AIB_BRIDGE_CABIN": self.CABIN}):
            sys.modules["auto_distill_hook"] = A
            cb._codexbot_distill(SID, NEW)
        self.assertEqual(seen.get("g"), NEW, "CodexBot 要用桌面對話自己的用量")

    def test_B5_sand_bridge_counts_sand(self):
        import sand_bridge as sb
        calls = []
        with mock.patch.object(A, "bot_turn", lambda *a, **k: calls.append(a) or ""), \
                mock.patch.dict(os.environ, {"AIB_BRIDGE_CABIN": self.CABIN}):
            sys.modules["auto_distill_hook"] = A
            for pid in ("grokbot", "musebot", "sand"):
                sb._bridge_distill(self.tmp, SID, pid, {"to": "human"})
        self.assertEqual(len(calls), 3, "舊的 sand 席也要算")


# ---------------------------------------------------------------- O 外層代觸發(API 金鑰席)
class Outer(Base):
    REC = {"provider": "deepseek", "session_uuid": SID, "cabin_id": "20200101-000000-zz9q", "socket": "s",
           "window_id": "@9"}

    def turn(self):
        sent = []
        A.outer_turn(SID, seat=dict(self.REC), runner=lambda cmd: sent.append((cmd[8], cmd[9])))
        return sent

    def test_O1_api_seat_85_dm_once(self):
        self.gauge(86)
        sent = self.turn()
        self.assertEqual([k for _t, k in sent], ["handoff"], sent)
        self.assertIn(MARK["handoff_now"], sent[0][0])
        self.assertEqual(self.turn(), [], "同格只一次")

    def test_O2_no_gauge_nothing(self):
        self.assertEqual(self.turn(), [])

    def test_O3_off(self):
        self.gauge(86)
        with mock.patch.dict(os.environ, {"AIB_AUTO_DISTILL": "0"}):
            self.assertEqual(self.turn(), [])

    def test_O4_every_api_repl_calls_turn_end(self):
        for name in ("deepseek_cli.py", "nvidia_cli.py", "openrouter_cli.py", "typesafe_cli.py"):
            f = ROOT / "src" / "cockpit" / name
            if not f.is_file():
                continue                       # typesafe_cli.py 公開版沒有(雲端補)
            self.assertIn("_api_tidy.turn_end(sess.name)", f.read_text(encoding="utf-8"), name)
        self.assertIn("adh.outer_turn", (ROOT / "src" / "memory" / "api_tidy.py").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- U 量不到用量:每 N 輪精煉(維護者 22:4x)
class Unmeasured(Base):
    REC = Outer.REC

    def policy(self, n):
        doc = json.loads((ROOT / "policy" / "ctx-thresholds.json").read_text(encoding="utf-8"))
        doc["unmeasured_tidy_every_turns"] = n
        p = self.tmp / "policy.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        q = mock.patch.dict(os.environ, {"EZ2_AIB_CTX_POLICY": str(p)})
        q.start()
        self.addCleanup(q.stop)

    def due_turns(self, n_turns, fn):
        return [i for i in range(1, n_turns + 1) if fn()]

    def test_U1_api_seat_every_20(self):
        outs = {}
        for i in range(1, 41):
            o = A.outer_turn(SID, seat=dict(self.REC), runner=lambda cmd: None)
            if o:
                outs[i] = o
        self.assertEqual(sorted(outs), [20, 40], "第 19 輪不產生、第 20 / 40 輪各一次")
        self.assertIn(MARK["tidy"], outs[20])

    def test_U2_policy_n_moves(self):
        self.policy(5)
        self.assertEqual(self.due_turns(12, lambda: A.outer_turn(SID, seat=dict(self.REC), runner=lambda c: None)),
                         [5, 10])

    def test_U3_api_tidy_turn_end_drives_repl(self):
        import api_tidy
        sys.modules["auto_distill_hook"] = A
        self.assertEqual(self.due_turns(40, lambda: api_tidy.turn_end(SID)), [20, 40])

    def test_U4_bot_reads_same_policy(self):
        self.policy(5)
        self.assertEqual(self.due_turns(10, lambda: A.bot_turn(CODE, "20200101-000000-zz9q")), [5, 10])

    def test_U5_measured_seat_unaffected(self):
        self.gauge(10)
        self.assertEqual(self.due_turns(20, lambda: A.outer_turn(SID, seat=dict(self.REC), runner=lambda c: None)), [])

    def test_U6_new_conversation_restarts_count(self):
        for _ in range(19):
            A.outer_turn(SID, seat=dict(self.REC), runner=lambda c: None)
        other = "00000000-0000-4000-8000-0000000ccccc"
        self.assertFalse(A.outer_turn(other, seat=dict(self.REC, session_uuid=other), runner=lambda c: None),
                         "換對話從 0 算,不疊算")


# ---------------------------------------------------------------- N 入口
class Entry(Base):
    def _env(self, home, fake_bins=("claude",)):
        fake = self.tmp / "fakebin"
        fake.mkdir(exist_ok=True)
        for b in fake_bins:
            (fake / b).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (fake / b).chmod(0o755)
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "AIB_AUTO_DISTILL") and not k.startswith(("LC_", "LANG"))}
        env.update(HOME=str(home), PATH="%s:/usr/bin:/bin" % fake, EZ2_AIB_LOCALE="zh-TW", LANG="C",
                   EZ2_AIB_STATE_ROOT=str(self.tmp / "aibstate"), EZ2_AIB_DESKTOP_DIR=str(self.tmp / "desk"),
                   PYTHONPYCACHEPREFIX=str(self.tmp / "pyc"))
        return env

    def test_N1_aib_init_installs(self):
        home = self.tmp / "home"
        home.mkdir()
        r = run_guarded([sys.executable, str(ROOT / "src" / "bootstrap" / "aib_init.py"), "--json"],
                        stdin=subprocess.DEVNULL, timeout=120, env=self._env(home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((home / ".claude" / "settings.json").is_file(), r.stderr)
        doc = json.loads(r.stdout)
        self.assertIn("claude", {h.get("brand") for h in doc.get("hooks") or []}, "--json 要列出各家 hook 結果")

    def test_N2_aib_update_runs_ensure_all_after_installer(self):
        root = self.tmp / "checkout"
        (root / ".git").mkdir(parents=True)
        (root / "aib").mkdir()
        shutil.copy2(ROOT / "cli.py", root / "aib" / "cli.py")
        stub = root / "aib" / "src" / "identity" / "auto_distill_hook.py"
        stub.parent.mkdir(parents=True)
        mark = self.tmp / "ensure-all-ran"
        stub.write_text("import sys\nopen(%r, 'w').write(' '.join(sys.argv[1:]))\n" % str(mark), encoding="utf-8")
        inst = self.tmp / "inst.sh"
        inst.write_text("# github.com/ez2app/ez2-aib.git\necho installer-ran >&2\nexit 0\n", encoding="utf-8")
        env = self._env(self.tmp / "home")
        env["AIB_INSTALL_URL"] = "file://%s" % inst
        r = run_guarded([sys.executable, str(root / "aib" / "cli.py"), "update"], stdin=subprocess.DEVNULL, timeout=120,
                        env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("installer-ran", r.stderr)
        self.assertTrue(mark.is_file(), "升級完成後要跑 ensure-all:%s" % r.stderr)
        self.assertEqual(mark.read_text(encoding="utf-8"), "ensure-all")

    def test_N3_aib_update_installer_failed_no_ensure(self):
        root = self.tmp / "checkout"
        (root / ".git").mkdir(parents=True)
        (root / "aib").mkdir()
        shutil.copy2(ROOT / "cli.py", root / "aib" / "cli.py")
        stub = root / "aib" / "src" / "identity" / "auto_distill_hook.py"
        stub.parent.mkdir(parents=True)
        mark = self.tmp / "ensure-all-ran"
        stub.write_text("open(%r, 'w').write('x')\n" % str(mark), encoding="utf-8")
        inst = self.tmp / "inst.sh"
        inst.write_text("# github.com/ez2app/ez2-aib.git\nexit 3\n", encoding="utf-8")
        env = self._env(self.tmp / "home")
        env["AIB_INSTALL_URL"] = "file://%s" % inst
        r = run_guarded([sys.executable, str(root / "aib" / "cli.py"), "update"], stdin=subprocess.DEVNULL, timeout=120,
                        env=env)
        self.assertEqual(r.returncode, 3, "安裝程式的結束碼要原樣傳回")
        self.assertFalse(mark.exists())

    def test_N4_install_sh_calls_ensure_all_before_init(self):
        text = (ROOT.parent / "install.sh").read_text(encoding="utf-8") if (ROOT.parent / "install.sh").is_file() else ""
        if not text:
            self.skipTest("這份樹沒有 install.sh(pipx 版)")
        m = re.search(r'if \[ "\$\{KEEP:-0\}" != 1 \]; then\n(.*?)\n      fi\n', text, re.S)
        self.assertIsNotNone(m, "找不到「沒有別的 aib 才動」那一段")
        block = m.group(1)
        self.assertIn("auto_distill_hook.py", block)
        self.assertIn("ensure-all", block)
        self.assertLess(block.index("ensure-all"), block.index("\"$abin\" init"), "ensure-all 在 aib init 之前(init 的輸出被吃掉)")


# ---------------------------------------------------------------- K 刀
HOOK_REL = "src/identity/auto_distill_hook.py"
KNIVES = {
    "no_claude": (HOOK_REL, '    ("claude", "Claude Code", ("claude",), "_ensure_claude_all"),\n', "",
                  ["EnsureAll.test_E1_claude"]),
    "no_codex": (HOOK_REL, '    ("codex", "Codex", ("codex", "ez2-codex"), "_ensure_codex_all"),\n', "",
                 ["EnsureAll.test_E2_codex"]),
    "no_agy": (HOOK_REL, '    ("agy", "Antigravity", ("agy",), "_ensure_agy_all"),\n', "", ["EnsureAll.test_E5_agy"]),
    "no_which": (HOOK_REL, "        if not any(which(b) for b in bins):\n", "        if False:\n",
                 ["EnsureAll.test_E6_absent_skipped_and_said"]),
    "no_by_seat": (HOOK_REL, "        code = _by_seat_code(sid, files, now)\n", "        code = None\n",
                   ["ClearRebind.test_R1_new_sid_via_by_seat_fires_with_right_code"]),
    "by_seat_no_fresh": (HOOK_REL, "        if ms is None or t - ms / 1000.0 > BY_SEAT_FRESH_S:\n",
                         "        if ms is None:\n", ["ClearRebind.test_R2_stale_by_seat_no_fire"]),
    "no_bot_handoff": (HOOK_REL, "            hand = _bot_handoff(code, cabin, st, trig, pct, now)\n",
                       '            hand = ""\n', ["Bridge.test_B1_bot_85_handoff_once"]),
    "no_outer": (HOOK_REL, "        out = on_stop({\"session_id\": sid}, now=now, seat=rec, runner=runner,",
                 "        out = \"\" and on_stop({\"session_id\": sid}, now=now, seat=rec, runner=runner,",
                 ["Outer.test_O1_api_seat_85_dm_once"]),
    "no_repl_call": ("src/cockpit/deepseek_cli.py", "            if _api_tidy.turn_end(sess.name):\n", "            if False:\n",
                     ["Outer.test_O4_every_api_repl_calls_turn_end"]),
    "no_init_call": ("src/bootstrap/aib_init.py", "    hooks = _ensure_hooks()\n", "    hooks = []\n",
                     ["Entry.test_N1_aib_init_installs"]),
    "no_update_call": ("cli.py", "'[ \"$rc\" -ne 0 ] || [ ! -f \"$AIB_UPDATE_HOOK\" ] || \"$AIB_UPDATE_PY\" \"$AIB_UPDATE_HOOK\" ensure-all >&2; '",
                       "''", ["Entry.test_N2_aib_update_runs_ensure_all_after_installer"]),
    "no_api_every": (HOOK_REL, "    text = _unmeasured_tidy(code, sid, rec)\n", '    text = ""\n',
                     ["Unmeasured.test_U1_api_seat_every_20"]),
    "bot_hardcoded_n": (HOOK_REL, "    elif unmeasured_due(code, gauge_sid or \"\"):\n",
                        "    elif st[\"turns\"] % 20 == 0:\n", ["Unmeasured.test_U4_bot_reads_same_policy"]),
    "no_codexbot_gauge": ("src/cockpit/codexbot_bridge.py",
                          "        return adh.bot_turn(uuid_tail5(session_id) or \"\", cabin, gauge_sid=gauge_sid)\n",
                          "        return adh.bot_turn(uuid_tail5(session_id) or \"\", cabin)\n",
                          ["Bridge.test_B4_codexbot_passes_desktop_session_gauge"]),
}


class Knives(unittest.TestCase):
    def _knife(self, name):
        rel, needle, repl, cells = KNIVES[name]
        with knife_copy(ROOT, rel, needle, repl, tests=(THIS,)) as copy:
            r = run_guarded([sys.executable, str(copy / "tests" / THIS)] + cells, cwd=str(copy),
                            stdin=subprocess.DEVNULL, timeout=300)
        out = r.stdout + r.stderr
        self.assertIsNone(r.tripped, out[-1500:])
        self.assertNotEqual(r.returncode, 0, "刀「%s」下應紅:\n%s" % (name, out[-1500:]))
        for c in cells:
            self.assertRegex(out, r"(FAIL|ERROR): %s " % re.escape(c.split(".")[1]), "宣稱的格沒紅:%s\n%s" % (c, out[-1500:]))


for _n in KNIVES:
    setattr(Knives, "test_K_%s" % _n, (lambda n: lambda self: self._knife(n))(_n))


if __name__ == "__main__":
    unittest.main()
