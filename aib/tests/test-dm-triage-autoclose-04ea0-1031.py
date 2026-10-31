#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""背景整理可結的四類(SPEC-dm-triage-auto v2 §4;@04ea0)。證據全由核心查,不信模型。
治具沿用 test-dm-hold-notify-abb89-1015.py(抄進暫存根、假 tmux),不起真 AI、不連網、不碰 ~/.ez2-aib。

C1 重複件:前一封已結 + 正文相同(去抬頭 / 空白)⇒ 可結;正文不同 / 前一封沒結 / 前一封比較晚 ⇒ 不收
C2 承接:同 thread + 同寄件席 + 新那封較晚 ⇒ 可結;不同 thread / 不同寄件席 / 新那封較早 ⇒ 不收
C3 自報過期:expires 已過 ⇒ 可結;還沒過 ⇒ 不收(格式保留;PREAMBLE 不列 expired,C3b)
C4 純確認:引用本席真的寄給對方的 q_ id、短、沒請 / 要 / 問號 / 回覆期限 / 檔案路徑 ⇒ 可結;
   引用不是本席寄的、帶問號、帶「前回」、帶路徑、沒引用 ⇒ 不收
C5 共同門檻:長訊信封(要讀 RELAY 全文)/ 真人寄的 / 禁字(Andy、准、High、可合、正式站、簽章…)⇒ 新四類一律不收
C6 PR 網址要同主題:信裡沒提到那張 PR ⇒ pr-off-topic(PR 網址存在 ≠ 內容滿足);提到 #號 或網址 ⇒ 照 gh 查
C7 自己指自己、格式不對 ⇒ 不收
Knife(最後):A1–A27 每個守衛一刀;單跑 python3 -B tests/<本檔> Knife
C8 整條 apply:新四類照常逐條驗;只擬不結時一封都不改;自報過期的信 plan 已排除(not-allowed)
C9 只擬不結一輪 ⇒ 寫通知 + 建議 .json;下一回合 hook 注入一行、只注入一次;沒建議就不寫
C10 triage-apply:不帶 --yes 只預覽;--yes 逐條重驗才結;建議檔席碼不對 / 修訂號變了 / 現在驗不過 ⇒ 不結
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

_spec = importlib.util.spec_from_file_location("dmhold_fixture", HERE / "test-dm-hold-notify-abb89-1015.py")
HN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HN)
SEAT = HN.SEAT
PEER = "77777"


def setUpModule():
    HN.setUpModule()


def tearDownModule():
    HN.tearDownModule()


SETUP = (
    "import dm_triage_headless as H\n"
    "from pathlib import Path\n"
    "t = int(__import__('time').time())\n"
    "S, P = %r, %r\n"
    "def put(msg, now, **kw):\n"
    "    kw.setdefault('sender_kind', 'seat'); kw.setdefault('sender_seat', P); kw.setdefault('sender_source', 'pane_pid_chain')\n"
    "    return Q.record(S, msg, '@' + P, status='delivered', rc=0, env=env, now=now, wid='@2', provider='claude',\n"
    "                    hub='aihub-t1', **kw)['id']\n"
    "def done(i, now):\n"
    "    Q.mark_read(S, i, 'seat:' + S, env=env, now=now, no_rev_check=True)\n"
    "    Q.mark_done(S, i, 'seat:' + S, None, env=env, now=now, no_rev_check=True, why='測試先結')\n"
    "def ev(i, e, gh=lambda u: True):\n"
    "    return list(H._evidence_ok(S, e, env, gh, item=Q._load_item(S, i, env), now=t + 1000))\n"
    "out = {}\n"
) % (SEAT, PEER)


def box(tc, code):
    return HN.QBox(tc).py(SETUP + code + "print(json.dumps(out))\n")


