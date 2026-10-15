#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib handoff(對話用量到 85% 或說「準備 clear」必做三步交接,全部 AI、不可跳過)。

  H1 collect(Python):沙盒 git 倉 + 假 origin + worktree(領先 2、未提交 1)+ 假租約檔 ⇒ 草稿有三段待填標題、
     main vs origin/main +1/-0、worktree 一列、沒合的分支、租約讀的是假檔。
  H2 write 驗證:三段空的(只有待填字)⇒ 拒;只填第一段 ⇒ 第 1 步過、第 2 步拒、第 3 步拒(順序);全拒的時候什麼都不寫。
  H3 write 四樣都寫:第二大腦(aib_memory.write 被叫、kind=handoff,每一步一次)、席位記憶三段、交接檔、收據
     {seat, session_uuid, ts, file, sha}(sha 對得上檔)。
  H4 入口格(生產頂 `bash src/cockpit/aib handoff`,子行程):collect → 空稿 write rc 2 → 填好 → 1、2、3 → status。
  H5 status:上一輪有收據 / 這一輪沒有。
  H6 入口格(生產頂 persona_reinject_hook.py,Claude 的 SessionStart hook,子行程):/clear 時上一輪沒收據 ⇒
     第一行是「[AIB 交接缺漏]」、自動補抓檔在 pending/、缺漏旗標在;AIB_PERSONA_REINJECT=0 一樣有(不能被關掉)。
     對照臂:上一輪有收據 ⇒ 沒有提醒、沒有旗標。
  H7 入口格(生產頂 seat_onboard.py --prepare-self-fetch,codex 席,子行程):席位檔 forked_from 指向沒交接的前身 ⇒
     開席單後面接提醒。對照臂:前身有收據 ⇒ 沒有。
  H8 走 dm 的家(copilot,onboard_one):提醒也在,整則一行、過 R-30 出站檢查。
  H9 入口格(生產頂 seat_load.py apply,子行程):載入沒交接的舊席 ⇒ handoff_check=ok、開場說明最後一段是提醒;
     橋接席(grokbot)⇒ not_inserted。
  H10 規則那一行在:開席單鐵則段、人格包寫回段、API 席系統說明(四個語系)、nvidia 席的系統說明。
  M2 (r17) SessionStart hook 毫秒級:git 卡 20 秒(PATH 上假 git)hook 仍 < 1 秒、基因先印、補抓在背景寫出 AUTO 檔;
     collect 守總時限(生產頂 aib handoff collect);每一支 git 都帶 GIT_OPTIONAL_LOCKS=0(記帳 git)。
  E1 第一次跑寫 enabled_at、啟用前開始的一輪不抓;E2 連按兩次 /clear(對話檔沒有真人訊息)不抓,對照臂講過話就抓;
  E3 前一席是 API 席 ⇒ 提醒請真人代跑(生產頂 seat_load apply)。
  M1 (r17) 收據騙不過:沒給 --step 拒;太短/兩段一樣/機器事實段被刪 拒;草稿頭是別席 拒;不是這一輪最後一次 collect
     或是 AUTO 檔 拒;寫完又 collect ⇒ 收據不算、下一輪(生產頂 hook)照樣抓;幫別席寫 ⇒ 提醒 + 收據 seat_mismatch。
  M3 (r17) 交接檔與 AUTO 檔都過真的 tools/dispatch/handoff_fields.py --strict-fixed;生產頂 aib_doctor 的交接單檢查是 ok。
  R18(r18 M1)內容閘:湊字數(同一個字湊到 40 個、或一個字佔一半以上)拒;兩段只差一個字拒;上一輪的三段換新 nonce
     重交拒;機器事實段被改拒;第 3 步的第一段和第 1 步寫進記憶的不一樣拒。全走生產頂 `aib handoff`(子行程),各有對照臂。
  R18(r18 M2)/clear 後 /resume:生產頂 hook 收到 source=resume ⇒ 不開新的一輪、接回的對話檔併進這一輪;
     接回之後真人講過話、沒收據 ⇒ 下一次 /clear 抓到。對話檔找不到 ⇒ 照查(抓)。
  R18 minor:steps.json 壞掉 write 不當掉;AUTO 檔名帶秒;之後一輪交了收據 ⇒ 缺漏旗標收掉、status 不再印。
  R19 內容閘:誠實的「這一輪沒變」(和上一輪很像但改寫過)過;草稿提示字拿掉前綴貼回來拒;第 1 步就擋三段幾乎一樣
     (什麼都還沒寫進記憶);上一輪三段一字不差換 nonce 仍拒(R18 m1c)。
  R19 resume:接回一份舊對話、接回後什麼都沒講就 /clear ⇒ 不抓(舊對話裡的話不算這一輪);接回後真人講話 ⇒ 抓。
  Z* 還原刀:每一層各一把(aib_handoff.py ×2、aib、persona_reinject_hook.py、seat_onboard.py、seat_load.py、
     persona_pack.py、aib_dm_tool.py、i18n/zh-TW.json、docs/templates/SEAT-ONBOARD.md),副本樹落刀 ⇒ 宣稱的格必紅。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aib_tmp_root import aib_tmp_root  # noqa: E402
import aib_cabin_wash as W  # noqa: E402
try:  # 租約落點搬到暫存:有共用治具就用它;沒有就就地搬,不碰真機那把租約
    import aib_host_lease_fixture  # noqa: E402,F401
except ImportError:
    if not os.environ.get("AIB_HOST_LEASE_FILE"):
        _lease_dir = tempfile.mkdtemp(prefix="aib-host-lease-fixture-", dir=aib_tmp_root())
        import atexit
        atexit.register(shutil.rmtree, _lease_dir, True)
        os.environ["AIB_HOST_LEASE_FILE"] = os.path.join(_lease_dir, "host-lease.json")

for _d in ("src/cockpit", "src/identity", "src/memory", "src/messaging"):
    sys.path.insert(0, str(ROOT / _d))
import aib_handoff as H  # noqa: E402

INST = "20200101-000000-zz9q"
SEAT = "00000000-0000-4000-8000-0000000abcde"
NEWSEAT = "00000000-0000-4000-8000-0000000fffff"
NOTICE = "[AIB 交接缺漏]"
# 公開版缺 docs/templates/SEAT-ONBOARD.md(seat_onboard.py 的 TEMPLATE_REL 指著它,開席單 fail-closed)⇒
#   依賴開席單模板的格(H7 / H7b / H7c / H8 / H10)與落在模板上的刀(Z5 / Z10)先跳過並說出來;模板補進來就自動恢復。
ONBOARD_TEMPLATE = ROOT / "docs" / "templates" / "SEAT-ONBOARD.md"
HAS_ONBOARD_TEMPLATE = ONBOARD_TEMPLATE.is_file()
NO_TEMPLATE_WHY = "公開版缺 docs/templates/SEAT-ONBOARD.md(已回報的公開版缺檔;seat_onboard 開席單讀不到模板)"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None):
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=60, env=dict(os.environ, **GIT_ENV))
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout


PADS = {"s1": "補充:這一段寫沒想到的事與建議,包含為什麼這樣建議、不照做會怎樣,給下一個 AI 判斷用。",
        "s2": "補充:這一段寫下一個 AI 不會知道的事,在跑的子代理、工作樹、分支、等誰回覆都列出來。",
        "s3": "補充:這一段是交接文字與我會如何開始,第一步讀交接檔、第二步跑測試、第三步回報真人。"}


# r18 M1 用的「真的內容」:每段兩行、字種多、彼此不像(A 組 = 第一輪,B 組 = 下一輪重寫)。
REAL = {
    "s1A": "我建議把 collect 的總時限做成每一席可設定,現在固定八秒,慢機器常常量不完。\n"
           "不照做的話,倉很多的席位每次只拿到一半的事實,下一個 AI 會誤判主線狀態。",
    "s2A": "下一個 AI 不會知道:背景還有一支子代理在 wt-abcde 跑審查,分支 feat/x-abcde 沒合。\n"
           "租約在我手上到十點,真人還沒回隱私權信箱那一題,別自己決定。",
    "s3A": "交接文字:主線 c0a12df0b,r18 兩個大問題修好待審,測試九支全綠。\n"
           "我會如何開始:先讀交接檔,再跑 aib test 那九支,最後用白話回報真人能不能用。",
    "s1B": "這一輪沒想到的是 Windows 路徑:交接檔名裡的冒號在別的系統會壞,建議改成全數字。\n"
           "另外 doctor 只看最新一份交接單,舊單堆多了要有人清,建議每週掃一次。",
    "s2B": "下一個 AI 要知道 enabled_at 只在第一次跑時寫,搬家目錄後會重寫一次,舊輪都不抓。\n"
           "另一台機器的遠端和這台不是同一個,推之前先 git remote -v 對一次。",
    "s3B": "總結:內容閘上線,三步都過。可直接貼的交接文字在上面第二段。\n"
           "我會如何開始:第一步 aib handoff status,第二步看缺漏旗標,第三步問真人要不要發版。",
}


