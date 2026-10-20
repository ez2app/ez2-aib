#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""對話面板核心 C2 / C3(10-20 Andy「對話面板放核心免費」;SPEC-chat-panel-formats v0.5):
aib seat transcript <席碼> / aib seat pending <席碼>,只讀紀錄檔與畫面。

樣本:tests/fixtures/chat-panel/(@ee0bb、@abb89、@d07f9 10-20 去識別化真實紀錄)+ 本檔合成的 dm / 壓縮摘要 / 未回答的提問。
T1 事件種類:user(pasted 區塊)、hook、human_shell(本人 ! 跑的)、system(task-notification / 登入 / 壓縮邊界)、
   notices(額度恢復、登入成功)、tool_call / tool_result(bytes)、ask;thinking 預設不出;壓縮摘要 / isMeta 的 user 不出;
   api-request* / attachment 等一律不出
T2 dm:一則裡的 dm 行 ⇒ kind=dm(from / to / relay_path);長訊落檔在 ~/.ez2-aib 底下 ⇒ relay_text 自動讀;落在別處 ⇒ 不讀
T3 游標:--since 增量、--since 找不到 ⇒ reset、--before 往上、--limit 截尾 truncated、--id 單筆、--clip 剪 + clipped、
   --offset 只讀新增、--redact 遮 hex / email / ip
T4 pending:未回答的 AskUserQuestion ⇒ ask type=question(options 帶 description / preview);答過 ⇒ null;
   畫面權限框 ⇒ type=permission options 1/2/3、at_prompt False;提示列 ⇒ idle / at_prompt True;思考中 ⇒ thinking;排隊中 n 則
T5 入口:bash src/cockpit/aib seat transcript <席> --json 接到 seat_panel;席碼不合 / 找不到 ⇒ rc 2 JSON
刀(複本):K1 不濾壓縮摘要 ⇒ T1 紅;K2 不看 tool_result 就當沒回答 ⇒ T4 紅
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
FIX = HERE / "fixtures" / "chat-panel"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

SESS = "11111111-2222-4333-8444-5555550ab12"   # 末五碼 0ab12
CODE = "0ab12"
TS = "2026-10-20T10:00:00.000Z"


def rec(t, content, uuid, **extra):
    d = {"type": t, "uuid": uuid, "timestamp": TS, "sessionId": SESS, "isSidechain": False, "parentUuid": None,
         "message": {"role": "user" if t == "user" else "assistant", "content": content}}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def ask_rec(uuid, tid, answered):
    q = {"questions": [{"header": "方向", "question": "走哪一條?", "multiSelect": False,
                        "options": [{"label": "A 快", "description": "先做", "preview": "A ----"}, {"label": "B 穩", "description": "慢做"}]}]}
    lines = [rec("assistant", [{"type": "tool_use", "id": tid, "name": "AskUserQuestion", "input": q}], uuid)]
    if answered:
        lines.append(rec("user", [{"type": "tool_result", "tool_use_id": tid, "content": json.dumps({"answers": {"走哪一條?": "A 快"}})}], uuid + "-r"))
    return lines


SCREEN_PERMISSION = """  Bash command
    rm -rf build/
  Do you want to proceed?
❯ 1. Yes
  2. Yes, and don't ask again for rm commands in ~/x
  3. No, and tell Claude what to do differently (esc)
"""
SCREEN_PROMPT = """⏺ 好了。

╭──────────────────────────────────────────────╮
│ ❯                                            │
╰──────────────────────────────────────────────╯
  ? for shortcuts
"""
SCREEN_THINKING = """  ◆ Run Find distill DM
  ⠧ Thinking… 6.4s        28s ⇣200k [stop]
"""