class C1Dup(unittest.TestCase):
    def test_dup(self):
        out = box(self, (
            "a = put('第一版:部署排程表已更新,見頁面', t); done(a, t + 5)\n"
            "b = put('第一版:部署排程表已更新,  見頁面', t + 10)\n"
            "c = put('第二版:部署排程表改了別的', t + 10)\n"
            "d = put('沒結的那封', t); e = put('沒結的那封', t + 10)\n"
            "f = put('比較晚才結', t + 50); done(f, t + 60); g = put('比較晚才結', t + 20)\n"
            "out['ok'] = ev(b, 'dup_of:' + a); out['differs'] = ev(c, 'dup_of:' + a)\n"
            "out['open'] = ev(e, 'dup_of:' + d); out['newer'] = ev(g, 'dup_of:' + f)\n"
            "out['missing'] = ev(b, 'dup_of:q_0000000000000000')\n"))
        self.assertEqual(out["ok"], [True, "dup"], out)
        self.assertEqual(out["differs"], [False, "dup-differs"], out)
        self.assertEqual(out["open"], [False, "dup-not-closed"], out)
        self.assertEqual(out["newer"], [False, "dup-newer"], out)
        self.assertEqual(out["missing"], [False, "ref-missing"], out)


class C2Superseded(unittest.TestCase):
    def test_superseded(self):
        out = box(self, (
            "a = put('進度:第一輪', t, thread='build/1'); b = put('進度:第二輪', t + 30, thread='build/1')\n"
            "c = put('進度:別的題', t + 30, thread='build/2')\n"
            "d = put('進度:別人寄的', t + 30, thread='build/1', sender_seat='66666')\n"
            "e = put('進度:更早', t - 30, thread='build/1')\n"
            "n = put('沒 thread', t); n2 = put('沒 thread 新', t + 30)\n"
            "q = Q.record(S, '進度:待真人審', '@' + P, status=Q.HOLD_STATUS, rc=3, env=env, now=t + 40, sender_kind='seat',\n"
            "             sender_seat=P, sender_source='pane_pid_chain', thread='build/1')['id']\n"
            "u = put('進度:自報寄件席', t + 30, thread='build/1', sender_source=None)\n"
            "out['queued'] = ev(a, 'superseded_by:' + q); out['unmeasured'] = ev(a, 'superseded_by:' + u)\n"
            "out['ok'] = ev(a, 'superseded_by:' + b); out['thread'] = ev(a, 'superseded_by:' + c)\n"
            "out['sender'] = ev(a, 'superseded_by:' + d); out['older'] = ev(a, 'superseded_by:' + e)\n"
            "out['nothread'] = ev(n, 'superseded_by:' + n2)\n"))
        self.assertEqual(out["ok"], [True, "superseded"], out)
        self.assertEqual(out["thread"], [False, "thread-differs"], out)
        self.assertEqual(out["sender"], [False, "sender-differs"], out)
        self.assertEqual(out["older"], [False, "not-newer"], out)
        self.assertEqual(out["nothread"], [False, "thread-differs"], out)
        self.assertEqual(out["queued"], [False, "ref-not-reached"], "新那封卡在待真人審(沒到本席)不算承接:%r" % out)
        self.assertEqual(out["unmeasured"], [False, "sender-differs"], "寄件席沒量到(自報)不算同寄件席:%r" % out)


class C3Expired(unittest.TestCase):
    def test_expired(self):
        out = box(self, (
            "a = put('提醒:週六維護', t, expires=t + 500); b = put('提醒:週日維護', t, expires=t + 5000)\n"
            "out['ok'] = ev(a, 'expired'); out['not'] = ev(b, 'expired')\n"))
        self.assertEqual(out["ok"], [True, "expired"], out)
        self.assertEqual(out["not"], [False, "not-expired"], out)


