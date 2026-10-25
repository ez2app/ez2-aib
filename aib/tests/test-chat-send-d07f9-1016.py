#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""全球對談寄件(開發文件第 7.1 節;22.1 PR-9 = 第 1 期 1.7 寄件那一半)。

當班的轉接程式起一支寄件工人(venv 的 chat_nostr.py serve):領 outbox → 解析位址(名冊、10050)→ 包 NIP-17 →
寄到對方收信站、另寄自己一份 → 回條 relayed;心跳只由它寫。全部只連本機假中繼站。
沙盒:主人 A 有兩席,X(寄件)與 Y(收件,列進名冊);寄件端走 dm_global_files.send_and_wait(dm-ctl 用的同一支)。

核心(一定跑):
C1 沒有 venv ⇒ 不起工人、沒有心跳;send_and_wait ⇒ rc 2 bridge-off,一個字都沒寫進 outbox
連 Nostr(要 AIB_TEST_NOSTR_VENV;沒有就跳過):
S1 X 寄 A/6ddf/Y ⇒ rc 2 refused(X 沒列名冊,Y 驗不到 ⇒ 拒收,10-16 好友制);假中繼站上有給 Y 的 gift wrap,
   Y 的金鑰拆得開:寄件人 = X 的 npub、內文對、aib-msgid / aib-from / aib-to / aib-priority 標籤對;另有一份給 X 自己