class Box(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-panel-")
        self.tmp = Path(self.td.name)
        self.home = self.tmp / "home"
        self.state = self.home / ".ez2-aib"
        self.cwd = self.tmp / "work"
        self.cwd.mkdir(parents=True)
        inst = self.state / "instances" / "20261020-000000-ab12" / "seats"
        inst.mkdir(parents=True)
        (inst / "w3.json").write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": SESS, "cwd": str(self.cwd), "provider": "claude",
                                                   "socket": "aihub-20261020-000000-ab12", "window_id": "@3", "started_epoch": 1}))
        import activity_probe
        self.proj = self.home / ".claude" / "projects" / activity_probe.escape_cwd(str(self.cwd))
        self.proj.mkdir(parents=True)
        self.jsonl = self.proj / (SESS + ".jsonl")
        (self.state / "pending").mkdir(parents=True)
        self.relay = self.state / "pending" / "RELAY-dm-test.txt"
        self.relay.write_text("長信內容:第一行\n第二行", encoding="utf-8")
        self.envp = mock.patch.dict(os.environ, {"HOME": str(self.home), "EZ2_AIB_STATE_ROOT": str(self.state), "EZ2_AIB_LOCALE": "zh-TW",
                                                 "EZ2_AIB_ROOT": str(REPO)})
        self.envp.start()
        for m in ("seat_panel", "seats_dir_lib", "activity_probe", "seat_now", "dm_queue"):
            sys.modules.pop(m, None)
        import seat_panel
        self.SP = seat_panel

    def tearDown(self):
        self.envp.stop()
        self.td.cleanup()

    def write(self, lines):
        self.jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def fixtures_lines(self):
        out = []
        for f in sorted(FIX.glob("*.jsonl")):
            out += [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        return out

    def transcript(self, **kw):
        res, rc = self.SP.transcript(CODE, **kw)
        self.assertTrue(res.get("ok"), res)
        return res

    def kinds(self, res):
        return [e["kind"] for e in res["events"]]


class Events(Box):
    def test_t1_kinds(self):
        lines = self.fixtures_lines() + [
            rec("user", "這是壓縮摘要", "u-cs", isCompactSummary=True, isVisibleInTranscriptOnly=True),
            rec("user", "meta", "u-meta", isMeta=True),
            rec("user", "真人親打的一句", "u-real"),
            rec("assistant", [{"type": "thinking", "thinking": "想一下"}, {"type": "text", "text": "我來做。"}], "a-text"),
            json.dumps({"type": "api-request-blob", "message": {"content": [{"type": "text", "text": "不要出現"}]}}),
            json.dumps({"type": "attachment", "message": {"content": "附件也不要出現"}}),
        ]
        self.write(lines)
        res = self.transcript(limit=0)
        ks = self.kinds(res)
        texts = " ".join(e["text"] for e in res["events"])
        for k in ("user", "hook", "human_shell", "system", "tool_call", "tool_result", "ask", "assistant"):
            self.assertIn(k, ks, (k, ks))
        self.assertNotIn("thinking", ks)
        self.assertNotIn("這是壓縮摘要", texts)
        self.assertNotIn("不要出現", texts)
        self.assertIn("真人親打的一句", texts)
        shell = [e for e in res["events"] if e["kind"] == "human_shell"]
        self.assertTrue(shell and "stdout" in shell[0]["meta"] and "<bash-input>" not in shell[0]["text"], shell[:1])
        pasted = [e for e in res["events"] if e["meta"].get("pasted")]
        self.assertTrue(pasted, "pasted_content 要標成區塊")
        notes = [n for e in res["events"] for n in e["meta"].get("notices", [])]
        self.assertTrue({"usage_reset", "login_ok"} <= set(notes), notes)
        subs = {e["meta"].get("subtype") for e in res["events"] if e["kind"] == "system"}
        self.assertTrue({"task-notification", "compact_boundary"} & subs, subs)
        tr = [e for e in res["events"] if e["kind"] == "tool_result"]
        self.assertTrue(all(isinstance(e["meta"].get("bytes"), int) for e in tr))
        self.assertTrue(self.kinds(self.transcript(limit=0, with_thinking=True)).count("thinking") >= 0)

    def test_t2_dm(self):
        line = ("IP:100.1.2.3/6ddf/AIB工程師@abb89 >> IP:100.1.2.3/6ddf/全球對談@%s 10-20 13:03(台北時間) 短訊一行\n"
                "本則發出於 13:03:01,現在 13:03:05,已過 4 秒\n"
                "IP:100.1.2.3/6ddf/艙外@offhub >> IP:100.1.2.3/6ddf/全球對談@%s 10-20 13:04(台北時間) 長訊已落檔(20 位元組,來自 @74e8b),請讀:%s\n"
                "IP:100.1.2.3/6ddf/艙外@offhub >> IP:100.1.2.3/6ddf/全球對談@%s 10-20 13:05(台北時間) 長訊已落檔(9 位元組),請讀:/etc/passwd"
                % (CODE, CODE, self.relay, CODE))
        self.write([rec("user", line, "u-dm")])
        res = self.transcript()
        dms = [e for e in res["events"] if e["kind"] == "dm"]
        self.assertEqual(len(dms), 3, res["events"])
        self.assertEqual((dms[0]["meta"]["from"], dms[0]["meta"]["to"], dms[0]["text"]), ("abb89", CODE, "短訊一行"))
        self.assertEqual(dms[1]["meta"]["relay_path"], str(self.relay))
        self.assertIn("第二行", dms[1]["text"])
        self.assertIsNone(dms[2]["meta"]["relay_text"], "狀態根外的檔不准讀")
        self.assertNotIn("root:", json.dumps(res, ensure_ascii=False))

    def test_t3_cursors(self):
        lines = [rec("user", "第 %d 句 secret deadbeefdeadbeefdeadbeefdeadbeefdead a@b.co 10.0.0.1" % i, "u%d" % i) for i in range(10)]
        self.write(lines)
        full = self.transcript(limit=0)
        ids = [e["id"] for e in full["events"]]
        self.assertEqual(ids, ["u%d" % i for i in range(10)])
        inc = self.transcript(since="u6")
        self.assertEqual([e["id"] for e in inc["events"]], ["u7", "u8", "u9"])
        self.assertFalse(inc["reset"])
        lost = self.transcript(since="nope")
        self.assertTrue(lost["reset"] and lost["since_lost"])
        self.assertEqual(len(lost["events"]), 10)
        self.assertEqual([e["id"] for e in self.transcript(before="u3")["events"]], ["u0", "u1", "u2"])
        lim = self.transcript(limit=4)
        self.assertTrue(lim["truncated"])
        self.assertEqual([e["id"] for e in lim["events"]], ["u6", "u7", "u8", "u9"], "沒 since 時取最後 N 筆")
        one = self.transcript(only_id="u2")
        self.assertEqual([e["id"] for e in one["events"]], ["u2"])
        cl = self.transcript(clip=5)
        self.assertTrue(all(len(e["text"]) <= 5 and e["meta"]["clipped"] for e in cl["events"]))
        off = full["last_offset"]
        with self.jsonl.open("a", encoding="utf-8") as fh:
            fh.write(rec("user", "新的一句", "u-new") + "\n")
        more = self.transcript(offset=off)
        self.assertEqual([e["id"] for e in more["events"]], ["u-new"])
        red = self.transcript(do_redact=True, only_id="u0")
        t = red["events"][0]["text"]
        self.assertTrue("<hex>" in t and "<email>" in t and "<ip>" in t, t)


class Pending(Box):
    def test_t4_pending(self):
        self.write(ask_rec("a1", "toolu_ask1", answered=False))
        res, rc = self.SP.pending(CODE, screen=SCREEN_PROMPT)
        self.assertEqual(rc, 0)
        ask = res["ask"]
        self.assertEqual((ask["type"], ask["source"], ask["multi"]), ("question", "transcript", False))
        self.assertEqual([o["key"] for o in ask["options"]], ["1", "2"])
        self.assertEqual((ask["options"][0]["description"], ask["options"][0]["preview"]), ("先做", "A ----"))
        self.assertEqual((res["busy"], res["at_prompt"]), ("waiting", False))
        self.write(ask_rec("a1", "toolu_ask1", answered=True))
        res, _ = self.SP.pending(CODE, screen=SCREEN_PROMPT)
        self.assertIsNone(res["ask"], "答過了就不再問")
        self.assertEqual((res["busy"], res["at_prompt"]), ("idle", True))
        res, _ = self.SP.pending(CODE, screen=SCREEN_PERMISSION)
        self.assertEqual((res["ask"]["type"], res["ask"]["source"]), ("permission", "screen"))
        self.assertEqual([o["key"] for o in res["ask"]["options"]], ["1", "2", "3"])
        self.assertTrue(res["ask"]["id"].startswith("screen:") and res["at_prompt"] is False)
        res2, _ = self.SP.pending(CODE, screen=SCREEN_PERMISSION)
        self.assertEqual(res["ask"]["id"], res2["ask"]["id"], "同一題 id 不變")
        res, _ = self.SP.pending(CODE, screen=SCREEN_THINKING)
        self.assertEqual((res["busy"], res["at_prompt"]), ("thinking", False))
        # 10-20 實機(cc203):提示列下面接狀態列 + 十幾行空行;提示列裡有沒送出的草稿 ⇒ idle 但不准打字
        real = "⏺ 狀態列已更新。\n✻ Crunched for 17s · done 下午10:01\n" + "─" * 40 + "\n❯\xa0了解用戶新訊息\n" + "─" * 40 + \
               "\n  ⚠ aib-boards: CTX 17% · 艙 6ddf · root@cc203 · dm 0\n  📌【我很閒】\n  ⏵⏵ bypass permissions on\n" + "\n" * 14
        res, _ = self.SP.pending(CODE, screen=real)
        self.assertEqual((res["busy"], res["at_prompt"]), ("idle", False), res)
        res, _ = self.SP.pending(CODE, screen=real.replace("❯\xa0了解用戶新訊息", "❯\xa0"))
        self.assertEqual((res["busy"], res["at_prompt"]), ("idle", True), res)
        self.write(ask_rec("a1", "toolu_ask1", answered=True) + [json.dumps({"type": "queue-operation", "sessionId": SESS})] * 2)
        res, _ = self.SP.pending(CODE, screen=SCREEN_THINKING)
        self.assertEqual(res["queued"], 2)
        self.assertEqual(res["hold_count"], 0)


class Entry(Box):
    def test_t5_cli(self):
        self.write([rec("user", "hi", "u1")])
        env = dict(os.environ, PATH=os.environ.get("PATH", ""))
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "transcript", CODE, "--json"], capture_output=True, text=True,
                           timeout=120, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        d = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual([e["text"] for e in d["events"]], ["hi"])
        p = subprocess.run(["bash", str(COCKPIT / "aib"), "seat", "pending", "zzzzz"], capture_output=True, text=True, timeout=120,
                           env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1])["why"], "no-seat")
        buf = io.StringIO()
        self.assertEqual(self.SP.main(["transcript", "ab/12"], out=buf), 2)


class Knife(unittest.TestCase):
    def _knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/seat_panel.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=40, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)

    def test_k1_compact_summary_shown(self):
        self._knife('                if rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):\n',
                    '                if False:\n', "Events.test_t1_kinds")

    def test_k2_answered_ignored(self):
        self._knife('                        answered.add(tid)\n', '', "Pending.test_t4_pending")


if __name__ == "__main__":
    unittest.main()