class C4Ack(unittest.TestCase):
    def test_ack(self):
        out = box(self, (
            "mine = Q.record(P, '頭碼 abc 已推', '@' + S, status='delivered', rc=0, env=env, now=t, sender_kind='seat',\n"
            "                sender_seat=S, sender_source='pane_pid_chain')['id']\n"
            "other = Q.record(P, '別人寄的', '@66666', status='delivered', rc=0, env=env, now=t, sender_kind='seat',\n"
            "                 sender_seat='66666', sender_source='pane_pid_chain')['id']\n"
            "a = put('收到 ' + mine + ',已派審', t + 10)\n"
            "b = put('收到 ' + other + ',已派審', t + 10)\n"
            "c = put('收到 ' + mine + ',頭碼對嗎?', t + 10)\n"
            "d = put('收到 ' + mine + ',22:00 前回', t + 10)\n"
            "e = put('收到 ' + mine + ',見 ~/x/notes.md', t + 10)\n"
            "f = put('收到,已派審', t + 10)\n"
            "g = put('收到 ' + mine + ',請再補一張', t + 10)\n"
            "z = put('收到 ' + mine + ',已放 /var/data/archive', t + 10)\n"
            "z2 = put('收到 ' + mine + ',已放 archive.zip', t + 10)\n"
            "qd = Q.record(P, '卡在對方待審', '@' + S, status=Q.HOLD_STATUS, rc=3, env=env, now=t, sender_kind='seat',\n"
            "              sender_seat=S, sender_source='pane_pid_chain')['id']\n"
            "y = put('收到 ' + qd + ',已派審', t + 10)\n"
            "out['zip'] = ev(z, 'ack_of:' + mine); out['zip2'] = ev(z2, 'ack_of:' + mine); out['queued'] = ev(y, 'ack_of:' + qd)\n"
            "out['ok'] = ev(a, 'ack_of:' + mine); out['notours'] = ev(b, 'ack_of:' + other)\n"
            "out['q'] = ev(c, 'ack_of:' + mine); out['due'] = ev(d, 'ack_of:' + mine)\n"
            "out['path'] = ev(e, 'ack_of:' + mine); out['nocite'] = ev(f, 'ack_of:' + mine)\n"
            "out['ask'] = ev(g, 'ack_of:' + mine)\n"))
        self.assertEqual(out["ok"], [True, "ack"], out)
        self.assertEqual(out["notours"], [False, "ack-not-ours"], out)
        for k in ("path", "ask", "zip", "zip2"):
            self.assertEqual(out[k], [False, "ack-not-pure"], (k, out))
        for k in ("q", "due"):                       # 問號 / 「前回」現在在共用的絕不碰就擋下
            self.assertEqual(out[k], [False, "never-words"], (k, out))
        self.assertEqual(out["queued"], [False, "ref-not-reached"], "本席那封還沒送到對方:%r" % out)
        self.assertEqual(out["nocite"], [False, "ack-not-cited"], out)


