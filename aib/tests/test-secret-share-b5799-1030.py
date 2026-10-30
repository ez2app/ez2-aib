#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib secret copy / paste(指令庫第二步;@aeb40 10-30 §9):值只在核心行程內走 stdin,每次一按 Touch ID。

P1 類別:兩個 site 都是 secret(高風險、max_window 0、時間窗不開);句子帶完整金鑰櫃名字 / 艙位碼/席碼,copy 句尾有 30 秒
P2 copy 正路:Touch ID 過 ⇒ pbcopy 的 stdin = 值;清除行程拿到的 stdin 是 sha256 不是值;argv / env / 輸出都沒有值
P3 copy 沒過:Touch ID 取消 / AIB_TOUCHID=0 / 不是 Mac / 席位呼叫 ⇒ 不碰剪貼簿;句子太長 ⇒ too_long、不截
P4 清剪貼簿:先起清除行程、起不來就不 pbcopy(clear_failed);交不了雜湊就當場清;固定 30 秒;還是同一份才清(pbcopy 空字串);被換掉 ⇒ 不清
P5 paste 正路:load-buffer -b <隨機名> -(值走 stdin)⇒ paste-buffer -p -d ⇒ delete-buffer;不送 Enter;argv 沒有值
P6 paste 擋:只給席碼 / 艙位碼不對 / 找不到 ⇒ no_seat;不在提示列 ⇒ seat_busy(不彈窗);Touch ID 沒過 ⇒ 不碰 tmux;
   load-buffer 失敗 ⇒ paste_failed 且照樣 delete-buffer;按指紋期間席位檔換了 ⇒ seat_busy、沒叫 tmux
P7 讀金鑰櫃:SE 專用 / 只放遠端 / 不認得的名字 ⇒ no_vault;讀取開關擋下 ⇒ vault_off;值有換行 / 控制字元 ⇒ bad_value
P8 紀錄:secret-share.jsonl 每列只有 at / op / ok / why;金鑰櫃名字、值都不進紀錄與輸出
P10 順序(@aeb40 10-30 03:10):先查讀取開關 ⇒ Touch ID ⇒ 才讀值;開關擋下 ⇒ vault_off、不彈指紋、不讀值
P9 真 tmux(有 tmux 才跑):paste 期間每次叫 tmux 都掃一次 ps -axww,掃出來沒有值;那一席的畫面打出了值、沒有送出
刀(手動):K1 paste 改 send-keys -l 值 ⇒ P5 / P9 紅;K2 copy 拿掉 Touch ID ⇒ P3 紅;K3 清除不比雜湊 ⇒ P4 紅;
    K4 site 類別改 seat_input ⇒ P1 紅;K5 先讀值再彈指紋(拿掉 precheck)⇒ P10 紅;
    K6 先 pbcopy 再起清除行程 ⇒ P2 紅;K7 按指紋後不重查 fresh_target ⇒ P6 紅
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
import aib_secrets as S  # noqa: E402
import approve_gate as AG  # noqa: E402
import secret_share as SS  # noqa: E402
import seat_panel as SPN  # noqa: E402
import secret_read_gate as GATE  # noqa: E402

VALUE = "sk-ant-api03-" + "Qw7" * 20
NAME = "ANTHROPIC_API_KEY"
CABIN, UUID = "96e0", "e6d260cb-3c3e-4568-b9e1-5ba955c12ab3"
CODE = UUID[-5:]


