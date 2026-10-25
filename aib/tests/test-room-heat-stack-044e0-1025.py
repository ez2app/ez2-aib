#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib room heat stack(10-25 @044e0,RULING-room-heat-local-opt 8.1 第 4 張):散熱元件插槽的資料。
全在暫存狀態根 + 暫存 HOME + 假紀錄跑;不叫外部指令、不取樣。
S1 空狀態:順序 = 契約;generated_at 帶本機時區 offset、generated_ts 是整數秒;沒紀錄 ⇒ result / last_run_at 都是 null(不是 0);
   沒有機房歷史 ⇒ stale;skill 找不到 ⇒ missing;設定讀得懂、沒有 footer-gate ⇒ on
S2 舊版 aib(< fixed_in)⇒ update_available
S3 有紀錄:memguard 7 天內收了幾次(7 天前的不算)、手動 / 自動釋放分開數、history 最後一點的 unseen ⇒ 數字、
   advice 快取 ⇒ 建議條數、known 換版後量過 ⇒ before / after + 已改善幾項;換版後還沒量 ⇒ after 是 null
S4 讀不到版號 ⇒ unmeasured version_unreadable(S4,不是 missing);aib 自帶 skill 沒寫版號 ⇒ 用 aib 版號;
   skill 寫 version: ⇒ 用它;外掛清單版號是「?」⇒ unmeasured
S5 Stop hook 還有 footer-gate ⇒ update_available;設定檔壞掉 ⇒ unmeasured hooks_incomplete
S6 不支援的平台 ⇒ 全部 unsupported unsupported_platform
S7 memguard 7 天內有 mem_guard_failed ⇒ error memguard_failed
S8 隱私 / 契約:JSON 不含暫存路徑;state / unit / code 都在清單內;每個 title_key / hint_key 四語字典都有;CLI --json / 文字模式
S9 自動釋放：裝了外掛但沒有自動收過的紀錄 ⇒ unmeasured no_record(不宣稱在跑);有紀錄 ⇒ on + 7 天次數
S10 壞時間(1e300)不炸、未來的時間(超過 60 秒)當壞資料丟:不算次數、不給 last_run_at、歷史點不算新鮮
S12 時間是負數 / 0(早於 2001)⇒ 也當壞資料:唯一一筆壞的自動完成不能判 on
S11 版號 v 開頭可比、非字串 / 比不了 ⇒ version_unreadable;外掛清單混雜非 dict 列不炸;文字模式 after 是 null 印「—」
刀(複本 + run_guarded):K1 時間不帶時區 ⇒ S1 紅;K2 版號讀不到當沒裝 ⇒ S4 紅;K3 換版後沒量也給 after ⇒ S3 紅;
K4 有看不懂的設定也判 on ⇒ S5 紅;K5 釋放次數條件改回「!= auto」⇒ S3 紅;K6 不丟未來時間 ⇒ S10 紅;K7 不丟太舊時間 ⇒ S12 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
os.environ.setdefault("EZ2_AIB_LOCALE", "zh-TW")
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

import room_heat as RH  # noqa: E402
import room_heat_stack as ST  # noqa: E402

