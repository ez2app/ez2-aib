# SPDX-License-Identifier: Apache-2.0
"""aib dm 投遞可靠度與寄件身分(10-15 開源版深查 B1–B9、E4)。

治具:整棵 src/ 抄進暫存根,`messaging/aib-send.sh`(咽喉+引擎)換成假咽喉、PATH 前面放假 tmux。
  · 假咽喉:每次被叫寫一行 JSON 進 calls.jsonl(argv 全收),rc 照 rc_seq 檔逐次取(用完取最後一個)。
  · 假 tmux:list-windows 回活窗;display-message 照 panes.json 回窗號/pane_pid;capture-pane 照 FAKE_CAP_MODE
    回同一幀(static)或每次不同(moving);send-keys / paste 只記帳。
  · HOME、狀態根、佇列根全在暫存根;不碰真艙、不碰 ~/.ez2-aib、不送真 dm。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import aib_tmp_root  # noqa: E402

SEAT_UUID = "11111111-2222-3333-4444-5555555abcde"      # 收件席 @abcde,窗 @2
SEAT2_UUID = "11111111-2222-3333-4444-5555555c0ffe"     # 第二收件席 @c0ffe,窗 @4
SENDER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeee77777"    # 「別席」@77777,窗 @3
HUB = "aihub-t1"

FAKE_TMUX = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_TMUX_DIR"]
args = sys.argv[1:]
while args and args[0] in ("-L", "-S"):
    args = args[2:]
cmd = args[0] if args else ""
with open(os.path.join(d, "tmux.log"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
def opt(name):
    return args[args.index(name) + 1] if name in args else ""
if cmd == "list-windows":
    print(open(os.path.join(d, "windows")).read().strip())
elif cmd == "display-message":
    t = opt("-t")
    fmt = args[-1]
    panes = json.load(open(os.path.join(d, "panes.json"))) if os.path.exists(os.path.join(d, "panes.json")) else {}
    p = panes.get(t)
    if p is None:
        if "#{window_id}" in fmt:
            sys.exit(1)
        p = {}
    out = fmt.replace("#{window_id}", p.get("wid", "")).replace("#{pane_pid}", str(p.get("pid", "")))
    out = out.replace("#{cursor_y}", "0")
    print(out)
elif cmd == "capture-pane":
    cnt = os.path.join(d, "cap.count")
    n = int(open(cnt).read()) if os.path.exists(cnt) else 0
    open(cnt, "w").write(str(n + 1))
    if os.environ.get("FAKE_CAP_MODE", "static") == "moving":
        print("worker output line %d\n❯ " % n)
    else:
        print("idle\n❯ ")
elif cmd == "list-panes":
    pass
else:
    pass
sys.exit(0)
'''

FAKE_THROAT = r'''#!/bin/bash
d="$FAKE_TMUX_DIR"
python3 - "$d" "$@" <<'PY'
import json, sys
d = sys.argv[1]
with open(d + "/calls.jsonl", "a") as fh:
    fh.write(json.dumps(sys.argv[2:], ensure_ascii=False) + "\n")
PY
for a in "$@"; do [ "$a" = "--execute" ] && exe=1; done
[ "${exe:-0}" = "1" ] || exit 0
n=$(wc -l < "$d/calls.jsonl" | tr -d ' ')
seq="$(cat "$d/rc_seq" 2>/dev/null || echo 0)"
set -- $seq
i=1; rc=0
for r in "$@"; do rc="$r"; [ "$i" -ge "$n" ] && break; i=$((i+1)); done
echo "aib-dm: fake engine rc=$rc"
[ -n "${FAKE_THROAT_SAY:-}" ] && cat "$FAKE_THROAT_SAY"
exit "$rc"
'''


def _tree():
    base = Path(tempfile.mkdtemp(prefix="aib-dmrel-", dir=aib_tmp_root.aib_tmp_root()))
    tree = base / "aib"
    tree.mkdir()
    ign = shutil.ignore_patterns("__pycache__")
    for name in ("src", "i18n", "schemas", "policy", "tools"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, tree / name, ignore=ign, symlinks=True)
    for f in ("VERSION",):
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, tree / f)
    (tree / "src" / "messaging" / "aib-send.sh").write_text(FAKE_THROAT)
    return base, tree


TREE_BASE, TREE = None, None


def setUpModule():
    global TREE_BASE, TREE
    TREE_BASE, TREE = _tree()


def tearDownModule():
    if TREE_BASE:
        shutil.rmtree(TREE_BASE, ignore_errors=True)


class Box:
    """一次測試的沙箱:HOME、假 tmux、席位檔。"""

    def __init__(self, tc, seats=((2, SEAT_UUID),), extra_windows=(3,)):
        self.tc = tc
        self.dir = Path(tempfile.mkdtemp(prefix="box-", dir=str(TREE_BASE)))
        self.home = self.dir / "home"
        self.fake = self.dir / "fake"
        self.bin = self.dir / "bin"
        for p in (self.home, self.fake, self.bin):
            p.mkdir()
        (self.bin / "tmux").write_text(FAKE_TMUX)
        os.chmod(self.bin / "tmux", 0o755)
        wins = [w for w, _ in seats] + list(extra_windows)
        (self.fake / "windows").write_text("\n".join("@%d" % w for w in wins) + "\n")
        self.aib_home = self.home / ".ez2-aib"
        self.seats = self.aib_home / "instances" / "t1" / "seats"
        self.seats.mkdir(parents=True)
        for w, u in seats:
            self.write_seat(w, u)
        self.rc_seq("0")

    def write_seat(self, wid, uuid, pid=None):
        rec = {"schema": "aib-seat/v1", "window_id": "@%d" % wid, "session_uuid": uuid,
               "socket": HUB, "pid": pid or os.getpid(), "provider": "claude", "role": "dev",
               "seat_id": "s%d" % wid}
        (self.seats / ("w%d.json" % wid)).write_text(json.dumps(rec))

    def rc_seq(self, s):
        (self.fake / "rc_seq").write_text(s)

    def panes(self, mapping):
        (self.fake / "panes.json").write_text(json.dumps(mapping))

    def calls(self):
        p = self.fake / "calls.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def exec_calls(self):
        return [c for c in self.calls() if "--execute" in c]

    def env(self, **kw):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("TMUX", "AIB_", "EZ2_", "CODEX_", "CLAUDE"))}
        e.update({
            "HOME": str(self.home), "EZ2_AIB_HOME": str(self.aib_home),
            "EZ2_AIHUB_SOCKET": HUB, "EZ2_AIB_INSTANCE": "t1",
            "EZ2_AIB_INSTANCE_HOME": str(self.aib_home / "instances" / "t1"),
            "PATH": "%s:%s" % (self.bin, os.environ.get("PATH", "")),
            "FAKE_TMUX_DIR": str(self.fake), "AIB_DM_ADDR_DONE": "1",
            "AIB_DM_SETTLE_SLEEP": "0", "AIB_DM_CONFIRM_SETTLE": "0", "AIB_DM_REPRESS_SLEEP": "0",
            "AIB_DM_RESEND_WAIT_S": "0", "AIB_DM_RELAY_DIR": str(self.aib_home / "pending"),
            "EZ2_AIB_LOCALE": "en", "AIB_GENES": "0", "AIB_AUTO_BIND": "0",
            "TMPDIR": str(self.dir),
        })
        e.update({k: str(v) for k, v in kw.items()})
        return e

    def run(self, args, stdin=None, timeout=90, **envkw):
        p = subprocess.run(["bash", str(TREE / "src" / "cockpit" / "dm-ctl.sh")] + list(args),
                           input=stdin, capture_output=True, text=True, timeout=timeout,
                           env=self.env(**envkw), cwd=str(self.dir))
        return p

    def queue_items(self):
        out = []
        for p in self.aib_home.rglob("q_*.json"):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                pass
        return out


def last_line(p):
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    return lines[-1] if lines else ""


class Smoke(unittest.TestCase):
    def test_plain_delivery(self):
        b = Box(self)
        p = b.run(["@abcde", "hello there"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(last_line(p).startswith("RESULT stage=delivered"), p.stdout)


def rc_contract(p):
    """stdout 裡最後一行 rc_contract JSON(解不開就讓測試紅在這裡,不吞)。"""
    rows = [l for l in p.stdout.splitlines() if l.startswith('{"rc_contract"')]
    return json.loads(rows[-1]) if rows else None


def result_fields(line):
    parts = line.split(" ")
    assert parts[0] == "RESULT", line
    return parts[1:]


class B1UnconfirmedIsNotResendable(unittest.TestCase):
    """B1:「未確認送達,不要自動重送」出口要回 4(勿重送),不是 2(可安全重送)。"""

    def test_unsettled_frames_rc4_and_no_auto_resend(self):
        b = Box(self)
        b.rc_seq("4")
        p = b.run(["@abcde", "hello b1"], FAKE_CAP_MODE="moving", AIB_DM_AUTO_RESEND="1")
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertEqual(rc_contract(p)["rc_contract"], 4, p.stdout)
        self.assertEqual(len(b.exec_calls()), 1, "未確認送達不得自動重送")

    def test_unsettled_frames_not_retried_by_retry_until(self):
        b = Box(self)
        b.rc_seq("4")
        p = b.run(["@abcde", "hello b1 retry", "--retry-until", "2", "--retry-every", "0"],
                  FAKE_CAP_MODE="moving")
        self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 1, "--retry-until 不得重送未確認送達")

    def test_settled_blocked_still_safe_resend_control(self):
        b = Box(self)
        b.rc_seq("4 0")
        p = b.run(["@abcde", "hello b1 control"], FAKE_CAP_MODE="static", AIB_DM_AUTO_RESEND="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(b.exec_calls()), 2)
        self.assertIn("resend=1", last_line(p))


class E4BlockedResultHasReason(unittest.TestCase):
    """E4:被擋時 RESULT 行帶 reason=<block_kind>;前四鍵與鍵序不變。"""

    def test_blocked_reason_foreign_draft(self):
        b = Box(self)
        b.rc_seq("4")
        (b.dir / "throat-say").write_text("aib-dm: block_kind=draft\n")
        p = b.run(["@abcde", "hello e4"], FAKE_CAP_MODE="static", FAKE_THROAT_SAY=b.dir / "throat-say")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        f = result_fields(last_line(p))
        self.assertEqual([x.split("=")[0] for x in f[:4]], ["stage", "id", "relay", "thread"])
        self.assertEqual(f[0], "stage=blocked")
        self.assertIn("reason=foreign-draft", f[4:], last_line(p))

    def test_blocked_reason_engine_rc_when_kind_unknown(self):
        b = Box(self)
        b.rc_seq("3")
        p = b.run(["@abcde", "hello e4b"], FAKE_CAP_MODE="static")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("reason=engine-rc-3", result_fields(last_line(p))[4:], last_line(p))

    def test_delivered_has_exactly_four_keys(self):
        b = Box(self)
        p = b.run(["@abcde", "hello e4c"])
        self.assertEqual(len(result_fields(last_line(p))), 4, last_line(p))


if __name__ == "__main__":
    unittest.main()