class Done(Exception):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-secret-share-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {"EZ2_AIB_STATE_ROOT": str(self.root), "EZ2_AIB_HOME": str(self.root), "HOME": str(self.root),
                    "EZ2_AIB_LOCALE": "zh-TW", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        p = mock.patch.dict(os.environ, self.env, clear=False)
        p.start()
        self.addCleanup(p.stop)
        for k in ("TMUX", "TMUX_PANE") + tuple(__import__("seat_act").SESSION_ENVS):
            os.environ.pop(k, None)
        self.reasons = []
        self.calls = []
        self.order = []
        p = mock.patch.object(S, "secret", side_effect=lambda name, env=None, fill=True: self.order.append("read") or VALUE)
        p.start()
        self.addCleanup(p.stop)
        self.gate = None          # None = 開關放行;否則丟這個原因的 SecretError
        def gate(name, env=None, **kw):
            self.order.append("gate")
            if self.gate:
                raise S.SecretError(self.gate, name)
        p = mock.patch.object(GATE, "assert_read_allowed", side_effect=gate)
        p.start()
        self.addCleanup(p.stop)

    def clearer(self, fail=False):
        """假清除子行程:記下 stdin 收到什麼;fail = 起不來(回 None)。"""
        def start():
            self.order.append("clearer")
            if fail:
                return None
            buf = io.BytesIO()
            buf.close = lambda: self.cleared.append(buf.getvalue())
            return type("P", (), {"stdin": buf})()
        self.cleared = []
        return start

    def touch(self, ok=True):
        def t(r):
            self.order.append("touch")
            self.reasons.append(r)
            return {"ok": ok, "why": "ok" if ok else "cancel"}
        return t

    def run_ok(self, argv, **kw):
        self.calls.append((list(argv), kw.get("input"), dict(kw.get("env") or {})))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def no_value_anywhere(self):
        for argv, _inp, env in self.calls:
            self.assertNotIn(VALUE, " ".join(map(str, argv)))
            self.assertNotIn(VALUE, json.dumps(env))

    def log_rows(self):
        p = self.root / "logs" / "secret-share.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class P1Category(Base):
    def test_both_sites_high_risk_no_window(self):
        for site in ("secret_share.copy", "secret_share.paste"):
            spec = AG.SITES[site]
            self.assertEqual(spec["mod"], "cockpit/secret_share.py")
            self.assertEqual(spec["cat"], "secret")
            self.assertTrue(AG.CATEGORIES["secret"]["high_risk"])
            self.assertEqual(AG.CATEGORIES["secret"]["max_window"], 0)
            self.assertTrue(all(v == AG.FULL for v in spec["facts"].values()), "名字 / 席位不截")

    def test_copy_sentence_full_name_and_30s(self):
        res, rc = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                             run=self.run_ok, spawn=self.clearer())
        self.assertEqual((res, rc), ({"ok": True, "cleared_after": 30}, 0))
        r = self.reasons[0]
        self.assertIn(NAME, str(r))
        self.assertIn("30", str(r))
        self.assertEqual(r.category, "secret")
        self.assertFalse(AG.window_open(r))