NOW = 1792900000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="aib-heat-stack-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.home = self.d / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {}}))
        self.state = self.d / "state"
        p = mock.patch.dict(os.environ, {"EZ2_AIB_STATE_ROOT": str(self.state), "HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        import room_heat_advice as AD
        m2 = mock.patch.dict(AD.MANAGED_SETTINGS, {"darwin": "", "linux": ""})
        m2.start()
        self.addCleanup(m2.stop)

    def room(self):
        r = Path(RH.state_dir())
        r.mkdir(parents=True, exist_ok=True)
        return r

    def run_stack(self, **kw):
        kw.setdefault("now", NOW)
        kw.setdefault("platform", "darwin")
        kw.setdefault("claude_home", str(self.claude))
        kw.setdefault("aib", "0.1.56")
        kw.setdefault("plugins_fn", lambda: [])
        kw.setdefault("home", str(self.home))
        return ST.stack(**kw)

    def by_id(self, s):
        return {it["id"]: it for it in s["items"]}


class Empty(Base):
    def test_s1_empty_state(self):
        s = self.run_stack()
        self.assertEqual([it["id"] for it in s["items"]], list(ST.IDS))
        self.assertEqual((s["v"], s["schema"], s["aib"]), (1, "aib-heat-stack/v1", "0.1.56"))
        self.assertIsInstance(s["generated_ts"], int)
        self.assertRegex(s["generated_at"], r"[+-]\d\d:\d\d$|Z$")
        local = time.strftime("%z", time.localtime(NOW))
        self.assertTrue(s["generated_at"].endswith(local[:3] + ":" + local[3:]), s["generated_at"])
        self.assertTrue(s["stale"])
        it = self.by_id(s)
        for iid in ("memguard", "test_memcap", "right_tabs", "statusline", "room_heat", "heat_release", "heat_unseen", "heat_advice", "heat_known"):
            self.assertEqual((it[iid]["state"], it[iid]["installed"]), ("on", "0.1.56"), iid)
            self.assertIsNone(it[iid]["result"], iid)
            self.assertIsNone(it[iid]["last_run_at"], iid)
        self.assertEqual(it["heat_auto_release"]["state"], "missing")
        self.assertTrue(it["heat_auto_release"]["paid"])
        self.assertEqual(it["hook_footer_gate"]["state"], "on")
        self.assertEqual(it["ez2-local-perf"]["title_key"], "aib.room.heat.stack.ez2_local_perf.title")

    def test_s2_old_aib(self):
        it = self.by_id(self.run_stack(aib="0.1.53"))
        self.assertEqual(it["memguard"]["state"], "update_available")
        self.assertEqual(it["statusline"]["state"], "update_available")
        self.assertEqual(it["room_heat"]["state"], "on")


class Records(Base):
    def test_s3_records(self):
        room = self.room()
        logs = self.state / "logs"
        logs.mkdir(parents=True)
        (logs / "memguard.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"ts": NOW - 3600, "action": "mem_terminated"}, {"ts": NOW - 7200, "action": "mem_killed"},
            {"ts": NOW - 8 * 86400, "action": "mem_killed"}, {"ts": NOW - 60, "action": "mem_dry_run"})) + "\nnot json\n")
        import room_heat_release as RR                  # 跟釋放程式寫的同一種格式(預留 + 完成兩筆)
        for t, auto, result in ((NOW - 100, False, "ok"), (NOW - 110, False, "release_failed"), (NOW - 120, False, "candidate_changed"),
                                (NOW - 200, True, "ok"), (NOW - 300, True, "partial"), (NOW - 400, True, "release_failed"),
                                (NOW - 9 * 86400, False, "ok")):
            if auto:
                RR._log({"t": t - 1, "kind": "headless_browser", "n_pids": 2, "mode": "auto", "result": "pending"})
            RR._log({"t": t, "kind": "headless_browser", "owner": "seat", "cpu": 1.0, "mem_mb": 10.0, "n_pids": 2,
                     "mode": "auto_done" if auto else "manual", "result": result})
        (room / "heat-history.json").write_text(json.dumps({"points": [{"t": NOW - 30, "level": "ok", "unseen_cpu": 1.25}]}))
        (room / "advice-last.json").write_text(json.dumps({"schema": "aib-heat-advice/v1", "t": NOW - 50, "items": [{}, {}, {}]}))
        (room / "known-hot.json").write_text(json.dumps({"schema": "aib-heat-known/v1", "codes": {
            "right_tabs_poll": {"befores": [30.0], "before_ver": "0.1.53", "after": 2.0, "after_ver": "0.1.56", "t": NOW - 40},
            "statusline_cost": {"befores": [5.0], "before_ver": "0.1.53", "after": 1.0, "after_ver": "0.1.54", "t": NOW - 45}}}))
        s = self.run_stack(plugins_fn=lambda: [{"name": "captain-room", "version": "0.1.46"}])
        it = self.by_id(s)
        self.assertFalse(s["stale"])
        self.assertEqual(it["memguard"]["result"], {"metric": "kills_7d", "unit": "count", "before": None, "after": None, "value": 2.0})
        self.assertEqual(it["heat_release"]["result"]["value"], 1.0)      # 手動:只算成功的、不算 9 天前、不算自動完成
        self.assertEqual(it["heat_release"]["last_run_at"], ST.iso(NOW - 100))
        self.assertEqual(it["heat_auto_release"]["result"]["value"], 2.0)  # 自動:ok + partial,預留和失敗不算
        self.assertEqual(it["heat_auto_release"]["last_run_at"], ST.iso(NOW - 200))
        self.assertEqual(it["heat_auto_release"]["state"], "on")
        self.assertEqual(it["heat_unseen"]["result"]["value"], 1.25)
        self.assertEqual(it["heat_advice"]["result"]["value"], 3.0)
        rt = it["right_tabs"]["result"]
        self.assertEqual((rt["unit"], rt["before"], rt["after"]), ("cores", 30.0, 2.0))
        sl = it["statusline"]["result"]
        self.assertEqual((sl["before"], sl["after"]), (5.0, None))          # 換版(0.1.55)後還沒量 ⇒ 不給 after
        self.assertEqual(it["heat_known"]["result"]["value"], 1.0)
        self.assertIsNotNone(it["heat_unseen"]["last_run_at"])


