#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CTX 門檻併成一張表、每一格由程式觸發(維護者 10-15 20:0x「把這二個合成一個,並要確定會由程式觸發」;
20:1x「只保留 A 一張表」;20:2x「85% 要強制執行」「真人訊息有『先做』那一則不擋」)。

  P  單一資料源:policy/ctx-thresholds.json 只有 triggers 七格(沒有 stages / actions);hook 不寫數字;
     改 policy 的值 ⇒ 觸發點跟著變;艦隊那邊的讀者(lifecycle / ctx_scheduler / seat_continuity / handover_prep /
     fleet_patrol / rotation)讀到的是表上的格(或刪掉的就是 None)
  C  Claude 席 UserPromptSubmit:每一格跨過的輸出、同格只一次、85 verify 沒過每則再提、讀不到 gauge 無輸出、錯 ⇒ rc 0
  G  Claude 席 Stop(review-gate):85 沒交接 ⇒ block;verify 過 ⇒ 放行;擋滿 3 次放行並標記;gauge 讀不到不擋;
     真人「先做」不擋、別席 dm 寫「先做」照擋
  S  非 Claude 席 stop:aib dm 給自己(提醒 / 要求交接 / 再提醒封頂 / 精煉)
  I  安裝:canonical_script 兩種版面(公開倉 git 連結工作樹、pipx 不在 git 裡)、ensure 寫進暫存 settings、
     provider-launch.sh 的 [ -t 0 ]:stdin 不是終端機不裝、tmux 窗格裡會裝
  T  開席單鐵則那一行跟表上的數字一致
  F  巡檢唯讀:≥ 85 且 verify 沒過 ⇒ 一行「85% 交接未完成」
  K  刀:各自退回一塊 ⇒ 對應格紅