class C5Never(unittest.TestCase):
    def test_gates(self):
        out = box(self, (
            "a = put('提醒:週六維護', t, expires=t + 500)\n"
            "r = put('長訊已落檔(800 位元組,來自 @77777),請讀:/tmp/x/RELAY-dm-aaaa-1031-120000-1.txt', t, expires=t + 500)\n"
            "h = put('提醒:週六維護', t, expires=t + 500, sender_kind='human', human_id='hub')\n"
            "res = {}\n"
            "for w in ('Andy 說可以', '已准', 'Codex High 一條', '可合', '正式站換版', '簽章時段', 'PyPI 核准',\n"
            "          '能完成嗎?', '這樣可以嗎', '今晚 23:00 前交付', '22:30 前回', 'Codex 審查結果 Low 一條', '維護者說是，已核可',\n"
            "          'approved', '截止 10 點', 'deadline tonight'):\n"
            "    res[w] = ev(put('提醒:' + w, t, expires=t + 500), 'expired')\n"
            "out['ok'] = ev(a, 'expired'); out['relay'] = ev(r, 'expired'); out['human'] = ev(h, 'expired')\n"
            "out['never'] = res\n"
            "xc = put('提醒:週六維護', t, expires=t + 500, sender_hub='aihub-other')\n"
            "un = put('提醒:週六維護', t, expires=t + 500, sender_kind='unknown')\n"
            "dc = put('提醒:週六維護', t, expires=t + 500, kind='decide')\n"
            "out['cross'] = ev(xc, 'expired'); out['unknown'] = ev(un, 'expired'); out['decide'] = ev(dc, 'expired')\n"
            "out['flow'] = ev(put('提醒:allow 與 flow 照常、週六維護', t, expires=t + 500), 'expired')\n"))
        self.assertEqual(out["ok"], [True, "expired"], out)
        self.assertEqual(out["relay"], [False, "relay-unread"], "長訊讀不到全文 ⇒ 不結(不拿信封去驗):%r" % out)
        self.assertEqual(out["cross"], [False, "cross-cabin"], out)
        self.assertEqual(out["unknown"], [False, "sender-not-seat"], out)
        self.assertEqual(out["decide"], [False, "kind"], out)
        self.assertEqual(out["human"], [False, "human"], out)
        for w, r in out["never"].items():
            self.assertEqual(r, [False, "never-words"], w)
        self.assertEqual(out["flow"], [True, "expired"], "allow / flow 裡的 low 不是審查結果 Low(英文字前後不黏字母)")


class C6PrTopic(unittest.TestCase):
    def test_pr_topic(self):
        out = box(self, (
            "M = 'https://github.com/ez2app/ez2-aib/pull/101'\n"
            "a = put('PR #101 合了', t); b = put('另一件事', t); c = put('見 ' + M, t); d = put('PR #1010 合了', t)\n"
            "out['ok'] = ev(a, M); out['off'] = ev(b, M); out['url'] = ev(c, M); out['prefix'] = ev(d, M)\n"
            "out['open'] = ev(a, M, gh=lambda u: False)\n"
            "e = put('見 https://github.com/ez2app/ez2-aib/pull/1010', t)\n"
            "f = put('見 https://github.com/ez2app/other-repo/pull/101', t)\n"
            "g = put('Andy 同意正式站刪資料,PR #101', t)\n"
            "out['url1010'] = ev(e, M); out['repo'] = ev(f, M); out['h1'] = ev(g, M)\n"))
        self.assertEqual(out["ok"], [True, "pr-merged"], out)
        self.assertEqual(out["off"], [False, "pr-off-topic"], out)
        self.assertEqual(out["url"], [True, "pr-merged"], out)
        self.assertEqual(out["prefix"], [False, "pr-off-topic"], "#1010 不是 #101:%r" % out)
        self.assertEqual(out["open"], [False, "pr-not-merged"], out)
        self.assertEqual(out["url1010"], [False, "pr-off-topic"], "pull/1010 不是 pull/101:%r" % out)
        self.assertEqual(out["repo"], [False, "pr-off-topic"], "別的 repo 的 101:%r" % out)
        self.assertEqual(out["h1"], [False, "never-words"], "已合 PR 也要先過絕不碰:%r" % out)


class C3bPreamble(unittest.TestCase):
    def test_preamble_has_no_expired(self):
        """@34d15 10-31 23:11:expired 一定被 plan 排除,不要讓模型產出一定被拒的建議。"""
        import importlib
        sys.path.insert(0, str(ROOT / "src" / "cockpit"))
        H = importlib.import_module("dm_triage_headless")
        self.assertNotIn('"expired"', H.PREAMBLE)
        for kind in ("dup_of:", "superseded_by:", "ack_of:", "/pull/"):
            self.assertIn(kind, H.PREAMBLE)


class C7Format(unittest.TestCase):
    def test_format(self):
        out = box(self, (
            "a = put('提醒:週六維護', t, expires=t + 500)\n"
            "out['self'] = ev(a, 'dup_of:' + a); out['bad'] = ev(a, 'dup:' + a); out['sha'] = ev(a, 'abc1234')\n"))
        self.assertEqual(out["self"], [False, "self-ref"], out)
        self.assertEqual(out["bad"], [False, "only-merged-pr"], out)
        self.assertEqual(out["sha"], [False, "only-merged-pr"], out)