def fill(text, s1="", s2="", s3="", raw=False):
    """草稿的三段待填行換成內容(空字串 = 留著待填)。raw=False ⇒ 每段在給的字後面再接一行該段固定的補充
    (過 r17 M1 的最低門檻:至少兩行、四十個字、各段不同);raw=True ⇒ 原樣(驗門檻用)。"""
    out = []
    for ln in text.splitlines():
        if ln.startswith(H.TODO_PREFIX):
            sec = out and H.MARK_RE.search(out[-1])
            key = sec.group(1) if sec else ""
            val = {"s1": s1, "s2": s2, "s3": s3}.get(key, "")
            out.append((val if raw else val + "\n" + PADS[key]) if val else ln)
        else:
            out.append(ln)
    return "\n".join(out) + "\n"


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="handoff-", dir=aib_tmp_root()))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.addCleanup(self.drain_bg)     # 先等背景補抓收工,再刪沙盒(不然它會把刪掉的目錄又建回來)
        self.home = self.td / "aib"
        self.inst = self.home / "instances" / INST
        self.seats = self.inst / "seats"
        self.seats.mkdir(parents=True)
        self.repo = self.td / "repo"
        self.lease = self.td / "host-lease.json"
        self.seat_rec(2, SEAT, provider="claude", cwd=str(self.repo), started_epoch=time.time() - 3600)
        env = mock.patch.dict(os.environ, {"EZ2_AIB_LOCALE": "zh-TW"})
        env.start()
        self.addCleanup(env.stop)          # patch.dict 停下時整份 os.environ 原樣還原(含下面洗掉的)
        W.wash_cabin_env()
        os.environ.update({"EZ2_AIB_HOME": str(self.home), "EZ2_VAULT_DIR": str(self.td / "vault"),
                           "AIB_HOST_LEASE_FILE": str(self.lease), "PYTHONDONTWRITEBYTECODE": "1"})
        # 公開版:家目錄與 Claude 設定也搬進沙盒,真 tmux 的導向拿掉(入口格要 tmux 的自己帶假 tmux + 假 TMUX)
        (self.td / "home").mkdir()
        os.environ.update({"HOME": str(self.td / "home"), "CLAUDE_CONFIG_DIR": str(self.td / "home" / ".claude"),
                           "EZ2_AIB_STATE_ROOT": str(self.home)})
        for k in ("AIB_PERSONA_REINJECT", "AIB_PERSONA_REINJECT_SOURCES", "AIB_HANDOFF_COLLECT_BUDGET_S",
                  "TMUX", "TMUX_PANE"):
            os.environ.pop(k, None)
        os.environ["AIB_GENES"] = "0"          # 基因開關不讀真家的 genes.json;要基因的格自己開
        os.environ["AIB_AUTO_BIND"] = "0"      # 10-13 第 2 包:交接第三步背景自動綁預設開 ⇒ 沙盒關掉(不跳真視窗、不真建卡);要驗的格自己開
        # 交接檢查啟用時刻:一天前(這之後開始的一輪才算缺漏);E 格自己刪掉它驗「第一次跑」
        H.handoff_dir().mkdir(parents=True, exist_ok=True)
        (H.handoff_dir() / "enabled_at").write_text("%.3f\n" % (time.time() - 86400), encoding="utf-8")

    def seat_rec(self, n, uuid, **kw):
        (self.seats / ("w%d.json" % n)).write_text(json.dumps(dict({"session_uuid": uuid}, **kw)), encoding="utf-8")

    def make_repo(self):
        origin = self.td / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(origin))
        git("init", "-q", "-b", "main", str(self.repo))
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        git("add", "a.txt", cwd=self.repo)
        git("commit", "-q", "-m", "one", cwd=self.repo)
        git("remote", "add", "origin", str(origin), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        git("add", "b.txt", cwd=self.repo)
        git("commit", "-q", "-m", "two", cwd=self.repo)       # main 領先 origin/main 1
        self.wt = self.td / "wt-abcde"
        git("worktree", "add", "-q", "-b", "feat/x-abcde", str(self.wt), cwd=self.repo)
        for i in (1, 2):
            (self.wt / ("w%d.txt" % i)).write_text("%d\n" % i, encoding="utf-8")
            git("add", "w%d.txt" % i, cwd=self.wt)
            git("commit", "-q", "-m", "w%d" % i, cwd=self.wt)
        (self.wt / "dirty.txt").write_text("x\n", encoding="utf-8")   # 未提交 1
        now = int(time.time())
        self.lease.write_text(json.dumps({"schema": "aib-host-lease/v1", "owner": "tester@zz9q", "what": "fixture",
                                          "run_id": "fixture-run", "pid": os.getpid(), "host": socket.gethostname(),
                                          "acquired_epoch": now, "expiry_epoch": now + 600}), encoding="utf-8")

    def seat(self):
        return H.resolve_seat("zz9q/abcde")

    def aib(self, *args, **kw):
        return subprocess.run(["bash", str(ROOT / "src" / "cockpit" / "aib"), "handoff"] + list(args),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, **kw)

    def fake_tmux(self, window):
        fake = self.td / "bin" / "tmux"
        fake.parent.mkdir(exist_ok=True)
        fake.write_text("#!/bin/bash\necho @%d\n" % window, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        # 先跑一次:macOS 第一次執行暫存目錄裡的新執行檔會先掃描,hook 問窗號只等 3 秒
        subprocess.run([str(fake)], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        return fake.parent

    def drain_bg(self, timeout=60.0):
        """log 裡每一個背景補抓的 pid 都等到結束(自己的子行程先收屍;不是自己的就看它還在不在)。"""
        try:
            lines = (self.home / "logs" / "handoff.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        pids = {json.loads(ln).get("auto_pid") for ln in lines if '"auto_pid"' in ln}
        pids = {p for p in pids if isinstance(p, int) and p > 0}
        end = time.time() + timeout
        for pid in pids:
            while time.time() < end:
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                    if done:
                        break
                except ChildProcessError:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                time.sleep(0.1)

    def wait_file(self, path, timeout=60.0):
        """背景補抓(脫離 hook 的行程)寫完 ⇒ 回內文;逾時 ⇒ 紅。"""
        end = time.time() + timeout
        while time.time() < end:
            if Path(path).is_file():
                return Path(path).read_text(encoding="utf-8")
            time.sleep(0.2)
        self.fail("背景補抓 %ss 內沒寫出 %s" % (timeout, path))

    def slow_git(self, seconds=20):
        """PATH 上放一支假 git:直接 exec sleep(行程本身就是 sleep,逾時被砍時不留孫行程拿著管子)。"""
        fake = self.td / "slowbin" / "git"
        fake.parent.mkdir(exist_ok=True)
        fake.write_text("#!/bin/bash\nexec sleep %d\n" % seconds, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        subprocess.run([str(fake)], capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
                       env=dict(os.environ, PATH="/bin:/usr/bin"))   # 先跑一次(macOS 首次執行掃描)
        return fake.parent

    def draft(self, repo=""):
        """Python 層的 collect + 戳記(CLI 的 collect 也是這兩步)。"""
        facts = H.collect(self.seat(), repo=repo)
        H.record_collect("abcde", facts)
        return H.render_draft(facts)

    def write_receipt(self, code, ts=None):
        """一張算數的收據:這一輪裡最後一次 collect 的 nonce 對得上(r17 M1)。"""
        ts = ts or time.time()
        nonce = os.urandom(6).hex()
        H._append_jsonl(H.collects_log(code), {"ts": ts - 1, "nonce": nonce, "round_ts": 0})
        H._write_json(H.receipt_path(code), {"schema": H.SCHEMA, "seat": code, "ts": ts, "file": "x", "sha": "0",
                                             "collect_ts": ts - 1, "collect_nonce": nonce})


class Collect(Sandbox):
    def test_H1_collect_reports_repo_worktrees_branches_lease(self):
        self.make_repo()
        facts = H.collect(self.seat(), repo=str(self.repo))
        self.assertEqual(facts["repo"]["main_vs_origin"], {"ahead": 1, "behind": 0})
        wt = {Path(w["path"]).resolve(): w for w in facts["worktrees"]["shown"]}
        row = wt[self.wt.resolve()]
        self.assertEqual((row["branch"], row["ahead"], row["behind"], row["dirty"]), ("feat/x-abcde", 2, 0, 1))
        self.assertIn("feat/x-abcde", [b["branch"] for b in facts["unmerged"]["shown"]])
        self.assertEqual(facts["lease"].get("path"), str(self.lease), "租約要讀假檔(AIB_HOST_LEASE_FILE)")
        draft = H.render_draft(facts)
        for k in H.SECTIONS:
            self.assertEqual(len(re.findall(r"<!-- handoff:%s -->" % k, draft)), 1)
        self.assertEqual(draft.count(H.TODO_PREFIX), 3)
        self.assertIn("main vs origin/main: +1/-0", draft)
        self.assertIn("feat/x-abcde", draft)
        with self.assertRaises(H.HandoffError):
            H.validate(draft, 1)


class Write(Sandbox):
    def test_H2_write_refuses_empty_and_out_of_order(self):
        draft = self.draft()
        with mock.patch("aib_memory.write", return_value={"uid": "u1", "path": "p"}) as mw:
            for step in (None, 1, 2, 3):
                with self.assertRaises(H.HandoffError, msg="step=%s" % step):
                    H.write(self.seat(), draft, step)
            self.assertFalse(H.steps_path("abcde").exists(), "全拒的時候什麼都不寫")
            self.assertFalse(H.seat_memory_path("abcde").exists())
            part = fill(draft, s1="建議 A")
            with self.assertRaises(H.HandoffError):
                H.write(self.seat(), part, 2)          # 第二段空
            full = fill(draft, s1="建議 A", s2="不知道 B", s3="重來 C")
            with self.assertRaises(H.HandoffError):
                H.write(self.seat(), full, 3)          # 同一輪沒做 1、2
            with self.assertRaises(H.HandoffError):
                H.write(self.seat(), full, 2)          # 沒做 1
            self.assertEqual(mw.call_count, 0)
            H.write(self.seat(), part, 1)
            with self.assertRaises(H.HandoffError):
                H.write(self.seat(), full, 3)          # 還缺 2
        self.assertFalse(H.receipt_path("abcde").exists())

    def test_H3_write_does_all_four_writes_and_the_receipt(self):
        full = fill(self.draft(), s1="建議 A1", s2="不知道 B2", s3="重來 C3")
        calls = []

        def fake_write(kind, title, body, tags=(), about=(), seat="", project="", route_key="", dry_run=False):
            calls.append((kind, title, body, list(tags), seat))
            return {"uid": "u%d" % len(calls), "path": "/vault/u%d.md" % len(calls)}
        with mock.patch("aib_memory.write", side_effect=fake_write):
            for step in (1, 2, 3):
                res = H.write(self.seat(), full, step, seat_arg="zz9q/abcde")
                self.assertTrue(res["brain"]["ok"] and res["seat_memory"]["ok"], res)
        self.assertEqual([c[0] for c in calls], ["handoff"] * 3)
        self.assertEqual([c[4] for c in calls], ["zz9q/abcde"] * 3)
        for i, needle in enumerate(("建議 A1", "不知道 B2", "重來 C3")):
            self.assertIn(needle, calls[i][2])
        mem = H.seat_memory_path("abcde").read_text(encoding="utf-8")
        for needle in ("建議 A1", "不知道 B2", "重來 C3"):
            self.assertEqual(mem.count(needle), 1, mem)
        rec = json.loads(H.receipt_path("abcde").read_text(encoding="utf-8"))
        for k in ("seat", "session_uuid", "ts", "file", "sha"):
            self.assertIn(k, rec)
        self.assertEqual((rec["seat"], rec["session_uuid"]), ("abcde", SEAT))
        f = Path(rec["file"])
        self.assertEqual(f.parent, self.home / "pending")
        self.assertTrue(f.name.startswith("HANDOFF-abcde-"), f.name)
        self.assertEqual(rec["sha"], hashlib.sha256(f.read_bytes()).hexdigest())
        self.assertEqual(rec["brain"]["uid"], "u3")
        self.assertIn("重來 C3", f.read_text(encoding="utf-8"))

    def test_H4_entry_aib_handoff_write_steps_and_status(self):
        """生產頂 `bash src/cockpit/aib handoff …`(子行程)。第二大腦在 aib test 的乾淨 HOME 裡寫不進去 ⇒ 只驗收據
        照實記了 brain.ok(不論真假),其他三樣一定在。"""
        self.make_repo()
        draft = self.td / "draft.md"
        p = self.aib("collect", "--seat", "zz9q/abcde", "--repo", str(self.repo), "--out", str(draft))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(draft.is_file(), "collect 要寫出草稿:" + p.stdout + p.stderr)
        text = draft.read_text(encoding="utf-8")
        self.assertIn("feat/x-abcde", text)
        self.assertIn("main vs origin/main: +1/-0", text)
        p = self.aib("write", "--step", "1", "--file", str(draft), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertFalse((self.home / "state" / "handoff" / "abcde.steps.json").exists(), "空稿被拒時什麼都不寫")
        draft.write_text(fill(text, s1="建議 E1", s2="不知道 E2", s3="重來 E3"), encoding="utf-8")
        p = self.aib("write", "--step", "3", "--file", str(draft), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 2, "沒做 1、2 不准做 3:" + p.stdout + p.stderr)
        for step in ("1", "2", "3"):
            p = self.aib("write", "--step", step, "--file", str(draft), "--seat", "zz9q/abcde")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("/clear", p.stdout)
        rec = json.loads((self.home / "state" / "handoff" / "abcde.json").read_text(encoding="utf-8"))
        self.assertIn("ok", rec["brain"])
        self.assertTrue(Path(rec["file"]).is_file())
        self.assertIn("重來 E3", (self.home / "memory" / "abcde" / "context.md").read_text(encoding="utf-8"))
        p = self.aib("status", "--seat", "zz9q/abcde", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads(p.stdout)
        self.assertEqual(st["this_round_receipt"]["sha"], rec["sha"])
        self.assertEqual(st["steps"], ["1", "2", "3"])

    def test_H5_status_previous_round(self):
        t0 = time.time() - 100
        H.record_round("abcde", "test", ts=t0)
        self.write_receipt("abcde", ts=t0 + 10)
        H.record_round("abcde", "test", ts=t0 + 20)
        st = H.status("abcde")
        self.assertEqual(st["previous_round_receipt"]["ts"], t0 + 10)
        self.assertEqual(st["this_round_receipt"], {})
        self.assertIn("上一輪有留收據", H.render_status(st))


class WriteGuards(Sandbox):
    """r17 M1:收據騙不過 —— 要給步驟、每段有實際內容、草稿是這一席這一輪最後一次 collect 的那一份。"""

    def cli_draft(self, out="draft.md", **kw):
        draft = self.td / out
        kw.setdefault("cwd", str(self.td))        # 沙盒不是 git 倉 ⇒ 倉那幾項記成沒量到,不去量跑測試的那棵樹
        p = self.aib("collect", "--seat", "zz9q/abcde", "--out", str(draft), **kw)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(draft.is_file(), "collect 要寫出草稿:" + p.stdout + p.stderr)
        return draft

    def test_M1a_entry_write_without_step_is_refused(self):
        draft = self.cli_draft()
        draft.write_text(fill(draft.read_text(encoding="utf-8"), s1="建議 M1", s2="不知道 M2", s3="重來 M3"),
                         encoding="utf-8")
        for step in ("1", "2"):
            p = self.aib("write", "--step", step, "--file", str(draft), "--seat", "zz9q/abcde")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.aib("write", "--file", str(draft), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 2, "沒給 --step 一律拒(沒有三步一次做完):" + p.stdout + p.stderr)
        self.assertFalse(H.receipt_path("abcde").exists(), "被拒時沒有收據")
        self.assertEqual(list((self.home / "pending").glob("HANDOFF-abcde-1*.md")), [])

    def test_M1b_trivial_sections_are_refused(self):
        draft = self.draft()
        with mock.patch("aib_memory.write", return_value={"uid": "u1", "path": "p"}) as mw:
            for s1 in ("ok", "a\nb", "這一段只有一行但是寫得很長很長很長很長很長很長很長很長很長很長很長很長很長"):
                with self.assertRaises(H.HandoffError, msg=s1):
                    H.write(self.seat(), fill(draft, s1=s1, raw=True), 1)
            same = "我建議把背景補抓的時限調成可設定。\n下一個 AI 要知道 enabled_at 是第一次跑才寫的。"
            with self.assertRaises(H.HandoffError, msg="第二段和第一段一字不差(r19:第 1 步就擋)"):
                H.write(self.seat(), fill(draft, s1=same, s2=same, raw=True), 1)
            H.write(self.seat(), fill(draft, s1=same, raw=True), 1)
            no_facts = fill(draft, s1="建議 F1", s2="不知道 F2").split("<!-- handoff:facts -->")[0]
            with self.assertRaises(H.HandoffError, msg="機器事實段被刪"):
                H.write(self.seat(), no_facts + "<!-- handoff:facts -->\n", 2)
            H.write(self.seat(), fill(draft, s1=same, s2="不知道 F2\n" + PADS["s2"], raw=True), 2)
        self.assertEqual(mw.call_count, 2, "只有過門檻的兩次真的寫了")

    def test_M1c_entry_wrong_seat_header_is_refused(self):
        self.seat_rec(3, NEWSEAT, provider="codex", cwd=str(self.repo), started_epoch=time.time() - 600)
        draft = self.cli_draft()
        draft.write_text(fill(draft.read_text(encoding="utf-8"), s1="建議 W1"), encoding="utf-8")
        p = self.aib("collect", "--seat", "zz9q/fffff", "--out", str(self.td / "other.md"), cwd=str(self.td))
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.aib("write", "--step", "1", "--file", str(draft), "--seat", "zz9q/fffff")
        self.assertEqual(p.returncode, 2, "草稿頭是 @abcde,不能拿來寫 @fffff:" + p.stdout + p.stderr)
        self.assertIn("@abcde", p.stderr)
        self.assertFalse(H.steps_path("fffff").exists())
        p = self.aib("write", "--step", "1", "--file", str(draft), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 0, "對照臂:同一份草稿寫回自己這一席 ⇒ 過:" + p.stdout + p.stderr)

    def test_M1d_entry_stale_or_auto_draft_is_refused(self):
        old = self.cli_draft("old.md")
        new = self.cli_draft("new.md")
        # 舊稿 = 新稿換回舊的 nonce(機器事實段和最後一次 collect 一字不差):只靠 nonce 才分得出它是舊的那一份
        # (r18:事實段雜湊另有一道;兩次 collect 落在不同秒時它也會擋,這一格不能靠它)。
        old_nonce = re.search(r"nonce=([0-9a-f]+)", old.read_text(encoding="utf-8")).group(1)
        new.write_text(fill(new.read_text(encoding="utf-8"), s1="建議 S1"), encoding="utf-8")
        old.write_text(re.sub(r"nonce=[0-9a-f]+", "nonce=" + old_nonce, new.read_text(encoding="utf-8"), count=1),
                       encoding="utf-8")
        p = self.aib("write", "--step", "1", "--file", str(old), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 2, "舊的那次 collect 印的草稿 ⇒ 拒:" + p.stdout + p.stderr)
        auto = self.td / "auto.md"
        auto.write_text(fill(H.render_draft(H.collect(self.seat(), repo=""), auto=True), s1="建議 S1"),
                        encoding="utf-8")
        p = self.aib("write", "--step", "1", "--file", str(auto), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 2, "機器補抓的 AUTO 檔不能拿來交接:" + p.stdout + p.stderr)
        p = self.aib("write", "--step", "1", "--file", str(new), "--seat", "zz9q/abcde")
        self.assertEqual(p.returncode, 0, "對照臂:最後一次 collect 的草稿 ⇒ 過:" + p.stdout + p.stderr)

    def test_M1f_entry_writing_for_another_seat_is_recorded(self):
        """跑指令的是 @fffff(TMUX 認得出),--seat 給 @abcde ⇒ stderr 提醒、收據記 seat_mismatch(真人代寫 API 席是正當的)。"""
        self.seat_rec(3, NEWSEAT, provider="codex", started_epoch=time.time() - 600)
        env = dict(os.environ, TMUX="/tmp/tmux-501/aihub-%s,1,0" % INST, TMUX_PANE="%3",
                   PATH=str(self.fake_tmux(3)) + os.pathsep + os.environ.get("PATH", ""))
        draft = self.cli_draft(env=env)
        draft.write_text(fill(draft.read_text(encoding="utf-8"), s1="建議 O1", s2="不知道 O2", s3="重來 O3"),
                         encoding="utf-8")
        for step in ("1", "2", "3"):
            p = self.aib("write", "--step", step, "--file", str(draft), "--seat", "zz9q/abcde", env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("@fffff", p.stderr, "要提醒:你在幫別席寫")
        rec = json.loads(H.receipt_path("abcde").read_text(encoding="utf-8"))
        self.assertEqual((rec["writer_seat"], rec["seat_mismatch"]), ("fffff", True), rec)

    # ---- r18 M1:內容閘(全走生產頂 `bash src/cockpit/aib handoff`)
    def put(self, draft, text, *steps):
        """把 text 寫進草稿,依序跑 write --step;回最後一次的 CompletedProcess。"""
        draft.write_text(text, encoding="utf-8")
        p = None
        for step in steps:
            p = self.aib("write", "--step", str(step), "--file", str(draft), "--seat", "zz9q/abcde")
            if p.returncode != 0:
                return p
        return p

    def test_R18_m1a_entry_padded_sections_are_refused(self):
        draft = self.cli_draft()
        text = draft.read_text(encoding="utf-8")
        for s1 in ("a" * 40 + "\na", "a" * 30 + "\nbcdefghijklmnop"):
            p = self.put(draft, fill(text, s1=s1, raw=True), 1)
            self.assertEqual(p.returncode, 2, "湊字數要拒:%r\n%s" % (s1, p.stdout + p.stderr))
            self.assertIn("湊字數", p.stderr)
        self.assertFalse(H.steps_path("abcde").exists(), "被拒時什麼都不寫")
        p = self.put(draft, fill(text, s1=REAL["s1A"], raw=True), 1)
        self.assertEqual(p.returncode, 0, "對照臂:真的內容 ⇒ 過:" + p.stdout + p.stderr)

    def test_R18_m1b_entry_near_duplicate_sections_are_refused(self):
        draft = self.cli_draft()
        text = draft.read_text(encoding="utf-8")
        near = REAL["s1A"].replace("八秒", "九秒")
        self.assertNotEqual(near, REAL["s1A"])
        p = self.put(draft, fill(text, s1=REAL["s1A"], s2=near, raw=True), 1, 2)
        self.assertEqual(p.returncode, 2, "第二段只和第一段差一個字 ⇒ 拒:" + p.stdout + p.stderr)
        self.assertIn("幾乎一樣", p.stderr)
        self.assertFalse(H.steps_path("abcde").exists(), "r19:第 1 步就擋(第二段已經填了),什麼都沒寫")
        p = self.put(draft, fill(text, s1=REAL["s1A"], s2=REAL["s2A"], raw=True), 1, 2)
        self.assertEqual(p.returncode, 0, "對照臂:第二段真的不同 ⇒ 過:" + p.stdout + p.stderr)

    def test_R18_m1c_entry_previous_round_draft_with_new_nonce_is_refused(self):
        d1 = self.cli_draft("r1.md")
        old = fill(d1.read_text(encoding="utf-8"), s1=REAL["s1A"], s2=REAL["s2A"], s3=REAL["s3A"], raw=True)
        p = self.put(d1, old, 1, 2, 3)
        self.assertEqual(p.returncode, 0, "第一輪照規矩交接:" + p.stdout + p.stderr)
        H.record_round("abcde", "claude:clear")                  # 新的一輪
        d2 = self.cli_draft("r2.md")
        text2 = d2.read_text(encoding="utf-8")
        new_nonce = re.search(r"nonce=([0-9a-f]+)", text2).group(1)
        swapped = re.sub(r"nonce=[0-9a-f]+", "nonce=" + new_nonce, old, count=1)
        p = self.put(d2, swapped, 1)
        self.assertEqual(p.returncode, 2, "上一輪整份草稿換 nonce 重交 ⇒ 拒:" + p.stdout + p.stderr)
        p = self.put(d2, fill(text2, s1=REAL["s1A"], s2=REAL["s2A"], s3=REAL["s3A"], raw=True), 1)
        self.assertEqual(p.returncode, 2, "新草稿裡貼上一輪的三段 ⇒ 拒:" + p.stdout + p.stderr)
        self.assertIn("上一輪交接檔", p.stderr)
        p = self.put(d2, fill(text2, s1=REAL["s1B"], s2=REAL["s2B"], s3=REAL["s3B"], raw=True), 1, 2, 3)
        self.assertEqual(p.returncode, 0, "對照臂:這一輪重寫 ⇒ 過:" + p.stdout + p.stderr)

    def test_R18_m1d_entry_tampered_facts_block_is_refused(self):
        draft = self.cli_draft()
        good = fill(draft.read_text(encoding="utf-8"), s1=REAL["s1A"], raw=True)
        anchor = "<!-- handoff:facts -->\n"
        self.assertEqual(good.count(anchor), 1)
        bad = good.replace(anchor, anchor + "- 我自己加的事實:全部測試都過了\n")
        p = self.put(draft, bad, 1)
        self.assertEqual(p.returncode, 2, "機器事實段被改 ⇒ 拒:" + p.stdout + p.stderr)
        self.assertIn("機器事實段和這次 collect", p.stderr)
        p = self.put(draft, good, 1)
        self.assertEqual(p.returncode, 0, "對照臂:事實段原樣 ⇒ 過:" + p.stdout + p.stderr)

    def test_R18_m1e_entry_step3_section_one_differs_from_step1_is_refused(self):
        draft = self.cli_draft()
        text = draft.read_text(encoding="utf-8")
        good = fill(text, s1=REAL["s1A"], s2=REAL["s2A"], s3=REAL["s3A"], raw=True)
        p = self.put(draft, good, 1, 2)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.put(draft, fill(text, s1=REAL["s1B"], s2=REAL["s2A"], s3=REAL["s3A"], raw=True), 3)
        self.assertEqual(p.returncode, 2, "第 3 步的第一段和第 1 步寫進記憶的不一樣 ⇒ 拒:" + p.stdout + p.stderr)
        self.assertIn("前一步已經寫進記憶", p.stderr)
        self.assertFalse(H.receipt_path("abcde").exists(), "被拒時沒有收據")
        p = self.put(draft, good, 3)
        self.assertEqual(p.returncode, 0, "對照臂:同一份草稿 ⇒ 過:" + p.stdout + p.stderr)

    # ---- r19 內容閘
    def test_R19_a_entry_honest_no_change_round_is_accepted(self):
        """這一輪沒變、還在等真人:三段和上一輪很像(相似度 0.9 以上)但用這一輪的話寫 ⇒ 過(r19 拿掉跨輪相似度)。"""
        d1 = self.cli_draft("r1.md")
        p = self.put(d1, fill(d1.read_text(encoding="utf-8"), s1=REAL["s1A"], s2=REAL["s2A"], s3=REAL["s3A"],
                              raw=True), 1, 2, 3)
        self.assertEqual(p.returncode, 0, "第一輪照規矩交接:" + p.stdout + p.stderr)
        H.record_round("abcde", "claude:clear")
        d2 = self.cli_draft("r2.md")
        same = {"s1": REAL["s1A"].replace("八秒", "八秒(這一輪沒變)"),
                "s2": REAL["s2A"].replace("真人還沒回", "真人到現在還沒回"),
                "s3": REAL["s3A"].replace("c0a12df0b", "ddb6d3872")}
        for k, v in same.items():
            self.assertNotEqual(v, REAL[k + "A"])
            self.assertGreaterEqual(H.similarity(v, REAL[k + "A"]), 0.9, "這一格量的就是「很像上一輪」的誠實稿")
        p = self.put(d2, fill(d2.read_text(encoding="utf-8"), raw=True, **same), 1, 2, 3)
        self.assertEqual(p.returncode, 0, "誠實的「這一輪沒變」⇒ 過:" + p.stdout + p.stderr)
        self.assertTrue(H.receipt_path("abcde").exists())

    def test_R19_b_entry_prompt_text_pasted_back_is_refused(self):
        draft = self.cli_draft()
        text = draft.read_text(encoding="utf-8")
        import i18n_lib
        zh, en = i18n_lib.load_dict("zh-TW"), i18n_lib.load_dict("en")
        q = lambda d, k: d["aib.handoff.todo.%s" % k]
        cases = {"去掉前綴的中文提示字": q(zh, "s1") + "\n" + q(zh, "s2"),
                 "英文提示字": q(en, "s1") + "\n" + q(en, "s3"),
                 "只去掉 > 的前綴": "TODO-HANDOFF: " + q(zh, "s1") + "\n" + "TODO-HANDOFF: " + q(zh, "s3"),
                 "提示字加段標題": zh["aib.handoff.section.s1"] + "\n" + q(zh, "s1") + " " + q(en, "s1")}
        for name, s1 in cases.items():
            p = self.put(draft, fill(text, s1=s1, raw=True), 1)
            self.assertEqual(p.returncode, 2, "%s ⇒ 拒:%r\n%s" % (name, s1, p.stdout + p.stderr))
            self.assertIn("提示字", p.stderr, name)
        self.assertFalse(H.steps_path("abcde").exists(), "被拒時什麼都不寫")
        honest = "我會如何開始:\n" + REAL["s3A"]          # 拿提示字當小標題、後面寫真的內容 ⇒ 過
        p = self.put(draft, fill(text, s1=REAL["s1A"], s2=REAL["s2A"], s3=honest, raw=True), 1, 2, 3)
        self.assertEqual(p.returncode, 0, "對照臂:真的內容(含一句提示字當小標題)⇒ 過:" + p.stdout + p.stderr)

    def test_R19_c_entry_step1_refuses_near_identical_sections_before_writing(self):
        draft = self.cli_draft()
        text = draft.read_text(encoding="utf-8")
        p = self.put(draft, fill(text, s1=REAL["s1A"], s2=REAL["s1A"].replace("八秒", "九秒"),
                                 s3=REAL["s1A"].replace("一半", "三成"), raw=True), 1)
        self.assertEqual(p.returncode, 2, "三段幾乎一樣 ⇒ 第 1 步就拒:" + p.stdout + p.stderr)
        self.assertIn("幾乎一樣", p.stderr)
        self.assertFalse(H.steps_path("abcde").exists(), "第 1 步被拒 ⇒ 沒有步驟紀錄")
        self.assertFalse(H.seat_memory_path("abcde").exists(), "第 1 步被拒 ⇒ 席位記憶沒寫")
        p = self.put(draft, fill(text, s1=REAL["s1A"], raw=True), 1)
        self.assertEqual(p.returncode, 0, "對照臂:只填第一段、別段還待填 ⇒ 過:" + p.stdout + p.stderr)

    def test_R18_m3_entry_malformed_steps_json_does_not_crash(self):
        draft = self.cli_draft()
        good = fill(draft.read_text(encoding="utf-8"), s1=REAL["s1A"], s2=REAL["s2A"], s3=REAL["s3A"], raw=True)
        H.steps_path("abcde").write_text("{not json", encoding="utf-8")
        p = self.put(draft, good, 1)
        self.assertEqual(p.returncode, 0, "steps.json 不是 JSON ⇒ 當空的,不當掉:" + p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        H.steps_path("abcde").write_text("[]\n", encoding="utf-8")
        p = self.put(draft, good, 1, 2, 3)
        self.assertEqual(p.returncode, 0, "steps.json 形狀不對 ⇒ 當空的,從第 1 步重來:" + p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        log = (self.home / "logs" / "handoff.jsonl").read_text(encoding="utf-8")
        self.assertGreaterEqual(log.count('"steps_malformed"'), 2, "兩次壞檔都記 log")


HOOK_DIR = ROOT / "src" / "identity"
COCKPIT = ROOT / "src" / "cockpit"


class ClaudeHook(Sandbox):
    """入口格自己寫 subprocess.run(生產頂 hook 檔名字面在格裡);同一格後續幾次走 run_hook。"""

    def hook_kw(self, source="clear", path_front=(), transcript="", **extra):
        path = os.pathsep.join([str(d) for d in path_front] + [str(self.fake_tmux(2)), os.environ.get("PATH", "")])
        env = dict(os.environ, TMUX="/tmp/tmux-501/aihub-%s,1,0" % INST, TMUX_PANE="%1", PATH=path, **extra)
        payload = {"source": source, "cwd": str(self.repo)}
        if transcript:
            payload["transcript_path"] = transcript
        return {"input": json.dumps(payload), "capture_output": True, "text": True, "env": env, "timeout": 120}

    def hook_out(self, p):
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def run_hook(self, source="clear", **kw):
        return self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                             "session-start"], **self.hook_kw(source, **kw)))

    def test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies(self):
        self.make_repo()
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertTrue(out.startswith(NOTICE), "第一行就是交接缺漏提醒:%r" % out[:300])
        flag = json.loads(H.missed_path("abcde").read_text(encoding="utf-8"))
        auto = Path(flag["auto_file"])
        self.assertTrue(auto.name.startswith("HANDOFF-abcde-AUTO-"), auto)
        self.assertIn(str(auto), out)
        body = self.wait_file(auto)          # 補抓在背景(hook 不等它)
        self.assertIn("<!-- handoff:facts -->", body)
        self.assertIn("feat/x-abcde", body)
        self.assertEqual(len(H.rounds("abcde")), 1, "記下新一輪的開始點")
        self.assertEqual(self.run_hook("compact"), "", "壓縮不是新的一輪,不查")
        out = self.run_hook("clear", AIB_PERSONA_REINJECT="0")
        self.assertTrue(out.startswith(NOTICE), "人格補回關掉也照樣提醒(交接不能被關):%s" % out[:200])

    def test_H6b_control_receipt_present_no_notice(self):
        self.write_receipt("abcde")
        out = self.run_hook("clear")
        self.assertNotIn(NOTICE, out)
        self.assertFalse(H.missed_path("abcde").exists())


class FastHook(ClaudeHook):
    """r17 M2:SessionStart hook 只給 10 秒;交接檢查要毫秒級,補抓丟背景,人格/基因先印。"""

    GENE_HEAD = "[AIB 對話剛被清除,自動補回 AIB 基因"

    def test_M2a_entry_hook_with_stuck_git_is_fast_and_genes_come_first(self):
        self.make_repo()
        kw = self.hook_kw("clear", path_front=[self.slow_git(20)], AIB_GENES="all", AIB_HANDOFF_COLLECT_BUDGET_S="2")
        t0 = time.time()
        p = subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"), "session-start"], **kw)
        elapsed = time.time() - t0
        out = self.hook_out(p)
        self.assertLess(elapsed, 1.0, "hook 要毫秒級(git 卡 20 秒也一樣):%.2fs" % elapsed)
        self.assertTrue(out.startswith(self.GENE_HEAD), "基因/人格先印:%r" % out[:200])
        self.assertIn(NOTICE, out)
        self.assertLess(out.index(self.GENE_HEAD), out.index(NOTICE))
        self.assertEqual(len(H.rounds("abcde")), 1, "記下新一輪的開始點")
        flag = json.loads(H.missed_path("abcde").read_text(encoding="utf-8"))
        self.assertTrue(flag["auto_pid"] > 0, flag)
        body = self.wait_file(flag["auto_file"], timeout=30)
        self.assertIn("<!-- handoff:facts -->", body)
        self.assertIn(H.T("aib.handoff.why.budget"), body, "git 卡住 ⇒ 背景補抓到時限就收,記成沒量到")

    def test_M2d_entry_hook_main_in_process(self):
        """Claude Code 叫的就是這支 main;行程內呼叫(stdin = hook JSON):基因先印、交接提醒在後。"""
        import persona_reinject_hook as PRH
        env = self.hook_kw("clear", AIB_GENES="all")["env"]
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("sys.stdin", io.StringIO(json.dumps({"source": "clear", "cwd": str(self.repo)}))), \
                contextlib.redirect_stdout(buf):
            rc = PRH.main(["session-start"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(self.GENE_HEAD), out[:200])
        self.assertIn(NOTICE, out)
        self.assertLess(out.index(self.GENE_HEAD), out.index(NOTICE))

    def test_M2b_entry_collect_respects_the_total_budget(self):
        """生產頂 `bash src/cockpit/aib handoff collect`;git 卡 20 秒、總時限 1.5 秒 ⇒ 幾秒內收、記成沒量到。"""
        self.make_repo()
        env = dict(os.environ, PATH=str(self.slow_git(20)) + os.pathsep + os.environ.get("PATH", ""),
                   AIB_HANDOFF_COLLECT_BUDGET_S="1.5")
        t0 = time.time()
        p = self.aib("collect", "--seat", "zz9q/abcde", "--repo", str(self.repo), "--json", env=env)
        took = time.time() - t0
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(took, 4.5, "總時限 1.5 秒(加 aib 殼、租約與 ps 的零頭):%.2fs" % took)
        facts = json.loads(p.stdout)
        self.assertIn("repo", {m["item"] for m in facts["unmeasured"]}, facts["unmeasured"])

    def test_M2c_entry_git_calls_carry_no_optional_locks(self):
        """生產頂 collect;PATH 上一支記帳的 git(記下 GIT_OPTIONAL_LOCKS 再交給真 git)。"""
        self.make_repo()
        real = shutil.which("git")
        spy = self.td / "spybin" / "git"
        spy.parent.mkdir()
        log = self.td / "git-env.log"
        spy.write_text('#!/bin/bash\necho "locks=${GIT_OPTIONAL_LOCKS:-unset}" >> "%s"\nexec "%s" "$@"\n' % (log, real),
                       encoding="utf-8")
        spy.chmod(spy.stat().st_mode | stat.S_IEXEC)
        subprocess.run([str(spy), "--version"], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        log.write_text("", encoding="utf-8")
        p = self.aib("collect", "--seat", "zz9q/abcde", "--repo", str(self.repo), "--json",
                     env=dict(os.environ, PATH=str(spy.parent) + os.pathsep + os.environ.get("PATH", "")))
        self.assertEqual(p.returncode, 0, p.stderr)
        seen = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln]
        self.assertGreater(len(seen), 5, "collect 叫 git 的次數太少,記帳 git 沒接上?")
        self.assertEqual(set(seen), {"locks=0"}, "每一支 git 都要 GIT_OPTIONAL_LOCKS=0(它會在別席的工作樹跑 status)")

    def test_E1_first_run_writes_enabled_at_and_does_not_flag_older_rounds(self):
        (H.handoff_dir() / "enabled_at").unlink()
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertNotIn(NOTICE, out, "啟用之前就開始的一輪不算缺漏")
        self.assertTrue((H.handoff_dir() / "enabled_at").is_file(), "第一次跑要寫 enabled_at(沙盒裡的狀態根)")
        self.assertFalse(H.missed_path("abcde").exists())
        self.assertIn(NOTICE, self.run_hook("clear"), "啟用之後開始的一輪沒收據 ⇒ 照樣抓")

    def test_E2_empty_round_double_clear_is_not_flagged(self):
        """連按兩次 /clear:這一輪的對話檔在,裡面只有 /clear 指令本身 ⇒ 不算缺漏。
        (r18 M2:對話檔**不在**不再算空的 —— 那是 test_R18_m2b 的事。)"""
        tr = self.td / "transcript.jsonl"
        tr.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                                              "content": "<command-name>/clear</command-name>"}}) + "\n",
                      encoding="utf-8")
        H.record_round("abcde", "test", ts=time.time() - 60, transcript=str(tr))
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear", transcript=str(self.td / "t2.jsonl"))))
        self.assertNotIn(NOTICE, out, "對話檔裡只有 /clear = 這一輪真人沒講話 ⇒ 不算缺漏")
        t2 = self.td / "t2.jsonl"
        t2.write_text("\n".join(json.dumps(d) for d in (
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>x"}},
            {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
            {"type": "system", "subtype": "informational"})) + "\n", encoding="utf-8")
        self.assertNotIn(NOTICE, self.run_hook("clear", transcript=str(self.td / "t3.jsonl")),
                         "只有 /clear 指令本身 ⇒ 還是空的一輪")
        t3 = self.td / "t3.jsonl"
        t3.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "幫我修這個 bug"}}) + "\n",
                      encoding="utf-8")
        self.assertIn(NOTICE, self.run_hook("clear"), "對照臂:真人講過話的一輪沒收據 ⇒ 抓")

    def test_R18_m2a_entry_resume_joins_the_round_and_is_checked_at_next_clear(self):
        """/clear 之後馬上 /resume 接回舊對話、在那裡做事 ⇒ 下一次 /clear 要抓(以前只量 /clear 那份空的對話檔)。"""
        t1 = self.td / "after-clear.jsonl"
        t1.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                                              "content": "<command-name>/clear</command-name>"}}) + "\n",
                      encoding="utf-8")
        H.record_round("abcde", "claude:clear", ts=time.time() - 60, transcript=str(t1))
        t0 = self.td / "resumed.jsonl"
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("resume", transcript=str(t0))))
        self.assertNotIn(NOTICE, out, "接回不是新的一輪,不查")
        self.assertEqual(len(H.rounds("abcde")), 1, "接回不開新的一輪")
        self.assertEqual(H.rounds("abcde")[-1].get("transcripts"), [str(t0)], "接回的對話檔併進這一輪")
        with open(t0, "a", encoding="utf-8") as fh:        # 接回之後真人講話(r19:只算接回之後的)
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "接著修昨天那個 bug"}}) + "\n")
        self.assertIn(NOTICE, self.run_hook("clear"), "接回之後真人講過話、沒收據 ⇒ 下一次 /clear 抓")

    def test_R18_m2b_entry_missing_transcript_is_checked(self):
        H.record_round("abcde", "claude:clear", ts=time.time() - 60, transcript=str(self.td / "gone.jsonl"))
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertIn(NOTICE, out, "這一輪的對話檔找不到 ⇒ 照查,不當作空的一輪放過")

    def test_R19_d_entry_resumed_old_conversation_counts_only_messages_after_the_resume(self):
        """r19:接回一份舊對話(裡面本來就有真人講過的話)、接回後什麼都沒講就 /clear ⇒ 不抓;接回後講了話 ⇒ 抓。"""
        def line(content, ago=None):
            d = {"type": "user", "message": {"role": "user", "content": content}}
            if ago is not None:
                t = time.time() - ago
                d["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)
            return json.dumps(d, ensure_ascii=False) + "\n"
        t1 = self.td / "after-clear.jsonl"
        t1.write_text(line("<command-name>/clear</command-name>"), encoding="utf-8")
        H.record_round("abcde", "claude:clear", ts=time.time() - 60, transcript=str(t1))
        old = self.td / "old-conversation.jsonl"            # 同一份檔接著寫(接回前的話沒有 timestamp 也不算)
        old.write_text(line("昨天的話:幫我修這個 bug") + line("昨天的第二句", ago=86400), encoding="utf-8")
        fork = self.td / "forked.jsonl"                     # 接回時還不在、之後搬進舊對話(帶舊的 timestamp)
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("resume", transcript=str(old))))
        self.assertNotIn(NOTICE, out)
        self.assertNotIn(NOTICE, self.run_hook("resume", transcript=str(fork)))
        fork.write_text(line("昨天的話:幫我修這個 bug", ago=86400) + line("<command-name>/resume</command-name>"),
                        encoding="utf-8")
        with open(old, "a", encoding="utf-8") as fh:
            fh.write(line("<command-name>/clear</command-name>", ago=0))
        self.assertNotIn(NOTICE, self.run_hook("clear", transcript=str(self.td / "t2.jsonl")),
                         "接回舊對話後什麼都沒講就 /clear ⇒ 不是跳過交接(舊對話裡的話不算這一輪)")
        self.assertFalse(H.missed_path("abcde").exists())
        # 對照臂:這一輪又接回同一份舊對話,接回之後真人講話 ⇒ 下一次 /clear 抓
        (self.td / "t2.jsonl").write_text(line("<command-name>/clear</command-name>"), encoding="utf-8")
        self.assertNotIn(NOTICE, self.run_hook("resume", transcript=str(old)))
        with open(old, "a", encoding="utf-8") as fh:
            fh.write(line("接回之後:繼續修", ago=0))
        self.assertIn(NOTICE, self.run_hook("clear"), "接回之後真人講過話、沒收據 ⇒ 抓")

    def test_R18_m4_entry_auto_file_name_has_seconds(self):
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertIn(NOTICE, out)
        name = Path(json.loads(H.missed_path("abcde").read_text(encoding="utf-8"))["auto_file"]).name
        self.assertRegex(name, r"^HANDOFF-abcde-AUTO-\d{4}-\d{6}\.md$", "AUTO 檔名帶到秒(同一分鐘兩次不撞名)")