全程暫存狀態根 / 暫存 gauge / 暫存 settings;不碰 ~/.claude、~/.ez2-aib。子行程經 aib_proc_guard.run_guarded。
"""
import contextlib
import datetime
import io
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

_BASE = tempfile.mkdtemp(prefix="ctx-triggers-", dir=aib_tmp_root())
for _k in ("EZ2_AIB_CTX_POLICY", "GROK_SESSION_ID", "AIB_AUTO_DISTILL_DM", "AIB_AUTO_DISTILL", "EZ2_AIB_HOME"):
    os.environ.pop(_k, None)
os.environ["EZ2_AIB_STATE_ROOT"] = os.path.join(_BASE, "state")
os.environ["EZ2_CTX_DIR"] = os.path.join(_BASE, "ctx")
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_BASE, "claude-config")   # 任何誤裝都落在暫存
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _BASE, True)

import auto_distill_hook as A  # noqa: E402
import ctx_policy as CP  # noqa: E402

SID = "00000000-0000-4000-8000-0000000abcde"
SID2 = "00000000-0000-4000-8000-0000000bbbbb"
CODE = "abcde"
HUMAN = "幫我看一下這個問題好嗎"
POLICY = ROOT / "policy" / "ctx-thresholds.json"
LOCALES = ("zh-TW", "en", "ja", "ko")
# zh-TW 字串裡各動作看得出來的那一段(測的是「哪一句被注入」;全文由 i18n 決定)
MARK = {"tidy": "aib memory tidy collect", "handoff_remind": "交接提醒", "handoff_now": "收尾三步",
        "handoff_again": "交接還沒通過", "red_line": "紅線", "gate": "不能結束這一輪"}


def _fake_human_filter():
    """公開版沒有 persona-collect/human_filter.py;測試給一支最小的:dm 標頭開頭的不是真人。"""
    m = types.ModuleType("human_filter")
    m.is_human = lambda seat, row, skip_ledger=True: (not str(row.get("text") or "").startswith("[dm"), {})
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cell-", dir=_BASE))
        self.state = self.tmp / "state"
        self.ctx = self.tmp / "ctx"
        self.ctx.mkdir(parents=True)
        env = {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_CTX_DIR": str(self.ctx)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for name, val in (("is_aib_seat", lambda sid: True), ("tidy_receipt", lambda code: {}),
                          ("inbox_block", lambda *a, **k: ""), ("_live_verify", lambda code: False)):
            q = mock.patch.object(A, name, val, create=True)   # 改之前的版本沒有 _live_verify
            q.start()
            self.addCleanup(q.stop)
        q = mock.patch.dict(sys.modules, {"human_filter": _fake_human_filter()})
        q.start()
        self.addCleanup(q.stop)

    def gauge(self, pct, sid=SID, age_s=0, at=None):
        t = datetime.datetime.fromtimestamp(at if at is not None else time.time() - age_s, datetime.timezone.utc)
        (self.ctx / ("%s.pct" % sid)).write_text(json.dumps({"pct": pct, "observed_at": t.strftime("%Y-%m-%dT%H:%M:%SZ")}),
                                                 encoding="utf-8")

    def prompt(self, text=HUMAN, sid=SID):
        return A.on_prompt({"session_id": sid, "prompt": text})

    def receipt(self, ok=True):
        d = self.state / "state" / "handoff"
        d.mkdir(parents=True, exist_ok=True)
        items = [{"item": "i%d" % i, "ok": ok, "detail": ""} for i in range(8)]
        (d / ("%s.json" % CODE)).write_text(json.dumps({"ts": time.time() + 1, "verify": {"ok": ok, "items": items}}),
                                            encoding="utf-8")

    def st(self):
        return json.loads((self.state / "state" / "auto-distill" / ("%s.json" % CODE)).read_text(encoding="utf-8"))

    def write_policy(self, cells):
        p = self.tmp / "policy.json"
        texts = json.loads(POLICY.read_text(encoding="utf-8"))["texts"]
        p.write_text(json.dumps({"schema_version": "2", "triggers": cells, "texts": texts}), encoding="utf-8")
        return p


# ---------------------------------------------------------------- P 單一資料源
class Policy(Base):
    def test_P1_single_table_seven_cells(self):
        doc = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertNotIn("stages", doc, "B 的 stages 要刪掉")
        self.assertNotIn("actions", doc, "B 的 actions 要刪掉")
        self.assertEqual([c["pct"] for c in CP.cells()], [30, 50, 70, 80, 85, 90, 95])
        self.assertEqual({c["pct"]: c["do"] for c in CP.cells()}[85], ("tidy", "handoff_now"))
        for loc in LOCALES:
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for t in CP.triggers():
                for f in CP.TEXT_FIELDS:
                    if f in t:
                        self.assertIn(t[f], d, "%s 缺 %s" % (loc, t[f]))
            self.assertIn("ctx_trigger.gate_block", d, loc)

    def test_P2_hook_writes_no_threshold_numbers(self):
        src = (ROOT / "src" / "identity" / "auto_distill_hook.py").read_text(encoding="utf-8")
        self.assertNotIn("AUTO_THRESHOLDS", src)
        self.assertNotIn("ctx-handoff-nudge", src)
        self.assertEqual(A.thresholds(), tuple(sorted({t["pct"] for t in CP.triggers() if t["kind"] == "tidy"})))

    def test_P3_policy_change_moves_triggers(self):
        p = self.write_policy([{"pct": 40, "do": ["tidy"]}, {"pct": 60, "do": ["handoff_remind"]},
                               {"pct": 66, "do": ["tidy", "handoff_now"]}, {"pct": 77, "do": ["red_line"]}])
        with mock.patch.dict(os.environ, {"EZ2_AIB_CTX_POLICY": str(p)}):
            self.gauge(35)
            self.assertEqual(self.prompt(), "", "35 < 40:預設表的 30 不該再觸發")
            self.gauge(41)
            self.assertIn(MARK["tidy"], self.prompt())
            self.gauge(61)
            out = self.prompt()
            self.assertIn(MARK["handoff_remind"], out)
            self.assertIn("61%", out)
            self.assertIn("66%", out, "提醒裡的「要求交接在幾 %」讀表")
            self.gauge(67)
            self.assertIn(MARK["handoff_now"], self.prompt())
            self.gauge(78)
            self.assertIn(MARK["red_line"], self.prompt())
            self.assertEqual(CP.stages_descending(), ((77, "red_line"), (66, "handoff_now"), (60, "handoff_remind"),
                                                      (0, "ok")))

    def test_P4_fleet_readers_read_the_table(self):
        """policy 檔沒有 stages / actions 也能跑,而且艦隊那邊讀到的是表上的格(改表 ⇒ 跟著變)。"""
        p = self.write_policy([{"pct": 30, "do": ["tidy"]}, {"pct": 72, "do": ["handoff_remind"]},
                               {"pct": 83, "do": ["tidy", "handoff_now"]}, {"pct": 94, "do": ["red_line"]}])
        code = r"""