class Unreadable(Base):
    def test_s4_unreadable_versions(self):
        s = self.run_stack(aib="unreadable", plugins_fn=lambda: [{"name": "captain-room", "version": "?"}])
        it = self.by_id(s)
        self.assertIsNone(s["aib"])
        self.assertEqual((it["memguard"]["state"], it["memguard"]["code"]), ("unmeasured", "version_unreadable"))
        self.assertEqual((it["heat_auto_release"]["state"], it["heat_auto_release"]["code"]), ("unmeasured", "version_unreadable"))
        sk = self.home / ".claude" / "skills" / "ez2-local-perf"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: ez2-local-perf\n---\nbody\n")
        it = self.by_id(self.run_stack())
        self.assertEqual((it["ez2-local-perf"]["state"], it["ez2-local-perf"]["code"]), ("unmeasured", "version_unreadable"))
        (sk / "SKILL.md").write_text("---\nname: ez2-local-perf\nversion: 1.2.0\n---\nversion: 9.9.9\n")
        it = self.by_id(self.run_stack())
        self.assertEqual((it["ez2-local-perf"]["state"], it["ez2-local-perf"]["installed"]), ("on", "1.2.0"))
        own = self.d / "aibroot"
        (own / "skills" / "ez2-local-perf").mkdir(parents=True)
        (own / "skills" / "ez2-local-perf" / "SKILL.md").write_text("---\nname: ez2-local-perf\n---\n")
        self.assertEqual(ST._skill_version("ez2-local-perf", "0.1.57", home=str(self.d / "nohome"), aib_root=str(own)), "0.1.57")


class Footer(Base):
    def test_s5_footer_gate(self):
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "python3 ~/.claude/hooks/footer-gate.py", "timeout": 10}]}]}}))
        self.assertEqual(self.by_id(self.run_stack())["hook_footer_gate"]["state"], "update_available")
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": "bad"}}))
        it = self.by_id(self.run_stack())["hook_footer_gate"]
        self.assertEqual((it["state"], it["code"]), ("unmeasured", "hooks_incomplete"))


class Platform(Base):
    def test_s6_unsupported(self):
        s = self.run_stack(platform="win32")
        self.assertEqual({(it["state"], it["code"]) for it in s["items"]}, {("unsupported", "unsupported_platform")})
        self.assertEqual(self.by_id(self.run_stack(platform="linux"))["room_heat"]["state"], "on")

    def test_s7_memguard_failed(self):
        logs = self.state / "logs"
        logs.mkdir(parents=True)
        (logs / "memguard.jsonl").write_text(json.dumps({"ts": NOW - 600, "action": "mem_guard_failed", "error": "OSError"}) + "\n")
        it = self.by_id(self.run_stack())["memguard"]
        self.assertEqual((it["state"], it["code"]), ("error", "memguard_failed"))
        with open(logs / "memguard.jsonl", "a") as fh:                  # 失敗之後又正常收過 ⇒ 不再判 error
            fh.write(json.dumps({"ts": NOW - 60, "action": "mem_terminated"}) + "\n")
        it = self.by_id(self.run_stack())["memguard"]
        self.assertEqual((it["state"], it["code"]), ("on", None))


