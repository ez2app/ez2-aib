#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib test 統一跑測試入口(docs/DESIGN-aib-test-entry-shadow-0929.md 第七節第 1、2、3、5 格)。

the maintainer 09-29:「跑測試時 1 啟用暗影預載、2 確認它正式啟用,除非 1 沒啟用才用原來的」。

入口格(test_c*):子行程打產品頂 tools/verify/test_entry.py(或 src/cockpit/aib),跑在暫存治具倉:
    治具倉 = 本樹的 tools/verify、tools/governance、src/cockpit/i18n_lib.py、i18n/ 複本 + git init,
    兩支考卷 tests/test-ok.py(讀 data.txt,綠)、tests/test-red.py(紅)。沿用池 = 每格自己的暫存目錄。
  c1  自檢故意失敗(治具拿掉 tools/verify/sitecustomize.py)⇒ 退回全跑、第一行印原因、紅的照樣報紅、不留紀錄。
  c2  自檢通過 ⇒ 絕不退回:第一行「已啟用且確認運作」、實跑留下紀錄;第二次沿用;--fresh 照樣實跑且留紀錄。
  c3  宣稱啟用卻沒寫進紀錄(沿用池位置是一個普通檔,寫不進去)⇒ 第一行判失敗、rc=3,不是通過。
  c5a `aib verify 關閉` / `off` 被拒:rc=2、說明已停用、設定檔不寫。
  c5b 舊設定檔寫著 off ⇒ 讀成快驗、狀態頁說出來、is_off 為假;入口照樣啟用並留紀錄。
  c5c AIB_SEAT_TEST_REUSE=0(API 席舊開關)⇒ 關不掉:seat_test_reuse 照樣留紀錄;入口照樣啟用並說一句。
  c6  逾時接得回來(a seat 09-29 12:35):package_trial 給 5 秒,睡 20 秒的考卷判 timeout;
      aib test --timeout 5 同一支判紅 rc=124,輸出有「測試超過 5 秒上限」。
  c8  每支顯示時間之前先講影子續跑(the maintainer 10-02):開頭一行「已載入並啟用」或「沒載入」;每支先說這次能不能沿用
      ——全新倉庫第一次「不能沿用(還沒有任何紀錄)⇒ 實跑」(不能說成紀錄池壞了)、沒改的第二次「影子續跑可用 ⇒ 沿用」;自檢沒過「沒載入」。
  c7  10% 示警(the maintainer 10-02 常設令):第一次沒前例 ⇒ 說「沒有前例」;同一支第二次慢到平常 10% 以上(且多 1 秒)
      ⇒ 跑的當下 stderr 就示警、結果列標慢、總帳「慢超過 10%:1 支」;第三次回到平常 ⇒ 不示警、0 支。
  c9  紀錄池夾進合法 JSON 但不是物件的列(null / [] / 1)⇒ 整批仍跑完、不 AttributeError。
  c10 基準是 0 秒、這次實跑超過 1 秒 ⇒ 結果列標慢但百分比印「?」、合計與慢帳兩行都印得出來。
  c11 自檢沒過退回全跑 ⇒ 結尾不印「沒有前例可比」(那條路沒讀基準,不能把每支都數進去)。
  c12 不能沿用的原因:字典有 lock-fp-changed 印白話;字典沒有的代碼原樣顯示,畫面不得出現 !!鍵!!。

對照臂(test_k0):同一份複本不落刀,十七格必須全綠(刀的紅才算是刀造成的)。
還原刀(test_k*):複本樹 → 產品檔恰好一次替換 → 在複本跑宣稱的入口格 → 那格必須在 FAIL:/ERROR: 紅集合。
刀與入口格逐格內聯(到達性閘看不見 helper)。每把刀改回的都是「舊碼的樣子」:
  k1 拿掉自檢(`if not gate.get("ok")` → `if False`)⇒ c1 紅
  k2 永遠退回(同一行 → `if True`)⇒ c2 紅
  k3 紀錄數改成相信實跑數(舊 runner 的算法)⇒ c3 紅
  k5a off/關閉 重新接回 off(別名與模式清單兩層一起,就是舊碼)⇒ c5a 紅
  k5b 拿掉舊 off 讀成快驗那兩行(舊 off 落進「讀不懂」=不寫收據)⇒ c5b 紅
  k5c enabled() 改回讀 AIB_SEAT_TEST_REUSE ⇒ c5c 紅
  k6  package_trial 不把 timeout 傳給入口(第一版的樣子)⇒ c6 紅
  k8  拿掉開頭那行「已載入並啟用」(= 舊碼:要等整批跑完才知道)⇒ c8 紅
  k7  10% 門檻改成永遠到不了(= 沒有示警的舊碼)⇒ c7 紅
  k9  拿掉「不是物件的列跳過」(= 舊 baselines 只吞壞 JSON)⇒ c9 紅
  k10 標慢那行改回直接 elapsed/baseline(= 基準 0 秒會除以零)⇒ c10 紅
  k11 結尾永遠印慢帳(= 退回全跑也把每支數成沒有前例)⇒ c11 紅
  k12 缺鍵判斷改回只比 txt==key(= i18n 缺鍵回 !!鍵!! 時原樣代碼退路失效)⇒ c12 紅

不在本檔(本包沒做):第七節第 4 格(擋直接跑測試的鉤子)、第 6 格(產品起測試的名冊絆線)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve()
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "src" / "cockpit"))
import aib_cabin_wash  # noqa: E402
import resume_policy as rp  # noqa: E402
import seat_test_reuse as seat  # noqa: E402

ENTRY_REL = "tools/verify/test_entry.py"
AIB = REPO / "src" / "cockpit" / "aib"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
OK_TEST = '''import pathlib, unittest
class T(unittest.TestCase):
    def test_reads_data(self):
        self.assertEqual((pathlib.Path(__file__).resolve().parents[1] / "data.txt").read_text(), "ok\\n")
if __name__ == "__main__":
    unittest.main()
'''
SLOW_TEST = '''import time, unittest
class T(unittest.TestCase):
    def test_slow(self):
        time.sleep(20)
if __name__ == "__main__":
    unittest.main()
'''
RED_TEST = '''import unittest
class T(unittest.TestCase):
    def test_red(self):
        self.assertEqual(1, 2)
if __name__ == "__main__":
    unittest.main()
'''
PACE_TEST = '''import pathlib, time, unittest
class T(unittest.TestCase):
    def test_pace(self):
        time.sleep(float((pathlib.Path(__file__).resolve().parents[1] / "pace.txt").read_text()))
if __name__ == "__main__":
    unittest.main()
'''
ENABLED_0_1 = "影子續跑:已啟用且確認運作(沿用 0 / 實跑 1,新紀錄 1 列)"
ENABLED_1_0 = "影子續跑:已啟用且確認運作(沿用 1 / 實跑 0,新紀錄 0 列)"
SHORT_1_0 = "影子續跑:宣稱啟用但實跑 1 支只留下 0 列紀錄(0<1)—— 失敗"


