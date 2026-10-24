#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib memory write 同一台多席同時寫不撞號(10-24 @ee4f8 查到 5 組 uid 重複:不同席同一刻寫,讀到同一份索引配到同一個號)。
全在 tmp 沙盒(暫存 HOME / EZ2_AIB_HOME / EZ2_AIB_STATE_ROOT / Vault),不碰真的第二大腦。
U1 兩支行程同時開跑、時鐘凍在同一毫秒,各寫 3 則 ⇒ 6 則全寫進、uid 全不同、索引 6 列
U2 鎖等太久 ⇒ 說原因(MemoryError_),不是卡死
刀(複本,走 run_guarded):K1 拿掉寫入鎖 ⇒ U1 紅
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src" / "memory"))
import aib_testlib  # noqa: E402,F401
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

HAVE_NODE = shutil.which("node") is not None

CHILD = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    go = Path(sys.argv[2])
    while not go.exists():
        time.sleep(0.005)
    time.time = lambda: 1792814400.123            # 兩支行程凍在同一毫秒 ⇒ 不鎖就配到同一個號
    import aib_memory as AM
    for i in range(3):
        AM.write("note", "同時寫 %s-%d" % (sys.argv[3], i), "內文", route_key="core.handoff")
""")


def sandbox(t):
    tmp = Path(tempfile.mkdtemp(prefix="aib-uidlock-"))
    t.addCleanup(shutil.rmtree, tmp, True)
    home, vault = tmp / "home", tmp / "vault"
    home.mkdir()
    vault.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("EZ2_VAULT_ROOT", "EZ2_AI_REPO", "EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT", "TMUX", "TMUX_PANE")}
    env.update({"HOME": str(home), "EZ2_VAULT_DIR": str(vault), "EZ2_AIB_LOCALE": "en",
                "EZ2_AIB_HOME": str(home / ".ez2-aib"), "EZ2_AIB_STATE_ROOT": str(home / ".ez2-aib"),
                "EZ2_EZ2AI_REPO": str(tmp / "no-repo"),
                # 套件預設契約只在 Vault 還空的時候自動用;第二則起 Vault 有東西了 ⇒ 明講用它
                "EZ2_ROUTE_CONTRACT_SPEC": str(ROOT / "src" / "persona_route" / "default-route-contract.json")})
    return tmp, home, env


def race(t, root):
    tmp, home, env = sandbox(t)
    child = tmp / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    go = tmp / "go"
    procs = [subprocess.Popen([sys.executable, str(child), str(root / "src" / "memory"), str(go), n], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in ("a", "b")]
    go.write_text("1", encoding="utf-8")
    outs = [p.communicate(timeout=180) for p in procs]
    idx = home / ".ez2-aib" / "memory-index" / "notes.jsonl"
    rows = [json.loads(x) for x in idx.read_text(encoding="utf-8").splitlines() if x.strip()] if idx.exists() else []
    return [p.returncode for p in procs], outs, rows


@unittest.skipUnless(HAVE_NODE, "沒有 node(note-format runner 要 node)")
class UidLock(unittest.TestCase):
    def test_u1_two_writers_same_ms_no_duplicate(self):
        rcs, outs, rows = race(self, ROOT)
        self.assertEqual(rcs, [0, 0], [o[1][-600:] for o in outs])
        uids = [r["uid"] for r in rows]
        self.assertEqual(len(uids), 6, rows)
        self.assertEqual(len(set(uids)), 6, uids)

    def test_u2_lock_timeout_says_why(self):
        tmp, home, env = sandbox(self)
        with mock.patch.dict(os.environ, env, clear=True):
            import aib_memory as AM
            import fcntl
            lock = AM.index_path().with_name(".write.lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            holder = open(lock, "a")
            self.addCleanup(holder.close)
            fcntl.flock(holder, fcntl.LOCK_EX)
            with mock.patch.object(AM, "WRITE_LOCK_WAIT_S", 0.2):
                with self.assertRaises(AM.MemoryError_) as cm:
                    with AM._WriteLock():
                        pass
            self.assertIn("鎖", str(cm.exception))


@unittest.skipUnless(HAVE_NODE, "沒有 node")
class Knife(unittest.TestCase):
    def test_k1_no_lock(self):
        name = Path(__file__).name
        with knife_copy(ROOT, "src/memory/aib_memory.py",
                        "                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
                        "                pass\n", tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), "UidLock.test_u1_two_writers_same_ms_no_duplicate"],
                            cwd=str(root), max_procs=60, timeout=300)
        self.assertIsNone(r.tripped, "刀測自己失控了:%s(峰值 %d)" % (r.tripped, r.peak))
        self.assertNotEqual(r.returncode, 0, "刀沒砍出紅\n" + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
