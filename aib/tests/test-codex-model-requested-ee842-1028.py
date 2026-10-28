#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Codex 席型號：使用者指定 > 預設、被拒照退版表退一版(10-28 @aeb40 工作單 A+B,#396 後續)。
全在 tmp:狀態根是 tmp(EZ2_AIB_STATE_ROOT)、模型清單是 tmp 裡的假 models_cache.json、
tmux / codex 一律注入假的，不讀真 ~/.codex、不開真 tmux。平台不假設 Mac。

A1 `aib seat re-register --model gpt-6.1-terra`(開席啟動指令自登記那一條)⇒ MODEL_REQUESTED 留 gpt-6.1-terra,
   不被「Codex 沒手指定就用清單最新」換成 gpt-6-luna(修前紅：正是工作單 A 的症狀)。
A2 沒帶 --model 的新開 Codex 席照舊用清單最新(修法沒把自動挑型號弄壞)。
A3 開席手指定 --model ⇒ 照指定。
B1 自動挑的型號被拒 ⇒ 照退版表連退(6.1-terra → 5.6-terra → 6-luna),每一步都記錄。
B2 一直被拒 ⇒ 最多退 MAX_STEPS 次就停(與 provider-launch 的 -lt 4 同一個上限)。
B3 手指定型號(auto=False)被拒也不退版。
B4 record:席位檔 model_requested 改成實際那一版、jsonl 留一筆;席位檔不在就不建。
B5 換帳號頁登入後(launch_after_login)被拒會退版、席位檔跟著寫(修前紅：以前直接 exec、沒退版)。
B6 手指定型號的席登入後照席位檔那一版開、不換成清單最新。
B7 provider-launch 啟動迴圈退版後也呼叫 record(席位檔跟著寫)。
刀:K1 拿掉 re-register 的 MODEL_FROM_RECORD ⇒ A1 紅;K2 launch_with_fallback 不看 MAX_STEPS ⇒ B2 紅;
   K3 recover 改回直接 exec ⇒ B5 紅。