class C8Apply(unittest.TestCase):
    def test_apply_dry_and_real(self):
        out = box(self, (
            "a = put('部署排程表已更新', t); done(a, t + 5); b = put('部署排程表已更新', t + 10)\n"
            "c = put('部署排程表改了別的', t + 10)\n"
            "x = put('提醒:週六維護', t, expires=t + 500)\n"
            "dec = [{'id': b, 'action': 'close', 'evidence': 'dup_of:' + a, 'wait': '', 'draft': '', 'why': ''},\n"
            "       {'id': c, 'action': 'close', 'evidence': 'dup_of:' + a, 'wait': '', 'draft': '', 'why': ''},\n"
            "       {'id': x, 'action': 'close', 'evidence': 'expired', 'wait': '', 'draft': '', 'why': ''}]\n"
            "dry = H.apply(S, dec, 'agent:%s/headless-1' % S, env=env, now=t + 1000, dry=True)\n"
            "out['dry'] = [[bool(r.get('would')), r['applied'], r.get('reason')] for r in dry]\n"
            "out['st_dry'] = (Q._load_item(S, b, env) or {}).get('status')\n"
            "real = H.apply(S, dec, 'agent:%s/headless-1' % S, env=env, now=t + 1000)\n"
            "out['real'] = [[r['applied'], r.get('reason')] for r in real]\n"
            "it = Q._load_item(S, b, env) or {}\n"
            "out['st'] = [it.get('status'), str(it.get('done_why') or '')]\n"
            "out['st_c'] = (Q._load_item(S, c, env) or {}).get('status')\n"))
        self.assertEqual(out["dry"][0], [True, False, "dup"], out)
        self.assertEqual(out["dry"][1], [False, False, "dup-differs"], out)
        # 自報過期的信 plan 本來就排除(不計數、只給艦長室)⇒ 背景整理碰不到
        self.assertEqual(out["dry"][2], [False, False, "not-allowed"], out)
        self.assertEqual(out["st_dry"], "delivered", "只擬不結一封都不改")
        self.assertEqual(out["real"], [[True, "dup"], [False, "dup-differs"], [False, "not-allowed"]], out)
        self.assertEqual(out["st"][0], "done", out)
        self.assertIn("背景整理驗過", out["st"][1], out)
        self.assertEqual(out["st_c"], "delivered", out)


RUN_DRY = (
    "import io, contextlib, inbox_brief_hook as IB\n"
    "a = put('部署排程表已更新', t); done(a, t + 5); b = put('部署排程表已更新', t + 10)\n"
    "c = put('另一件要做的事', t + 10)\n"
    "def ask(brand, prompt):\n"
    "    return json.dumps([{'id': b, 'action': 'close', 'evidence': 'dup_of:' + a, 'why': '同一封'},\n"
    "                       {'id': c, 'action': 'close', 'evidence': 'dup_of:' + a, 'why': '亂結'}]), 'ok'\n"
    "r = H.run(S, 'claude', env=env, now=t + 1000, ask=ask, gh=lambda u: False, dry=True)\n"
    "def hook():\n"
    "    buf = io.StringIO()\n"
    "    with contextlib.redirect_stdout(buf):\n"
    "        IB.main(stdin=io.StringIO(json.dumps({'session_id': 'x-' + S})), env=env)\n"
    "    return [l for l in buf.getvalue().splitlines() if '背景整理' in l]\n"
)


