#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位忙時的背景整理 AI(0.1.62;Andy 10-25 22:3x「不要跳過」、22:5x 同意排下一版;@abb89 23:22 定案,@044e0)。
治具沿用 test-dm-hold-notify-abb89-1015.py(整棵 src 抄進暫存根、假 tmux);起背景行程 / 問 AI / gh 一律注入假的,
不起真 AI、不連網、不碰 ~/.ez2-aib。

H1 觸發:到門檻、席位忙 ⇒ 記 busy_since;忙 29 分 59 秒不起、滿 30 分起一次(引擎照登記);起過的 3 分鐘內不再起;
   有子代理登記中不起;閒下來 busy_since 歸零
H2 引擎:API 席(digest)/ 不是 claude、codex 的家 ⇒ 不起
H3 負載:load1 > 12 不起
H4 逐條驗(@abb89 硬規矩):hold_human / 寫了等什麼的 / 不存在的 id ⇒ not-allowed;PR 沒合 ⇒ leave(不降級);
   PR 已合 ⇒ 結;pending 外的檔 ⇒ leave;pending 內的檔 ⇒ 結;本席寄出的回信 id ⇒ 結;wait ⇒ 寫;draft ⇒ 只進整理檔
H5 整輪:問 AI(假的,回夾雜字句的 JSON)→ 套用 → 整理檔 → 放鎖記 handled_ids;抽查看板列出背景整理結的信
H6 關工具:claude 照 power_ask 的關工具指令叫(--tools "" 等);codex 關不乾淨(MCP 還開著)⇒ 不叫
H7 gh:只有 MERGED 算已合;OPEN / CLOSED / 查不到都不算
刀(複本 + run_guarded):K1 不看 excluded ⇒ H4 紅;K2 不驗證據 ⇒ H4 紅;K3 claude 不帶關工具旗標 ⇒ H6 紅;
K4 gh 不看 MERGED ⇒ H7 紅;K5 忙不滿 30 分也起 ⇒ H1 紅。刀一律斷言「FAIL: <目標>」且沒有 ERROR。
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)
SEAT = HN.SEAT


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


SETUP = (
    "Q.LOAD_FN = lambda: (0.0, 0.0, 0.0)\n"
    "import dm_triage_headless as H, dm_triage_agent as TA\n"
    "def put(msg, now, seat=%r, provider='claude'):\n"
    "    return Q.record(seat, msg, '@77777', status='delivered', rc=0, env=env, now=now,\n"
    "                    sender_kind='ai', wid='@2', provider=provider, hub='aihub-t1')['id']\n"
    "spawned = []\n"
    "Q.SPAWN_HEADLESS = lambda seat, brand, env: spawned.append([seat, brand])\n"
    "BUSY = [True]\n"
    "def tick(now):\n"
    "    return Q.triage_tick(env=env, now=now, hold_fn=lambda s, r: (BUSY[0], 'busy' if BUSY[0] else 'idle'),\n"
    "                         deliver_fn=lambda it: 0, force=True)\n"
    "def tri(seat=%r):\n"
    "    return Q.TR.load(Q.triage_path(seat, env)) or {}\n"
    "t = int(__import__('time').time())\n"
    "B = H.HEADLESS_AFTER_S\n"
) % (SEAT, SEAT)


class H1Trigger(unittest.TestCase):
    def test_busy_30min_starts_once(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); since = tri().get('busy_since')\n"
            "tick(t + 10 + B - 1); a = len(spawned)\n"
            "tick(t + 10 + B); b = len(spawned)\n"
            "tick(t + 10 + B + 60); c = len(spawned)\n"
            "BUSY[0] = False; tick(t + 10 + B + 120); idle_since = tri().get('busy_since')\n"
            "print(json.dumps({'since': since, 't': t, 'n': [a, b, c], 'sp': spawned, 'idle': idle_since}))\n"))
        self.assertEqual(out["since"], out["t"] + 10, out)
        self.assertEqual(out["n"], [0, 1, 1], "忙 30 分才起、起過的 3 分鐘內不再起:%r" % out)
        self.assertEqual(out["sp"][0], [SEAT, "claude"], out)
        self.assertIsNone(out["idle"], "閒下來 busy_since 歸零:%r" % out)

    def test_agent_lock_blocks(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "tick(t + 10); TA.agent_lock(%r, 'agent:%s/tri-1', env=env, now=t + B - 60)\n"
            "tick(t + 10 + B); print(json.dumps({'n': len(spawned)}))\n") % (SEAT, SEAT))
        self.assertEqual(out["n"], 0, out)