import json, sys
sys.path[:0] = [sys.argv[1] + "/src/rotation", sys.argv[1] + "/src/cockpit", sys.argv[1] + "/src"]
import lifecycle, handover_prep, rotation, fleet_patrol
from fleet_identity import seat_continuity
out = {"stages": [list(x) for x in lifecycle.STAGES], "stage_73": lifecycle.stage_for(73)[0],
       "final_floor": lifecycle.FINAL_CLEAR_ADVICE_FLOOR, "prep_82": handover_prep.suggest(82),
       "prep_83": bool(handover_prep.suggest(83)), "rotate": rotation.DEFAULT_ROTATE_THRESHOLD,
       "advisor": rotation.ADVISOR_STOP_CTX, "seat_gate": fleet_patrol.SEAT_GATE_CTX_THRESHOLD,
       "gate_verdict": fleet_patrol.seat_gate([{"ctx_pct": 99}])[0]}
import hashlib
raw = open(sys.argv[2], "rb").read()
out["continuity"] = seat_continuity._policy(sys.argv[2], hashlib.sha256(raw).hexdigest())[2]
print(json.dumps(out))
"""
        env = dict(os.environ, EZ2_AIB_CTX_POLICY=str(p))
        r = run_guarded([sys.executable, "-c", code, str(ROOT), str(p)], env=env, stdin=subprocess.DEVNULL,
                        timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(got["stages"], [[94, "red_line"], [83, "handoff_now"], [72, "handoff_remind"], [0, "ok"]])
        self.assertEqual(got["stage_73"], "handoff_remind", "派工閘 = 表上交接提醒那格")
        self.assertEqual(got["final_floor"], 83)
        self.assertIsNone(got["prep_82"])
        self.assertTrue(got["prep_83"])
        self.assertEqual(got["continuity"], 83, "交接憑證讀表上要求交接那格")
        self.assertIsNone(got["rotate"], "輪替門檻刪掉(不改寫成新數字)")
        self.assertIsNone(got["advisor"], "顧問停止門檻刪掉")
        self.assertIsNone(got["seat_gate"], "增員門檻刪掉")
        self.assertFalse(got["gate_verdict"], "增員閘不再自動判 ⇒ 不准(由人決定)")

    def test_P5_broken_table_raises_with_guard_mark(self):
        p = self.tmp / "bad.json"
        for doc in ({"triggers": []}, {"triggers": [{"pct": 30, "do": ["tidy"]}]},
                    {"triggers": [{"pct": 30, "do": ["nope"]}], "texts": {}},
                    {"triggers": [{"pct": 30, "do": ["tidy"]}, {"pct": 30, "do": ["red_line"]}],
                     "texts": {"tidy": {"key": "a"}, "red_line": {"key": "b"}}}):
            p.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises((KeyError, ValueError)) as cm:
                CP.triggers(str(p))
            self.assertIn(CP.GUARD_MARK, str(cm.exception))


# ---------------------------------------------------------------- C Claude 席 UserPromptSubmit
class Prompt(Base):
    def test_C1_tidy_cell(self):
        self.gauge(31)
        out = self.prompt()
        self.assertIn(MARK["tidy"], out)
        self.assertIn("31%", out)
        self.assertEqual(self.prompt(), "", "同格同對話只一次")

    def test_C2_remind_at_80_once(self):
        self.gauge(81)
        out = self.prompt()
        self.assertIn(MARK["handoff_remind"], out)
        self.assertIn("81%", out)
        self.assertEqual(self.prompt(), "", "同格同對話只一次")

    def test_C3_handoff_now_injected_with_read_pct(self):
        self.gauge(86)
        out = self.prompt()
        self.assertIn(MARK["handoff_now"], out)
        self.assertIn("86%", out, "% 是讀 gauge 來的")
        self.assertIn("aib handoff verify", out)
        self.assertNotIn(MARK["tidy"], out, "收尾三步已含整理,不另叫 tidy")
        self.assertIn("handoff_now@85", self.st()["fired"])

    def test_C4_reminds_every_message_until_verify_passes(self):
        self.gauge(86)
        self.prompt()
        self.gauge(87)
        out = self.prompt()
        self.assertIn(MARK["handoff_again"], out, "沒收據 ⇒ 每則再提醒")
        self.assertIn("87%", out)
        self.assertIn(MARK["handoff_again"], self.prompt())
        self.receipt(ok=True)
        self.assertEqual(self.prompt(), "", "收據 verify 過 ⇒ 不再提醒")

    def test_C4b_receipt_failed_then_live_verify(self):
        self.gauge(86)
        self.prompt()
        self.receipt(ok=False)
        self.assertIn(MARK["handoff_again"], self.prompt(), "收據寫著沒過、當下重驗也沒過 ⇒ 再提醒")
        with mock.patch.object(A, "_live_verify", lambda code: True):
            self.assertEqual(self.prompt(), "", "補好之後當下重驗過 ⇒ 不提醒")

    def test_C5_red_line(self):
        self.gauge(96)
        out = self.prompt()
        self.assertIn(MARK["red_line"], out)
        self.assertIn("96%", out)
        self.assertNotIn(MARK["handoff_now"] + "]", out, "同一則只講最重那一句")

    def test_C6_no_gauge_no_output(self):
        self.assertEqual(self.prompt(), "")
        self.gauge(90, age_s=1200)                     # 過期
        self.assertEqual(self.prompt(), "")
        self.assertEqual(self.st().get("fired"), [], "讀不到不猜:不記任何格")

    def test_C7_errors_rc0_no_output(self):
        self.gauge(86)
        with mock.patch.dict(os.environ, {"EZ2_AIB_CTX_POLICY": str(self.tmp / "missing.json")}), \
                mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": SID, "prompt": HUMAN}))), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = A.main(["prompt"])
        self.assertEqual((rc, out.getvalue()), (0, ""))
        with mock.patch("sys.stdin", io.StringIO("{not json")), contextlib.redirect_stdout(io.StringIO()) as out:
            rc = A.main(["prompt"])
        self.assertEqual((rc, out.getvalue()), (0, ""))

    def test_C8_new_conversation_fires_again(self):
        self.gauge(81)
        self.assertIn(MARK["handoff_remind"], self.prompt())
        self.gauge(81, sid=SID2)
        self.assertIn(MARK["handoff_remind"], A.on_prompt({"session_id": SID2, "prompt": HUMAN}))


# ---------------------------------------------------------------- G Claude 席 Stop:85 強制
class StopGate(Base):
    def gate(self, sid=SID):
        return A.on_ctx_gate({"session_id": sid})

    def test_G1_unverified_blocks(self):
        self.gauge(86)
        self.prompt()
        out = self.gate()
        self.assertTrue(out, "85 沒交接 ⇒ 要擋")
        doc = json.loads(out)
        self.assertEqual(doc["decision"], "block")
        self.assertIn("86%", doc["reason"])
        self.assertIn(MARK["gate"], doc["reason"])

    def test_G2_verified_releases(self):
        self.gauge(86)
        self.prompt()
        self.receipt(ok=True)
        self.assertEqual(self.gate(), "")

    def test_G3_cap_then_release_and_mark(self):
        self.gauge(86)
        self.prompt()
        for _ in range(A.CTX_GATE_MAX):
            self.assertTrue(self.gate())
        self.assertEqual(self.gate(), "", "擋滿 ⇒ 放行")
        self.assertTrue(A.gate_gave_up(CODE), "擋滿放行要記下來(巡檢列)")
        self.prompt()
        self.assertTrue(self.gate(), "下一則重新算")

    def test_G4_no_gauge_no_block(self):
        self.prompt()
        self.assertEqual(self.gate(), "")
        self.gauge(70)
        self.assertEqual(self.gate(), "", "沒到 85 不擋")

    def test_G5_human_xianzuo_not_blocked(self):
        self.gauge(86)
        self.prompt("先做完這個小修再交接")
        self.assertEqual(self.gate(), "")
        log = (self.state / "logs" / "auto-distill.jsonl").read_text(encoding="utf-8")
        self.assertIn("ctx-gate-bypass", log, "放行要記 log")
        self.prompt()
        self.assertTrue(self.gate(), "下一則沒有「先做」⇒ 恢復擋")

    def test_G6_dm_xianzuo_still_blocked(self):
        self.gauge(86)
        self.prompt("[dm abcd/x1y2z > abcd/abcde] 先做 A 再交接")
        self.assertTrue(self.gate(), "別席 dm 寫「先做」不算")

    def test_G7_main_review_gate_prints_block(self):
        self.gauge(86)
        self.prompt()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": SID}))), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = A.main(["review-gate"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["decision"], "block")

    def test_G8_verify_unknown_no_block(self):
        self.gauge(86)
        self.prompt()
        self.receipt(ok=False)
        with mock.patch.object(A, "_live_verify", lambda code: None):
            self.assertEqual(self.gate(), "", "verify 本身出錯 ⇒ 不擋")


# ---------------------------------------------------------------- S 非 Claude 席 stop
class Stop(Base):
    REC = {"provider": "codex", "session_uuid": SID, "cabin_id": "20200101-000000-zz9q", "socket": "s",
           "window_id": "@9"}

    def stop(self, now=None):
        sent = []
        A.on_stop({}, now=now, seat=dict(self.REC), runner=lambda cmd: sent.append((cmd[8], cmd[9])),
                  refresh=lambda p, s: None, gate=lambda argv, text=None: (0, ""))
        return sent

    def test_S1_remind_dm(self):
        self.gauge(81)
        sent = self.stop()
        self.assertTrue(any(k == "handoff" and MARK["handoff_remind"] in t and "81%" in t for t, k in sent), sent)
        self.assertTrue(any(k == "tidy" for _t, k in sent), "提醒不吃掉精煉格")
        self.assertEqual(self.stop(), [], "同格只一次")

    def test_S2_handoff_now_dm(self):
        self.gauge(86)
        sent = self.stop()
        self.assertEqual([k for _t, k in sent], ["handoff"], sent)
        self.assertIn(MARK["handoff_now"], sent[0][0])
        self.assertIn("86%", sent[0][0])

    def test_S3_again_capped(self):
        self.gauge(86)
        self.stop()
        t = time.time()
        n = 0
        for i in range(1, 7):
            at = t + i * (A.FRESH_S + 1)
            self.gauge(87, at=at)
            sent = self.stop(now=at)
            n += sum(1 for txt, k in sent if k == "handoff" and MARK["handoff_again"] in txt)
        self.assertEqual(n, A.STOP_NAG_MAX, "再提醒要封頂(dm 會開新的一輪)")

    def test_S4_tidy_dm(self):
        self.gauge(31)
        sent = self.stop()
        self.assertEqual([k for _t, k in sent], ["tidy"])
        self.assertIn("31%", sent[0][0])

    def test_S5_dm_off(self):
        self.gauge(86)
        with mock.patch.dict(os.environ, {"AIB_AUTO_DISTILL_DM": "0"}):
            self.assertEqual(self.stop(), [])


# ---------------------------------------------------------------- I 安裝
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
PROBE = "import sys; sys.path.insert(0, sys.argv[1]); import auto_distill_hook as a; print(a.canonical_script())"


def _place(dst_src):
    """把本支與它要的 canonical_tree 擺進一棵 src/ 版面。"""
    for rel in ("identity/auto_distill_hook.py", "cockpit/canonical_tree.py", "policy/hooks/aib-now-gate.py"):
        (dst_src / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "src" / rel, dst_src / rel)


def _probe(src_dir):
    r = run_guarded([sys.executable, "-c", PROBE, str(src_dir / "identity")], stdin=subprocess.DEVNULL, timeout=60,
                    env=dict(os.environ, **GIT_ENV))
    return r.returncode, r.stdout.strip(), r.stderr


class Install(Base):
    def test_I1_canonical_open_source_git_layout(self):
        repo = self.tmp / "ez2-aib"
        _place(repo / "aib" / "src")
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
            r = run_guarded(["git"] + args, cwd=str(repo), env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
        wt = self.tmp / "wt"
        r = run_guarded(["git", "worktree", "add", "-q", str(wt)], cwd=str(repo), env=dict(os.environ, **GIT_ENV),
                        stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        rc, out, err = _probe(wt / "aib" / "src")
        self.assertEqual(rc, 0, err)
        want = repo / "aib" / "src" / "identity" / "auto_distill_hook.py"
        self.assertEqual(os.path.realpath(out), os.path.realpath(str(want)), "公開倉:正本樹 + aib/ 前綴")
        self.assertTrue(os.path.isfile(out))

    def test_I2_canonical_pipx_layout(self):
        site = self.tmp / "venv" / "lib" / "site-packages" / "aib" / "src"
        _place(site)
        rc, out, err = _probe(site)
        self.assertEqual(rc, 0, err)
        self.assertEqual(os.path.realpath(out), os.path.realpath(str(site / "identity" / "auto_distill_hook.py")),
                         "不在 git 裡 ⇒ 本支就是正本")

    def test_I3_ensure_writes_temp_settings(self):
        settings = self.tmp / "claude" / "settings.json"
        r = run_guarded([sys.executable, str(ROOT / "src" / "identity" / "auto_distill_hook.py"), "ensure",
                         "--settings", str(settings)], stdin=subprocess.DEVNULL, timeout=60,
                        env=dict(os.environ, CLAUDE_CONFIG_DIR=str(self.tmp / "claude")))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        ups = [h["command"] for g in cfg["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        stops = [h["command"] for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
        self.assertTrue(any("auto_distill_hook.py" in c and c.endswith(" prompt") for c in ups), ups)
        self.assertTrue(any("auto_distill_hook.py" in c and c.endswith(" review-gate") for c in stops), stops)
        path = re.search(r"python3 (\S+auto_distill_hook\.py) prompt", ups[-1]).group(1)
        self.assertTrue(os.path.isfile(path.strip("'")), "裝進去的路徑要真的存在:%s" % path)

    def _launch_block(self):
        text = (ROOT / "src" / "cockpit" / "provider-launch.sh").read_text(encoding="utf-8")
        m = re.search(r'^if \[ "\$PROVIDER" = "claude" \][^\n]*identity/auto_distill_hook\.py" \]; then\n.*?\nfi\n',
                      text, re.M | re.S)
        self.assertIsNotNone(m, "provider-launch.sh 裡找不到 Claude 席裝 auto_distill 的那一段")
        return m.group(0)

    def _script(self, conf):
        sh = self.tmp / "launch-part.sh"
        sh.write_text("HERE=%s\nPROVIDER=claude\nexport CLAUDE_CONFIG_DIR=%s\n%s" % (
            ROOT / "src" / "cockpit", conf, self._launch_block()), encoding="utf-8")
        return sh

    def test_I4a_launch_without_tty_does_not_install(self):
        conf = self.tmp / "conf-a"
        r = run_guarded(["bash", str(self._script(conf))], stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((conf / "settings.json").exists(), "stdin 不是終端機(測試 / 管線)不准寫使用者設定")

    @unittest.skipUnless(shutil.which("tmux"), "沒有 tmux")
    def test_I4b_launch_in_tmux_pane_installs(self):
        """開席是 tmux respawn-pane 起的:窗格的 stdin 是 pty ⇒ [ -t 0 ] 成立 ⇒ 會裝(不是今天沒觸發的原因)。"""
        conf = self.tmp / "conf-b"
        done = self.tmp / "done"
        sock = "ctxtrig-%d" % os.getpid()
        cmd = "bash %s; echo $? > %s" % (self._script(conf), done)
        self.addCleanup(lambda: run_guarded(["tmux", "-L", sock, "kill-server"], stdin=subprocess.DEVNULL, timeout=20))
        r = run_guarded(["tmux", "-L", sock, "-f", "/dev/null", "new-session", "-d", "-s", "p", cmd],
                        stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        t0 = time.time()
        while not done.exists() and time.time() - t0 < 60:
            time.sleep(0.2)
        self.assertTrue(done.exists(), "tmux 窗格裡那段沒跑完")
        self.assertTrue((conf / "settings.json").is_file(), "tmux 窗格裡應該會裝")


# ---------------------------------------------------------------- T 開席單 / F 巡檢
class Text(Base):
    def test_T1_onboard_rule_matches_table(self):
        text = (ROOT / "docs" / "templates" / "SEAT-ONBOARD.md").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if "aib handoff" in ln and ln[:2].strip(". ").isdigit()]
        self.assertEqual(len(lines), 1, lines)
        for kind in ("handoff_remind", "handoff_now", "red_line"):
            self.assertIn("%d%%" % CP.trigger_pct(kind), lines[0], kind)

    def test_F1_patrol_lists_unverified_handoff(self):
        import fleet_patrol
        line = fleet_patrol.handoff_unverified_line(CODE, 88, "machine")
        self.assertIn("85% 交接未完成", line)
        self.assertIn("88%", line)
        self.assertEqual(fleet_patrol.handoff_unverified_line(CODE, 88, "screen"), "", "畫面值不算")
        self.assertEqual(fleet_patrol.handoff_unverified_line(CODE, 84, "machine"), "")
        self.receipt(ok=True)
        self.assertEqual(fleet_patrol.handoff_unverified_line(CODE, 88, "machine"), "")


# ---------------------------------------------------------------- K 刀
HARD = ('    return tuple({"pct": p, "kind": k, "key": "auto_distill.instruct"} for p, k in '
        '((30, "tidy"), (50, "tidy"), (70, "tidy"), (80, "handoff_remind"), (85, "tidy"), (85, "handoff_now"), '
        '(90, "tidy"), (95, "red_line")))   # knife: 寫死\n')
OLD_CANON = ('    import subprocess\n'
             '    out = subprocess.run(["git", "-C", str(HERE), "worktree", "list", "--porcelain"], capture_output=True,\n'
             '                         text=True, stdin=subprocess.DEVNULL).stdout\n'
             '    for line in out.splitlines():\n'
             '        if line.startswith("worktree "):\n'
             '            return os.path.join(line[len("worktree "):], "src", "identity", MARKER)   # knife: 私倉版面\n'
             '    return str(Path(__file__).resolve())\n')
HOOK = "src/identity/auto_distill_hook.py"
KNIVES = {
    "hardcode": (HOOK, "    return ctx_policy.triggers()\n", HARD, ["Policy.test_P3_policy_change_moves_triggers"]),
    "no_85_inject": (HOOK, '            hand_line = T(top["key"], params, lang)\n', '            hand_line = ""\n',
                     ["Prompt.test_C3_handoff_now_injected_with_read_pct"]),
    "no_again": (HOOK, '            hand_line = T(owed.get("again_key") or owed["key"], params, lang)\n',
                 '            hand_line = ""\n', ["Prompt.test_C4_reminds_every_message_until_verify_passes"]),
    "canonical": (HOOK, "    return canonical_tree.canonical_path(str(Path(__file__).resolve()))\n", OLD_CANON,
                  ["Install.test_I1_canonical_open_source_git_layout"]),
    "no_tty_guard": ("src/cockpit/provider-launch.sh",
                     'if [ "$PROVIDER" = "claude" ] && [ -t 0 ] && [ -f "$HERE/../identity/auto_distill_hook.py" ]; then',
                     'if [ "$PROVIDER" = "claude" ] && [ -f "$HERE/../identity/auto_distill_hook.py" ]; then',
                     ["Install.test_I4a_launch_without_tty_does_not_install"]),
    "no_block": (HOOK, '    return json.dumps({"decision": "block", "reason": T("ctx_trigger.gate_block", {',
                 '    return "" and json.dumps({"decision": "block", "reason": T("ctx_trigger.gate_block", {',
                 ["StopGate.test_G1_unverified_blocks"]),
    "no_cap": (HOOK, '    if int(g.get("blocks") or 0) >= CTX_GATE_MAX:\n', '    if False:\n',
               ["StopGate.test_G3_cap_then_release_and_mark"]),
    "bypass_any": (HOOK, "    if human and BYPASS_WORD in prompt:", "    if BYPASS_WORD in prompt:",
                   ["StopGate.test_G6_dm_xianzuo_still_blocked"]),
    "no_stop_path": (HOOK, "    hand_text, hand_why = stop_handoff(code, rec, st, pct, runner, now)",
                     '    hand_text, hand_why = "", ""', ["Stop.test_S2_handoff_now_dm"]),
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
            self.assertRegex(out, r"FAIL: %s " % re.escape(c.split(".")[1]), "宣稱的格沒紅:%s\n%s" % (c, out[-1500:]))

    def test_K1_hardcode(self):
        self._knife("hardcode")

    def test_K2_no_85_inject(self):
        self._knife("no_85_inject")

    def test_K3_no_again(self):
        self._knife("no_again")

    def test_K4_canonical_revert(self):
        self._knife("canonical")

    def test_K5_no_tty_guard(self):
        self._knife("no_tty_guard")

    def test_K6_no_block(self):
        self._knife("no_block")

    def test_K7_no_cap(self):
        self._knife("no_cap")

    def test_K8_bypass_any(self):
        self._knife("bypass_any")

    def test_K9_no_stop_path(self):
        self._knife("no_stop_path")


if __name__ == "__main__":
    unittest.main()