class P2Copy(Base):
    def test_value_only_on_stdin(self):
        res, rc = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                             run=self.run_ok, spawn=self.clearer())
        self.assertEqual(rc, 0)
        self.assertEqual([(a, i) for a, i, _e in self.calls], [([SS.PBCOPY], VALUE)])
        self.assertEqual(self.cleared, [hashlib.sha256(VALUE.encode()).hexdigest().encode()], "清除行程只拿到雜湊")
        self.assertLess(self.order.index("clearer"), len(self.order))
        self.no_value_anywhere()
        self.assertNotIn(VALUE, json.dumps(res))

    def test_clearer_first_or_no_copy(self):
        """#435 R1 L4:清除行程起不來 ⇒ 不 pbcopy、回 clear_failed;pbcopy 失敗 ⇒ 清除行程只收到空的"""
        res, rc = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                             run=self.run_ok, spawn=self.clearer(fail=True))
        self.assertEqual((res, rc), ({"ok": False, "why": "clear_failed"}, 4))
        self.assertEqual(self.calls, [], "沒排上清除就不放剪貼簿")
        def bad_copy(argv, **kw):
            self.calls.append((list(argv), kw.get("input"), {}))
            return subprocess.CompletedProcess(argv, 1, "", "")
        self.calls[:] = []
        res, _ = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                            run=bad_copy, spawn=self.clearer())
        self.assertEqual(res["why"], "copy_failed")
        self.assertEqual(self.cleared, [b""])

    def test_arm_fails_clears_now(self):
        """pbcopy 成功但交不了雜湊 ⇒ 當場比對清掉、回 clear_failed"""
        seen = []
        def run(argv, **kw):
            seen.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, VALUE if argv == [SS.PBPASTE] else "", "")
        broken = lambda: type("P", (), {"stdin": None})()
        res, _ = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                            run=run, spawn=broken)
        self.assertEqual(res, {"ok": False, "why": "clear_failed"})
        self.assertEqual(seen, [([SS.PBCOPY], VALUE), ([SS.PBPASTE], None), ([SS.PBCOPY], "")])

    def test_clear_later_gets_hash_not_value(self):
        got = {}

        class P:
            def __init__(self, argv, **kw):
                got["argv"], got["env"] = argv, kw.get("env")
                self.stdin = io.BytesIO()
                self.stdin.close = lambda: got.setdefault("stdin", self.stdin.getvalue())
        proc = SS.start_clearer(run=P)
        self.assertTrue(SS.arm_clearer(proc, VALUE))
        self.assertEqual(got["stdin"], hashlib.sha256(VALUE.encode()).hexdigest().encode())
        self.assertNotIn(VALUE, json.dumps(got["argv"]) + json.dumps(got["env"]))
        self.assertEqual(got["argv"][-1], "_clear")


class P3CopyRefused(Base):
    def test_denied_never_touches_clipboard(self):
        res, rc = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(False), touchid_available=lambda: True,
                             run=self.run_ok, spawn=lambda: self.fail("不該起清除"))
        self.assertEqual(res, {"ok": False, "why": "denied"})
        self.assertEqual(self.calls, [])

    def test_touchid_off_not_mac_seat_caller(self):
        env = dict(self.env, AIB_TOUCHID="0")
        res, _ = SS.do_copy(NAME, env=env, platform="darwin", touchid=self.touch(), run=self.run_ok)
        self.assertEqual(res["why"], "denied")
        res, _ = SS.do_copy(NAME, env=self.env, platform="linux", touchid=self.touch(), run=self.run_ok)
        self.assertEqual(res["why"], "not_mac")
        res, _ = SS.do_copy(NAME, env=dict(self.env, TMUX_PANE="%1"), platform="darwin", touchid=self.touch(), run=self.run_ok)
        self.assertEqual(res["why"], "caller_is_seat")
        self.assertEqual((self.reasons, self.calls), ([], []))

    def test_too_long_not_truncated(self):
        long_name = "A" + "B" * 127
        with mock.patch.object(SS, "precheck", return_value=None), mock.patch.object(SS, "read_value", return_value=(VALUE, None)):
            res, _ = SS.do_copy(long_name, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                                run=self.run_ok)
        self.assertEqual(res["why"], "too_long")
        self.assertEqual((self.reasons, self.calls), ([], []))


class P4Clear(Base):
    def fake(self, now):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, now if argv == [SS.PBPASTE] else "", "")
        return run, calls

    def test_clears_only_ours_after_30s(self):
        digest = hashlib.sha256(VALUE.encode()).hexdigest()
        slept = []
        run, calls = self.fake(VALUE)
        self.assertEqual(SS.clear_if_ours(digest, run=run, sleep=slept.append), "cleared")
        self.assertEqual(slept, [30])
        self.assertEqual(calls, [([SS.PBPASTE], None), ([SS.PBCOPY], "")])
        run, calls = self.fake("something the human copied later")
        self.assertEqual(SS.clear_if_ours(digest, run=run, sleep=lambda s: None), "changed")
        self.assertEqual(calls, [([SS.PBPASTE], None)], "被換掉就不清")

    def test_clear_entry_rejects_bad_digest(self):
        """_clear 子行程只收 64 碼 sha256;收到別的(例如值本身)⇒ 立刻 rc 1,不睡、不碰剪貼簿"""
        for bad in (VALUE, "", "z" * 64):
            r = subprocess.run([sys.executable, "-I", str(REPO / "src" / "cockpit" / "secret_share.py"), "_clear"],
                               input=bad, capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 1, repr(bad[:8]))

    def test_no_clear_parameter(self):
        out = io.StringIO()
        with mock.patch.object(SS, "do_copy", side_effect=AssertionError("不該跑")):
            rc = SS.main(["copy", NAME, "--json", "--clear-after", "600"], out=out)
        self.assertEqual((rc, json.loads(out.getvalue())), (2, {"ok": False, "why": "bad_args"}))