class H2Engine(unittest.TestCase):
    def test_digest_and_unknown_engines_skip(self):
        out = HN.QBox(self).py(SETUP + (
            "for s, p in (('fghij', 'deepseek'), ('klmno', 'grok')):\n"
            "    put('第一件', t, seat=s, provider=p); put('第二件', t, seat=s, provider=p)\n"
            "tick(t + 10); tick(t + 10 + B)\n"
            "print(json.dumps({'sp': spawned}))\n"))
        self.assertEqual(out["sp"], [], out)


class H3Load(unittest.TestCase):
    def test_high_load_skips(self):
        out = HN.QBox(self).py(SETUP + (
            "put('第一件:請看 PR 頁面上的說明', t); put('第二件:另一個題目要你決定', t)\n"
            "Q.LOAD_FN = lambda: (13.0, 0.0, 0.0); tick(t + 10); tick(t + 10 + B)\n"
            "print(json.dumps({'n': len(spawned)}))\n"))
        self.assertEqual(out["n"], 0, out)


APPLY = SETUP + (
    "import os\n"
    "a = put('一:PR 已合的那件', t); b = put('二:PR 還開著', t); c = put('三:檔案在外面', t)\n"
    "d = put('四:檔案在 pending', t); e = put('五:要等 @abb89', t); f = put('六:要回信', t)\n"
    "g = put('七:回過信了', t); w = put('八:已寫等什麼', t)\n"
    "Q.mark_read(%(s)r, w, 'seat:%(s)s', env=env, now=t, no_rev_check=True)\n"
    "import dm_take as DT; DT.set_wait(%(s)r, w, 'seat:%(s)s', '等 Andy', env=env, now=t)\n"
    "h = Q.record(%(s)r, '九:等真人', '@77777', status=Q.HOLD_STATUS, rc=None, env=env, now=t, sender_kind='ai')['id']\n"
    "pend = H.pending_dir(env); pend.mkdir(parents=True, exist_ok=True); pf = pend / 'note.md'; pf.write_text('x')\n"
    "reply = Q.record('fghij', '回覆', '@%(s)s', status='delivered', rc=0, env=env, now=t, sender_kind='seat',\n"
    "                 sender_seat=%(s)r, sender_source='pane_pid_chain')['id']\n"
    "M = 'https://github.com/ez2app/ez2-aib/pull/101'; O = 'https://github.com/ez2app/ez2-aib/pull/102'\n"
    "dec = [{'id': a, 'action': 'close', 'evidence': M}, {'id': b, 'action': 'close', 'evidence': O},\n"
    "       {'id': c, 'action': 'close', 'evidence': '/etc/hosts'}, {'id': d, 'action': 'close', 'evidence': str(pf)},\n"
    "       {'id': e, 'action': 'wait', 'wait': '等 @abb89 回'}, {'id': f, 'action': 'draft', 'draft': '好,我明天補'},\n"
    "       {'id': g, 'action': 'close', 'evidence': reply}, {'id': w, 'action': 'close', 'evidence': M},\n"
    "       {'id': h, 'action': 'close', 'evidence': M}, {'id': 'q_0000000000000000', 'action': 'close', 'evidence': M}]\n"
    "dec = [dict(x, evidence=x.get('evidence', ''), wait=x.get('wait', ''), draft=x.get('draft', ''), why='') for x in dec]\n"
    "res = H.apply(%(s)r, dec, 'agent:%(s)s/headless-1', env=env, now=t + 100, gh=lambda u: u == M)\n"
    "st = {k: Q._load_item(%(s)r, v, env) for k, v in dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g, w=w, h=h).items()}\n"
    "print(json.dumps({'res': {r['id']: [r['action'], r['applied'], r.get('reason')] for r in res},\n"
    "                  'ids': dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g, w=w, h=h),\n"
    "                  'st': {k: v.get('status') for k, v in st.items()}, 'ewait': (st['e'].get('wait') or {}).get('text')}))\n"
) % {"s": SEAT}