S2 --dry-run(op=resolve)⇒ rc 0 resolved,detail 有 Y 的 npub 與收信站;沒有寄出任何 gift wrap
S3 寄件人沒量到(attested=false)⇒ rc 2 refused unattested,中繼站上沒有任何 gift wrap
S4 沒公開的席 ⇒ rc 2 refused no_seat;過期的信 ⇒ rc 2 expired;都沒寄
S5 對方收信站全連不上 ⇒ 重試 3 次後 failed(rc 2,或逾時 rc 4 後回條補 failed);不會無限重試
S6 有工人就有心跳;off ⇒ 工人結束、心跳刪掉、寄件端回 bridge-off
L1 洩漏:沙盒(金鑰檔以外)、回條、工人 log、中繼站所有事件都沒有 nsec1 與私鑰 hex
沙盒收尾(10-25 @abb89:int0155 樹跑完留 chat_bridge run + chat_nostr serve 孤兒 10 小時):
O1 Box.close 收掉轉接程式與工人,收完 ps 查不到任何指令帶這個沙盒路徑的行程;有就殺掉並判紅
O2 測試行程自己被 SIGKILL(清理沒機會跑)⇒ 看門狗幾秒內收掉那個沙盒的轉接程式 + 工人
O3 pid 被重用(#346 r1):pgid 對得上但指令不是這支 chat_bridge.py 的行程 ⇒ 不算殘留、close 不殺
O4 看門狗盯的組長換成別的行程(pid 重用)⇒ 看門狗收手、不 killpg 別人那組
刀(複本):K1 拿掉「沒量到不代簽」⇒ S3 紅;K2 不寄自己那一份 ⇒ S1 紅;K3 不起看門狗 ⇒ O2 紅;
K4 只看 pgid ⇒ O3 紅;K5 看門狗不驗組長指令 ⇒ O4 紅
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COCKPIT = REPO / "src" / "cockpit"
BRIDGE = COCKPIT / "chat_bridge.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(COCKPIT))
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402
import dm_global_files as GF  # noqa: E402
import nostr_keys as NK  # noqa: E402

NOSTR_VENV = os.environ.get("AIB_TEST_NOSTR_VENV") or ""
HAVE_NOSTR = bool(NOSTR_VENV) and (Path(NOSTR_VENV) / "bin" / "python").exists() and subprocess.run(
    [str(Path(NOSTR_VENV) / "bin" / "python"), "-c", "import nostr_sdk"], capture_output=True).returncode == 0

UNWRAP = r'''
import json, sys
import nostr_sdk as N
req = json.loads(sys.stdin.read())
k = N.Keys.parse(req["secret"])
out = []
for raw in req["events"]:
    try:
        u = N.UnwrappedGift.from_gift_wrap(k, N.Event.from_json(json.dumps(raw)))
    except Exception as e:
        out.append({"error": type(e).__name__}); continue
    r = u.rumor()
    out.append({"sender": u.sender().to_bech32(), "content": r.content(), "kind": r.kind().as_u16(),
                "tags": [t.to_vec() for t in r.tags()]})
print(json.dumps(out, ensure_ascii=False))
'''


# 看門狗(獨立 session、不帶沙盒路徑在 argv):測試行程不在了 ⇒ 殺轉接程式那一組 + 工人;轉接程式自己結束 ⇒ 也走
REAPER = r'''
import json, os, signal, subprocess, sys, time
parent, pgid = int(sys.argv[1]), int(sys.argv[2])
root, bridge = os.environ["AIB_REAPER_ROOT"], os.environ["AIB_REAPER_BRIDGE"]
def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
def ours():
    # pid 可能被別的行程重用(#346 r1):組長還是那支轉接程式才算,不是就收手
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pgid)], capture_output=True, text=True).stdout
    return bridge in cmd
while alive(pgid) and ours():
    if not alive(parent):
        try:
            wp = json.load(open(os.path.join(root, "status.json"))).get("worker_pid")
        except Exception:
            wp = None
        if ours():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if isinstance(wp, int) and wp > 1:
            cmd = os.popen("ps -o command= -p %d" % wp).read()
            if root in cmd:
                try:
                    os.kill(wp, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        break
    time.sleep(0.5)
'''


def _ours(cmd, pgid, root, pgids):
    """指令帶沙盒路徑(工人 serve --root …),或在轉接程式那一組裡而且指令就是這支 chat_bridge.py。
    只看 pgid 不夠:轉接程式走了、pid 被別的行程組長重用 ⇒ 會殺到別人(#346 r1)。"""
    return str(root) in cmd or (pgid in pgids and str(BRIDGE) in cmd)


def leftovers(root, pgids=()):
    """還活著的沙盒行程(條件見 _ours)。看門狗另開 session、argv 不帶路徑,不算。"""
    r = subprocess.run(["ps", "-axo", "pid=,pgid=,stat=,command="], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        f = line.split(None, 3)
        if len(f) < 4 or not (f[0].isdigit() and f[1].isdigit()) or f[2].startswith("Z"):
            continue
        pid, pgid, cmd = int(f[0]), int(f[1]), f[3]
        if pid != os.getpid() and _ours(cmd, pgid, root, pgids):
            out.append(pid)
    return out


def _still_ours(pid, root, pgids):
    """殺之前再量一次(中間可能剛好結束、pid 被重用)。"""
    r = subprocess.run(["ps", "-o", "pgid=,command=", "-p", str(pid)], capture_output=True, text=True)
    f = r.stdout.strip().split(None, 1)
    return len(f) == 2 and f[0].isdigit() and _ours(f[1], int(f[0]), root, pgids)


class Box(object):
    def __init__(self, relays=None):
        self.td = Path(tempfile.mkdtemp(prefix="aib-send-"))
        self.home = self.td / "home"
        self.home.mkdir()
        self.chat = self.home / ".ez2-aib" / "chat"
        self.root = self.td / "bridge"
        self.relays = relays or []
        self.procs = []
        self.reapers = []
        self.extra = {}                      # 測試要改的環境變數(例 AIB_CHAT_READ_BATCH);env() 與 start_run 都帶

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("EZ2_", "AIB_", "TMUX")) and k not in ("PYTHONPATH", "BASH_ENV")}
        env.update({"HOME": str(self.home), "AIB_CHAT_BRIDGE_DIR": str(self.root), "AIB_CHAT_TICK": "0.2",
                    "EZ2_AIB_LOCALE": "zh-TW", "PYTHONDONTWRITEBYTECODE": "1", "AIB_CHAT_NET_TIMEOUT": "3",
                    "AIB_CHAT_POLL": "0.1", "AIB_CHAT_RETRY_GAP": "0.3"})
        if self.relays:
            env["AIB_CHAT_RELAYS"] = ",".join(self.relays)
        env.update(self.extra)
        env.update(extra)
        return env

    def cli(self, *args, **extra):
        return subprocess.run([sys.executable, str(BRIDGE)] + list(args), capture_output=True, text=True,
                              env=self.env(**extra), timeout=120)

    def seat(self, inst, win, uuid, uid, name):
        d = self.home / ".ez2-aib" / "instances" / inst / "seats"
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s.json" % win)).write_text(json.dumps({"schema": "aib-seat/v1", "session_uuid": uuid, "role": "dev"}))
        disp = self.home / ".ez2-aib" / "identity" / "seat-display.json"
        disp.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(disp.read_text()) if disp.exists() else {"display": {}}
        cur["display"][uuid.replace("-", "")[-5:]] = {"persona_uid": uid, "display_name": name}
        disp.write_text(json.dumps(cur))
        return uuid.replace("-", "")[-5:]

    def start_run(self):
        p = subprocess.Popen([sys.executable, str(BRIDGE), "run"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=self.env(), start_new_session=True)
        self.procs.append(p)
        self.reapers.append(subprocess.Popen([sys.executable, "-c", REAPER, str(os.getpid()), str(p.pid)],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True,
                                             env=dict(os.environ, AIB_REAPER_ROOT=str(self.root),
                                                      AIB_REAPER_BRIDGE=str(BRIDGE))))
        return p

    def status(self):
        try:
            return json.loads((self.root / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait_for(self, cond, secs=30):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False

    def close(self):
        self.cli("off")
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._kill_worker()
        for r in self.reapers:
            if r.poll() is None:
                r.kill()
            r.wait(timeout=10)
        left = []
        end = time.time() + 5
        while time.time() < end:
            left = leftovers(self.root, [p.pid for p in self.procs])
            if not left:
                break
            time.sleep(0.2)
        for pid in left:                                  # 收不掉的:殺掉再判紅(不讓孤兒活下去)
            if not _still_ours(pid, self.root, [p.pid for p in self.procs]):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        shutil.rmtree(self.td, ignore_errors=True)
        if left:
            raise AssertionError("沙盒收尾後還有行程:%s" % left)

    def _kill_worker(self):
        """aib chat off 後 run 先結束、寄件工人可能還沒走(@abb89 #297 r1 看到收尾留行程)。
        只殺指令裡帶這個沙盒路徑的那一個 pid(pid 可能被別的程式重用,不寬殺)。"""
        pid = self.status().get("worker_pid")
        if not isinstance(pid, int) or pid <= 1:
            return
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        if str(self.root) in r.stdout:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def outbox_rec(to, frm_seat, content="你好,全球", attested=True, op="send", **extra):
    rec = {"v": 1, "msgid": GF.new_msgid(), "created_at": int(time.time()), "op": op,
           "from": {"cabin": "6ddf", "seat": frm_seat, "attested": attested}, "to": to, "priority": "important"}
    if op == "send":
        rec["content"] = content
    rec.update(extra)
    return rec


class Core(unittest.TestCase):
    def test_c1_no_venv_no_heartbeat(self):
        b = Box()
        self.addCleanup(b.close)
        x = b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        r = b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        b.start_run()
        self.assertTrue(b.wait_for(lambda: b.status().get("state") in ("no_relays", "published")), b.status())
        time.sleep(0.6)
        self.assertIsNone(b.status().get("worker_pid"))
        self.assertFalse(GF.bridge_alive(b.root))
        res = GF.send_and_wait(b.root, outbox_rec("npub1" + "q" * 58, x), wait=1)
        self.assertEqual((res["rc"], res["stage"]), (2, "bridge-off"))
        self.assertEqual(list((b.root / "outbox").glob("*.json")), [])


CRASH = r'''
import os, signal, sys, time
sys.path.insert(0, sys.argv[1])
import importlib.util
spec = importlib.util.spec_from_file_location("sendbox", sys.argv[2])
SB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SB)
b = SB.Box()
if sys.argv[3]:
    b.chat.mkdir(parents=True, exist_ok=True)
    os.symlink(sys.argv[3], str(b.chat / "venv"))
b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
b.start_run()
b.wait_for(lambda: b.status().get("state") in ("no_relays", "published") and
           (not sys.argv[3] or b.status().get("worker_pid")), 30)
print(b.root, b.status().get("worker_pid") or "-", b.procs[0].pid, flush=True)
os.kill(os.getpid(), signal.SIGKILL)                 # 清理沒機會跑
'''


class Teardown(unittest.TestCase):
    def test_o1_close_leaves_nothing(self):
        b = Box()
        b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        p = b.start_run()
        self.assertTrue(b.wait_for(lambda: bool(leftovers(b.root, [p.pid])), 10))
        b.close()
        self.assertEqual(leftovers(b.root, [p.pid]), [])

    def _stranger(self):
        """自己一組(start_new_session ⇒ pgid = pid)、指令不是 chat_bridge.py 的行程:模擬 pid 被重用。"""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_o3_pid_reuse_not_ours(self):
        p = self._stranger()
        root = Path(tempfile.mkdtemp(prefix="aib-send-")) / "bridge"
        self.addCleanup(shutil.rmtree, str(root.parent), True)
        self.assertEqual(leftovers(root, [p.pid]), [])
        self.assertFalse(_still_ours(p.pid, root, [p.pid]))
        class Gone(object):                             # 轉接程式已經結束(收過了),它的 pid 被 p 重用
            pid = p.pid

            def poll(self):
                return 0

            def communicate(self, timeout=None):
                return None, None
        b = Box()
        b.procs.append(Gone())
        b.close()
        self.assertIsNone(p.poll(), "別人的行程被殺了")

    def test_o4_reaper_leaves_stranger(self):
        p = self._stranger()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()                                     # 測試行程「已經不在」
        root = Path(tempfile.mkdtemp(prefix="aib-send-")) / "bridge"
        self.addCleanup(shutil.rmtree, str(root.parent), True)
        r = subprocess.run([sys.executable, "-c", REAPER, str(dead.pid), str(p.pid)], timeout=30,
                           env=dict(os.environ, AIB_REAPER_ROOT=str(root), AIB_REAPER_BRIDGE=str(BRIDGE)))
        self.assertEqual(r.returncode, 0)
        time.sleep(0.3)
        self.assertIsNone(p.poll(), "看門狗 killpg 了別人那組")

    def test_o2_reaper_after_sigkill(self):
        venv = NOSTR_VENV if HAVE_NOSTR else ""
        r = subprocess.run([sys.executable, "-c", CRASH, str(HERE), str(Path(__file__).resolve()), venv],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stderr)
        root, wp, pg = r.stdout.split()[:3]
        pg = [int(pg)]
        self.addCleanup(shutil.rmtree, str(Path(root).parent), True)
        if venv:
            self.assertTrue(wp.isdigit(), "有 venv 時要等到工人起來才砍:%r" % r.stdout)
        self.assertTrue(leftovers(root, pg), "砍測試行程時轉接程式要還活著(不然驗不到)")
        end, left = time.time() + 15, None
        while time.time() < end:
            left = leftovers(root, pg)
            if not left:
                break
            time.sleep(0.2)
        for pid in left or []:
            os.kill(pid, signal.SIGKILL)
        self.assertEqual(left, [], "看門狗沒收掉")


@unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV(裝了 nostr-sdk==0.45.1 的 venv)")
class Network(unittest.TestCase):
    def setUp(self):
        from aib_fake_relay import FakeRelay
        self.relay = FakeRelay()
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        self.b = Box([self.relay.url])
        self.addCleanup(self.b.close)
        self.b.chat.mkdir(parents=True, exist_ok=True)
        os.symlink(NOSTR_VENV, str(self.b.chat / "venv"))
        self.x = self.b.seat("20261016-000000-6ddf", "w2", "00000000-0000-0000-0000-00000000aaa1", "xxxx11", "寄件席")
        self.y = self.b.seat("20261016-000000-6ddf", "w3", "00000000-0000-0000-0000-00000000bbb2", "yyyy22", "收件席")
        self.hidden = self.b.seat("20261016-000000-6ddf", "w4", "00000000-0000-0000-0000-00000000ccc3", "zzzz33", "沒公開")
        r = self.b.cli("on", "--no-venv", "--name", "Andy", "--country", "TW")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.b.cli("publish", self.y)
        self.b.start_run()
        ok = self.b.wait_for(lambda: self.b.status().get("state") == "published" and GF.bridge_alive(self.b.root), 40)
        self.assertTrue(ok, self.b.status())
        self.env = self.b.env()
        self.owner = NK.read_map(self.env)["owner"]["npub"]

    def npub_of(self, seat):
        return next(e["npub"] for e in NK.read_map(self.env)["seats"] if e["seat5"] == seat and e["status"] == "active")

    def stem_of(self, seat):
        return next(e["key"] for e in NK.read_map(self.env)["seats"] if e["seat5"] == seat and e["status"] == "active")

    def wraps_for(self, seat):
        from aib_fake_relay import RelayClient
        c = RelayClient(self.relay.url)
        c.send(["REQ", "w", {"kinds": [1059], "#p": [NK.npub_to_hex(self.npub_of(seat))]}])
        evs = c.recv_until_eose("w")
        return evs

    def unwrap(self, seat, events):
        req = json.dumps({"secret": NK.load_secret(self.stem_of(seat), self.env), "events": events})
        r = subprocess.run([str(Path(NOSTR_VENV) / "bin" / "python"), "-c", UNWRAP], input=req, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_s1_send(self):
        rec = outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x, thread="t-1")
        res = GF.send_and_wait(self.b.root, rec, wait=20)
        # X 沒列名冊 ⇒ Y 那邊驗不到 ⇒ 拒收(維護者 10-16 12:1x:只收本人與好友)⇒ 寄件端 rc 2,說得出原因
        self.assertEqual((res["rc"], res["stage"]), (2, "refused"), res)
        self.assertIn("名冊", res["detail"])
        got = self.unwrap(self.y, self.wraps_for(self.y))
        self.assertEqual(len(got), 1, got)
        g = got[0]
        self.assertEqual((g["sender"], g["content"], g["kind"]), (self.npub_of(self.x), "你好,全球", 14))
        tags = {t[0]: t[1] for t in g["tags"] if len(t) > 1}
        self.assertEqual(tags["aib-msgid"], rec["msgid"])
        self.assertEqual(tags["aib-from"], "%s/6ddf/%s" % (self.owner, self.x))
        self.assertEqual(tags["aib-to"], rec["to"])
        self.assertEqual((tags["aib-priority"], tags["aib-thread"], tags["aib-v"], tags["aib-type"]), ("important", "t-1", "1", "dm"))
        mine = [m for m in self.unwrap(self.x, self.wraps_for(self.x)) if m.get("sender") == self.npub_of(self.x)]
        self.assertEqual([m["content"] for m in mine], ["你好,全球"], "要另寄一份給自己(NIP-17)")

    def test_s2_dry_run(self):
        res = GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x, op="resolve"), wait=8)
        self.assertEqual((res["rc"], res["stage"]), (0, "resolved"), res)
        d = json.loads(res["detail"])
        self.assertEqual((d["npub"], d["dm_relays"], d["display_name"]), (self.npub_of(self.y), [self.relay.url], "Andy"))
        self.assertEqual(self.wraps_for(self.y), [])

    def test_s3_unattested_not_signed(self):
        res = GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x, attested=False), wait=8)
        self.assertEqual((res["rc"], res["stage"], res["detail"]), (2, "refused", "unattested"), res)
        self.assertEqual(self.wraps_for(self.y), [])
        self.assertEqual(self.wraps_for(self.x), [])

    def test_s4_no_seat_and_expired(self):
        res = GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.hidden), self.x), wait=8)
        self.assertEqual((res["rc"], res["stage"], res["detail"]), (2, "refused", "no_seat"), res)
        res = GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x,
                                                       expires=int(time.time()) - 5), wait=8)
        self.assertEqual((res["rc"], res["stage"]), (2, "expired"), res)
        self.assertEqual(self.wraps_for(self.y), [])

    def test_s5_relays_down_gives_up(self):
        # 對方 10050 列的站連不上:用 npub 直寄、10050 先發到假站,裡面寫一個關著的埠
        from aib_fake_relay import RelayClient
        dead = "ws://127.0.0.1:9"
        target = NK.npub(NK.pubkey_hex("%064x" % 11))
        # 假站不驗簽,但 nostr-sdk 收事件會驗 ⇒ 用 venv 真的簽一則
        signed = subprocess.run([str(Path(NOSTR_VENV) / "bin" / "python"), "-c",
                                 "import nostr_sdk as N,sys;k=N.Keys.parse('%064x'%11);"
                                 "print(N.EventBuilder(N.Kind(10050),'').tags([N.Tag.parse(['relay',sys.argv[1]])]).finalize(k).as_json())",
                                 dead], capture_output=True, text=True, timeout=60)
        ev = json.loads(signed.stdout)
        c = RelayClient(self.relay.url)
        c.send(["EVENT", ev])
        c.recv()
        rec = outbox_rec(target, self.x)
        res = GF.send_and_wait(self.b.root, rec, wait=20)
        if res["rc"] == 4:                                        # 逾時:晚到的回條要是 failed
            self.assertTrue(self.b.wait_for(lambda: GF.final_receipt(GF.read_receipts(self.b.root, rec["msgid"])) is not None, 30))
            res = {"rc": 2, "stage": GF.final_receipt(GF.read_receipts(self.b.root, rec["msgid"]))["stage"]}
        self.assertEqual((res["rc"], res["stage"]), (2, "failed"), res)
        self.assertEqual(list((self.b.root / "sending").glob(rec["msgid"] + ".*")), [], "放棄後不准再重試")

    def test_s6_heartbeat_and_off(self):
        self.assertTrue(GF.bridge_alive(self.b.root))
        pid = self.b.status().get("worker_pid")
        self.assertIsInstance(pid, int)
        self.b.cli("off")
        self.assertTrue(self.b.wait_for(lambda: not (self.b.root / "heartbeat.json").exists(), 15), "off 後心跳要刪")
        res = GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x), wait=1)
        self.assertEqual(res["stage"], "bridge-off")

    def test_l1_no_secret_leak(self):
        GF.send_and_wait(self.b.root, outbox_rec("%s/6ddf/%s" % (self.owner, self.y), self.x), wait=4)
        m = NK.read_map(self.env)
        stems = ["owner"] + [e["key"] for e in m["seats"] if e["status"] == "active"]
        secrets = [NK.load_secret(s, self.env) for s in stems]
        nsecs = [NK.bech32_encode("nsec", bytes.fromhex(s)) for s in secrets]
        from aib_fake_relay import RelayClient
        c = RelayClient(self.relay.url)
        c.send(["REQ", "all", {}])
        evs = c.recv_until_eose("all")
        blobs = [("relay", json.dumps(evs).lower())]
        for p in self.b.td.rglob("*"):
            if p.is_file() and not p.name.endswith(".key") and p.name != "wrap.key" and "venv" not in p.parts:
                blobs.append((str(p), p.read_bytes().decode("utf-8", "replace").lower()))
        for where, text in blobs:
            self.assertNotIn("nsec1", text, where)
            for s, ns in zip(secrets, nsecs):
                self.assertNotIn(s, text, where)
                self.assertNotIn(ns, text, where)