class Seat(Base):
    def setUp(self):
        super().setUp()
        d = self.root / "instances" / ("20261028-153924-" + CABIN) / "seats"
        d.mkdir(parents=True)
        self.rec_file = d / "w5.json"
        self.rec_file.write_text(json.dumps({"session_uuid": UUID, "socket": "aihub-" + CABIN, "window_id": "@5",
                                             "pid": 4242, "started_epoch": 1}))
        p = mock.patch.object(SPN, "pending", side_effect=lambda code, **kw: ({"at_prompt": self.at_prompt, "ask": None}, 0))
        p.start()
        self.addCleanup(p.stop)
        self.at_prompt = True

    def paste(self, seat=None, ok=True, run=None):
        return SS.do_paste(NAME, "%s/%s" % (CABIN, CODE) if seat is None else seat, env=self.env, platform="darwin", touchid=self.touch(ok),
                           touchid_available=lambda: True, run=run or self.run_ok)


class P5Paste(Seat):
    def test_stdin_buffer_no_enter(self):
        res, rc = self.paste()
        self.assertEqual((res, rc), ({"ok": True}, 0))
        argvs = [a for a, _i, _e in self.calls]
        self.assertEqual([a[3] for a in argvs], ["load-buffer", "paste-buffer", "delete-buffer"])
        load, paste, delete = argvs
        self.assertEqual(load[-1], "-")
        buf = load[load.index("-b") + 1]
        self.assertRegex(buf, r"^aib-secret-[0-9a-f]{32}$")
        self.assertIn("-d", paste)
        self.assertIn("-p", paste)
        self.assertEqual(paste[paste.index("-b") + 1], buf)
        self.assertEqual(delete[-1], buf)
        self.assertEqual(self.calls[0][1], VALUE, "值只走 load-buffer 的 stdin")
        self.assertFalse([a for a in argvs if "send-keys" in a], "不送 Enter")
        self.no_value_anywhere()

    def test_sentence_has_cabin_and_seat(self):
        self.paste()
        text = str(self.reasons[0])
        self.assertIn("%s/%s" % (CABIN, CODE), text)
        self.assertIn(NAME, text)
        self.assertEqual(self.reasons[0].category, "secret")