class BadTime(Base):
    def test_s10_bad_and_future_timestamps(self):
        self.assertIsNone(ST.iso(1e300))
        self.assertIsNone(ST.iso(-1e300))
        room = self.room()
        logs = self.state / "logs"
        logs.mkdir(parents=True)
        (logs / "memguard.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"ts": 1e300, "action": "mem_killed"}, {"ts": NOW + 3600, "action": "mem_guard_failed"})) + "\n")
        import room_heat_release as RR
        RR._log({"t": 1e300, "mode": "manual", "result": "ok"})
        RR._log({"t": NOW + 3600, "mode": "manual", "result": "ok"})
        RR._log({"t": NOW + 3600, "mode": "auto_done", "result": "ok"})
        (room / "heat-history.json").write_text(json.dumps({"points": [{"t": NOW + 3600, "level": "ok", "unseen_cpu": 1.0}]}))
        (room / "advice-last.json").write_text(json.dumps({"schema": "aib-heat-advice/v1", "t": 1e300, "items": [{}]}))
        (room / "known-hot.json").write_text(json.dumps({"schema": "aib-heat-known/v1", "codes": {
            "right_tabs_poll": {"befores": [30.0], "before_ver": "0.1.53", "after": 2.0, "after_ver": "0.1.56", "t": 1e300}}}))
        s = self.run_stack(plugins_fn=lambda: [{"name": "captain-room", "version": "0.1.46"}])
        it = self.by_id(s)
        self.assertTrue(s["stale"])                                    # 未來的歷史點不算新鮮
        self.assertEqual((it["memguard"]["state"], it["memguard"]["result"]["value"], it["memguard"]["last_run_at"]), ("on", 0.0, None))
        self.assertEqual((it["heat_release"]["result"]["value"], it["heat_release"]["last_run_at"]), (0.0, None))
        self.assertEqual((it["heat_auto_release"]["state"], it["heat_auto_release"]["code"]), ("unmeasured", "no_record"))
        for iid in ("room_heat", "heat_unseen", "heat_advice", "right_tabs", "heat_known"):
            self.assertIsNone(it[iid]["last_run_at"], iid)
        json.dumps(s)

    def test_s12_too_old_timestamps(self):
        import room_heat_release as RR                 # 唯一一筆自動完成的時間是負數 / 0 ⇒ 不算有紀錄
        RR._log({"t": -5, "mode": "auto_done", "result": "ok"})
        RR._log({"t": 0, "mode": "auto_done", "result": "ok"})
        it = self.by_id(self.run_stack(plugins_fn=lambda: [{"name": "captain-room", "version": "0.1.46"}]))["heat_auto_release"]
        self.assertEqual((it["state"], it["code"], it["last_run_at"]), ("unmeasured", "no_record", None))

    def test_s11_versions_and_rows(self):
        self.assertEqual(ST._ver("v0.1.56"), (0, 1, 56))
        self.assertIsNone(ST._ver(0.156))
        it = self.by_id(self.run_stack(aib="0.1.x", plugins_fn=lambda: ["junk", None, {"name": "captain-room", "version": "v0.1.46"}]))
        self.assertEqual((it["memguard"]["state"], it["memguard"]["code"]), ("unmeasured", "version_unreadable"))
        self.assertEqual((it["heat_auto_release"]["installed"], it["heat_auto_release"]["code"]), ("v0.1.46", "no_record"))
        it = self.by_id(self.run_stack(aib=156))
        self.assertEqual((it["memguard"]["state"], it["memguard"]["code"]), ("unmeasured", "version_unreadable"))
        s = self.run_stack()
        self.by_id(s)["statusline"]["result"] = {"metric": "cpu", "unit": "cores", "before": 5.0, "after": None, "value": None}
        out = io.StringIO()
        with mock.patch.object(ST, "stack", return_value=s):
            self.assertEqual(RH.main(["stack"], out=out), 0)
        self.assertIn("5.0→—", out.getvalue())
        self.assertNotIn("None", out.getvalue())


class Contract(Base):
    def test_s8_privacy_contract_i18n_cli(self):
        s = self.run_stack(plugins_fn=lambda: [{"name": "captain-room", "version": "0.1.46"}])
        txt = json.dumps(s, ensure_ascii=False)
        self.assertNotIn(str(self.d), txt)
        self.assertNotIn("/Users/", txt)
        codes = {"version_unreadable", "hooks_incomplete", "memguard_failed", "no_record", "unsupported_platform", None}
        for it in s["items"]:
            self.assertIn(it["state"], ST.STATES)
            self.assertIn(it["code"], codes)
            self.assertIn(it["kind"], ("core", "skill", "plugin", "setting"))
            if it["result"]:
                self.assertIn(it["result"]["unit"], ST.UNITS)
        for lang in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for it in s["items"]:
                self.assertIn(it["title_key"], d, lang)
                self.assertIn(it["hint_key"], d, lang)
            for st in ST.STATES:
                self.assertIn("aib.heat.stack.state." + st, d, lang)
        out = io.StringIO()
        with mock.patch.object(ST, "stack", return_value=s):
            self.assertEqual(RH.main(["stack", "--json"], out=out), 0)
        self.assertEqual(json.loads(out.getvalue())["schema"], "aib-heat-stack/v1")
        out = io.StringIO()
        with mock.patch.object(ST, "stack", return_value=s):
            self.assertEqual(RH.main(["stack"], out=out), 0)
        self.assertIn("散熱元件", out.getvalue())
        self.assertIn("memguard", out.getvalue())

    def test_s9_auto_release_needs_record(self):
        it = self.by_id(self.run_stack(plugins_fn=lambda: [{"name": "captain-room", "version": "0.1.46"}]))["heat_auto_release"]
        self.assertEqual((it["state"], it["code"], it["installed"]), ("unmeasured", "no_record", "0.1.46"))


KNIVES = [
    ("K1", "src/cockpit/room_heat_stack.py", "    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec=\"seconds\")\n",
     "    return datetime.datetime.fromtimestamp(ts).isoformat(timespec=\"seconds\")\n", "Empty.test_s1_empty_state"),
    ("K2", "src/cockpit/room_heat_stack.py",
     "    if installed is not None and (not isinstance(installed, str) or _ver(installed) is None):\n", "    if False:\n",
     "Unreadable.test_s4_unreadable_versions"),
    ("K3", "src/cockpit/room_heat_stack.py", "        after = rec.get(\"after\") if st in (\"improved\", \"not_improved\", \"fixed\") else None\n",
     "        after = rec.get(\"after\")\n", "Records.test_s3_records"),
    ("K4", "src/cockpit/room_heat_stack.py", "    return (\"on\", None) if complete else (\"unmeasured\", \"hooks_incomplete\")\n",
     "    return (\"on\", None)\n", "Footer.test_s5_footer_gate"),
    ("K5", "src/cockpit/room_heat_stack.py",
     "    mine = [r for r in rows if r.get(\"mode\") == want and r.get(\"result\") in RELEASED_OK and _ok_t(r.get(\"t\"), now)]\n",
     "    mine = [r for r in rows if (r.get(\"mode\") != \"auto\") != auto and _ok_t(r.get(\"t\"), now)]\n", "Records.test_s3_records"),
    ("K6", "src/cockpit/room_heat_stack.py", "    return _finite(t) and MIN_T <= t <= now + FUTURE_SLACK_S\n", "    return _finite(t) and MIN_T <= t\n",
     "BadTime.test_s10_bad_and_future_timestamps"),
    ("K7", "src/cockpit/room_heat_stack.py", "    return _finite(t) and MIN_T <= t <= now + FUTURE_SLACK_S\n", "    return _finite(t) and t <= now + FUTURE_SLACK_S\n",
     "BadTime.test_s12_too_old_timestamps"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name,)) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=40, timeout=120)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