class C9Notice(unittest.TestCase):
    def test_dry_round_notice_once(self):
        out = box(self, RUN_DRY + (
            "out['r'] = [r['ok'], r['dry'], [x.get('would', False) for x in r['results']]]\n"
            "sug = H.suggest_path(r['file'])\n"
            "out['sug'] = [x['id'] == b for x in json.loads(sug.read_text())['items']]\n"
            "out['st'] = [(Q._load_item(S, i, env) or {}).get('status') for i in (b, c)]\n"
            "out['h1'] = hook(); out['h2'] = hook(); out['b'] = b; out['c'] = c\n"))
        self.assertEqual(out["r"], [True, True, [True, False]], out)
        self.assertEqual(out["sug"], [True], "建議檔只列驗得過的那封")
        self.assertEqual(out["st"], ["delivered", "delivered"], "只擬不結")
        self.assertEqual(len(out["h1"]), 1, out)
        self.assertIn(out["b"], out["h1"][0])
        self.assertNotIn(out["c"], out["h1"][0])
        self.assertIn("triage-apply", out["h1"][0])
        self.assertEqual(out["h2"], [], "同一份只注入一次")

    def test_nothing_to_suggest_no_notice(self):
        out = box(self, (
            "import io, contextlib, inbox_brief_hook as IB\n"
            "c = put('另一件要做的事', t)\n"
            "r = H.run(S, 'claude', env=env, now=t + 1000, ask=lambda b, p: (json.dumps([{'id': c, 'action': 'leave'}]), 'ok'),\n"
            "          gh=lambda u: False, dry=True)\n"
            "out['notice'] = (Q.seat_dir(S, env) / H.NOTICE_FILE).exists()\n"
            "out['sug'] = H.suggest_path(r['file']).exists()\n"))
        self.assertEqual(out, {"notice": False, "sug": False})


class C10TriageApply(unittest.TestCase):
    def test_apply_cli(self):
        out = box(self, RUN_DRY + (
            "f = str(H.suggest_path(r['file']))\n"
            "pv = H.triage_apply(S, f, yes=False, env=env, now=t + 1100)\n"
            "out['pv'] = [pv['dry'], [[x.get('would', False), x['applied']] for x in pv['results']]]\n"
            "out['st_pv'] = (Q._load_item(S, b, env) or {}).get('status')\n"
            "out['other'] = H.triage_apply(P, f, yes=True, env=env, now=t + 1100)['reason']\n"
            "yes = H.triage_apply(S, f, yes=True, env=env, now=t + 1100)\n"
            "out['yes'] = [[x['applied'], x.get('reason')] for x in yes['results']]\n"
            "out['st'] = (Q._load_item(S, b, env) or {}).get('status')\n"
            "out['again'] = [[x['applied'], x.get('reason')] for x in H.triage_apply(S, f, yes=True, env=env, now=t + 1200)['results']]\n"
            # 修訂號變了:另一輪建議,建議之後內容被改(rev +1)⇒ 不結
            "d = put('又一封部署排程表已更新', t + 20); e = put('又一封部署排程表已更新', t + 30); done(d, t + 25)\n"
            "sp = Path(f).with_name('x.json')\n"
            "sp.write_text(json.dumps({'schema': H.SUGGEST_SCHEMA, 'seat': S, 'at': t, 'items': [{'id': e, 'evidence': 'dup_of:' + d, 'rev': 1}]}))\n"
            "it = dict(Q._load_item(S, e, env)); it['rev'] = 2; it['msg'] = it['msg'] + '(改)'\n"
            "Q.item_path(S, e, env).write_text(json.dumps(it))\n"
            "out['revchg'] = [[x['applied'], x.get('reason')] for x in H.triage_apply(S, str(sp), yes=True, env=env, now=t + 1300)['results']]\n"
            "out['bad'] = H.triage_apply(S, str(Path(f).with_name('nope.json')), yes=True, env=env)['reason']\n"
            "g1 = put('第三封部署排程表已更新', t + 40); g2 = put('第三封部署排程表已更新', t + 50); done(g1, t + 45)\n"
            "nr = Path(f).with_name('norev.json')\n"
            "nr.write_text(json.dumps({'schema': H.SUGGEST_SCHEMA, 'seat': S, 'at': t, 'items': [\n"
            "    {'id': g2, 'evidence': 'dup_of:' + g1}, {'id': g2, 'evidence': 'dup_of:' + g1, 'rev': '1'}]}))\n"
            "out['norev'] = [[x['applied'], x.get('reason')] for x in H.triage_apply(S, str(nr), yes=True, env=env, now=t + 1400)['results']]\n"
            "out['st_norev'] = (Q._load_item(S, g2, env) or {}).get('status')\n"))
        self.assertEqual(out["pv"], [True, [[True, False]]], out)
        self.assertEqual(out["st_pv"], "delivered", "預覽不改")
        self.assertEqual(out["other"], "seat-mismatch", out)
        self.assertEqual(out["yes"], [[True, "dup"]], out)
        self.assertEqual(out["st"], "done", out)
        self.assertEqual(out["again"][0][0], False, "已結的不再結:%r" % out)
        self.assertEqual(out["revchg"], [[False, "rev-changed"]], out)
        self.assertEqual(out["bad"], "unreadable", out)
        self.assertEqual(out["norev"], [[False, "rev-missing"], [False, "rev-missing"]], "缺 rev / rev 不是整數 ⇒ 那一條不套用:%r" % out)
        self.assertEqual(out["st_norev"], "delivered", out)