class H4Apply(unittest.TestCase):
    def test_each_decision_verified(self):
        out = HN.QBox(self).py(APPLY)
        r, ids, st = out["res"], out["ids"], out["st"]
        self.assertEqual(r[ids["a"]][:2], ["close", True], out)
        self.assertEqual(r[ids["b"]], ["leave", False, "pr-not-merged"], "PR 沒合 ⇒ leave、不降級:%r" % out)
        self.assertEqual(r[ids["c"]], ["leave", False, "file-outside-pending"], out)
        self.assertEqual(r[ids["d"]][:2], ["close", True], out)
        self.assertEqual(r[ids["g"]][:2], ["close", True], "本席寄出的回信 id 可結:%r" % out)
        self.assertEqual(r[ids["e"]][:2], ["wait", True], out)
        self.assertEqual(out["ewait"], "等 @abb89 回")
        self.assertEqual(r[ids["f"]], ["draft", False, "no-change"], out)
        for k in ("w", "h"):
            self.assertEqual(r[ids[k]][2], "not-allowed", "%s 不准動:%r" % (k, out))
        self.assertEqual(r["q_0000000000000000"][2], "not-allowed")
        self.assertEqual((st["a"], st["d"], st["g"], st["b"], st["c"], st["w"], st["h"]),
                         ("done", "done", "done", "delivered", "delivered", "read", "hold_human"), out)


class H5EndToEnd(unittest.TestCase):
    def test_run_writes_report_and_releases(self):
        out = HN.QBox(self).py(SETUP + (
            "a = put('一:PR 已合的那件', t); b = put('二:要回信', t)\n"
            "M = 'https://github.com/ez2app/ez2-aib/pull/101'\n"
            "def ask(brand, prompt):\n"
            "    assert a in prompt and '一:PR 已合的那件' in prompt\n"
            "    return ('好的,結果如下 ' + json.dumps([{'id': a, 'action': 'close', 'evidence': M, 'why': '已合'},\n"
            "            {'id': b, 'action': 'draft', 'draft': '收到,明天回'}]) + ' 以上'), 'ok'\n"
            "r = H.run(%r, 'claude', env=env, now=t + 100, ask=ask, gh=lambda u: True)\n"
            "lk = Q._triage_lock(%r, env)\n"
            "body = open(r['file'], encoding='utf-8').read()\n"
            "closed = TA.agent_closed(%r, env=env, now=t + 200)\n"
            "print(json.dumps({'ok': r['ok'], 'body': body, 'lk': lk, 'a': a, 'b': b, 'closed': closed}))\n")
            % (SEAT, SEAT, SEAT))
        self.assertTrue(out["ok"], out)
        self.assertIn("草稿:收到,明天回", out["body"])
        self.assertIn("沒有寄出", out["body"])
        self.assertEqual((out["lk"]["until"], out["lk"]["handled_ids"]), (0, [out["b"]]), out)
        self.assertEqual([c["id"] for c in out["closed"]], [out["a"]], out)
        self.assertTrue(out["closed"][0]["by"].startswith("agent:%s/headless-" % SEAT), out)