class Knife(unittest.TestCase):
    def _knife(self, needle, replacement, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/chat_nostr.py", needle, replacement, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅:" + cell + "\n" + r.stdout + r.stderr)

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k1_sign_unattested(self):
        self._knife('        if not frm.get("attested"):', '        if False:', "Network.test_s3_unattested_not_signed")

    @unittest.skipUnless(HAVE_NOSTR, "沒有 AIB_TEST_NOSTR_VENV")
    def test_k2_no_self_copy(self):
        self._knife('            await c.send_event(mine, N.SendEventTarget.to([N.RelayUrl.parse(r) for r in own]))',
                    '            pass', "Network.test_s1_send")


    def test_k3_no_reaper(self):
        name = Path(__file__).name
        self._self_knife('"-c", REAP' + 'ER, str(os.getpid())', '"-c", "pass", str(os.getpid())',
                         "Teardown.test_o2_reaper_after_sigkill")

    def _self_knife(self, needle, repl, cell):
        name = Path(__file__).name
        with knife_copy(REPO, "tests/" + name, needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cell], cwd=str(root), max_procs=80, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)
        self.assertIn("AssertionError", r.stdout + r.stderr)

    def test_k4_pgid_only(self):
        self._self_knife("    return str(root) in cmd or (pgid in pgids and str(BRI" + "DGE) in cmd)",
                         "    return str(root) in cmd or pgid in pgids", "Teardown.test_o3_pid_reuse_not_ours")

    def test_k5_reaper_no_check(self):
        self._self_knife("    return bridge in " + "cmd", "    return True", "Teardown.test_o4_reaper_leaves_stranger")


if __name__ == "__main__":
    unittest.main()