KNIVES = [
    ('A1', 'src/cockpit/dm_triage_headless.py', '    if other.get("status") not in ("done", "archived"):\n        return False, "dup-not-closed"',
     '    if False:\n        return False, "dup-not-closed"', 'C1Dup.test_dup'),
    ('A2', 'src/cockpit/dm_triage_headless.py', '        if int(other.get("ts") or 0) > int(item.get("ts") or 0):\n            return False, "dup-newer"',
     '        if False:\n            return False, "dup-newer"', 'C1Dup.test_dup'),
    ('A3', 'src/cockpit/dm_triage_headless.py', '    return (True, "dup") if a and a == b else (False, "dup-differs")',
     '    return (True, "dup")', 'C1Dup.test_dup'),
    ('A4', 'src/cockpit/dm_triage_headless.py', '    if not th or other.get("thread") != th:',
     '    if False:', 'C2Superseded.test_superseded'),
    ('A5', 'src/cockpit/dm_triage_headless.py', '    if a is None or a != b:\n        return False, "sender-differs"',
     '    if False:\n        return False, "sender-differs"', 'C2Superseded.test_superseded'),
    ('A6', 'src/cockpit/dm_triage_headless.py', '    if not sent or Q._sender_key(sent) != ("seat", seat):',
     '    if not sent:', 'C4Ack.test_ack'),
    ('A7', 'src/cockpit/dm_triage_headless.py', '    if len(text or "") > ACK_MAX or _ACK_BAD_RE.search(text or ""):',
     '    if False:', 'C4Ack.test_ack'),
    ('A8', 'src/cockpit/dm_triage_headless.py', '    return t if isinstance(t, str) else None',
     '    return t if isinstance(t, str) else str(item.get("msg") or "")', 'C5Never.test_gates'),
    ('A9', 'src/cockpit/dm_triage_headless.py', '    if item.get("sender_kind") == "human" or item.get("status") == Q.HOLD_STATUS:',
     '    if False:', 'C5Never.test_gates'),
    ('A10', 'src/cockpit/dm_triage_headless.py', '    if _NEVER_RE.search(text) or _NEVER_RE.search(str(item.get("msg") or "")):',
     '    if False:', 'C5Never.test_gates'),
    ('A11', 'src/cockpit/dm_triage_headless.py', '        os.replace(p, taken)\n',
     '        __import__("shutil").copy(p, taken)\n', 'C9Notice.test_dry_round_notice_once'),
    ('A12', 'src/cockpit/dm_triage_headless.py', '    if rec.get("seat") != seat:\n        return {"ok": False, "reason": "seat-mismatch"}',
     '    if False:\n        return {"ok": False, "reason": "seat-mismatch"}', 'C10TriageApply.test_apply_cli'),
    ('A13', 'src/cockpit/dm_triage_headless.py', 'revs={i["id"]: i["rev"] for i in good}, dry=not yes',
     'revs=None, dry=not yes', 'C10TriageApply.test_apply_cli'),
    ('A14', 'src/cockpit/dm_triage_headless.py', 'revs={i["id"]: i["rev"] for i in good}, dry=not yes',
     'revs={i["id"]: i["rev"] for i in good}, dry=False', 'C10TriageApply.test_apply_cli'),
    ('A15', 'src/cockpit/dm_triage_headless.py', '    if not rows:\n        return None\n    if dry:',
     '    if False:\n        return None\n    if dry:', 'C9Notice.test_nothing_to_suggest_no_notice'),
    ('A16', 'src/cockpit/dm_triage_headless.py', '        if not _mentions_pr(text, ev, m.group(1)):',
     '        if False:', 'C6PrTopic.test_pr_topic'),
    ('A17', 'src/cockpit/dm_triage_headless.py', '(?<![0-9])#%s(?![0-9])',
     '(?<![0-9])#%s', 'C6PrTopic.test_pr_topic'),
    ('A18', 'src/cockpit/dm_triage_headless.py', '  * it only acknowledges a message',
     '  * the sender\'s own expiry time has passed: "expired"\n  * it only acknowledges a message', 'C3bPreamble.test_preamble_has_no_expired'),
    ('A19', 'src/cockpit/dm_triage_headless.py', '    ok, why = _never_touch(item, text)\n    if not ok:\n        return False, why\n',
     '', 'C6PrTopic.test_pr_topic'),
    ('A20', 'src/cockpit/dm_triage_headless.py', '    if sh and rh and sh != rh:',
     '    if False:', 'C5Never.test_gates'),
    ('A21', 'src/cockpit/dm_triage_headless.py', '    if Q.normalize_sender_kind(item.get("sender_kind")) != "seat" or not item.get("sender_seat"):',
     '    if False:', 'C5Never.test_gates'),
    ('A22', 'src/cockpit/dm_triage_headless.py', '    if other.get("status") not in _REACHED:\n        return False, "ref-not-reached"',
     '    if False:\n        return False, "ref-not-reached"', 'C2Superseded.test_superseded'),
    ('A23', 'src/cockpit/dm_triage_headless.py', '    if sent.get("status") not in _REACHED:',
     '    if False:', 'C4Ack.test_ack'),
    ('A24', 'src/cockpit/dm_triage_headless.py', '    if want and any((m.group(1), m.group(2)) == (want.group(1), want.group(2)) for m in _PULL_ANY_RE.finditer(t)):',
     '    if url.rstrip("/") in t:', 'C6PrTopic.test_pr_topic'),
    ('A25', 'src/cockpit/dm_triage_headless.py', '    bad = [i for i in items if type(i.get("rev")) is not int]\n    good = [i for i in items if type(i.get("rev")) is int]',
     '    bad = []\n    good = items', 'C10TriageApply.test_apply_cli'),
    ('A26', 'src/cockpit/dm_triage_headless.py', 'r"請|要|[/\\\\~]|',
     'r"請|要|', 'C4Ack.test_ack'),
    ('A27', 'src/cockpit/dm_triage_headless.py', 'r"Codex|審查|審過|" + _A + r"(?:review|High|Medium|Low)" + _Z + r"|',
     'r"Codex|審查|審過|(?:review|High|Medium|Low)|', 'C5Never.test_gates'),
]


class Knife(unittest.TestCase):
    """每個守衛一刀,落在樹的複本(knife_copy)、看門狗跑(run_guarded),只跑目標格,要那一格 FAIL(ERROR 不算)。
    單跑:python3 -B tests/test-dm-triage-autoclose-04ea0-1031.py Knife"""
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