class HookReceipts(ClaudeHook):
    """r17 M1 下一輪的判準 + M3 交接檔帶 handoff-fields 圍欄(doctor 與右欄讀的)。"""

    def three_steps(self):
        draft = self.td / "draft.md"
        p = self.aib("collect", "--seat", "zz9q/abcde", "--repo", str(self.repo), "--out", str(draft))
        self.assertEqual(p.returncode, 0, p.stderr)
        draft.write_text(fill(draft.read_text(encoding="utf-8"), s1="建議 R1", s2="不知道 R2", s3="重來 R3"),
                         encoding="utf-8")
        for step in ("1", "2", "3"):
            p = self.aib("write", "--step", step, "--file", str(draft), "--seat", "zz9q/abcde")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return Path(json.loads(H.receipt_path("abcde").read_text(encoding="utf-8"))["file"])

    def fields(self, path):
        tool = ROOT / "tools" / "dispatch" / "handoff_fields.py"
        p = subprocess.run([sys.executable, str(tool), str(path), "--json", "--strict-fixed"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else {}), p.stderr

    def test_M1e_entry_receipt_before_the_last_collect_does_not_count(self):
        self.make_repo()
        self.three_steps()
        self.assertTrue(H.status("abcde")["this_round_receipt"], "寫完三步 ⇒ 這一輪有收據")
        p = self.aib("collect", "--seat", "zz9q/abcde", "--repo", str(self.repo), "--out", str(self.td / "again.md"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(H.status("abcde")["this_round_receipt"], {}, "之後又 collect 卻沒再寫 ⇒ 收據不算")
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertIn(NOTICE, out, "下一輪開始:最後一次 collect 之後沒有收據 ⇒ 缺漏")

    def test_M1e2_control_receipt_after_the_last_collect_counts(self):
        self.make_repo()
        self.three_steps()
        self.assertNotIn(NOTICE, self.run_hook("clear"), "對照臂:照規矩寫完 ⇒ 下一輪沒有提醒")
        self.assertIn(NOTICE, self.run_hook("clear"), "上一輪的收據不能算到這一輪(這一輪沒寫)")

    def test_M3a_entry_written_and_auto_files_pass_handoff_fields(self):
        self.make_repo()
        written = self.three_steps()
        rc, info, err = self.fields(written)
        self.assertEqual(rc, 0, "交接檔要過 handoff_fields.py --strict-fixed:%s %s" % (err, info.get("missing")))
        self.assertEqual((info["layer"], info["from_seat"]), ("fixed", "abcde"))
        H.record_round("abcde", "test", ts=time.time() + 1)       # 新的一輪開始,這一輪沒寫
        time.sleep(1.2)
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertIn(NOTICE, out)
        auto = json.loads(H.missed_path("abcde").read_text(encoding="utf-8"))["auto_file"]
        body = self.wait_file(auto)
        rc, info, err = self.fields(auto)
        self.assertEqual(rc, 0, "AUTO 檔也要過:%s %s" % (err, info.get("missing")))
        self.assertIn(H.T("aib.handoff.fields.auto_missing"), body, "人知道的幾格要寫明「上一棒跳過交接」")

    def test_M3b_entry_doctor_handoff_check_is_ok(self):
        self.make_repo()
        written = self.three_steps()
        doctor = ROOT / "src" / "bootstrap" / "aib_doctor.py"
        p = subprocess.run([sys.executable, str(doctor), "--state-root", str(self.home), "--repo-root", str(ROOT),
                            "--provider", "claude", "--json"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
        checks = {c["id"]: c for c in json.loads(p.stdout)["checks"]}
        got = checks["handoff_fields"]
        self.assertEqual(got["status"], "ok", got)
        self.assertTrue(got["detail"].startswith(written.name), got)
        self.assertNotIn("△", got["detail"], "要是固定層(有圍欄),不是舊單")

    def test_R18_m5_entry_missed_flag_clears_after_a_later_receipt(self):
        out = self.hook_out(subprocess.run([sys.executable, str(HOOK_DIR / "persona_reinject_hook.py"),
                                            "session-start"], **self.hook_kw("clear")))
        self.assertIn(NOTICE, out)
        p = self.aib("status", "--seat", "zz9q/abcde", "--json")
        self.assertTrue(json.loads(p.stdout)["missed"], "上一輪缺漏 ⇒ status 印旗標:" + p.stdout + p.stderr)
        self.three_steps()                                   # 這一輪照規矩交接
        self.assertFalse(H.missed_path("abcde").exists(), "之後一輪交了收據 ⇒ 缺漏旗標收掉")
        p = self.aib("status", "--seat", "zz9q/abcde", "--json")
        self.assertEqual(json.loads(p.stdout)["missed"], {}, "status 不再印過期的缺漏:" + p.stdout + p.stderr)


@unittest.skipUnless(HAS_ONBOARD_TEMPLATE, NO_TEMPLATE_WHY)
class Onboard(Sandbox):
    def onboard_args(self, window):
        return ["--prepare-self-fetch", "--window", "@%d" % window, "--provider", "codex", "--seats", str(self.seats)]

    def onboard_kw(self):
        return {"capture_output": True, "text": True, "stdin": subprocess.DEVNULL, "timeout": 120,
                "env": dict(os.environ, EZ2_AIB_INSTANCE_HOME=str(self.inst))}

    def onboard_out(self, p):
        self.assertEqual(p.returncode, 0, p.stderr)
        return Path(p.stdout.strip()).read_text(encoding="utf-8")

    def onboard(self, window):
        return self.onboard_out(subprocess.run([sys.executable, str(COCKPIT / "seat_onboard.py")]
                                               + self.onboard_args(window), **self.onboard_kw()))

    def test_H7_entry_onboard_non_claude_predecessor_missed(self):
        self.seat_rec(3, NEWSEAT, provider="codex", forked_from=SEAT)
        body = self.onboard_out(subprocess.run([sys.executable, str(COCKPIT / "seat_onboard.py")]
                                               + self.onboard_args(3), **self.onboard_kw()))
        self.assertIn(NOTICE, body)
        self.assertIn("@abcde", body.split(NOTICE, 1)[1])
        # 10-15 整合(#58 × #50):公開版開席單模板只有一份中立版(英文,不跟 EZ2_AIB_LOCALE 走),沒有私倉的「鐵則八」。
        #   要驗的是「收尾交接那條鐵則跟著開席單送到」⇒ 從模板鐵則段取出含 aib handoff 的那一行(恰好一行),斷言它在開席單裡;
        #   不寫死任何語言的字。
        import seat_onboard as SO
        rules = SO.apply_profile((ROOT / SO.TEMPLATE_REL).read_text(encoding="utf-8"), "rules")
        rule = [ln for ln in rules.splitlines() if "aib handoff" in ln]
        self.assertEqual(len(rule), 1, "開席單鐵則段要恰好一行收尾交接規則:%r" % rule)
        self.assertIn("85%", rule[0])
        self.assertIn(rule[0], body, "收尾交接鐵則沒跟著開席單送到")
        self.assertTrue(H.missed_path("abcde").exists())

    def test_H7c_entry_onboard_main_in_process(self):
        """開席單的 main(aib seat onboard 叫的就是它),行程內呼叫:前身沒交接 ⇒ 開席單後面接提醒。"""
        import seat_onboard as SO
        self.seat_rec(3, NEWSEAT, provider="codex", forked_from=SEAT)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.onboard_kw()["env"], clear=True), contextlib.redirect_stdout(buf):
            rc = SO.main(self.onboard_args(3))
        self.assertEqual(rc or 0, 0)
        body = Path(buf.getvalue().strip()).read_text(encoding="utf-8")
        self.assertIn(NOTICE, body)
        self.assertIn("@abcde", body.split(NOTICE, 1)[1])

    def test_H7b_control_predecessor_with_receipt(self):
        self.write_receipt("abcde")
        self.seat_rec(3, NEWSEAT, provider="codex", forked_from=SEAT)
        self.assertNotIn(NOTICE, self.onboard(3))
        self.seat_rec(4, "00000000-0000-4000-8000-0000000ggggg", provider="codex")
        self.assertNotIn(NOTICE, self.onboard(4), "沒有前身 ⇒ 沒有提醒")

    def test_H8_inline_family_gets_one_lint_clean_line(self):
        import seat_onboard as SO
        import r30_lint
        self.seat_rec(3, NEWSEAT, provider="copilot", forked_from=SEAT)
        os.environ["EZ2_AIB_INSTANCE_HOME"] = str(self.inst)
        raw = (ROOT / SO.TEMPLATE_REL).read_bytes()
        got = []

        def fake_deliver(target, body, dry_run=False):
            got.append(body)
            return 0, {}
        with contextlib.redirect_stderr(io.StringIO()):
            SO.onboard_one("@3", "fffff", "dev", raw.decode("utf-8"), SO.template_sha256(raw),
                           str(self.td / "ledger.jsonl"), dry_run=True, deliver_fn=fake_deliver,
                           provider="copilot", target_code="fffff")
        self.assertEqual(len(got), 1)
        self.assertIn(NOTICE, got[0])
        self.assertNotIn("\n", got[0])
        self.assertEqual([f for f in r30_lint.lint(got[0])], [])


class SeatLoad(Sandbox):
    def load_args(self, provider):
        return ["apply", "--provider", provider, "--from", SEAT, "--to", NEWSEAT, "--window", "@3",
                "--aib-home", str(self.home)]

    def load_kw(self):
        return {"capture_output": True, "text": True, "stdin": subprocess.DEVNULL, "timeout": 120,
                "env": dict(os.environ, HOME=str(self.td), CLAUDE_CONFIG_DIR=str(self.td / "claude"),
                            EZ2_CODEX_SESSIONS_ROOT=str(self.td / "codex-sessions"))}

    def load_out(self, p):
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def load(self, provider):
        return self.load_out(subprocess.run([sys.executable, str(COCKPIT / "seat_load.py")] + self.load_args(provider),
                                            **self.load_kw()))

    def test_H9_entry_seat_load_apply_appends_notice(self):
        d = self.load_out(subprocess.run([sys.executable, str(COCKPIT / "seat_load.py")] + self.load_args("codex"),
                                         **self.load_kw()))
        self.assertEqual(d["handoff_check"], "ok", d)
        body = Path(d["deliver_file"]).read_text(encoding="utf-8")
        self.assertTrue(body.rstrip().split("\n\n")[-1].strip().startswith(NOTICE), body[-400:])
        self.assertTrue(H.missed_path("abcde").exists())
        self.assertEqual(self.load("grokbot")["handoff_check"], "not_inserted", "橋接席沒有開場說明可接")

    def test_E3_entry_api_predecessor_notice_asks_a_human(self):
        """前一席是 API 席(nvidia,自己不能跑指令)⇒ 提醒換說法:請真人代跑。對照臂:codex 前身照舊說法。"""
        self.seat_rec(2, SEAT, provider="nvidia", cwd=str(self.repo), started_epoch=time.time() - 3600)
        d = self.load_out(subprocess.run([sys.executable, str(COCKPIT / "seat_load.py")] + self.load_args("codex"),
                                         **self.load_kw()))
        self.assertEqual(d["handoff_check"], "ok", d)
        last = Path(d["deliver_file"]).read_text(encoding="utf-8").rstrip().split("\n\n")[-1].strip()
        self.assertTrue(last.startswith(NOTICE), last)
        self.assertIn(H.T("aib.handoff.notice.predecessor_api", code="abcde", since="", file="")[-12:], last)
        self.assertNotIn(H.T("aib.handoff.notice.predecessor_api", code="abcde", since="", file="")[-12:],
                         H.notice_text("abcde", 0, "f", "fffff", "codex"), "會跑指令的家照舊說法")

    def test_H9c_entry_seat_load_main_in_process(self):
        """載入舊席位的 main(aib 載入流程叫的就是它),行程內呼叫:開場說明最後一段是提醒。"""
        import seat_load as SL
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.load_kw()["env"], clear=True), contextlib.redirect_stdout(buf):
            rc = SL.main(self.load_args("codex"))
        self.assertEqual(rc or 0, 0)
        d = json.loads(buf.getvalue())
        self.assertEqual(d["handoff_check"], "ok", d)
        last = Path(d["deliver_file"]).read_text(encoding="utf-8").rstrip().split("\n\n")[-1].strip()
        self.assertTrue(last.startswith(NOTICE), last)

    def test_H9b_control_receipt_present_is_clean(self):
        self.write_receipt("abcde")
        self.assertEqual(self.load("codex")["handoff_check"], "clean")


class RuleLine(unittest.TestCase):
    def test_H10_rule_line_everywhere(self):
        # 人格包、API 席工具、nvidia 席三支模組名在執行期拼:本格只驗「規則那一行在」,不替那三層宣稱到達性
        # (到達性掃描器讀碼裡的模組引用;它們的入口在別的尺)。
        import importlib
        P = importlib.import_module("persona" + "_pack")
        import seat_onboard as SO
        D = importlib.import_module("aib_dm" + "_tool")
        if HAS_ONBOARD_TEMPLATE:   # 公開版缺開席單模板(見 NO_TEMPLATE_WHY):那一段先不量,其餘三層照量
            rules = SO.apply_profile((ROOT / SO.TEMPLATE_REL).read_text(encoding="utf-8"), "rules")
            line = [ln for ln in rules.splitlines() if "aib handoff" in ln]
            self.assertEqual(len(line), 1, "開席單鐵則段恰好一行")
            self.assertIn("85%", line[0])
        text, _ = P.assemble("測試", "abc123", "deadbeef", [("", "卡片正文")], "", [], "core.handoff", "C/x",
                             "abcde")
        self.assertIn(P.HANDOFF_RULE, text.split("── 寫回", 1)[1], "人格包寫回段")
        for loc in ("zh-TW", "en", "ja", "ko"):
            self.assertIn("aib handoff", D.dm_system_snippet(loc), loc)
            self.assertIn("85%", D.dm_system_snippet(loc), loc)
        nv = importlib.import_module("nvidia" + "_cli")
        self.assertIn(D.handoff_rule_line(), nv.DM_SYSTEM["content"], "API 席系統說明")


# ---------------------------------------------------------------- 還原刀
COPY_DIRS = ("src", "tests", "i18n", "docs/templates", "tools/governance", "tools/dispatch", "schemas", "policy")


def _copy_tree(tc):
    tmp = Path(tempfile.mkdtemp(prefix="handoff-knife-", dir=str(aib_tmp_root())))
    tc.addCleanup(shutil.rmtree, tmp, True)
    copy = tmp / "tree"
    for d in COPY_DIRS:
        if not (ROOT / d).is_dir():
            continue
        shutil.copytree(ROOT / d, copy / d, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    # r18:aib 啟動要讀倉根的 VERSION;副本少了它,每一格走 `aib handoff` 的都在第一行就紅 ——
    # 刀不管落沒落都「紅」,量不到東西。先複製,再確認副本裡的 aib 真的跑得起來。
    shutil.copy2(ROOT / "VERSION", copy / "VERSION")
    p = subprocess.run(["bash", str(copy / "src" / "cockpit" / "aib"), "handoff", "--help"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=120)
    tc.assertEqual(p.returncode, 0, "副本樹裡的 aib 要跑得起來(不然入口格在刀下必紅、刀量不到):" + p.stderr[-400:])
    return copy


def _copy_pythonpath(copy):
    """aib test 的 PYTHONPATH 指著原樹的 src/cockpit 等 ⇒ 副本裡的子行程會 import 原樹的模組、刀落空。
    每一條在原樹底下的都換成副本裡的同一條。"""
    root = ROOT.resolve()
    out = []
    for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if not p:
            continue
        try:
            out.append(str(copy / Path(p).resolve().relative_to(root)))
        except ValueError:
            out.append(p)
    return os.pathsep.join(out)


def _run_copy(copy, test_file, cells):
    """子跑副本樹裡的本檔(檔名字面寫在每一把刀裡,掃描器認得出「刀會跑測試」)。"""
    assert test_file == Path(__file__).name, (test_file, Path(__file__).name)
    r = subprocess.run([sys.executable, str(copy / "tests" / test_file)] + list(cells),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=_copy_pythonpath(copy)))
    out = r.stdout + r.stderr
    return r.returncode, out, set(re.findall(r"^FAIL: (\S+)", out, re.M))


class RestoreKnives(unittest.TestCase):
    """刀的每一步寫在格子裡(層檔名、恰好一次、替換、子跑副本樹的本檔、抄 FAIL:/ERROR:、宣稱紅的格名)。"""

    def test_Z1_knife_validate_accepts_empty_reds_write_cells(self):
        claimed = {"test_H2_write_refuses_empty_and_out_of_order", "test_H4_entry_aib_handoff_write_steps_and_status"}
        copy = _copy_tree(self)
        layer = copy / "src/cockpit/aib_handoff.py"
        old = '    bad = [k for k in STEP_NEEDS[step] if not section_body(parsed.get(k, ""))]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    return {k: section_body(parsed.get(k, "")) for k in SECTIONS}   # knife\n'),
                         encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["Write.test_H2_write_refuses_empty_and_out_of_order",
                                          "Write.test_H4_entry_aib_handoff_write_steps_and_status"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z2_knife_receipt_check_always_clean_reds_hook_and_onboard(self):
        claimed = {"test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies",
                   "test_H7_entry_onboard_non_claude_predecessor_missed"}
        cells = ["ClaudeHook.test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies",
                 "Onboard.test_H7_entry_onboard_non_claude_predecessor_missed"]
        if not HAS_ONBOARD_TEMPLATE:   # 開席單那一格在公開版先跳過(見 NO_TEMPLATE_WHY)⇒ 只宣稱 hook 那一格
            claimed.discard("test_H7_entry_onboard_non_claude_predecessor_missed")
            cells = cells[:1]
        copy = _copy_tree(self)
        layer = copy / "src/cockpit/aib_handoff.py"
        old = '    if receipt_between(code, prev_start, home=home):\n        return ""\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if True:   # knife\n        return ""\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", cells)
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z3_knife_aib_dispatch_reds_entry(self):
        claimed = {"test_H4_entry_aib_handoff_write_steps_and_status"}
        copy = _copy_tree(self)
        layer = copy / "src/cockpit/aib"
        old = '  aib_delegate_subcommand py "$AIB_SOURCE_ROOT/src/cockpit/aib_handoff.py" "$@"\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, "  exit 0   # knife\n"), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["Write.test_H4_entry_aib_handoff_write_steps_and_status"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z4_knife_hook_drops_notice_reds_hook_entry(self):
        claimed = {"test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies",
                   "test_M2d_entry_hook_main_in_process"}
        copy = _copy_tree(self)
        layer = copy / "src/identity/persona_reinject_hook.py"
        old = "    notice = handoff_notice(source, payload)\n"
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    notice = ""   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["ClaudeHook.test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies",
                                                                             "FastHook.test_M2d_entry_hook_main_in_process"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    @unittest.skipUnless(HAS_ONBOARD_TEMPLATE, NO_TEMPLATE_WHY)
    def test_Z5_knife_onboard_drops_notice_reds_onboard_entry(self):
        claimed = {"test_H7_entry_onboard_non_claude_predecessor_missed", "test_H7c_entry_onboard_main_in_process"}
        copy = _copy_tree(self)
        layer = copy / "src/cockpit/seat_onboard.py"
        old = "        _handoff = handoff_notice_block(args.window, args.seats or None)\n"
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        _handoff = ""   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["Onboard.test_H7_entry_onboard_non_claude_predecessor_missed",
                                                                             "Onboard.test_H7c_entry_onboard_main_in_process"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z6_knife_seat_load_skips_check_reds_load_entry(self):
        claimed = {"test_H9_entry_seat_load_apply_appends_notice", "test_H9c_entry_seat_load_main_in_process"}
        copy = _copy_tree(self)
        layer = copy / "src/cockpit/seat_load.py"
        old = ('    out["handoff_check"] = insert_handoff_notice(prep["preamble_file"], old_uuid, new_uuid, '
               'aib_home, kind)\n')
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    out["handoff_check"] = "clean"   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["SeatLoad.test_H9_entry_seat_load_apply_appends_notice",
                                                                             "SeatLoad.test_H9c_entry_seat_load_main_in_process"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z7_knife_persona_pack_drops_rule_reds_rule_cell(self):
        claimed = {"test_H10_rule_line_everywhere"}
        copy = _copy_tree(self)
        layer = copy / "src" / "identity" / ("persona" + "_pack.py")       # 執行期拼(同 H10)
        old = '                  "- " + HANDOFF_RULE,\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, ""), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["RuleLine.test_H10_rule_line_everywhere"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z8_knife_api_snippet_drops_rule_reds_rule_cell(self):
        claimed = {"test_H10_rule_line_everywhere"}
        copy = _copy_tree(self)
        layer = copy / "src" / "cockpit" / ("aib_dm" + "_tool.py")       # 執行期拼(同 H10)
        old = ' + " " + handoff_rule_line(locale)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, "\n"), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["RuleLine.test_H10_rule_line_everywhere"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z9_knife_i18n_notice_loses_tag_reds_hook_entry(self):
        claimed = {"test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies"}
        copy = _copy_tree(self)
        layer = copy / "i18n/zh-TW.json"
        old = '"aib.handoff.notice.self": "[AIB 交接缺漏] '
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '"aib.handoff.notice.self": "'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["ClaudeHook.test_H6_entry_claude_clear_without_receipt_auto_collects_and_notifies"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    @unittest.skipUnless(HAS_ONBOARD_TEMPLATE, NO_TEMPLATE_WHY)
    def test_Z10_knife_template_drops_rule_reds_rule_and_onboard(self):
        claimed = {"test_H10_rule_line_everywhere", "test_H7_entry_onboard_non_claude_predecessor_missed"}
        copy = _copy_tree(self)
        layer = copy / "docs/templates/SEAT-ONBOARD.md"
        # 10-15 整合(#58 × #50):刀錨換成公開版中立模板裡收尾交接那條鐵則(全檔唯一)。
        old = "3. At about 85% context use, or when the human says to prepare a clear, run `aib handoff`.\n"
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, ""), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ["RuleLine.test_H10_rule_line_everywhere",
                                          "Onboard.test_H7_entry_onboard_non_claude_predecessor_missed"])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z11_knife_auto_collect_inline_makes_hook_slow_reds_hook_entry(self):
        claimed = {"test_M2a_entry_hook_with_stuck_git_is_fast_and_genes_come_first"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        pid = spawn_auto_collect(code, cwd, auto_file, home)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        auto_collect_main(code, cwd, auto_file)   # knife: 同步補抓\n        pid = -1\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_M2a_entry_hook_with_stuck_git_is_fast_and_genes_come_first'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z12_knife_hook_prints_notice_before_genes_reds_hook_entry(self):
        claimed = {"test_M2a_entry_hook_with_stuck_git_is_fast_and_genes_come_first"}
        copy = _copy_tree(self)
        layer = copy / 'src/identity/persona_reinject_hook.py'
        old = '    if text:                         # r17 M2:人格/基因先印出去,後面的交接檢查就算出事也不會把它拖走\n        print(text, flush=True)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, "    print(handoff_notice(source, payload))   # knife: 交接先跑先印\n"
                                           "    if text:\n        print(text, flush=True)\n    return 0\n"), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_M2a_entry_hook_with_stuck_git_is_fast_and_genes_come_first'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z13_knife_no_deadline_clamp_reds_collect_budget_entry(self):
        claimed = {"test_M2b_entry_collect_respects_the_total_budget"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    return timeout if dl is None else min(timeout, dl - time.time())\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    return timeout   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_M2b_entry_collect_respects_the_total_budget'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z14_knife_git_optional_locks_dropped_reds_entry(self):
        claimed = {"test_M2c_entry_git_calls_carry_no_optional_locks"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '_SUB_ENV_EXTRA = {"GIT_OPTIONAL_LOCKS": "0"}\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '_SUB_ENV_EXTRA = {}   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_M2c_entry_git_calls_carry_no_optional_locks'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z15_knife_enabled_at_cutoff_dropped_reds_entry(self):
        claimed = {"test_E1_first_run_writes_enabled_at_and_does_not_flag_older_rounds"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if prev_start < enabled_at(home):\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_E1_first_run_writes_enabled_at_and_does_not_flag_older_rounds'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z16_knife_empty_round_check_dropped_reds_entry(self):
        claimed = {"test_E2_empty_round_double_clear_is_not_flagged"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if prev_entry is not None and round_was_empty(prev_entry):\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_E2_empty_round_double_clear_is_not_flagged'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z17_knife_api_wording_dropped_reds_load_entry(self):
        claimed = {"test_E3_entry_api_predecessor_notice_asks_a_human"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        key += "_api"\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        pass   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['SeatLoad.test_E3_entry_api_predecessor_notice_asks_a_human'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])


    def test_Z18_knife_step_optional_again_reds_no_step_entry(self):
        claimed = {"test_M1a_entry_write_without_step_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    w.add_argument("--step", type=int, choices=(1, 2, 3), required=True)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    w.add_argument("--step", type=int, choices=(1, 2, 3), default=3)   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_M1a_entry_write_without_step_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z19_knife_thin_check_dropped_reds_trivial_cell(self):
        claimed = {"test_M1b_trivial_sections_are_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    thin = [k for k in STEP_NEEDS[step] if _thin(secs[k])]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    thin = []   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_M1b_trivial_sections_are_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z20_knife_header_seat_unchecked_reds_wrong_seat_entry(self):
        claimed = {"test_M1c_entry_wrong_seat_header_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if m.group(2) != code:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_M1c_entry_wrong_seat_header_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z21_knife_nonce_unchecked_reds_stale_entry(self):
        claimed = {"test_M1d_entry_stale_or_auto_draft_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if m.group(4) != last["nonce"]:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_M1d_entry_stale_or_auto_draft_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z22_knife_receipt_ignores_last_collect_reds_hook_entry(self):
        claimed = {"test_M1e_entry_receipt_before_the_last_collect_does_not_count"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '            and r.get("collect_nonce") == last["nonce"]]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '            ]   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['HookReceipts.test_M1e_entry_receipt_before_the_last_collect_does_not_count'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z23_knife_seat_mismatch_not_recorded_reds_entry(self):
        claimed = {"test_M1f_entry_writing_for_another_seat_is_recorded"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '                   "writer_seat": writer, "seat_mismatch": bool(writer) and writer != code,\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '                   "writer_seat": writer, "seat_mismatch": False,   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_M1f_entry_writing_for_another_seat_is_recorded'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z24_knife_written_file_without_fields_reds_doctor_and_fields_entries(self):
        claimed = {"test_M3a_entry_written_and_auto_files_pass_handoff_fields", "test_M3b_entry_doctor_handoff_check_is_ok"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        target.write_text(with_fields(text, facts, home), encoding="utf-8")\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        target.write_text(text, encoding="utf-8")   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['HookReceipts.test_M3a_entry_written_and_auto_files_pass_handoff_fields', 'HookReceipts.test_M3b_entry_doctor_handoff_check_is_ok'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z25_knife_auto_file_without_fields_reds_fields_entry(self):
        claimed = {"test_M3a_entry_written_and_auto_files_pass_handoff_fields"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        lines += ["", "> " + T("aib.handoff.draft.auto_note"), ""] + fields_block(facts, auto=True)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        lines += ["", "> " + T("aib.handoff.draft.auto_note")]   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['HookReceipts.test_M3a_entry_written_and_auto_files_pass_handoff_fields'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z26_knife_padded_check_dropped_reds_padding_entry(self):
        claimed = {"test_R18_m1a_entry_padded_sections_are_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    padded = [k for k in STEP_NEEDS[step] if _padded(secs[k])]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    padded = []   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m1a_entry_padded_sections_are_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z27_knife_similarity_back_to_exact_reds_near_duplicate_entry(self):
        claimed = {"test_R18_m1b_entry_near_duplicate_sections_are_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '            if similarity(secs[a], secs[b]) >= MAX_PAIR_SIMILARITY]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '            if _norm(secs[a]) == _norm(secs[b])]   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m1b_entry_near_duplicate_sections_are_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z28_knife_reuse_check_dropped_reds_resubmit_entry(self):
        claimed = {"test_R18_m1c_entry_previous_round_draft_with_new_nonce_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    reused = reused_sections(code, secs, home)\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    reused = []   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m1c_entry_previous_round_draft_with_new_nonce_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z29_knife_facts_hash_unchecked_reds_tampered_facts_entry(self):
        claimed = {"test_R18_m1d_entry_tampered_facts_block_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if stamp.get("facts_sha") and facts_sha(parse_draft(text).get(FACTS, "")) != stamp["facts_sha"]:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m1d_entry_tampered_facts_block_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z30_knife_step_consistency_dropped_reds_step3_entry(self):
        claimed = {"test_R18_m1e_entry_step3_section_one_differs_from_step1_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if changed:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m1e_entry_step3_section_one_differs_from_step1_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z31_knife_hook_ignores_resume_reds_resume_entry(self):
        claimed = {"test_R18_m2a_entry_resume_joins_the_round_and_is_checked_at_next_clear"}
        copy = _copy_tree(self)
        layer = copy / 'src/identity/persona_reinject_hook.py'
        old = '    if source not in HANDOFF_SOURCES and source != RESUME_SOURCE:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if source not in HANDOFF_SOURCES:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_R18_m2a_entry_resume_joins_the_round_and_is_checked_at_next_clear'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z32_knife_rounds_drop_resume_lines_reds_resume_entry(self):
        claimed = {"test_R18_m2a_entry_resume_joins_the_round_and_is_checked_at_next_clear"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        elif isinstance(d.get("resume_at"), (int, float)) and out and d.get("transcript"):\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        elif False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_R18_m2a_entry_resume_joins_the_round_and_is_checked_at_next_clear'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z33_knife_missing_transcript_counts_empty_again_reds_entry(self):
        claimed = {"test_R18_m2b_entry_missing_transcript_is_checked"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '        fh = open(tp, "rb")\n    except OSError:\n        return False\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '        fh = open(tp, "rb")\n    except FileNotFoundError:   # knife\n        return True\n    except OSError:\n        return False\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_R18_m2b_entry_missing_transcript_is_checked'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z34_knife_malformed_steps_crash_again_reds_entry(self):
        claimed = {"test_R18_m3_entry_malformed_steps_json_does_not_crash"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    steps = doc.get("steps") if isinstance(doc, dict) else None\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    steps = doc.get("steps")   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R18_m3_entry_malformed_steps_json_does_not_crash'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z35_knife_auto_name_without_seconds_reds_entry(self):
        claimed = {"test_R18_m4_entry_auto_file_name_has_seconds"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '"HANDOFF-%s-AUTO-%s.md" % (code, time.strftime("%m%d-%H%M%S", '
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '"HANDOFF-%s-AUTO-%s.md" % (code, time.strftime("%m%d-%H%M", '), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_R18_m4_entry_auto_file_name_has_seconds'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z36_knife_missed_flag_never_superseded_reds_entry(self):
        claimed = {"test_R18_m5_entry_missed_flag_clears_after_a_later_receipt"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    prev = flag.get("prev_round_ts")\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    prev = None   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['HookReceipts.test_R18_m5_entry_missed_flag_clears_after_a_later_receipt'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z37_knife_cross_round_similarity_back_reds_honest_no_change_entry(self):
        claimed = {"test_R19_a_entry_honest_no_change_round_is_accepted"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    hits = [k for k in SECTIONS if _norm(secs[k]) == _norm(section_body(old.get(k, "")))]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    hits = [k for k in SECTIONS if similarity(secs[k], section_body(old.get(k, ""))) >= 0.9]   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R19_a_entry_honest_no_change_round_is_accepted'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z38_knife_prompt_check_dropped_reds_prompt_entry(self):
        claimed = {"test_R19_b_entry_prompt_text_pasted_back_is_refused"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    prompt = [k for k in STEP_NEEDS[step] if _prompt_only(secs[k])]\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    prompt = []   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R19_b_entry_prompt_text_pasted_back_is_refused'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z39_knife_step1_compares_needed_only_reds_step1_entry(self):
        claimed = {"test_R19_c_entry_step1_refuses_near_identical_sections_before_writing"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    filled = [k for k in SECTIONS if k in STEP_NEEDS[step] or secs[k]]      # r19:第 1 步就比已經填了的段\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    filled = list(STEP_NEEDS[step])   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['WriteGuards.test_R19_c_entry_step1_refuses_near_identical_sections_before_writing'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])

    def test_Z40_knife_resume_reads_whole_old_conversation_reds_resume_entry(self):
        claimed = {"test_R19_d_entry_resumed_old_conversation_counts_only_messages_after_the_resume"}
        copy = _copy_tree(self)
        layer = copy / 'src/cockpit/aib_handoff.py'
        old = '    if marks:\n'
        text = layer.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "刀錨必須全檔恰好一次")
        layer.write_text(text.replace(old, '    if False:   # knife\n'), encoding="utf-8")
        self.assertEqual(layer.read_text(encoding="utf-8").count(old), 0, "刀沒落")
        rc, out, names = _run_copy(copy, "test-handoff-flow-abb89-1015.py", ['FastHook.test_R19_d_entry_resumed_old_conversation_counts_only_messages_after_the_resume'])
        self.assertNotIn("ERROR:", out, out[-800:])
        self.assertEqual(rc, 1, "刀下應紅:rc=%s\n%s" % (rc, out[-800:]))
        self.assertTrue(claimed <= names, out[-800:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