class P6PasteRefused(Seat):
    def test_seat_lookup(self):
        for seat in (CODE, "ffff/" + CODE, CABIN + "/abcde", "", "96e0/" + CODE + "x"):
            with self.subTest(seat=seat):
                self.assertEqual(self.paste(seat)[0], {"ok": False, "why": "no_seat"})
        self.assertEqual((self.reasons, self.calls), ([], []))

    def test_record_changed_during_touch(self):
        """#435 R1 L1:按指紋期間席位檔換了(同席碼換窗 / 換人)⇒ seat_busy、完全沒叫 tmux"""
        for change in ({"window_id": "@9"}, {"pid": 999}, {"retired": True}):
            with self.subTest(change=change):
                orig = self.rec_file.read_text()
                def t(r, change=change):
                    self.reasons.append(r)
                    rec = json.loads(orig)
                    rec.update(change)
                    self.rec_file.write_text(json.dumps(rec))
                    return {"ok": True, "why": "ok"}
                self.calls[:] = []
                res, _ = SS.do_paste(NAME, "%s/%s" % (CABIN, CODE), env=self.env, platform="darwin", touchid=t,
                                     touchid_available=lambda: True, run=self.run_ok)
                self.assertEqual(res, {"ok": False, "why": "seat_busy"})
                self.assertEqual(self.calls, [])
                self.rec_file.write_text(orig)

    def test_busy_no_popup(self):
        self.at_prompt = False
        self.assertEqual(self.paste()[0], {"ok": False, "why": "seat_busy"})
        self.assertEqual((self.reasons, self.calls), ([], []))

    def test_denied_no_tmux(self):
        self.assertEqual(self.paste(ok=False)[0], {"ok": False, "why": "denied"})
        self.assertEqual(self.calls, [])

    def test_load_fails_still_deletes(self):
        def run(argv, **kw):
            self.calls.append((list(argv), kw.get("input"), {}))
            return subprocess.CompletedProcess(argv, 1 if "load-buffer" in argv else 0, "", "")
        self.assertEqual(self.paste(run=run)[0], {"ok": False, "why": "paste_failed"})
        self.assertEqual([a[3] for a, _i, _e in self.calls], ["load-buffer", "delete-buffer"])


class P7Vault(Base):
    def test_codes(self):
        self.assertEqual(SS.read_value("CF_TOKEN_MINTER", self.env), (None, "no_vault"))
        self.assertEqual(SS.read_value("FLUENTMAIL_AWS_SECRET_ACCESS_KEY", self.env), (None, "no_vault"))
        self.assertEqual(SS.read_value("NOT_A_KNOWN_NAME_X", self.env), (None, "no_vault"))
        self.assertEqual(SS.read_value("bad name", self.env), (None, "no_vault"))
        self.assertEqual(SS.read_value(NAME, self.env), (VALUE, None))
        for reason, code in (("disabled", "vault_off"), ("needs_confirm", "vault_off"), ("missing_line", "no_vault"),
                             ("decrypt_failed", "no_vault")):
            with self.subTest(reason=reason), mock.patch.object(S, "secret", side_effect=S.SecretError(reason, NAME)):
                self.assertEqual(SS.read_value(NAME, self.env), (None, code))
        for bad in (VALUE + "\n", "a\rb", "a\x1bb", "a\x00"):
            with self.subTest(bad=repr(bad)), mock.patch.object(S, "secret", return_value=bad):
                self.assertEqual(SS.read_value(NAME, self.env), (None, "bad_value"))


class P8Log(Seat):
    def test_log_and_output_have_no_name_or_value(self):
        out = io.StringIO()
        real = SS.do_paste
        with mock.patch.object(SS, "do_paste", side_effect=lambda n, s: real(n, s, env=self.env, platform="darwin", touchid=self.touch(),
                                                                          touchid_available=lambda: True, run=self.run_ok)):
            rc = SS.main(["paste", NAME, "--seat", "%s/%s" % (CABIN, CODE), "--json"], out=out)
        self.assertEqual((rc, json.loads(out.getvalue())), (0, {"ok": True}))
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"at", "op", "ok", "why"})
        blob = (self.root / "logs" / "secret-share.jsonl").read_text() + out.getvalue()
        self.assertNotIn(NAME, blob)
        self.assertNotIn(VALUE, blob)
        self.assertEqual(stat.S_IMODE((self.root / "logs" / "secret-share.jsonl").stat().st_mode), 0o600)