def _fixture(td: Path, *, tracer=True) -> Path:
    fx = td / "fx"
    for sub in ("tools/verify", "tools/governance"):
        shutil.copytree(REPO / sub, fx / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (fx / "src" / "cockpit").mkdir(parents=True)
    shutil.copy2(REPO / "src" / "cockpit" / "i18n_lib.py", fx / "src" / "cockpit" / "i18n_lib.py")
    (fx / "i18n").mkdir()
    for f in (REPO / "i18n").glob("*.json"):
        shutil.copy2(f, fx / "i18n" / f.name)
    (fx / "tests").mkdir()
    (fx / "tests" / "test-ok.py").write_text(OK_TEST, encoding="utf-8")
    (fx / "tests" / "test-red.py").write_text(RED_TEST, encoding="utf-8")
    (fx / "tests" / "test-slow.py").write_text(SLOW_TEST, encoding="utf-8")
    (fx / "tests" / "test-pace.py").write_text(PACE_TEST, encoding="utf-8")
    (fx / "pace.txt").write_text("0.2", encoding="utf-8")
    (fx / "tools" / "collab").mkdir()
    shutil.copy2(REPO / "tools" / "collab" / "package_trial.py", fx / "tools" / "collab" / "package_trial.py")
    (fx / "data.txt").write_text("ok\n", encoding="utf-8")
    if not tracer:
        (fx / "tools" / "verify" / "sitecustomize.py").unlink()
    genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(td), **GIT_ENV}
    for argv in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "fx"]):
        subprocess.run(argv, cwd=str(fx), env=genv, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return fx


def _env(td: Path, **extra) -> dict:
    env = dict(os.environ)
    aib_cabin_wash.wash_cabin_env(env, str(REPO))
    for k in ("AIB_SEAT_TEST_REUSE", "AIB_WITNESS_SHADOW", "AIB_SEAT_TEST_STORE", "EZ2_VERIFY_TRACE",
              "EZ2_VERIFY_SHADOW_DIR", "PYTHONPATH", "BASH_ENV"):
        env.pop(k, None)
    home = td / "home"
    home.mkdir(exist_ok=True)
    env.update({"HOME": str(home), "EZ2_AIB_LOCALE": "zh-TW", "EZ2_VERIFY_SHADOW_DIR": str(td / "shadow"),
                "PYTHONDONTWRITEBYTECODE": "1", **GIT_ENV})
    env.update(extra)
    return env


def _rows(shadow: Path) -> list:
    out = []
    if shadow.is_dir():
        for f in sorted(shadow.glob("*.rows.jsonl")):
            out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


class EntryCells(unittest.TestCase):
    def test_c1_gate_fails_falls_back_and_says_why(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td, tracer=False)
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py", "tests/test-red.py"],
                               cwd=str(fx), env=_env(td), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            lines = p.stdout.splitlines()
            self.assertTrue(lines, p.stderr[-2000:])
            self.assertTrue(lines[0].startswith("影子續跑:未啟用 —— setup:"), lines[0])
            self.assertIn("sitecustomize.py missing", lines[0], "第一行要說出自檢哪裡沒過")
            self.assertTrue(lines[0].endswith(";本輪全部實跑"), lines[0])
            self.assertEqual(p.returncode, 1, "退回全跑時紅的考卷仍要報紅:" + p.stdout + p.stderr[-2000:])
            self.assertIn("· tests/test-ok.py(實跑 rc=0", p.stdout)
            self.assertIn("· tests/test-red.py(實跑 rc=1", p.stdout)
            self.assertIn("這批沒拿機器租約,別席同時跑會互相干擾", p.stdout)
            self.assertEqual(_rows(td / "shadow"), [], "沒啟用就不該假裝留了紀錄")

    def test_c2_gate_passes_never_falls_back(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            first = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx),
                                   env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            self.assertEqual(first.stdout.splitlines()[0], ENABLED_0_1, first.stdout + first.stderr[-2000:])
            rows = [r for r in _rows(td / "shadow") if r.get("test") == "tests/test-ok.py"]
            self.assertEqual(len(rows), 1, "實跑一支就要在沿用池留一列")
            self.assertIs(rows[0].get("measured"), True, rows[0].get("why"))
            self.assertEqual(rows[0].get("source"), "aib-test", "入口寫的列要標自己的來源")
            second = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx),
                                    env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr[-2000:])
            self.assertEqual(second.stdout.splitlines()[0], ENABLED_1_0, "什麼都沒改的第二次要沿用")
            fresh = subprocess.run([sys.executable, str(fx / ENTRY_REL), "--fresh", "tests/test-ok.py"],
                                   cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr[-2000:])
            self.assertEqual(fresh.stdout.splitlines()[0], ENABLED_0_1, "fresh 仍開著影子續跑、照樣留紀錄")
            self.assertEqual(len([r for r in _rows(td / "shadow") if r.get("test") == "tests/test-ok.py"]), 2)

    def test_c3_claimed_on_without_rows_is_a_failure(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            blocker = td / "not-a-folder"
            blocker.write_text("", encoding="utf-8")
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx),
                               env=_env(td, EZ2_VERIFY_SHADOW_DIR=str(blocker)), capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.stdout.splitlines()[:1], [SHORT_1_0], p.stdout + p.stderr[-2000:])
            self.assertEqual(p.returncode, 3, "考卷綠但紀錄短少,這一輪不算通過")

    def test_c5a_verify_off_command_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            cfg = rp.config_path(fx)
            for word in ("關閉", "off"):
                p = subprocess.run([str(AIB), "verify", word], cwd=str(fx), env=env, capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL, timeout=60)
                self.assertEqual(p.returncode, 2, word + p.stdout + p.stderr)
                self.assertIn("「關閉」已停用", p.stderr, word)
                self.assertFalse(cfg.exists(), "被拒的指令不得寫設定檔")

    def test_c5b_legacy_off_file_reads_as_quick_and_still_records(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            cfg = rp.config_path(fx)
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps({"schema": "aib-verify-mode/v1", "mode": "off"}), encoding="utf-8")
            self.assertEqual(rp.current_mode(fx)[:2], ("quick", "legacy_off"))
            self.assertFalse(rp.is_off(fx), "舊 off 不得變成「不寫收據」")
            s = subprocess.run([str(AIB), "verify", "status"], cwd=str(fx), env=env, capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
            self.assertIn("驗證模式:快驗", s.stdout)
            self.assertIn("寫的是已停用的「關閉」,現在以快驗執行", s.stdout)
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-2000:])
            self.assertEqual(p.stdout.splitlines()[0], ENABLED_0_1)
            self.assertEqual(len([r for r in _rows(td / "shadow") if r.get("test") == "tests/test-ok.py"]), 1)

    def test_c5c_seat_switch_zero_no_longer_turns_off(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            store = td / "seat-store"
            saved = {k: os.environ.get(k) for k in ("AIB_SEAT_TEST_REUSE", "AIB_SEAT_TEST_STORE")}
            os.environ.update({"AIB_SEAT_TEST_REUSE": "0", "AIB_SEAT_TEST_STORE": str(store)})
            try:
                self.assertTrue(seat.enabled(), "AIB_SEAT_TEST_REUSE=0 不得再關掉")
                rc, _out = seat.run(fx, "tests/test-ok.py", environ=_env(td, AIB_SEAT_TEST_REUSE="0"))
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(rc, 0)
            self.assertEqual([r.get("test") for r in _rows(store)], ["tests/test-ok.py"], "設 0 也要照樣留紀錄")
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx),
                               env=_env(td, AIB_SEAT_TEST_REUSE="0", AIB_WITNESS_SHADOW="0"),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-2000:])
            self.assertEqual(p.stdout.splitlines()[0], ENABLED_0_1)
            self.assertIn("AIB_SEAT_TEST_REUSE=0 已不能關閉影子續跑", p.stderr)
            self.assertIn("AIB_WITNESS_SHADOW=0 已不能關閉影子續跑", p.stderr)


    def test_c6_timeout_reaches_the_test(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            trial = subprocess.run([sys.executable, "-c",
                                    "import sys; sys.path.insert(0, 'tools/collab'); import package_trial as P; "
                                    "print(P.run_test(sys.argv[1], 'tests/test-slow.py', 5))", str(fx)],
                                   cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(trial.returncode, 0, trial.stderr[-2000:])
            self.assertEqual(trial.stdout.strip().splitlines()[-1:], ["timeout"], "package_trial --timeout 5 要判逾時")
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "--timeout", "5", "tests/test-slow.py"],
                               cwd=str(fx), env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr[-2000:])
            self.assertIn("· tests/test-slow.py(實跑 rc=124", p.stdout)
            self.assertIn("測試超過 5 秒上限", p.stderr)



    def test_c8_each_test_says_if_shadow_resume_is_loaded_and_usable_before_its_time(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            err = first.stderr.splitlines()
            self.assertEqual(err[0], "影子續跑:已載入並啟用(自檢通過)", "開頭先講有沒有載入、有沒有啟用")
            self.assertIn("▶ tests/test-ok.py:影子續跑這次不能沿用(這個倉庫還沒有任何紀錄,第一次跑)⇒ 實跑", first.stderr,
                          "全新的倉庫沒有紀錄 ≠ 紀錄池壞了")
            second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr[-2000:])
            self.assertIn("✓ tests/test-ok.py:影子續跑可用 ⇒ 沿用上次綠燈,這次不跑", second.stderr)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td, tracer=False)
            env = _env(td)
            off = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx), env=env,
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertTrue(off.stderr.startswith("影子續跑:沒載入"), off.stderr[-2000:])
            self.assertIn("▶ tests/test-ok.py:影子續跑沒載入 ⇒ 一般實跑", off.stderr)

    def test_c7_over_10pct_of_usual_warns_while_running(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "--fresh", "tests/test-pace.py"]
            run = lambda: subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                         stdin=subprocess.DEVNULL, timeout=300)
            first = run()
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            self.assertIn("沒有前例,這次起開始記", first.stderr)
            self.assertIn("比平常慢超過 10%:0 支;沒有前例可比:1 支", first.stdout)
            (fx / "pace.txt").write_text("4.0", encoding="utf-8")   # 即時計時器另補準備時間(預設 1.5 秒)⇒ 要明顯超過
            slow = run()
            self.assertEqual(slow.returncode, 0, slow.stdout + slow.stderr[-2000:])
            self.assertRegex(slow.stderr, r"▶ tests/test-pace\.py:影子續跑這次不能沿用.*⇒ 實跑;平常 \d+\.\d 秒")
            self.assertIn("⚠ tests/test-pace.py 已超過平常 10%", slow.stderr, "跑的當下就要示警")
            self.assertRegex(slow.stdout, r"test-pace\.py\(實跑 rc=0,[0-9.]+ 秒\) ⚠ 比平常 \d+\.\d 秒慢 \d+%")
            self.assertIn("比平常慢超過 10%:1 支;沒有前例可比:0 支", slow.stdout)
            (fx / "pace.txt").write_text("0.2", encoding="utf-8")
            back = run()
            self.assertEqual(back.returncode, 0, back.stdout + back.stderr[-2000:])
            self.assertNotIn("⚠", back.stderr + back.stdout, "回到平常不能示警(平常是最近幾次的平均,含剛才慢的那次)")
            self.assertIn("比平常慢超過 10%:0 支", back.stdout)

    def test_c9_non_object_pool_rows_do_not_crash(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "--fresh", "tests/test-ok.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            pools = list((td / "shadow").glob("*.rows.jsonl"))
            self.assertTrue(pools, "實跑後要留下紀錄池才能塞壞列")
            for pf in pools:
                with pf.open("a", encoding="utf-8") as fh:
                    fh.write("null\n[]\n1\n")
            second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr[-2000:])
            self.assertNotIn("AttributeError", second.stderr + second.stdout)
            self.assertIn("合計 1 支", second.stdout)

    def test_c10_zero_baseline_prints_row_and_summary(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "--fresh", "tests/test-pace.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            pools = list((td / "shadow").glob("*.rows.jsonl"))
            self.assertTrue(pools, "要先有一筆綠燈才能把基準改成 0 秒")
            for pf in pools:
                out = []
                for ln in pf.read_text(encoding="utf-8").splitlines():
                    if not ln.strip():
                        continue
                    r = json.loads(ln)
                    if isinstance(r, dict) and r.get("test") == "tests/test-pace.py":
                        r["duration_s"] = 0
                    out.append(json.dumps(r, ensure_ascii=False))
                pf.write_text("\n".join(out) + "\n", encoding="utf-8")
            (fx / "pace.txt").write_text("2.0", encoding="utf-8")
            slow = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(slow.returncode, 0, slow.stdout + slow.stderr[-2000:])
            self.assertRegex(slow.stdout, r"test-pace\.py\(實跑 rc=0,[0-9.]+ 秒\) ⚠ 比平常 0\.0 秒慢 \?%")
            self.assertIn("合計 1 支", slow.stdout)
            self.assertIn("比平常慢超過 10%:1 支;沒有前例可比:0 支", slow.stdout)

    def test_c11_fallback_does_not_count_nobase(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td, tracer=False)
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py", "tests/test-red.py"],
                               cwd=str(fx), env=_env(td), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr[-2000:])
            self.assertTrue(p.stdout.splitlines()[0].startswith("影子續跑:未啟用"), p.stdout)
            self.assertIn("合計 2 支", p.stdout)
            self.assertNotIn("沒有前例可比", p.stdout, "退回全跑沒讀基準,結尾不得把每支數成沒有前例")

    def test_c12_why_text_missing_key_shows_code(self):
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, 'tools/verify'); import test_entry as te; "
                 "print('LOCK=' + te._why_text('lock-fp-changed', 'x')); "
                 "print('UNK=' + te._why_text('not-a-real-why-code'))"],
                cwd=str(fx), env=_env(td), capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr[-2000:])
            lines = dict(ln.split("=", 1) for ln in probe.stdout.splitlines() if "=" in ln)
            self.assertEqual(lines.get("LOCK"), "鎖指紋變了:x", probe.stdout + probe.stderr[-2000:])
            self.assertNotIn("!!", lines.get("LOCK", ""), "字典有鍵時畫面不得出現 !!鍵!!")
            self.assertEqual(lines.get("UNK"), "not-a-real-why-code",
                             "字典沒有的代碼要原樣顯示,不能留下 !!aib.test.why…!!:" + probe.stdout)

    def test_c13_each_real_run_says_green_or_red_the_moment_it_ends(self):
        # 10-04 the maintainer「為什麼不能一紅就知」:紅的那支一結束就在 stderr 說紅,而且在下一支開跑之前(不等總表)。
        # (不寫 docstring:k0 數 -v 輸出裡同一行的「... ok」)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-red.py", "tests/test-ok.py"],
                               cwd=str(fx), env=_env(td), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr[-2000:])
            err = p.stderr
            red_at = err.find("✗ 紅 tests/test-red.py(rc=1")
            ok_start = err.find("▶ tests/test-ok.py")
            green_at = err.find("綠 tests/test-ok.py(rc=0")
            self.assertGreaterEqual(red_at, 0, "紅的那支結束要即時說紅:" + err[-2000:])
            self.assertGreaterEqual(ok_start, 0, err[-2000:])
            self.assertLess(red_at, ok_start, "紅要在下一支開跑之前說,不是等整批跑完:" + err[-2000:])
            self.assertGreater(green_at, ok_start, "綠的那支結束也要說:" + err[-2000:])


    def _resume_twice(self, between=None):
        # 10-04 the maintainer「把可接續做成程式化」:同一批名跑兩次(都 --fresh,影子沿用關掉,只剩批次日誌在接續)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "--fresh", "--resume", "b1", "tests/test-red.py", "tests/test-ok.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            if between:
                between(td, fx)
            second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=300)
            return first, second

    def test_c14_resume_skips_green_reruns_red(self):
        first, second = self._resume_twice()
        self.assertNotEqual(first.returncode, 0, first.stderr[-2000:])
        self.assertIn("↻ tests/test-ok.py:接續批次 b1", second.stderr, "上次綠的不重跑:" + second.stderr[-2000:])
        self.assertIn("▶ tests/test-red.py", second.stderr, "上次紅的一定重跑:" + second.stderr[-2000:])
        self.assertNotEqual(second.returncode, 0, "紅照樣紅,接續不洗綠")

    def test_c15_bad_journal_line_reruns_everything(self):
        def corrupt(td, fx):
            j = td / "shadow" / "batch-b1.jsonl"
            with open(j, "a", encoding="utf-8") as f:
                f.write("not json\n")
        first, second = self._resume_twice(corrupt)
        self.assertIn("日誌不能用(bad-line", second.stderr, second.stderr[-2000:])
        self.assertIn("▶ tests/test-ok.py", second.stderr, "壞一行整份不用,綠的也重跑:" + second.stderr[-2000:])

    def test_c16_tree_change_does_not_resume(self):
        def commit(td, fx):
            (fx / "data.txt").write_text("changed\n", encoding="utf-8")
            genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(td), **GIT_ENV}
            for a in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "x"]):
                subprocess.run(a, cwd=str(fx), env=genv, check=True, capture_output=True, stdin=subprocess.DEVNULL)
        first, second = self._resume_twice(commit)
        self.assertNotIn("↻ tests/test-ok.py", second.stderr, "樹變了不接續:" + second.stderr[-2000:])
        self.assertIn("▶ tests/test-ok.py", second.stderr, second.stderr[-2000:])

    def test_c17_test_that_leaves_tracked_file_changed_is_red_and_restored(self):
        # 10-04 雲端實踩:自刀改寫真樹、中途被打斷沒還原 ⇒ 後面的測試全在壞樹上誤紅。
        # 測試自己 rc=0 也要判紅、說出哪個檔,並當場還原,下一支照樣綠。
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-dirty.py").write_text(
                "from pathlib import Path\n"
                "Path(__file__).resolve().parent.parent.joinpath('data.txt').write_text('dirty\\n')\n",
                encoding="utf-8")
            p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "--fresh", "tests/test-dirty.py", "tests/test-ok.py"],
                               cwd=str(fx), env=_env(td), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertNotEqual(p.returncode, 0, "改了真樹沒還原要判紅:" + p.stdout + p.stderr[-2000:])
            self.assertIn("⛔ tests/test-dirty.py", p.stderr, p.stderr[-2000:])
            self.assertIn("data.txt", p.stderr, p.stderr[-2000:])
            self.assertEqual((fx / "data.txt").read_text(encoding="utf-8"), "ok\n", "要當場還原")
            self.assertIn("綠 tests/test-ok.py(rc=0", p.stderr, "下一支不受影響:" + p.stderr[-2000:])

    def test_c18_tree_dirtying_test_is_never_reused_next_round(self):
        # 10-04 full4 第二輪實踩:執行器先把綠列寫進池,樹防線才判紅 ⇒ 下一輪沿用了那列綠(把紅藏成綠)。
        # 判紅時要作廢池裡那支的綠列:第二輪(不加 --fresh)必須重跑、照樣紅。
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-dirty.py").write_text(
                "from pathlib import Path\n"
                "p = Path(__file__).resolve().parent.parent.joinpath('data.txt')\n"
                "p.write_text(p.read_text() + 'x')\n",
                encoding="utf-8")
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-dirty.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=300)
            self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=300)
            self.assertIn("▶ tests/test-dirty.py", second.stderr, "改了真樹的那支下一輪不准沿用:" + second.stderr[-2000:])
            self.assertNotEqual(second.returncode, 0, "第二輪照樣紅:" + second.stdout + second.stderr[-2000:])

    def test_c19_shared_tree_change_is_not_blamed_not_restored_but_voided(self):
        # 10-04 full4 三份並行實踩:同一棵樹上別批的原地落刀改了檔,被算到剛跑完的這支頭上、還被還原(弄壞那支的刀)。
        # 同樹有別批活著 ⇒ 不判紅、不還原;但這支的綠不能當證據 ⇒ 下一輪必須重跑。
        import test_entry as TE
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-dirty.py").write_text(
                "from pathlib import Path\n"
                "p = Path(__file__).resolve().parent.parent.joinpath('data.txt')\n"
                "p.write_text(p.read_text() + 'x')\n",
                encoding="utf-8")
            other = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL)
            marker = TE._batch_dir(fx) / str(other.pid)
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(str(other.pid), encoding="utf-8")
                env = _env(td)
                argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-dirty.py"]
                first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL, timeout=300)
                self.assertEqual(first.returncode, 0, "同樹有別批 ⇒ 歸咎不了,不准判紅:" + first.stdout + first.stderr[-2000:])
                self.assertIn("⚠ tests/test-dirty.py", first.stderr, first.stderr[-2000:])
                self.assertEqual((fx / "data.txt").read_text(encoding="utf-8"), "ok\nx", "同樹有別批 ⇒ 不准還原(會弄壞別批)")
                second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=300)
                self.assertIn("▶ tests/test-dirty.py", second.stderr, "那次的綠不准沿用:" + second.stderr[-2000:])
            finally:
                other.kill()
                other.wait()
                try:
                    marker.unlink()
                except OSError:
                    pass

    def test_c20_kill_switch_runs_everything_and_still_records(self):
        # 10-04(the maintainer 同意):總開關 AIB_TEST_NO_REUSE=1 = 這一輪全部實跑,照樣追蹤、照樣留紀錄;關掉就恢復沿用
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            n1 = len(_rows(td / "shadow"))
            off = subprocess.run(argv, cwd=str(fx), env=dict(env, AIB_TEST_NO_REUSE="1"), capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=300)
            self.assertIn("▶ tests/test-ok.py", off.stderr, "總開關開著必須實跑:" + off.stderr[-2000:])
            self.assertIn("AIB_TEST_NO_REUSE=1", off.stderr + off.stdout)
            self.assertGreater(len(_rows(td / "shadow")), n1, "總開關開著照樣留紀錄")
            on = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertNotIn("▶ tests/test-ok.py", on.stderr, "關掉就恢復沿用:" + on.stderr[-2000:])

    def test_c21_always_run_marker_is_never_reused(self):
        # 10-04(the maintainer 同意):測試檔前 40 行有 `# aib: always-run` ⇒ 永遠實跑;同一批沒標的照常沿用(對照)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-mark.py").write_text("# aib: always-run\n" + OK_TEST, encoding="utf-8")
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-mark.py", "tests/test-ok.py"]
            first = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            second = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertIn("▶ tests/test-mark.py", second.stderr, "標了 always-run 必須實跑:" + second.stderr[-2000:])
            self.assertIn("always-run", second.stderr)
            self.assertNotIn("▶ tests/test-ok.py", second.stderr, "沒標的照常沿用(對照):" + second.stderr[-2000:])


    def test_c22_why_names_the_input_and_runs_nothing(self):
        # 10-04(the maintainer 同意,第 5 項):aib test --why 說出會不會沿用、卡在哪個輸入;不跑測試、不寫紀錄池
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            run = [sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py", "tests/test-red.py"]
            first = subprocess.run(run, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 1, first.stdout + first.stderr[-2000:])   # test-red 本來就紅
            n1 = len(_rows(td / "shadow"))
            why = [sys.executable, str(fx / ENTRY_REL), "--why", "tests/test-ok.py", "tests/test-red.py"]
            a = subprocess.run(why, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(a.returncode, 0, a.stdout + a.stderr[-2000:])
            self.assertIn("✓ tests/test-ok.py", a.stdout, a.stdout)
            self.assertIn("▶ tests/test-red.py", a.stdout, "紅的不沿用,要說出來:" + a.stdout)
            (fx / "data.txt").write_text("changed\n", encoding="utf-8")
            b = subprocess.run(why + ["--json"], cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            got = {e["test"]: e for e in json.loads(b.stdout)["why"]}
            self.assertEqual(got["tests/test-ok.py"]["verdict"], "must-run", b.stdout)
            self.assertEqual(got["tests/test-ok.py"]["why"], "input-fp-changed", b.stdout)
            self.assertIn("open:./data.txt", got["tests/test-ok.py"]["changed"], "要點名是哪個檔:" + b.stdout)
            self.assertEqual(got["tests/test-red.py"]["why"], "previous-red", b.stdout)
            self.assertNotIn("▶", a.stderr + b.stderr, "--why 不准實跑任何一支")
            self.assertEqual(len(_rows(td / "shadow")), n1, "--why 不准寫紀錄池")

    def _commit(self, fx, td):
        genv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(td), **GIT_ENV}
        for argv in (["git", "add", "-A"], ["git", "commit", "-q", "-m", "more"]):
            subprocess.run(argv, cwd=str(fx), env=genv, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    def _shift_rows(self, td, test, hours=None, drop=False):
        """把池裡某支測試的列的 time_utc 往回撥 hours 小時(drop=True:整欄拿掉),模擬「那是幾天前留的」。"""
        import datetime as dt
        for f in sorted((td / "shadow").glob("*.rows.jsonl")):
            out = []
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get("test") == test:
                        if drop:
                            r.pop("time_utc", None)
                        else:
                            t = dt.datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(hours=hours)
                            r["time_utc"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
                    line = json.dumps(r, ensure_ascii=False)
                out.append(line)
            f.write_text("\n".join(out) + "\n", encoding="utf-8")

    def _why(self, fx, env, *tests):
        p = subprocess.run([sys.executable, str(fx / ENTRY_REL), "--why", "--json", *tests], cwd=str(fx), env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-2000:])
        return {e["test"]: e for e in json.loads(p.stdout)["why"]}

    def test_c23_old_row_expires(self):
        # 10-04:列超過期限(第一段約 72 小時 ±10%)不沿用;沒有 UTC 時間的舊列也不沿用;60 小時照沿用(對照)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            first = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx), env=env,
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            # 10-04:期限每支固定 ±10% 錯開 ⇒ 第一段期限在 64.8~79.2 小時之間;60 小時一定沿用、85 小時一定過期
            self._shift_rows(td, "tests/test-ok.py", hours=60)
            self.assertEqual(self._why(fx, env, "tests/test-ok.py")["tests/test-ok.py"]["verdict"], "reuse")
            self._shift_rows(td, "tests/test-ok.py", hours=25)
            got = self._why(fx, env, "tests/test-ok.py")["tests/test-ok.py"]
            self.assertEqual((got["verdict"], got["why"]), ("must-run", "row-expired"), got)
            self.assertEqual(self._why(fx, dict(env, AIB_REUSE_MAX_AGE_H="9999"), "tests/test-ok.py")["tests/test-ok.py"]["why"],
                             "row-expired", "環境變數只能縮短期限,不能放寬")
            self._shift_rows(td, "tests/test-ok.py", drop=True)
            self.assertEqual(self._why(fx, env, "tests/test-ok.py")["tests/test-ok.py"]["why"], "row-age-unknown")
            again = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"], cwd=str(fx), env=env,
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertIn("▶ tests/test-ok.py", again.stderr, "過期就真的實跑:" + again.stderr[-2000:])

    def test_c24_date_in_read_file_crossed_reruns(self):
        # 10-04(E0.2 活例):測試讀的檔寫著昨天的日期,列是前天留的 ⇒ 中間跨過那一天 ⇒ 必跑;
        #   寫著十天前日期的檔(早就跨過、這段期間沒跨)⇒ 照沿用(對照,不是見日期就重跑)
        import datetime as dt
        today = dt.datetime.now(dt.timezone.utc).date()
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "due.json").write_text('{"expires_at": "%s"}\n' % (today - dt.timedelta(days=1)).isoformat(), encoding="utf-8")
            (fx / "old.json").write_text('{"expires_at": "%s"}\n' % (today - dt.timedelta(days=10)).isoformat(), encoding="utf-8")
            for name, f in (("test-due.py", "due.json"), ("test-old.py", "old.json")):
                (fx / "tests" / name).write_text(OK_TEST.replace('"data.txt").read_text(), "ok\\n"',
                                                                 '"%s").read_text()[:1], "{"' % f), encoding="utf-8")
            self._commit(fx, td)
            env = _env(td)
            first = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-due.py", "tests/test-old.py"], cwd=str(fx),
                                   env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            self._shift_rows(td, "tests/test-due.py", hours=60)
            self._shift_rows(td, "tests/test-old.py", hours=60)
            got = self._why(fx, env, "tests/test-due.py", "tests/test-old.py")
            self.assertEqual((got["tests/test-due.py"]["verdict"], got["tests/test-due.py"]["why"]), ("must-run", "date-crossed"),
                             got["tests/test-due.py"])
            self.assertIn("due.json", got["tests/test-due.py"]["detail"])
            self.assertEqual(got["tests/test-old.py"]["verdict"], "reuse", got["tests/test-old.py"])

    def test_c25_needs_unmet_is_unverified_not_green(self):
        # 10-04:`# aib: needs <條件>` 這台不符合 ⇒ 不跑、不寫池、不算綠也不算紅,總表另列,整批 rc=4;
        #   符合的條件照跑;看不懂的條件照跑(打錯字不准變成沒驗);同批有紅 ⇒ rc 仍是 1
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-needs.py").write_text("# aib: needs tool:no-such-tool-zz9\n" + OK_TEST, encoding="utf-8")
            (fx / "tests" / "test-needs-ok.py").write_text("# aib: needs tool:sh\n" + OK_TEST, encoding="utf-8")
            (fx / "tests" / "test-needs-odd.py").write_text("# aib: needs wibble:zz\n" + OK_TEST, encoding="utf-8")
            self._commit(fx, td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-needs.py", "tests/test-needs-ok.py", "tests/test-needs-odd.py"]
            p = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(p.returncode, 4, "有沒驗的、沒有紅 ⇒ rc=4:" + p.stdout + p.stderr[-2000:])
            self.assertIn("⊘ tests/test-needs.py", p.stdout, p.stdout)
            tail = p.stdout[p.stdout.index("合計"):] if "合計" in p.stdout else p.stdout
            self.assertIn("⊘", tail, "總表後要另列沒驗的那一行:" + p.stdout)
            self.assertIn("tool:no-such-tool-zz9", tail)
            self.assertIn("▶ tests/test-needs-ok.py", p.stderr, "符合的條件照跑")
            self.assertIn("▶ tests/test-needs-odd.py", p.stderr, "看不懂的條件照跑")
            self.assertNotIn("▶ tests/test-needs.py", p.stderr, "缺條件的不准跑")
            self.assertNotIn("tests/test-needs.py", {r.get("test") for r in _rows(td / "shadow")}, "沒驗的不寫池")
            got = self._why(fx, env, "tests/test-needs.py")["tests/test-needs.py"]
            self.assertEqual((got["verdict"], got["why"]), ("must-run", "needs-unmet"), got)
            q = subprocess.run([sys.executable, str(fx / ENTRY_REL), "tests/test-needs.py", "tests/test-red.py"], cwd=str(fx),
                               env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(q.returncode, 1, "同批有紅 ⇒ rc=1(紅優先)")

    def test_c26_reconfirmed_green_doubles_the_window(self):
        # 10-04(the maintainer:「72 小時都沒有改動就延長一倍」):到期重跑又綠 ⇒ 下一段期限約 144 小時;
        #   同樣 100 小時大的列,只確認過 0 次 ⇒ 過期(對照)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            env = _env(td)
            run = [sys.executable, str(fx / ENTRY_REL), "tests/test-ok.py"]
            first = subprocess.run(run, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr[-2000:])
            self._shift_rows(td, "tests/test-ok.py", hours=100)
            self.assertEqual(self._why(fx, env, "tests/test-ok.py")["tests/test-ok.py"]["why"], "row-expired", "對照:沒確認過就過期")
            again = subprocess.run(run, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            self.assertIn("▶ tests/test-ok.py", again.stderr, "過期要真的重跑:" + again.stderr[-2000:])
            self._shift_rows(td, "tests/test-ok.py", hours=100)      # 兩列一起再老 100 小時:最新那列 100 小時大
            got = self._why(fx, env, "tests/test-ok.py")["tests/test-ok.py"]
            self.assertEqual(got["verdict"], "reuse", "確認一次後期限約 144 小時,100 小時照沿用:%s" % got)

    def test_c27_skipped_cells_are_shown_not_hidden_in_green(self):
        # 10-04:綠燈裡跳過的格子要看得到(實跑與沿用都標),總表另列;結束碼不變(跑了的格子確實綠)
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test-skip.py").write_text(OK_TEST.replace(
                "class T(unittest.TestCase):",
                "class T(unittest.TestCase):\n    @unittest.skip(\"x\")\n    def test_skipped(self):\n        pass\n"),
                encoding="utf-8")
            self._commit(fx, td)
            env = _env(td)
            argv = [sys.executable, str(fx / ENTRY_REL), "tests/test-skip.py", "tests/test-ok.py"]
            for round_ in ("實跑", "沿用"):
                p = subprocess.run(argv, cwd=str(fx), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
                self.assertEqual(p.returncode, 0, round_ + p.stdout + p.stderr[-2000:])
                line = [ln for ln in p.stdout.splitlines() if "tests/test-skip.py" in ln and "⊘" in ln and "合計" not in ln]
                self.assertTrue(line, "%s:那一支要標跳過格數:%s" % (round_, p.stdout))
                self.assertIn("1", line[0])
                ok_line = [ln for ln in p.stdout.splitlines() if "tests/test-ok.py" in ln]
                self.assertFalse(any("⊘" in ln for ln in ok_line), "沒跳過的不准標:" + p.stdout)
                tail = p.stdout[p.stdout.index("合計"):]
                self.assertIn("tests/test-skip.py(1)", tail, "%s:總表要另列:%s" % (round_, p.stdout))
            self.assertIn("✓ tests/test-skip.py", p.stderr, "第二輪要真的是沿用:" + p.stderr[-2000:])

    def test_c28_pytest_style_file_without_main_really_runs(self):
        # 10-04:沒有 main 守衛的 pytest 寫法,以前直接執行 = 只定義函式就 rc 0(一格都沒驗的綠;
        #   test-ctx-foot-line-0920 從 09-20 起就這樣)。現在要真的跑:會紅的紅、會綠的真的跑到格數;
        #   要 pytest fixture 而這台沒有 pytest ⇒ 紅(不准當綠、不准跳過)
        import importlib.util
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            fx = _fixture(td)
            (fx / "tests" / "test_plain_red.py").write_text("def test_bad():\n    assert 1 == 2\n", encoding="utf-8")
            (fx / "tests" / "test_plain_ok.py").write_text("def test_good():\n    assert 1 == 1\n", encoding="utf-8")
            (fx / "tests" / "test_plain_fixture.py").write_text("def test_fx(tmp_path):\n    assert tmp_path\n",
                                                                encoding="utf-8")
            self._commit(fx, td)
            env = _env(td)
            def run(name):
                return subprocess.run([sys.executable, str(fx / ENTRY_REL), "--fresh", name], cwd=str(fx), env=env,
                                      capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
            red = run("tests/test_plain_red.py")
            self.assertEqual(red.returncode, 1, "沒有 main 的失敗測試必須紅:" + red.stdout + red.stderr[-1500:])
            ok = run("tests/test_plain_ok.py")
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr[-1500:])
            row = [r for r in _rows(td / "shadow") if r.get("test") == "tests/test_plain_ok.py"][-1]
            self.assertEqual(row.get("ran"), 1, "要真的跑到那一格:%s" % row.get("tail"))
            if importlib.util.find_spec("pytest") is None:
                fxr = run("tests/test_plain_fixture.py")
                self.assertEqual(fxr.returncode, 1, "要 fixture 又沒有 pytest ⇒ 紅:" + fxr.stdout + fxr.stderr[-1500:])

class EntryKnives(unittest.TestCase):
    """每把刀:複本樹 → 產品檔恰好一次替換 → 在複本跑宣稱的入口格 → 那格必須在 FAIL:/ERROR: 紅集合。"""

    def test_k0_control_copy_without_a_knife_is_all_green(self):
        """對照臂:同一份複本、不落刀,二十九格必須全綠 —— 否則下面刀的紅可能是複本缺檔,不是刀。"""
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "-v", "EntryCells"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            self.assertEqual(len(re.findall(r"^test_c\w+ .* \.\.\. ok$", p.stderr, re.M)), 29, p.stderr[-2000:])

    def test_k1_no_self_check_reds_c1(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '        if not gate.get("ok"):\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "        if False:\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c1_gate_fails_falls_back_and_says_why"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c1_gate_fails_falls_back_and_says_why", red, p.stderr[-2000:])

    def test_k2_always_fall_back_reds_c2(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '        if not gate.get("ok"):\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "        if True:\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c2_gate_passes_never_falls_back"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c2_gate_passes_never_falls_back", red, p.stderr[-2000:])

    def test_k3_trusting_the_run_count_reds_c3(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = "        kept = sum(1 for n in set(ran_names) if n in landed)\n"
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "        kept = len(set(ran_names))\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c3_claimed_on_without_rows_is_a_failure"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c3_claimed_on_without_rows_is_a_failure", red, p.stderr[-2000:])

    def test_k5a_off_word_wired_back_reds_c5a(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "resume_policy.py"
            src = layer.read_text(encoding="utf-8")
            # 舊碼有兩層都放行 off:指令別名與模式清單(cmd_set 只擋清單外的值)。只拆一層,另一層照樣擋 ⇒ 兩層一起改回。
            needle = '    "off": "retired-off", "關閉": "retired-off",\n'
            needle2 = 'MODES = ("quick", "strict")\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            self.assertEqual(src.count(needle2), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    "off": "off", "關閉": "off",\n')
                             .replace(needle2, 'MODES = ("quick", "strict", "off")\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c5a_verify_off_command_is_refused"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c5a_verify_off_command_is_refused", red, p.stderr[-2000:])

    def test_k5b_legacy_off_as_unreadable_reds_c5b(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "resume_policy.py"
            src = layer.read_text(encoding="utf-8")
            needle = '        if mode in RETIRED_MODES:\n            return DEFAULT_MODE, "legacy_off", path\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, ""), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c5b_legacy_off_file_reads_as_quick_and_still_records"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c5b_legacy_off_file_reads_as_quick_and_still_records", red, p.stderr[-2000:])

    def test_k5c_seat_switch_read_again_reds_c5c(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "src" / "cockpit" / "seat_test_reuse.py"
            src = layer.read_text(encoding="utf-8")
            needle = "(AIB_SEAT_TEST_REUSE=0 no longer switches anything off).\"\"\"\n    return True\n"
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "\"\"\"\n    return (environ if environ is not None else "
                                                 "os.environ).get(\"AIB_SEAT_TEST_REUSE\", \"1\") != \"0\"\n"),
                             encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c5c_seat_switch_zero_no_longer_turns_off"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c5c_seat_switch_zero_no_longer_turns_off", red, p.stderr[-2000:])


    def test_k6_timeout_not_passed_reds_c6(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "collab" / "package_trial.py"
            src = layer.read_text(encoding="utf-8")
            needle = "    res = test_entry.run_tests(wt, [test], timeout=timeout)\n"
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "    res = test_entry.run_tests(wt, [test])\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c6_timeout_reaches_the_test"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c6_timeout_reaches_the_test", red, p.stderr[-2000:])


    def test_k7_threshold_never_reached_reds_c7(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "slow_warn.py"
            src = layer.read_text(encoding="utf-8")
            needle = "SLOW_RATIO = 1.10\n"
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "SLOW_RATIO = 1000.0\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c7_over_10pct_of_usual_warns_while_running"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c7_over_10pct_of_usual_warns_while_running", red, p.stderr[-2000:])

    def test_k8_no_loaded_line_up_front_reds_c8(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '                live("gate_ok", {})\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "                pass\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c8_each_test_says_if_shadow_resume_is_loaded_and_usable_before_its_time"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c8_each_test_says_if_shadow_resume_is_loaded_and_usable_before_its_time", red,
                          p.stderr[-2000:])

    def test_k9_no_dict_guard_reds_c9(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = "                if not isinstance(r, dict):\n                    continue\n"
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, ""), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c9_non_object_pool_rows_do_not_crash"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c9_non_object_pool_rows_do_not_crash", red, p.stderr[-2000:])

    def test_k10_unguarded_div_reds_c10(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = (
                '                base = r.get("baseline_s")\n'
                '                el = r.get("elapsed_s")\n'
                '                if isinstance(base, (int, float)) and base > 0 and isinstance(el, (int, float)):\n'
                '                    line += T("row_slow", {"base": "%.1f" % base,\n'
                '                                           "pct": "%d" % round((el / base - 1) * 100)})\n'
                '                else:\n'
                '                    line += T("row_slow", {"base": "%.1f" % (base or 0), "pct": "?"})\n'
            )
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle,
                                         '                line += T("row_slow", {"base": "%.1f" % r["baseline_s"],\n'
                                         '                                       "pct": "%d" % round((r["elapsed_s"] / r["baseline_s"] - 1) * 100)})\n'),
                             encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c10_zero_baseline_prints_row_and_summary"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c10_zero_baseline_prints_row_and_summary", red, p.stderr[-2000:])

    def test_k11_always_print_slow_summary_reds_c11(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = (
                '    if res.get("state") != "fallback":\n'
                '        nobase = sum(1 for r in res["tests"] if not r["reused"] and not r.get("unverified") and r.get("baseline_s") is None)\n'
                '        print(T("slow_summary", {"slow": len(slow), "nobase": nobase}))\n'
            )
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle,
                                         '    nobase = sum(1 for r in res["tests"] if not r["reused"] and not r.get("unverified") and r.get("baseline_s") is None)\n'
                                         '    print(T("slow_summary", {"slow": len(slow), "nobase": nobase}))\n'),
                             encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c11_fallback_does_not_count_nobase"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c11_fallback_does_not_count_nobase", red, p.stderr[-2000:])

    def test_k12_old_missing_check_reds_c12(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = (
                '    missing = (not txt) or txt == key or (txt.startswith("!!") and txt.endswith("!!"))\n'
                '    return code if missing else txt\n'
            )
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "    return code if (not txt or txt == key) else txt\n"),
                             encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c12_why_text_missing_key_shows_code"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c12_why_text_missing_key_shows_code", red, p.stderr[-2000:])

    def test_k13_no_live_done_reds_c13(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    live("done_green" if r.get("rc") == 0 else "done_red", params)\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "    pass\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c13_each_real_run_says_green_or_red_the_moment_it_ends"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c13_each_real_run_says_green_or_red_the_moment_it_ends", red, p.stderr[-2000:])

    def test_k14_resume_red_too_reds_c14(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    return {t for t, rc in last.items() if rc == 0}, None\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return set(last), None\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c14_resume_skips_green_reruns_red"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c14_resume_skips_green_reruns_red", red, p.stderr[-2000:])

    def test_k15_skip_bad_line_reds_c15(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '        except ValueError:\n            return set(), "bad-line %d" % n\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '        except ValueError:\n            continue\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c15_bad_journal_line_reruns_everything"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c15_bad_journal_line_reruns_everything", red, p.stderr[-2000:])

    def test_k16_ignore_tree_reds_c16(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = 'if row["tree"] == tree and bool(row["fresh"]) == bool(fresh):'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, 'if bool(row["fresh"]) == bool(fresh):'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c16_tree_change_does_not_resume"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c16_tree_change_does_not_resume", red, p.stderr[-2000:])
    def test_k17_no_tree_guard_reds_c17(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    if baseline is None:\n        return\n    now = _tracked_dirty(repo)\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c17_test_that_leaves_tracked_file_changed_is_red_and_restored"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c17_test_that_leaves_tracked_file_changed_is_red_and_restored", red, p.stderr[-2000:])

    def test_k18_no_void_on_tree_dirty_reds_c18(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = ('    reuse_index.write_void(runner._pool_file(repo), {"test": result.get("name", "?"), "green_only": True},\n'
                      '                           "tree-dirtied:" + files, slack_s=2)\n')
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, "    pass\n"), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c18_tree_dirtying_test_is_never_reused_next_round"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c18_tree_dirtying_test_is_never_reused_next_round", red, p.stderr[-2000:])

    def test_k19_never_see_other_batches_reds_c19(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    if _other_batches(repo):\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    if False:\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name),
                                "EntryCells.test_c19_shared_tree_change_is_not_blamed_not_restored_but_voided"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c19_shared_tree_change_is_not_blamed_not_restored_but_voided", red, p.stderr[-2000:])

    def test_k20_kill_switch_ignored_reds_c20(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    return str((environ if environ is not None else os.environ).get("AIB_TEST_NO_REUSE") or "") == "1"\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return False\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c20_kill_switch_runs_everything_and_still_records"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c20_kill_switch_runs_everything_and_still_records", red, p.stderr[-2000:])

    def test_k21_marker_ignored_reds_c21(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "test_entry.py"
            src = layer.read_text(encoding="utf-8")
            needle = '    return bool(ALWAYS_RUN_MARK.search(head))\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return False\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c21_always_run_marker_is_never_reused"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c21_always_run_marker_is_never_reused", red, p.stderr[-2000:])


    def test_k22_why_without_item_diff_reds_c22(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / "tools" / "verify" / "reuse_index.py"
            src = layer.read_text(encoding="utf-8")
            needle = '                    found.append("%s:%s" % (kind, path) if path else (it if len(it) <= 160 else it[:157] + "..."))\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '                    pass\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c22_why_names_the_input_and_runs_nothing"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c22_why_names_the_input_and_runs_nothing", red, p.stderr[-2000:])

    def test_k23_no_age_limit_reds_c23(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/reuse_index.py'
            src = layer.read_text(encoding="utf-8")
            needle = '        if age_h > ttl:\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '        if False:\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c23_old_row_expires"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c23_old_row_expires", red, p.stderr[-2000:])

    def test_k24_no_date_scan_reds_c24(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/reuse_index.py'
            src = layer.read_text(encoding="utf-8")
            needle = '        crossed = self._date_crossed(repo, prev, born, now_utc)\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '        crossed = ""\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c24_date_in_read_file_crossed_reruns"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c24_date_in_read_file_crossed_reruns", red, p.stderr[-2000:])

    def test_k25_needs_ignored_reds_c25(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/test_entry.py'
            src = layer.read_text(encoding="utf-8")
            needle = '    return [c for c in _needs_of(repo, name) if _need_met(repo, c) is False]\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return []\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c25_needs_unmet_is_unverified_not_green"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c25_needs_unmet_is_unverified_not_green", red, p.stderr[-2000:])

    def test_k25b_unknown_need_skips_reds_c25(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/test_entry.py'
            src = layer.read_text(encoding="utf-8")
            needle = '    return [c for c in _needs_of(repo, name) if _need_met(repo, c) is False]\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return [c for c in _needs_of(repo, name) if _need_met(repo, c) is not True]\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c25_needs_unmet_is_unverified_not_green"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c25_needs_unmet_is_unverified_not_green", red, p.stderr[-2000:])
    def test_k26_no_doubling_reds_c26(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/reuse_index.py'
            src = layer.read_text(encoding="utf-8")
            needle = '                    ttl = min(ttl * 2, MAX_TTL_H)\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '                    pass\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c26_reconfirmed_green_doubles_the_window"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c26_reconfirmed_green_doubles_the_window", red, p.stderr[-2000:])
    def test_k27_skips_hidden_reds_c27(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/test_entry.py'
            src = layer.read_text(encoding="utf-8")
            needle = '    return int(m[-1]) if m else 0\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    return 0\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c27_skipped_cells_are_shown_not_hidden_in_green"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c27_skipped_cells_are_shown_not_hidden_in_green", red, p.stderr[-2000:])
    def test_k28_direct_exec_reds_c28(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "prod"
            for sub in ("src/cockpit", "tools/verify", "tools/governance", "tools/collab", "i18n"):
                shutil.copytree(REPO / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "VERSION", dst / "VERSION")
            (dst / "tests").mkdir()
            shutil.copy2(THIS, dst / "tests" / THIS.name)
            shutil.copy2(REPO / "tests" / "aib_cabin_wash.py", dst / "tests" / "aib_cabin_wash.py")
            shutil.copy2(REPO / "tests" / "aib_testlib.py", dst / "tests" / "aib_testlib.py")
            layer = dst / 'tools/verify/runner.py'
            src = layer.read_text(encoding="utf-8")
            needle = '    if _MAIN_GUARD.search(text) or not _HAS_TESTS.search(text):\n'
            self.assertEqual(src.count(needle), 1, "刀錨要恰好出現一次")
            layer.write_text(src.replace(needle, '    if True:\n'), encoding="utf-8")
            p = subprocess.run([sys.executable, str(dst / "tests" / THIS.name), "EntryCells.test_c28_pytest_style_file_without_main_really_runs"],
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
            red = set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", p.stderr, re.M))
            self.assertIn("test_c28_pytest_style_file_without_main_really_runs", red, p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