"""
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
COCKPIT = HERE.parent / "src" / "cockpit"
sys.path.insert(0, str(COCKPIT))

import codex_account_recover as R  # noqa: E402
import codex_model_fallback as FB  # noqa: E402

LISTED = [
    {"slug": "gpt-6.1-sol", "visibility": "list"},
    {"slug": "gpt-6.1-terra", "visibility": "list"},
    {"slug": "gpt-6-luna", "visibility": "list"},
    {"slug": "gpt-5.6-terra", "visibility": "list"},
    {"slug": "gpt-5.6-luna", "visibility": "list"},
]
REJECT = "error: The 'gpt-x' model is not supported when using Codex with a ChatGPT account."


def _cache(tmp, models):
    path = pathlib.Path(tmp) / "models_cache.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    return str(path)


def _segment(src, start, end):
    i = src.index(start)
    j = src.index(end, i) + len(end)
    return src[i:j]


class _Done:
    def __init__(self, rc=0, stdout=""):
        self.returncode, self.stdout = rc, stdout


class ModelResolutionInAib(unittest.TestCase):
    """抽 aib 真碼(re-register 前置解析 + 型號決定那兩段)出來跑，不開 tmux。"""

    @classmethod
    def setUpClass(cls):
        src = (COCKPIT / "aib").read_text(encoding="utf-8")
        cls.rereg = _segment(src, 'if [ "$AIB_SEAT_REREGISTER" = "1" ]; then\n  REREG_WID=""',
                             '  NEW=0; AGAIN=0; INSTANCE=""\nfi\n')
        cls.model = _segment(src, "MODEL_FROM_REVIVE=0\n",
                             'die "aib: requested model is missing or invalid" 2\n')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        fake = self.tmp / "cockpit"
        fake.mkdir()
        (fake / "codex_pack.py").write_text("def enabled():\n    return True\n", encoding="utf-8")
        (fake / "codex_luna_model.py").write_text(
            "def latest_default_slug():\n    return 'gpt-6-luna'\n", encoding="utf-8")
        (fake / "providers.json").write_text(json.dumps({"providers": [
            {"id": "codex", "status": "supported", "default_requested_model": "gpt-6.1-terra"}]}),
            encoding="utf-8")
        self.fake = fake

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *, rereg_args=None, flag_model=""):
        prelude = "\n".join([
            'die() { printf "%s\\n" "$1" >&2; exit "${2:-1}"; }',
            't() { printf "%s\\n" "$*"; }',
            'T_PY3=%s' % sys.executable,
            'HERE=%s' % self.fake,
            'PROVIDER=codex',
            'MODEL_REQUESTED=%s' % (flag_model or '""'),
            'MODEL_FROM_FLAG=%s' % ("1" if flag_model else "0"),
            'MODEL_FROM_RECORD=0',
        ])
        body = ""
        if rereg_args is not None:
            prelude += "\nAIB_SEAT_REREGISTER=1\nREREG_ARGS=(%s)\n" % " ".join(rereg_args)
            body += self.rereg
        else:
            prelude += "\nAIB_SEAT_REREGISTER=0\n"
        body += self.model + 'printf "MODEL=%s\\n" "$MODEL_REQUESTED"\n'
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "EZ2_AIB_STATE_ROOT": str(self.tmp / "state"),
               "EZ2_AIHUB_SOCKET": "aihub-t", "EZ2_AIB_INSTANCE": "t"}
        out = subprocess.run(["bash", "-c", prelude + "\n" + body], capture_output=True,
                             text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return re.search(r"^MODEL=(.*)$", out.stdout, re.M).group(1)

    def test_a1_reregister_keeps_recorded_model(self):
        got = self._run(rereg_args=["@7", "--provider", "codex", "--mode", "dgrs", "--role", "dev",
                                    "--cwd", "/x", "--model", "gpt-6.1-terra", "--session-uuid", "u"])
        self.assertEqual(got, "gpt-6.1-terra", "re-register 帶來的型號不能被清單最新蓋掉")

    def test_a2_fresh_auto_open_uses_latest(self):
        self.assertEqual(self._run(), "gpt-6-luna")

    def test_a3_explicit_flag_kept(self):
        self.assertEqual(self._run(flag_model="gpt-6.1-terra"), "gpt-6.1-terra")


class FallbackLoop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.cache = _cache(self.tmp, LISTED)

    def tearDown(self):
        self._tmp.cleanup()

    def _loop(self, rejected, start, *, auto=True, cache=None):
        ran, recorded = [], []

        def run(argv, **_kw):
            ran.append(argv[-1])
            return _Done(1 if argv[-1] in rejected else 0)

        def capture():
            return REJECT if ran[-1] in rejected else "ok"

        def rec(old, new, **_kw):
            recorded.append((old, new))
            return {}
        rc, used, steps = FB.launch_with_fallback(
            lambda m: ["codex", "--model", m], start, {}, auto=auto, run=run, capture=capture,
            record_fn=rec, cache_path=cache or self.cache, err=io.StringIO())
        return rc, used, steps, ran, recorded

    def test_b1_steps_down_and_records(self):
        rc, used, steps, ran, recorded = self._loop({"gpt-6.1-terra", "gpt-5.6-terra"}, "gpt-6.1-terra")
        self.assertEqual((rc, used, steps), (0, "gpt-6-luna", 2))
        self.assertEqual(ran, ["gpt-6.1-terra", "gpt-5.6-terra", "gpt-6-luna"])
        self.assertEqual(recorded, [("gpt-6.1-terra", "gpt-5.6-terra"), ("gpt-5.6-terra", "gpt-6-luna")])

    def test_b2_capped(self):
        many = [{"slug": "gpt-%d-terra" % v, "visibility": "list"} for v in range(9, 0, -1)]
        cache = _cache(self.tmp, many)
        rejected = {m["slug"] for m in many}
        rc, used, steps, ran, _rec = self._loop(rejected, "gpt-9-terra", cache=cache)
        self.assertEqual(steps, FB.MAX_STEPS)
        self.assertEqual(len(ran), FB.MAX_STEPS + 1)
        self.assertEqual(used, "gpt-5-terra")
        self.assertEqual(rc, 1)

    def test_b3_explicit_no_fallback(self):
        rc, used, steps, ran, recorded = self._loop({"gpt-6.1-terra"}, "gpt-6.1-terra", auto=False)
        self.assertEqual((rc, used, steps, ran, recorded), (1, "gpt-6.1-terra", 0, ["gpt-6.1-terra"], []))


class RecordAndRecover(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.seats = self.state / "instances" / "t" / "seats"
        self.seats.mkdir(parents=True)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIHUB_SOCKET": "aihub-t",
                    "TMUX_PANE": "%1", "AIB_TMUX_BIN": "faketmux", "CODEX_BIN": str(self.tmp / "no-codex"),
                    "EZ2_CODEX_MODELS_CACHE": _cache(self.tmp, LISTED), "PATH": os.environ.get("PATH", "")}
        self._old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self._tmp.cleanup()

    def _seat(self, model):
        path = self.seats / "w7.json"
        path.write_text(json.dumps({"seat_id": "abcde", "provider": "codex", "model_requested": model}),
                        encoding="utf-8")
        return path

    def _log(self):
        path = self.state / FB.LOG_NAME
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_b4_record_writes_seat_and_log(self):
        seat = self._seat("gpt-6.1-terra")
        row = FB.record("gpt-6.1-terra", "gpt-5.6-terra", window="@7", environ=self.env, now=1)
        self.assertTrue(row["seat_written"])
        self.assertEqual(json.loads(seat.read_text())["model_requested"], "gpt-5.6-terra")
        self.assertEqual(json.loads(seat.read_text())["seat_id"], "abcde")
        self.assertEqual(self._log()[-1]["to"], "gpt-5.6-terra")
        row = FB.record("a", "b", window="@9", environ=self.env)
        self.assertFalse(row["seat_written"])
        self.assertFalse((self.seats / "w9.json").exists(), "席位檔不在就不建")
        with self.assertRaises(ValueError):
            FB.record("a", "bad model;", window="@7", environ=self.env)

    def _fake_run(self, rejected, ran):
        def run(argv, **kw):
            if argv[0] == "faketmux":
                return _Done(0, "@7\n" if "display-message" in argv else "")
            model = argv[-1]
            ran.append(model)
            if model in rejected:
                kw["stdout"].write(REJECT)
                return _Done(1)
            return _Done(0)
        return run

    def test_b5_recover_login_falls_back(self):
        seat = self._seat("gpt-6.1-terra")
        env = dict(self.env, AIB_CODEX_LUNA_ERRFILE=str(self.tmp / "err.txt"))
        os.environ.update(env)
        ran = []
        rc = R.launch_after_login(str(self.tmp / "slot"), environ=env,
                                  run=self._fake_run({"gpt-6.1-terra"}, ran))
        self.assertEqual(rc, 0)
        self.assertEqual(ran, ["gpt-6.1-terra", "gpt-5.6-terra"])
        self.assertEqual(json.loads(seat.read_text())["model_requested"], "gpt-5.6-terra")
        self.assertEqual([(r["from"], r["to"]) for r in self._log()], [("gpt-6.1-terra", "gpt-5.6-terra")])

    def test_b6_recover_explicit_uses_seat_model(self):
        seat = self._seat("gpt-5.6-terra")
        env = dict(self.env, AIB_CODEX_LUNA_ERRFILE=str(self.tmp / "err.txt"), AIB_CODEX_MODEL_EXPLICIT="1")
        os.environ.update(env)
        ran = []
        rc = R.launch_after_login(str(self.tmp / "slot"), environ=env,
                                  run=self._fake_run({"gpt-5.6-terra"}, ran))
        self.assertEqual((rc, ran), (1, ["gpt-5.6-terra"]))
        self.assertEqual(json.loads(seat.read_text())["model_requested"], "gpt-5.6-terra")
        self.assertEqual(self._log(), [])

    def test_b7_provider_launch_records_fallback(self):
        src = (COCKPIT / "provider-launch.sh").read_text(encoding="utf-8")
        i = src.index('if [ -n "$_older" ]; then')
        block = src[i:src.index("continue", i)]
        self.assertIn('codex_model_fallback.py" record "$REQUESTED_MODEL" "$_older"', block)
        self.assertLess(block.index("codex_model_fallback.py"), block.index('REQUESTED_MODEL="$_older"'),
                        "要在覆寫 REQUESTED_MODEL 之前記,不然記到的舊值是新值")


if __name__ == "__main__":
    unittest.main()