class H6ToolsOff(unittest.TestCase):
    def test_claude_locked_cmd_and_codex_refused(self):
        out = HN.QBox(self).py(SETUP + (
            "import power_ask as PA, subprocess\n"
            "PA._light = lambda brand: 'green'\n"
            "calls = []\n"
            "def run(cmd, **kw):\n"
            "    calls.append(cmd)\n"
            "    if cmd[:1] == ['codex'] and 'mcp' in cmd:\n"
            "        return subprocess.CompletedProcess(cmd, 0, json.dumps([{'name': 'evil', 'enabled': True}]), '')\n"
            "    return subprocess.CompletedProcess(cmd, 0, '[]', '')\n"
            "c1 = H.ask_ai('claude', 'p', run=run)\n"
            "c2 = H.ask_ai('codex', 'p', run=run)\n"
            "c3 = H.ask_ai('grok', 'p', run=run)\n"
            "print(json.dumps({'c1': c1, 'c2': c2, 'c3': c3, 'first': calls[0], 'ran_codex_exec': any(\n"
            "    x[:2] == ['codex', 'exec'] for x in calls)}))\n"))
        first = out["first"]
        self.assertEqual(out["c1"], ["[]", "ok"], out)
        i = first.index("--tools")
        self.assertEqual(first[i + 1], "", "claude 一定 --tools \"\":%r" % first)
        for flag in ("--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, first)
        self.assertEqual(out["c2"], [None, "tools-not-disabled"], "codex 關不乾淨 ⇒ 不叫:%r" % out)
        self.assertFalse(out["ran_codex_exec"], out)
        self.assertEqual(out["c3"], [None, "engine-unsupported"])


class H7Gh(unittest.TestCase):
    def test_only_merged_counts(self):
        out = HN.QBox(self).py(SETUP + (
            "import subprocess\n"
            "def fake(state, rc=0):\n"
            "    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, state + '\\n', '')\n"
            "def boom(cmd, **kw):\n"
            "    raise OSError('no gh')\n"
            "u = 'https://github.com/ez2app/ez2-aib/pull/1'\n"
            "print(json.dumps([H.gh_merged(u, run=fake('MERGED')), H.gh_merged(u, run=fake('OPEN')),\n"
            "                  H.gh_merged(u, run=fake('CLOSED')), H.gh_merged(u, run=fake('', 1)), H.gh_merged(u, run=boom)]))\n"))
        self.assertEqual(out, [True, False, False, False, False])


KNIVES = [
    ("K1", "src/cockpit/dm_triage_headless.py", '    allowed = {r["id"]: r for r in plan["rows"] if "excluded" not in r}\n',
     '    allowed = {r["id"]: r for r in plan["rows"]}\n', "H4Apply.test_each_decision_verified"),
    ("K2", "src/cockpit/dm_triage_headless.py", '            ok, why = _evidence_ok(seat, d["evidence"], env, gh)\n',
     '            ok, why = True, "x"\n', "H4Apply.test_each_decision_verified"),
    ("K3", "src/cockpit/dm_triage_headless.py", "            cmd = list(PA.CLAUDE_CMD)\n",
     '            cmd = ["claude", "-p", "--tools", "Bash"]\n', "H6ToolsOff.test_claude_locked_cmd_and_codex_refused"),
    ("K4", "src/cockpit/dm_triage_headless.py", '        return r.returncode == 0 and r.stdout.strip() == "MERGED"\n',
     "        return r.returncode == 0\n", "H7Gh.test_only_merged_counts"),
    ("K5", "src/cockpit/dm_triage_headless.py", "    if busy_for < HEADLESS_AFTER_S:\n", "    if False:\n",
     "H1Trigger.test_busy_30min_starts_once"),
]


class Knife(unittest.TestCase):
    def test_knives(self):
        name = Path(__file__).name
        for tag, rel, needle, repl, target in KNIVES:
            with self.subTest(tag):
                with knife_copy(ROOT, rel, needle, repl, tests=(name, "test-dm-hold-notify-abb89-1015.py",
                                                                "test-dm-reliability-abb89-1015.py")) as root:
                    r = run_guarded([sys.executable, "-B", str(root / "tests" / name), target], cwd=str(root),
                                    max_procs=60, timeout=300)
                self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, "%s 沒砍出紅\n%s" % (tag, out))
                self.assertIn("FAIL: " + target.split(".")[-1], out, "%s 紅的不是目標斷言\n%s" % (tag, out))
                self.assertNotIn("ERROR: ", out, "%s 是炸掉不是斷言紅\n%s" % (tag, out))


if __name__ == "__main__":
    unittest.main()