class P10Order(Seat):
    def test_gate_then_touch_then_read(self):
        res, _ = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                            run=self.run_ok, spawn=self.clearer())
        self.assertTrue(res["ok"])
        self.assertEqual(self.order[:2], ["gate", "touch"])
        self.assertEqual(self.order[2:].count("read"), 1)
        self.order.clear()
        self.assertEqual(self.paste()[0], {"ok": True})
        self.assertEqual(self.order[:2], ["gate", "touch"])
        self.assertIn("read", self.order[2:])

    def test_gate_blocks_before_touch(self):
        for reason in ("disabled", "outside_window", "needs_confirm", "token_short"):
            with self.subTest(reason=reason):
                self.gate, self.order[:] = reason, []
                res, _ = SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(), touchid_available=lambda: True,
                                    run=self.run_ok, spawn=self.clearer())
                self.assertEqual(res, {"ok": False, "why": "vault_off"})
                self.assertEqual(self.paste()[0], {"ok": False, "why": "vault_off"})
                self.assertEqual(self.order, ["gate", "gate"], "不彈指紋、不讀值")
        self.assertEqual((self.reasons, self.calls), ([], []))

    def test_denied_never_reads(self):
        SS.do_copy(NAME, env=self.env, platform="darwin", touchid=self.touch(False), touchid_available=lambda: True, run=self.run_ok)
        self.paste(ok=False)
        self.assertNotIn("read", self.order, "沒按過就不讀值")


TMUX = shutil.which("tmux")


@unittest.skipUnless(TMUX and shutil.which("ps"), "需要 tmux 與 ps")
class P9RealTmux(Seat):
    def setUp(self):
        super().setUp()
        self.sock = "aihub-t" + os.urandom(4).hex()
        tmpdir = Path(tempfile.mkdtemp(prefix="aibtm", dir="/tmp"))
        self.addCleanup(shutil.rmtree, tmpdir, True)
        os.environ["TMUX_TMPDIR"] = str(tmpdir)
        self.scan = self.root / "ps-scan.txt"
        self.shim = self.root / "tmux-shim"
        self.shim.write_text('#!/bin/sh\nps -axww >> "%s"\nexec "%s" "$@"\n' % (self.scan, TMUX))
        self.shim.chmod(0o700)
        base = [TMUX, "-L", self.sock, "-f", "/dev/null"]
        subprocess.run(base + ["new-session", "-d", "-x", "200", "-y", "20", "cat"], check=True, timeout=10,
                       stdin=subprocess.DEVNULL, capture_output=True)
        self.addCleanup(subprocess.run, base + ["kill-server"], capture_output=True, timeout=10)
        wid = subprocess.run(base + ["display-message", "-p", "#{window_id}"], capture_output=True, text=True, timeout=10).stdout.strip()
        rec = json.loads(self.rec_file.read_text())
        rec.update(socket=self.sock, window_id=wid)
        self.rec_file.write_text(json.dumps(rec))
        self.base = base

    def test_value_never_in_ps_and_typed_without_enter(self):
        res, rc = SS.do_paste(NAME, "%s/%s" % (CABIN, CODE), env=self.env, platform="darwin", touchid=self.touch(),
                              touchid_available=lambda: True, tmux=str(self.shim))
        self.assertEqual((res, rc), ({"ok": True}, 0))
        self.assertTrue(self.scan.exists() and self.scan.read_text().strip(), "shim 有掃到 ps")
        self.assertFalse(VALUE in self.scan.read_text(), "ps -axww 掃到值")   # 不印行程表(別的行程的 argv 不進測試輸出)
        screen = ""
        for _ in range(20):
            screen = subprocess.run(self.base + ["capture-pane", "-p"], capture_output=True, text=True, timeout=10).stdout
            if VALUE in screen:
                break
            time.sleep(0.1)
        lines = [ln for ln in screen.splitlines() if ln.strip()]
        self.assertEqual(lines, [VALUE], "打出了值、沒有按 Enter(cat 沒回顯第二份)")
        bufs = subprocess.run(self.base + ["list-buffers"], capture_output=True, text=True, timeout=10).stdout
        self.assertNotIn("aib-secret-", bufs, "buffer 用完即刪")


if __name__ == "__main__":
    unittest.main()
